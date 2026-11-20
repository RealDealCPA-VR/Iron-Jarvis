"""ONE server-side writer for chat lines that arrive while nobody is looking
(v1.345.0, "an @ reply comes home").

THE BUG this exists for (the user's install, 2026-10-16): in a NEW unsaved chat
the user typed ``@hermes-mac-mini do some tax research...`` — a remote round
that took six minutes — then opened another saved chat. When the round
answered, the page's ``sendPanel`` continuation did ``setMessages([@, reply])``
+ ``queueSave`` over WHATEVER CHAT WAS OPEN: the saved chat's two messages were
overwritten on disk and the reply never appeared in the chat it was asked in.

So the daemon now APPENDS such lines itself — a panel round's replies
(``POST /chat/panel`` with ``thread_id``), a remote agent reporting back into a
chat-bound room (``POST /agents/remote/{name}/inbound``), and an agent
session's final line (``options.chat_thread_id``) — through this one function,
and the page RE-READS instead of composing. The page's ``PUT /chat/threads/{id}``
with a stale ``if_updated_at`` then correctly 409s (the v1.232.0 two-window
rule), which is the signal it re-reads on.

Rules, each pinned in ``tests/test_chat_append_v1345.py``:

* the read-modify-write runs under :data:`~iron_jarvis.core.db.CONVERSATION_WRITE_LOCK`
  — the SAME process-wide lock the PUT route and the comm store take, so an
  append and a whole-array autosave can never interleave (a private lock here
  would guard against nothing the PUT does);
* every line is WHITELISTED to the keys the page's own ``ChatMessage`` writes
  (:data:`LINE_KEYS`) — unknown keys are dropped, and ``at`` is stamped by the
  server exactly as the PUT route stamps (aware ISO, ``+00:00``);
* a line whose ``sessionId`` / ``fromSession`` already names a session that
  has a line in the thread is SKIPPED (idempotent — a session's terminal
  path and a page that still composes must not make two replies); so is a
  line whose ``mediaJob`` already has one (v1.347.0 — a job's own finish
  and the turn-link route may both try to deliver the same result); so is a
  REPLY for a turn (``turnId``) the chat already holds a reply for, at the
  top level or inside a fork (third plan G6: the daemon writes every reply
  home, ``durable_turns.write_home``, and a reply is in a chat once);
* ``updated_at`` moves the way the PUT route moves it (strictly monotonic,
  the v1.326.0 tick rule) and ``archived_at`` is cleared (a reply un-archives,
  like ``comm/threads.append``);
* ``CHAT_THREAD_UPDATED {thread_id, who, entries}`` is published only with a
  bus — scheduled on the running loop, or run under ``asyncio.run`` from a
  worker thread (the detections-bell pattern; the bus delivers thread-safely);
* a missing thread answers ``{"ok": False, "why": "no such chat"}``; a
  DAEMON-OWNED (phone) thread is refused too — ``comm/threads.py`` is its one
  writer; this function NEVER raises.

The DB write runs where the caller is: a worker thread calls it directly, a
coroutine on the loop wraps it in ``asyncio.to_thread`` (the v1.153.1 rule).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import ChatThreadRecord

log = get_logger(__name__)

#: The keys a chat line may carry — the page's own ``ChatMessage`` shape for a
#: panel reply (``sendPanel``), a remote's mirrored line (``mirrorRoomInbound``)
#: and an agent session's reply (``finalize``). Anything else is dropped.
LINE_KEYS: tuple[str, ...] = (
    "role",
    "content",
    "panelWho",
    "panelThreadId",
    "panelKind",
    "panelError",
    "panelNote",
    "panelAt",
    "documents",
    "agentType",
    "sessionId",
    "fromSession",
    # v1.347.0: a media job's line names its job, so a second delivery of the
    # same result (the link route racing the job's own finish, the boot
    # reconcile) is SKIPPED like a session's second line.
    "mediaJob",
    "runResult",
    "receipt",
    "remembered",
    # Third plan G6 (CS5): the turn a reply belongs to (the name the page
    # minted, ``body.turn_id``), who saved it ("daemon": a reply the daemon
    # wrote home itself, ``durable_turns.write_home``), and whether it was
    # cut short — the keys the page's own reply for a turn carries.
    "turnId",
    "savedBy",
    "interrupted",
    # Fourth plan G28 (DT1): a reply that handed off while no window was open
    # — the daemon starts the run and writes the line the page's ``sendAgent``
    # saves: the reason (``escalated``), the specialist (``escalatedTo``), a
    # remote pick run here instead (``droppedRemote``) and the run the chat
    # waits on (``awaitingSession``); ``handOff: "not started"`` marks the
    # line of a hand-off that could not start (with its reason in
    # ``escalated``: the page's "Start it now" press hands off with it).
    "escalated",
    "escalatedTo",
    "droppedRemote",
    "awaitingSession",
    "handOff",
    # Idea 39 (39-a): a REPORT, a message that something set up from this chat
    # (a schedule, a watcher, a goal, a workflow, a night task…) wrote when it
    # ran (``agents/chat_delivery.report_to_chat``, the one report writer):
    # where it came from (``reportFrom`` {kind, name, id, href, outcome}), its
    # repeat key (``reportKey``: a restart or two finishers write it once), and
    # the day-fold (``reportFold`` "<kind>:<id>:<local date>": a second report
    # of the same record on the same day REPLACES the day's line in place,
    # ``reportCount`` says how many it holds, ``reportSeen`` keeps the keys it
    # replaced so a late repeat of one is still a repeat).
    "reportFrom",
    "reportKey",
    "reportFold",
    "reportCount",
    "reportSeen",
)

#: Idea 39 (39-a): how many replaced report keys a folded line keeps.
REPORT_SEEN_MAX = 60

#: The ``who`` on ``CHAT_THREAD_UPDATED`` when the daemon wrote a turn's reply
#: into its chat itself (third plan G6); the event also names ``turn_id``.
REPLY_WHO = "reply"

#: The PUT route keeps the newest 200 messages LIVE; so does an append. The
#: older ones move to :class:`ChatOlderMessage` (v1.499.3), never deleted.
MAX_MESSAGES = 200

# --------------------------------------------------------------------------- #
# OLDER MESSAGES (v1.499.3): a long chat's oldest messages are KEPT, not deleted
# --------------------------------------------------------------------------- #
# The PUT route and this module keep the newest MAX_MESSAGES in
# ``messages_json`` (the page's live window: what it loads, renders and sends
# back). Until v1.499.3 message 201 and older were simply DELETED from the row
# on the next save, while the compaction header told the model "the full
# transcript is still stored". They now MOVE here, in order, in the same
# transaction as the save, and history search keeps finding them
# (``SearchIndex.add_older``). ``GET /chat/threads/{id}`` says how many there
# are (``older``) and ``GET /chat/threads/{id}/older`` reads them back.


class ChatOlderMessage(SQLModel, table=True):
    """One message that left a saved chat's live window (v1.499.3).

    A plain new table (rows are only ever added, or deleted with their chat).
    Created on first use by :func:`ensure_older_table`, OUTSIDE any open
    write transaction (a CREATE inside the save's transaction would wait on
    the save's own writer slot)."""

    __tablename__ = "chatoldermessage"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(default="", index=True)
    #: Position in the chat's older messages: 0 = the very first message.
    seq: int = 0
    #: The message exactly as the chat stored it (the page's ``ChatMessage``).
    message_json: str = "{}"
    moved_at: datetime = Field(default_factory=utcnow)


_OLDER_READY: set[int] = set()
#: How many of the live window's FIRST messages are matched to find where the
#: stored window sits in what the page sent (see :func:`leaving_live_window`).
_ALIGN = 8


def ensure_older_table(engine: Any) -> bool:
    """Create the older-messages table if it is missing — once per engine,
    never inside a transaction. BLOCKING (a sync route or a worker thread
    calls it). Never raises; False when the table cannot be made."""
    key = id(engine)
    if key in _OLDER_READY:
        return True
    try:
        ChatOlderMessage.__table__.create(bind=engine, checkfirst=True)
        _OLDER_READY.add(key)
        return True
    except Exception:  # noqa: BLE001 — the save goes on; the cap then keeps as before
        log.warning("chat older-messages table unavailable", exc_info=True)
        return False


def _same(a: Any, b: Any) -> bool:
    """The same message for alignment: role and words (``at`` is restamped by
    the page until a chat is reopened, so it cannot be compared)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return (str(a.get("role") or ""), str(a.get("content") or "")) == (
        str(b.get("role") or ""), str(b.get("content") or ""),
    )


def leaving_live_window(stored: list, sent: list, keep: int = MAX_MESSAGES) -> list:
    """The messages a save of ``sent`` pushes OUT of the live window that are
    not kept yet (v1.499.3), oldest first.

    ``stored`` is the live window on disk before this save; ``sent`` the
    page's whole array. The page holds every message of the session, so after
    the first overflow ``sent`` STARTS with messages that already moved. The
    stored window's first messages are found in ``sent`` (role and words,
    :data:`_ALIGN` of them): what sits before them already moved, and what
    leaves now is the stored window's own head — taken from ``stored``, the
    copy that was on disk. When the stored window cannot be found (the page
    rewrote its start, or there was none), whatever the cap would drop is what
    leaves. Pure; never raises."""
    try:
        sent = [m for m in (sent or [])]
        drop = len(sent) - keep
        if drop <= 0:
            return []
        stored = [m for m in (stored or [])]
        if stored:
            probe = stored[:_ALIGN]
            for p in range(0, len(sent) - len(probe) + 1):
                if all(_same(sent[p + i], probe[i]) for i in range(len(probe))):
                    # sent[:p] already moved; sent[p:drop] is stored[:drop - p].
                    return list(stored[: max(0, drop - p)])
        return list(sent[:drop])
    except Exception:  # noqa: BLE001 — a fault keeps nothing rather than guessing
        log.warning("could not tell which chat messages leave the live window", exc_info=True)
        return []


def keep_older(db: Any, record: Any, moved: list, *, engine: Any = None, kind: str = "chat") -> int:
    """Add ``moved`` (oldest first) to the chat's older messages in the
    CALLER's transaction, and to history search (``add_older``). The table
    must already exist (:func:`ensure_older_table`, before the transaction).
    Returns how many were kept. RAISES when they cannot be kept: the caller
    then keeps them in the live window instead (nothing is ever dropped)."""
    rows = [m for m in (moved or []) if isinstance(m, dict)]
    if not rows:
        return 0
    try:
        from sqlalchemy import func
        from sqlmodel import select

        start = db.exec(
            select(func.count()).select_from(ChatOlderMessage).where(
                ChatOlderMessage.thread_id == record.id
            )
        ).one()
        start = int(start[0] if isinstance(start, tuple) else start or 0)
        for i, m in enumerate(rows):
            db.add(ChatOlderMessage(
                thread_id=record.id, seq=start + i, message_json=json.dumps(m),
            ))
        if str(getattr(record, "surface", "") or "") != "build":
            try:
                from ..core.db import search_index

                index = search_index(engine) if engine is not None else None
                if index is not None:
                    index.add_older(
                        record.id, kind, record.title or "", record.project_id or "",
                        rows, first=start, db=db,
                    )
            except Exception:  # noqa: BLE001 — search never costs a message
                log.warning("history-search could not add older messages of %s", record.id,
                            exc_info=True)
        return len(rows)
    except Exception:  # noqa: BLE001
        log.warning("could not keep older messages of chat %s", getattr(record, "id", "?"),
                    exc_info=True)
        raise


def older_count(engine: Any, thread_id: str) -> int:
    """How many older messages a chat keeps. BLOCKING. 0 on any fault."""
    try:
        from sqlalchemy import func
        from sqlmodel import select

        if not ensure_older_table(engine):
            return 0
        with session_scope(engine) as db:
            n = db.exec(
                select(func.count()).select_from(ChatOlderMessage).where(
                    ChatOlderMessage.thread_id == thread_id
                )
            ).one()
        return int(n[0] if isinstance(n, tuple) else n or 0)
    except Exception:  # noqa: BLE001
        return 0


def read_older(engine: Any, thread_id: str, *, offset: int = 0, limit: int = 200) -> list[dict]:
    """A page of a chat's older messages, oldest first. BLOCKING."""
    from sqlmodel import select

    if not ensure_older_table(engine):
        return []
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    with session_scope(engine) as db:
        rows = db.exec(
            select(ChatOlderMessage)
            .where(ChatOlderMessage.thread_id == thread_id)
            .order_by(ChatOlderMessage.seq)
            .offset(offset)
            .limit(limit)
        ).all()
        out: list[dict] = []
        for r in rows:
            try:
                m = json.loads(r.message_json or "{}")
            except Exception:  # noqa: BLE001 — a corrupt cell is skipped, not a 500
                continue
            if isinstance(m, dict):
                out.append(m)
    return out


def drop_older(db: Any, thread_id: str) -> int:
    """Delete a chat's older messages in the caller's transaction (the chat
    is being deleted: "delete" means all of it). Never raises."""
    try:
        from sqlalchemy import delete

        res = db.exec(delete(ChatOlderMessage).where(ChatOlderMessage.thread_id == thread_id))
        return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 — no table yet: nothing to delete
        return 0


#: Serialises this append with the PUT route's whole-array rewrite and the comm
#: store's appends — ONE process-wide lock (see ``core.db.CONVERSATION_WRITE_LOCK``
#: for why three locks around three transactions deadlock on SQLite).
_APPEND_LOCK = CONVERSATION_WRITE_LOCK


def _session_of(line: dict[str, Any]) -> str:
    """The session a line belongs to, by either key the page knows."""
    return str(line.get("sessionId") or line.get("fromSession") or "").strip()


def _line_key(line: dict[str, Any]) -> str:
    """What makes a line a REPEAT: its session (either key), else its media
    job (v1.347.0) — ``media:<id>`` so a job id can never collide with a
    session id."""
    sid = _session_of(line)
    if sid:
        return sid
    job = str(line.get("mediaJob") or "").strip()
    if job:
        return f"media:{job}"
    report = str(line.get("reportKey") or "").strip()
    return f"report:{report}" if report else ""


def _report_keys(line: dict[str, Any]) -> set[str]:
    """Idea 39 (39-a): the ``report:<key>`` names a report line answers to:
    its own ``reportKey`` and every key a day-fold replaced (``reportSeen``)."""
    keys: set[str] = set()
    own = str(line.get("reportKey") or "").strip()
    if own:
        keys.add(f"report:{own}")
    seen = line.get("reportSeen")
    if isinstance(seen, list):
        for k in seen:
            k = str(k or "").strip()
            if k:
                keys.add(f"report:{k}")
    return keys


def _turn_of(line: Any) -> str:
    """The turn an ASSISTANT line answers (third plan G6), "" for any other
    line — a question carries its turn's name too, and is never a reply."""
    if not isinstance(line, dict) or str(line.get("role") or "") != "assistant":
        return ""
    return str(line.get("turnId") or "").strip()


def _line_keys(line: dict[str, Any]) -> set[str]:
    """Every name that makes a line a repeat: its session or media job
    (:func:`_line_key`) and, for a reply, ``turn:<id>`` (third plan G6: one
    reply per turn)."""
    keys = set()
    key = _line_key(line)
    if key:
        keys.add(key)
    # Idea 39 (39-a): a report is a repeat by its report key too (a run's
    # report carries its session id AND ``report:run:<id>``).
    keys |= _report_keys(line)
    turn = _turn_of(line)
    if turn:
        keys.add(f"turn:{turn}")
    return keys


#: How deep :func:`reply_turn_ids` follows forks inside forks.
_BRANCH_DEPTH = 8


def reply_turn_ids(msgs: Any, _depth: int = 0) -> set[str]:
    """The turns a chat's messages hold a reply for (third plan G6): every
    assistant message's ``turnId`` at the top level AND inside a message's
    ``branch`` (the page's fork after a Try again or an edited question:
    ``{"tails": [[message, ...] | null, ...]}``, where the older reply is kept
    out of the top level). Pure; never raises."""
    out: set[str] = set()
    if not isinstance(msgs, list) or _depth > _BRANCH_DEPTH:
        return out
    try:
        for m in msgs:
            if not isinstance(m, dict):
                continue
            turn = _turn_of(m)
            if turn:
                out.add(turn)
            fork = m.get("branch")
            tails = fork.get("tails") if isinstance(fork, dict) else None
            if isinstance(tails, list):
                for tail in tails:
                    out |= reply_turn_ids(tail, _depth + 1)
    except Exception:  # noqa: BLE001 — a malformed fork is "no fork"
        log.debug("could not read a chat's forks", exc_info=True)
    return out


def has_turn_reply(msgs: Any, turn_id: str) -> bool:
    """Does the chat's message list hold a reply for ``turn_id`` (at the top
    level or inside a fork)? Pure."""
    turn = str(turn_id or "").strip()
    return bool(turn) and turn in reply_turn_ids(msgs)


def one_reply_per_turn(msgs: list) -> list:
    """The messages with ONE reply per turn (third plan G6, CS2), order kept.

    When two assistant messages carry the same ``turnId``, one is kept: the
    page's own message wins over a ``savedBy: "daemon"`` one (the page's
    carries the steps, receipts and documents); between two of the same
    kind, the later. A message with no ``turnId``, every user message, and a
    line that is an agent's, a media job's or a panel's (they are matched by
    their own names, ``_line_key``) are never dropped. No ``turnId`` anywhere:
    the very same list. Pure; never raises."""
    try:
        if not isinstance(msgs, list):
            return msgs
        winner: dict[str, int] = {}
        counted: dict[str, int] = {}
        for i, m in enumerate(msgs):
            turn = _turn_of(m)
            if not turn or _line_key(m) or str(m.get("panelWho") or ""):
                continue
            counted[turn] = counted.get(turn, 0) + 1
            best = winner.get(turn)
            if best is None:
                winner[turn] = i
                continue
            mine_daemon = str(m.get("savedBy") or "") == "daemon"
            best_daemon = str(msgs[best].get("savedBy") or "") == "daemon"
            if not mine_daemon or best_daemon:
                winner[turn] = i  # the page over the daemon; else the later
        if not any(n > 1 for n in counted.values()):
            return msgs
        out = []
        for i, m in enumerate(msgs):
            turn = _turn_of(m)
            if turn and counted.get(turn, 0) > 1 and not _line_key(m) \
                    and not str(m.get("panelWho") or "") and winner.get(turn) != i:
                continue
            out.append(m)
        return out
    except Exception:  # noqa: BLE001 — a save never fails on this; nothing dropped
        log.warning("could not match chat replies by turn", exc_info=True)
        return msgs


def _clean_line(raw: Any, stamp: str) -> dict[str, Any] | None:
    """One whitelisted, server-stamped line — or None for a non-dict / a
    line with no role (nothing the page could render)."""
    if not isinstance(raw, dict):
        return None
    line: dict[str, Any] = {}
    for key in LINE_KEYS:
        if key in raw and raw[key] is not None:
            line[key] = raw[key]
    role = str(line.get("role") or "").strip()
    if not role:
        return None
    line["role"] = role
    line["content"] = str(line.get("content") or "")
    # The server is the writer here: no client time to defer to (the v1.142.0
    # stamp, the PUT route's format).
    line["at"] = stamp
    return line


def _fold_index(msgs: list, line: dict[str, Any]) -> "int | None":
    """Idea 39 (39-a): where the live window holds the day's line of the same
    record (the same ``reportFold``), newest first; None when it holds none or
    the line is not a report that folds. Pure.

    Review: a day's line the USER has answered since is never rewritten (the
    chat, and the history the model reads, keep what was talked about and its
    run link): None, so the report is appended as a new line, which later
    reports that day fold into. An unattended hourly schedule still keeps one
    line a day."""
    fold = str(line.get("reportFold") or "").strip()
    if not fold or not isinstance(line.get("reportFrom"), dict):
        return None
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if isinstance(m, dict) and str(m.get("reportFold") or "").strip() == fold:
            if any(isinstance(after, dict) and after.get("role") == "user" for after in msgs[i + 1:]):
                return None
            return i
    return None


def _folded(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """The day's line after one more report of the same record (idea 39,
    39-a): the NEW report's words and source, how many reports the day's line
    now holds (``reportCount``) and the keys it stands for (``reportSeen``,
    the newest :data:`REPORT_SEEN_MAX`), so a late repeat of an earlier one is
    still skipped. The line keeps its place in the chat. Pure."""
    try:
        count = int(old.get("reportCount") or 1)
    except (TypeError, ValueError):
        count = 1
    seen = [str(k) for k in (old.get("reportSeen") or []) if isinstance(k, str) and k]
    own = str(old.get("reportKey") or "").strip()
    if own:
        seen.append(own)
    # An earlier report's session id, so a re-delivery of THAT run is a repeat.
    sid = _session_of(old)
    if sid and f"run:{sid}" not in seen:
        seen.append(f"run:{sid}")
    line = dict(new)
    line["reportCount"] = count + 1
    line["reportSeen"] = seen[-REPORT_SEEN_MAX:]
    return line


def _next_stamp(prev: datetime | None) -> datetime:
    """The PUT route's monotonic bump: the new ``updated_at`` must MOVE past the
    stored one even inside one Windows clock tick (v1.326.0)."""
    new_stamp = utcnow()
    if prev is not None:
        if prev.tzinfo is not None:
            prev = prev.astimezone(timezone.utc).replace(tzinfo=None)
        if new_stamp.astimezone(timezone.utc).replace(tzinfo=None) <= prev:
            new_stamp = (prev + timedelta(microseconds=1)).replace(tzinfo=timezone.utc)
    return new_stamp


def _index(db: Any, engine: Any, record: ChatThreadRecord, msgs: list) -> None:
    """Re-index for history search in the caller's session — best-effort, as
    the PUT route and the comm store do. Never raises."""
    if str(getattr(record, "surface", "") or "") == "build":
        return  # v1.367.0: a Build session is never in Chat's history search
    try:
        from ..core.db import search_index  # lazy: keeps the import graph flat

        index = search_index(engine)
        if index is None:
            return
        index.sync_thread(
            record.id, "chat", record.title or "", record.project_id or "", msgs, db=db
        )
    except Exception:  # noqa: BLE001 — an append must never fail on search
        log.warning("history-search sync failed for chat thread %s", record.id, exc_info=True)


def _publish(event_bus: Any, thread_id: str, who: str, entries: int,
             extra: "dict[str, Any] | None" = None) -> None:
    """Best-effort CHAT_THREAD_UPDATED — on the loop as a task, off it under
    ``asyncio.run`` (the bus wakes each subscriber's own loop thread-safely).
    ``extra`` (third plan G6): more keys, after the three (a turn's reply
    names its ``turn_id``). Never raises."""
    if event_bus is None:
        return
    try:
        coro = event_bus.publish(
            EventType.CHAT_THREAD_UPDATED,
            {"thread_id": thread_id, "who": who, "entries": entries, **(extra or {})},
        )
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception:  # noqa: BLE001 — an event must never break an append
        log.warning("chat.thread_updated publish failed", exc_info=True)


def append_to_chat_thread(
    engine: Any,
    thread_id: str,
    lines: list[dict[str, Any]],
    *,
    event_bus: Any = None,
    who: str = "",
    event_extra: "dict[str, Any] | None" = None,
    unarchive: bool = True,
) -> dict[str, Any]:
    """Append ``lines`` to the saved chat ``thread_id`` (see the module doc).

    Returns ``{"ok": bool, "updated_at": str | None, "why": str, "appended":
    int, "skipped": int}`` — ``updated_at`` is the new stamp in the PUT route's
    format (the page adopts it as its next ``if_updated_at``); ``why`` is ""
    on success, else one plain reason. ``who`` names the writer on the event
    (defaults to the first line's ``panelWho``, else ``"daemon"``).

    Idea 39 (39-a): ``unarchive=False`` leaves an archived chat archived (a
    report never undoes the user's archive, D-39.2); the answer then says
    ``archived: True`` and ``title`` so the caller can ring the bell. A line
    with ``reportFold`` whose fold is already in the live window REPLACES that
    line in place (``folded`` counts them): one line a day per record.
    """
    thread_id = str(thread_id or "").strip()
    out: dict[str, Any] = {"ok": False, "updated_at": None, "why": "", "appended": 0, "skipped": 0}
    if not thread_id:
        out["why"] = "no such chat"
        return out
    stamp = utcnow().isoformat()
    cleaned = [c for c in (_clean_line(x, stamp) for x in (lines or [])) if c is not None]
    # v1.499.3: before the transaction (a CREATE inside it would wait on it).
    older_ok = ensure_older_table(engine)
    try:
        with _APPEND_LOCK, session_scope(engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
            if r is None:
                out["why"] = "no such chat"
                return out
            if (getattr(r, "owner", "user") or "user") == "daemon":
                out["why"] = "a messaging thread — Iron Jarvis's messaging store writes it"
                out["refused"] = True  # third plan G6: another writer's chat
                return out
            if str(getattr(r, "surface", "") or "") == "build":
                out["refused"] = True
                # v1.367.0 (Phase 4 review): a Build session has ONE writer —
                # its own turns (harness/sessions.append_lines). A chat-shaped
                # line from an @-panel round, an agent's delivery, a media job
                # or a remote's inbound must not land in its transcript (nor
                # un-archive it); the caller reads ``why``.
                out["why"] = "a coding session — its own turns write it"
                return out
            try:
                msgs = json.loads(r.messages_json or "[]")
                if not isinstance(msgs, list):
                    msgs = []
            except Exception:  # noqa: BLE001 — a corrupt blob must not wedge the append
                msgs = []
            known: set[str] = set()
            for m in msgs:
                if isinstance(m, dict):
                    known |= _line_keys(m)
            # ...and a reply kept inside a fork (a Try again's older reply).
            known |= {f"turn:{t}" for t in reply_turn_ids(msgs)}
            added: list[dict[str, Any]] = []
            folded = 0
            for line in cleaned:
                # Third plan G6: a reply for a turn that already has one is a
                # repeat too (``turn:<id>``), like a session's second line.
                keys = _line_keys(line)
                if keys & known:
                    out["skipped"] += 1
                    continue
                known |= keys
                # Idea 39 (39-a): the day's line of the same record takes
                # this report's place (one line a day per record).
                at = _fold_index(msgs, line)
                if at is not None:
                    msgs[at] = _folded(msgs[at], line)
                    folded += 1
                    added.append(msgs[at])
                    continue
                added.append(line)
            if not added:
                # Nothing new — the thread is untouched, its stamp unchanged
                # (a no-op must not 409 the page's next save).
                out["ok"] = True
                out["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
                return out
            msgs = msgs + [line for line in added if not any(line is m for m in msgs)]
            # v1.499.3: what leaves the live window is KEPT (older messages),
            # in this transaction; if it cannot be kept, it stays live.
            if len(msgs) > MAX_MESSAGES and older_ok:
                try:
                    keep_older(db, r, msgs[:-MAX_MESSAGES], engine=engine)
                    msgs = msgs[-MAX_MESSAGES:]
                except Exception:  # noqa: BLE001 — logged in keep_older
                    pass
            r.messages_json = json.dumps(msgs)
            r.updated_at = _next_stamp(r.updated_at)
            # A reply un-archives (comm/threads.append's rule): a chat the user
            # tucked away comes back when someone answers in it. A REPORT does
            # not (idea 39, D-39.2): archiving was the user's decision.
            if getattr(r, "archived_at", None) is not None:
                if unarchive:
                    r.archived_at = None
                else:
                    out["archived"] = True
                    out["title"] = str(r.title or "")
            db.add(r)
            _index(db, engine, r, msgs)
            db.commit()
            db.refresh(r)
            out["ok"] = True
            out["appended"] = len(added) - folded
            out["folded"] = folded
            out["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
            count = len(msgs)
    except Exception as exc:  # noqa: BLE001 — never raises; the caller reads ``why``
        log.warning("chat append failed for thread %s", thread_id, exc_info=True)
        out["ok"] = False
        out["why"] = f"could not append: {type(exc).__name__}"
        return out
    speaker = (who or "").strip() or str(added[0].get("panelWho") or "").strip() or "daemon"
    _publish(event_bus, thread_id, speaker, count, event_extra)
    return out
