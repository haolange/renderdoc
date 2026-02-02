# MCP 工具手册（Tool Catalog）

本页目标：把 21 个 MCP tools 的“用途、关键参数、典型返回字段”说清楚，方便你在 Agent 中编排调用。

## 通用返回结构：`ToolResponse`（JSON 字符串）

RDX-MCP 的每个 tool 都返回一个 **JSON 字符串**，基本形态是 `ToolResponse`（见 `extensions/rdx-mcp/rdx/models.py`）：

```json
{
  "ok": true,
  "trace_id": "trc_xxx",
  "artifact": {
    "uri": "rdx://artifacts/..",
    "sha256": "...",
    "mime": "image/png",
    "bytes": 1234,
    "meta": {}
  }
}
```

**约定**

- `ok=false` 时会包含 `error: { code, message, details }`。
- tool 可能在 envelope 上“追加字段”（例如 `session`、`capture`、`pipeline`、`verify_result` 等），这些字段按 tool 不同而变化（见 `extensions/rdx-mcp/rdx/server.py` 的 `_ok_response`）。

## 输入参数的常见形态

由于 MCP tool 的签名限制，部分参数会以 **“JSON 字符串”** 形式传入：

- `verifier_params`：例如 `{"diff_threshold":0.01}` 的 JSON 字符串
- `ops`：`PatchOp` 数组的 JSON 字符串
- `counter_ids`：整数数组的 JSON 字符串
- `bug_type_hints`：字符串数组的 JSON 字符串

> **建议**：在 Agent 层统一做 `json.dumps(...)`，并对不可控输入做校验（避免把半截 JSON 传进去导致 tool 直接失败）。

## 工具清单（按功能分组）

### 会话与 capture

1) `rd.session.create`：创建 replay session（local/remote）

- **关键参数**：`backend_type`（默认 `local`）、`gpu_index`、`force_api_validation`
- **关键返回**：`session.session_id`、`session.capabilities`

2) `rd.session.close`：关闭 session

- **关键参数**：`session_id`
- **关键返回**：`closed_session_id`

3) `rd.capture.open`：在 session 中打开 `.rdc`

- **关键参数**：`session_id`、`rdc_path`
- **关键返回**：`capture.capture_id`、`capture.api`、`capture.total_events` 等

4) `rd.capture.get_event_tree`：获取事件树

- **关键参数**：`session_id`、`include_passes`（可选）
- **关键返回**：`event_tree`（树形）、`passes`（可选）、`event_range`、`total_events`

### 事件定位与渲染/读回

5) `rd.event.set`：把 replay 导航到指定 `event_id`

- **关键参数**：`session_id`、`event_id`
- **关键返回**：`event_id`、`status`

6) `rd.event.bisect_first_bad`：在区间内找首个“坏事件”

- **关键参数**：`session_id`、`capture_id`、`range_lo`、`range_hi`
- **验证器相关**：`verifier_type`（`naninf`/`image_diff`）、`verifier_params`（JSON 字符串）
- **关键返回**：`bisect.first_bad_event_id`、`bisect.confidence`、`bisect.iterations` 等

7) `rd.output.render`：渲染某个 event 的输出并保存为 artifact

- **关键参数**：`session_id`、`event_id`
- **可选参数**：`output_format`（`png`/`exr`/`hdr`）、`source_config`、`view_config`（JSON 字符串）
- **关键返回**：`artifact`、`render_meta`

8) `rd.output.readback`：读回指定 texture 的内容并保存为 artifact

- **关键参数**：`session_id`、`event_id`、`texture_id`
- **可选参数**：`subresource`、`region`（JSON 字符串）
- **关键返回**：`artifact`、`texture_meta`

### Verifiers（异常检测/对比）

9) `rd.verify.naninf`：检测渲染输出中的 NaN/Inf

- **关键参数**：`session_id`、`capture_id`、`event_id`
- **可选参数**：`threshold`
- **关键返回**：`verify_result.passed`、`verify_result.metrics`、`verify_result.anomaly`、`verify_result.artifacts`

10) `rd.verify.image_diff`：与参考图对比

- **关键参数**：`session_id`、`capture_id`、`event_id`
- **可选参数**：`reference_artifact_sha`、`diff_threshold`
- **关键返回**：同上（`verify_result.*`）

### Pipeline / Shader / Pixel 调试

11) `rd.pipeline.snapshot`：获取指定 event 的 pipeline state 快照

- **关键参数**：`session_id`、`event_id`
- **关键返回**：`pipeline`（包含 shaders、render targets、blend/depth、bindings 等）

12) `rd.shader.export_artifacts`：导出 shader 相关 artifacts（反汇编/反射/IR/源等）

- **关键参数**：`session_id`、`event_id`
- **可选参数**：`stage`（默认 `ps`）
- **关键返回**：`shader_export`（包含 artifact refs）

13) `rd.debug.pixel`：像素级调试 pixel shader invocation

- **关键参数**：`session_id`、`event_id`、`x`、`y`
- **可选参数**：`sample`、`mode`（如 `run_to_naninf`）、`max_steps`
- **关键返回**：`debug_result`、`artifact`（trace artifact）

### Patch 与 Experiments

14) `rd.patch.apply`：应用 shader hot-patch

- **关键参数**：`session_id`、`event_id`
- **可选参数**：`stage`（默认 `ps`）、`intent`、`ops`（`PatchOp[]` 的 JSON 字符串）
- **关键返回**：`patch`（`patch_id`、success、错误信息等）

15) `rd.patch.revert`：回滚 patch

- **关键参数**：`session_id`、`patch_id`
- **关键返回**：`reverted` / `patch_id`（按实现返回字段为准）

16) `rd.experiment.run`：运行一次实验（可选应用 patch + verifier 对比）

- **关键参数**：`session_id`、`capture_id`、`event_id`
- **可选参数**：`verifier_type`、`verifier_params`（JSON 字符串）、`patch_id`、`description`
- **关键返回**：`experiment`（含 before/after evidence、verdict 等）

### 性能、报告、知识库

17) `rd.perf.sample_counters`：采样 GPU performance counters

- **关键参数**：`session_id`、`range_lo`、`range_hi`
- **可选参数**：`counter_ids`（整数数组 JSON 字符串；省略则采样全部可用 counters）
- **关键返回**：`perf`

18) `rd.report.build_bundle`：生成自包含报告包

- **关键参数**：`task_state_json`（完整 `TaskState` 的 JSON 字符串）
- **可选参数**：`output_dir`（省略则写到 `<RDX_ARTIFACT_DIR>/reports/<task_id>/`）
- **关键返回**：`report`（包含生成文件路径集合）

19) `rd.kb.search`：BM25 文本检索

- **关键参数**：`query`
- **可选参数**：`file_type`、`path_prefix`、`project_id`、`limit`
- **关键返回**：`results[]`（`path`/`score`/`snippet`/`line_number`）、`total`

20) `rd.fingerprint.match`：匹配历史 fingerprint

- **关键参数**：`fingerprint_type`（`pass`/`shader`）、`fingerprint_json`（JSON 字符串）
- **可选参数**：`threshold`
- **关键返回**：`matches[]`（`record` + `score`）、`total`

### 端到端 Pipeline

21) `rd.pipeline.run_full_debug`：一键执行 S0–S7

- **关键参数**：`rdc_path`、`description`
- **可选参数**：`reference_image_path`、`expected_image_path`、`bug_type_hints`（JSON 数组字符串）、`backend_type`、`project_id`
- **关键返回**：`task_state`（完整状态机数据）、`summary`（提炼字段）

