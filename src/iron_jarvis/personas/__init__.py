"""Editable, savable chat personas.

Built-in personas ship in-memory; this package adds a durable store for the
user's edits/creations and the merge logic the chat routes use.
"""

from __future__ import annotations

from .builtins import BUILTIN_PERSONAS
from .models import PersonaRecord
from .store import (
    PersonaError,
    PersonaStore,
    create_persona_record,
    delete_persona_record,
    merged,
    resolve_prompt,
    save_persona_record,
    slugify,
)
from .voice import VOICE_HEADER, voice_section

__all__ = [
    "BUILTIN_PERSONAS",
    "PersonaError",
    "PersonaRecord",
    "PersonaStore",
    "create_persona_record",
    "delete_persona_record",
    "save_persona_record",
    "VOICE_HEADER",
    "merged",
    "resolve_prompt",
    "slugify",
    "voice_section",
]
