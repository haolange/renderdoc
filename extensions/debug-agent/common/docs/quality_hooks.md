# AIRD Framework · Quality Hooks 规范（M4）

Quality Hooks 将调试流程的关键质量门槛从“建议遵循”提升为“强制验证”。目前包含三类 Hook，对应三个验证脚本（见 `common/hooks/README.md`）。

---

## 0. 依赖

验证脚本依赖 Python 3 与 PyYAML：

```bash
python3 -m pip install -r common/hooks/requirements.txt
```

---

## 1. Hook 1：BugCard 入库前完整性检查

### 触发
- 写入 `kb/` 目录中的 BugCard YAML 文件时触发（平台原生 Hook 或 Prompt 层降级）。

### 文件与命名约定（推荐）
- 目录：`common/kb/bugcards/`
- 文件名：必须包含 `bugcard`（以匹配默认 hook 的 file_pattern），例如：
  - `common/kb/bugcards/bugcard_BUG-PREC-001.yaml`

### Schema
以 `common/hooks/schemas/bugcard_required_fields.yaml` 为准。

---

## 2. Hook 2：结案前反事实验证检查

### 触发
- Team Lead 输出“最终裁决/结案/VALIDATED”等关键词前触发（Stop Hook）。

### 输入文件约定
默认检查 `session_evidence.yaml`（路径按平台 Hook 配置决定）。

支持两种格式：

```yaml
# 格式 A：直接是 evidence 列表
- evidence_id: E-CF-001
  type: counterfactual_test
  result: passed
  pixel_before: {x: 512, y: 384, rgba: [0.21, 0.19, 0.18, 1.0]}
  pixel_after:  {x: 512, y: 384, rgba: [0.38, 0.35, 0.33, 1.0]}
  description: "half→float 后目标像素恢复正常"

# 格式 B：对象内含 evidence 键
evidence:
  - { ... 同上 ... }
```

---

## 3. Hook 3：Skeptic 签署检查

### 触发
- 结案前（Stop Hook）
- BugCard 入库前（PostToolUse Hook）

### 输入文件约定
- 默认检查 `skeptic_signoff.yaml` 或写入的 `skeptic_*.yaml`。
- 结构建议对齐 `common/hooks/schemas/skeptic_signoff_schema.yaml`。

---

## 4. 平台差异

不同平台的 Hook 配置位置：
- Claude Code：`debug-agent/.claude/settings.json`
- Code Buddy：`platforms/code-buddy/hooks/hooks.json`

无原生 Hook 的平台应在各 Agent Prompt 的「质量门槛检查清单」中体现降级约束。

