"""The tool NAMES a chat keeps between replies, so its tool list stays the
same (the intelligence plan, release 7; the contract is in ``context/prefix``).

One small table, ``chatkept``, one row a chat: every tool name the chat has
been sent, in the order first sent, and which of them its last reply was
sent. The next reply keeps that order and puts a new tool at the end.

NAMES ONLY. Never a definition, an argument, a result, a message or a file's
text: nothing of what a person wrote or attached is written here (a test
plants a marker in an attachment and finds it in no row). A name here is a
definition to SEND, never a permission: every call is still decided by the
tool registry's gate, as before.

A NEW chat's first reply is sent before the chat has an id: its row is
written under the reply's turn id and given its chat when the page links the
turn (``routes/chat.link_chat_turn`` -> :func:`home_turn`), like every other
first-reply record. A row that never got a chat is removed after
:data:`ORPHAN_DAYS` by the next write. A reply with neither id keeps nothing.
The row goes with its chat when the chat is deleted
(``routes/chat.delete_thread``).

BLOCKING throughout: the lanes call it through ``asyncio.to_thread``. Nothing
here raises into a reply, and nothing here publishes an event.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlmodel import Field, SQLModel, select

from ..core.db import row_values, session_scope
from ..core.ids import new_id, utcnow

log = logging.getLogger(__name__)

#: A row whose turn never became a saved chat is removed this long after.
ORPHAN_DAYS = 2
#: Tool names kept for a chat, and the longest name (the reply record's own
#: bounds, ``eval/pricing``).
MAX_TOOLS = 64
MAX_NAME = 200
ID_MAX = 80


class ChatKept(SQLModel, table=True):
    """The tool names one chat keeps between replies."""

    __tablename__ = "chatkept"

    id: str = Field(default_factory=lambda: new_id("kept"), primary_key=True)
    #: The saved chat ("" = not yet: :func:`home_turn`).
    thread_id: str = Field(default="", index=True)
    turn_id: str = Field(default="", index=True)
    #: JSON ``{"names": [...], "held": [...]}``: tool names, nothing else.
    names_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow, index=True)


@dataclass
class Kept:
    """What one chat holds when a reply begins."""

    #: Every tool name the chat has sent, in the order first sent.
    tools: "list[str]" = field(default_factory=list)
    #: The ones its LAST reply was sent.
    held: "set[str]" = field(default_factory=set)


_READY: set[int] = set()


def ensure_table(engine: Any) -> bool:
    """Create the table if it is missing (once per engine). Never raises."""
    key = id(engine)
    if key in _READY:
        return True
    try:
        ChatKept.__table__.create(bind=engine, checkfirst=True)
        _READY.add(key)
        return True
    except Exception:  # noqa: BLE001 — a kept list is never worth a failed reply
        log.warning("the chat's kept tool names are unavailable", exc_info=True)
        return False


def _ids(thread_id: Any, turn_id: Any) -> "tuple[str, str]":
    return str(thread_id or "").strip()[:ID_MAX], str(turn_id or "").strip()[:ID_MAX]


def _owner(thread: str, turn: str) -> Any:
    """The row of one chat: its saved id, else the turn of its first reply."""
    if thread:
        return ChatKept.thread_id == thread
    return (ChatKept.thread_id == "") & (ChatKept.turn_id == turn)


def clean_names(names: Iterable[Any]) -> "list[str]":
    out: list[str] = []
    for n in names or ():
        s = str(n or "").strip()[:MAX_NAME]
        if s and s not in out:
            out.append(s)
        if len(out) >= MAX_TOOLS:
            break
    return out


def load(engine: Any, thread_id: Any, turn_id: Any = "") -> Kept:
    """What the chat holds. An empty :class:`Kept` for a new chat, a reply
    with no id, or any fault (never raises)."""
    thread, turn = _ids(thread_id, turn_id)
    out = Kept()
    if not (thread or turn) or engine is None or not ensure_table(engine):
        return out
    try:
        with session_scope(engine) as db:
            row = db.exec(
                select(ChatKept).where(_owner(thread, turn)).order_by(ChatKept.created_at.desc())  # type: ignore[arg-type,attr-defined]
            ).first()
            if row is None:
                return out
            try:
                saved = json.loads(row.names_json or "{}")
            except ValueError:
                saved = {}
            saved = saved if isinstance(saved, dict) else {}
            names, held = saved.get("names"), saved.get("held")
            out.tools = clean_names(names if isinstance(names, list) else [])
            out.held = set(clean_names(held if isinstance(held, list) else [])) & set(out.tools)
    except Exception:  # noqa: BLE001
        log.warning("the chat's kept tool names could not be read", exc_info=True)
        return Kept()
    return out


def save_tools(engine: Any, thread_id: Any, turn_id: Any, names: Iterable[Any], *,
               held: Iterable[Any] = ()) -> bool:
    """The chat's tool names, in the order first sent, and which of them its
    last reply was sent (*held*), replacing what it held. False when nothing
    identifies the chat or on any fault (never raises)."""
    thread, turn = _ids(thread_id, turn_id)
    if not (thread or turn) or engine is None or not ensure_table(engine):
        return False
    try:
        row = row_values(
            ChatKept, id=new_id("kept"), thread_id=thread, turn_id=turn,
            names_json=json.dumps(
                {"names": clean_names(names), "held": clean_names(held)}, ensure_ascii=False,
            ),
        )
        with engine.begin() as conn:
            conn.execute(sa_delete(ChatKept).where(_owner(thread, turn)))
            conn.execute(ChatKept.__table__.insert(), [row])
            conn.execute(sa_delete(ChatKept).where(
                ChatKept.thread_id == "", ChatKept.created_at < utcnow() - timedelta(days=ORPHAN_DAYS),
            ))
        _sweep_gone(engine)
        return True
    except Exception:  # noqa: BLE001
        log.warning("the chat's tool names could not be kept", exc_info=True)
        return False


def _sweep_gone(engine: Any) -> None:
    """Remove the rows of chats that no longer exist (a reply into a deleted
    chat's id writes a row again) once they are :data:`ORPHAN_DAYS` old. A
    chat that was never saved has no chat record either: its row goes the
    same way when the chat has been quiet that long. Never raises."""
    try:
        from ..core.models import ChatThreadRecord

        with engine.begin() as conn:
            conn.execute(sa_delete(ChatKept).where(
                ChatKept.thread_id != "",
                ChatKept.created_at < utcnow() - timedelta(days=ORPHAN_DAYS),
                ChatKept.thread_id.notin_(select(ChatThreadRecord.id)),  # type: ignore[attr-defined]
            ))
    except Exception:  # noqa: BLE001 — a tidy-up is never worth a failed reply
        log.debug("kept tool names of chats that are gone could not be swept", exc_info=True)


def home_turn(engine: Any, turn_id: Any, thread_id: Any) -> int:
    """Give the row a new chat's first reply wrote its chat. A chat that
    already holds a row keeps it (the turn's row is dropped). Never raises."""
    thread, turn = _ids(thread_id, turn_id)
    if not thread or not turn or engine is None or not ensure_table(engine):
        return 0
    try:
        with engine.begin() as conn:
            if conn.execute(select(ChatKept.id).where(ChatKept.thread_id == thread).limit(1)).first() is not None:
                conn.execute(sa_delete(ChatKept).where(ChatKept.thread_id == "", ChatKept.turn_id == turn))
                return 0
            res = conn.execute(
                sa_update(ChatKept)
                .where(ChatKept.turn_id == turn, ChatKept.thread_id == "")
                .values(thread_id=thread)
            )
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001
        log.warning("a first reply's kept tool names could not be given their chat", exc_info=True)
        return 0


def drop_thread(db: Any, thread_id: Any) -> None:
    """Remove a deleted chat's row, inside the caller's transaction."""
    db.exec(sa_delete(ChatKept).where(ChatKept.thread_id == str(thread_id or "")))


__all__ = [
    "MAX_TOOLS", "ORPHAN_DAYS", "ChatKept", "Kept", "clean_names",
    "drop_thread", "ensure_table", "home_turn", "load", "save_tools",
]
