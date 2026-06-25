"""Obsidian vault connector (§21) — fully offline.

Treats a folder of ``.md`` files (recursively, matching Obsidian's nested
folders) as a vault: search by filename + content, append a slugified note.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import MarkdownDirConnector


class ObsidianConnector(MarkdownDirConnector):
    """An Obsidian vault on disk, exposed as a long-term-memory connector."""

    name = "obsidian"

    def __init__(self, vault_dir: Path | str, embedder: Any = None) -> None:
        super().__init__(vault_dir, embedder=embedder, recursive=True)
