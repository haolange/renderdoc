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
evidence:
  - source_tool: RenderDoc
    type: screenshot
    description: 截图描述

# 工具调用链
action_chain:
  - agent_id: triage
    tool_name: rdx.event.get_actions
    result_status: Success

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
  - source_tool: RenderDoc
    type: screenshot
    description: 角色脸部白点截图
    screen_coordinates: [1234, 567]
    frame_number: 45

  - source_tool: RenderDoc
    type: pixel_history
    description: 像素历史追溯到PS阶段
    event_id: 1245

  - source_tool: Shader Analysis
    type: code
    description: 发现normalize(v.normal)可能接收零向量

# 工具调用链
action_chain:
  - agent_id: triage
    tool_name: rdx.event.get_actions
    result_status: Success

  - agent_id: forensics
    tool_name: rdx.texture.get_data
    result_status: Success

  - agent_id: shader
    tool_name: rdx.shader.get_source
    result_status: Success

# 泛化信息
generalization:
  recommended_sop: SOP-NAN-01
  recommended_tools:
    - rdx.texture.get_data
    - rdx.event.get_pixels
    - rdx.shader.analyze
  affected_platforms: [All]
```
