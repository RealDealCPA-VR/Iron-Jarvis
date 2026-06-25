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


def skill_tools(registry: SkillRegistry) -> list[Tool]:
    """Build the skill tools bound to ``registry`` (§23)."""
    return [SkillSearchTool(registry), SkillLoadTool(registry)]
