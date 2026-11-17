"""The browser sidebar's conversation, kept as a chat (third north-star fix
plan, SB3, decision G7).

THE DEFECT THIS CLOSES: the sidebar's conversation lived only in the daemon's
memory (``PanelTurns._history``). A restart of Iron Jarvis (every update)
forgot it while the panel in the browser still showed it, so "now the second
one" was answered with no context and nothing said so; and once the panel was
closed the conversation was in no chat list and no history search.

THE CHAT IS ONE THE DAEMON WRITES, the way a phone conversation is: a
``ChatThreadRecord`` with ``owner == "daemon"``, appended through
:class:`~iron_jarvis.comm.threads.CommThreadStore` (the shared conversation
lock, the 200-message tail the chat save keeps, the history-search index in
the same transaction, ``CHAT_THREAD_UPDATED`` for an open Chat page). The Chat
page's autosave is refused for such a chat by the route guard, so the two
never fight over it.

MARKED AS THE SIDEBAR'S, NEVER A PHONE'S. ``comm_channel`` is
:data:`SIDEBAR_CHANNEL`, which the chat list paints as the chat's origin. No
``CommIdentityRecord`` is ever bound to it, and every outbound path (the reply
fan-out of ``POST /comm/threads/{id}/send``, an agent's line mirrored to the
phone) finds its destination through that binding only, so a sidebar chat is
never sent anywhere.

WHICH CHAT IS "THE" CONVERSATION: the newest sidebar chat whose ``setup_json``
holds :data:`OPEN_KEY`. Reset removes the key (the chat stays in the list,
whole); the next exchange creates a new chat. No new table, no new column.

Everything here is BLOCKING (SQLite): the panel calls it through
``asyncio.to_thread``.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import select

from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
from ..core.logging import get_logger
from ..core.models import ChatThreadRecord

logger = get_logger(__name__)

#: The ``comm_channel`` that marks the sidebar's chats. Written in words, so the
#: chat list's origin label (the channel, first letter capitalised) reads
#: "Browser sidebar" with no special case.
SIDEBAR_CHANNEL = "browser sidebar"

#: The ``comm_display`` of a sidebar chat.
SIDEBAR_DISPLAY = "Browser sidebar"

#: The ``setup_json`` key that names the sidebar's CURRENT conversation. Reset
#: removes it; the chat itself is kept.
OPEN_KEY = "browserSidebarOpen"

#: A chat's title is its first question's first line, cut to this many
#: characters (the length a coding session's first-message title uses).
TITLE_CHARS = 60

#: How many of the newest sidebar chats are looked at for the open one. Only
#: the newest is ever open (Reset closes it before the next one is made); the
#: margin covers a chat whose marker a crash left behind.
LOOK_BACK = 20

#: The one sentence the panel shows when the conversation it shows cannot be
#: carried on: the chat was deleted in the chat list, or could not be read.
NOT_AVAILABLE = "The earlier conversation is not available any more, so this one starts fresh."


class SidebarChatUnreadable(Exception):
    """The database could not be read; the conversation cannot be restored."""


def chat_title(question: str) -> str:
    """The first line with words, spaces collapsed, secrets masked, cut to
    :data:`TITLE_CHARS` (an ellipsis marks a cut)."""
    from ..detections.redact import mask

    for line in str(question or "").splitlines():
        words = " ".join(line.split())
        if words:
            value = mask(words)
            return value if len(value) <= TITLE_CHARS else value[: TITLE_CHARS - 1] + "…"
    return SIDEBAR_DISPLAY


def _is_open(row: ChatThreadRecord) -> bool:
    try:
        setup = json.loads(row.setup_json or "{}") if row.setup_json else {}
    except Exception:  # noqa: BLE001 — a corrupt setup is not the open chat
        return False
    return isinstance(setup, dict) and bool(setup.get(OPEN_KEY))


def _rows(messages_json: str) -> list[dict[str, str]]:
    """The stored messages as the sidebar's ``{role, content}`` rows."""
    msgs = json.loads(messages_json or "[]")
    if not isinstance(msgs, list):
        return []
    out: list[dict[str, str]] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        role = "user" if m.get("role") == "user" else "assistant"
        out.append({"role": role, "content": content})
    return out


class SidebarChat:
    """Read, write and close the sidebar's chat. BLOCKING; never on the loop."""

    def __init__(self, store: Any, engine: Any) -> None:
        self.store = store
        self.engine = engine

    def _open_rows(self, db: Any) -> list[ChatThreadRecord]:
        found = db.exec(
            select(ChatThreadRecord)
            .where(ChatThreadRecord.owner == "daemon")
            .where(ChatThreadRecord.comm_channel == SIDEBAR_CHANNEL)
            .order_by(ChatThreadRecord.created_at.desc())  # type: ignore[attr-defined]
            .limit(LOOK_BACK)
        ).all()
        return [r for r in found if _is_open(r)]

    def current(self) -> tuple[str, list[dict[str, str]]] | None:
        """The open sidebar chat's id and its messages, or ``None`` when there
        is none. Raises :class:`SidebarChatUnreadable` when the database could
        not be read (the caller says so instead of starting over silently)."""
        try:
            with session_scope(self.engine) as db:
                rows = self._open_rows(db)
                if not rows:
                    return None
                return rows[0].id, _rows(rows[0].messages_json)
        except Exception as exc:  # noqa: BLE001 — named, the caller says it plainly
            logger.warning("the sidebar's chat could not be read", exc_info=True)
            raise SidebarChatUnreadable(str(exc)) from exc

    def exists(self, chat_id: str) -> bool:
        """Is this chat still there? True on a read fault: an unreadable
        database is not a deletion, and the next write finds out for itself."""
        if not chat_id:
            return False
        try:
            with session_scope(self.engine) as db:
                return db.get(ChatThreadRecord, chat_id) is not None
        except Exception:  # noqa: BLE001
            logger.warning("could not check the sidebar's chat", exc_info=True)
            return True

    def _create(self, question: str) -> str:
        record = ChatThreadRecord(
            title=chat_title(question),
            owner="daemon",
            comm_channel=SIDEBAR_CHANNEL,
            comm_display=SIDEBAR_DISPLAY,
            setup_json=json.dumps({OPEN_KEY: True}),
        )
        with CONVERSATION_WRITE_LOCK, session_scope(self.engine) as db:
            # One open sidebar chat at a time: any other still marked (a crash
            # between a Reset and this write) is closed in the same breath.
            for other in self._open_rows(db):
                other.setup_json = _without_open(other.setup_json)
                db.add(other)
            db.add(record)
            db.commit()
            return record.id

    def save_exchange(self, chat_id: str, question: str, answer: str) -> str:
        """Append one exchange to ``chat_id`` (created when empty) and return
        the chat it landed in. A chat deleted in the chat list meanwhile is
        replaced by a new one. Raises on a database fault (the caller logs
        it; the conversation in memory is kept either way)."""
        cid = chat_id or self._create(question)
        try:
            self._append(cid, question, answer)
        except ValueError:
            # Deleted between the check before the reply and this write.
            cid = self._create(question)
            self._append(cid, question, answer)
        return cid

    def _append(self, chat_id: str, question: str, answer: str) -> None:
        if question:
            self.store.append(chat_id, "user", question)
        if answer:
            self.store.append(chat_id, "assistant", answer)

    def close_all(self) -> None:
        """Reset: no sidebar chat is the current conversation any more. The
        chats themselves are kept, whole, in the chat list."""
        with CONVERSATION_WRITE_LOCK, session_scope(self.engine) as db:
            for row in self._open_rows(db):
                row.setup_json = _without_open(row.setup_json)
                db.add(row)
            db.commit()


def _without_open(setup_json: str) -> str:
    try:
        setup = json.loads(setup_json or "{}") if setup_json else {}
    except Exception:  # noqa: BLE001
        setup = {}
    if not isinstance(setup, dict):
        setup = {}
    setup.pop(OPEN_KEY, None)
    return json.dumps(setup) if setup else ""
