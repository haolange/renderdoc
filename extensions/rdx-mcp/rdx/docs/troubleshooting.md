# 常见问题排查（Troubleshooting）

本页以“先定位、再收敛”的方式列出常见故障点与对应的处理建议。

## `ImportError: No module named 'renderdoc'`

**现象**

- 启动后在调用任意需要 RenderDoc 的工具（例如 `rd.capture.open`、`rd.output.render`、`rd.debug.pixel`）时报错，或日志提示 renderdoc module 不可用。

**原因**

- RenderDoc 的 Python module 没有在当前进程的 `sys.path` 中（见 `extensions/rdx-mcp/rdx/core/render_service.py` 的错误信息）。

**处理**

- 设置 `RDX_RENDERDOC_PATH` 指向 RenderDoc 的 Python module 所在目录（`run.py` 会把它加入 `sys.path`）。
- 确认该目录下确实能 `import renderdoc`（可在同环境下手动验证）。

## SSE 监听不符合预期（host/port）

**现象**

- 传了 `--host/--port` 但实际监听地址不对，或端口冲突。

**要点**

- `extensions/rdx-mcp/run.py` 会把命令行的 `--host/--port` 写回环境变量 `RDX_SSE_HOST` / `RDX_SSE_PORT`。
- `extensions/rdx-mcp/rdx/server.py` 的 `main_sse()` 会从 `RDX_SSE_HOST` / `RDX_SSE_PORT` 读取最终监听地址。

**处理**

- 检查环境变量是否被其他启动脚本覆盖。
- 端口冲突时更换 `--port` 或终止占用进程。

## `rd.event.bisect_first_bad` 结果不稳定/置信度低

**常见原因**

- verifier 对“好/坏”的判定不够稳定（例如阈值过严/过宽、对比参考不一致）。
- capture 内部存在非确定性（例如依赖未初始化内存、随机采样、时间相关输入）。

**处理建议**

- 使用更稳的 verifier（例如从 `naninf` 切到 `image_diff`，或调整阈值参数）。
- 缩小搜索区间：把 `range_lo/range_hi` 收敛到怀疑的 pass/marker 周围。
- 对同一 `event_id` 重复运行 verifier，确认其输出是否一致（作为上层编排的健壮性检查）。

## `rd.patch.apply` 失败

**典型症状**

- 返回 `patch.success=false`，或 `Shader build failed`，或提示 stage 不支持。

**排查要点**

- **stage 支持范围**：当前 patch engine 仅支持 `vs/hs/ds/gs/ps/cs`（见 `extensions/rdx-mcp/rdx/core/patch_engine.py` 的 stage 映射）。
- **编译器/编码支持**：patch 流程依赖 RenderDoc 的 `BuildTargetShader`，其可用性取决于 capture 的 API、shader 编码以及 RenderDoc 能否在当前环境完成编译。

**处理建议**

- 先通过 `rd.shader.export_artifacts` 确认该 stage 的 shader 能被正确导出（至少能得到可编辑的文本）。
- 缩小 patch 操作：先做最小变更（例如单条 guard）验证链路可用，再逐步叠加复杂 patch。
- 失败后及时 `rd.patch.revert`（如果 patch 已部分生效或你不确定状态），确保后续实验环境干净。

## `rd.kb.search` 没有结果 / 结果很差

**检查清单**

- 启动时是否配置了 `RDX_KB_INDEX_DIRS`（为空则不会索引任何目录）。
- 目标文件扩展名是否在可索引集合中（见 `extensions/rdx-mcp/rdx/knowledge/kb_connector.py` 的 `_INDEXABLE_EXTENSIONS`，包括 `.md/.txt/.h/.cpp/.usf/.ush/.py`）。
- Windows 多目录配置是否被 `:` 分隔规则影响（见 `configuration.md`）。

**改进建议**

- 将 query 写得更“可检索”：用更具体的标识符（函数名、shader 名、pass 名、资源 binding 关键字）。
- 用 `file_type` / `path_prefix` 做过滤，降低噪声。

## 报告生成失败（`rd.report.build_bundle`）

**现象**

- 返回 `REPORT_BUILD_ERROR` 或输出目录缺文件。

**排查要点**

- `task_state_json` 必须是完整且可被 `TaskState.model_validate(...)` 解析的 JSON 字符串（见 `extensions/rdx-mcp/rdx/server.py` 的 `report_build_bundle`）。
- 输出目录默认在 `<RDX_ARTIFACT_DIR>/reports/<task_id>/`，在 Windows 下建议显式设置一个可写路径（例如放到项目目录或用户目录下）。

**处理**

- 先将 `task_state_json` 保存到文件并用 Python 验证能否被 `TaskState` 反序列化（定位是 JSON 结构问题还是文件系统问题）。

