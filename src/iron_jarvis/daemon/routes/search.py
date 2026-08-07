"""History-search routes (v1.142.0): ``GET /search/history``.

One read-only endpoint over :class:`~iron_jarvis.search.index.SearchIndex`, so
the dashboard's command palette (and any other surface) can find a past
conversation by what was SAID in it rather than by thread title.

REGISTRATION ORDER: this module registers FIRST in ``create_app`` (before every
other domain module). Nothing else in ``routes/`` claims a ``/search`` prefix
today — the only path-converter catch-alls in the tree are
``/creative/items/{name:path}`` and ``/creative/file/{name:path}``, both under
their own prefix — but registering first makes shadowing structurally
impossible rather than merely currently-true (the ``/skills/learning`` lesson:
a later-added ``GET /search/{something}`` would silently swallow this path).

DEGRADATION CONTRACT (Pair S4 depends on this): on an OLDER daemon this route
does not exist, so FastAPI's default 404 handler answers
``404 {"detail": "Not Found"}``. There is no SPA/catch-all mount and no
route-level exception handler that could turn that into a 200, so the palette's
"404 → switch the lane off" detection is sound. On THIS daemon the route
answers 200 for every value of ``q`` / ``kind`` / ``project_id`` / ``after`` /
``before`` — 6000-char pastes, NUL bytes, FTS5 syntax errors, unparseable
dates, unknown kinds and SQL-injection attempts all come back
``{"hits": [], ...}`` rather than an error, and an unavailable index degrades to
``{"hits": [], "mode": "basic", "count": 0}``. Never a 404, never a 500
(``SearchIndex.search`` never raises).

The ONE non-200: ``limit`` is a typed ``int``, so a non-numeric ``limit`` is
rejected by FastAPI's own query validation with the app-wide 422 — deliberately
NOT coerced. A caller sending ``limit=abc`` has a bug, and every other route in
this daemon says so the same way; a 422 also cannot be mistaken for the 404 the
palette keys on. Out-of-range integers ARE accepted and clamped by the index
(``-1``/``0`` → 1, ``999999`` → 200), because those are a UI slider's problem,
not a type error. Pinned by ``test_route_limit_is_typed_but_out_of_range_is_clamped``.

Closure-local state is reached through ``d`` (the create_app deps object).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/search/history")
    def search_history(
        q: str = "",
        kind: str = "",
        project_id: str = "",
        after: str = "",
        before: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Ranked hits from conversation history.

        ``q`` is required in spirit but not in status: an empty/whitespace query
        returns an empty result set rather than a 422, because the palette types
        into this endpoint character by character and a transient empty box must
        not paint an error. ``kind`` accepts one kind or a comma-separated list
        (``chat,comm``). ``after``/``before`` are inclusive ISO bounds. ``limit``
        is clamped by the index (max 200).
        """
        index = getattr(d, "search_index", None)
        if index is None:  # pragma: no cover — always built by build_platform
            return {"hits": [], "mode": "basic", "count": 0}
        kinds = [k.strip() for k in (kind or "").split(",") if k.strip()] or None
        hits = index.search(
            q or "",
            kinds=kinds,
            project_id=(project_id or "").strip() or None,
            after=(after or "").strip() or None,
            before=(before or "").strip() or None,
            limit=limit,
        )
        return {
            "hits": [h.as_dict() for h in hits],
            "mode": index.mode,
            "count": len(hits),
        }
