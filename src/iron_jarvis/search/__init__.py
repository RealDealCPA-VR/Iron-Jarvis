"""History search: one ranked index over every conversation Iron Jarvis keeps.

Public surface (FROZEN — other subsystems code against exactly this)::

    from iron_jarvis.search import SearchIndex, SearchHit, SearchDocRecord

    index = SearchIndex(engine)
    index.available() -> bool
    index.sync_thread(thread_id, kind, title, project_id, entries, *, ref=None, db=None) -> int
    index.sync_session(session, *, db=None) -> int
    index.drop_thread(thread_id, *, db=None) -> int
    index.drop_session(session_id, *, db=None) -> int
    index.drop_run(run_id, *, db=None) -> int
    index.drop_refs(refs, *, db=None) -> int
    index.rebuild(*, db=None) -> int
    index.search(query, *, kinds=None, project_id=None, after=None, before=None,
                 limit=20) -> list[SearchHit]
    index.stats() -> {"docs", "threads", "sessions", "available", "mode"}
    index.backfill(batch=200, cursor=None, *, force=False)
        -> {"indexed", "scanned", "cursor", "done"}

Reads never raise; writes log and continue. See ``index.py``'s module docstring
for the three design decisions (own-content FTS5, score normalization, query
hardening) that everything above rests on.
"""

from __future__ import annotations

from .index import SearchIndex
from .models import (
    FTS_TABLE,
    MAX_ENTRIES,
    MAX_TEXT,
    SEARCH_KINDS,
    SearchDocRecord,
    SearchHit,
)

__all__ = [
    "FTS_TABLE",
    "MAX_ENTRIES",
    "MAX_TEXT",
    "SEARCH_KINDS",
    "SearchDocRecord",
    "SearchHit",
    "SearchIndex",
]
