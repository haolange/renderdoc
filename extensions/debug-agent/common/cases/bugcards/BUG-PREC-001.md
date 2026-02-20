# BugCard: BUG-PREC-001

```yaml
bug_card_id: BUG-PREC-001
title: "Adreno 650：头发/衣物发白（Local Light RelaxedPrecision）"
bug_family: Rendering.Precision
invariants_broken: [I-LIGHT-UNPACK-01, I-PREC-01]
symptom_tags: [颜色错误, 过亮, washout]
trigger_tags: [Adreno650, Vulkan, RelaxedPrecision, local_light_unpack]
anchor: "MobileBasePass / ForwardLocalLight"
key_evidence:
  - type: image_compare
    description: 左侧发白（坏），右侧正常；Adreno 650 vs Adreno 740 对比截图
  - type: spirv_decoration_diff
    description: 移除 %404/%144/%213/%493 的 RelaxedPrecision 后恢复；主因 %493
  - type: shader_source_mapping
    description: "LightData.Color = LightIntensity * DwordToUNorm(Vec1.z).xyz；DwordToUNorm 返回 half4"
root_cause_one_liner: "Adreno 6xx 在 local light color unpack 的 half/RelaxedPrecision 数据流上触发 driver/codegen 异常，光照颜色偏高 -> 发白。"
fix_one_liner: "unpack 后显式 cast 到 float 再乘 LightIntensity，打断 half/RelaxedPrecision 链路。"
recommended_sop: SOP_Precision_RelaxedPrecision_Adreno_v1
related_cases: [BUG-PREC-002]   # 同族：Adreno Vulkan RelaxedPrecision精度类，互为发白/发黑对照
```
