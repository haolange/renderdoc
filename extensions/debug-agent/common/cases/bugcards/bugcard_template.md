# BugCard 模板

```yaml
# BugCard 轻量级检索快照
bug_card_id: BUG-[FAMILY]-[SEQ]
title: "<20字问题描述>"
bug_family: Rendering.[Category]      # 如 Rendering.Lighting
invariants_broken: [I-NAN-01, I-PREC-01]
symptom_tags: [标签1, 标签2]
trigger_tags: [Adreno GPU, Android 11]
anchor: "Pass/Event/DrawCall ID/像素坐标"
key_evidence:
  - type: screenshot
    description: 描述
  - type: pixel_history
    description: 描述
root_cause_one_liner: 根因一句话描述
fix_one_liner: 修复方案一句话描述
recommended_sop: SOP-NAN-01
related_cases: []           # 同族/相似案例引用列表，格式: [BUG-XXX-001, ...]
```

---

# BugCard 示例: BUG-NAN-001

```yaml
bug_card_id: BUG-NAN-001
title: "角色脸部白点闪烁"
bug_family: Rendering.NAPropagation
invariants_broken: [I-NAN-01]
symptom_tags: [白色斑点, 闪烁, 脸部渲染异常]
trigger_tags: [PBR材质, 角色渲染]
anchor: "Event 1245, PSMain pixel (1234, 567)"
key_evidence:
  - type: screenshot
    description: 角色左脸可见白点，右脸正常
  - type: pixel_history
    description: PS阶段输出NaN
  - type: shader_analysis
    description: normalize(v.normal)当normal=0时产生NaN
root_cause_one_liner: PS中normalize(v.normal)当法线向量长度为0时返回NaN
fix_one_liner: 使用SafeNormalize函数，当length<0.0001时返回默认值
recommended_sop: SOP-NAN-01
related_cases: []
```
