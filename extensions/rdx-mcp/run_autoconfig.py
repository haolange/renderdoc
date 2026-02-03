#!/usr/bin/env python3
"""Compute local SSE settings for run.bat."""

from __future__ import annotations

import socket
import sys


def pick_port(preferred: int = 8765) -> int:
    """Pick preferred port if free; otherwise choose an ephemeral free port."""
    s = socket.socket()
    try:
        s.bind(("", preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
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


def main() -> None:
    port = pick_port()
    ip = get_lan_ip()
    lines = [f"set RDX_PORT={port}", f"set LAN_IP={ip}"]

    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
