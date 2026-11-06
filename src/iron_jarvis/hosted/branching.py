"""Branch from here and Rewind to here for Build › Code sessions (v1.441.0).

Every action goes through the coding tool's OWN way of doing it, and a tool
that has none offers none (the screen hides it). Files are never touched here:
the per-file Revert in the Changes pane stays the way to put a file back.

MEASURED on this PC (each without a model call unless said):

* **Claude Code 2.1.316** (a stand-in Anthropic server on 127.0.0.1 and a
  scratch ``CLAUDE_CONFIG_DIR``; no model): the ``uuid`` a stream-json user
  message is sent with IS that message's record ``uuid`` in the transcript, and
  its ``parentUuid`` is the chain entry before it. ``--resume <H>
  --fork-session --session-id <N> --resume-session-at <entry>`` starts a NEW
  conversation ``N`` holding H's messages up to and including ``entry`` (the
  stand-in received exactly those); H's file is untouched; ``N.jsonl`` is
  written only with N's first message (an ``initialize`` alone writes no file)
  and keeps the copied records' uuids; ``claude --resume N`` then loads all of
  it. ``--resume <H> --resume-session-at <entry>`` (no fork) answers from the
  truncated history and its message's record hangs off ``entry``; a plain
  ``--resume H`` afterwards loads that NEW branch (Claude Code follows the
  newest leaf), but before a message is sent it still loads the old one — so
  a rewind is kept here until the next message lands (:data:`PENDING_NAME`).
  ``rewind_files`` (dry run) answers ``canRewind: false``, "File rewinding is
  not enabled." — Iron Jarvis does not start Claude Code with file
  checkpoints, so a rewind never restores files.
* **Codex 0.157.0** (``app-server`` against a stand-in Responses server in a
  scratch ``CODEX_HOME``; no model): ``thread/fork {threadId, beforeTurnId?,
  excludeTurns}`` answers a new thread whose turns keep their ids; it is saved
  at once (``thread/unsubscribe``, a restart and ``thread/resume`` of it show
  every kept turn). The app-server that made the fork stays its WRITER even
  after ``thread/unsubscribe``: a second app-server's ``thread/resume`` of it
  was refused "already has an active writer" (found driving the real service
  end to end), so that process is stopped after a fork. ``thread/revert {threadId, beforeTurnId}`` drops that turn
  and every later one, and the drop survives a restart (the next turn's model
  input held only the kept turns). Files untouched (the schema: "Turn excluded
  from the replacement history").
* **Pi 1.3.0** (``pi --mode rpc --offline --no-mcp`` on a hand-made session
  file in a scratch ``PI_CODING_AGENT_DIR``; no model): ``get_fork_messages``
  lists the active branch's user messages ``{entryId, text}``; ``fork
  {entryId}`` writes a NEW session file (header ``parentSession``) holding the
  messages BEFORE that entry and SWITCHES the process to it; ``clone`` copies
  the whole branch into a new file and switches too; the original file is
  untouched. ``--session-id <new id>`` opens the new file.
* **OpenCode 1.18.18** (``opencode acp`` in scratch XDG folders; no model):
  ``initialize`` says ``sessionCapabilities.fork``; ``session/fork
  {sessionId, cwd, mcpServers}`` answers a new ``sessionId``. Its ACP code
  forks the WHOLE session (no message id is passed), and ACP has no rewind.
* **Grok** and **Antigravity**: their ACP ``sessionCapabilities`` have no fork
  and no rewind (``grok.py``, ``antigravity.py``): neither is offered.

Which message is which: a message is named by its ``user`` event's ``at`` (the
view sends it back; no new key, so the event contract is unchanged), and the
tool's own reference for it is written
beside the log (:data:`REFS_NAME`) right after it was sent — Claude Code's
message uuid, Codex's turn id. Pi has none: its message is found by matching
the view's sent messages against ``get_fork_messages`` (newest last), and a
list that does not match is refused in words. A message sent before this
version (or one that came from the tool's own transcript) offers no
per-message action; "Branch" from the end still works.

The log and the view agree after a rewind: the session's log is cut before the
message (the whole old log is moved to Iron Jarvis's trash first), and for a
session whose history came from the tool's transcript the merged history up
to the message is written into the log and the transcript is no longer
merged (``outside.json`` removed), so nothing that was rewound away comes back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import history as HIST
from . import store
from .events import clip, session_dir

log = logging.getLogger("ironjarvis.hosted.branching")

CLAUDE = "claude-code"
CODEX = "codex"
PI = "pi"
OPENCODE = "opencode"

#: What each tool can do (``fork`` = Branch from the end; ``fork_at`` = from a
#: message; ``rewind`` = drop the conversation from a message on).
CAPS: dict[str, dict[str, bool]] = {
    CLAUDE: {"fork": True, "fork_at": True, "rewind": True},
    CODEX: {"fork": True, "fork_at": True, "rewind": True},
    PI: {"fork": True, "fork_at": True, "rewind": True},
    OPENCODE: {"fork": True, "fork_at": False, "rewind": False},
}
NONE = {"fork": False, "fork_at": False, "rewind": False}

REFS_NAME = "refs.json"
PENDING_NAME = "branch.json"
#: Review (v1.441.0): when a rewind was made. The tool's own file can still
#: hold what it dropped (Claude Code keeps the old branch; Iron Jarvis's Codex
#: reader does not read a revert), so a transcript merged in later (the
#: conversation went outside the view again, or the log is empty) leaves out
#: every entry from before it — what was kept is in the log already.
REWOUND_NAME = "rewound.json"
#: How many sent messages' references a session keeps (the newest).
MAX_REFS = 500
#: How many per-message ids a view lists.
MAX_VIEW_IDS = 500

_LOCK = threading.Lock()

# -- words ------------------------------------------------------------------
LABELS = {CLAUDE: "Claude Code", CODEX: "Codex", PI: "Pi", OPENCODE: "OpenCode",
          "grok": "Grok", "antigravity": "Antigravity"}
NOT_OFFERED_FORK = "{label} cannot branch a conversation, so Iron Jarvis does not offer it."
NOT_OFFERED_REWIND = "{label} cannot rewind a conversation, so Iron Jarvis does not offer it."
NOTHING_YET = "There is no conversation to branch yet: send a message first."
NOTHING_TO_REWIND = "There is no conversation to rewind yet."
REPLY_RUNS = "A reply is running: wait until it ends (or stop it) first."
HELPERS_RUN = "{label} is still working in the background: wait until it ends first."
IN_TERMINAL = ("This conversation is open in a terminal: close {label} there first, so only one "
               "place writes to it.")
ARCHIVED = "This session is archived: bring it back first."
PENDING_FIRST = ("Send a message in this branch first: {label} makes the branch with your next message, "
                 "and only then can it be branched or rewound.")
GONE_MESSAGE = "That message is not in this session any more."
NO_REF = ("Iron Jarvis did not record {label}'s own reference for that message (it was sent before this "
          "version, or came from the tool's own saved history), so it cannot branch or rewind from it.")
NOT_IN_TRANSCRIPT = "{label}'s saved conversation does not hold that message, so nothing was changed."
PI_LIST_DIFFERS = ("Pi's own list of your messages does not match this view, so nothing was changed. "
                   "In Pi's terminal, /fork does it by hand.")
PI_EXTENSION_STOPPED = "One of your Pi extensions stopped the branch, so nothing was changed."
TOOL_REFUSED = "{label} refused: {said}"
NO_NEW_ID = "{label} did not say the new conversation's id, so nothing was made."
PENDING_TERMINAL = ("Send a message here first: {label} makes this branch with your next message, so the "
                    "terminal has nothing to open yet.")
#: Review: a Claude Code rewind not yet sent — a plain ``claude --resume``
#: in a terminal would open the conversation as it was BEFORE the rewind
#: (re-measured on 2.1.316: the model got the rewound-away message back).
PENDING_REWIND_TERMINAL = ("This conversation was rewound here: send a message first, so {label} goes back "
                           "with it. A terminal now would open it as it was before the rewind.")
NOT_KEPT = "Iron Jarvis could not keep a copy of this conversation's log first, so nothing was changed."
NO_ANSWER = "{label} did not answer, so Iron Jarvis stopped it. Try again."

FILES_SAME = "Your files were not changed: put a file back with Revert in the Changes pane."
FORK_MADE = "A branch of this conversation, on the same tool, model and account."
FORK_AT_NOTE = "It holds the conversation before your message; the message is in the box to edit and send."
FORK_FIRST_NOTE = "That was the first message, so the branch starts a new conversation."
FORK_WHOLE_ONLY = "{label} branches the whole conversation, so this branch holds all of it."
FORK_CLAUDE_LATER = "Claude Code makes the branch with your first message here."
REWIND_DONE = "The conversation now ends before that message, which is back in the box."
REWIND_CLAUDE_LATER = "Claude Code goes back with your next message."
REWIND_FIRST = "That was the first message, so the next one starts a new conversation."
REWIND_PI_KEPT = "Pi kept the conversation as it was as a saved session of its own."
REWIND_CODEX_KEPT = "Codex dropped those turns from its saved conversation."


def label(harness: str) -> str:
    return LABELS.get(harness, harness or "The tool")


def caps(harness: str) -> dict[str, bool]:
    return dict(CAPS.get(str(harness or ""), NONE))


# -- the sidecar files (BLOCKING) --------------------------------------------
def _path(home: Any, sid: str, name: str) -> Path:
    return session_dir(home, sid) / name


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def read_refs(home: Any, sid: str) -> dict[str, dict[str, Any]]:
    data = _read_json(_path(home, sid, REFS_NAME))
    refs = data.get("refs") if isinstance(data.get("refs"), dict) else {}
    return {str(k): v for k, v in refs.items() if isinstance(v, dict)}


def _write_refs(home: Any, sid: str, refs: dict[str, dict[str, Any]]) -> None:
    items = list(refs.items())[-MAX_REFS:]
    _write_json(_path(home, sid, REFS_NAME), {"refs": dict(items)})


def note_sent(home: Any, sid: str, uid: str, ref: str, hid: str, text: str) -> None:
    """BLOCKING, never raises: the message ``uid`` reached the tool (its own
    reference ``ref``, "" when it has none) in conversation ``hid``."""
    if not uid:
        return
    try:
        with _LOCK:
            refs = read_refs(home, sid)
            refs.pop(uid, None)
            refs[uid] = {"ref": str(ref or ""), "hid": str(hid or ""), "text": clip(text, 400)}
            _write_refs(home, sid, refs)
    except Exception:  # noqa: BLE001 — a reference never fails a message
        log.warning("could not record a Code-view message's reference", exc_info=True)


def read_pending(home: Any, sid: str) -> dict[str, Any]:
    return _read_json(_path(home, sid, PENDING_NAME))


def write_pending(home: Any, sid: str, pending: dict[str, Any] | None) -> None:
    path = _path(home, sid, PENDING_NAME)
    if not pending:
        try:
            path.unlink()
        except OSError:
            pass
        return
    _write_json(path, pending)


def pending_fork(home: Any, sid: str) -> bool:
    return bool(read_pending(home, sid).get("from"))


def pending_rewind(home: Any, sid: str) -> bool:
    """Review: a Claude Code rewind waiting for its next message."""
    p = read_pending(home, sid)
    return bool(p.get("at")) and not p.get("from")


def note_rewound(home: Any, sid: str, at: str | None = None) -> None:
    _write_json(_path(home, sid, REWOUND_NAME),
                {"at": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")})


def rewound_at(home: Any, sid: str) -> str:
    """BLOCKING (one small read): when this session was last rewound ("")."""
    return str(_read_json(_path(home, sid, REWOUND_NAME)).get("at") or "")


def without_rewound(home: Any, sid: str, imported: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BLOCKING: the tool's transcript entries, minus every one from before
    the session's last rewind (those the rewind kept are in the log)."""
    cut = HIST._parse_at(rewound_at(home, sid))
    if cut is None or not imported:
        return imported
    out = []
    for ev in imported:
        at = HIST._parse_at(ev.get("at"))
        if at is not None and at <= cut:
            continue
        out.append(ev)
    return out


# -- around a message ----------------------------------------------------------
def ref_before(driver: Any) -> str:
    """What the driver's own reference reads BEFORE a message is sent (Codex:
    the turn id the previous turn left)."""
    return str(getattr(driver, "turn_id", "") or "") if _harness_of(driver) == CODEX else ""


def ref_after(driver: Any, before: str) -> str:
    """The tool's own reference for the message just sent: Claude Code's
    message uuid; Codex's new turn id (only when it CHANGED — a compaction's
    turn id comes later, and the old one would name the wrong turn)."""
    h = _harness_of(driver)
    if h == CLAUDE:
        return str(getattr(driver, "_turn_mid", "") or "")
    if h == CODEX:
        now = str(getattr(driver, "turn_id", "") or "")
        return now if now and now != before else ""
    return ""


def _harness_of(driver: Any) -> str:
    from .claude import ClaudeDriver
    from .codex import CodexDriver

    if isinstance(driver, ClaudeDriver):
        return CLAUDE
    if isinstance(driver, CodexDriver):
        return CODEX
    return ""


def prepare_driver(home: Any, row: Any, driver: Any) -> None:
    """BLOCKING, before a Claude Code process starts: a branch made here, or a
    rewind not yet sent, rides its command line (``ClaudeDriver.argv``)."""
    if str(getattr(row, "harness", "")) != CLAUDE:
        return
    pending = read_pending(home, row.id)
    driver.fork_from = str(pending.get("from") or "")
    driver.resume_at = str(pending.get("at") or "")


def settle_pending(home: Any, row: Any, mid: str) -> bool:
    """BLOCKING, after a Claude Code reply ended: once its message is in the
    conversation's file the branch (or rewind) is made, and the command line
    goes back to a plain ``--resume``. True = settled."""
    pending = read_pending(home, row.id)
    if not pending or not mid:
        return False
    hid = str(row.harness_session_id or "")
    from .service import home_of

    path = HIST.transcript_source(CLAUDE, home_of(CLAUDE, store.account_record(row)), row.folder or "", hid)
    if path is None:
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if json.dumps(mid).encode() not in data:
        return False
    write_pending(home, row.id, None)
    return True


# -- the view ---------------------------------------------------------------
def view_block(home: Any, row: Any) -> dict[str, Any]:
    """BLOCKING: what the view offers (``branch`` of ``GET …/sessions/{id}``):
    ``fork`` / ``fork_at`` / ``rewind`` and ``at``, the messages that can be
    branched or rewound from."""
    c = caps(row.harness)
    hid = str(row.harness_session_id or "")
    pending = read_pending(home, row.id) if row.harness == CLAUDE else {}
    held = bool(pending.get("from"))
    out: dict[str, Any] = {
        "fork": c["fork"] and bool(hid) and not held,
        "fork_at": c["fork_at"] and not held,
        "rewind": c["rewind"] and bool(hid) and not held,
        "at": [],
    }
    if held:
        out["note"] = PENDING_FIRST.format(label=label(row.harness))
    if hid and not held and (c["fork_at"] or c["rewind"]):
        refs = read_refs(home, row.id)
        ids = [uid for uid, r in refs.items() if r.get("hid") == hid and (r.get("ref") or row.harness == PI)]
        out["at"] = ids[-MAX_VIEW_IDS:]
    return out


# -- the log --------------------------------------------------------------------
def _log_events(home: Any, sid: str) -> list[dict[str, Any]]:
    return HIST._read_events(session_dir(home, sid) / "events.jsonl")


def _index_of(events: list[dict[str, Any]], uid: str) -> int:
    for i, ev in enumerate(events):
        if ev.get("type") == "user" and ev.get("at") == uid and not ev.get("carry") and not ev.get("imported"):
            return i
    return -1


def _write_events(home: Any, sid: str, events: list[dict[str, Any]], dropped: int) -> None:
    d = session_dir(home, sid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"events.jsonl.{uuid.uuid4().hex[:8]}.tmp"
    with open(tmp, "wb") as fh:
        fh.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events).encode("utf-8"))
    os.replace(tmp, d / "events.jsonl")
    meta = _read_json(d / "meta.json")
    meta["dropped"] = int(dropped)
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _trash_log(home: Any, sid: str) -> str:
    """BLOCKING: a copy of the whole log (and its references) in Iron Jarvis's
    own trash before a rewind cuts it — never deleted."""
    src = session_dir(home, sid)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dest = Path(home) / "trash" / stamp / "hosted-rewind" / sid
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for name in ("events.jsonl", "meta.json", REFS_NAME):
            if (src / name).is_file():
                shutil.copy2(src / name, dest / name)
        return str(dest)
    except OSError:
        log.warning("could not keep a Code-view session's log before a rewind", exc_info=True)
        return ""


async def _history(svc: Any, row: Any, live: Any) -> tuple[list[dict[str, Any]], int, bool]:
    """The session's whole history as its view shows it (the log, and for a
    conversation that lived outside the view the tool's own transcript merged
    in): ``(events, dropped, merged)``."""
    if live is not None:
        await asyncio.to_thread(live.log.flush)
    events = await asyncio.to_thread(_log_events, svc.home, row.id)
    dropped = HIST._meta_dropped(session_dir(svc.home, row.id) / "meta.json")
    merged = await svc._with_transcript(row, live, events, cut=False)
    return merged, dropped, len(merged) != len(events)


# -- the checks -------------------------------------------------------------------
async def _checks(svc: Any, sid: str, *, rewind: bool) -> tuple[Any, Any]:
    from .service import Refused

    row = await svc._row(sid)
    lab = label(row.harness)
    c = caps(row.harness)
    if rewind and not c["rewind"]:
        raise Refused(NOT_OFFERED_REWIND.format(label=lab), 409, code="not_offered")
    if not rewind and not c["fork"]:
        raise Refused(NOT_OFFERED_FORK.format(label=lab), 409, code="not_offered")
    if getattr(row, "archived_at", None):
        raise Refused(ARCHIVED)
    if not row.harness_session_id:
        raise Refused(NOTHING_TO_REWIND if rewind else NOTHING_YET)
    live = svc.live.get(sid)
    if live is not None:
        if live.turn is not None and live.turn.running:
            raise Refused(REPLY_RUNS)
        if live.running and live.keeps_working(svc.clock()):
            raise Refused(HELPERS_RUN.format(label=lab))
    if row.harness == CLAUDE and await asyncio.to_thread(pending_fork, svc.home, row.id):
        raise Refused(PENDING_FIRST.format(label=lab))
    if await asyncio.to_thread(svc._in_a_terminal, row):
        raise Refused(IN_TERMINAL.format(label=lab))
    return row, live


def _ref_for(svc: Any, row: Any, uid: str) -> str:
    """BLOCKING: the tool's reference for message ``uid`` in this conversation."""
    from .service import Refused

    r = read_refs(svc.home, row.id).get(uid)
    if not r or r.get("hid") != row.harness_session_id or not r.get("ref"):
        raise Refused(NO_REF.format(label=label(row.harness)))
    return str(r["ref"])


def _claude_parent(svc: Any, row: Any, mid: str) -> tuple[bool, str]:
    """BLOCKING: ``(found, parent)`` — the chain entry before Claude Code's
    record ``mid`` ("" = it was the first)."""
    from .service import home_of

    path = HIST.transcript_source(CLAUDE, home_of(CLAUDE, store.account_record(row)), row.folder or "",
                                  row.harness_session_id)
    if path is None:
        return False, ""
    try:
        with open(path, "rb") as fh:
            for line in fh:
                if b'"uuid"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("uuid") == mid:
                    return True, str(rec.get("parentUuid") or "")
    except OSError:
        return False, ""
    return False, ""


def _pi_sent(svc: Any, row: Any) -> list[tuple[str, str]]:
    """BLOCKING: the view's messages sent to this Pi conversation, oldest first
    ``(id, text)``."""
    refs = read_refs(svc.home, row.id)
    return [(uid, str(r.get("text") or "")) for uid, r in refs.items() if r.get("hid") == row.harness_session_id]


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


async def _pi_entry(svc: Any, row: Any, driver: Any, uid: str) -> str:
    """Pi's entry id for message ``uid``: the view's sent messages matched
    against Pi's own list, newest last (refused in words when they differ)."""
    from .service import Refused

    sent = await asyncio.to_thread(_pi_sent, svc, row)
    ids = [s[0] for s in sent]
    if uid not in ids:
        raise Refused(NO_REF.format(label=label(PI)))
    got = await driver.request("get_fork_messages")
    msgs = [m for m in (got or {}).get("messages") or [] if isinstance(m, dict) and m.get("entryId")]
    if len(msgs) < len(sent):
        raise Refused(PI_LIST_DIFFERS)
    tail = msgs[len(msgs) - len(sent):]
    for (_, text), m in zip(sent, tail):
        a, b = _norm(text), _norm(str(m.get("text") or ""))
        if not (a == b or (len(a) >= 400 and b.startswith(a[:390]))):
            raise Refused(PI_LIST_DIFFERS)
    return str(tail[ids.index(uid)]["entryId"])


async def _running_driver(svc: Any, row: Any, live: Any) -> Any:
    """The session's tool, started if it is not running (no model call: a
    start only opens the saved conversation)."""
    from .process import HarnessGone

    live = svc._live(row)
    async with live.lock:
        await svc._ensure_running(live, row)
    if live.driver is None:
        raise HarnessGone("the coding tool stopped")
    return live, live.driver


async def _programs_of(live: Any) -> list[Any]:
    """Review (v1.422.0 B4): what a stop of ``live`` ends that serves (a dev
    server), read before the stop so it can be said after."""
    from . import programs as PG

    if live is None or not getattr(live, "running", False) or not hasattr(live, "sid"):
        return []
    try:
        return PG.known_programs((await PG.read_many([live])).get(live.sid), live)
    except Exception:  # noqa: BLE001 — a read never stops a branch
        log.debug("could not read a Code-view session's programs", exc_info=True)
        return []


async def _keep_log_first(svc: Any, row: Any) -> None:
    """Review: the whole old log is in Iron Jarvis's trash BEFORE anything is
    changed — when it cannot be, nothing is."""
    from .service import Refused

    if not await asyncio.to_thread(_trash_log, svc.home, row.id):
        raise Refused(NOT_KEPT)


def _said(exc: BaseException) -> str:
    return clip(exc.args[0] if exc.args else type(exc).__name__, 300)


# -- Branch from here ---------------------------------------------------------------
async def fork(svc: Any, sid: str, at: str = "") -> dict[str, Any]:
    """POST …/fork: a NEW session in the same folder, on the same tool, model,
    mode and account, holding the conversation before message ``at`` (its
    words come back for the box) or all of it. The original stays as it was."""
    from .driver import ControlRefused
    from .process import HarnessGone
    from .service import Refused

    at = str(at or "").strip()
    row, live = await _checks(svc, sid, rewind=False)
    lab = label(row.harness)
    c = caps(row.harness)
    whole_only = bool(at) and not c["fork_at"]
    if whole_only:
        at = ""
    history, dropped, merged = await _history(svc, row, live)
    text, files = "", []
    if at:
        idx = _index_of(history, at)
        if idx < 0:
            raise Refused(GONE_MESSAGE, 404)
        kept = history[:idx]
        text = str(history[idx].get("text") or "")
        files = [str(f) for f in history[idx].get("files") or [] if isinstance(f, str)]
    else:
        kept = list(history)
    live = live or svc._live(row)
    svc._claim(live, "being branched")
    notes: list[str] = [FORK_MADE]
    pending: dict[str, Any] | None = None
    first = False
    try:
        new_hid = ""
        if row.harness == CLAUDE:
            if at:
                mid = await asyncio.to_thread(_ref_for, svc, row, at)
                found, parent = await asyncio.to_thread(_claude_parent, svc, row, mid)
                if not found:
                    raise Refused(NOT_IN_TRANSCRIPT.format(label=lab))
                if parent:
                    new_hid = str(uuid.uuid4())
                    pending = {"from": row.harness_session_id, "at": parent, "fork": True}
                else:
                    first = True
                    notes.append(FORK_FIRST_NOTE)
            else:
                # A rewind not yet sent: the conversation on screen ends at its
                # entry, so the branch does too (Claude Code's newest leaf is
                # still the old one until the next message).
                held = await asyncio.to_thread(read_pending, svc.home, row.id)
                new_hid = str(uuid.uuid4())
                pending = {"from": row.harness_session_id, "at": str(held.get("at") or ""), "fork": True}
            if pending:
                notes.append(FORK_CLAUDE_LATER)
        else:
            live2, drv = await _running_driver(svc, row, live)
            asked = False  # a forking request went out: the process is stopped after
            ended: list[Any] = []
            try:
                if row.harness in (CODEX, PI):
                    ended = await _programs_of(live2)
                if row.harness == CODEX:
                    params: dict[str, Any] = {"threadId": row.harness_session_id, "excludeTurns": True}
                    if row.folder:
                        params["cwd"] = row.folder
                    if at:
                        params["beforeTurnId"] = await asyncio.to_thread(_ref_for, svc, row, at)
                    asked = True
                    res = await drv.request("thread/fork", params)
                    thread = (res or {}).get("thread") if isinstance(res, dict) else None
                    new_hid = str((thread or {}).get("id") or "")
                    if new_hid:
                        try:
                            await drv.request("thread/unsubscribe", {"threadId": new_hid})
                        except Exception:  # noqa: BLE001 — the fork is saved either way
                            log.debug("Codex did not unsubscribe a fork", exc_info=True)
                        # MEASURED (codex 0.157.0, two app-servers on one
                        # CODEX_HOME): even unsubscribed, the process that made
                        # the fork stays its WRITER, and the branch's own start
                        # was refused ("thread … already has an active
                        # writer"). It goes (below); the original reopens with
                        # its next message.
                elif row.harness == PI:
                    if at:
                        entry = await _pi_entry(svc, row, drv, at)
                        asked = True
                        res = await drv.request("fork", entryId=entry)
                    else:
                        asked = True
                        res = await drv.request("clone")
                    if isinstance(res, dict) and res.get("cancelled"):
                        raise Refused(PI_EXTENSION_STOPPED)
                    state = await drv.request("get_state")
                    new_hid = str((state or {}).get("sessionId") or "") if isinstance(state, dict) else ""
                    if new_hid == row.harness_session_id:
                        new_hid = ""
                    # The process now runs the NEW session: it goes (below), and
                    # the next message here starts on the original again.
                elif row.harness == OPENCODE:
                    drv._loading = True  # the new session's own updates are not this one's
                    try:
                        res = await drv.request("session/fork", {
                            "sessionId": row.harness_session_id, "cwd": row.folder or "",
                            "mcpServers": list(getattr(drv, "mcp_servers", []) or []),
                        })
                        new_hid = str((res or {}).get("sessionId") or "") if isinstance(res, dict) else ""
                        if new_hid:
                            try:
                                await drv.request("session/close", {"sessionId": new_hid})
                            except Exception:  # noqa: BLE001 — the fork is saved either way
                                log.debug("OpenCode did not close a fork", exc_info=True)
                    finally:
                        drv._loading = False
            except ControlRefused as exc:
                raise Refused(TOOL_REFUSED.format(label=lab, said=_said(exc))) from None
            except (TimeoutError, HarnessGone):
                raise Refused(NO_ANSWER.format(label=lab)) from None
            finally:
                if asked:
                    # Review: ALWAYS once a fork went out — Pi switched its
                    # process to the fork (a failure after it used to leave
                    # the next message going into the wrong conversation), and
                    # Codex stays the fork's writer.
                    await svc._stop_process(live2, why="branched")
                    if ended:
                        svc._said_stopped(live2, ended, why="branch")
            if not new_hid:
                raise Refused(NO_NEW_ID.format(label=lab))
        if whole_only:
            notes.append(FORK_WHOLE_ONLY.format(label=lab))
        elif at and not first:
            notes.append(FORK_AT_NOTE)
        made = await asyncio.to_thread(_make_row, svc, row, new_hid, kept, dropped, pending, at)
    finally:
        live.claim = ""
    view = svc._with_live(store.view(made))
    return {"session": svc._with_root(view), "text": text, "files": files, "note": " ".join(notes),
            "from_end": not at}


def _make_row(svc: Any, row: Any, new_hid: str, kept: list[dict[str, Any]], dropped: int,
              pending: dict[str, Any] | None, at: str) -> Any:
    """BLOCKING: the branch's row, its log (the history it holds), its
    references and (Claude Code) what its first start does."""
    title = clip(f"{row.title or 'Session'} (branch)", 120)
    made = store.create(
        svc.engine, harness=row.harness, folder=row.folder or "", title=title,
        account=row.account or "", account_json=row.account_json or "", model=row.model or "",
        effort=row.effort or "", mode=row.mode or "ask", harness_session_id=new_hid,
        no_folder=bool(getattr(row, "no_folder", False)),
        capabilities_json=getattr(row, "capabilities_json", "") or "",
        load_folder_resources=bool(getattr(row, "load_folder_resources", False)),
    )
    _write_events(svc.home, made.id, [_stripped(e) for e in kept], dropped)
    if new_hid:
        ids = {e.get("at") for e in kept if e.get("type") == "user" and e.get("at")}
        with _LOCK:
            refs = {uid: {**r, "hid": new_hid} for uid, r in read_refs(svc.home, row.id).items()
                    if uid in ids and r.get("hid") == row.harness_session_id}
            _write_refs(svc.home, made.id, refs)
    write_pending(svc.home, made.id, pending)
    when = rewound_at(svc.home, row.id)
    if when:
        # Review: the branch's tool file may hold what the original's rewind
        # dropped (a Codex fork copies its rollout): never merged in either.
        note_rewound(svc.home, made.id, when)
    return made


def _stripped(ev: dict[str, Any]) -> dict[str, Any]:
    """An event as a new log holds it: a turn's id and the log's own ``seq``
    belong to the session they were written in."""
    return {k: v for k, v in ev.items() if k != "seq"}


# -- Rewind to here ------------------------------------------------------------
async def rewind(svc: Any, sid: str, to: str) -> dict[str, Any]:
    """POST …/rewind: the conversation drops message ``to`` and everything
    after it, through the tool's own way; files are not touched. Its words
    come back for the box."""
    from .driver import ControlRefused
    from .process import HarnessGone
    from .service import Refused

    to = str(to or "").strip()
    if not to:
        raise Refused("Name the message to rewind to.", 400)
    row, live = await _checks(svc, sid, rewind=True)
    lab = label(row.harness)
    history, dropped, merged = await _history(svc, row, live)
    idx = _index_of(history, to)
    if idx < 0:
        raise Refused(GONE_MESSAGE, 404)
    kept = history[:idx]
    text = str(history[idx].get("text") or "")
    files = [str(f) for f in history[idx].get("files") or [] if isinstance(f, str)]
    live = live or svc._live(row)
    svc._claim(live, "being rewound")
    notes = [REWIND_DONE]
    ended: list[Any] = []
    try:
        new_hid = row.harness_session_id
        pending: dict[str, Any] | None = None
        if row.harness == CLAUDE:
            mid = await asyncio.to_thread(_ref_for, svc, row, to)
            found, parent = await asyncio.to_thread(_claude_parent, svc, row, mid)
            if not found:
                raise Refused(NOT_IN_TRANSCRIPT.format(label=lab))
            # Review: the old log is kept BEFORE anything changes.
            await _keep_log_first(svc, row)
            if live.running:
                ended = await _programs_of(live)
                await svc._stop_process(live, why="rewound")
            if parent:
                pending = {"from": "", "at": parent, "fork": False}
                notes.append(REWIND_CLAUDE_LATER)
            else:
                new_hid = ""
                notes.append(REWIND_FIRST)
        else:
            live2, drv = await _running_driver(svc, row, live)
            ended = await _programs_of(live2)
            asked = False  # a request that changes the conversation went out
            try:
                if row.harness == CODEX:
                    turn = await asyncio.to_thread(_ref_for, svc, row, to)
                    # Review: the old log is kept BEFORE the tool drops anything.
                    await _keep_log_first(svc, row)
                    asked = True
                    await drv.request("thread/revert", {"threadId": row.harness_session_id, "beforeTurnId": turn})
                    notes.append(REWIND_CODEX_KEPT)
                elif row.harness == PI:
                    entry = await _pi_entry(svc, row, drv, to)
                    await _keep_log_first(svc, row)
                    asked = True
                    res = await drv.request("fork", entryId=entry)
                    if isinstance(res, dict) and res.get("cancelled"):
                        raise Refused(PI_EXTENSION_STOPPED)
                    state = await drv.request("get_state")
                    got = str((state or {}).get("sessionId") or "") if isinstance(state, dict) else ""
                    if not got or got == row.harness_session_id:
                        raise Refused(NO_NEW_ID.format(label=lab))
                    new_hid = got
                    notes.append(REWIND_PI_KEPT)
            except ControlRefused as exc:
                raise Refused(TOOL_REFUSED.format(label=lab, said=_said(exc))) from None
            except (TimeoutError, HarnessGone):
                raise Refused(NO_ANSWER.format(label=lab)) from None
            finally:
                # Review: ALWAYS once it acted — Pi switched to its fork even
                # when reading the new id failed afterwards.
                if asked:
                    await svc._stop_process(live2, why="rewound")
                else:
                    ended = []
        notes.append(FILES_SAME)
        await asyncio.to_thread(_cut_log, svc, row, live, kept, dropped, merged, new_hid, pending)
        if new_hid != row.harness_session_id:
            await asyncio.to_thread(store.update, svc.engine, row.id, harness_session_id=new_hid)
    finally:
        live.claim = ""
    # A fresh LiveSession (its log read again) for the cut log.
    svc.live.pop(row.id, None)
    made = await asyncio.to_thread(store.get, svc.engine, row.id)
    if ended:
        # Review (the v1.422.0 B4 rule): a stop that ended programs the tool
        # served is said, in the conversation as it now is.
        svc._said_stopped(svc._live(made), ended, why="rewind")
    view = svc._with_live(store.view(made))
    return {"session": svc._with_root(view), "text": text, "files": files, "note": " ".join(notes)}


def _cut_log(svc: Any, row: Any, live: Any, kept: list[dict[str, Any]], dropped: int, merged: bool,
             new_hid: str, pending: dict[str, Any] | None) -> None:
    """BLOCKING: the session's log is ``kept`` (the old one in the trash
    first); its references keep only kept messages; a transcript that was
    merged is no longer (``kept`` holds it already)."""
    try:
        live.log.close()
    except Exception:  # noqa: BLE001
        log.debug("could not close a Code-view session's log", exc_info=True)
    # (The whole old log went to the trash first: _keep_log_first.)
    _write_events(svc.home, row.id, list(kept), dropped)
    note_rewound(svc.home, row.id)
    ids = {e.get("at") for e in kept if e.get("type") == "user" and e.get("at")}
    with _LOCK:
        refs = {uid: ({**r, "hid": new_hid} if r.get("hid") == row.harness_session_id else r)
                for uid, r in read_refs(svc.home, row.id).items() if uid in ids}
        _write_refs(svc.home, row.id, refs)
    write_pending(svc.home, row.id, pending)
    # ``kept`` holds the merged transcript already, and the tool's own file
    # may still hold what was rewound away (Claude Code keeps the old branch
    # until the next message): it is not merged in again.
    try:
        (session_dir(svc.home, row.id) / "outside.json").unlink()
    except OSError:
        pass
