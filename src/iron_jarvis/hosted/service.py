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
the oldest IDLE one (no turn, no open ask) is stopped first, else the start
WAITS for one to finish (v1.425.0, B7 — said once; refused in words after
:data:`POOL_WAIT_S`). The pool's lock is held only to decide (a starting
session holds its place, ``LiveSession.starting``), never while a tool
starts, so one session's slow start never holds another's message (v1.425.0).
A process with no turn for :data:`IDLE_S` is stopped by the reaper; the next
message starts it again RESUMING THE SAME harness session
(``claude --resume <id>`` / ``thread/resume``). Archive, delete and the
daemon's shutdown stop it. Every stop is a tree kill, off the loop.

NOTHING IS GRANTED HERE: every permission question the harness asks becomes a
card (``approval`` / ``question``); only the user's answer goes back. "Allow
for this session" is the harness's OWN session-wide allow, offered only when
the harness offers one (and never one that would switch on Full access).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Collection
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
from . import pi_catalog as PC
from . import pi_trust as PT
from .pi import PiDriver
from . import opencode as O
from . import opencode_catalog as OCC
from .opencode import HARNESS as OPENCODE
from .opencode import OpenCodeDriver
from . import grok as G
from .grok import HARNESS as GROK
from .grok import GrokDriver
from . import antigravity as A
from .antigravity import HARNESS as ANTIGRAVITY
from .antigravity import AntigravityDriver
from . import antigravity_catalog as ACC
from . import antigravity_agents as AGA
from . import branching as BR
from . import jarvis_tools as JT
from . import news as NEWS
from .events import TEXT_CHARS, EventLog, clip, coalesce_into, make, session_dir
from . import history as HIST  # v1.418.0 (C4.6, C4.11)
from . import queued as Q  # v1.418.0 (C4.8)
from . import programs as PG  # v1.422.0 (B4, C4.7)
from . import typed as T  # v1.439.0: Claude Code's and Grok's typed built-ins
from .process import HarnessGone, HarnessProcess, binary_for, build_env
from . import hostlink as HL  # v1.422.0: the code host
from . import open_steps as OS  # v1.427.2: a step runs only while its tool does
from . import archive_summary as AS  # v1.453.0: the summary kept at archive time
from . import events as EV
from . import handoff as HO  # v1.474.0: Hand off

log = logging.getLogger(__name__)

HARNESSES: tuple[str, ...] = (CLAUDE, CODEX, PI, OPENCODE, GROK, ANTIGRAVITY)
#: The tools that sign in by themselves (no Iron-Proxy account applies).
#: v1.439.0: Antigravity too (its own Google sign-in; no Iron-Proxy vendor).
OWN_SIGN_IN: tuple[str, ...] = (PI, OPENCODE, ANTIGRAVITY)
#: v1.416.0: the tools whose model list is what their PROCESS last said (never
#: a start just to ask), each model with its own effort levels, and which keep
#: a reopened conversation's last model.
LISTED_MODELS: tuple[str, ...] = (PI, OPENCODE, GROK, ANTIGRAVITY)
#: v1.416.0: the tools listed only when installed (Grok is newer to the view;
#: the four before it are always listed, with "not installed" said).
ONLY_WHEN_INSTALLED: tuple[str, ...] = (GROK, ANTIGRAVITY)
#: At most this many harness processes at once.
MAX_LIVE = 6
#: v1.425.0 (Wave 5, B7): with every place taken by a session that is working
#: (none can be stopped), a new message WAITS this long for one to finish — the
#: idle reaper's own period — before it is refused in words.
POOL_WAIT_S = 15 * 60.0
#: ...and looks again at least this often (a tool's background work ending is
#: not announced).
POOL_RECHECK_S = 5.0
#: A process with no turn for this long is stopped by the reaper.
IDLE_S = 15 * 60.0
#: How often the reaper looks.
REAP_EVERY_S = 60.0
#: v1.417.0 (a USER BUG): a process whose tool still runs BACKGROUND tasks
#: (Claude Code's helper agents and background commands, which outlive the
#: reply that started them) is never reaped or evicted — stopping it killed
#: every helper. Bounded: once the tool has said nothing for this long the
#: reaper treats the session as idle again (a forgotten dev server must not
#: hold a process forever).
BACKGROUND_SILENT_MAX_S = 2 * 60 * 60.0
#: v1.417.0: a reply the tool started BY ITSELF (Claude Code's report once its
#: helpers end) counts as being written while the tool was heard from within
#: this long — the Code view reads the session again meanwhile, and a reply
#: whose end never came stops being one.
SELF_REPLY_QUIET_S = 10 * 60.0
#: How long a finished turn stays in memory for a rejoin.
FINISHED_KEEP_S = 600.0
#: v1.414.3: how long a reply's end waits for the tool's session id to be
#: saved. v1.417.0 (C4.7): 10 -> 2 s — past it the save goes on in the
#: background (it is shielded) and the reply ends now.
ID_SAVE_WAIT_S = 2.0
#: v1.417.0 review: how long a press that NEEDS the id ("Open in terminal",
#: "Continue on the next account") waits for a save still running.
ID_SAVE_LATE_S = 15.0
#: The newest events ``GET /build/app/sessions/{id}`` returns by default.
DEFAULT_EVENTS = 2000
#: A turn's in-memory replay keeps at most this many characters of streamed
#: text; past it only the non-delta events are kept (``text_done`` still
#: carries every answer whole).
MAX_TURN_CHARS = 8_000_000
#: v1.472.0: a live viewer's streamed pieces are merged for this long before
#: they are sent (one message instead of one per piece). Under a frame at
#: 25 a second, so nothing looks later; a word is never held longer.
LIVE_MERGE_S = 0.04
#: The interrupt route answers within this (the harness is told; the turn
#: ends when it says so, or when its process goes).
INTERRUPT_WAIT_S = 5.0
#: v1.412.0: a note sent while the tool is still STARTING its turn waits this
#: long for the turn to be open before it is refused in words.
STEER_WAIT_S = 20.0
#: Disk listings read at most this many conversation files (newest first).
DISK_FILES = 500

from .claude import ALLOW_COMMANDS  # noqa: E402 — v1.417.0, kept apart from the imports above

#: v1.417.0 (C1.1): every answer the route takes — the driver's three and
#: "Allow all commands this session".
ANSWER_DECISIONS: tuple[str, ...] = DECISIONS + (ALLOW_COMMANDS,)


def metric_decision(kind: str, decision: str) -> str:
    """v1.417.0 (C1.11): a Build › Code answer in the autonomy metric's own
    words (``core.approvals.InterruptNote.answered``): an approval's once /
    conversation / deny, a question's answered / skipped."""
    if kind == "question":
        return "skipped" if decision == "deny" else "answered"
    if decision == "deny":
        return "deny"
    if decision in ("allow_session", ALLOW_COMMANDS):
        return "conversation"
    return "once"

#: Effort levels each harness takes.
EFFORTS: dict[str, tuple[str, ...]] = {
    CLAUDE: ("low", "medium", "high", "xhigh", "max"),
    CODEX: ("minimal", "low", "medium", "high", "xhigh"),
    PI: P.EFFORTS,
    OPENCODE: O.EFFORTS,
    GROK: G.EFFORTS,
    ANTIGRAVITY: A.EFFORTS,
}
_MODEL_RX = re.compile(r"^[A-Za-z0-9._:/\-\[\]]{1,120}$")
#: A harness session id typed into a terminal: a UUID (Claude) or Codex's
#: UUID-shaped thread id — nothing else is ever typed.
_SID_RX = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

def typable_sid(harness: str, hid: str) -> bool:
    """Is ``hid`` a session id Iron Jarvis would type into a terminal — a
    UUID, or (v1.413.0) OpenCode's own ``ses_…`` id for an OpenCode row?"""
    if _SID_RX.match(str(hid or "")):
        return True
    return harness == OPENCODE and bool(O.SESSION_ID_RX.match(str(hid or "")))


STOPPED_TITLE = "Iron Jarvis stopped while this was working"
STOPPED_FIX = "Send the message again — the conversation is kept."


class _StoppedWhileWaiting(Exception):
    """v1.425.0 (B7): the user pressed Stop while the message waited for a
    place in the pool — the turn already ended ("interrupted"), nothing was
    started, nothing is said again."""


class Refused(Exception):
    """A request the Code view refuses, in one plain sentence."""

    def __init__(self, sentence: str, status: int = 409, field: str = "", code: str = "",
                 choices: list[dict[str, str]] | None = None) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status
        #: v1.427.3: Continue on another account found none free — the
        #: accounts the user may still try anyway (``[{id, title, why}]``).
        self.choices = list(choices or [])
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
    if harness == OPENCODE:
        return O.MODES
    if harness == GROK:
        return G.MODES
    if harness == ANTIGRAVITY:
        return A.MODES
    return CLAUDE_MODES if harness == CLAUDE else CODEX_MODES


#: Every preset in the menu's order: a tool's OWN rules first (v1.439.0:
#: Antigravity's default; Pi's only mode), then the shared ones, Claude Code's
#: own Auto (v1.412.0) and Full access.
MODE_ORDER: tuple[str, ...] = (P.OWN, "ask", "edits", "plan", "auto", "full")


def mode_about(harness: str, mode: str) -> str:
    """v1.412.0: what a preset does for THIS tool, in plain words (the mode
    menu says it under each one)."""
    if harness == PI:
        return P.MODE_ABOUT.get(mode, "")
    if harness == OPENCODE:
        return O.MODE_ABOUT.get(mode, "")
    if harness == GROK:
        return G.MODE_ABOUT.get(mode, "")
    if harness == ANTIGRAVITY:
        return A.MODE_ABOUT.get(mode, "")
    if harness == CODEX:
        return CODEX_ABOUT.get(mode, "")
    return CLAUDE_ABOUT.get(mode, "")


def mode_label(harness: str, mode: str) -> str:
    """A preset's words for this tool ("Pi's own rules" is Pi's alone)."""
    if harness == OPENCODE and O.agent_of(mode):
        return O.agent_of(mode)  # v1.441.0: the user's own agent, by its name
    if harness == ANTIGRAVITY and AGA.agent_of(mode):
        return AGA.agent_of(mode)  # v1.447.0: the user's own Antigravity agent
    if harness == PI and mode in P.MODE_LABELS:
        return P.MODE_LABELS[mode]
    if harness == ANTIGRAVITY and mode in A.MODE_LABELS:
        return A.MODE_LABELS[mode]
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
    rows = [
        {"id": m, "label": mode_label(harness, m), "dangerous": m == "full", "about": mode_about(harness, m)}
        for m in MODE_ORDER if m in modes_for(harness)
    ]
    if harness == OPENCODE:
        # v1.441.0: the user's own OpenCode agents (what an OpenCode process
        # listed), before Full access.
        agents = O.agent_mode_rows()
        at = next((i for i, r in enumerate(rows) if r["id"] == "full"), len(rows))
        rows[at:at] = agents
    if harness == ANTIGRAVITY:
        # v1.447.0: the user's own Antigravity agents (``agy agents``), before
        # Full access.
        at = next((i for i, r in enumerate(rows) if r["id"] == "full"), len(rows))
        rows[at:at] = AGA.agent_mode_rows()
    return rows


def hidden_mode_rows(harness: str) -> list[dict[str, str]]:
    if harness == CLAUDE:
        return []
    hidden = (P.HIDDEN_MODES if harness == PI else O.HIDDEN_MODES if harness == OPENCODE
              else G.HIDDEN_MODES if harness == GROK else A.HIDDEN_MODES if harness == ANTIGRAVITY
              else CODEX_HIDDEN)
    return [{"id": m, "label": MODE_LABELS.get(m, m), "reason": why} for m, why in hidden.items()]


def check_mode(harness: str, mode: str, confirm: bool) -> str:
    raw = str(mode or default_mode(harness)).strip()
    if harness == OPENCODE and O.agent_of(raw):
        # v1.441.0: one of the user's own OpenCode agents (its name keeps its
        # case) — only one an OpenCode process listed.
        if any(a["name"] == O.agent_of(raw) for a in O.known_agents()):
            return raw
        raise Refused(W.OPENCODE_UNKNOWN_AGENT.format(agent=O.agent_of(raw)), 400)
    if harness == ANTIGRAVITY and raw.startswith(AGA.AGENT_PREFIX):
        # v1.447.0: one of the user's own Antigravity agents — only one
        # ``agy agents`` listed (an unknown name would run the default agent
        # with no word over its protocol: measured).
        if AGA.agent_of(raw) and AGA.agent_of(raw) in AGA.known_agents():
            return raw
        raise Refused(AGA.UNKNOWN_AGENT.format(agent=clip(raw[len(AGA.AGENT_PREFIX):], 80)), 400)
    mode = raw.lower()
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
    if harness == CODEX:
        # v1.424.0 (D2): the levels Codex's OWN model list names for that
        # model (its models_cache.json, the cached reading — never a start).
        known_codex = _codex_efforts(model)
        if known_codex is not None:
            return known_codex
    if harness in LISTED_MODELS and pi_rows and (model or harness in (OPENCODE, GROK, PI, ANTIGRAVITY)):
        # (OpenCode, Grok and — v1.429.0 — Pi: the default row "" carries its
        # default model's levels, when they are known.)
        for r in pi_rows:
            if r.get("id") == model and isinstance(r.get("efforts"), list):
                if harness == OPENCODE:
                    return tuple(str(x) for x in r["efforts"])  # OpenCode's own names, its order
                return tuple(x for x in EFFORTS[harness] if x in r["efforts"])
    return EFFORTS[harness]


def _kept_commands(harness: str, folder: str = "") -> list[dict[str, Any]]:
    """v1.439.0: the `/` list a tool's process said last (this process). For a
    tool whose list is its FOLDER's (OpenCode: a project's own commands) only
    that folder's; with none kept for it, or no folder, only the commands
    EVERY folder seen had (two or more), so one project's commands never show
    in another project's session."""
    from .claude import FOLDER_COMMANDS, LAST_COMMANDS, commands_key

    if harness not in FOLDER_COMMANDS:
        return list(LAST_COMMANDS.get(harness, []))
    if folder:
        own = LAST_COMMANDS.get(commands_key(harness, folder))
        if own:
            return list(own)
    if LAST_COMMANDS.get(harness):
        # v1.441.0 (review P3): Claude Code also keeps the part of its list no
        # folder changes (its own built-ins, ``builtin: true``) under the bare
        # key — a session in a folder it has not been read for shows those.
        return list(LAST_COMMANDS[harness])
    prefix = f"{harness}@"
    # (review) a COPY: the harnesses listing reads this on a worker thread
    # while the loop may keep another folder's list (a dict that changes size
    # during iteration raises).
    lists = [v for k, v in LAST_COMMANDS.copy().items() if k.startswith(prefix) and v]
    if len(lists) < 2:
        return []
    common = set.intersection(*({str(c.get("name") or "") for c in v} for v in lists))
    return [dict(c) for c in lists[0] if str(c.get("name") or "") in common]


def _own_commands(harness: str, live: "LiveSession | None", folder: str) -> list[dict[str, Any]]:
    """v1.441.0 (review P3): the `/` list the tool said FOR THIS FOLDER — its
    running process's, else the one kept for the folder ([] when none was
    read for it; never another folder's, never the lists' common part)."""
    from .claude import FOLDER_COMMANDS, LAST_COMMANDS, commands_key

    if live and live.driver and live.driver.commands:
        return list(live.driver.commands)
    if harness in FOLDER_COMMANDS:
        return list(LAST_COMMANDS.get(commands_key(harness, folder)) or []) if folder else []
    return list(LAST_COMMANDS.get(harness) or [])


def _codex_efforts(model: Any) -> "tuple[str, ...] | None":
    """v1.424.0 (D2): Codex's effort levels for ``model`` as its own list
    names them (``providers.codex_models``), in this view's order — None
    when the list is not read or names none for it. Never blocks."""
    try:
        from ..providers import codex_models

        row = codex_models.lookup(model)
    except Exception:  # noqa: BLE001 — unknown: the tool's own list
        return None
    if row is None or not row.get("efforts"):
        return None
    return tuple(x for x in EFFORTS[CODEX] if x in row["efforts"])


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


#: v1.459.0: the tools whose own compact is a "/compact" MESSAGE (the Session
#: tab's Context press sends it; every other tool compacts by itself).
#: Antigravity also lists ``compact`` but was never measured here.
COMPACTS_BY_MESSAGE = (CLAUDE, CODEX, PI, OPENCODE, GROK)
#: OpenCode's compact in its `/` menu (it does not list it itself).
OPENCODE_COMPACT_ROW: dict[str, str] = {
    "name": "compact",
    "description": "Summarize the conversation to free up room (OpenCode's own compact).",
}


# --------------------------------------------------------------------------- #
# Homes and the harnesses' own saved sessions
# --------------------------------------------------------------------------- #
def default_home(harness: str) -> str:
    """The CLI's own default home (this PC's login): the daemon's environment
    variable, else ``~/.claude`` / ``~/.codex`` / ``~/.pi/agent``."""
    if harness == PI:
        return str(P.agent_dir())
    if harness == OPENCODE:
        return str(O.data_dir())
    if harness == GROK:
        return str(G.default_home())
    if harness == ANTIGRAVITY:
        return str(A.default_home())
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

    if harness == OPENCODE:
        # OpenCode keeps sessions in its own database (read-only here).
        return O.disk_sessions(home, folder, since=since, limit=DISK_FILES)
    if harness == GROK:
        # Grok keeps each session's summary.json in its own folder (read-only here).
        return G.disk_sessions(home, folder, since=since, limit=DISK_FILES)
    if harness == ANTIGRAVITY:
        # Antigravity keeps its conversations' summaries in its own database
        # (read-only here).
        return A.disk_sessions(home, folder, since=since, limit=DISK_FILES)
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


def pi_keeps_last(row: Any) -> bool:
    """BLOCKING: does this Pi (or, v1.413.0, OpenCode) session's conversation
    already hold messages? Then the tool, reopening it, restores the model and
    thinking level it last used (Pi: core/sdk.js; OpenCode: ``session/load``
    keeps them, measured), so "its default" no longer applies to it."""
    hid = str(getattr(row, "harness_session_id", "") or "")
    if getattr(row, "harness", "") == GROK and hid:
        try:
            return G.has_messages(home_of(GROK, store.account_record(row)), hid)
        except Exception:  # noqa: BLE001 — not known: the default stays offered
            return False
    if getattr(row, "harness", "") == OPENCODE and hid:
        try:
            return O.has_messages(home_of(OPENCODE, None), hid)
        except Exception:  # noqa: BLE001 — not known: the default stays offered
            return False
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
    return {"claude": CLAUDE, "codex": CODEX, "pi": PI, "opencode": OPENCODE, "grok": GROK,
            "agy": ANTIGRAVITY}.get(cli)


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


def _pane_since(pane: Any) -> "float | None":
    """When the pane started (epoch s, 5 s early for a coarse clock), or None."""
    created = getattr(pane, "created_at", None)
    if isinstance(created, datetime):
        return (created if created.tzinfo else created.replace(tzinfo=timezone.utc)).timestamp() - 5
    return None


def claude_pane_home(pane: Any) -> str:
    """The Claude Code home a Build terminal's ``claude`` reads and writes.
    v1.426.0 review: THE pane rule (``terminals/continue_on.claude_home``,
    which the restart Resume and Continue on the next account already read):
    its Iron-Proxy account's recorded home; ``~/.claude`` for "this PC's
    login" (``source: "default"`` — the pane REMOVED an inherited
    ``CLAUDE_CONFIG_DIR``, so the daemon's own is ANOTHER account's home);
    else the daemon's environment. ``home_of`` read the daemon's
    ``CLAUDE_CONFIG_DIR`` for a "this PC" pane and handed over a conversation
    from another account's home."""
    from ..terminals import continue_on as CO

    return CO.claude_home(pane)


#: v1.426.0 review (E3): the command Claude Code records as the FIRST thing a
#: conversation a ``/clear`` started says. MEASURED on this PC's own
#: transcripts (claude 2.1.280 and 2.1.296): the new conversation's file
#: begins with a ``mode`` / ``file-history-snapshot`` line, a meta caveat
#: record (``isMeta``), then a ``user`` record whose content is
#: ``<command-name>/clear</command-name>…`` — both carrying the NEW session's
#: id. An ordinary conversation never begins that way. ``/reset`` and ``/new``
#: are Claude Code's own aliases of ``/clear``.
_CLEAR_COMMANDS = frozenset({"/clear", "/reset", "/new"})
_CLEAR_RECORD_RX = re.compile(r"\s*<command-name>\s*(/[A-Za-z-]+)\s*</command-name>")
#: How much of a conversation's head is read to find it (bounded: a file
#: can be tens of MB; the record measured sits within the first 8 lines).
CLEAR_HEAD_BYTES = 65_536
CLEAR_HEAD_LINES = 40


def started_by_clear(path: Any) -> bool:
    """v1.426.0 review (E3): does the Claude Code conversation in ``path``
    BEGIN with a ``/clear`` (its first non-meta user record is Claude Code's
    own command record)? BLOCKING (one bounded read of the file's head);
    never raises (False on any failure — never a guess)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(CLEAR_HEAD_BYTES)
    except OSError:
        return False
    try:
        for i, raw in enumerate(head.split(b"\n")):
            if i >= CLEAR_HEAD_LINES:
                return False
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isMeta"):
                continue
            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
            m = _CLEAR_RECORD_RX.match(content) if isinstance(content, str) else None
            # The FIRST thing the user's side of the conversation says decides.
            return bool(m) and m.group(1).lower() in _CLEAR_COMMANDS
    except Exception:  # noqa: BLE001 — a reading never breaks a hand-over
        return False
    return False


def claude_later(pane: Any, home: str, recorded: str, *, taken: "Collection[str]" = ()) -> list[dict[str, Any]]:
    """v1.426.0 (Wave 6, E3): the conversations a Build terminal's Claude
    Code moved to AFTER the one Iron Jarvis recorded for it: what a
    ``/clear`` (a new conversation id) typed inside the terminal leaves
    behind. BLOCKING; never raises ([] on any failure).

    Empty while the recorded conversation is still the NEWEST in its folder
    (``terminals/continue_on.newest_recorded``: one glob and one folder's
    stats, nothing opened; the usual case). Otherwise: the conversations FOR
    THE PANE'S FOLDER in the same Claude home, written since the pane started
    and after the recorded one was last written, that a ``/clear`` STARTED
    (:func:`started_by_clear`), newest first, minus ``taken`` (the ids another
    holder owns: a Code-view row's conversation, or another live terminal's
    recorded one, are THEIRS, never this pane's). Each is the history
    reader's summary (``id``, ``title``, ``mtime``).

    v1.426.0 review: "newer in the same folder" alone is NOT the pane's — the
    user's own Claude Code running OUTSIDE Iron Jarvis in the same folder (the
    Claude app, an editor's panel) writes such files all the time, and its
    conversation was handed over (and could be resumed while that Claude still
    wrote it). Only a conversation a ``/clear`` began counts. A ``/resume``
    typed in the terminal (an EXISTING conversation appended) is not read: the
    recorded one is handed over, as before v1.426.0. Known: the user's own
    outside Claude ``/clear``-ing in the same folder while the terminal did not
    still reads as the terminal's."""
    from ..history import index as HI
    from ..terminals import continue_on as CO

    try:
        if not home or not _SID_RX.match(str(recorded or "")):
            return []
        if CO.newest_recorded(home, recorded) is not None:
            return []
        since = _pane_since(pane)
        floor = since if since is not None else 0.0
        for path in (Path(home) / "projects").glob(f"*/{recorded}.jsonl"):
            try:
                floor = max(floor, path.stat().st_mtime)
            except OSError:
                continue
            break
        skip = {str(t) for t in taken if t} | {str(recorded)}
        folder = str(getattr(pane, "cwd", "") or "")
        later: list[dict[str, Any]] = []
        # Cheapest first: a stat, then the bounded head read, and only then
        # the history reader's summary (it reads the whole file).
        for path in _conversation_files(CLAUDE, home):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime <= floor or path.stem in skip or not started_by_clear(path):
                continue
            s = HI.summarize(CLAUDE, path)
            if s and same_folder(s.get("project"), folder) and str(s.get("id") or "") not in skip:
                s["mtime"] = mtime
                later.append(s)
        later.sort(key=lambda s: float(s.get("mtime") or 0), reverse=True)
        return later
    except Exception:  # noqa: BLE001 - a reading never breaks a hand-over
        log.debug("could not read which conversation a terminal moved to", exc_info=True)
        return []


def other_terminal_ids(terminals: Any, besides: str) -> set[str]:
    """The Claude session ids every OTHER live Build terminal recorded."""
    out: set[str] = set()
    try:
        for info in terminals.list():
            if str(info.get("id")) == str(besides) or not info.get("alive", True):
                continue
            pane = terminals.get(info["id"])
            sid = str(getattr(pane, "claude_session_id", None) or "") if pane is not None else ""
            if sid:
                out.add(sid)
    except Exception:  # noqa: BLE001
        pass
    return out


def terminal_holds(platform: Any, hid: str, *, harness: str = CLAUDE, rows: Any = None) -> bool:
    """Does a live Build terminal hold this conversation, its CLI still
    running? Claude Code: the session id the pane recorded, and (v1.426.0,
    Wave 6, E3) a conversation it moved to since with ``/clear``
    (:func:`claude_later`): the Code view must never open a SECOND writer on
    the conversation a running terminal's Claude writes now. A Code-view
    row's own conversation (``rows``) and another terminal's recorded one are
    never counted as this pane's: they are theirs. Codex and Pi record none
    (v1.411.0), so a terminal holds one when it runs that CLI and either the
    Code view opened it there (a row's ``terminal_id``) or its own screen
    still shows the id it was started with (``pi --session <id>`` /
    ``codex resume <id>`` typed in it). Never raises."""
    terminals = getattr(platform, "terminals", None)
    if terminals is None or not hid:
        return False
    opened = {str(r.terminal_id) for r in (rows or [])
              if getattr(r, "terminal_id", "") and r.harness == harness and r.harness_session_id == hid}
    row_ids = {str(getattr(r, "harness_session_id", "") or "") for r in (rows or [])} - {""}
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
                recorded = str(getattr(pane, "claude_session_id", None) or "")
                if recorded != hid:
                    if not recorded or hid in row_ids or pane_exited(pane):
                        continue
                    taken = row_ids | other_terminal_ids(terminals, str(info["id"]))
                    later = claude_later(pane, claude_pane_home(pane), recorded, taken=taken)
                    if hid not in {str(s.get("id") or "") for s in later}:
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
#: v1.415.0: how long the Context drop-down waits for the tool's own answer.
CONTEXT_READ_S = 20.0


def _iso_after(a: Any, b: Any) -> bool:
    """Is ISO time ``a`` at or after ``b``? (False when ``a`` is unreadable;
    True when only ``b`` is.)"""
    def parse(v: Any) -> "datetime | None":
        if not isinstance(v, str) or not v.strip():
            return None
        try:
            t = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)

    ta, tb = parse(a), parse(b)
    if ta is None:
        return False
    return tb is None or ta >= tb


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
        #: v1.414.0: the service clock when the turn began (its length for the
        #: "Your coding session finished" notice — :mod:`.news`).
        self.started_mono: float | None = None
        #: v1.414.0: an account's limit card was shown in this turn (that card
        #: is the news: no finish notice beside it).
        self.account_limit = False
        self._chars = 0
        #: True once the turn's end is being written (the done follows).
        self.ending = False
        #: v1.425.0 (review, B7): True from the moment ``_drive`` takes this
        #: message until it hands it to the tool (``send_user``) — while it
        #: waits for a place in the pool or its tool STARTS (the handshake
        #: included). A Stop then ends the turn itself: the tool has nothing to
        #: interrupt. False for every turn that did not begin in ``_drive``.
        self.unsent = False

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
                batch, ended = await _gather_live(q, ev)
                yield "".join(sse(e["type"], e) for e in batch)
                if ended:
                    return
        finally:
            self._followers.discard(q)


def _is_piece(ev: dict[str, Any]) -> bool:
    return ev.get("type") in ("text", "thinking") and "delta" in ev


async def _gather_live(q: asyncio.Queue, first: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """v1.472.0: ``first`` and what follows it, merged the replay's way
    (:func:`.events.coalesce_into`: pieces of one item joined and masked
    again, a step's partial update replaced by the newer one). A streamed
    piece waits up to :data:`LIVE_MERGE_S` for more; anything else is sent
    with whatever is already queued. Returns the batch and whether the turn
    ended (its ``None`` was taken off the queue)."""
    batch: list[dict[str, Any]] = []
    coalesce_into(batch, first)
    if _is_piece(first):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LIVE_MERGE_S
        while True:
            left = deadline - loop.time()
            if left <= 0:
                break
            try:
                nxt = await asyncio.wait_for(q.get(), left)
            except TimeoutError:
                break
            if nxt is None:
                return batch, True
            coalesce_into(batch, nxt)
            if not _is_piece(nxt):
                break
    while True:
        try:
            nxt = q.get_nowait()
        except asyncio.QueueEmpty:
            return batch, False
        if nxt is None:
            return batch, True
        coalesce_into(batch, nxt)


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
        #: v1.417.0: when the running tool last sent anything (any frame,
        #: owned by a reply or not) — the background-work bound reads it.
        self.last_heard = self.last_active
        self.lock = asyncio.Lock()
        #: v1.425.0 (Wave 5, B7): this session holds a place in the pool while
        #: its tool STARTS (decided under the pool's lock, which the start no
        #: longer holds), and the conversation it starts on — never a victim,
        #: never a second process on the same conversation.
        self.starting = False
        self.starting_conv = ""
        #: The process must restart before the next message (an effort change).
        self.restart = False
        self.limit: Any = None
        #: v1.414.3: the background save of the tool's own session id; a
        #: reply's end waits for it, so the row read right after names it.
        self.id_save: asyncio.Future | None = None
        #: v1.441.0: the newest message's reference being written beside the
        #: log (``hosted/branching``); a reply's end waits for it.
        self.ref_save: asyncio.Future | None = None
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
        #: v1.414.0: the session's title, for Running now and the notices.
        self.title = ""
        #: v1.414.0: ask id -> the card's way to the phone (:class:`.news.PhoneWatch`).
        self.phones: dict[str, Any] = {}
        #: v1.414.0: the session's Capabilities (five boxes, the pane's shape),
        #: read LIVE by its Iron Jarvis token (:mod:`.jarvis_tools`), and its
        #: folder (where a tool call through that token runs).
        self.caps: dict[str, bool] = {}
        self.folder = ""
        #: v1.417.0 (C1.11): ask id -> its ``core.approvals.InterruptNote``
        #: (the autonomy metrics count Build › Code's cards too).
        self.notes: dict[str, Any] = {}
        #: v1.417.0 (C4.7): why the last process went ("idle" | "evicted"),
        #: said when the next message starts one again.
        self.stopped_why = ""
        #: v1.422.0 (B4, C4.7 — :mod:`.programs`): when the Code view last
        #: showed this session (service clock; a view read or its beat), the
        #: process whose FIRST step was noted and when (wall clock), what the
        #: reaper last kept it for (a :class:`.programs.Reading`, or None),
        #: reads in a row that could not answer, and — said at the next start
        #: and on the row — the programs the last stop ended and after how long.
        self.looked_at = float("-inf")
        self.first_step_proc: Any = None
        self.first_step_at: float | None = None
        self.keeping: Any = None
        self.unread = 0
        self.stopped_programs: list[str] = []
        self.stopped_note = ""
        self.stopped_after_s = 0.0
        #: v1.418.0 (C4.8): the messages waiting behind the running reply
        #: (:class:`.queued.SessionQueue`; None until first read).
        self.queue: Any = None
        #: v1.422.0 (the code host): what a restarted Iron Jarvis needs beside
        #: the driver's own state (OpenCode's rules note, the MCP servers its
        #: session and Grok's are told about).
        self.kept: dict[str, Any] = {}
        #: v1.422.0: "no code host, so this stops with a restart" said once.
        self.no_host_said = False
        #: v1.427.2: the steps the tool began and has not finished yet (item ->
        #: kind, name, title, parent, turn) — closed ``stopped`` when the
        #: process ends (:mod:`.open_steps`).
        self.open_steps: dict[str, dict[str, Any]] = {}

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.alive

    @property
    def working_alone(self) -> bool:
        """The tool is running something no message owns (Pi: a run an
        extension started by itself) — never reaped, never evicted."""
        return bool(self.driver is not None and getattr(self.driver, "busy_alone", False))

    @property
    def background_tasks(self) -> int:
        """v1.417.0: how many background tasks the tool says are still open
        (Claude Code's helper agents and background commands). A new message
        is still welcome; only the reaper and the pool leave the process be."""
        try:
            return int(getattr(self.driver, "background_tasks", 0) or 0) if self.driver is not None else 0
        except Exception:  # noqa: BLE001 — a driver's count must never break the pool
            return 0

    def keeps_working(self, now: float) -> bool:
        """Open background tasks the tool was heard from within
        :data:`BACKGROUND_SILENT_MAX_S`."""
        return self.background_tasks > 0 and now - self.last_heard < BACKGROUND_SILENT_MAX_S

    @property
    def background_helpers(self) -> int:
        """v1.417.0: the open background tasks that are helper AGENTS."""
        try:
            return int(getattr(self.driver, "background_helpers", 0) or 0) if self.driver is not None else 0
        except Exception:  # noqa: BLE001 — a driver's count must never break a view
            return 0

    def replying_alone(self, now: float) -> bool:
        """v1.417.0: the tool is writing a reply no message of the user's
        owns (Claude Code's report after its helpers ended; Pi's run of its
        own), heard from within :data:`SELF_REPLY_QUIET_S`. Never while a
        reply the user started runs (that reply shows itself)."""
        if not self.running or (self.turn is not None and self.turn.running):
            return False
        try:
            alone = self.working_alone or bool(getattr(self.driver, "replying_alone", False))
        except Exception:  # noqa: BLE001
            return False
        return alone and now - self.last_heard < SELF_REPLY_QUIET_S

    def helpers_working(self, now: float) -> bool:
        """v1.417.0: helpers still at work (the tool heard from within
        :data:`BACKGROUND_SILENT_MAX_S`) with no reply of the user's running —
        what Running now and the busy counts list beside the replies."""
        if not self.running or (self.turn is not None and self.turn.running):
            return False
        return self.background_helpers > 0 and now - self.last_heard < BACKGROUND_SILENT_MAX_S

    @property
    def busy(self) -> bool:
        return ((self.turn is not None and self.turn.running) or bool(self.asks) or bool(self.claim)
                or self.working_alone)


def _stoppable(lv: "LiveSession", now: float) -> bool:
    """v1.425.0 (Wave 5, B7): may the pool stop this session to make room?
    Only an IDLE one — its process up, no start in flight, no reply running,
    no card open, nothing claimed, no background work (the pool's rules since
    v1.409.0 / v1.417.0; :func:`programs.evict_pick` then puts one that serves
    a program, or that the user looks at, last)."""
    return lv.running and not lv.starting and not lv.busy and not lv.keeps_working(now)


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
def heard_from(driver: Any, obj: dict[str, Any]) -> bool:
    """v1.441.0 review: does this frame count as hearing from the tool (the
    two-silent-hours rule)? Not the answer to Iron Jarvis's own background
    read. Never raises."""
    try:
        return not (driver is not None and driver.quiet_frame(obj))
    except Exception:  # noqa: BLE001 — a driver's check must never drop a frame
        return True



def _plan_exit_mode(ask: Any, decision: str, driver: Any = None) -> str:
    """v1.441.0 (integration): the mode an approved plan leaves the session
    in for a driver with no rule of its own (Codex's plan card). The card's
    ``leaves_plan_to`` is used only for an allow, never over a mode the
    button names itself (``sets_mode``)."""
    if decision not in ("allow", "allow_session"):
        return ""
    if decision == "allow_session" and ask.data.get("sets_mode"):
        return ""
    back = ask.data.get("leaves_plan_to") or ""
    return back if back in MODE_LABELS and back not in ("plan", "full") else ""

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
        #: v1.425.0 (B7): set when a place in the pool may have come free.
        self._room: asyncio.Event | None = None
        self._handover_lock: asyncio.Lock | None = None
        #: The model list a Pi process last said, kept for the next listing.
        self._pi_catalog = self.home / "hosted" / "pi_models.json"
        #: The model list an OpenCode process last said (v1.413.0).
        self._oc_catalog = self.home / "hosted" / "opencode_models.json"
        #: v1.441.0: OpenCode's own list, read in the background before any
        #: session (``opencode models --verbose``, hosted/opencode_catalog).
        self._oc_read = self.home / "hosted" / "opencode_models_read.json"
        #: v1.441.0: the user's own OpenCode agents each process listed.
        self._oc_agents = self.home / "hosted" / "opencode_agents.json"
        #: The model list a Grok process last said (v1.416.0).
        self._grok_catalog = self.home / "hosted" / "grok_models.json"
        #: The model list an Antigravity process last said (v1.439.0).
        self._agy_catalog = self.home / "hosted" / "antigravity_models.json"
        # v1.443.0: what one short background ``agy models`` read listed (and
        # whether it said Antigravity is signed in), before any session ran.
        self._agy_read = self.home / "hosted" / "antigravity_read.json"
        #: v1.447.0: the user's own Antigravity agents (``agy agents``, read
        #: in the background; hosted/antigravity_agents).
        self._agy_agents = self.home / "hosted" / "antigravity_agents.json"
        #: v1.414.0: how long a card waits in the app before the phone gets
        #: it (``harness/stall.PHONE_AFTER_S``; a test sets it short).
        from ..harness.stall import PHONE_AFTER_S

        self.phone_after_s = PHONE_AFTER_S
        #: v1.414.0: Iron Jarvis's own tools for a session — tokens of their
        #: own (memory only), revoked with the process.
        self.tokens = JT.HostedTokens(self)
        #: v1.415.0: the newest Context reading a tool gave per session
        #: (memory only; the Session tab's drop-down).
        self._context_seen: dict[str, dict[str, Any]] = {}
        #: v1.441.0 (review P4): a name given while the session's tool was NOT
        #: running — told to the tool once, right after its next start
        #: (memory only: a restart of Iron Jarvis in between forgets it).
        self._renames: dict[str, str] = {}
        #: v1.474.0 (Hand off): each helper session's current piece of work
        #: (its turn id), and the background stops of a session's helpers.
        self.handoff_turns: dict[str, str] = {}
        self._handoff_tasks: set[asyncio.Task] = set()

    # -- helpers -----------------------------------------------------------------
    def _lock(self) -> asyncio.Lock:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    def _handover(self) -> asyncio.Lock:
        """v1.426.0 (Wave 6, E3): ONE hand-over of a conversation at a time
        (Open in Code view from a terminal, opening a saved conversation):
        its "who holds it" check and the row it makes are one step. Never
        held across a process stop or a turn (only short reads and one row)."""
        if self._handover_lock is None:
            self._handover_lock = asyncio.Lock()
        return self._handover_lock

    async def _row(self, sid: str) -> Any:
        row = await asyncio.to_thread(store.get, self.engine, sid)
        if row is None:
            raise Refused("No such Code-view session.", 404)
        return row

    def _live(self, row: Any) -> LiveSession:
        live = self.live.get(row.id)
        if live is None:
            # v1.418.0 (C4.11): the log keeps its newest events in memory too.
            live = LiveSession(row.id, row.harness, HIST.TailLog(self.home, row.id))
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
    def harnesses(self, *, recheck: str = "") -> list[dict[str, Any]]:
        """What can be started, from CACHED reads only (never a probe).
        BLOCKING (a PATH lookup): the route is a sync ``def``. v1.443.0:
        ``recheck="antigravity"`` (Connections' "Check again") starts a fresh
        background read of Antigravity's models and sign-in whatever the age
        of the kept one (never before Antigravity's own sign-in finished)."""
        if recheck == ANTIGRAVITY:
            argv_agy = binary_for(ANTIGRAVITY)
            if argv_agy is not None:
                ACC.refresh_soon(self._agy_read, self.home, argv_agy, A.default_home(), force=True)
        from ..harness import build_accounts as BA
        from ..providers.cli_auth import DEFAULT_PROBE, never_probe

        snap = BA.snapshot(getattr(self.platform, "iron_proxy", None))
        O.load_agents(self._oc_agents)  # v1.441.0: the agent modes (a small file, once)
        AGA.load(self._agy_agents)  # v1.447.0: Antigravity's own agents (a small file, once)
        argv_agy_agents = binary_for(ANTIGRAVITY)
        if argv_agy_agents is not None:
            # v1.447.0: ``agy agents`` in the background (never on this path).
            AGA.refresh_soon(self._agy_agents, self.home, argv_agy_agents, A.default_home().parent)
        out = []
        for h in HARNESSES:
            cli = W.CLI[h]
            installed = binary_for(h) is not None
            signed = None
            # Pi signs in by ITSELF, per provider (`/login`, its own auth
            # file): there is no one verdict to read, and Iron-Proxy's
            # accounts are other tools' homes. "Not known" is honest.
            if h == GROK:
                # v1.416.0: Grok's own sign-in file (one small read, never a
                # process); the token is never kept.
                try:
                    signed = G.signed_in() if installed else None
                except Exception:  # noqa: BLE001
                    signed = None
            elif h == ANTIGRAVITY:
                # v1.443.0: Antigravity keeps its sign-in in Windows'
                # credential store; its own onboarding file says whether a
                # sign-in finished, and the background model read says whether
                # it still works (hosted/antigravity_catalog).
                try:
                    signed = ACC.sign_in_state(self._agy_read, A.default_home()) if installed else None
                except Exception:  # noqa: BLE001
                    signed = None
            elif h not in OWN_SIGN_IN:
                with never_probe():
                    try:
                        signed = DEFAULT_PROBE.verdict(cli)
                    except Exception:  # noqa: BLE001
                        signed = None
            accounts = []
            if snap is not None and h == GROK:
                accounts = BA.proxy_account_rows(snap, W.PROXY[GROK])
            elif snap is not None and h not in OWN_SIGN_IN:
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
                # v1.441.0 (g1): an effort change reaches the running tool at
                # once (Claude Code's own apply_flag_settings), so the chip
                # stays open while a reply runs.
                "effort_live": h == CLAUDE and ClaudeDriver.effort_live,
                "accounts": accounts,
                "own_sign_in": h in OWN_SIGN_IN,
                # v1.416.0: a tool the page lists only when it is installed.
                "only_when_installed": h in ONLY_WHEN_INSTALLED,
                # v1.413.0: how THIS tool signs in by itself (the chip's title).
                "sign_in_hint": W.SIGN_IN_HINTS.get(h, ""),
                "commands": self.commands_for(h, disk=True),
                **(self._pi_list_state() if h == PI and installed else {}),
                **(self._agy_list_state(signed) if h == ANTIGRAVITY and installed else {}),
                # v1.443.0: Antigravity's own onboarding file — did a sign-in
                # through agy finish? (a hint for Connections; the Code view
                # reads ``signed_in``, which this never sets to False).
                **({"signin_finished": ACC.onboarded(A.default_home())} if h == ANTIGRAVITY and installed else {}),
            })
        return out

    def _pi_list_state(self) -> dict[str, Any]:
        """v1.429.0: whether Pi's model list is being read right now
        (``models_reading``: the page looks again shortly), and — when Pi
        listed NO model — the one line saying why (``models_note``)."""
        out: dict[str, Any] = {}
        if PC.reading(self._pi_catalog):
            out["models_reading"] = True
        elif not P.cached_models(self._pi_catalog) and P._read_doc(self._pi_catalog).get("fetched_at"):
            out["models_note"] = W.PI_NO_MODELS
        return out

    def _agy_list_state(self, signed: bool | None) -> dict[str, Any]:
        """v1.443.0: whether Antigravity's model list is being read right now
        (``models_reading``: the page looks again shortly), and — when it
        listed no model — the one line saying why (``models_note``)."""
        out: dict[str, Any] = {}
        if ACC.reading(self._agy_read):
            out["models_reading"] = True
        elif signed is False or ACC.onboarded(A.default_home()) is False:
            out["models_note"] = W.ANTIGRAVITY_NOT_SIGNED_IN
        elif not ACC.cached_rows(self._agy_read) and not A.cached_models(self._agy_catalog)[0]:
            said = str(ACC.read_doc(self._agy_read).get("said") or "")
            if said:
                out["models_note"] = W.antigravity_models_unread(said)
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
        elif harness == CODEX:
            # v1.424.0 (D2): each Codex model's own levels (Codex's list).
            for r in rows:
                known_codex = _codex_efforts(r.get("id") or "")
                if known_codex is not None:
                    r["efforts"] = list(known_codex)
                else:
                    r.pop("efforts", None)
        elif harness not in LISTED_MODELS:
            for r in rows:
                r.pop("efforts", None)
        return rows

    def _models(self, harness: str) -> list[dict[str, Any]]:
        rows = [{"id": "", "label": f"{W.label(harness)}'s default"}]
        if harness == PI:
            # What a Pi process last listed — v1.429.0: and, before any Pi
            # session ran, what one short background read of Pi listed
            # (hosted/pi_catalog: no model call, no session, no network).
            argv0 = binary_for(PI)
            if argv0 is not None:
                PC.refresh_soon(self._pi_catalog, self.home, argv0)
            rows += P.cached_models(self._pi_catalog)
            default = P.cached_default(self._pi_catalog)
            if default.get("label"):
                rows[0]["label"] = f"Pi's default ({default['label']})"
                rows[0]["default_name"] = default["label"]
            if isinstance(default.get("efforts"), list):
                rows[0]["efforts"] = list(default["efforts"])
        if harness in (OPENCODE, GROK, ANTIGRAVITY):
            # What an OpenCode (Grok, Antigravity) process last listed (never a
            # start just to ask); the default row takes the levels of the model
            # it starts on.
            listed, default = (O.cached_models(self._oc_catalog) if harness == OPENCODE
                               else G.cached_models(self._grok_catalog) if harness == GROK
                               else A.cached_models(self._agy_catalog))
            if harness == ANTIGRAVITY:
                # v1.443.0: and, before any Antigravity session ran, what one
                # short background ``agy models`` read listed (no session, no
                # model call; only once Antigravity's own sign-in finished).
                argv_agy = binary_for(ANTIGRAVITY)
                if argv_agy is not None:
                    ACC.refresh_soon(self._agy_read, self.home, argv_agy, A.default_home())
                listed = ACC.merged(ACC.cached_rows(self._agy_read), listed)
                default = default or ACC.cached_default(self._agy_read)
            if harness == OPENCODE:
                # v1.441.0: and, before any OpenCode session ran, what one
                # short background read of OpenCode listed (no session, no
                # model call, models.dev not fetched).
                argv0 = binary_for(OPENCODE)
                if argv0 is not None:
                    OCC.refresh_soon(self._oc_read, self.home, argv0)
                listed = O.grouped(OCC.merged(OCC.cached_rows(self._oc_read), listed))
            rows += listed
            known = next((r for r in listed if r.get("id") == default), None)
            if known is not None and isinstance(known.get("efforts"), list):
                rows[0]["efforts"] = list(known["efforts"])
        if harness == CODEX:
            # v1.424.0 (D2): the models Codex ITSELF offers the signed-in
            # account (its models_cache.json, the one cached reading every
            # model menu reads — never a start just to ask), with Codex's own
            # names, in its order. Nothing read: the default row alone, as
            # before.
            try:
                from ..providers import codex_models

                for m in codex_models.offered():
                    rows.append({"id": m["id"], "label": m["label"]})
            except Exception:  # noqa: BLE001 — the default row still works
                log.debug("codex model list unreadable", exc_info=True)
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

    def commands_for(self, harness: str, live: "LiveSession | None" = None, *,
                     disk: bool = False, folder: str = "") -> list[dict[str, str]]:
        """The harness's own `/` commands: what its running process said, else
        what it said last in this process (never a start just to ask).
        ``disk`` (v1.429.0, a caller OFF the loop): Pi's kept list may be read
        from its file when nothing is in memory yet. ``folder`` (v1.439.0):
        the session's folder (OpenCode's list is kept per folder)."""
        rows = (live.driver.commands if (live and live.driver and live.driver.commands)
                else _kept_commands(harness, folder))
        if harness == CLAUDE and folder and not (live and live.driver and live.driver.commands):
            # v1.447.0: nothing read for this folder yet (after a restart) —
            # one short background ``initialize`` of Claude Code's
            # (``hosted.claude_catalog``: no hook, no MCP server, no model
            # call); the menu shows it from the next look.
            from . import claude_catalog as CCAT

            CCAT.refresh_soon(self.home, lambda: binary_for(CLAUDE), folder)
        if harness == PI:
            # v1.429.0: Pi's own commands (done here, or said why not), then
            # the extensions, templates and skills Pi listed — a session's own
            # list, else what was kept (a background read, an earlier session).
            theirs = list(rows) if rows else (P.cached_commands(self._pi_catalog) if disk else [])
            taken = {str(c.get("name") or "").lower() for c in theirs}
            rows = [c for c in P.builtin_rows() if c["name"] not in taken] + theirs
        if not rows and harness == CODEX:
            # v1.426.0 (E4): Codex's own compact is there before its first
            # start (its skills are read when it starts).
            from .tool_setup import CODEX_COMPACT_ROW

            rows = [dict(CODEX_COMPACT_ROW)]
        # v1.439.0: Grok without its approval commands (they would change what
        # it runs without asking, outside the cards) and with /model and
        # /effort; Claude Code with a note row for each built-in that needs its
        # own terminal and is not in its own list.
        rows = T.menu_rows(harness, rows)
        if harness == OPENCODE and not any(str(c.get("name") or "").lower() == "compact" for c in rows):
            # v1.459.0: OpenCode does not LIST its compact, but the program itself
            # turns a message that is exactly "/compact" into its own
            # summarize (read from opencode 1.18.18's program and measured
            # with a stand-in model server: a summarization request, the
            # summary as the reply, ``end_turn``).
            rows = [*rows, dict(OPENCODE_COMPACT_ROW)]
        # v1.418.0 (C4.8): the `/` menu shows each command's argument hint.
        # v1.426.0 (E4): a Codex skill is typed ``$<name>`` (``prefix``).
        return [{"name": c["name"], "description": c.get("description", ""),
                 **({"argument_hint": c["argument_hint"]} if c.get("argument_hint") else {}),
                 **({"prefix": c["prefix"]} if c.get("prefix") else {}),
                 # v1.429.0: what kind of command (Pi), and why one stays in
                 # Pi's own terminal (the menu shows that line, never a gap).
                 **({"source": c["source"]} if c.get("source") else {}),
                 **({"note": c["note"]} if c.get("note") else {})} for c in rows]

    async def commands(self, sid: str) -> list[dict[str, str]]:
        row = await self._row(sid)
        return self.commands_for(row.harness, self.live.get(sid), folder=row.folder or "")

    # -- v1.415.0: the Session tab's Context drop-down ---------------------------
    async def context_view(self, row: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
        """What fills this session's conversation, in the TOOL's own numbers.

        Asked of the tool only while its process runs (never started for
        this) and, for a tool that cannot answer mid-reply, only while no
        reply runs (``Driver.context_while_working``): Claude Code's own
        ``/context`` (``get_context_usage``), Pi's ``get_session_stats``. A
        reading is kept in memory with the time it was taken; otherwise the
        total the tool said after its last reply (``panes.seen_context``).
        Codex and OpenCode say only a total. Never a model call."""
        from ..context.breakdown import clean, is_not_known_now, total_only
        from . import panes as PN

        sid = row.id
        harness = str(row.harness or "")
        label = W.label(harness)
        live = self.live.get(sid)
        driver = live.driver if live is not None and live.running else None
        reading: dict[str, Any] | None = None
        waiting = False
        unknown_now = False
        if driver is not None:
            if live.busy and not getattr(driver, "context_while_working", False):
                waiting = True
            else:
                try:
                    got = await asyncio.wait_for(driver.context_breakdown(), timeout=CONTEXT_READ_S)
                except Exception:  # noqa: BLE001 — no reading, never an error
                    got = None
                if is_not_known_now(got):
                    # v1.415.0 review: the tool ANSWERED that it does not
                    # know right now (Pi just compacted): every older reading
                    # — kept or seen — is out of date.
                    self._context_seen.pop(sid, None)
                    unknown_now = True
                got = clean(got)
                if got is not None:
                    got["at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    self._context_seen[sid] = got
                    reading = got
        seen = None if unknown_now else PN.seen_context(events)
        kept = self._context_seen.get(sid)
        if reading is None and kept is not None:
            # A kept reading is shown only while no newer total says otherwise.
            if seen is None or _iso_after(kept.get("at"), seen.get("at")):
                reading = kept
        if reading is None and seen is not None:
            reading = total_only(seen["used"], seen["window"], at=seen.get("at"))
        out: dict[str, Any] = {"harness": harness, "reading": reading, "waiting": waiting}
        if reading is not None and not reading.get("parts"):
            out["note"] = f"{label} says only how full the conversation is, not what fills it."
        if waiting:
            out["waiting_note"] = f"{label} is asked again when this reply ends."
        if harness in COMPACTS_BY_MESSAGE:
            # v1.426.0 (E4): Codex's own compact too — "/compact" as a message,
            # which its driver turns into ``thread/compact/start`` (measured).
            # v1.459.0: Pi (the Code view runs Pi's RPC ``compact`` for the
            # typed command, ``PiDriver.run_builtin``), OpenCode (the program itself
            # summarizes on "/compact", measured) and Grok (it lists
            # ``compact`` in its own commands) — the same press for each.
            out["compact"] = {"how": "message", "text": "/compact"}
        else:
            out["compact"] = {"how": "", "why": f"{label} compacts by itself when the conversation is full."}
        return out

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
            raise Refused('Choose "claude-code", "codex", "pi", "opencode", "grok" or "antigravity".', 400,
                          field="tool")
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
        if harness == ANTIGRAVITY:
            await asyncio.to_thread(AGA.load, self._agy_agents)
        try:
            mode = check_mode(harness, body.get("mode") or default_mode(harness), bool(body.get("confirm")))
        except Refused as r:
            r.field = r.field or "mode"
            raise
        if harness == ANTIGRAVITY and resume_id and AGA.agent_of(mode):
            # v1.447.0: a saved conversation keeps the agent it was opened
            # with (Antigravity's own record; measured) — another is refused.
            kept = await asyncio.to_thread(A.conversation_agent, A.default_home(), resume_id)
            if kept is not None and kept != AGA.agent_of(mode):
                raise Refused(AGA.AGENT_KEPT.format(
                    current=f"“{kept}”" if kept else AGA.DEFAULT_AGENT_WORDS,
                    wanted=AGA.agent_words(mode)), 400, field="mode")
        try:
            model = check_model(body.get("model"))
        except Refused as r:
            r.field = r.field or "model"
            raise
        pi_rows = await asyncio.to_thread(self._models, harness) if harness in LISTED_MODELS else None
        effort = check_effort(harness, body.get("effort"), model, pi_rows)
        title = clip(body.get("title") or "", 120)
        account = str(body.get("account") or "").strip()
        if account and harness in OWN_SIGN_IN:
            raise Refused(W.PI_OWN_SIGN_IN if harness == PI
                          else W.ANTIGRAVITY_OWN_SIGN_IN if harness == ANTIGRAVITY
                          else W.OPENCODE_OWN_SIGN_IN, 400, field="account")
        try:
            acc = await asyncio.to_thread(self._resolve_account, harness, account)
        except Refused as r:
            if r.status == 400:
                r.field = r.field or "account"
            raise
        # v1.426.0 (Wave 6, E3): opening a SAVED conversation is a hand-over:
        # the "who holds it" check and the new row are one step (two presses
        # made two rows on one conversation).
        async with (self._handover() if resume_id else contextlib.AsyncExitStack()):
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
        if resume_id:  # v1.418.0 (C4.6): its history is the tool's own transcript
            await asyncio.to_thread(HIST.mark_outside, self.home, row.id, "disk")
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
        raise Refused("That account is not one of your accounts for this tool.", 404)

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
        view["background"] = live.background_tasks if live and live.running else 0
        # v1.417.0: of those, the helper agents (the words say "helpers" only
        # for these), and whether the tool is writing a reply of its own —
        # the Code view reads the session again while either holds.
        view["background_helpers"] = live.background_helpers if live and live.running else 0
        view["self_reply"] = bool(live and live.replying_alone(self.clock()))
        # v1.422.0 (B4): the programs the reaper keeps this session open for
        # (masked command + ports, never a pid) with the line that says so and
        # how to stop it; and the words of a stop that ended programs (until
        # the next start says it).
        kept = live.keeping.programs if live and live.running and live.keeping is not None else []
        view["programs"] = [p.row() for p in kept]
        view["keep_note"] = PG.keep_words(W.label(view.get("harness") or ""), kept) if kept else ""
        view["stopped_note"] = live.stopped_note if live and not live.running else ""
        # v1.441.0: the row's "Branch" (the tool can, and there is a
        # conversation to branch).
        view["can_branch"] = bool(view.get("source") == "app" and BR.caps(view.get("harness") or "")["fork"]
                                  and view.get("harness_session_id") and not view.get("archived_at"))
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
        # v1.417.0: the live flags (helpers, a reply of the tool's own) are
        # taken BEFORE the events are read: a "nothing is happening" answer
        # then always comes with every event that work wrote, so the Code
        # view's last read never misses the end of the report. (v1.418.0's
        # memory view below replaced the file read that followed this line.)
        session = self._with_live(store.view(row))
        live = self.live.get(sid)
        if live is not None:
            # v1.422.0 (C4.7): the Code view shows this session — the reaper
            # gives the session in front 60 minutes, not 15 (:mod:`.programs`).
            PG.note_looked(live, self.clock())
        # v1.418.0 (C4.11): from memory when this daemon drove the session
        # (the file is read once); else the file.
        got = await self._view_events(sid, live, max(1, min(int(limit or DEFAULT_EVENTS), 20_000)))
        running = live.turn.id if live and live.turn and live.turn.running else None
        # v1.418.0 (C4.6): the coding tool's own transcript, for a conversation
        # that lived outside this view (opened from disk, or a terminal).
        events = await self._with_transcript(row, live, got["events"], cut=bool(got["older"]))
        dropped = got["dropped"]
        out: dict[str, Any] = {
            "session": self._with_root(session),
            "events": events,
            "events_total": got["events_total"] + (len(events) - len(got["events"])),
            "dropped": dropped,
            # The running turn's id (rejoin with GET …/turns/{id}/stream — its
            # replay starts with the turn's `user` event), else null.
            "running": running,
            "turn_id": running,
            "pending": [a.event for a in (live.asks.values() if live else [])],
        }
        out["commands"] = self.commands_for(row.harness, live, folder=row.folder or "")
        # v1.441.0: Branch from here / Rewind to here — what this tool can do,
        # and the messages it can do it from.
        try:
            out["branch"] = await asyncio.to_thread(BR.view_block, self.home, row)
        except Exception:  # noqa: BLE001 — a view never fails over this
            log.warning("could not read a Code-view session's branch facts", exc_info=True)
        if row.harness in LISTED_MODELS:
            out["keeps_last"] = await asyncio.to_thread(pi_keeps_last, row)
        if row.harness == PI:
            # v1.431.0: the folder's own Pi resources Pi skips here (it can
            # only ask about them in its terminal) — said, with one press.
            folder = await asyncio.to_thread(self._pi_folder_view, row)
            if folder is not None:
                out["pi_folder"] = folder
        # v1.453.0: the summary kept from an archive (one stat; absent when none).
        facts = await asyncio.to_thread(AS.summary_facts, self.home, row.id)
        if facts is not None:
            out["archive_summary"] = facts
        # v1.474.0 (Hand off): the helpers this session's tool started, and
        # for a helper, the session that started it. Absent when none.
        try:
            helpers = await HO.helpers_view(self, row.id)
            if helpers:
                out["helpers"] = helpers
            parent = str(getattr(row, "handed_from", "") or "")
            if parent:
                prow = await asyncio.to_thread(store.get, self.engine, parent)
                out["handed_from"] = {"id": parent, "title": (prow.title if prow is not None else ""),
                                      "label": W.label(prow.harness) if prow is not None else ""}
        except Exception:  # noqa: BLE001 — a view never fails over this
            log.warning("could not read a Code-view session's helpers", exc_info=True)
        # v1.418.0 (C4.6): "Load earlier" reads the page before this cursor.
        out["older"] = got["older"]
        # v1.418.0 (C4.8): the messages waiting behind the reply.
        out.update(await self._queue_view(sid, running=bool(running)))
        # v1.422.0 (B3, B5): a reply a restart cut off (the Continue press)
        # and a limit's wait (the card: until when, with Cancel).
        from . import carry_on as CO

        try:
            out.update(await asyncio.to_thread(CO.view_facts, self.home, sid, row, events, running=bool(running)))
        except Exception:  # noqa: BLE001 — a view never fails over these
            log.warning("could not read a Code-view session's carry-on facts", exc_info=True)
        return out

    # -- v1.418.0 (C4.6, C4.11): history that stays --------------------------------
    async def _view_events(self, sid: str, live: "LiveSession | None", limit: int) -> dict[str, Any]:
        """The newest ``limit`` events: from the live log's memory (seeded by
        ONE file read), else the file."""
        log_ = live.log if live is not None else None
        if isinstance(log_, HIST.TailLog):
            got = log_.served(limit)
            if got is None and not log_.seeded:
                await asyncio.to_thread(log_.seed)
                got = log_.served(limit)
            if got is not None:
                return got
            await asyncio.to_thread(log_.flush)
        d = session_dir(self.home, sid)
        return await asyncio.to_thread(HIST.file_view, d / "events.jsonl", d / "meta.json", limit)

    async def _with_transcript(self, row: Any, live: "LiveSession | None", events: list[dict[str, Any]],
                               *, cut: bool) -> list[dict[str, Any]]:
        """The tool's own transcript merged in by time — only for a session
        whose conversation lived outside this view (nothing in its log yet,
        or opened from disk / a terminal: :func:`.history.mark_outside`).
        Never fails a view."""
        hid = str(row.harness_session_id or "")
        if not hid or not typable_sid(row.harness, hid):
            return events
        if events and not await asyncio.to_thread(HIST.went_outside, self.home, row.id):
            return events
        running = bool(live and live.turn and live.turn.running)
        home = home_of(row.harness, store.account_record(row))
        imported = await asyncio.to_thread(
            HIST.transcript_events, row.harness, home, row.folder or "", hid, allow_stale=running,
        )
        if imported:
            # v1.441.0 review: what a rewind dropped is still in the tool's own
            # file; nothing from before the rewind is merged in again.
            imported = await asyncio.to_thread(BR.without_rewound, self.home, row.id, imported)
        return HIST.merge_transcript(events, imported, cut=cut) if imported else events

    async def events_page(self, sid: str, before: str, limit: int = 500) -> dict[str, Any]:
        """``GET …/events?before=``: the events older than a view's (or a
        page's) ``older`` cursor — the file, read when the user asks."""
        await self._row(sid)
        live = self.live.get(sid)
        if live is not None:
            await asyncio.to_thread(live.log.flush)
        d = session_dir(self.home, sid)
        try:
            return await asyncio.to_thread(HIST.read_page, d / "events.jsonl", d / "meta.json", before, limit)
        except HIST.BadCursor:
            raise Refused(W.BAD_CURSOR, 400) from None

    async def item_text(self, sid: str, item: str) -> dict[str, Any]:
        """``GET …/events/{item}``: the whole text the log keeps for one item
        (``EventLog.item_text`` when the log has it, else the record's own)."""
        await self._row(sid)
        live = self.live.get(sid)
        log_ = live.log if live is not None else EventLog(self.home, sid)
        own = getattr(log_, "item_text", None)
        got: Any = None
        if callable(own):
            await asyncio.to_thread(log_.flush)
            got = await asyncio.to_thread(own, item)
            if isinstance(got, str):
                got = {"item": item, "text": got}
        if not isinstance(got, dict):
            await asyncio.to_thread(log_.flush)
            got = await asyncio.to_thread(HIST.item_text, log_.path, item)
        if not got:
            raise Refused(W.NO_SUCH_ITEM, 404)
        return got

    # -- v1.418.0 (C4.8): messages that wait behind the reply -----------------------
    async def _queue_of(self, sid: str) -> Q.SessionQueue:
        live = self.live.get(sid)
        if live is not None and live.queue is not None:
            return live.queue
        saved = await asyncio.to_thread(Q.read, self.home, sid)
        # Review (v1.418.0): a queue read from disk was left by an EARLIER
        # run of Iron Jarvis (this run keeps its own on the live session) —
        # the reply it waited for is gone, so it is HELD: back to the box,
        # never sent behind the next reply (a shutdown returns before it
        # could write the hold).
        if saved.get("items"):
            saved = {**saved, "held": True}
        live = self.live.get(sid)
        if live is not None:
            if live.queue is None:
                live.queue = Q.SessionQueue(self.home, sid, saved)
            return live.queue
        return Q.SessionQueue(self.home, sid, saved)

    async def _queue_view(self, sid: str, *, running: bool) -> dict[str, Any]:
        q = await self._queue_of(sid)
        # The queue's OWN hold (a Stop, a failure, a limit card, or a queue
        # an earlier run left — held when it is read back). Review (v1.418.0):
        # never "nothing is running" — between a clean end and the next
        # waiting message's turn starting nothing runs, and the page would
        # pull a message the daemon is about to send back into the box.
        return q.view()

    def _persist_queue(self, q: Q.SessionQueue) -> None:
        from ..harness.stall import keep

        version, data = q.snapshot()
        with contextlib.suppress(RuntimeError):
            keep(asyncio.ensure_future(asyncio.to_thread(q.write, version, data)))

    async def queue_add(self, sid: str, text: str, files: list[str] | None = None) -> dict[str, Any]:
        """A message typed while a reply runs WAITS (sent when the reply ends
        cleanly). Refused with ``code: not_working`` when no reply runs (the
        page sends it as a message instead)."""
        row = await self._row(sid)
        label = W.label(row.harness)
        text = str(text or "")
        files = [str(f) for f in (files or []) if str(f).strip()]
        if not text.strip() and not files:
            raise Refused("Type a message first.", 400)
        problems = await asyncio.to_thread(self._file_problems, files, row.folder or "")
        if problems:
            raise Refused(problems, 400)
        q = await self._queue_of(sid)
        live = self.live.get(sid)  # read AFTER the awaits: no await from here on
        if live is None or live.turn is None or not live.turn.running or live.turn.ending:
            raise Refused(W.QUEUE_NOT_WORKING.format(label=label), 409, code="not_working")
        if live.queue is None:
            live.queue = q
        q = live.queue
        if len(q.items) >= Q.MAX_QUEUED:
            raise Refused(W.QUEUE_FULL.format(n=len(q.items), label=label))
        item = q.add(text, files)
        self._persist_queue(q)
        return {"ok": True, "id": item["id"], **(await self._queue_view(sid, running=True))}

    async def queue_remove(self, sid: str, qid: str) -> dict[str, Any]:
        await self._row(sid)
        q = await self._queue_of(sid)
        if not q.remove(str(qid or "")):
            raise Refused("That message is not waiting any more (it was sent or removed).", 404)
        self._persist_queue(q)
        live = self.live.get(sid)
        return await self._queue_view(sid, running=bool(live and live.turn and live.turn.running))

    async def queue_take(self, sid: str) -> dict[str, Any]:
        """Every waiting message, taken out (the page puts them in the box)."""
        await self._row(sid)
        q = await self._queue_of(sid)
        taken = q.clear()
        self._persist_queue(q)
        return {"ok": True, "taken": taken, "queued": [], "queue_held": False}

    def _after_turn_queue(self, live: "LiveSession", turn: HostedTurn, done: dict[str, Any]) -> None:
        """A reply ended: a clean end sends the first waiting message; any
        other end (Stop, a failure, an account's limit card) HOLDS the queue
        — its words go back to the box, never sent behind the user's back."""
        q = live.queue
        if q is None or not q.items or self._closing:
            return
        clean = str(done.get("stop") or "") == "completed" and not turn.account_limit
        if not clean:
            q.hold()
            self._persist_queue(q)
            return
        from ..harness.stall import keep

        with contextlib.suppress(RuntimeError):
            keep(asyncio.ensure_future(self._send_queued(live.sid)))

    async def _send_queued(self, sid: str) -> None:
        live = self.live.get(sid)
        q = live.queue if live is not None else None
        if q is None or q.held:
            return
        item = q.pop_first()
        if item is None:
            return
        self._persist_queue(q)
        try:
            await self.start_turn(sid, item["text"], "", item["files"])
        except Refused as r:
            q.put_back(item)
            q.hold()
            self._persist_queue(q)
            if live is not None:
                self._emit(live, make("notice", text=W.QUEUE_NOT_SENT.format(why=r.sentence)))
        except Exception:  # noqa: BLE001 — a waiting message is never lost
            log.warning("a waiting Code-view message could not be sent", exc_info=True)
            q.put_back(item)
            q.hold()
            self._persist_queue(q)

    async def patch(self, sid: str, body: dict[str, Any]) -> dict[str, Any]:
        row = await self._row(sid)
        before_model, before_effort = row.model or "", row.effort or ""
        fields: dict[str, Any] = {}
        applied: dict[str, str] = {}
        live = self.live.get(sid)
        running_turn = bool(live and live.turn and live.turn.running)
        tell_title = ""
        if "title" in body and body["title"] is not None:
            fields["title"] = clip(body["title"], 120)
            if fields["title"].strip() and fields["title"] != (row.title or ""):
                # v1.439.0: the name reaches the tool too (Claude Code's
                # rename_session, Codex's thread/name/set), in the
                # background, never failing or delaying the rename — and
                # (review) only once the rename is SAVED: a PATCH a later
                # field refuses (Full access with no confirm, a mode the
                # tool refused) renames nothing, the tool included.
                tell_title = fields["title"].strip()
        if body.get("mode") is not None:
            if row.harness == ANTIGRAVITY:
                await asyncio.to_thread(AGA.load, self._agy_agents)
            mode = check_mode(row.harness, body["mode"], bool(body.get("confirm")))
            if row.harness == ANTIGRAVITY:
                # v1.447.0: a conversation keeps the agent it was opened with
                # (measured) — a change after that is refused in words.
                problem = AGA.agent_change_problem(row.mode, mode, row.harness_session_id)
                if problem:
                    raise Refused(problem)
            if live and live.running and live.driver:
                try:
                    applied["mode"] = await live.driver.set_mode(mode)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
                self._refresh_asks(live)
                if getattr(live.driver, "restart_needed", False):
                    # v1.413.0: OpenCode's permission rules are its process's
                    # own environment: a new process takes them, before the
                    # next message (the conversation is reopened).
                    live.restart = True
            else:
                applied["mode"] = "next"
            if row.harness == CODEX and running_turn and live is not None:
                self._emit(live, make("notice", text=W.CODEX_MODE_NEXT.format(mode=MODE_LABELS[mode])))
            elif row.harness in (OPENCODE, GROK, ANTIGRAVITY) and running_turn and live is not None \
                    and applied["mode"] == "next":
                self._emit(live, make("notice", text=W.MODE_NEXT.format(
                    label=W.label(row.harness), mode=mode_label(row.harness, mode))))
            elif applied["mode"] == "now" and live is not None and live.asks and mode != row.mode:
                # v1.412.0: the mode changed while the tool waited on a card —
                # that card was asked under the old mode and still needs YOU.
                self._emit(live, make("notice", text=W.MODE_CARD_STILL_OPEN.format(
                    label=W.label(row.harness), mode=mode_label(row.harness, mode))))
            fields["mode"] = mode
        next_model = check_model(body["model"]) if body.get("model") is not None else row.model
        pi_rows = await asyncio.to_thread(self._models, row.harness) if row.harness in LISTED_MODELS else None
        if row.harness in LISTED_MODELS and (
            (body.get("model") is not None and not check_model(body["model"]) and row.model)
            or (body.get("effort") is not None
                and not check_effort(row.harness, body["effort"], next_model, pi_rows) and row.effort)
        ) and await asyncio.to_thread(pi_keeps_last, row):
            # Back to "Pi's default" would change nothing: reopening a
            # conversation with messages, Pi restores its LAST model and
            # thinking level (core/sdk.js) — said, never pretended.
            raise Refused(W.PI_KEEPS_LAST if row.harness == PI
                          else W.GROK_KEEPS_LAST if row.harness == GROK else W.OPENCODE_KEEPS_LAST)
        if body.get("model") is not None:
            model = check_model(body["model"])
            if live and live.running and live.driver and model:
                try:
                    applied["model"] = await live.driver.set_model(model)
                except ControlRefused as exc:
                    raise Refused(str(exc)) from None
            else:
                applied["model"] = "next"
                if live and live.running and row.harness in (CLAUDE, PI, OPENCODE, GROK, ANTIGRAVITY):
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
            if ((row.harness == CLAUDE and applied["effort"] != "now")
                    or (row.harness in LISTED_MODELS and not effort)) and live and live.running:
                # --effort is read at start only (v1.441.0: unless Claude Code
                # took it live — ClaudeDriver.set_effort reads it back); Pi's
                # own default (no --thinking) applies only to a process
                # started without one.
                live.restart = True
            fields["effort"] = effort
        if isinstance(body.get("capabilities"), dict):
            # v1.414.0: the session's Capabilities, a MERGE (the pane's own
            # rule: a box not named keeps its value). Read LIVE by the
            # session's Iron Jarvis token, so a box turned OFF takes effect on
            # the tool's next call; a change of whether the connection is
            # needed at all (the first box on, the last one off) reaches the
            # tool with a new process before the next message.
            before = JT.load(getattr(row, "capabilities_json", "") or "")
            if (getattr(row, "handed_from", "") or "") and                     JT.normalise({HO.HANDOFF: body["capabilities"].get(HO.HANDOFF)})[HO.HANDOFF]:
                # v1.474.0: a helper can never hand off again (no chain).
                raise Refused(HO.HELPER_CANNOT_BOX, 400)
            merged = dict(before)
            for key, value in body["capabilities"].items():
                if key in merged:
                    merged[key] = JT.normalise({key: value})[key]
            if merged != before:
                fields["capabilities_json"] = json.dumps(merged, sort_keys=True)
                # v1.441.0 (review A5): a tool that takes no Iron Jarvis
                # tools (Antigravity 1.5.2) is never restarted for a box.
                needs = JT.takes(row.harness) and JT.wanted(merged) != JT.wanted(before)
                if live is not None:
                    live.caps = merged
                    if live.running and needs:
                        live.restart = True
                applied["capabilities"] = "next" if live is not None and live.running and needs else "now"
        if body.get("load_folder_resources") is not None:
            # v1.431.0: the ONE way a Pi session loads its folder's own
            # extensions, skills and settings — the user's press. Pi reads them
            # at start only, so a running Pi starts again (same conversation)
            # before the next message.
            if row.harness != PI:
                raise Refused(W.PI_FOLDER_NOT_PI)
            load = bool(body["load_folder_resources"])
            got = await asyncio.to_thread(PT.check, row.folder, fresh=True)
            if load:
                problem = {PT.NONE: W.PI_FOLDER_NOTHING, PT.TRUSTED: W.PI_FOLDER_ALREADY,
                           PT.DECLINED: W.PI_FOLDER_DECLINED, PT.UNKNOWN: W.PI_FOLDER_CANNOT_TELL}.get(got["state"])
                if problem:
                    raise Refused(problem)
            if load != bool(getattr(row, "load_folder_resources", False)):
                fields["load_folder_resources"] = load
                things = PT.found_words(got["found"])
                if live is not None:
                    if live.running:
                        live.restart = True
                    self._emit(live, make("notice", text=(W.PI_FOLDER_LOAD_NOW if load
                                                          else W.PI_FOLDER_SKIP_NOW).format(things=things)))
            applied["load_folder_resources"] = "next"
        if fields:
            row = await asyncio.to_thread(store.update, self.engine, sid, **fields)
            await self._say_model_change(row, before_model, before_effort, applied)
        if tell_title and live is not None and live.running and live.driver is not None:
            self._renames.pop(sid, None)
            self._tell_rename(live.driver, tell_title)
        elif tell_title:
            # v1.441.0 (review P4): the tool is not running — it hears the
            # name once it starts again (``_start_process``), never lost.
            self._renames[sid] = tell_title
        out: dict[str, Any] = {"session": self._with_live(store.view(row)), "applied": applied}
        if row.harness in LISTED_MODELS:
            out["keeps_last"] = await asyncio.to_thread(pi_keeps_last, row)
        if row.harness == PI:
            out["pi_folder"] = await asyncio.to_thread(self._pi_folder_view, row)
        return out

    async def _say_model_change(self, row: Any, before_model: str, before_effort: str,
                                applied: dict[str, str]) -> None:
        """v1.475.0: a model or effort change in a conversation leaves ONE
        quiet line in it ("Model changed: Sonnet 5.5 → Opus 5.5, from your
        next message"), in the session's own log so every window and a
        reload show it. Nothing before the conversation's first message.
        Never raises."""
        try:
            model, effort = row.model or "", row.effort or ""
            if (model, effort) == (before_model, before_effort):
                return
            live = self.live.get(row.id)
            if not (row.harness_session_id or (live is not None and live.turn is not None)):
                return
            rows = await asyncio.to_thread(self._models, row.harness)
            text = W.model_change_words(row.harness, rows, before_model, model, before_effort, effort,
                                        applied.get("model") or applied.get("effort") or "")
            if text:
                self._emit(self._live(row), make("notice", text=text))
        except Exception:  # noqa: BLE001 — a change is never refused over its line
            log.debug("could not say a Code session's model change", exc_info=True)

    def _tell_rename(self, driver: Driver, title: str) -> None:
        """v1.439.0: best effort, in the background (``Driver.rename``)."""
        from ..harness.stall import keep

        async def tell() -> None:
            try:
                await driver.rename(title)
            except Exception:  # noqa: BLE001 — the tool's own name is a nicety
                log.debug("could not tell the coding tool its new name", exc_info=True)

        keep(asyncio.ensure_future(tell()))

    def _pi_folder_view(self, row: Any) -> dict[str, Any] | None:
        """BLOCKING. The view's ``pi_folder`` key (``hosted/pi_trust``), or None."""
        try:
            return PT.view(row.folder or "", bool(getattr(row, "load_folder_resources", False)))
        except Exception:  # noqa: BLE001 — a view never fails over this
            log.warning("could not read a Pi session folder's own resources", exc_info=True)
            return None

    async def archive(self, sid: str, archived: bool = True) -> dict[str, Any]:
        row = await self._row(sid)
        live = self._live(row)
        if not archived:
            # v1.453.0: the summary written at archive time stays with it.
            row = await asyncio.to_thread(store.update, self.engine, sid, archived_at=None)
            return self._with_live(store.view(row))
        # v1.453.0: never while work runs in the background — the stop below
        # would end it (Claude Code's helpers and background commands, Pi's
        # run of its own, a reply the tool writes by itself).
        now = self.clock()
        if live.running and (live.keeps_working(now) or live.working_alone or live.replying_alone(now)):
            raise Refused(W.ARCHIVE_BACKGROUND.format(label=W.label(row.harness)))
        self._claim(live, "being archived")
        HO.stop_helpers_soon(self, sid)  # v1.474.0: its helpers stop with it
        summary: dict[str, Any] | None = None
        summary_error = ""
        try:
            await self._stop_process(live, why="archived")
            # v1.453.0: the short Markdown summary (``hosted/archive_summary``),
            # from the session's own log and git — never a model call. A
            # summary that cannot be written never stops the archive; it is said.
            try:
                summary = await self._write_archive_summary(row, live)
            except AS.SummaryNotWritten as exc:
                summary_error = exc.words
            except Exception:  # noqa: BLE001 — the archive itself must still land
                log.warning("could not write a Code-view session's archive summary", exc_info=True)
                summary_error = W.ARCHIVE_SUMMARY_FAILED
            # v1.412.0 review: an archived session KEEPS its own folder,
            # empty or not — Claude Code and Pi keep a conversation by
            # the folder it ran in, so unarchiving must find it at the
            # same path (only a real delete may remove an empty one).
            row = await asyncio.to_thread(store.update, self.engine, sid, archived_at=utcnow())
        finally:
            live.claim = ""
        out = self._with_live(store.view(row))
        if summary is not None:
            out["archive_summary"] = {"path": summary["path"], "at": summary["at"]}
        if summary_error:
            out["archive_summary_error"] = summary_error
        return out

    async def _write_archive_summary(self, row: Any, live: LiveSession) -> dict[str, Any]:
        """v1.453.0: every event the log keeps (flushed first), the tool's own
        transcript for a conversation that lived outside this view, then the
        summary written beside the log."""
        events, _total, dropped = await asyncio.to_thread(live.log.read, EV.MAX_LINES)
        events = await self._with_transcript(row, live, events, cut=False)
        return await asyncio.to_thread(AS.write_summary, self.home, row, events, dropped=dropped,
                                       engine=self.engine)

    async def archive_summary(self, sid: str) -> dict[str, Any]:
        """v1.453.0: the kept summary ``{text, path, at}`` (404 when none)."""
        row = await self._row(sid)
        got = await asyncio.to_thread(AS.read_summary, self.home, row.id)
        if got is None:
            raise Refused(W.NO_ARCHIVE_SUMMARY, 404)
        return got

    async def delete(self, sid: str) -> dict[str, Any]:
        row = await self._row(sid)
        live = self._live(row)
        self._claim(live, "being deleted")
        HO.stop_helpers_soon(self, sid)  # v1.474.0: its helpers stop with it
        try:
            await self._stop_process(live, why="deleted")
            self.live.pop(sid, None)
            await asyncio.to_thread(live.log.close)
            await asyncio.to_thread(store.delete, self.engine, sid)
            await asyncio.to_thread(self._drop_own_folder, row)
            moved = await asyncio.to_thread(self._trash_events, sid)
            # v1.425.0 (C3): the session's kept index (a cache of its folder,
            # ``panes.kept_index``) goes with it.
            from . import panes as PN

            await asyncio.to_thread(PN.forget_kept_index, self.home, sid)
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
        if live.running and live.restart and live.keeps_working(self.clock()):
            # v1.417.0 review: a restart is a tree kill — it would stop every
            # helper still working in the background. It waits (``restart``
            # stays set) and this message runs on the process as it is.
            self._emit(live, make("notice", text=W.RESTART_WAITS.format(
                label=W.label(row.harness), tasks=W.background_count(live.background_tasks))))
            return
        if live.running and live.restart:
            # v1.422.0 (B4): what the old process ran that serves (a dev
            # server) stops with it — read first, said after (never silent).
            ended = PG.known_programs((await PG.read_many([live])).get(live.sid), live)
            # v1.413.0: the old process goes WITHOUT ending the turn that is
            # starting now (it is already ``live.turn``) — this stop used to
            # end that very turn "interrupted" before it began, so a Claude
            # Code effort change or a model back to the default never reached
            # the next message (found by the OpenCode mode-change pin).
            await self._stop_process(live, end_turn=False)
            if ended:
                self._said_stopped(live, ended, why="restart")
                live.stopped_programs, live.stopped_note = [], ""  # said now, in this very reply
        live.restart = False
        argv0 = await asyncio.to_thread(binary_for, row.harness)  # a PATH walk: off the loop
        if argv0 is None:
            raise Refused(W.NOT_INSTALLED.format(label=W.label(row.harness)))
        if row.folder and not await asyncio.to_thread(os.path.isdir, row.folder):
            # v1.412.0 review: Windows says "The directory name is invalid"
            # (WinError 267) and that read as the tool's fault.
            raise Refused(W.FOLDER_MISSING.format(folder=row.folder))
        # v1.425.0 (Wave 5, B7): the pool's lock is held only to DECIDE — the
        # slot is reserved for this session (``live.starting``) and, when the
        # pool is full, its idle victim chosen — never while a tool starts
        # (up to a minute for a cold Claude Code: every other session's
        # message used to wait behind it).
        victim, readings = await self._reserve_slot(live, row)
        try:
            if victim is not None:
                # Right after the decision, with no wait between: the victim
                # stops counting the moment its stop begins (``live.proc`` is
                # cleared before the stop's first wait).
                await self._stop_process(victim)
                victim.stopped_why = "evicted"  # v1.417.0 (C4.7)
                self._said_stopped(victim, PG.known_programs(readings.get(victim.sid), victim), why="evicted")
            await self._start_process(live, row, argv0)
        finally:
            live.starting, live.starting_conv = False, ""
            self._room_changed()
        self._ensure_reaper()

    def _holding_places(self, live: LiveSession) -> list[LiveSession]:
        """v1.425.0 (B7): the OTHER sessions holding a place in the pool — a
        process, or a start in flight (``starting``)."""
        return [lv for lv in self.live.values() if lv is not live and (lv.running or lv.starting)]

    def _room_event(self) -> asyncio.Event:
        """v1.425.0 (B7): the event the NEXT change of the pool sets. A waiter
        takes it BEFORE it looks, so a change after its look always wakes it
        (an event is never cleared: each change sets the current one and
        leaves a fresh one for later waiters)."""
        if self._room is None:
            self._room = asyncio.Event()
        return self._room

    def _room_changed(self) -> None:
        """v1.425.0 (B7): a slot may have come free (a reply ended, a process
        stopped, a start finished) — a message waiting for room looks again."""
        if self._room is not None:
            self._room.set()
            self._room = asyncio.Event()

    async def _reserve_slot(self, live: LiveSession, row: Any) -> tuple[Any, dict[str, Any]]:
        """v1.425.0 (Wave 5, B7): a place in the pool for this session's
        process — ``(victim, readings)``: an idle session to stop first (the
        pool's own order, ``programs.evict_pick``), or None. Decided under the
        pool's lock, which is never held while a tool starts; a session
        STARTING holds its slot (``live.starting``) and is never a victim.

        THE SEVENTH SESSION MAKES ROOM: with every place taken it stops the
        least recently used IDLE session, as the pool always did — never one
        that works, keeps background work, or (since this change) is still
        starting; one that serves a program or that the user looks at goes
        last. When NONE can stop (all six are working), the message WAITS for
        one to finish — said once — instead of being refused at once, and is
        refused only after :data:`POOL_WAIT_S` (the old words). A Stop ends
        the wait (the turn's end wakes it) and nothing is started."""
        waited = False
        deadline = time.monotonic() + POOL_WAIT_S
        turn = live.turn  # the message this start is for (``_start_turn_in`` set it)
        while True:
            if turn is not None and not turn.running:
                raise _StoppedWhileWaiting()
            room = self._room_event()  # taken before the look (never cleared)
            async with self._lock():
                # Another row's process on this very conversation (a race the
                # turn's own check could miss) — running, or still starting:
                # never a second process.
                conv = row.harness_session_id or ""
                for other in self.live.values():
                    if other is live or not conv:
                        continue
                    if (other.running and other.driver is not None and other.driver.session_id == conv) \
                            or (other.starting and other.starting_conv == conv):
                        raise Refused(W.HELD_BY_ROW)
                others = self._holding_places(live)
                victim, readings = None, {}
                if len(others) >= MAX_LIVE:
                    now = self.clock()
                    # v1.417.0 review: least recently HEARD first, like the
                    # reaper (a reply the tool started by itself owns no turn).
                    idle = [lv for lv in others if _stoppable(lv, now)]
                    if idle:
                        # v1.422.0 (B4): a session running a program it
                        # started that serves (a dev server) is picked LAST,
                        # one the user looks at next-to-last — and when one
                        # must go, what it ran is SAID.
                        readings = await PG.read_many(idle)
                        # v1.425.0: one that got a message (or stopped) while
                        # its programs were read is no victim any more — and
                        # a place that came free meanwhile needs none.
                        others = self._holding_places(live)
                        idle = [lv for lv in idle if _stoppable(lv, now)]
                    if idle and len(others) >= MAX_LIVE:
                        victim = PG.evict_pick(idle, readings, now,
                                               key=lambda lv: max(lv.last_active, lv.last_heard))
                if len(others) < MAX_LIVE or victim is not None:
                    live.starting, live.starting_conv = True, conv
                    return victim, readings
            if not waited:
                waited = True
                self._emit(live, make("notice", text=W.POOL_WAITING))
            left = deadline - time.monotonic()
            if left <= 0:
                raise Refused(W.POOL_FULL)
            # Woken when a reply ends — ANY reply, so THIS one too when Stop
            # ends it (``interrupt`` ends a turn that has no tool yet) — or a
            # process stops; looked at again every few seconds anyway (a
            # tool's background work ending says nothing).
            try:
                await asyncio.wait_for(room.wait(), min(left, POOL_RECHECK_S))
            except asyncio.TimeoutError:
                pass
            if turn is not None and not turn.running:
                # Stopped while it waited: never started afterwards.
                raise _StoppedWhileWaiting()

    async def _start_process(self, live: LiveSession, row: Any, argv0: list[str]) -> None:
        from ..terminals import pane_accounts as PA

        loop = asyncio.get_running_loop()
        acc = store.account_record(row)
        accounts = PA.from_recorded({W.PROXY[row.harness]: acc})[0] if acc and row.harness in W.PROXY else None
        # v1.424.0 (D4): on a Claude account that is not this PC's own login the
        # environment also carries the owner's setup (`iron_proxy.claude_setup`),
        # which reads and writes small files — off the loop. No account = the
        # same synchronous build as before.
        env = await asyncio.to_thread(build_env, accounts) if accounts else build_env(accounts)
        if row.harness == PI:
            env = P.child_env(env)  # a parent Pi session's markers never cross
        if row.harness == ANTIGRAVITY:
            # v1.443.0: Antigravity's own updater never starts inside the
            # session's kill-on-close Job (a stop would end an update half
            # way); the user's own ``agy`` runs still update it.
            env.update(ACC.NO_UPDATE_ENV)
        rules_note = ""
        if row.harness == OPENCODE:
            # v1.413.0: the mode's permission rules, for THIS process only (the
            # user's own config files are read, never written).
            env, rules_note = await asyncio.to_thread(O.child_env, env, row.mode, row.folder)
        # v1.414.0: Iron Jarvis's own tools (Memory, Browser) — ONLY when the
        # user ticked one this server serves; else no token, no configuration.
        live.caps = JT.load(getattr(row, "capabilities_json", "") or "")
        live.folder = row.folder or ""
        connection = JT.Connection()
        url = str(getattr(getattr(self.platform, "terminals", None), "mcp_url", "") or "")
        took_before = JT.takes(row.harness)
        if JT.wanted(live.caps) and url and took_before:
            token = self.tokens.mint(row.id, live.caps)
            connection = await asyncio.to_thread(
                JT.connect, row.harness, url=url, token=token, session_dir=session_dir(self.home, row.id),
            )
            env.update(connection.env)
        resume = bool(row.harness_session_id)
        # Claude Code and Pi take the id to save under (`--session-id`): minted
        # here, so the row names the conversation before it starts.
        sid = row.harness_session_id or (str(uuid.uuid4()) if row.harness in (CLAUDE, PI) else "")
        proc_holder: dict[str, Any] = {}

        async def write(obj: dict[str, Any]) -> None:
            p = proc_holder["p"]
            awrite = getattr(p, "awrite", None)
            if awrite is not None:
                await awrite(obj)  # v1.422.0: through the code host
            else:
                await asyncio.to_thread(p.write, obj)

        driver = self._new_driver(live, row, write, rules_note=rules_note, acp_servers=connection.acp_servers)
        # v1.441.0: a Claude Code branch made here (or a rewind not yet sent)
        # rides its command line until its next message lands.
        await asyncio.to_thread(BR.prepare_driver, self.home, row, driver)
        if isinstance(driver, AntigravityDriver):
            # v1.447.0: its own MCP servers for the Session tab — one
            # ``agy mcp list`` in the session's folder, off the loop, once the
            # session is open (names and on/off only).
            mcp_env, mcp_cwd, mcp_argv0 = dict(env), row.folder or "", list(argv0)
            driver.read_mcp = lambda: AGA.read_mcp_list(mcp_argv0, mcp_env, mcp_cwd)
        if isinstance(driver, AntigravityDriver) and driver.log_path:
            # v1.439.0: the folder its own log goes in (``--log-file``).
            await asyncio.to_thread(os.makedirs, os.path.dirname(driver.log_path), exist_ok=True)
            await asyncio.to_thread(A.trim_log, driver.log_path)
        argv = argv0 + driver.argv(session_id=sid, resume=resume) + connection.argv
        # v1.416.0 review: only Grok's stop ends what it left outside its Job.
        ends_left_behind = bool(getattr(driver, "notes_started_on_stop", False))
        if HL.enabled():
            # v1.422.0: the tool runs in the CODE HOST, which outlives an Iron
            # Jarvis restart (hosted/hostlink.py); no host = started as before.
            proc: Any = HL.new_process(argv, env=env, cwd=row.folder, name=f"hosted-{row.id}",
                                       ends_left_behind=ends_left_behind, home=self.home, sid=row.id)
            live.kept = {"rules_note": rules_note, "acp_servers": list(connection.acp_servers)}
        else:
            proc = HarnessProcess(argv, env=env, cwd=row.folder, name=f"hosted-{row.id}",
                                  ends_left_behind=ends_left_behind)
        proc_holder["p"] = proc

        def on_exit(code: int | None) -> None:
            self._on_exit(live, proc, code)

        def on_message(obj: dict[str, Any]) -> None:
            if heard_from(driver, obj):
                live.last_heard = self.clock()
            # A frame the parser cannot read must never end the session.
            try:
                driver.on_message(obj)
            except Exception:  # noqa: BLE001
                log.warning("a %s frame could not be read", row.harness, exc_info=True)

        try:
            await asyncio.to_thread(proc.start, loop, on_message, on_exit)
        except OSError as exc:
            self.tokens.revoke(row.id)
            # The program could not be started at all (v1.411.0: npm's POSIX
            # `pi` shim beside pi.cmd is not a Windows program, WinError 193):
            # one plain sentence, never a bare OSError.
            raise Refused(W.could_not_start(row.harness, argv0[0] if argv0 else "", exc)) from None
        live.proc, live.driver = proc, driver
        if isinstance(proc, HL.RemoteProcess):
            from . import adopt as ADOPT

            ADOPT.watch(self, live, proc)
        try:
            await driver.open(session_id=sid, resume=resume)
        except Exception as exc:  # noqa: BLE001 — said in words below
            # v1.439.0: the turn is NOT ended here as "interrupted" — the
            # refusal below is said as the reply's error and ends it "failed"
            # (it read "Stopped: the coding tool was closed", its reason lost).
            await self._stop_process(live, end_turn=False)
            said = exc.args[0] if exc.args else type(exc).__name__
            raise Refused(f"{W.label(row.harness)} did not start: {clip(said, 200)}") from None
        if not took_before and JT.takes(row.harness) and JT.wanted(live.caps) and url:
            # v1.441.0 (review of lane fix1): this start's handshake said the
            # tool DOES take Iron Jarvis's tools (a newer Antigravity) — too
            # late for this process, which got none: the next message starts
            # it again with them, so a ticked box is never shown on for nothing.
            live.restart = True
        pending = self._renames.pop(row.id, "")
        if pending:
            # v1.441.0 (review P4): a rename given while it was stopped.
            self._tell_rename(driver, pending)
        if isinstance(proc, HL.RemoteProcess):
            proc.snapshot_now()
            if proc.local is not None and not live.no_host_said:
                live.no_host_said = True  # v1.422.0: said once per session
                self._emit(live, make("notice", text=HL.NO_HOST_NOTE))

    def _new_driver(self, live: LiveSession, row: Any, write: Any, *, emit: Any = None, ask: Any = None,
                    rules_note: str = "", acp_servers: Any = ()) -> Driver:
        """The driver for one process of this session (v1.422.0: lifted out of
        :meth:`_start_process`, so a session adopted after a restart gets the
        very same one — ``hosted/adopt.py`` passes its own ``emit`` / ``ask``)."""
        cls = {CLAUDE: ClaudeDriver, CODEX: CodexDriver, PI: PiDriver, OPENCODE: OpenCodeDriver,
               GROK: GrokDriver, ANTIGRAVITY: AntigravityDriver}[row.harness]
        driver = cls(
            write=write,
            emit=emit or (lambda ev: self._emit(live, ev)),
            ask=ask or (lambda a: self._ask(live, a)),
            on_session_id=lambda s: self._on_session_id(live, s),
            mode=row.mode, model=row.model, effort=row.effort, cwd=row.folder,
        )
        if isinstance(driver, PiDriver):
            driver.catalog_path = self._pi_catalog
            # v1.431.0: only the user's press sets this (the row's column).
            driver.approve_folder = bool(getattr(row, "load_folder_resources", False))
            driver.forget_ask = lambda ask_id: self._forget_ask(live, ask_id)
        if isinstance(driver, OpenCodeDriver):
            driver.catalog_path = self._oc_catalog
            driver.agents_path = self._oc_agents
            driver.rules_note = rules_note
            driver.mcp_servers = list(acp_servers)
        if isinstance(driver, GrokDriver):
            driver.catalog_path = self._grok_catalog
            driver.mcp_servers = list(acp_servers)
        if isinstance(driver, AntigravityDriver):
            driver.catalog_path = self._agy_catalog
            driver.mcp_servers = list(acp_servers)
            # Its own log, beside the session's events (a failure's reason is
            # read from it; never the user's ~/.gemini log folder).
            driver.log_path = str(session_dir(self.home, row.id) / A.LOG_NAME)
            driver.on_title = lambda title: self._title_if_untitled(live, title)
            driver.on_effort_dropped = lambda effort: self._effort_dropped(live, effort)
        driver.steer_read = lambda ref: self._steer_read(live, ref)
        return driver

    def _title_if_untitled(self, live: LiveSession, title: str) -> None:
        """v1.439.0: the tool named its conversation (Antigravity's
        ``session_info_update``) — kept only for a session with no title yet
        (the first message names it otherwise). Off the loop; never raises."""
        async def save() -> None:
            try:
                kept = clip(title, 120)
                # (review) checked and written in one transaction: a title the
                # user set meanwhile is never replaced.
                if await asyncio.to_thread(store.set_title_if_empty, self.engine, live.sid, kept):
                    live.title = kept
            except Exception:  # noqa: BLE001 — a title is never worth a failure
                log.debug("could not keep a Code-view session's title", exc_info=True)

        with contextlib.suppress(RuntimeError):
            asyncio.ensure_future(save())

    def _effort_dropped(self, live: LiveSession, effort: str) -> None:
        """v1.441.0 (review A6): the tool's model does not take the session's
        effort (Antigravity, after its list changed) — the row and the chip
        go back to the default level (only while the row still says it). Off
        the loop; never raises."""
        async def save() -> None:
            try:
                await asyncio.to_thread(store.clear_effort_if, self.engine, live.sid, effort)
            except Exception:  # noqa: BLE001 — a chip's tidy-up is never worth a failure
                log.debug("could not clear a Code-view session's effort", exc_info=True)

        with contextlib.suppress(RuntimeError):
            asyncio.ensure_future(save())

    def _on_session_id(self, live: LiveSession, sid: str) -> None:
        async def save() -> None:
            try:
                row = await asyncio.to_thread(store.get, self.engine, live.sid)
                if row is not None and row.harness_session_id != sid:
                    await asyncio.to_thread(store.update, self.engine, live.sid, harness_session_id=sid)
            except Exception:  # noqa: BLE001 — the next start mints again, said nowhere
                log.warning("could not record a Code-view session's harness id", exc_info=True)

        live.id_save = asyncio.ensure_future(save())

    def _on_exit(self, live: LiveSession, proc: HarnessProcess, code: int | None) -> None:
        if live.proc is not proc:
            return
        self.tokens.revoke(live.sid)  # v1.414.0: the token dies with the process
        drv = live.driver
        if drv is not None:
            drv.on_exit()
        live.proc, live.driver = None, None
        # v1.414.0 review: our own connection files go with a process that
        # ended BY ITSELF too (a crash, a kill) — off the loop, and under the
        # session's own lock (v1.425.0: a start holds it, no longer the
        # pool's), so a new start writing them again is never undone.
        with contextlib.suppress(RuntimeError):
            asyncio.ensure_future(self._remove_files_if_stopped(live))
        self._room_changed()  # v1.425.0 (B7): a place in the pool came free
        self._expire_asks(live)
        self._close_open_steps(live)
        turn = live.turn
        if turn is not None and turn.running and not turn.ending and not proc.stopped:
            technical = proc.stderr_text() or f"exit code {code}"
            # Pi signs in by itself and has no accounts to move between: its
            # own words, never the other tools' sign-in or account presses.
            ev = (P.failure_event(technical) if live.harness == PI
                  else O.failure_event(technical) if live.harness == OPENCODE
                  else G.failure_event(technical) if live.harness == GROK
                  else A.failure_event(technical) if live.harness == ANTIGRAVITY
                  else W.failure_for(live.harness, technical))
            ev["title"] = W.GONE_DURING_TURN.format(label=W.label(live.harness))
            self._emit(live, ev)
            asyncio.ensure_future(self._end_turn(live, "failed"))

    async def _remove_files_if_stopped(self, live: LiveSession) -> None:
        """v1.414.0 review: remove the connection files of a process that ended
        by itself — only while no new process runs (a start writes them under
        the same lock: v1.425.0, the session's own — ``_drive`` holds it
        through the start, which no longer holds the pool's). Never raises."""
        try:
            async with live.lock:
                if live.proc is None:
                    await asyncio.to_thread(JT.remove_files, session_dir(self.home, live.sid))
        except Exception:  # noqa: BLE001 — files that hold no credential
            log.debug("could not remove a Code session's connection files", exc_info=True)

    async def _stop_process(self, live: LiveSession, *, why: str = "", end_turn: bool = True) -> None:
        """Stop the process (tree kill, off the loop). ALWAYS: its open cards
        close and its running turn ends "interrupted" — a stop never leaves a
        follower waiting for a done that cannot come. ``end_turn=False``: only
        for a restart before the turn that is starting (it runs on the new
        process)."""
        proc = live.proc
        live.proc = None
        drv, live.driver = live.driver, None
        # v1.414.0: the token dies with the process (before the stop: a call
        # made while it ends is refused, never served).
        self.tokens.revoke(live.sid)
        if proc is not None:
            await asyncio.to_thread(proc.stop)
            await asyncio.to_thread(JT.remove_files, session_dir(self.home, live.sid))
        if drv is not None:
            drv.on_exit()
        self._expire_asks(live)
        self._close_open_steps(live)
        self._room_changed()  # v1.425.0 (B7): a place in the pool came free
        turn = live.turn
        if end_turn and turn is not None and turn.running and not turn.ending:
            self._emit(live, make("notice", text=W.STOPPED_BECAUSE.format(why=why) if why else W.STOPPED_PLAIN))
            await self._end_turn(live, "interrupted")

    # -- events ----------------------------------------------------------------
    def _emit(self, live: LiveSession, ev: dict[str, Any]) -> None:
        turn = live.turn
        PG.note_event(live, ev)  # v1.422.0 (B4): when the tool first acted
        if ev.get("type") == "done":
            # The ONE way a turn ends (the row is stamped first).
            stop = str(ev.get("stop") or "completed")
            asyncio.ensure_future(self._end_turn(live, stop, ev))
            return
        if turn is not None and turn.running:
            ev["turn"] = turn.id
        OS.note(live.open_steps, ev)  # v1.427.2
        if ev.get("type") == "resolved":
            # v1.414.0: a card answered (anywhere), expired or stopped: its way
            # to the phone closes with it.
            phone = live.phones.pop(str(ev.get("request_id") or ""), None)
            if phone is not None:
                phone.close(str(ev.get("decision") or ""))
        if ev.get("type") == "error":
            live.last_error = str(ev.get("title") or "")
            if ev.get("fix_kind") == "account_limit":
                if turn is not None:
                    turn.account_limit = True
                ev.setdefault("account", live.account)
                live.limit = getattr(live.driver, "limit", None)
                live.limit_since = datetime.now(timezone.utc).isoformat()
                # v1.422.0 (B5): WHEN it resets, read now (the tool's own
                # record, the usage windows it reported, or its words) — the
                # turn's end may arm a wait on it (the view's ``account_wait``
                # says until when; the event itself is unchanged).
                from . import carry_on as CO

                live.limit_reset = CO.reset_from(live.driver, ev)
        stamp = HL.stamp_of(live.proc)
        if stamp is not None:
            # v1.422.0: the log's copy names the tool's line it came from, so
            # a restarted Iron Jarvis knows what was already shown
            # (hosted/adopt.py); the turn's watchers get the event as before.
            from . import adopt as ADOPT

            live.log.append(ADOPT.stamped(ev, stamp))
        else:
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
        drv = live.driver
        if stop == "interrupted" and getattr(drv, "close_after_stop", False) and live.proc is not None:
            # v1.416.0: Grok's cancel ends the reply and leaves the command it
            # was running RUNNING (measured). The process goes (its Job takes
            # the command with it) before the done; the conversation is on
            # disk and reopens with the next message.
            drv.close_after_stop = False
            try:
                await self._stop_process(live, end_turn=False)
            except Exception:  # noqa: BLE001 — the done still goes out
                log.warning("could not close Grok after a Stop", exc_info=True)
            self._emit(live, make("notice", text=W.GROK_STOP_CLOSED))
        pending = live.id_save
        if pending is not None and not pending.done():
            # v1.414.3: the tool's session id is on the row before the reply
            # ends (it was saved in the background, and a read right after the
            # reply — "Open in terminal", a reopen — could find it empty).
            try:
                await asyncio.wait_for(asyncio.shield(pending), ID_SAVE_WAIT_S)
            except Exception:  # noqa: BLE001 — the done still goes out
                log.debug("a Code-view session's id was still being saved", exc_info=True)
        saving = live.ref_save
        if saving is not None and not saving.done():
            # v1.441.0: the message's reference is on disk before its reply ends.
            try:
                await asyncio.wait_for(asyncio.shield(saving), ID_SAVE_WAIT_S)
            except Exception:  # noqa: BLE001 — the done still goes out
                log.debug("a Code-view message's reference was still being written", exc_info=True)
        error = live.last_error
        try:
            await asyncio.to_thread(store.update, self.engine, live.sid, last_error=error)
        except Exception:  # noqa: BLE001 — the done still goes out
            log.debug("could not stamp a Code-view session", exc_info=True)
        # v1.422.0 (B5): an account's limit ARMS its wait by itself (or a turn
        # that served clears the card) — written before the done, so the
        # page's re-read right after shows the waiting card. Never raises.
        from . import carry_on as CO

        await CO.turn_ended(self, live, turn, str((done or {}).get("stop") or stop))
        await self._settle_branch(live)
        done = dict(done) if done is not None else make("done", stop=stop)
        done.setdefault("stop", stop)
        if NEWS.outcome_of(str(done.get("stop"))) == "failed":
            # Readiness 0j.5 item 17: a reply that FAILED keeps every edit it
            # made — said, from its own finished edit/write steps, before the
            # done (nothing is rolled back; the user must not have to guess).
            from .kept_files import kept_notice

            _kept = kept_notice(list(turn.events))
            if _kept:
                self._emit(live, make("notice", text=_kept))
        done["turn"] = turn.id
        live.log.append(done)
        turn.add(done)
        live.last_active = self.clock()
        live.last_error = ""
        turn.end("done" if done.get("stop") == "completed" else str(done.get("stop")))
        if live.turn is turn:
            live.turn = None
        self._room_changed()  # v1.425.0 (B7): this session may be stoppable now
        self._after_turn_queue(live, turn, done)  # v1.418.0 (C4.8)
        # v1.414.0: a long reply that ENDED is told to the bell and the tray
        # (never for a Stop or a cut-off, never beside an account's limit
        # card) — after its done is on the record.
        if turn.started_mono is not None and not turn.account_limit and not self._closing:
            elapsed = self.clock() - turn.started_mono
            if elapsed > NEWS.FINISH_NOTICE_AFTER_S and NEWS.outcome_of(str(done.get("stop"))):
                from ..harness.stall import keep

                with contextlib.suppress(RuntimeError):
                    keep(asyncio.ensure_future(NEWS.announce_finished(
                        self.platform, live.sid, turn.id, live.title, live.harness,
                        str(done.get("stop")), elapsed,
                    )))

    async def _settle_branch(self, live: LiveSession) -> None:
        """v1.441.0: a Claude Code branch (or rewind) is made once its message
        is in the conversation's file; its command line goes back to a plain
        ``--resume`` then. Never fails the reply."""
        drv = live.driver
        if live.harness != CLAUDE or drv is None:
            return
        mid = str(getattr(drv, "_turn_mid", "") or "")
        if not mid:
            return
        try:
            row = await asyncio.to_thread(store.get, self.engine, live.sid)
            if row is not None and await asyncio.to_thread(BR.settle_pending, self.home, row, mid):
                drv.fork_from, drv.resume_at = "", ""
        except Exception:  # noqa: BLE001
            log.debug("could not settle a Code-view branch", exc_info=True)

    def _close_open_steps(self, live: LiveSession) -> None:
        """v1.427.2: the tool's process is gone (or going), so every step it
        began and never finished is closed "did not finish" (status
        ``stopped``) — never a spinner on a step nothing runs. Steps a reply
        ran are closed silently (that reply's own end says why); steps the
        tool ran ON ITS OWN get one notice. Never raises."""
        steps = live.open_steps
        if not steps:
            return
        try:
            loose = OS.outside(steps)
            rows = OS.closing_rows(steps)
            live.open_steps = {}
            for row in rows:
                self._emit(live, row)
            if loose:
                words = W.STEPS_CUT_RESTART if self._closing else W.STEPS_CUT_STOPPED
                self._emit(live, make("notice", text=words.format(
                    label=W.label(live.harness), steps=W.steps_words(loose))))
        except Exception:  # noqa: BLE001 — a stop never fails over its rows
            log.warning("could not close a Code-view session's unfinished steps", exc_info=True)

    def _expire_asks(self, live: LiveSession) -> None:
        """Every open card closes when its process goes (nobody can answer it)."""
        for ask_id in list(live.asks):
            live.asks.pop(ask_id, None)
            self._note_answered(live, ask_id, "stopped", "app")
            self._emit(live, make("resolved", request_id=ask_id, decision="expired"))

    def _forget_ask(self, live: LiveSession, ask_id: str) -> None:
        """One card the tool stopped waiting for (a Pi dialog that timed out)."""
        if live.asks.pop(ask_id, None) is not None:
            self._note_answered(live, ask_id, "timeout", "timeout")
            self._emit(live, make("resolved", request_id=ask_id, decision="expired"))

    # -- v1.417.0 (C1.11): the autonomy metrics count these cards ---------------
    def _note_asked(self, live: LiveSession, ask: Ask) -> None:
        """``interrupt.asked`` for a card or question put to the user (names
        only; never raises — a metric never fails a card)."""
        try:
            from ..core.approvals import InterruptNote

            bus = getattr(self.platform, "event_bus", None)
            if bus is None:
                return
            turn = live.turn.id if live.turn is not None and live.turn.running else ""
            ev = ask.event or {}
            # (review) Claude Code's ExitPlanMode card is a PLAN approval: the
            # metrics never count one as avoidable and report it apart.
            kind = ("question" if ask.kind == "question"
                    else "plan" if ask.tool in ("ExitPlanMode", "codex_plan") else "card")
            note = InterruptNote(
                bus, kind=kind, tool=ask.tool or "",
                surface=NEWS.SURFACE, ask_id=ask.id, thread_id=live.sid, turn_id=turn,
                session_id=NEWS.session_id_for(live.sid), once_only=bool(ev.get("once_only")),
            )
            note.asked()
            live.notes[ask.id] = note
        except Exception:  # noqa: BLE001
            log.debug("could not note a Build › Code card for the metrics", exc_info=True)

    def _note_answered(self, live: LiveSession, ask_id: str, decision: str, actor: str) -> None:
        """``interrupt.answered`` once, in the metric's own words (once /
        conversation / deny; answered / skipped; stopped / timeout)."""
        note = live.notes.pop(ask_id, None)
        if note is None:
            return
        try:
            note.answered(decision, actor)
        except Exception:  # noqa: BLE001
            log.debug("could not note a Build › Code answer for the metrics", exc_info=True)

    def _refresh_asks(self, live: LiveSession) -> None:
        """v1.441.0 (review of lane fix1): the mode moved while cards wait —
        a card whose words name a mode (Claude Code's plan card: "Approve,
        then ask first") is re-sent with the mode it will REALLY leave the
        session in, so its words, Enter's refusal of Full access and the
        mode applied are one fact. Never raises."""
        driver = live.driver
        for ask in list(live.asks.values()):
            try:
                ev = driver.refresh_ask(ask) if driver is not None else None
            except Exception:  # noqa: BLE001 — a card's words are never worth a failure
                log.debug("could not refresh a Code-view card", exc_info=True)
                continue
            if ev is not None:
                self._emit(live, ev)

    def _ask(self, live: LiveSession, ask: Ask) -> None:
        live.asks[ask.id] = ask
        self._note_asked(live, ask)
        self._emit(live, ask.event)
        # v1.414.0: a card nobody answers in the app goes to the phone after
        # ``stall.PHONE_AFTER_S`` (only the kinds :func:`.news.phone_may_send`
        # allows); it closes with the card's ``resolved``.
        if ask.kind == "approval":
            watch = NEWS.PhoneWatch(self, live, ask, after_s=self.phone_after_s)
            live.phones[ask.id] = watch
            watch.start()

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
        if row.harness == PI and P.typed_builtin(text, self.commands_for(row.harness, live)) is not None:
            # v1.429.0: a note reaches Pi as plain words — one of Pi's own
            # commands goes as a message of its own, after this reply.
            raise Refused(W.PI_COMMAND_NOT_A_NOTE, 400)
        typed = T.typed_builtin(row.harness, text, _own_commands(row.harness, live, row.folder or ""))
        if typed is not None:
            # v1.439.0: Grok's approval commands are never sent (as a note
            # either); a setting or a terminal-only built-in is a message of
            # its own.
            # (review) a command done nowhere — Grok's approval commands, a
            # Claude Code built-in that needs its terminal — says its OWN
            # words: "send it once it has finished" would only be refused then.
            why = T.refusal(row.harness, typed[0])
            raise Refused(why or T.NOT_A_NOTE.format(label=label), 400)
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
    async def start_turn(self, sid: str, text: str, turn_id: str, files: list[str] | None = None,
                         *, carry: dict[str, Any] | None = None) -> HostedTurn:
        """``carry`` (v1.422.0): a message Iron Jarvis sends BY ITSELF to carry
        a reply on (``hosted.carry_on``) — its ``user`` event carries it (the
        page shows a line, not a bubble) and it ends no wait. Any other
        message is the user's own: it ends a limit's wait (``_drive``)."""
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
            return await self._start_turn_in(row, sid, turn_id, text, files, made, forget, carry=carry)
        except BaseException:
            if made:
                await asyncio.to_thread(self._take_back_folder, sid, made, forget=forget)
            raise

    async def _start_turn_in(self, row: Any, sid: str, turn_id: str, text: str, files: list[str],
                             made: str = "", forget: bool = False, *,
                             carry: dict[str, Any] | None = None) -> HostedTurn:
        live = self._live(row)
        live.account = row.account
        if live.claim:
            raise Refused(W.CLAIMED.format(what=live.claim))
        if live.turn is not None and live.turn.running:
            raise Refused(W.BUSY)
        if live.working_alone:
            raise Refused(W.PI_STILL_WORKING)
        builtin = await self._pi_typed(row, live, text, files) if row.harness == PI and not carry else None
        if row.harness in (CLAUDE, GROK) and not carry:
            builtin = await self._typed_setting(row, live, text, files)
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
        turn.started_mono = self.clock()
        # v1.422.0 review: a turn Iron Jarvis started BY ITSELF (a carry-on) is
        # one Emergency stop stops (``carry_on.stop_unattended_turns``).
        turn.carry = dict(carry) if carry else None
        # v1.422.0: a carry-on Iron Jarvis sends by itself never names the
        # session (its words are the app's, not the user's).
        live.title = (row.title or "").strip() or ("" if carry or builtin else first_message_title(text))
        self.turns[turn_id] = turn
        live.turn = turn
        live.last_active = self.clock()
        turn.task = asyncio.ensure_future(self._drive(live, row, turn, text, files, made, forget, carry=carry,
                                                      builtin=builtin))
        # v1.409.0 (found live): a session is titled by its first message, as
        # a chat is — never "New Claude Code session" forever. Only an EMPTY
        # title (a name the user gave is theirs) and only once the turn is
        # really running (a refused send titles nothing).
        if not (row.title or "").strip() and not carry and not builtin:
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

    async def _pi_typed(self, row: Any, live: "LiveSession", text: str,
                        files: list[str]) -> tuple[str, str] | None:
        """v1.429.0: the message is one of Pi's OWN ``/`` commands? Pi's RPC
        would hand it to the model as plain text (measured), so the Code view
        does it: ``(name, words)`` to run, a Refused (one line) for one only
        Pi's terminal has, None for any other message (an extension, template
        or skill of the user's with that name included — Pi runs theirs)."""
        typed = P.typed_builtin(text, self.commands_for(row.harness, live, folder=row.folder or ""))
        if typed is None:
            return None
        name, args = typed
        if name in P.TERMINAL_ONLY:
            raise Refused(P.TERMINAL_ONLY[name], 400)
        if name in P.SHELL_BUILTINS and not args:
            raise Refused(P.SHELL_NEEDS_COMMAND, 400)  # v1.441.0
        if files:
            raise Refused("Pi's own commands take no files — send the files in a message of their own.", 400)
        if name == "model":
            if not args:
                raise Refused(P.MODEL_NEEDS_NAME, 400)
            got = P.resolve_model(args, await asyncio.to_thread(self._models, PI))
            if not got:
                raise Refused(W.PI_NO_SUCH_MODEL.format(name=clip(args, 80)), 400)
            args = got
        elif name == "thinking":
            if not args:
                raise Refused(P.THINKING_NEEDS_LEVEL, 400)
            rows = await asyncio.to_thread(self._models, PI)
            args = check_effort(PI, args.split()[0], row.model, rows)
        return name, args

    async def _typed_setting(self, row: Any, live: "LiveSession", text: str,
                             files: list[str]) -> tuple[str, str] | None:
        """v1.439.0: the message is one of Claude Code's or Grok's own commands
        done here (``hosted/typed``)? ``(name, value)`` for ``/model`` and
        ``/effort`` (resolved against that tool's own lists), a Refused (one
        line) for one Iron Jarvis does not send, None for anything else."""
        typed = T.typed_builtin(row.harness, text, _own_commands(row.harness, live, row.folder or ""))
        if typed is None:
            return None
        name, args = typed
        label = W.label(row.harness)
        why = T.refusal(row.harness, name)
        if why:
            raise Refused(why, 400)
        if files:
            raise Refused(T.TAKES_NO_FILES.format(label=label), 400)
        rows = await asyncio.to_thread(self._models_with_efforts, row.harness)
        if name == "model":
            if not args:
                raise Refused(T.MODEL_NEEDS_NAME, 400)
            if row.harness == CLAUDE:
                # v1.441.0 (review P2): Claude Code's own names too (opus,
                # sonnet, haiku, the [1m] forms); a name nothing resolves goes
                # to Claude Code as typed (v1.438.0), never refused here.
                got = await asyncio.to_thread(T.resolve_claude_model, args, rows)
                if got is None:
                    return None
                return name, got
            got = T.resolve_model(args, rows)
            if got is None:
                raise Refused(T.NO_SUCH_MODEL.format(label=label, name=clip(args, 80)), 400)
            return name, got
        if not args:
            raise Refused(T.EFFORT_NEEDS_LEVEL, 400)
        level = args.split()[0].lower()
        if level in T.DEFAULT_WORDS:
            return name, ""
        return name, check_effort(row.harness, level, row.model,
                                  rows if row.harness in LISTED_MODELS else None)

    async def _typed_setting_run(self, live: "LiveSession", row: Any, name: str, value: str) -> None:
        """v1.439.0: a typed ``/model`` or ``/effort`` through the chips' own
        path (``patch``), said in one line; no turn of the tool's."""
        label = W.label(row.harness)
        # (review) Claude Code's own "now answers with …" line is held while
        # the typed /model switches it: the switch is said ONCE, below.
        drv = live.driver
        quiet = name == "model" and drv is not None and getattr(drv, "say_model_switch", None) is True
        if quiet:
            drv.say_model_switch = False
        try:
            out = await self.patch(live.sid, {"model": value} if name == "model" else {"effort": value})
        except Refused as r:
            self._emit(live, W.error(r.sentence, "", ""))
            await self._end_turn(live, "failed")
            return
        finally:
            if quiet:
                drv.say_model_switch = True
        when = (out.get("applied") or {}).get(name)
        later = " from your next message" if when == "next" and live.running else ""
        if name == "model":
            if value:
                rows = await asyncio.to_thread(self._models, row.harness)
                hit = next((r for r in rows if r.get("id") == value), {})
                said = f"{label} now uses {hit.get('label') or value}{later}."
            else:
                said = f"{label} goes back to its own default model{later}."
            cleared = (out.get("applied") or {}).get("effort_cleared")
            if cleared:
                said += f" This model does not take the {cleared} effort level, so {label}'s default level applies."
        else:
            said = (f"{label}'s effort level is now {value}{later}." if value
                    else f"{label} goes back to its own default effort level{later}.")
        self._emit(live, make("notice", text=said))
        await self._end_turn(live, "completed")

    async def _pi_setting(self, live: "LiveSession", row: Any, name: str, args: str) -> None:
        """v1.429.0: ``/model``, ``/thinking`` and ``/reload`` — the session's
        own settings, through the same path as the chips (``patch``), said
        in one line; no Pi process is started for them."""
        if name == "reload":
            if live.running:
                live.restart = True
                said = "Pi will read your extensions, skills and templates again before your next message."
            else:
                said = "Pi reads your extensions, skills and templates fresh when it starts, with your next message."
            self._emit(live, make("notice", text=said))
            await self._end_turn(live, "completed")
            return
        try:
            out = await self.patch(live.sid, {"model": args} if name == "model" else {"effort": args})
        except Refused as r:
            self._emit(live, W.error(r.sentence, "", ""))
            await self._end_turn(live, "failed")
            return
        if name == "model":
            rows = await asyncio.to_thread(self._models, PI)
            hit = next((r for r in rows if r.get("id") == args), {})
            label = str(hit.get("label") or args)
            group = str(hit.get("group") or "")
            said = f"Pi now uses {label}{f' ({group})' if group else ''}."
            cleared = (out.get("applied") or {}).get("effort_cleared")
            if cleared:
                said += f" This model does not take the {cleared} thinking level, so Pi's default level applies."
        else:
            said = f"Pi's thinking level is now {args}."
        self._emit(live, make("notice", text=said))
        await self._end_turn(live, "completed")

    async def _drive(self, live: LiveSession, row: Any, turn: HostedTurn, text: str, files: list[str],
                     made: str = "", forget: bool = False, *, carry: dict[str, Any] | None = None,
                     builtin: tuple[str, str] | None = None) -> None:
        user = make("user", text=clip(text, TEXT_CHARS))
        if files:
            user["files"] = [clip(f, 500) for f in files]
        if carry:
            user["carry"] = dict(carry)  # v1.422.0: Iron Jarvis carried it on
        if builtin is not None and row.harness == PI and builtin[0] in P.SHELL_BUILTINS:
            # v1.441.0 review: a command the user ran with ``!`` — a restart
            # that cuts it off never carries it on by itself
            # (``carry_on.WHY_COMMAND``): there is no reply for the model to
            # carry on, and Iron Jarvis never starts the model's work on it.
            user["command"] = True
        self._emit(live, user)
        started = False
        turn.unsent = True  # v1.425.0 (review, B7): not the tool's yet (``interrupt``)
        try:
            if not carry:
                # v1.422.0 (B5): the user's own message ends a limit's wait
                # (its turn is already ``live.turn``, so the loop skips it).
                from . import carry_on as CO

                try:
                    await asyncio.to_thread(CO.on_user_message, self.home, live.sid)
                except Exception:  # noqa: BLE001 — a wait's file never fails a message
                    log.warning("could not end a Code-view session's wait", exc_info=True)
            # v1.420.0 (C4.12): the folder's git tree as this message starts,
            # before the tool hears it — the Changes pane's "This message".
            # Off the loop, bounded, never failing the turn; a late tree is
            # dropped (the tool may already be working).
            from . import panes as PN

            if builtin is not None and row.harness in (CLAUDE, GROK):
                # v1.439.0: /model or /effort typed for Claude Code or Grok —
                # the chips' own path, no turn of the tool's.
                await self._typed_setting_run(live, row, *builtin)
                return
            if builtin is not None and builtin[0] in P.SETTING_BUILTINS:
                # v1.429.0: the session's own setting — no Pi process needed.
                await self._pi_setting(live, row, *builtin)
                return
            await PN.record_turn_start_async(self.home, live.sid, str(getattr(row, "folder", "") or ""), turn.id)
            async with live.lock:
                self._say_start(live, row)  # v1.417.0 (C4.7)
                await self._ensure_running(live, row)
            started = True
            if live.driver is None:
                raise HarnessGone("the coding tool stopped")
            if not turn.running:
                # v1.425.0 (B7): Stop ended this turn while its tool started (or
                # while it waited for a place): the tool never hears it.
                raise _StoppedWhileWaiting()
            turn.unsent = False  # from here a Stop is the tool's to act on
            if builtin is not None:
                # v1.429.0: one of Pi's own commands (compact, session, name,
                # export) through Pi's RPC, as a turn of its own.
                await live.driver.run_builtin(*builtin)
                if builtin[0] == "name" and builtin[1]:
                    await asyncio.to_thread(store.update, self.engine, live.sid, title=clip(builtin[1], 120))
            else:
                before = BR.ref_before(live.driver)
                live.ref_save = saving = asyncio.get_running_loop().create_future()
                try:
                    await live.driver.send_user(text, files)
                except BaseException:
                    # Review: a message the tool never received is never
                    # recorded as sent (it was offered for a branch, and for
                    # Pi it broke the text match of every later one).
                    if not saving.done():
                        saving.set_result(None)
                    raise
                await self._note_sent(live, row, user, before, text)
            if turn.running:
                await turn.finished.wait()
        except Refused as r:
            self._emit(live, W.error(r.sentence, "Fix that, then send your message again.", ""))
            await self._end_turn(live, "failed")
        except _StoppedWhileWaiting:
            pass  # v1.425.0 (B7): Stop already ended the turn; no tool heard it
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

    async def _note_sent(self, live: LiveSession, row: Any, user: dict[str, Any], before: str, text: str) -> None:
        """v1.441.0: the tool's own reference for the message just sent, beside
        the log (``hosted/branching``). Never fails the message."""
        drv = live.driver
        saving = live.ref_save
        try:
            if drv is None or user.get("carry"):
                return
            ref = BR.ref_after(drv, before)
            hid = str(getattr(drv, "session_id", "") or row.harness_session_id or "")
            # v1.441.0: a message is named by its ``user`` event's ``at`` (the view's
            # Branch from here / Rewind to here send it back).
            await asyncio.to_thread(BR.note_sent, self.home, live.sid, str(user.get("at") or ""), ref, hid, text)
        except Exception:  # noqa: BLE001
            log.debug("could not note a Code-view message's reference", exc_info=True)
        finally:
            # The reply's end waits for this (a fast reply could otherwise end
            # before its message's reference is on disk).
            if saving is not None and not saving.done():
                saving.set_result(None)

    def _say_start(self, live: LiveSession, row: Any) -> None:
        """v1.417.0 (C4.7): a process (re)starting for this message is SAID —
        a cold start (the first message, after the idle reaper, after an
        effort change, after an eviction) used to show the same spinner with
        no word for many seconds."""
        if live.running and not live.restart:
            return
        why = "restart" if live.running else live.stopped_why
        live.stopped_why = ""
        # v1.422.0 (B4, C4.7): the idle stop said with the limit it really
        # used (60 minutes for the session the user looked at, the 8-hour
        # bound for one kept for its programs), and the programs that stop
        # ended, named again — then forgotten (the row stops saying it).
        after_s, live.stopped_after_s = live.stopped_after_s, 0.0
        ended, live.stopped_programs = live.stopped_programs, []
        live.stopped_note = ""
        base = W.start_notice(row.harness, why=why, resumed=bool(row.harness_session_id),
                              idle_minutes=int(IDLE_S // 60))
        if why == "idle" and after_s and int(after_s) != int(IDLE_S):
            base = PG.idle_start_words(W.label(row.harness), after_s)
        self._emit(live, make("notice", text=PG.start_text(base, ended)))

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
                     answers: Any = None, *, actor: str = "app") -> dict[str, Any]:
        await self._row(sid)
        live = self.live.get(sid)
        decision = str(decision or "").strip()
        if decision not in ANSWER_DECISIONS:
            raise Refused(f"Answer with one of: {', '.join(ANSWER_DECISIONS)}.", 400)
        rid = str(request_id or "")
        ask = live.asks.get(rid) if live else None
        if live is None or ask is None or live.driver is None:
            raise Refused("No such question is waiting (it was answered, or the session stopped).", 404)
        if ask.kind == "question" and decision in ("allow_session", ALLOW_COMMANDS):
            decision = "allow"
        if decision == ALLOW_COMMANDS and live.harness not in (CLAUDE, CODEX):
            # v1.417.0 (C1.1): Pi and OpenCode have no such answer.
            raise Refused(W.ALL_COMMANDS_NOT_OFFERED.format(label=W.label(live.harness)), 400)
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
        self._note_answered(live, ask.id, metric_decision(ask.kind, decision), actor)
        self._emit(live, make("resolved", request_id=ask.id, decision=decision))
        new_mode = ask.data.get("sets_mode") if decision == "allow_session" else None
        # v1.441.0 (review P1): the mode an approved plan goes back to is
        # read NOW, before the driver hears the answer — never the one the
        # card was raised with. A mode picked on the chip while the card
        # waited is the session's already (and Claude Code's: it leaves Plan
        # only while still in it), so it is never overwritten.
        exit_mode = getattr(driver, "plan_exit_mode", None)
        if not callable(exit_mode):
            exit_mode = _plan_exit_mode
        back = exit_mode(ask, decision) if not new_mode else ""
        if new_mode:
            self._emit(live, make("notice", text=W.MODE_FROM_SESSION_ALLOW.format(
                label=W.label(live.harness), mode=MODE_LABELS[new_mode])))
        elif back in MODE_LABELS:
            # v1.439.0 (review): an approved plan leaves Plan for the mode the
            # card named ("Approve, then ask first"): the row and the chip
            # follow, so the next process does not start in Plan again.
            new_mode = back
            self._emit(live, make("notice", text=W.MODE_AFTER_PLAN.format(
                label=W.label(live.harness), mode=MODE_LABELS[new_mode])))
        if payload is not None:
            try:
                await driver.write(payload)
            except HarnessGone:
                raise Refused("The session stopped before your answer reached it.", 409) from None
            driver.answered(ask, decision)
            self._refresh_asks(live)
        elif prepare is not None and callable(getattr(driver, "answer_here", None)):
            # v1.441.0: a card the DRIVER answers itself (Codex's plan card: no
            # request of Codex's waits on it) — after the card closed.
            await driver.answer_here(ask, decision, message)
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
        # v1.474.0 (Hand off): the user's Stop stops this session's running
        # helpers too (in the background; a Stop never waits on them).
        HO.stop_helpers_soon(self, sid)
        # v1.422.0 review: the Stop is KEPT before the tool is told — if the app
        # goes before the tool confirms it, the reply is never read as one a
        # restart cut off (never carried on by itself). Never fails a Stop.
        from . import carry_on as CO

        try:
            await asyncio.to_thread(CO.note_stopped, self.home, sid, live.turn.id)
        except Exception:  # noqa: BLE001
            log.warning("could not keep a Code-view session's Stop", exc_info=True)
        turn = live.turn
        if turn is not None and turn.running and turn.unsent:
            # v1.425.0 (review, B7): the message is not the tool's yet — it
            # waits for a place, or the tool is still STARTING (its process up,
            # its handshake not done). Telling the tool to interrupt stopped
            # nothing (no reply runs there), the turn stayed open and the
            # message was sent once the start finished. The turn ends here;
            # ``_drive`` then never hands it over.
            await self._end_turn(live, "interrupted")
            return {"ok": True, "running": True}
        driver = live.driver
        if driver is not None and live.running:
            async def tell() -> None:
                stopping = getattr(driver, "stopping", None)
                if callable(stopping):
                    # v1.416.0 review: Grok ends its reply on the deny below,
                    # before the cancel — the driver knows it is a Stop first.
                    stopping()
                for ask_id in list(live.asks):  # an unanswered card goes with the stop
                    ask = live.asks.pop(ask_id, None)
                    if ask is None:
                        continue
                    try:
                        await driver.answer(ask, "deny", "The user pressed Stop.", None)
                    except Exception:  # noqa: BLE001
                        pass
                    self._note_answered(live, ask_id, "stopped", "app")
                    self._emit(live, make("resolved", request_id=ask_id, decision="deny"))
                proc = live.proc
                if getattr(driver, "notes_started_on_stop", False) and proc is not None:
                    # v1.416.0: Grok's cancel leaves its command running with
                    # no parent — what it started is read BEFORE it is told.
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(proc.note_started)
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
            try:
                await self.reap_once()
            except Exception:  # noqa: BLE001 — v1.422.0: one bad minute never ends the reaper
                log.warning("the Code view's idle check failed; it looks again in a minute", exc_info=True)

    async def reap_once(self, now: float | None = None) -> list[str]:
        """Stop every process idle longer than its limit (:data:`IDLE_S`;
        v1.422.0: 60 minutes for the session the user looks at), unless its
        tool still runs a program it started that serves (:mod:`.programs`)
        — and say what a stop ended."""
        now = self.clock() if now is None else now
        stopped = []
        due: list[tuple[LiveSession, float]] = []
        for live in list(self.live.values()):
            quiet = now - max(live.last_active, live.last_heard)
            if live.running and not live.busy and not live.keeps_working(now) and quiet >= IDLE_S:
                due.append((live, quiet))
            else:
                live.keeping = None  # not at risk: nothing to say on its row
        if not due:
            return stopped
        # v1.422.0 (B4): ONE read of every due session's tree, off the loop
        # and bounded (only sessions past the 15 minutes are ever read).
        readings = await PG.read_many([lv for lv, _ in due])
        for live, _was in due:
            quiet = now - max(live.last_active, live.last_heard)
            if not live.running or live.busy or live.keeps_working(now) or quiet < IDLE_S:
                continue  # it changed while the trees were read (a message came)
            verdict = PG.decide(live, readings.get(live.sid), quiet=quiet, now=now, base=IDLE_S)
            if verdict.keep:
                continue
            await self._stop_process(live)
            live.stopped_why = "idle"  # v1.417.0 (C4.7): said at the next start
            live.stopped_after_s = verdict.after_s or IDLE_S
            self._said_stopped(live, list(verdict.programs), why=verdict.why, after_s=verdict.after_s)
            stopped.append(live.sid)
        return stopped

    def _said_stopped(self, live: LiveSession, programs: list[Any], *, why: str, after_s: float = 0.0) -> None:
        """v1.422.0 (B4): a stop that ENDED programs the tool started is
        never silent — a notice in the session's own conversation now, the
        row's ``stopped_note``, and the next start's notice name them."""
        live.keeping = None
        live.stopped_programs = [p.words() for p in programs]
        words = PG.stopped_words(W.label(live.harness), programs, why=why, after_s=after_s, max_live=MAX_LIVE)
        live.stopped_note = words
        if words:
            log.info("Code-view session %s: its coding tool was closed (%s) with %d program(s) it started",
                     live.sid, why, len(programs))
            self._emit(live, make("notice", text=words))

    async def seen(self, sid: str) -> dict[str, Any]:
        """v1.422.0 (C4.7): the Code view still shows this session (its beat,
        every :data:`.programs.BEAT_S` while the window is seen)."""
        live = self.live.get(sid)
        if live is None:
            await self._row(sid)  # 404 for a session that does not exist
            return {"ok": True}
        PG.note_looked(live, self.clock())
        return {"ok": True}

    async def close_tool(self, sid: str) -> dict[str, Any]:
        """v1.422.0 (B4): the user's own "Close it now" — the coding tool and
        everything it runs stop (a tree kill); the conversation stays, and the
        next message resumes it. Refused while a reply runs."""
        await self._row(sid)
        live = self.live.get(sid)
        if live is None or not live.running:
            return {"ok": True, "closed": False}
        if (live.turn is not None and live.turn.running) or live.claim:
            raise Refused(W.RUNNING)
        now = self.clock()
        if live.busy or live.replying_alone(now) or live.helpers_working(now):
            # v1.422.0 review: working with no message of the user's running —
            # a reply Claude Code writes on its own once its helpers end, a card
            # waiting with no turn open, helper agents at work, Pi's own run. A
            # tree kill here ended that work mid-way; the press waits for it.
            raise Refused(PG.CLOSE_WAITS.format(label=W.label(live.harness)))
        ended = list(live.keeping.programs) if live.keeping is not None else []
        await self._stop_process(live)
        live.stopped_why = ""
        live.keeping = None
        live.stopped_programs = []
        live.stopped_note = ""
        return {"ok": True, "closed": True, "stopped": [p.words() for p in ended]}

    async def shutdown(self, *, keep_tools: bool = False) -> None:
        """The daemon is stopping: every harness process goes (tree kill, off
        the loop), every running turn ends with what it wrote kept.

        v1.422.0: ``keep_tools`` (a RESTART, ``POST /shutdown`` with
        ``restart``): a tool the code host runs is left running, its state
        kept one last time — the next Iron Jarvis carries it on
        (:meth:`adopt`). A tool that is not in the host still goes."""
        self._closing = True
        if self._reaper is not None:
            self._reaper.cancel()
        lives = list(self.live.values())
        if keep_tools:
            kept = [lv for lv in lives if HL.kept_by_host(lv.proc)]
            for lv in kept:
                lv.proc.final_snapshot()
            await asyncio.gather(*(asyncio.to_thread(lv.log.close) for lv in kept), return_exceptions=True)
            lives = [lv for lv in lives if lv not in kept]
        for live in lives:
            self._expire_asks(live)
            self._close_open_steps(live)
            if live.turn is not None and live.turn.running and not live.turn.ending:
                self._emit(live, W.error(STOPPED_TITLE, STOPPED_FIX, ""))
        await asyncio.gather(*(self._end_turn(lv, "interrupted") for lv in lives), return_exceptions=True)
        await asyncio.gather(*(self._stop_process(lv) for lv in lives), return_exceptions=True)
        await asyncio.gather(*(asyncio.to_thread(lv.log.close) for lv in lives), return_exceptions=True)

    async def adopt(self, into: list[str] | None = None) -> list[str]:
        """v1.422.0 (boot): carry on every session whose tool the code host
        kept running across the restart (``hosted/adopt.py``). Returns the
        session ids now live again (``into`` gets each as it goes — see
        ``adopt.at_boot``). Never raises."""
        from . import adopt as ADOPT

        try:
            return await ADOPT.adopt_all(self, into)
        except Exception:  # noqa: BLE001 — never block boot
            log.warning("could not carry on the Code-view sessions kept across the restart", exc_info=True)
            return []

    async def _row_with_saved_id(self, sid: str) -> Any:
        """v1.417.0 review: the row once the tool's session id it reported is
        saved. A reply's end waits at most :data:`ID_SAVE_WAIT_S` (2 s) for
        that save, so a slower one (a busy database) can still be running
        when "Open in terminal" or "Continue on the next account" is pressed
        right after — they wait for it here (bounded), then read the row
        again: never "no conversation yet" for a conversation that has one
        (the v1.414.4 bug)."""
        live = self.live.get(sid)
        pending = live.id_save if live is not None else None
        if pending is not None and not pending.done():
            try:
                await asyncio.wait_for(asyncio.shield(pending), ID_SAVE_LATE_S)
            except Exception:  # noqa: BLE001 — the row is read as it stands
                log.debug("a Code-view session's id was still being saved", exc_info=True)
        return await self._row(sid)

    # -- terminals -----------------------------------------------------------------
    async def open_terminal(self, sid: str) -> dict[str, Any]:
        row = await self._row(sid)
        live = self.live.get(sid)
        if live and live.turn and live.turn.running:
            raise Refused(W.RUNNING)
        if live and live.claim:
            # v1.426.0 (Wave 6, E3): a second press while the first is still
            # opening the terminal (a double click) is refused in words.
            raise Refused(W.CLAIMED.format(what=live.claim))
        if live and live.running and live.keeps_working(self.clock()):
            raise Refused(W.BACKGROUND_OPEN_TERMINAL.format(
                label=W.label(row.harness), tasks=W.background_count(live.background_tasks)))
        # v1.426.0 (Wave 6, E3): the session is HELD while it is handed to a
        # terminal (no turn, no second press, no move): ONE terminal per
        # conversation.
        live = self._live(row)
        self._claim(live, W.CLAIM_TERMINAL)
        try:
            row = await self._row_with_saved_id(sid)  # v1.417.0 review
            hid = row.harness_session_id
            if not hid:
                raise Refused(W.NO_SESSION_YET)
            if row.harness == CLAUDE and await asyncio.to_thread(BR.pending_fork, self.home, sid):
                # v1.441.0: the branch is made with its first message here.
                raise Refused(BR.PENDING_TERMINAL.format(label=W.label(row.harness)))
            if row.harness == CLAUDE and await asyncio.to_thread(BR.pending_rewind, self.home, sid):
                # v1.441.0 review: a plain ``claude --resume`` would open the
                # conversation as it was before the rewind (re-measured).
                raise Refused(BR.PENDING_REWIND_TERMINAL.format(label=W.label(row.harness)))
            if not typable_sid(row.harness, hid):
                raise Refused("That session's id is not one Iron Jarvis types into a terminal.", 409)
            # v1.426.0 (Wave 6, E3): pressed again after it opened (or the
            # conversation runs in another Build terminal): a second terminal
            # would be a second writer on one conversation.
            if await asyncio.to_thread(self._in_a_terminal, row):
                raise Refused(W.ALREADY_IN_TERMINAL.format(
                    label=W.label(row.harness), exit=W.exit_command(row.harness)))
            # v1.422.0 review (B4): what the tool ran that serves (a dev server)
            # stops with it — read first, said after (never silent).
            ended = PG.known_programs((await PG.read_many([live])).get(live.sid), live) if live.running else []
            await self._stop_process(live)  # two processes on one session corrupt it
            if ended:
                self._said_stopped(live, ended, why="terminal")
            session = await asyncio.to_thread(self._launch_terminal, row, hid)
            # The row is now the terminal's until that terminal's CLI has exited.
            await asyncio.to_thread(store.update, self.engine, sid, terminal_id=str(session.id))
        finally:
            if live.claim == W.CLAIM_TERMINAL:
                live.claim = ""
        # v1.418.0 (C4.6): what the user does in that terminal shows here later.
        await asyncio.to_thread(HIST.mark_outside, self.home, sid, "terminal")
        return {"terminal_id": session.id}

    def _in_a_terminal(self, row: Any) -> bool:
        """BLOCKING: is ``row``'s conversation already running in a Build
        terminal: the one Open in terminal started for it (its CLI not yet
        exited), or another one that holds it (:func:`terminal_holds`)?"""
        if self._opened_terminal_runs(row):
            return True
        return terminal_holds(self.platform, row.harness_session_id, harness=row.harness,
                              rows=store.list_rows(self.engine))

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
        if row.harness == OPENCODE:
            # OpenCode's own way back into a saved session: `--session <id>`
            # (its TUI, in the session's folder).
            command = f"{entry['command']} --session {hid}"
            return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                               accounts=accounts, command=command)
        if row.harness == GROK:
            # Grok's own way back into a saved session: `--resume <id>` (its TUI).
            command = f"{entry['command']} --resume {hid}"
            return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                               accounts=accounts, command=command)
        if row.harness == ANTIGRAVITY:
            # Antigravity's own way back into a saved conversation (its docs).
            command = f"{entry['command']} --conversation {hid}"
            return launch_pane(self.platform, TerminalLaunch(cli=cli, cwd=row.folder),
                               accounts=accounts, command=command)
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
        # v1.426.0 (Wave 6, E3): ONE hand-over at a time. Two presses (a
        # double click, two windows) both read "no row holds it" and each made
        # a row: two rows on one conversation, each able to start a writer.
        async with self._handover():
            harness, hid, title, acc = await asyncio.to_thread(self._pane_facts, pane)
            recorded = str(getattr(pane, "claude_session_id", None) or "")
            if harness == CLAUDE and _SID_RX.match(recorded) and recorded != hid:
                # The terminal moved to this conversation (`/clear`): it is the
                # one the pane ran last, so it is the one it records now (its
                # restart Resume, a second press and the hold read it).
                try:
                    pane.claude_session_id = hid
                except Exception:  # noqa: BLE001 - a pane that keeps no record
                    pass
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
        await asyncio.to_thread(HIST.mark_outside, self.home, row.id, "terminal")  # v1.418.0 (C4.6)
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
            raise Refused(W.NOT_A_CODING_TERMINAL)
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
        # v1.426.0 review: the home the PANE's Claude writes — a "this PC"
        # pane removed an inherited CLAUDE_CONFIG_DIR, so the daemon's own
        # (``home_of``) is ANOTHER account's home.
        home = claude_pane_home(pane) if harness == CLAUDE else home_of(harness, acc)
        if harness == CLAUDE and _SID_RX.match(hid):
            # v1.426.0 (Wave 6, E3): the RECORDED id is the conversation Iron
            # Jarvis started there; a `/clear` typed in the terminal moved
            # Claude Code to a NEW one, and handing over the recorded one
            # opened the OLDER conversation. The newest the pane wrote since
            # (``claude_later``: never a Code-view row's own conversation or
            # another terminal's) is the one it ran last; several of them is
            # never a guess.
            terminals = getattr(self.platform, "terminals", None)
            taken = {str(r.harness_session_id or "") for r in store.list_rows(self.engine)
                     if r.harness == CLAUDE}
            if terminals is not None:
                taken |= other_terminal_ids(terminals, str(getattr(pane, "id", "") or ""))
            later = claude_later(pane, home, hid, taken=taken)
            if len(later) > 1:
                raise Refused(W.CANNOT_TELL.format(label=W.label(harness)))
            if later:
                hid, title = str(later[0].get("id") or ""), str(later[0].get("title") or "")
        if not hid or not typable_sid(harness, hid):
            since = _pane_since(pane)
            found = disk_sessions(harness, home, str(pane.cwd or ""), since=since)
            if len(found) != 1:
                raise Refused(W.CANNOT_TELL.format(label=W.label(harness)))
            hid, title = str(found[0].get("id") or ""), str(found[0].get("title") or "")
        return harness, hid, clip(title, 120), acc

    # -- continue on the next account (Claude Code; v1.422.0: and Codex) -------
    async def continue_on(self, sid: str, account: str = "", *, note: str = "",
                          force: bool = False) -> dict[str, Any]:
        """Move the conversation to another account (a COPY) and run the
        session there from the next message. ``note``: the line said in the
        conversation (v1.422.0: a move made BY ITSELF within an account group
        says so, ``hosted.carry_on``). v1.422.0 (B5): Codex too — measured,
        its rollout file copied into the other ``CODEX_HOME`` resumes there
        (:mod:`.codex_move`). v1.427.3: ``force`` (with ``account``) tries
        that Claude account although Iron-Proxy has it parked for a limit —
        the user's own press; never one signed out, turned off, or signed in
        to the same Claude account as this one."""
        row = await self._row(sid)
        if row.harness not in (CLAUDE, CODEX):
            raise Refused(W.PI_CONTINUE if row.harness == PI
                          else W.OPENCODE_CONTINUE if row.harness == OPENCODE
                          else W.GROK_CONTINUE if row.harness == GROK
                          else W.ANTIGRAVITY_CONTINUE if row.harness == ANTIGRAVITY else W.CODEX_CONTINUE)
        if not row.harness_session_id:
            row = await self._row_with_saved_id(sid)  # v1.417.0 review
        if not row.harness_session_id:
            raise Refused(W.NO_SESSION_YET)
        live = self._live(row)
        self._claim(live, "moving to another account")
        try:
            limit = live.limit
            # v1.422.0 review (B4): what the tool ran that serves stops with it,
            # so it is read before the stop and said after it (both vendors).
            ended = PG.known_programs((await PG.read_many([live])).get(live.sid), live) if live.running else []
            if row.harness == CODEX:
                from . import codex_move as CM

                src_home = home_of(CODEX, store.account_record(row))
                try:
                    lease, source, client, current = await asyncio.to_thread(
                        CM.plan, self.platform, row, str(account or "").strip(), src_home,
                    )
                    # The process goes BEFORE the copy: its last write lands first.
                    await self._stop_process(live, why="moving to another account")
                    if ended:
                        self._said_stopped(live, ended, why="account")
                    await asyncio.to_thread(CM.carry, lease, source, src_home, client, current, limit,
                                            self.home / "trash")
                except CM.MoveRefused as exc:
                    raise Refused(str(exc)) from None
            else:
                plan = await asyncio.to_thread(
                    self._continue_blocking, row, str(account or "").strip(), limit, live.limit_since,
                    **({"force": True} if force else {}),
                )
                lease, source, client, current = plan
                # The process goes BEFORE the copy: its last write lands first.
                await self._stop_process(live, why="moving to another account")
                if ended:
                    self._said_stopped(live, ended, why="account")
                await asyncio.to_thread(self._carry, lease, source, client, current, limit)
            from ..terminals import pane_accounts as PA

            proxy = W.PROXY[row.harness]
            out = PA.PaneAccounts()
            out.use_lease(proxy, lease)
            rec = out.records[proxy]
            row = await asyncio.to_thread(
                store.update, self.engine, sid, account=str(lease.profile_id), account_json=json.dumps(rec),
            )
            note = note or W.CONTINUED.format(title=lease.title)
            live.limit, live.limit_since = None, ""
            live.account = str(lease.profile_id)
            self._emit(live, make("notice", text=clip(note, 500)))
        finally:
            live.claim = ""
        return {"session": self._with_live(store.view(row)), "account": lease.profile_id,
                "account_name": lease.title, "note": note}

    def _continue_blocking(
        self, row: Any, account: str, limit: Any, since: str, force: bool = False,
    ) -> tuple[Any, Path, Any, str]:
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
        mine = str(rec.get("home") or "")
        if account:
            lease = None
            for prof in CO._others(profiles, current):
                if str(prof.get("id")) != account:
                    continue
                st = states.get(prof.get("id")) if isinstance(states.get(prof.get("id")), dict) else {}
                cli = prof.get("cli") if isinstance(prof.get("cli"), dict) else {}
                home = str(cli.get("home") or "").strip()
                title = str(prof.get("title") or account)
                if home and CO.same_login_as(prof, mine):
                    # v1.427.3 (a USER BUG): it shares this account's limit.
                    raise Refused(
                        CO.SAME_LOGIN.format(title=title, current=str(rec.get("title") or current)) + ". "
                        + CO.SAME_LOGIN_FIX, code="same_login",
                    )
                if home and not CO._usable(st) and force and st.get("status") not in ("unauthenticated", "disabled"):
                    # v1.427.3: the user knows it has usage left — Iron-Proxy's
                    # park ends first (it may be Iron Jarvis's own guess).
                    try:
                        client.unpark(str(prof.get("id")))
                    except Exception as exc:  # noqa: BLE001
                        raise Refused(f'The account manager would not let "{title}" be tried again: '
                                      f"{PA._unavailable_sentence(exc)}") from None
                    st = {**st, "status": "ready", "parkedUntil": ""}
                if home and CO._usable(st):
                    pid = str(prof.get("id"))
                    lease = _acc.make_lease(PA.SPECS[CO.PROVIDER], pid, title,
                                            home, sorted(PA.PANE_OWN_KEYS[CO.PROVIDER]))
                elif st.get("status") == "unauthenticated" or _acc._account_kind(dict(st)) == "signin":
                    raise Refused(f'"{title}" needs to sign in again on {PA._CONNECTIONS} first.')
                else:
                    raise Refused(f'"{title}" cannot take over right now.')
            if lease is None:
                raise Refused("That account is not another Claude account in your accounts.", 404)
        else:
            lease = CO._own_choice(profiles, states, current, mine)
            if lease is None:
                none = CO._none_free(profiles, states, current, mine)
                raise Refused(none.sentence, code="none_free" if none.choices else "", choices=none.choices)
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


def reconcile_cut_turns(home: Any, keep: "Collection[str]" = ()) -> int:
    """BLOCKING (boot, off the loop): a turn a crash cut off (its last event
    is not a ``done``) gets its error + ``done`` (interrupted) appended to the
    session's ``events.jsonl``, so a reopened window shows it ended. Returns
    how many were closed. Never raises.

    v1.422.0 (B3): ``keep`` — sessions to leave ALONE (a host that survived
    the restart still runs them: nothing is closed, nothing recorded). For
    every other session whose newest reply a restart cut off — the crash
    case above, and a reply Iron Jarvis's own stop ended (a graceful
    shutdown's ``STOPPED_TITLE`` beside an ``interrupted`` done) — what the
    carry-on pass needs is kept beside the log (``hosted.carry_on``: the
    turn, whether a card or question was open, whether it was itself a
    carry-on, whether it stopped on an account's limit)."""
    from . import carry_on as CO

    root = Path(home) / "hosted"
    skip = CO.reconcile_keep(keep)
    closed = 0
    try:
        logs = list(root.glob("*/events.jsonl"))
    except OSError:
        return 0
    for path in logs[:2000]:
        sid = path.parent.name
        if sid in skip:
            # v1.422.0 review: a session a surviving host still runs was not
            # cut — an older undecided cut record of it goes (no later pass
            # acts on it).
            CO.forget_undecided(home, sid)
            continue
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - _RECONCILE_TAIL))
                tail = fh.read().splitlines()
            events = []
            for raw in tail:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    events.append(ev)
            facts = CO.analyse_tail(events, stopped_title=STOPPED_TITLE)
            # v1.427.2: every step the log leaves running is closed "did not
            # finish" — a cut reply's steps AND the ones the tool ran on its
            # own after its reply ended (no turn, so the check above never
            # saw them: their spinners stayed for days).
            rows = OS.close_in_tail(events)
            if rows:
                log.info("closed %d unfinished step(s) in Code-view session %s",
                         sum(1 for r in rows if r.get("type") == "tool"), sid)
            if facts is None and not rows:
                continue
            if facts is not None and not facts["closed"]:
                turn = facts["turn"]
                rows += [
                    {**W.error(STOPPED_TITLE, STOPPED_FIX, ""), "turn": turn},
                    {**make("done", stop="interrupted"), "turn": turn},
                ]
                closed += 1
            if rows:
                with open(path, "ab") as fh:
                    fh.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"))
            if facts is not None:
                CO.note_cut(home, sid, facts)
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
