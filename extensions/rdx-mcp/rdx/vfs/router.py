"""Path router for RDX virtual filesystem."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class RouteMatch:
    kind: str  # dir | json | bin
    path: str
    params: Dict[str, Any]


_STAGE_RE = re.compile(r"^(vs|hs|ds|gs|ps|cs)$", re.IGNORECASE)


def resolve_path(path: str) -> Optional[RouteMatch]:
    p = (path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/") or "/"
    parts = [x for x in p.split("/") if x]

    if p == "/":
        return RouteMatch(kind="dir", path=p, params={})

    if p == "/capture":
        return RouteMatch(kind="dir", path=p, params={})
    if p == "/capture/current":
        return RouteMatch(kind="json", path=p, params={})

    if p == "/events":
        return RouteMatch(kind="dir", path=p, params={})
    if len(parts) == 2 and parts[0] == "events" and parts[1].isdigit():
        return RouteMatch(kind="json", path=p, params={"event_id": int(parts[1])})
    if len(parts) == 3 and parts[0] == "events" and parts[1].isdigit() and parts[2].lower() == "screenshot.png":
        return RouteMatch(kind="bin", path=p, params={"event_id": int(parts[1]), "export": "event_screenshot"})

    if p == "/pipeline":
        return RouteMatch(kind="dir", path=p, params={})
    if p == "/pipeline/current":
        return RouteMatch(kind="json", path=p, params={"detail": "full"})
    if p == "/pipeline/current/summary":
        return RouteMatch(kind="json", path=p, params={"detail": "summary"})

    if p == "/resources":
        return RouteMatch(kind="dir", path=p, params={})
    if len(parts) == 2 and parts[0] == "resources" and parts[1].isdigit():
        return RouteMatch(kind="json", path=p, params={"resource_id": parts[1]})

    if p == "/textures":
        return RouteMatch(kind="dir", path=p, params={})
    if len(parts) == 2 and parts[0] == "textures" and parts[1].isdigit():
        return RouteMatch(kind="dir", path=p, params={"texture_id": parts[1]})
    if len(parts) == 3 and parts[0] == "textures" and parts[1].isdigit() and parts[2] == "info":
        return RouteMatch(kind="json", path=p, params={"texture_id": parts[1]})
    if len(parts) == 3 and parts[0] == "textures" and parts[1].isdigit() and parts[2].lower() == "image.png":
        return RouteMatch(kind="bin", path=p, params={"texture_id": parts[1], "export": "texture_png"})

    if p == "/shaders":
        return RouteMatch(kind="dir", path=p, params={})
    if len(parts) == 2 and parts[0] == "shaders" and _STAGE_RE.match(parts[1]):
        return RouteMatch(kind="json", path=p, params={"stage": parts[1].lower()})

    return None

