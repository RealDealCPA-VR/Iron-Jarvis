"""Long-term memory manager (§21).

``LongTermMemory`` registers LTM connectors and routes search/append either to a
single named source or — for search — across every registered connector, merging
results round-robin so each store is fairly represented.
"""

from __future__ import annotations

from typing import Any

from .base import LTMConnector


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
        return conn.append(title, content)

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
