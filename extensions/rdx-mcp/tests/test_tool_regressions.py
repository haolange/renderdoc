from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RDX_RENDERDOC_PATH = Path(r"d:\Projects\Native\Renderdoc MCP\x64\Development\pymodules")
TMP_DIR = Path(tempfile.gettempdir()) / "rdx_mcp_pytest_regression_tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)


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
    data_dir = Path(tempfile.gettempdir()) / f"rdx_mcp_pytest_regr_data_{uuid.uuid4().hex[:10]}"
    data_dir.mkdir(parents=True, exist_ok=True)
    env["RDX_DATA_DIR"] = str(data_dir)
    env["RDX_ARTIFACT_DIR"] = str(TMP_DIR / f"artifacts_{uuid.uuid4().hex[:8]}")
    env["RDX_LOG_LEVEL"] = "WARNING"
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


async def _open_replay_session(session: ClientSession) -> Tuple[str, str]:
    if not RDC_PATH.is_file():
        pytest.skip(f"missing test rdc: {RDC_PATH}")
    payload, _ = await _call_tool(
        session,
        "rd.core.init",
        {"global_env": {"artifact_dir": str(TMP_DIR)}, "enable_remote": True, "enable_app_api": True},
    )
    assert payload is not None and "success" in payload

    payload, _ = await _call_tool(session, "rd.capture.open_file", {"file_path": str(RDC_PATH), "read_only": True})
    if not payload or not payload.get("success"):
        pytest.skip(f"failed to open rdc: {payload}")
    capture_file_id = payload["capture_file_id"]

    payload, _ = await _call_tool(session, "rd.capture.open_replay", {"capture_file_id": capture_file_id, "options": {}})
    if not payload or not payload.get("success"):
        pytest.skip(f"failed to open replay: {payload}")
    session_id = payload["session_id"]

    await _call_tool(session, "rd.replay.set_frame", {"session_id": session_id, "frame_index": 0})
    return session_id, capture_file_id


async def _pick_texture_id(session: ClientSession, session_id: str) -> str:
    payload, _ = await _call_tool(session, "rd.resource.list_textures", {"session_id": session_id})
    textures = (payload or {}).get("textures", [])
    if not textures:
        pytest.skip("capture has no textures")
    texture = textures[0]
    texture_id = texture.get("texture_id") or texture.get("resource_id")
    if not texture_id:
        pytest.skip("capture returned texture without id")
    return str(texture_id)


@pytest.mark.asyncio
async def test_search_actions_accepts_string_query():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            payload, raw = await _call_tool(session, "rd.event.search_actions", {"session_id": session_id, "query": "Draw"})
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            assert isinstance(payload.get("matches", []), list)


@pytest.mark.asyncio
async def test_export_screenshot_accepts_scalar_target():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)

            payload, _ = await _call_tool(session, "rd.resource.list_textures", {"session_id": session_id})
            textures = (payload or {}).get("textures", [])
            if not textures:
                pytest.skip("capture has no textures")
            texture_id = textures[0].get("texture_id") or textures[0].get("resource_id")
            if not texture_id:
                pytest.skip("no usable texture id")

            output_path = TMP_DIR / f"screenshot_{uuid.uuid4().hex[:8]}.png"
            call_payload, raw = await _call_tool(
                session,
                "rd.export.screenshot",
                {
                    "session_id": session_id,
                    "target": str(texture_id),
                    "event_id": 0,
                    "output_path": str(output_path),
                    "file_format": "png",
                    "overlay": "none",
                },
            )
            assert call_payload is not None, f"non-json response: {raw[:180]}"
            error_text = str(call_payload.get("error_message", ""))
            assert "Expected dict-compatible value, got: str" not in error_text
            assert "success" in call_payload
            if call_payload.get("success"):
                saved_path = call_payload.get("saved_path") or call_payload.get("image_path")
                if saved_path:
                    assert Path(saved_path).is_file()


@pytest.mark.asyncio
async def test_texture_save_to_file_dds_is_real_dds():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            texture_id = await _pick_texture_id(session, session_id)
            output_path = TMP_DIR / f"texture_export_{uuid.uuid4().hex[:8]}.dds"
            payload, raw = await _call_tool(
                session,
                "rd.texture.save_to_file",
                {
                    "session_id": session_id,
                    "texture_id": texture_id,
                    "output_path": str(output_path),
                    "file_format": "dds",
                },
            )
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            saved_path = payload.get("saved_path") or str(output_path)
            exported = Path(saved_path)
            assert exported.is_file()
            blob = exported.read_bytes()
            assert len(blob) > 128
            assert blob[:4] == b"DDS "


@pytest.mark.asyncio
async def test_export_texture_dds_not_png_payload():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            texture_id = await _pick_texture_id(session, session_id)
            output_path = TMP_DIR / f"export_texture_{uuid.uuid4().hex[:8]}.dds"
            payload, raw = await _call_tool(
                session,
                "rd.export.texture",
                {
                    "session_id": session_id,
                    "texture_id": texture_id,
                    "output_path": str(output_path),
                    "file_format": "dds",
                },
            )
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            saved_path = payload.get("saved_path") or str(output_path)
            exported = Path(saved_path)
            assert exported.is_file()
            head = exported.read_bytes()[:8]
            assert head[:4] == b"DDS "
            assert head != b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_resource_get_current_contents_texture_output_is_not_npz():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            texture_id = await _pick_texture_id(session, session_id)
            output_path = TMP_DIR / f"current_contents_{uuid.uuid4().hex[:8]}.raw"
            payload, raw = await _call_tool(
                session,
                "rd.resource.get_current_contents",
                {
                    "session_id": session_id,
                    "resource_id": texture_id,
                    "output_path": str(output_path),
                    "file_format": "raw",
                },
            )
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            contents = payload.get("current_contents", {})
            saved_path = contents.get("saved_path") or str(output_path)
            exported = Path(saved_path)
            assert exported.is_file()
            head = exported.read_bytes()[:8]
            assert head[:4] != b"PK\x03\x04"
            assert head != b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_resource_list_textures_exposes_fused_names():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            payload, raw = await _call_tool(session, "rd.resource.list_textures", {"session_id": session_id})
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            textures = payload.get("textures", [])
            if not textures:
                pytest.skip("capture has no textures")
            first = textures[0]
            assert "name" in first
            assert "resource_name" in first
            assert "binding_names" in first
            assert "name_stem" in first
            assert isinstance(first.get("binding_names"), list)


@pytest.mark.asyncio
async def test_export_screenshot_all_is_adaptive_and_named():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            texture_id = await _pick_texture_id(session, session_id)
            output_dir = TMP_DIR / f"screenshot_pack_{uuid.uuid4().hex[:8]}"
            payload, raw = await _call_tool(
                session,
                "rd.export.screenshot",
                {
                    "session_id": session_id,
                    "target": str(texture_id),
                    "event_id": 0,
                    "output_path": str(output_dir),
                    "file_format": "all",
                    "overlay": "none",
                },
            )
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            selected_formats = payload.get("selected_formats", [])
            assert selected_formats
            assert set(selected_formats).issubset({"png", "jpg", "exr", "hdr"})
            exports = payload.get("exports", [])
            assert exports
            for item in exports:
                saved = item.get("saved_path")
                if saved:
                    assert Path(saved).is_file()


@pytest.mark.asyncio
async def test_export_screenshot_without_target_selects_output_slot():
    params = StdioServerParameters(command="python", args=["run.py"], cwd=str(ROOT), env=_server_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            session_id, _ = await _open_replay_session(session)
            output_path = TMP_DIR / f"screenshot_no_target_{uuid.uuid4().hex[:8]}.png"
            payload, raw = await _call_tool(
                session,
                "rd.export.screenshot",
                {
                    "session_id": session_id,
                    "event_id": 0,
                    "output_path": str(output_path),
                    "file_format": "png",
                    "overlay": "none",
                },
            )
            assert payload is not None, f"non-json response: {raw[:180]}"
            assert payload.get("success") is True, payload
            if payload.get("saved_path"):
                assert Path(payload["saved_path"]).is_file()


def _try_import_renderdoc() -> Any:
    if not DEFAULT_RDX_RENDERDOC_PATH.exists():
        pytest.skip(f"missing renderdoc path: {DEFAULT_RDX_RENDERDOC_PATH}")
    if os.name == "nt":
        try:
            os.add_dll_directory(str(DEFAULT_RDX_RENDERDOC_PATH))  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            os.add_dll_directory(str(DEFAULT_RDX_RENDERDOC_PATH.parent))  # type: ignore[attr-defined]
        except Exception:
            pass
    if str(DEFAULT_RDX_RENDERDOC_PATH) not in sys.path:
        sys.path.insert(0, str(DEFAULT_RDX_RENDERDOC_PATH))
    try:
        import renderdoc as rd
    except Exception as exc:
        pytest.skip(f"renderdoc import failed: {exc}")
    return rd


def test_overlay_mapping_compatible_with_current_renderdoc():
    rd = _try_import_renderdoc()
    from rdx.core import render_service

    expected_depth = getattr(rd.DebugOverlay, "DepthTest", getattr(rd.DebugOverlay, "Depth", rd.DebugOverlay.NoOverlay))
    expected_stencil = getattr(rd.DebugOverlay, "StencilTest", getattr(rd.DebugOverlay, "Stencil", rd.DebugOverlay.NoOverlay))

    assert render_service._resolve_overlay("depth") == expected_depth
    assert render_service._resolve_overlay("stencil") == expected_stencil
    assert render_service._resolve_overlay("none") == rd.DebugOverlay.NoOverlay
    assert render_service._resolve_overlay("wireframe") is not None
    assert render_service._resolve_overlay("quad_overdraw") is not None
    assert render_service._resolve_overlay("triangle_size") is not None


@pytest.mark.asyncio
async def test_validation_errors_are_structured_not_exception_logged(caplog: pytest.LogCaptureFixture):
    import rdx.server as server

    caplog.set_level(logging.ERROR, logger="rdx.server")
    response = await server._dispatch_tool("rd.capture.list_frames", {})
    payload = json.loads(response)
    assert payload.get("success") is False
    assert "error_message" in payload
    assert "capture_file_id" in str(payload["error_message"])

    response = await server._dispatch_tool(
        "rd.util.diff_images",
        {
            "image_a_path": str(TMP_DIR / f"missing_a_{uuid.uuid4().hex[:8]}.png"),
            "image_b_path": str(TMP_DIR / f"missing_b_{uuid.uuid4().hex[:8]}.png"),
        },
    )
    payload = json.loads(response)
    assert payload.get("success") is False
    assert "error_message" in payload

    assert not any("Tool dispatch failed" in rec.getMessage() for rec in caplog.records if rec.name == "rdx.server")
