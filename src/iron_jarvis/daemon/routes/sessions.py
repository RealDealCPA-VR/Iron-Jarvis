"""Agent session routes: lifecycle, traces, evaluation, reviews.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json

from dataclasses import asdict
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Any

from ..app import _agent_type, _session_view
from ..schemas import ContinueBody, FeedbackBody, SessionCreate, SessionsClearBody


def _sse(event: str, data: dict[str, Any]) -> str:
    """Serialize one Server-Sent Event frame (FX-01 wire format)."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.post("/sessions")
    async def create_session(body: SessionCreate) -> dict[str, Any]:
        try:
            session = await d.orchestrator.create_session(
                body.task,
                _agent_type(body.agent_type),
                body.provider,
                model=body.model,
                self_dev=body.self_dev,
                project_id=body.project_id or None,
                allow_tools=body.allow_tools or None,
                origin=body.origin,
            )
        except (PermissionError, RuntimeError) as exc:  # self-dev gating
            raise HTTPException(status_code=400, detail=str(exc))
        if body.wait:
            session = await d.orchestrator.run_session(session.id)
        else:
            d._spawn_bg(session.id, d.orchestrator.run_session(session.id))
        return _session_view(session)

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: str) -> dict[str, Any]:
        try:
            session = d.orchestrator.cancel_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _session_view(session)

    @app.post("/sessions/{session_id}/rerun")
    async def rerun_session(session_id: str, wait: bool = True) -> dict[str, Any]:
        try:
            session = await d.orchestrator.rerun_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except (PermissionError, RuntimeError) as exc:  # self-dev gating on a maintainer rerun
            raise HTTPException(status_code=400, detail=str(exc))
        if wait:
            session = await d.orchestrator.run_session(session.id)
        else:
            d._spawn_bg(session.id, d.orchestrator.run_session(session.id))
        return _session_view(session)

    @app.post("/sessions/{session_id}/continue")
    async def continue_session(session_id: str, body: ContinueBody) -> dict[str, Any]:
        try:
            session = await d.orchestrator.continue_session(session_id, body.message)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:  # workspace busy — a continuation is running
            raise HTTPException(status_code=409, detail=str(exc))
        if body.wait:
            session = await d.orchestrator.run_session(session.id)
        else:
            d._spawn_bg(session.id, d.orchestrator.run_session(session.id))
        return _session_view(session)

    @app.post("/sessions/clear")
    def clear_sessions(body: SessionsClearBody) -> dict[str, Any]:
        """Bulk-clear FINISHED sessions by status (completed/failed/cancelled) —
        the Kanban 'clear completed' / 'dismiss failed' action. Active sessions
        are never touched; per-session failures are skipped, not fatal."""
        wanted = {s.lower() for s in (body.statuses or [])} - {"active"}
        if not wanted:
            raise HTTPException(status_code=400, detail="no clearable statuses given")
        cleared = 0
        for view in d.orchestrator.list_sessions(limit=1000):
            status = view.status.value if hasattr(view.status, "value") else str(view.status)
            if status.lower() not in wanted:
                continue
            try:
                d.orchestrator.delete_session(view.id)
                cleared += 1
            except Exception:  # noqa: BLE001 — skip stragglers (e.g. review-locked)
                continue
        return {"cleared": cleared, "statuses": sorted(wanted)}

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        try:
            d.orchestrator.delete_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"deleted": session_id}

    @app.get("/sessions/{session_id}/export")
    def export_session(session_id: str, format: str = "md"):
        session = d.orchestrator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        transcript = d.orchestrator.transcript(session_id)
        try:
            ev = d.platform.evaluator.latest(session_id)
        except Exception:  # noqa: BLE001
            ev = None
        view = _session_view(session)
        if format == "json":
            return {
                "session": view,
                "transcript": transcript,
                "evaluation": ev.model_dump() if ev is not None else None,
            }
        from fastapi.responses import PlainTextResponse

        lines = [
            f"# Iron Jarvis session — {session.task}",
            "",
            f"- id: {session.id}",
            f"- status: {session.status.value}",
            f"- provider/model: {session.provider} / {session.model}",
            f"- created: {session.created_at}",
            f"- finished: {session.finished_at}",
            "",
            "## Summary",
            session.summary or "(none)",
            "",
            "## Tool calls",
        ]
        for t in transcript.get("tools", []):
            lines.append(
                f"- `{t.get('tool', '')}` ({t.get('verdict', '')}) ok={t.get('ok')}: "
                f"{(t.get('output') or '')[:200]}"
            )
        if ev is not None:
            lines += [
                "",
                "## Evaluation",
                "```json",
                json.dumps(ev.model_dump(), indent=2, default=str),
                "```",
            ]
        return PlainTextResponse("\n".join(lines), media_type="text/markdown")

    @app.get("/sessions")
    def list_sessions(limit: int = 200, project_id: str = "") -> dict[str, Any]:
        # Bounded window (default 200 most-recent) so the polled list stays cheap as
        # sessions accumulate over weeks; clients page for more via ?limit=.
        lim = None if limit <= 0 else limit
        pid = (project_id or "").strip()
        if pid:
            # Scope the query to ONE project at the DB level so the global 200-row
            # recency window can't hide older sessions of a quieter project
            # (Session.project_id is indexed). Same {"sessions": [...]} shape and
            # same _session_view rows as the unfiltered list.
            from sqlmodel import select

            from ...core.db import session_scope
            from ...core.models import Session as SessionModel

            with session_scope(d.platform.engine) as db:
                stmt = (
                    select(SessionModel)
                    .where(SessionModel.project_id == pid)
                    .order_by(SessionModel.created_at.desc())  # type: ignore[attr-defined]
                )
                if lim is not None:
                    stmt = stmt.limit(lim)
                scoped = list(db.exec(stmt))
            return {"sessions": [_session_view(s) for s in scoped]}
        return {"sessions": [_session_view(s) for s in d.orchestrator.list_sessions(limit=lim)]}

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = d.orchestrator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session": _session_view(session),
            "transcript": d.orchestrator.transcript(session_id),
        }

    @app.get("/sessions/{session_id}/result")
    def get_session_result(session_id: str) -> dict[str, Any]:
        """What this session ACTUALLY did — derived from the tool ledger and the
        undo journal, never from the model's closing paragraph (v1.149.0).

        See ``agents/outcome.py``: files created/changed come from journaled
        mutations, so a reply that claims a file it never wrote disagrees with
        this endpoint, and the disagreement is the point.
        """
        from ...agents.outcome import session_result

        result = session_result(d.platform.engine, session_id)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail="session not found")
        return result

    @app.get("/sessions/{session_id}/team")
    def session_team(session_id: str) -> dict[str, Any]:
        """The delegation tree under one session (v1.166.0).

        Derivation is the honest record, never the model's narrative: this
        session's AgentRun ids -> child AgentRuns whose ``parent_id`` is one of
        them -> those runs' sessions, recursed to depth 3 (the delegation cap,
        which also makes a corrupt parent_id cycle harmless). ``children`` rows
        are ``_session_view`` shapes plus ``parent_run_id``; ``runs`` carries
        the parent's AND every discovered child's runs. Read-only. An unknown
        id is ``found: false`` + empty lists (200) so the polling session page
        never turns a just-deleted session into an error toast.
        """
        from sqlmodel import select

        from ...core.db import session_scope
        from ...core.models import AgentRun, Session as SessionModel

        with session_scope(d.platform.engine) as db:
            if db.get(SessionModel, session_id) is None:
                return {
                    "found": False,
                    "session_id": session_id,
                    "children": [],
                    "runs": [],
                }

            runs_out: list[dict[str, Any]] = []
            seen_runs: set[str] = set()
            seen_sessions: set[str] = {session_id}
            children: list[dict[str, Any]] = []

            def _run_row(r: AgentRun) -> dict[str, Any]:
                return {
                    "id": r.id,
                    "session_id": r.session_id,
                    "parent_id": r.parent_id,
                    "agent_type": r.agent_type.value,
                    "state": r.state.value,
                }

            def _record_run(r: AgentRun) -> None:
                """Append this run's row to ``runs`` exactly once."""
                if r.id not in seen_runs:
                    seen_runs.add(r.id)
                    runs_out.append(_run_row(r))

            def _collect_runs(session_ids: list[str]) -> list[AgentRun]:
                """Record (deduped) run rows for these sessions; return ALL of
                them so their ids become the next parent frontier — a row
                already recorded via its parent_id link still parents the next
                depth (the sessions themselves are fresh, so no re-walk)."""
                if not session_ids:
                    return []
                rows = list(
                    db.exec(
                        select(AgentRun).where(
                            AgentRun.session_id.in_(session_ids)  # type: ignore[attr-defined]
                        )
                    )
                )
                for r in rows:
                    _record_run(r)
                return rows

            frontier = _collect_runs([session_id])
            for _depth in range(3):  # the delegation cap
                parent_ids = [r.id for r in frontier]
                if not parent_ids:
                    break
                linked = list(
                    db.exec(
                        select(AgentRun).where(
                            AgentRun.parent_id.in_(parent_ids)  # type: ignore[attr-defined, union-attr]
                        )
                    )
                )
                next_session_ids: list[str] = []
                for r in linked:
                    # The linked run ALWAYS lands in ``runs`` — it was
                    # discovered via parent_id, and this endpoint claims
                    # ``runs`` carries every discovered child's runs. Before
                    # this (v1.166.0 fix) a run whose Session row was deleted,
                    # blank, or already seen vanished from the honest record
                    # without trace.
                    _record_run(r)
                    sid = r.session_id
                    if not sid or sid in seen_sessions:
                        continue
                    seen_sessions.add(sid)
                    child = db.get(SessionModel, sid)
                    if child is None:  # run outlived its session — no child row
                        continue
                    children.append(
                        {**_session_view(child), "parent_run_id": r.parent_id}
                    )
                    next_session_ids.append(sid)
                frontier = _collect_runs(next_session_ids)

        return {
            "found": True,
            "session_id": session_id,
            "children": children,
            "runs": runs_out,
        }

    @app.get("/sessions/{session_id}/stream")
    async def stream_session(session_id: str, request: Request):
        """Live SSE feed for a running session (FX-01).

        Subscribes to the platform stream hub and forwards the run's EPHEMERAL
        frames (token deltas, tool-call starts/finishes, rounds) to one browser as
        the perceive->act loop produces them — never persisted, keyed by
        session_id (see core/streams.py). Emits a ``: keepalive`` comment every
        ~15s of idle so a proxy doesn't drop the connection, and closes once the
        run's terminal ``done`` frame is forwarded. EventSource can't set headers,
        so this GET authenticates via the ``?token=`` query param (already handled
        by the daemon's auth middleware — no middleware change)."""
        hub = getattr(d.platform, "streams", None)
        if hub is None:  # bare-platform / misconfigured — nothing to stream
            raise HTTPException(status_code=503, detail="streaming not available")
        q = hub.subscribe(session_id)

        async def gen():
            try:
                while not await request.is_disconnected():
                    try:
                        frame = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(frame["event"], frame["data"])
                    if frame["event"] == "done":
                        break
            finally:
                hub.unsubscribe(session_id, q)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sessions/{session_id}/traces")
    def traces(session_id: str) -> dict[str, Any]:
        return {"traces": d.platform.observability.traces(session_id)}

    @app.get("/sessions/{session_id}/evaluation")
    def evaluation(session_id: str) -> dict[str, Any]:
        ev = d.platform.evaluator.latest(session_id)
        if ev is None:
            try:
                ev = d.platform.evaluator.evaluate(session_id)
            except Exception:
                ev = None
        if ev is None:
            raise HTTPException(status_code=404, detail="no evaluation")
        return ev.model_dump()

    @app.post("/sessions/{session_id}/feedback")
    def session_feedback(session_id: str, body: FeedbackBody) -> dict[str, Any]:
        fb = d.platform.learning.record_feedback(session_id, body.rating, body.comment)
        return {"id": fb.id, "rating": fb.rating}

    @app.get("/reviews")
    def list_reviews() -> dict[str, Any]:
        """All PENDING reviews in one call — so the Kanban board can place cards
        in the In-Review lane without probing /sessions/{id}/review per session."""
        return {
            "reviews": [
                {"session_id": sid, **asdict(rv)}
                for sid, rv in d.orchestrator.pending_reviews().items()
            ]
        }

    @app.get("/sessions/{session_id}/review")
    def get_review(session_id: str) -> dict[str, Any]:
        review = d.orchestrator.get_review(session_id)
        if review is None:
            raise HTTPException(status_code=404, detail="no review for session")
        return asdict(review)

    @app.post("/reviews/{session_id}/approve")
    def approve_review(session_id: str) -> dict[str, Any]:
        if d.orchestrator.get_review(session_id) is None:
            raise HTTPException(status_code=404, detail="no review for session")
        return {"merged": d.orchestrator.approve_review(session_id)}

    @app.post("/reviews/{session_id}/reject")
    def reject_review(session_id: str) -> dict[str, Any]:
        if d.orchestrator.get_review(session_id) is None:
            raise HTTPException(status_code=404, detail="no review for session")
        d.orchestrator.reject_review(session_id)
        return {"status": "rejected"}
