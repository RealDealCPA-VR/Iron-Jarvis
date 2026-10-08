"""Which pages are opened (calm UI redesign, step S0).

Before this, nothing recorded which dashboard surface a person opened: the
only signal was a lifetime tally in the browser's own storage (counted only
for menu presses) — not enough to say which surfaces are used. The dashboard
now posts one ``POST /ui/visit`` per navigation, and this publishes
``ui.page_opened`` through the event bus, whose persistence handler already
writes every event to ``EventRecord``. ``GET /ui/visits`` counts them.

Privacy and shape: the route is cut to its FIRST path segment
(``/sessions/abc?x=1`` → ``/sessions``), so no id, query or file name is ever
stored; anything that is not a plain lowercase segment is refused. It stays on
this PC like every other event.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from ...core.db import session_scope
from ...core.events import EventType
from ...core.ids import utcnow
from ...core.models import EventRecord

_SEGMENT = re.compile(r"^[a-z0-9-]{1,40}$")
#: How the page was reached. "nav" = a menu, palette or tile press; "link" =
#: anything else (a link in a page, a deep link, Back/Forward, the landing).
VIA = ("nav", "link")


class UiVisitBody(BaseModel):
    route: str
    via: str = "link"


def normalize_route(raw: str) -> str | None:
    """``/sessions/abc?x=1#y`` → ``/sessions``; ``/`` → ``/``; junk → None."""
    path = (raw or "").split("?", 1)[0].split("#", 1)[0].strip()
    if not path.startswith("/"):
        return None
    first = path.strip("/").split("/", 1)[0]
    if first == "":
        return "/"
    first = first.lower()
    return f"/{first}" if _SEGMENT.match(first) else None


def register(app: FastAPI, d) -> None:
    @app.post("/ui/visit")
    async def ui_visit(body: UiVisitBody) -> dict[str, Any]:
        route = normalize_route(body.route)
        if route is None:
            raise HTTPException(status_code=422, detail="route must be a dashboard path")
        via = body.via if body.via in VIA else "link"
        bus = getattr(d.platform, "event_bus", None)
        if bus is not None:
            await bus.publish(EventType.UI_PAGE_OPENED, {"route": route, "via": via})
        return {"ok": True, "route": route}

    @app.get("/ui/visits")
    def ui_visits(days: int = 30) -> dict[str, Any]:
        """Opens per route over the last ``days`` (1–365), most opened first."""
        days = max(1, min(365, int(days)))
        since = utcnow() - timedelta(days=days)
        counts: dict[str, dict[str, int]] = {}
        with session_scope(d.platform.engine) as db:
            rows = db.exec(
                select(EventRecord.payload_json).where(
                    EventRecord.type == EventType.UI_PAGE_OPENED,
                    EventRecord.created_at >= since,
                )
            )
            for raw in rows:
                try:
                    data = json.loads(raw or "{}")
                except (TypeError, ValueError):
                    continue
                route = str(data.get("route") or "")
                if not route:
                    continue
                c = counts.setdefault(route, {"opens": 0, "nav": 0, "link": 0})
                c["opens"] += 1
                via = data.get("via") if data.get("via") in VIA else "link"
                c[via] += 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1]["opens"], kv[0]))
        return {"days": days, "routes": [{"route": r, **c} for r, c in ranked]}
