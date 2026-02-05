# 快速开始（Quickstart）

本页目标：**最短路径跑起来**，并能在 MCP 客户端中调用 `rd.pipeline.run_full_debug` 或按步骤调用工具。

## 前置条件

**必需**

- Python `>= 3.10`（见 `extensions/rdx-mcp/pyproject.toml`）。
- 需要本仓库的 RenderDoc 源码并完成本地编译，生成 `renderdoc.pyd` 与 `renderdoc.dll`（Windows 默认输出见下）。
- RenderDoc 的 Python module 可被导入：`import renderdoc`。
  - 常见做法：设置 `RDX_RENDERDOC_PATH`，将 RenderDoc 的 Python module 所在目录加入 `sys.path`（`extensions/rdx-mcp/run.py` 会读取它）。
  - Windows 下 `run.bat` 会自动探测默认输出布局（例如 `x64\Development\pymodules`），未命中时再手动设置即可。

**可选（按需）**

- 若你要用 `rd.patch.apply` 修改 SPIR-V/HLSL 等并触发重编译：可能需要额外的编译/工具链与 RenderDoc 支持的编码（详见 `configuration.md` 的“Patch/工具链”小节）。
- 若你要用 KB 检索（`rd.kb.search`）：建议配置 `RDX_KB_INDEX_DIRS`，让服务启动时索引你的文档/源码目录（详见 `configuration.md`）。

## 安装（可选）

RDX-MCP 本质上是一个 Python 包 + MCP server 入口。你可以不安装，直接运行 `run.py`；也可以用 editable 安装得到 `rdx-mcp` 命令。

```powershell
cd extensions/rdx-mcp
python -m pip install -e .
```

## RenderDoc 源码构建（必需，Windows 示例）

- 打开仓库根目录的 `renderdoc.sln`。
- 选择 `x64` + `Development`，编译 `pyrenderdoc_module`（会联动生成 `renderdoc.dll`）。
- 默认输出：
  - `x64\Development\pymodules\renderdoc.pyd`
  - `x64\Development\renderdoc.dll`

## 启动服务

### 一键启动（Windows）

双击 `extensions/rdx-mcp/run.bat`，脚本会提示选择：

- `L`（LAN）：默认输出 SSE 内网 URL（例如 `http://192.168.x.x:PORT/sse`）
- `I`（INTERNET）：会提示选择 **HTTP**（推荐，`https://.../mcp`）或 **SSE**（`https://.../sse`）公网 URL

脚本会做基础自检（IP 类型、ngrok 安装/授权），并把最终 URL 复制到剪贴板，直接粘贴到客户端即可。
如需跳过提示并强制 SSE 或 HTTP，可在 `run.env.bat` 里设置 `RDX_TRANSPORT=sse` 或 `RDX_TRANSPORT=http`。
首次运行时会询问默认 `.rdc` 目录，并保存到 `extensions/rdx-mcp/.rdx_mcp.json`（已忽略提交）。

ngrok 安装方式（Windows，任选其一）：

- `winget install ngrok.ngrok`
- 或手动下载 `ngrok.exe` 并放到 `extensions/rdx-mcp/`（与 `run.bat` 同目录）或仓库根目录

安装后需执行一次：`ngrok config add-authtoken <TOKEN>`（否则 INTERNET 模式会自检失败）。

如果未配置 authtoken，脚本会提示你粘贴并将其保存到 `extensions/rdx-mcp/.rdx_mcp.json`（已加入 `.gitignore`，避免意外提交）。

### 方式 A：stdio（默认，适合桌面客户端/Agent 集成）

```powershell
python extensions/rdx-mcp/run.py
```

### 方式 B：SSE（适合 Web client）

```powershell
python extensions/rdx-mcp/run.py --transport sse --host 127.0.0.1 --port 8765
```

> **说明**：SSE 监听地址最终由 `RDX_SSE_HOST` / `RDX_SSE_PORT` 决定；`run.py` 会把命令行参数写回环境变量后再启动（见 `extensions/rdx-mcp/run.py`、`extensions/rdx-mcp/rdx/server.py`）。

### 方式 C：使用 `rdx-mcp` 入口（安装后）

```powershell
rdx-mcp
```

## MCP 客户端最小配置（示例）

不同客户端的配置文件格式不完全一致，但核心都是“启动一个 stdio MCP server 的命令行”。以下是一个通用形态的示例（仅展示关键字段）：

```json
{
  "command": "python",
  "args": ["extensions/rdx-mcp/run.py"],
  "env": {
    "RDX_RENDERDOC_PATH": "D:/path/to/RenderDoc/python",
    "RDX_ARTIFACT_DIR": "D:/rdx/artifacts",
    "RDX_DB_DIR": "D:/rdx/db",
    "RDX_LOG_LEVEL": "INFO"
  }
}
```

## 远程 Agent 如何打开你本机的 .rdc？

远程/云端 Agent 调用 `rd.capture.open` 时，传入的 `rdc_path` 会在 **运行 RDX-MCP 的这台机器**上读取，
所以它必须是你本机可访问的路径（例如 `D:\captures\foo.rdc`）。

为了减少手动输入路径，可以：

- 在 `run.env.bat` 设置 `RDX_RDC_DIRS`（Windows 用 `;` 分隔多个目录）
- 或让 Agent 调 `rd.capture.set_dirs` 保存默认目录（会写入 `extensions/rdx-mcp/.rdx_mcp.json`）
- 然后让 Agent 调 `rd.capture.list` 选择文件，再把返回的 `path` 传给 `rd.capture.open`

## 第一次调用：一键端到端

最省心的入口是 `rd.pipeline.run_full_debug`。它会按 S0–S7 顺序执行 pipeline，并返回最终 `TaskState` 与 summary。

**你需要准备**

- `rdc_path`：RenderDoc capture 文件（`.rdc`）路径
- `description`：对问题的自然语言描述（尽量包含“什么不对、期望是什么、发生在哪一帧/哪类对象”）

**示例参数（概念示例）**

- `bug_type_hints` 是一个 JSON 数组字符串，例如：`["naninf", "precision"]`

后续建议先阅读：`tools.md`（各工具输入输出）、`workflows.md`（S0–S7 过程与可观测数据）。
