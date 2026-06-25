"""Skill Registry (§23).

Discovers ``<dir>/<name>/SKILL.md`` bundles, exposes search/list/get, and can
inject named skills' instructions into an agent's system prompt (§11/§23). Skill
search is intentionally simple: case-insensitive token overlap of the query
against each skill's name + description.
"""

from __future__ import annotations

import re
from pathlib import Path

from .loader import SKILL_FILE, Skill, load_skill


def builtin_dir() -> Path:
    """Path to the bundled example skills shipped with Iron Jarvis (§23)."""
    return Path(__file__).resolve().parent / "builtin"


def _tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens used for relevance ranking."""
    return re.findall(r"[a-z0-9]+", text.lower())


class SkillRegistry:
    """In-memory registry of discovered skills (§23)."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    # Allow both ``SkillRegistry.builtin_dir()`` and ``framework.builtin_dir()``.
    builtin_dir = staticmethod(builtin_dir)

    def discover(self, *dirs: Path) -> "SkillRegistry":
        """Load every ``<dir>/<name>/SKILL.md`` found under each ``dir``.

        Missing directories are skipped so callers can pass an as-yet-uncreated
        ``config.home/'skills'`` safely. Returns self for chaining.
        """
        for d in dirs:
            base = Path(d)
            if not base.is_dir():
                continue
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / SKILL_FILE).is_file():
                    skill = load_skill(child)
                    self._skills[skill.name] = skill
        return self

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return [self._skills[n] for n in sorted(self._skills)]

    def search(self, query: str, k: int = 5) -> list[Skill]:
        """Rank skills by token overlap of ``query`` vs name + description."""
        terms = set(_tokens(query))
        if not terms:
            return []
        scored: list[tuple[int, Skill]] = []
        for skill in self._skills.values():
            hay = set(_tokens(f"{skill.name} {skill.description}"))
            score = len(terms & hay)
            if score:
                scored.append((score, skill))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [skill for _, skill in scored[:k]]

    def inject(self, system_prompt: str, skill_names: list[str]) -> str:
        """Append a ``# Skills`` section with each named skill's instructions."""
        blocks: list[str] = []
        for name in skill_names:
            skill = self._skills.get(name)
            if skill is None:
                continue
            blocks.append(f"## {skill.name}\n{skill.instructions}")
        if not blocks:
            return system_prompt
        return system_prompt + "\n\n# Skills\n" + "\n\n".join(blocks)
