# 专家行为强约束

## 概述

本文档定义了 AIRD 框架中所有专家 Agent 必须严格遵守的三条核心行为准则。这些约束是框架的基础，确保调试过程的科学性、严谨性和可靠性。

**所有 Agent 在执行任务时必须100%遵守这些约束，任何违反都将导致结论被标记为不可靠。**

---

## 约束一：禁止从症状直接推断根因

### 原文

> **观察 → 假设 → 验证 → 结论**
> 
> 禁止在完成系统性观察和分析之前提出因果推断。

### 详细说明

调试是一个严格的科学过程，必须遵循"观察-假设-验证-结论"的逻辑链条。

#### 错误行为示例

❌ **直接推断**:
```
观察到: 像素显示为黑色
推断:   一定是顶点着色器没有输出颜色
```

❌ **过早结论**:
```
观察到: 纹理显示错误
结论:   纹理加载失败
```

❌ **假设即结论**:
```
假设:   可能是 shader 错误
直接:   得出结论"shader 有 bug"
```

#### 正确行为示例

✅ **观察优先**:
```
1. 观察: 像素 (x,y) 显示黑色
2. 检查: 管线状态 - Color Write Enable = TRUE
3. 检查: Pixel Shader 输出 = (0,0,0,1)
4. 检查: 顶点着色器输出位置 = (0.5, 0.5, 0, 1)
5. 假设: 顶点位置在视锥体外
6. 验证: 对比 Viewport 设置
```

✅ **系统性分析**:
```
观察到异常后:
1. 记录异常特征
2. 收集相关管线状态
3. 收集相关 API 调用
4. 分析可能的假设空间
5. 逐一验证假设
6. 得出结论
```

### 执行规则

```yaml
constraint_1_rules:
  name: "禁止从症状直接推断根因"
  
  mandatory_sequence:
    - step: 1
      name: "症状观察"
      description: "记录异常表现的完整特征"
      required: true
    - step: 2
      name: "状态收集"
      description: "收集相关的管线状态/API调用/Shader代码"
      required: true
    - step: 3
      name: "假设形成"
      description: "基于证据提出可验证的假设"
      required: true
    - step: 4
      name: "假设验证"
      description: "通过反事实验证假设"
      required: true
    - step: 5
      name: "结论"
      description: "基于验证结果得出结论"
      required: true
  
  forbidden_shortcuts:
    - "观察到症状后直接提出根因"
    - "假设未经验证即作为结论"
    - "跳过状态收集直接推断"
    - "选择性收集证据支持假设"
  
  verification_trigger:
    - "Skeptic Agent 审查时"
    - "提交 BugCard 时"
    - "Team Lead 评审时"
```

### 触发场景

| 场景 | 要求 |
|------|------|
| 发现异常渲染结果 | 必须先观察、记录，再分析 |
| 创建 Hypothesis | 必须引用具体观察证据 |
| 验证假设 | 必须有明确的验证步骤和预期结果 |
| 提交结论 | 必须有完整的假设-验证链 |

---

## 约束二：优先使用差异调试

### 原文

> **A/B 对比优先于单次观察**
> 
> 调试时优先通过对比正常/异常状态的差异来定位问题。

### 详细说明

差异调试是定位渲染问题的最有效方法。通过对比正常和异常状态的差异，可以快速缩小问题范围，直指根因。

#### 错误行为示例

❌ **单点分析**:
```
只检查异常 DrawCall 的状态
不对比正常 DrawCall
```

❌ **盲目猜测**:
```
"让我检查一下顶点着色器"
没有目标地检查各种可能
```

❌ **忽略基准**:
```
只关注"异常是什么"
不关注"正常是什么"
```

#### 正确行为示例

✅ **帧对比**:
```
1. 选择一个异常帧
2. 选择一个正常帧（相同 DrawCall）
3. 使用 Frame Diff 对比差异
4. 定位差异发生的位置
5. 分析差异原因
```

✅ **参数扫描**:
```
1. 保持其他参数不变
2. 改变单一参数 X
3. 观察结果变化
4. 确定 X 与问题的因果关系
```

✅ **状态二分**:
```
1. 将管线状态分为两组
2. 逐组启用/禁用
3. 定位问题所在的组
4. 递归细分
```

### 差异调试 SOP

```yaml
constraint_2_sop:
  name: "差异调试 SOP"
  
  step_1_baseline:
    action: "建立基准"
    description: |
      找到或构造一个"正常"的参照状态
      - 相同 DrawCall 的正常帧
      - 相同场景的正常渲染结果
      - 预期的正确输出
    tools:
      - Frame Comparison
      - API Search (相同 DrawCall)
  
  step_2_comparison:
    action: "差异对比"
    description: |
      系统性对比正常和异常状态的差异
      - 管线状态差异
      - Shader 输入输出差异
      - 纹理数据差异
      - 顶点数据差异
    tools:
      - Frame Diff
      - Pipeline State Diff
      - Shader Diff
  
  step_3_localization:
    action: "差异定位"
    description: |
      定位第一个差异点
      - 差异在哪个阶段产生
      - 差异的传播路径
    tools:
      - Event Browser
      - Pass Graph
  
  step_4_root_cause:
    action: "根因分析"
    description: |
      分析导致差异的根本原因
      - 为什么这里会产生差异
      - 什么参数/状态导致了差异
```

### 差异调试工具链

| 工具 | 用途 |
|------|------|
| Frame Diff | 对比两帧的渲染结果差异 |
| Pipeline State Diff | 对比管线状态差异 |
| Shader Diff | 对比 Shader 代码差异 |
| Event Browser | 对比 API 调用序列 |
| Pixel History | 对比像素的渲染历史 |
| Texture Diff | 对比纹理数据差异 |

### 触发场景

| 场景 | 差异调试要求 |
|------|-------------|
| 渲染结果异常 | 必须对比正常帧 |
| Shader 输出异常 | 必须对比正常 Shader 输出 |
| 状态不一致 | 必须列出具体差异项 |
| 无法定位问题时 | 优先使用差异方法 |

---

## 约束三：根因必须有反事实验证

### 原文

> **反事实验证**
> 
> 每个被声称的根因都必须经过"如果不 X，则 Y 不会发生"的反事实验证。

### 详细说明

仅仅观察到相关性不足以得出因果结论。必须通过反事实验证来确认因果关系——即证明"如果 X 不发生，则 Y 不会发生"。

#### 错误行为示例

❌ **相关性当因果**:
```
观察到: 纹理坐标 UV 和错误像素位置相关
结论:   "纹理坐标计算错误是根因"
```

❌ **单一观察验证**:
```
观察到: 修改 A 后问题消失
结论:   "A 是根因"
(可能存在其他因素)
```

❌ **不完整验证**:
```
假设: X 导致 Y
验证: 只验证 X 存在时 Y 存在
未验证: X 不存在时 Y 是否消失
```

#### 正确行为示例

✅ **必要条件验证**:
```
假设: input.w 接近 0 导致 NaN
验证: 
  1. 构造 input.w = 1.0 的场景
  2. 观察是否仍然产生 NaN
  3. 如果 NaN 消失，则必要条件满足
```

✅ **充分条件验证**:
```
假设: input.w 接近 0 导致 NaN
验证:
  1. 找到一个 input.w 接近 0 的正常 DrawCall
  2. 观察是否产生 NaN
  3. 如果产生，则充分条件可能满足
```

✅ **替代解释排除**:
```
假设: 纹理坐标错误是根因
验证:
  1. 列出其他可能原因
  2. 逐一排除
  3. 确认纹理坐标错误是唯一可能
```

### 反事实验证模板

```yaml
counterfactual_verification:
  hypothesis_id: "HYP-XXX-001"
  hypothesis: "X 导致 Y"
  
  # 必要条件: 如果非 X，则非 Y
  necessary_condition:
    question: "如果不满足 X，Y 是否消失？"
    method: |
      构造 X 不成立的场景
      观察 Y 是否仍然发生
    pass_criteria: "Y 消失"
    fail_criteria: "Y 仍然发生"
    evidence_required: [screenshot, shader_output]
  
  # 充分条件: 如果 X，则 Y
  sufficient_condition:
    question: "如果 X 成立，Y 是否必然发生？"
    method: |
      构造 X 成立的场景
      观察 Y 是否发生
    pass_criteria: "Y 发生"
    fail_criteria: "Y 不发生（存在其他必要条件）"
    evidence_required: [screenshot, shader_output]
  
  # 替代解释排除
  alternative_explanation:
    question: "是否存在其他导致 Y 的原因？"
    alternatives:
      - alt_1: "可能的替代原因 1"
        ruled_out: true/false
        evidence: "排除/支持依据"
      - alt_2: "可能的替代原因 2"
        ruled_out: true/false
        evidence: "排除/支持依据"
    pass_criteria: "所有替代解释被排除或被验证"
```

### 执行规则

```yaml
constraint_3_rules:
  name: "根因必须有反事实验证"
  
  required_for_conclusion:
    - "BugCard 标记为 RESOLVED"
    - "BugFull 标记为 COMPLETE"
    - "Hypothesis 标记为 VALIDATED"
  
  verification_types:
    necessary:
      description: "验证 X 是 Y 的必要条件"
      question: "如果非 X，则非 Y？"
    
    sufficient:
      description: "验证 X 是 Y 的充分条件"
question: "如果 X，则 Y？"
    
    alternative:
      description: "排除替代解释"
      question: "是否存在其他原因？"
  
  min_verifications: 2
  # 至少需要必要条件验证 + 替代解释排除
  
  evidence_requirements:
    - type: "before_after"
      description: "修改前后的对比截图"
    - type: "state_snapshot"
      description: "验证时的状态快照"
    - type: "test_case"
      description: "构造的测试用例"
```

### 触发场景

| 场景 | 反事实验证要求 |
|------|---------------|
| 假设被验证 | 必须包含反事实验证 |
| 提交 BugCard | 根因必须有反事实证据 |
| 提交 BugFull | 根因必须有反事实证据 |
| Team Lead 评审 | 审查反事实验证完整性 |

---

## 三条约束的协同

### 完整调试流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    COMPLETE DEBUGGING FLOW                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 步骤 1: 观察症状 (Constraint 1: No inferring)              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 步骤 2: 收集状态 (Constraint 1: Systematic observation)   │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 步骤 3: 形成假设 (Constraint 1: Evidence-based hypothesis)  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 步骤 4: 差异调试 (Constraint 2: A/B comparison)            │ │
│  │         - 建立基准                                          │ │
│  │         - 对比差异                                          │ │
│  │         - 定位问题                                          │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 步骤 5: 反事实验证 (Constraint 3: Counterfactual)           │ │
│  │         - 必要条件验证                                      │ │
│  │         - 充分条件验证                                      │ │
│  │         - 替代解释排除                                      │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ 步骤 6: 结论                                               │ │
│  │         - 结论有据                                         │ │
│  │         - 修复有方                                         │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 约束检查清单

```yaml
debugging_checklist:
  before_conclusion:
    - [ ] 约束1: 是否完成系统性观察？
    - [ ] 约束1: 假设是否基于证据而非推断？
    - [ ] 约束2: 是否使用了差异调试方法？
    - [ ] 约束2: 是否建立了正常基准进行对比？
    - [ ] 约束3: 是否进行了反事实验证？
    - [ ] 约束3: 是否验证了必要条件和充分条件？
    - [ ] 约束3: 是否排除了替代解释？
  
  quality_gates:
    - [ ] Hypothesis Board 状态为 VALIDATED
    - [ ] BugCard 包含完整的验证证据
    - [ ] BugFull 包含反事实验证记录
    - [ ] Skeptic Agent 已审查
```

---

## 违规处理

### 违规类型

| 违规类型 | 描述 | 处罚 |
|---------|------|------|
| 轻微违反 | 跳过部分观察步骤但结论正确 | 警告，要求补充 |
| 中度违反 | 假设未经充分验证 | 标记为 UNVERIFIED，退回重审 |
| 严重违反 | 从症状直接推断根因 | 结论无效，重新开始 |
| 极端违反 | 伪造证据 | 清除案例，永不录用 |

### 申诉流程

如果 Agent 认为约束判定有误，可以：
1. 提供完整的决策链证据
2. 说明约束是否适用于当前场景
3. 由 Team Lead 重新评审

---

## 培训与执行

### Agent 培训要求

所有 Agent 必须通过以下培训：

1. **基础培训**: 三条约束的文字学习
2. **案例学习**: 违反约束的典型案例分析
3. **实践考核**: 在模拟场景中正确执行约束

### 持续监督

- **Skeptic Agent**: 专门负责质疑违反约束的行为
- **Team Lead**: 定期审查约束执行情况
- **BugCard 审核**: 提交前检查约束合规性

---

**相关文档**:
- [Hypothesis Board](./hypothesis_board.md)
- [Quality Hooks](./quality_hooks.md)
- [BugCard Template](../cases/bugcards/bugcard_template.md)
- [Invariant Library](../invariants/invariant_library.md)
