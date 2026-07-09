"""Agent-facing semantic ``recall`` tool (§22 retrieval — the Memory Fabric).

``recall`` is the single "remember anything" entry point. It delegates to the
:class:`~iron_jarvis.memory.fabric.MemoryFabric`, which federates EVERY memory
store — indexed file roots, long-term memory (brain/Obsidian/Notion), the
layered memory graph, a project's attached knowledge, self-correction lessons,
and past sessions — and returns ranked, de-duplicated snippets. One tool, one
call, the whole of what Iron Jarvis knows.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .fabric import MemoryFabric


class RecallTool(Tool):
    """Federated semantic recall across every Iron Jarvis memory store."""

    name = "recall"
    returns_untrusted_content = True  # blends files + notes + knowledge (planted content)
    description = (
        "Recall anything Iron Jarvis knows, ranked by MEANING (not just "
        "substring), across ALL memory at once: indexed file roots, long-term "
        "memory (brain / Obsidian / Notion), the memory graph, the current "
        "project's attached knowledge, lessons learned, and past sessions. "
        "Returns ranked snippets tagged with their source. Use this whenever you "
        "need context, prior work, notes, or what happened before — broader and "
        "smarter than a grep. Stays within configured roots; never reads "
        "protected paths."
    )
    permission_key = "recall"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1},
            "project_id": {
                "type": "string",
                "description": "Optional: also search this project's attached knowledge.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional filter: any of files/notes/memory/knowledge/lessons/sessions.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, fabric: MemoryFabric) -> None:
        self.fabric = fabric

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = args.get("query", "")
        k = int(args.get("k", 6))
        project_id = args.get("project_id") or None
        sources = args.get("sources") or None
        try:
            hits = self.fabric.recall(query, k=k, project_id=project_id, sources=sources)
        except Exception as exc:  # a recall must never crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        results = [h.as_dict() for h in hits]
        lines: list[str] = []
        for h in hits:
            head = h.title or h.ref or h.source
            lines.append(f"[{h.source}] {head}: {h.snippet}")
        # Per-source breakdown so callers/tests can see the federation at work.
        by_source: dict[str, int] = {}
        for h in hits:
            by_source[h.source] = by_source.get(h.source, 0) + 1
        data = {"results": results, "count": len(results), "by_source": by_source}
        return ToolResult(ok=True, output="\n".join(lines), data=data)


def recall_tools(fabric: MemoryFabric) -> list[Tool]:
    """Build the recall tool bound to the shared Memory Fabric."""
    return [RecallTool(fabric)]
