"""Data model for history search: the indexed row + the ranked hit.

``SearchDocRecord`` is an ORDINARY mapped SQLModel table — one row per indexed
unit of history (one chat message, one comm message, one round entry, one agent
session). The FTS5 virtual table that makes it searchable is deliberately NOT a
model (see ``core/db.py::_ensure_fts``); it is created with raw DDL and keyed to
``SearchDocRecord.n``.

Why ``n`` exists next to ``id``
------------------------------
FTS5 joins on an INTEGER ``rowid``. Every other Iron Jarvis table uses a string
primary key (``new_id("...")``), which cannot be a rowid. So this table carries
BOTH: ``n`` (``INTEGER PRIMARY KEY`` → a true SQLite rowid alias, verified in
``tests/test_search_index.py::test_n_is_a_rowid_alias``) is what the FTS index
points at, and ``id`` stays the conventional prefixed string id so the row looks
like every other record to the rest of the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: The kinds of history a doc can describe. ``chat`` = a browser-owned chat
#: thread message, ``comm`` = a daemon-owned messaging thread message,
#: ``round`` = an Agents-page round-table entry, ``session`` = one agent run.
SEARCH_KINDS = ("chat", "comm", "round", "session")

#: Per-doc text cap. Long enough to keep a real message whole, short enough that
#: the index (which stores its own copy of the text — see ``search/index.py``)
#: stays a fraction of the DB.
MAX_TEXT = 4000

#: Tail cap per thread, in PARITY with ``PUT /chat/threads/{id}`` (``msgs[-200:]``)
#: and ``CommThreadStore._MAX_MESSAGES``. The index must never remember more of a
#: thread than the thread itself keeps.
MAX_ENTRIES = 200

#: The unmapped FTS5 virtual table. Kept here so index.py and core/db.py agree.
FTS_TABLE = "searchdoc_fts"


class SearchDocRecord(SQLModel, table=True):
    """One searchable unit of history.

    Rows are written ONLY through :class:`~iron_jarvis.search.index.SearchIndex`
    — it keeps the mapped row and its FTS5 shadow row in the same transaction.
    A bulk ``DELETE`` issued anywhere else would orphan an FTS row.
    """

    #: INTEGER PRIMARY KEY → SQLite rowid alias → the FTS5 ``rowid``. Never set
    #: by hand; SQLite assigns it and the index reads it back after ``flush()``.
    n: int | None = Field(default=None, primary_key=True)
    #: Conventional prefixed string id (stable, unique, not the rowid).
    id: str = Field(default_factory=lambda: new_id("sdoc"), index=True, unique=True)
    kind: str = Field(default="chat", index=True)
    #: Owning thread; ``""`` for ``session`` docs (they have no thread).
    thread_id: str = Field(default="", index=True)
    #: Deep-link target: the thread id for thread kinds, the session id for
    #: ``session``. What a UI navigates to when the hit is opened.
    ref: str = Field(default="", index=True)
    #: Position within the thread (message index); 0 for sessions.
    seq: int = 0
    role: str = ""
    #: The indexed text, capped at :data:`MAX_TEXT`. Also what the ``basic``
    #: (no-FTS5) fallback LIKE-scans, so it must live here either way.
    text: str = ""
    #: ``""`` (never NULL) so an equality filter never has to think about NULL.
    project_id: str = Field(default="", index=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    #: Display label (thread title / session task) shown next to the snippet.
    title: str = ""


@dataclass
class SearchHit:
    """One ranked search result.

    ``score`` is ALWAYS normalized into ``[0, 1]`` (see
    ``index.py::_normalize_scores``) because the memory fabric filters on
    ``min_score`` and sorts hits ACROSS sources — a raw BM25 value (unbounded,
    negative-signed) would swamp every cosine hit it is ranked against.
    """

    kind: str
    ref: str
    thread_id: str = ""
    title: str = ""
    snippet: str = ""
    role: str = ""
    at: datetime | None = None
    project_id: str = ""
    score: float = 0.0
    seq: int = 0

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form (``at`` as an ISO string, ``""`` when unknown)."""
        return {
            "kind": self.kind,
            "ref": self.ref,
            "thread_id": self.thread_id,
            "title": self.title,
            "snippet": self.snippet,
            "role": self.role,
            "at": self.at.isoformat() if self.at else "",
            "project_id": self.project_id,
            "score": round(float(self.score), 4),
            "seq": int(self.seq),
        }
