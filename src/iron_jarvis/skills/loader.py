"""Skill loading (§23).

Parses a ``SKILL.md`` file with YAML frontmatter into a :class:`Skill`. The
frontmatter carries ``name`` / ``description``; the markdown body becomes the
skill's ``instructions``. Optional ``examples/``, ``scripts/`` and ``templates/``
subfolders are discovered as filename lists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SKILL_FILE = "SKILL.md"


def slugify(name: str) -> str:
    """A safe directory slug for a skill name (letters/digits/dash)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "skill"


def save_skill(skills_root: Path, name: str, description: str, instructions: str) -> Path:
    """Write a user-authored ``<skills_root>/<slug>/SKILL.md`` and return its dir.

    The frontmatter carries the display name + description; the body is the
    instructions. Overwrites an existing skill of the same slug (edit in place).
    Raises ``ValueError`` on an empty name or empty instructions.
    """
    name = (name or "").strip()
    instructions = (instructions or "").strip()
    if not name:
        raise ValueError("skill name is required")
    if not instructions:
        raise ValueError("skill instructions are required")
    skill_dir = Path(skills_root) / slugify(name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    # yaml.safe_dump escapes special chars, so a name with ':' or quotes is safe.
    front = yaml.safe_dump(
        {"name": name, "description": (description or "").strip()},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    (skill_dir / SKILL_FILE).write_text(
        f"---\n{front}\n---\n\n{instructions}\n", encoding="utf-8"
    )
    return skill_dir


@dataclass
class Skill:
    """A reusable instruction bundle (§23)."""

    name: str
    description: str
    instructions: str
    dir: Path
    examples: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    templates: list[str] = field(default_factory=list)
    #: Where this skill came from — "builtin", "user", "claude", "codex", or
    #: "custom" (a user-added search path). Surfaced in the dashboard so it's
    #: clear which skills are Iron Jarvis's own vs pulled in from Claude/Codex.
    source: str = "user"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``--- yaml --- body`` into (metadata, body)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                meta = {}
            return meta, parts[2].lstrip("\n")
    return {}, text


def _list_files(directory: Path) -> list[str]:
    """Return sorted filenames directly inside ``directory`` (empty if absent)."""
    if directory.is_dir():
        return sorted(p.name for p in directory.iterdir() if p.is_file())
    return []


def load_skill(dir: Path, source: str = "user") -> Skill:
    """Load ``dir/SKILL.md`` into a :class:`Skill` (§23).

    ``source`` tags where the skill came from (builtin/user/claude/codex/custom).
    Raises ``FileNotFoundError`` with a clear message if SKILL.md is missing.
    """
    skill_dir = Path(dir)
    md = skill_dir / SKILL_FILE
    if not md.is_file():
        raise FileNotFoundError(f"no {SKILL_FILE} found in skill dir: {skill_dir}")

    meta, body = _parse_frontmatter(md.read_text(encoding="utf-8"))
    name = str(meta.get("name") or skill_dir.name).strip()
    description = str(meta.get("description") or "").strip()

    return Skill(
        name=name,
        description=description,
        instructions=body.strip(),
        dir=skill_dir,
        examples=_list_files(skill_dir / "examples"),
        scripts=_list_files(skill_dir / "scripts"),
        templates=_list_files(skill_dir / "templates"),
        source=source,
    )
