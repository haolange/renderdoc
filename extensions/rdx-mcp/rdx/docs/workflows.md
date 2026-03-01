# 工作流（推荐链路与常用组合）

本页给出一套“最小闭环”链路与常用组合，全部基于 `extensions/rdx-mcp/rdx/spec/tool_catalog_196.json` 中的 196 个工具（不包含任何额外扩展工具）。

## 1) 初始化与打开

1. `rd.core.init`
2. `rd.capture.open_file` → 得到 `capture_file_id`
3. `rd.capture.open_replay` → 得到 `session_id`
4. `rd.replay.set_frame` / `rd.replay.get_frame_info`

## 2) 浏览事件并选定锚点（event_id）

- `rd.event.get_action_tree`（用 `max_depth` 控制树大小）
- `rd.event.search_actions`（用字符串 query 快速定位 marker/drawcall）
- `rd.event.set_active` / `rd.event.get_active`
- `rd.event.get_action_details` / `rd.event.get_marker_stack`
- `rd.event.get_api_calls` / `rd.event.get_callstack`

## 3) Inspect：管线与资源

- Pipeline：
  - `rd.pipeline.get_state_summary`
  - `rd.pipeline.get_output_targets` / `rd.pipeline.get_render_targets` / `rd.pipeline.get_depth_target`
  - `rd.pipeline.get_resource_bindings` / `rd.pipeline.get_constant_buffers`
  - `rd.pipeline.get_shader`（按 stage 获取绑定 shader）
- Resources：
  - `rd.resource.list_textures` / `rd.resource.list_buffers` / `rd.resource.list_all`
  - `rd.resource.get_details` / `rd.resource.get_history`
  - `rd.resource.get_current_contents`（导出当前内容到 `output_path`）

## 4) 导出证据（便于复现/分享）

- `rd.export.screenshot`
- `rd.export.pipeline_state_json` / `rd.export.event_tree_json`
- `rd.export.shader_bundle` / `rd.export.cbuffer_dump`
- `rd.debug.pixel_history` + `rd.export.pixel_history_json`（需要你先选定像素坐标/target）
- 一键打包（宏工具）：
  - `rd.macro.build_bug_report_pack`
  - 或更底层的 `rd.export.repro_bundle_zip` + `rd.export.markdown_report`

> 注意：所有 `output_path/output_dir` 都是在**运行 RDX-MCP 的机器**上写文件。

## 5) Shader 调试与热修复（可选）

- Shader debugger：
  - `rd.shader.debug_start` → 得到 `shader_debug_id`
  - `rd.debug.step` / `rd.debug.continue` / `rd.debug.run_to`
  - `rd.debug.get_variables` / `rd.debug.evaluate_expression`
  - `rd.debug.finish`
- 热修复/替换：
  - `rd.shader.edit_and_replace`
  - `rd.shader.revert_replacement`
  - `rd.macro.shader_hotfix_validate`（辅助验证替换前后截图/指标）

## 6) 关闭与清理

- `rd.capture.close_replay`
- `rd.capture.close_file`
- `rd.core.shutdown`
