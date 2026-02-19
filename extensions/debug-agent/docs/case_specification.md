# BugCard 规范文档

## 概述

BugCard是渲染Bug案例的精简检索版本，用于快速匹配和RAG检索。每个BugCard控制在20-40行，是BugFull的蒸馏版本。

## 规范格式

```yaml
# BugCard 模板
bug_card:
  # 唯一标识符
  card_id: BUG-[FAMILY]-[SEQ]
  
  # 案例标题（简洁描述）
  title: "<20字问题描述>"
  
  # Bug家族分类
  bug_family: 
    - nan_propagation
    - negative_scale
    - double_gamma
    - depth_precision
    - texture_addressing
    - blend_mode
    - shader_compile
    - pipeline_state
  
  # 违反的不变量
  invariants_broken:
    - I-NAN-01
    - I-COLOR-01
    # 更多...
  
  # 症状标签（用于匹配）
  symptom_tags:
    - white_spot
    - object_missing
    - color_too_dark
    # 更多...
  
  # 触发条件标签
  trigger_tags:
    - negative_scale_matrix
    - zero_division
    - uninitialized_variable
    # 更多...
  
  # 锚点信息（快速定位）
  anchor:
    event_id: 1234
    pipeline_stage: PS
    shader_name: "pixel_main"
    api_object: "pipeline_abc"
  
  # 关键证据（最小化信息）
  key_evidence:
    - type: screenshot
      description: "角色脸部白点闪烁"
      location: "capture_001.png"
    - type: pixel_history
      event_range: "1200-1250"
      finding: "PS阶段引入NaN"
  
  # 推荐SOP
  recommended_sop: SOP-NAN-01
  
  # 摘要（<100字）
  summary: |
    角色脸部渲染出现白点闪烁问题。
    根因：PS中normalize(v.normal)当normal为0时产生NaN。
    修复：使用SafeNormalize包装函数。
```

## 使用场景

### RAG检索

当AI Agent遇到新问题时，通过症状标签检索相似BugCard：

```
[RAG_QUERY]
symptom_tags: [white_spot, flickering]
trigger_tags: [normalize, uninitialized]
invariants_broken_needed: [I-NAN-01]

[RAG_RESULT]
matched_cards:
  - BUG-NAN-001 (score: 0.92)
  - BUG-NAN-003 (score: 0.78)
```

### 上下文注入

将检索到的BugCard摘要注入AI上下文：

```
# 历史案例参考
## BUG-NAN-001
**问题**: 角色脸部白点闪烁
**根因**: normalize(0) -> NaN
**修复**: SafeNormalize
**关键证据**: PS阶段第45行
```

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| card_id | 是 | 格式: BUG-[家族]-[序号] |
| title | 是 | 简洁描述，<20字 |
| bug_family | 是 | 家族分类数组 |
| invariants_broken | 是 | 违反的不变量ID数组 |
| symptom_tags | 是 | 症状标签数组 |
| trigger_tags | 否 | 触发条件标签数组 |
| anchor | 是 | 快速定位信息 |
| key_evidence | 是 | 关键证据（2-3条） |
| recommended_sop | 是 | 推荐使用的SOP |
| summary | 是 | 100字以内摘要 |

## 示例案例

### BUG-NAN-001: SafeNormalize修复案例

```yaml
bug_card:
  card_id: BUG-NAN-001
  title: "角色脸部白点闪烁"
  bug_family: [nan_propagation]
  invariants_broken: [I-NAN-01]
  symptom_tags: [white_spot, flickering, nan_propagation]
  trigger_tags: [normalize, zero_vector, uninitialized]
  anchor:
    event_id: 1245
    pipeline_stage: PS
    shader_name: "PSMain"
    api_object: "pipeline_skin"
  key_evidence:
    - type: screenshot
      description: "左脸可见白点，右脸正常"
      location: "bug_nan_001.png"
    - type: pixel_history
      event_range: "1240-1245"
      finding: "PS输出NaN"
    - type: shader_analysis
      finding: "normalize(v.normal)当normal=0时产生NaN"
  recommended_sop: SOP-NAN-01
  summary: |
    角色脸部渲染时出现随机白点闪烁。
    根因：PS中normalize(v.normal)在normal向量长度为0时返回NaN。
    修复：使用SafeNormalize函数，当length<0.0001时返回默认值。
```

---

# BugFull 规范文档

## 概述

BugFull是渲染Bug的完整档案，用于长期沉淀、全面复现和训练数据。每个BugFull包含完整的调查过程、证据链和根因分析。

## 规范格式

```yaml
# BugFull 模板
bug_full:
  # 基础信息
  case_id: BUG-[FAMILY]-[SEQ]
  title: "<问题描述>"
  severity: [critical/major/minor]
  status: [active/fixed/known_issue]
  reporter: "<报告人>"
  created_at: "YYYY-MM-DD"
  updated_at: "YYYY-MM-DD"
  
  # 症状描述
  symptoms:
    - description: "<具体描述>"
      location: "<屏幕位置>"
      frequency: "<偶发/必现>"
      conditions:
        - "<触发条件1>"
        - "<触发条件2>"
  
  # 证据收集
  evidence:
    screenshots:
      - path: "<路径>"
        description: "<描述>"
        timestamp: "YYYY-MM-DD HH:MM"
    
    captures:
      - type: rdc
        path: "<路径>"
        frame: 123
        event: 456
        description: "<描述>"
    
    logs:
      - source: "<日志源>"
        content: "<关键内容>"
        timestamp: "YYYY-MM-DD HH:MM"
  
  # 调查过程（行动链）
  investigation:
    action_chain:
      - step: 1
        tool: rd.event.get_actions
        params: {...}
        result: "获取到25个DrawCall"
        finding: "问题发生在第15个DrawCall"
      
      - step: 2
        tool: rd.texture.get_data
        params: {...}
        result: "获取到问题像素原始数据"
        finding: "像素值为(NaN, NaN, NaN, 1)"
      
      # 更多步骤...
    
    hypotheses:
      - id: 1
        description: "假设：Shader中除零导致NaN"
        tests:
          - test: "检查所有除法运算"
            result: "未发现明显零除法"
        status: rejected
      
      - id: 2
        description: "假设：normalize(0)导致NaN"
        tests:
          - test: "检查normalize调用"
            result: "发现normalize(v.normal)可能接收零向量"
        status: accepted
  
  # 根因分析
  root_cause:
    category: "<shader/logic/pipeline/api>"
    location:
      file: "shaders/pixel.hlsl"
      line: 45
      function: "PSMain"
    description: "<详细描述>"
    code_snippet: |
      // 问题代码
      float3 normal = normalize(v.normal);
    contributing_factors:
      - "<因素1>"
      - "<因素2>"
  
  # 修复方案
  fix:
    type: [shader_change/config_change/workaround]
    patch: |
      // 修复代码
      float3 SafeNormalize(float3 v) {
          float len = length(v);
          return len > 0.0001f ? v / len : float3(0, 1, 0);
      }
      
      float3 normal = SafeNormalize(v.normal);
    verification:
      - method: "使用RenderDoc Find NaN功能"
        result: "未发现NaN"
      - method: "多帧验证"
        result: "问题已修复"
  
  # 泛化信息
  generalization:
    applicable_to:
      - "<其他可能受影响的项目/模块>"
    related_invariants:
      - I-NAN-01
    related_sops:
      - SOP-NAN-01
    lessons_learned: |
      <可复用的经验教训>
  
  # 附件
  attachments:
    - type: rdc
      path: "/path/to/capture.rdc"
    - type: screenshot
      path: "/path/to/screenshot.png"
```

## 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| case_id | 是 | 唯一标识符 |
| title | 是 | 问题标题 |
| severity | 是 | 严重程度 |
| status | 是 | 当前状态 |
| symptoms | 是 | 症状列表 |
| evidence | 是 | 证据列表 |
| investigation.action_chain | 是 | 调查行动链 |
| investigation.hypotheses | 是 | 假设列表 |
| root_cause | 是 | 根因分析 |
| fix | 是 | 修复方案 |
| generalization | 否 | 泛化信息 |

## 案例索引

### 案例检索

```
[BUG_FULL_QUERY]
bug_family: nan_propagation
severity: major
status: fixed

[BUG_FULL_RESULT]
cases:
  - BUG-NAN-001: "角色脸部白点闪烁"
  - BUG-NAN-002: "水面反射NaN"
```

### 训练数据导出

BugFull可导出用于训练：

```json
{
  "input": {
    "symptoms": "角色脸部白点闪烁",
    "evidence": {...}
  },
  "output": {
    "root_cause": "normalize(0) -> NaN",
    "fix": "SafeNormalize"
  }
}
```
