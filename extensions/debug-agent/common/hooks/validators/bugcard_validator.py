#!/usr/bin/env python3
"""
BugCard 完整性验证器 — AIRD Framework M4 Quality Hooks

用法：
  python3 bugcard_validator.py <bugcard.yaml>
  python3 bugcard_validator.py <bugcard.yaml> --strict

返回码：
  0 — 验证通过
  1 — 验证失败（输出缺失/不合规字段列表）
  2 — 文件解析错误
"""

import sys
import re
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    print("错误：缺少依赖 'PyYAML'，无法解析 YAML。")
    print(f"请先安装依赖：python3 -m pip install -r {req}")
    sys.exit(2)

# ── 必填字段规则 ────────────────────────────────────────────────
REQUIRED_FIELDS = [
    "bugcard_id",
    "title",
    "symptom_tags",
    "trigger_tags",
    "violated_invariants",
    "recommended_sop",
    "root_cause_summary",
    "fingerprint",
    "fix_verified",
    "skeptic_signed",
    "bugcard_skeptic_signed",
]

FINGERPRINT_SUBFIELDS = ["pattern", "risk_category", "shader_stage"]
FIX_VERIFICATION_SUBFIELDS = ["pixel_before", "pixel_after"]

BUGCARD_ID_PATTERN = re.compile(r"^BUG-[A-Z]+-\d{3}$")
SOP_ID_PATTERN = re.compile(r"^SOP-[A-Z]+-\d{2}$")

VAGUE_PATTERNS = ["可能是", "大概", "不确定", "maybe", "probably", "似乎", "或许"]

ANSI_RED   = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_RESET = "\033[0m"

ALLOWED_SHADER_STAGES = {"VS", "PS", "CS", "GS", "HS", "DS", "MS", "TS"}


def _load_yaml(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _load_reference_sets():
    """
    加载跨文件引用集合（用于 --strict 校验）。
    返回 dict[str, set[str]]
    """
    root = Path(__file__).resolve().parents[3]  # debug-agent/
    symptom = _load_yaml(root / "common" / "taxonomy" / "symptom_taxonomy.yaml") or {}
    trigger = _load_yaml(root / "common" / "taxonomy" / "trigger_taxonomy.yaml") or {}
    inv = _load_yaml(root / "common" / "invariants" / "invariant_library.yaml") or {}
    sop = _load_yaml(root / "common" / "skills" / "sop_library.yaml") or {}

    symptoms = {s.get("tag") for s in (symptom.get("symptoms") or []) if isinstance(s, dict)}
    triggers = {t.get("tag") for t in (trigger.get("triggers") or []) if isinstance(t, dict)}
    invariants = {i.get("id") for i in (inv.get("invariants") or []) if isinstance(i, dict)}
    sops = {s.get("id") for s in (sop.get("sops") or []) if isinstance(s, dict)}

    return {
        "symptom_tags": {x for x in symptoms if x},
        "trigger_tags": {x for x in triggers if x},
        "violated_invariants": {x for x in invariants if x},
        "recommended_sop": {x for x in sops if x},
    }


def validate_bugcard(data: dict, strict: bool = False) -> list:
    """验证 BugCard 数据，返回错误列表（空列表表示通过）。"""
    errors = []

    # 1. 必填字段存在性检查
    for field in REQUIRED_FIELDS:
        if field not in data or data[field] is None:
            errors.append(f"[缺失] 必填字段 '{field}' 不存在或为空")

    if errors:
        return errors  # 字段缺失时不继续深度检查

    # 2. bugcard_id 格式
    if not BUGCARD_ID_PATTERN.match(str(data.get("bugcard_id", ""))):
        errors.append(f"[格式] bugcard_id '{data['bugcard_id']}' 不符合格式 BUG-<类别>-<序号>（如 BUG-PREC-002）")

    # 3. title 长度
    title = str(data.get("title", ""))
    if len(title) < 10:
        errors.append(f"[长度] title 过短（{len(title)} 字），至少需要 10 字")
    if len(title) > 120:
        errors.append(f"[长度] title 过长（{len(title)} 字），最多 120 字")

    # 4. symptom_tags / trigger_tags / violated_invariants 非空列表
    for list_field in ["symptom_tags", "trigger_tags", "violated_invariants"]:
        val = data.get(list_field)
        if not isinstance(val, list) or len(val) == 0:
            errors.append(f"[类型] '{list_field}' 必须是非空列表")
        elif any(not isinstance(x, str) or not x.strip() for x in val):
            errors.append(f"[类型] '{list_field}' 列表元素必须为非空字符串")

    # 5. recommended_sop 格式
    sop = str(data.get("recommended_sop", ""))
    if not SOP_ID_PATTERN.match(sop):
        errors.append(f"[格式] recommended_sop '{sop}' 不符合格式 SOP-<类别>-<序号>（如 SOP-PREC-01）")

    # 6. root_cause_summary 长度 + 禁止模糊表述
    rcs = str(data.get("root_cause_summary", ""))
    if len(rcs) < 30:
        errors.append(f"[长度] root_cause_summary 过短（{len(rcs)} 字），至少需要 30 字")
    for vague in VAGUE_PATTERNS:
        if vague in rcs:
            errors.append(f"[质量] root_cause_summary 包含模糊表述 '{vague}'，必须精确描述根因")
            break

    # 7. fingerprint 子字段
    fp = data.get("fingerprint")
    if isinstance(fp, dict):
        for sub in FINGERPRINT_SUBFIELDS:
            if sub not in fp or not fp[sub]:
                errors.append(f"[缺失] fingerprint.{sub} 不存在或为空")
        stage = str(fp.get("shader_stage", "")).strip()
        if stage and stage not in ALLOWED_SHADER_STAGES:
            errors.append(f"[格式] fingerprint.shader_stage '{stage}' 非法，允许值：{sorted(ALLOWED_SHADER_STAGES)}")
    else:
        errors.append("[类型] fingerprint 必须是包含 pattern/risk_category/shader_stage 的对象")

    # 8. fix_verification_data（当 fix_verified=True 时）
    if not isinstance(data.get("fix_verified"), bool):
        errors.append("[类型] fix_verified 必须为 boolean（true/false）")

    if data.get("fix_verified") is True:
        fvd = data.get("fix_verification_data")
        if isinstance(fvd, dict):
            for sub in FIX_VERIFICATION_SUBFIELDS:
                if sub not in fvd or not fvd[sub]:
                    errors.append(f"[缺失] fix_verification_data.{sub} 不存在或为空")
        else:
            errors.append("[类型] fix_verification_data 必须包含 pixel_before 和 pixel_after")

    # 9. Skeptic 签署状态
    if not isinstance(data.get("skeptic_signed"), bool):
        errors.append("[类型] skeptic_signed 必须为 boolean（true/false）")
    if data.get("skeptic_signed") is not True:
        errors.append("[签署] skeptic_signed 必须为 true（需 Skeptic Agent 签署后方可入库）")
    if not isinstance(data.get("bugcard_skeptic_signed"), bool):
        errors.append("[类型] bugcard_skeptic_signed 必须为 boolean（true/false）")
    if data.get("bugcard_skeptic_signed") is not True:
        errors.append("[签署] bugcard_skeptic_signed 必须为 true（需 Skeptic Agent 对 BugCard 内容二次签署）")

    # 10. --strict：跨文件引用一致性检查
    if strict and not errors:
        ref = _load_reference_sets()

        for tag in data.get("symptom_tags", []):
            if tag not in ref["symptom_tags"]:
                errors.append(f"[引用] symptom_tags 中包含未知 tag：'{tag}'（不在 symptom_taxonomy.yaml）")

        for tag in data.get("trigger_tags", []):
            if tag not in ref["trigger_tags"]:
                errors.append(f"[引用] trigger_tags 中包含未知 tag：'{tag}'（不在 trigger_taxonomy.yaml）")

        for inv in data.get("violated_invariants", []):
            if inv not in ref["violated_invariants"]:
                errors.append(f"[引用] violated_invariants 中包含未知 id：'{inv}'（不在 invariant_library.yaml）")

        sop = str(data.get("recommended_sop", "")).strip()
        if sop and sop not in ref["recommended_sop"]:
            errors.append(f"[引用] recommended_sop '{sop}' 不存在于 sop_library.yaml")

    return errors


def main():
    strict = "--strict" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("用法：python3 bugcard_validator.py <bugcard.yaml> [--strict]")
        sys.exit(2)

    path = Path(args[0])
    if not path.exists():
        print(f"{ANSI_RED}错误：文件不存在 — {path}{ANSI_RESET}")
        sys.exit(2)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"{ANSI_RED}错误：YAML 解析失败 — {e}{ANSI_RESET}")
        sys.exit(2)

    if not isinstance(data, dict):
        print(f"{ANSI_RED}错误：BugCard 必须是 YAML 对象，当前类型：{type(data)}{ANSI_RESET}")
        sys.exit(2)

    errors = validate_bugcard(data, strict=strict)

    print(f"\n{'═'*55}")
    print(f"  AIRD BugCard 验证器 — {path.name}")
    print(f"{'═'*55}")

    if not errors:
        print(f"\n{ANSI_GREEN}✅ 验证通过 — BugCard 符合所有必填字段规范{ANSI_RESET}")
        print(f"   bugcard_id : {data.get('bugcard_id')}")
        print(f"   title      : {data.get('title', '')[:60]}")
        print(f"   skeptic    : {'✅ 已签署' if data.get('skeptic_signed') else '❌ 未签署'}")
        sys.exit(0)
    else:
        print(f"\n{ANSI_RED}❌ 验证失败 — 发现 {len(errors)} 个问题：{ANSI_RESET}\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i:2d}. {err}")
        print(f"\n{ANSI_YELLOW}⚠  BugCard 不得入库，请修复以上问题后重新验证。{ANSI_RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
