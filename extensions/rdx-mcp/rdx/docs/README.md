# RDX-MCP 文档总览

RDX-MCP 现已采用注册表驱动工具架构，对外发布工具集为：

- **196 个文档工具**

详细清单见 `tools.md`，规格源见 `../spec/tool_catalog_196.json`。

## 快速入口

- 工具与契约：`tools.md`
- 运行与启动：`quickstart.md`
- 配置说明：`configuration.md`
- 工作流：`workflows.md`
- 问题排查：`troubleshooting.md`

## 关键约定

- 所有工具返回 JSON 字符串，统一包含 `success`，失败时包含 `error_message`。
- 复杂参数支持 `dict/list` 或 JSON 字符串。
- 大体积输出优先 artifact 路径返回，避免大 JSON 内联。
- `frame_index` 统一为 0-based。

## 远端与应用内能力

`rd.remote.*`、`rd.app.*` 已实现完整接口层，但实机连通依赖外部前置条件：

- 远程 RenderDoc target 可达（remote）
- 目标应用集成 RenderDoc In-Application API（app）

当前无目标设备时会返回结构化失败（`success=false` + 原因字段），这是预期降级行为。
