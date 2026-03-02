#!/usr/bin/env python3
"""Safe hook dispatcher for Code Buddy and Claude-style hooks.

Modes:
  - write-bugcard
  - write-skeptic
  - stop-gate
  - stop-gate-force
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

KEYWORDS = (
    "AIRD_FINAL_VERDICT",
    "最终裁决",
    "根因确认",
    "结案",
    "VALIDATED",
    "final verdict",
    "case closed",
)

BUGCARD_RE = re.compile(r"knowledge[/\\]library[/\\].*bugcard.*\\.ya?ml$", re.IGNORECASE)
SKEPTIC_RE = re.compile(r"skeptic_.*\\.ya?ml$", re.IGNORECASE)


def _debug_agent_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _validator_paths(root: Path) -> tuple[Path, Path, Path]:
    validators = root / "common" / "hooks" / "validators"
    return (
        validators / "bugcard_validator.py",
        validators / "counterfactual_validator.py",
        validators / "skeptic_signoff_checker.py",
    )


def _script_paths(root: Path) -> tuple[Path, Path]:
    resolve_artifact = root / "common" / "hooks" / "utils" / "resolve_session_artifact.py"
    validate_contract = root / "scripts" / "validate_tool_contract.py"
    return resolve_artifact, validate_contract


def _extract_tool_output_file() -> str:
    payload = os.environ.get("CODEBUDDY_TOOL_INPUT", "").strip()
    if payload:
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            obj = {}
        file_path = str(obj.get("file_path", "")).strip()
        if file_path:
            return file_path
    return str(os.environ.get("TOOL_OUTPUT_FILE", "")).strip()


def _relay(proc: subprocess.CompletedProcess[str]) -> None:
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)


def _cmd_write_bugcard(root: Path) -> int:
    bugcard_validator, _, _ = _validator_paths(root)
    _, _, skeptic_checker = _validator_paths(root)
    _, validate_contract = _script_paths(root)
    file_path = _extract_tool_output_file()
    if not file_path:
        return 0
    if not BUGCARD_RE.search(file_path.replace("\\", "/")):
        return 0
    strict = _run(["python3", str(validate_contract), "--strict"])
    _relay(strict)
    if strict.returncode != 0:
        return strict.returncode
    proc = _run(["python3", str(bugcard_validator), file_path])
    _relay(proc)
    if proc.returncode != 0:
        return proc.returncode

    # Bind BugCard write to a real Skeptic BugCard signoff under current session.
    current = root / "common" / "knowledge" / "library" / "sessions" / ".current_session"
    if not current.is_file():
        print(f"missing session marker: {current}", file=sys.stderr)
        return 1
    session_id = current.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if (not session_id) or (session_id == "session-unset"):
        print(f"invalid current session id: {session_id!r} ({current})", file=sys.stderr)
        return 1
    signoff_path = root / "common" / "knowledge" / "library" / "sessions" / session_id / "skeptic_signoff.yaml"
    if not signoff_path.is_file():
        print(f"missing skeptic signoff artifact: {signoff_path}", file=sys.stderr)
        return 1

    proc2 = _run(["python3", str(skeptic_checker), str(signoff_path), "--mode", "bugcard"])
    _relay(proc2)
    return proc2.returncode


def _cmd_write_skeptic(root: Path) -> int:
    _, _, skeptic_checker = _validator_paths(root)
    _, validate_contract = _script_paths(root)
    file_path = _extract_tool_output_file()
    if not file_path:
        return 0
    if not SKEPTIC_RE.search(Path(file_path).name):
        return 0
    strict = _run(["python3", str(validate_contract), "--strict"])
    _relay(strict)
    if strict.returncode != 0:
        return strict.returncode
    proc = _run(["python3", str(skeptic_checker), file_path, "--mode", "format"])
    _relay(proc)
    return proc.returncode


def _resolve_artifact(root: Path, artifact: str) -> Tuple[int, str, str]:
    resolve_artifact, _ = _script_paths(root)
    proc = _run(
        [
            "python3",
            str(resolve_artifact),
            "--artifact",
            artifact,
            "--must-exist",
        ],
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _extract_assistant_message(stdin_text: str) -> str:
    if not stdin_text.strip():
        return ""
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("assistant_message", ""))


def _should_gate_stop(stdin_text: str) -> bool:
    msg = _extract_assistant_message(stdin_text)
    if not msg:
        return False
    return any(token in msg for token in KEYWORDS)


def _emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def _cmd_stop_gate(root: Path, force: bool = False) -> int:
    stdin_text = sys.stdin.read()
    if (not force) and (not _should_gate_stop(stdin_text)):
        return 0

    _, counterfactual_validator, skeptic_checker = _validator_paths(root)
    _, validate_contract = _script_paths(root)

    errors: list[str] = []

    strict = _run(["python3", str(validate_contract), "--strict"])
    if strict.returncode != 0:
        _relay(strict)
        errors.append("tool contract validation failed")

    rc_evi, evidence_path, evidence_err = _resolve_artifact(root, "session_evidence")
    rc_ske, signoff_path, signoff_err = _resolve_artifact(root, "skeptic_signoff")
    rc_act, action_chain_path, action_chain_err = _resolve_artifact(root, "action_chain")

    if rc_evi != 0:
        errors.append(f"missing session evidence artifact ({evidence_err or 'session_evidence.yaml'})")
    if rc_ske != 0:
        errors.append(f"missing skeptic signoff artifact ({signoff_err or 'skeptic_signoff.yaml'})")
    if rc_act != 0:
        errors.append(f"missing action chain artifact ({action_chain_err or 'action_chain.jsonl'})")

    if not errors:
        r1 = _run(["python3", str(counterfactual_validator), evidence_path])
        _relay(r1)
        if r1.returncode != 0:
            errors.append("counterfactual validator failed")

        r2 = _run(["python3", str(skeptic_checker), signoff_path, "--mode", "hypothesis"])
        _relay(r2)
        if r2.returncode != 0:
            errors.append("skeptic signoff checker failed")

    if errors:
        _emit_block("Finalization blocked: " + "; ".join(errors))
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: codebuddy_hook_dispatch.py <write-bugcard|write-skeptic|stop-gate|stop-gate-force>",
            file=sys.stderr,
        )
        return 2

    mode = sys.argv[1].strip().lower()
    root = _debug_agent_root()

    if mode == "write-bugcard":
        return _cmd_write_bugcard(root)
    if mode == "write-skeptic":
        return _cmd_write_skeptic(root)
    if mode == "stop-gate":
        return _cmd_stop_gate(root, force=False)
    if mode == "stop-gate-force":
        return _cmd_stop_gate(root, force=True)

    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
