#!/usr/bin/env python3
"""
Skeptic 签署状态检查器 — AIRD Framework M4 Quality Hooks

检查 Skeptic Agent 是否已对当前假设或 BugCard 完成签署，
且所有质疑项均已被回应（status: addressed）。

用法：
  python skeptic_signoff_checker.py <skeptic_output.yaml>
  python skeptic_signoff_checker.py <skeptic_output.yaml> --mode bugcard

返回码：
  0 — 签署完整，可以继续
  1 — 签署不完整（输出未解决的质疑项）
  2 — 文件解析错误
"""

import sys
import yaml
from pathlib import Path

ANSI_RED    = "\033[91m"
ANSI_GREEN  = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_CYAN   = "\033[96m"
ANSI_RESET  = "\033[0m"

FIVE_BLADES = [
    "刀1: 相关性刀",
    "刀2: 覆盖性刀",
    "刀3: 反事实刀",
    "刀4: 工具证据刀",
    "刀5: 替代假设刀",
]


def check_signoff(data: dict, mode: str = "hypothesis") -> tuple:
    """
    检查 Skeptic 签署文档。
    mode: "hypothesis"（假设签署）或 "bugcard"（BugCard 签署）
    返回 (ok: bool, issues: list[str])
    """
    issues = []

    msg_type = data.get("message_type", "")

    # ── SKEPTIC_CHALLENGE 模式：有未解质疑 ──────────────────────
    if msg_type == "SKEPTIC_CHALLENGE":
        challenges = data.get("challenges", [])
        open_challenges = [c for c in challenges if c.get("status") == "open"]
        if open_challenges:
            issues.append(f"存在 {len(open_challenges)} 个未回应的质疑项（status: open）：")
            for c in open_challenges:
                issues.append(f"  [{c.get('challenge_id', '?')}] {c.get('blade', '?')}: {c.get('challenge', '')[:80]}")
                issues.append(f"    → 要求行动：{c.get('required_action', '')[:80]}")
        signed = data.get("sign_off", {}).get("signed", False)
        if not signed:
            issues.append("sign_off.signed = false，Skeptic 拒绝签署")
        return len(issues) == 0, issues

    # ── SKEPTIC_SIGN_OFF 模式：验证五把刀 ───────────────────────
    if msg_type == "SKEPTIC_SIGN_OFF":
        blade_review = data.get("blade_review", [])
        reviewed_blades = [b.get("blade", "") for b in blade_review]

        # 检查五把刀是否全部覆盖
        for blade in FIVE_BLADES:
            # 模糊匹配（支持中文刀名的部分匹配）
            blade_key = blade.split(": ")[1]  # 如 "相关性刀"
            found = any(blade_key in rb for rb in reviewed_blades)
            if not found:
                issues.append(f"[五把刀] '{blade}' 未被检验")

        # 检查是否有失败项
        failed_blades = [b for b in blade_review if b.get("result") != "pass"]
        if failed_blades:
            issues.append(f"以下解剖刀审查未通过：")
            for b in failed_blades:
                issues.append(f"  {b.get('blade', '?')}: result={b.get('result', '?')} — {b.get('note', '')[:60]}")

        # 检查 sign_off
        sign_off = data.get("sign_off", {})
        if not sign_off.get("signed"):
            issues.append(f"sign_off.signed = false，原因：{sign_off.get('reason', '未说明')}")

        # BugCard 模式额外检查
        if mode == "bugcard":
            if not data.get("bugcard_skeptic_signed") and not sign_off.get("signed"):
                issues.append("bugcard_skeptic_signed 字段未设置为 true")

        return len(issues) == 0, issues

    # ── 未知格式 ─────────────────────────────────────────────────
    issues.append(f"未知的 message_type：'{msg_type}'，期望 SKEPTIC_SIGN_OFF 或 SKEPTIC_CHALLENGE")
    return False, issues


def main():
    mode = "hypothesis"
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1]

    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != mode]

    if not args:
        print("用法：python skeptic_signoff_checker.py <skeptic_output.yaml> [--mode hypothesis|bugcard]")
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
        print(f"{ANSI_RED}错误：文件必须是 YAML 对象{ANSI_RESET}")
        sys.exit(2)

    mode_label = "假设签署" if mode == "hypothesis" else "BugCard 签署"
    print(f"\n{'═'*55}")
    print(f"  AIRD Skeptic 签署检查器 — {mode_label}模式")
    print(f"  文件：{path.name}")
    print(f"{'═'*55}")

    ok, issues = check_signoff(data, mode=mode)

    if ok:
        print(f"\n{ANSI_GREEN}✅ Skeptic 签署完整 — 可以继续下一阶段{ANSI_RESET}")
        blade_review = data.get("blade_review", [])
        if blade_review:
            print(f"\n  五把刀审查结果：")
            for b in blade_review:
                icon = "✅" if b.get("result") == "pass" else "❌"
                print(f"    {icon} {b.get('blade', '?')}: {b.get('note', '')[:50]}")
        sys.exit(0)
    else:
        print(f"\n{ANSI_RED}❌ Skeptic 签署不完整 — 不得继续{ANSI_RESET}\n")
        for issue in issues:
            print(f"  • {issue}")
        print(f"\n{ANSI_YELLOW}⚠  请先解决以上质疑项，再由 Skeptic Agent 重新签署。{ANSI_RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
