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
  and the turn-link route may both try to deliver the same result);
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
)

#: The PUT route keeps the newest 200 messages; so does an append.
MAX_MESSAGES = 200

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
    return f"media:{job}" if job else ""


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


def _publish(event_bus: Any, thread_id: str, who: str, entries: int) -> None:
    """Best-effort CHAT_THREAD_UPDATED — on the loop as a task, off it under
    ``asyncio.run`` (the bus wakes each subscriber's own loop thread-safely).
    Never raises."""
    if event_bus is None:
        return
    try:
        coro = event_bus.publish(
            EventType.CHAT_THREAD_UPDATED,
            {"thread_id": thread_id, "who": who, "entries": entries},
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
) -> dict[str, Any]:
    """Append ``lines`` to the saved chat ``thread_id`` (see the module doc).

    Returns ``{"ok": bool, "updated_at": str | None, "why": str, "appended":
    int, "skipped": int}`` — ``updated_at`` is the new stamp in the PUT route's
    format (the page adopts it as its next ``if_updated_at``); ``why`` is ""
    on success, else one plain reason. ``who`` names the writer on the event
    (defaults to the first line's ``panelWho``, else ``"daemon"``).
    """
    thread_id = str(thread_id or "").strip()
    out: dict[str, Any] = {"ok": False, "updated_at": None, "why": "", "appended": 0, "skipped": 0}
    if not thread_id:
        out["why"] = "no such chat"
        return out
    stamp = utcnow().isoformat()
    cleaned = [c for c in (_clean_line(x, stamp) for x in (lines or [])) if c is not None]
    try:
        with _APPEND_LOCK, session_scope(engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
            if r is None:
                out["why"] = "no such chat"
                return out
            if (getattr(r, "owner", "user") or "user") == "daemon":
                out["why"] = "a messaging thread — Iron Jarvis's messaging store writes it"
                return out
            if str(getattr(r, "surface", "") or "") == "build":
                # v1.367.0 (Phase 4 review): a Build session has ONE writer —
                # its own turns (harness/sessions.append_lines). A chat-shaped
                # line from an @-panel round, an agent's delivery, a media job
                # or a remote's inbound must not land in its transcript (nor
                # un-archive it); the caller reads ``why``.
                out["why"] = "a Build session — its own turns write it"
                return out
            try:
                msgs = json.loads(r.messages_json or "[]")
                if not isinstance(msgs, list):
                    msgs = []
            except Exception:  # noqa: BLE001 — a corrupt blob must not wedge the append
                msgs = []
            known = {
                _line_key(m) for m in msgs if isinstance(m, dict) and _line_key(m)
            }
            added: list[dict[str, Any]] = []
            for line in cleaned:
                sid = _line_key(line)
                if sid and sid in known:
                    out["skipped"] += 1
                    continue
                if sid:
                    known.add(sid)
                added.append(line)
            if not added:
                # Nothing new — the thread is untouched, its stamp unchanged
                # (a no-op must not 409 the page's next save).
                out["ok"] = True
                out["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
                return out
            msgs = (msgs + added)[-MAX_MESSAGES:]
            r.messages_json = json.dumps(msgs)
            r.updated_at = _next_stamp(r.updated_at)
            # A reply un-archives (comm/threads.append's rule): a chat the user
            # tucked away comes back when someone answers in it.
            if getattr(r, "archived_at", None) is not None:
                r.archived_at = None
            db.add(r)
            _index(db, engine, r, msgs)
            db.commit()
            db.refresh(r)
            out["ok"] = True
            out["appended"] = len(added)
            out["updated_at"] = r.updated_at.isoformat() if r.updated_at else None
            count = len(msgs)
    except Exception as exc:  # noqa: BLE001 — never raises; the caller reads ``why``
        log.warning("chat append failed for thread %s", thread_id, exc_info=True)
        out["ok"] = False
        out["why"] = f"could not append: {type(exc).__name__}"
        return out
    speaker = (who or "").strip() or str(added[0].get("panelWho") or "").strip() or "daemon"
    _publish(event_bus, thread_id, speaker, count)
    return out
