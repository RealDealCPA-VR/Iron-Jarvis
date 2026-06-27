"""Trigger System (SPEC §25).

Decides *when* a workflow runs. SPEC §25 enumerates seven kinds:

    manual, cron, webhook, file (change), email, calendar, api

``manual`` and ``cron`` are fully wired here; ``file`` is wired to the Sentinels
subsystem (a declared ``[[triggers]]`` of kind ``file`` registers a durable,
suggest-only filesystem watcher — see :mod:`iron_jarvis.sentinels`). The
remaining kinds (``webhook``, ``email``, ``calendar``, ``api``) are inert stubs
that raise ``NotImplementedError``: ``email``/``calendar`` are intentionally NOT
faked — they need the integration/network layer before they can be real.
``cron`` is backed by APScheduler.

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


def file_trigger(spec: TriggerSpec, callback: Callable[[], Any], sentinels):
    """Wire a ``file`` trigger to the Sentinels subsystem (SPEC §25).

    A declared ``[[triggers]]`` of kind ``file`` becomes a durable, SUGGEST-ONLY
    filesystem watcher: when matching files appear/change, the Sentinel mints a
    proposal into the Motivation Layer backlog — it NEVER runs ``callback`` (or
    any session) directly. The ``callback`` is accepted only for signature parity
    with the other handlers; firing is decoupled by design (that decoupling IS the
    safety model — a noticed signal becomes a suggestion, not an action).

    The watch spec comes from the trigger's ``extra`` keys: ``path`` (required —
    a file, directory, or glob) and optional ``glob``/``task``/``risk``. The
    suggested ``task`` defaults to reviewing the change for the bound workflow.
    """
    if sentinels is None:
        raise ValueError("file triggers require a SentinelService instance")
    path = spec.extra.get("path") or spec.extra.get("watch")
    if not path:
        raise ValueError(f"file trigger {spec.name!r} requires a 'path'")
    task = spec.extra.get("task") or (
        f"Files watched by trigger '{spec.name}' changed; review what changed and "
        f"whether to run the '{spec.workflow}' workflow. Take no action yet."
    )
    return sentinels.add(
        spec.name,
        path=str(path),
        glob=spec.extra.get("glob"),
        task=task,
        kind="file",
        risk=spec.extra.get("risk", "low"),
    )


webhook_handler = _stub("webhook")
email_handler = _stub("email")
calendar_handler = _stub("calendar")
api_handler = _stub("api")

# Registry mapping kind -> handler for the non-cron, non-file kinds. ``cron`` is
# handled by :class:`CronScheduler` and ``file`` by the Sentinels subsystem (both
# need a live collaborator), so they dispatch separately in ``register_trigger``.
TRIGGER_HANDLERS: dict[str, Callable[[TriggerSpec, Callable[[], Any]], Any]] = {
    "manual": manual_handler,
    "webhook": webhook_handler,
    "email": email_handler,
    "calendar": calendar_handler,
    "api": api_handler,
}


def register_trigger(
    spec: TriggerSpec,
    callback: Callable[[], Any],
    scheduler: CronScheduler | None = None,
    sentinels=None,
):
    """Route a trigger to its handler (SPEC §25).

    ``cron`` triggers require a :class:`CronScheduler`; ``file`` triggers require
    a :class:`~iron_jarvis.sentinels.SentinelService` (suggest-only). The still
    inert kinds (webhook/email/calendar/api) raise ``NotImplementedError`` so
    callers see a clear message.
    """
    if spec.kind == "cron":
        if scheduler is None:
            raise ValueError("cron triggers require a CronScheduler instance")
        return scheduler.add(spec, callback)
    if spec.kind == "file":
        return file_trigger(spec, callback, sentinels)
    handler = TRIGGER_HANDLERS.get(spec.kind)
    if handler is None:
        raise ValueError(f"unknown trigger kind: {spec.kind!r}")
    return handler(spec, callback)
