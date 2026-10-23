"""A skill from the session's OWN folder, picked with "/" for one message
(v1.373.0, IJ harness part 2 Phase 2).

The "/" menu in a Build session's box offers the user's skills (``GET
/skills``; one rides the turn as the chat's "/skill" playbook through
``ChatBody.skill``) AND the session folder's own harness skills
(``<folder>/.ironjarvis/harness/skills/<name>/SKILL.md``, the ones
``components.read_project_layer`` validates and the harness prompt already
lists by their trigger line). A folder skill is not in the app's skill
registry, so it rides the turn here:

* the build route reads its SKILL.md through the SAME validator (only a
  skill of a VALID layer is offered or read; the file is inside the harness
  folder, capped, UTF-8), clips it to the chat playbook's 8,000 characters
  and scans it through ``core/promptguard`` — it is untrusted text in the
  user's folder, exactly as the layer's other texts are;
* it sets :data:`PICKED` while the turn's lane is built and its driver task
  created (the task copies the context, like the Browser pane's address in
  :mod:`.preview`);
* the harness block (``mode.HarnessTurn.block``) adds it as the playbook for
  this message, in the chat's own words. Like the date, it is NOT part of the
  harness fingerprint (it changes per message).

Nothing here runs a script or follows a link out of the harness folder.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: ``(name, playbook)`` of the folder skill picked for this turn; ("", "") = none.
PICKED: ContextVar[tuple[str, str]] = ContextVar("ij_picked_folder_skill", default=("", ""))

#: The playbook's cap — the chat's "/skill" cap (``routes/chat``: ``[:8000]``).
PLAYBOOK_CHARS = 8000


def set_picked(name: str, playbook: str) -> Token[tuple[str, str]]:
    """Arm this turn's folder skill (``name`` "" = none)."""
    return PICKED.set((str(name or ""), str(playbook or "")))


def reset_picked(token: Token[tuple[str, str]]) -> None:
    PICKED.reset(token)


def prompt_section() -> str:
    """The picked folder skill as the turn's playbook ("" when none) — the
    chat's "/skill" words, with where it came from."""
    name, playbook = PICKED.get()
    if not name or not playbook.strip():
        return ""
    return (
        f"\n\n# Skill invoked by the user: {name} (from this folder's harness skills)\n"
        "FOLLOW this playbook for this request.\n" + playbook[:PLAYBOOK_CHARS]
    )


def _layer(folder: Any, known_tools: "Iterable[str] | None", event_bus: Any, session_id: "str | None"):
    from .components import read_project_layer

    return read_project_layer(folder, known_tools=known_tools, event_bus=event_bus, session_id=session_id)


def folder_skills(folder: Any, *, known_tools: "Iterable[str] | None" = None) -> tuple[list[dict[str, str]], str]:
    """The folder's harness skills as ``[{name, description, rel}]`` (the
    trigger line is the description), and the layer's note ("" when it is
    valid or absent — an INVALID layer offers nothing and says why). Only a
    valid layer's skills are offered: the turn would not read the others.
    BLOCKING (stats + small reads). Never raises."""
    try:
        layer = _layer(folder, known_tools, None, None)
    except Exception:  # noqa: BLE001 — a project file never breaks the list
        log.warning("listing a folder's harness skills failed", exc_info=True)
        return [], ""
    if not layer.used:
        return [], layer.note or ""
    return [{"name": s.name, "description": s.trigger, "rel": s.rel} for s in layer.skills], ""


def read_playbook(
    folder: Any,
    name: str,
    *,
    known_tools: "Iterable[str] | None" = None,
    event_bus: Any = None,
    session_id: "str | None" = None,
) -> "str | None":
    """The playbook text of the folder skill ``name`` — its SKILL.md without
    the header, clipped to :data:`PLAYBOOK_CHARS` and scanned — or None when
    the folder's VALID layer has no skill by that name (or it cannot be read
    now). BLOCKING. Never raises."""
    from ..core.promptguard import guard
    from ..projects.folder_rules import _usable_folder
    from .components import HARNESS_PARTS, MAX_SKILL_CHARS, LayerInvalid, _frontmatter, _inside, _read_text

    wanted = str(name or "").strip()
    if not wanted:
        return None
    try:
        layer = _layer(folder, known_tools, event_bus, session_id)
        if not layer.used:
            return None
        skill = next((s for s in layer.skills if s.name == wanted), None)
        base = _usable_folder(folder)
        if skill is None or base is None:
            return None
        path = base.joinpath(*skill.rel.split("/"))
        hroot = base.joinpath(*HARNESS_PARTS).resolve()
        if not _inside(path, hroot):
            return None
        try:
            text = _read_text(path, MAX_SKILL_CHARS, f"skills/{wanted}/SKILL.md")
        except LayerInvalid:
            return None
        _fields, body = _frontmatter(text)
        body = body.strip()[:PLAYBOOK_CHARS]
        if not body:
            return None
        return guard(
            body, source=f"project harness skill {wanted}", event_bus=event_bus,
            session_id=session_id, cap=None,
        )
    except Exception:  # noqa: BLE001 — a project file must never break a turn
        log.warning("reading a folder skill's playbook failed", exc_info=True)
        return None


__all__ = [
    "PICKED",
    "PLAYBOOK_CHARS",
    "folder_skills",
    "prompt_section",
    "read_playbook",
    "reset_picked",
    "set_picked",
]
