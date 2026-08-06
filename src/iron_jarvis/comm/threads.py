"""Comm threads: server-owned chat threads for the messaging surfaces.

The dashboard's chat threads are BROWSER-owned (the client assembles
``messages_json`` and autosaves via PUT). A phone conversation can't work that
way — the poller thread and the daemon's reply both need to land in the same
thread durably, with nobody's browser open. :class:`CommThreadStore` is the
single writer for those DAEMON-owned threads:

* ``resolve`` maps a remote identity ``(channel, sender_id)`` to its thread —
  one thread per identity ("Telegram · Val"), created on first contact,
  re-minted if the thread row was deleted from the dashboard.
* ``append`` is the atomic read-modify-write on ``messages_json`` (one
  ``session_scope`` under one ``threading.Lock``) — the poller thread and a
  route handler can race, and SQLite's WAL serializes writers but NOT the
  read→append→commit across two transactions.
* ``retire`` implements ``/new``: the identity row is deleted so the next
  message mints a fresh thread; the old thread stays in the desktop list.

Every append publishes :data:`EventType.CHAT_THREAD_UPDATED` best-effort so an
open dashboard thread live-refreshes. Publishing must work from BOTH worlds —
the async daemon loop and a sync foreign thread — mirroring
``daemon/app.py``'s ``_publish_skill_proposal``: running-loop ``create_task``,
else ``run_coroutine_threadsafe`` onto the loop handed to :meth:`set_loop`,
else drop silently (bare store in unit tests).
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import ChatThreadRecord
from .models import CommIdentityRecord

log = get_logger("comm.threads")

#: Same tail cap as PUT /chat/threads/{id} — a comm thread must not diverge
#: from what a desktop save would keep.
_MAX_MESSAGES = 200

#: Same per-message content cap as the chat turn's input handling (chat.py
#: truncates each message to 12000 chars before the provider sees it).
_MAX_CONTENT = 12000


class CommThreadStore:
    """Find-or-create + atomic append for daemon-owned chat threads."""

    def __init__(self, engine: Engine, *, event_bus: Any = None) -> None:
        self.engine = engine
        self.event_bus = event_bus
        # Serializes find-or-create (uniqueness of (channel, sender_id) has no
        # DB constraint — see CommIdentityRecord) AND the messages_json
        # read-modify-write (poller thread vs. route thread).
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Last-seen project binding per identity (v1.141.0), refreshed on
        # every resolve. When the dashboard deletes a project-tagged comm
        # thread, the healed re-mint would otherwise silently drop the
        # project — this cache lets the fresh thread keep it. BEST-EFFORT by
        # design: in-memory only (a restart between the delete and the next
        # message loses it — the old thread row is gone, there is nowhere
        # durable to read it back from), and deliberately NOT a schema change.
        self._last_project: dict[tuple[str, str], str | None] = {}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Hand over the daemon loop so sync-thread appends can still publish."""
        self._loop = loop

    # -- identity → thread ---------------------------------------------------
    def resolve(
        self, channel: str, sender_id: str, display: str = ""
    ) -> ChatThreadRecord:
        """The thread for ``(channel, sender_id)`` — created on first contact.

        Reuses the bound thread when it exists; if the thread row was deleted
        from the dashboard, a fresh one is minted and the identity re-bound
        (the phone must never lose its line to Iron Jarvis over a tidy-up).
        The heal KEEPS the project binding when recoverable (v1.141.0): every
        resolve caches the bound thread's ``project_id`` per identity, and a
        re-mint copies the last-seen value onto the fresh thread — so deleting
        a project-tagged phone thread doesn't silently drop the project from
        the conversation. Best-effort (in-memory; see ``_last_project``).
        """
        sender_id = str(sender_id)
        key = (channel, sender_id)
        with self._lock, session_scope(self.engine) as db:
            identity = db.exec(
                select(CommIdentityRecord).where(
                    CommIdentityRecord.channel == channel,
                    CommIdentityRecord.sender_id == sender_id,
                )
            ).first()
            if identity is not None:
                thread = db.get(ChatThreadRecord, identity.thread_id)
                if display and identity.display_name != display:
                    identity.display_name = display
                    db.add(identity)
                if thread is None:
                    # Dashboard deleted the thread row — mint fresh, re-bind.
                    # Prefer the display name the identity already knows over
                    # the bare sender id when this call didn't carry one.
                    label = display or identity.display_name or sender_id
                    thread = self._new_thread(channel, label)
                    # Heal keeps the project: the deleted row's own project_id
                    # is unrecoverable (the row is gone), so the fresh thread
                    # carries the last binding this identity was seen with.
                    thread.project_id = self._last_project.get(key)
                    identity.thread_id = thread.id
                    db.add(thread)
                    db.add(identity)
                db.commit()
                db.refresh(thread)
                # Refresh the cache with the CURRENT truth (also when the user
                # just un-tagged the thread — never re-apply a stale project).
                self._last_project[key] = thread.project_id
                return thread
            thread = self._new_thread(channel, display or sender_id)
            identity = CommIdentityRecord(
                channel=channel,
                sender_id=sender_id,
                thread_id=thread.id,
                display_name=display,
            )
            db.add(thread)
            db.add(identity)
            db.commit()
            db.refresh(thread)
            self._last_project[key] = thread.project_id
            return thread

    @staticmethod
    def _new_thread(channel: str, label: str) -> ChatThreadRecord:
        return ChatThreadRecord(
            title=f"{channel.strip().title()} · {label}",
            owner="daemon",
            comm_channel=channel,
            comm_display=label,
        )

    def retire(self, channel: str, sender_id: str) -> bool:
        """``/new``: unbind the identity so the next message mints a fresh
        thread. The old thread SURVIVES (still listed on the desktop) — only
        the binding is deleted. Returns True when a binding existed. The
        cached project binding is dropped too: "/new" is a DELIBERATE fresh
        start, so a later heal must not resurrect the retired thread's
        project (only a dashboard delete of a live thread heals-with-project).
        """
        sender_id = str(sender_id)
        self._last_project.pop((channel, str(sender_id)), None)
        with self._lock, session_scope(self.engine) as db:
            identity = db.exec(
                select(CommIdentityRecord).where(
                    CommIdentityRecord.channel == channel,
                    CommIdentityRecord.sender_id == sender_id,
                )
            ).first()
            if identity is None:
                return False
            db.delete(identity)
            db.commit()
            return True

    # -- the atomic append ---------------------------------------------------
    def append(self, thread_id: str, role: str, content: str) -> int:
        """Append one message atomically; returns the new message count.

        Read-modify-write of ``messages_json`` in ONE session under the store
        lock; tail-capped to the same 200 messages PUT keeps; content capped
        at the turn's 12000-char input cap; bumps ``updated_at``. Raises
        ``ValueError`` on a vanished thread (callers ``resolve`` first, so
        this is a bug/race worth hearing about — silently dropping a phone
        message would be worse). Publishes CHAT_THREAD_UPDATED best-effort.
        """
        entry = {"role": role, "content": str(content or "")[:_MAX_CONTENT]}
        with self._lock, session_scope(self.engine) as db:
            r = db.get(ChatThreadRecord, thread_id)
            if r is None:
                raise ValueError(f"no such chat thread: {thread_id}")
            try:
                msgs = json.loads(r.messages_json or "[]")
                if not isinstance(msgs, list):
                    msgs = []
            except Exception:  # noqa: BLE001 — a corrupt blob must not wedge comm
                msgs = []
            msgs.append(entry)
            msgs = msgs[-_MAX_MESSAGES:]
            r.messages_json = json.dumps(msgs)
            r.updated_at = utcnow()
            db.add(r)
            db.commit()
            count = len(msgs)
        self._publish_updated(thread_id, count)
        return count

    def _publish_updated(self, thread_id: str, messages: int) -> None:
        """Best-effort CHAT_THREAD_UPDATED — never raises, works from the
        daemon loop (create_task), a foreign thread (run_coroutine_threadsafe
        onto ``set_loop``'s loop), or nowhere (silently dropped)."""
        if self.event_bus is None:
            return
        try:
            coro = self.event_bus.publish(
                EventType.CHAT_THREAD_UPDATED,
                {"thread_id": thread_id, "messages": messages},
            )
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                loop = self._loop
                if loop is not None and loop.is_running():
                    asyncio.run_coroutine_threadsafe(coro, loop)
                else:
                    coro.close()  # no loop to publish on — best-effort by design
        except Exception:  # noqa: BLE001 — an event must never break an append
            log.warning("chat.thread_updated publish failed", exc_info=True)

    # -- reads (never raise) -------------------------------------------------
    def history_body(self, thread_id: str, limit: int = 30) -> list[dict[str, str]]:
        """The thread tail as ChatBody-shaped ``[{role, content}]`` — last
        ``limit`` messages, roles coerced to user/assistant (a provider round
        rejects anything else). ``[]`` on any failure."""
        try:
            with session_scope(self.engine) as db:
                r = db.get(ChatThreadRecord, thread_id)
                if r is None:
                    return []
                msgs = json.loads(r.messages_json or "[]")
            if not isinstance(msgs, list):
                return []
            limit = int(limit)
            if limit <= 0:  # -0 slices to the WHOLE list — guard explicitly
                return []
            out: list[dict[str, str]] = []
            for m in msgs[-limit:]:
                if not isinstance(m, dict):
                    continue
                role = "user" if m.get("role") == "user" else "assistant"
                out.append({"role": role, "content": str(m.get("content") or "")})
            return out
        except Exception:  # noqa: BLE001 — a read helper must never take out a turn
            log.warning("history_body failed for %s", thread_id, exc_info=True)
            return []

    def is_daemon_owned(self, thread_id: str) -> bool:
        """True iff the daemon writes this thread. Pre-reconciler rows read
        ``owner`` as NULL — that's "user" (zero behavior change). Never raises."""
        try:
            with session_scope(self.engine) as db:
                r = db.get(ChatThreadRecord, thread_id)
                return bool(r is not None and (r.owner or "user") == "daemon")
        except Exception:  # noqa: BLE001
            return False

    def thread_channel(self, thread_id: str) -> tuple[str, str] | None:
        """``(channel, sender_id)`` bound to a thread, for reply fan-out —
        ``None`` when unbound (retired via ``/new``, or not a comm thread).
        Never raises."""
        try:
            with session_scope(self.engine) as db:
                identity = db.exec(
                    select(CommIdentityRecord).where(
                        CommIdentityRecord.thread_id == thread_id
                    )
                ).first()
                if identity is None:
                    return None
                return (identity.channel, identity.sender_id)
        except Exception:  # noqa: BLE001
            return None
