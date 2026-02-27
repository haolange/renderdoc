# AIRD Framework · 完全体开发路线图

> 目标：将 debug-agent（除 rdx-mcp 工具层外）从「骨架与文档」完善到设计完全体，并在自进化、质量约束、多平台一致性三个维度实现超越。
> 创建于：2026-02-27

---

## 背景决策记录

| 决策项 | 选择 | 说明 |
|--------|------|------|
| 目标平台 | **全平台同步**（Claude Code / Claude Work / Copilot / MiniMax / Manus） | 先建核心语义层，再为各平台生成适配版本 |
| Agent 知识注入方式 | **运行时动态加载**（prompt 保持精简，声明加载路径） | 避免 prompt 膨胀，知识文件独立维护 |
| Quality Hooks 实现 | **有原生机制用系统级，无则降级到 Prompt 层** | Claude Code 用 settings.json Hooks + 验证脚本；其余平台 Prompt 内嵌检查清单 |
| 知识库格式 | **YAML**（与现有 BugCard/BugFull 格式一致） | 可被 rd.kb.search 索引，兼顾程序可读与人工可维护 |
| 超越方向 | **全部实现**（跨设备指纹图谱 + 反事实评分规范 + Action Chain 自动记录 + SOP 草稿提取） | 见 M5 |

---

## 里程碑总览

```
M1（知识层结构化）
  └─► M2（Agent Prompt 重写）
        └─► M3（多平台适配）
              └─► M4（Quality Hooks）
M2 完成后可并行 ──► M5（自进化基础设施）
M2 完成后可并行 ──► M6（Project Plugin + 收尾）
```

---

## M1 · 知识层结构化

**状态：** 🔲 未开始
**依赖：** 无
**目标：** 将现有散文知识文档转换为机器可读的 YAML，建立可程序化查询的知识基础

### 产出物

| 文件 | 来源 | 说明 |
|------|------|------|
| `common/invariants/invariant_library.yaml` | 现有 `invariant_library.md` | 每条不变量含完整结构化字段 |
| `common/taxonomy/symptom_taxonomy.yaml` | 现有 `symptom_taxonomy.md` | 症状标签结构化 |
| `common/taxonomy/trigger_taxonomy.yaml` | 现有 `trigger_taxonomy.md` | 触发条件标签结构化 |
| `common/skills/sop_library.yaml` | 现有 `sop_library.md` | 6 条 SOP 结构化，含触发条件、目标不变量、工具链 |

### invariant_library.yaml 每条字段规范

```yaml
- id: I-NAN-01
  name: "输出值必须有限"
  category: Numerical
  description: "..."
  symptom_tags: [white_spot, flickering, black_pixel]
  trigger_tags: []
  typical_root_causes:
    - normalize(zero_vector)
    - half_overflow
    - division_by_zero
  fix_patterns:
    - SafeNormalize
    - Clamp
    - CastToFloat
  linked_sop: SOP-NAN-01
  detection_tools:
    - rd.texture.get_data
    - rd.event.get_pixels
    - rd.shader.get_debug
  example_cards:
    - BUG-PREC-001
```

### 验收标准

- [ ] 所有 YAML 文件可被 Python `yaml.safe_load()` 无错加载
- [ ] invariant_library.yaml 每条记录字段无缺失（含 linked_sop、example_cards）
- [ ] symptom → SOP 的映射可通过 Python 一行代码查询
- [ ] 与现有 `config/mcp_tools.json` 的 invariant_mcp_association 字段对齐

---

## M2 · Agent Prompt 全面重写

**状态：** ✅ 已完成（2026-02-27）
**依赖：** M1
**目标：** 9 个 Agent 的 prompt 从当前 10–30 行骨架增强为完整的行为规范，覆盖设计文档所有质量门槛

### 产出物

- `common/agents/` 目录（新建）：存放 9 个 Agent 的**平台无关核心版本** prompt
- 每个 core prompt 包含：
  1. **角色定义与职责边界**（严格 Delegate Mode，禁止越界）
  2. **动态加载声明**（声明运行时应读取的知识文件路径）
  3. **Agent 间消息 Schema**（强制遵守的输入/输出格式）
  4. **强制质量约束**（内嵌检查清单，作为 Prompt 层降级保障）
  5. **质量门槛**（设计文档角色表中每个角色的具体门槛）

### 9 个 Agent 核心增强点

| Agent | 关键增强 |
|-------|---------|
| Team Lead | Hypothesis Board 状态机内嵌逻辑（ACTIVE/VALIDATED/REFUTED）；Delegate Mode 强制；Skeptic 未签署禁止结案 |
| Triage & Taxonomy | 动态加载 symptom_taxonomy.yaml + invariant_library.yaml；输出严格 schema；只分类不推断根因 |
| Capture & Repro | A/B 对比捕获强制规范；Anchor 明确性验证；可重放性检查 |
| Pass Graph / Pipeline | RenderGraph 差分分析流程；必须缩小到 Pass 级别的质量门槛 |
| Pixel / Value Forensics | First Bad Event 追踪流程；数值范围检查清单；NaN/Inf 传播路径要求 |
| Shader & IR | HLSL↔IR↔SPIR-V↔ISA 关联分析流程；差分证据强制要求 |
| Driver / Device Specialist | 「内容必现」排除流程；跨设备对比规范；驱动归因裁决标准 |
| Skeptic | 反证三要素（反例/遗漏假设/因果质疑）；所有质疑被回应前禁止结案 |
| Report & Knowledge Curator | BugCard 完整性清单（所有必填字段）；Action Chain 记录格式；SOP 索引更新流程 |

### 超越点

Team Lead 内嵌 Hypothesis Board 状态机，使其在 prompt 层能自我维护假设生命周期，无需外部状态存储即可运行。

### 验收标准

- [ ] 每个 Agent 的 core prompt 覆盖设计文档角色描述表中所有职责和质量门槛
- [ ] 每个 prompt 有明确的动态加载声明（列出应加载的文件路径）
- [ ] Agent 间消息 schema 在发送方和接收方 prompt 中双向一致
- [ ] Skeptic prompt 的反证流程覆盖设计文档中的三类质疑模式

---

## M3 · 多平台适配层生成

**状态：** ✅ 已完成（2026-02-27）
**依赖：** M2
**目标：** 基于 M2 的核心 prompt，为 5 个平台生成格式适配版本，保持语义一致

### 产出物

| 平台 | 适配形式 | 特殊处理 |
|------|---------|---------|
| Claude Code | 独立 agent 文件 + `CLAUDE.md` 共享上下文声明 | 动态加载通过文件系统路径实现 |
| Claude Work | plugin.json 更新 + agent 文件 | 保持现有结构 |
| Copilot | 独立 agent 文件格式适配 | — |
| MiniMax | 保持合并格式，内容同步更新 | 多 Agent 合并为单文件 |
| Manus | 工作流文件同步 | 映射为工作流步骤 |

- `common/AGENT_CORE.md`（新建）：各平台适配时的「单一真相来源」，记录核心角色定义与质量规范的权威版本

### 验收标准

- [ ] 5 个平台的 Skeptic Agent 核心职责描述语义一致（可人工对照验证）
- [ ] 平台差异仅体现在格式层，不存在核心逻辑差异
- [ ] `common/AGENT_CORE.md` 完整覆盖所有 9 个角色

---

## M4 · Quality Hooks 系统

**状态：** ✅ 已完成（2026-02-27）
**依赖：** M2
**目标：** 将质量门槛从「被建议的」变为「被强制执行的」

### 产出物

```
common/hooks/
├── README.md                        # Hooks 系统说明与扩展指南
├── validators/
│   ├── bugcard_validator.py         # BugCard 必填字段完整性检查
│   ├── counterfactual_validator.py  # 反事实验证记录检查
│   └── skeptic_signoff_checker.py   # Skeptic 签署状态检查
└── schemas/
    ├── bugcard_required_fields.yaml # BugCard 必填字段清单
    └── skeptic_signoff_schema.yaml  # Skeptic 签署记录格式
```

- `.claude/settings.json`（Claude Code 平台）：配置 PostToolUse + Stop 触发的 Hooks
- 其余平台（Copilot/MiniMax/Manus）：在对应 Agent prompt 中内嵌降级检查清单

### 三类 Hook 详细规范

**Hook 1：BugCard 完整性检查**
- 触发时机：Curator Agent 生成 BugCard 后（Stop 事件）
- 检查项：bug_family / invariants_broken / symptom_tags / trigger_tags / anchor / key_evidence / root_cause_one_liner / fix_one_liner / recommended_sop 全部非空
- 失败行为：输出缺失字段列表，要求补全

**Hook 2：Counterfactual 验证检查**
- 触发时机：Team Lead 尝试标记 Bug 为「已解决」时
- 检查项：evidence 集合中存在至少一条 `type: counterfactual_test` 且 `result: passed` 的记录
- 失败行为：拒绝结案，要求补充反事实验证记录

**Hook 3：Skeptic 签署检查**
- 触发时机：Team Lead 做出最终裁决前
- 检查项：Skeptic 的审查意见已提交，且所有质疑项状态为 `addressed`
- 失败行为：拒绝裁决，列出未回应的质疑项

### 验收标准

- [ ] Claude Code 平台：BugCard 缺失必填字段时，验证脚本能检测并输出具体缺失字段
- [ ] Claude Code 平台：settings.json hooks 配置语法正确
- [ ] 验证脚本可独立运行（`python validators/bugcard_validator.py <file>`）
- [ ] 无 Hook 平台的降级检查清单在对应 prompt 中明确标注「[质量门槛检查]」

---

## M5 · 自进化基础设施

**状态：** ✅ 已完成（2026-02-27）
**依赖：** M2（可与 M3/M4 并行）
**目标：** 为框架的自我进化建立数据记录标准和提取机制

### 产出物

| 文件 | 说明 |
|------|------|
| `common/docs/action_chain_schema.yaml` | Action Chain 标准记录格式（工具调用序列、假设状态变更、证据引用） |
| `common/docs/sop_extraction_guide.md` | 从 Action Chain 半自动提取 SOP 草稿的操作规范（含人工审核工作流） |
| `common/docs/cross_device_fingerprint_spec.md` | 跨设备指纹图谱规范（多 capture BugFull 的关联结构定义） |
| `common/docs/counterfactual_scoring_spec.md` | 反事实评分引擎规范（Patch 前后渲染差异的结构化评估标准） |
| `common/cases/action_chains/` | 新增目录，存放已记录的调试 Action Chain（`.jsonl` 格式） |
| `common/cases/action_chains/example_adreno_prec.jsonl` | 基于设计文档「Adreno 精度案例」的示范 Action Chain |

### Action Chain Schema 核心字段

```yaml
session_id: "dbg-20260301-001"
bug_id: "BUG-PREC-002"
start_time: "2026-03-01T10:00:00Z"
steps:
  - step_id: 1
    agent: triage
    action_type: tool_call          # tool_call | hypothesis_update | evidence_add | message_send
    tool: rd.capture.open_file
    params: {...}
    output_summary: "..."
    timestamp: "..."
  - step_id: 2
    agent: team_lead
    action_type: hypothesis_update
    hypothesis_id: "H-001"
    from_state: ACTIVE
    to_state: VALIDATED
    reason: "..."
outcome:
  verdict: resolved
  root_cause_invariant: I-PREC-01
  fix_applied: true
  counterfactual_passed: true
  total_steps: 42
  bugcard_id: BUG-PREC-002
```

### 跨设备指纹图谱结构（超越点）

```yaml
fingerprint_cluster_id: "FPC-PREC-001"
description: "Adreno RelaxedPrecision 精度降低类问题"
linked_invariant: I-PREC-01
captures:
  - device: Adreno 650
    platform: Android 12
    bugcard_id: BUG-PREC-001
    shader_fingerprint_id: "SF-001"
    symptom: hair_whitening
  - device: Adreno 740
    platform: Android 13
    bugcard_id: BUG-PREC-002
    shader_fingerprint_id: "SF-002"
    symptom: hair_blackening
cross_device_delta:
  - dimension: spirv_decoration
    adreno_650: RelaxedPrecision on diffuse
    adreno_740: RelaxedPrecision on specular
    mali_baseline: no RelaxedPrecision
  - dimension: symptom_direction
    note: "精度损失方向不同（溢出 vs 截断），根因相同"
driver_attribution: compiler_precision_lowering
recommended_sop: SOP-PREC-01
```

### 验收标准

- [ ] Action Chain schema 能完整描述设计文档「Adreno 案例」的 10 个阶段
- [ ] 示范 Action Chain 文件格式正确，可被 Python 逐行加载
- [ ] 跨设备指纹图谱规范有完整字段定义和两个设备的对比示例
- [ ] 反事实评分规范定义了「异常特征消失」的可量化判断标准
- [ ] SOP 提取指南包含「从哪些 Action Chain 字段提取工具链」的具体操作步骤

---

## M6 · Project Plugin 完整实例 + 框架收尾

**状态：** ✅ 已完成（2026-02-27）
**依赖：** M2（可与 M3/M4/M5 并行）
**目标：** 将 Project Plugin 从空白模板升级为完整规范，并更新所有顶层文档

### 产出物

| 文件 | 说明 |
|------|------|
| `common/project_plugin/PLUGIN_SPEC.md` | Plugin 接口规范（字段定义、填写指南、与框架的集成方式） |
| `common/project_plugin/example_mobile_game.yaml` | 示范 Plugin：虚构移动游戏项目，含材质模块化结构、Block 计算指纹、引擎资源映射 |
| `README.md` | 全面更新，反映完全体架构，包含快速上手指南 |
| `docs/多平台适配说明.md` | 更新为反映 M3 实际实现的版本（Markdown 版本，替代原 docx） |

### example_mobile_game.yaml 覆盖内容

```yaml
project_name: "ExampleMobileGame"
engine: UnrealEngine 5.3
target_platforms: [Android_Adreno, Android_Mali, iOS_Apple]
material_blocks:
  - block_id: BASE_COLOR_BLOCK
    description: "基础色彩模块"
    hlsl_fingerprint: "BaseColor = tex2D(DiffuseMap, UV) * TintColor"
    engine_asset_path: "Materials/M_Character_Base"
    parameters: [DiffuseMap, TintColor]
  - block_id: LIGHTING_BLOCK
    description: "光照计算模块"
    hlsl_fingerprint: "half diffuse = dot(N, L) * LightColor"
    engine_asset_path: "Materials/M_Character_Lighting"
    known_issues:
      - invariant: I-PREC-01
        note: "half 类型在 Adreno 上存在精度风险"
project_invariants:
  - id: P-METAL-01
    description: "角色材质金属度必须在 [0, 0.9] 范围内"
    rationale: "美术风格约束"
```

### 验收标准

- [ ] Plugin 示例可被 Agent prompt 的动态加载声明直接引用
- [ ] README 导航覆盖所有模块（M1–M5 产出物均有说明入口）
- [ ] Plugin 规范明确定义「框架只规定接口，不规定内容」的边界

---

## 风险登记

| 风险 | 概率 | 影响 | 对策 |
|------|------|------|------|
| Claude Code Hooks 触发时机与预期有偏差 | 中 | 中 | M4 中 Prompt 层约束作为保底，Hooks 作为加强层 |
| 各平台格式差异导致语义丢失 | 低 | 高 | M3 先建立核心版本，适配层只做格式转换；AGENT_CORE.md 作为单一真相来源 |
| Action Chain schema 设计过重，人工填写成本高 | 中 | 中 | M5 提供最小必填字段集，其余为可选扩展 |
| 多平台同步维护成本随时间累积 | 高 | 中 | AGENT_CORE.md 的存在使未来修改只需改一处，各平台适配层自动跟进 |

---

## 进度追踪

| 里程碑 | 状态 | 完成时间 | 备注 |
|--------|------|---------|------|
| M1 知识层结构化 | ✅ 已完成 | 2026-02-27 | 4 个 YAML 全部通过验收，23 invariants / 7 SOPs / 37 symptom 索引条目 |
| M2 Agent Prompt 重写 | ✅ 已完成 | 2026-02-27 | 9 个 Agent 全部完成，共 1514 行，结构验收 100% 通过 |
| M3 多平台适配 | ✅ 已完成 | 2026-02-27 | 5 平台全覆盖；AGENT_CORE.md 建立；Skeptic 五把刀语义一致性 100% |
| M4 Quality Hooks | ✅ 已完成 | 2026-02-27 | 3 个验证脚本 + 2 个 Schema + settings.json；Python 语法 + JSON/YAML 格式全部通过 |
| M5 自进化基础设施 | ✅ 已完成 | 2026-02-27 | 4 个规范文档 + 34 步示范 Action Chain（S_cf=0.97）；SOP 提取 / 跨设备指纹图 / 反事实评分全部落地 |
| M6 Plugin + 收尾 | ✅ 已完成 | 2026-02-27 | PLUGIN_SPEC + 完整示例（5 材质模块 / 3 项目不变量）+ 多平台适配说明 + README v2.0 全面重写 |

---

*路线图版本：v2.0 · 2026-02-27*
*🎉 M1 ✅ M2 ✅ M3 ✅ M4 ✅ M5 ✅ M6 ✅ — AIRD Framework 完全体开发完成。*
