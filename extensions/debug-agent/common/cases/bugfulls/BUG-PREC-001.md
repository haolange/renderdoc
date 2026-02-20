# BugFull: BUG-PREC-001

```yaml
bug_full_id: BUG-PREC-001
title: "【发白】Adreno 650 Vulkan 下角色头发/衣物发白（Local Light color unpack RelaxedPrecision）"

meta:
  severity: S2_medium
  status: fixed
  created_at: "2026-02-07"
  owner: "TA/Graphics"

# 观测描述（只描述现象，不包含原因推断）
observed:
  description: "Adreno 650 上，有 local light 的场景中角色头发/发丝/衣物整体偏白，暗部缺失；同场景 Adreno 740 表现正常。"
  symptom_tags: [颜色错误, 过亮, washout]
  trigger_tags: [Adreno650, Vulkan, RelaxedPrecision, local_light_unpack]
  frequency: 必现
  conditions:
    - "启用 local lights（LightGrid/Forward+）"
    - "同一 shader 在 Adreno 650 复现，在 Adreno 740 不复现"

environment:
  platform: Android
  api: Vulkan
  gpu: "Adreno 650 (A6xx)"
  driver: unknown
  engine: "Unreal Engine"
  renderer_path: "Mobile Forward / LightGrid (ForwardLocalLightDataPacked)"
  shading_scope: "hair + local lights"

# 根因分析
root_cause:
  invariant_broken: [I-LIGHT-UNPACK-01, I-PREC-01]
  category_primary: "Driver.Compiler.PrecisionLowering"
  category_secondary: "Lighting.LocalLight.UnpackUNorm4x8"
  mechanism: "Adreno 650 上 local light color unpack 的 half/RelaxedPrecision 数据流触发 driver/codegen 异常，导致光照颜色/强度链路偏高，表现为整体发白。"
  blame:
    component: shader_include
    location: "LightGridCommon.ush: GetMobileLocalLightData -> LightData.Color"

# 修复方案
fix:
  fix_type: [PrecisionFence, CastToFloat]
  summary: "在 unpack 后显式转 float，再乘 LightIntensity，打断 half/RelaxedPrecision 链路（最小改动，已验证）。"
  patch_diff: |
    diff --git a/LightGridCommon.ush b/LightGridCommon.ush
    @@
    -   LightData.Color = LightIntensity * DwordToUNorm(Vec1.z).xyz;
    +   half3  LightColorUNorm_h = DwordToUNorm(Vec1.z).xyz;
    +   float3 LightColorUNorm_f = (float3)LightColorUNorm_h;
    +   LightData.Color = LightIntensity * LightColorUNorm_f;
  verification_plan: |
    - Adreno 650：应用上述 HLSL patch 后重放同场景，发白消失。
    - Adreno 740：同 patch 不引入回归。
    - 对照：SPIR-V 层移除 RelaxedPrecision（%404/%144/%213/%493）亦可恢复（历史证据）。

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
      file: "企业微信截图_17696752708429.png"
    data_payload: "artifacts/BUG-PREC-001/screenshot_compare.png"
    timestamp: "2026-02-07T00:00:00Z"
    environment_context:
      os: Android
      driver: unknown
      api: Vulkan
      hardware: "Adreno 650 (A6xx)"
    type: image_compare
    description: "Adreno 650 左侧发白，右侧（Adreno 740）正常对照。"

  - source_tool: manual.spirv_edit
    parameters:
      file: "HairWhiteSpirV.txt"
      removed_decorations: ["%404", "%144", "%213", "%493"]
      decoration: RelaxedPrecision
    data_payload: "artifacts/BUG-PREC-001/HairWhiteSpirV_patched.txt"
    timestamp: "2026-02-07T00:00:00Z"
    environment_context:
      os: Android
      driver: unknown
      api: Vulkan
      hardware: "Adreno 650 (A6xx)"
    type: spirv_decoration_diff
    description: "必须同时移除四个 RelaxedPrecision 才稳定恢复；主因定位为 %493（local light color unpack 数据流）。"

  - source_tool: L3.ShaderStaticScan
    parameters:
      file: "LightGridCommon.txt"
      symbol: "LightData.Color"
    data_payload: "artifacts/BUG-PREC-001/hlsl_light_color_unpack.txt"
    timestamp: "2026-02-07T00:00:00Z"
    environment_context:
      os: Android
      driver: unknown
      api: Vulkan
      hardware: "Adreno 650 (A6xx)"
    type: shader_source_mapping
    description: "LightData.Color = LightIntensity * DwordToUNorm(Vec1.z).xyz，且 DwordToUNorm 返回 half4。"

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
      file: "企业微信截图_17696752708429.png"
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-PREC-001/screenshot_compare.png"]
    decision_context: "症状标签 [过亮, washout] 命中 I-LIGHT-UNPACK-01 / I-PREC-01，先截图确认设备差异。"

  - agent_id: shader
    tool_name: manual.spirv_decoration_scan
    tool_parameters:
      file: "HairWhiteSpirV.txt"
      decoration: RelaxedPrecision
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-PREC-001/HairWhiteSpirV_patched.txt"]
    decision_context: "发白→精度问题假设；扫描 SPIR-V RelaxedPrecision decoration，逐步移除定位主因 %493。"

  - agent_id: shader
    tool_name: L3.ShaderStaticScan
    tool_parameters:
      file: "LightGridCommon.txt"
      symbol: "LightData.Color"
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: ["artifacts/BUG-PREC-001/hlsl_light_color_unpack.txt"]
    decision_context: "%493 追溯到 HLSL DwordToUNorm 返回 half4，链路全程 half/RelaxedPrecision 未打断。"

  - agent_id: shader
    tool_name: manual.device_validation
    tool_parameters:
      gpu: Adreno650
      patch: "LightGridCommon.ush PrecisionFence"
    start_timestamp: "2026-02-07T00:00:00Z"
    end_timestamp: "2026-02-07T00:00:00Z"
    result_status: Success
    output_evidence_refs: []
    decision_context: "应用 float cast patch 后在 Adreno 650 验证发白消失；Adreno 740 无回归，修复确认。"

# 泛化信息
generalization:
  recommended_sop: SOP_Precision_RelaxedPrecision_Adreno_v1
  recommended_tools:
    - L3.ShaderStaticScan
    - L3.SPIRVDecorationScan
    - L4.DeviceA_B_Compare
  affected_platforms: [Android / Adreno 6xx]

notes:
  derived_feature:
    key_spirv_ids: ["%493", "%404", "%213", "%144"]
    main_trigger_id: "%493"
    hlsl_anchor: "LightData.Color = LightIntensity * DwordToUNorm(Vec1.z).xyz"

# 关联案例
related_cases:
  - bug_id: BUG-PREC-002
    relation: "同族问题（同平台Adreno Vulkan RelaxedPrecision精度类），互为对照：PREC-001为过亮/发白，PREC-002为过暗/发黑，修复模式相同（float cast打断half链路）。"

# 反事实验证记录
# 遵从 expert_constraints.md 约束三：每个根因必须有"如果不X，则Y不发生"的反事实验证
hypotheses_verification:

  # 候选假设列表（调试过程中的全部假设，包括被排除的）
  candidate_hypotheses:
    - hyp_id: "HYP-PREC001-A"
      hypothesis: "纹理数据错误导致光照颜色偏高（纹理解码错误）"
      status: REFUTED
      refutation_evidence: |
        切换到 Adreno 740 后同纹理渲染正常，排除纹理数据本身错误；
        纹理数据为静态资产，在不同GPU上读取结果一致。
      ruled_out_at: "设备A/B对比阶段"

    - hyp_id: "HYP-PREC001-B"
      hypothesis: "Tone Mapping 或曝光参数在 Adreno 650 上配置异常"
      status: REFUTED
      refutation_evidence: |
        Tone Mapping 参数为 CPU 侧 uniform，在两台设备上传值一致（通过 API log 确认）；
        问题仅影响有 local light 的物体，全局曝光异常会影响全场景，不符合症状。
      ruled_out_at: "Pass范围缩小阶段"

    - hyp_id: "HYP-PREC001-C"
      hypothesis: "half/RelaxedPrecision 数据流在 Adreno 650 driver/codegen 中触发异常，导致 local light color unpack 结果偏高"
      status: VALIDATED
      confirmation_evidence: |
        SPIR-V 层手动移除 %493 RelaxedPrecision decoration 后问题消失（必要条件满足）；
        HLSL 层显式 cast 到 float 后问题消失（充分条件满足）；
        Adreno 740 上同 shader 不复现，符合 A6xx driver 特定行为。

  # 最终确认假设的反事实验证（对应 HYP-PREC001-C）
  counterfactual_verification:
    hypothesis: "half/RelaxedPrecision 链路在 Adreno 650 codegen 下导致 LightData.Color 偏高 -> 发白"

    # 必要条件验证："如果不存在 RelaxedPrecision，则发白不发生"
    necessary_condition:
      question: "移除 RelaxedPrecision decoration 后，发白是否消失？"
      method: |
        手动编辑 SPIR-V，逐一移除 %404/%144/%213/%493 的 OpDecorate RelaxedPrecision；
        在 Adreno 650 设备上重放同一帧。
      result: PASS
      observation: "移除 %493（主因）后发白消失；移除其余三个后效果稳定；仅移除无关 decoration 无效果。"
      evidence_ref: "artifacts/BUG-PREC-001/HairWhiteSpirV_patched.txt"

    # 充分条件验证："如果恢复 RelaxedPrecision，则发白重现"
    sufficient_condition:
      question: "恢复 RelaxedPrecision decoration 后，发白是否重现？"
      method: |
        在已 patch 的 SPIR-V 中重新加回 %493 RelaxedPrecision；
        在 Adreno 650 设备上重放同一帧。
      result: PASS
      observation: "重新加回 %493 后发白重现，与原始症状一致。"

    # 替代解释排除
    alternative_explanations:
      - alternative: "纹理数据错误（HYP-PREC001-A）"
        ruled_out: true
        evidence: "Adreno 740 同纹理正常；纹理数据与GPU无关。"

      - alternative: "Tone Mapping/曝光参数差异（HYP-PREC001-B）"
        ruled_out: true
        evidence: "API log 确认 uniform 传值一致；症状为局部（local light物体），非全场景。"

      - alternative: "HLSL 逻辑在所有设备上均有问题（shader本身bug）"
        ruled_out: true
        evidence: "Adreno 740 / Mali / PC 上同 shader 表现正常，确认为 Adreno 650 driver特定行为。"

    # HLSL级别反事实验证（代码修复层面）
    hlsl_counterfactual:
      question: "HLSL显式cast到float后，Adreno 650发白是否消失，Adreno 740是否无回归？"
      patch_applied: |
        half3  LightColorUNorm_h = DwordToUNorm(Vec1.z).xyz;
        float3 LightColorUNorm_f = (float3)LightColorUNorm_h;
        LightData.Color = LightIntensity * LightColorUNorm_f;
      result_adreno_650: PASS  # 发白消失
      result_adreno_740: PASS  # 无回归
      conclusion: "float cast 打断 half/RelaxedPrecision 链路是充分且最小的修复，根因确认。"
```
