"""Skill tools (§23 + §19).

Exposes the registry to agents: ``skill_search`` finds relevant skills and
``skill_load`` returns a skill's full instructions so the agent can apply them.
Both wrap a :class:`SkillRegistry` injected via the constructor.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .framework import SkillRegistry


class SkillSearchTool(Tool):
    """Find skills relevant to a query (§23)."""

    name = "skill_search"
    description = "Search available skills by topic; returns matching names and descriptions."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer"},
        },
        "required": ["query"],
    }

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", ""))
        k = int(args.get("k", 5))
        hits = self._registry.search(query, k)
        if not hits:
            return ToolResult(ok=True, output="(no matching skills)", data={"skills": []})
        lines = [f"{s.name}: {s.description}" for s in hits]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"skills": [{"name": s.name, "description": s.description} for s in hits]},
        )


class SkillLoadTool(Tool):
    """Load a skill's instructions by name (§23)."""

    name = "skill_load"
    description = "Load a skill by name and return its full instructions."
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("name", ""))
        skill = self._registry.get(name)
        if skill is None:
            return ToolResult(ok=False, error=f"unknown skill '{name}'")
        return ToolResult(
            ok=True,
            output=skill.instructions,
            data={
                "name": skill.name,
                "description": skill.description,
                "examples": skill.examples,
                "scripts": skill.scripts,
                "templates": skill.templates,
            },
        )


class SkillCreateTool(Tool):
    """Author a durable skill from the loop (v1.90.0) — how the agent keeps a
    PROVEN solution (a validated formula workflow, a working script) for
    future reuse instead of re-deriving it every time."""

    name = "skill_create"
    description = (
        "SAVE a proven approach as a reusable skill for future conversations: "
        "name + one-line description + full markdown instructions (include "
        "the exact formulas/spec/code that worked and how they were "
        "validated). The skill appears on the Skills page immediately and is "
        "searchable via skill_search. Use after solving something non-obvious "
        "— e.g. a validated financial-statement formula workflow, or the "
        "run_code script that cracked a task."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "instructions": {"type": "string", "description": "Full markdown playbook"},
        },
        "required": ["name", "description", "instructions"],
    }

    def __init__(self, registry: SkillRegistry, config: Any) -> None:
        self._registry = registry
        self._config = config

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .loader import save_skill

        try:
            path = save_skill(
                self._config.home / "skills",
                str(args.get("name", "")),
                str(args.get("description", "")),
                str(args.get("instructions", "")),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        # Live rescan (same repopulate the daemon's /skills endpoints use) so
        # the new skill is searchable in THIS session, not after a restart.
        try:
            self._registry.repopulate(
                self._config.home,
                getattr(self._config, "extra_skill_paths", None),
            )
        except Exception:  # noqa: BLE001 — the file is saved; a rescan hiccup
            pass  # must not fail the creation (next boot picks it up)
        return ToolResult(
            ok=True,
            output=f"skill saved: {path}",
            data={"path": str(path), "name": str(args.get("name", "")).strip()},
        )


def skill_tools(registry: SkillRegistry, config: Any = None) -> list[Tool]:
    """Build the skill tools bound to ``registry`` (§23). With ``config`` the
    set includes ``skill_create`` (needs the home dir + rescan paths)."""
    tools: list[Tool] = [SkillSearchTool(registry), SkillLoadTool(registry)]
    if config is not None:
        tools.append(SkillCreateTool(registry, config))
    return tools
