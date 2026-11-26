"""What a chat KEEPS between replies so its prefix stays the same (the
intelligence plan, release 7; the contract is in ``context/prefix``).

One small table, ``chatkept``, two kinds of row, both tied to ONE chat:

* ``tools``: the NAMES of the tool definitions the chat has been sent, in the
  order they were first sent, and which of them its last reply was sent. The
  next reply keeps that order and puts a new tool at the end. Names only: never a definition, an argument or a result.
  A name here is a definition to send, never a permission: every call is still
  decided by the tool registry's gate, as before.
* ``file``: the text of what was attached to one message of the chat, in the
  very bytes the model was first sent, so the next replies send that message
  again exactly as it was. This is the chat's own content (the file itself is
  in the uploads folder already); it is kept only here, on this PC, bounded
  (:data:`MAX_FILES` messages a chat, ``prefix.KEPT_MAX_CHARS`` each), and it
  goes with its chat when the chat is deleted (``routes/chat.delete_thread``).

A NEW chat's first reply is sent before the chat has an id: its rows are
written under the reply's turn id and given their chat when the page links
the turn (``routes/chat.link_chat_turn`` -> :func:`home_turn`), like every
other first-reply record. Rows that never got a chat are removed after
:data:`ORPHAN_DAYS` by the next write. A reply with neither id keeps nothing.

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

TOOLS = "tools"
FILE = "file"
#: A row whose turn never became a saved chat is removed this long after.
ORPHAN_DAYS = 2
#: Messages of one chat whose attached text is kept (a chat's Files rail
#: holds up to 30 files); past it the oldest kept message's text goes.
MAX_FILES = 30
#: Tool names kept for a chat, and the longest name (the reply record's own
#: bounds, ``eval/pricing``).
MAX_TOOLS = 64
MAX_NAME = 200
ID_MAX = 80


class ChatKept(SQLModel, table=True):
    """One thing a chat keeps between replies (see the module docstring)."""

    __tablename__ = "chatkept"

    id: str = Field(default_factory=lambda: new_id("kept"), primary_key=True)
    #: The saved chat ("" = not yet: :func:`home_turn`).
    thread_id: str = Field(default="", index=True)
    turn_id: str = Field(default="", index=True)
    #: ``tools`` | ``file``
    kind: str = Field(default="", index=True)
    #: ``file``: the message's key inside its chat (``prefix.message_key``).
    key: str = Field(default="", index=True)
    #: ``tools``: a JSON list of names. ``file``: the kept block, as sent.
    text: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


@dataclass
class Kept:
    """What one chat holds when a reply begins."""

    #: Every tool name the chat has sent, in the order first sent.
    tools: "list[str]" = field(default_factory=list)
    #: The ones its LAST reply was sent.
    held: "set[str]" = field(default_factory=set)
    #: A message's key -> the block kept with it.
    files: "dict[str, str]" = field(default_factory=dict)


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
        log.warning("the chat's kept record is unavailable", exc_info=True)
        return False


def _ids(thread_id: Any, turn_id: Any) -> "tuple[str, str]":
    return str(thread_id or "").strip()[:ID_MAX], str(turn_id or "").strip()[:ID_MAX]


def _owner(thread: str, turn: str) -> Any:
    """The rows of one chat: its saved id, else the turn of its first reply."""
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
            rows = list(db.exec(
                select(ChatKept).where(_owner(thread, turn)).order_by(ChatKept.created_at)  # type: ignore[arg-type]
            ))
            for row in rows:
                if row.kind == TOOLS:
                    try:
                        saved = json.loads(row.text or "{}")
                    except ValueError:
                        saved = {}
                    saved = saved if isinstance(saved, dict) else {}
                    names = saved.get("names")
                    held = saved.get("held")
                    out.tools = clean_names(names if isinstance(names, list) else [])
                    out.held = set(clean_names(held if isinstance(held, list) else [])) & set(out.tools)
                elif row.kind == FILE and row.key and row.text:
                    out.files[row.key] = row.text
    except Exception:  # noqa: BLE001
        log.warning("the chat's kept record could not be read", exc_info=True)
        return Kept()
    return out


def _sweep(conn: Any) -> None:
    conn.execute(sa_delete(ChatKept).where(
        ChatKept.thread_id == "", ChatKept.created_at < utcnow() - timedelta(days=ORPHAN_DAYS),
    ))


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
            ChatKept, id=new_id("kept"), thread_id=thread, turn_id=turn, kind=TOOLS, key="",
            text=json.dumps(
                {"names": clean_names(names), "held": clean_names(held)}, ensure_ascii=False,
            ),
        )
        with engine.begin() as conn:
            conn.execute(sa_delete(ChatKept).where(_owner(thread, turn), ChatKept.kind == TOOLS))
            conn.execute(ChatKept.__table__.insert(), [row])
            _sweep(conn)
        return True
    except Exception:  # noqa: BLE001
        log.warning("the chat's tool names could not be kept", exc_info=True)
        return False


def save_file(engine: Any, thread_id: Any, turn_id: Any, key: str, text: str) -> bool:
    """The kept block of one message of the chat (replacing the one held for
    that message). False when nothing identifies the chat, the block is
    empty, or on any fault (never raises)."""
    thread, turn = _ids(thread_id, turn_id)
    key = str(key or "").strip()[:ID_MAX]
    if not (thread or turn) or not key or not text or engine is None or not ensure_table(engine):
        return False
    try:
        row = row_values(
            ChatKept, id=new_id("kept"), thread_id=thread, turn_id=turn, kind=FILE, key=key, text=str(text),
        )
        with engine.begin() as conn:
            conn.execute(sa_delete(ChatKept).where(
                _owner(thread, turn), ChatKept.kind == FILE, ChatKept.key == key,
            ))
            conn.execute(ChatKept.__table__.insert(), [row])
            held = list(conn.execute(
                select(ChatKept.id).where(_owner(thread, turn), ChatKept.kind == FILE)  # type: ignore[arg-type]
                .order_by(ChatKept.created_at.desc())  # type: ignore[attr-defined]
            ))
            extra = [r[0] for r in held[MAX_FILES:]]
            if extra:
                conn.execute(sa_delete(ChatKept).where(ChatKept.id.in_(extra)))  # type: ignore[attr-defined]
            _sweep(conn)
        return True
    except Exception:  # noqa: BLE001
        log.warning("a message's attached text could not be kept", exc_info=True)
        return False


def home_turn(engine: Any, turn_id: Any, thread_id: Any) -> int:
    """Give the rows a new chat's first reply wrote their chat. Rows the chat
    already holds stay (a chat is linked once). Never raises."""
    thread, turn = _ids(thread_id, turn_id)
    if not thread or not turn or engine is None or not ensure_table(engine):
        return 0
    try:
        with engine.begin() as conn:
            if conn.execute(
                select(ChatKept.id).where(ChatKept.thread_id == thread, ChatKept.kind == TOOLS).limit(1)
            ).first() is not None:
                conn.execute(sa_delete(ChatKept).where(
                    ChatKept.thread_id == "", ChatKept.turn_id == turn, ChatKept.kind == TOOLS,
                ))
            res = conn.execute(
                sa_update(ChatKept)
                .where(ChatKept.turn_id == turn, ChatKept.thread_id == "")
                .values(thread_id=thread)
            )
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001
        log.warning("a first reply's kept record could not be given its chat", exc_info=True)
        return 0


def drop_thread(db: Any, thread_id: Any) -> None:
    """Remove a deleted chat's rows, inside the caller's transaction."""
    db.exec(sa_delete(ChatKept).where(ChatKept.thread_id == str(thread_id or "")))


__all__ = [
    "FILE", "MAX_FILES", "MAX_TOOLS", "ORPHAN_DAYS", "TOOLS", "ChatKept", "Kept", "clean_names",
    "drop_thread", "ensure_table", "home_turn", "load", "save_file", "save_tools",
]
