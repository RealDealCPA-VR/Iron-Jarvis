"""File search across configured roots (§18 extension).

A broader search than the workspace-only ``grep`` builtin: it walks one or more
*configured roots* (e.g. several project directories) and finds files by name
(glob/substring), by content (regex), and — when an embedder is injected —
by semantic similarity. It respects ignore patterns and never escapes the
configured roots.
"""

from __future__ import annotations

from .service import DEFAULT_IGNORE, FileSearchService
from .tools import FileSearchTool, filesearch_tools

__all__ = [
    "DEFAULT_IGNORE",
    "FileSearchService",
    "FileSearchTool",
    "filesearch_tools",
]
