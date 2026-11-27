"""The add-time rules of a schedule, ONE function for every door that saves
one with the user's say-so: ``POST /schedules`` and ``PATCH /schedules/{name}``
(``daemon/routes/system``) and Accept on a schedule Iron Jarvis offers
(next leaps 19-b, ``daemon/routes/schedule_offers``). Moved here word for
word from the routes' closure so a new door cannot keep a weaker copy.

:func:`check_payload` RAISES :class:`ScheduleRefused` with the HTTP status
and the sentence the route answers with. BLOCKING when ``workspace_root`` is
set (the folder probe writes a file): callers hop it off the loop.
"""

from __future__ import annotations

from typing import Any

from . import knobs


class ScheduleRefused(Exception):
    """A schedule the add-time rules refuse: ``status_code`` and ``detail``
    are what the route answers with."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def check_payload(platform: Any, name: str, kind: str, payload: dict) -> None:
    """The add-time rules, shared by POST and PATCH (v1.299.0) so an edit
    can never land a payload the add would have refused. Fail NOW, not at
    3am fire time: a task schedule needs its prompt, a typo'd destination
    would silently deliver to nobody, an unknown agent would silently run
    as builder, and a knob the fire cannot honour is refused by name.
    BLOCKING when ``workspace_root`` is set (the folder probe writes a
    file) — the callers hop it off the loop."""
    if kind == "task" and not str(payload.get("task") or "").strip():
        raise ScheduleRefused(
            status_code=400, detail="a task schedule needs 'task' text in payload"
        )
    # v1.171.0 (contract 3): a task schedule may name WHO does the work —
    # validated NOW, not at 3am fire time. The name must be a builtin
    # agent type or an existing dynamic agent's exact name (the same keys
    # the fire resolves against). A non-string is refused, never coerced.
    raw_agent = payload.get("agent_type") if kind == "task" else None
    if raw_agent not in (None, ""):
        from ..core.models import AgentType

        agent = raw_agent.strip() if isinstance(raw_agent, str) else ""
        builtin = {t.value for t in AgentType}
        try:
            dynamic = {r.name for r in platform.agents_registry.list()}
        except Exception:  # noqa: BLE001 — a broken registry refuses, never guesses
            dynamic = set()
        if not agent or (agent not in builtin and agent not in dynamic):
            raise ScheduleRefused(
                status_code=422,
                detail=(
                    f"unknown agent_type {raw_agent!r} — use a builtin agent "
                    "type or an existing custom agent's name (Agents page)"
                ),
            )
    # v1.424.0 (D6): a task schedule may name the Iron-Proxy ACCOUNT its
    # runs use (``payload.account``) — checked NOW by the one rule
    # (``iron_proxy.choice``): one Iron-Proxy lists, turned on, of the
    # vendor the run's model is on (the payload's model, else the
    # schedule's custom agent's, else the default). The fire checks it
    # again and pins every lease to it.
    raw_account = payload.get("account") if kind == "task" else None
    if raw_account not in (None, ""):
        if not isinstance(raw_account, str):
            raise ScheduleRefused(status_code=422, detail="account must be an account's id (text)")
        from ..iron_proxy import choice as _choice

        provider = str(payload.get("provider") or "")
        model = str(payload.get("model") or "")
        if not provider and isinstance(raw_agent, str) and raw_agent.strip():
            try:
                rec = platform.agents_registry.get(raw_agent.strip())
            except Exception:  # noqa: BLE001 — a builtin, or none: the default
                rec = None
            if rec is not None and str(getattr(rec, "provider", "") or "").strip():
                provider = str(rec.provider)
                model = model or str(getattr(rec, "model", "") or "")
        _row, why = _choice.check(platform, raw_account, provider, model, who="schedule")
        if not why and isinstance(raw_agent, str) and raw_agent.strip():
            # v1.424.0 (review): a custom agent with an account of ITS
            # OWN runs on it (its scope's pin is armed inside the
            # schedule's) — another account named here is refused in
            # words, never quietly lost at the fire.
            try:
                _rec = platform.agents_registry.get(raw_agent.strip())
            except Exception:  # noqa: BLE001 — a builtin: no account of its own
                _rec = None
            why = _choice.agent_conflict(platform, raw_account, _rec, who="schedule")
        if why:
            raise ScheduleRefused(status_code=409, detail=why)
    wanted = payload.get("notify_channels")
    if wanted:
        known = set(platform.notifier.channels())
        unknown = [c for c in wanted if c not in known]
        if unknown:
            raise ScheduleRefused(
                status_code=400,
                detail=f"unknown destination(s): {', '.join(unknown)} — "
                f"add them on the Notifications page first",
            )
    # v1.299.0: the knobs, ONE rule set (scheduling/knobs.validate_knobs)
    # for POST, PATCH and the agent-made tool. A route is the user.
    try:
        knobs.validate_knobs(
            payload,
            name=name,
            kind=kind,
            scheduler=platform.scheduler,
            skills=getattr(platform, "skills", None),
            from_user=True,
        )
    except knobs.KnobError as exc:
        raise ScheduleRefused(status_code=exc.status, detail=exc.detail) from None
