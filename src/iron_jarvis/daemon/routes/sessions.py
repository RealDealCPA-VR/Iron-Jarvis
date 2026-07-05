"""Agent session routes: lifecycle, traces, evaluation, reviews.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import json

from dataclasses import asdict
from fastapi import FastAPI, HTTPException
from typing import Any

from ..app import _agent_type, _session_view
from ..schemas import ContinueBody, FeedbackBody, SessionCreate, SessionsClearBody


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
    def list_sessions(limit: int = 200) -> dict[str, Any]:
        # Bounded window (default 200 most-recent) so the polled list stays cheap as
        # sessions accumulate over weeks; clients page for more via ?limit=.
        lim = None if limit <= 0 else limit
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
