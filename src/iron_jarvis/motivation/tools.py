"""Agent-facing Motivation Layer tools (§19 tool interface).

``goal_add`` lets an agent (or the user via the agent loop) record a *standing
goal* Iron Jarvis should carry forward; ``goal_list`` reads them back. Recording
a goal NEVER acts on it — whether a goal ever turns into a self-initiated session
is governed entirely by the goal's autonomy dial + budget and the global
``config.autonomy_enabled`` flag, which are OFF / suggest-only by default. Both
tools are constructed with the assembled ``platform`` (like the scheduling tool)
and operate on ``platform.intent``.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult


class GoalAddTool(Tool):
    """Record a standing goal for Iron Jarvis to deliberate toward later."""

    name = "goal_add"
    description = (
        "Record a STANDING GOAL Iron Jarvis should keep working toward over time "
        "(e.g. 'keep my inbox under 20 unread', 'ship the v2 docs'). This only "
        "records the intent — it never acts on its own. Whether a goal ever leads "
        "to a self-initiated action is gated by its autonomy dial (default "
        "'suggest', i.e. propose-only) and the global autonomy switch (off by "
        "default). Use this for durable objectives, not one-off tasks."
    )
    permission_key = "goal_add"
    # Calm UI redesign (AUDIT §6.3): a goal recorded from chat has an Undo (it
    # removes the goal) — settings.records holds the snapshot/restore. The
    # engine mints the id, so the capture is named after the write.
    reversibility = Reversibility.REVERSIBLE
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "category": {"type": "string"},
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["text"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        from ..settings.records import capture_create_undo

        return await capture_create_undo(self.platform, "goal", "", pending=True)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            rec = self.platform.intent.add_goal(
                args.get("text") or "",
                source="inferred",  # an agent recorded it on the user's behalf
                category=args.get("category", "general"),
                priority=int(args.get("priority", 3)),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        data: dict[str, Any] = {"id": rec.id, "autonomy_level": rec.autonomy_level, "status": rec.status}
        try:
            from ..settings.records import bind_created, create_card

            bind_created(rec.id)
            card = create_card("goal", rec.id, f"{rec.status}, {rec.autonomy_level}", label=f"Goal “{rec.text}”")
        except Exception:  # noqa: BLE001 — a card never fails the tool
            card = None
        if card:
            data["record_change"] = card
        return ToolResult(
            ok=True,
            output=f"recorded standing goal: {rec.text}",
            data=data,
        )


class GoalListTool(Tool):
    """List the standing goals Iron Jarvis is holding."""

    name = "goal_list"
    description = "List the standing goals Iron Jarvis is currently holding (and their status/dial)."
    permission_key = "goal_list"
    input_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["active", "paused", "done", "abandoned"]}
        },
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        goals = self.platform.intent.list_goals(status=args.get("status"))
        results = [
            {
                "id": g.id, "text": g.text, "category": g.category,
                "priority": g.priority, "autonomy_level": g.autonomy_level,
                "status": g.status,
            }
            for g in goals
        ]
        output = "\n".join(
            f"- [{r['status']} p{r['priority']} {r['autonomy_level']}] {r['text']}"
            for r in results
        )
        return ToolResult(ok=True, output=output, data={"goals": results, "count": len(results)})


def goal_tools(platform) -> list[Tool]:
    """Build the Motivation Layer agent tools bound to the assembled ``platform``."""
    return [GoalAddTool(platform), GoalListTool(platform)]
