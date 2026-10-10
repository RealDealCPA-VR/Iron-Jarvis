"""Agent session routes: lifecycle, traces, evaluation, reviews.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re

from dataclasses import asdict
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from typing import Any

from ..app import _agent_type, _session_view
from ..schemas import (
    ContinueBody,
    FeedbackBody,
    MissionCreate,
    SessionCreate,
    SessionsClearBody,
)

#: The origin the mission door stamps (v1.307.0). Starts with "job", so it is
#: an ATTENDED origin (runtime.ATTENDED_ORIGINS): its asks wait for the user.
from ...agents.team import MISSION_ORIGIN  # noqa: E402  (one definition)

#: How many recent missions ``GET /missions`` lists.
_MISSIONS_LIMIT = 30


def _sse(event: str, data: dict[str, Any]) -> str:
    """Serialize one Server-Sent Event frame (FX-01 wire format)."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _clear_interrupted(d, session_id: str) -> bool:
    """Drop the interrupted tag from one session (v1.249.0, R-02).

    BLOCKING (a DB write) — every caller hops off the loop. Returns False
    only when the session does not exist; clearing an already-clear row is a
    no-op success, so a double click reads as "already handled".
    """
    from ...core.db import session_scope
    from ...core.models import Session

    with session_scope(d.platform.engine) as db:
        row = db.get(Session, session_id)
        if row is None:
            return False
        if row.interrupted_at is not None:
            row.interrupted_at = None
            db.add(row)
            db.commit()
    return True


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
    out: dict[str, Any] = {
        "approval_id": str(approval_id),
        "tool": str(first.get("tool") or ""),
    }
    # v1.247.0: a batched ask says how many calls one answer covers — a
    # number, never the calls' arguments (this row rides every listing).
    _count = first.get("count")
    if isinstance(_count, int) and not isinstance(_count, bool) and _count > 1:
        out["count"] = _count
    return out


def _project_team(d, project_id: str) -> list[str]:
    """A project's saved team (roster names), or ``[]`` for none / no such
    project (v1.308.0). BLOCKING (one read) — callers hop off the loop."""
    from ...core.db import session_scope
    from ...core.models import Project
    from ...projects.world import decode_team

    try:
        with session_scope(d.platform.engine) as db:
            row = db.get(Project, project_id)
            return decode_team(getattr(row, "team_json", "")) if row is not None else []
    except Exception:  # noqa: BLE001 — no team readable = no restriction
        return []


def _stamp_continuation(
    d,
    new_id: str,
    parent_id: str,
    objective: str | None,
    *,
    resume: bool = False,
    retry_failed: int = 0,
) -> str:
    """Link a MISSION's continuation to the mission it continues (v1.309.0,
    contract 5). A no-op unless the parent's origin is ``job:mission``: only
    the mission view reads the link, and every other continuation keeps its
    run options byte-identical (``test_schedule_knobs_v1299`` pins that a
    continue carries exactly the parent's options).

    Writes ``options["continued_from"] = parent_id`` on the NEW row — always
    overwriting, because run options carry over (``_stored_options``), so a
    continuation of a continuation would otherwise point at its grandparent.
    ``mission.continued_as`` reads it back to say "Continued in → open".

    ``objective`` (the display text, contract 3) is written only when the row
    does not already carry an objective of its OWN: Track A's
    ``continue_session`` stores the user's follow-up there, and this keeps
    the same words whichever side lands first. A row whose ``objective`` is
    merely the PARENT's (copied with the options) gets the new one.

    ``resume`` (v1.309.0 review): the message is the APP's instruction ("pick
    up where you left off" after a restart), not the user's follow-up — so
    the parent's display objective is kept, overwriting the message Track A's
    ``continue_session`` stored. The resumed round is listed by what the user
    asked for, never by "Continue where you left off — Iron Jarvis
    restarted…".

    ``retry_failed`` (v1.310.0): the retry-failed door's count. The rerun keeps
    the task byte-identical (it is the worklist board's key), so nothing told
    the MODEL that only the failed items remain; this stamps
    ``options.retry_failed = {"session": new_id, "count": N}`` and the runtime
    turns it into one model-facing sentence (``runtime.mission_retry_note``).
    Bound to the NEW row's id, so a later continuation that inherits the
    options never repeats it.

    BLOCKING (one read + one write) — callers hop off the loop, and they do
    it BEFORE the run is spawned, so the runtime's own fresh read of the row
    (``run_session`` loads it by id) already carries the stamp. Returns the
    new ``options_json`` ("" when nothing was written)."""
    from ...agents.mission import session_options
    from ...core.db import session_scope
    from ...core.models import Session

    with session_scope(d.platform.engine) as db:
        row = db.get(Session, new_id)
        parent = db.get(Session, parent_id)
        if row is None or parent is None or getattr(parent, "origin", None) != MISSION_ORIGIN:
            return ""
        opts = session_options(row)
        opts["continued_from"] = parent_id
        if retry_failed > 0:
            opts["retry_failed"] = {"session": new_id, "count": int(retry_failed)}
        else:
            opts.pop("retry_failed", None)  # a retry's options carried over
        if resume:
            from ...agents.mission import display_objective

            kept = display_objective(parent).strip()
            if kept:
                opts["objective"] = kept
        elif objective:
            inherited = session_options(parent).get("objective")
            mine = opts.get("objective")
            if not (isinstance(mine, str) and mine.strip()) or mine == inherited:
                opts["objective"] = objective
        row.options_json = json.dumps(opts, default=str)
        db.add(row)
        db.commit()
        return row.options_json


def _managed_reuse_path(d, prev) -> str:
    """The folder a mission RETRY must reuse that ``rerun_session`` would not
    (v1.309.0 review), or "" when the rerun already lands in the right place.

    ``rerun_session`` keeps a project / user-folder mission in its folder,
    but gives a mission that ran in a managed SCRATCH workspace a fresh,
    empty one — so a retry could not see a single file the first run made.
    That is the folder this returns, under ``continue_session``'s own rule:
    never a git worktree (a review/reject can discard it out from under the
    run), never a folder that is gone. BLOCKING (a stat)."""
    from pathlib import Path

    from ...agents.runtime import is_direct_workspace

    ws = str(getattr(prev, "workspace_path", "") or "")
    if not ws or is_direct_workspace(d.platform.config, ws):
        return ""
    if getattr(d.orchestrator, "_git_sessions", {}).get(prev.id) is not None:
        return ""
    try:
        return ws if Path(ws).is_dir() else ""
    except OSError:
        return ""


def _folder_busy(d, workspace: str) -> bool:
    """A run is running or queued in ``workspace`` (``continue_session``'s
    busy rule: QUEUED counts — a parked run still owns the folder).
    BLOCKING."""
    from sqlmodel import select

    from ...core.db import session_scope
    from ...core.models import Session, SessionStatus

    with session_scope(d.platform.engine) as db:
        return db.exec(
            select(Session.id).where(
                Session.workspace_path == workspace,
                Session.status.in_(  # type: ignore[attr-defined]
                    (SessionStatus.ACTIVE, SessionStatus.QUEUED)
                ),
            )
        ).first() is not None


def _move_into(d, new_id: str, workspace: str) -> None:
    """Point a just-created (never started) run at ``workspace`` and drop the
    empty scratch folder ``create_session`` made for it. Done before the run
    is spawned, so ``run_session``'s fresh read of the row works there.
    BLOCKING."""
    from pathlib import Path

    from ...core.db import session_scope
    from ...core.models import Session

    with session_scope(d.platform.engine) as db:
        row = db.get(Session, new_id)
        if row is None:
            raise KeyError(new_id)
        fresh = str(row.workspace_path or "")
        row.workspace_path = workspace
        db.add(row)
        db.commit()
    if fresh and fresh != workspace:
        try:
            Path(fresh).rmdir()  # only ever empty: nothing has run in it
        except OSError:
            pass


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    """RFC 7232 weak comparison of an ``If-None-Match`` header against *etag*
    (``*`` matches anything; a ``W/`` prefix on either side is ignored)."""
    if not if_none_match:
        return False
    strip = lambda t: t[2:] if t.startswith("W/") else t  # noqa: E731
    for tag in (t.strip() for t in if_none_match.split(",")):
        if tag == "*" or strip(tag) == strip(etag):
            return True
    return False


def _etagged_json(body: Any, request: Request) -> Response:
    """Serialise *body* exactly as FastAPI's JSONResponse would and answer
    it with a weak ETag — or a bodiless 304 when the caller's ``If-None-Match``
    already names this content (v1.230.0, audit FP3).

    The tag is a hash of the SERIALISED body, so it is the same across daemon
    restarts and changes whenever any row does (status, outcome, waiting_on,
    a new session). The Overview polls ``/sessions`` every 5 s and the list
    rarely changes between ticks; a 304 costs no body (the default 200-row
    listing was ~60 KB per tick, ~1 GB/day). ``Cache-Control: no-store`` stays
    on every response — the dashboard sends the tag itself; the browser cache
    is never involved.
    """
    raw = json.dumps(
        jsonable_encoder(body),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    etag = 'W/"%s"' % hashlib.sha1(raw).hexdigest()
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers={"ETag": etag})
    return Response(content=raw, media_type="application/json", headers={"ETag": etag})


def _session_row(d, session) -> dict[str, Any]:
    """``_session_view`` plus the two truth fields every row carries since
    v1.227.0 — ADDITIVE, the base shape is untouched:

    * ``outcome`` — ``completed`` | ``completed_with_failures`` | ``needs_you``
      | None, the ledger's verdict on the JOB (``Session.outcome``);
    * ``waiting_on`` — ``{approval_id, tool}`` while the run is paused on an
      ask, else None — so the kanban and the session page can show a paused
      run as waiting for the user instead of as ordinary running work.

    v1.298.0: ``trust`` (``"full"`` | ``"low"``), ``trust_reason`` (one
    sentence or ``""``) and ``tainted_at`` (ISO or None) — the session's
    TRUST posture — ride ``_session_view`` itself (``app.py``), so EVERY
    route that serves a row carries them; nothing is added here (one
    renderer, so the pin on this route covers them all).
    """
    return _session_view(session, d)



def interrupted_rows(db) -> list:
    """The jobs a restart cut off that are still offered Continue (v1.249.0,
    R-02; mission roots only since v1.309.0): newest first, the last 3 days,
    at most 20. Shared by ``GET /sessions/interrupted`` and the home's status
    line (``GET /ui/status-line``, calm UI redesign S8)."""
    from datetime import timedelta

    from sqlalchemy import exists, or_
    from sqlmodel import select

    from ...agents.team import MISSION_MEMBER_ORIGIN as MEMBER_ORIGIN
    from ...core.ids import utcnow
    from ...core.models import AgentRun, Session, SessionStatus

    cutoff = utcnow() - timedelta(days=3)
    delegated = exists().where(
        AgentRun.session_id == Session.id,
        AgentRun.parent_id.is_not(None),  # type: ignore[union-attr]
    )
    return list(
        db.exec(
            select(Session)
            .where(
                Session.interrupted_at.is_not(None),  # type: ignore[union-attr]
                Session.interrupted_at >= cutoff,  # type: ignore[operator]
                Session.status == SessionStatus.FAILED,
                or_(Session.origin.is_(None), Session.origin != MEMBER_ORIGIN),  # type: ignore[union-attr]
                ~delegated,
            )
            .order_by(Session.interrupted_at.desc())  # type: ignore[union-attr]
            .limit(20)
        )
    )

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

            # v1.228.0: the predicate now PROBES writability (creates a file),
            # so it leaves the loop — a user-picked share can stall.
            if not await asyncio.to_thread(usable_workspace_root, workspace_root):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "workspace_root must be an existing, absolute, "
                        "non-protected folder this app may write in "
                        "(missing, protected, not a directory, or not "
                        f"writable): {workspace_root} — pick a folder you can "
                        "save files in"
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
                # THE POSTURE RIDES (v1.232.0, A7); yolo is normalised away
                # inside create_session, the one door every session passes.
                approval_mode=body.approval_mode,
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

    @app.post("/missions", status_code=201)
    async def create_mission(body: MissionCreate) -> dict[str, Any]:
        """ONE OBJECTIVE, THE WHOLE TEAM (v1.307.0). The Agents page's
        mission door: the user does not address agents one by one — Jarvis
        (a SUPERVISOR run) splits the objective and hands the parts to the
        team. Same seams as ``POST /sessions`` with ``agent_type: supervisor``
        (folder guard, posture, step budget, the governor via ``_spawn_bg``),
        plus the run option ``deliverable`` so the coordinator's final message
        is the work product. Always background: the page watches
        ``/sessions/{id}/mission`` and the session's stream."""
        from ...core.models import AgentType

        objective = (body.objective or "").strip()
        if not objective:
            raise HTTPException(status_code=400, detail="an objective is required")
        workspace_root = (body.workspace_root or "").strip() or None
        if workspace_root:
            from ...core.fs_policy import usable_workspace_root

            if not await asyncio.to_thread(usable_workspace_root, workspace_root):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "workspace_root must be an existing, absolute, "
                        "non-protected folder this app may write in "
                        "(missing, protected, not a directory, or not "
                        f"writable): {workspace_root} — pick a folder you can "
                        "save files in"
                    ),
                )
        options: dict[str, Any] = {"deliverable": True}
        project_id = (body.project_id or "").strip()
        if project_id:
            # THE PROJECT'S TEAM (v1.308.0), snapshotted onto the run: the
            # coordinator is shown only these agents and `delegate` /
            # `spawn_agent` refuse the rest. A project with no team leaves
            # the run unrestricted (Jarvis picks from everyone).
            team = await asyncio.to_thread(_project_team, d, project_id)
            if team:
                options["team"] = team
        session = await d.orchestrator.create_session(
            objective,
            AgentType.SUPERVISOR,
            body.provider,
            model=body.model,
            project_id=project_id or None,
            allow_tools=body.allow_tools or None,
            workspace_root=workspace_root,
            origin=MISSION_ORIGIN,
            max_steps=body.max_steps,
            approval_mode=body.approval_mode,
            options=options,
        )
        if d._spawn_bg(session.id, d.orchestrator.run_session(session.id)) is None:
            session = d.orchestrator.get_session(session.id) or session
        return _session_row(d, session)

    @app.get("/missions")
    def list_missions(project_id: str = "") -> dict[str, Any]:
        """The most recent missions (v1.307.0), newest first — the rows the
        mission door made, by its origin stamp. Bounded. ``project_id``
        (v1.308.0) narrows to one project's missions.

        v1.309.0 (contract 6): ``objective`` is the user's own words
        (``mission.display_objective`` — a follow-up is listed by the
        follow-up, not by the model-facing recap), and ``waiting`` says the
        mission — its coordinator or any member — is parked on an ask, so the
        list stops saying "working" while the whole team waits on the user.
        Only a running/queued row can be waiting; the check is one in-memory
        registry plus at most one statement per delegation depth for every
        row together (``mission.missions_waiting``)."""
        from sqlmodel import select

        from ...agents.mission import display_objective, missions_waiting
        from ...core.db import session_scope
        from ...core.models import Session as SessionModel
        from ...core.models import SessionStatus

        stmt = select(SessionModel).where(SessionModel.origin == MISSION_ORIGIN)
        if project_id.strip():
            stmt = stmt.where(SessionModel.project_id == project_id.strip())
        with session_scope(d.platform.engine) as db:
            rows = list(
                db.exec(
                    stmt.order_by(SessionModel.created_at.desc())  # type: ignore[attr-defined]
                    .limit(_MISSIONS_LIMIT)
                )
            )
            live = [
                r.id for r in rows if r.status in (SessionStatus.ACTIVE, SessionStatus.QUEUED)
            ]
            try:
                waiting = missions_waiting(d.platform, db, live)
            except Exception:  # noqa: BLE001 — a listing never breaks on a hint
                waiting = set()
        return {
            "missions": [
                {
                    "id": r.id,
                    "objective": display_objective(r),
                    "waiting": r.id in waiting,
                    "status": r.status.value,
                    "outcome": getattr(r, "outcome", None) or None,
                    "project_id": r.project_id,
                    "created_at": r.created_at.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in rows
            ]
        }

    @app.post("/missions/{session_id}/retry-failed", status_code=201)
    async def retry_failed_mission(session_id: str) -> dict[str, Any]:
        """Retry a mission's FAILED worklist items in ONE server-side step
        (v1.309.0, contract 6, mission-dead-end-no-followup).

        The session page's version is two requests (reset-failed, then
        continue), and a page that dies between them leaves the items reset
        with no run to take them. Here the new run is CREATED first, then the
        failed items are re-opened, then the run is started — every refusal
        happens before anything is written: 404 unknown (or not a mission),
        409 while it is still running, 409 with no worklist, 409 with nothing
        failed.

        THE NEW RUN IS A RERUN OF THE MISSION, NOT A ``continue_session``
        ROUND, on purpose. A worklist board is keyed by the job's TASK
        (``worklist/store.board_for_root``), and a continuation's task is the
        recap text — so a continuation lands on a DIFFERENT, empty board and
        cannot see the items this door just re-opened (measured: the mission's
        board ``job:dd10a76b…``, its continuation's ``job:ce656e83…``). A
        rerun keeps the task, the project/folder, the posture, the grants,
        the origin and the run options (the project's team, ``deliverable``),
        so it lands on the SAME board, and the worklist's own rule — done
        items are never handed out again, ``worklist_add`` re-adds nothing on
        a repeated job (``supervisor.WORKLIST_PATTERN``) — makes it take
        exactly the reset ones. It is linked as this mission's continuation
        (``options.continued_from``) so the old screen says where the work
        went on, and its objective names the retry in the user's terms.

        …IN THE SAME FOLDER (v1.309.0 review). A plain rerun of a mission
        that ran in a managed SCRATCH workspace got a fresh, empty one, so the
        retry could not see a single file the first run made. Here it is
        moved into the mission's own folder before it starts (the rule
        ``continue_session`` uses: never a git worktree; 409 while another run
        is working there, checked under the orchestrator's continue lock so a
        concurrent /continue cannot share it). A project / user-folder
        mission already reran in its folder. Answers the new session row,
        flat, like every start door."""
        from ...agents.mission import display_objective
        from ...core.models import SessionStatus

        prev = d.orchestrator.get_session(session_id)
        if prev is None or getattr(prev, "origin", None) != MISSION_ORIGIN:
            raise HTTPException(status_code=404, detail="mission not found")
        if prev.status in (SessionStatus.ACTIVE, SessionStatus.QUEUED):
            raise HTTPException(
                status_code=409,
                detail="This mission is still running. Wait for it to finish before retrying.",
            )
        store = getattr(d.platform, "worklist", None)
        if store is None:  # pragma: no cover - a platform without the store
            raise HTTPException(status_code=409, detail="this mission has no worklist")
        board_id = await asyncio.to_thread(store.root_session_for, session_id)
        summary = await asyncio.to_thread(store.summary, board_id)
        if int(summary.get("total") or 0) == 0:
            raise HTTPException(status_code=409, detail="this mission has no worklist")
        failed = int(summary.get("failed") or 0)
        if failed == 0:
            raise HTTPException(status_code=409, detail="nothing failed in this mission")
        reuse = await asyncio.to_thread(_managed_reuse_path, d, prev)
        # The orchestrator's continue lock: a /continue and a retry can never
        # both claim one folder (a double without one runs unserialised).
        lock = getattr(d.orchestrator, "_continue_lock", None)
        async with lock if lock is not None else contextlib.nullcontext():
            if reuse and await asyncio.to_thread(_folder_busy, d, reuse):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A follow-up of this mission is already running or "
                        "queued. Wait for it to finish before retrying."
                    ),
                )
            try:
                session = await d.orchestrator.rerun_session(session_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="mission not found")
            except (PermissionError, RuntimeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if reuse and getattr(d.orchestrator, "_git_sessions", {}).get(session.id) is None:
                try:
                    await asyncio.to_thread(_move_into, d, session.id, reuse)
                    session.workspace_path = reuse
                except Exception as exc:  # noqa: BLE001 — never run it blind
                    try:
                        d.orchestrator.delete_session(session.id)
                    except Exception:  # noqa: BLE001
                        pass
                    raise HTTPException(
                        status_code=500,
                        detail=f"could not reuse the mission's folder: {exc}",
                    )
        try:
            await asyncio.to_thread(store.reset_failed, board_id)
        except Exception as exc:  # noqa: BLE001 — never leave a run with nothing to do
            try:
                d.orchestrator.delete_session(session.id)
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=500, detail=f"could not re-open the failed items: {exc}"
            )
        items = "item" if failed == 1 else "items"
        # A retry of a retry names the ORIGINAL objective once (v1.309.0
        # review): strip a previous "Retry the N failed item(s): " prefix.
        base = re.sub(r"^(?:Retry the \d+ failed items?: )+", "", display_objective(prev))
        objective = f"Retry the {failed} failed {items}: {base}"
        session.options_json = await asyncio.to_thread(
            _stamp_continuation, d, session.id, session_id, objective, retry_failed=failed
        ) or session.options_json
        # Continuing the work answers a restart's prompt, as /continue does.
        await asyncio.to_thread(_clear_interrupted, d, session_id)
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
            session = await d.orchestrator.continue_session(
                session_id,
                body.message,
                # GRANTS + POSTURE RIDE THE CONTINUE (v1.232.0, A6/A7):
                # unioned with the stored grant / inherited when blank.
                allow_tools=body.allow_tools or None,
                approval_mode=body.approval_mode or None,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="session not found")
        except ValueError as exc:  # workspace busy — a continuation is running
            raise HTTPException(status_code=409, detail=str(exc))
        # v1.309.0 (contract 5): a MISSION's continuation is linked to it
        # BEFORE it runs, so the old mission screen can say "Continued in →
        # open", and the user's own follow-up becomes its display objective.
        # Any other continuation is left exactly as continue_session made it.
        session.options_json = await asyncio.to_thread(
            _stamp_continuation, d, session.id, session_id, (body.message or "").strip(),
            resume=bool(body.resume),
        ) or session.options_json
        # v1.249.0 (R-02): this job is being picked up again, so it is no
        # longer a prompt. The tag is cleared on the ORIGINAL row — the
        # continuation is a new session, and the old one keeps its verdict.
        await asyncio.to_thread(_clear_interrupted, d, session_id)
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
    def list_sessions(request: Request, limit: int = 200, project_id: str = "") -> Response:
        """``{"sessions": [...]}`` — newest first, bounded by ``limit``.

        Answers with a weak ``ETag``; repeat the GET with ``If-None-Match`` and
        an unchanged list is a bodiless 304 (v1.230.0).
        """
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
            return _etagged_json({"sessions": [_session_row(d, s) for s in scoped]}, request)
        rows = [_session_row(d, s) for s in d.orchestrator.list_sessions(limit=lim)]
        return _etagged_json({"sessions": rows}, request)

    @app.get("/sessions/interrupted")
    def interrupted_sessions() -> dict[str, Any]:
        """Jobs a RESTART cut off, newest first (v1.249.0, R-02).

        REGISTERED BEFORE ``/sessions/{session_id}`` on purpose: FastAPI
        matches in registration order, so the parameter route would swallow
        "interrupted" and answer 404 for a path that exists.

        The boot reconcile tags each one (``Session.interrupted_at``); the
        bell and the Overview offer Continue while the tag is set, and
        continuing or dismissing clears it. Bounded to the last 3 days and 20
        rows — an interrupted job nobody came back to stops being a prompt
        and stays in the session list like any other failed run.

        ONLY THE MISSION ROOT IS OFFERED (v1.309.0, contract 7,
        restart-orphans-children-no-mission-recovery). A restart mid-mission
        tagged the coordinator AND every member it had delegated to, and each
        member was offered Continue as if it were a job of its own — N+1
        prompts, and continuing a member started a lone builder with no
        coordinator to report to. A delegated child is excluded by EITHER
        mark: its origin (``job:mission-member``, stamped at creation — the
        one that survives a child killed before its AgentRun row was written)
        or an ``AgentRun.parent_id`` link (a child from before the stamp, or
        from a non-mission delegation). Continuing the root re-runs the team.
        Each row also carries ``origin`` (so a mission's Continue can land on
        the mission screen) and ``objective`` (the user's words, not the
        recap) — additive.
        """
        from ...agents.mission import display_objective
        from ...core.db import session_scope

        with session_scope(d.platform.engine) as db:
            rows = interrupted_rows(db)
            out = [
                {
                    "id": s.id,
                    "task": (s.task or "")[:300],
                    "objective": display_objective(s)[:300],
                    "origin": s.origin,
                    "agent_type": getattr(s.agent_type, "value", str(s.agent_type)),
                    "project_id": s.project_id,
                    "interrupted_at": (
                        s.interrupted_at.isoformat() if s.interrupted_at else None
                    ),
                }
                for s in rows
            ]
        return {"sessions": out}

    @app.post("/sessions/{session_id}/interrupted/dismiss")
    async def dismiss_interrupted(session_id: str) -> dict[str, Any]:
        """Stop offering Continue for this interrupted job (v1.249.0, R-02).

        The run itself is untouched: this clears the PROMPT, nothing else.
        """
        cleared = await asyncio.to_thread(_clear_interrupted, d, session_id)
        if not cleared:
            raise HTTPException(status_code=404, detail="session not found")
        return {"ok": True, "id": session_id}

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

    @app.get("/sessions/{session_id}/mission")
    def session_mission(session_id: str, request: Request) -> Response:
        """The MISSION view of a coordinator session (v1.307.0): the team's
        members with honest progress, a plain-words activity log and the
        deliverable — composed from the ledger by ``agents/mission.py``.
        Sync on purpose (FastAPI's threadpool): SQLite reads only. An unknown
        id is ``found: false`` (200), like ``/team``, so a polling page never
        turns a deleted mission into an error toast.

        v1.309.0: served through ``_etagged_json`` (the ``/sessions``
        pattern). The page polls this every 2 s; an unchanged tick is now a
        bodiless 304, so neither the body nor a re-render crosses for
        nothing. The view carries no clock-derived field (every time in it is
        a stored timestamp), which is what makes the tag stable. The DB work
        is made cheap in ``mission_view`` itself — a tag is a hash of the
        built body, so it saves transfer, not the read."""
        from ...agents.mission import mission_view

        view = mission_view(d.platform, session_id)
        if view is None:
            return _etagged_json({"found": False, "session_id": session_id}, request)
        return _etagged_json(view, request)

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
        """The session's pending review (flat), or ``{"review": null}``.

        v1.232.0 (audit U14): "no review" is the NORMAL state of a session
        (git-native off, or already approved), and every detail visit used to
        log a 404 for it. A session that does not exist is still a 404.
        """
        if d.orchestrator.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        review = d.orchestrator.get_review(session_id)
        if review is None:
            return {"review": None}
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
