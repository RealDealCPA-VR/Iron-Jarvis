"""Agent-facing scheduling tool (§19 tool interface, §25 cron).

``schedule_create`` lets an agent register its *own* durable scheduled task
through the tool loop. It is constructed with the assembled ``platform`` (like
:class:`~iron_jarvis.agents.delegate_tool.DelegateTool`) and acts on
``platform.scheduler``. ``schedule_tools(platform)`` builds it for registration.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult


class ScheduleCreateTool(Tool):
    """Create a durable scheduled task (cron, one-time date, or interval)."""

    name = "schedule_create"
    description = (
        "Create a persistent scheduled task. Supply exactly one trigger: `cron` "
        "(a 5-field crontab string), `run_at` (an ISO-8601 datetime for a "
        "one-time fire), or `interval_seconds` (a fixed repeat period). `kind` is "
        "'workflow' (default — `payload` is a workflow definition) or 'event' "
        "(`payload` carries the event to publish). Returns the "
        "created task name and its next run time."
    )
    permission_key = "schedule_create"
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "cron": {"type": "string"},
            "run_at": {"type": "string"},
            "interval_seconds": {"type": "integer", "minimum": 1},
            "kind": {"type": "string", "enum": ["workflow", "event"]},
            "payload": {"type": "object"},
        },
        "required": ["name"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        from . import knobs as _knobs

        payload = args.get("payload")
        kind = args.get("kind", "workflow")
        # THE SCRIPT KNOB IS THE USER'S (v1.299.0): a pre-run command runs on
        # the host with the shell tool's confinement, so a schedule an AGENT
        # makes may never carry one — refused by name, before anything else,
        # even though this tool's kinds (workflow/event) could not fire it.
        if isinstance(payload, dict) and payload.get("script") not in (None, {}):
            return ToolResult(ok=False, error=_knobs.SCRIPT_FROM_AGENT_REFUSAL)
        try:
            # The same knob rules the routes apply (``from_user=False``); the
            # folder probe inside is blocking, so off the loop.
            await asyncio.to_thread(
                _knobs.validate_knobs,
                payload,
                name=str(args.get("name") or ""),
                kind=str(kind),
                scheduler=self.platform.scheduler,
                skills=getattr(self.platform, "skills", None),
                from_user=False,
            )
        except _knobs.KnobError as exc:
            return ToolResult(ok=False, error=exc.detail)
        try:
            rec = self.platform.scheduler.add_task(
                args.get("name") or "",
                args.get("cron"),
                run_at=args.get("run_at"),
                interval_seconds=args.get("interval_seconds"),
                kind=args.get("kind", "workflow"),
                payload=args.get("payload"),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))

        next_run = rec.next_run.isoformat() if rec.next_run is not None else None
        return ToolResult(
            ok=True,
            output=f"scheduled task '{rec.name}' (next run: {next_run})",
            data={
                "name": rec.name,
                "trigger_type": rec.trigger_type,
                "kind": rec.kind,
                "next_run": next_run,
            },
        )


def schedule_tools(platform) -> list[Tool]:
    """Build the scheduling tool bound to the assembled ``platform``."""
    return [ScheduleCreateTool(platform)]
