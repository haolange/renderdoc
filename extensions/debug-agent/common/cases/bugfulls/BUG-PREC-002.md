# BugFull: BUG-PREC-002

```yaml
bug_full_id: BUG-PREC-002
title: "【发黑】Adreno 740 Vulkan 下头发/披风整体发黑（KajiyaDiffuse negative -> clamp）"

meta:
  severity: S2_medium
  status: fixed
  created_at: "2026-02-07"
  owner: "TA/Graphics"

# 观测描述（只描述现象，不包含原因推断）
observed:
  description: "Adreno 740 上，角色背部头发/披风整体被压到接近纯黑，细节消失；同场景 Adreno 650 表现正常。"
  symptom_tags: [颜色错误, 过暗, blackout]
  trigger_tags: [Adreno740, Vulkan, RelaxedPrecision, hair_shading]
  frequency: 必现
  conditions:
    - "使用 hair shading（MobileHairShading / MobileKajiyaKayDiffuseAttenuation）"
    - "同一 shader 在 Adreno 740 复现，在 Adreno 650 不复现"

environment:
  platform: Android
  api: Vulkan
  gpu: "Adreno 740 (A7xx)"
  driver: unknown
  engine: "Unreal Engine"
  renderer_path: "Mobile Hair Shading (Kajiya-Kay + scatter)"
  shading_scope: "hair / cloak"

# 根因分析
root_cause:
  invariant_broken: [I-SHADING-NONNEG-01, I-PREC-01]
  category_primary: "Numerical.Precision"
  category_secondary: "Hair.KajiyaDiffuse"
  mechanism: "RelaxedPrecision lowering 下 KajiyaDiffuse = 1 - abs(dot(N,L)) 在 Adreno 740 上产生负值/异常，叠加到 S 后被非负钳位链压成 0，导致塌黑。"
  blame:
    component: shader
    location: "MobileShadingModels.ush: MobileKajiyaKayDiffuseAttenuation (KajiyaDiffuse)"

# 修复方案
fix:
  fix_type: [CastToFloat, PrecisionFence]
  summary: "将 dot(N,L) 用 float 计算并回写到 half，避免 relaxed/half 链路异常（最小改动，已验证）。可选：max(0,...) 作为边界保险。"
  patch_diff: |
    diff --git a/MobileShadingModels.ush b/MobileShadingModels.ush
    @@
    -   half KajiyaDiffuse = 1 - abs(dot(N, L));
    +   float NoL_f = dot((float3)N, (float3)L);
    +   half  KajiyaDiffuse = (half)(1.0 - abs(NoL_f));
    +   // optional hardening:
    +   // KajiyaDiffuse = max((half)0, KajiyaDiffuse);
  verification_plan: |
    - Adreno 740：应用上述 HLSL patch 后重放同场景，塌黑消失。
    - Adreno 650：同 patch 不引入回归。
    - 对照：SPIR-V 层移除 %732 RelaxedPrecision 亦可恢复（历史证据）。

# 证据集合
# 每条 Evidence 必须携带完整五字段，以构建可复用观测路径：
#   source_tool       — 生成该证据的工具名称（是重新调用的入口）
#   parameters        — 调用 source_tool 时的全部输入参数（保证可重现）
#   data_payload      — 实际证据数据（文件路径 或 序列化数据块）
#   timestamp         — 证据生成时间戳（ISO 8601）
#   environment_context — 捕获时的环境信息（OS/Driver/API/硬件等）
evidence:
  - source_tool: manual.screenshot_compare
    parameters:
      file: "企业微信截图_17696749508825.png"
    data_payload: "artifacts/BUG-PREC-002/screenshot_compare.png"
    timestamp: "2026-02-07T00:00:00Z"
    environment_context:
      os: Android
      driver: unknown
      api: Vulkan
      hardware: "Adreno 740 (A7xx)"
    type: image_compare
    description: "Adreno 740 左侧塌黑，右侧正常灰色对照。"

  - source_tool: manual.spirv_scan
    parameters:
      file: "HairBlackSpirV.txt"
      ids: ["%728-%733", "%732"]
    data_payload: "artifacts/BUG-PREC-002/HairBlackSpirV_analysis.txt"
    timestamp: "2026-02-07T00:00:00Z"
    environment_context:
      os: Android
      driver: unknown
      api: Vulkan
      hardware: "Adreno 740 (A7xx)"
    type: spirv_slice
    description: "%732 由 (1 - abs(dot(N,L))) 链路生成，且 OpDecorate %732 RelaxedPrecision；移除后恢复。"

  - source_tool: L3.ShaderStaticScan
    parameters:
      file: "MobileShadingModels.txt"
      symbol: "MobileKajiyaKayDiffuseAttenuation"
    data_payload: "artifacts/BUG-PREC-002/hlsl_kajiya_diffuse.txt"
    timestamp: "2026-02-07T00:00:00Z"
    environment_context:
      os: Android
      driver: unknown
      api: Vulkan
      hardware: "Adreno 740 (A7xx)"
    type: shader_source_mapping
    description: "HLSL: half KajiyaDiffuse = 1 - abs(dot(N, L)); 后续 S = -min(-S, 0.0) 做非负钳位。"

# 工具调用链
# 每条 Action 必须携带完整七字段，以支持策略学习与 SOP 进化：
#   agent_id              — 执行该操作的 Agent 标识
#   tool_name             — 被调用的工具名称
#   tool_parameters       — 调用工具时传入的具体参数
#   start_timestamp       — 操作开始时间（ISO 8601）
#   end_timestamp         — 操作结束时间（ISO 8601）
#   result_status         — 执行结果：Success / Failure / Timeout / Partial
#   output_evidence_refs  — 该操作产出的 Evidence 引用列表
#   decision_context      — Agent 执行此操作时的决策依据
action_chain:
  - agent_id: triage
    tool_name: manual.image_compare
    tool_parameters:
      file: "企业微信截图_17696749508825.png"
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-PREC-002/screenshot_compare.png"]
    decision_context: "症状标签 [过暗, blackout] 命中 I-SHADING-NONNEG-01 / I-PREC-01，先截图确认设备差异。"

  - agent_id: shader
    tool_name: manual.spirv_decoration_scan
    tool_parameters:
      file: "HairBlackSpirV.txt"
      decoration: RelaxedPrecision
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-PREC-002/HairBlackSpirV_analysis.txt"]
    decision_context: "发黑→非负值被钳零假设；扫描 SPIR-V RelaxedPrecision decoration，锁定 %732 为主因。"

  - agent_id: shader
    tool_name: L3.ShaderStaticScan
    tool_parameters:
      file: "MobileShadingModels.txt"
      symbol: "MobileKajiyaKayDiffuseAttenuation"
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-PREC-002/hlsl_kajiya_diffuse.txt"]
    decision_context: "%732 追溯到 HLSL KajiyaDiffuse = 1 - abs(dot(N,L))，后续非负钳位链确认负值→零路径。"

  - agent_id: shader
    tool_name: manual.device_validation
    tool_parameters:
      gpu: Adreno740
      patch: "MobileShadingModels.ush CastToFloat"
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: []
    decision_context: "应用 float cast patch 后在 Adreno 740 验证塌黑消失；Adreno 650 无回归，修复确认。"

# 泛化信息
generalization:
  recommended_sop: SOP_Precision_RelaxedPrecision_Adreno_v1
  recommended_tools:
    - L3.ShaderStaticScan
    - L3.SPIRVDecorationScan
    - L4.DeviceA_B_Compare
  affected_platforms: [Android / Adreno 7xx]

notes:
  derived_feature:
    main_trigger_id: "%732"
    hlsl_anchor: "half KajiyaDiffuse = 1 - abs(dot(N, L))"

# 关联案例
related_cases:
  - bug_id: BUG-PREC-001
    relation: "同族问题（同平台Adreno Vulkan RelaxedPrecision精度类），互为对照：PREC-001为过亮/发白（A6xx），PREC-002为过暗/发黑（A7xx），修复模式相同（float cast打断half链路）。"

# 反事实验证记录
# 遵从 expert_constraints.md 约束三：每个根因必须有"如果不X，则Y不发生"的反事实验证
hypotheses_verification:

  # 候选假设列表（调试过程中的全部假设，包括被排除的）
  candidate_hypotheses:
    - hyp_id: "HYP-PREC002-A"
      hypothesis: "光照贡献被 alpha blending 错误遮挡，导致发黑"
      status: REFUTED
      refutation_evidence: |
        Pixel History 显示头发材质渲染顺序正确，无异常 blend；
        禁用 alpha blending 后症状无变化，排除混合层问题。
      ruled_out_at: "像素取证阶段"

    - hyp_id: "HYP-PREC002-B"
      hypothesis: "NdotL 值为负（法线方向错误）导致漫反射为负被钳零"
      status: REFUTED
      refutation_evidence: |
        Shader Debug 显示 N 和 L 方向向量在 Adreno 740 和 Adreno 650 上完全一致；
        Adreno 650 上 KajiyaDiffuse > 0，Adreno 740 上 KajiyaDiffuse ≈ 0（负值被钳），
        法线方向不是区分因素，排除。
      ruled_out_at: "Shader分析阶段"

    - hyp_id: "HYP-PREC002-C"
      hypothesis: "RelaxedPrecision lowering 在 Adreno 740 下导致 KajiyaDiffuse = 1-abs(dot(N,L)) 产生负值，后续非负钳位链将其压成 0，导致塌黑"
      status: VALIDATED
      confirmation_evidence: |
        SPIR-V 层移除 %732 RelaxedPrecision decoration 后塌黑消失（必要条件满足）；
        HLSL 层显式用 float 计算 dot(N,L) 后塌黑消失（充分条件满足）；
        Adreno 650 上同 shader 不复现，确认为 A7xx driver 特定行为。

  # 最终确认假设的反事实验证（对应 HYP-PREC002-C）
  counterfactual_verification:
    hypothesis: "RelaxedPrecision lowering 在 Adreno 740 下使 KajiyaDiffuse 产生负值，非负钳位链将其压成 0 -> 塌黑"

    # 必要条件验证："如果不存在 RelaxedPrecision，则塌黑不发生"
    necessary_condition:
      question: "移除 %732 RelaxedPrecision decoration 后，塌黑是否消失？"
      method: |
        手动编辑 SPIR-V，移除 %732 的 OpDecorate RelaxedPrecision；
        在 Adreno 740 设备上重放同一帧。
      result: PASS
      observation: "移除 %732 RelaxedPrecision 后塌黑消失，头发恢复正常灰色受光。"
      evidence_ref: "artifacts/BUG-PREC-002/HairBlackSpirV_analysis.txt"

    # 充分条件验证："如果恢复 RelaxedPrecision，则塌黑重现"
    sufficient_condition:
      question: "恢复 %732 RelaxedPrecision decoration 后，塌黑是否重现？"
      method: |
        在已 patch 的 SPIR-V 中重新加回 %732 RelaxedPrecision；
        在 Adreno 740 设备上重放同一帧。
      result: PASS
      observation: "重新加回 %732 后塌黑重现，与原始症状一致。"

    # 替代解释排除
    alternative_explanations:
      - alternative: "Alpha blending 遮挡（HYP-PREC002-A）"
        ruled_out: true
        evidence: "禁用 alpha blending 无效果；Pixel History 显示渲染顺序正常。"

      - alternative: "法线方向错误（HYP-PREC002-B）"
        ruled_out: true
        evidence: "Shader Debug 确认 N/L 在两台设备上相同；区别仅在于 KajiyaDiffuse 最终值。"

      - alternative: "HLSL 逻辑在所有设备上均有问题（shader本身bug）"
        ruled_out: true
        evidence: "Adreno 650 / Mali / PC 上同 shader 正常，确认为 Adreno 740 (A7xx) driver特定行为。"

    # HLSL级别反事实验证（代码修复层面）
    hlsl_counterfactual:
      question: "HLSL显式用float计算dot(N,L)后，Adreno 740塌黑是否消失，Adreno 650是否无回归？"
      patch_applied: |
        float NoL_f = dot((float3)N, (float3)L);
        half  KajiyaDiffuse = (half)(1.0 - abs(NoL_f));
        // optional: KajiyaDiffuse = max((half)0, KajiyaDiffuse);
      result_adreno_740: PASS  # 塌黑消失
      result_adreno_650: PASS  # 无回归
      conclusion: "float cast 打断 half/RelaxedPrecision 链路，KajiyaDiffuse 恢复非负，根因确认。"
```
