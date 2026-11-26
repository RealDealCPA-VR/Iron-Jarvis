"""Trigger System (SPEC §25): reading a ``[[triggers]]`` block, checking a cron.

What STARTS a workflow lives elsewhere, each in one place: a schedule
(``scheduling/``) runs it at a time, and a rule (``reflex/``) runs it when
something happens. Every kind of signal goes through the one start hub
(``triggers/starts.StartHub``) and the Rules page lists them all, schedules
included (``GET /starts``). Leap 05-b retired this module's old handler table
(``register_trigger``, its ``manual`` / ``file`` handlers, the inert
``webhook`` / ``email`` / ``calendar`` / ``api`` stubs that raised "not
implemented", and the unused ``CronScheduler``): nothing in the app called
them, and every one of those kinds is real through the hub.

TOML authoring shape (SPEC §25 ``[[triggers]]`` example)::

    [[triggers]]
    name = "monthly_close"
    schedule = "0 8 1 * *"
    workflow = "monthly_close"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The seven trigger kinds enumerated in SPEC §25.
TRIGGER_KINDS: tuple[str, ...] = (
    "manual",
    "cron",
    "webhook",
    "file",
    "email",
    "calendar",
    "api",
)

# Keys consumed directly by TriggerSpec; everything else lands in ``extra``.
_KNOWN_KEYS = frozenset({"kind", "name", "workflow", "schedule"})


@dataclass
class TriggerSpec:
    """A declared trigger binding a workflow to a firing condition (SPEC §25)."""

    kind: str
    name: str
    workflow: str
    schedule: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def parse_triggers(toml_dict: dict) -> list[TriggerSpec]:
    """Read ``[[triggers]]`` entries from a parsed TOML mapping (SPEC §25).

    ``kind`` defaults to ``"cron"`` when a ``schedule`` is present, else
    ``"manual"``.
    """
    specs: list[TriggerSpec] = []
    for raw in toml_dict.get("triggers", []) or []:
        schedule = raw.get("schedule")
        kind = raw.get("kind") or ("cron" if schedule else "manual")
        extra = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}
        specs.append(
            TriggerSpec(
                kind=str(kind),
                name=str(raw.get("name", "")),
                workflow=str(raw.get("workflow", "")),
                schedule=schedule,
                extra=extra,
            )
        )
    return specs


def validate_cron(expr: str) -> bool:
    """Return True iff ``expr`` is a valid 5-field crontab expression."""
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(expr)
        return True
    except Exception:
        return False
