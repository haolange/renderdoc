"""Optional debug-agent AIRD evidence bridge for action_chain/session_evidence."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _debug_agent_root() -> Optional[Path]:
    env_root = os.environ.get("RDX_AIRD_ROOT")
    if env_root:
        p = Path(env_root).resolve()
        return p if p.is_dir() else None
    here = Path(__file__).resolve()
    candidate = here.parents[3] / "debug-agent"
    if candidate.is_dir():
        return candidate
    return None


def _read_current_session(root: Path) -> str:
    marker = root / "common" / "knowledge" / "library" / "sessions" / ".current_session"
    if not marker.is_file():
        return ""
    value = marker.read_text(encoding="utf-8").lstrip("\ufeff").strip()
    if value == "session-unset":
        return ""
    return value


def _resolve_session_id(root: Path) -> str:
    candidate = str(os.environ.get("RDX_AIRD_SESSION_ID") or "").strip()
    if not candidate:
        candidate = _read_current_session(root)
    if not candidate:
        return ""
    if not _SAFE_SESSION_ID_RE.match(candidate):
        return ""
    return candidate


def _session_dir(root: Path, session_id: str) -> Path:
    return root / "common" / "knowledge" / "library" / "sessions" / session_id


def _append_action_chain(path: Path, entry: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_json_if_exists(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        # Existing human-authored YAML can stay untouched.
        return {}


def _update_session_evidence(path: Path, evidence_item: Dict[str, Any], session_id: str) -> None:
    payload = _load_json_if_exists(path)
    if not payload:
        payload = {"session_id": session_id, "evidence": []}
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence.append(evidence_item)
    if len(evidence) > 500:
        evidence = evidence[-500:]
    payload["evidence"] = evidence
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML, so debug-agent validators can still parse this file.
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def record_aird_evidence(
    *,
    operation: str,
    args: Dict[str, Any],
    payload: Dict[str, Any],
    transport: str,
    trace_id: str,
) -> Dict[str, Any]:
    if not _bool_env("RDX_AIRD_ENABLE", default=True):
        return {}
    root = _debug_agent_root()
    if root is None:
        return {}
    session_id = _resolve_session_id(root)
    if not session_id:
        return {}

    sess_dir = _session_dir(root, session_id)
    action_chain_path = sess_dir / "action_chain.jsonl"
    session_evidence_path = sess_dir / "session_evidence.yaml"

    ts_ms = int(time.time() * 1000)
    ok = bool(payload.get("ok"))
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []

    action_entry = {
        "ts_ms": ts_ms,
        "operation": str(operation),
        "transport": str(transport),
        "trace_id": str(trace_id),
        "ok": ok,
        "args_keys": sorted(str(k) for k in args.keys()),
        "error_code": str(err.get("code") or ""),
    }
    _append_action_chain(action_chain_path, action_entry)

    evidence_item = {
        "evidence_id": f"rdx-{trace_id}",
        "type": "tool_execution",
        "result": "passed" if ok else "failed",
        "description": f"{operation} via {transport}",
        "operation": str(operation),
        "trace_id": str(trace_id),
        "ts_ms": ts_ms,
        "artifacts": artifacts,
    }
    _update_session_evidence(session_evidence_path, evidence_item, session_id=session_id)

    return {
        "aird_session_id": session_id,
        "aird_action_chain_path": str(action_chain_path),
        "aird_session_evidence_path": str(session_evidence_path),
    }
