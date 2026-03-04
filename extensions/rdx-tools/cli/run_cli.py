#!/usr/bin/env python3
"""Standalone CLI launcher for rdx-tools."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _tools_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _init_pythonpath() -> None:
    root = _tools_root()
    os.environ.setdefault("RDX_TOOLS_ROOT", str(root))
    core = root / "core"
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))


def main() -> int:
    _init_pythonpath()
    from rdx.runtime_paths import binaries_root, ensure_runtime_dirs, pymodules_dir

    ensure_runtime_dirs()
    os.environ.setdefault("RDX_RENDERDOC_PATH", str(pymodules_dir()))
    os.environ.setdefault("RDX_RUNTIME_DLL_DIR", str(binaries_root()))

    from rdx import cli as rdx_cli

    rdx_cli.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
