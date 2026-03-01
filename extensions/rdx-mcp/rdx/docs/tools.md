# RenderDoc MCP Tool Catalog

本文档描述当前对外发布工具集：**196 个文档工具**。

## 响应契约

所有工具统一返回 JSON 字符串，顶层字段固定为：

- `success: bool`
- `error_message?: str`（当 `success=false` 时必有）
- 其余字段为工具结果字段

大数据结果优先返回 artifact 路径（`artifact_path` / `saved_path` / `*_path`）。

## 参数契约

复杂参数统一支持两种输入形式：

- 原生 `dict` / `list`
- JSON 字符串（自动解析）

常用句柄：

- `capture_file_id`
- `session_id`
- `remote_id`
- `shader_debug_id`

`frame_index` 固定为 0-based。

## 文档工具清单（196）

完整清单由 `extensions/rdx-mcp/rdx/spec/tool_catalog_196.json` 维护并驱动注册。

分组统计如下：

- `core`: 9
- `capture`: 7
- `replay`: 4
- `event`: 14
- `pipeline`: 25
- `resource`: 13
- `texture`: 12
- `buffer`: 4
- `mesh`: 7
- `shader`: 16
- `debug`: 11
- `perf`: 6
- `analysis`: 4
- `export`: 12
- `diag`: 11
- `macro`: 14
- `util`: 6
- `remote`: 12
- `app`: 9

## 远端/应用内能力说明

`rd.remote.*` 与 `rd.app.*` 在无真实目标设备或无应用内集成时，会返回结构化失败：

- `success=false`
- `error_message` 明确说明原因
- 包含能力标记（如 `requires_remote_device` / `requires_app_integration`）

这属于预期行为，不会抛出未处理异常。

## 校验入口

可通过以下命令校验 catalog 一致性：

```powershell
python extensions/rdx-mcp/rdx/spec/build_catalog.py
python extensions/rdx-mcp/rdx/spec/validate_catalog.py
```
