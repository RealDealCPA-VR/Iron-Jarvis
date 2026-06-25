"""Agent-facing long-term-memory tools (§19 tool interface).

Two thin tools over :class:`LongTermMemory`, each constructed with the manager
injected:

* ``ltm_search`` — search one source or merge across all connectors.
* ``ltm_append`` — append a note/page (defaults to the built-in ``brain`` store).

``ltm_tools(manager)`` builds the pair for registration in the tool registry.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .manager import LongTermMemory


class LTMSearchTool(Tool):
    """Search long-term memory connectors (Obsidian / brain / Notion)."""

    name = "ltm_search"
    description = (
        "Search long-term memory (external knowledge stores: Obsidian vault, "
        "markdown brain, Notion). Omit `source` to merge across all connectors."
    )
    permission_key = "ltm_search"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1},
            "source": {"type": "string"},
        },
        "required": ["query"],
    }

    def __init__(self, manager: LongTermMemory) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        k = int(args.get("k", 5))
        source = args.get("source")
        try:
            hits = self.manager.search(args["query"], k=k, source=source)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        output = "\n".join(
            f"[{h['source']}] {h['title']}: {h['snippet']}" for h in hits
        )
        return ToolResult(
            ok=True, output=output, data={"results": hits, "count": len(hits)}
        )


class LTMAppendTool(Tool):
    """Append a note/page to a long-term memory store."""

    name = "ltm_append"
    description = (
        "Append a titled note to a long-term memory store. Defaults to the "
        "built-in `brain`; pass `source` to target Obsidian or Notion."
    )
    permission_key = "ltm_append"
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": ["title", "content"],
    }

    def __init__(self, manager: LongTermMemory) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        source = args.get("source") or self.manager.default_source()
        if source is None:
            return ToolResult(ok=False, error="no LTM connector registered")
        try:
            ref = self.manager.append(args["title"], args["content"], source)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(
            ok=True,
            output=f"appended to {source}: {ref}",
            data={"ref": ref, "source": source},
        )


def ltm_tools(manager: LongTermMemory) -> list[Tool]:
    """Build the LTM tool pair bound to a single ``LongTermMemory`` instance."""
    return [LTMSearchTool(manager), LTMAppendTool(manager)]
