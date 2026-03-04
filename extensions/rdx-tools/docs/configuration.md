# Configuration

## Required runtime layout

- `binaries/windows/x64/renderdoc.dll`
- `binaries/windows/x64/renderdoc.json`
- `binaries/windows/x64/pymodules/renderdoc.pyd`

## Environment variables

- `RDX_TOOLS_ROOT` optional; auto-detected from launcher scripts.
- `RDX_RENDERDOC_PATH` optional override for `renderdoc.pyd` directory.
- `RDX_ARTIFACT_DIR` optional override for artifact output root.
- `RDX_LOG_LEVEL` optional logging level.

Default outputs are under `intermediate/`.
