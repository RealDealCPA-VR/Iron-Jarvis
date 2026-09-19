"""Which project a folder belongs to, and the block that says so (v1.280.0).

The dashboard has done this since the Build pane grew a chat: `projectForCwd`
in `components/terminal/paneChatCore.ts` picks the most specific project root
that contains the pane's folder. The daemon had no counterpart, so the Build
pane's AI-assist bar (`POST /terminals/{id}/ai`) answered inside a project
folder knowing nothing about the project. This is the one server-side answer
to "what is the user working on here?" for a path.

Rules, identical to the client's: a root matches at a SEGMENT boundary
(``C:\\work`` must not claim ``C:\\workshop``); the longest root wins when
projects nest; archived projects never claim anything; comparison is
case-insensitive with both separators folded, because the daemon and the
pane both speak Windows paths. Never raises: a broken store answers ``None``.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.models import Project

#: Caps for the block the assist prompt carries — the chat lanes' own limits.
_INSTRUCTIONS_CHARS = 2000
_BRIEF_CHARS = 1500


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").lower()


def project_for_path(engine: Any, path: str) -> Project | None:
    """The most specific ACTIVE project whose root contains ``path``, or None."""
    target = _norm(path)
    if not target:
        return None
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(select(Project)))
            best: Project | None = None
            best_len = -1
            for p in rows:
                if (getattr(p, "status", "active") or "active") == "archived":
                    continue
                root = _norm(getattr(p, "root", "") or "")
                if not root:
                    continue
                if target != root and not target.startswith(root + "/"):
                    continue
                if len(root) > best_len:
                    best, best_len = p, len(root)
            if best is None:
                return None
            # Detach a plain copy so the caller never touches an expired row.
            return Project(
                id=best.id,
                name=best.name,
                root=best.root,
                brief=best.brief,
                instructions=best.instructions,
                default_provider=best.default_provider,
                default_model=best.default_model,
                memory_sources=best.memory_sources,
                status=best.status,
            )
    except Exception:  # noqa: BLE001 — a store that cannot answer is "no project"
        return None


def project_context_block(project: Project | None) -> str:
    """``# Project`` — name, instructions, brief, folder — or ``""``."""
    if project is None:
        return ""
    lines = ["# Project", f"- Name: {project.name}"]
    instructions = (project.instructions or "").strip()
    if instructions:
        lines.append(f"- Instructions: {instructions[:_INSTRUCTIONS_CHARS]}")
    brief = (project.brief or "").strip()
    if brief:
        lines.append(f"- Brief: {brief[:_BRIEF_CHARS]}")
    if (project.root or "").strip():
        lines.append(f"- Project folder: {project.root.strip()}")
    return "\n".join(lines)


__all__ = ["project_for_path", "project_context_block"]
