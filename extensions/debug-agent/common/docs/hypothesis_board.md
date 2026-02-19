# Hypothesis Board 假设板

## 概述

Hypothesis Board 是 AIRD 框架中的核心推理追踪工具，用于系统性地记录、管理和验证调试过程中的假设。每个假设都必须经过结构化的验证流程，确保调试过程严谨、可追溯。

## 假设结构

```yaml
hypothesis_id: HYP-[INVARIANT]-[TIMESTAMP]
invariant_id: I-NAN-01
title: "假设：NaN 来自顶点着色器阶段的除零操作"
status: [ACTIVE|VALIDATED|REFUTED|SPLIT]
priority: [CRITICAL|HIGH|MEDIUM|LOW]
created_by: AgentID
created_at: YYYY-MM-DD HH:mm:ss

# 症状观察
observations:
  - type: screenshot
    location: Pass 3, Event 1247
    description: "像素 (x,y) 显示 NaN 渲染结果"
    evidence_ref: E001
  - type: pipeline_state
    location: "Vertex Shader Stage"
    description: "顶点着色器输出包含 NaN 值"
    evidence_ref: E002

# 假设内容
hypothesis: |
  根据不变量 I-NAN-01 的约束，顶点着色器中的除法操作
  (output.pos.w = 1.0 / input.w) 在 input.w 接近零时可能产生
  数值溢出或 NaN。

# 验证计划
validation_plan:
  - step: 1
    action: "检查 input.w 的值范围"
    tool: "Pipeline State Viewer"
    expected_outcome: "如果 input.w > 0 且不过小，则假设被反驳"
  - step: 2
    action: "对比正常渲染的相同 DrawCall"
    tool: "Frame Comparison"
    expected_outcome: "差异定位在顶点着色器阶段"
  - step: 3
    action: "注入 test.w = 1.0 作为对照"
    tool: "Shader Debugging"
    expected_outcome: "如果结果正常则假设被验证"

# 验证结果
validation_results:
  - step: 1
    result: VALIDATED
    evidence: "input.w 范围 [0.0001, 0.001]，存在极端小值"
    timestamp: "2024-01-15 10:30:00"
  - step: 2
    result: VALIDATED
    evidence: "差异确实在顶点着色器输出"
    timestamp: "2024-01-15 10:35:00"

# 结论
conclusion: |
  假设被验证。顶点着色器中缺少对 input.w 的最小值保护，
  导致除法产生 NaN 并传播到最终渲染结果。
fix_suggestion: "在除法前添加：if (input.w < 0.0001) input.w = 1.0"
```

## 假设生命周期

```
┌─────────────────────────────────────────────────────────────────┐
│                      HYPOTHESIS LIFECYCLE                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐ │
│   │  ACTIVE  │───▶│ VALIDATE │───▶│ VALIDATED│     │  REFUTED │ │
│   └──────────┘    └──────────┘    └──────────┘    └──────────┘ │
│        │               │                                   │    │
│        │               │ 验证失败                            │    │
│        ▼               ▼                                   ▼    │
│   ┌──────────┐    ┌──────────┐                       ┌──────────┐│
│   │  SPLIT   │    │ INVAILD  │                       │ ARCHIVED ││
│   └──────────┘    └──────────┘                       └──────────┘│
│                                                                  │
│   SPLIT: 假设被拆分为多个子假设                                   │
│   INVALID: 假设违反基本物理定律或 Invariant                      │
│   ARCHIVED: 已完成验证的历史假设                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 假设板使用规范

### 1. 假设创建时机

- **必须创建**：当发现违反 Invariant 的症状时
- **必须创建**：当需要解释异常渲染结果时
- **可选创建**：当存在多种可能的根因时

### 2. 假设质量标准

每个假设必须满足：

1. **可验证性**：存在明确的验证方法和预期结果
2. **可证伪性**：存在能够证明假设错误的可能性
3. **最小性**：假设应该是最简单的可能解释
4. **可追溯性**：假设必须引用具体的证据

### 3. 假设数量限制

- 同时存在的活跃假设不超过 **7 个**
- 超过时必须归档或拆分
- 优先验证优先级高的假设

### 4. 假设更新规则

- 每次验证步骤后必须更新状态
- 新证据可能导致假设状态变化
- Team Lead 每周审查活跃假设

## 假设板模板

### 快速创建格式

```
HYP-[INVARIANT]-[TIMESTAMP]
━━━━━━━━━━━━━━━━━━━━━━━━━━
状态: [ACTIVE]
优先级: [HIGH]
假设: [一句话假设]

证据:
- [证据1]
- [证据2]

验证计划:
1. [步骤1]
2. [步骤2]
```

### 完整格式

参见上方结构化模板

## 假设板管理 SOP

### SOP-HYP-01: 新假设创建流程

```
1. Triage Agent 发现症状违反 Invariant
2. 创建 Hypothesis Board 条目
3. 引用相关 Evidence
4. 制定验证计划
5. 分配给 Forensics Agent 执行验证
6. 记录验证结果
7. 根据结果更新状态
```

### SOP-HYP-02: 假设验证流程

```
1. 按照验证计划执行验证步骤
2. 记录每一步的实际输出
3. 与预期结果对比
4. 更新假设状态:
   - 全部验证通过 → VALIDATED
   - 任一验证失败 → REFUTED
   - 需要进一步分析 → SPLIT
5. 如果 VALIDATED，生成修复建议
6. 归档假设到历史记录
```

### SOP-HYP-03: 假设审查流程

```
1. Team Lead 每周审查所有 ACTIVE 假设
2. 检查验证计划是否合理
3. 检查是否存在资源竞争
4. 优先排序
5. 调整验证计划（如需要）
```

## 与其他组件的关系

```
┌────────────────────────────────────────────────────────────────┐
│                     HYPOTHESIS BOARD                            │
│                                                                │
│    ┌──────────┐     ┌──────────┐     ┌──────────┐            │
│    │Invariant │     │Evidence  │     │ BugCard  │            │
│    │ Library  │────▶│ Structure│◀────│          │            │
│    └──────────┘     └──────────┘     └──────────┘            │
│         │                │                 │                │
│         │                │                 │                │
│         ▼                ▼                 ▼                │
│    ┌──────────────────────────────────────────────┐          │
│    │            HYPOTHESIS BOARD                   │          │
│    │  - 假设创建                                     │          │
│    │  - 验证计划                                     │          │
│    │  - 状态追踪                                     │          │
│    └──────────────────────────────────────────────┘          │
│                          │                                      │
│                          ▼                                      │
│    ┌──────────┐     ┌──────────┐     ┌──────────┐            │
│    │SOP Library│     │Skeptic   │     │ BugFull  │            │
│    │          │◀────│  Agent   │────▶│          │            │
│    └──────────┘     └──────────┘     └──────────┘            │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

## 示例

### 示例 1: NaN 假设

```yaml
hypothesis_id: HYP-NAN-20240115-001
invariant_id: I-NAN-01
title: "顶点着色器除零导致 NaN"
status: VALIDATED
priority: CRITICAL
created_by: triage
created_at: 2024-01-15 09:00:00

observations:
  - type: screenshot
    description: "像素 (512, 384) 显示全白（NaN渲染结果）"
  - type: shader_output
    description: "VS 输出 position.w = Inf"

hypothesis: |
  顶点着色器中 "output.pos = input.pos / input.w" 当 input.w 
  接近 0 时产生除零，导致 NaN/Inf 并传播到片段着色器。

validation_plan:
  - step: 1
    action: "检查 input.w 的原始值"
    tool: "Pipeline State > Vertex Buffer"
  - step: 2
    action: "对比正常帧的相同 DrawCall"
    tool: "Frame Diff"

validation_results:
  - step: 1
    result: VALIDATED
    evidence: "input.w 最小值为 0.00001"
  - step: 2
    result: VALIDATED
    evidence: "正常帧无此问题"

conclusion: 假设验证成功
fix_suggestion: "在 VS 中添加: if (abs(input.w) < 0.001) input.w = sign(input.w) * 0.001"
```

### 示例 2: 颜色假设

```yaml
hypothesis_id: HYP-COLOR-20240115-002
invariant_id: I-COLOR-01
title: "sRGB 转换错误导致过曝"
status: VALIDATED
priority: HIGH
created_by: triage

observations:
  - type: screenshot
    description: "亮部区域显示纯白（255,255,255），无颜色层次"
  - type: pipeline_state
    description: "Render Target 使用 LINEAR 格式"

hypothesis: |
  渲染管线未正确执行 sRGB->Linear 转换，导致 HDR 颜色值
  被线性映射到低动态范围，产生过曝。

validation_plan:
  - step: 1
    action: "检查 Shader 代码中的颜色输出"
    tool: "Shader Editor"
  - step: 2
    action: "检查 Render Target 格式"
    tool: "Pipeline State"

validation_results:
  - step: 1
    result: VALIDATED
    evidence: "Shader 直接输出 HDR 值到 U8 Render Target"
  - step: 2
    result: VALIDATED
    evidence: "RT 格式为 R8G8B8A8 (非 sRGB)"

fix_suggestion: "使用 sRGB Render Target 或在 Shader 中手动 gamma 校正"
```

---

**相关文档**:
- [Invariant Library](../invariants/invariant_library.md)
- [Evidence Structure](./evidence_structure.md)
- [BugCard Template](../cases/bugcards/bugcard_template.md)
- [Skeptic Agent SOP](./sop_library.md)
