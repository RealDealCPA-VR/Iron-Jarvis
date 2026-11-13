"""Agent-facing scheduling tool (§19 tool interface, §25 cron).

``schedule_create`` lets an agent register its *own* durable scheduled task
through the tool loop. It is constructed with the assembled ``platform`` (like
:class:`~iron_jarvis.agents.delegate_tool.DelegateTool`) and acts on
``platform.scheduler``. ``schedule_tools(platform)`` builds it for registration.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult


class ScheduleCreateTool(Tool):
    """Create a durable scheduled task (cron, one-time date, or interval)."""

    name = "schedule_create"
    description = (
        "Create a persistent scheduled task. Supply exactly one trigger: `cron` "
        "(a 5-field crontab string), `run_at` (an ISO-8601 datetime for a "
        "one-time fire), or `interval_seconds` (a fixed repeat period). For work "
        "to do on a schedule, give `task`: what to do each time, in the user's "
        "own words (e.g. 'pull together the list of flats with rent due this "
        "week'); it runs as an agent task at every fire, which finds the files "
        "and details then, so they need not be asked for first. Otherwise "
        "`kind` is 'workflow' (default — `payload` is a workflow definition) or "
        "'event' (`payload` carries the event to publish). Returns the created "
        "task name and its next run time."
    )
    permission_key = "schedule_create"
    # Calm UI redesign S5: a schedule made from chat has an Undo (it removes
    # the schedule) — settings.records holds the snapshot/restore.
    reversibility = Reversibility.REVERSIBLE
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "cron": {"type": "string"},
            "run_at": {"type": "string"},
            "interval_seconds": {"type": "integer", "minimum": 1},
            "task": {"type": "string"},
            "kind": {"type": "string", "enum": ["task", "workflow", "event"]},
            "payload": {"type": "object"},
        },
        "required": ["name"],
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        from ..settings.records import capture_create_undo

        return await capture_create_undo(self.platform, "schedule", str(args.get("name") or ""))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        from . import knobs as _knobs

        payload = args.get("payload")
        # v1.483.0: a plain TASK schedule from chat (the Schedules page could
        # always make one; this tool offered only a whole workflow definition,
        # so "every Monday at 8, pull together the rent list" became questions
        # instead of a schedule). The task runs as an agent task at each fire,
        # exactly like a task schedule made on the page; no new power (a
        # workflow can hold the same agent step).
        task = args.get("task")
        if task is not None and not isinstance(task, str):
            return ToolResult(ok=False, error="task must be the work to do each time, as text")
        task = (task or "").strip()
        kind = args.get("kind") or ("task" if task else "workflow")
        if kind == "task":
            if payload is not None and not isinstance(payload, dict):
                return ToolResult(ok=False, error="payload must be an object of schedule knobs")
            payload = dict(payload or {})
            if task:
                payload["task"] = task
            if not str(payload.get("task") or "").strip():
                return ToolResult(ok=False, error=(
                    "a task schedule needs `task`: what to do each time, in the user's own words"))
        elif task:
            return ToolResult(ok=False, error=(
                f"`task` makes a task schedule; leave `kind` out, or set it to 'task' (not {kind!r})"))
        # THE SCRIPT KNOB IS THE USER'S (v1.299.0): a pre-run command runs on
        # the host with the shell tool's confinement, so a schedule an AGENT
        # makes may never carry one — refused by name, before anything else
        # (v1.483.0: a TASK schedule made here could fire it, so this refusal
        # is what keeps it the user's).
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
                kind=kind,
                payload=payload,
            )
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))

        next_run = rec.next_run.isoformat() if rec.next_run is not None else None
        data: dict[str, Any] = {
            "name": rec.name,
            "trigger_type": rec.trigger_type,
            "kind": rec.kind,
            "next_run": next_run,
        }
        card = _created_card(rec.name)
        if card:
            data["record_change"] = card
        return ToolResult(
            ok=True,
            output=f"scheduled task '{rec.name}' (next run: {next_run})",
            data=data,
        )


#: v1.486.0: how many schedules ``schedule_list`` shows at most.
LIST_MAX = 50
_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _clock(minute: str, hour: str) -> str | None:
    if not (minute.isdigit() and hour.isdigit()):
        return None
    h, m = int(hour), int(minute)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _day_words(field: str) -> str | None:
    if field in ("1-5", "MON-FRI", "mon-fri"):
        return "every weekday"
    if field in ("0,6", "6,0", "SAT,SUN", "sat,sun"):
        return "every weekend day"
    names = []
    for part in field.split(","):
        if not part.isdigit() or int(part) > 7:
            return None
        names.append(_DAYS[int(part) % 7])
    return "every " + " and ".join(names) if names else None


def when_words(rec: Any) -> str:
    """The schedule's timing in plain words, with the exact trigger kept.

    Common cron shapes are said in words ("every day at 8:00 AM"); anything
    else is given as it is stored ("cron 0 */2 * * 1"), never guessed.
    """
    kind = getattr(rec, "trigger_type", "cron")
    if kind == "date":
        at = getattr(rec, "run_at", None)
        return f"once, at {at.isoformat() if at is not None else 'an unknown time'}"
    if kind == "interval":
        secs = int(getattr(rec, "interval_seconds", 0) or 0)
        for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
            if secs and secs % size == 0:
                n = secs // size
                return f"every {n} {unit}{'' if n == 1 else 's'}"
        return f"every {secs} seconds"
    cron = str(getattr(rec, "cron", "") or "").strip()
    parts = cron.split()
    if len(parts) == 5:
        minute, hour, dom, month, dow = parts
        at = _clock(minute, hour)
        if at and month == "*":
            if dom == "*" and dow == "*":
                return f"every day at {at} (cron {cron})"
            if dom == "*":
                days = _day_words(dow)
                if days:
                    return f"{days} at {at} (cron {cron})"
            if dow == "*" and dom.isdigit():
                return f"on day {int(dom)} of every month at {at} (cron {cron})"
    return f"cron {cron}"


def _does_words(rec: Any) -> str:
    try:
        payload = rec.decoded_payload()
    except Exception:  # noqa: BLE001 — a bad payload still lists the schedule
        payload = {}
    if rec.kind == "task":
        task = " ".join(str(payload.get("task") or "").split())
        return f"does: {task[:200]}" if task else "a task schedule with no task"
    if rec.kind == "event":
        return f"publishes the event {payload.get('event') or payload.get('type') or '(unnamed)'}"
    name = payload.get("name") if isinstance(payload, dict) else None
    return f"runs the workflow {name}" if name else "runs a workflow"


class ScheduleListTool(Tool):
    """List the scheduled tasks, so a schedule can be found by what it does."""

    name = "schedule_list"
    description = (
        "List the user's scheduled tasks (the Schedules page): each one's name, when it runs "
        "in plain words, what it does, whether it is on or paused, and its next run and how "
        "the last run went. Call it to find which schedule the user means ('the daily 8 "
        "o'clock reminder') before changing one with schedule_update or schedule_delete, "
        "which take the schedule's name. It only reads."
    )
    permission_key = "schedule_list"
    reversibility = Reversibility.READONLY
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional words to keep only schedules whose name or work holds them.",
            },
        },
    }

    def __init__(self, platform) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        scheduler = getattr(self.platform, "scheduler", None)
        if scheduler is None:
            return ToolResult(ok=False, error="Schedules are not available here.")
        rows = await asyncio.to_thread(scheduler.list)
        words = [w for w in str(args.get("query") or "").lower().split() if w]
        lines: list[str] = []
        results: list[dict[str, Any]] = []
        for rec in rows:
            when, does = when_words(rec), _does_words(rec)
            text = f"{rec.name} {does}".lower()
            if words and not all(w in text for w in words):
                continue
            state = "on" if rec.enabled else "paused"
            nxt = rec.next_run.isoformat() if rec.next_run is not None and rec.enabled else ""
            last = rec.last_status or "never run"
            lines.append(
                f"- {rec.name}: {when}; {does}; {state}"
                + (f"; next run {nxt}" if nxt else "")
                + f"; last run: {last}"
            )
            results.append({
                "name": rec.name, "when": when, "does": does, "enabled": bool(rec.enabled),
                "next_run": nxt or None, "last_status": rec.last_status or "",
            })
        total = len(results)
        if not rows:
            return ToolResult(ok=True, output="There are no scheduled tasks.", data={"schedules": []})
        if not results:
            return ToolResult(
                ok=True,
                output=f"No scheduled task matches {args.get('query')!r}. All {len(rows)}: "
                + ", ".join(r.name for r in rows[:LIST_MAX]),
                data={"schedules": []},
            )
        shown = lines[:LIST_MAX]
        more = f"\n…and {total - LIST_MAX} more." if total > LIST_MAX else ""
        return ToolResult(
            ok=True,
            output=f"{total} scheduled task{'' if total == 1 else 's'}:\n" + "\n".join(shown) + more,
            data={"schedules": results[:LIST_MAX]},
        )


def _created_card(name: str) -> dict[str, Any] | None:
    try:
        from ..settings.records import create_card

        return create_card("schedule", name, "created")
    except Exception:  # noqa: BLE001 — a card never fails the tool
        return None


def schedule_tools(platform) -> list[Tool]:
    """Build the scheduling tools bound to the assembled ``platform``."""
    return [ScheduleCreateTool(platform), ScheduleListTool(platform)]
