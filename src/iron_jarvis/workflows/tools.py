"""Agent-facing workflow-authoring tool (§19 tool interface, §24).

``workflow_create`` lets an agent save its *own* workflow definition through the
tool loop. The workflow persists as a
:class:`~iron_jarvis.workflows.models.WorkflowRecord` (via :class:`WorkflowStore`)
so the user sees it in the dashboard and the engine can re-load + run it. The
tool is constructed with the assembled ``platform`` (like
:class:`~iron_jarvis.agents.delegate_tool.DelegateTool`) and acts on
``platform.engine``. ``workflow_tools(platform)`` builds it for registration.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .store import WorkflowStore


class WorkflowCreateTool(Tool):
    """Save a named, ordered workflow so it persists and shows in the dashboard."""

    name = "workflow_create"
    description = (
        "Save a reusable workflow that persists (the user sees it in the "
        "dashboard and it can be scheduled/run later). `steps` is an ordered "
        "list of {name, agent, task} objects — each step runs `agent` on `task`. "
        "Re-using a `name` updates the existing workflow in place. Returns the "
        "saved workflow name and step count."
    )
    permission_key = "workflow_create"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "agent": {"type": "string"},
                        "task": {"type": "string"},
                    },
                },
            },
            "description": {"type": "string"},
        },
        "required": ["name", "steps"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = args.get("name") or ""
        if not name:
            return ToolResult(ok=False, error="name is required")
        steps = args.get("steps") or []
        rec = WorkflowStore(self.platform.engine).save(
            name, steps, args.get("description", "")
        )
        return ToolResult(
            ok=True,
            output=f"saved workflow '{rec.name}' with {len(steps)} step(s)",
            data={"name": rec.name, "steps": len(steps), "id": rec.id},
        )


def workflow_tools(platform) -> list[Tool]:
    """Build the workflow-authoring tool bound to the assembled ``platform``."""
    return [WorkflowCreateTool(platform)]
