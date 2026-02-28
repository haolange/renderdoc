# AIRD Framework · Hypothesis Board 规范

Hypothesis Board（假设板）是一次调试会话的「唯一裁决文档」：它记录所有假设、证据引用、状态迁移与结案条件，供 Team Lead 进行调度与裁决。

> 约定：本文件路径相对于 `common/`。建议会话级文档放在 `kb/sessions/<session_id>/` 下，并在假设板中用 `evidence_refs` 引用。

---

## 1. 数据结构（YAML）

```yaml
hypothesis_board:
  session_id: "session-AIRD-YYYYMMDD-XXX"
  bug_description: "一句话描述问题（设备 + 症状 + 复现条件）"
  created_at: "2026-02-28"

  hypotheses:
    - id: H-001
      invariant_id: I-PREC-01
      title: "可被反证的具体假设（一句话）"

      status: ACTIVE        # ACTIVE | VALIDATE | VALIDATED | REFUTED | SPLIT | ARCHIVED
      priority: HIGH        # CRITICAL | HIGH | MEDIUM | LOW
      assigned_to: shader_ir_agent

      evidence_refs:        # 证据引用（文件路径/消息路径/制品路径）
        - "kb/sessions/<session_id>/evidence/e-001_pixel_history.json"
        - "kb/sessions/<session_id>/evidence/e-002_spirv_diff.txt"

      counterfactual_done: false
      skeptic_signed: false

      notes: ""
```

字段约束：
- `id`：会话内唯一，建议 `H-001` 递增。
- `invariant_id`：必须存在于 `invariants/invariant_library.yaml`。
- `status`：见下方状态机规则。
- `assigned_to`：建议使用固定 agent_id（见 `docs/agent_collaboration.md`）。
- `evidence_refs`：必须可追溯；禁止只写“见截图/见日志”。

---

## 2. 状态机与迁移规则

| 触发条件 | 迁移 |
|---|---|
| 专家 Agent 提交支持性证据（直接工具证据） | `ACTIVE → VALIDATE` |
| 反事实验证通过（量化）且 Skeptic 签署 | `VALIDATE → VALIDATED` |
| 专家 Agent 提交反驳证据（或更强替代解释成立） | `* → REFUTED` |
| 假设过于宽泛，需要拆分为更具体子假设 | `ACTIVE → SPLIT` |
| 已生成 BugFull/BugCard 且结案完成 | `VALIDATED → ARCHIVED` |

强制约束：
- 同时存在的 `ACTIVE` 假设**不得超过 7 个**（防止发散）。
- 任何 `VALIDATED` 假设必须满足：
  - `counterfactual_done: true`
  - `skeptic_signed: true`

---

## 3. 常见错误（必须避免）

- 把“直觉”写成假设：例如“感觉像驱动 Bug”，没有可验证机制。
- `evidence_refs` 为空但推进到 `VALIDATE/VALIDATED`。
- 只写“修了就好了”，没有反事实对照与量化数据（像素/误差/频率）。

