"""The Code view's sessions, turns, asks and processes (v1.409.0).

ONE :class:`HostedService` per platform (``platform.hosted``, made on first
use by :func:`service_for`). The routes (``daemon/routes/build_app.py``) are
thin: every rule lives here.

THE TURN IS THE DAEMON'S (the ``durable_turns`` pattern): ``POST …/turn``
starts it as its own task and the response only FOLLOWS it
(:meth:`HostedTurn.follow`: what it said so far, then each new event, then
the end). Closing the page never stops a turn — only Stop (``POST
…/interrupt``) does, or the harness ending. Any number of windows rejoin
(``GET …/turns/{id}/stream``); a turn this process no longer holds is replayed
from the session's ``events.jsonl``. A turn ENDS in exactly one place
(:meth:`HostedService._end_turn`): the row's ``last_error`` is written first,
then the ``done`` reaches the log and every follower.

ONE TURN AT A TIME: a message sent while one runs is refused in words
(:data:`hosted.words.BUSY`) — Claude Code would queue it, Codex would not, and
a queued message would have no turn of its own to follow. v1.412.0: what the
user types WHILE it works is a NOTE (:meth:`HostedService.steer`) — part of
the running turn, given through each tool's own way of taking one (Claude
Code: a user message mid-turn, read at its next step or right after; Codex:
``turn/steer``; Pi: ``steer``). A ``steer`` event says it in order (sent →
read); a note the tool never read when the turn ends goes back to the box
(``returned``) — never dropped. Stop cancels notes not yet read.

ONE HARNESS SESSION, ONE PROCESS: two processes resuming one Claude Code
session (or one Codex thread) corrupt it. A create, an open-from-terminal and
a message are refused in words when another live Code-view row or a Build
terminal holds that conversation; Open in terminal marks the row, and its next
message is refused until that terminal's CLI has exited.

ONE THING AT A TIME PER SESSION: continue on another account, delete and
archive hold a CLAIM across check → stop → copy; a message during a claim is
refused in words. Stopping a process always ends its running turn
("interrupted") and closes its open cards.

THE POOL: at most :data:`MAX_LIVE` harness processes; when a new one is needed
the oldest IDLE one (no turn, no open ask) is stopped first, else the start is
refused in words. A process with no turn for :data:`IDLE_S` is stopped by the
reaper; the next message starts it again RESUMING THE SAME harness session
(``claude --resume <id>`` / ``thread/resume``). Archive, delete and the
daemon's shutdown stop it. Every stop is a tree kill, off the loop.

NOTHING IS GRANTED HERE: every permission question the harness asks becomes a
card (``approval`` / ``question``); only the user's answer goes back. "Allow
for this session" is the harness's OWN session-wide allow, offered only when
the harness offers one (and never one that would switch on Full access).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.ids import utcnow
from . import store
from . import words as W
from .claude import HARNESS as CLAUDE
from .claude import MODE_ABOUT as CLAUDE_ABOUT
from .claude import MODE_LABELS
from .claude import ClaudeDriver
from .claude import MODES as CLAUDE_MODES
from .codex import HARNESS as CODEX
from .codex import HIDDEN_MODES as CODEX_HIDDEN
from .codex import MODE_ABOUT as CODEX_ABOUT
from .codex import MODES as CODEX_MODES
from .codex import CodexDriver
from .driver import DECISIONS, Ask, AskRefused, ControlRefused, Driver
from . import pi as P
from .pi import HARNESS as PI
from .pi import PiDriver
from .events import TEXT_CHARS, EventLog, clip, coalesce_into, make, session_dir
from .process import HarnessGone, HarnessProcess, binary_for, build_env

log = logging.getLogger(__name__)

HARNESSES: tuple[str, ...] = (CLAUDE, CODEX, PI)
#: At most this many harness processes at once.
MAX_LIVE = 6
#: A process with no turn for this long is stopped by the reaper.
IDLE_S = 15 * 60.0
#: How often the reaper looks.
REAP_EVERY_S = 60.0
#: How long a finished turn stays in memory for a rejoin.
FINISHED_KEEP_S = 600.0
#: The newest events ``GET /build/app/sessions/{id}`` returns by default.
DEFAULT_EVENTS = 2000
#: A turn's in-memory replay keeps at most this many characters of streamed
#: text; past it only the non-delta events are kept (``text_done`` still
#: carries every answer whole).
MAX_TURN_CHARS = 8_000_000
#: The interrupt route answers within this (the harness is told; the turn
#: ends when it says so, or when its process goes).
INTERRUPT_WAIT_S = 5.0
#: v1.412.0: a note sent while the tool is still STARTING its turn waits this
#: long for the turn to be open before it is refused in words.
STEER_WAIT_S = 20.0
#: Disk listings read at most this many conversation files (newest first).
DISK_FILES = 500

#: Effort levels each harness takes.
EFFORTS: dict[str, tuple[str, ...]] = {
    CLAUDE: ("low", "medium", "high", "xhigh", "max"),
    CODEX: ("minimal", "low", "medium", "high", "xhigh"),
    PI: P.EFFORTS,
}
_MODEL_RX = re.compile(r"^[A-Za-z0-9._:/\-\[\]]{1,120}$")
#: A harness session id typed into a terminal: a UUID (Claude) or Codex's
#: UUID-shaped thread id — nothing else is ever typed.
_SID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

STOPPED_TITLE = "Iron Jarvis stopped while this was working"
STOPPED_FIX = "Send the message again — the conversation is kept."


class Refused(Exception):
    """A request the Code view refuses, in one plain sentence."""

    def __init__(self, sentence: str, status: int = 409, field: str = "", code: str = "") -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status
        #: v1.412.0 review: which choice of the start composer the refusal is
        #: about ("folder", "tool", "account", "mode", "model", "effort"; ""
        #: = none in particular) — the page says it on THAT chip.
        self.field = field
        #: v1.412.0: a machine word for a refusal the page acts on
        #: ("not_working": a note with no reply running — send it as a message).
        self.code = code


def same_folder(a: Any, b: Any) -> bool:
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except (TypeError, ValueError):
        return False


def modes_for(harness: str) -> dict[str, Any]:
    if harness == PI:
        return P.MODES
    return CLAUDE_MODES if harness == CLAUDE else CODEX_MODES


#: Every preset in the menu's order (the shared ones, Claude Code's own Auto
#: (v1.412.0), Full access, then Pi's own).
MODE_ORDER: tuple[str, ...] = ("ask", "edits", "plan", "auto", "full", P.OWN)


def mode_about(harness: str, mode: str) -> str:
    """v1.412.0: what a preset does for THIS tool, in plain words (the mode
    menu says it under each one)."""
    if harness == PI:
        return P.MODE_ABOUT.get(mode, "")
    if harness == CODEX:
        return CODEX_ABOUT.get(mode, "")
    return CLAUDE_ABOUT.get(mode, "")


def mode_label(harness: str, mode: str) -> str:
    """A preset's words for this tool ("Pi's own rules" is Pi's alone)."""
    if harness == PI and mode in P.MODE_LABELS:
        return P.MODE_LABELS[mode]
    return MODE_LABELS.get(mode, mode)


def default_mode(harness: str) -> str:
    """The preset a new session starts in: Ask first, else the tool's first."""
    modes = modes_for(harness)
    return "ask" if "ask" in modes else next(iter(modes))


#: How long a session's title from its first message may be.
TITLE_CHARS = 60


def first_message_title(text: str) -> str:
    """A session's title from its first message: the first line with words,
    spaces collapsed, masked and cut to :data:`TITLE_CHARS` (an ellipsis marks
    a cut). "" when the message has no words (files only)."""
    for line in str(text or "").splitlines():
        words = " ".join(line.split())
        if words:
            return clip(words, TITLE_CHARS)
    return ""


def mode_rows(harness: str) -> list[dict[str, Any]]:
    return [
        {"id": m, "label": mode_label(harness, m), "dangerous": m == "full", "about": mode_about(harness, m)}
        for m in MODE_ORDER if m in modes_for(harness)
    ]


def hidden_mode_rows(harness: str) -> list[dict[str, str]]:
    if harness == CLAUDE:
        return []
    hidden = P.HIDDEN_MODES if harness == PI else CODEX_HIDDEN
    return [{"id": m, "label": MODE_LABELS.get(m, m), "reason": why} for m, why in hidden.items()]


def check_mode(harness: str, mode: str, confirm: bool) -> str:
    mode = str(mode or default_mode(harness)).strip().lower()
    if mode not in modes_for(harness):
        hidden = dict((r["id"], r["reason"]) for r in hidden_mode_rows(harness))
        if mode in hidden:
            raise Refused(f"{MODE_LABELS.get(mode, mode)} is not offered for {W.label(harness)}: {hidden[mode]}", 400)
        offered = [m for m in MODE_ORDER if m in modes_for(harness)]
        raise Refused(f'"{mode}" is not a mode {W.label(harness)} has — use {" or ".join(offered)}.', 400)
    if mode == "full" and not confirm:
        raise Refused(W.FULL_NEEDS_CONFIRM.format(label=W.label(harness)), 400)
    return mode


def check_model(model: Any) -> str:
    text = str(model or "").strip()
    if text and not _MODEL_RX.match(text):
        raise Refused("That model name has characters a model id never has.", 400)
    return text


def efforts_for(harness: str, model: Any = "", pi_rows: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    """v1.412.0 review: the effort levels a session on ``model`` can take, as
    far as is KNOWN without starting anything — Claude Code's cached picker
    (``claude_models.model_efforts``: ``()`` when the model takes none), Pi's
    last model list (a model it says has no reasoning takes none); otherwise
    the tool's own list. Never a probe."""
    if harness == CLAUDE:
        try:
            from ..providers import claude_models

            known = claude_models.model_efforts(str(model or ""))
        except Exception:  # noqa: BLE001 — unknown: the tool's own list
            known = None
        if known is not None:
            return tuple(x for x in EFFORTS[CLAUDE] if x in known)
    if harness == PI and model and pi_rows:
        for r in pi_rows:
            if r.get("id") == model and isinstance(r.get("efforts"), list):
                return tuple(x for x in EFFORTS[PI] if x in r["efforts"])
    return EFFORTS[harness]


def check_effort(harness: str, effort: Any, model: Any = "", pi_rows: list[dict[str, Any]] | None = None) -> str:
    text = str(effort or "").strip().lower()
    if not text:
        return text
    offered = efforts_for(harness, model, pi_rows)
    if not offered:
        raise Refused(f"That {W.label(harness)} model takes no effort level — leave it on the default.",
                      400, field="effort")
    if text not in offered:
        raise Refused(f'"{text}" is not an effort {W.label(harness)} takes — use '
                      f"{', '.join(offered)}.", 400, field="effort")
    return text


# --------------------------------------------------------------------------- #
# Homes and the harnesses' own saved sessions
# --------------------------------------------------------------------------- #
def default_home(harness: str) -> str:
    """The CLI's own default home (this PC's login): the daemon's environment
    variable, else ``~/.claude`` / ``~/.codex`` / ``~/.pi/agent``."""
    if harness == PI:
        return str(P.agent_dir())
    if harness == CLAUDE:
        return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def home_of(harness: str, account: dict[str, Any] | None) -> str:
    """The home a session's CLI reads its saved conversations from: the
    account's recorded home, else the CLI's default."""
    home = str((account or {}).get("home") or "").strip()
    return home or default_home(harness)


def _conversation_files(harness: str, home: str, folder: str = "") -> list[Path]:
    """BLOCKING: the conversation files under ``home``, newest first, at most
    :data:`DISK_FILES` (a stat each; nothing is opened)."""
    root = Path(home)
    try:
        if harness == PI:
            # Pi keeps sessions in its own sessions folder (environment,
            # setting, else under its config folder); the file NAME carries
            # the id (``P.session_id_of``).
            # (a project's own .pi/settings.json may name it, so the folder counts).
            files = P.conversation_files(
                P.sessions_dir(folder) if same_folder(home, P.agent_dir()) else root / "sessions")
        elif harness == CLAUDE:
            files = [p for p in root.glob("projects/*/*.jsonl") if _SID_RX.match(p.stem)]
        else:
            files = list(root.glob("sessions/*/*/*/*.jsonl"))
    except OSError:
        return []
    stamped = []
    for p in files:
        try:
            stamped.append((p.stat().st_mtime, p))
        except OSError:
            continue
    stamped.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in stamped[:DISK_FILES]]


def disk_sessions(harness: str, home: str, folder: str, *, since: float | None = None) -> list[dict[str, Any]]:
    """BLOCKING: the harness's OWN saved conversations for ``folder`` in
    ``home`` (read-only, through the ``history`` reader's summaries), newest
    first. ``since``: only files written at or after that time (epoch s)."""
    from ..history import index as HI

    out = []
    for path in _conversation_files(harness, home, folder):
        try:
            if since is not None and path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        s = P.summarize(path) if harness == PI else HI.summarize(harness, path)
        if s and same_folder(s.get("project"), folder):
            s["mtime"] = path.stat().st_mtime if path.exists() else 0
            out.append(s)
    return out


def _pi_login_typed(text: str, commands: list[dict[str, str]]) -> bool:
    """Is the message Pi's interactive ``/login`` (or ``/logout``) — unless
    an extension of the user's offers a command by that name?"""
    word = (str(text or "").strip().split() or [""])[0].lower()
    if word not in ("/login", "/logout"):
        return False
    return word[1:] not in {str(c.get("name") or "").lower().lstrip("/") for c in commands or []}


def pi_keeps_last(row: Any) -> bool:
    """BLOCKING: does this Pi session's conversation already hold messages?
    Then Pi, reopening it, restores the model and thinking level it last used
    (core/sdk.js), so "Pi's default" no longer applies to it."""
    hid = str(getattr(row, "harness_session_id", "") or "")
    if getattr(row, "harness", "") != PI or not hid:
        return False
    try:
        return P.has_messages(P.find_session_file(P.sessions_dir(row.folder), hid))
    except Exception:  # noqa: BLE001 — not known: the default stays offered
        return False


# --------------------------------------------------------------------------- #
# Terminals
# --------------------------------------------------------------------------- #
def pane_harness(pane: Any) -> str | None:
    """The Code-view harness a Build terminal ran (Claude Code / Codex), else None."""
    cli = str(getattr(pane, "agent_cli", None) or getattr(pane, "resume_cli", None) or "").lower()
    if not cli:
        try:
            cli = str(pane.activity().cli or "").lower()
        except Exception:  # noqa: BLE001
            cli = ""
    return {"claude": CLAUDE, "codex": CODEX, "pi": PI}.get(cli)


def pane_exited(pane: Any) -> bool:
    """Is the terminal back at its SHELL prompt (the CLI has exited)? A row
    Claude Code drew can end like a prompt, so its own screen furniture in the
    last lines says it still runs (``routes/terminals._claude_ui_visible``)."""
    from ..daemon.routes.terminals import _claude_ui_visible
    from ..terminals.handoff import at_shell_prompt

    try:
        tail = pane.output_tail()
        return bool(at_shell_prompt(tail, pane.shell)) and not _claude_ui_visible(tail)
    except Exception:  # noqa: BLE001
        return False


def can_open_in_code(pane: Any) -> bool:
    """A terminal row's ``can_open_in_code``: its Claude Code / Codex has
    exited and a conversation can be named — Claude by the session id the
    pane recorded; Codex records none, so the press looks for the pane's own
    thread (and refuses in words when it cannot tell which). Reads the pane's
    cached tail only. Never raises."""
    try:
        harness = pane_harness(pane)
        if harness is None or not getattr(pane, "alive", True):
            return False
        if harness == CLAUDE and not _SID_RX.match(str(getattr(pane, "claude_session_id", None) or "")):
            return False
        return pane_exited(pane)
    except Exception:  # noqa: BLE001
        return False


def terminal_holds(platform: Any, hid: str, *, harness: str = CLAUDE, rows: Any = None) -> bool:
    """Does a live Build terminal hold this conversation, its CLI still
    running? Claude Code: the session id the pane recorded. Codex and Pi
    record none (v1.411.0), so a terminal holds one when it runs that CLI and
    either the Code view opened it there (a row's ``terminal_id``) or its own
    screen still shows the id it was started with (``pi --session <id>`` /
    ``codex resume <id>`` typed in it). Never raises."""
    terminals = getattr(platform, "terminals", None)
    if terminals is None or not hid:
        return False
    opened = {str(r.terminal_id) for r in (rows or [])
              if getattr(r, "terminal_id", "") and r.harness == harness and r.harness_session_id == hid}
    try:
        infos = terminals.list()
    except Exception:  # noqa: BLE001
        return False
    for info in infos:
        try:
            if not info.get("alive", True):
                continue
            pane = terminals.get(info["id"])
            if pane is None:
                continue
            if harness == CLAUDE:
                if str(getattr(pane, "claude_session_id", None) or "") != hid:
                    continue
            else:
                if pane_harness(pane) != harness:
                    continue
                if str(info["id"]) not in opened and hid not in str(pane.output_tail() or ""):
                    continue
            if not pane_exited(pane):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


# --------------------------------------------------------------------------- #
# Turns
# --------------------------------------------------------------------------- #
def sse(event: str, data: Any) -> str:
    from ..daemon.durable_turns import sse as _sse

    return _sse(event, data)


class HostedTurn:
    """One message and everything the harness did with it."""

    def __init__(self, turn_id: str, sid: str) -> None:
        self.id = turn_id
        self.sid = sid
        #: The replay: consecutive deltas of one item merged (masked again
        #: after the merge), bounded by :data:`MAX_TURN_CHARS`.
        self.events: list[dict[str, Any]] = []
        self.status = "running"
        self._followers: set[asyncio.Queue] = set()
        self.finished = asyncio.Event()
        self.finished_at: float | None = None
        self.task: asyncio.Task | None = None
        self.started_at = utcnow()
        self._chars = 0
        #: True once the turn's end is being written (the done follows).
        self.ending = False

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def watched(self) -> bool:
        return bool(self._followers)

    def add(self, ev: dict[str, Any]) -> None:
        delta = ev.get("delta") if ev.get("type") in ("text", "thinking") else None
        if delta is not None and self._chars > MAX_TURN_CHARS:
            pass  # replay past its bound: only non-delta events are kept
        else:
            self._chars += len(str(delta or ""))
            coalesce_into(self.events, ev)
        for q in list(self._followers):
            q.put_nowait(ev)

    def end(self, status: str) -> None:
        if not self.running:
            return
        self.status = status
        self.finished_at = time.monotonic()
        for q in list(self._followers):
            q.put_nowait(None)
        self.finished.set()

    async def follow(self) -> AsyncIterator[str]:
        """Everything so far as one chunk, then each new event, until the end.
        Snapshot and subscription in one step (no await between)."""
        q: asyncio.Queue = asyncio.Queue()
        head = "".join(sse(e["type"], e) for e in self.events)
        running = self.running
        if running:
            self._followers.add(q)
        try:
            if head:
                yield head
            if not running:
                return
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield sse(ev["type"], ev)
        finally:
            self._followers.discard(q)


class LiveSession:
    """One session as this process holds it."""

    def __init__(self, sid: str, harness: str, log_: EventLog) -> None:
        self.sid = sid
        self.harness = harness
        self.log = log_
        self.proc: HarnessProcess | None = None
        self.driver: Driver | None = None
        self.turn: HostedTurn | None = None
        self.asks: dict[str, Ask] = {}
        self.last_active = time.monotonic()
        self.lock = asyncio.Lock()
        #: The process must restart before the next message (an effort change).
        self.restart = False
        self.limit: Any = None
        self.limit_since = ""
        self.last_error = ""
        #: The Iron-Proxy account the session runs on ("" = this PC's login).
        self.account = ""
        #: What holds the session right now ("" = nothing): continue-on,
        #: delete, archive. A message during a claim is refused in words.
        self.claim = ""
        #: v1.412.0: the notes sent to the running turn (ref -> {text, files,
        #: shown, read}); what was never read goes back to the box at its end.
        self.steers: dict[str, dict[str, Any]] = {}

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.alive

    @property
    def working_alone(self) -> bool:
        """The tool is running something no message owns (Pi: a run an
        extension started by itself) — never reaped, never evicted."""
        return bool(self.driver is not None and getattr(self.driver, "busy_alone", False))

    @property
    def busy(self) -> bool:
        return ((self.turn is not None and self.turn.running) or bool(self.asks) or bool(self.claim)
                or self.working_alone)


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
class HostedService:
    def __init__(self, platform: Any, *, clock: Any = time.monotonic) -> None:
        self.platform = platform
        self.engine = platform.engine
        self.home = Path(platform.config.home)
        self.clock = clock
        self.live: dict[str, LiveSession] = {}
        self.turns: dict[str, HostedTurn] = {}
        self._reaper: asyncio.Task | None = None
        self._closing = False
        self._pool_lock: asyncio.Lock | None = None
        #: The model list a Pi process last said, kept for the next listing.
        self._pi_catalog = self.home / "hosted" / "pi_models.json"

    # -- helpers -----------------------------------------------------------------
    def _lock(self) -> asyncio.Lock:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    async def _row(self, sid: str) -> Any:
        row = await asyncio.to_thread(store.get, self.engine, sid)
        if row is None:
            raise Refused("No such Code-view session.", 404)
        return row

    def _live(self, row: Any) -> LiveSession:
        live = self.live.get(row.id)
        if live is None:
            live = LiveSession(row.id, row.harness, EventLog(self.home, row.id))
            self.live[row.id] = live
        return live

    def _evict_turns(self) -> None:
        now = time.monotonic()
        for tid, t in list(self.turns.items()):
            if t.finished_at is not None and now - t.finished_at > FINISHED_KEEP_S:
                self.turns.pop(tid, None)

    def _claim(self, live: LiveSession, what: str) -> None:
        if live.claim:
            raise Refused(W.CLAIMED.format(what=live.claim))
        if live.turn is not None and live.turn.running:
            raise Refused(W.RUNNING)
        live.claim = what

    # -- the harnesses -----------------------------------------------------------
    def harnesses(self) -> list[dict[str, Any]]:
        """What can be started, from CACHED reads only (never a probe).
        BLOCKING (a PATH lookup): the route is a sync ``def``."""
        from ..harness import build_accounts as BA
        from ..providers.cli_auth import DEFAULT_PROBE, never_probe

        snap = BA.snapshot(getattr(self.platform, "iron_proxy", None))
        out = []
        for h in HARNESSES:
            cli = W.CLI[h]
            installed = binary_for(h) is not None
            signed = None
            # Pi signs in by ITSELF, per provider (`/login`, its own auth
            # file): there is no one verdict to read, and Iron-Proxy's
            # accounts are other tools' homes. "Not known" is honest.
            if h != PI:
                with never_probe():
                    try:
                        signed = DEFAULT_PROBE.verdict(cli)
                    except Exception:  # noqa: BLE001
                        signed = None
            accounts = []
            if snap is not None and h != PI:
                for r in BA.vendor_rows(snap, "claude" if h == CLAUDE else "codex"):
                    accounts.append({"id": r["id"], "name": r["name"], "status": r["status"]})
            out.append({
                "id": h,
                "label": W.label(h),
                "installed": installed,
                "signed_in": signed if installed else None,
                "version": None,
                "modes": mode_rows(h),
                "hidden_modes": hidden_mode_rows(h),
                "models": self._models_with_efforts(h),
                "efforts": list(EFFORTS[h]),
                "accounts": accounts,
                "own_sign_in": h == PI,
                "commands": self.commands_for(h),
            })
        return out

    def _models_with_efforts(self, harness: str) -> list[dict[str, Any]]:
        """v1.412.0 review: the model rows, each with the effort levels THAT
        model takes when it is known (``efforts``; ``[]`` = none — the chip
        hides). A row with no ``efforts`` key: the tool's own list applies."""
        rows: list[dict[str, Any]] = [dict(r) for r in self._models(harness)]
        if harness == CLAUDE:
            for r in rows:
                try:
                    from ..providers import claude_models

                    known = claude_models.model_efforts(str(r.get("id") or ""))
                except Exception:  # noqa: BLE001 — unknown: no key
                    known = None
                if known is not None:
                    r["efforts"] = [x for x in EFFORTS[CLAUDE] if x in known]
        elif harness != PI:
            for r in rows:
                r.pop("efforts", None)
        return rows

    def _models(self, harness: str) -> list[dict[str, Any]]:
        rows = [{"id": "", "label": f"{W.label(harness)}'s default"}]
        if harness == PI:
            # What a Pi process last listed (never a start just to ask).
            rows += P.cached_models(self._pi_catalog)
        if harness == CLAUDE:
            try:
                from ..providers import claude_models

                cat = claude_models.catalog(refresh=False)
                for m in cat.get("models") or []:
                    if isinstance(m, dict) and m.get("id"):
                        rows.append({"id": str(m["id"]), "label": str(m.get("label") or m["id"])})
            except Exception:  # noqa: BLE001 — the default row still works
                log.debug("claude model catalog unreadable", exc_info=True)
        return rows

    def commands_for(self, harness: str, live: "LiveSession | None" = None) -> list[dict[str, str]]:
        """The harness's own `/` commands: what its running process said, else
        what it said last in this process (never a start just to ask)."""
        from .claude import LAST_COMMANDS

        rows = live.driver.commands if (live and live.driver and live.driver.commands) else LAST_COMMANDS.get(harness, [])
        return [{"name": c["name"], "description": c.get("description", "")} for c in rows]

    async def commands(self, sid: str) -> list[dict[str, str]]:
        row = await self._row(sid)
        return self.commands_for(row.harness, self.live.get(sid))

    # -- who holds a conversation -------------------------------------------------
    def _holder(self, harness: str, hid: str, *, except_sid: str = "") -> str:
        """BLOCKING: why ``hid`` cannot be opened here ("" = it can): another
        live (not archived) Code-view row holds it, or a Build terminal does."""
        if not hid:
            return ""
        rows = store.list_rows(self.engine)
        for r in rows:
            if r.id != except_sid and r.harness == harness and r.harness_session_id == hid:
                return W.HELD_BY_ROW
        if terminal_holds(self.platform, hid, harness=harness, rows=rows):
            return W.HELD_BY_TERMINAL.format(label=W.label(harness), exit=W.exit_command(harness))
        return ""

    def _opened_terminal_runs(self, row: Any) -> bool:
        """BLOCKING-light: is the terminal Open in terminal started for this row
        still running the CLI? (A gone or exited terminal releases the row.)"""
        tid = str(row.terminal_id or "")
        if not tid:
            return False
        terminals = getattr(self.platform, "terminals", None)
        pane = terminals.get(tid) if terminals is not None else None
        if pane is None or not getattr(pane, "alive", True):
            return False
        return not pane_exited(pane)

    # -- sessions ----------------------------------------------------------------
    async def create(self, body: dict[str, Any]) -> dict[str, Any]:
        harness = str(body.get("harness") or "").strip().lower()
        if harness not in HARNESSES:
            raise Refused('Choose "claude-code", "codex" or "pi".', 400, field="tool")
        folder = str(body.get("folder") or "").strip()
        resume_id = str(body.get("harness_session_id") or "").strip()
        # v1.412.0: no folder chosen is a session of its own — its first
        # message makes it a folder under the chat files folder (the v1.379.0
        # rule; :meth:`_own_folder`). A saved conversation belongs to ITS
        # folder, so opening one still names it.
        no_folder = not folder
        if no_folder and resume_id:
            raise Refused(W.NO_FOLDER_RESUME, 400, field="folder")
        if not no_folder:
            from ..core.fs_policy import root_problem

            problem = await asyncio.to_thread(root_problem, folder)
            if problem:
                raise Refused(problem, 400, field="folder")
        try:
            mode = check_mode(harness, body.get("mode") or default_mode(harness), bool(body.get("confirm")))
        except Refused as r:
            r.field = r.field or "mode"
            raise
        try:
            model = check_model(body.get("model"))
        except Refused as r:
            r.field = r.field or "model"
            raise
        pi_rows = await asyncio.to_thread(self._models, PI) if harness == PI else None
        effort = check_effort(harness, body.get("effort"), model, pi_rows)
        title = clip(body.get("title") or "", 120)
        account = str(body.get("account") or "").strip()
        if account and harness == PI:
            raise Refused(W.PI_OWN_SIGN_IN, 400, field="account")
        try:
            acc = await asyncio.to_thread(self._resolve_account, harness, account)
        except Refused as r:
            if r.status == 400:
                r.field = r.field or "account"
            raise
        if resume_id:
            held = await asyncio.to_thread(self._holder, harness, resume_id)
            if held:
                raise Refused(held)
            found = await asyncio.to_thread(
                self._disk_session, harness, folder, resume_id, home_of(harness, acc)
            )
            if found is None:
                raise Refused(f"{W.label(harness)} has no saved conversation with that id in this folder.", 404)
            title = title or clip(found.get("title") or "", 120)
        row = await asyncio.to_thread(
            store.create, self.engine, harness=harness, folder=folder, title=title,
            account=str(acc.get("id") or ""), account_json=json.dumps(acc) if acc else "",
            model=model, effort=effort, mode=mode, harness_session_id=resume_id,
            no_folder=no_folder,
        )
        return self._with_root(store.view(row))

    def _with_root(self, view: dict[str, Any]) -> dict[str, Any]:
        """A session with no folder chosen says where its own folder goes
        (``work_root``: the chat files folder)."""
        if view.get("no_folder"):
            cfg = getattr(self.platform, "config", None)
            try:
                view["work_root"] = str(cfg.chat_files_dir) if cfg is not None else ""
            except Exception:  # noqa: BLE001 — a view never fails on this
                view["work_root"] = ""
        return view

    # -- a session with no folder chosen (v1.412.0) --------------------------------
    def _own_folder(self, sid: str, text: str) -> tuple[Any, str]:
        """BLOCKING: make the OWN folder of a session started with no folder
        — ``<chat files>/<YYYY-MM-DD> <first words>`` through the chat's own
        work-folder helper (``documents/workfolder``, the v1.379.0 title rule)
        — and keep it on the row (first wins: a folder another message made
        meanwhile is used and this one removed). Answers ``(row, made_now)``
        where ``made_now`` is the path THIS call made ("" when it used one
        already kept). Refused in words when it cannot be made or used."""
        from ..core.fs_policy import root_problem
        from ..daemon.routes.build import _remove_if_empty, _work_folder_title
        from ..documents.workfolder import make_chat_workfolder

        cfg = self.platform.config
        root = cfg.chat_files_dir
        try:
            made = make_chat_workfolder(root, _work_folder_title(text), [], cfg.home / "uploads")
        except OSError as exc:
            raise Refused(W.NO_FOLDER_MADE.format(root=root, why=exc.strerror or exc)) from None
        path = str(made["path"])
        problem = root_problem(path)
        if problem:
            _remove_if_empty(path)
            raise Refused(W.NO_FOLDER_UNUSABLE.format(why=problem))
        row = store.set_folder_if_empty(self.engine, sid, path)
        if row is None:
            _remove_if_empty(path)
            raise Refused("No such session.", 404)
        if row.folder != path:
            _remove_if_empty(path)
            return row, ""
        return row, path

    def _take_back_folder(self, sid: str, path: str, *, forget: bool = True) -> None:
        """BLOCKING: the message the folder was made for never started — the
        folder goes again when it is still EMPTY (never anything inside it),
        and (``forget``: the FIRST message made it) the session has no folder
        again. A folder made AGAIN at a session's kept path (``forget`` False)
        is only removed: the session keeps its path. Never raises."""
        try:
            from ..daemon.routes.build import _remove_if_empty

            live = self.live.get(sid)
            if live is not None and live.turn is not None and live.turn.running:
                return  # another message started in it meanwhile
            if _remove_if_empty(path) and forget:
                store.clear_folder_if(self.engine, sid, path)
        except Exception:  # noqa: BLE001 — a refusal never fails on this
            log.debug("could not take back session %s's own folder", sid, exc_info=True)

    def _remake_own_folder(self, path: str) -> str:
        """BLOCKING: a session's OWN folder the user removed is made again at
        the same path (``routes/build._make_again``, the one rule) — ``path``
        when it was made now, "" when it is there. Refused in words when it is
        missing and cannot be made (the folder above it is gone: an unplugged
        drive or a moved chat files folder is not a removed folder)."""
        from ..daemon.routes.build import _make_again

        if os.path.isdir(path):
            return ""
        if _make_again(path):
            return path
        raise Refused(W.NO_FOLDER_GONE.format(folder=path))

    def _drop_own_folder(self, row: Any) -> None:
        """BLOCKING: a deleted session that started with no folder
        takes its own folder with it when that folder is still EMPTY and no
        other session works in it; one holding files is kept. Only a folder
        inside the chat files folder. Never raises."""
        try:
            if not getattr(row, "no_folder", False) or not row.folder:
                return
            from ..core.fs_policy import _canonical, _within
            from ..daemon.routes.build import _remove_if_empty

            root = _canonical(str(self.platform.config.chat_files_dir))
            if not _within(_canonical(row.folder), root):
                return
            if any(r.id != row.id and same_folder(r.folder, row.folder)
                   for r in store.list_rows(self.engine, archived=True)):
                return
            if _remove_if_empty(row.folder):
                store.clear_folder_if(self.engine, row.id, row.folder)
        except Exception:  # noqa: BLE001 — archive and delete never fail on this
            log.debug("could not drop session %s's empty own folder", getattr(row, "id", ""), exc_info=True)

    def _resolve_account(self, harness: str, account: str) -> dict[str, Any]:
        """BLOCKING: the account record a session starts on (a pane's rules:
        an explicit id or "default" is exactly that; none = Iron-Proxy's
        first free account from the CACHED snapshot, else this PC's login)."""
        from ..terminals import pane_accounts as PA

        provider = W.PROXY.get(harness)
        if provider is None:  # Pi: its own sign-in, never an Iron-Proxy account
            return {}
        svc = getattr(self.platform, "iron_proxy", None)
        request = {provider: account} if account else {}
        try:
            got = PA.resolve(svc, request)
        except PA.PaneAccountRefused as refused:
            raise Refused(refused.sentence, refused.status) from None
        rec = got.records.get(provider)
        return dict(rec) if rec else {}

    def _account_for_listing(self, harness: str, account: str) -> dict[str, Any]:
        """BLOCKING: the account a disk listing reads (the CACHED snapshot —
        never a call): "" or "default" = this PC's login."""
        if not account or account == "default" or harness not in W.PROXY:
            return {}
        from ..harness import build_accounts as BA

        snap = BA.snapshot(getattr(self.platform, "iron_proxy", None))
        for prof in (snap[0] if snap else []):
            if str(prof.get("id")) == account and prof.get("provider") == W.PROXY[harness]:
                cli = prof.get("cli") if isinstance(prof.get("cli"), dict) else {}
                return {"id": account, "home": str(cli.get("home") or "")}
        raise Refused("That account is not one Iron-Proxy lists for this tool.", 404)

    async def list_sessions(self, *, archived: bool = False, folder: str = "", disk: bool = False,
                            account: str = "") -> dict[str, Any]:
        rows = await asyncio.to_thread(store.list_rows, self.engine, archived=archived, folder=folder)
        out = [self._with_root(self._with_live(store.view(r))) for r in rows]
        result: dict[str, Any] = {"sessions": out}
        # v1.412.0: where a session started with no folder keeps its files
        # (the start composer's folder chip says so before one exists).
        result["work_root"] = self._with_root({"no_folder": True}).get("work_root", "")
        if disk:
            if not folder:
                raise Refused("Name the folder whose saved sessions to list.", 400)
            used = {r.harness_session_id for r in await asyncio.to_thread(store.list_rows, self.engine)
                    if r.harness_session_id}
            result["disk"] = await asyncio.to_thread(self._disk_rows, folder, used, account)
        return result

    def _with_live(self, view: dict[str, Any]) -> dict[str, Any]:
        live = self.live.get(view["id"])
        view["running"] = bool(live and live.turn and live.turn.running)
        view["process"] = bool(live and live.running)
        view["waiting"] = bool(live and live.asks)
        return view

    def _disk_rows(self, folder: str, used: set[str], account: str = "") -> list[dict[str, Any]]:
        """BLOCKING: each harness's OWN saved sessions for ``folder``, from the
        CHOSEN account's home (read-only, names only)."""
        out = []
        for harness in HARNESSES:
            try:
                acc = self._account_for_listing(harness, account)
            except Refused:
                continue
            for s in disk_sessions(harness, home_of(harness, acc), folder):
                if s.get("id") in used:
                    continue
                out.append({
                    "source": "disk", "harness": harness, "harness_session_id": s.get("id"),
                    "title": clip(s.get("title") or "", 120), "folder": folder,
                    "started": s.get("started"), "ended": s.get("ended"),
                })
        out.sort(key=lambda r: str(r.get("ended") or ""), reverse=True)
        return out

    def _disk_session(self, harness: str, folder: str, sid: str, home: str | None = None) -> dict[str, Any] | None:
        for s in disk_sessions(harness, home or default_home(harness), folder):
            if s.get("id") == sid:
                return s
        return None

    async def view(self, sid: str, *, limit: int = DEFAULT_EVENTS) -> dict[str, Any]:
        row = await self._row(sid)
        log_ = self.live[sid].log if sid in self.live else EventLog(self.home, sid)
        events, total, dropped = await asyncio.to_thread(log_.read, max(1, min(int(limit or DEFAULT_EVENTS), 20_000)))
        live = self.live.get(sid)
        running = live.turn.id if live and live.turn and live.turn.running else None
        out: dict[str, Any] = {
            "session": self._with_root(self._with_live(store.view(row))),
            "events": events,
            "events_total": total,
            "dropped": dropped,
            # The running turn's id (rejoin with GET …/turns/{id}/stream — its
            # replay starts with the turn's `user` event), else null.
            "running": running,
            "turn_id": running,
            "pending": [a.event for a in (live.asks.values() if live else [])],
        }
        out["commands"] = self.commands_for(row.harness, live)
        if row.harness == PI:
            out["keeps_last"] = await asyncio.to_thread(pi_keeps_last, row)
        return out

    async def patch(self, sid: str, body: dict[str, Any]) -> dict[str, Any]:
        row = await self._row(sid)
        fields: dict[str, Any] = {}
        applied: dict[str, str] = {}
        live = self.live.get(sid)
        running_turn = bool(live and live.turn and live.turn.running)
        if "title" in body and body["title"] is not None:
            fields["title"] = clip(body["title"], 120)
        if body.get("mode") is not None:
            mode = check_mode(row.harness, body["mode"], bool(body.get("confirm")))
            if live and live.running and live.driver:
                try:
                    applied["mode"] = await live.driver.set_mode(mode)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
            else:
                applied["mode"] = "next"
            if row.harness == CODEX and running_turn and live is not None:
                self._emit(live, make("notice", text=W.CODEX_MODE_NEXT.format(mode=MODE_LABELS[mode])))
            elif applied["mode"] == "now" and live is not None and live.asks and mode != row.mode:
                # v1.412.0: the mode changed while the tool waited on a card —
                # that card was asked under the old mode and still needs YOU.
                self._emit(live, make("notice", text=W.MODE_CARD_STILL_OPEN.format(
                    label=W.label(row.harness), mode=mode_label(row.harness, mode))))
            fields["mode"] = mode
        next_model = check_model(body["model"]) if body.get("model") is not None else row.model
        pi_rows = await asyncio.to_thread(self._models, PI) if row.harness == PI else None
        if row.harness == PI and (
            (body.get("model") is not None and not check_model(body["model"]) and row.model)
            or (body.get("effort") is not None
                and not check_effort(row.harness, body["effort"], next_model, pi_rows) and row.effort)
        ) and await asyncio.to_thread(pi_keeps_last, row):
            # Back to "Pi's default" would change nothing: reopening a
            # conversation with messages, Pi restores its LAST model and
            # thinking level (core/sdk.js) — said, never pretended.
            raise Refused(W.PI_KEEPS_LAST)
        if body.get("model") is not None:
            model = check_model(body["model"])
            if live and live.running and live.driver and model:
                try:
                    applied["model"] = await live.driver.set_model(model)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
            else:
                applied["model"] = "next"
                if live and live.running and row.harness in (CLAUDE, PI):
                    live.restart = True  # back to the CLI's default: a restart
            fields["model"] = model
        if body.get("effort") is None and body.get("model") is not None and row.effort \
                and row.effort not in efforts_for(row.harness, next_model, pi_rows):
            # v1.412.0 review: the new model does not take the effort kept on
            # the session — it goes back to the default rather than being
            # sent to a model that refuses it.
            body = {**body, "effort": ""}
            applied["effort_cleared"] = row.effort
        if body.get("effort") is not None:
            effort = check_effort(row.harness, body["effort"], next_model, pi_rows)
            applied["effort"] = "next"
            set_effort = getattr(live.driver, "set_effort", None) if live and live.running and live.driver else None
            if set_effort is not None:
                # Pi changes its thinking level live (`set_thinking_level`).
                try:
                    applied["effort"] = await set_effort(effort)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
            elif live and live.driver:
                live.driver.effort = effort
            if (row.harness == CLAUDE or (row.harness == PI and not effort)) and live and live.running:
                # --effort is read at start only; Pi's own default (no
                # --thinking) applies only to a process started without one.
                live.restart = True
            fields["effort"] = effort
        if fields:
            row = await asyncio.to_thread(store.update, self.engine, sid, **fields)
        out: dict[str, Any] = {"session": self._with_live(store.view(row)), "applied": applied}
        if row.harness == PI:
            out["keeps_last"] = await asyncio.to_thread(pi_keeps_last, row)
        return out

    async def archive(self, sid: str, archived: bool = True) -> dict[str, Any]:
        row = await self._row(sid)
        live = self._live(row)
        if archived:
            self._claim(live, "being archived")
            try:
                await self._stop_process(live, why="archived")
                # v1.412.0 review: an archived session KEEPS its own folder,
                # empty or not — Claude Code and Pi keep a conversation by
                # the folder it ran in, so unarchiving must find it at the
                # same path (only a real delete may remove an empty one).
                row = await asyncio.to_thread(store.update, self.engine, sid, archived_at=utcnow())
            finally:
                live.claim = ""
        else:
            row = await asyncio.to_thread(store.update, self.engine, sid, archived_at=None)
        return self._with_live(store.view(row))

    async def delete(self, sid: str) -> dict[str, Any]:
        row = await self._row(sid)
        live = self._live(row)
        self._claim(live, "being deleted")
        try:
            await self._stop_process(live, why="deleted")
            self.live.pop(sid, None)
            await asyncio.to_thread(live.log.close)
            await asyncio.to_thread(store.delete, self.engine, sid)
            await asyncio.to_thread(self._drop_own_folder, row)
            moved = await asyncio.to_thread(self._trash_events, sid)
        finally:
            live.claim = ""
        return {"ok": True, "deleted": sid, "events_moved_to": moved}

    def _trash_events(self, sid: str) -> str:
        """BLOCKING: the session's events folder moved to Iron Jarvis's own
        trash (never deleted). The harness's own session file is untouched."""
        src = session_dir(self.home, sid)
        if not src.exists():
            return ""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        dest = self.home / "trash" / stamp / "hosted" / sid
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            return str(dest)
        except OSError:
            log.warning("could not move a Code-view session's events to the trash", exc_info=True)
            return ""

    # -- the process ---------------------------------------------------------------
    async def _ensure_running(self, live: LiveSession, row: Any) -> None:
        if live.running and not live.restart:
            return
        if live.running and live.restart:
            await self._stop_process(live)
        live.restart = False
        argv0 = await asyncio.to_thread(binary_for, row.harness)  # a PATH walk: off the loop
        if argv0 is None:
            raise Refused(W.NOT_INSTALLED.format(label=W.label(row.harness)))
        if row.folder and not await asyncio.to_thread(os.path.isdir, row.folder):
            # v1.412.0 review: Windows says "The directory name is invalid"
            # (WinError 267) and that read as the tool's fault.
            raise Refused(W.FOLDER_MISSING.format(folder=row.folder))
        async with self._lock():
            # Another row's process on this very conversation (a race the
            # turn's own check could miss): never a second process.
            for other in self.live.values():
                if other is not live and other.running and other.driver is not None \
                        and row.harness_session_id and other.driver.session_id == row.harness_session_id:
                    raise Refused(W.HELD_BY_ROW)
            others = [lv for lv in self.live.values() if lv is not live and lv.running]
            if len(others) >= MAX_LIVE:
                idle = sorted((lv for lv in others if not lv.busy), key=lambda lv: lv.last_active)
                if not idle:
                    raise Refused(W.POOL_FULL)
                await self._stop_process(idle[0])
            await self._start_process(live, row, argv0)
        self._ensure_reaper()

    async def _start_process(self, live: LiveSession, row: Any, argv0: list[str]) -> None:
        from ..terminals import pane_accounts as PA

        loop = asyncio.get_running_loop()
        acc = store.account_record(row)
        accounts = PA.from_recorded({W.PROXY[row.harness]: acc})[0] if acc and row.harness in W.PROXY else None
        env = build_env(accounts)
        if row.harness == PI:
            env = P.child_env(env)  # a parent Pi session's markers never cross
        resume = bool(row.harness_session_id)
        # Claude Code and Pi take the id to save under (`--session-id`): minted
        # here, so the row names the conversation before it starts.
        sid = row.harness_session_id or (str(uuid.uuid4()) if row.harness in (CLAUDE, PI) else "")
        cls = {CLAUDE: ClaudeDriver, CODEX: CodexDriver, PI: PiDriver}[row.harness]
        proc_holder: dict[str, HarnessProcess] = {}

        async def write(obj: dict[str, Any]) -> None:
            p = proc_holder["p"]
            await asyncio.to_thread(p.write, obj)

        driver = cls(
            write=write,
            emit=lambda ev: self._emit(live, ev),
            ask=lambda a: self._ask(live, a),
            on_session_id=lambda s: self._on_session_id(live, s),
            mode=row.mode, model=row.model, effort=row.effort, cwd=row.folder,
        )
        if isinstance(driver, PiDriver):
            driver.catalog_path = self._pi_catalog
            driver.forget_ask = lambda ask_id: self._forget_ask(live, ask_id)
        driver.steer_read = lambda ref: self._steer_read(live, ref)
        proc = HarnessProcess(
            argv0 + driver.argv(session_id=sid, resume=resume), env=env, cwd=row.folder,
            name=f"hosted-{row.id}",
        )
        proc_holder["p"] = proc

        def on_exit(code: int | None) -> None:
            self._on_exit(live, proc, code)

        def on_message(obj: dict[str, Any]) -> None:
            # A frame the parser cannot read must never end the session.
            try:
                driver.on_message(obj)
            except Exception:  # noqa: BLE001
                log.warning("a %s frame could not be read", row.harness, exc_info=True)

        try:
            await asyncio.to_thread(proc.start, loop, on_message, on_exit)
        except OSError as exc:
            # The program could not be started at all (v1.411.0: npm's POSIX
            # `pi` shim beside pi.cmd is not a Windows program, WinError 193):
            # one plain sentence, never a bare OSError.
            raise Refused(W.could_not_start(row.harness, argv0[0] if argv0 else "", exc)) from None
        live.proc, live.driver = proc, driver
        try:
            await driver.open(session_id=sid, resume=resume)
        except Exception as exc:  # noqa: BLE001 — said in words below
            await self._stop_process(live)
            said = exc.args[0] if exc.args else type(exc).__name__
            raise Refused(f"{W.label(row.harness)} did not start: {clip(said, 200)}") from None

    def _on_session_id(self, live: LiveSession, sid: str) -> None:
        async def save() -> None:
            try:
                row = await asyncio.to_thread(store.get, self.engine, live.sid)
                if row is not None and row.harness_session_id != sid:
                    await asyncio.to_thread(store.update, self.engine, live.sid, harness_session_id=sid)
            except Exception:  # noqa: BLE001 — the next start mints again, said nowhere
                log.warning("could not record a Code-view session's harness id", exc_info=True)

        asyncio.ensure_future(save())

    def _on_exit(self, live: LiveSession, proc: HarnessProcess, code: int | None) -> None:
        if live.proc is not proc:
            return
        drv = live.driver
        if drv is not None:
            drv.on_exit()
        live.proc, live.driver = None, None
        self._expire_asks(live)
        turn = live.turn
        if turn is not None and turn.running and not turn.ending and not proc.stopped:
            technical = proc.stderr_text() or f"exit code {code}"
            # Pi signs in by itself and has no accounts to move between: its
            # own words, never the other tools' sign-in or account presses.
            ev = P.failure_event(technical) if live.harness == PI else W.failure_for(live.harness, technical)
            ev["title"] = W.GONE_DURING_TURN.format(label=W.label(live.harness))
            self._emit(live, ev)
            asyncio.ensure_future(self._end_turn(live, "failed"))

    async def _stop_process(self, live: LiveSession, *, why: str = "") -> None:
        """Stop the process (tree kill, off the loop). ALWAYS: its open cards
        close and its running turn ends "interrupted" — a stop never leaves a
        follower waiting for a done that cannot come."""
        proc = live.proc
        live.proc = None
        drv, live.driver = live.driver, None
        if proc is not None:
            await asyncio.to_thread(proc.stop)
        if drv is not None:
            drv.on_exit()
        self._expire_asks(live)
        turn = live.turn
        if turn is not None and turn.running and not turn.ending:
            self._emit(live, make("notice", text=W.STOPPED_BECAUSE.format(why=why) if why else W.STOPPED_PLAIN))
            await self._end_turn(live, "interrupted")

    # -- events ----------------------------------------------------------------
    def _emit(self, live: LiveSession, ev: dict[str, Any]) -> None:
        turn = live.turn
        if ev.get("type") == "done":
            # The ONE way a turn ends (the row is stamped first).
            stop = str(ev.get("stop") or "completed")
            asyncio.ensure_future(self._end_turn(live, stop, ev))
            return
        if turn is not None and turn.running:
            ev["turn"] = turn.id
        if ev.get("type") == "error":
            live.last_error = str(ev.get("title") or "")
            if ev.get("fix_kind") == "account_limit":
                ev.setdefault("account", live.account)
                live.limit = getattr(live.driver, "limit", None)
                live.limit_since = datetime.now(timezone.utc).isoformat()
        live.log.append(ev)
        if turn is not None and turn.running:
            turn.add(ev)

    async def _end_turn(self, live: LiveSession, stop: str, done: dict[str, Any] | None = None) -> None:
        """Stamp the row's ``last_error`` (this turn's own outcome) FIRST, then
        put the ``done`` in the log and in front of every follower. Idempotent:
        a turn already ending is left to the first caller."""
        turn = live.turn
        if turn is None or not turn.running or turn.ending:
            if done is not None:
                live.log.append(done)
            return
        turn.ending = True
        self._return_steers(live)
        error = live.last_error
        try:
            await asyncio.to_thread(store.update, self.engine, live.sid, last_error=error)
        except Exception:  # noqa: BLE001 — the done still goes out
            log.debug("could not stamp a Code-view session", exc_info=True)
        done = dict(done) if done is not None else make("done", stop=stop)
        done.setdefault("stop", stop)
        done["turn"] = turn.id
        live.log.append(done)
        turn.add(done)
        live.last_active = self.clock()
        live.last_error = ""
        turn.end("done" if done.get("stop") == "completed" else str(done.get("stop")))
        if live.turn is turn:
            live.turn = None

    def _expire_asks(self, live: LiveSession) -> None:
        """Every open card closes when its process goes (nobody can answer it)."""
        for ask_id in list(live.asks):
            live.asks.pop(ask_id, None)
            self._emit(live, make("resolved", request_id=ask_id, decision="expired"))

    def _forget_ask(self, live: LiveSession, ask_id: str) -> None:
        """One card the tool stopped waiting for (a Pi dialog that timed out)."""
        if live.asks.pop(ask_id, None) is not None:
            self._emit(live, make("resolved", request_id=ask_id, decision="expired"))

    def _ask(self, live: LiveSession, ask: Ask) -> None:
        live.asks[ask.id] = ask
        self._emit(live, ask.event)

    # -- notes sent while it works (v1.412.0) -------------------------------------
    def _steer_event(self, live: LiveSession, ref: str, state: str) -> None:
        note = live.steers.get(ref) or {}
        ev = make("steer", id=ref, state=state, text=clip(note.get("text") or "", TEXT_CHARS))
        if note.get("files"):
            ev["files"] = [clip(f, 500) for f in note["files"]]
        if state == "sent":
            ev["how"] = W.STEER_HOW.get(live.harness, "")
        self._emit(live, ev)

    def _steer_read(self, live: LiveSession, ref: str) -> None:
        """The tool took a note (its own signal): said once, in order."""
        note = live.steers.get(ref)
        if note is None or note.get("read"):
            return
        note["read"] = True
        if note.get("shown"):
            self._steer_event(live, ref, "read")

    def _return_steers(self, live: LiveSession) -> None:
        """The turn is ending: every note the tool never read goes BACK to the
        box (the v1.287.0 rule — a note is never dropped without a word)."""
        drv = live.driver
        unread = set(drv.unread_steers()) if drv is not None else set(live.steers)
        for ref, note in list(live.steers.items()):
            if not note.get("shown"):
                # v1.412.0 review: a note still on its way (its steer() has
                # not come back): kept, with what the tool did with it, so the
                # answer says the truth — a note the tool READ must never be
                # handed back to the box (it would be sent twice).
                note["ended"] = True
                if ref not in unread:
                    note["read"] = True
                continue
            if not note.get("read") and ref in unread:
                self._steer_event(live, ref, "returned")
            del live.steers[ref]
        if drv is not None:
            drv.steers.clear()

    async def steer(self, sid: str, text: str, files: list[str] | None = None) -> dict[str, Any]:
        """A note for the RUNNING turn, through the tool's own way of taking
        one (``Driver.steer``). Refused with ``code: not_working`` when no
        reply runs (the page then sends it as a message)."""
        row = await self._row(sid)
        live = self.live.get(sid)
        label = W.label(row.harness)
        text = str(text or "")
        files = [str(f) for f in (files or []) if str(f).strip()]
        if not text.strip() and not files:
            raise Refused("Type a note first.", 400)
        if live is None or live.turn is None or not live.turn.running or live.turn.ending:
            raise Refused(W.STEER_NOT_WORKING.format(label=label), 409, code="not_working")
        problems = await asyncio.to_thread(self._file_problems, files, row.folder or "")
        if problems:
            raise Refused(problems, 400)
        turn = live.turn
        # The tool may still be starting (its process, its first message):
        # the note waits for the turn to be open, a short while.
        deadline = time.monotonic() + STEER_WAIT_S
        while (live.driver is None or not live.driver.turn_open) and turn.running and not turn.ending:
            if time.monotonic() > deadline:
                raise Refused(W.STEER_TOO_LATE.format(label=label))
            await asyncio.sleep(0.05)
        driver = live.driver
        if driver is None or not turn.running or turn.ending or live.turn is not turn:
            raise Refused(W.STEER_NOT_WORKING.format(label=label), 409, code="not_working")
        ref = f"note-{uuid.uuid4().hex[:12]}"
        live.steers[ref] = {"text": text, "files": files, "shown": False, "read": False}
        try:
            how = await driver.steer(ref, text, files)
        except ControlRefused as exc:
            live.steers.pop(ref, None)
            raise Refused(str(exc)) from None
        except HarnessGone:
            live.steers.pop(ref, None)
            raise Refused(W.STEER_TOO_LATE.format(label=label)) from None
        note = live.steers.get(ref)
        if note is None or note.get("ended"):
            # The turn ended while the note went out. Read by the tool: said
            # (in order, after the reply); else it goes back to the box.
            live.steers.pop(ref, None)
            if note is not None and note.get("read"):
                note["shown"] = True
                live.steers[ref] = note
                self._steer_event(live, ref, "sent")
                self._steer_event(live, ref, "read")
                live.steers.pop(ref, None)
                return {"ok": True, "id": ref, "state": "read", "how": how}
            return {"ok": True, "id": ref, "state": "returned", "how": how}
        note["shown"] = True
        self._steer_event(live, ref, "sent")
        if note.get("read"):  # the tool took it before the line above was written
            self._steer_event(live, ref, "read")
        live.last_active = self.clock()
        return {"ok": True, "id": ref, "state": "read" if note.get("read") else "sent", "how": how}

    # -- turns -------------------------------------------------------------------
    async def start_turn(self, sid: str, text: str, turn_id: str, files: list[str] | None = None) -> HostedTurn:
        self._evict_turns()
        turn_id = str(turn_id or "").strip()[:120] or f"turn-{uuid.uuid4().hex[:12]}"
        held = self.turns.get(turn_id)
        if held is not None:
            if held.sid != sid:
                raise Refused("That turn id belongs to another session.", 409)
            return held  # a repeated send of the same turn: follow it
        row = await self._row(sid)
        if row.archived_at is not None:
            raise Refused(W.ARCHIVED)
        text = str(text or "")
        files = [str(f) for f in (files or []) if str(f).strip()]
        if not text.strip() and not files:
            raise Refused("Type a message first.", 400)
        problems = await asyncio.to_thread(self._file_problems, files, row.folder or "")
        if problems:
            raise Refused(problems, 400)
        # v1.412.0: a session started with no folder gets its OWN folder now,
        # with its first message; taken back (while still empty) when this
        # message does not start after all.
        made = ""
        forget = False
        if not row.folder and getattr(row, "no_folder", False):
            row, made = await asyncio.to_thread(self._own_folder, sid, text)
            forget = bool(made)
        elif row.folder and getattr(row, "no_folder", False):
            # v1.412.0 review: the user removed this session's own folder
            # between messages — it is made AGAIN at the same path (the
            # v1.379.0 ``_make_again`` rule): Claude Code and Pi keep the
            # conversation by that folder, so a new one would lose it.
            made = await asyncio.to_thread(self._remake_own_folder, row.folder)
        try:
            return await self._start_turn_in(row, sid, turn_id, text, files, made, forget)
        except BaseException:
            if made:
                await asyncio.to_thread(self._take_back_folder, sid, made, forget=forget)
            raise

    async def _start_turn_in(self, row: Any, sid: str, turn_id: str, text: str, files: list[str],
                             made: str = "", forget: bool = False) -> HostedTurn:
        live = self._live(row)
        live.account = row.account
        if live.claim:
            raise Refused(W.CLAIMED.format(what=live.claim))
        if live.turn is not None and live.turn.running:
            raise Refused(W.BUSY)
        if live.working_alone:
            raise Refused(W.PI_STILL_WORKING)
        if row.harness == PI and _pi_login_typed(text, self.commands_for(row.harness, live)):
            # /login is Pi's sign-in SCREEN (a terminal's only): typed here it
            # would reach the model as plain text.
            raise Refused(W.PI_LOGIN_HERE, 400)
        if row.terminal_id:
            if await asyncio.to_thread(self._opened_terminal_runs, row):
                raise Refused(W.OPEN_IN_TERMINAL.format(label=W.label(row.harness), exit=W.exit_command(row.harness)))
            await asyncio.to_thread(store.update, self.engine, sid, terminal_id="")
        if not live.running:
            holder = await asyncio.to_thread(self._holder, row.harness, row.harness_session_id, except_sid=sid)
            if holder == W.HELD_BY_ROW:
                # Another row: held only while ITS process runs.
                for other in self.live.values():
                    if other.sid != sid and other.running and other.driver is not None \
                            and other.driver.session_id == row.harness_session_id:
                        raise Refused(holder)
            elif holder:
                raise Refused(holder)
            if live.claim or (live.turn is not None and live.turn.running):
                raise Refused(W.BUSY)
        turn = HostedTurn(turn_id, sid)
        self.turns[turn_id] = turn
        live.turn = turn
        live.last_active = self.clock()
        turn.task = asyncio.ensure_future(self._drive(live, row, turn, text, files, made, forget))
        # v1.409.0 (found live): a session is titled by its first message, as
        # a chat is — never "New Claude Code session" forever. Only an EMPTY
        # title (a name the user gave is theirs) and only once the turn is
        # really running (a refused send titles nothing).
        if not (row.title or "").strip():
            title = first_message_title(text)
            if title:
                try:
                    await asyncio.to_thread(store.update, self.engine, sid, title=title)
                except Exception:  # noqa: BLE001 — a title never fails a turn
                    log.debug("could not title session %s", sid, exc_info=True)
        return turn

    def _file_problems(self, files: list[str], folder: str = "") -> str:
        """BLOCKING: an attached file must exist and pass the app's read policy.
        v1.412.0 review: a file spelled as a network share (or a device path)
        is refused from its TEXT, before anything touches the disk — a stat
        of a share path is an SMB connection that sends this PC's sign-in
        hash (the v1.379.0 lesson) — unless the session's own folder is on
        a share."""
        from ..core.fs_policy import fs_read_ok
        from ..harness.sessions import _network_spelling

        on_share = _network_spelling(folder)
        for f in files:
            if _network_spelling(f) and not on_share:
                return W.ATTACHED_ON_SHARE
            if not os.path.isabs(f) or not os.path.isfile(f):
                return f"Attached file not found: {clip(os.path.basename(f), 120)}"
            ok, why = fs_read_ok(f)
            if not ok:
                return why or f"Iron Jarvis may not read {clip(os.path.basename(f), 120)}."
        return ""

    async def _drive(self, live: LiveSession, row: Any, turn: HostedTurn, text: str, files: list[str],
                     made: str = "", forget: bool = False) -> None:
        user = make("user", text=clip(text, TEXT_CHARS))
        if files:
            user["files"] = [clip(f, 500) for f in files]
        self._emit(live, user)
        started = False
        try:
            async with live.lock:
                await self._ensure_running(live, row)
            started = True
            if live.driver is None:
                raise HarnessGone("the coding tool stopped")
            await live.driver.send_user(text, files)
            if turn.running:
                await turn.finished.wait()
        except Refused as r:
            self._emit(live, W.error(r.sentence, "Fix that, then send your message again.", ""))
            await self._end_turn(live, "failed")
        except P.PiStillWorking:
            self._emit(live, W.error(W.PI_STILL_WORKING_TITLE, W.PI_STILL_WORKING_FIX, ""))
            await self._end_turn(live, "failed")
        except HarnessGone as exc:
            if turn.running and not turn.ending:
                self._emit(live, W.generic_error(live.harness, str(exc)))
                await self._end_turn(live, "failed")
        except asyncio.CancelledError:
            if turn.running and not turn.ending:
                self._emit(live, W.error(STOPPED_TITLE, STOPPED_FIX, ""))
                await asyncio.shield(self._end_turn(live, "interrupted"))
            raise
        except Exception as exc:  # noqa: BLE001 — every failure becomes words
            log.warning("a Code-view turn failed", exc_info=True)
            if turn.running and not turn.ending:
                self._emit(live, W.generic_error(live.harness, f"{type(exc).__name__}: {exc}"))
                await self._end_turn(live, "failed")
        finally:
            if turn.running and not turn.ending:  # never leave a follower hanging
                await asyncio.shield(self._end_turn(live, "failed"))
            if live.turn is turn and not turn.running:
                live.turn = None
            live.last_active = self.clock()
            if made and not started:
                # v1.412.0 review: the folder this message made (or made
                # again) goes back while EMPTY when the tool never started —
                # not installed, or Windows could not start it. Once the tool
                # ran, the conversation belongs to that folder: it stays.
                await asyncio.shield(asyncio.to_thread(self._take_back_folder, live.sid, made, forget=forget))

    def turn(self, turn_id: str) -> HostedTurn | None:
        self._evict_turns()
        return self.turns.get(turn_id)

    async def replay(self, sid: str, turn_id: str) -> str | None:
        """A turn this process no longer holds, from the session's log."""
        await self._row(sid)
        log_ = self.live[sid].log if sid in self.live else EventLog(self.home, sid)
        events, _total, _dropped = await asyncio.to_thread(log_.read, 0, turn=turn_id)
        if not events:
            return None
        return "".join(sse(e.get("type") or "notice", e) for e in events)

    async def answer(self, sid: str, request_id: str, decision: str, message: str = "",
                     answers: Any = None) -> dict[str, Any]:
        await self._row(sid)
        live = self.live.get(sid)
        decision = str(decision or "").strip()
        if decision not in DECISIONS:
            raise Refused(f"Answer with one of: {', '.join(DECISIONS)}.", 400)
        rid = str(request_id or "")
        ask = live.asks.get(rid) if live else None
        if live is None or ask is None or live.driver is None:
            raise Refused("No such question is waiting (it was answered, or the session stopped).", 404)
        if ask.kind == "question" and decision == "allow_session":
            decision = "allow"
        if decision == "allow_session" and not ask.can_session:
            raise Refused(f"{W.label(live.harness)} offers no session-wide allow for this one — allow it once.", 400)
        # CLAIMED before the await: a second answer to this card finds nothing.
        live.asks.pop(rid, None)
        driver = live.driver
        message = clip(message or "", 2000)
        prepare = getattr(driver, "prepare_answer", None)
        try:
            if prepare is None:  # a driver with one step (tests)
                await driver.answer(ask, decision, message, answers)
                payload = None
            else:
                # Built (and refused) BEFORE anything is sent or said.
                payload = prepare(ask, decision, message, answers)
        except AskRefused as exc:
            live.asks[rid] = ask  # still waiting: the answer did not reach the harness
            raise Refused(str(exc), 400) from None
        except HarnessGone:
            raise Refused("The session stopped before your answer reached it.", 409) from None
        # Said BEFORE the answer is sent, so the card closes ahead of whatever
        # the harness does next (its frames arrive only after it reads this).
        self._emit(live, make("resolved", request_id=ask.id, decision=decision))
        new_mode = ask.data.get("sets_mode") if decision == "allow_session" else None
        if new_mode:
            self._emit(live, make("notice", text=W.MODE_FROM_SESSION_ALLOW.format(
                label=W.label(live.harness), mode=MODE_LABELS[new_mode])))
        if payload is not None:
            try:
                await driver.write(payload)
            except HarnessGone:
                raise Refused("The session stopped before your answer reached it.", 409) from None
            driver.answered(ask, decision)
        if new_mode:
            await asyncio.to_thread(store.update, self.engine, sid, mode=new_mode)
        return {"ok": True, "request_id": ask.id, "decision": decision}

    async def interrupt(self, sid: str) -> dict[str, Any]:
        """Tell the harness to stop; answers within :data:`INTERRUPT_WAIT_S`
        (the turn ends when the harness says so, or with its process)."""
        await self._row(sid)
        live = self.live.get(sid)
        if live is None or live.turn is None or not live.turn.running:
            return {"ok": True, "running": False}
        driver = live.driver
        if driver is not None and live.running:
            async def tell() -> None:
                for ask_id in list(live.asks):  # an unanswered card goes with the stop
                    ask = live.asks.pop(ask_id, None)
                    if ask is None:
                        continue
                    try:
                        await driver.answer(ask, "deny", "The user pressed Stop.", None)
                    except Exception:  # noqa: BLE001
                        pass
                    self._emit(live, make("resolved", request_id=ask_id, decision="deny"))
                try:
                    await driver.interrupt()
                except Exception:  # noqa: BLE001 — a gone process ends the turn
                    pass

            task = asyncio.ensure_future(tell())
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=INTERRUPT_WAIT_S)
            except asyncio.TimeoutError:
                pass  # the harness is slow to answer: the turn still ends when it does
        else:
            await self._end_turn(live, "interrupted")
        return {"ok": True, "running": True}

    # -- the bell ----------------------------------------------------------------
    def waiting_rows(self) -> list[dict[str, Any]]:
        """One row per session WAITING on the user with nobody following its
        turn — NAMES ONLY (the tool's name), never arguments."""
        rows = []
        for live in list(self.live.values()):
            if not live.asks:
                continue
            if live.turn is not None and live.turn.watched:
                continue
            newest = list(live.asks.values())[-1]
            rows.append({
                "id": f"build_app:{live.sid}",
                "kind": "chat",
                "thread_id": live.sid,
                "tool": newest.tool,
                "requested_at": newest.asked_at,
                "mission_id": None,
                "project_id": None,
                "surface": "build_app",
                "app_session": live.sid,
                "harness": live.harness,
            })
        return rows

    # -- the reaper --------------------------------------------------------------
    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.ensure_future(self._reap_loop())

    async def _reap_loop(self) -> None:
        while not self._closing:
            await asyncio.sleep(REAP_EVERY_S)
            await self.reap_once()

    async def reap_once(self, now: float | None = None) -> list[str]:
        """Stop every process idle longer than :data:`IDLE_S`."""
        now = self.clock() if now is None else now
        stopped = []
        for live in list(self.live.values()):
            if live.running and not live.busy and now - live.last_active >= IDLE_S:
                await self._stop_process(live)
                stopped.append(live.sid)
        return stopped

    async def shutdown(self) -> None:
        """The daemon is stopping: every harness process goes (tree kill, off
        the loop), every running turn ends with what it wrote kept."""
        self._closing = True
        if self._reaper is not None:
            self._reaper.cancel()
        lives = list(self.live.values())
        for live in lives:
            self._expire_asks(live)
            if live.turn is not None and live.turn.running and not live.turn.ending:
                self._emit(live, W.error(STOPPED_TITLE, STOPPED_FIX, ""))
        await asyncio.gather(*(self._end_turn(lv, "interrupted") for lv in lives), return_exceptions=True)
        await asyncio.gather(*(self._stop_process(lv) for lv in lives), return_exceptions=True)
        await asyncio.gather(*(asyncio.to_thread(lv.log.close) for lv in lives), return_exceptions=True)

    # -- terminals -----------------------------------------------------------------
    async def open_terminal(self, sid: str) -> dict[str, Any]:
        row = await self._row(sid)
        live = self.live.get(sid)
        if live and ((live.turn and live.turn.running) or live.claim):
            raise Refused(W.RUNNING)
        hid = row.harness_session_id
        if not hid:
            raise Refused(W.NO_SESSION_YET)
        if not _SID_RX.match(hid):
            raise Refused("That session's id is not one Iron Jarvis types into a terminal.", 409)
        if live:
            await self._stop_process(live)  # two processes on one session corrupt it
        session = await asyncio.to_thread(self._launch_terminal, row, hid)
        # The row is now the terminal's until that terminal's CLI has exited.
        await asyncio.to_thread(store.update, self.engine, sid, terminal_id=str(session.id))
        return {"terminal_id": session.id}

    def _launch_terminal(self, row: Any, hid: str) -> Any:
        """BLOCKING: a Build terminal in the session's folder, on the
        session's account, typing the harness's own resume line."""
        from ..daemon.routes.terminals import launch_pane
        from ..daemon.schemas import TerminalLaunch
        from ..terminals import pane_accounts as PA
        from ..terminals.ai_clis import launch_entry

        cli = W.CLI[row.harness]
        entry = launch_entry(cli) or {"command": cli}
        acc = store.account_record(row)
        accounts = (PA.from_recorded({W.PROXY[row.harness]: acc})[0] if acc and row.harness in W.PROXY
                    else PA.PaneAccounts())
        if row.harness == PI:
            # Pi's own way back into a saved session: `--session <id>`.
            command = f"{entry['command']} --session {hid}"
            return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                               accounts=accounts, command=command)
        if row.harness == CLAUDE:
            command = f"{entry['command']} --resume {hid}"
            return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                               accounts=accounts, command=command, claude_session_id=hid)
        command = f"{entry['command']} resume {hid}"
        return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                           accounts=accounts, command=command)

    async def from_terminal(self, pane_id: str) -> dict[str, Any]:
        terminals = getattr(self.platform, "terminals", None)
        pane = terminals.get(pane_id) if terminals is not None else None
        if pane is None:
            raise Refused("No such terminal.", 404)
        from ..core.fs_policy import root_problem

        problem = await asyncio.to_thread(root_problem, str(pane.cwd or ""))
        if problem:
            raise Refused(problem, 400)
        harness, hid, title, acc = await asyncio.to_thread(self._pane_facts, pane)
        # A repeated press reuses the row that already holds this conversation.
        for r in await asyncio.to_thread(store.list_rows, self.engine):
            if r.harness == harness and r.harness_session_id == hid:
                out = store.view(r)
                out["reused"] = True
                return out
        row = await asyncio.to_thread(
            store.create, self.engine, harness=harness, folder=str(pane.cwd or ""),
            title=title or "From a terminal", account=str(acc.get("id") or ""),
            account_json=json.dumps(acc) if acc else "", mode=default_mode(harness), harness_session_id=hid,
        )
        return store.view(row)

    def _pane_facts(self, pane: Any) -> tuple[str, str, str, dict[str, Any]]:
        """BLOCKING: ``(harness, session id, title, account)`` of a terminal
        whose Claude Code / Codex has EXITED, else :class:`Refused`. The
        conversation is the one the PANE ran: Claude's recorded session id;
        else (Codex records none) the ONE conversation for this folder written
        in the pane's own account home since the pane started — never a guess
        among several."""
        harness = pane_harness(pane)
        if harness is None:
            raise Refused("This terminal is not running Claude Code, Codex or Pi.")
        if not pane_exited(pane):
            raise Refused(
                f"{W.label(harness)} is still running in this terminal — exit it there first "
                f"(type {W.exit_command(harness)}), then open it in the Code view."
            )
        proxy = W.PROXY.get(harness)  # Pi: none (its own sign-in)
        acc = ((getattr(pane, "accounts", None) or {}).get(proxy) or {}) if proxy else {}
        acc = {k: v for k, v in dict(acc).items() if k in ("id", "title", "source", "home", "unset", "note")}
        hid = str(getattr(pane, "claude_session_id", None) or "") if harness == CLAUDE else ""
        title = ""
        if not hid or not _SID_RX.match(hid):
            since = None
            created = getattr(pane, "created_at", None)
            if isinstance(created, datetime):
                since = (created if created.tzinfo else created.replace(tzinfo=timezone.utc)).timestamp() - 5
            found = disk_sessions(harness, home_of(harness, acc), str(pane.cwd or ""), since=since)
            if len(found) != 1:
                raise Refused(W.CANNOT_TELL.format(label=W.label(harness)))
            hid, title = str(found[0].get("id") or ""), str(found[0].get("title") or "")
        return harness, hid, clip(title, 120), acc

    # -- continue on the next account (Claude Code) -------------------------------
    async def continue_on(self, sid: str, account: str = "") -> dict[str, Any]:
        row = await self._row(sid)
        if row.harness != CLAUDE:
            raise Refused(W.PI_CONTINUE if row.harness == PI else W.CODEX_CONTINUE)
        if not row.harness_session_id:
            raise Refused(W.NO_SESSION_YET)
        live = self._live(row)
        self._claim(live, "moving to another account")
        try:
            limit = live.limit
            plan = await asyncio.to_thread(
                self._continue_blocking, row, str(account or "").strip(), limit, live.limit_since
            )
            lease, source, client, current = plan
            # The process goes BEFORE the copy: its last write lands first.
            await self._stop_process(live, why="moving to another account")
            await asyncio.to_thread(self._carry, lease, source, client, current, limit)
            from ..terminals import pane_accounts as PA

            out = PA.PaneAccounts()
            out.use_lease(W.PROXY[CLAUDE], lease)
            rec = out.records[W.PROXY[CLAUDE]]
            row = await asyncio.to_thread(
                store.update, self.engine, sid, account=str(lease.profile_id), account_json=json.dumps(rec),
            )
            note = W.CONTINUED.format(title=lease.title)
            live.limit, live.limit_since = None, ""
            live.account = str(lease.profile_id)
            self._emit(live, make("notice", text=clip(note, 500)))
        finally:
            live.claim = ""
        return {"session": self._with_live(store.view(row)), "account": lease.profile_id,
                "account_name": lease.title, "note": note}

    def _continue_blocking(self, row: Any, account: str, limit: Any, since: str) -> tuple[Any, Path, Any, str]:
        """BLOCKING: pick the next account and FIND the conversation (and check
        it can be copied) — refusing in words BEFORE Iron-Proxy is told
        anything or anything is copied. Returns ``(lease, source file, client,
        current account id)``."""
        from ..iron_proxy import accounts as _acc
        from ..terminals import continue_on as CO
        from ..terminals import pane_accounts as PA

        rec = store.account_record(row)
        current = str(rec.get("id") or "")
        if rec.get("source") != "iron-proxy" or not current:
            raise Refused(W.THIS_PC)
        svc = getattr(self.platform, "iron_proxy", None)
        if svc is None or not getattr(svc, "enabled", False):
            raise Refused(PA._OFF)
        try:
            client = svc.lease_client(timeout_s=PA.LEASE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            raise Refused(PA._unavailable_sentence(exc)) from None
        if client is None:
            raise Refused(PA._OFF)
        try:
            profiles, states = client.profiles(), client.states()
        except Exception as exc:  # noqa: BLE001
            raise Refused(PA._unavailable_sentence(exc)) from None
        profiles = [p for p in (profiles if isinstance(profiles, list) else []) if isinstance(p, dict)]
        states = states if isinstance(states, dict) else {}
        if account:
            lease = None
            for prof in CO._others(profiles, current):
                if str(prof.get("id")) != account:
                    continue
                st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), dict) else {}
                cli = prof.get("cli") if isinstance(prof.get("cli"), dict) else {}
                home = str(cli.get("home") or "").strip()
                if home and CO._usable(st):
                    pid = str(prof.get("id"))
                    lease = _acc.make_lease(PA.SPECS[CO.PROVIDER], pid, str(prof.get("title") or pid),
                                            home, sorted(PA.PANE_OWN_KEYS[CO.PROVIDER]))
                else:
                    raise Refused(f'"{prof.get("title") or account}" cannot take over right now.')
            if lease is None:
                raise Refused("That account is not another Claude account in Iron-Proxy.", 404)
        else:
            lease = CO._own_choice(profiles, states, current)
            if lease is None:
                raise Refused(CO._none_free(profiles, states, current).sentence)
        home = str(rec.get("home") or "")
        source = CO.newest_recorded(home, row.harness_session_id) if home else None
        if source is None and home:
            try:
                hits = list((Path(home) / "projects").glob(f"*/{row.harness_session_id}.jsonl"))
            except OSError:
                hits = []
            source = hits[0] if hits else None
        if source is None or not source.is_file():
            raise Refused(W.NO_CONVERSATION_FILE)
        dest = Path(lease.home) / "projects" / source.parent.name / source.name
        if not str(lease.home or "").strip():
            raise Refused(W.CANNOT_COPY)
        if dest.exists() and dest.resolve() != source.resolve() and not (
            CO._same_bytes(source, dest) or CO._is_prefix(dest, source)
        ):
            raise Refused(W.DIVERGED.format(title=lease.title))
        return lease, source, client, current

    def _carry(self, lease: Any, source: Path, client: Any, current: str, limit: Any) -> None:
        """BLOCKING: tell Iron-Proxy (ONLY the limit message) and COPY the
        conversation into the next account's home (``carry_over``: never
        moved, an older copy kept in Iron Jarvis's trash)."""
        from ..iron_proxy import accounts as _acc
        from ..terminals import continue_on as CO

        if limit is not None:
            try:
                client.signal(current, text=limit.signal_text())
            except Exception as exc:  # noqa: BLE001 — the user pressed the button: carry on
                _acc._note("continue signal", exc)
        carried = CO.carry_over(source, lease.home, lease.title, "", self.home / "trash")
        if not carried.resumed:
            raise Refused(W.CANNOT_COPY)


# --------------------------------------------------------------------------- #
# Boot
# --------------------------------------------------------------------------- #
#: How much of a session's log the boot reconcile reads (its END).
_RECONCILE_TAIL = 256 * 1024


def reconcile_cut_turns(home: Any) -> int:
    """BLOCKING (boot, off the loop): a turn a crash cut off (its last event
    is not a ``done``) gets its error + ``done`` (interrupted) appended to the
    session's ``events.jsonl``, so a reopened window shows it ended. Returns
    how many were closed. Never raises."""
    root = Path(home) / "hosted"
    closed = 0
    try:
        logs = list(root.glob("*/events.jsonl"))
    except OSError:
        return 0
    for path in logs[:2000]:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - _RECONCILE_TAIL))
                tail = fh.read().splitlines()
            last = None
            for raw in reversed(tail):
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("turn"):
                    last = ev
                    break
            if last is None or last.get("type") == "done":
                continue
            turn = last["turn"]
            rows = [
                {**W.error(STOPPED_TITLE, STOPPED_FIX, ""), "turn": turn},
                {**make("done", stop="interrupted"), "turn": turn},
            ]
            with open(path, "ab") as fh:
                fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"))
            closed += 1
        except OSError:
            continue
    return closed


def service_for(platform: Any) -> HostedService:
    svc = getattr(platform, "hosted", None)
    if svc is None:
        svc = HostedService(platform)
        platform.hosted = svc
    return svc


def waiting_rows(platform: Any) -> list[dict[str, Any]]:
    """The bell's rows (see :meth:`HostedService.waiting_rows`); [] when the
    Code view was never used. Never raises."""
    svc = getattr(platform, "hosted", None)
    if svc is None:
        return []
    try:
        return svc.waiting_rows()
    except Exception:  # noqa: BLE001 — a listing never fails over this
        log.warning("could not list waiting Code-view sessions", exc_info=True)
        return []
