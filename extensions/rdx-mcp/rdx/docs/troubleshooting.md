# 常见问题排查（Troubleshooting）

本页以“先定位、再收敛”的方式列出常见故障点与对应的处理建议。

## `ImportError: No module named 'renderdoc'`

**现象**

- 启动后在调用任意需要 RenderDoc 的工具（例如 `rd.capture.open`、`rd.output.render`、`rd.debug.pixel`）时报错，或日志提示 renderdoc module 不可用。

**原因**

- RenderDoc 的 Python module 没有在当前进程的 `sys.path` 中（见 `extensions/rdx-mcp/rdx/core/render_service.py` 的错误信息）。
- RenderDoc 源码未编译完成，导致 `renderdoc.pyd` 不存在或路径不在默认输出目录。

**处理**

- 设置 `RDX_RENDERDOC_PATH` 指向 RenderDoc 的 Python module 所在目录（`run.py` 会把它加入 `sys.path`）。
- 确认该目录下确实能 `import renderdoc`（可在同环境下手动验证）。
- 在 Windows 上编译 `renderdoc.sln` 的 `pyrenderdoc_module`（`x64` + `Development`），默认输出：
  - `x64\Development\pymodules\renderdoc.pyd`
  - `x64\Development\renderdoc.dll`

## `ImportError: DLL load failed while importing renderdoc: The specified module could not be found.`

**现象**

- MCP 能启动、客户端也能“看到 tools”，但一调用需要 RenderDoc 的工具就报上述错误。

**原因（常见）**

- **编译时用错 Python 版本**：`renderdoc.pyd` 链接的还是 `python36.dll`，但你运行 MCP 用的是 3.10+/3.11+/3.14…
- **Windows DLL 搜索路径问题（Python 3.8+）**：即使版本匹配，`renderdoc.pyd` 依赖的 `renderdoc.dll`（以及同目录的其它 DLL）也可能因为安全策略导致 **PATH 不生效**，需要用 `os.add_dll_directory()` 显式加入 DLL 目录。

**处理**

- 确认 `renderdoc.pyd` 依赖的 Python DLL（示例会输出 `python314.dll` / `python36.dll` 等）：
  - `py -3 -c "import pathlib,re; b=pathlib.Path(r'x64\\Development\\pymodules\\renderdoc.pyd').read_bytes(); print(sorted(set(m.group(0).decode('ascii','ignore') for m in re.finditer(rb'python\\d{2,3}\\.dll', b, re.I))))"`
- 确认你是通过 `extensions/rdx-mcp/run.py` 启动（它会把 `RDX_RENDERDOC_PATH` 及其父目录通过 `os.add_dll_directory()` 加入 DLL 搜索路径）。
- 若你自行启动/嵌入 Python，请在导入前显式加 DLL 目录：
  - `py -3 -c "import os; os.add_dll_directory(r'x64\\Development'); os.add_dll_directory(r'x64\\Development\\pymodules'); import sys; sys.path.insert(0,r'x64\\Development\\pymodules'); import renderdoc"`

## SSE 监听不符合预期（host/port）

**现象**

- 传了 `--host/--port` 但实际监听地址不对，或端口冲突。

**要点**

- `extensions/rdx-mcp/run.py` 会把命令行的 `--host/--port` 写回环境变量 `RDX_SSE_HOST` / `RDX_SSE_PORT`。
- `extensions/rdx-mcp/rdx/server.py` 的 `main_sse()` 会从 `RDX_SSE_HOST` / `RDX_SSE_PORT` 读取最终监听地址。

**处理**

- 检查环境变量是否被其他启动脚本覆盖。
- 端口冲突时更换 `--port` 或终止占用进程。

## 远程客户端（Manus 等）无法连接 SSE

**现象**

- Manus 提示无法访问 `http://192.168.x.x:PORT/sse`，或连接超时 / OAuth 失败。

**原因**

- `192.168.* / 10.* / 172.16.*` 属于内网地址，远程沙箱无法直接访问你的局域网。

**处理**

- 使用 `run.bat` 的 **INTERNET** 模式，自动启用 ngrok 并获得公网 URL。
- 确保已执行：`ngrok config add-authtoken <TOKEN>`，且 `ngrok` 在 PATH 中。
- 连接时使用 `run.bat` 输出并复制的 URL（形如 `https://xxxx.ngrok-free.app/sse`）。

**补充**

- INTERNET 模式支持 **HTTP/streamable**（`/mcp`）或 **SSE**（`/sse`）。HTTP 更稳定；一键运行会提示选择。Manus 中请选择 **HTTP** 并粘贴 `https://.../mcp`。

### 报错 `HTTP 421` / `Invalid Host header`

**现象**

- 日志出现 `Invalid Host header: <ngrok域名>`，并返回 `HTTP 421`。

**原因**

- MCP 的 DNS rebinding 保护拒绝了 ngrok 域名的 Host 头。

**处理**

- 重新运行 `run.bat` 的 **INTERNET** 模式（脚本会自动注入 `RDX_ALLOWED_HOSTS`）。

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
