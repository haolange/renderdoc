# 常见问题排查（Troubleshooting）

本页以“先定位、再收敛”的方式列出常见故障点与对应的处理建议。

## `ImportError: No module named 'renderdoc'`

**现象**

- 启动后在调用任意需要 RenderDoc 的工具（例如 `rd.capture.open_file`、`rd.replay.set_frame`、`rd.export.screenshot`、`rd.debug.pixel_history`）时报错，或日志提示 renderdoc module 不可用。

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

## 状态变化点定位结果不稳定（`rd.macro.find_state_change_point`）

**常见原因**

- capture 内部存在非确定性（例如依赖未初始化内存、随机采样、时间相关输入），导致“同一 event 的输出/状态”在不同运行间不一致。
- `state_path/target_value` 选择不够稳定（例如引用会变化的动态数组项、或状态在多个相邻 event 内来回切换）。

**处理建议**

- 先用 `rd.event.search_actions` / `rd.event.get_action_tree` 把 `event_range` 缩小到可疑 marker/pass 周围。
- 对候选 `event_id` 导出证据验证稳定性：`rd.export.screenshot`、`rd.export.pipeline_state_json`、`rd.export.pixel_history_json`（必要时重复运行对比）。
- 若二分结果不可靠，改用 `search_policy='linear'` 或进一步缩小区间，再用 `rd.macro.compare_events_report` 对比前后事件差异。

## Shader 替换/热修复失败（`rd.shader.edit_and_replace`）

**典型症状**

- 返回 `success=false` / `error_message` 提示编译失败、stage 不支持、或替换后输出无变化。

**排查要点**

- 用 `rd.shader.get_source` / `rd.shader.get_disassembly` 确认目标 shader 可导出；必要时先用 `rd.shader.extract_binary` 获取原始二进制。
- 先用 `rd.shader.compile` 对修改后的代码做编译验证（同一 shader 模型/entry/stage）。
- 用最小变更验证链路（先改一行/加一条 guard），再逐步叠加复杂修改。
- 替换后用 `rd.shader.get_messages` 查看编译/替换日志；用 `rd.shader.list_replacements` 确认替换是否生效。

**处理建议**

- 用 `rd.macro.shader_hotfix_validate` 做“替换前/后”对比（可结合 `rd.export.screenshot` 保存证据）。
- 需要回滚时用 `rd.shader.revert_replacement`，确保后续实验环境干净。

## 报告/证据包生成失败（`rd.macro.build_bug_report_pack` / `rd.export.repro_bundle_zip`）

**现象**

- 输出路径缺文件、zip 为空、或报权限/路径相关错误。

**排查要点**

- 确认 `RDX_ARTIFACT_DIR` 与 `output_dir/output_path` 指向可写目录（Windows 下尽量避免需要管理员权限的路径）。
- 确认已成功打开 capture 并进入 replay（否则导出类工具缺少上下文）。

**处理**

- 优先使用 `rd.macro.build_bug_report_pack` 生成一份包含 repro bundle + 解释文本的包；或仅调用 `rd.export.repro_bundle_zip` / `rd.export.markdown_report` 输出最小证据。
- 若仍失败，记录 `error_message` 并将导出路径下已有 artifacts（如 `rd.export.pipeline_state_json`、`rd.export.event_tree_json`）一并提供，便于离线排查。
