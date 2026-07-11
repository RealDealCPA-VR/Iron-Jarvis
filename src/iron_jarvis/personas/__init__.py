"""Editable, savable chat personas.

Built-in personas ship in-memory; this package adds a durable store for the
user's edits/creations and the merge logic the chat routes use.
"""

from __future__ import annotations

from .models import PersonaRecord
from .store import PersonaStore, merged, resolve_prompt, slugify

__all__ = [
    "PersonaRecord",
    "PersonaStore",
    "merged",
    "resolve_prompt",
    "slugify",
]
