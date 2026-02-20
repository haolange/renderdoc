# BugCard: BUG-PREC-002

```yaml
bug_card_id: BUG-PREC-002
title: "Adreno 740：头发/披风发黑（KajiyaDiffuse RelaxedPrecision）"
bug_family: Rendering.Precision
invariants_broken: [I-SHADING-NONNEG-01, I-PREC-01]
symptom_tags: [颜色错误, 过暗, blackout]
trigger_tags: [Adreno740, Vulkan, RelaxedPrecision, hair_shading]
anchor: "MobileHairShading (Kajiya-Kay diffuse scatter)"
key_evidence:
  - type: image_compare
    description: 左侧塌黑（坏），右侧正常灰色；Adreno 740 vs 参考对比截图
  - type: spirv_slice
    description: "%732 由 (1 - abs(dot(N,L))) 链路生成且 RelaxedPrecision；移除后恢复"
  - type: shader_source_mapping
    description: "MobileKajiyaKayDiffuseAttenuation: half KajiyaDiffuse = 1 - abs(dot(N, L));"
root_cause_one_liner: "Adreno 7xx 在 RelaxedPrecision lowering 下使 KajiyaDiffuse 出现负值/异常，后续非负钳位链把输出压成 0 -> 发黑。"
fix_one_liner: "用 float 计算 dot(N,L)（必要时加 max(0,...)）避免负值传播。"
recommended_sop: SOP_Precision_RelaxedPrecision_Adreno_v1
related_cases: [BUG-PREC-001]   # 同族：Adreno Vulkan RelaxedPrecision精度类，互为发白/发黑对照
```
