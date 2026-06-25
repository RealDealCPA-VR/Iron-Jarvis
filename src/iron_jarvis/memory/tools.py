"""Memory tools (§19 tool interface, §21 layered memory).

Three thin tools over ``MemoryLayers`` — write, read, and similarity search —
each constructed with the layer manager injected. ``memory_tools(layers)`` builds
the trio for registration in the tool registry.
"""

from __future__ import annotations

from typing import Any

from ..core.events import EventType
from ..tools.base import Tool, ToolContext, ToolResult
from .layers import MemoryLayers


class MemoryWriteTool(Tool):
    """Store or update a memory entry in a layer (§21)."""

    name = "memory_write"
    description = "Write or update a memory entry (layer/key/text) in layered memory."
    permission_key = "memory_write"
    input_schema = {
        "type": "object",
        "properties": {
            "layer": {"type": "string", "enum": list(MemoryLayers.LAYERS)},
            "key": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["layer", "key", "text"],
    }

    def __init__(self, layers: MemoryLayers) -> None:
        self.layers = layers

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        record = self.layers.write(args["layer"], args["key"], args["text"])
        await ctx.event_bus.publish(
            EventType.MEMORY_UPDATED,
            {"layer": record.layer, "key": record.key, "id": record.id},
            session_id=ctx.session_id,
        )
        return ToolResult(
            ok=True,
            output=f"stored memory '{record.key}' in layer '{record.layer}'",
            data={"id": record.id, "layer": record.layer, "key": record.key},
        )


class MemoryReadTool(Tool):
    """Read a memory entry by (layer, key) (§21)."""

    name = "memory_read"
    description = "Read a memory entry by layer and key; empty result if absent."
    permission_key = "memory_read"
    input_schema = {
        "type": "object",
        "properties": {
            "layer": {"type": "string", "enum": list(MemoryLayers.LAYERS)},
            "key": {"type": "string"},
        },
        "required": ["layer", "key"],
    }

    def __init__(self, layers: MemoryLayers) -> None:
        self.layers = layers

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = self.layers.read(args["layer"], args["key"])
        if text is None:
            return ToolResult(ok=True, output="", data={"found": False})
        return ToolResult(ok=True, output=text, data={"found": True})


class MemorySearchTool(Tool):
    """Cosine-similarity search across memory (§22)."""

    name = "memory_search"
    description = "Search layered memory by semantic similarity; returns top-k matches."
    permission_key = "memory_search"
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1},
        },
        "required": ["query"],
    }

    def __init__(self, layers: MemoryLayers) -> None:
        self.layers = layers

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        k = int(args.get("k", 5))
        hits = self.layers.search(args["query"], k=k)
        results = [
            {
                "id": rec.id,
                "layer": rec.layer,
                "key": rec.key,
                "text": rec.text,
                "score": score,
            }
            for rec, score in hits
        ]
        output = "\n".join(
            f"[{r['score']:.3f}] ({r['layer']}/{r['key']}) {r['text']}" for r in results
        )
        return ToolResult(
            ok=True, output=output, data={"results": results, "count": len(results)}
        )


def memory_tools(layers: MemoryLayers) -> list[Tool]:
    """Build the memory tool trio bound to a single ``MemoryLayers`` instance."""
    return [
        MemoryWriteTool(layers),
        MemoryReadTool(layers),
        MemorySearchTool(layers),
    ]
