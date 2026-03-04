# Quickstart

1. Validate environment:

```bat
rdx.bat --non-interactive mcp --ensure-env
```

2. Open capture with CLI:

```bat
rdx.bat --non-interactive cli capture open --file "C:\Users\a1824\Desktop\rdcFiles\TestRdc_Desktop.rdc" --frame-index 0
```

3. Start MCP stdio transport:

```bat
python mcp/run_mcp.py --transport stdio
```
