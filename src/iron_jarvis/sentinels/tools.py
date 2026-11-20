"""Agent-facing Sentinel tool (§19 tool interface).

``sentinel_add`` lets an agent (or the user via the agent loop) register an
always-on watcher. Registering a Sentinel NEVER acts: a fired Sentinel only mints
a SUGGEST-ONLY proposal into the Motivation Layer backlog, and even that runner is
OFF unless ``config.sentinels_enabled`` is set. Built with the assembled
``platform`` (like the scheduling tool) and operates on ``platform.sentinels``.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult


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
    # Calm UI redesign (AUDIT §6.3): a sentinel made from chat has an Undo (it
    # removes the sentinel) — settings.records holds the snapshot/restore.
    reversibility = Reversibility.REVERSIBLE
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
    # v1.489.0: WHICH folder to watch is a detail only the user has. Seen
    # live: "keep an eye on the folder called bookings on my desktop" ended on
    # a question in words (or on "I can't read that folder") with no call.
    # Left out, or given as a bare name, the user is asked for the full
    # location on a card before the approval card (tools/details).
    user_details = {
        "path": {
            "title": "The folder's full location",
            "what": "the full location of the folder to watch",
            "description": "Starting with its drive, for example C:\\Users\\you\\Desktop\\bookings",
        },
    }
    user_details_lead = "Iron Jarvis needs to know which folder to watch."

    def __init__(self, platform) -> None:
        self.platform = platform

    def missing_details(self, args: dict[str, Any], ctx: ToolContext | None = None) -> dict[str, dict[str, Any]]:
        given = args if isinstance(args, dict) else {}
        text = str(given.get("path") or "").strip().strip("\"'")
        if text:
            try:
                from ..settings.records_build import _absolute, _network

                # A full location, or a network spelling (the tool refuses
                # that in its own words), is not asked about.
                if _network(text) or _absolute(text):
                    return {}
            except Exception:  # noqa: BLE001 — a fault asks nothing
                return {}
        return dict(self.user_details)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        from ..settings.records import capture_create_undo

        return await capture_create_undo(self.platform, "sentinel", str(args.get("name") or "").strip())

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        # Idea 39 (39-a): made in a chat, what it notices is reported there.
        from ..agents.chat_delivery import stampable_chat

        report_chat = await asyncio.to_thread(
            stampable_chat, getattr(self.platform, "engine", None), getattr(ctx, "thread_id", "")
        )
        try:
            rec = self.platform.sentinels.add(
                args.get("name") or "",
                path=args.get("path"),
                glob=args.get("glob"),
                task=args.get("task", ""),
                kind=args.get("kind", "file"),
                agent_type=args.get("agent_type", "builder"),
                risk=args.get("risk", "low"),
                **({"chat_thread_id": report_chat} if report_chat else {}),
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        data: dict[str, Any] = {
            "id": rec.id,
            "name": rec.name,
            "kind": rec.kind,
            "enabled": rec.enabled,
        }
        reports = bool(getattr(rec, "chat_thread_id", ""))
        # Review (idea 39, release check 8): the watcher loop is OFF until the
        # user turns watchers on, so nothing is noticed or reported before
        # that. The card and the answer say the one step.
        watching = bool(getattr(getattr(self.platform, "config", None), "sentinels_enabled", False))
        card = _created_card(rec.name, rec.decoded_config().get("path") or rec.kind)
        if card:
            if reports:
                # Idea 39 (39-a): the card says where what it notices will go.
                from ..scheduling.tools import REPORTS_HERE_CARD

                here = REPORTS_HERE_CARD if watching else f"{REPORTS_HERE_CARD} {REPORTS_ONCE_ON}"
                card["new"] = f"{card.get('new') or ''} \u00b7 {here}".strip(" \u00b7")
            data["record_change"] = card
        data["reports_here"] = reports
        data["watching"] = watching
        output = f"created sentinel '{rec.name}' (kind={rec.kind}); suggest-only"
        if reports:
            output += f"; {REPORTS_HERE_WATCHER}"
        if not watching:
            output += f". {WATCHERS_OFF_WORDS}"
        return ToolResult(ok=True, output=output, data=data)


#: Idea 39 (39-a): what a watcher made in a chat tells the model and the user.
REPORTS_HERE_WATCHER = (
    "it reports what it notices in this chat, at most once an hour "
    "(Stop reporting is on its row and on each report)"
)
#: The card's words while watchers are off.
REPORTS_ONCE_ON = "once watchers are on"
#: The one step while watchers are off (the Watchers page's own button words).
WATCHERS_OFF_WORDS = (
    "Watchers are off, so it notices nothing and reports nothing until you turn watchers on: "
    "press Turn watchers on on the Watchers page, or ask me to turn on folder watchers"
)


def _created_card(name: str, where: str) -> dict[str, Any] | None:
    """The reply's "Sentinel created … [Undo]" card — only when this call
    captured an Undo (a direct ``execute`` keeps its old ``data``)."""
    try:
        from ..settings.records import create_card

        return create_card("sentinel", name, f"watches {where}")
    except Exception:  # noqa: BLE001 — a card never fails the tool
        return None


def sentinel_tools(platform) -> list[Tool]:
    """Build the Sentinel agent tool bound to the assembled ``platform``."""
    return [SentinelAddTool(platform)]
