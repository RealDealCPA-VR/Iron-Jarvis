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


class PersonaError(ValueError):
    """A persona could not be saved; ``str()`` is the route's own words."""


def _saved(store: PersonaStore, builtins: dict[str, dict[str, str]], slug: str) -> dict[str, Any] | None:
    return next((p for p in merged(store, builtins) if p["name"] == slug), None)


def save_persona_record(
    engine: Engine,
    builtins: dict[str, dict[str, str]],
    name: str,
    *,
    title: str = "",
    description: str = "",
    prompt: str = "",
) -> dict[str, Any]:
    """Create or update the persona ``name`` (a built-in name -> an override;
    a new name -> a new persona) — THE body of ``PUT /chat/personas/{name}``
    (v1.438.0: lifted so chat's ``persona_update`` takes the same path).
    Raises :class:`PersonaError` for an empty prompt. Blocking."""
    slug = slugify(name)
    if not (prompt or "").strip():
        raise PersonaError("a persona prompt is required")
    store = PersonaStore(engine)
    store.upsert(
        slug,
        title=((title or "").strip() or slug.capitalize()),
        description=(description or "").strip(),
        prompt=(prompt or "").strip(),
    )
    return {"saved": slug, "persona": _saved(store, builtins, slug)}


def create_persona_record(
    engine: Engine,
    builtins: dict[str, dict[str, str]],
    *,
    name: str = "",
    title: str = "",
    description: str = "",
    prompt: str = "",
) -> dict[str, Any]:
    """Create a NEW persona, its id slugified from ``name`` or ``title`` —
    THE body of ``POST /chat/personas`` (v1.438.0: lifted for chat's
    ``persona_create``). Raises :class:`PersonaError`. Blocking."""
    if not (prompt or "").strip():
        raise PersonaError("a persona prompt is required")
    slug = slugify(name or title)
    store = PersonaStore(engine)
    store.upsert(
        slug,
        title=((title or "").strip() or slug.capitalize()),
        description=(description or "").strip(),
        prompt=(prompt or "").strip(),
    )
    return {"created": slug, "persona": _saved(store, builtins, slug)}


def delete_persona_record(engine: Engine, builtins: dict[str, dict[str, str]], name: str) -> dict[str, Any]:
    """Remove a saved persona — reverts a built-in to its default, or deletes
    a custom one — THE body of ``DELETE /chat/personas/{name}`` (v1.438.0:
    lifted for chat's ``persona_delete``). Raises ``KeyError`` when the name
    is neither saved nor a built-in. Blocking."""
    slug = slugify(name)
    removed = PersonaStore(engine).delete(slug)
    if not removed and slug not in builtins:
        raise KeyError(slug)
    return {"deleted": slug, "reverted_to_builtin": slug in builtins}


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
    store: PersonaStore,
    builtins: dict[str, dict[str, str]],
    want: str,
    *,
    default: str = "",
) -> str:
    """The system prompt for a persona ``want``: a user override/creation wins,
    then a built-in, then the value is treated as free-text instructions (the
    long-standing behaviour), falling back to the default assistant prompt.

    ``default`` (the configured ``default_persona``) substitutes for an EMPTY
    ``want`` at the top of the chain, so it inherits the full precedence: a
    user's override of the default slug wins over the raw built-in (the old
    empty-want path jumped straight to the raw builtin "assistant"), and a
    free-text default applies verbatim. An explicit ``want`` always beats it.
    """
    want = (want or "").strip() or (default or "").strip()
    row = store.get(want) if want else None
    if row is not None and row.prompt.strip():
        return row.prompt
    if want in builtins:
        return builtins[want]["prompt"]
    if want:
        return want  # free-text persona instructions, used verbatim
    return builtins.get("assistant", {}).get("prompt", "")
