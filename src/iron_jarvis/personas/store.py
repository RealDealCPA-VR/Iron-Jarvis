"""Persona store — merge built-in personas with the user's saved edits.

The daemon owns the built-in personas (a ``{name: {description, prompt}}`` dict).
This store adds durable user personas + overrides on top: :func:`merged` returns
the effective catalog (built-ins with any override applied, then user creations),
and :func:`resolve_prompt` is what the chat handler calls to turn a persona name
into a system prompt (user override → built-in → free-text passthrough).
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import PersonaRecord


def slugify(text: str) -> str:
    """A stable, filesystem-free id for a persona from a title."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "persona"


class PersonaStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, name: str) -> PersonaRecord | None:
        with session_scope(self.engine) as db:
            return db.get(PersonaRecord, name)

    def all(self) -> list[PersonaRecord]:
        with session_scope(self.engine) as db:
            return list(db.exec(select(PersonaRecord)))

    def upsert(
        self, name: str, *, title: str = "", description: str = "", prompt: str = ""
    ) -> PersonaRecord:
        with session_scope(self.engine) as db:
            row = db.get(PersonaRecord, name)
            if row is None:
                row = PersonaRecord(name=name)
            row.title = title
            row.description = description
            row.prompt = prompt
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def delete(self, name: str) -> bool:
        with session_scope(self.engine) as db:
            row = db.get(PersonaRecord, name)
            if row is None:
                return False
            db.delete(row)
            db.commit()
        return True


def merged(store: PersonaStore, builtins: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """The effective persona catalog: built-ins (with any user override applied)
    first, then the user's own personas. Every entry is fully EDITABLE and carries
    its prompt so the UI can show and modify it."""
    user = {p.name: p for p in store.all()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, spec in builtins.items():
        u = user.get(name)
        out.append(
            {
                "name": name,
                "title": (u.title if u and u.title else name.capitalize()),
                "description": (u.description if u else spec.get("description", "")),
                "prompt": (u.prompt if u and u.prompt else spec.get("prompt", "")),
                "builtin": True,
                "overridden": u is not None,
            }
        )
        seen.add(name)
    for name, u in user.items():
        if name in seen:
            continue
        out.append(
            {
                "name": name,
                "title": u.title or name.capitalize(),
                "description": u.description,
                "prompt": u.prompt,
                "builtin": False,
                "overridden": False,
            }
        )
    return out


def resolve_prompt(
    store: PersonaStore, builtins: dict[str, dict[str, str]], want: str
) -> str:
    """The system prompt for a persona ``want``: a user override/creation wins,
    then a built-in, then the value is treated as free-text instructions (the
    long-standing behaviour), falling back to the default assistant prompt."""
    want = (want or "").strip()
    row = store.get(want) if want else None
    if row is not None and row.prompt.strip():
        return row.prompt
    if want in builtins:
        return builtins[want]["prompt"]
    if want:
        return want  # free-text persona instructions, used verbatim
    return builtins.get("assistant", {}).get("prompt", "")
