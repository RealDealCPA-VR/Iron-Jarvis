"""Agent-facing Sentinel tool (§19 tool interface).

``sentinel_add`` lets an agent (or the user via the agent loop) register an
always-on watcher. Registering a Sentinel NEVER acts: a fired Sentinel only mints
a SUGGEST-ONLY proposal into the Motivation Layer backlog, and even that runner is
OFF unless ``config.sentinels_enabled`` is set. Built with the assembled
``platform`` (like the scheduling tool) and operates on ``platform.sentinels``.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult


class SentinelAddTool(Tool):
    """Create an always-on watcher that surfaces noticed changes as suggestions."""

    name = "sentinel_add"
    description = (
        "Create an always-on SENTINEL that watches your machine and surfaces "
        "noticed changes as SUGGESTIONS (never actions). The only kind today is "
        "'file': give a `path` (a file, directory, or glob) and an optional `glob` "
        "pattern; when matching files appear or change, the Sentinel mints a "
        "suggest-only proposal carrying `task` into the backlog for you to approve. "
        "It never runs anything on its own — execution still flows through the "
        "autonomy dial + budget + approval, and the whole watcher subsystem is OFF "
        "unless sentinels are enabled in Settings."
    )
    permission_key = "sentinel_add"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "task": {"type": "string"},
            "kind": {"type": "string", "enum": ["file"]},
            "agent_type": {"type": "string"},
            "risk": {"type": "string", "enum": ["low", "med"]},
        },
        "required": ["name", "path"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            rec = self.platform.sentinels.add(
                args.get("name") or "",
                path=args.get("path"),
                glob=args.get("glob"),
                task=args.get("task", ""),
                kind=args.get("kind", "file"),
                agent_type=args.get("agent_type", "builder"),
                risk=args.get("risk", "low"),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(
            ok=True,
            output=f"created sentinel '{rec.name}' (kind={rec.kind}); suggest-only",
            data={
                "id": rec.id,
                "name": rec.name,
                "kind": rec.kind,
                "enabled": rec.enabled,
            },
        )


def sentinel_tools(platform) -> list[Tool]:
    """Build the Sentinel agent tool bound to the assembled ``platform``."""
    return [SentinelAddTool(platform)]
