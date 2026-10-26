"""TRY ONE TASK ON SEVERAL MODELS — the routes (v1.397.0, ``harness/attempts``).

* ``POST /build/sessions/{id}/turn`` (``routes/build``) asks
  :func:`maybe_start` first: a message that says "try this with A, B and C"
  (or the box's "Try on…" control, ``try_on``) starts the ATTEMPTS turn — a
  daemon-owned turn like any other (it is followed, rejoined, stopped by its
  name and its reply line written by ``routes/build.recorded_turn``), whose
  frames are the attempts' rows and whose ``done`` carries the card. A
  message whose names are none of the user's models is an ordinary message
  (None), and the ordinary turn starts exactly as before. Every refusal —
  too many, an unknown or ambiguous name, a cloud model under Local only, an
  account past its floor, a pay-per-use model past the limit, a folder that
  is not in a git repository, changes not committed — is ONE sentence before
  anything started (no worktree, no question line, no model call).
* ``POST /build/sessions/{id}/attempts/keep {turn_id, attempt}`` — KEEP one:
  a person's press, off the loop under the session's git lock, refused while
  the session runs (the attempts are part of its running turn).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...harness import attempts as A
from ...harness import sessions as S

log = logging.getLogger(__name__)

#: Attempts take the words of the message only (the files and the "/" skill
#: of a message are not given to every attempt yet).
WORDS_ONLY = (
    "Trying on several models takes the message's words only. Send the files, the skill or the "
    "command in a message of their own first, then ask to try it; nothing started."
)


class KeepBody(BaseModel):
    turn_id: str = Field(min_length=1, max_length=80)
    attempt: str = Field(min_length=1, max_length=20)


class DiscardBody(BaseModel):
    turn_id: str = Field(min_length=1, max_length=80)


def menu_rows(d: Any) -> list[dict[str, Any]]:
    """The model menu's rows (``GET /models``'s own catalog). BLOCKING."""
    from .connections import selectable_models

    return selectable_models(d)


def request_of(body: Any, text: str) -> "A.Request | None":
    """What the message asks for: the "Try on…" control's picks, else the
    words (:func:`harness.attempts.parse_request`), else None."""
    picks = list(getattr(body, "try_on", None) or [])
    if picks:
        # Review (v1.397.0): picks AND the words "try this with A and B: …"
        # — the work is the words' own task, never "try this with A and B"
        # handed to every attempt as part of it.
        said = A.parse_request(text)
        return A.Request(
            picks=[(str(p.provider or "").strip(), str(p.model or "").strip()) for p in picks],
            task=said.task if said is not None else text,
        )
    return A.parse_request(text)


def _last_question(row: Any) -> str:
    """The session's last question (its words as the model read them), for a
    request that names no work of its own ("try it on two models")."""
    for m in reversed(S.history(row)):
        if m.get("role") == "user" and str(m.get("content") or "").strip():
            return str(m["content"]).strip()
    return ""


def _undo(made: list[dict[str, str]], sha: str) -> None:
    from ...harness import branches as B
    from pathlib import Path

    for got in made:
        with contextlib.suppress(Exception):
            B._undo_start(got.get("repo", ""), Path(got.get("worktree", "")),  # noqa: SLF001
                          got.get("branch", ""), sha)


async def maybe_start(d: Any, thread_id: str, text: str, turn_id: str, body: Any) -> Any:
    """Start the attempts turn and return its ``TurnLog`` — or None when the
    message asks for no attempts. Raises ``routes/build.SessionBusy`` (a
    turn runs), ``HTTPException`` in words (every refusal, before anything
    started)."""
    from ...agents import model_team as MT
    from ...projects.locate import project_for_path
    from ..schemas import ChatMessageBody
    from .build import (
        _COMPACTING,
        _STARTING,
        ARCHIVED_SENTENCE,
        COMPACTING_SENTENCE,
        SessionBusy,
        _command_rule,
        _running_turn,
        _setup_of,
        _turn_pair,
        build_step_limit,
        recorded_turn,
        turn_account,
    )
    from .chat import _turn_logs

    req = request_of(body, text)
    if req is None:
        return None
    platform = d.platform
    if thread_id in _COMPACTING:
        raise HTTPException(status_code=409, detail=COMPACTING_SENTENCE)
    if thread_id in _STARTING or _running_turn(d, thread_id):
        raise SessionBusy(thread_id)
    _STARTING.add(thread_id)
    made: list[dict[str, str]] = []
    start: "A.StartPoint | None" = None
    started = False
    try:
        row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        if getattr(row, "archived_at", None) is not None:
            raise HTTPException(status_code=409, detail=ARCHIVED_SENTENCE)
        setup = _setup_of(row)
        local_only = setup.get("local_only") is True
        project = await asyncio.to_thread(project_for_path, platform.engine, S.checkout_of(row))
        session_pair = _turn_pair(platform, SimpleNamespace(
            provider=str(setup.get("provider") or ""), model=str(setup.get("model") or ""),
        ), project)
        try:
            rows = await asyncio.to_thread(menu_rows, d)
        except Exception as exc:  # noqa: BLE001 — said, never an empty menu
            raise HTTPException(
                status_code=503, detail=f"The model menu could not be read ({type(exc).__name__}); nothing started.",
            ) from None
        members = await asyncio.to_thread(MT.team_members, platform)
        menu = A.Menu(rows=rows, members=members, session_provider=session_pair[0],
                      session_model=session_pair[1])
        try:
            targets = await asyncio.to_thread(A.resolve, platform, req, menu, local_only=local_only)
        except A.AttemptsRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        if targets is None:
            return None  # an ordinary message after all
        # From here on the message IS a request for attempts: every refusal is
        # said, and nothing starts.
        if (list(getattr(body, "attachments", None) or []) or list(getattr(body, "files", None) or [])
                or str(getattr(body, "skill", "") or "").strip() or str(getattr(body, "folder_skill", "") or "").strip()
                or str(getattr(body, "command", "") or "").strip()):
            raise HTTPException(status_code=400, detail=WORDS_ONLY)
        if S.mode_of(row) == S.MODE_PLAN:
            raise HTTPException(status_code=409, detail=A.PLAN_MODE)
        task = req.task.strip() or await asyncio.to_thread(_last_question, row)
        if not task:
            raise HTTPException(status_code=400, detail=A.NO_TASK)
        try:
            caller_pin, _provider = await asyncio.to_thread(turn_account, platform, thread_id, setup)
        except HTTPException:
            caller_pin = None
        pins = []
        try:
            for t in targets:
                pins.append(await asyncio.to_thread(
                    A.check_target, platform, t, local_only=local_only, session_pair=session_pair,
                    caller_pin=caller_pin, scope=f"build:{thread_id}:{turn_id}",
                ))
            start = await asyncio.to_thread(A.start_point, row)
            made = await asyncio.to_thread(A.make_worktrees, start, targets)
        except A.AttemptsRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        from ...agents import model_team as _mt

        attempts: list[A.Attempt] = []
        for n, (target, pin, got) in enumerate(zip(targets, pins, made), start=1):
            aid = f"{A.ID_PREFIX}{n}"
            provider = str(getattr(pin, "ij_provider", "") or "") if pin is not None else ""
            if provider and provider != target.provider:
                # The account's vendor CLI serves it (a keyless API name).
                target.provider = provider
            facts = {k: v for k, v in got.items() if k != "folder"}
            attempts.append(A.Attempt(
                id=aid, target=target, pin=pin, git=facts, folder=got.get("folder") or facts["worktree"],
                sid=A.attempt_sid(thread_id, aid), turn_id=f"{turn_id}.{aid}",
                cost=_mt.cost_type(platform, target.provider, target.model),
            ))
        from ...harness import compact as _compact

        history_raw, summary = await asyncio.to_thread(_compact.history_for_turn, row)
        command_rule = await asyncio.to_thread(_command_rule, row, setup, None, False)
        rounds, no_limit = build_step_limit(platform.config)
        plan = A.Plan(
            thread_id=thread_id, turn_id=turn_id, task=task,
            history=[ChatMessageBody(**m) for m in history_raw], summary=summary,
            setup=setup, project_id=project.id if project is not None else "",
            start=start, attempts=attempts, command_rule=command_rule,
            ceiling=A.ceiling_for(platform), rounds=rounds, local_only=local_only,
        )
        question: dict[str, Any] = {"role": "user", "content": text or task, "turnId": turn_id}
        saved = await asyncio.to_thread(S.append_lines, platform.engine, thread_id, [question])
        if not saved.get("ok"):
            raise HTTPException(status_code=404, detail="no such Build session")
        logs = _turn_logs(d)
        lg = logs.start(turn_id, thread_id, recorded_turn(
            platform.engine, thread_id, turn_id,
            A.run_attempts(platform, getattr(d, "_PERSONAS", {}), plan),
            platform=platform, no_limit=no_limit,
        ))
        lg.surface = "build"
        started = True
        return lg
    finally:
        _STARTING.discard(thread_id)
        if made and not started and start is not None:
            # Refused after the worktrees were made (the question could not be
            # written): they hold nothing of the user's — undone.
            await asyncio.to_thread(_undo, made, start.sha)


def register(app: FastAPI, d: Any) -> None:
    from .build import _COMPACTING, _STARTING, COMPACTING_SENTENCE, _running_turn
    from .build_git import _GIT_ACTING, GIT_BUSY, GIT_RUNNING, _locked

    @app.post("/build/sessions/{thread_id}/attempts/keep")
    async def keep_attempt(thread_id: str, body: KeepBody) -> Any:
        """KEEP ONE ATTEMPT (``harness/attempts.keep``): its work is committed
        on its branch and merged into the session's branch (or, for a session
        that worked in its folder directly, the session moves onto it); the
        attempts' folders go to the trash, their branches kept. Refused in
        words while any attempt still runs, on a merge conflict (nothing
        moved), and when the session's own branch is not clean."""
        if thread_id in _GIT_ACTING:
            return JSONResponse(status_code=409, content={"detail": GIT_BUSY})
        if thread_id in _COMPACTING:
            return JSONResponse(status_code=409, content={"detail": COMPACTING_SENTENCE})
        running = _running_turn(d, thread_id)
        if thread_id in _STARTING or running:
            words = A.KEEP_RUNNING if running and running in A.running_for(thread_id) else GIT_RUNNING
            return JSONResponse(status_code=409, content={"detail": words})
        _STARTING.add(thread_id)
        _GIT_ACTING.add(thread_id)
        platform = d.platform
        try:
            return await asyncio.to_thread(
                _locked, thread_id, lambda: A.keep(platform, thread_id, body.turn_id, body.attempt),
            )
        except A.AttemptsRefused as exc:
            return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 — GitRefused from the lock wait
            status = getattr(exc, "status", None)
            if isinstance(status, int):
                content: dict[str, Any] = {"detail": str(exc)}
                if getattr(exc, "details", ""):
                    content["details"] = exc.details  # type: ignore[attr-defined]
                return JSONResponse(status_code=status, content=content)
            raise
        finally:
            _GIT_ACTING.discard(thread_id)
            _STARTING.discard(thread_id)

    @app.post("/build/sessions/{thread_id}/attempts/discard")
    async def discard_attempts(thread_id: str, body: DiscardBody) -> Any:
        """DISCARD ALL (review, v1.397.0): every attempt's folder of that
        message goes to the trash, every branch stays (``harness/attempts.
        discard``). Refused in words while the session runs, for a card a
        branched session copied, and when nothing is left to discard."""
        if thread_id in _GIT_ACTING:
            return JSONResponse(status_code=409, content={"detail": GIT_BUSY})
        if thread_id in _COMPACTING:
            return JSONResponse(status_code=409, content={"detail": COMPACTING_SENTENCE})
        running = _running_turn(d, thread_id)
        if thread_id in _STARTING or running:
            words = A.KEEP_RUNNING if running and running in A.running_for(thread_id) else GIT_RUNNING
            return JSONResponse(status_code=409, content={"detail": words})
        _STARTING.add(thread_id)
        _GIT_ACTING.add(thread_id)
        platform = d.platform
        try:
            return await asyncio.to_thread(
                _locked, thread_id, lambda: A.discard(platform, thread_id, body.turn_id),
            )
        except A.AttemptsRefused as exc:
            return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 — GitRefused from the lock wait
            status = getattr(exc, "status", None)
            if isinstance(status, int):
                return JSONResponse(status_code=status, content={"detail": str(exc)})
            raise
        finally:
            _GIT_ACTING.discard(thread_id)
            _STARTING.discard(thread_id)


__all__ = ["DiscardBody", "KeepBody", "WORDS_ONLY", "maybe_start", "menu_rows", "register", "request_of"]
