#!/usr/bin/env python3
"""Compute SSE settings and optional ngrok URL for run.bat."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from shutil import which
from urllib.parse import urlparse


def find_ngrok() -> str | None:
    # 1) PATH
    ngrok = which("ngrok")
    if ngrok:
        return ngrok

    # 2) Beside this script or in a local bin/ folder (repo-friendly, no PATH edits)
    script_dir = Path(__file__).resolve().parent
    repo_root = (script_dir / ".." / "..").resolve()
    candidates = [
        script_dir / "ngrok.exe",
        script_dir / "bin" / "ngrok.exe",
        script_dir / "tools" / "ngrok.exe",
        repo_root / "ngrok.exe",
        repo_root / "bin" / "ngrok.exe",
        repo_root / "tools" / "ngrok.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)

    return None


def local_config_path() -> Path:
    return Path(__file__).resolve().parent / ".rdx_mcp.json"


def load_local_config() -> dict:
    path = local_config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def save_local_config(data: dict) -> None:
    path = local_config_path()
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
    except Exception:
        pass


def get_local_authtoken() -> str:
    cfg = load_local_config()
    token = cfg.get("ngrok_authtoken") or cfg.get("NGROK_AUTHTOKEN") or ""
    return str(token).strip()


def pick_port(preferred: int = 8765) -> int:
    """Pick preferred port if free; otherwise choose an ephemeral free port."""
    s = socket.socket()
    try:
        s.bind(("", preferred))
        return preferred
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get_lan_ip() -> str:
    """Best-effort LAN IP detection."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        try:
            s.close()
        except Exception:
            pass


def is_private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def is_public_host(host: str) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def _fetch_tunnels() -> list[dict] | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as resp:
            data = json.load(resp)
        return data.get("tunnels", [])
    except Exception:
        return None


def _select_public_url(tunnels: list[dict], port: int) -> str:
    def matches_port(t: dict) -> bool:
        cfg = t.get("config", {})
        addr = cfg.get("addr") or ""
        return f":{port}" in str(addr)

    for t in tunnels:
        if matches_port(t) and t.get("proto") == "https":
            return t.get("public_url", "")
    for t in tunnels:
        if matches_port(t) and t.get("public_url"):
            return t.get("public_url", "")
    for t in tunnels:
        if t.get("proto") == "https":
            return t.get("public_url", "")
    for t in tunnels:
        if t.get("public_url"):
            return t.get("public_url", "")
    return ""


def start_ngrok(port: int, token: str) -> str:
    ngrok = find_ngrok()
    if not ngrok:
        raise RuntimeError(
            "ngrok not found. Install ngrok (or put ngrok.exe next to run.bat) and try again."
        )

    tunnels = _fetch_tunnels()
    if tunnels:
        url = _select_public_url(tunnels, port)
        if url:
            return url
        raise RuntimeError(
            "ngrok is already running but no tunnel matches this port. "
            "Close ngrok or reuse its existing tunnel."
        )

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x08000000  # CREATE_NO_WINDOW

    env = os.environ.copy()
    if token and not env.get("NGROK_AUTHTOKEN"):
        env["NGROK_AUTHTOKEN"] = token

    subprocess.Popen(
        [ngrok, "http", f"127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        env=env,
    )

    for _ in range(30):
        tunnels = _fetch_tunnels()
        if tunnels:
            url = _select_public_url(tunnels, port)
            if url:
                return url
        time.sleep(1)

    raise RuntimeError("ngrok tunnel not ready. Check ngrok status and try again.")


def read_ngrok_config_authtoken() -> str:
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / ".ngrok2" / "ngrok.yml",
        Path(os.environ.get("LOCALAPPDATA", "")) / "ngrok" / "ngrok.yml",
        Path(os.environ.get("APPDATA", "")) / "ngrok" / "ngrok.yml",
    ]

    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("authtoken"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    value = parts[1].strip().strip("'\"")
                    if value:
                        return value
    return ""


def resolve_ngrok_authtoken(interactive: bool) -> str:
    env_token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if env_token:
        return env_token

    local_token = get_local_authtoken()
    if local_token:
        return local_token

    cfg_token = read_ngrok_config_authtoken()
    if cfg_token:
        return cfg_token

    if not interactive:
        return ""

    print("[RDX-MCP] ngrok auth token required.")
    print("[RDX-MCP] Paste your ngrok authtoken and press Enter (leave empty to cancel).")
    token = input("[RDX-MCP] Authtoken: ").strip()
    if not token:
        return ""

    data = load_local_config()
    data["ngrok_authtoken"] = token
    save_local_config(data)
    return token


def has_ngrok_authtoken() -> bool:
    return bool(resolve_ngrok_authtoken(interactive=False))


def ensure_ngrok_ready(interactive: bool) -> str:
    if not find_ngrok():
        raise RuntimeError(
            "ngrok not found. Install ngrok (or put ngrok.exe next to run.bat) and try again."
        )
    token = resolve_ngrok_authtoken(interactive=interactive)
    if not token:
        raise RuntimeError(
            "ngrok auth token not found. Run: ngrok config add-authtoken <YOUR_TOKEN>"
        )
    return token


def validate_public_url(url: str) -> None:
    if not url:
        raise RuntimeError("ngrok public URL is empty. Check ngrok status and try again.")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise RuntimeError(f"ngrok public URL is invalid: {url}")
    if not is_public_host(host):
        raise RuntimeError(
            f"Internet mode selected but public URL is not public ({host}). "
            "Check ngrok status and try again."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["lan", "internet"], required=True)
    parser.add_argument("--env", required=True, help="Path to .bat env file to write")
    args = parser.parse_args()

    port = pick_port()
    lan_ip = get_lan_ip()
    if not lan_ip:
        lan_ip = "127.0.0.1"

    transport_env = os.environ.get("RDX_TRANSPORT", "").strip().lower()
    if transport_env in ("http", "streamable-http"):
        transport = "streamable-http"
    elif transport_env == "sse":
        transport = "sse"
    else:
        transport = "streamable-http" if args.mode == "internet" else "sse"

    sse_host = "0.0.0.0" if args.mode == "lan" else "127.0.0.1"
    rdx_args = f"--transport {transport} --host {sse_host} --port {port}"

    manus_url = ""
    manus_transport = "SSE" if transport == "sse" else "HTTP"
    endpoint_path = "/sse" if transport == "sse" else "/mcp"
    if args.mode == "lan":
        print("[RDX-MCP] Mode: LAN")
        if lan_ip == "127.0.0.1":
            print("[RDX-MCP] WARNING: LAN IP detection failed; using 127.0.0.1.")
            print("[RDX-MCP] WARNING: This only works on the same machine.")
        elif not is_private_ip(lan_ip):
            print(f"[RDX-MCP] WARNING: LAN IP looks public ({lan_ip}).")
            print("[RDX-MCP] WARNING: If Manus is remote, use INTERNET mode.")
        manus_url = f"http://{lan_ip}:{port}{endpoint_path}"
    else:
        print("[RDX-MCP] Mode: INTERNET (ngrok)")
        print("[RDX-MCP] Checking ngrok installation and auth...")
        token = ensure_ngrok_ready(interactive=True)
        public_url = start_ngrok(port, token)
        validate_public_url(public_url)
        manus_url = f"{public_url.rstrip('/')}{endpoint_path}"
        parsed = urlparse(public_url)
        public_host = parsed.hostname or ""

    lines = [
        f"set RDX_PORT={port}",
        f"set LAN_IP={lan_ip}",
        f"set RDX_SSE_HOST={sse_host}",
        f"set RDX_SSE_PORT={port}",
        f"set RDX_ARGS={rdx_args}",
        f"set MANUS_TRANSPORT={manus_transport}",
        f"set MANUS_URL={manus_url}",
    ]

    if args.mode == "internet" and public_host:
        allowed_hosts = f"{public_host},{public_host}:*,127.0.0.1:*,localhost:*"
        allowed_origins = (
            f"https://{public_host},http://{public_host},"
            f"https://{public_host}:*,http://{public_host}:*"
        )
        lines.append(f"set RDX_ALLOWED_HOSTS={allowed_hosts}")
        lines.append(f"set RDX_ALLOWED_ORIGINS={allowed_origins}")

    with open(args.env, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    # Print config for the user.
    print("[RDX-MCP] Manus config:")
    print(f"[RDX-MCP]   Transport: {manus_transport}")
    print(f"[RDX-MCP]   URL: {manus_url}")

    # Copy to clipboard (best-effort).
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{manus_url}'"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[RDX-MCP] ERROR: {exc}")
        sys.exit(1)
