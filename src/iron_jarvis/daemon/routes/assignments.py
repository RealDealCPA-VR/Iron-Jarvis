"""Assignments (v1.296.0): give an agent a job and the job waits for it.

An ASSIGNMENT is a durable row queued for a named agent (``builder``,
``custom:<name>``); ``assignments.dispatcher`` runs it when that agent is
free. These are the HTTP doors over ``platform.assignments``
(``assignments.store.AssignmentStore``):

* ``POST /assignments`` creates one (201) or coalesces a re-post carrying the
  same ``idempotency_key`` onto the queued row (200, ``created: false``);
* ``GET /assignments`` lists with filters, ``GET /assignments/{id}`` reads one;
* ``POST /assignments/{id}/cancel`` stops it — a RUNNING one has its session
  cancelled through the orchestrator FIRST, so the agent actually stops;
* ``POST /assignments/{id}/unblock`` lets a blocked (repeatedly failed) one
  queue again; ``POST /assignments/{id}/retry`` makes a NEW row from a
  finished one (201).

Every refusal is plain words: 422 for a bad body or an unknown assignee, 404
for an unknown id, 409 when the store refuses the transition. List routes are
sync ``def`` (DB reads in FastAPI's threadpool); the cancel route is async
because it reaches the orchestrator's running task.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Response

from ..schemas import AssignmentCreate

log = logging.getLogger("iron_jarvis.assignments")

#: Statuses ``GET /assignments?status=`` accepts (a typo is a 422, not an
#: empty list that reads as "nothing queued").
ASSIGNMENT_STATUSES = (
    "queued", "claimed", "running", "done", "failed", "blocked", "cancelled",
)
_LIST_LIMIT_MAX = 500


def resolve_assignee(d, raw: str) -> str:
    """The ROSTER name an assignment is queued under, or a 422 saying why not.

    Accepts a builtin agent type (``builder``), a custom agent's bare name
    (``tax-reader``) or its roster form (``custom:tax-reader``); returns the
    roster name the dispatcher and ``agents.roster`` key on. Validation is the
    schedule-add route's (``routes/system.py``): a builtin or an EXISTING
    custom name, checked now rather than when the job comes up. ``supervisor``
    is refused — the builtin supervisor reroutes to ``run_supervised`` and a
    queued job cannot carry that door's arguments."""
    from ...core.models import AgentType

    name = (raw or "").strip()
    bare = name[len("custom:"):] if name.startswith("custom:") else name
    builtin = {t.value for t in AgentType}
    if not name:
        raise HTTPException(
            status_code=422,
            detail="assignee is required — a builtin agent type or a custom agent's name",
        )
    if name in builtin:
        if name == AgentType.SUPERVISOR.value:
            raise HTTPException(
                status_code=422,
                detail=(
                    "the supervisor cannot take an assignment — it runs other "
                    "agents; assign the job to a worker agent instead"
                ),
            )
        return name
    rec = None
    try:
        rec = d.platform.agents_registry.get(bare) if bare else None
    except Exception:  # noqa: BLE001 — a broken registry refuses, never guesses
        rec = None
    if rec is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown assignee {raw!r} — use a builtin agent type or an "
                "existing custom agent's name (Agents page)"
            ),
        )
    return f"custom:{rec.name}"


def _store(d):
    store = getattr(d.platform, "assignments", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="assignments are not available on this daemon — restart it",
        )
    return store


async def _wake(d) -> None:
    """Nudge the dispatcher after a queue change. Absent or failing is fine:
    the dispatcher polls on its own clock; this only makes it sooner."""
    disp = getattr(d.platform, "assignment_dispatcher", None)
    wake = getattr(disp, "wake", None)
    if not callable(wake):
        return
    try:
        result = wake()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 — a wake must never fail the request
        log.debug("assignment dispatcher wake failed", exc_info=True)


def _row(store, record) -> dict[str, Any]:
    return store.as_dict(record)


def _get_or_404(store, assignment_id: str):
    record = store.get(assignment_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no assignment {assignment_id!r}")
    return record


_FINISHED = ("done", "failed", "cancelled")
#: Which rows each transition may start from (the store's own vocabulary —
#: ``AssignmentStore.cancel/unblock/retry`` leave any other row untouched and
#: hand it back unchanged, so the refusal has to be read off the STATUS).
_ALLOWED_FROM = {
    "cancel": lambda status: status not in _FINISHED,
    "unblock": lambda status: status == "blocked",
    "retry": lambda status: status in _FINISHED,
}
_REFUSAL_WORDS = {
    "cancel": "it has already finished",
    "unblock": "only a blocked assignment can be unblocked",
    "retry": "only a finished (done, failed or cancelled) assignment can be retried",
}


def _refuse(verb: str, assignment_id: str, status: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            f"cannot {verb} assignment {assignment_id!r}: it is {status or 'unknown'} "
            f"— {_REFUSAL_WORDS[verb]}"
        ),
    )


async def _transition(store, assignment_id: str, verb: str):
    """Run one store transition off the loop. A row the transition does not
    apply to is a plain 409 naming its CURRENT status (checked here, then
    again on the store's answer — a None or a ValueError is the same refusal)."""
    before = _get_or_404(store, assignment_id)
    status = str(getattr(before, "status", "") or "")
    if not _ALLOWED_FROM[verb](status):
        raise _refuse(verb, assignment_id, status)
    try:
        record = await asyncio.to_thread(getattr(store, verb), assignment_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if record is None:
        current = store.get(assignment_id) or before
        raise _refuse(verb, assignment_id, str(getattr(current, "status", "") or ""))
    return record


def register(app: FastAPI, d) -> None:
    @app.post("/assignments", status_code=201)
    async def create_assignment(body: AssignmentCreate, response: Response) -> dict[str, Any]:
        assignee = resolve_assignee(d, body.assignee)
        store = _store(d)
        payload = dict(body.payload or {})
        workspace_root = (payload.get("workspace_root") or "").strip()
        if workspace_root:
            from ...core.fs_policy import usable_workspace_root

            # Same predicate and words as POST /sessions and /agents/{name}/spawn;
            # the writability probe creates a file, so it leaves the loop.
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
            payload["workspace_root"] = workspace_root
        else:
            payload.pop("workspace_root", None)
        try:
            record, created = await asyncio.to_thread(
                store.create,
                assignee,
                body.task,
                project_id=body.project_id or "",
                source="user",
                reason=body.reason or "",
                priority=body.priority,
                payload=payload or None,
                idempotency_key=body.idempotency_key or "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not created:
            response.status_code = 200
        await _wake(d)
        return {"assignment": _row(store, record), "created": bool(created)}

    @app.get("/assignments")
    def list_assignments(
        assignee: str = "",
        project_id: str = "",
        status: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        statuses = [s.strip().lower() for s in status.split(",") if s.strip()]
        unknown = [s for s in statuses if s not in ASSIGNMENT_STATUSES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown status {', '.join(repr(s) for s in unknown)} — "
                    f"one of {', '.join(ASSIGNMENT_STATUSES)}"
                ),
            )
        if limit < 1 or limit > _LIST_LIMIT_MAX:
            raise HTTPException(
                status_code=422, detail=f"limit must be between 1 and {_LIST_LIMIT_MAX}"
            )
        who = assignee.strip() or None
        if who is not None:
            who = resolve_assignee(d, who)
        store = _store(d)
        rows = store.list(
            assignee=who,
            project_id=project_id.strip() or None,
            statuses=statuses or None,
            limit=limit,
        )
        return {"assignments": [_row(store, r) for r in rows]}

    @app.get("/assignments/{assignment_id}")
    def get_assignment(assignment_id: str) -> dict[str, Any]:
        store = _store(d)
        return {"assignment": _row(store, _get_or_404(store, assignment_id))}

    @app.post("/assignments/{assignment_id}/cancel")
    async def cancel_assignment(assignment_id: str) -> dict[str, Any]:
        store = _store(d)
        record = _get_or_404(store, assignment_id)
        session_id = str(getattr(record, "session_id", "") or "")
        if getattr(record, "status", "") == "running" and session_id:
            # Stop the agent FIRST: the row saying "cancelled" over a session
            # still burning tokens would be a lie the user acts on.
            try:
                d.orchestrator.cancel_session(session_id)
            except (KeyError, ValueError):
                # Unknown or already settled — the store decides what the row
                # becomes; nothing is running that we could stop.
                pass
        record = await _transition(store, assignment_id, "cancel")
        return {"assignment": _row(store, record)}

    @app.post("/assignments/{assignment_id}/unblock")
    async def unblock_assignment(assignment_id: str) -> dict[str, Any]:
        store = _store(d)
        record = await _transition(store, assignment_id, "unblock")
        await _wake(d)
        return {"assignment": _row(store, record)}

    @app.post("/assignments/{assignment_id}/retry", status_code=201)
    async def retry_assignment(assignment_id: str) -> dict[str, Any]:
        store = _store(d)
        record = await _transition(store, assignment_id, "retry")
        await _wake(d)
        return {"assignment": _row(store, record)}
