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
import importlib.util
import logging
import os
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


def ensure_renderdoc_available(logger: logging.Logger) -> None:
    if importlib.util.find_spec("renderdoc") is not None:
        return

    rdoc_path = os.environ.get("RDX_RENDERDOC_PATH")
    module_name = "renderdoc.pyd" if os.name == "nt" else "renderdoc.so"
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
