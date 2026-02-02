# 工作流（S0–S7）与端到端 Pipeline

本页把 RDX-MCP 的“自动化 GPU 调试”拆成两种使用方式：

- **一键模式**：直接调用 `rd.pipeline.run_full_debug`
- **编排模式**：用 21 个 tools 按你的产品/引擎语义进行更细粒度的控制

## S0–S7：skills pipeline 是什么

RDX-MCP 在 `extensions/rdx-mcp/rdx/skills/workflows.py` 中定义了 8 个可组合 skill（S0–S7），并通过 `run_full_debug_pipeline(...)` 顺序执行它们：

- **S0 `intake_and_normalize`**：打开 capture、解析描述、构建 event tree
- **S1 `localize_anomaly`**：渲染输出、运行 verifiers、定位异常像素/区域
- **S2 `search_first_bad`**：在事件范围内二分搜索首个“坏事件”
- **S3 `attribute_pass_draw`**：把问题进一步归因到具体 pass / draw call
- **S4 `extract_pipeline_shader`**：抓取 pipeline 快照并导出 shader artifacts
- **S5 `hypothesis_and_patch_loop`**：生成修复假设、打补丁、跑实验并排序
- **S6 `map_to_engine`**：将 pipeline/shader 线索映射到引擎侧（例如 UE 模块）
- **S7 `build_report`**：构建最终 report bundle（JSON/MD/HTML + assets）

### 失败策略：降级继续

`run_full_debug_pipeline` 的策略是：某一步失败会记录日志，并把 `TaskState.status` 置为 `degraded_after_<skill>`，然后**继续执行后续步骤**（用已有的部分信息尽量产出可用报告）。

## 一键模式：`rd.pipeline.run_full_debug`

**适用场景**

- 你希望“给一个 `.rdc` + 描述 → 产出报告”，不想自己编排细节。
- 你能接受 pipeline 的默认策略（默认 verifier、默认假设生成策略、默认报告结构）。

**输入要点**

- `description` 建议包含：
  - **现象**：例如“某材质在特定角度出现闪烁/大片 NaN”
  - **期望**：例如“应为平滑渐变、没有黑块”
  - **线索**：例如“发生在某个 pass / 某个后处理之后”
- `bug_type_hints`（可选）是 JSON 数组字符串（例如 `["naninf","precision"]`），用于引导假设生成与 patch 优先级。

## 编排模式：推荐的最小闭环

当你想在 Agent 层更可控（例如只做定位，不做 patch），可以按以下“最小闭环”编排。

### 1) 打开与浏览：建立可导航的事件空间

1. `rd.session.create`
2. `rd.capture.open`
3. `rd.capture.get_event_tree`

**产出**

- event tree（用于 UI 展示或供 Agent 选择区间）
- `event_range`（用于 bisect 的 `range_lo`/`range_hi`）

### 2) 快速定位异常：从“最后输出”开始

1. 选定一个候选 `event_id`（例如最后一个 draw/marker 附近）
2. `rd.output.render`（把输出落盘为 artifact，便于人类确认）
3. `rd.verify.naninf` 或 `rd.verify.image_diff`

**决策点**

- **如果 verifier 通过**：说明该 event 并非问题点，换一个 event 或调整 verifier 参数。
- **如果 verifier 失败并给出 bbox/mask**：将 bbox 对应的像素坐标作为 `rd.debug.pixel` 的候选输入。

### 3) 二分定位：把“出问题的时刻”钉到一个 draw call

1. `rd.event.bisect_first_bad`（设置 `verifier_type` 与可选 `verifier_params`）

**产出**

- `first_bad_event_id` + `confidence`（用于后续 pipeline snapshot / patch / experiment）

### 4) 深挖：pipeline / shader / pixel

1. `rd.pipeline.snapshot`（在 `first_bad_event_id` 上抓快照）
2. `rd.shader.export_artifacts`（导出可读信息）
3. `rd.debug.pixel`（对 bbox 内的像素点进行单步或 run-to-NaN/Inf）

**建议**

- **优先 debug 一两个代表像素**（mask 最密集区域），避免把 debug trace 做得过大。

### 5) 修复验证：patch + experiment

1. `rd.patch.apply`（可先不传 `ops`，只给 `intent`，让上层以策略生成 `PatchOp`）
2. `rd.experiment.run`（指定 `patch_id` + verifier）
3. 若效果不佳：`rd.patch.revert`，换假设/换 patch 再跑

### 6) 产出报告：`rd.report.build_bundle`

把当前 `TaskState`（来自你的编排状态机）序列化为 JSON 字符串，调用 `rd.report.build_bundle` 生成 HTML/MD/JSON 报告与 assets。

## 知识复用：KB + fingerprint

- `rd.kb.search` 适合“从现象/术语”反查引擎或 shader 代码位置（BM25，走本地索引）。
- `rd.fingerprint.match` 适合“相似 pass/shader 的历史问题复用”，用于快速给出候选根因与修复方向。

建议在你自己的工程里以 `project_id` 维度隔离多个项目的索引与 fingerprint 记录，减少噪声。

