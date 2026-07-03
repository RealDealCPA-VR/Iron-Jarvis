"""Skills Framework (§23).

Skills are reusable instruction bundles (instructions + optional examples,
scripts, templates, documents, workflows). They live at
``.ironjarvis/skills/<name>/SKILL.md`` and are loaded into a :class:`SkillRegistry`
that the orchestrator can search and inject into an agent's system prompt.
"""

from __future__ import annotations

from .framework import SkillRegistry, builtin_dir, external_skill_roots
from .loader import Skill, load_skill, save_skill, slugify
from .tools import SkillLoadTool, SkillSearchTool, skill_tools

__all__ = [
    "Skill",
    "load_skill",
    "save_skill",
    "slugify",
    "SkillRegistry",
    "builtin_dir",
    "external_skill_roots",
    "SkillSearchTool",
    "SkillLoadTool",
    "skill_tools",
]
