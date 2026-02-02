#!/usr/bin/env python3
"""
RDX-MCP: RenderDoc GPU Debug MCP Server

Entry point for running the MCP server.

Usage:
    # stdio transport (default, for Claude Desktop / Claude Code / agent integration)
    python run.py

    # SSE transport (for web clients)
    python run.py --transport sse --host 127.0.0.1 --port 8765

Environment variables:
    RDX_RENDERDOC_PATH  - Path to directory containing renderdoc Python module
    RDX_ARTIFACT_STORE  - Path to artifact storage directory (default: ./rdx_artifacts)
    RDX_DATA_DIR        - Path to data directory for DBs (default: ./rdx_data)
    RDX_REPORT_DIR      - Path to report output directory (default: ./rdx_reports)
    RDX_LOG_LEVEL       - Logging level (default: INFO)
    RDX_GPU_VENDOR      - Preferred GPU vendor: nvidia, amd, intel, arm, any
    RDX_SPIRV_TOOLS_PATH - Path to SPIRV-Tools binaries
    RDX_HEADLESS        - Force headless mode: 1/true/yes
    RDX_SSE_HOST        - SSE server host (default: 127.0.0.1)
    RDX_SSE_PORT        - SSE server port (default: 8765)
"""

import argparse
import logging
import os
import sys


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RDX-MCP: RenderDoc GPU Debug MCP Server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
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
    logger.info("Starting RDX-MCP server (transport=%s)", args.transport)

    if args.transport == "stdio":
        from rdx.server import main as server_main
        server_main()
    elif args.transport == "sse":
        os.environ["RDX_SSE_HOST"] = args.host
        os.environ["RDX_SSE_PORT"] = str(args.port)
        from rdx.server import main_sse
        main_sse()


if __name__ == "__main__":
    main()
