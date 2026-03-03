from __future__ import annotations

import asyncio
import json
import os

from rdx.core.engine import CoreEngine
from rdx.core.operation_registry import OperationRegistry
from rdx.core.tsv_projection import to_tsv_string
from rdx.daemon.server import DaemonRuntime
from rdx import server


def test_engine_normalizes_legacy_success_payload() -> None:
    registry = OperationRegistry()

    async def handler(args, env):  # type: ignore[no-untyped-def]
        return {"success": True, "value": 42}

    registry.set_default(handler)
    engine = CoreEngine(registry=registry)
    payload = asyncio.run(engine.execute("rd.test.success", {}))
    assert payload["ok"] is True
    assert payload["success"] is True
    assert payload["result_kind"] == "rd.test.success"
    assert payload["data"]["value"] == 42
    assert payload["value"] == 42


def test_engine_normalizes_legacy_error_payload() -> None:
    registry = OperationRegistry()

    async def handler(args, env):  # type: ignore[no-untyped-def]
        return {"success": False, "error_message": "boom", "code": "x"}

    registry.set_default(handler)
    engine = CoreEngine(registry=registry)
    payload = asyncio.run(engine.execute("rd.test.error", {}))
    assert payload["ok"] is False
    assert payload["success"] is False
    assert payload["error"]["message"] == "boom"
    assert payload["error_message"] == "boom"


def test_tsv_projection_contract_fields() -> None:
    text = to_tsv_string(
        [{"event_id": 7, "name": "DrawIndexed"}],
        columns=["event_id", "name"],
    )
    lines = text.splitlines()
    assert lines
    header = lines[0].split("\t")
    assert "format_version" in header
    assert "details_json_path" in header
    assert "details_json_url" in header


def test_no_fork_core_entry_shared_by_cli_and_mcp(monkeypatch) -> None:
    calls = []

    class _FakeEngine:
        async def execute(self, operation, args, context=None):  # type: ignore[no-untyped-def]
            calls.append((operation, getattr(context, "transport", "")))
            return {
                "schema_version": "2.0.0",
                "tool_version": "1.0.0",
                "result_kind": operation,
                "ok": True,
                "success": True,
                "data": {"operation": operation},
                "operation": operation,
                "artifacts": [],
                "error": None,
                "meta": {"transport": getattr(context, "transport", "unknown")},
            }

    async def _noop_startup() -> None:
        return None

    monkeypatch.setattr(server, "runtime_startup", _noop_startup)
    monkeypatch.setattr(server, "_ensure_core_engine", lambda: _FakeEngine())

    cli_payload = asyncio.run(server.dispatch_operation("rd.core.get_version", {}, transport="cli"))
    mcp_payload_text = asyncio.run(server._dispatch_tool("rd.core.get_version", {}))
    mcp_payload = json.loads(mcp_payload_text)

    assert cli_payload["ok"] is True
    assert mcp_payload["ok"] is True
    assert calls[0][1] == "cli"
    assert calls[1][1] == "mcp"
    assert calls[0][0] == calls[1][0] == "rd.core.get_version"


def test_no_fork_core_entry_shared_by_daemon_exec(monkeypatch) -> None:
    calls = []

    async def _fake_dispatch(operation, args, transport="core", remote=False):  # type: ignore[no-untyped-def]
        calls.append((operation, transport, remote, dict(args)))
        return {
            "schema_version": "2.0.0",
            "tool_version": "1.0.0",
            "result_kind": operation,
            "ok": True,
            "success": True,
            "data": {"operation": operation},
            "operation": operation,
            "artifacts": [],
            "error": None,
            "meta": {"transport": transport},
        }

    monkeypatch.setattr("rdx.daemon.server.dispatch_operation", _fake_dispatch)

    runtime = DaemonRuntime(pipe_name="rdx-test", token="tok")
    response = runtime.handle_request(
        {
            "token": "tok",
            "method": "exec",
            "params": {
                "operation": "rd.core.get_version",
                "args": {"x": 1},
                "transport": "daemon",
                "remote": False,
            },
        },
    )
    assert response["ok"] is True
    assert response["result"]["ok"] is True
    assert calls
    assert calls[0][0] == "rd.core.get_version"
    assert calls[0][1] == "daemon"


def test_aird_bridge_writes_action_chain_and_evidence(tmp_path, monkeypatch) -> None:
    registry = OperationRegistry()

    async def handler(args, env):  # type: ignore[no-untyped-def]
        return {"success": True, "value": 7}

    registry.set_default(handler)
    engine = CoreEngine(registry=registry)

    aird_root = tmp_path / "debug-agent"
    sess_dir = aird_root / "common" / "knowledge" / "library" / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / ".current_session").write_text("session-test", encoding="utf-8")

    monkeypatch.setenv("RDX_AIRD_ENABLE", "1")
    monkeypatch.setenv("RDX_AIRD_ROOT", str(aird_root))
    payload = asyncio.run(engine.execute("rd.test.aird", {"k": "v"}))

    assert payload["ok"] is True
    meta = payload.get("meta", {})
    action_path = str(meta.get("aird_action_chain_path") or "")
    evidence_path = str(meta.get("aird_session_evidence_path") or "")
    assert action_path and os.path.isfile(action_path)
    assert evidence_path and os.path.isfile(evidence_path)
