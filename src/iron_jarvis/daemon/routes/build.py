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
  folder as its workspace, ``build_round_budget`` rounds (v1.384.0: no limit
  by default — the 1,000-step safety stop; a number the user set otherwise),
  never stopped for being unwatched, never handed
  to an agent run (it ends in the conversation), every tool call under the
  session's OWN ``build:<id>`` (never "chat"), the session's mode (plan:
  read-only tools + ``exit_plan_mode``) and ``ask_user`` (the screen draws
  the question). The turn is NAMED and DAEMON-OWNED (``TurnLogs``): leaving
  Build, a reload or a closed window does not stop it; Stop does. The daemon
  writes the question when the turn starts and the reply when it ends — the
  screen never saves a session's transcript. v1.373.0: the box's "/" and "@"
  ride it as ``skill`` / ``folder_skill`` (a playbook for this message) and
  ``files`` (files picked in the session's folder, checked to be inside it);
* ``GET /build/sessions/{id}/skills`` (v1.373.0) — the folder's own harness
  skills, for the "/" menu;
* ``GET /build/sessions/{id}/commands`` (v1.379.0) — Claude Code's own custom
  "/" commands for the session (``harness/claude_commands``: personal,
  project and enabled plugins' command files, and a plugin's skills under
  Claude Code's ``<plugin>:<skill>`` names) — names, descriptions and
  argument hints, never a command's text. A turn's ``command`` runs one: its
  text is expanded the way Claude Code does it and sent as the message,
  without running anything it names;
* v1.379.0: a session may start with NO folder (``POST /build/sessions``
  without ``folder``): it works in its OWN folder under the chat files folder
  (``config.chat_files_dir``), made when its first turn starts and removed
  when it is archived or deleted while still empty;
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
#: Steps a reply line records WITH their detail, at most.
MAX_STEPS = 60
#: v1.384.0 review: steps a reply line COUNTS, at most. A reply with no step
#: limit (the default) often takes more than ``MAX_STEPS``, and a line that
#: kept only the first 60 made its work line under-report ("read 54 files"
#: for 70). Past ``MAX_STEPS`` a step is kept COMPACT — its name, outcome,
#: time and target, never its detail (a few dozen bytes) — up to this many:
#: two calls a round at the safety stop (``harness.mode.BUILD_SAFETY_ROUNDS``).
MAX_COUNTED_STEPS = 2000

RUNNING_SENTENCE = (
    "This session is still answering. Steer it, queue your message, or stop it first."
)
ARCHIVED_SENTENCE = "This session is archived. Bring it back before you continue it."
#: v1.381.0 review: a project's or a plugin's command whose shell lines could
#: not be kept on the session is not started — its carry-on (and every later
#: turn's match) reads ONLY that record, so a turn that ran without it would
#: hand the carry-on every command with no card.
COMMAND_RECORD_SENTENCE = (
    "Iron Jarvis could not keep this command's shell lines on the session, so it did not start "
    "/{name}. Nothing ran. Try again."
)

#: Sessions whose turn is being started right now (between the "nothing is
#: running" check and ``TurnLogs.start``) — on the loop thread only, so two
#: messages sent at once cannot both start a turn.
_STARTING: set[str] = set()
#: v1.374.0 (part 2, Phase 3): sessions whose older turns are being
#: summarized right now (Compact). They are in ``_STARTING`` too (no turn,
#: rewind or resumed wait starts meanwhile); this set only lets a refusal say
#: so in its own words. On the loop thread only.
_COMPACTING: set[str] = set()

COMPACTING_SENTENCE = (
    "This session is summarizing its earlier turns. Send your message when that finishes."
)
#: Harness gaps review: the press is "Summarize" (fix 4); this refusal was
#: still worded for the old "Compact".
COMPACT_RUNNING = "This session is still answering. Wait for the reply, or stop it, before you summarize it."

#: v1.374.0: a turn that ran longer than this, and ended, is said by the bell
#: (and by the desktop app from the tray) — ``build.turn_finished``.
FINISH_NOTICE_AFTER_S = 60.0


class BuildSessionCreate(BaseModel):
    #: v1.379.0: "" (or omitted) = a session with NO folder — it works in its
    #: own folder under the chat files folder, made with its first turn.
    folder: str = Field(default="", max_length=1000)
    title: str = Field(default="", max_length=400)
    provider: str = ""
    model: str = ""
    reasoning: str = ""
    approval_mode: str = ""
    mode: str = ""
    #: v1.368.0: the Iron-Proxy account to run on; "" = the vendor's Default.
    account: str = Field(default="", max_length=200)
    #: v1.375.0 (part 2, Phase 4): work on a NEW BRANCH in its own worktree
    #: beside the repository (``harness/branches``). True = a branch, or a
    #: refusal in words when the folder cannot have one; False = work in the
    #: folder directly (exactly the old behaviour); None (omitted, an older
    #: screen or an API caller) = a branch when the folder can have one, else
    #: the folder directly.
    branch: bool | None = None


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
    text: str = ""
    turn_id: str = ""
    attachments: list[str] = Field(default_factory=list, max_length=20)
    #: v1.371.0: the address the session's Browser pane shows. Only a page on
    #: this PC or the user's network is kept (harness/preview); the agent may
    #: look at that dev server without asking (page_screenshot).
    #: Not length-checked here: an address too long to be a page is simply
    #: dropped by ``harness.preview.local_address`` (a 422 would have failed
    #: the whole message over the pane's address).
    preview_url: str = ""
    #: v1.373.0 (part 2, Phase 2): a skill picked with "/" in the box, loaded
    #: for this turn as the chat's "/skill" playbook (``ChatBody.skill``). An
    #: unknown one is refused (404, the chat's words) before any model call.
    skill: str = Field(default="", max_length=200)
    #: v1.373.0: files picked with "@" from the SESSION's folder. Each must be
    #: a file inside that folder (``harness/sessions.folder_files``: links and
    #: ".." resolved first), and with the uploads no more than one turn reads
    #: (``chat_turn._MAX_ATTACHMENTS``), or the message is refused in words;
    #: the turn reads them the way it reads an attachment.
    files: list[str] = Field(default_factory=list, max_length=20)
    #: v1.373.0: a skill of the session FOLDER's own harness layer
    #: (``.ironjarvis/harness/skills/<name>``), picked with "/" — read through
    #: the layer's validator and scanned (``harness/picked_skill``). One skill
    #: per message: ``skill`` or this, not both.
    folder_skill: str = Field(default="", max_length=64)
    #: v1.379.0: one of Claude Code's own custom commands, typed
    #: ``/<command> <words>`` (``harness/claude_commands``). ``text`` holds
    #: the words after the name — its ARGUMENTS. Not with a skill.
    command: str = Field(default="", max_length=200)
    #: v1.384.0: tag every frame of the answer with its RESUME POSITION
    #: (``durable_turns``: ``id: <i>:<c>``, ``resume`` / ``caught_up``), so
    #: the page can come back to this reply with
    #: ``GET /chat/turns/{id}/stream?after=<i>:<c>`` and be sent only what it
    #: missed. False (an API caller): the frames exactly as before.
    ids: bool = False


class BuildCompactBody(BaseModel):
    """``POST /build/sessions/{id}/compact`` (v1.379.0 review): Claude Code's
    ``/compact [instructions]`` — what the summary should keep in view, in
    the user's own words ("" = none, exactly the v1.374.0 summary)."""

    focus: str = Field(default="", max_length=2000)


class BuildRewindBody(BaseModel):
    """``POST /build/sessions/{id}/rewind`` (v1.370.0): rewind TO before the
    turn ``to`` (that turn and every later one)."""

    to: str = Field(min_length=1, max_length=80)


class BuildBranchBody(BaseModel):
    """``POST /build/sessions/{id}/branch`` (v1.377.0, part 2 Phase 6):
    start a NEW session from one of this session's messages — ``at`` =
    ``"reply"``: the conversation up to and including turn ``turn_id``'s
    reply; ``"question"``: the conversation before that turn's question
    (whose words and files go into the new session's box)."""

    turn_id: str = Field(min_length=1, max_length=80)
    at: str = Field(default="reply", max_length=20)


def skill_source(question: Any) -> str:
    """v1.377.0 review: where a saved question's "/" skill came from —
    ``folder`` (the folder's own harness skills) or ``yours`` — or "" (none,
    or an older line that did not say; the screen then restores nothing)."""
    src = question.get("skillSource") if isinstance(question, dict) else None
    return src if src in ("folder", "yours") else ""


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


def _turn_record(engine: Any, turn_id: str, *, with_ids: bool = False) -> "tuple[str, str] | None":
    """``(thread_id, sse_text)`` of a turn's RECORD (``ChatTurnRecord``), or
    None when it was never recorded. BLOCKING: call it off the loop.
    ``with_ids`` (v1.384.0): the tagged whole turn (``durable_turns.
    tagged_replay`` — a record is never resumed part-way)."""
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
        frames = [f for f in frames if isinstance(f, list) and len(f) == 2]
        if with_ids:
            return str(row.thread_id or ""), _durable.tagged_replay(frames)
        text = "".join(_durable.sse(f[0], f[1]) for f in frames)
        return str(row.thread_id or ""), text


async def _replay_once(text: str):
    if text:
        yield text


def build_step_limit(config: Any) -> tuple[int, bool]:
    """``(rounds, no_limit)`` — the ``build_round_budget`` setting as a turn
    can use it (v1.384.0).

    0 (the default) is NO LIMIT: the turn gets the safety stop,
    ``harness.mode.BUILD_SAFETY_ROUNDS`` (1,000), and ``no_limit`` is True — a
    reply that ever meets it says it reached the SAFETY stop, not "your
    limit". A number the user set (1..1000) is honoured. The settings writer
    checks the range, but a hand-edited ``config.toml`` does not — an
    out-of-range value is CLAMPED, never a 400 on every turn of every session
    (Phase 4 review): above the ceiling is the ceiling, below 1 (other than
    0) is 1, and anything unreadable is no limit."""
    from ...harness.mode import BUILD_SAFETY_ROUNDS
    from ..chat_turn import MAX_TURN_ROUNDS

    ceiling = min(BUILD_SAFETY_ROUNDS, MAX_TURN_ROUNDS)
    try:
        value = int(getattr(config, "build_round_budget", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    if value == 0:
        return ceiling, True
    return max(1, min(ceiling, value)), False


def build_round_budget(config: Any) -> int:
    """The turn's round budget (see :func:`build_step_limit`)."""
    return build_step_limit(config)[0]


def _never_orphaned() -> bool:
    """A BUILD turn's ``should_stop`` (v1.384.0): never. A chat turn nobody
    has watched for ``durable_turns.ORPHAN_STOP_S`` (an hour) is stopped — it
    would otherwise hold an unanswered question forever. A Build reply is a
    long task the user starts and then works elsewhere: it ends only by the
    model, Stop (the turn registry's handle, which the lane checks first),
    archive or delete, a shutdown, the account limit card, or its step
    limit. Leaving it unwatched is what the user is meant to do."""
    return False


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


def _turn_pair(platform: Any, body: Any, project: Any) -> "tuple[str, str]":
    """The provider + model a Build turn REALLY runs on (v1.372.0 review):
    the session's own pick, else its project folder's default, else the
    configured default — the stream lane's own order (``provider_choice``).
    A session made from the dashboard names no provider, and a hold on ""
    binds nothing, so the hold is armed with the resolved pair. "auto" stays
    "auto" (the user delegated the choice). Never raises."""
    prov = str(getattr(body, "provider", "") or "").strip()
    mdl = str(getattr(body, "model", "") or "").strip()
    if not prov and project is not None:
        prov = str(getattr(project, "default_provider", "") or "").strip()
        mdl = mdl or str(getattr(project, "default_model", "") or "").strip()
    if not prov:
        cfg = getattr(platform, "config", None)
        prov = str(getattr(cfg, "default_provider", "") or "").strip()
        mdl = mdl or str(getattr(cfg, "default_model", "") or "").strip()
    return prov, mdl


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


#: Harness gaps (fix 2): how much of a failure's own words (the error frame's
#: ``technical``) a reply line keeps for the Details fold — the frame's are
#: already masked and cut at this length (``failure_words.technical_text``).
FAILURE_DETAIL_CHARS = 2000


def failure_detail(raw: Any) -> str:
    """A failure's technical text as a reply line keeps it: masked again
    (``failure_words.mask_failure_text`` — the shared mask plus the secret
    shapes it does not know, harness gaps review; idempotent) and bounded. ""
    for nothing. Never raises."""
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return ""
    try:
        from ...providers.failure_words import mask_failure_text

        text = mask_failure_text(text)
    except Exception:  # noqa: BLE001 — wording never fails a turn
        pass
    return text[:FAILURE_DETAIL_CHARS]


class _ReplyWatch:
    """Reads a build turn's frames as they pass and keeps what its reply line
    needs (and the two things that change the SESSION mid-turn: an approved
    plan and an "allow for this conversation" answer).

    v1.390.0 (the Timeline): it also keeps WHEN — each step's start, each
    model call (``calls``), each helper step's start and length — through the
    reply's own clock (``harness/timeline.ReplyClock``), and :meth:`see`
    answers the frame's data with those offsets added for the frames it
    stamps, so the page draws the running reply from the same numbers the
    line keeps (``recorded_turn`` sends the stamped frame on)."""

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
        #: v1.390.0: the reply's clock (offsets from ``started_at``).
        from ...harness.timeline import ReplyClock

        self.clock = ReplyClock(self.started_at)
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
        #: v1.372.0: the helper agents this turn started (``helper`` frames),
        #: by call id, in the order they started — kept on the reply line.
        self.helpers: dict[str, dict[str, Any]] = {}
        #: v1.390.0: (helper id, step id) -> what a helper's step that has
        #: not finished was done to (its saved ``target``).
        self._helper_targets: dict[tuple[str, str], str] = {}
        #: v1.374.0: the turn's task list as its last ``plan_update`` left it
        #: (``[{step, status}]``, the plan tool's own vocabulary) — the
        #: checklist the reply line keeps (:func:`saved_plan`).
        self.last_plan: list[dict[str, str]] = []
        #: v1.384.0: the turn ran with NO step limit (the default), so a
        #: ``step_limit`` it met is the SAFETY stop (the reply line says so).
        self.no_limit = False

    def see(self, event: str, raw: str) -> "dict[str, Any] | None":
        """Fold one frame. Answers the frame's data with its Timeline offsets
        added (v1.390.0) for a frame the clock stamps — ``round``,
        ``tool_call``, ``model_call``, ``helper`` — and None for every other
        frame (sent on unchanged)."""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        stamped: "dict[str, Any] | None" = None
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
        elif event == "round":
            if data.get("steer"):
                self.steers.append(str(data["steer"]))
            # v1.390.0: the tool loop is about to ask the model (a call in
            # flight until its `model_call` frame) — the frame says from when.
            stamped = {**data, "start": self.clock.round_started()}
        elif event == "meta":
            self.clock.meta(data.get("provider"), data.get("model"))
        elif event == "model_call":
            # v1.390.0: one billed model call (the lane's frame): kept on the
            # line's `calls`, its start added to the frame.
            stamped = self.clock.call_seen(data)
        elif event == "tool_call":
            cid = str(data.get("id") or "")
            name = str(data.get("name") or "")
            # v1.390.0 review: a step Iron Jarvis ran itself (the finish
            # check's test run, a project check) says so — on its frames and
            # its saved step — so the Timeline never draws it as the model's.
            from ...harness.timeline import check_kind

            check = check_kind(cid)
            marked = {"check": check} if check else {}
            if data.get("status") == "started":
                self._started[cid] = (name, time.time(), data.get("args"))
                stamped = {**data, "start": self.clock.step_started(cid), **marked}
            elif data.get("status") == "finished":
                began = self._started.pop(cid, None)
                ok = data.get("ok")
                # v1.390.0: WHEN it ran — its start (the reply's clock) and
                # its length (the lane's own measure of a call that ran
                # beside others, else the time between its two frames).
                start, ms = self.clock.step_finished(cid, data.get("ms"))
                stamped = {**data, "start": start, "ms": ms, **marked}
                if len(self.steps) < MAX_COUNTED_STEPS:
                    step: dict[str, Any] = {
                        "name": name,
                        "ok": bool(ok) if ok is not None else None,
                        "ms": ms if began else None,
                        "start": start,
                        **marked,
                    }
                    if cid and cid in self.helpers:
                        # v1.390.0: this call STARTED a helper — the helper's
                        # own row stands in for it on the Timeline.
                        step["helper"] = True
                    # v1.378.0 review: what the step was done to ("cart.py",
                    # "python"), from the frame's own arguments — the words
                    # the live row shows (lib/workTarget's twin), saved
                    # before the detail as the chat page saves them.
                    from ...harness.step_target import step_target

                    target = step_target(name, began[2] if began else None)
                    if target:
                        step["target"] = target
                    # v1.384.0 review: only the first MAX_STEPS keep their
                    # detail; the rest are counted, compact.
                    if isinstance(data.get("detail"), dict) and len(self.steps) < MAX_STEPS:
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
                        self.last_plan = [dict(st) for st in steps]
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
        elif event == "helper":
            # v1.390.0: the helper's row and its step on the reply's clock.
            stamped = self.clock.helper(data)
            self.see_helper(stamped)
        elif event == "approval_resolved" and data.get("decision") == "conversation":
            tool = str(data.get("tool") or "")
            if tool and tool not in self.granted:
                self.granted.append(tool)
        elif event == "done":
            self.done = data
        elif event == "error":
            self.error = data
        return stamped

    def see_helper(self, data: dict[str, Any]) -> None:
        """Fold one ``helper`` frame (v1.372.0) into what the reply keeps of
        that helper: who, what it was asked, how it ended, its report and
        its finished steps (each step's name, outcome and masked detail —
        never its raw arguments or output)."""
        from ...harness.helpers import MAX_SAVED_HELPERS, MAX_SAVED_STEPS, STATES

        hid = str(data.get("id") or "")
        if not hid:
            return
        h = self.helpers.get(hid)
        if h is None:
            if len(self.helpers) >= MAX_SAVED_HELPERS:
                return
            h = {"id": hid, "work": [], "steps": 0}
            self.helpers[hid] = h
        for key, dest in (("agent", "agent"), ("task", "task"), ("session", "session"),
                          ("result", "result")):
            val = data.get(key)
            if isinstance(val, str) and val:
                h[dest] = val
        state = data.get("state")
        if state in STATES:
            h["state"] = state
        if isinstance(data.get("steps"), int):
            h["steps"] = max(int(h.get("steps") or 0), int(data["steps"]))
        if isinstance(data.get("since"), int) and "startedAt" not in h:
            h["startedAt"] = data["since"]
        if isinstance(data.get("ended"), int):
            h["endedAt"] = data["ended"]
        # v1.390.0 (the Timeline): its row on the reply's clock (stamped by
        # `ReplyClock.helper`): when its first frame came, and its end.
        if isinstance(data.get("start"), int) and "start" not in h:
            h["start"] = data["start"]
        if isinstance(data.get("end"), int):
            h["end"] = data["end"]
        # v1.390.0 (the Timeline): the model the helper ran on (its run's own
        # `meta`) and, once it ended, what it used (its session's row) — the
        # helper's cost is its OWN, never part of the reply's receipt.
        for key in ("provider", "model"):
            val = data.get(key)
            if isinstance(val, str) and val:
                h[key] = val[:200]
        if isinstance(data.get("usage"), dict):
            h["usage"] = data["usage"]
        # v1.374.0: the helper's plan as its row last showed it (the turn's
        # end reads it once more, settled — _with_helper_plans).
        if isinstance(data.get("plan"), dict) and isinstance(data["plan"].get("steps"), list):
            h["plan"] = {"steps": list(data["plan"]["steps"])[:50], "total": data["plan"].get("total")}
        step = data.get("step")
        sid = str(step.get("id") or "") if isinstance(step, dict) else ""
        if isinstance(step, dict) and step.get("status") != "finished" and sid:
            # v1.390.0: what the helper's step was done to ("util.py"), from
            # its started frame's (redacted) arguments — the run log's words
            # the Timeline names it by, the same rule as the reply's own.
            from ...harness.step_target import step_target

            target = step_target(str(step.get("name") or ""), step.get("args"))
            if target:
                self._helper_targets[(hid, sid)] = target
        if isinstance(step, dict) and step.get("status") == "finished":
            target = self._helper_targets.pop((hid, sid), None)
            if len(h["work"]) < MAX_SAVED_STEPS:
                item: dict[str, Any] = {
                    "name": str(step.get("name") or ""),
                    "ok": bool(step.get("ok")) if step.get("ok") is not None else None,
                    # v1.390.0: its length and start on the reply's clock
                    # (stamped by `ReplyClock.helper`; None from an older frame).
                    "ms": step.get("ms") if isinstance(step.get("ms"), int) else None,
                }
                if isinstance(step.get("start"), int):
                    item["start"] = step["start"]
                if target:
                    item["target"] = target
                if isinstance(step.get("detail"), dict):
                    item["detail"] = step["detail"]
                h["work"].append(item)

    def helper_lines(self, status: str) -> list[dict[str, Any]]:
        """The helpers as the reply keeps them. A helper the turn left
        unfinished (stopped, cut off) reads as stopped, never as running."""
        out = []
        for h in self.helpers.values():
            row = {k: v for k, v in h.items() if v not in (None, "")}
            if row.get("state") not in ("done", "failed", "stopped"):
                row["state"] = "stopped"
                row.setdefault("endedAt", int(time.time() * 1000))
                # v1.390.0: its bar ends where the reply did.
                if "start" in row:
                    row.setdefault("end", self.clock.offset())
            out.append(row)
        return out

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
            # Harness gaps (fix 3): every receipt fact the chat page keeps on
            # a reply, so the Build receipt says what the chat's says — the
            # envelope's adaptation, a kept preference and the earlier chats
            # read, each only when the turn had one (absent = nothing to say).
            if isinstance(done.get("adapted"), dict) and done["adapted"]:
                line["adapted"] = done["adapted"]
            remembered = [str(x) for x in (done.get("remembered") or []) if isinstance(x, str) and x.strip()]
            if remembered:
                line["remembered"] = remembered
            refs = [r for r in (done.get("thread_refs") or []) if isinstance(r, dict)]
            if refs:
                line["threadRefs"] = refs
            # v1.374.0: how full the conversation was for the model this turn
            # — the planner's own numbers, never a guess on the screen.
            ctx = context_receipt(done.get("context"))
            if ctx is not None:
                line["context"] = ctx
            # v1.384.0: the reply met its STEP LIMIT (the lane's `step_limit`)
            # — the line keeps how many steps and whether that was the safety
            # stop of a reply with no limit; the screen says so under the
            # reply and offers "Keep going" (a new turn, never automatic).
            sl = done.get("step_limit")
            if isinstance(sl, dict) and isinstance(sl.get("limit"), int) and not isinstance(sl.get("limit"), bool):
                line["stepLimit"] = {"limit": int(sl["limit"]), "safety": bool(self.no_limit)}
            # Part 3, Phase 2: what the reply's FINISH CHECK did (present only
            # when it ran) — the work line's "Checked: tests passed, …".
            from ...harness.finish import saved_record

            fin = saved_record(done.get("finish"))
            if fin is not None:
                line["finish"] = fin
            if done.get("trust") and done.get("trust") != "full":
                line["trust"] = done.get("trust")
                line["trustReason"] = done.get("trust_reason") or ""
                line["trustNote"] = done.get("trust_note") or ""
        elif self.error is not None:
            err = self.error
            # `title. fix` — the join the chat's failure line makes
            # (lib/failureLine.failureSentence; v1.378.0: it was joined with a
            # bare space, "…declined to answer this conversation Its safety…").
            headline = title = str(err.get("title") or "").strip()
            fix = str(err.get("fix") or "").strip()
            if title and fix and title[-1] not in ".!?":
                title += "."
            words = " ".join(p for p in (title, fix) if p) or str(err.get("detail") or "The reply failed.")
            line.update({"content": partial, "error": words, "interrupted": bool(partial)})
            # v1.378.0: a SAFETY REFUSAL is marked on the line, so the
            # session offers Rewind (never Retry: the same conversation is
            # declined again) — also after it is opened again.
            kind = str(err.get("fix_kind") or "")
            if kind == "refusal":
                line["errorKind"] = "refusal"
            elif kind in ("sign_in", "key"):
                # Harness gaps (fix 2): the ONE press that fixes it (the chat's
                # `fix_action`) is kept too — a signed-out Claude Code / Codex
                # offers Sign in, a refused key the link to its place — and
                # the provider it is about (an id, never a secret).
                line["errorKind"] = kind
                provider = str(err.get("fix_provider") or "").strip()[:80]
                if provider:
                    line["errorProvider"] = provider
            # Harness gaps (fix 2): the headline alone (the line reads just it
            # when a press stands beside it, the chat's rule) and the
            # server's own words, folded under Details on the screen — kept
            # on the line so they are there after a reload too. Masked again
            # (the frame's are already) and bounded.
            if headline:
                line["errorTitle"] = headline
            technical = failure_detail(err.get("technical"))
            if technical and technical != words:
                line["errorDetail"] = technical
        else:
            if not partial.strip() and not self.helpers:
                return out  # stopped before a word: nothing to keep
            # (v1.372.0: a turn stopped while its helpers worked keeps a line
            # for them — what they did is part of the session.)
            line.update({"content": partial, "interrupted": True})
            if status == "interrupted":
                line["error"] = _durable.INTERRUPTED_DETAIL
        if self.thinking:
            line["thinking"] = "".join(self.thinking)
        if self.account_limit:
            line["accountLimit"] = self.account_limit
        if self.steps:
            line["steps"] = self.steps
        # v1.390.0 (the Timeline): every billed model call of the reply, and
        # the one in flight when it ended (stopped, cut off, or failed).
        calls = list(self.clock.calls)
        if self.done is None:
            open_call = self.clock.open_call("failed" if self.error is not None else "stopped")
            if open_call is not None:
                calls.append(open_call)
        if calls:
            line["calls"] = calls
        if self.helpers:
            # v1.372.0: the helper agents this turn started.
            line["helpers"] = self.helper_lines(status)
        if self.last_plan:
            # v1.374.0: the turn's task list as it last stood (the reply folds it).
            line["plan"] = saved_plan(self.last_plan)
        # `timing` is an EPOCH window on the WALL clock (v1.390.0 review): the
        # reply's own Changes row asks the journal for the rows between
        # `startedAt` and `endedAt` (paneTurnWindow → POST /chat/changes), and
        # those rows are stamped by the wall clock — a window measured on any
        # other clock loses the changes made after the wall clock stepped
        # forward mid-reply (a time sync after the PC wakes). The reply's
        # PRECISE length — the Timeline's axis, the clock every bar's offset
        # is on — rides beside it as `ms`, and marks a line written since the
        # Timeline.
        line["timing"] = {
            "startedAt": int(self.started_at * 1000),
            "firstTokenAt": int(self.first_token_at * 1000) if self.first_token_at else None,
            "endedAt": int(time.time() * 1000),
            "ms": self.clock.offset(),
        }
        out.append(line)
        return out


#: The plan tool's statuses as the checklist's states (lib/planChecklist).
_PLAN_STATE = {"pending": "waiting", "in_progress": "running", "completed": "done"}


def saved_plan(steps: list[dict[str, str]]) -> dict[str, Any]:
    """A ``plan_update`` step list in the checklist's saved form (the chat's
    ``agentRun.plan``: ``{steps: [{goal, state}], total}``). States are kept
    as the model last reported them; the screen settles a step left waiting
    or working once the turn is over (``lib/planChecklist.settlePlan``)."""
    kept = [
        {"goal": str(st.get("step") or "")[:500], "state": _PLAN_STATE.get(str(st.get("status")), "waiting")}
        for st in steps if str(st.get("step") or "").strip()
    ]
    return {"steps": kept, "total": len(kept)}


def context_receipt(ctx: Any) -> "dict[str, Any] | None":
    """The done frame's ``context`` as the reply line keeps it (v1.374.0): the
    planner's own numbers for this turn — ``window`` (what the model can read
    at once, ``_context_window``), ``used`` (what the planned transcript sent)
    and, from the compaction report, ``tokens`` (what the whole conversation
    needs with nothing left out), ``percent`` (that against the window — the
    figure the Compact offer keys off, ``compaction.SUGGEST_AT``) and
    ``level`` / ``suggest_at``. None when there is no window to measure by."""
    if not isinstance(ctx, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("window", "used", "tokens", "percent", "suggest_at", "auto_at"):
        v = ctx.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            out[key] = int(v)
    if not out.get("window"):
        return None
    if ctx.get("level") in ("ok", "suggest", "auto"):
        out["level"] = ctx["level"]
    if ctx.get("compacted") is True:
        out["compacted"] = True
    # Review (v1.374.0): the planner's window was the app's own safe size (no
    # pin, measurement, catalog or probe knows this model's) — the meter says
    # so instead of calling it the model's limit; and compaction turned OFF
    # in Settings (the remedy off, the reading still true) — no Compact offer,
    # the chat's own rule.
    if ctx.get("window_known") is False:
        out["window_known"] = False
    if ctx.get("disabled") is True:
        out["disabled"] = True
    return out


def _with_helper_plans(engine: Any, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """v1.374.0: each helper the reply keeps gains its run's PLAN when the run
    planned its work (the decomposed lane): the daemon's own checklist read
    once more now the run is over (``harness/helpers.helper_plan`` — settled),
    in place of the last one its row was sent. BLOCKING (two reads per
    helper, at most ``MAX_SAVED_HELPERS``); a plan that cannot be read keeps
    what the row last showed. Never raises."""
    from ...harness.helpers import helper_plan

    for line in lines:
        for h in line.get("helpers") or []:
            sid = str(h.get("session") or "") if isinstance(h, dict) else ""
            if not sid:
                continue
            plan = helper_plan(engine, sid)
            if plan:
                h["plan"] = plan
    return lines


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


async def _keep_account_limit(engine: Any, thread_id: str, turn_id: str,
                              limit: "dict[str, Any] | None", *, done: bool) -> None:
    """The RECORD half of a turn's account outcome: keep the session's limit
    card across a reload (``setup.account_limit``) — or clear it (and a
    "Resume when ready" wait) when a turn on the account was served. Written
    BEFORE the turn's terminal frame reaches anyone (v1.369.0 review), so a
    client that reads the session the moment the turn ends sees the card.
    Off the loop; never raises."""
    try:
        if limit:
            record = {
                **{k: v for k, v in limit.items() if v is not None},
                "at": utcnow().isoformat(), "turn_id": turn_id,
            }
            await asyncio.to_thread(S.update, engine, thread_id, setup={"account_limit": record})
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


async def _announce_account_limit(platform: Any, thread_id: str, limit: "dict[str, Any]") -> None:
    """The NEWS half of a limit: refresh the cached account snapshot (so the
    chip and the list say "Limit reached" at once instead of at the next watch
    cycle) and tell the bell. Runs AFTER the terminal frame — Iron-Proxy is
    never waited on before the user sees the turn end. Off the loop; never
    raises."""
    engine = platform.engine
    try:
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
    except Exception:  # noqa: BLE001 — the turn never fails on its session record
        log.warning("announcing build session %s's account limit failed", thread_id, exc_info=True)


async def _announce_turn_finished(platform: Any, thread_id: str, turn_id: str, status: str,
                                  seconds: float) -> None:
    """``build.turn_finished`` (v1.374.0) for a long turn that ended: the
    session's title, how long it took and how it ended ("done" | "failed").
    The bell says it (unless the user is looking at that session); "This PC"
    stays quiet for it (``comm/notifier.bell_pings``). Never raises."""
    try:
        bus = getattr(platform, "event_bus", None)
        if bus is None:
            return
        from ...core.events import EventType

        row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
        await bus.publish(EventType.BUILD_TURN_FINISHED, {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "title": (getattr(row, "title", "") or "") if row is not None else "",
            "seconds": int(seconds),
            "outcome": "done" if status == "done" else "failed",
        }, session_id=S.session_id_for(thread_id))
    except Exception:  # noqa: BLE001 — the turn never fails on its notice
        log.warning("announcing build session %s's finished turn failed", thread_id, exc_info=True)


async def _note_account_limit(platform: Any, thread_id: str, turn_id: str,
                              limit: "dict[str, Any] | None", *, done: bool) -> None:
    """Both halves at once (:func:`_keep_account_limit` then
    :func:`_announce_account_limit`). Off the loop; never raises."""
    await _keep_account_limit(platform.engine, thread_id, turn_id, limit, done=done)
    if limit:
        await _announce_account_limit(platform, thread_id, limit)


def _turn_served(pin: Any, status: str) -> bool:
    """Did the session's account SERVE this turn (a pinned lease succeeded)?
    Then the card and any "Resume when ready" wait are over — whether the turn
    finished, failed for another reason, or the user stopped it. A turn cut
    off by a shutdown ("interrupted") keeps the wait (v1.368.0 review)."""
    return status == "done" or (
        pin is not None and status in ("error", "stopped") and bool(getattr(pin, "leased", None))
    )


async def _record_turn_end(engine: Any, thread_id: str, turn_id: str, watch: "_ReplyWatch",
                           status: str, pin: Any, platform: Any) -> None:
    """What a turn's END writes on the session: the reply line (plus any steer
    notes), then the account outcome's record. Never raises."""
    lines = watch.lines(turn_id, status)
    if lines:
        with contextlib.suppress(Exception):
            if watch.helpers:
                lines = await asyncio.to_thread(_with_helper_plans, engine, lines)
            await asyncio.to_thread(S.append_lines, engine, thread_id, lines)
    served = _turn_served(pin, status)
    if pin is not None and platform is not None and (watch.account_limit or served):
        await _keep_account_limit(engine, thread_id, turn_id, watch.account_limit, done=served)


async def recorded_turn(engine: Any, thread_id: str, turn_id: str, frames: Any, *,
                        pin: Any = None, platform: Any = None, no_limit: bool = False):
    """``frames`` (the lane's SSE iterator), passed through unchanged, with
    the session kept up to date: an approved plan and a conversation grant
    land as they happen, and the reply line (plus any steer notes) is written
    when the turn ends — done, failed, stopped, or cut off by a shutdown.

    v1.368.0: ``pin`` — the turn's ONE account (``iron_proxy.pin``). When it
    could not serve (parked, needs sign-in), a typed ``account_limit`` frame
    goes out right BEFORE the turn's last frame and the card is kept on the
    session; the ``done`` frame's ``route.account`` names the account.

    v1.384.0: ``no_limit`` — the turn ran with no step limit, so a step limit
    it meets is the safety stop (kept on the reply line's ``stepLimit``)."""
    watch = _ReplyWatch()
    watch.no_limit = bool(no_limit)
    status = "stopped"
    limit_sent = False
    # THE END IS WRITTEN BEFORE IT IS SAID (v1.369.0 review). The reply line
    # and the account's limit card land on the session BEFORE the terminal
    # frame is passed on: the screen re-reads the session the moment it sees
    # `done` / `error`, and a write that ran after that frame (it used to sit
    # in the finally) was a race the screen lost — the card the stream had
    # just shown vanished on the re-read. ONE write per turn: the finally
    # awaits the same task when the frame was seen (a cancel during the
    # shielded write keeps it running), and starts it otherwise.
    ending: "asyncio.Future[None] | None" = None

    def end(st: str) -> "asyncio.Future[None]":
        nonlocal ending
        if ending is None:
            ending = asyncio.ensure_future(
                _record_turn_end(engine, thread_id, turn_id, watch, st, pin, platform),
            )
        return ending

    try:
        async with contextlib.aclosing(frames):
            async for chunk in frames:
                parsed = _durable.parse_chunk(chunk)
                terminal = any(ev in _TERMINAL_EVENTS for ev, _ in parsed)
                if pin is not None and terminal:
                    if pin.limit and not limit_sent:
                        limit_sent = True
                        card = {**pin.limit, "turn_id": turn_id}
                        watch.account_limit = card
                        yield _durable.sse("account_limit", card)
                    if pin.limit:
                        chunk = _with_limit_words(chunk, pin.limit)
                    chunk = _with_account(chunk, pin)
                    parsed = _durable.parse_chunk(chunk)
                # v1.390.0 (the Timeline): a frame the reply's clock stamps
                # (a step, a model call, a helper) goes on WITH its offsets —
                # the page draws the running reply from the numbers the line
                # will keep. Every other frame passes byte for byte.
                stamped_frames: "list[str] | None" = None
                for k, (event, data) in enumerate(parsed):
                    stamped = watch.see(event, data)
                    if stamped is not None and stamped_frames is None:
                        stamped_frames = [_durable.sse(e, d) for e, d in parsed[:k]]
                    if stamped_frames is not None:
                        stamped_frames.append(_durable.sse(event, stamped if stamped is not None else data))
                if stamped_frames is not None:
                    chunk = "".join(stamped_frames)
                if watch.approved_plans or watch.granted:
                    await _apply_session_changes(engine, thread_id, watch)
                if terminal and (watch.done is not None or watch.error is not None):
                    await asyncio.shield(end("done" if watch.done is not None else "error"))
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
        # The reply line and the account outcome's record: already written
        # when the terminal frame went out; else (stopped, cut off, broken)
        # written now. A turn the account SERVED (a pinned lease succeeded)
        # clears the card and any "Resume when ready" wait; a turn the
        # account parked keeps its own card; a turn cut off by a shutdown
        # ("interrupted") keeps the wait (v1.368.0 review, `_turn_served`).
        with contextlib.suppress(BaseException):
            await asyncio.shield(end(status))
        # v1.376.0 (part 2, Phase 5) DECISION: a Stop CLOSES the session's
        # agent browser (harness/page_browser) — a Stop never leaves an action
        # running or a browser process behind, also when it lands while the
        # model is thinking (an action under way was already closed by the
        # tool itself). The next turn opens a fresh one; the pane keeps the
        # last view. Off the loop; a no-op when no browser is open.
        if status == "stopped":
            from ...harness import page_browser as _page_browser

            if _page_browser.BROWSERS.is_open(S.session_id_for(thread_id)):
                with contextlib.suppress(BaseException):
                    await asyncio.shield(asyncio.to_thread(
                        _page_browser.close_for_stop, S.session_id_for(thread_id),
                    ))
        # The news (snapshot refresh + the bell) after the frame: Iron-Proxy
        # is never waited on before the user sees the turn end.
        if pin is not None and platform is not None and watch.account_limit:
            with contextlib.suppress(BaseException):
                await asyncio.shield(_announce_account_limit(platform, thread_id, watch.account_limit))
        # v1.374.0 (part 2, Phase 3): a turn that ran longer than a minute and
        # ENDED (done or failed) is told to the bell — ONCE, after its reply
        # line is written (the notice opens a session that already shows it).
        # Never for a turn the user stopped or a shutdown cut off, and never
        # beside an account's limit card (that card is the same news).
        elapsed = time.time() - watch.started_at
        if (
            platform is not None and status in ("done", "error") and not watch.account_limit
            and elapsed > FINISH_NOTICE_AFTER_S
        ):
            with contextlib.suppress(BaseException):
                await asyncio.shield(_announce_turn_finished(platform, thread_id, turn_id, status, elapsed))
        # v1.370.0 (Phase 7): the EXPERIENCE LIBRARY notes a turn that
        # FINISHED; the session is reflected on ONCE it has been idle (one
        # model call per idle period over every noted turn — never one per
        # turn: on a Claude Code or Codex plan it spends the user's limits).
        # It never delays or fails the turn.
        if status == "done" and platform is not None:
            from ...harness import library as _library

            with contextlib.suppress(Exception):
                _library.note_turn_finished(platform, thread_id, turn_id)


# --------------------------------------------------------------------------- #
# starting a build turn (the route, "Choose account", "Resume when ready")
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# a session with no folder (v1.379.0)
# --------------------------------------------------------------------------- #

NO_FOLDER_NO_BRANCH = (
    "A session with no folder is not in a repository, so it cannot work on a branch of its own."
)
#: The title a folderless session's own folder takes when its first message
#: has no words to name it (a picture alone, a skill alone).
WORK_FOLDER_TITLE = "Build session"


def _work_folder_title(text: str) -> str:
    """The first words of the first message — the folder's name after its
    date, like the chat's own work folder (``documents/workfolder``)."""
    words = " ".join(str(text or "").split())
    if words.startswith("/"):
        # "/review app.py" names a folder "review app.py", not "/review…".
        words = words.lstrip("/")
    return words[:60] or WORK_FOLDER_TITLE


def make_work_folder(platform: Any, thread_id: str, text: str) -> tuple[str, bool]:
    """The OWN folder of a session started with no folder: made now under the
    chat files folder (``<chat files>/<YYYY-MM-DD> <first words>``, the
    v1.244.0 rule — where the user finds what the agent made) and kept on the
    session (first wins: a folder another turn made meanwhile is used, and
    this one removed). Answers ``(folder, made_now)``. Raises ``OSError`` when
    it cannot be made. BLOCKING."""
    from ...documents.workfolder import make_chat_workfolder

    root = platform.config.chat_files_dir
    made = make_chat_workfolder(root, _work_folder_title(text), [], platform.config.home / "uploads")
    path = str(made["path"])
    kept = S.set_work_folder(platform.engine, thread_id, path)
    if kept != path:
        _remove_if_empty(path)
        return kept, False
    return path, True


def _remove_if_empty(path: str) -> bool:
    """Remove ``path`` when it is an EMPTY folder (never anything inside it).
    True when it was removed. Never raises."""
    import os

    try:
        p = S.resolved_folder(path)
        if p and os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
            return True
    except OSError:
        pass
    return False


def _make_again(path: str) -> bool:
    """Make a session's OWN folder again at ``path`` when the user removed it
    — only while the folder above it is still there (an unplugged drive or a
    moved chat files folder is NOT a removed folder: nothing is made, and the
    turn is refused in words). True when it was made now. BLOCKING."""
    import os

    try:
        p = S.resolved_folder(path)
        if not p or os.path.isdir(p) or os.path.lexists(p):
            return False
        if not os.path.isdir(os.path.dirname(p)):
            return False
        os.mkdir(p)
        return True
    except OSError:
        return False


def drop_empty_work_folder(platform: Any, thread_id: str) -> bool:
    """A session with no folder that is archived or deleted takes its OWN
    folder with it when that folder is still EMPTY (nothing was made there);
    one holding files is kept — the user's files are never touched. Only a
    folder inside the chat files folder, and only when no other session works
    in it (a branched session shares its origin's). The session forgets it,
    so a later turn makes a new one. BLOCKING; never raises."""
    try:
        row = S.get_row(platform.engine, thread_id)
        if row is None or not S.is_folderless(row):
            return False
        folder = S.folder_of(row)
        if not folder:
            return False
        from ...core.fs_policy import _canonical, _within

        try:
            root = _canonical(str(platform.config.chat_files_dir))
            inside = _within(_canonical(folder), root)
        except Exception:  # noqa: BLE001 — an unreadable path is not ours to remove
            return False
        if not inside or S.folder_users(platform.engine, folder, besides=thread_id):
            return False
        if _remove_if_empty(folder):
            S.set_work_folder(platform.engine, thread_id, None)
            return True
    except Exception:  # noqa: BLE001 — archive and delete never fail on this
        log.warning("removing build session %s's empty folder failed", thread_id, exc_info=True)
    return False


def command_folders(row: Any) -> list[str]:
    """The folders whose ``.claude/commands`` a session's "/" offers: its
    working folder, and (v1.375.0) the folder the user picked when it works
    in a worktree. A folderless session's own folder counts once made."""
    return list(dict.fromkeys(f for f in (S.folder_of(row), S.checkout_of(row)) if f))


class SessionBusy(Exception):
    """The session already has a turn running (or starting)."""


class WaitEnded(Exception):
    """A "Resume when ready" continuation found its wait gone when it was
    about to start: the user cancelled it, chose another account, or another
    pass took it. Nothing was started."""


def _command_rule(row: Any, setup: "dict[str, Any]", started: Any, continuation: bool) -> Any:
    """v1.381.0: this turn's :class:`~iron_jarvis.harness.command_shell.
    TurnRule` (None = nothing to ask about). BLOCKING (parses the transcript
    for a continuation): called through ``asyncio.to_thread``. A transcript
    that cannot be read is no continuation of anything — the recorded lines
    still apply."""
    from ...harness import command_shell as _cmd_shell

    messages: list[Any] = []
    if continuation and started is None:
        try:
            messages = S._messages(row)
        except Exception:  # noqa: BLE001
            messages = []
    return _cmd_shell.rule_for_turn(setup, messages, started=started, continuation=continuation)


async def start_build_turn(d: Any, thread_id: str, text: str, turn_id: str, *,
                           attachments: "list[str] | None" = None,
                           preview_url: str = "",
                           skill: str = "",
                           files: "list[str] | None" = None,
                           folder_skill: str = "",
                           command: str = "",
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
    own limit card can never be wiped by a clear that ran after it.

    v1.373.0 (part 2, Phase 2): ``skill`` — a skill picked with "/" rides the
    turn as ``ChatBody.skill`` (the lane's eager check refuses an unknown one
    with a 404 before anything is written or asked of a model); ``files`` —
    files picked with "@", checked to be inside the session's folder
    (``harness/sessions.folder_files``; 400 in words otherwise), then read
    like attachments; ``folder_skill`` — a skill of the folder's own harness
    layer (``harness/picked_skill``; 404 in words when the folder's valid
    layer has none by that name). The question line records them
    (``skill``, ``filePaths``).

    v1.379.0: ``command`` — one of Claude Code's own custom commands
    (``harness/claude_commands``), typed ``/<command> <text>``: ``text`` is
    its arguments; the command's file is expanded Claude Code's way and sent
    as the message (its ``@`` files ride as "@" files when they pass the
    folder's checks; its shell lines are NEVER run by the app — the model is
    told to run them with its own shell tool). The question line shows what
    was typed and keeps the expansion for later turns (``commandText``). A
    skill alone is a message too (its words: ``sessions.SKILL_ALONE``). A
    session with no folder makes its own folder now, at its first turn
    (:func:`make_work_folder`); a turn that then does not start leaves no
    empty folder behind."""
    from .chat import _turn_logs
    from ...iron_proxy.pin import reset_pin, set_pin

    platform = d.platform
    logs = _turn_logs(d)
    # v1.373.0 review: a blank entry is no upload (it was counted, saved on
    # the question and silently skipped by the lane).
    attachments = [str(a) for a in (attachments or []) if str(a or "").strip()]
    if thread_id in _COMPACTING:
        # v1.374.0: Compact holds the session; say so, not "still answering".
        raise HTTPException(status_code=409, detail=COMPACTING_SENTENCE)
    if thread_id in _STARTING or _running_turn(d, thread_id):
        raise SessionBusy(thread_id)
    _STARTING.add(thread_id)
    # v1.370.0 review: the session is not idle — its library reflection waits
    # (this turn's end starts the idle wait again).
    from ...harness import library as _library

    _library.note_turn_started(thread_id)
    # v1.379.0: a folder THIS call made for a session with no folder — taken
    # back (when still empty) if no turn starts after all.
    made_folder = ""
    started = False
    try:
        row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        if getattr(row, "archived_at", None) is not None:
            raise HTTPException(status_code=409, detail=ARCHIVED_SENTENCE)
        folder = S.folder_of(row)
        if folder and S.is_folderless(row) and await asyncio.to_thread(_make_again, folder):
            # v1.379.0 review: the user removed this session's own folder
            # between turns — it is made again at the same place (every later
            # message was refused "folder does not exist" for good). Taken
            # back, like a first one, if no turn starts after all.
            made_folder = folder
        if not folder and S.is_folderless(row):
            # v1.379.0: a session started with no folder gets its OWN folder
            # now, at its first turn (an abandoned empty session made none).
            # Review: a command names it "<command> <words>", not by its words
            # alone ("/review app.py" made "<date> app").
            command_name = str(command or "").strip().lstrip("/")
            named = f"{command_name} {text}".strip() if command_name else text
            try:
                folder, fresh = await asyncio.to_thread(
                    make_work_folder, platform, thread_id,
                    named or str(skill or "").strip() or str(folder_skill or "").strip(),
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This session's own folder could not be made in {platform.config.chat_files_dir}: "
                        f"{exc.strerror or exc}"
                    ),
                ) from None
            if fresh:
                made_folder = folder
            row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
            if row is None:
                raise HTTPException(status_code=404, detail="no such Build session")
        problem = await asyncio.to_thread(S.folder_problem, folder)
        if problem:
            raise HTTPException(
                status_code=409, detail=f"This session's folder cannot be used now: {problem}",
            )
        # v1.373.0: the "@" files — inside this session's folder, or nothing
        # is started (and nothing outside it is read).
        picked: list[str] = []
        if files:
            try:
                picked = await asyncio.to_thread(S.folder_files, folder, list(files))
            except S.FolderFileRefused as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
        # The lane reads this many attachments per turn and drops the rest
        # without a word (``chat_turn._MAX_ATTACHMENTS``). A file sent must be
        # READ, or the saved question would claim it was sent: a message that
        # would lose some is refused in words instead. v1.373.0 review: the
        # uploads ALONE count too — five pictures and no "@" file used to
        # pass, four were read and the question said five were sent.
        from ..chat_turn import _MAX_ATTACHMENTS

        total = len(attachments) + len(picked)
        if total > _MAX_ATTACHMENTS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"One message reads at most {_MAX_ATTACHMENTS} files, and this one has {total}. "
                    "Remove some, or send them over two messages."
                ),
            )
        # v1.373.0: a skill of the folder's own harness layer — its playbook,
        # read and scanned now, rides the harness block (``picked_skill``).
        folder_skill = str(folder_skill or "").strip()
        command = str(command or "").strip().lstrip("/")
        if command and (folder_skill or str(skill or "").strip()):
            raise HTTPException(status_code=400, detail="Send one command or one skill per message.")
        # What the MODEL is sent as this message, and what the question line
        # shows (v1.379.0: they differ for a command and for a skill alone).
        model_text = text
        content = text
        line_extra: dict[str, Any] = {}
        # v1.381.0: a PROJECT's or a PLUGIN's command with shell lines — every
        # command this turn runs asks first, in every posture
        # (harness/command_shell); None for anything else.
        from ...harness import command_shell as _cmd_shell

        command_entry: "_cmd_shell.CommandLines | None" = None
        if command:
            from ...harness import claude_commands as CC

            cmd = await asyncio.to_thread(CC.find, command_folders(row), command)
            if cmd is None:
                raise HTTPException(status_code=404, detail=f"There is no command /{command} for this session.")
            try:
                expanded = await asyncio.to_thread(
                    CC.expand, cmd, text, folder=folder, read_room=_MAX_ATTACHMENTS - total,
                    event_bus=getattr(platform, "event_bus", None), session_id=S.session_id_for(thread_id),
                )
            except CC.CommandRefused as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from None
            model_text = expanded.text
            picked += [f for f in expanded.files if f not in picked]
            content = f"/{cmd.name}" + (f" {text}" if text else "")
            line_extra = {"command": cmd.name, "commandText": expanded.text[:S.MAX_COMMAND_TEXT]}
            command_entry = _cmd_shell.from_expansion(expanded, turn_id)
        elif not text and not attachments and not picked and (folder_skill or str(skill or "").strip()):
            # v1.379.0: a skill picked with "/" and nothing else is a message.
            model_text = S.SKILL_ALONE.format(name=folder_skill or str(skill).strip())
        playbook = ""
        if folder_skill:
            if str(skill or "").strip():
                raise HTTPException(status_code=400, detail="Pick one skill per message.")
            from ...harness.picked_skill import read_playbook

            try:
                known = set(platform.registry.names())
            except Exception:  # noqa: BLE001 — the validator then skips tool names
                known = None
            # v1.375.0: the layer of a session on its own branch is its
            # worktree's, else the main checkout's (S.layer_folder_of).
            layer_folder = await asyncio.to_thread(S.layer_folder_of, row)
            got = await asyncio.to_thread(
                read_playbook, layer_folder, folder_skill, known_tools=known,
                event_bus=getattr(platform, "event_bus", None), session_id=S.session_id_for(thread_id),
            )
            if got is None:
                raise HTTPException(status_code=404, detail=f"no such skill in this folder: {folder_skill}")
            if not got.strip():
                # v1.373.0 review: listed by "/" (its header is the trigger
                # line) but nothing to follow — not "no such skill".
                raise HTTPException(
                    status_code=400,
                    detail=f"The skill {folder_skill} has nothing to follow: its SKILL.md holds only a header.",
                )
            playbook = got
        setup = _setup_of(row)
        # v1.377.0 (part 2, Phase 6): the Browser pane's address is KEPT on the
        # session with every message the screen sends (a local page only, or
        # nothing), so a CONTINUATION the daemon starts itself — "Resume when
        # ready", "Continue on another account", with nobody's screen behind
        # it — still knows the dev server the user was looking at.
        from ...harness.preview import local_address

        shown = local_address(preview_url)
        kept = local_address(str(setup.get("preview_url") or ""))
        if continuation and not shown:
            preview_url = kept
        elif not continuation and shown != kept:
            await asyncio.to_thread(S.update, platform.engine, thread_id,
                                    setup={"preview_url": shown or None})
        pin, account_provider = await asyncio.to_thread(turn_account, platform, thread_id, setup)
        from ...projects.locate import project_for_path

        # v1.375.0 (part 2, Phase 4) DECISION: a session on its own branch
        # belongs to the project of the folder the user PICKED — its worktree
        # sits beside the repository, outside every project's root
        # (S.checkout_of; the session's folder otherwise, as before).
        project = await asyncio.to_thread(project_for_path, platform.engine, S.checkout_of(row))
        # The transcript is up to 200 lines of JSON (replies with steps
        # and thinking): parsed OFF the loop, like every other read here.
        # Role + content only: no native carrier (no thinking, no signature)
        # ever crosses into a turn — so a session MOVES between accounts and
        # vendors with nothing another account would refuse (v1.368.0).
        # v1.374.0 (part 2, Phase 3): a standing summary (Compact) stands in
        # for the older turns — they are left out of the history, and the
        # summary rides the harness block (armed below with the turn).
        from ...harness import compact as _compact

        _history, _summary = await asyncio.to_thread(_compact.history_for_turn, row)
        history = [ChatMessageBody(**m) for m in _history]
        # v1.381.0: what this turn's command-running calls must ask about
        # (harness/command_shell) — every command when a project's or a
        # plugin's command with shell lines starts it (or it CONTINUES such a
        # turn), else a later command that runs one of the session's recorded
        # lines; None when the session has none (every turn exactly as
        # before). Armed below with the pin and the hold: the driver task
        # copies it, and so does every helper the turn starts.
        command_rule = await asyncio.to_thread(
            _command_rule, row, setup, command_entry, continuation,
        )
        if command_entry is not None:
            # Kept on the session, so a LATER turn's command that runs one of
            # these lines asks too (the expansion stays in the history the
            # model reads). The session's own write — never a client's (a
            # PATCH has no field for it). Written BEFORE anything starts (no
            # generator is open yet); a turn refused after this leaves a record
            # that only ever asks more. v1.381.0 review: a write that FAILS
            # stops the command here — its turn would ask for every command
            # anyway, but a carry-on of that turn reads only the record and
            # would ask for none (``COMMAND_RECORD_SENTENCE``).
            try:
                await asyncio.to_thread(
                    S.update, platform.engine, thread_id,
                    setup={_cmd_shell.SETUP_KEY: _cmd_shell.recorded_with(setup, command_entry)},
                )
            except Exception:  # noqa: BLE001
                log.warning("recording the shell lines of /%s failed", command_entry.name, exc_info=True)
                raise HTTPException(
                    status_code=503, detail=COMMAND_RECORD_SENTENCE.format(name=command_entry.name),
                ) from None
        chat_body = ChatBody(
            messages=[*history, ChatMessageBody(role="user", content=model_text)],
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
            attachments=[*attachments, *picked],
            local_only=setup.get("local_only") is True,
            skill=str(skill or "").strip(),
        )
        token = set_pin(pin) if pin is not None else None
        # v1.371.0: the Browser pane's address for this turn — armed while the
        # lane is built and the driver task created (the task copies the
        # context, like the pin): the harness prompt names it and
        # page_screenshot looks at it without asking.
        from ...harness.preview import reset_preview, set_preview

        preview_token = set_preview(preview_url)
        # v1.372.0: the session's MODEL is held for the turn and every helper
        # it starts, pinned account or not (``providers.hold``): nothing moves
        # the turn to another provider on its own.
        from ...providers.hold import reset_hold, set_hold

        hold_token = set_hold(*_turn_pair(platform, chat_body, project))
        # v1.373.0: the folder skill picked with "/", for the harness block.
        from ...harness.picked_skill import reset_picked, set_picked

        picked_token = set_picked(folder_skill, playbook)
        summary_token = _compact.set_summary(_summary)
        # v1.375.0 (part 2, Phase 4): the session's own git branch, for the
        # turn and its helpers (the driver task copies it, like the pin): the
        # prompt names it, the main checkout stays the user's, and a commit
        # or push the agent runs asks first (harness/git_guard). None for a
        # session that works in its folder directly.
        from ...harness import branches as _branches

        branch_token = _branches.set_turn_branch(_branches.facts_of(setup), folder)
        # v1.378.0: a failure in this turn is worded for a Build session
        # (`failure_words`: a safety refusal's way out is Rewind here, not a
        # new chat). The driver task copies the context, like the pin.
        from ...providers import failure_words as _failure_words

        surface_token = _failure_words.set_build_surface()
        # v1.381.0: the command rule (read above), armed last and reset first.
        command_token = _cmd_shell.set_turn_rule(command_rule)
        # v1.384.0: the reply's step limit — none by default (the safety
        # stop), else the user's number.
        rounds, no_limit = build_step_limit(platform.config)
        try:
            try:
                gen = await stream_chat_turn(
                    platform,
                    getattr(d, "_PERSONAS", {}),
                    chat_body,
                    # v1.384.0: NEVER the orphan guard — a Build reply keeps
                    # working while nobody watches (see `_never_orphaned`).
                    should_stop=_never_orphaned,
                    suggest_preferences=False,
                    harness="auto",
                    round_budget=rounds,
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
            question: dict[str, Any] = {"role": "user", "content": content, "turnId": turn_id, **line_extra}
            if attachments:
                question["attachmentPaths"] = list(attachments)
            if picked:
                question["filePaths"] = list(picked)
            if chat_body.skill or folder_skill:
                question["skill"] = chat_body.skill or folder_skill
                # v1.377.0 review: WHERE it came from, so a rewind or a
                # branch can put the chip back in the box exactly.
                question["skillSource"] = "yours" if chat_body.skill else "folder"
            if continuation:
                question["continuation"] = True
            saved = await asyncio.to_thread(S.append_lines, platform.engine, thread_id, [question])
            if not saved.get("ok"):
                with contextlib.suppress(Exception):
                    await gen.aclose()
                raise HTTPException(status_code=404, detail="no such Build session")
            lg = logs.start(turn_id, thread_id, recorded_turn(
                platform.engine, thread_id, turn_id, gen, pin=pin, platform=platform,
                no_limit=no_limit,
            ))
            started = True
        finally:
            _cmd_shell.reset_turn_rule(command_token)
            _failure_words.reset_surface(surface_token)
            _branches.reset_turn_branch(branch_token)
            _compact.reset_summary(summary_token)
            reset_picked(picked_token)
            reset_hold(hold_token)
            reset_preview(preview_token)
            if token is not None:
                reset_pin(token)
        lg.surface = "build"  # the waiting row says it is not a chat
    finally:
        _STARTING.discard(thread_id)
        if made_folder and not started:
            # v1.379.0: no turn started after all — the folder made for it
            # goes again (only while it is empty), so a refused first message
            # leaves nothing behind; the next one makes a new folder.
            with contextlib.suppress(Exception):
                if await asyncio.to_thread(_remove_if_empty, made_folder):
                    await asyncio.to_thread(S.set_work_folder, platform.engine, thread_id, None)
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


def rewind_commit_facts(row: Any, cut_at: Any) -> dict[str, Any]:
    """v1.377.0 (part 2, Phase 6): a rewind to before a message whose work was
    COMMITTED on the session's own branch says so — the files go back as
    changes not committed yet, the commits stay on the branch
    (``branches.commits_since`` / ``commits_note``). ``{}`` for a session with
    no branch or nothing committed since. BLOCKING (git); never raises."""
    from ...harness import branches as B

    try:
        facts = B.facts_of(_setup_of(row))
        commits = B.commits_since(facts, cut_at) if facts else []
    except Exception:  # noqa: BLE001
        return {}
    if not commits or not facts:
        return {}
    branch = facts.get("branch", "")
    return {
        "committed": {"branch": branch, "count": len(commits), "commits": commits[:5]},
        "commits_note": B.commits_note(commits, branch),
    }


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
    if thread_id in _COMPACTING:
        return JSONResponse(status_code=409, content={"detail": COMPACTING_SENTENCE})
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
                getattr(platform, "permissions", None),
            )
        except R.RewindError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        if plan.problems:
            return JSONResponse(status_code=409, content={
                "detail": R.problems_sentence(plan.problems), "rewound": False, **plan.view(),
            })
        # v1.377.0: read before anything moves (the commits stay either way).
        committed = await asyncio.to_thread(rewind_commit_facts, row, plan.cut_at)
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
        # The experience library's half: a reflection still running on a
        # rewound turn stops, and the PROPOSALS made from rewound turns go —
        # they describe work that is no longer there (an accepted lesson was
        # the user's decision and stays).
        from ...harness import library as _library

        _library.cancel_reflections(thread_id, plan.turns)
        dropped = await asyncio.to_thread(_library.forget_turns, platform.engine, thread_id, plan.turns)
        bus = getattr(platform, "event_bus", None)
        if bus is not None:
            with contextlib.suppress(Exception):
                await bus.publish(EventType.BUILD_REWOUND, {
                    "thread_id": thread_id, "to": to, "turns": len(plan.turns),
                    "files": len(plan.files),
                }, session_id=S.session_id_for(thread_id))
        return {
            "rewound": True, **plan.view(), **committed,
            "note": R.commands_note(plan.commands),
            "messages": cut.get("count", 0),
            "mode": cut.get("mode", S.MODE_ACT),
            "library_dropped": dropped,
            "updated_at": cut.get("updated_at"),
        }
    finally:
        _STARTING.discard(thread_id)


# --------------------------------------------------------------------------- #
# a new session on its own git branch (v1.375.0, part 2 Phase 4)
# --------------------------------------------------------------------------- #


async def _create_on_branch(d: Any, folder: str, body: Any, setup: dict[str, Any],
                            engine_of: Any, full_view: Any) -> Any:
    """A NEW session in ``folder`` on its own branch ``ij/<name>`` in a
    worktree beside the repository (``harness/branches.start`` — the user's
    decisions 1 and 2). A folder that is not in a repository (or git not
    installed) is a session in the folder, exactly as before. ``body.branch``
    True: a folder that cannot have a branch is refused in words; None: it
    works in the folder and the answer says why (``branch_note``). Every git
    call is off the loop."""
    from ...harness import branches as B

    info = await asyncio.to_thread(B.inspect_folder, folder)
    if not info["repo"]:
        if body.branch is True:
            raise HTTPException(status_code=400, detail=info["note"] or B.NOT_A_REPO)
        row = await asyncio.to_thread(S.create, engine_of(), folder, body.title, setup)
        return await asyncio.to_thread(full_view, row)
    if not info["can_branch"]:
        if body.branch is True:
            raise HTTPException(status_code=400, detail=info["note"])
        row = await asyncio.to_thread(S.create, engine_of(), folder, body.title, setup)
        view = await asyncio.to_thread(full_view, row)
        view["branch_note"] = info["note"]
        return view
    try:
        started = await asyncio.to_thread(B.start, folder, title=body.title)
    except B.GitRefused as exc:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.details:
            content["details"] = exc.details
        return JSONResponse(status_code=exc.status, content=content)
    work = started.pop("folder")
    problem = await asyncio.to_thread(S.folder_problem, work)
    if problem:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(B.remove_worktree, d.platform.config.home, started)
        raise HTTPException(status_code=400, detail=f"The session's worktree cannot be used: {problem}")
    try:
        row = await asyncio.to_thread(
            S.create, engine_of(), work, body.title, {**setup, B.SETUP_KEY: started},
        )
    except Exception:
        # The session could not be saved: its worktree goes to the trash (the
        # branch stays), never left behind with no session that knows it.
        with contextlib.suppress(Exception):
            await asyncio.to_thread(B.remove_worktree, d.platform.config.home, started)
        raise
    return await asyncio.to_thread(full_view, row)


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
        # v1.375.0: a session on its own branch is named by the folder the
        # user picked (S.checkout_of — the session's folder otherwise).
        folder = S.checkout_of(row)
        names = S.folder_names(engine_of(), [folder]) if folder else {}
        view = S.session_view(row, states=TURNS.thread_states(), names=names, full=True)
        try:
            queued = json.loads(getattr(row, "queued_json", "") or "[]")
        except (TypeError, ValueError):
            queued = []
        view["queued"] = [q for q in queued if isinstance(q, dict)] if isinstance(queued, list) else []
        view["turn_id"] = _running_turn(d, row.id) if view["running"] else ""
        # v1.377.0 (part 2, Phase 6): Compact is writing this session's summary
        # right now — a screen that comes back to it mid-Compact says
        # "Summarizing earlier turns…" (a send would be refused meanwhile).
        view["compacting"] = row.id in _COMPACTING
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
        if view.get("no_folder"):
            # v1.379.0: where a session with no folder makes its own folder
            # (the header says it before the first turn makes it).
            view["work_root"] = str(d.platform.config.chat_files_dir)
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
        out = S.list_grouped(engine_of(), include_archived=include, states=TURNS.thread_states())
        # v1.379.0: where a session started with no folder keeps its files
        # (New session says so in one line).
        out["work_root"] = str(d.platform.config.chat_files_dir)
        return out

    @app.post("/build/sessions", status_code=201)
    async def create_build_session(body: BuildSessionCreate) -> dict[str, Any]:
        """A new session working in ``folder``. The folder must be usable
        (``fs_policy.root_problem``: absolute, on this PC, allowed, and
        writable) — refused with the reason otherwise. v1.379.0: no folder
        at all is a session that works in its OWN folder under the chat files
        folder, made with its first turn (no git, no branch)."""
        folderless = not (body.folder or "").strip()
        if folderless and body.branch is True:
            raise HTTPException(status_code=400, detail=NO_FOLDER_NO_BRANCH)
        folder = "" if folderless else S.resolved_folder(body.folder)
        if not folderless:
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
        if folderless:
            row = await asyncio.to_thread(
                S.create, engine_of(), "", body.title, {**setup, S.NO_FOLDER_KEY: True},
            )
            return await asyncio.to_thread(_full_view, row)
        if body.branch is False:
            # v1.375.0: "work in the folder directly" — exactly the old path,
            # no git call at all.
            row = await asyncio.to_thread(S.create, engine_of(), folder, body.title, setup)
            return await asyncio.to_thread(_full_view, row)
        return await _create_on_branch(d, folder, body, setup, engine_of, _full_view)

    @app.get("/build/sessions/{thread_id}")
    def get_build_session(thread_id: str) -> dict[str, Any]:
        """One session: its folder (and the folder's name), mode, plan,
        setup, transcript, queue, and the running turn's id (to rejoin with
        ``GET /chat/turns/{id}/stream``)."""
        return _full_view(_session_or_404(thread_id))

    @app.get("/build/sessions/{thread_id}/browser")
    def build_agent_browser(thread_id: str) -> dict[str, Any]:
        """v1.376.0 (part 2, Phase 5): what the session's AGENT'S BROWSER
        last saw, for the Browser pane — ``{view: null}`` when it has not
        opened a page, else ``{view: {open, where, blank, title, at, shot,
        shot_at, note, closed_reason?}}`` (``where``: host and path, never
        the raw address — a query string can carry a token; ``blank``: a
        blank page; ``shot``: whether ``…/browser/shot`` has a
        picture). In memory, never a browser call: the pane reads it on
        open, when a page tool of the session finishes and when a turn ends."""
        from ...harness.page_browser import BROWSERS

        _session_or_404(thread_id)
        return {"view": BROWSERS.view(S.session_id_for(thread_id))}

    @app.get("/build/sessions/{thread_id}/browser/shot")
    def build_agent_browser_shot(thread_id: str) -> Any:
        """The picture behind ``…/browser`` (a JPEG of what the agent's
        browser showed after its last action); 404 when there is none."""
        from fastapi.responses import Response

        from ...harness.page_browser import BROWSERS

        _session_or_404(thread_id)
        data = BROWSERS.shot(S.session_id_for(thread_id))
        if not data:
            raise HTTPException(status_code=404, detail="The agent's browser has no picture yet.")
        return Response(content=data, media_type="image/jpeg")

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
            # v1.370.0 review: an archived session is left — its library
            # reflection runs now over the turns noted since the last one
            # (still only when its account has room; never raises).
            from ...harness import library as _library

            _library.flush(d.platform, thread_id)
            # v1.379.0: a session with no folder takes its own folder with it
            # when nothing was made there (one with files is kept).
            await asyncio.to_thread(drop_empty_work_folder, d.platform, thread_id)
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

        row = _session_or_404(thread_id)
        from .build_git import _GIT_ACTING, GIT_BUSY

        if thread_id in _GIT_ACTING:
            return JSONResponse(status_code=409, content={"detail": GIT_BUSY})
        if TURNS.thread_states().get(thread_id, {}).get("running") or thread_id in _STARTING:
            return JSONResponse(status_code=409, content={"detail": RUNNING_SENTENCE})
        # v1.375.0 (part 2, Phase 4) DECISION: deleting a session on its own
        # branch never silently destroys work that is not merged — its
        # worktree MOVES to the trash first (the Discard path) and the BRANCH
        # STAYS (its commits are kept until the user deletes the branch). A
        # worktree that cannot be moved (a program or a terminal open in it)
        # refuses the delete in words, with nothing deleted.
        from ...harness import branches as B

        facts = B.facts_of(_setup_of(row))
        moved: dict[str, Any] | None = None
        if facts and not facts.get("removed_at"):
            from .build_git import _locked

            S.end_session_programs(thread_id)
            try:
                # Under the session's git lock: a Branch view read in flight
                # (a git call whose working folder is the worktree) finishes
                # before the folder is moved.
                moved = _locked(thread_id, lambda: B.remove_worktree(d.platform.config.home, facts))
            except B.GitRefused as exc:
                content: dict[str, Any] = {
                    "detail": f"{exc} The session was not deleted.",
                }
                if exc.details:
                    content["details"] = exc.details
                return JSONResponse(status_code=exc.status, content=content)
        # v1.379.0: a session with no folder takes its own folder with it when
        # nothing was made there (one with files is kept, and said so below).
        kept_folder = ""
        if S.is_folderless(row):
            S.end_session_programs(thread_id)
            if not drop_empty_work_folder(d.platform, thread_id):
                kept_folder = S.folder_of(row)
        if not delete_thread(engine_of(), thread_id):
            raise HTTPException(status_code=404, detail="no such Build session")
        out: dict[str, Any] = {"deleted": thread_id}
        if moved is not None:
            out["worktree"] = {**moved, "branch": facts.get("branch", "")}
        if kept_folder:
            out["kept_folder"] = kept_folder
        return out

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
                    getattr(platform, "permissions", None),
                )
                # v1.377.0: committed work since that message is said BEFORE
                # the second press (the files go back; the commits stay).
                row = await asyncio.to_thread(S.get_row, platform.engine, thread_id)
                committed = (
                    await asyncio.to_thread(rewind_commit_facts, row, plan.cut_at)
                    if row is not None else {}
                )
                return {**plan.view(), **committed, "note": R.commands_note(plan.commands),
                        "detail": R.problems_sentence(plan.problems) if plan.problems else ""}
            points = await asyncio.to_thread(R.checkpoints, platform.engine, thread_id)
        except R.RewindError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        return {"checkpoints": points}

    @app.get("/build/sessions/{thread_id}/skills")
    def build_folder_skills(thread_id: str) -> dict[str, Any]:
        """v1.373.0 (part 2, Phase 2): the session FOLDER's own harness skills
        for the box's "/" menu (``{skills: [{name, description, rel}], note}``)
        — only a VALID layer's (the turn would not read the others); an
        invalid layer offers none and its ``note`` says why. The user's own
        skills are ``GET /skills``, as in Chat. Sync: the threadpool runs it."""
        from ...harness.picked_skill import folder_skills

        row = S.get_row(d.platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        try:
            known = set(d.platform.registry.names())
        except Exception:  # noqa: BLE001 — the validator then skips tool names
            known = None
        # v1.375.0: a branch's layer is its worktree's, else the main
        # checkout's (S.layer_folder_of — the session's folder otherwise).
        skills, note = folder_skills(S.layer_folder_of(row), known_tools=known)
        return {"skills": skills, "note": note}

    @app.get("/build/sessions/{thread_id}/commands")
    def build_session_commands(thread_id: str) -> dict[str, Any]:
        """v1.379.0: Claude Code's own custom "/" commands for this session,
        for the box's "/" menu — ``{commands: [{name, description,
        argument_hint, source: personal|project|plugin, plugin, kind:
        command|skill, skill?}], note}``: the user's personal commands, the
        session folder's (and, on a branch, the picked folder's)
        ``.claude/commands``, and every ENABLED plugin's commands — plus a
        plugin's skills under Claude Code's ``<plugin>:<skill>`` names (kind
        "skill", run as the app's own skill of that name). A catalog plugin
        that was never installed is not offered. Names, descriptions and
        argument hints only — never a command's text. Sync: the threadpool
        runs it (stats and small reads, cached)."""
        from ...harness import claude_commands as CC

        row = S.get_row(d.platform.engine, thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such Build session")
        found = CC.discover(command_folders(row), registry=getattr(d.platform, "skills", None))
        return {
            "commands": [c.row() for c in found.commands] + [s.row() for s in found.skills],
            "note": found.note,
        }

    @app.post("/build/sessions/{thread_id}/rewind")
    async def build_rewind(thread_id: str, body: BuildRewindBody) -> Any:
        """Rewind the session to before turn ``to`` — files and conversation
        (:func:`rewind_session`)."""
        return await rewind_session(d, thread_id, body.to.strip())

    @app.post("/build/sessions/{thread_id}/followups")
    async def build_followups(thread_id: str) -> dict[str, Any]:
        """NEXT STEP (v1.377.0, part 2 Phase 6): up to two questions the user
        might ask next, after the session's newest reply — the chat's
        follow-ups (``chat_followups``, off by default: no model call when
        off) on the SESSION's own model and account, pinned, in its Local only
        scope, billed to the session (``harness/followups``). Never a 500 for
        a model failure: ``{suggestions: [], reason}``."""
        from ...harness import followups as _followups

        await asyncio.to_thread(_session_or_404, thread_id)
        running = thread_id in _STARTING or bool(_running_turn(d, thread_id))
        return await _followups.suggest(d.platform, thread_id, running=running)

    @app.post("/build/sessions/{thread_id}/branch", status_code=201)
    async def branch_build_session(thread_id: str, body: BuildBranchBody) -> Any:
        """BRANCH FROM A MESSAGE (v1.377.0, part 2 Phase 6): a NEW session
        holding this session's conversation up to one of its messages, in the
        same folder, on the same model and account — NOTHING of this session
        changes. DECISIONS: a session on its own git branch gets the new
        session its OWN new branch and worktree, never the same worktree
        (two sessions would write the same files), started where the
        conversation was at that message — the original branch's newest
        commit at or before it, else where the original session started
        (``branches.branch_point``, the v1.377.0 review; work not committed
        is not carried and ``branched_from.code`` says so); the new
        session starts in act mode with no approved plan and no conversation
        grants (it asks again), and the account, model, reasoning, approval
        setting, Local only and the Browser pane's address go with it. A
        question branch answers ``draft`` (the question's words and files)
        for the new session's box."""
        from ...harness import branches as B

        at = (body.at or "reply").strip().lower()
        if at not in ("reply", "question"):
            raise HTTPException(status_code=400, detail="at must be 'reply' or 'question'")
        engine = engine_of()
        row = await asyncio.to_thread(_session_or_404, thread_id)
        setup = _setup_of(row)
        messages = await asyncio.to_thread(S._messages, row)
        turn_id = body.turn_id.strip()
        try:
            lines, question = S.branch_lines(messages, turn_id, at)
        except S.BranchRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        title = S.BRANCH_TITLE.format(title=(row.title or S.NEW_SESSION_TITLE))
        new_setup: dict[str, Any] = {
            k: setup[k] for k in (
                "provider", "model", "reasoning", "approval_mode", "local_only", "account",
                "account_name", "preview_url",
                # v1.381.0: the branch's history holds the same commands'
                # expansions, so their shell lines still ask there
                # (harness/command_shell).
                "command_shell",
            ) if setup.get(k) not in (None, "", [])
        }
        branched = {"thread_id": thread_id, "title": row.title or "", "turn_id": turn_id, "at": at}
        facts = B.facts_of(setup)
        if facts:
            # Its OWN branch, in its own worktree beside the repository, from
            # where the conversation was at that message (off the loop: git).
            cut_at = lines[-1].get("at") if lines else None
            point = await asyncio.to_thread(B.branch_point, facts, cut_at, bool(lines))
            start_point = str(point.get("commit") or "")
            if point:
                branched["code"] = {
                    **{k: point[k] for k in ("short", "subject", "from", "uncommitted")},
                    "branch": facts.get("branch", ""),
                }
            checkout = facts.get("checkout") or facts.get("repo", "")
            problem = await asyncio.to_thread(S.folder_problem, checkout)
            if problem:
                raise HTTPException(status_code=409, detail=f"This session's folder cannot be used now: {problem}")
            try:
                started = await asyncio.to_thread(
                    lambda: B.start(checkout, title=title, start_point=start_point,
                                    base=facts.get("base", "")),
                )
            except B.GitRefused as exc:
                content: dict[str, Any] = {"detail": str(exc)}
                if exc.details:
                    content["details"] = exc.details
                return JSONResponse(status_code=exc.status, content=content)
            work = started.pop("folder")
            branched["branch"] = started.get("branch", "")
            try:
                new_row = await asyncio.to_thread(
                    S.create_with_lines, engine, work, title,
                    {**new_setup, B.SETUP_KEY: started, "branched_from": branched}, lines,
                )
            except Exception:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(B.remove_worktree, d.platform.config.home, started)
                raise
        elif S.is_folderless(row):
            # v1.379.0: a branch of a session with no folder has no folder
            # either, and works in its origin's OWN folder when one was made
            # (like a branch of a folder session, which shares its folder);
            # before the first turn it makes its own.
            folder = S.folder_of(row)
            if folder:
                problem = await asyncio.to_thread(S.folder_problem, folder)
                if problem:
                    raise HTTPException(status_code=409, detail=f"This session's folder cannot be used now: {problem}")
            new_row = await asyncio.to_thread(
                S.create_with_lines, engine, folder, title,
                {**new_setup, S.NO_FOLDER_KEY: True, "branched_from": branched}, lines,
            )
        else:
            folder = S.folder_of(row)
            problem = await asyncio.to_thread(S.folder_problem, folder)
            if problem:
                raise HTTPException(status_code=409, detail=f"This session's folder cannot be used now: {problem}")
            new_row = await asyncio.to_thread(
                S.create_with_lines, engine, folder, title, {**new_setup, "branched_from": branched}, lines,
            )
        view = await asyncio.to_thread(_full_view, new_row)
        if question is not None:
            from ...harness.rewind import _paths

            view["draft"] = {
                "text": str(question.get("content") or ""),
                "attachments": _paths(question.get("attachmentPaths")),
                "picked_files": _paths(question.get("filePaths")),
                "skill": str(question.get("skill") or "") if isinstance(question.get("skill"), str) else "",
                "skill_source": skill_source(question),
            }
        return view

    @app.post("/build/sessions/{thread_id}/compact")
    async def build_compact(thread_id: str, body: BuildCompactBody | None = None) -> dict[str, Any]:
        """COMPACT (v1.374.0, part 2 Phase 3): summarize the session's older
        turns with its OWN model and account, check the summary against the
        conversation and the session's ledger, and keep it on the session —
        later turns read it in place of those turns; the transcript is never
        cut (``harness/compact``). Refused while a turn runs or starts; the
        session is held while the model writes (no turn, rewind or resumed
        wait starts meanwhile). Answers the stored summary (what the card
        shows) plus ``window``; a refusal in words (400 nothing to summarize /
        no real model, 409 running / changed, 409 the local-only shape, 502
        nothing could be checked / no summary / the model failed). v1.379.0
        review: ``focus`` — "/compact keep the API decisions" — is what the
        summary should keep in view (Claude Code's own words after
        ``/compact``; they were dropped)."""
        from ...harness import compact as _compact

        if thread_id in _COMPACTING:
            raise HTTPException(status_code=409, detail=COMPACTING_SENTENCE)
        if thread_id in _STARTING or _running_turn(d, thread_id):
            raise HTTPException(status_code=409, detail=COMPACT_RUNNING)
        _STARTING.add(thread_id)
        _COMPACTING.add(thread_id)
        try:
            focus = (body.focus if body is not None else "").strip()
            if focus:
                return await _compact.compact(d.platform, thread_id, focus=focus)
            return await _compact.compact(d.platform, thread_id)
        except _compact.CompactRefused as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        finally:
            _COMPACTING.discard(thread_id)
            _STARTING.discard(thread_id)

    @app.post("/build/sessions/{thread_id}/turn")
    async def build_turn(thread_id: str, body: BuildTurnBody):
        """ONE build turn (SSE — the same frames as ``POST /chat/stream``).
        See the module docstring. A ``turn_id`` already running for this
        session is FOLLOWED, never started twice."""
        from .chat import _follow_response, _turn_logs

        platform = d.platform
        text = (body.text or "").strip()
        # v1.373.0 review: a blank entry is no file — `{"files": ["  "]}` used
        # to pass this check and start a turn that sent the model nothing.
        has_files = any(str(p or "").strip() for p in [*(body.attachments or []), *(body.files or [])])
        picks = bool((body.skill or "").strip() or (body.folder_skill or "").strip() or (body.command or "").strip())
        if not text and not has_files and not picks:
            # v1.371.0: a picture alone is a message (the chat page's rule,
            # v1.275.0) — v1.373.0: so is a file picked with "@"; v1.379.0:
            # so is a skill or a command typed with "/" (Claude Code runs
            # "/name" alone); nothing at all is not.
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
                recorded = await asyncio.to_thread(_turn_record, platform.engine, turn_id,
                                                   with_ids=body.ids)
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
                return _follow_response(existing.follow(with_ids=body.ids))
        else:
            turn_id = "bt_" + secrets.token_hex(8)
        try:
            lg = await start_build_turn(d, thread_id, text, turn_id,
                                        attachments=list(body.attachments or []),
                                        preview_url=body.preview_url,
                                        skill=body.skill,
                                        files=list(body.files or []),
                                        folder_skill=body.folder_skill,
                                        command=body.command)
        except SessionBusy:
            return JSONResponse(
                status_code=409,
                content={"detail": RUNNING_SENTENCE, "turn_id": _running_turn(d, thread_id)},
            )
        # v1.384.0: `ids` — every frame tagged with its resume position, so a
        # page that leaves mid-reply comes back with only what it missed.
        return _follow_response(lg.follow(with_ids=body.ids))

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
