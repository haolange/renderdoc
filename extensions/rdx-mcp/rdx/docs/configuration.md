# 配置与目录结构

本页聚焦：**如何把 RDX-MCP 跑稳**（路径、目录、日志、KB 索引），以及常见配置项在当前实现中“到底在哪里被读取”。

## 环境变量一览（按当前实现）

> **约定**：下表以 `extensions/rdx-mcp/rdx/server.py`（server 生命周期）与 `extensions/rdx-mcp/run.py`（启动入口）为准；`extensions/rdx-mcp/rdx/config.py` 会读取一组环境变量并生成 `RdxConfig`，但并非所有字段都会在 server 中被消费。

| 变量 | 作用 | 默认值（代码） | 读取位置（代码） |
|---|---|---|---|
| `RDX_RENDERDOC_PATH` | 将 RenderDoc Python module 目录加入 `sys.path`（解决 `import renderdoc`） | 无 | `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/config.py` |
| `RDX_LOG_LEVEL` | 日志级别 | `INFO` | `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/server.py`、`extensions/rdx-mcp/rdx/config.py` |
| `RDX_SSE_HOST` / `RDX_SSE_PORT` | SSE 监听地址 | `0.0.0.0` / `8765`（`main_sse`） | `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/server.py` |
| `RDX_ARTIFACT_DIR` | artifact 存储根目录（CAS 目录） | `/tmp/rdx-artifacts` | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_DB_DIR` | DB 根目录（fingerprints / KB 索引 SQLite） | `/tmp/rdx-db` | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_KB_INDEX_DIRS` | 启动时索引的目录列表（用于 `rd.kb.search`） | 空（不索引） | `extensions/rdx-mcp/rdx/server.py` |
| `RDX_ARTIFACT_STORE` | artifact 目录（进入 `RdxConfig`） | `./rdx_artifacts` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_DATA_DIR` | metadata/fingerprints 路径基准（进入 `RdxConfig`） | `./rdx_data` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_REPORT_DIR` | report 输出目录（进入 `RdxConfig`） | `./rdx_reports` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_GPU_VENDOR` | GPU vendor 偏好（进入 `RdxConfig`） | `any` | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_SPIRV_TOOLS_PATH` | SPIRV-Tools 路径（进入 `RdxConfig`） | 空 | `extensions/rdx-mcp/rdx/config.py` |
| `RDX_HEADLESS` | 强制 headless（进入 `RdxConfig`） | `true` | `extensions/rdx-mcp/rdx/config.py` |

### 重要差异：`RDX_ARTIFACT_DIR` vs `RDX_ARTIFACT_STORE`

- **artifact store 的实际根目录**：当前 server 在启动时使用 `RDX_ARTIFACT_DIR` 初始化 `ArtifactStore`（见 `extensions/rdx-mcp/rdx/server.py`）。
- **`RDX_ARTIFACT_STORE`**：会写入 `RdxConfig.artifact.store_path`，但目前 server 未将该字段用于初始化 `ArtifactStore`。

如果你只想“跑起来且所有 artifacts 都能落盘”，建议**优先设置 `RDX_ARTIFACT_DIR`**。

### Windows 下 `RDX_KB_INDEX_DIRS` 的注意点

当前实现使用 `:` 分隔多个目录（见 `extensions/rdx-mcp/rdx/server.py` 里 `split(":")`）。在 Windows 上盘符也包含 `:`（例如 `D:`），多目录配置会变得不直观。

**建议**

- **只配置一个目录**（最简单且最不踩坑），例如：`RDX_KB_INDEX_DIRS=D:\Work\Docs`。
- 如需多目录：优先在 WSL / 容器环境使用类 Unix 路径，或自行在代码侧改造分隔符（属于开发任务）。

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

### DB（fingerprints / KB 索引）

server 启动时会在 `RDX_DB_DIR` 下创建并使用：

- `fingerprints.db`（`FingerprintStore`）
- `kb_index.db`（`KBConnector`）

### 报告输出（Report Bundle）

`rd.report.build_bundle` 支持指定 `output_dir`；若不指定，默认输出到：

```
<RDX_ARTIFACT_DIR>/reports/<task_id>/
```

（见 `extensions/rdx-mcp/rdx/server.py` 中 `report_build_bundle` 的默认路径逻辑。）

## Patch / 工具链（概念说明）

`rd.patch.apply` 会在 replay 中获取 shader 的可编辑文本（反汇编/反编译结果），应用 `PatchOp` 后调用 RenderDoc 的 `BuildTargetShader` 触发重编译并热替换（见 `extensions/rdx-mcp/rdx/core/patch_engine.py`）。

**你需要关注**

- **可用编码/编译器**：取决于 capture 的 API、shader 目标与 RenderDoc 支持的 `BuildTargetShader` 路径。
- **阶段支持**：当前 patch engine 仅映射 `vs/hs/ds/gs/ps/cs`（不覆盖 mesh/amplification stages）。

若 patch 相关调用失败，优先阅读：`troubleshooting.md` 的“Patch 失败”小节。

