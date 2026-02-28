# AIRD Framework · Agent 协作协议（消息与命名规范）

本协议用于约束多 Agent 间的通信格式、证据引用方式与文件命名，确保调试过程可追溯、可复现、可被质量 Hook 自动验证。

> 约定：所有 message 以 YAML 输出，必须包含 `message_type / from / to` 三字段。

---

## 1. 角色与固定 agent_id

| 角色 | agent_id（建议） |
|---|---|
| Team Lead | `team_lead` |
| Triage & Taxonomy | `triage_agent` |
| Capture & Repro | `capture_repro_agent` |
| Pass Graph / Pipeline | `pipeline_agent` |
| Pixel Forensics | `forensics_agent` |
| Shader & IR | `shader_ir_agent` |
| Driver & Device | `driver_device_agent` |
| Skeptic | `skeptic_agent` |
| Curator | `curator_agent` |

平台适配时允许更改展示名称，但 `from/to` 字段应保持稳定（便于脚本与人类一致理解）。

---

## 2. 消息类型与最小字段集

### 2.1 TASK_DISPATCH（Team Lead → 专家）

```yaml
message_type: TASK_DISPATCH
from: team_lead
to: shader_ir_agent

task_id: "session-...-shader-001"
input:
  capture_file: "<.rdc path>"
  anchor: "<pass/pixel/resource 之一>"
  focus: "<本次任务的明确目标>"
quality_requirements:
  - "<必须产出的证据/字段>"
```

### 2.2 各专家 RESULT（专家 → Team Lead）

专家输出必须：
- 给出可复现的工具证据（`rd.*` 工具输出/制品路径/关键数值）。
- 给出可被 Team Lead 放入假设板的结构化字段（如 event_id、resource_id、fingerprint）。

示例（CAPTURE_RESULT）：
```yaml
message_type: CAPTURE_RESULT
from: capture_repro_agent
to: team_lead

captures:
  anomalous:
    file_path: "<capture_A.rdc>"
    device: "Adreno 740"
  baseline:
    file_path: "<capture_B.rdc>"
    device: "Mali-G99"
anchor:
  type: pixel_coordinates
  value: "(512, 384)"
  confidence: high
```

### 2.3 SKEPTIC_SIGN_OFF / SKEPTIC_CHALLENGE

Skeptic 输出结构以 `common/hooks/schemas/skeptic_signoff_schema.yaml` 为准。

### 2.4 BUGCARD_REVIEW_REQUEST（Curator → Skeptic）

```yaml
message_type: BUGCARD_REVIEW_REQUEST
from: curator_agent
to: skeptic_agent
bugcard_draft_ref: "kb/bugcards/bugcard_BUG-XXX-001.yaml"
required: "bugcard_skeptic_signed == true"
```

---

## 3. 证据与制品命名（建议）

为便于自动化与检索，建议将会话输出组织为：

```
common/kb/
  sessions/<session_id>/
    evidence/
    artifacts/
  bugcards/
  bugfull/
```

`evidence_refs` / `action_chain_ref` 应使用相对路径，避免机器/用户路径差异导致不可复现。

