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


def _waiting_on(d, session_id: str) -> dict[str, Any] | None:
    """``{approval_id, tool}`` for the OLDEST ask this session is paused on,
    or None (v1.227.0, audit A4). Derived from the shared approvals registry
    — the one place a live pause exists — via ``pending_for``, which the
    approvals lane adds this release; until it lands (or on a bare platform)
    the guard answers None rather than guessing. Read-only, in-memory: safe
    on the loop."""
    approvals = getattr(getattr(d, "platform", None), "approvals", None)
    pending_for = getattr(approvals, "pending_for", None)
    if pending_for is None:
        return None
    try:
        rows = list(pending_for(session_id) or [])
    except Exception:  # noqa: BLE001 — a listing must never break a row
        return None
    if not rows:
        return None
    first = rows[0] if isinstance(rows[0], dict) else {}
    approval_id = first.get("approval_id")
    if not approval_id:
        return None
    return {"approval_id": str(approval_id), "tool": str(first.get("tool") or "")}


def _session_row(d, session) -> dict[str, Any]:
    """``_session_view`` plus the two truth fields every row carries since
    v1.227.0 — ADDITIVE, the base shape is untouched:

    * ``outcome`` — ``completed`` | ``completed_with_failures`` | ``needs_you``
      | None, the ledger's verdict on the JOB (``Session.outcome``);
    * ``waiting_on`` — ``{approval_id, tool}`` while the run is paused on an
      ask, else None — so the kanban and the session page can show a paused
      run as waiting for the user instead of as ordinary running work.
    """
    return _session_view(session, d)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.post("/sessions")
    async def create_session(body: SessionCreate) -> dict[str, Any]:
        # THE FOLDER RIDES THE ESCALATION (v1.189.0). Validated with the SAME
        # tests the chat lane applies to its workspace — and unlike chat's
        # silent fallback, an EXPLICIT folder that fails them is an honest 400:
        # the caller named a folder on purpose, and running the job in a
        # scratch dir instead would reproduce the exact failure this field
        # exists to close (every write refused as outside-workspace, in a
        # workspace the user never chose).
        workspace_root = (body.workspace_root or "").strip() or None
        if workspace_root:
            from ...core.fs_policy import usable_workspace_root

            if not usable_workspace_root(workspace_root):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "workspace_root must be an existing, absolute, "
                        "non-protected folder this app may write in"
                    ),
                )
        try:
            session = await d.orchestrator.create_session(
                body.task,
                _agent_type(body.agent_type),
                body.provider,
                model=body.model,
                self_dev=body.self_dev,
                project_id=body.project_id or None,
                allow_tools=body.allow_tools or None,
                workspace_root=workspace_root,
                origin=body.origin,
                # Contract 4 (v1.174.0): the caller's per-session step budget.
                # Already range-validated by SessionCreate (a 422 outside
                # 1..200); None = the configured default.
                max_steps=body.max_steps,
            )
        except (PermissionError, RuntimeError) as exc:  # self-dev gating
            raise HTTPException(status_code=400, detail=str(exc))
        if body.wait:
            session = await d.orchestrator.run_session(session.id)
        else:
            # A parked spawn returns None (v1.167.0): the governor marked the
            # row QUEUED, so re-read it — serializing the stale in-memory
            # object here claimed "active" for work that never started.
            if d._spawn_bg(session.id, d.orchestrator.run_session(session.id)) is None:
                session = d.orchestrator.get_session(session.id) or session
        return _session_row(d, session)

    @app.post("/sessions/{session_id}/cancel")
    def cancel_session(session_id: str) -> dict[str, Any]:
        try:
            session = d.orchestrator.cancel_session(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return _session_row(d, session)

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
            # A parked spawn returns None (v1.167.0): the governor marked the
            # row QUEUED, so re-read it — serializing the stale in-memory
            # object here claimed "active" for work that never started.
            if d._spawn_bg(session.id, d.orchestrator.run_session(session.id)) is None:
                session = d.orchestrator.get_session(session.id) or session
        return _session_row(d, session)

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
            # A parked spawn returns None (v1.167.0): the governor marked the
            # row QUEUED, so re-read it — serializing the stale in-memory
            # object here claimed "active" for work that never started.
            if d._spawn_bg(session.id, d.orchestrator.run_session(session.id)) is None:
                session = d.orchestrator.get_session(session.id) or session
        return _session_row(d, session)

    @app.post("/sessions/{session_id}/worklist/reset-failed")
    async def reset_failed_worklist_items(session_id: str) -> dict[str, Any]:
        """Re-open this session's FAILED worklist items (v1.227.0).

        Flips every ``failed`` row on the session's board back to ``todo``
        (``pending``) with its claim cleared and answers ``{reset: N,
        board_id}``; the dashboard then posts the existing
        ``/sessions/{id}/continue`` so a follow-up run claims exactly those
        rows through ``worklist_next``. Nothing ``done`` is touched. 404 when
        the session is unknown OR has no worklist — an empty board is not a
        board, and "reset 0" over nothing would read as success. The board is
        resolved the way ``GET /worklist/{id}`` resolves it (root session ->
        job), so the panel and this door always agree on which list. Store
        calls hop off the loop like the worklist tools do.
        """
        if d.orchestrator.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        store = getattr(d.platform, "worklist", None)
        if store is None:  # pragma: no cover - a platform without the store
            raise HTTPException(status_code=404, detail="this session has no worklist")
        board_id = await asyncio.to_thread(store.root_session_for, session_id)
        if await asyncio.to_thread(store.count, board_id) == 0:
            raise HTTPException(status_code=404, detail="this session has no worklist")
        reset = await asyncio.to_thread(store.reset_failed, board_id)
        return {"reset": int(reset), "board_id": board_id}

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
        view = _session_row(d, session)
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
            return {"sessions": [_session_row(d, s) for s in scoped]}
        return {"sessions": [_session_row(d, s) for s in d.orchestrator.list_sessions(limit=lim)]}

    @app.get("/sessions/teams")
    def sessions_teams() -> dict[str, Any]:
        """Child-session → parent-session map for the whole board (v1.168.0).

        ``{"parents": {child_session_id: parent_session_id, ...}}`` derived in
        ONE query pass from ``AgentRun.parent_id`` links — the honest record,
        never the model's narrative (same derivation as ``/sessions/{id}/team``
        but flattened board-wide, so the Kanban can nest team members under
        their parent's card without probing per session). Rules:

        * a child run's ``parent_id`` names a RUN; the mapping resolves it to
          that run's owning session — a dangling parent run id maps nowhere;
        * blank session ids are skipped (a run that outlived its session);
        * a link between two runs of the SAME session (continuations) is not a
          team edge — no self-mapping;
        * when a session's runs disagree, the EARLIEST recorded link wins
          (link rows are walked in ``created_at`` order), so the map is stable.

        Registered BEFORE ``GET /sessions/{session_id}`` on purpose: FastAPI
        matches in registration order, so moving this below that route would
        turn every call into a 404 ("session not found" for id "teams").

        BOUNDED on purpose (v1.168.0 review finding): AgentRun is unbounded
        run history and every mounted board polls this every 8s, so a bare
        SELECT over the whole table grows forever. Two index-backed passes
        instead: link rows only (``parent_id IS NOT NULL``), then an ``IN()``
        lookup resolving just the referenced parent run ids — solo runs (the
        vast majority) are never read at all.
        """
        from sqlmodel import select

        from ...core.db import session_scope
        from ...core.models import AgentRun

        with session_scope(d.platform.engine) as db:
            linked = list(
                db.exec(
                    select(
                        AgentRun.id,
                        AgentRun.session_id,
                        AgentRun.parent_id,
                    )
                    .where(AgentRun.parent_id.is_not(None))  # type: ignore[union-attr]
                    .order_by(AgentRun.created_at)  # type: ignore[arg-type, attr-defined]
                )
            )
            # Resolve only the run ids the links actually name. Chunked so a
            # pathological history can't overflow SQLite's variable limit.
            wanted = sorted({parent for _rid, _sid, parent in linked if parent})
            run_session: dict[str, str] = {}
            for i in range(0, len(wanted), 500):
                chunk = wanted[i : i + 500]
                for rid, sid in db.exec(
                    select(AgentRun.id, AgentRun.session_id).where(
                        AgentRun.id.in_(chunk)  # type: ignore[attr-defined]
                    )
                ):
                    if sid:
                        run_session[rid] = sid
        parents: dict[str, str] = {}
        for _rid, sid, parent in linked:
            if not sid or not parent:
                continue
            parent_sid = run_session.get(parent)
            if not parent_sid or parent_sid == sid:
                continue
            parents.setdefault(sid, parent_sid)
        return {"parents": parents}

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = d.orchestrator.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session": _session_row(d, session),
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
                        {**_session_row(d, child), "parent_run_id": r.parent_id}
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
