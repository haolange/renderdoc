#!/usr/bin/env python3
"""Unified RDX-MCP launcher and environment bootstrap."""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
import socket
import site
import subprocess
import sys
import sysconfig
import time
import traceback
import urllib.request
from pathlib import Path
from shutil import which
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

REQUIRED_DEPENDENCIES: list[tuple[str, str]] = [
    ("mcp", "mcp"),
    ("pydantic", "pydantic"),
    ("numpy", "numpy"),
    ("Pillow", "PIL"),
    ("pyarrow", "pyarrow"),
    ("jinja2", "jinja2"),
    ("aiofiles", "aiofiles"),
]

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


def _project_root() -> Path:
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if candidate.name.lower() == "rdx-mcp" and (candidate / "pyproject.toml").is_file():
            return candidate
        alt = candidate / "extensions" / "rdx-mcp"
        if alt.is_dir() and (alt / "pyproject.toml").is_file():
            return alt
    return start.parent


def _repo_root() -> Path:
    root = _project_root()
    return root.parent if root.name.lower() == "rdx-mcp" else root


def _candidate_renderdoc_module_name() -> str:
    return "renderdoc.pyd" if os.name == "nt" else "renderdoc.so"


def _is_interactive() -> bool:
    return bool(sys.stdin) and sys.stdin.isatty() and not bool(os.environ.get("RDX_NON_INTERACTIVE"))


def _read_input(prompt: str) -> Optional[str]:
    try:
        return input(prompt)
    except EOFError:
        print("[RDX] Input stream unavailable.")
        return None
    except KeyboardInterrupt:
        print("\n[RDX] Prompt cancelled.")
        return None


def _to_command_repr(cmd: Sequence[str]) -> str:
    return " ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd)


def _run_command(cmd: Sequence[str], *, dry_run: bool = False, check: bool = False) -> int:
    display = _to_command_repr(cmd)
    print(f"[RDX] CMD: {display}")
    if dry_run:
        return 0
    result = subprocess.run(cmd, check=False)
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result.returncode


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = _read_input(f"[RDX] {prompt} ({hint}): ")
        if raw is None:
            print("[RDX] Treating reply as 'no'.")
            return False
        answer = raw.strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("[RDX] Please type y or n.")


def _print_help_commands(commands: Iterable[str]) -> None:
    print("[RDX] You can run these commands manually:")
    for cmd in commands:
        print(f"[RDX]   {cmd}")


def _has_uv() -> bool:
    if which("uv"):
        return True
    try:
        return (
            subprocess.run(
                [sys.executable, "-m", "uv", "--version"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    except Exception:
        return False


def _prepend_user_scripts_to_path() -> None:
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    candidates: list[Path] = []
    try:
        scripts = Path(sysconfig.get_path("scripts") or "")
        if scripts:
            candidates.append(scripts)
    except Exception:
        pass
    try:
        user_base = site.getuserbase()
        if user_base:
            pyver = f"Python{sys.version_info.major}{sys.version_info.minor}"
            candidates.append(Path(user_base) / pyver / "Scripts")
    except Exception:
        pass

    prepend: list[str] = []
    for path in candidates:
        if not path.is_dir():
            continue
        text = str(path)
        if text in parts or text in prepend:
            continue
        prepend.append(text)

    if not prepend:
        return
    os.environ["PATH"] = os.pathsep.join(prepend + ([current] if current else []))


def _discover_renderdoc_paths() -> list[Path]:
    root = _project_root()
    repo = _repo_root()
    candidates: list[Path] = []
    for base in (root, repo):
        candidates.extend(
            [
                base / "library" / "renderdoc" / "x64" / "Development" / "pymodules",
                base / "library" / "renderdoc" / "x64" / "Release" / "pymodules",
                base / "library" / "renderdoc" / "Win32" / "Development" / "pymodules",
                base / "library" / "renderdoc" / "Win32" / "Release" / "pymodules",
                base / "x64" / "Development" / "pymodules",
                base / "x64" / "Release" / "pymodules",
            ],
        )
    deduped: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _find_renderdoc_path() -> Optional[str]:
    for path in _discover_renderdoc_paths():
        if (path / _candidate_renderdoc_module_name()).is_file():
            return str(path)
    return None


def _normalize_renderdoc_path(value: str) -> Optional[str]:
    raw = value.strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        if candidate.name.lower() in {"renderdoc.pyd", "renderdoc.so"}:
            candidate = candidate.parent
        else:
            return None
    if not candidate.is_dir():
        return None
    if (candidate / _candidate_renderdoc_module_name()).is_file():
        return str(candidate)
    return None


def _resolve_renderdoc_path(interactive: bool) -> Optional[str]:
    env_path = os.environ.get("RDX_RENDERDOC_PATH", "").strip()
    if env_path:
        normalized = _normalize_renderdoc_path(env_path)
        if normalized:
            os.environ["RDX_RENDERDOC_PATH"] = normalized
            return normalized
        if interactive:
            print(f"[RDX] Invalid RDX_RENDERDOC_PATH: {env_path}")

    found = _find_renderdoc_path()
    if found:
        os.environ["RDX_RENDERDOC_PATH"] = found
        return found

    if interactive:
        print("[RDX] I can help you locate RenderDoc Python module, but I failed to auto-detect it.")
        print("[RDX] Please set RDX_RENDERDOC_PATH to a directory that contains renderdoc.pyd.")
        candidates = [str(p / ("renderdoc.pyd" if os.name == "nt" else "renderdoc.so")) for p in _discover_renderdoc_paths()]
        for item in candidates:
            print(f"[RDX]   - {item}")
    else:
        print("[RDX] RDX_RENDERDOC_PATH not set and auto-detect failed.")
        print("[RDX] Add renderdoc.pyd directory to RDX_RENDERDOC_PATH.")

    return None


def _local_config_path() -> Path:
    return Path(__file__).resolve().parent / ".rdx_mcp.json"


def _load_local_config() -> dict[str, Any]:
    path = _local_config_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _save_local_config(payload: dict[str, Any]) -> None:
    path = _local_config_path()
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _get_local_rdc_dirs() -> list[str]:
    value = _load_local_config().get("rdc_dirs", [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return _split_dirs(value)
    return []


def _set_local_rdc_dirs(rdc_dirs: list[str]) -> None:
    cfg = _load_local_config()
    cfg["rdc_dirs"] = rdc_dirs
    _save_local_config(cfg)


def _split_dirs(value: str) -> list[str]:
    if not value:
        return []
    if ";" in value:
        parts = value.split(";")
    elif "|" in value:
        parts = value.split("|")
    elif "\n" in value:
        parts = value.splitlines()
    else:
        parts = [value]
    return [item.strip() for item in parts if item.strip()]


def _get_rdc_dirs() -> list[str]:
    env_dirs = os.environ.get("RDX_RDC_DIRS", "").strip()
    if env_dirs:
        return _split_dirs(env_dirs)

    local_dirs = _get_local_rdc_dirs()
    if local_dirs:
        return local_dirs

    if _is_interactive():
        raw = _read_input("[RDX] Optional: set RDX_RDC_DIRS (use ';' separated). Empty to skip: ")
        text = raw.strip() if raw is not None else ""
        if text:
            dirs = _split_dirs(text)
            _set_local_rdc_dirs(dirs)
            return dirs
    return []


def _pick_port(preferred: int = DEFAULT_PORT) -> int:
    sock = socket.socket()
    try:
        sock.bind(("", preferred))
        return preferred
    except OSError:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass

    sock = socket.socket()
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _get_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return ""
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _is_private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _is_public_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return True


def _find_ngrok() -> Optional[str]:
    binary = which("ngrok")
    if binary:
        return binary
    script_dir = Path(__file__).resolve().parent
    repo_root = _repo_root()
    for candidate in (
        script_dir / "ngrok.exe",
        script_dir / "bin" / "ngrok.exe",
        script_dir / "tools" / "ngrok.exe",
        repo_root / "ngrok.exe",
        repo_root / "bin" / "ngrok.exe",
        repo_root / "tools" / "ngrok.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _read_ngrok_config_token() -> str:
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
                pieces = line.split(":", 1)
                if len(pieces) == 2:
                    token = pieces[1].strip().strip("'\"")
                    if token:
                        return token
    return ""


def _resolve_ngrok_token(interactive: bool) -> str:
    env_token = os.environ.get("NGROK_AUTHTOKEN", "").strip()
    if env_token:
        return env_token

    local_token = _load_local_config().get("ngrok_authtoken", "").strip()
    if isinstance(local_token, str) and local_token:
        return local_token.strip()

    file_token = _read_ngrok_config_token()
    if file_token:
        return file_token

    if not interactive:
        return ""

    raw = _read_input("[RDX] Enter ngrok token (empty to skip): ")
    token = raw.strip() if raw is not None else ""
    if not token:
        return ""

    cfg = _load_local_config()
    cfg["ngrok_authtoken"] = token
    _save_local_config(cfg)
    return token


def _fetch_tunnels() -> Optional[list[dict]]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as response:
            payload = json.load(response)
        tunnels = payload.get("tunnels", [])
        return tunnels if isinstance(tunnels, list) else []
    except Exception:
        return None


def _pick_public_url(tunnels: list[dict], port: int) -> str:
    def by_port(entry: dict) -> bool:
        cfg = entry.get("config", {})
        return f":{port}" in str(cfg.get("addr", ""))

    for tunnel in tunnels:
        if by_port(tunnel) and str(tunnel.get("proto", "")).lower() == "https":
            return str(tunnel.get("public_url", ""))
    for tunnel in tunnels:
        if by_port(tunnel):
            return str(tunnel.get("public_url", ""))
    for tunnel in tunnels:
        if str(tunnel.get("proto", "")).lower() == "https":
            return str(tunnel.get("public_url", ""))
    for tunnel in tunnels:
        value = str(tunnel.get("public_url", ""))
        if value:
            return value
    return ""


def _start_ngrok(port: int, token: str) -> str:
    binary = _find_ngrok()
    if not binary:
        raise RuntimeError("ngrok not found")

    existing = _fetch_tunnels()
    if existing:
        url = _pick_public_url(existing, port)
        if url:
            return url
        raise RuntimeError("ngrok is running but no tunnel matches selected port.")

    env = os.environ.copy()
    if token and not env.get("NGROK_AUTHTOKEN"):
        env["NGROK_AUTHTOKEN"] = token

    creation = 0x08000000 if os.name == "nt" else 0
    subprocess.Popen(
        [binary, "http", f"127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
        env=env,
    )

    for _ in range(30):
        existing = _fetch_tunnels()
        if existing:
            url = _pick_public_url(existing, port)
            if url:
                return url
        time.sleep(1)

    raise RuntimeError("ngrok did not become ready in time")


def _install_uv(interactive: bool) -> bool:
    if _has_uv():
        return True

    install_cmds = [
        ["winget", "install", "--id", "astral-sh.uv", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
    ]
    if _ensure_pip():
        install_cmds.append([sys.executable, "-m", "pip", "install", "--user", "uv"])

    if not interactive:
        _print_help_commands([_to_command_repr(cmd) for cmd in install_cmds])
        print("[RDX] uv is optional. Continue without it.")
        return False

    print("[RDX] Missing uv. I will try to install it automatically.")

    for cmd in install_cmds:
        if _run_command(cmd) == 0:
            _prepend_user_scripts_to_path()
            if _has_uv():
                print("[RDX] uv installation completed.")
                return True

    _print_help_commands([_to_command_repr(cmd) for cmd in install_cmds])
    return False


def _install_ngrok(interactive: bool) -> bool:
    if _find_ngrok():
        return True

    install_cmds = [
        ["winget", "install", "ngrok.ngrok", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"],
    ]
    if not interactive:
        _print_help_commands([_to_command_repr(cmd) for cmd in install_cmds])
        print("[RDX] You can download ngrok from https://ngrok.com/download and add ngrok.exe to PATH.")
        return False

    print("[RDX] I can help install ngrok for INTERNET mode.")
    if not _ask_yes_no("Install ngrok now?", default=True):
        return False

    for cmd in install_cmds:
        if _run_command(cmd) == 0:
            return True

    _print_help_commands([_to_command_repr(cmd) for cmd in install_cmds])
    print("[RDX] Manual: download ngrok from https://ngrok.com/download and add ngrok.exe to PATH.")
    return False


def _missing_dependencies() -> list[str]:
    missing = []
    for package, import_name in REQUIRED_DEPENDENCIES:
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)
    return missing


def ensure_environment(interactive: bool, *, require_internet: bool = False) -> bool:
    required_commands: list[str] = []
    optional_commands: list[str] = []
    if which("python") is None and which("py") is None:
        print("[RDX] Python runtime not found. Install Python 3.10+ first.")
        return False

    deps = _missing_dependencies()
    if deps:
        if not _ensure_pip():
            print("[RDX] pip not available in current Python runtime.")
            if interactive:
                print("[RDX] Suggested: python -m ensurepip --upgrade")
            else:
                _print_help_commands([f"{sys.executable} -m ensurepip --upgrade"])
            return False
        print(f"[RDX] Missing required Python packages: {', '.join(deps)}")
        command = _to_command_repr([sys.executable, "-m", "pip", "install", "--user", *deps])
        if interactive:
            if _ask_yes_no("I can help install them now, continue?", default=True):
                if _run_command([sys.executable, "-m", "pip", "install", "--user", *deps]) != 0:
                    print("[RDX] pip install command failed.")
                    _print_help_commands([command])
                    return False
            else:
                _print_help_commands([command])
                return False
        else:
            required_commands.append(command)

    if not _install_uv(interactive):
        optional_commands.extend(
            [
                _to_command_repr(["winget", "install", "--id", "astral-sh.uv", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]),
                _to_command_repr([sys.executable, "-m", "pip", "install", "--user", "uv"]),
            ],
        )

    if require_internet:
        ngrok_ok = _install_ngrok(interactive)
        if not ngrok_ok:
            return False

    if required_commands:
        _print_help_commands(required_commands)
        return False

    if optional_commands:
        if interactive:
            print("[RDX] uv is optional. Continue without it.")
        else:
            print("[RDX] uv is optional. Install command list:")
        _print_help_commands(optional_commands)

    return True


def _normalize_transport(mode: Optional[str], requested: Optional[str]) -> str:
    if requested is None:
        return "streamable-http" if mode == "internet" else "sse"
    if requested == "http":
        return "streamable-http"
    if requested in {"stdio", "sse", "streamable-http"}:
        return requested
    return "sse" if mode != "internet" else "streamable-http"


def _build_launch_env_lan(transport: str, interactive: bool) -> Dict[str, str]:
    lan_ip = _get_lan_ip()
    if not lan_ip:
        lan_ip = DEFAULT_HOST
    port = _pick_port(DEFAULT_PORT)

    if not _is_private_ip(lan_ip):
        print(f"[RDX] WARN: detected LAN IP looks unusual: {lan_ip}")

    end = "/sse" if transport == "sse" else "/mcp"
    env = {
        "RDX_PORT": str(port),
        "LAN_IP": lan_ip,
        "RDX_SSE_HOST": "0.0.0.0",
        "RDX_SSE_PORT": str(port),
        "RDX_ARGS": f"--transport {transport} --host 0.0.0.0 --port {port}",
        "MANUS_TRANSPORT": "SSE" if transport == "sse" else "HTTP",
        "MANUS_URL": f"http://{lan_ip}:{port}{end}",
    }

    rdc_dirs = _get_rdc_dirs()
    if rdc_dirs:
        env["RDX_RDC_DIRS"] = ";".join(rdc_dirs)
    return env


def _build_launch_env_internet(transport: str, interactive: bool) -> Dict[str, str]:
    token = _resolve_ngrok_token(interactive)
    if not _find_ngrok():
        raise RuntimeError("ngrok not found. Install or place ngrok.exe in PATH.")
    if not token:
        if interactive:
            print("[RDX] No ngrok token detected; trying anonymous launch. This may require a token in some regions.")
        else:
            print("[RDX] No ngrok token found; anonymous launch requested.")

    port = _pick_port(DEFAULT_PORT)
    public_url = _start_ngrok(port, token)
    parsed = urlparse(public_url)
    if not parsed.hostname or not _is_public_host(parsed.hostname):
        raise RuntimeError(f"ngrok public URL is invalid: {public_url}")

    end = "/sse" if transport == "sse" else "/mcp"
    manuscript_url = f"{public_url.rstrip('/')}{end}"
    env = {
        "RDX_PORT": str(port),
        "LAN_IP": _get_lan_ip() or DEFAULT_HOST,
        "RDX_SSE_HOST": "127.0.0.1",
        "RDX_SSE_PORT": str(port),
        "RDX_ARGS": f"--transport {transport} --host 127.0.0.1 --port {port}",
        "MANUS_TRANSPORT": "SSE" if transport == "sse" else "HTTP",
        "MANUS_URL": manuscript_url,
        "RDX_ALLOWED_HOSTS": f"{parsed.hostname},{parsed.hostname}:*,127.0.0.1:*,localhost:*",
        "RDX_ALLOWED_ORIGINS": f"https://{parsed.hostname},http://{parsed.hostname},https://{parsed.hostname}:*,http://{parsed.hostname}:*",
    }

    rdc_dirs = _get_rdc_dirs()
    if rdc_dirs:
        env["RDX_RDC_DIRS"] = ";".join(rdc_dirs)

    print(f"[RDX] INTERNET URL: {manuscript_url}")
    return env


def _write_env_file(path: str, values: Dict[str, str]) -> None:
    out = [f"set {key}={value}" for key, value in values.items() if value is not None]
    Path(path).write_text("\n".join(out), encoding="utf-8")


def _build_launch_env_direct(parsed: argparse.Namespace, extra_args: list[str]) -> Dict[str, str]:
    parsed_extra = _parse_extra_args(extra_args)
    transport = _normalize_transport(None, parsed.transport or parsed_extra.get("transport"))
    host = (
        parsed.host
        or parsed_extra.get("host")
        or os.environ.get("RDX_SSE_HOST")
        or DEFAULT_HOST
    )
    raw_port = parsed.port or parsed_extra.get("port") or os.environ.get("RDX_SSE_PORT") or str(DEFAULT_PORT)
    try:
        port = int(str(raw_port))
    except Exception:
        port = DEFAULT_PORT

    env = {
        "RDX_PORT": str(port),
        "RDX_SSE_HOST": host,
        "RDX_SSE_PORT": str(port),
        "RDX_ARGS": f"--transport {transport} --host {host} --port {port}",
        "RDX_LOG_LEVEL": parsed.log_level or parsed_extra.get("log_level") or os.environ.get("RDX_LOG_LEVEL", "INFO"),
        "MANUS_TRANSPORT": "SSE" if transport == "sse" else "HTTP" if transport == "streamable-http" else "STDIO",
    }
    return env


def _parse_extra_args(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--host", "--port", "--transport", "--log-level"}:
            if i + 1 < len(argv):
                out[arg.lstrip("-")] = argv[i + 1]
                i += 2
                continue
            print(f"[RDX] WARN: {arg} requires a value.")
        i += 1
    return out


def _ensure_pip() -> bool:
    try:
        return subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            check=False,
        ).returncode == 0
    except Exception:
        return False


def _apply_runtime_env(env: Dict[str, str]) -> None:
    for key, value in env.items():
        if value is None:
            continue
        os.environ[key] = value


def _start_server(transport: str, *, interactive: bool) -> int:
    if not _resolve_renderdoc_path(interactive=interactive):
        print("[RDX] Startup blocked: RenderDoc path not resolved.")
        print("[RDX] Please set RDX_RENDERDOC_PATH to the folder containing renderdoc.pyd.")
        return 1

    _apply_runtime_env({"RDX_LOG_LEVEL": os.environ.get("RDX_LOG_LEVEL", "INFO").upper()})
    try:
        from rdx.server import main, main_sse, main_streamable_http
    except Exception as exc:
        print(f"[RDX] Failed to import rdx.server: {exc}")
        return 1

    if transport == "sse":
        main_sse()
    elif transport == "streamable-http":
        main_streamable_http()
    else:
        main()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RDX-MCP unified launcher")
    parser.add_argument("--ensure-env", action="store_true", help="Only validate/install prerequisite tooling.")
    parser.add_argument("--mode", choices=["lan", "internet"], help="MCP mode for launch.")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http", "http"], help="Transport mode.")
    parser.add_argument("--host", help="Override host (direct mode only).")
    parser.add_argument("--port", type=int, help="Override port (direct mode only).")
    parser.add_argument("--log-level", dest="log_level", help="Set RDX_LOG_LEVEL.")
    parser.add_argument("--prepare-rdc", action="store_true", help="Prepare RDX_RDC_DIRS and exit.")
    parser.add_argument("--env", help="Write env vars to a .bat file.")
    parser.add_argument("--non-interactive", action="store_true", help="No prompts; print command list only.")
    return parser


def main() -> int:
    parser = _build_parser()
    parsed, extra = parser.parse_known_args()
    if parsed.non_interactive:
        os.environ["RDX_NON_INTERACTIVE"] = "1"
    interactive = _is_interactive() and not parsed.non_interactive

    if parsed.ensure_env:
        require_internet = parsed.mode == "internet"
        return 0 if ensure_environment(interactive, require_internet=require_internet) else 1

    if parsed.prepare_rdc:
        rdc_dirs = _get_rdc_dirs()
        if parsed.env:
            _write_env_file(parsed.env, {"RDX_RDC_DIRS": ";".join(rdc_dirs)})
        else:
            os.environ["RDX_RDC_DIRS"] = ";".join(rdc_dirs)
        print(f"[RDX] RDX_RDC_DIRS={'(empty)' if not rdc_dirs else ';'.join(rdc_dirs)}")
        return 0

    if not ensure_environment(interactive, require_internet=(parsed.mode == "internet")):
        return 1

    if parsed.mode:
        transport = _normalize_transport(parsed.mode, parsed.transport)
        if transport == "streamable-http" and not (parsed.transport and parsed.transport != "http"):
            pass
        env = (
            _build_launch_env_internet(transport, interactive)
            if parsed.mode == "internet"
            else _build_launch_env_lan(transport, interactive)
        )
        env["MANUS_TRANSPORT"] = "HTTP" if transport == "streamable-http" else "SSE"
        env["RDX_LOG_LEVEL"] = parsed.log_level or os.environ.get("RDX_LOG_LEVEL", "INFO").upper()

        if parsed.env:
            _write_env_file(parsed.env, env)
            print(f"[RDX] Wrote launch env file: {parsed.env}")

        _apply_runtime_env(env)
        return _start_server(transport, interactive=interactive)

    env = _build_launch_env_direct(parsed, extra)
    env["RDX_ARGS"] = env.get("RDX_ARGS", "").strip()
    if parsed.env:
        _write_env_file(parsed.env, env)
    _apply_runtime_env(env)
    return _start_server(_normalize_transport(None, parsed.transport), interactive=interactive)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        log_path = _project_root() / "rdx_launcher_crash.log"
        trace = traceback.format_exc()
        print("[RDX] Unexpected launcher crash. See log for details:")
        print(f"[RDX]   {log_path}")
        print(trace)
        try:
            log_path.write_text(trace, encoding="utf-8")
        except Exception:
            pass
        raise SystemExit(1)
