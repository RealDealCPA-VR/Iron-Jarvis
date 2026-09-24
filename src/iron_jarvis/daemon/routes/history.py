"""History routes (v1.290.0): the user's Claude Code / Codex sessions, read-only.

``GET /history/sessions?harness=&limit=`` lists session summaries (newest
first) and ``GET /history/sessions/{harness}/{session_id}?offset=&limit=``
returns ONE PAGE of the normalized EVENT list (``limit`` 1..2000, default 500)
as ``{session, events, events_total, offset, limit, findings,
findings_error?}`` — ``findings`` is the detections scan over ALL the
session's events, cached per (session, signature) so paging never rescans.
At most two session loads run at once. Token-guarded by the app's auth middleware like
every other route. Every file read (and the scan) runs in ``asyncio.to_thread``:
a listing touches hundreds of files and a session can be tens of MB, and the
daemon is ONE event loop.

The session id is matched against the LISTED sessions (``history.find_session``),
never joined into a path. ``iron_jarvis.detections`` is imported lazily inside
the handler, so this module never depends on it being importable.
"""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from typing import Any

from fastapi import FastAPI, HTTPException, Query

#: At most this many session loads (parse + scan) run at once; a third waits
#: in its worker thread — never on the event loop. A heavy session is tens of
#: MB of events in memory while it is paged.
_LOAD_SLOTS = threading.BoundedSemaphore(2)
#: Findings are a scan over ALL of a session's events; paging must not rescan.
#: Keyed by (harness, id, session signature); the few most recent are kept.
_FINDINGS_MAX = 8
_findings_cache: OrderedDict[tuple, list[dict]] = OrderedDict()
_findings_lock = threading.Lock()


def _findings(events: list[dict]) -> tuple[list[dict], str | None]:
    """Detections over ``events``; ``([], reason)`` when the engine is unavailable."""
    try:
        from ...detections import scan
    except Exception as exc:  # noqa: BLE001 - a missing/broken engine is not a 500
        return [], f"detections unavailable: {type(exc).__name__}"
    try:
        return [f.to_dict() for f in scan(events)], None
    except Exception as exc:  # noqa: BLE001 - one bad rule must not hide the session
        return [], f"detections failed: {type(exc).__name__}: {exc}"


def _cached_findings(key: tuple, events: list[dict]) -> tuple[list[dict], str | None]:
    """``_findings`` once per key; a failed scan is not cached (retry next time)."""
    with _findings_lock:
        hit = _findings_cache.get(key)
        if hit is not None:
            _findings_cache.move_to_end(key)
            return hit, None
    findings, error = _findings(events)
    if error is None:
        with _findings_lock:
            _findings_cache[key] = findings
            while len(_findings_cache) > _FINDINGS_MAX:
                _findings_cache.popitem(last=False)
    return findings, error


def session_page(
    harness: str, session_id: str, offset: int, limit: int
) -> dict[str, Any]:
    """One page of a session's events + the findings over ALL of them.

    Blocking (file reads, the scan): call it off the event loop. Raises
    ValueError (bad harness) / SessionNotFound.
    """
    from ... import history
    from ...history import index

    with _LOAD_SLOTS:
        found = index.find_session(harness, session_id)
        if found is None:
            raise history.SessionNotFound(f"no {harness} session {session_id!r}")
        signature = index.session_signature(harness, found)
        summary, events = history.load_session(harness, session_id)
        findings, error = _cached_findings((harness, summary["id"], signature), events)
    out: dict[str, Any] = {
        "session": summary,
        "events": events[offset : offset + limit],
        "events_total": len(events),
        "offset": offset,
        "limit": limit,
        "findings": findings,
    }
    if error:
        out["findings_error"] = error
    return out


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object (unused)."""

    @app.get("/history/sessions")
    async def history_sessions(
        harness: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """Claude Code + Codex session summaries, newest first."""
        from ...history import list_sessions

        try:
            sessions = await asyncio.to_thread(list_sessions, harness or None, limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"sessions": sessions, "count": len(sessions)}

    @app.get("/history/sessions/{harness}/{session_id}")
    async def history_session(
        harness: str,
        session_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(500, ge=1, le=2000),
    ) -> dict[str, Any]:
        """One session, paged: ``{session, events, events_total, offset, limit,
        findings, findings_error?}`` — ``findings`` cover ALL its events."""
        from ...history import SessionNotFound

        try:
            return await asyncio.to_thread(
                session_page, harness, session_id, offset, limit
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SessionNotFound as exc:
            raise HTTPException(status_code=404, detail="no such session") from exc
