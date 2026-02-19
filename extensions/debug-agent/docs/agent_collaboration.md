# Agent 协作规范

## 概述

本文档定义Renderdoc Debug Agent系统中多Agent协作的规范和接口。多个专业Agent协同工作，每个Agent负责特定领域，通过消息传递和状态共享完成复杂调试任务。

## Agent角色定义

### 1. Triage Agent（分诊 Agent）

**职责**: 
- 接收初始问题报告
- 进行初步症状分类
- 决定问题优先级和路由

**输入**:
```
[ISSUE_REPORT]
description: "角色脸部渲染出现白点闪烁"
screenshots: ["screenshot1.png"]
render_capture: "capture.rdc"
```

**输出**:
```
[TRIAGE_RESULT]
severity: major
category: nan_propagation
confidence: 0.85
routed_to: [Pixel Forensics Agent, Shader Analysis Agent]
initial_hypothesis: "PS阶段可能存在NaN引入"
recommended_sops: [SOP-NAN-01]
```

**调用工具**:
- `rd.capture.open_file` - 打开捕获文件
- `rd.event.get_actions` - 获取Draw Call列表
- `rd.texture.get_data` - 获取问题区域数据

---

### 2. Pixel Forensics Agent（像素取证 Agent）

**职责**:
- 深入分析问题像素的历史
- 追踪异常值的传播路径
- 提取关键证据

**输入**:
```
[FORENSICS_REQUEST]
target_pixel: {x: 1234, y: 567}
event_range: [1000, 1500]
investigate: [value_trace, event_history]
```

**输出**:
```
[FORENSICS_RESULT]
pixel_value_at_issue: {r: NaN, g: NaN, b: NaN, a: 1.0}
origin_event: 1245
origin_stage: PS
propagation_path:
  - event: 1240, stage: VS, value: {normal: (0, 0, 0)}
  - event: 1245, stage: PS, value: {NaN introduced}
key_evidence:
  - type: pixel_history
    description: "VS输出零向量normal"
  - type: shader_analysis
    description: "PS中normalize(0)产生NaN"
confidence: 0.95
```

**调用工具**:
- `rd.event.get_pixels` - 获取像素历史
- `rd.texture.get_data` - 获取纹理数据
- `rd.shader.analyze` - 分析Shader

---

### 3. Shader Analysis Agent（Shader分析 Agent）

**职责**:
- 分析Shader代码中的潜在问题
- 检查常见错误模式
- 提供修复建议

**输入**:
```
[SHADER_ANALYSIS_REQUEST]
shader_stage: PS
shader_name: "PSMain"
focus_areas: [division, normalize, sqrt, log]
```

**输出**:
```
[SHADER_ANALYSIS_RESULT]
issues_found:
  - location: "line 45"
    code: "float3 n = normalize(v.normal);"
    severity: high
    issue_type: "potential_nan_source"
    description: "normalize(0)会产生NaN"
    suggestion: "使用SafeNormalize包装"
  - location: "line 78"
    code: "float t = a / b;"
    severity: medium
    issue_type: "potential_zero_divide"
    description: "b可能为零"
    suggestion: "添加零值检查"
warnings: []
compilation_success: true
```

**调用工具**:
- `rd.shader.get_source` - 获取Shader源码
- `rd.shader.compile` - 重新编译Shader
- `rd.shader.analyze` - 分析Shader

---

### 4. Capture & Repro Agent（捕获与复现 Agent）

**职责**:
- 构建可复现的测试用例
- 生成最小化复现步骤
- 验证修复有效性

**输入**:
```
[REPRO_REQUEST]
root_cause: "normalize(0) in PS"
minimal_repro: true
target_api: DirectX12
```

**输出**:
```
[REPRO_RESULT]
repro_script: |
  # 最小复现步骤
  1. 创建一个全零法线的平面
  2. 应用标准PBR着色
  3. 观察输出是否存在NaN
test_case_path: "/repro/test_nan.zip"
verification: "修复验证通过"
```

**调用工具**:
- `rd.capture.create` - 创建测试捕获
- `rd.capture.save_modified` - 保存修改后的捕获

---

## Agent间通信协议

### 消息格式

```json
{
  "message_id": "msg_001",
  "sender": "Triage Agent",
  "receiver": "Pixel Forensics Agent",
  "type": "request|response|notification",
  "content": {
    "action": "analyze_pixel",
    "parameters": {...}
  },
  "context": {
    "session_id": "session_abc",
    "capture_file": "capture.rdc",
    "current_event": 1234
  },
  "timestamp": "2026-02-19T10:00:00Z"
}
```

### 状态共享

使用共享上下文存储中间结果：

```
[SHARED_CONTEXT]
session_id: session_abc
variables:
  current_symptom: "white_spot"
  severity: "major"
  matched_invariants: ["I-NAN-01"]
  current_hypothesis: "normalize_produces_nan"
  evidence_collected:
    - pixel_history: "completed"
    - shader_analysis: "in_progress"
  progress:
    triage: 100
    forensics: 60
    shader_analysis: 40
```

## 协作工作流

### 标准调试流程

```
┌─────────────┐
│   初始报告   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Triage Agent │──────► Invariant Library (Search)
└──────┬──────┘
       │ 路由
       ├──────────────────────┐
       │                      │
       ▼                      ▼
┌─────────────┐      ┌─────────────┐
│Pixel Forensics│      │Shader Analysis│
│    Agent     │      │    Agent     │
└──────┬──────┘      └──────┬──────┘
       │                    │
       ├────────┬───────────┤
       │        │           │
       ▼        ▼           ▼
┌─────────────┐      ┌─────────────┐
│  根因确认   │◄─────│  证据整合   │
└──────┬──────┘      └──────┬──────┘
       │                    │
       ▼                    ▼
┌─────────────┐      ┌─────────────┐
│Capture&Repro│      │ BugCard记录 │
│    Agent    │      └──────┬──────┘
       │                    │
       ▼                    ▼
┌─────────────┐      ┌─────────────┐
│  修复验证   │      │ BugFull归档 │
└─────────────┘      └─────────────┘
```

### 并行执行模式

当多个调查方向独立时，Agent可以并行工作：

```
[TASK_DECOMPOSITION]
problem: "画面整体偏暗"
independent_tasks:
  - task_id: 1
    agent: "Color Analysis Agent"
    focus: "gamma_correction"
  - task_id: 2
    agent: "Pipeline Analysis Agent"  
    focus: "blend_state"
  - task_id: 3
    agent: "Texture Analysis Agent"
    focus: "texture_format"

[PARALLEL_EXECUTION]
task_1: {status: "completed", finding: "无Gamma错误"}
task_2: {status: "completed", finding: "混合状态正常"}
task_3: {status: "in_progress", progress: 60%}

[SYNTHESIS]
conclusion: "各通道均未发现问题，需要进一步检查后处理"
```

## Agent配置文件

### skill_manifest.json

```json
{
  "agents": [
    {
      "agent_id": "triage_agent",
      "name": "Triage Agent",
      "capabilities": ["symptom_classification", "routing", "prioritization"],
      "skills": ["symptom_matcher", "invariant_retriever"],
      "tools": ["rd.capture.*", "rd.event.*"]
    },
    {
      "agent_id": "pixel_forensics_agent",
      "name": "Pixel Forensics Agent",
      "capabilities": ["pixel_analysis", "value_tracing", "evidence_extraction"],
      "skills": ["pixel_history_analyzer"],
      "tools": ["rd.texture.*", "rd.event.get_pixels"]
    },
    {
      "agent_id": "shader_analysis_agent",
      "name": "Shader Analysis Agent",
      "capabilities": ["shader_inspection", "pattern_matching", "fix_suggestion"],
      "skills": ["shader_linter", "common_pattern_detector"],
      "tools": ["rd.shader.*"]
    },
    {
      "agent_id": "capture_repro_agent",
      "name": "Capture & Repro Agent",
      "capabilities": ["repro_creation", "verification", "minimization"],
      "skills": ["test_case_generator"],
      "tools": ["rd.capture.*"]
    }
  ]
}
```

## 使用方式

### Claude Code / Claude Work

在Claude中调用这些Agent：

```
# 使用方式1：直接检索
@invariant_library 查找 NaN 相关不变量

# 使用方式2：调用SOP
@skills 执行 SOP-NAN-01 排查白色斑点

# 使用方式3：多Agent协作
@agents 协作排查画面偏暗问题
```

### MiniMax Agent

```
# 检索不变量
search_tool: invariant_library
query: "NaN传播"

# 调用技能
skill: SOP-NAN-01
context: {symptom: "white_spot"}
```

---

## 资源索引

| 资源类型 | 位置 | 用途 |
|----------|------|------|
| 不变量库 | `/invariants/` | Search Tool |
| SOP库 | `/skills/` | Skills |
| 案例库 | `/cases/bugcards/` | Search Tool |
| 案例库 | `/cases/bugfulls/` | 完整档案 |
| Agent配置 | `/config/agent_manifest.json` | Agent定义 |
