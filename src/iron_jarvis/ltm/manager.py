"""Long-term memory manager (§21).

``LongTermMemory`` registers LTM connectors and routes search/append either to a
single named source or — for search — across every registered connector, merging
results round-robin so each store is fairly represented.
"""

from __future__ import annotations

from typing import Any

from .base import LTMConnector

#: Bumped on every successful append (v1.146.1). Read by
#: ``memory.index_block._local_notes`` to invalidate its folder-scan cache.
#:
#: WHY A COUNTER AND NOT THE FOLDER'S mtime (which is what the cache used to
#: rely on): appending writes a file, and a new file was assumed to move the
#: directory's mtime, making the note visible on the very next turn. On NTFS it
#: does not reliably — Windows stamps timestamps off the system clock (~15.6ms
#: tick) and updates directory metadata lazily, so two appends inside one tick
#: produce a byte-identical ``st_mtime``. The cache then read "nothing changed"
#: and served the pre-append scan: "remember this" followed by a question about
#: it showed the OLD note count and titles for up to the 60s TTL. Measured 9
#: failures in 24 runs of the regression test on Windows.
#:
#: The counter moves because WE WROTE, which no filesystem timestamp
#: granularity can defeat. It is process-local and deliberately global: an
#: append is rare next to a turn, so busting every base's cached scan costs one
#: redundant glob, and per-source bookkeeping is not worth it. Writes made
#: OUTSIDE the app (editing the vault in Obsidian, a sync client) do not bump
#: it and keep falling back to mtime + TTL exactly as before — the cache now
#: busts when the mtime moved OR when we appended.
_APPEND_EPOCH = 0


def append_epoch() -> int:
    """How many appends this process has made. Only ever compared for equality
    — callers cache it alongside a scan and re-scan when it has moved."""
    return _APPEND_EPOCH


class LongTermMemory:
    """Front door to all registered long-term-memory connectors."""

    def __init__(self) -> None:
        self._connectors: dict[str, LTMConnector] = {}

    def register(self, connector: LTMConnector) -> LTMConnector:
        if not getattr(connector, "name", ""):
            raise ValueError("LTM connector must have a name")
        self._connectors[connector.name] = connector
        return connector

    def deregister(self, name: str) -> bool:
        """Remove a registered connector (a deleted custom source takes effect
        LIVE, not on the next restart). False when no such source."""
        return self._connectors.pop(name, None) is not None

    def connectors(self) -> list[LTMConnector]:
        return list(self._connectors.values())

    def sources(self) -> list[str]:
        return list(self._connectors)

    def get(self, source: str) -> LTMConnector | None:
        return self._connectors.get(source)

    def default_source(self) -> str | None:
        """The store appends route to when none is named — ``brain`` if present."""
        if "brain" in self._connectors:
            return "brain"
        return next(iter(self._connectors), None)

    def search(
        self, query: str, k: int = 5, source: str | None = None
    ) -> list[dict[str, Any]]:
        if source is not None:
            conn = self._connectors.get(source)
            if conn is None:
                raise ValueError(f"unknown LTM source '{source}'")
            return conn.search(query, k=k)
        return self._merge_search(query, k)

    def append(self, title: str, content: str, source: str) -> str:
        conn = self._connectors.get(source)
        if conn is None:
            raise ValueError(f"unknown LTM source '{source}'")
        ref = conn.append(title, content)
        # AFTER the write succeeded — a failed append changed nothing, and
        # bumping anyway would throw away a valid cached scan for free. This is
        # the ONLY place in the tree that calls a connector's append (verified
        # by grep), so every path — routes, tools, the CLI, the importers —
        # rides it. See :data:`_APPEND_EPOCH` for why this exists.
        global _APPEND_EPOCH
        _APPEND_EPOCH += 1
        return ref

    # -- internals --------------------------------------------------------
    def _merge_search(self, query: str, k: int) -> list[dict[str, Any]]:
        per_source: list[list[dict[str, Any]]] = []
        for conn in self._connectors.values():
            try:
                per_source.append(conn.search(query, k=k))
            except Exception:  # one failing connector must not break the merge
                per_source.append([])
        merged: list[dict[str, Any]] = []
        rank = 0
        while len(merged) < k and any(rank < len(lst) for lst in per_source):
            for lst in per_source:
                if rank < len(lst):
                    merged.append(lst[rank])
                    if len(merged) >= k:
                        break
            rank += 1
        return merged
