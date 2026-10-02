"""Standing-grant routes (v1.299.0): list what the user has said "always" to,
and take it back.

``core/grants.py`` holds the store; ``platform.grants`` owns the instance.
Two verbs, both thin — the store validates, these map:

* ``GET /grants?scope_kind=&scope_id=&live=`` → ``{grants: [...]}`` newest
  first. ``live`` (default true) hides revoked and expired rows; ``live=false``
  lists everything the table holds (the audit view). Labels are the REDACTED
  one-liners the cards showed; arguments never ride here.
* ``POST /grants/{id}/revoke`` → ``{grant}``; idempotent (revoking a revoked
  row answers the row), 404 for an unknown id. Revocation is immediate: the
  store's in-memory live view is what ``authorize`` consults, and ``revoke``
  drops the row from it before it answers.

SYNC handlers on purpose: a store call is one small SQLite read/write and
FastAPI runs sync handlers in its threadpool, so nothing here touches the
event loop (v1.153.1). The store publishes ``grant.revoked`` from the worker
thread through its bound loop (``GrantStore.bind_loop`` — the platform binds
it at start; unbound, the row still changes and only the bell is quiet).

A daemon whose platform has no store (an older build, a bare test platform)
answers 503 honestly rather than an empty list that reads as "nothing granted".
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ...core.grants import SCOPE_KINDS

_LIST_MAX = 500


def _store(d):
    store = getattr(d.platform, "grants", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="standing grants are not available on this daemon — restart it",
        )
    return store


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/grants")
    def list_grants(
        scope_kind: str = "",
        scope_id: str = "",
        live: bool = True,
    ) -> dict[str, Any]:
        kind = (scope_kind or "").strip()
        if kind and kind not in SCOPE_KINDS:
            # An unknown kind answering [] would read as "nothing granted";
            # name the vocabulary instead.
            raise HTTPException(
                status_code=400,
                detail=f"unknown scope_kind {scope_kind!r}; expected one of {SCOPE_KINDS}",
            )
        store = _store(d)
        rows = store.list(kind or None, (scope_id or "").strip() or None, live_only=bool(live))
        return {"grants": [store.as_dict(r) for r in rows[:_LIST_MAX]]}

    @app.post("/grants/{grant_id}/revoke")
    def revoke_grant(grant_id: str) -> dict[str, Any]:
        store = _store(d)
        rec = store.revoke(grant_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="grant not found")
        return {"grant": store.as_dict(rec)}
