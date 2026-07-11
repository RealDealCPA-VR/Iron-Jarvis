"""Persona records — the user's editable/saved chat personas.

A persona is a named system-prompt preset for direct chat. The built-in set lives
in-memory (daemon ``_PERSONAS``); this table holds the user's ADDITIONS and
OVERRIDES: editing a built-in writes a row with the same ``name`` (which then wins
at resolve time), and a brand-new persona is just a row with a new ``name``.
Deleting a row reverts a built-in to its default (or removes a custom one).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import utcnow


class PersonaRecord(SQLModel, table=True):
    """One user-saved (or user-overridden) chat persona."""

    #: The stable id/slug — matches a built-in name to override it, or a new slug.
    name: str = Field(primary_key=True)
    title: str = ""          # display name (what the picker shows)
    description: str = ""     # one-line summary
    prompt: str = ""          # the system prompt this persona applies
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
