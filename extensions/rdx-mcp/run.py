#!/usr/bin/env python3
"""
RDX-MCP: RenderDoc GPU Debug MCP Server

运行 MCP server 的入口。

Usage:
    # stdio transport（默认，适用于 Claude Desktop / Claude Code / agent integration）
    python run.py

    # SSE transport（用于 web clients）
    python run.py --transport sse --host 127.0.0.1 --port 8765

Environment variables:
    RDX_RENDERDOC_PATH  - renderdoc Python module 所在目录路径
    RDX_ARTIFACT_STORE  - artifact 存储目录路径（默认：./rdx_artifacts）
    RDX_DATA_DIR        - DB 数据目录路径（默认：./rdx_data）
    RDX_REPORT_DIR      - report 输出目录路径（默认：./rdx_reports）
    RDX_LOG_LEVEL       - Logging level（默认：INFO）
    RDX_GPU_VENDOR      - 首选 GPU vendor：nvidia, amd, intel, arm, any
    RDX_SPIRV_TOOLS_PATH - SPIRV-Tools binaries 路径
    RDX_HEADLESS        - 强制 headless mode：1/true/yes
    RDX_SSE_HOST        - SSE server host（默认：127.0.0.1）
    RDX_SSE_PORT        - SSE server port（默认：8765）
"""

import argparse
import importlib
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def setup_renderdoc_path() -> None:
    rdoc_path = os.environ.get("RDX_RENDERDOC_PATH")
    if rdoc_path and rdoc_path not in sys.path:
        sys.path.insert(0, rdoc_path)

_dll_dir_handles: list[object] = []


def _add_dll_dir(path: str) -> None:
    """
    On Windows (Python 3.8+), extension-module DLL search is restricted and may ignore PATH.
    `os.add_dll_directory()` is the supported way to add dependent DLL directories.
    """
    if os.name != "nt":
        return
    if not path:
        return
    try:
        handle = os.add_dll_directory(path)  # type: ignore[attr-defined]
    except (AttributeError, FileNotFoundError, OSError):
        return
    _dll_dir_handles.append(handle)


def setup_renderdoc_dll_dirs() -> None:
    """
    Ensure directories containing RenderDoc and its dependent DLLs are visible to the loader.

    Typical layout:
      RDX_RENDERDOC_PATH = <repo>\\x64\\Development\\pymodules
      renderdoc.dll      = <repo>\\x64\\Development\\renderdoc.dll

    We add both `<...>\\pymodules` and its parent directory.
    """
    if os.name != "nt":
        return

    rdoc_path = os.environ.get("RDX_RENDERDOC_PATH")
    if not rdoc_path:
        return

    pyd_dir = Path(rdoc_path)
    _add_dll_dir(str(pyd_dir))

    parent = pyd_dir.parent
    if parent.exists():
        _add_dll_dir(str(parent))


def _extract_pe_dll_names(path: Path) -> list[str]:
    """
    Best-effort: extract referenced DLL names from a PE file by scanning ASCII strings.

    This isn't as accurate as parsing the PE import table, but is good enough to surface
    common missing deps such as `python36.dll` / `python314.dll`.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []

    pattern = re.compile(rb"[A-Za-z0-9_.-]{3,64}\.dll", re.IGNORECASE)
    raw = [m.group(0).decode("ascii", "ignore") for m in pattern.finditer(data)]

    seen: set[str] = set()
    dlls: list[str] = []
    for name in raw:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        dlls.append(name)
    return dlls


def ensure_renderdoc_available(logger: logging.Logger) -> None:
    rdoc_path = os.environ.get("RDX_RENDERDOC_PATH")
    module_name = "renderdoc.pyd" if os.name == "nt" else "renderdoc.so"

    spec = importlib.util.find_spec("renderdoc")
    if spec is None:
        if rdoc_path:
            expected = Path(rdoc_path) / module_name
            logger.error("RenderDoc python module not found at %s", expected)
        else:
            logger.error("RenderDoc python module not found and RDX_RENDERDOC_PATH is not set.")

        repo_root = Path(__file__).resolve().parents[2]
        logger.error("RDX-MCP requires a local RenderDoc source build to provide MCP tools.")
        logger.error(
            "Build renderdoc.sln -> pyrenderdoc_module (x64 Development) to generate %s",
            repo_root / "x64" / "Development" / "pymodules" / module_name,
        )
        raise SystemExit(1)

    try:
        rd = importlib.import_module("renderdoc")
    except Exception as exc:
        expected = Path(rdoc_path) / module_name if rdoc_path else None
        if expected is not None:
            logger.error("RenderDoc module exists at %s but failed to import: %s", expected, exc)
        else:
            logger.error("Failed to import RenderDoc module: %s", exc)

        if os.name == "nt" and expected is not None and expected.exists():
            dlls = _extract_pe_dll_names(expected)
            python_dlls = [
                d for d in dlls
                if d.lower().startswith("python") and d.lower().endswith(".dll")
            ]
            if python_dlls:
                logger.error("renderdoc.pyd references Python DLL(s): %s", ", ".join(python_dlls))

            logger.error(
                "Tip: on Windows (Python 3.8+), PATH may be ignored for dependent DLL loading. "
                "RDX-MCP calls os.add_dll_directory() for RDX_RENDERDOC_PATH and its parent, "
                "but if you run custom launchers ensure those env vars are set before import."
            )

        raise SystemExit(1) from exc

    if getattr(rd, "__file__", None) is None or not hasattr(rd, "ResultCode"):
        loaded_from = getattr(rd, "__file__", None)
        if loaded_from is None:
            loaded_from = f"namespace package paths={getattr(rd, '__path__', None)}"
        logger.error(
            "`import renderdoc` resolved to %s, but it doesn't look like RenderDoc bindings.",
            loaded_from,
        )
        raise SystemExit(1)

    return


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RDX-MCP: RenderDoc GPU Debug MCP Server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("RDX_SSE_HOST", "127.0.0.1"),
        help="SSE server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("RDX_SSE_PORT", "8765")),
        help="SSE server port (default: 8765)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("RDX_LOG_LEVEL", "INFO"),
        help="Logging level (default: INFO)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)
    setup_renderdoc_path()
    setup_renderdoc_dll_dirs()

    logger = logging.getLogger("rdx-mcp")
    ensure_renderdoc_available(logger)
    logger.info("Starting RDX-MCP server (transport=%s)", args.transport)

    transport = args.transport
    if transport == "http":
        transport = "streamable-http"

    if transport == "stdio":
        from rdx.server import main as server_main
        server_main()
    elif transport == "sse":
        os.environ["RDX_SSE_HOST"] = args.host
        os.environ["RDX_SSE_PORT"] = str(args.port)
        from rdx.server import main_sse
        main_sse()
    elif transport == "streamable-http":
        os.environ["RDX_SSE_HOST"] = args.host
        os.environ["RDX_SSE_PORT"] = str(args.port)
        from rdx.server import main_streamable_http
        main_streamable_http()


if __name__ == "__main__":
    main()
