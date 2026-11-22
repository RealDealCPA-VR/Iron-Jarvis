"""The record of where each figure was read (v1.549.0, idea 30-a).

One ``sourcemark`` row per figure Iron Jarvis read from a file in a chat
turn: the file, the place in it (page and line, paragraph, table cell, sheet
cell, line), the figure as written, the line it sat on (200 characters,
secret-masked) and the turn that read it. Rows are written by the code that
did the reading (``tools/registry._record`` after the call's own ledger row,
and the attachment step of both chat lanes), never from a model's argument.

The figure links in a reply point at these rows (``documents/sources``).
They go with their chat when it is deleted (``routes/chat.delete_thread``)
unless ``kept`` (ideas 31 and 33 keep a sorted document's facts and a
recording's minutes). A new chat's first turn is written before the chat
exists, with no chat id; ``home_turn`` gives those rows their chat when the
page links the turn, and rows that never got a chat are removed after
:data:`ORPHAN_DAYS` by the next write (no loop of its own).

BLOCKING throughout: callers are on a worker thread (the registry's
``_record``) or hop to one (``asyncio.to_thread``).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlmodel import Field, SQLModel, select

from ..core.db import row_values, session_scope
from ..core.ids import utcnow

log = logging.getLogger(__name__)

#: A row whose turn never became a saved chat (an abandoned new chat) is
#: removed this long after it was written.
ORPHAN_DAYS = 2
#: How many marks one request may ask for (a link's chooser holds at most 6).
MAX_LOOKUP = 12


def new_mark_id() -> str:
    """64 random bits: a turn writes thousands of rows, so the 48-bit
    ``new_id`` would collide too soon, and the id rides every link."""
    return f"src_{uuid.uuid4().hex[:16]}"


class SourceMark(SQLModel, table=True):
    """One figure Iron Jarvis read, and where it read it."""

    __tablename__ = "sourcemark"

    id: str = Field(default_factory=new_mark_id, primary_key=True)
    thread_id: str = Field(default="", index=True)
    turn_id: str = Field(default="", index=True)
    #: The ledger row of the read (``ToolInvocation.id``); "" for an attachment.
    invocation_id: str = ""
    path: str = ""
    #: The file's bytes when it was read ("" when it was too large to hash).
    file_sha256: str = ""
    #: pdf | docx | text | csv | sheet | pptx
    kind: str = ""
    #: ``{page, line}`` / ``{paragraph}`` / ``{table, row, col}`` / ``{line}``
    #: (``col`` too for a CSV) / ``{sheet, cell}``.
    locator_json: str = "{}"
    #: The figure as the file writes it ("48,210.00").
    shown: str = ""
    #: The figure as it is compared (``core/numbers.number_key``).
    key: str = ""
    #: The line it sat on, at most 200 characters, secret-masked.
    context: str = ""
    #: Kept when its chat is deleted (ideas 31 and 33).
    kept: bool = False
    created_at: datetime = Field(default_factory=utcnow, index=True)


_READY: set[int] = set()


def ensure_table(engine: Any) -> bool:
    """Create the table if it is missing (once per engine, never inside a
    transaction). Never raises; False when it cannot be made."""
    key = id(engine)
    if key in _READY:
        return True
    try:
        SourceMark.__table__.create(bind=engine, checkfirst=True)
        _READY.add(key)
        return True
    except Exception:  # noqa: BLE001 — a mark is never worth a failed turn
        log.warning("figure record table unavailable", exc_info=True)
        return False


def write_marks(
    engine: Any,
    marks: Iterable[Any],
    *,
    path: str,
    kind: str,
    thread_id: str = "",
    turn_id: str = "",
    invocation_id: str = "",
    file_sha256: str = "",
    kept: bool = False,
) -> "list[dict[str, Any]]":
    """Write ``marks`` (``sources.Mark``) as rows; return what the reply
    linker needs of each (``id``, ``key``, ``path``, ``kind``, ``locator``).
    One transaction. Rows that never got a chat are swept here too.

    ``kept`` (v1.556.0, idea 31-a): the facts of a document Iron Jarvis
    wrote from a sorted scan stay when their chat is deleted."""
    rows: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for m in marks:
        mid = new_mark_id()
        rows.append(row_values(
            SourceMark,
            id=mid, thread_id=str(thread_id or ""), turn_id=str(turn_id or ""),
            invocation_id=str(invocation_id or ""), path=str(path),
            file_sha256=str(file_sha256 or ""), kind=str(kind),
            locator_json=json.dumps(m.locator, sort_keys=True),
            shown=m.shown, key=m.key, context=m.context, kept=bool(kept),
        ))
        out.append({"id": mid, "key": m.key, "path": str(path), "kind": str(kind),
                    "locator": dict(m.locator)})
    if not ensure_table(engine):
        return []
    cutoff = utcnow() - timedelta(days=ORPHAN_DAYS)
    with engine.begin() as conn:
        if rows:
            conn.execute(SourceMark.__table__.insert(), rows)
        conn.execute(
            sa_delete(SourceMark).where(
                SourceMark.thread_id == "",
                SourceMark.kept == False,  # noqa: E712 — SQL, not Python
                SourceMark.created_at < cutoff,
            )
        )
    return out


def as_dict(row: SourceMark) -> dict[str, Any]:
    try:
        locator = json.loads(row.locator_json or "{}")
    except ValueError:
        locator = {}
    return {
        "id": row.id, "path": row.path, "kind": row.kind,
        "locator": locator if isinstance(locator, dict) else {},
        "shown": row.shown, "key": row.key, "context": row.context,
        "file_sha256": row.file_sha256, "thread_id": row.thread_id, "turn_id": row.turn_id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def get_marks(engine: Any, ids: "list[str]") -> "list[dict[str, Any]]":
    """The rows for ``ids``, in the order asked; unknown ids are left out."""
    wanted = [i for i in dict.fromkeys(ids) if i][:MAX_LOOKUP]
    if not wanted or not ensure_table(engine):
        return []
    with session_scope(engine) as db:
        found = {r.id: as_dict(r) for r in db.exec(select(SourceMark).where(SourceMark.id.in_(wanted)))}
    return [found[i] for i in wanted if i in found]


def drop_thread(db: Any, thread_id: str) -> None:
    """Remove a deleted chat's rows (not the kept ones), inside the caller's
    transaction."""
    db.exec(sa_delete(SourceMark).where(
        SourceMark.thread_id == str(thread_id),
        SourceMark.kept == False,  # noqa: E712
    ))


def drop_kept_paths(engine: Any, paths: "Iterable[str]") -> int:
    """Remove the kept rows of files that were taken away again (the Undo of
    a sorted scan, v1.556.0): a fact must not point at a file that is gone."""
    wanted = [str(p) for p in dict.fromkeys(paths) if p]
    if not wanted or not ensure_table(engine):
        return 0
    removed = 0
    with engine.begin() as conn:
        for start in range(0, len(wanted), 200):
            res = conn.execute(sa_delete(SourceMark).where(
                SourceMark.path.in_(wanted[start:start + 200]),
                SourceMark.kept == True,  # noqa: E712
            ))
            removed += int(res.rowcount or 0)
    return removed


def home_turn(engine: Any, turn_id: str, thread_id: str) -> int:
    """Give a turn's rows written before its chat existed their chat."""
    if not turn_id or not thread_id or not ensure_table(engine):
        return 0
    with engine.begin() as conn:
        res = conn.execute(
            sa_update(SourceMark)
            .where(SourceMark.turn_id == str(turn_id), SourceMark.thread_id == "")
            .values(thread_id=str(thread_id))
        )
    return int(res.rowcount or 0)


__all__ = [
    "MAX_LOOKUP", "ORPHAN_DAYS", "SourceMark", "as_dict", "drop_kept_paths", "drop_thread", "ensure_table",
    "get_marks", "home_turn", "new_mark_id", "write_marks",
]
