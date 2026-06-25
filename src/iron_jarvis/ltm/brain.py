"""Generic local markdown "brain" connector (§21) — fully offline.

A flat folder of markdown notes (covers gbrain / second-brain style stores).
Same shape as the Obsidian connector but simpler (non-recursive). This is the
built-in default LTM store, mounted at ``config.home/'brain'``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import MarkdownDirConnector


class MarkdownBrainConnector(MarkdownDirConnector):
    """A flat markdown folder used as the built-in default long-term memory."""

    name = "brain"

    def __init__(self, directory: Path | str, embedder: Any = None) -> None:
        super().__init__(directory, embedder=embedder, recursive=False)
