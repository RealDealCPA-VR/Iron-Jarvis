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
    #: v1.368.0: the Iron-Proxy account to run on; "" = the vendor's Default.
    account: str = Field(default="", max_length=200)


class BuildSessionPatch(BaseModel):
    """PARTIAL: an omitted field keeps its value; ``""`` clears a model pick.
    ``account`` (v1.368.0): another account ("" or null = none: this PC's own
    login, exactly Phase 4); it may move the session to that account's vendor
    and model, and it cancels a "Resume when ready" wait."""

    title: str | None = Field(default=None, max_length=400)
    provider: str | None = None
    model: str | None = None
    reasoning: str | None = None
    approval_mode: str | None = None
    mode: str | None = None
    local_only: bool | None = None
    account: str | None = Field(default=None, max_length=200)


class BuildAccountChoice(BaseModel):
    """``POST /build/sessions/{id}/account`` (v1.368.0, "Choose account")."""

    model_config = {"populate_by_name": True}

    account: str = Field(min_length=1, max_length=200)
    #: Continue the cut-off work at once, as a new build turn.
    continue_: bool = Field(default=False, alias="continue")
    #: A model of that account's vendor ("" = keep / the vendor's default).
    model: str = Field(default="", max_length=200)


class BuildFolderRename(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str = Field(max_length=400)


class BuildTurnBody(BaseModel):
    text: str
    turn_id: str = ""
    attachments: list[str] = Field(default_factory=list, max_length=20)


class BuildRewindBody(BaseModel):
    """``POST /build/sessions/{id}/rewind`` (v1.370.0): rewind TO before the
    turn ``to`` (that turn and every later one)."""

    to: str = Field(min_length=1, max_length=80)


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
# the session's account (v1.368.0, IJ harness Phase 5)
# --------------------------------------------------------------------------- #

#: The question a continuation turn asks ("Resume when ready", "Choose
#: account"). The transcript marks it ``continuation`` (the screen hides it,
#: like the chat's hidden Continue turn); the model reads it as the user's.
CONTINUE_TEXT = (
    "Carry on with the task above from where it stopped. Do not repeat work that is "
    "already done; check the files first if you are not sure what was finished."
)
ACCOUNT_OFF = "Iron-Proxy is off; turn it on in Connections first."
ACCOUNT_UNKNOWN = (
    "There is no Claude Code or Codex account like that in Iron-Proxy — choose one from "
    "the account list."
)
ACCOUNT_TURNED_OFF = (
    "That account is turned off on the Iron-Proxy card — turn it on in Connections first."
)
NOTHING_TO_WAIT_FOR = (
    "This session's account is not waiting on a limit — send your message, or choose "
    "another account."
)
NO_ACCOUNT = "This session does not run on an Iron-Proxy account."


def _svc_of(platform: Any) -> Any:
    return getattr(platform, "iron_proxy", None)


def account_patch(platform: Any, setup: dict[str, Any], account_id: "str | None",
                  model: "str | None" = None) -> dict[str, Any]:
    """The setup changes that put a session on ``account_id`` — validated
    against the CACHED snapshot (never a call). ``""`` / None clears the
    account: with Iron-Proxy off that is this PC's own login (exactly Phase
    4); with Iron-Proxy on, the next turn stores the vendor's Default again
    (:func:`turn_account` — a session never runs "first free, rotating"). To
    use this PC's own login under Iron-Proxy, pick the account Iron-Proxy
    adopted for it. The account decides the vendor: a
    Claude Code account serves ``claude-cli``, a Codex account ``codex-cli``;
    another account of the SAME vendor keeps the model, a different vendor's
    gets ``model`` or that vendor's default. Any change cancels a "Resume when
    ready" wait and clears the old limit card. ``HTTPException`` in words."""
    from ...harness import build_accounts as BA

    if not account_id:
        return {"account": None, "account_name": None, "account_wait": None, "account_limit": None}
    svc = _svc_of(platform)
    if svc is None or not getattr(svc, "enabled", False):
        raise HTTPException(status_code=409, detail=ACCOUNT_OFF)
    if BA.snapshot(svc) is None:
        raise HTTPException(status_code=409, detail=BA.NOT_READ_NOTE)
    row = BA.find(svc, account_id)
    if row is None:
        raise HTTPException(status_code=400, detail=ACCOUNT_UNKNOWN)
    if row.get("status") == BA.OFF:
        raise HTTPException(status_code=409, detail=ACCOUNT_TURNED_OFF)
    patch: dict[str, Any] = {
        "account": row["id"], "account_name": row["name"], "provider": row["provider"],
        "account_wait": None, "account_limit": None,
    }
    if model:
        patch["model"] = model
    elif BA.vendor_of(platform, str(setup.get("provider") or "")) != row["vendor"]:
        patch["model"] = None  # that vendor's own default model
    return patch


def _with_initial_account(platform: Any, setup: dict[str, Any], account_id: str) -> dict[str, Any]:
    """A NEW session's setup with its account: ``account_id`` when asked for
    (validated; the model asked for is kept when it is that account's
    vendor's, another vendor's account gets its own default model), else the
    Default of the vendor its model uses. BLOCKING-safe (cached reads)."""
    from ...harness import build_accounts as BA

    out = dict(setup)
    if account_id:
        patch = account_patch(platform, out, account_id, None)
        out.update({k: v for k, v in patch.items() if v is not None})
        if "model" in patch and patch["model"] is None:
            out.pop("model", None)
        return out
    vendor = BA.vendor_of(platform, str(out.get("provider") or ""))
    row = BA.default_for(_svc_of(platform), vendor) if vendor else None
    if row is not None:
        out.update({"account": row["id"], "account_name": row["name"], "provider": row["provider"]})
    return out


def turn_account(platform: Any, thread_id: str, setup: dict[str, Any]) -> "tuple[Any, str]":
    """``(AccountPin | None, provider)`` for one build turn. BLOCKING (it may
    re-read Iron-Proxy's accounts once and store the session's account).

    * Iron-Proxy off and no account: ``(None, "")`` — exactly Phase 4.
    * no account yet, Iron-Proxy on: the vendor's Default (the badged one)
      is STORED on the session and pinned — a session never runs
      "first free, rotating" (that would land on an account nobody picked);
      no account for the vendor (or a local model / an API key): Phase 4.
    * an account: pinned; the provider is that account's vendor's CLI.
      Iron-Proxy off: a 409 in words (the session's account cannot be
      reached; nothing runs on another login)."""
    from ...harness import build_accounts as BA
    from ...iron_proxy.pin import AccountPin

    svc = _svc_of(platform)
    enabled = bool(svc is not None and getattr(svc, "enabled", False))
    account = str(setup.get("account") or "")
    name = str(setup.get("account_name") or "")
    if not account:
        if not enabled:
            return None, ""
        vendor = BA.vendor_of(platform, str(setup.get("provider") or ""))
        if not vendor:
            return None, ""
        if BA.snapshot(svc) is None:
            # Not read yet (a turn right after boot, before the watch loop's
            # first cycle): locate Iron-Proxy and read its accounts once, so
            # the session gets its Default instead of an unpinned "first free".
            with contextlib.suppress(Exception):
                if svc.check():
                    svc.refresh_accounts()
        row = BA.default_for(svc, vendor)
        if row is None:
            return None, ""
        S.update(platform.engine, thread_id, setup={
            "account": row["id"], "account_name": row["name"], "provider": row["provider"],
        })
        return AccountPin(profile_id=row["id"], ij_provider=row["provider"], title=row["name"]), row["provider"]
    if not enabled:
        who = f' "{name}"' if name else ""
        raise HTTPException(
            status_code=409,
            detail=f"This session runs on the account{who}, and Iron-Proxy is off — turn it on "
                   "in Connections to continue.",
        )
    row = BA.find(svc, account)
    if row is not None:
        provider, name = row["provider"], row["name"] or name
    else:
        vendor = BA.vendor_of(platform, str(setup.get("provider") or ""))
        provider = BA.VENDORS[vendor]["provider"] if vendor else ""
        if not provider:
            raise HTTPException(status_code=409, detail="Choose this session's account again.")
    return AccountPin(profile_id=account, ij_provider=provider, title=name), provider


def check_account(platform: Any, setup: dict[str, Any]) -> "dict[str, Any] | None":
    """Can the session's account serve RIGHT NOW? A pinned pick through
    Iron-Proxy (the authority: it clears a park whose reset has passed).
    None = usable; else the limit card (``kind``, ``resets_at``) — or
    ``{"kind": "unknown"}`` when Iron-Proxy could not say. BLOCKING."""
    from ...iron_proxy import accounts as _acc
    from ...iron_proxy.pin import reset_pin, set_pin

    if not setup.get("account"):
        return {"kind": "unknown"}
    try:
        pin_account, provider = turn_account(platform, "", setup)
    except HTTPException:
        return {"kind": "unknown"}
    if pin_account is None:
        return None
    token = set_pin(pin_account)
    try:
        _acc.lease(provider)
        return None
    except Exception:  # noqa: BLE001 — the refusal's facts are on the pin
        return pin_account.limit or {"kind": "unknown"}
    finally:
        reset_pin(token)


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
        #: The step list of the last plan_update call that ran this turn —
        #: an approved plan written as prose keeps THESE as its steps
        #: (v1.367.0 ship: the review saw a prose plan keep none).
        self.plan_update_steps: list[dict[str, str]] = []
        #: v1.368.0: the account-limit card this turn ended on (the
        #: ``account_limit`` frame's payload), kept on the reply line.
        self.account_limit: dict[str, Any] | None = None

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
                if name == "plan_update" and ok is True:
                    from ...harness.tools import parse_plan

                    args = began[2] if began else None
                    try:
                        steps, _ = parse_plan(args if isinstance(args, dict) else {})
                    except ValueError:
                        steps = []
                    if steps:
                        self.plan_update_steps = [
                            {"step": st["step"], "status": "pending"} for st in steps
                        ]
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
        if self.account_limit:
            line["accountLimit"] = self.account_limit
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
            "text": text, "steps": plan_steps(text) or list(watch.plan_update_steps),
            "approved_at": utcnow().isoformat(),
        }
    if watch.granted:
        try:
            row = await asyncio.to_thread(S.get_row, engine, thread_id)
        except Exception:  # noqa: BLE001 — "never raises" (Phase 4 review): a
            # busy database here used to escape into recorded_turn and end
            # the live turn mid-stream. The grant stays pending (the next
            # frame tries again); an approved plan above is still written.
            log.warning("reading build session %s failed", thread_id, exc_info=True)
        else:
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


#: Frames that END a turn: the account-limit card goes out right BEFORE one.
_TERMINAL_EVENTS = ("done", "error")

#: The receipt's words when a call was sent again without another account's
#: thinking (v1.368.0).
THINKING_DROPPED_NOTE = (
    "This account would not accept the thinking from earlier replies, so the "
    "request was sent again without it."
)


def account_receipt(pin: Any) -> dict[str, Any]:
    """``route.account`` of a build turn that ran on an account: its id and
    NAME (the Iron-Proxy title — never an e-mail), and the note when the call
    was sent again without earlier thinking (v1.368.0)."""
    out: dict[str, Any] = {"id": pin.profile_id, "name": pin.title or ""}
    if getattr(pin, "thinking_dropped", False):
        out["note"] = THINKING_DROPPED_NOTE
    return out


def _with_account(chunk: str, pin: Any) -> str:
    """The ``done`` frame with ``route.account`` added (the receipt names the
    account). Any other chunk, or one that does not parse, passes unchanged."""
    frames = _durable.parse_chunk(chunk)
    if len(frames) != 1 or frames[0][0] != "done":
        return chunk
    try:
        data = json.loads(frames[0][1] or "{}")
    except ValueError:
        return chunk
    if not isinstance(data, dict):
        return chunk
    route = data.get("route") if isinstance(data.get("route"), dict) else {}
    data["route"] = {**route, "account": account_receipt(pin)}
    return _durable.sse("done", data)


#: The second half of a limit's words (the plan's card: nothing is lost).
LIMIT_KEPT = "Your conversation and the work so far are kept."


def limit_words(limit: "dict[str, Any] | None") -> str:
    """The account's own sentence for a limit card: why it cannot serve and
    what to do (wait for the reset, sign it in, or choose another account) —
    the SAME words the pinned lease refused with (``accounts.pinned_words``).
    "" when the card names no provider Iron Jarvis knows."""
    from ...iron_proxy import accounts as _acc

    if not isinstance(limit, dict):
        return ""
    spec = _acc.PROVIDERS.get(str(limit.get("provider") or ""))
    if spec is None:
        return ""
    return _acc.pinned_words(
        spec, str(limit.get("account_name") or "the session's account"),
        str(limit.get("kind") or "other"), limit.get("resets_at"),
    )


def _with_limit_words(chunk: str, limit: "dict[str, Any]") -> str:
    """An ``error`` frame for an account that could not serve, worded by
    Iron Jarvis: ``title`` = :func:`limit_words`, ``fix`` = nothing is lost.
    The generic "pick another model" fix — and Iron-Proxy's "pick without a
    profile id" — would be the wrong advice inside a session the user pinned.
    ``detail`` (the technical text) is unchanged. Any other chunk passes."""
    frames = _durable.parse_chunk(chunk)
    if len(frames) != 1 or frames[0][0] != "error":
        return chunk
    try:
        data = json.loads(frames[0][1] or "{}")
    except ValueError:
        return chunk
    said = limit_words(limit)
    if not isinstance(data, dict) or not said:
        return chunk
    data["title"] = said
    data["fix"] = LIMIT_KEPT
    return _durable.sse("error", data)


async def _note_account_limit(platform: Any, thread_id: str, turn_id: str,
                              limit: "dict[str, Any] | None", *, done: bool) -> None:
    """Keep the session's limit card across a reload (``setup.account_limit``)
    — or clear it when a turn on the account finished — and tell the bell.
    Off the loop; never raises."""
    engine = platform.engine
    try:
        if limit:
            record = {
                **{k: v for k, v in limit.items() if v is not None},
                "at": utcnow().isoformat(), "turn_id": turn_id,
            }
            await asyncio.to_thread(S.update, engine, thread_id, setup={"account_limit": record})
            # The account list reads the CACHED snapshot: refresh it now (one
            # profiles + states read, off the loop), so the chip and the list
            # say "Limit reached" at once instead of at the next watch cycle.
            svc = _svc_of(platform)
            if svc is not None and hasattr(svc, "refresh_accounts"):
                await asyncio.to_thread(svc.refresh_accounts)
            row = await asyncio.to_thread(S.get_row, engine, thread_id)
            bus = getattr(platform, "event_bus", None)
            if bus is not None:
                from ...core.events import EventType

                await bus.publish(EventType.BUILD_ACCOUNT_LIMIT, {
                    "thread_id": thread_id,
                    "title": getattr(row, "title", "") or "",
                    "account_name": limit.get("account_name") or "",
                    "vendor": limit.get("vendor") or "",
                    "kind": limit.get("kind") or "",
                    "resets_at": limit.get("resets_at"),
                }, session_id=S.session_id_for(thread_id))
        elif done:
            # The account served the turn (it finished, or the user's own turn
            # ran on it and then failed for another reason or was stopped):
            # the card goes, and so does a "Resume when ready" wait (the user
            # carried on themselves — a later continuation would answer a
            # question nobody asked).
            row = await asyncio.to_thread(S.get_row, engine, thread_id)
            setup = _setup_of(row) if row is not None else {}
            if setup.get("account_limit") or setup.get("account_wait"):
                await asyncio.to_thread(S.update, engine, thread_id, setup={
                    "account_limit": None, "account_wait": None,
                })
    except Exception:  # noqa: BLE001 — the turn never fails on its session record
        log.warning("recording build session %s's account limit failed", thread_id, exc_info=True)


async def recorded_turn(engine: Any, thread_id: str, turn_id: str, frames: Any, *,
                        pin: Any = None, platform: Any = None):
    """``frames`` (the lane's SSE iterator), passed through unchanged, with
    the session kept up to date: an approved plan and a conversation grant
    land as they happen, and the reply line (plus any steer notes) is written
    when the turn ends — done, failed, stopped, or cut off by a shutdown.

    v1.368.0: ``pin`` — the turn's ONE account (``iron_proxy.pin``). When it
    could not serve (parked, needs sign-in), a typed ``account_limit`` frame
    goes out right BEFORE the turn's last frame and the card is kept on the
    session; the ``done`` frame's ``route.account`` names the account."""
    watch = _ReplyWatch()
    status = "stopped"
    limit_sent = False
    try:
        async with contextlib.aclosing(frames):
            async for chunk in frames:
                parsed = _durable.parse_chunk(chunk)
                if pin is not None and any(ev in _TERMINAL_EVENTS for ev, _ in parsed):
                    if pin.limit and not limit_sent:
                        limit_sent = True
                        card = {**pin.limit, "turn_id": turn_id}
                        watch.account_limit = card
                        yield _durable.sse("account_limit", card)
                    if pin.limit:
                        chunk = _with_limit_words(chunk, pin.limit)
                    chunk = _with_account(chunk, pin)
                    parsed = _durable.parse_chunk(chunk)
                for event, data in parsed:
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
        # The account SERVED this turn (a pinned lease succeeded): the card
        # and any "Resume when ready" wait are over — whether the turn
        # finished, failed for another reason, or the user stopped it. A later
        # continuation would answer a question nobody asked (the user is
        # driving the session). A turn the account parked ends on its own
        # card instead (``watch.account_limit`` wins below), and a turn cut
        # off by a shutdown ("interrupted") keeps the wait (v1.368.0 review).
        served = status == "done" or (
            pin is not None and status in ("error", "stopped") and bool(getattr(pin, "leased", None))
        )
        if pin is not None and platform is not None and (watch.account_limit or served):
            with contextlib.suppress(BaseException):
                await asyncio.shield(_note_account_limit(
                    platform, thread_id, turn_id, watch.account_limit, done=served,
                ))
        # v1.370.0 (Phase 7): the EXPERIENCE LIBRARY may reflect on a turn
        # that FINISHED — in the background, after the reply line is written
        # (it reads the ledger to see whether the turn did real work). It
        # never delays or fails the turn.
        if status == "done" and platform is not None:
            from ...harness import library as _library

            with contextlib.suppress(Exception):
                _library.schedule(platform, thread_id, turn_id)


# --------------------------------------------------------------------------- #
# starting a build turn (the route, "Choose account", "Resume when ready")
# --------------------------------------------------------------------------- #


class SessionBusy(Exception):
    """The session already has a turn running (or starting)."""


class WaitEnded(Exception):
    """A "Resume when ready" continuation found its wait gone when it was
    about to start: the user cancelled it, chose another account, or another
    pass took it. Nothing was started."""


async def start_build_turn(d: Any, thread_id: str, text: str, turn_id: str, *,
                           attachments: "list[str] | None" = None,
                           continuation: bool = False,
                           resume_of: "dict[str, Any] | None" = None) -> Any:
    """Start ONE named, daemon-owned build turn and return its ``TurnLog``
    (follow it with ``lg.follow()``; nobody has to). The question is written
    when the turn starts, the reply when it ends (:func:`recorded_turn`).
    ``continuation``: the daemon's own "carry on" question (the transcript
    marks it). Raises :class:`SessionBusy`, or ``HTTPException`` in words
    (404 no session, 409 archived / folder / account unreachable, 400 a lane
    refusal).

    v1.368.0: the turn runs on the session's ONE account (:func:`turn_account`)
    — an :class:`~iron_jarvis.iron_proxy.pin.AccountPin` is armed while the
    turn's driver task is created (the task copies the context), so every
    lease in the turn is a pinned pick of that account and the router holds
    the turn on its provider.

    ``resume_of`` (the wait loop only): the "Resume when ready" wait this
    continuation answers. It is TAKEN — checked and removed with the old
    limit card in one locked write — right before the question is written,
    so a Cancel or a Choose account that landed while the account was being
    re-checked starts nothing (:class:`WaitEnded`), and the continuation's
    own limit card can never be wiped by a clear that ran after it."""
    from .chat import _turn_logs
    from ...iron_proxy.pin import reset_pin, set_pin

    platform = d.platform
    logs = _turn_logs(d)
    if thread_id in _STARTING or _running_turn(d, thread_id):
        raise SessionBusy(thread_id)
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
        pin, account_provider = await asyncio.to_thread(turn_account, platform, thread_id, setup)
        from ...projects.locate import project_for_path

        project = await asyncio.to_thread(project_for_path, platform.engine, folder)
        # The transcript is up to 200 lines of JSON (replies with steps
        # and thinking): parsed OFF the loop, like every other read here.
        # Role + content only: no native carrier (no thinking, no signature)
        # ever crosses into a turn — so a session MOVES between accounts and
        # vendors with nothing another account would refuse (v1.368.0).
        history = [ChatMessageBody(**m) for m in await asyncio.to_thread(S.history, row)]
        chat_body = ChatBody(
            messages=[*history, ChatMessageBody(role="user", content=text)],
            provider=account_provider or str(setup.get("provider") or ""),
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
            attachments=list(attachments or []),
            local_only=setup.get("local_only") is True,
        )
        holder: dict[str, Any] = {}
        token = set_pin(pin) if pin is not None else None
        try:
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
            if resume_of is not None:
                took = await asyncio.to_thread(
                    S.take_wait, platform.engine, thread_id,
                    str(resume_of.get("armed_at") or ""), str(resume_of.get("account") or ""),
                )
                if not took:
                    with contextlib.suppress(Exception):
                        await gen.aclose()
                    raise WaitEnded(thread_id)
            question: dict[str, Any] = {"role": "user", "content": text, "turnId": turn_id}
            if attachments:
                question["attachmentPaths"] = list(attachments)
            if continuation:
                question["continuation"] = True
            saved = await asyncio.to_thread(S.append_lines, platform.engine, thread_id, [question])
            if not saved.get("ok"):
                with contextlib.suppress(Exception):
                    await gen.aclose()
                raise HTTPException(status_code=404, detail="no such Build session")
            lg = logs.start(turn_id, thread_id, recorded_turn(
                platform.engine, thread_id, turn_id, gen, pin=pin, platform=platform,
            ))
        finally:
            if token is not None:
                reset_pin(token)
        lg.surface = "build"  # the waiting row says it is not a chat
        holder["log"] = lg
    finally:
        _STARTING.discard(thread_id)
    return lg


# --------------------------------------------------------------------------- #
# "Resume when ready": the durable wait and its loop (v1.368.0)
# --------------------------------------------------------------------------- #

#: The wait loop's own pace (it also wakes when a wait is armed).
WAIT_TICK_S = 60.0
#: After the reset, an account that is still not usable is re-checked this
#: often ("and every few minutes after"), and a wait with no reset time too.
WAIT_RECHECK_S = 300.0

#: (loop, Event) the wait loop sleeps on — set by :func:`wake_wait_loop`.
#: Holds no client and no live service (the v1.367.0 test-isolation rule).
_WAKE: dict[str, Any] = {}


def wake_wait_loop() -> None:
    """Make the wait loop look now (a wait was armed). Thread-safe; a no-op
    when no loop runs."""
    loop, event = _WAKE.get("loop"), _WAKE.get("event")
    if loop is None or event is None:
        return
    try:
        if loop.is_running():
            loop.call_soon_threadsafe(event.set)
    except RuntimeError:  # the loop closed between the check and the call
        pass


def _parse_iso(value: Any):
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def wait_record(setup: dict[str, Any], resets_at: Any = None) -> dict[str, Any]:
    """The durable wait stored on the session (``setup.account_wait``): the
    account, the reset to wait for, when it was armed and when to look next
    (the reset, or now when none is known)."""
    now = utcnow()
    reset = _parse_iso(resets_at) if resets_at else None
    nxt = reset if reset is not None and reset > now else now
    return {
        "account": str(setup.get("account") or ""),
        "account_name": str(setup.get("account_name") or ""),
        "resets_at": reset.isoformat() if reset is not None else "",
        "armed_at": now.isoformat(),
        "next_check_at": nxt.isoformat(),
    }


async def resume_due(d: Any, *, now: Any = None) -> list[str]:
    """ONE pass of the wait loop: every session whose "Resume when ready" wait
    is due (its reset passed, or the next re-check) has its account checked
    (a pinned pick); a usable account CONTINUES the same conversation as a new
    named build turn, clears the wait and tells the bell (``build.resumed``);
    a still-parked one moves the next check to its new reset (or a few
    minutes on). A session that is running, archived, or moved to another
    account is skipped / its wait cleared. Returns the thread ids resumed.
    Survives a restart: the waits live on the sessions."""
    from datetime import timedelta

    from ...core.events import EventType

    platform = d.platform
    svc = _svc_of(platform)
    if svc is None or not getattr(svc, "enabled", False):
        return []
    engine = platform.engine
    waits = await asyncio.to_thread(S.waiting_sessions, engine)
    when = now or utcnow()
    resumed: list[str] = []
    for thread_id, setup in waits:
        wait = setup.get("account_wait") or {}
        due = _parse_iso(wait.get("next_check_at")) or _parse_iso(wait.get("resets_at")) or when
        if due > when:
            continue
        if thread_id in _STARTING or _running_turn(d, thread_id):
            continue
        if not setup.get("account") or wait.get("account") != setup.get("account"):
            # The session moved to another account: this wait is not its.
            await asyncio.to_thread(S.update, engine, thread_id, setup={"account_wait": None})
            continue
        limit = await asyncio.to_thread(check_account, platform, setup)
        if limit is None:
            turn_id = "bt_" + secrets.token_hex(8)
            try:
                # The wait is TAKEN inside the start (one locked write, right
                # before the question): the check above can take seconds, and
                # a Cancel / Choose account that landed meanwhile must start
                # nothing — and no clear may run after the continuation began
                # (it would wipe the card that turn's own limit wrote).
                lg = await start_build_turn(d, thread_id, CONTINUE_TEXT, turn_id, continuation=True,
                                            resume_of=wait)
            except (SessionBusy, WaitEnded):
                continue
            except HTTPException as exc:
                log.info("resuming build session %s did not start: %s", thread_id, exc.detail)
                continue
            row = await asyncio.to_thread(S.get_row, engine, thread_id)
            bus = getattr(platform, "event_bus", None)
            if bus is not None:
                await bus.publish(EventType.BUILD_RESUMED, {
                    "thread_id": thread_id,
                    "title": getattr(row, "title", "") or "",
                    "account": str(setup.get("account") or ""),
                    "account_name": str(setup.get("account_name") or ""),
                    "turn_id": lg.turn_id,
                }, session_id=S.session_id_for(thread_id))
            resumed.append(thread_id)
            continue
        if limit.get("resumable") is False:
            # The account is gone: no reset will bring it back. The card
            # says so (choose another account); the wait ends.
            await asyncio.to_thread(S.update, engine, thread_id, setup={
                "account_wait": None,
                "account_limit": {**{k: v for k, v in limit.items() if v is not None},
                                  "at": when.isoformat()},
            })
            continue
        reset = _parse_iso(limit.get("resets_at"))
        nxt = reset if reset is not None and reset > when else when + timedelta(seconds=WAIT_RECHECK_S)
        new_wait = {**wait, "next_check_at": nxt.isoformat()}
        if reset is not None:
            new_wait["resets_at"] = reset.isoformat()
        await asyncio.to_thread(S.update, engine, thread_id, setup={"account_wait": new_wait})
    return resumed


async def account_wait_loop(d: Any, on_tick: Any) -> None:
    """The lifespan loop behind "Resume when ready": a pass every
    :data:`WAIT_TICK_S` (and at once when a wait is armed), each pass
    reporting ITS OWN outcome on ``loop_health["build_accounts"]`` (armed is
    not healthy). Iron-Proxy off: the waits stay, nothing is checked."""
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    _WAKE.update(loop=loop, event=event)
    try:
        while True:
            try:
                await resume_due(d)
                on_tick(True, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
                on_tick(False, exc)
            event.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=WAIT_TICK_S)
    finally:
        if _WAKE.get("event") is event:
            _WAKE.clear()


# --------------------------------------------------------------------------- #
# rewind (v1.370.0, IJ harness Phase 7)
# --------------------------------------------------------------------------- #

REWIND_RUNNING = "This session is still answering. Stop it before you rewind it."


async def rewind_session(d: Any, thread_id: str, to: str) -> Any:
    """Rewind the session TO before turn ``to`` (``harness/rewind``): every
    file its turns changed from there on is put back through the undo journal
    — newest first, each through ``POST /undo``'s own code — and the
    conversation is cut back to before that turn's question. ALL OR NOTHING:
    a file that cannot be put back (changed since, no saved copy) is named
    and NOTHING is touched (409 ``problems``). Refused while a turn runs or
    starts (the session is held for the rewind, so none can start during it).
    Returns the answer dict, or a ``JSONResponse`` for a refusal that carries
    more than a sentence."""
    from ...core.events import EventType
    from ...harness import rewind as R
    from .undo import perform_undo

    platform = d.platform
    if thread_id in _STARTING or _running_turn(d, thread_id):
        return JSONResponse(status_code=409, content={"detail": REWIND_RUNNING})
    _STARTING.add(thread_id)
    try:
        row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        if getattr(row, "archived_at", None) is not None:
            raise HTTPException(status_code=409, detail=ARCHIVED_SENTENCE)
        try:
            plan = await asyncio.to_thread(
                R.plan, platform.engine, platform.config.home, platform.registry, thread_id, to,
            )
        except R.RewindError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        if plan.problems:
            return JSONResponse(status_code=409, content={
                "detail": R.problems_sentence(plan.problems), "rewound": False, **plan.view(),
            })
        restored: list[str] = []
        for action_id in plan.actions:
            try:
                await perform_undo(d, action_id)
            except HTTPException as exc:
                # The disk moved between the plan and this revert (a window
                # of milliseconds). Stop HERE and say what already went back;
                # the conversation is left as it was.
                files = ", ".join(plan.action_files.get(action_id) or []) or "a change"
                done = sorted({f for a in restored for f in plan.action_files.get(a) or []})
                return JSONResponse(status_code=409, content={
                    "detail": (
                        f"Rewinding stopped part way: {files} could not be put back ({exc.detail}). "
                        + (f"Already put back: {', '.join(done)}. " if done else "Nothing was put back. ")
                        + "The conversation was not changed."
                    ),
                    "rewound": False, "partial": bool(restored),
                    "restored": done, "not_restored": plan.action_files.get(action_id) or [],
                    **{k: v for k, v in plan.view().items() if k not in ("files",)},
                })
            restored.append(action_id)
        cut = await asyncio.to_thread(
            S.cut_transcript, platform.engine, thread_id, to, plan.keep, plan.cut_at,
        )
        if not cut.get("ok"):
            return JSONResponse(status_code=409, content={
                "detail": (
                    "The files were put back, but the conversation could not be cut back: "
                    f"{cut.get('reason') or 'it could not be saved'}."
                ),
                "rewound": False, "partial": True,
                "restored": [f["rel"] for f in plan.files], **plan.view(),
            })
        bus = getattr(platform, "event_bus", None)
        if bus is not None:
            with contextlib.suppress(Exception):
                await bus.publish(EventType.BUILD_REWOUND, {
                    "thread_id": thread_id, "to": to, "turns": len(plan.turns),
                    "files": len(plan.files),
                }, session_id=S.session_id_for(thread_id))
        return {
            "rewound": True, **plan.view(),
            "note": R.commands_note(plan.commands),
            "messages": cut.get("count", 0),
            "mode": cut.get("mode", S.MODE_ACT),
            "updated_at": cut.get("updated_at"),
        }
    finally:
        _STARTING.discard(thread_id)


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
        # v1.368.0: the session's account (with its live status, from the
        # CACHED snapshot) and which vendor its model belongs to ("" = a
        # local model or an API key: no account; the header names the place).
        from ...harness import build_accounts as BA

        setup = _setup_of(row)
        svc = _svc_of(d.platform)
        view["account"] = BA.session_account(
            svc, str(setup.get("account") or ""), str(setup.get("account_name") or ""),
        )
        view["account_vendor"] = (
            (view["account"] or {}).get("vendor")
            or BA.vendor_of(d.platform, str(setup.get("provider") or ""))
        )
        return view

    def _vendor_switch(setup: dict[str, Any], provider: "str | None") -> dict[str, Any]:
        """A model pick on ANOTHER vendor moves the session to that vendor's
        Default account (or, with none / a local model / an API key, to no
        account); the same vendor keeps the account. Cached reads only."""
        from ...harness import build_accounts as BA

        platform = d.platform
        new_vendor = BA.vendor_of(platform, str(provider or ""))
        if not setup.get("account"):
            return {}
        held = BA.find(_svc_of(platform), str(setup.get("account")))
        old_vendor = (held or {}).get("vendor") or BA.vendor_of(platform, str(setup.get("provider") or ""))
        if new_vendor == old_vendor:
            return {}
        row = BA.default_for(_svc_of(platform), new_vendor) if new_vendor else None
        if row is None:
            return {"account": None, "account_name": None, "account_wait": None, "account_limit": None}
        return {"account": row["id"], "account_name": row["name"], "account_wait": None,
                "account_limit": None}

    @app.get("/build/accounts")
    def build_accounts() -> dict[str, Any]:
        """The account list behind a session's account chip (v1.368.0):
        ``{claude: [...], codex: [...], iron_proxy: {enabled, running,
        needs_update, note}}`` — each account ``{id, name, vendor,
        vendor_label, provider, default, plan, plan_label, status}`` with
        ``status`` ``ready`` | ``limit`` (+ ``resets_at``, ``limit_kind``) |
        ``needs_sign_in`` | ``usage`` (+ ``usage: {percent, window,
        window_mins, resets_at}``). From the CACHED snapshot — the chip never
        makes Iron-Proxy run every CLI's status command (v1.302.0); a Claude
        account whose plan Iron-Proxy does not know yet has it read in the
        background (at most every 10 minutes)."""
        from ...harness import build_accounts as BA

        svc = _svc_of(d.platform)
        view = BA.accounts_view(svc)
        BA.kick_plan_reads(svc, view["claude"])
        return view

    @app.post("/build/accounts/{account_id}/default")
    async def make_default_account(account_id: str) -> dict[str, Any]:
        """"Make default": this account goes FIRST in its vendor's order —
        Iron-Proxy's own order, changed through the same reorder the
        Connections card uses. Answers the account list."""
        from ...harness import build_accounts as BA
        from ...iron_proxy.client import IronProxyError

        svc = _svc_of(d.platform)
        if svc is None or not getattr(svc, "enabled", False):
            raise HTTPException(status_code=409, detail=ACCOUNT_OFF)
        snap = BA.snapshot(svc)
        if snap is None:
            raise HTTPException(status_code=409, detail=BA.NOT_READ_NOTE)
        row = BA.find(svc, account_id)
        if row is None:
            raise HTTPException(status_code=404, detail=ACCOUNT_UNKNOWN)
        proxy = BA.VENDORS[row["vendor"]]["proxy"]
        profiles, _states = snap
        ids = [str(p.get("id")) for p in sorted(
            (p for p in profiles if p.get("provider") == proxy), key=lambda p: p.get("order") or 0,
        )]
        ordered = [account_id] + [i for i in ids if i != account_id]
        client = await asyncio.to_thread(svc.client)
        if client is None:
            raise HTTPException(status_code=409, detail=svc.status().get("error") or BA.NOT_READ_NOTE)
        try:
            await asyncio.to_thread(client.reorder, proxy, ordered)
        except IronProxyError as exc:
            from .iron_proxy import _said

            raise HTTPException(status_code=409 if exc.status in (None, 401) else 502,
                                detail=_said(exc)) from None
        await asyncio.to_thread(svc.refresh_accounts)
        return BA.accounts_view(svc)

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
        # v1.368.0: the session's ONE account — the one asked for, else the
        # Default of the vendor its model uses (the account the list badges
        # Default, whatever its status). Iron-Proxy off / no account for that vendor / a local model
        # or an API key: none (exactly Phase 4). Off the loop: the vendor of a
        # keyless API provider is read through provider availability.
        setup = await asyncio.to_thread(_with_initial_account, d.platform, setup, body.account.strip())
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
        current = _setup_of(_session_or_404(thread_id))
        setup: dict[str, Any] = {}
        fields = body.model_fields_set
        for key in ("provider", "model", "reasoning", "approval_mode"):
            if key in fields:
                value = getattr(body, key)
                setup[key] = value if value else None
        # v1.368.0: the account decides the vendor. An explicit account wins
        # (validated against the cached snapshot); a model pick on another
        # vendor moves the session to that vendor's Default account.
        if "account" in fields:
            patch = account_patch(
                d.platform, current, (body.account or "").strip(),
                (body.model or None) if "model" in fields else None,
            )
            if "model" in fields and patch.get("account"):
                patch.pop("model", None)  # the user's own pick (set above) wins
            setup.update(patch)
        elif "provider" in fields:
            setup.update(_vendor_switch(current, body.provider))
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
        out = await archive_thread(
            engine_of(), thread_id, bool(body is not None and body.stop), noun="session",
        )
        if isinstance(out, dict):
            # v1.368.0 review: an archived session's "Resume when ready" wait
            # ENDS — the wait loop skips archived sessions, so a wait left on
            # the row came due the moment the session was unarchived, and
            # the daemon carried on with work the user had put away.
            await asyncio.to_thread(S.update, engine_of(), thread_id, setup={"account_wait": None})
        return out

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

    @app.get("/build/sessions/{thread_id}/rewind")
    async def build_rewind_points(thread_id: str, to: str = "") -> dict[str, Any]:
        """v1.370.0: without ``to``, every turn of the session as a point it
        can be rewound to (``{checkpoints: [{turn_id, question, at,
        continuation, files, commands}]}``, oldest first); with ``to``, what
        rewinding there would do (files, problems, commands) — nothing is
        touched."""
        from ...harness import rewind as R

        platform = d.platform
        try:
            if to:
                plan = await asyncio.to_thread(
                    R.plan, platform.engine, platform.config.home, platform.registry, thread_id, to,
                )
                return {**plan.view(), "note": R.commands_note(plan.commands),
                        "detail": R.problems_sentence(plan.problems) if plan.problems else ""}
            points = await asyncio.to_thread(R.checkpoints, platform.engine, thread_id)
        except R.RewindError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        return {"checkpoints": points}

    @app.post("/build/sessions/{thread_id}/rewind")
    async def build_rewind(thread_id: str, body: BuildRewindBody) -> Any:
        """Rewind the session to before turn ``to`` — files and conversation
        (:func:`rewind_session`)."""
        return await rewind_session(d, thread_id, body.to.strip())

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
        try:
            lg = await start_build_turn(d, thread_id, text, turn_id,
                                        attachments=list(body.attachments or []))
        except SessionBusy:
            return JSONResponse(
                status_code=409,
                content={"detail": RUNNING_SENTENCE, "turn_id": _running_turn(d, thread_id)},
            )
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

    # ------------------------------------------------------------------ #
    # the session's account (v1.368.0, IJ harness Phase 5)
    # ------------------------------------------------------------------ #

    @app.post("/build/sessions/{thread_id}/account")
    async def choose_build_account(thread_id: str, body: BuildAccountChoice) -> Any:
        """"Choose account": put the session on ``account`` (validated
        against the cached snapshot; another vendor's account moves the model
        to that vendor's — ``model``, else its default), cancel a "Resume
        when ready" wait, and with ``continue: true`` carry on with the
        cut-off work at once as a new build turn on that account (follow it
        with ``GET /chat/turns/{turn_id}/stream``). The conversation is the
        session's own record (role + content), so it moves between accounts
        and vendors as it is — no thinking and no native blocks cross.
        Answers ``{session, turn_id}`` (``turn_id`` "" without continue)."""
        platform = d.platform
        if thread_id in _STARTING or _running_turn(d, thread_id):
            return JSONResponse(
                status_code=409,
                content={"detail": RUNNING_SENTENCE, "turn_id": _running_turn(d, thread_id)},
            )
        row = await asyncio.to_thread(_session_or_404, thread_id)
        patch = await asyncio.to_thread(
            account_patch, platform, _setup_of(row), body.account.strip(), body.model.strip() or None,
        )
        row = await asyncio.to_thread(S.update, platform.engine, thread_id, setup=patch)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        turn_id = ""
        if body.continue_:
            turn_id = "bt_" + secrets.token_hex(8)
            try:
                await start_build_turn(d, thread_id, CONTINUE_TEXT, turn_id, continuation=True)
            except SessionBusy:
                return JSONResponse(
                    status_code=409,
                    content={"detail": RUNNING_SENTENCE, "turn_id": _running_turn(d, thread_id)},
                )
            row = await asyncio.to_thread(_session_or_404, thread_id)
        return {"session": await asyncio.to_thread(_full_view, row), "turn_id": turn_id}

    @app.post("/build/sessions/{thread_id}/resume-when-ready")
    async def arm_resume_when_ready(thread_id: str) -> dict[str, Any]:
        """"Resume when ready": a DURABLE wait on the session
        (``setup.account_wait``). The daemon re-checks the account at its
        reset (and every few minutes after), continues the same conversation
        when it is usable, and the bell says it resumed (``build.resumed``).
        It survives a restart. 409 when the session's account is not waiting
        on anything, or a turn is running. Answers the session."""
        from ...harness import build_accounts as BA

        platform = d.platform
        if thread_id in _STARTING or _running_turn(d, thread_id):
            raise HTTPException(status_code=409, detail=RUNNING_SENTENCE)
        row = await asyncio.to_thread(_session_or_404, thread_id)
        setup = _setup_of(row)
        if not setup.get("account"):
            raise HTTPException(status_code=409, detail=NO_ACCOUNT)
        limit = setup.get("account_limit") if isinstance(setup.get("account_limit"), dict) else None
        if limit is not None and limit.get("account") != setup.get("account"):
            limit = None
        live = BA.find(_svc_of(platform), str(setup["account"]))
        if limit is None and live is not None and live.get("status") in (BA.LIMIT, BA.NEEDS_SIGN_IN):
            limit = {"resets_at": live.get("resets_at")}
        if limit is None:
            raise HTTPException(status_code=409, detail=NOTHING_TO_WAIT_FOR)
        if limit.get("resumable") is False:
            raise HTTPException(
                status_code=409,
                detail="This account no longer exists in Iron-Proxy — choose another account.",
            )
        wait = wait_record(setup, limit.get("resets_at"))
        row = await asyncio.to_thread(S.update, platform.engine, thread_id, setup={"account_wait": wait})
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        wake_wait_loop()
        return await asyncio.to_thread(_full_view, row)

    @app.delete("/build/sessions/{thread_id}/resume-when-ready")
    async def cancel_resume_when_ready(thread_id: str) -> dict[str, Any]:
        """Cancel a "Resume when ready" wait (nothing waits any more; the
        limit card stays). Answers the session."""
        await asyncio.to_thread(_session_or_404, thread_id)
        row = await asyncio.to_thread(
            S.update, d.platform.engine, thread_id, setup={"account_wait": None},
        )
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        return await asyncio.to_thread(_full_view, row)
