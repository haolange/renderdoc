# BugFull 模板

```yaml
# BugFull 完整研究报告
bug_full_id: BUG-[FAMILY]-[SEQ]

# 观测描述（只描述现象，不包含原因推断）
observed:
  description: 详细描述观察到的现象
  symptom_tags: [标签列表]
  trigger_tags: [触发条件列表]
  frequency: 必现/偶发
  conditions:
    - 条件1
    - 条件2

# 根因分析
root_cause:
  invariant_broken: I-NAN-01
  category_primary: Shader Logic Error
  blame:
    location: "文件路径:行号 / Shader函数 / Pass名称"
    description: 位置描述

# 修复方案
fix:
  fix_type: Code Patch / Configuration Change / Workaround / Driver Update
  summary: 修复方案描述
  patch_diff: |
    --- a/shader.hlsl
    +++ b/shader.hlsl
    @@ -1,5 +1,8 @@
    +float3 SafeNormalize(float3 v) {
    +    float len = length(v);
    +    return len > 0.0001f ? v / len : float3(0, 1, 0);
    +}
  verification_plan: 验证计划描述

# 证据集合
# 每条 Evidence 必须携带完整五字段，以构建可复用观测路径：
#   source_tool       — 生成该证据的工具名称（是重新调用的入口）
#   parameters        — 调用 source_tool 时的全部输入参数（保证可重现）
#   data_payload      — 实际证据数据（文件路径 或 序列化数据块）
#   timestamp         — 证据生成时间戳（ISO 8601）
#   environment_context — 捕获时的环境信息（OS/Driver/API/硬件等）
evidence:
  - source_tool: RenderDoc
    parameters:
      session_id: "<session_id>"
      event_id: 0
      x: 0
      y: 0
    data_payload: "artifacts/screenshot_frame_N.png"
    timestamp: "YYYY-MM-DDTHH:MM:SSZ"
    environment_context:
      os: "OS 版本"
      driver: "GPU 驱动版本"
      api: "D3D12 / Vulkan"
      hardware: "GPU 型号"
    type: screenshot
    description: 截图描述

# 工具调用链
# 每条 Action 必须携带完整七字段，以支持策略学习与 SOP 进化：
#   agent_id              — 执行该操作的 Agent 标识
#   tool_name             — 被调用的工具名称
#   tool_parameters       — 调用工具时传入的具体参数（指令侧，对应 Evidence.parameters 的观测侧）
#   start_timestamp       — 操作开始时间（ISO 8601）
#   end_timestamp         — 操作结束时间（ISO 8601）
#   result_status         — 执行结果：Success / Failure / Timeout / Partial
#   output_evidence_refs  — 该操作产出的 Evidence 引用列表
#   decision_context      — Agent 执行此操作时的决策依据（当前状态/假设/推理）
action_chain:
  - agent_id: triage
    tool_name: rdx.event.get_actions
    tool_parameters:
      session_id: "<session_id>"
      include_markers: true
    start_timestamp: "YYYY-MM-DDTHH:MM:SSZ"
    end_timestamp: "YYYY-MM-DDTHH:MM:SSZ"
    result_status: Success
    output_evidence_refs: []
    decision_context: "初步分类症状，获取事件树以定位异常 Pass"

# 泛化信息
generalization:
  recommended_sop: SOP-NAN-01
  recommended_tools:
    - rdx.texture.get_data
    - rdx.event.get_pixels
  affected_platforms: [Windows, macOS]
```

---

# BugFull 示例: BUG-NAN-001

```yaml
bug_full_id: BUG-NAN-001
title: 角色脸部白点闪烁

# 观测描述
observed:
  description: 角色脸部渲染时出现随机白点闪烁，左脸可见白点，右脸正常
  symptom_tags: [白色斑点, 闪烁, 脸部渲染异常]
  trigger_tags: [PBR材质, 角色渲染, 法线为零]
  frequency: 偶发
  conditions:
    - 角色法线向量为零时
    - 使用标准PBR着色

# 根因分析
root_cause:
  invariant_broken: I-NAN-01
  category_primary: Shader Logic Error
  blame:
    location: "shaders/pixel.hlsl:45, PSMain函数"
    description: normalize(v.normal)当normal向量长度为0时返回NaN

# 修复方案
fix:
  fix_type: Code Patch
  summary: 使用SafeNormalize函数替代直接normalize调用
  patch_diff: |
    float3 SafeNormalize(float3 v) {
        float len = length(v);
        return len > 0.0001f ? v / len : float3(0, 1, 0);
    }

    // 替换
    // float3 n = normalize(v.normal);
    float3 n = SafeNormalize(v.normal);
  verification_plan: |
    1. 使用RenderDoc Find NaN功能确认无NaN
    2. 多帧验证问题已修复

# 证据集合
evidence:
  - source_tool: rdx.frame.take_screenshot
    parameters:
      session_id: "sess_nan001"
      event_id: 1244
    data_payload: "artifacts/BUG-NAN-001/screenshot_frame45.png"
    timestamp: "2026-02-14T10:12:03Z"
    environment_context:
      os: "Android 11"
      driver: "Adreno 630 Driver v512.415.0"
      api: "Vulkan 1.1"
      hardware: "Qualcomm Adreno 630"
    type: screenshot
    description: 角色脸部白点截图，像素 (1234,567) 显示全白

  - source_tool: rdx.event.get_pixels
    parameters:
      session_id: "sess_nan001"
      event_id: 1245
      x: 1234
      y: 567
    data_payload: "artifacts/BUG-NAN-001/pixel_history_1245.json"
    timestamp: "2026-02-14T10:13:21Z"
    environment_context:
      os: "Android 11"
      driver: "Adreno 630 Driver v512.415.0"
      api: "Vulkan 1.1"
      hardware: "Qualcomm Adreno 630"
    type: pixel_history
    description: 像素历史追溯，PS 阶段 Event 1245 首次输出 NaN

  - source_tool: rdx.shader.get_source
    parameters:
      session_id: "sess_nan001"
      event_id: 1245
      stage: "PS"
    data_payload: "artifacts/BUG-NAN-001/pixel_shader_src.hlsl"
    timestamp: "2026-02-14T10:15:44Z"
    environment_context:
      os: "Android 11"
      driver: "Adreno 630 Driver v512.415.0"
      api: "Vulkan 1.1"
      hardware: "Qualcomm Adreno 630"
    type: code
    description: PS 源码，第 45 行 normalize(v.normal) 未检查零向量

# 工具调用链
action_chain:
  - agent_id: triage
    tool_name: rdx.event.get_actions
    tool_parameters:
      session_id: "sess_nan001"
      include_markers: true
    start_timestamp: "2026-02-14T10:10:00Z"
    end_timestamp: "2026-02-14T10:10:08Z"
    result_status: Success
    output_evidence_refs: []
    decision_context: "症状标签 [白色斑点, 闪烁] → 命中 I-NAN-01，获取事件树定位问题 Pass"

  - agent_id: forensics
    tool_name: rdx.frame.take_screenshot
    tool_parameters:
      session_id: "sess_nan001"
      event_id: 1244
    start_timestamp: "2026-02-14T10:12:00Z"
    end_timestamp: "2026-02-14T10:12:05Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-NAN-001/screenshot_frame45.png"]
    decision_context: "截取问题帧以确认像素坐标，为 Pixel History 追溯提供入口"

  - agent_id: forensics
    tool_name: rdx.event.get_pixels
    tool_parameters:
      session_id: "sess_nan001"
      event_id: 1245
      x: 1234
      y: 567
    start_timestamp: "2026-02-14T10:13:18Z"
    end_timestamp: "2026-02-14T10:13:25Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-NAN-001/pixel_history_1245.json"]
    decision_context: "逆向追溯像素历史，定位首个 NaN 出现的 DrawCall 和 Shader 阶段"

  - agent_id: shader
    tool_name: rdx.shader.get_source
    tool_parameters:
      session_id: "sess_nan001"
      event_id: 1245
      stage: "PS"
    start_timestamp: "2026-02-14T10:15:40Z"
    end_timestamp: "2026-02-14T10:15:47Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-NAN-001/pixel_shader_src.hlsl"]
    decision_context: "Pixel History 指向 PS 阶段，拉取源码定位具体表达式"

# 泛化信息
generalization:
  recommended_sop: SOP-NAN-01
  recommended_tools:
    - rdx.texture.get_data
    - rdx.event.get_pixels
    - rdx.shader.analyze
  affected_platforms: [All]

# 关联案例
related_cases: []   # 同族/相似案例引用列表，格式: [BUG-XXX-001, ...]

# 反事实验证记录
# 遵从 expert_constraints.md 约束三：每个根因必须有"如果不X，则Y不发生"的反事实验证
hypotheses_verification:

  # 候选假设列表（调试过程中提出的全部假设，包含被排除的）
  candidate_hypotheses:
    - hyp_id: "HYP-[ID]-A"
      hypothesis: "备选假设描述"
      status: REFUTED   # REFUTED / VALIDATED
      refutation_evidence: "排除依据（具体证据）"
      ruled_out_at: "排除阶段名称"

    - hyp_id: "HYP-[ID]-B"
      hypothesis: "最终确认假设描述"
      status: VALIDATED
      confirmation_evidence: "确认依据（具体证据）"

  # 最终确认假设的反事实验证
  counterfactual_verification:
    hypothesis: "X 导致 Y 的完整陈述"

    # 必要条件："如果不 X，则 Y 不发生"
    necessary_condition:
      question: "移除/消除 X 后，Y 是否消失？"
      method: "验证步骤描述"
      result: PASS   # PASS / FAIL
      observation: "实际观察结果"
      evidence_ref: "证据文件路径"

    # 充分条件："如果 X，则 Y 发生"
    sufficient_condition:
      question: "恢复 X 后，Y 是否重现？"
      method: "验证步骤描述"
      result: PASS
      observation: "实际观察结果"

    # 替代解释排除（至少列出2条）
    alternative_explanations:
      - alternative: "可能的替代原因 1"
        ruled_out: true
        evidence: "排除依据"
      - alternative: "可能的替代原因 2"
        ruled_out: true
        evidence: "排除依据"
```
