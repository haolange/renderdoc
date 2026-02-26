# AIRD Framework 现状差距与超越路径分析报告

> **AIRD（AI-driven Invariant-Reasoning Debugger）**
> 一个以不变量为灵魂、以多智能体协作为骨骼、以工具执行为血脉的渲染调试框架
> 本报告：从「已建成的」到「设计的完全体」，再到「超越」的三级距离分析

---

## 执行摘要

你正在建造一座精密的哥特式大教堂——设计图纸宏伟壮观，主体轮廓已然耸立，然而部分拱顶悬空，飞扶壁尚未闭合，地下管道的连通仍靠人工搬运代为维系。

现有实现覆盖了设计的约 **55–65%**。已完成的部分扎实可靠：工具层（rdx-mcp）功能丰富、建模精确，知识层（Invariant Library、BugCard/BugFull）规范清晰，Agent 框架的角色定义言之有物。但从设计文档所描绘的「自进化调试生命体」到现实中的「精良工具集合」，仍有数条关键裂缝尚待弥合。

---

## 目录

1. [架构全景：设计 vs 实现对照图](#一架构全景设计-vs-实现对照图)
2. [知识层：不变量驱动的推理尚未闭环](#二知识层不变量驱动的推理尚未闭环)
3. [工具层：196 把利刃，却缺少握刀的手](#三工具层196-把利刃却缺少握刀的手)
4. [协作层：Agent 团队存在于纸面，协议悬浮于空中](#四协作层agent-团队存在于纸面协议悬浮于空中)
5. [质量层：Quality Hooks 是被写下的誓言，不是运行的代码](#五质量层quality-hooks-是被写下的誓言不是运行的代码)
6. [学习层：自进化闭环是设计的皇冠，现在还锁在玻璃柜](#六学习层自进化闭环是设计的皇冠现在还锁在玻璃柜)
7. [多平台适配：五个平台，五种方言，零个共同语法树](#七多平台适配五个平台五种方言零个共同语法树)
8. [超越设计的可能性：三个尚未被命名的方向](#八超越设计的可能性三个尚未被命名的方向)
9. [优先级路线图](#九优先级路线图)
10. [总结：你已建好的，和你尚欠的](#十总结你已建好的和你尚欠的)

---

## 一、架构全景：设计 vs 实现对照图

设计文档（三份 .docx）将 AIRD 框架划分为五个层次：**知识层 → 工具层 → 协作层 → 质量层 → 自进化层**。下表呈现各层的实现成熟度：

| 层次 | 设计意图 | 已实现 | 缺失 / 裂缝 | 成熟度 |
|------|----------|--------|------------|--------|
| **知识层** | Invariant Library + SOP Library + Case Library + Project Plugin | 不变量库完备（20+ invariant）；BugCard/BugFull 格式规范；2 个真实案例；SOP 6 条已文档化 | 不变量与 SOP 未做机器可读的结构化绑定；Project Plugin 仅有 template，无实例；BugCard 检索尚无向量语义维度 | ★★★☆☆ |
| **工具层** | RDX-MCP 作为执行底层，覆盖 Capture/Replay/Shader/Pipeline/Perf/Export 全域 | 196 工具文档化 + 3 扩展工具；Pydantic 强类型建模；S0–S7 Pipeline 骨架存在；BM25 知识库检索；指纹匹配 | rdx-mcp 核心服务大量为「存根」（stub）；真实 RenderDoc Python 绑定的集成深度未知；Patch Engine 设计完整但未与 Agent 自动联动 | ★★★☆☆ |
| **协作层** | 9 Agent（Team Lead + 6 专家 + Skeptic + Curator）的 Multi-Agent 团队，有 Mailbox 通信和共享任务列表 | Agent 角色 prompt 存在（但极度精简：10–30 行）；5 个平台均有 Agent 定义；协作规范文档（agent_collaboration.md）完整 | Agent prompt 与设计文档的质量要求相差悬殊；无真实 Agent 间消息路由实现；Hypothesis Board 没有状态机实现 | ★★☆☆☆ |
| **质量层** | Counterfactual Hook + BugCard Hook + Skeptic Hook，可编程拦截关键决策点 | 三类钩子在文档中有完整规范（quality_hooks.md）；检查清单结构清晰 | 零行代码实现；Hook 在 Agent prompt 中也未被引用为强制约束 | ★☆☆☆☆ |
| **自进化层** | Action Chain → SOP 自动生成 → 知识库增长 → 调试效率正反馈 | 设计文档描述了三阶段路线（RAG → SFT → RFT）；KB connector 支持离线索引 | 无 Action Chain 自动记录；无 SOP 自动提取机制；BugCard 的积累仅靠人工 | ★☆☆☆☆ |

---

## 二、知识层：不变量驱动的推理尚未闭环

### 2.1 已建成的地基

Invariant Library 是这个框架最漂亮的部分之一。它以 YAML 格式精确定义了 20+ 不变量，涵盖数值（NaN/Inf）、几何（可见性/环绕顺序/索引）、颜色（色彩空间/范围）、纹理（UV/Mipmap/LOD）、深度、Shader、精度、光照、Alpha/Blend、性能十个维度。每条不变量都配备了：检测方法、关联症状、根因清单、HLSL 修复模板。

这是一个经过思考的领域模型，不是简单的 checklist，是把「渲染为什么出错」这件事系统化成了可推理的公理体系。

`mcp_tools.json` 实现了从 SOP 到工具的映射，`symptom_sop_mapping` 也构建了症状标签到 SOP 的路由表。这是知识层与工具层之间一座真实存在的桥梁。

### 2.2 尚未弥合的裂缝

**裂缝一：不变量库缺乏机器可读的结构绑定**

现有的 `invariant_library.md` 本质是人类可读的文档，而非 Agent 可以程序化调用的知识图谱。设计文档明确描述了每条不变量应有 `typical_root_causes`、`fix_patterns`、`linked_sop`、`example_cards` 字段，但实现中这些字段仅存在于散文叙述里，不存在于可被 `rd.kb.search` 或 Agent 直接消费的结构化格式（JSON/YAML/SQLite）中。

当前状况：Triage Agent 需要手动阅读 invariant_library.md 才能知道 `I-NAN-01` 关联 `SOP-NAN-01`。
理想状况：Triage Agent 调用 `rd.kb.search(symptom_tags=["white_spot"])` 即可获得结构化响应，包含关联不变量、推荐 SOP、历史相似案例打分。

**裂缝二：Invariant → Hypothesis Board 的自动推导尚未实现**

设计文档描述了一个清晰的流程：症状观察 → 标签匹配 → 候选不变量列表 → 逐一验证 → 确认违反的不变量 → 生成 Hypothesis Board 条目。

这个流程在文档中是流程图，在代码中是虚空。没有任何 Agent 在接到症状描述后，会自动查询不变量库、构造假设条目并进入验证循环。

**裂缝三：Project Plugin 是一张空白处方**

设计文档对 Project Plugin 的描述充满价值：它应包含材质模块化数据流定义、Block 计算指纹（即从截帧 Shader/IR 反推引擎 Block）、Block 与引擎资源映射。这是让框架从「通用 GPU 调试器」跃升为「项目原生诊断助理」的核心差异点。

然而 `common/project_plugin/template.md` 里是一张空白模板，无任何真实项目数据填充，也无将 Plugin 数据注入 Agent 上下文的机制。

**裂缝四：BugCard 的检索维度单薄**

现有 BM25 检索基于词频，而渲染 Bug 的相似性往往不是词汇相似，是「症状拓扑相似」——比如两个截然不同描述的 Bug 可能都违反了 `I-PREC-01` 且都发生在 `Adreno GPU` 上。指纹匹配系统（`fingerprint_store.py`）设计存在，但与 BugCard 检索的融合尚未打通。

---

## 三、工具层：196 把利刃，却缺少握刀的手

### 3.1 已建成的地基

rdx-mcp 是这个项目里代码量最丰富、架构最完整的部分。它建立了：

- **199 工具的注册驱动架构**：通过 `tool_catalog_196.json` 驱动动态注册，优雅地解决了工具膨胀问题。
- **严格的响应契约**：所有工具返回 `{success: bool, ...}` 的标准格式，异常也有 `error_message` 字段，这是工程质量的体现。
- **S0–S7 八阶段 Pipeline 骨架**：`workflows.py` 描述了从 Intake 到 Report 的完整自动化流程，结构清晰、边界明确。
- **内容寻址的 Artifact Store**：SHA256 分片存储，原子写入，这是生产级别的存储设计。
- **UE 模块映射启发式**：`kb_connector.py` 中有将 Shader 资源名映射到 UE 模块的逻辑（如 `SceneColor → PostProcessing`），这已经超越了纯工具层，进入了领域知识推理的边界。

### 3.2 尚未弥合的裂缝

**裂缝五：核心服务大量为存根（Stub），尚未真实落地**

`server.py` 中的 dispatch 函数普遍存在「参数解析完整，实际执行缺失」的现象。远程设备工具（`_dispatch_remote`）和应用内 API（`_dispatch_app`）整组标注为「optional / graceful degradation」。更关键的是，`patch_engine.py`（Shader Patch 与热替换）这一在设计文档中被视为「反事实验证」核心支柱的组件，其与 Agent 的联动接口并不存在。

当前：Agent 可以读取 Shader 源码，分析 IR，但无法通过工具链自动构造最小 Patch 并验证效果。
设计要求：S5 阶段（Hypothesis and Patch Loop）应支持 Agent 驱动的迭代实验：提出假设 → 构造 Patch → 执行替换 → 读取渲染结果 → 更新假设状态。

**裂缝六：S0–S7 Pipeline 是骨架，肌肉和神经尚未连接**

`run_full_debug` 工具调用了 `workflows.py` 中的 `run_full_debug_pipeline`，但各阶段的输出如何传递给下一阶段、如何反馈给 Agent、如何触发质量钩子——这些关键的神经连接并不存在。

Pipeline 目前是「一次性顺序执行」，而设计描述的是「循环迭代、可中断、可人工介入、Skeptic 可在任意点注入质疑」的动态过程。

**裂缝七：性能计数器层空洞**

`perf_service.py` 和相关工具文档化了 GPU 性能计数器的接口，但设计文档中提及的「瓶颈自动检测」（bottleneck detection）、「Pipeline Stall 根因分析」是需要将计数器数据与 API 调用序列联合分析才能完成的高阶推断，这一层完全空白。

---

## 四、协作层：Agent 团队存在于纸面，协议悬浮于空中

### 4.1 已建成的地基

九个 Agent 角色均有 Markdown 定义文件，角色边界描述清晰，质量门槛有明确文字说明。设计文档对 Multi-Agent 架构的哲学论证（为什么选择多 Agent 而非单一全能 Agent）相当有说服力：任务可并行化、需要深度专业知识、避免上下文污染。

Skeptic 角色的设计尤其出色——将「确认偏误」制度化为一个角色的职责，这是一个理念层面的创新。

### 4.2 尚未弥合的裂缝

**裂缝八：Agent Prompt 的深度与设计要求相差一个数量级**

以 Team Lead 为例：设计文档中描述它需要「维护 Hypothesis Board、追踪证据门槛、执行委托模式（Delegate Mode）、在 Skeptic 签署前禁止结案」；而 `01_team_lead.md` 的 prompt 只有 30 行，核心约束只有寥寥几句话。

这不是批评简洁，而是观察到一个现实：当 Agent 真正运行时，遇到歧义情境、证据冲突、假设竞争等复杂状况，现有 prompt 的信息密度不足以驱动设计文档所期望的行为。

类似地，Triage Agent 的 prompt 没有嵌入症状分类学（symptom_taxonomy.md）；Skeptic 的 prompt 没有引用反事实验证规则（quality_hooks.md）；Curator 的 prompt 没有嵌入 BugCard 完整性检查规范。

**裂缝九：Agent 间消息协议是设计文档里的 JSON 范例，不是运行中的通信基础设施**

`agent_collaboration.md` 描述了 `[TRIAGE_RESULT]`、`[FORENSICS_REQUEST]`、`[FORENSICS_RESULT]` 等结构化消息格式。但在任何平台实现中（claude-code、copilot、minimax），都没有任何机制确保这些格式被遵守，也没有消息路由、任务队列、共享状态存储的实际实现。

在 Claude Code 平台，Agent 间通信是通过文件系统的（Claude Code 原生机制），但并无框架确保通信内容符合协作规范定义的 schema。

**裂缝十：Hypothesis Board 是 YAML 文档，不是状态机**

设计文档中的 Hypothesis Board 有完整的生命周期（ACTIVE → VALIDATE → VALIDATED/REFUTED → ARCHIVED/SPLIT）和管理 SOP（SOP-HYP-01/02/03）。实现中它是一个 `.md` 文件模板，没有状态转换触发器，没有条件检查，没有与 Quality Hooks 的联动。

---

## 五、质量层：Quality Hooks 是被写下的誓言，不是运行的代码

这是整个框架中实现与设计落差最大的一层。

`quality_hooks.md` 是框架中最精心设计的文档之一：它定义了三类钩子（Counterfactual Hook、BugCard Hook、Skeptic Hook）、详细的验证规则、YAML 配置格式、按 Agent 类型的触发矩阵，以及具体的执行示例。

**然而在整个代码库中，这三类钩子零行代码实现。**

不仅如此，在任何 Agent 的 prompt 里，这些钩子也没有被作为强制约束引入。Agent 被告知「要有反事实验证」，但没有任何机制在它尝试结案时拦截并强制执行这一要求。

这是设计理念中最核心的质量保障机制——「通过代码化的钩子将抽象质量要求转化为具体刚性约束」——与实现现状之间最深的鸿沟。

---

## 六、学习层：自进化闭环是设计的皇冠，现在还锁在玻璃柜

### 6.1 设计描述的完全体

设计文档中「自迭代能力闭环」是整个框架最雄心勃勃的愿景：每一次调试产生完整 Action Chain → Curator 将其沉淀为 BugFull → 系统自动提取高效 Action Chain 生成新 SOP → 新 SOP 使下一次调试更高效 → 知识库增长与调试效率形成正反馈。

三阶段演进路线：RAG（当前阶段）→ SFT（监督微调，数百案例后）→ RFT（强化学习，特定可量化子任务）。

### 6.2 现实中的鸿沟

**Action Chain 没有自动记录机制。** Agent 执行的每个工具调用、每次假设状态变更、每个证据引用，均未被自动序列化为可用于后续学习的结构化记录。BugFull 模板有 `action_chain` 字段，但填写完全依赖人工。

**SOP 生成是手工编写的。** 现有 6 条 SOP 是专家手工撰写的，不是从历史案例中提取的。

**BM25 检索已有，向量检索未到位。** KB Connector 使用 BM25，适合关键词匹配，不适合语义相似。随着 BugCard 积累，「症状描述语义相近但词汇迥异」的检索召回率会成为瓶颈。

**知识库与案例库是物理上孤立的两个系统。** `kb_index.db` 索引的是代码和文档文件，`fingerprints.db` 存储 Pass/Shader 指纹，而 BugCard/BugFull 存储在 `common/cases/` 的 Markdown 文件中。三者之间没有统一的查询接口。

---

## 七、多平台适配：五个平台，五种方言，零个共同语法树

框架支持 Claude Code、Claude Work、MiniMax、Code Buddy、Manus 五个平台。这是一个有战略眼光的决策。

但现状是：五个平台各自独立维护 Agent prompt，相同概念（如 Skeptic 的职责）在各平台有不同描述，某些平台有 MiniMax 独有的合并格式（将多个 Agent 打包在一个文件），某些平台有单一工作流文件（Manus），各平台的功能覆盖也不均等。

**缺失的是一个平台无关的核心语义层。** 理想架构应是：核心 Agent 行为规范（接口）存在一次，各平台适配层只处理「如何将核心规范翻译为平台原生语法」。目前的结构接近于「五份独立实现，靠人工保持语义一致」。

同时，`多平台适配层差异化设计说明.docx` 这份文件的存在本身说明有人意识到了这个问题，但该文件的内容尚未被落实为代码层面的平台抽象。

---

## 八、超越设计的可能性：三个尚未被命名的方向

以下是三个现有设计文档中未曾触及，但有机会让这个框架真正超越同类工具的方向：

### 8.1 跨设备指纹图谱（Cross-Device Fingerprint Graph）

目前的 `fingerprint_store.py` 存储单一 capture 的 Pass/Shader 指纹。设想一个更宏观的能力：当同一个 Bug 的 BugFull 积累了来自 Adreno、Mali、PowerVR 三个平台的捕获时，系统自动构建一张「Bug 在不同 GPU 上的表现差异图谱」。

这张图谱可以回答：「这个 Bug 是驱动行为差异导致的，还是内容本身的问题？」——这正是设计文档中 Driver/Device Specialist 的核心职责，但需要跨 capture 的指纹关联才能真正实现。

### 8.2 反事实实验的自动化评分器（Counterfactual Scoring Engine）

设计文档要求反事实验证（「如果不 X，Y 是否消失」），但验证结果的判断目前全靠 Agent 的语言理解。

可以建立一个专用的视觉差异评分器：在 Patch 应用前后，自动对比目标像素区域的渲染结果，计算语义差异分数（不仅是像素差，而是「异常特征是否消失」的结构化评估）。这将反事实验证从「主观判断」变为「可量化的实验结论」，是质量钩子真正具备机械执行力的基础。

### 8.3 调试策略的强化学习基底（Debug Strategy RL Foundation）

设计文档提及了 RFT（Reinforcement Fine-Tuning）作为第三阶段，但描述比较抽象。一个更具体的路径是：

将「用最少工具调用步骤定位到 First Bad Event」定义为一个可自动评估的奖励函数——这是完全可测量的（调用次数、最终是否找到正确的 First Bad Event）。配合 S0–S7 Pipeline 的执行记录，可以为强化学习构建一个真实的 GPU 调试 MDP（Markov Decision Process），而不需要等到「数百案例积累完成」的 SFT 阶段才开始优化。

---

## 九、优先级路线图

基于以上分析，建议按照以下节奏逐步弥合差距，从价值密度最高的工作开始：

### Phase 1：夯实基础，让已有结构真正联通（1–2 个月）

这一阶段的目标是让框架从「静态文档集合」变为「可运行的端到端原型」。

**P1-A：知识层结构化**
将 `invariant_library.md` 转换为机器可读的 JSON/YAML，添加 `typical_root_causes`、`fix_patterns`、`linked_sop`、`example_cards` 字段。实现一个简单的 CLI 工具：输入症状标签，输出候选不变量 + 推荐 SOP + 相似历史案例。

**P1-B：Agent Prompt 增强**
为每个 Agent 的 prompt 注入对应的核心知识文档摘要：Triage 注入症状分类学 + 不变量路由表，Skeptic 注入反事实检查清单，Curator 注入 BugCard 完整性规范。这是不增加代码量却能显著提升 Agent 行为质量的杠杆点。

**P1-C：最小化 Quality Hook 实现**
在 Curator Agent 的 prompt 中引入强制检查清单（BugCard Hook），并在 Claude Code 平台通过 `PostToolUse` Hook 验证 BugCard 必填字段完整性。这是让质量保障从「誓言」变为「约束」的第一步。

### Phase 2：协作层激活（2–4 个月）

**P2-A：Hypothesis Board 状态机**
实现一个轻量的 Hypothesis Board 服务（可以是简单的 JSON 文件 + Python 工具），支持 ACTIVE → VALIDATED/REFUTED 的状态转换，并通过 `rd.*` 工具暴露给 Agent。

**P2-B：Patch-and-Verify 闭环**
将 `patch_engine.py` 的 Shader 热替换能力与 S5 阶段（Hypothesis Loop）真正联通，使 Agent 可以发起「最小 Patch → 渲染对比 → 反事实判定」的完整实验。

**P2-C：Action Chain 自动记录**
在 `server.py` 的工具 dispatch 层添加透明的调用记录（调用工具名、参数、返回值摘要、时间戳），写入 session 级别的 `action_chain.jsonl`，为后续 BugFull 生成和 SOP 提取建立数据基础。

### Phase 3：自进化闭环启动（4–8 个月）

**P3-A：统一知识查询接口**
将 BM25 KB、指纹数据库、BugCard Markdown 三个孤立系统统一到一个查询接口后面，支持跨源检索（症状标签 + 语义描述 + 指纹相似度的联合查询）。

**P3-B：语义向量层叠加**
在现有 BM25 之上叠加向量嵌入检索（可使用轻量本地模型），解决「相似 Bug 但描述词汇迥异」的召回问题。

**P3-C：SOP 提取实验**
从积累的 `action_chain.jsonl` 中，提取成功调试案例的工具调用序列模式，尝试自动生成 SOP 草稿，由人工审核后入库。

---

## 十、总结：你已建好的，和你尚欠的

### 你已建好的

这个框架有两样东西格外珍贵，超过很多同类项目：

一是**领域模型的质量**。Invariant Library 不是一份 FAQ，是一个可演绎的公理体系。BugCard/BugFull 的双层结构不是格式要求，是对「什么是可复用的调试知识」这一问题的深刻回答。这些东西难以被后来的代码取代，因为它们是智慧的结晶，不是工程的产物。

二是**工具层的工程严谨性**。199 个工具有一致的响应契约，Pydantic 建模覆盖所有数据结构，内容寻址存储设计合理，测试套件覆盖契约验证。这是一个可以信赖的执行地基。

### 你尚欠的

你尚欠的，大多数都是「连接」而非「创造」——将已有的积木按设计图插接起来。

最紧迫的欠缺，按影响力排序：

第一，**知识层的机器可读化**：将 Invariant Library 从散文变成数据，让 Agent 可以程序化推理而非人工阅读。

第二，**Agent Prompt 的深度注入**：将设计文档中精心设计的规范、约束和知识实际嵌入到 Agent 的行为指令中，而非仅存于独立文档。

第三，**Quality Hooks 的最小实现**：哪怕只实现 BugCard 必填字段检查，也让「质量门槛」从誓言变为约束。

第四，**Action Chain 的自动记录**：这是自进化闭环的唯一数据源，而数据的积累需要从第一天开始。

第五，**Patch-and-Verify 闭环**：这是让 Skeptic 和反事实验证从哲学概念变为工程实体的关键一步。

---

从设计的完全体到「超越」，路上的第一道门其实并不遥远——它就藏在「把写下的规范变成跑起来的代码」这件再朴素不过的事情里。

教堂的图纸已经足够宏伟。现在，是时候让每一块砖彼此真正咬合。

---

*报告生成于 2026 年 2 月，基于对 debug-agent（含设计文档三份、实现文件 50+ 个）与 rdx-mcp（server.py 3600+ 行、workflows.py 2000+ 行、完整知识系统）的全量阅读与分析。*
