"""Agent-facing learning tools (§19 tool interface).

Two thin tools over :class:`LearningEngine`, each constructed with the engine
injected:

* ``remember_preference`` — let the agent record a preference it inferred during
  a conversation, so future runs honour it. Permission default ``allow``.
* ``recall_lessons``      — let the agent consult the lessons it has accumulated.
  Permission default ``allow``.
"""

from __future__ import annotations

from typing import Any

from ..core.events import EventType
from ..tools.base import Tool, ToolContext, ToolResult
from .engine import LearningEngine


class RememberPreferenceTool(Tool):
    """Record a durable user preference the agent inferred mid-conversation."""

    name = "remember_preference"
    description = (
        "Record a durable user preference you inferred (e.g. 'prefers concise "
        "bullet-point summaries') so every future run honours it."
    )
    permission_key = "remember_preference"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self, learning: LearningEngine) -> None:
        self.learning = learning

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        record = self.learning.note_preference(args["text"])
        await ctx.event_bus.publish(
            EventType.MEMORY_UPDATED,
            {"kind": "preference", "id": record.id, "weight": record.weight},
            session_id=ctx.session_id,
        )
        return ToolResult(
            ok=True,
            output=f"remembered preference: {record.text}",
            data={
                "id": record.id,
                "weight": record.weight,
                "scope": record.scope,
                # v1.282.0: the sentence itself, for the receipt under the reply.
                "text": record.text,
            },
        )


class RecallLessonsTool(Tool):
    """Recall the lessons + preferences learned about working with the user."""

    name = "recall_lessons"
    description = (
        "Recall the lessons and preferences learned about working with the user, "
        "ordered by importance."
    )
    permission_key = "recall_lessons"
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["user", "project"]},
            "limit": {"type": "integer", "minimum": 1},
        },
    }

    def __init__(self, learning: LearningEngine) -> None:
        self.learning = learning

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        scope = args.get("scope", "user")
        limit = int(args.get("limit", 12))
        items = self.learning.lessons(scope=scope, limit=limit)
        results = [
            {
                "id": lesson.id,
                "text": lesson.text,
                "source": lesson.source,
                "weight": lesson.weight,
                "scope": lesson.scope,
            }
            for lesson in items
        ]
        output = "\n".join(
            f"- [{r['source']} w{r['weight']}] {r['text']}" for r in results
        )
        return ToolResult(
            ok=True,
            output=output,
            data={"lessons": results, "count": len(results)},
        )


def learning_tools(learning: LearningEngine) -> list[Tool]:
    """Build the learning tool pair bound to a single :class:`LearningEngine`."""
    return [RememberPreferenceTool(learning), RecallLessonsTool(learning)]
