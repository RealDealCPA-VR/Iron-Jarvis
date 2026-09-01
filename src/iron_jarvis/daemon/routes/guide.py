"""The Iron Jarvis Guide routes (v1.223.0).

The Guide itself is a chat persona (``guide``) grounded at both chat seams;
these routes are its INSPECTION surface — what it knows (``/guide/status``),
what it would retrieve for a question (``/guide/search``), and the exact
block a turn would inject (``/guide/ground``) — so the Help page can say
honestly how much reference material this install carries, and a wrong
answer can be traced to the sections it was given.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Query


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    from ...guide import index_for

    # Remember the app on the shared index so the live catalog can list every
    # route — built lazily on first use, when registration is complete.
    index_for(d.platform, app)

    @app.get("/guide/status")
    async def guide_status() -> dict[str, Any]:
        """What the Guide can draw on for THIS install: the bundled docs it
        found (and any missing), how many sections, and the live catalogs."""
        idx = index_for(d.platform, app)
        return await asyncio.to_thread(idx.status)

    @app.get("/guide/search")
    async def guide_search(
        q: str = Query("", description="the question"),
        k: int = Query(8, ge=1, le=25),
    ) -> dict[str, Any]:
        """The sections the Guide would retrieve for ``q`` — origin, heading,
        score, and a preview. Empty ``q`` returns the overview sections."""
        idx = index_for(d.platform, app)

        def _run():
            hits = idx.search(q, k=k) if q.strip() else [(0.0, s) for s in idx.overview()]
            return [
                {
                    "doc": s.doc,
                    "label": s.label,
                    "live": s.live,
                    "score": round(score, 3),
                    "preview": s.text[:240],
                }
                for score, s in hits
            ]

        return {"q": q, "hits": await asyncio.to_thread(_run)}

    @app.get("/guide/ground")
    async def guide_ground(q: str = Query("", description="the question")) -> dict[str, Any]:
        """The exact reference block a Guide turn injects for ``q``."""
        idx = index_for(d.platform, app)
        block = await asyncio.to_thread(idx.ground, q)
        return {"q": q, "block": block, "chars": len(block)}
