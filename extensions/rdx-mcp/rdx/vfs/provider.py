"""VFS provider backed by unified core operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List

from .router import RouteMatch, resolve_path

Executor = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]


def _require_ok(payload: Dict[str, Any], *, operation: str) -> Dict[str, Any]:
    if bool(payload.get("ok")):
        data = payload.get("data")
        return dict(data) if isinstance(data, dict) else {}
    err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    message = str(err.get("message") or payload.get("error_message") or f"{operation} failed")
    raise RuntimeError(message)


def _row_with_details(base_path: str, key: str, value: str, name: str, kind: str) -> Dict[str, Any]:
    return {
        key: value,
        "name": name,
        "kind": kind,
        "details_json_path": base_path,
        "details_json_url": "",
    }


class VfsProvider:
    async def ls(self, path: str, *, session_id: str, execute: Executor) -> Dict[str, Any]:
        match = resolve_path(path)
        if match is None:
            raise RuntimeError(f"not found: {path}")
        if match.kind != "dir":
            return {"path": match.path, "kind": match.kind, "children": []}

        p = match.path
        if p == "/":
            children = [
                {"name": "capture", "kind": "dir", "details_json_path": "/capture/current", "details_json_url": ""},
                {"name": "events", "kind": "dir", "details_json_path": "/events", "details_json_url": ""},
                {"name": "pipeline", "kind": "dir", "details_json_path": "/pipeline/current", "details_json_url": ""},
                {"name": "resources", "kind": "dir", "details_json_path": "/resources", "details_json_url": ""},
                {"name": "textures", "kind": "dir", "details_json_path": "/textures", "details_json_url": ""},
                {"name": "shaders", "kind": "dir", "details_json_path": "/shaders", "details_json_url": ""},
            ]
            return {"path": p, "kind": "dir", "children": children}

        if p == "/capture":
            return {
                "path": p,
                "kind": "dir",
                "children": [{"name": "current", "kind": "json", "details_json_path": "/capture/current", "details_json_url": ""}],
            }

        if p == "/pipeline":
            return {
                "path": p,
                "kind": "dir",
                "children": [
                    {"name": "current", "kind": "json", "details_json_path": "/pipeline/current", "details_json_url": ""},
                    {"name": "current/summary", "kind": "json", "details_json_path": "/pipeline/current/summary", "details_json_url": ""},
                ],
            }

        if p == "/events":
            event_payload = await execute("rd.event.get_actions", {"session_id": session_id})
            data = _require_ok(event_payload, operation="rd.event.get_actions")
            actions = data.get("actions", [])
            children: List[Dict[str, Any]] = []
            if isinstance(actions, list):
                for item in actions:
                    if not isinstance(item, dict):
                        continue
                    event_id = item.get("event_id")
                    if event_id is None:
                        continue
                    children.append(
                        {
                            "event_id": int(event_id),
                            "name": str(item.get("name", "")),
                            "kind": "json",
                            "details_json_path": f"/events/{int(event_id)}",
                            "details_json_url": "",
                        },
                    )
            return {"path": p, "kind": "dir", "children": children}

        if p == "/resources":
            payload = await execute("rd.resource.list_all", {"session_id": session_id})
            data = _require_ok(payload, operation="rd.resource.list_all")
            resources = data.get("resources", [])
            children = []
            if isinstance(resources, list):
                for item in resources:
                    if not isinstance(item, dict):
                        continue
                    rid = str(item.get("resource_id", ""))
                    if not rid:
                        continue
                    children.append(
                        _row_with_details(
                            base_path=f"/resources/{rid}",
                            key="resource_id",
                            value=rid,
                            name=str(item.get("name", "")),
                            kind="json",
                        ),
                    )
            return {"path": p, "kind": "dir", "children": children}

        if p == "/textures":
            payload = await execute("rd.resource.list_textures", {"session_id": session_id})
            data = _require_ok(payload, operation="rd.resource.list_textures")
            textures = data.get("textures", [])
            children = []
            if isinstance(textures, list):
                for item in textures:
                    if not isinstance(item, dict):
                        continue
                    tid = str(item.get("texture_id") or item.get("resource_id") or "")
                    if not tid:
                        continue
                    children.append(
                        {
                            "texture_id": tid,
                            "name": str(item.get("name", "")),
                            "kind": "dir",
                            "details_json_path": f"/textures/{tid}/info",
                            "details_json_url": "",
                        },
                    )
            return {"path": p, "kind": "dir", "children": children}

        if p.startswith("/textures/"):
            tid = str(match.params.get("texture_id", ""))
            return {
                "path": p,
                "kind": "dir",
                "children": [
                    {"name": "info", "kind": "json", "details_json_path": f"/textures/{tid}/info", "details_json_url": ""},
                    {"name": "image.png", "kind": "bin", "details_json_path": f"/textures/{tid}/image.png", "details_json_url": ""},
                ],
            }

        if p == "/shaders":
            payload = await execute("rd.pipeline.get_state_summary", {"session_id": session_id})
            data = _require_ok(payload, operation="rd.pipeline.get_state_summary")
            summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
            shaders = summary.get("shaders", [])
            children = []
            if isinstance(shaders, list):
                for item in shaders:
                    if not isinstance(item, dict):
                        continue
                    stage = str(item.get("stage", "")).lower()
                    if not stage:
                        continue
                    children.append(
                        {
                            "stage": stage,
                            "name": str(item.get("entry_point", stage)),
                            "kind": "json",
                            "details_json_path": f"/shaders/{stage}",
                            "details_json_url": "",
                        },
                    )
            return {"path": p, "kind": "dir", "children": children}

        raise RuntimeError(f"unsupported vfs directory path: {path}")

    async def cat(self, path: str, *, session_id: str, execute: Executor) -> Dict[str, Any]:
        match = resolve_path(path)
        if match is None:
            raise RuntimeError(f"not found: {path}")
        if match.kind == "dir":
            return await self.ls(path, session_id=session_id, execute=execute)

        p = match.path
        if p == "/capture/current":
            frame = _require_ok(await execute("rd.replay.get_frame_info", {"session_id": session_id}), operation="rd.replay.get_frame_info")
            api = _require_ok(await execute("rd.replay.get_api_properties", {"session_id": session_id}), operation="rd.replay.get_api_properties")
            return {"frame_info": frame.get("frame_info", {}), "api_properties": api.get("api_properties", {})}

        if p.startswith("/events/") and "event_id" in match.params and not p.endswith("screenshot.png"):
            return _require_ok(
                await execute("rd.event.get_action_details", {"session_id": session_id, "event_id": int(match.params["event_id"])}),
                operation="rd.event.get_action_details",
            )

        if p == "/pipeline/current":
            return _require_ok(
                await execute("rd.pipeline.get_state", {"session_id": session_id}),
                operation="rd.pipeline.get_state",
            )

        if p == "/pipeline/current/summary":
            return _require_ok(
                await execute("rd.pipeline.get_state_summary", {"session_id": session_id}),
                operation="rd.pipeline.get_state_summary",
            )

        if p.startswith("/resources/"):
            rid = str(match.params.get("resource_id", ""))
            return _require_ok(
                await execute("rd.resource.get_details", {"session_id": session_id, "resource_id": rid}),
                operation="rd.resource.get_details",
            )

        if p.endswith("/info") and "texture_id" in match.params:
            rid = str(match.params.get("texture_id"))
            return _require_ok(
                await execute("rd.resource.get_details", {"session_id": session_id, "resource_id": rid}),
                operation="rd.resource.get_details",
            )

        if p.startswith("/shaders/") and "stage" in match.params:
            stage = str(match.params["stage"])
            return _require_ok(
                await execute("rd.pipeline.get_shader", {"session_id": session_id, "stage": stage}),
                operation="rd.pipeline.get_shader",
            )

        raise RuntimeError(f"unsupported vfs cat path: {path}")

    async def get(self, path: str, *, out_path: str, session_id: str, execute: Executor) -> Dict[str, Any]:
        match = resolve_path(path)
        if match is None:
            raise RuntimeError(f"not found: {path}")
        if match.kind != "bin":
            payload = await self.cat(path, session_id=session_id, execute=execute)
            out = Path(out_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(str(payload), encoding="utf-8")
            return {"saved_path": str(out), "text_fallback": True}

        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        p = match.path
        if p.endswith("screenshot.png"):
            event_id = int(match.params["event_id"])
            data = _require_ok(
                await execute(
                    "rd.export.screenshot",
                    {
                        "session_id": session_id,
                        "event_id": event_id,
                        "file_format": "png",
                        "output_path": str(out),
                    },
                ),
                operation="rd.export.screenshot",
            )
            return {"saved_path": str(data.get("saved_path") or out)}
        if p.endswith("/image.png"):
            texture_id = str(match.params["texture_id"])
            data = _require_ok(
                await execute(
                    "rd.export.texture",
                    {
                        "session_id": session_id,
                        "texture_id": texture_id,
                        "file_format": "png",
                        "output_path": str(out),
                    },
                ),
                operation="rd.export.texture",
            )
            return {"saved_path": str(data.get("saved_path") or out)}
        raise RuntimeError(f"unsupported vfs get path: {path}")
