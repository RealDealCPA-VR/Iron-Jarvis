"""Build's IJ harness sessions (v1.367.0, plan Phase 4) — the session backend.

An IJ harness session IS a chat thread with ``surface = "build"``
(``harness/sessions``), so it inherits durable named turns
(``daemon/durable_turns``: rejoin with ``GET /chat/turns/{id}/stream``, Stop
with ``POST /chat/turns/{id}/stop``, steer with ``POST /chat/turns/{id}/steer``),
the queue (``PUT /chat/threads/{id}/queue``), per-thread usage
(``GET /chat/threads/{id}/usage``), the archive rules and the approval cards.
What these routes add:

* ``GET /build/sessions`` — every session grouped by its folder
  (``{folders: [{path, name, is_project, project_id, sessions: [...]}]}``,
  newest first; ``?archived=1`` includes archived ones);
* ``POST /build/sessions {folder, title?}`` — a new session in a folder that
  passes ``fs_policy.root_problem`` (refused in words otherwise);
* ``GET`` / ``PATCH`` / ``DELETE /build/sessions/{id}``, archive / unarchive;
* ``POST /build/folders/rename {path, name}`` — a DISPLAY name (a project
  root's name IS the project's name; the folder on disk is never renamed);
* ``POST /build/sessions/{id}/turn {text, turn_id?}`` — ONE build turn: the
  stream lane in HARNESS mode (the model family's dialect), the session's
  folder as its workspace, ``build_round_budget`` rounds (60), never handed
  to an agent run (it ends in the conversation), every tool call under the
  session's OWN ``build:<id>`` (never "chat"), the session's mode (plan:
  read-only tools + ``exit_plan_mode``) and ``ask_user`` (the screen draws
  the question). The turn is NAMED and DAEMON-OWNED (``TurnLogs``): leaving
  Build, a reload or a closed window does not stop it; Stop does. The daemon
  writes the question when the turn starts and the reply when it ends — the
  screen never saves a session's transcript;
* ``POST /build/sessions/{id}/answer {ask_id, answer | skip}`` — the user's
  answer to an ``ask_user`` question of THIS session's running turn.

Every database touch is off the event loop (sync ``def`` routes run in the
threadpool; async ones hop with ``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import durable_turns as _durable
from ..chat_stream import stream_chat_turn
from ..schemas import ChatArchiveBody, ChatBody, ChatMessageBody
from ...core.ids import utcnow
from ...core.turns import TURNS
from ...harness import sessions as S

log = logging.getLogger(__name__)

#: A caller-chosen turn id: letters, digits, "-" / "_" / ".", at most 80.
_TURN_ID_RX = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
#: A session's message, at most (a prompt is not a document upload).
MAX_TURN_CHARS = 200_000
#: An answer to ``ask_user``, at most (as JSON).
MAX_ANSWER_CHARS = 20_000
#: The thinking a reply line keeps (the chat page's THINKING_CAP).
THINKING_CAP = 20_000
#: Steps a reply line records, at most.
MAX_STEPS = 60

RUNNING_SENTENCE = (
    "This session is still answering. Steer it, queue your message, or stop it first."
)
ARCHIVED_SENTENCE = "This session is archived. Bring it back before you continue it."

#: Sessions whose turn is being started right now (between the "nothing is
#: running" check and ``TurnLogs.start``) — on the loop thread only, so two
#: messages sent at once cannot both start a turn.
_STARTING: set[str] = set()


class BuildSessionCreate(BaseModel):
    folder: str = Field(min_length=1, max_length=1000)
    title: str = Field(default="", max_length=400)
    provider: str = ""
    model: str = ""
    reasoning: str = ""
    approval_mode: str = ""
    mode: str = ""


class BuildSessionPatch(BaseModel):
    """PARTIAL: an omitted field keeps its value; ``""`` clears a model pick."""

    title: str | None = Field(default=None, max_length=400)
    provider: str | None = None
    model: str | None = None
    reasoning: str | None = None
    approval_mode: str | None = None
    mode: str | None = None
    local_only: bool | None = None


class BuildFolderRename(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str = Field(max_length=400)


class BuildTurnBody(BaseModel):
    text: str
    turn_id: str = ""
    attachments: list[str] = Field(default_factory=list, max_length=20)


class BuildAnswerBody(BaseModel):
    ask_id: str = Field(min_length=1, max_length=80)
    answer: Any = None
    skip: bool = False


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _setup_of(row: Any) -> dict[str, Any]:
    try:
        data = json.loads(getattr(row, "setup_json", "") or "{}")
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _running_turn(d: Any, thread_id: str) -> str:
    """The id of this session's running daemon-owned turn, or "" (a turn the
    registry says is running without a log — a caller of the lane that is not
    this route — answers "running")."""
    from .chat import _turn_logs

    for lg in _turn_logs(d).running():
        if lg.thread_id == thread_id:
            return lg.turn_id
    state = TURNS.thread_states().get(thread_id) or {}
    return "running" if state.get("running") else ""


def _turn_record(engine: Any, turn_id: str) -> "tuple[str, str] | None":
    """``(thread_id, sse_text)`` of a turn's RECORD (``ChatTurnRecord``), or
    None when it was never recorded. BLOCKING: call it off the loop."""
    from ...core.db import session_scope
    from ...core.models import ChatTurnRecord

    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        if row is None:
            return None
        try:
            frames = json.loads(row.frames_json or "[]")
        except ValueError:
            frames = []
        text = "".join(
            _durable.sse(f[0], f[1]) for f in frames
            if isinstance(f, list) and len(f) == 2
        )
        return str(row.thread_id or ""), text


async def _replay_once(text: str):
    if text:
        yield text


def build_round_budget(config: Any) -> int:
    """The ``build_round_budget`` setting as a turn can use it: a whole number
    from 1 to ``chat_turn.MAX_TURN_ROUNDS`` (the lane's own bounds). The
    settings writer checks the range, but a hand-edited ``config.toml`` does
    not — an out-of-range value is CLAMPED, never a 400 on every turn of every
    session (Phase 4 review)."""
    from ..chat_turn import MAX_TURN_ROUNDS

    try:
        value = int(getattr(config, "build_round_budget", 60) or 60)  # unset / 0 = the default
    except (TypeError, ValueError):
        value = 60
    return max(1, min(MAX_TURN_ROUNDS, value))


def _clean_answer(answer: Any) -> Any:
    """A string, a list of strings, or ``{key: str | [str]}`` — 422 for
    anything else or anything too large."""
    def ok_leaf(v: Any) -> bool:
        return isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))

    if answer is None:
        raise HTTPException(status_code=422, detail="Give an answer, or skip the question.")
    good = ok_leaf(answer) or (
        isinstance(answer, dict) and all(isinstance(k, str) and ok_leaf(v) for k, v in answer.items())
    )
    if not good:
        raise HTTPException(
            status_code=422,
            detail="An answer is text, a list of choices, or one of those per question.",
        )
    if len(json.dumps(answer)) > MAX_ANSWER_CHARS:
        raise HTTPException(status_code=422, detail="That answer is too long.")
    return answer


# --------------------------------------------------------------------------- #
# the reply line, written by the daemon when the turn ends
# --------------------------------------------------------------------------- #


class _ReplyWatch:
    """Reads a build turn's frames as they pass and keeps what its reply line
    needs (and the two things that change the SESSION mid-turn: an approved
    plan and an "allow for this conversation" answer)."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.thinking_chars = 0
        self.steers: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self._started: dict[str, tuple[str, float, Any]] = {}
        self.done: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self.started_at = time.time()
        self.first_token_at: float | None = None
        #: (plan text) of an exit_plan_mode call that ran (the user allowed it).
        self.approved_plans: list[str] = []
        #: The last plan the user approved this turn — kept on the reply line
        #: (see :meth:`lines`).
        self.approved_text = ""
        #: tools allowed "for this conversation".
        self.granted: list[str] = []

    def see(self, event: str, raw: str) -> None:
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if event == "token":
            if self.first_token_at is None:
                self.first_token_at = time.time()
            self.text.append(str(data.get("text") or ""))
        elif event == "reset":
            # The lane discarded what was streamed (a nudge, a rewrite).
            self.text = []
        elif event == "thinking":
            chunk = str(data.get("text") or "")
            room = THINKING_CAP - self.thinking_chars
            if room > 0:
                self.thinking.append(chunk[:room])
                self.thinking_chars += len(chunk[:room])
        elif event == "round" and data.get("steer"):
            self.steers.append(str(data["steer"]))
        elif event == "tool_call":
            cid = str(data.get("id") or "")
            name = str(data.get("name") or "")
            if data.get("status") == "started":
                self._started[cid] = (name, time.time(), data.get("args"))
            elif data.get("status") == "finished":
                began = self._started.pop(cid, None)
                ok = data.get("ok")
                if len(self.steps) < MAX_STEPS:
                    step: dict[str, Any] = {
                        "name": name,
                        "ok": bool(ok) if ok is not None else None,
                        "ms": int((time.time() - began[1]) * 1000) if began else None,
                    }
                    if isinstance(data.get("detail"), dict):
                        step["detail"] = data["detail"]
                    self.steps.append(step)
                if name == "exit_plan_mode" and ok is True:
                    # The plan the user approved: the call's own `plan`, else
                    # what the reply had said so far (Claude Code's
                    # ExitPlanMode takes no argument — the plan is the reply
                    # the user read above the card).
                    args = began[2] if began else None
                    plan = args.get("plan") if isinstance(args, dict) else None
                    approved = str(plan or "").strip() or "".join(self.text).strip()
                    self.approved_plans.append(approved)
                    self.approved_text = approved
        elif event == "approval_resolved" and data.get("decision") == "conversation":
            tool = str(data.get("tool") or "")
            if tool and tool not in self.granted:
                self.granted.append(tool)
        elif event == "done":
            self.done = data
        elif event == "error":
            self.error = data

    def lines(self, turn_id: str, status: str) -> list[dict[str, Any]]:
        """The steer notes (as the user's) and the reply line."""
        out: list[dict[str, Any]] = [
            {"role": "user", "content": note, "steer": True, "turnId": turn_id} for note in self.steers
        ]
        partial = "".join(self.text)
        line: dict[str, Any] = {"role": "assistant", "turnId": turn_id}
        if self.done is not None:
            done = self.done
            content = str(done.get("reply") or "")
            # THE PLAN IS PART OF THE CONVERSATION: the lane's reply is the
            # last round's words ("Plan approved — ready"), while the plan the
            # user approved was written in an earlier round. Kept on the line
            # so the transcript shows what was approved and the NEXT turn's
            # model (now in act mode) reads it in its history.
            plan = self.approved_text.strip()
            if plan and plan not in content:
                content = (plan + "\n\n" + content).strip()
            line.update({
                "content": content,
                "route": done.get("route"),
                "toolsUsed": done.get("tools_used") or [],
                "documents": done.get("documents") or [],
                "deniedTools": done.get("denied_tools") or [],
                "usage": done.get("usage"),
                "truncated": bool(done.get("truncated")),
                "folderRules": done.get("folder_rules") or [],
                "approved": done.get("approved") or [],
                "refit": done.get("refit"),
                "doors": done.get("doors") or [],
            })
            if done.get("trust") and done.get("trust") != "full":
                line["trust"] = done.get("trust")
                line["trustReason"] = done.get("trust_reason") or ""
                line["trustNote"] = done.get("trust_note") or ""
        elif self.error is not None:
            err = self.error
            words = " ".join(
                p for p in (str(err.get("title") or "").strip(), str(err.get("fix") or "").strip()) if p
            ) or str(err.get("detail") or "The reply failed.")
            line.update({"content": partial, "error": words, "interrupted": bool(partial)})
        else:
            if not partial.strip():
                return out  # stopped before a word: nothing to keep
            line.update({"content": partial, "interrupted": True})
            if status == "interrupted":
                line["error"] = _durable.INTERRUPTED_DETAIL
        if self.thinking:
            line["thinking"] = "".join(self.thinking)
        if self.steps:
            line["steps"] = self.steps
        line["timing"] = {
            "startedAt": int(self.started_at * 1000),
            "firstTokenAt": int(self.first_token_at * 1000) if self.first_token_at else None,
            "endedAt": int(time.time() * 1000),
        }
        out.append(line)
        return out


async def _apply_session_changes(engine: Any, thread_id: str, watch: _ReplyWatch) -> None:
    """An approved plan switches the session to act mode for the NEXT turn
    and keeps the plan; an "allow for this conversation" answer is kept for
    the next turns. Off the loop; never raises."""
    setup: dict[str, Any] = {}
    if watch.approved_plans:
        from ...harness.session_tools import plan_steps

        text = watch.approved_plans[-1]
        watch.approved_plans = []
        setup["mode"] = None  # act is stored as nothing
        setup["plan"] = {
            "text": text, "steps": plan_steps(text),
            "approved_at": utcnow().isoformat(),
        }
    if watch.granted:
        row = await asyncio.to_thread(S.get_row, engine, thread_id)
        have = list(_setup_of(row).get("granted_tools") or []) if row is not None else []
        merged = have + [g for g in watch.granted if g not in have]
        watch.granted = []
        if merged != have:
            setup["granted_tools"] = merged
    if not setup:
        return
    try:
        await asyncio.to_thread(S.update, engine, thread_id, setup=setup)
    except Exception:  # noqa: BLE001 — the turn never fails on its session record
        log.warning("updating build session %s failed", thread_id, exc_info=True)


async def recorded_turn(engine: Any, thread_id: str, turn_id: str, frames: Any):
    """``frames`` (the lane's SSE iterator), passed through unchanged, with
    the session kept up to date: an approved plan and a conversation grant
    land as they happen, and the reply line (plus any steer notes) is written
    when the turn ends — done, failed, stopped, or cut off by a shutdown."""
    watch = _ReplyWatch()
    status = "stopped"
    try:
        async with contextlib.aclosing(frames):
            async for chunk in frames:
                for event, data in _durable.parse_chunk(chunk):
                    watch.see(event, data)
                if watch.approved_plans or watch.granted:
                    await _apply_session_changes(engine, thread_id, watch)
                yield chunk
        status = "done" if watch.done is not None else "error" if watch.error is not None else "stopped"
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception as exc:  # noqa: BLE001 — re-raised; TurnLogs._drive logs it
        # The lane broke OUTSIDE its own error handling (it yields its own
        # errors as frames). The log gets an error frame from the driver;
        # the transcript must say so too, never end on the question with no
        # reply at all (Phase 4 review — "a turn never ends empty").
        if watch.done is None and watch.error is None:
            watch.error = {"detail": f"The reply stopped on an internal error ({type(exc).__name__})."}
        status = "error"
        raise
    finally:
        lines = watch.lines(turn_id, status)
        if lines:
            with contextlib.suppress(BaseException):
                await asyncio.shield(asyncio.to_thread(S.append_lines, engine, thread_id, lines))


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #


def register(app: FastAPI, d: Any) -> None:
    engine_of = lambda: d.platform.engine  # noqa: E731 — read live (tests swap the platform)

    def _session_or_404(thread_id: str) -> Any:
        row = S.get_row(engine_of(), thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        return row

    def _full_view(row: Any) -> dict[str, Any]:
        folder = S.folder_of(row)
        names = S.folder_names(engine_of(), [folder]) if folder else {}
        view = S.session_view(row, states=TURNS.thread_states(), names=names, full=True)
        try:
            queued = json.loads(getattr(row, "queued_json", "") or "[]")
        except (TypeError, ValueError):
            queued = []
        view["queued"] = [q for q in queued if isinstance(q, dict)] if isinstance(queued, list) else []
        view["turn_id"] = _running_turn(d, row.id) if view["running"] else ""
        return view

    @app.get("/build/sessions")
    def build_sessions(archived: str = "") -> dict[str, Any]:
        """Every IJ harness session grouped by its folder (newest first).
        ``archived=1`` includes the archived ones (each row says so)."""
        include = str(archived or "").strip().lower() in ("1", "true", "yes", "all")
        return S.list_grouped(engine_of(), include_archived=include, states=TURNS.thread_states())

    @app.post("/build/sessions", status_code=201)
    async def create_build_session(body: BuildSessionCreate) -> dict[str, Any]:
        """A new session working in ``folder``. The folder must be usable
        (``fs_policy.root_problem``: absolute, on this PC, allowed, and
        writable) — refused with the reason otherwise."""
        folder = S.resolved_folder(body.folder)
        problem = await asyncio.to_thread(S.folder_problem, folder)
        if problem:
            raise HTTPException(status_code=400, detail=f"This folder cannot be used: {problem}")
        mode = (body.mode or "").strip().lower()
        if mode and mode not in S.MODES:
            raise HTTPException(status_code=400, detail="mode must be 'act' or 'plan'")
        setup = {
            k: v for k, v in {
                "provider": body.provider, "model": body.model, "reasoning": body.reasoning,
                "approval_mode": body.approval_mode, "mode": mode,
            }.items() if v
        }
        row = await asyncio.to_thread(S.create, engine_of(), folder, body.title, setup)
        return await asyncio.to_thread(_full_view, row)

    @app.get("/build/sessions/{thread_id}")
    def get_build_session(thread_id: str) -> dict[str, Any]:
        """One session: its folder (and the folder's name), mode, plan,
        setup, transcript, queue, and the running turn's id (to rejoin with
        ``GET /chat/turns/{id}/stream``)."""
        return _full_view(_session_or_404(thread_id))

    @app.patch("/build/sessions/{thread_id}")
    def patch_build_session(thread_id: str, body: BuildSessionPatch) -> dict[str, Any]:
        """PARTIAL: title, model / provider / reasoning, approval mode, mode
        ("act" | "plan"), local only. The folder never changes. A mode change
        takes effect from the NEXT turn (a running turn keeps its tools)."""
        _session_or_404(thread_id)
        setup: dict[str, Any] = {}
        fields = body.model_fields_set
        for key in ("provider", "model", "reasoning", "approval_mode"):
            if key in fields:
                value = getattr(body, key)
                setup[key] = value if value else None
        if "mode" in fields and body.mode is not None:
            mode = body.mode.strip().lower()
            if mode not in S.MODES:
                raise HTTPException(status_code=400, detail="mode must be 'act' or 'plan'")
            setup["mode"] = "plan" if mode == S.MODE_PLAN else None
        if "local_only" in fields and body.local_only is not None:
            setup["local_only"] = True if body.local_only else None
        title = body.title if "title" in fields else None
        row = S.update(engine_of(), thread_id, title=title, setup=setup)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        return _full_view(row)

    @app.post("/build/sessions/{thread_id}/archive")
    async def archive_build_session(thread_id: str, body: ChatArchiveBody | None = None) -> Any:
        """Archive a session: the chat archive's rules (a session running
        something answers 409 and lists it, unless ``stop``), and the
        programs it started are stopped (``procs.end_session``)."""
        from .chat import archive_thread

        await asyncio.to_thread(_session_or_404, thread_id)
        return await archive_thread(
            engine_of(), thread_id, bool(body is not None and body.stop), noun="session",
        )

    @app.post("/build/sessions/{thread_id}/unarchive")
    def unarchive_build_session(thread_id: str) -> dict[str, Any]:
        from .chat import set_thread_archived

        _session_or_404(thread_id)
        out = set_thread_archived(engine_of(), thread_id, False)
        if out is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        return out

    @app.delete("/build/sessions/{thread_id}")
    def delete_build_session(thread_id: str) -> Any:
        """Delete a session (its transcript; its programs are stopped). A
        session still answering is refused: stop it first."""
        from .chat import delete_thread

        _session_or_404(thread_id)
        if TURNS.thread_states().get(thread_id, {}).get("running"):
            return JSONResponse(status_code=409, content={"detail": RUNNING_SENTENCE})
        if not delete_thread(engine_of(), thread_id):
            raise HTTPException(status_code=404, detail="no such Build session")
        return {"deleted": thread_id}

    @app.post("/build/folders/rename")
    def rename_build_folder(body: BuildFolderRename) -> dict[str, Any]:
        """The folder's DISPLAY name in Build. A Jarvis project's root shows
        the project's name, so renaming it renames the project. The folder
        on disk is never renamed."""
        try:
            return S.rename_folder(engine_of(), S.resolved_folder(body.path), body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/build/sessions/{thread_id}/turn")
    async def build_turn(thread_id: str, body: BuildTurnBody):
        """ONE build turn (SSE — the same frames as ``POST /chat/stream``).
        See the module docstring. A ``turn_id`` already running for this
        session is FOLLOWED, never started twice."""
        from .chat import _follow_response, _turn_logs

        platform = d.platform
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Write a message first.")
        if len(text) > MAX_TURN_CHARS:
            raise HTTPException(status_code=413, detail="That message is too long.")
        turn_id = (body.turn_id or "").strip()
        if turn_id and not _TURN_ID_RX.match(turn_id):
            raise HTTPException(status_code=400, detail="turn_id may hold letters, digits, '-', '_' and '.' only")
        logs = _turn_logs(d)
        if turn_id:
            existing = logs.get(turn_id)
            if existing is None:
                # NEVER RUN TWICE (Phase 4 review): this process forgets a
                # finished turn after FINISHED_KEEP_S, and a restart forgets
                # every one — but the RECORD stays (7 days). A turn id that is
                # on disk was already run: its frames are replayed (the
                # rejoin route's answer), never a second run that would apply
                # the same edits and commands again and append the question
                # twice. Off the loop.
                recorded = await asyncio.to_thread(_turn_record, platform.engine, turn_id)
                # A twin request may have started it during that read.
                existing = logs.get(turn_id)
                if existing is None and recorded is not None:
                    rec_thread, text = recorded
                    if rec_thread != thread_id:
                        raise HTTPException(status_code=409, detail="That turn belongs to another conversation.")
                    return _follow_response(_replay_once(text))
            if existing is not None:
                if existing.thread_id != thread_id:
                    raise HTTPException(status_code=409, detail="That turn belongs to another conversation.")
                return _follow_response(existing.follow())
        else:
            turn_id = "bt_" + secrets.token_hex(8)
        if thread_id in _STARTING or _running_turn(d, thread_id):
            return JSONResponse(
                status_code=409,
                content={"detail": RUNNING_SENTENCE, "turn_id": _running_turn(d, thread_id)},
            )
        _STARTING.add(thread_id)
        try:
            row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
            if row is None:
                raise HTTPException(status_code=404, detail="no such Build session")
            if getattr(row, "archived_at", None) is not None:
                raise HTTPException(status_code=409, detail=ARCHIVED_SENTENCE)
            folder = S.folder_of(row)
            problem = await asyncio.to_thread(S.folder_problem, folder)
            if problem:
                raise HTTPException(
                    status_code=409, detail=f"This session's folder cannot be used now: {problem}",
                )
            setup = _setup_of(row)
            from ...projects.locate import project_for_path

            project = await asyncio.to_thread(project_for_path, platform.engine, folder)
            # The transcript is up to 200 lines of JSON (replies with steps
            # and thinking): parsed OFF the loop, like every other read here.
            history = [ChatMessageBody(**m) for m in await asyncio.to_thread(S.history, row)]
            chat_body = ChatBody(
                messages=[*history, ChatMessageBody(role="user", content=text)],
                provider=str(setup.get("provider") or ""),
                model=str(setup.get("model") or ""),
                reasoning=str(setup.get("reasoning") or ""),
                approval_mode=str(setup.get("approval_mode") or ""),
                granted_tools=list(setup.get("granted_tools") or []),
                workspace_dir=folder,
                project_id=project.id if project is not None else "",
                thread_id=thread_id,
                turn_id=turn_id,
                # The Build screen draws the question cards: an app's question
                # and the model's own (ask_user).
                mcp_cards=True,
                attachments=list(body.attachments or []),
                local_only=setup.get("local_only") is True,
            )
            holder: dict[str, Any] = {}
            try:
                gen = await stream_chat_turn(
                    platform,
                    getattr(d, "_PERSONAS", {}),
                    chat_body,
                    should_stop=_durable.stop_predicate(lambda: holder.get("log")),
                    suggest_preferences=False,
                    harness="auto",
                    round_budget=build_round_budget(platform.config),
                    stay_in_chat=True,
                    session_id=S.session_id_for(thread_id),
                    plan_mode=S.mode_of(row) == S.MODE_PLAN,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            question = {"role": "user", "content": text, "turnId": turn_id}
            if body.attachments:
                question["attachmentPaths"] = list(body.attachments)
            saved = await asyncio.to_thread(S.append_lines, platform.engine, thread_id, [question])
            if not saved.get("ok"):
                with contextlib.suppress(Exception):
                    await gen.aclose()
                raise HTTPException(status_code=404, detail="no such Build session")
            lg = logs.start(turn_id, thread_id, recorded_turn(platform.engine, thread_id, turn_id, gen))
            holder["log"] = lg
        finally:
            _STARTING.discard(thread_id)
        return _follow_response(lg.follow())

    @app.post("/build/sessions/{thread_id}/answer")
    def answer_build_question(thread_id: str, body: BuildAnswerBody) -> dict[str, Any]:
        """The user's answer to a question (``ask_user``) this session's
        running turn is waiting on — or ``skip``. 404 for a question that is
        not waiting (answered, stopped, or another session's)."""
        from .. import mcp_turn

        ask = mcp_turn.find(body.ask_id)
        if (
            ask is None or ask.kind != "ask_user" or ask.answered
            or getattr(ask.turn, "owner", "") != thread_id
        ):
            raise HTTPException(status_code=404, detail="no such question waiting")
        if body.skip:
            settled = ask.resolve("skipped", None)
        else:
            settled = ask.resolve("answered", _clean_answer(body.answer))
        if not settled:
            raise HTTPException(status_code=404, detail="no such question waiting")
        return {"ok": True, "skipped": bool(body.skip)}
