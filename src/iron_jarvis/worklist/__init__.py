"""Worklist substrate: durable per-item state for a bulk job (v1.174.0).

A department (a root session plus every subagent delegated from it) shares ONE
worklist. Surveying a folder writes the units of work down ONCE; subagents
CLAIM chunks of it; finishing an item records what it became. The point is that
none of that lives in a transcript: a run that hits its step ceiling, or a job
the user starts again tomorrow, resumes from what is actually pending instead
of from nothing.

Exports the store, the four agent tools, and :func:`register` — the HTTP read
the session page's worklist panel calls.
"""

from __future__ import annotations

from typing import Any

from .models import (
    DOING,
    DONE,
    FAILED,
    PENDING,
    STATUSES,
    WorklistItem,
    normalize_key,
)
from .store import (
    DEFAULT_CLAIM,
    DEFAULT_STALE_SECONDS,
    MAX_BOARD_ITEMS,
    MAX_CLAIM,
    MAX_ITEMS_PER_ADD,
    WorklistStore,
    fingerprint_file,
    item_view,
)
from .tools import WORKLIST_TOOL_NAMES, worklist_tools

#: Items one HTTP read returns. The panel shows counts plus the outstanding
#: work; a 5,000-item board must not ship 5,000 rows to a browser to render
#: "412 of 5,000 done".
MAX_VIEW_ITEMS = 300


def register(app, d) -> None:
    """Mount ``GET /worklist/{session_id}`` (same convention as ``routes/*``).

    Wired from ``daemon/app.py`` beside the other ``register`` calls::

        from ..worklist import register as _register_worklist
        _register_worklist(app, d)

    The response's ``board_id`` reports the board ACTUALLY served: a TeamTree
    link lands the user on a child session whose own id owns no items, so the
    store walks up to the root — and the client is never told a child id owns
    the root's list (the blackboard route's rule, for the same reason). Since
    the board follows the JOB rather than the session id, that value is also
    how two sessions can be seen to be the SAME job (a re-run and its original
    resolve to one board id), which is the property the acceptance test rests on.
    """

    @app.get("/worklist/{session_id}")
    def worklist(session_id: str) -> dict[str, Any]:
        engine = getattr(d.platform, "engine", None)
        if engine is None:  # pragma: no cover - a platform without a DB
            return {"board_id": session_id, "items": [], "summary": _empty(session_id)}
        store = getattr(d.platform, "worklist", None) or WorklistStore(
            engine, config=getattr(d.platform, "config", None)
        )
        board_id = store.root_session_for(session_id)
        summary = store.summary(board_id)
        items = store.items(board_id, limit=MAX_VIEW_ITEMS)
        return {
            "board_id": board_id,
            "summary": summary,
            "items": [item_view(row) for row in items],
            #: True when the board holds more items than one read returns, so
            #: the panel can say the list is clipped rather than imply the
            #: counts and the rows disagree.
            "clipped": summary["total"] > len(items),
        }


def _empty(board_id: str) -> dict[str, Any]:
    return {
        "board_id": board_id,
        "total": 0,
        "counts": dict.fromkeys(STATUSES, 0),
        "done": 0,
        "failed": 0,
        "pending": 0,
        "doing": 0,
        "remaining": 0,
        "complete": False,
    }


__all__ = [
    "DEFAULT_CLAIM",
    "DEFAULT_STALE_SECONDS",
    "DOING",
    "DONE",
    "FAILED",
    "MAX_BOARD_ITEMS",
    "MAX_CLAIM",
    "MAX_ITEMS_PER_ADD",
    "MAX_VIEW_ITEMS",
    "PENDING",
    "STATUSES",
    "WORKLIST_TOOL_NAMES",
    "WorklistItem",
    "WorklistStore",
    "fingerprint_file",
    "item_view",
    "normalize_key",
    "register",
    "worklist_tools",
]
