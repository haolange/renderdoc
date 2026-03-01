# 配置与目录结构

本页聚焦：**如何把 RDX-MCP 跑稳**（路径、目录、日志、网络传输安全），以及常见配置项在当前实现中“到底在哪里被读取”。

## 环境变量一览（按当前实现）

> **约定**：下表以 `extensions/rdx-mcp/rdx/server.py`（server 生命周期）与 `extensions/rdx-mcp/run.py`（启动入口）为准；`extensions/rdx-mcp/rdx/config.py` 会读取一组环境变量并生成 `RdxConfig`，但并非所有字段都会在 server 中被消费。

| 变量 | 作用 | 默认值（代码） | 读取位置（代码） |
|---|---|---|---|
| `RDX_RENDERDOC_PATH` | 将 RenderDoc Python module 目录加入 `sys.path`（解决 `import renderdoc`） | 无 | `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/config.py` |
| `RDX_LOG_LEVEL` | 日志级别 | `INFO` | `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/server.py`、`extensions/rdx-mcp/rdx/config.py` |
| `RDX_SSE_HOST` / `RDX_SSE_PORT` | SSE 监听地址 | `127.0.0.1` / `8765` | `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/server.py` |
| `RDX_ARTIFACT_DIR` | artifact 存储根目录（CAS 目录） | `./rdx_artifacts` | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_ALLOWED_HOSTS` / `RDX_ALLOWED_ORIGINS` | 允许的 Host/Origin（用于公网转发时放行） | 空（不限制） | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_ARTIFACT_STORE` | artifact 目录（进入 `RdxConfig`） | `./rdx_artifacts` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_DATA_DIR` | `RdxConfig` 数据目录（当前 server 未直接消费） | `./rdx_data` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_REPORT_DIR` | `RdxConfig` report 输出目录（当前 server 未直接消费） | `./rdx_reports` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_GPU_VENDOR` | GPU vendor 偏好（进入 `RdxConfig`） | `any` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_SPIRV_TOOLS_PATH` | SPIRV-Tools 路径（进入 `RdxConfig`） | 空 | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_HEADLESS` | 强制 headless（进入 `RdxConfig`） | `true` | `extensions/rdx-mcp/rdx/config.py` |

### 重要差异：`RDX_ARTIFACT_DIR` vs `RDX_ARTIFACT_STORE`

- **artifact store 的实际根目录**：当前 server 在启动时使用 `RDX_ARTIFACT_DIR` 初始化 `ArtifactStore`（见 `extensions/rdx-mcp/rdx/server.py`）。
- **`RDX_ARTIFACT_STORE`**：会写入 `RdxConfig.artifact.store_path`，但目前 server 未将该字段用于初始化 `ArtifactStore`。

如果你只想“跑起来且所有 artifacts 都能落盘”，建议**优先设置 `RDX_ARTIFACT_DIR`**。

## 目录结构与产物

### Artifact Store（CAS）

`ArtifactStore` 使用 SHA256 做内容寻址，落盘布局类似 git object storage（见 `extensions/rdx-mcp/rdx/utils/artifact_store.py`）：

```
<RDX_ARTIFACT_DIR>/
  <sha[:2]>/
    <sha[2:4]>/
      <sha256>
```

工具返回的 `ArtifactRef.uri` 使用 `rdx://` scheme，例如：

```
rdx://artifacts/ab/cd/abcdef0123...
```

### 导出文件（`rd.export.*` / 部分 `rd.macro.*`）

- 大多数导出类工具要求显式传入 `output_path` / `output_dir`，并在**运行 RDX-MCP 的机器**上写文件。
- 建议在 Windows 下使用绝对路径，或确保相对路径的工作目录可写。
