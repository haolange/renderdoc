from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "rdx" / "spec" / "tool_catalog_196.json"
DEFAULT_RDX_RENDERDOC_PATH = Path(r"d:\Projects\Native\Renderdoc MCP\x64\Development\pymodules")
TMP_DIR = Path(tempfile.gettempdir()) / "rdx_mcp_pytest_tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)

REMOVED_OLD_TOOLS = {
    "rd.session.create",
    "rd.session.close",
    "rd.capture.open",
    "rd.capture.list",
    "rd.capture.set_dirs",
    "rd.capture.get_event_tree",
    "rd.event.set",
    "rd.event.bisect_first_bad",
    "rd.output.render",
    "rd.output.readback",
    "rd.verify.naninf",
    "rd.verify.image_diff",
    "rd.pipeline.snapshot",
    "rd.shader.export_artifacts",
    "rd.debug.pixel",
    "rd.patch.apply",
    "rd.patch.revert",
    "rd.experiment.run",
    "rd.report.build_bundle",
}

REMOVED_EXTENSION_TOOLS = {
    ".".join(["rd", "kb", "search"]),
    ".".join(["rd", "fingerprint", "match"]),
    ".".join(["rd", "pipeline", "run_full_debug"]),
}


def _pick_rdc_path() -> Path:
    candidates = [
        Path(r"C:\Users\a1824\Desktop\rdcFiles\TestRdc.rdc"),
        Path(r"C:\Users\a1824\Desktop\rdcFiles\TestRdc_Desktop.rdc"),
        Path(r"C:\Users\a1824\Desktop\rdcFiles\TestRdc_Mobile.rdc"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    fallback_dir = Path.home() / "Desktop" / "rdcFiles"
    if fallback_dir.is_dir():
        for path in sorted(fallback_dir.glob("*.rdc")):
            if path.is_file():
                return path
    return candidates[0]


RDC_PATH = _pick_rdc_path()


def _server_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.setdefault("RDX_RENDERDOC_PATH", str(DEFAULT_RDX_RENDERDOC_PATH))
    data_dir = Path(tempfile.gettempdir()) / f"rdx_mcp_pytest_data_{uuid.uuid4().hex[:10]}"
    data_dir.mkdir(parents=True, exist_ok=True)
    env["RDX_DATA_DIR"] = str(data_dir)
    env["RDX_ARTIFACT_DIR"] = str(TMP_DIR / f"artifacts_{uuid.uuid4().hex[:8]}")
    return env


def _parse_tool_result(result: Any) -> Tuple[Dict[str, Any] | None, str]:
    text = ""
    if hasattr(result, "content") and result.content:
        first = result.content[0]
        text = getattr(first, "text", str(first))
    try:
        return json.loads(text), text
    except Exception:
        return None, text


async def _call_tool(session: ClientSession, name: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any] | None, str]:
    result = await session.call_tool(name, args)
    return _parse_tool_result(result)


def _param_map() -> Dict[str, List[str]]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    mapping = {tool["name"]: list(tool.get("param_names", [])) for tool in data["tools"]}
    return mapping


def _build_args(tool: str, param_names: List[str], ctx: Dict[str, Any]) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    for param in param_names:
        if param in ctx and ctx[param] is not None:
            args[param] = ctx[param]
            continue
        if param == "session_id":
            args[param] = ctx.get("session_id")
        elif param == "capture_file_id":
            args[param] = ctx.get("capture_file_id")
        elif param == "remote_id":
            args[param] = ctx.get("remote_id", "remote_dummy")
        elif param == "shader_debug_id":
            args[param] = ctx.get("shader_debug_id", "sdbg_dummy")
        elif param == "replacement_id":
            args[param] = ctx.get("replacement_id", "repl_dummy")
        elif param == "file_path":
            args[param] = str(RDC_PATH)
        elif param == "frame_index":
            args[param] = 0
        elif param in {"event_id", "event_a", "event_b"}:
            args[param] = ctx.get("event_id", 1)
        elif param in {"x", "y"}:
            args[param] = 1
        elif param in {"mip", "slice", "sample", "offset", "size", "count", "max_elements", "max_results", "num_frames", "capture_delay_ms", "timeout_ms", "stride"}:
            args[param] = 0
        elif param == "stage":
            args[param] = "ps"
        elif param == "mode":
            args[param] = "pixel"
        elif param == "params":
            args[param] = {"x": 1, "y": 1}
        elif param == "target":
            args[param] = {"texture_id": ctx.get("texture_id")} if ctx.get("texture_id") else {}
        elif param == "subresource":
            args[param] = {"mip": 0, "slice": 0, "sample": 0}
        elif param == "rect":
            args[param] = {"x": 0, "y": 0, "w": 1, "h": 1}
        elif param == "event_range":
            args[param] = {"start_event_id": ctx.get("event_id", 1), "end_event_id": ctx.get("event_id", 1)}
        elif param == "pass_range":
            args[param] = {"begin_event_id": ctx.get("event_id", 1), "end_event_id": ctx.get("event_id", 1)}
        elif param == "query":
            args[param] = {"name_contains": "Draw"} if tool.startswith("rd.event.") else "shader"
        elif param == "history_item":
            args[param] = {"event_id": ctx.get("event_id", 1), "flags": "Unknown"}
        elif param == "resource_id":
            args[param] = ctx.get("resource_id", ctx.get("texture_id", "0"))
        elif param == "texture_id":
            args[param] = ctx.get("texture_id", "0")
        elif param == "buffer_id":
            args[param] = ctx.get("buffer_id", "0")
        elif param == "vertex_buffer_id":
            args[param] = ctx.get("buffer_id", "0")
        elif param == "index_buffer_id":
            args[param] = ctx.get("buffer_id", "0")
        elif param == "shader_id":
            args[param] = ctx.get("shader_id", "0")
        elif param == "tex_a":
            args[param] = {"texture_id": ctx.get("texture_id"), "subresource": {"mip": 0, "slice": 0, "sample": 0}}
        elif param == "tex_b":
            args[param] = {"texture_id": ctx.get("texture_id"), "subresource": {"mip": 0, "slice": 0, "sample": 0}}
        elif param == "counter_ids":
            args[param] = []
        elif param == "layout":
            args[param] = {"stride": 4, "fields": [{"name": "v", "type": "u32", "offset": 0}]}
        elif param == "file_format":
            args[param] = "png"
        elif param == "output_dir":
            args[param] = str(TMP_DIR)
        elif param == "output_path":
            args[param] = str(TMP_DIR / f"{tool.replace('.', '_')}.out")
        elif param == "local_path":
            args[param] = str(TMP_DIR / "capture_copy.rdc")
        elif param == "paths":
            args[param] = [str(TMP_DIR)]
        elif param == "a":
            args[param] = "left"
        elif param == "b":
            args[param] = "right"
        elif param == "a_is_path":
            args[param] = False
        elif param == "b_is_path":
            args[param] = False
        elif param == "image_a_path":
            args[param] = str(TMP_DIR / "a.png")
        elif param == "image_b_path":
            args[param] = str(TMP_DIR / "b.png")
        elif param == "algo":
            args[param] = "sha256"
        elif param == "host":
            args[param] = "127.0.0.1"
        elif param == "port":
            args[param] = 38920
        elif param == "connection":
            args[param] = {"pid": 0}
        elif param == "option":
            args[param] = "allow_vsync"
        elif param == "value":
            args[param] = True
        elif param == "name":
            args[param] = "mcp-test"
        elif param == "state_path":
            args[param] = "topology"
        elif param == "target_value":
            args[param] = ""
        elif param == "bundle_spec":
            args[param] = {}
        elif param == "report_spec":
            args[param] = {}
        elif param == "focus":
            args[param] = {}
        elif param == "replacement":
            args[param] = {"stage": "ps", "shader_id": ctx.get("shader_id", "0")}
        elif param == "validation":
            args[param] = {"x": 1, "y": 1}
        elif param == "rdc_path":
            args[param] = str(RDC_PATH)
        elif param == "description":
            args[param] = "test bug description"
        elif param == "backend_type":
            args[param] = "local"
        elif param == "project_id":
            args[param] = "pytest-contract"
        elif param == "fingerprint_type":
            args[param] = "pass"
        elif param == "fingerprint_json":
            args[param] = {"rt_formats": ["RGBA8"], "blend_modes": ["Opaque"], "binding_pattern": ["t0"]}
        elif param == "threshold":
            args[param] = 0.0
    return {k: v for k, v in args.items() if v is not None}


@pytest.mark.asyncio
async def test_tool_list_contract_196():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted([t.name for t in listed.tools])
            assert len(names) == 196
            assert not (REMOVED_EXTENSION_TOOLS & set(names))
            assert not (REMOVED_OLD_TOOLS & set(names))


@pytest.mark.asyncio
async def test_response_contract_all_tools_called_once():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    mapping = _param_map()

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted([t.name for t in listed.tools])

            ctx: Dict[str, Any] = {"output_dir": str(TMP_DIR), "output_path": str(TMP_DIR / "out.bin")}
            if RDC_PATH.is_file():
                await _call_tool(session, "rd.core.init", {"global_env": {"artifact_dir": str(TMP_DIR)}, "enable_remote": True, "enable_app_api": True})
                payload, _ = await _call_tool(session, "rd.capture.open_file", {"file_path": str(RDC_PATH), "read_only": True})
                if payload and payload.get("success"):
                    ctx["capture_file_id"] = payload.get("capture_file_id")
                if ctx.get("capture_file_id"):
                    payload, _ = await _call_tool(session, "rd.capture.open_replay", {"capture_file_id": ctx["capture_file_id"], "options": {}})
                    if payload and payload.get("success"):
                        ctx["session_id"] = payload.get("session_id")
                if ctx.get("session_id"):
                    await _call_tool(session, "rd.replay.set_frame", {"session_id": ctx["session_id"], "frame_index": 0})
                    payload, _ = await _call_tool(session, "rd.replay.get_frame_info", {"session_id": ctx["session_id"], "frame_index": 0})
                    if payload and payload.get("success"):
                        event_range = payload.get("frame_info", {}).get("event_range", {})
                        ctx["event_id"] = event_range.get("start") or 1
                    payload, _ = await _call_tool(session, "rd.resource.list_textures", {"session_id": ctx["session_id"]})
                    if payload and payload.get("success") and payload.get("textures"):
                        t = payload["textures"][0]
                        ctx["texture_id"] = t.get("texture_id") or t.get("resource_id")
                        ctx["resource_id"] = ctx["texture_id"]
                    payload, _ = await _call_tool(session, "rd.resource.list_buffers", {"session_id": ctx["session_id"]})
                    if payload and payload.get("success") and payload.get("buffers"):
                        b = payload["buffers"][0]
                        ctx["buffer_id"] = b.get("buffer_id") or b.get("resource_id")
                        ctx.setdefault("resource_id", ctx["buffer_id"])
                    payload, _ = await _call_tool(session, "rd.pipeline.get_shader", {"session_id": ctx["session_id"], "stage": "ps"})
                    if payload and payload.get("success") and payload.get("shader"):
                        ctx["shader_id"] = payload["shader"].get("shader_id")
                    payload, _ = await _call_tool(session, "rd.shader.debug_start", {"session_id": ctx["session_id"], "mode": "pixel", "params": {"x": 1, "y": 1}, "event_id": ctx.get("event_id", 1)})
                    if payload and payload.get("success"):
                        ctx["shader_debug_id"] = payload.get("shader_debug_id")
                    payload, _ = await _call_tool(session, "rd.shader.edit_and_replace", {"session_id": ctx["session_id"], "stage": "ps", "shader_id": ctx.get("shader_id", "")})
                    if payload and payload.get("success"):
                        ctx["replacement_id"] = payload.get("replacement_id")
            payload, _ = await _call_tool(session, "rd.remote.connect", {"host": "127.0.0.1", "port": 38920})
            if payload and payload.get("success"):
                ctx["remote_id"] = payload.get("remote_id")

            call_errors: List[Tuple[str, str]] = []
            parse_errors: List[Tuple[str, str]] = []
            schema_errors: List[str] = []

            for name in names:
                args = _build_args(name, mapping.get(name, []), ctx)
                try:
                    payload, raw = await _call_tool(session, name, args)
                except Exception as exc:
                    call_errors.append((name, str(exc)))
                    continue
                if payload is None:
                    parse_errors.append((name, raw[:200]))
                    continue
                if "success" not in payload:
                    schema_errors.append(name)
                    continue
                if payload.get("success") is False and "error_message" not in payload:
                    schema_errors.append(name)

            assert not call_errors, f"tool call exceptions: {call_errors[:5]}"
            assert not parse_errors, f"non-json responses: {parse_errors[:5]}"
            assert not schema_errors, f"schema violations: {schema_errors[:10]}"


@pytest.mark.asyncio
async def test_local_rdc_end_to_end_chain():
    if not RDC_PATH.is_file():
        pytest.skip(f"missing test rdc: {RDC_PATH}")

    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            p, _ = await _call_tool(session, "rd.core.init", {"global_env": {"artifact_dir": str(TMP_DIR)}, "enable_remote": True, "enable_app_api": True})
            assert p and "success" in p

            p, _ = await _call_tool(session, "rd.capture.open_file", {"file_path": str(RDC_PATH), "read_only": True})
            assert p and p.get("success"), p
            capture_file_id = p["capture_file_id"]

            p, _ = await _call_tool(session, "rd.capture.open_replay", {"capture_file_id": capture_file_id, "options": {}})
            assert p and p.get("success"), p
            session_id = p["session_id"]

            for tool, args in [
                ("rd.replay.set_frame", {"session_id": session_id, "frame_index": 0}),
                ("rd.event.get_action_tree", {"session_id": session_id, "max_depth": 3, "filter": {}}),
                ("rd.pipeline.get_state", {"session_id": session_id, "detail_level": "summary"}),
                ("rd.resource.list_textures", {"session_id": session_id}),
                ("rd.resource.list_buffers", {"session_id": session_id}),
                ("rd.perf.enumerate_counters", {"session_id": session_id}),
                ("rd.diag.scan_common_issues", {"session_id": session_id, "severity_min": "info", "include_suggestions": True}),
                ("rd.macro.summarize_frame", {"session_id": session_id, "frame_index": 0}),
            ]:
                payload, raw = await _call_tool(session, tool, args)
                assert payload is not None, f"{tool} returned non-json: {raw[:160]}"
                assert "success" in payload, f"{tool} missing success: {payload}"

            p_tex, _ = await _call_tool(session, "rd.resource.list_textures", {"session_id": session_id})
            texture_id = None
            if p_tex and p_tex.get("success") and p_tex.get("textures"):
                texture_id = p_tex["textures"][0].get("texture_id") or p_tex["textures"][0].get("resource_id")

            if texture_id:
                payload, raw = await _call_tool(
                    session,
                    "rd.texture.get_data",
                    {
                        "session_id": session_id,
                        "texture_id": texture_id,
                        "subresource": {"mip": 0, "slice": 0, "sample": 0},
                        "format": "raw",
                    },
                )
                assert payload is not None, f"rd.texture.get_data non-json: {raw[:160]}"
                assert "success" in payload

                payload, raw = await _call_tool(
                    session,
                    "rd.export.screenshot",
                    {
                        "session_id": session_id,
                        "target": {"texture_id": texture_id},
                        "event_id": 0,
                        "output_path": str(TMP_DIR / "chain_screenshot.png"),
                        "file_format": "png",
                        "include_alpha": True,
                        "overlay": "none",
                    },
                )
                assert payload is not None, f"rd.export.screenshot non-json: {raw[:160]}"
                assert "success" in payload

            payload, raw = await _call_tool(session, "rd.remote.connect", {"host": "127.0.0.1", "port": 38920})
            assert payload is not None, f"rd.remote.connect non-json: {raw[:160]}"
            assert "success" in payload
            remote_id = payload.get("remote_id")
            if remote_id:
                payload, raw = await _call_tool(session, "rd.remote.ping", {"remote_id": remote_id})
                assert payload is not None, f"rd.remote.ping non-json: {raw[:160]}"
                assert "success" in payload
                if payload.get("success") is False:
                    assert "error_message" in payload
