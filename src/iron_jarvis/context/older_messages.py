"""A long conversation's OLDER messages: KEPT, never deleted (v1.499.3, 48-b).

Every conversation keeps its newest messages LIVE (the window a page loads,
renders and sends back): a chat and a phone chat 200, a coding session 200
(cut at a turn start), a round-table 400. What leaves that window MOVES here,
in order, in the same transaction as the write that pushed it out, and
history search keeps finding it (``SearchIndex.add_older``; a coding session
is never in Chat's history search). It is deleted only with its owner.

The writers live in four packages: ``daemon/chat_append`` and
``daemon/routes/chat`` (a chat), ``comm/threads`` (a phone chat),
``harness/sessions`` (a coding session) and ``agents/threads`` (a
round-table). The store lives HERE, below all of them, so none of the other
three reaches into ``daemon/`` for it (48-b review:
``tests/test_remember_ladder_shared_v1185.py`` pins that ``agents/threads``
never imports from ``daemon``). ``daemon/chat_append`` keeps every name for
its callers.

Rules (``docs/rules/context.md``):

* :func:`ensure_older_table` runs BEFORE the write's transaction (a CREATE
  inside it would wait on the write's own writer slot);
* :func:`keep_older` RAISES when the rows cannot be kept, and the caller then
  keeps them in the live window: nothing is ever dropped;
* rows before a position never change (only added at the end, or deleted
  with their conversation), so a page that holds a position can keep asking
  from it while newer messages move.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger

log = get_logger(__name__)


class ChatOlderMessage(SQLModel, table=True):
    """One message that left a conversation's live window (v1.499.3).

    A plain new table (rows are only ever added, or deleted with their
    conversation). Created on first use by :func:`ensure_older_table`,
    OUTSIDE any open write transaction (a CREATE inside the save's
    transaction would wait on the save's own writer slot), and at boot
    (``core.db._LATE_MODEL_MODULES``)."""

    __tablename__ = "chatoldermessage"

    id: int | None = Field(default=None, primary_key=True)
    thread_id: str = Field(default="", index=True)
    #: Position in the conversation's older messages: 0 = the very first one.
    seq: int = 0
    #: The message exactly as the conversation stored it.
    message_json: str = "{}"
    moved_at: datetime = Field(default_factory=utcnow)


_OLDER_READY: set[int] = set()


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


def keep_older(db: Any, record: Any, moved: list, *, engine: Any = None, kind: str = "chat") -> int:
    """Add ``moved`` (oldest first) to the conversation's older messages in
    the CALLER's transaction, and to history search (``add_older``; never
    for a coding session, ``surface == "build"``). The table must already
    exist (:func:`ensure_older_table`, before the transaction). Returns how
    many were kept. RAISES when they cannot be kept: the caller then keeps
    them in the live window instead (nothing is ever dropped)."""
    rows = [m for m in (moved or []) if isinstance(m, dict)]
    if not rows:
        return 0
    try:
        from sqlalchemy import func
        from sqlmodel import select

        # 48-b: every row is written out BEFORE any is added, so a fault
        # leaves nothing half-added in the caller's write (the rows then
        # stay live, and none is also kept).
        texts = [json.dumps(m) for m in rows]
        start = db.exec(
            select(func.count()).select_from(ChatOlderMessage).where(
                ChatOlderMessage.thread_id == record.id
            )
        ).one()
        start = int(start[0] if isinstance(start, tuple) else start or 0)
        db.add_all([
            ChatOlderMessage(thread_id=record.id, seq=start + i, message_json=text)
            for i, text in enumerate(texts)
        ])
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
    """How many older messages a conversation keeps. BLOCKING. 0 on any fault."""
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


def older_count_in(db: Any, thread_id: str) -> int:
    """:func:`older_count` in the CALLER's open session (the table must
    already exist: :func:`ensure_older_table` before the transaction).
    0 on any fault, never raises."""
    try:
        from sqlalchemy import func
        from sqlmodel import select

        n = db.exec(
            select(func.count()).select_from(ChatOlderMessage).where(
                ChatOlderMessage.thread_id == thread_id
            )
        ).one()
        return int(n[0] if isinstance(n, tuple) else n or 0)
    except Exception:  # noqa: BLE001
        return 0


#: Older messages read per page by :func:`older_by_who`.
_COUNT_PAGE = 500


def older_by_who(engine: Any, thread_id: str, who_of: Any) -> dict[str, int]:
    """How many of a conversation's older messages each speaker said (48-b),
    as ``who_of(message)`` names them (None or "": not counted) — the
    left-out count a remote seat is told (build-out R0.1: every row the
    sender does not pass is counted, and older messages are never passed).
    BLOCKING: read a page at a time, in the order they were kept. On a fault
    what was counted so far, logged."""
    out: dict[str, int] = {}
    try:
        from sqlmodel import select

        if not thread_id or not ensure_older_table(engine):
            return out
        last = 0
        while True:
            with session_scope(engine) as db:
                page = list(
                    db.exec(
                        select(ChatOlderMessage.id, ChatOlderMessage.message_json)
                        .where(ChatOlderMessage.thread_id == thread_id, ChatOlderMessage.id > last)
                        .order_by(ChatOlderMessage.id)
                        .limit(_COUNT_PAGE)
                    ).all()
                )
            for _id, text in page:
                try:
                    m = json.loads(text or "null")
                except (TypeError, ValueError):
                    continue
                who = who_of(m) if isinstance(m, dict) else None
                if who:
                    out[str(who)] = out.get(str(who), 0) + 1
            if len(page) < _COUNT_PAGE:
                return out
            last = int(page[-1][0] or 0)
    except Exception:  # noqa: BLE001 — a count, never a reason to fail a turn
        log.warning("could not count the older messages of %s", thread_id, exc_info=True)
        return out


#: The most older messages one read answers.
OLDER_PAGE_MAX = 1000


def older_window(*, offset: int = 0, limit: int = 200, before: "int | None" = None,
                 total: int = 0) -> tuple[int, int]:
    """Where a read of a conversation's older messages starts and how many it
    takes (48-a). With ``before`` (a position: 0 is the very first message):
    the ``limit`` messages just before it, a position past the end read as
    the end and a negative one as the start. Without it: ``offset`` and
    ``limit`` as before. Pure; never raises."""
    try:
        limit = max(1, min(int(limit or 200), OLDER_PAGE_MAX))
    except (TypeError, ValueError):
        limit = 200
    if before is not None:
        try:
            end = max(0, min(int(before), max(0, int(total or 0))))
        except (TypeError, ValueError):
            end = 0
        start = max(0, end - limit)
        return start, end - start
    try:
        return max(0, int(offset or 0)), limit
    except (TypeError, ValueError):
        return 0, limit


def read_older(engine: Any, thread_id: str, *, offset: int = 0, limit: int = 200,
               before: "int | None" = None) -> list[dict]:
    """A page of a conversation's older messages, oldest first. BLOCKING.

    ``before`` (48-a, the chat page's scroll back): the ``limit`` messages
    just before that position (see :func:`older_window`). Rows before a
    position never change (older messages are only added at the end, or
    deleted with their conversation), so a page that holds a position can
    keep asking from it while newer messages move."""
    from sqlmodel import select

    if not ensure_older_table(engine):
        return []
    if before is not None:
        offset, limit = older_window(before=before, limit=limit,
                                     total=older_count(engine, thread_id))
        if limit <= 0:
            return []
    else:
        offset, limit = older_window(offset=offset, limit=limit)
    with session_scope(engine) as db:
        rows = db.exec(
            select(ChatOlderMessage)
            .where(ChatOlderMessage.thread_id == thread_id)
            .order_by(ChatOlderMessage.seq, ChatOlderMessage.id)
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
    """Delete a conversation's older messages in the caller's transaction
    (it is being deleted: "delete" means all of it). Never raises."""
    try:
        from sqlalchemy import delete

        res = db.exec(delete(ChatOlderMessage).where(ChatOlderMessage.thread_id == thread_id))
        return int(getattr(res, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001 — no table yet: nothing to delete
        return 0
