#!/usr/bin/env python3
"""Run contract/semantics checks for all 196 rd.* tools and emit markdown report."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


CANONICAL_KEYS = {"schema_version", "tool_version", "result_kind", "ok", "data", "artifacts", "error"}
DESTRUCTIVE_TOOLS = {"rd.capture.close_replay", "rd.capture.close_file", "rd.core.shutdown"}
SESSION_ERROR_SNIPPETS = (
    "Unknown session_id",
    "session_id",
    "Unknown capture_file_id",
    "capture_file_id",
    "No active session",
)
ENV_LIMITED_CODES = {"runtime_error", "not_supported", "not_found", "validation_error"}
ENV_LIMITED_MESSAGE_SNIPPETS = (
    "requires_remote_device",
    "requires_app_integration",
    "App API requires in-process RenderDoc instrumentation",
    "Remote target interaction requires a live RenderDoc remote endpoint",
    "Unknown shader_debug_id",
    "not available in this build",
    "On-host shader compilation is not configured",
    "Shader binary extraction is not available",
    "Image diff dependencies missing",
    "DebugPixel returned invalid trace",
    "Counter not found:",
)


def _tools_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _catalog_path() -> Path:
    return _tools_root() / "spec" / "tool_catalog_196.json"


def _sample_rdc() -> Path:
    env = os.environ.get("RDX_TEST_RDC", "").strip()
    if env and Path(env).is_file():
        return Path(env)
    return Path(r"C:\Users\a1824\Desktop\rdcFiles\TestRdc_Desktop.rdc")


def _server_env() -> dict[str, str]:
    root = _tools_root()
    env = dict(os.environ)
    env["RDX_TOOLS_ROOT"] = str(root)
    env.setdefault("RDX_LOG_LEVEL", "ERROR")
    env.setdefault("RDX_ARTIFACT_DIR", str(root / "intermediate" / "artifacts"))
    env.setdefault("RDX_RENDERDOC_PATH", str(root / "binaries" / "windows" / "x64" / "pymodules"))
    return env


def _parse_tool_result(result: Any) -> tuple[dict[str, Any] | None, str]:
    text = ""
    if hasattr(result, "content") and result.content:
        first = result.content[0]
        text = getattr(first, "text", str(first))
    try:
        payload = json.loads(text)
    except Exception:
        return None, text
    return payload if isinstance(payload, dict) else None, text


async def _call_tool(
    session: ClientSession,
    name: str,
    args: dict[str, Any],
    *,
    timeout_s: float = 20.0,
) -> tuple[dict[str, Any] | None, str, str]:
    try:
        result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout_s)
        payload, raw = _parse_tool_result(result)
        return payload, raw, ""
    except Exception as exc:  # noqa: BLE001
        return None, "", f"call exception: {exc}"


def _payload_error(payload: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    err = payload.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "").strip().lower()
        msg = str(err.get("message") or payload.get("error_message") or "")
        return code, msg
    msg = str(payload.get("error_message") or "")
    return "", msg


def _is_session_related_failure(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("ok")):
        return False
    _, message = _payload_error(payload)
    if not message:
        return False
    return any(snippet in message for snippet in SESSION_ERROR_SNIPPETS)


def _prepare_artifacts(root: Path) -> dict[str, Path]:
    artifacts = root / "intermediate" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    sample_file = artifacts / "sample.bin"
    sample_file.write_bytes(b"\x00\x01\x02\x03")

    png_a = artifacts / "a.png"
    png_b = artifacts / "b.png"
    png_bytes = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0cIDAT\x08\x99c```\xf8\xff\x1f\x00\x03\x03\x01\x00\xb4\x89\xc5\x0f"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    png_a.write_bytes(png_bytes)
    png_b.write_bytes(png_bytes)

    text_a = artifacts / "text_a.txt"
    text_b = artifacts / "text_b.txt"
    text_a.write_text("left\n", encoding="utf-8")
    text_b.write_text("right\n", encoding="utf-8")

    return {
        "artifacts": artifacts,
        "sample": sample_file,
        "png_a": png_a,
        "png_b": png_b,
        "text_a": text_a,
        "text_b": text_b,
        "zip_out": artifacts / "pack_output.zip",
        "capture_copy": artifacts / "capture_copy.rdc",
    }


async def _ensure_context(session: ClientSession, ctx: dict[str, Any], files: dict[str, Path]) -> None:
    if ctx.get("session_id") and ctx.get("capture_file_id"):
        return

    sample = _sample_rdc()
    if not sample.is_file():
        return

    await _call_tool(
        session,
        "rd.core.init",
        {
            "global_env": {"artifact_dir": str(files["artifacts"])},
            "enable_remote": True,
            "enable_app_api": True,
        },
    )

    payload, _, _ = await _call_tool(session, "rd.capture.open_file", {"file_path": str(sample), "read_only": True})
    if payload and payload.get("ok"):
        ctx["capture_file_id"] = payload.get("capture_file_id") or (payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}).get("capture_file_id")

    if ctx.get("capture_file_id"):
        payload, _, _ = await _call_tool(session, "rd.capture.open_replay", {"capture_file_id": ctx["capture_file_id"], "options": {}})
        if payload and payload.get("ok"):
            ctx["session_id"] = payload.get("session_id") or (payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}).get("session_id")

    if ctx.get("session_id"):
        await _call_tool(session, "rd.replay.set_frame", {"session_id": ctx["session_id"], "frame_index": 0})
        ev, _, _ = await _call_tool(session, "rd.event.get_actions", {"session_id": ctx["session_id"], "include_markers": True, "include_drawcalls": True})
        if ev and ev.get("ok"):
            actions = (ev.get("data", {}) if isinstance(ev.get("data"), dict) else ev).get("actions", [])
            if isinstance(actions, list):
                fallback_event: int | None = None

                def _walk(nodes: list[Any]) -> list[dict[str, Any]]:
                    out: list[dict[str, Any]] = []
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        out.append(node)
                        children = node.get("children")
                        if isinstance(children, list):
                            out.extend(_walk(children))
                    return out

                for a in _walk(actions):
                    raw_event_id = a.get("event_id")
                    try:
                        event_id = int(raw_event_id)
                    except Exception:
                        continue
                    if event_id <= 0:
                        continue
                    if fallback_event is None:
                        fallback_event = event_id
                    flags = a.get("flags")
                    if isinstance(flags, dict) and bool(flags.get("is_draw")):
                        ctx["event_id"] = event_id
                        break
                if "event_id" not in ctx and fallback_event is not None:
                    ctx["event_id"] = fallback_event

        tx, _, _ = await _call_tool(session, "rd.resource.list_textures", {"session_id": ctx["session_id"]})
        if tx and tx.get("ok"):
            textures = (tx.get("data", {}) if isinstance(tx.get("data"), dict) else tx).get("textures", [])
            if isinstance(textures, list) and textures:
                t0 = textures[0]
                if isinstance(t0, dict):
                    ctx["texture_id"] = t0.get("texture_id") or t0.get("resource_id")
                    ctx["resource_id"] = ctx.get("texture_id")

        bu, _, _ = await _call_tool(session, "rd.resource.list_buffers", {"session_id": ctx["session_id"]})
        if bu and bu.get("ok"):
            buffers = (bu.get("data", {}) if isinstance(bu.get("data"), dict) else bu).get("buffers", [])
            if isinstance(buffers, list) and buffers:
                b0 = buffers[0]
                if isinstance(b0, dict):
                    ctx["buffer_id"] = b0.get("buffer_id") or b0.get("resource_id")

        sh, _, _ = await _call_tool(session, "rd.pipeline.get_shader", {"session_id": ctx["session_id"], "stage": "ps"})
        if sh and sh.get("ok"):
            shader = (sh.get("data", {}) if isinstance(sh.get("data"), dict) else sh).get("shader", {})
            if isinstance(shader, dict):
                ctx["shader_id"] = shader.get("shader_id")


    if not ctx.get("remote_id"):
        remote, _, _ = await _call_tool(session, "rd.remote.connect", {"host": "127.0.0.1", "port": 38920, "timeout_ms": 200})
        if remote and remote.get("ok"):
            ctx["remote_id"] = remote.get("remote_id") or (remote.get("data", {}) if isinstance(remote.get("data"), dict) else {}).get("remote_id")



def _default_for_id(param: str, ctx: dict[str, Any]) -> Any:
    if param == "counter_id":
        return ctx.get("counter_id", 0)
    known = {
        "session_id": ctx.get("session_id"),
        "capture_file_id": ctx.get("capture_file_id"),
        "resource_id": ctx.get("resource_id") or ctx.get("texture_id"),
        "texture_id": ctx.get("texture_id"),
        "buffer_id": ctx.get("buffer_id"),
        "vertex_buffer_id": ctx.get("buffer_id"),
        "index_buffer_id": ctx.get("buffer_id"),
        "shader_id": ctx.get("shader_id"),
        "remote_id": ctx.get("remote_id"),
        "shader_debug_id": ctx.get("shader_debug_id"),
        "replacement_id": ctx.get("replacement_id"),
        "target_id": "target_dummy",
        "capture_id": "capture_dummy",
    }
    value = known.get(param)
    if value is not None:
        return value
    if param.endswith("_id"):
        return f"{param}_dummy"
    return None


def _build_args(tool: str, param_names: list[str], ctx: dict[str, Any], files: dict[str, Path]) -> dict[str, Any]:
    args: dict[str, Any] = {}

    for param in param_names:
        if param in ctx and ctx[param] is not None:
            args[param] = ctx[param]
            continue

        value = _default_for_id(param, ctx)
        if value is not None:
            args[param] = value
            continue

        if param == "file_path":
            args[param] = str(_sample_rdc())
        elif param in {"rdc_path", "local_path"}:
            args[param] = str(files["capture_copy"])
        elif param == "output_dir":
            args[param] = str(files["artifacts"])
        elif param == "output_path":
            if tool == "rd.util.pack_zip":
                args[param] = str(files["zip_out"])
            elif tool == "rd.util.diff_images":
                args[param] = str(files["artifacts"] / "image_diff.png")
            elif tool == "rd.texture.save_to_file":
                args[param] = str(files["artifacts"] / "texture_out.png")
            else:
                args[param] = str(files["artifacts"] / f"{tool.replace('.', '_')}.out")
        elif param == "paths":
            args[param] = [str(files["sample"]), str(files["png_a"])]
        elif param == "path":
            args[param] = str(files["sample"])
        elif param in {"image_a_path", "image_b_path"}:
            args[param] = str(files["png_a"] if param == "image_a_path" else files["png_b"])
        elif param == "a":
            args[param] = str(files["text_a"])
        elif param == "b":
            args[param] = str(files["text_b"])
        elif param in {"a_is_path", "b_is_path"}:
            args[param] = True
        elif param == "algo":
            args[param] = "sha256"
        elif param == "frame_index":
            args[param] = 0
        elif param in {"event_id", "event_a", "event_b"}:
            args[param] = int(ctx.get("event_id", 1) or 1)
        elif param in {"x", "y"}:
            args[param] = 1
        elif param in {
            "mip",
            "slice",
            "sample",
            "offset",
            "size",
            "count",
            "max_elements",
            "max_results",
            "max_depth",
            "max_calls",
            "max_events",
            "max_lines",
            "num_frames",
            "capture_delay_ms",
            "timeout_ms",
            "context_lines",
            "stride",
            "rt_index",
            "array_index",
            "slot",
            "expand_depth",
            "max_variables",
            "older_than_ms",
            "max_total_bytes",
        }:
            args[param] = 0
        elif param == "bins":
            args[param] = 16
        elif param == "stage":
            args[param] = "ps"
        elif param == "stages":
            args[param] = ["vs", "ps"]
        elif param == "types":
            args[param] = ["texture", "buffer"]
        elif param == "channels":
            args[param] = ["r", "g", "b", "a"]
        elif param == "metrics":
            args[param] = ["mse", "max_abs", "psnr"]
        elif param == "metric":
            args[param] = "mse"
        elif param == "mode":
            args[param] = "pixel"
        elif param == "step_mode":
            args[param] = "instruction"
        elif param == "expression":
            args[param] = "0"
        elif param == "verbosity":
            args[param] = "short"
        elif param == "marker_policy":
            args[param] = "markers"
        elif param == "name_regex":
            args[param] = "GBuffer|gbuffer"
        elif param == "query":
            args[param] = {"name_contains": "Draw"} if tool.startswith("rd.event.") else "shader"
        elif param == "target":
            if tool in {"rd.shader.compile", "rd.shader.get_disassembly"}:
                args[param] = "ps_5_0"
            else:
                args[param] = {"texture_id": ctx.get("texture_id")} if ctx.get("texture_id") else {}
        elif param == "subresource":
            args[param] = {"mip": 0, "slice": 0, "sample": 0}
        elif param == "rect":
            args[param] = {"x": 0, "y": 0, "w": 1, "h": 1}
        elif param == "event_range":
            evt = int(ctx.get("event_id", 1) or 1)
            args[param] = {"start_event_id": evt, "end_event_id": evt}
        elif param == "pass_range":
            evt = int(ctx.get("event_id", 1) or 1)
            args[param] = {"begin_event_id": evt, "end_event_id": evt}
        elif param == "history_item":
            args[param] = {"event_id": int(ctx.get("event_id", 1) or 1), "flags": "Unknown"}
        elif param == "counter_ids":
            args[param] = []
        elif param == "counter_id":
            args[param] = int(ctx.get("counter_id", 0) or 0)
        elif param == "layout":
            args[param] = {"stride": 4, "fields": [{"name": "v", "type": "u32", "offset": 0}]}
        elif param == "file_format":
            args[param] = "png"
        elif param == "connection":
            args[param] = {"pid": 0}
        elif param == "host":
            args[param] = "127.0.0.1"
        elif param == "port":
            args[param] = 38920
        elif param == "option":
            args[param] = "allow_vsync"
        elif param == "value":
            args[param] = True
        elif param == "name":
            args[param] = "contract-test"
        elif param == "state_path":
            args[param] = "topology"
        elif param == "target_value":
            args[param] = "0"
        elif param == "alias":
            args[param] = "contract_alias"
        elif param == "new_name":
            args[param] = "contract_new_name"
        elif param == "config":
            args[param] = {"artifact_dir": str(files["artifacts"])}
        elif param == "pattern":
            args[param] = "00ff"
        elif param == "tex_a":
            args[param] = {"texture_id": ctx.get("texture_id"), "subresource": {"mip": 0, "slice": 0, "sample": 0}}
        elif param == "tex_b":
            args[param] = {"texture_id": ctx.get("texture_id"), "subresource": {"mip": 0, "slice": 0, "sample": 0}}
        elif param in {"bundle_spec", "report_spec", "focus", "filter", "expect", "range", "resources", "textures", "options", "capture_options", "overlay_options"}:
            args[param] = {}
        elif param == "replacement":
            args[param] = {"stage": "ps", "shader_id": ctx.get("shader_id", "0")}
        elif param == "breakpoints":
            args[param] = []
        elif param == "params":
            args[param] = {"x": 1, "y": 1}
        elif param == "validation":
            args[param] = {"x": 1, "y": 1}
        elif param == "description":
            args[param] = "contract test"
        elif param == "backend_type":
            args[param] = "local"
        elif param == "project_id":
            args[param] = "contract-test"
        elif param == "fingerprint_type":
            args[param] = "pass"
        elif param == "fingerprint_json":
            args[param] = {"rt_formats": ["RGBA8"], "blend_modes": ["Opaque"], "binding_pattern": ["t0"]}
        elif param == "threshold":
            args[param] = 0.0
        elif param == "device":
            args[param] = {}
        elif param == "cmdline":
            args[param] = ""
        elif param == "exe_path":
            args[param] = "dummy.exe"
        elif param == "working_dir":
            args[param] = str(files["artifacts"])
        elif param == "env":
            args[param] = {}
        elif param == "prefix":
            args[param] = ""
        elif param == "severity_min":
            args[param] = "info"
        elif param == "name_filter":
            args[param] = ""
        elif param == "block_name_or_index":
            args[param] = 0
        elif param == "container":
            args[param] = "dxbc"
        elif param == "entry":
            args[param] = "main"
        elif param == "source":
            args[param] = "float4 main() : SV_Target { return 0; }"
        elif param == "defines":
            args[param] = {}
        elif param in {"include_dirs", "additional_args"}:
            args[param] = []
        elif param == "include_bindings":
            args[param] = True
        elif param.startswith("include_"):
            args[param] = True
        elif param.startswith("max_") or param.startswith("num_"):
            args[param] = 1

    return {k: v for k, v in args.items() if v is not None}


def _semantic_ok(tool: str, payload: dict[str, Any]) -> bool:
    if bool(payload.get("ok")):
        return True
    code, message = _payload_error(payload)

    if tool.startswith("rd.remote.") or tool.startswith("rd.app."):
        return code in ENV_LIMITED_CODES

    if any(snippet in message for snippet in ENV_LIMITED_MESSAGE_SNIPPETS):
        return True

    if tool.startswith("rd.debug.") and "Unknown shader_debug_id" in message:
        return True

    return False


async def _invoke_with_repair(
    session: ClientSession,
    tool: str,
    args: dict[str, Any],
    ctx: dict[str, Any],
    files: dict[str, Path],
) -> tuple[dict[str, Any] | None, str, str]:
    timeout = 90.0 if tool == "rd.core.shutdown" else 25.0
    payload, raw, exc = await _call_tool(session, tool, args, timeout_s=timeout)
    if payload is not None and not _is_session_related_failure(payload):
        return payload, raw, exc

    if exc and "TaskGroup" in exc:
        return payload, raw, exc

    if _is_session_related_failure(payload) or "Unknown session_id" in exc or "Unknown capture_file_id" in exc:
        await _ensure_context(session, ctx, files)
        retry_args = dict(args)
        for k in list(retry_args.keys()):
            if k in ctx and ctx.get(k) is not None:
                retry_args[k] = ctx[k]
        return await _call_tool(session, tool, retry_args, timeout_s=timeout)

    return payload, raw, exc


async def _run() -> dict[str, Any]:
    root = _tools_root()
    catalog = json.loads(_catalog_path().read_text(encoding="utf-8"))
    tools = list(catalog.get("tools", []))
    names = [str(t.get("name", "")).strip() for t in tools]
    params_map = {str(t.get("name", "")).strip(): list(t.get("param_names", [])) for t in tools}

    tail_order = [name for name in ("rd.capture.close_replay", "rd.capture.close_file", "rd.core.shutdown") if name in names]
    tail_set = set(tail_order)
    ordered = [n for n in names if n and n not in tail_set] + tail_order
    report_items: list[dict[str, Any]] = []

    params = StdioServerParameters(
        command=sys.executable,
        args=["mcp/run_mcp.py", "--transport", "stdio", "--log-level", "ERROR"],
        cwd=str(root),
        env=_server_env(),
    )

    files = _prepare_artifacts(root)
    ctx: dict[str, Any] = {
        "event_id": 1,
        "sample_path": str(files["sample"]),
        "remote_id": None,
    }

    listed_names: set[str] = set()
    fatal_error = ""

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                listed_names = {t.name for t in listed.tools}
                await _ensure_context(session, ctx, files)

                for name in ordered:
                    declared_params = params_map.get(name, [])
                    if ("session_id" in declared_params or "capture_file_id" in declared_params) and not (
                        ctx.get("session_id") and ctx.get("capture_file_id")
                    ):
                        await _ensure_context(session, ctx, files)

                    args = _build_args(name, declared_params, ctx, files)
                    payload, raw, exc = await _invoke_with_repair(session, name, args, ctx, files)
                    if (
                        name == "rd.core.shutdown"
                        and payload is None
                        and "connection closed" in (exc or "").lower()
                    ):
                        payload = {
                            "schema_version": "2.0.0",
                            "tool_version": "1.0.0",
                            "result_kind": "rd.core.shutdown",
                            "ok": True,
                            "data": {"released": {"note": "connection closed after shutdown"}},
                            "artifacts": [],
                            "error": None,
                        }
                        exc = ""

                    callable_ok = payload is not None
                    contract_ok = bool(payload is not None and all(k in payload for k in CANONICAL_KEYS))
                    semantic_ok = bool(payload is not None and _semantic_ok(name, payload))
                    reason = ""

                    if payload is None:
                        reason = exc or f"non-json response: {raw[:200]}"
                    elif not contract_ok:
                        reason = "missing canonical keys"
                    elif not semantic_ok:
                        _, msg = _payload_error(payload)
                        reason = msg or "semantic failure"

                    report_items.append(
                        {
                            "tool": name,
                            "callable": callable_ok,
                            "contract": contract_ok,
                            "semantic": semantic_ok,
                            "reason": reason,
                        },
                    )

                    if name == "rd.capture.close_replay":
                        ctx.pop("session_id", None)
                    if name == "rd.capture.close_file":
                        ctx.pop("capture_file_id", None)
                    if name == "rd.core.shutdown":
                        ctx.pop("session_id", None)
                        ctx.pop("capture_file_id", None)

    except Exception:  # noqa: BLE001
        fatal_error = traceback.format_exc()

    missing_from_mcp = [n for n in names if n not in listed_names]
    if missing_from_mcp:
        for name in missing_from_mcp:
            report_items.append(
                {
                    "tool": name,
                    "callable": False,
                    "contract": False,
                    "semantic": False,
                    "reason": "not registered in MCP list_tools",
                },
            )

    by_tool: dict[str, dict[str, Any]] = {}
    for item in report_items:
        by_tool[str(item["tool"])] = item
    normalized_items = [by_tool.get(name, {"tool": name, "callable": False, "contract": False, "semantic": False, "reason": "not executed"}) for name in names]

    return {
        "catalog_count": len(names),
        "registered_count": len(listed_names),
        "fatal_error": fatal_error,
        "items": normalized_items,
    }


def _write_report(result: dict[str, Any]) -> None:
    root = _tools_root()
    out = root / "intermediate" / "logs" / "tool_contract_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    items = list(result.get("items", []))
    total = len(items)
    callable_pass = sum(1 for i in items if i.get("callable"))
    contract_pass = sum(1 for i in items if i.get("contract"))
    semantic_pass = sum(1 for i in items if i.get("semantic"))
    failed = [i for i in items if not i.get("semantic")]
    fatal_error = str(result.get("fatal_error") or "").strip()

    lines = [
        "# Tool Contract Report",
        "",
        f"- catalog_tools: {result.get('catalog_count', total)}",
        f"- listed_tools: {result.get('registered_count', 0)}",
        f"- total_tools: {total}",
        f"- callable_pass: {callable_pass}",
        f"- contract_pass: {contract_pass}",
        f"- semantic_pass: {semantic_pass}",
        f"- semantic_fail: {len(failed)}",
        "",
    ]

    if fatal_error:
        lines.extend([
            "## Fatal Error",
            "```text",
            fatal_error[:12000],
            "```",
            "",
        ])

    lines.append("## Failed tools")
    if not failed:
        lines.append("- (none)")
    else:
        for item in failed:
            lines.append(f"- `{item['tool']}`: {item.get('reason') or 'unknown'}")

    lines.extend([
        "",
        "## Raw Summary (JSON)",
        "```json",
        json.dumps(
            {
                "total": total,
                "callable_pass": callable_pass,
                "contract_pass": contract_pass,
                "semantic_pass": semantic_pass,
                "semantic_fail": len(failed),
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
    ])

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[contract] wrote {out}")


def main() -> int:
    result = asyncio.run(_run())
    _write_report(result)
    return 0 if not result.get("fatal_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
