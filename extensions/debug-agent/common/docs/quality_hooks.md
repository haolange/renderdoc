# Quality Hooks 质量钩子

## 概述

Quality Hooks 是 AIRD 框架中的自动化质量保障机制，通过在关键决策点插入验证触发器，确保调试过程符合规范、假设验证严谨、结论可靠。质量钩子分为三种类型：

1. **Counterfactual Hook** - 反事实验证钩子
2. **BugCard Hook** - 案例完整性钩子
3. **Skeptic Hook** - 质疑审查钩子

## 1. Counterfactual Hook 反事实验证钩子

### 目的

确保每个被验证的假设都经过反事实验证——即必须证明"如果不 X，则不会 Y"的因果关系，而非仅仅观察到相关性。

### 触发条件

- 当 Hypothesis Board 状态变为 VALIDATED 时触发
- 当 Agent 提交调试结论时触发

### 验证规则

#### 规则 1: 必要条件验证

```
反事实断言: "如果 X 不成立，则 Y 不会发生"

验证方法:
1. 构造反事实场景（修改/移除 X）
2. 观察 Y 是否消失
3. 如果 Y 仍然存在，则假设被证伪
```

**示例**:
- 假设: "顶点着色器中 input.w 接近 0 导致 NaN"
- 反事实验证: "如果将 input.w 强制设为 1.0，NaN 是否消失？"

#### 规则 2: 充分条件验证

```
反事实断言: "如果 X 成立，则必然导致 Y"

验证方法:
1. 构造 X 成立的场景
2. 确认 Y 确实发生
3. 如果 Y 未发生，则假设不完整
```

#### 规则 3: 替代解释排除

```
反事实断言: "不存在其他原因导致 Y"

验证方法:
1. 列出所有可能导致 Y 的其他因素
2. 逐一排除
3. 如果存在未排除的替代解释，则假设不可靠
```

### 自动化检查清单

```yaml
counterfactual_check:
  necessary_condition:
    - question: "如果不满足假设条件，症状是否消失？"
      required: true
      evidence_type: [screenshot, shader_output, pipeline_state]
  
  sufficient_condition:
    - question: "假设条件成立时，症状是否必然出现？"
      required: true
      evidence_type: [screenshot, shader_output, pipeline_state]
  
  alternative_explanation:
    - question: "是否存在其他可能的根因？"
      required: true
      alternatives_must_be: [ruled_out, investigated]
```

### Counterfactual Hook 执行流程

```
┌─────────────────────────────────────────────────────────────────┐
│                  COUNTERFACTUAL HOOK EXECUTION                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐                                               │
│  │ Hypothesis   │                                               │
│  │ VALIDATED    │                                               │
│  └──────┬───────┘                                               │
│         │ 触发                                                   │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 1. 提取假设的核心因果关系             │                       │
│  │    "X → Y"                           │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 2. 构建反事实问题                     │                       │
│  │    "如果非 X，Y 是否消失？"           │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 3. 验证反事实场景                     │                       │
│  │    - 修改输入/状态                     │                       │
│  │    - 观察结果                          │                       │
│  │    - 记录证据                          │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────────┐    ┌─────────────────┐                   │
│  │ 反事实验证通过   │    │ 反事实验证失败   │                   │
│  │ ✓ 假设可靠      │    │ ✗ 假设需重审    │                   │
│  └─────────────────┘    └─────────────────┘                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. BugCard Hook 案例完整性钩子

### 目的

确保每个案例（Bugs/发现）在提交前具备完整的检索信息，支持未来的知识复用和模式匹配。

### 触发条件

- 当创建新 BugCard 时触发
- 当 BugCard 状态变为 FINALIZED 时触发
- 当进行案例库搜索前触发

### 验证规则

#### 规则 1: 必填字段检查

```yaml
bugcard_required_fields:
  - bug_card_id:       # 唯一标识符
  - bug_family:        # 分类家族
  - invariants_broken: # 违反的不变量列表
  - symptom_tags:      # 症状标签
  - trigger_tags:     # 触发条件标签
  - anchor:           # 定位信息
  - key_evidence:    # 关键证据（至少 1 项）
  - root_cause_one_liner: # 根因一句话
  - fix_one_liner:    # 修复方案一句话
```

#### 规则 2: 证据完整性检查

```yaml
evidence_completeness:
  minimum_evidence_count: 1
  
  evidence_types_acceptable:
    - screenshot      # 截图
    - pipeline_state  # 管线状态
    - shader_source  # Shader 源码
    - api_log         # API 调用日志
    - shader_output   # Shader 输出值
  
  evidence_quality:
    - must_include: location    # 位置信息
    - must_include: timestamp   # 时间戳
    - must_include: description  # 描述
```

#### 规则 3: 可检索性检查

```yaml
searchability_check:
  tags_count:
    minimum: 2
    maximum: 10
  
  anchor_quality:
    - must_be_specific: true      # 必须具体
    - format_accepted: 
        - "Pass/Event/DrawCall ID"
        - "Pixel (x,y)"
        - "Shader Name"
        - "API Call Index"
  
  invariance_link:
    - must_reference: invariant_id  # 必须引用不变量
    - reason: "支持按不变量检索"
```

### BugCard Hook 执行流程

```
┌─────────────────────────────────────────────────────────────────┐
│                   BUGCARD HOOK EXECUTION                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐                                               │
│  │ BugCard      │                                               │
│  │ CREATED      │                                               │
│  └──────┬───────┘                                               │
│         │ 触发                                                   │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 1. 必填字段检查                        │                       │
│  │    检查所有必填字段是否存在             │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 2. 证据质量检查                        │                       │
│  │    - 证据数量 >= 1                     │                       │
│  │    - 证据类型合法                      │                       │
│  │    - 位置/描述完整                     │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 3. 可检索性检查                        │                       │
│  │    - 标签数量 2-10                     │                       │
│  │    - 锚点格式正确                      │                       │
│  │    - 引用不变量 ID                     │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────────┐    ┌─────────────────┐                   │
│  │ 检查通过        │    │ 检查失败        │                   │
│  │ ✓ BugCard      │    │ ✗ 返回修改     │                   │
│  │   可提交        │    │   要求补充     │                   │
│  └─────────────────┘    └─────────────────┘                   │
││
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Skeptic Hook 质疑审查钩子

### 目的

在关键决策点引入质疑视角，防止确认偏误，确保所有可能的解释都被考虑。

### 触发条件

- 当 Hypothesis 状态变为 VALIDATED 时触发
- 当 Agent 提交调试结论时触发
- 当 BugCard 提交审核时触发
- Team Lead 手动触发

### 验证规则

#### 规则 1: 证据充分性质疑

```yaml
skeptic_evidence_check:
  questions:
    - "这个结论基于多少证据？"
    - "证据是否直接支持结论？"
    - "是否存在选择性证据？"
    - "证据是否可重复验证？"
  
  minimum_evidence_per_conclusion: 2
  
  evidence_directness:
    - required: 至少 1 项直接证据
    - acceptable: 间接证据需有逻辑链
```

#### 规则 2: 替代解释质疑

```yaml
skeptic_alternative_check:
  questions:
    - "还有什么其他可能的原因？"
    - "这些替代解释被排除的依据是什么？"
    - "是否存在更简单的解释？"
    - "结论是否过度复杂化？"
  
  alternative_count:
    minimum_alternatives: 2
    all_alternatives_must_be: [investigated, ruled_out]
```

#### 规则 3: 逻辑一致性质疑

```yaml
skeptic_logic_check:
  questions:
    - "因果关系方向是否正确？"
    - "是否存在循环论证？"
    - "相关性能否推出因果？"
    - "样本量是否足够？"
```

#### 规则 4: 不变量一致性质疑

```yaml
skeptic_invariant_check:
  questions:
    - "结论是否与已知不变量一致？"
    - "是否存在违反不变量的隐藏假设？"
    - "是否需要引入新的不变量？"
  
  invariant_verification:
    required: true
    reference: invariant_library
```

### Skeptic Hook 执行流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    SKEPTIC HOOK EXECUTION                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐                                               │
│  │ Conclusion   │                                               │
│  │ Submitted    │                                               │
│  └──────┬───────┘                                               │
│         │ 触发                                                   │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 1. 证据充分性审查                      │                       │
│  │    - 证据数量 >= 2                     │                       │
│  │    - 直接/间接证据分布                  │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 2. 替代解释审查                        │                       │
│  │    - 至少 2 个替代解释                 │                       │
│  │    - 逐一排除或标记                    │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 3. 逻辑一致性审查                      │                       │
│  │    - 因果方向                          │                       │
│  │    - 循环论证                          │                       │
│  │    - 样本量评估                        │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌──────────────────────────────────────┐                       │
│  │ 4. 不变量一致性审查                    │                       │
│  │    - 与已知不变量对比                  │                       │
│  │    - 检查隐藏假设                      │                       │
│  └──────┬───────────────────────────────┘                       │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────────┐    ┌─────────────────┐                   │
│  │ 审查通过        │    │ 审查质疑        │                   │
│  │ ✓ 结论可靠      │    │ ⚠ 需回应质疑   │                   │
│  └─────────────────┘    └─────────────────┘                   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 质量钩子配置

### 全局配置

```yaml
quality_hooks_config:
  # 是否启用所有钩子
  enabled: true
  
  # 钩子级别: [strict|moderate|relaxed]
  level: strict
  
  # Counterfactual Hook 配置
  counterfactual:
    enabled: true
    require_both_necessary_and_sufficient: true
    min_alternatives: 2
  
  # BugCard Hook 配置
  bugcard:
    enabled: true
    require_minimum_evidence: 1
    require_invariant_reference: true
    tag_count_range: [2, 10]
  
  # Skeptic Hook 配置
  skeptic:
    enabled: true
    min_evidence_per_conclusion: 2
    min_alternatives_per_conclusion: 2
    require_invariant_check: true
```

### 按 Agent 类型配置

```yaml
agent_hook_config:
  team_lead:
    - counterfactual: always
    - bugcard: on_conclusion
    - skeptic: manual
  
  triage:
    - counterfactual: on_hypothesis_create
    - bugcard: on_bugcard_create
  
  forensics:
    - counterfactual: always
    - skeptic: on_conclusion
  
  skeptic:
    - skeptic: always
  
  curator:
    - bugcard: always
    - skeptic: on_submission
```

---

## 与其他组件的关系

```
┌────────────────────────────────────────────────────────────────┐
│                      QUALITY HOOKS                               │
│                                                                │
│    ┌─────────────────┐    ┌─────────────────┐                │
│    │ Counterfactual  │    │     BugCard     │                │
│    │     Hook        │    │      Hook       │                │
│    └────────┬────────┘    └────────┬────────┘                │
│             │                      │                          │
│             │    ┌─────────────────┴─────────────────┐       │
│             │    │                                   │       │
│             ▼    ▼                                   ▼       │
│    ┌─────────────────────────────────────────────────────┐   │
│    │              HYPOTHESIS BOARD                        │   │
│    │         (VALIDATED state triggers hooks)            │   │
│    └─────────────────────────────────────────────────────┘   │
│                          │                                      │
│                          ▼                                      │
│    ┌─────────────────────────────────────────────────────┐   │
│    │                  BUGCARD / BUGFULL                    │   │
│    │         (creation/submission triggers hooks)        │   │
│    └─────────────────────────────────────────────────────┘   │
│                          │                                      │
│                          ▼                                      │
│    ┌─────────────────┐                                        │
│    │    Skeptic      │◀──── (手动/自动触发)                   │
│    │     Hook        │                                        │
│    └─────────────────┘                                        │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

---

## 执行示例

### 示例: Counterfactual Hook 执行

```
场景: 假设 "顶点着色器 input.w 接近 0 导致 NaN" 已被验证

Hook 触发 → 检查必要条件:
─────────────────────────────────────────
问题: "如果不满足 input.w 接近 0，NaN 是否消失？"

执行反事实验证:
1. 创建一个测试 DrawCall
2. 将所有 input.w 强制设为 1.0
3. 渲染并观察结果

结果: NaN 消失 ✓

Hook 结论: 必要条件验证通过
```

### 示例: Skeptic Hook 执行

```
场景: 提交结论 "纹理坐标计算错误导致纹理采样偏移"

Hook 触发 → 证据充分性质疑:
─────────────────────────────────────────
问题: "这个结论基于多少证据？"
答案: 2 项
  - Shader 代码中 UV 计算公式
  - 异常像素的 UV 值输出

问题: "是否存在选择性证据？"
答案: 否，已对比正常 DrawCall

Hook 触发 → 替代解释质疑:
─────────────────────────────────────────
问题: "还有什么其他可能的原因？"
答案: 
  1. 纹理坐标偏移可能来自顶点数据
  2. 可能来自纹理坐标变换矩阵
  3. 可能来自 Mipmap LOD 计算

结论: 需要进一步排除替代解释
```

---

**相关文档**:
- [Hypothesis Board](./hypothesis_board.md)
- [BugCard Template](../cases/bugcards/bugcard_template.md)
- [BugFull Template](../cases/bugfulls/bugfull_template.md)
- [Invariant Library](../invariants/invariant_library.md)
