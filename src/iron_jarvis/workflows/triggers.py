"""Trigger System (SPEC §25).

Decides *when* a workflow runs. SPEC §25 enumerates seven kinds:

    manual, cron, webhook, file (change), email, calendar, api

``manual`` and ``cron`` are fully wired here; the remaining kinds are inert
stubs that raise ``NotImplementedError`` so the surface matches the spec while
keeping the slice dependency-light. ``cron`` is backed by APScheduler.

TOML authoring shape (SPEC §25 ``[[triggers]]`` example)::

    [[triggers]]
    name = "monthly_close"
    schedule = "0 8 1 * *"
    workflow = "monthly_close"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

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


class CronScheduler:
    """Thin wrapper over APScheduler's ``BackgroundScheduler`` (SPEC §25 cron)."""

    def __init__(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        self.scheduler = BackgroundScheduler()

    def add(self, spec: TriggerSpec, callback: Callable[[], Any]):
        """Schedule ``callback`` per ``spec.schedule`` (a crontab string)."""
        from apscheduler.triggers.cron import CronTrigger

        if not spec.schedule:
            raise ValueError(f"cron trigger {spec.name!r} has no schedule")
        trigger = CronTrigger.from_crontab(spec.schedule)
        return self.scheduler.add_job(
            callback,
            trigger=trigger,
            id=spec.name or None,
            name=spec.name or None,
            replace_existing=True,
        )

    def start(self) -> None:
        self.scheduler.start()

    def shutdown(self, wait: bool = False) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)


# --- inert stubs for the not-yet-wired kinds (SPEC §25) --------------------


def _stub(kind: str) -> Callable[[TriggerSpec, Callable[[], Any]], Any]:
    def handler(spec: TriggerSpec, callback: Callable[[], Any]):
        raise NotImplementedError(
            f"{kind!r} triggers are not implemented yet (SPEC §25); "
            f"only 'manual' and 'cron' are wired in this slice."
        )

    handler.__name__ = f"{kind}_trigger_handler"
    return handler


def manual_handler(spec: TriggerSpec, callback: Callable[[], Any]):
    """Manual triggers fire on demand — nothing to schedule; return callback."""
    return callback


webhook_handler = _stub("webhook")
file_handler = _stub("file")
email_handler = _stub("email")
calendar_handler = _stub("calendar")
api_handler = _stub("api")

# Registry mapping kind -> handler for the non-cron kinds. ``cron`` is handled
# by :class:`CronScheduler` (it needs a live scheduler), so it dispatches there.
TRIGGER_HANDLERS: dict[str, Callable[[TriggerSpec, Callable[[], Any]], Any]] = {
    "manual": manual_handler,
    "webhook": webhook_handler,
    "file": file_handler,
    "email": email_handler,
    "calendar": calendar_handler,
    "api": api_handler,
}


def register_trigger(
    spec: TriggerSpec,
    callback: Callable[[], Any],
    scheduler: CronScheduler | None = None,
):
    """Route a trigger to its handler (SPEC §25).

    cron triggers require a :class:`CronScheduler`; the inert kinds raise
    ``NotImplementedError`` so callers see a clear message.
    """
    if spec.kind == "cron":
        if scheduler is None:
            raise ValueError("cron triggers require a CronScheduler instance")
        return scheduler.add(spec, callback)
    handler = TRIGGER_HANDLERS.get(spec.kind)
    if handler is None:
        raise ValueError(f"unknown trigger kind: {spec.kind!r}")
    return handler(spec, callback)
