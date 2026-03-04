# Troubleshooting

## `renderdoc.pyd` import failed

- Confirm `binaries/windows/x64/pymodules/renderdoc.pyd` exists.
- Confirm `binaries/windows/x64/renderdoc.dll` exists.
- Run `rdx.bat --non-interactive mcp --ensure-env`.

## MCP stdio client cannot initialize

- Make sure launcher prints no extra stdout in stdio mode:
  - `python mcp/run_mcp.py --transport stdio`

## Empty bindings in tools

- Open capture and set frame first:
  - `rd.capture.open_file`
  - `rd.capture.open_replay`
  - `rd.replay.set_frame`
- Verify on a draw event, not frame root.
