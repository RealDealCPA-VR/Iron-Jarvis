"""WHO does work that starts with nobody there (leap 05-a).

A schedule's fire and a rule's fire both name the agent that does the work;
idea 08's goals will too. Before 05-a the resolution lived inside the
schedule's fire (``platform._dispatch_scheduled``); it is ONE helper now, so
a schedule and a rule can never disagree about what a name means:

* the DYNAMIC record first, resolved through the registry at FIRE time (an
  edit to the agent applies to later fires), mirroring
  ``POST /agents/{name}/spawn``: a supervisor-based record is refused (the
  run would silently drop its prompt), a paused or exhausted one is refused
  (its allowance), a model team member runs held to its own model, an agent
  with an account of its own runs on that account;
* then a builtin :class:`AgentType` name;
* a name that is neither is refused in words (it was deleted) — never a
  silent fallback to another agent;
* no name: the door's own default (a schedule's builder, a rule's
  supervisor), exactly as before.

A refusal is :class:`UnattendedRefusal` carrying the sentence the door's row
shows. Pure resolution: no session row is written here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.models import AgentType


class UnattendedRefusal(Exception):
    """The named agent cannot take this work; ``str(exc)`` is the sentence."""


@dataclass
class UnattendedAgent:
    """What a fire runs as."""

    agent_type: AgentType
    definition: Any = None
    provider: str | None = None
    model: str | None = None
    #: The custom agent's job card (None for a builtin or an unset card).
    max_steps: int | None = None
    approval_mode: str | None = None
    #: ``custom:<name>`` for a custom agent (its own standing OKs apply and
    #: the run is credited to it), None for a builtin.
    agent_name: str | None = None
    #: A model team member (or an agent with its own account) the run is
    #: held to (``model_team.run_as_member``), else None.
    member: Any = None


def _supervisor_words(name: str, who: str) -> str:
    return (
        f"dynamic agent '{name}' is based on "
        f"'supervisor' and cannot take a {who} — "
        "re-create it with a non-supervisor base type"
    )


def _gone_words(name: str, who: str) -> str:
    if who == "schedule":
        # The schedule's words since v1.171.0, byte for byte.
        return (
            f"scheduled agent '{name}' no longer exists "
            "— it may have been deleted; re-create it or "
            "re-add the schedule with another agent"
        )
    return (
        f"the agent '{name}' this {who} names no longer exists — it may have "
        f"been deleted; re-create it or change the {who} to use another agent"
    )


def resolve_for_unattended(
    platform: Any,
    name: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    who: str = "schedule",
    default: AgentType = AgentType.BUILDER,
) -> UnattendedAgent:
    """Resolve ``name`` (a custom agent's name, a builtin type's value, or ""
    for ``default``) for a fire of kind ``who`` ("schedule", "rule").
    ``provider`` / ``model`` are the door's own explicit pair (they win over
    a record's pinned pair). Raises :class:`UnattendedRefusal`."""
    raw = name.strip() if isinstance(name, str) else ""
    out = UnattendedAgent(agent_type=default, provider=provider or None, model=model or None)
    if not raw:
        return out
    registry = getattr(platform, "agents_registry", None)
    definition = registry.definition(raw) if registry is not None else None
    if definition is None:
        try:
            out.agent_type = AgentType(raw)
        except ValueError:
            raise UnattendedRefusal(_gone_words(raw, who)) from None
        return out
    if definition.type is AgentType.SUPERVISOR:
        # Mirrors the spawn route's 409 (v1.166.0): run_session reroutes
        # SUPERVISOR-typed sessions to the builtin supervisor, which would
        # silently discard this record's custom system prompt.
        raise UnattendedRefusal(_supervisor_words(raw, who))
    out.agent_type = definition.type
    out.definition = definition
    rec = registry.get(raw)
    # THE DAY OFF / THE ALLOWANCE (v1.295.0): a paused or exhausted agent
    # fails the fire HONESTLY, never a silent run of another agent.
    from . import allowance as _allowance

    reason = _allowance.refusal_for(rec, getattr(platform, "engine", None))
    if reason:
        raise UnattendedRefusal(reason)
    # A MODEL TEAM MEMBER (v1.395.0): refused in words BEFORE any session
    # row when its account is not one the user's groups allow, or the door
    # names ANOTHER model than the member runs on (never silently swapped);
    # else it runs on ITS OWN model, held there.
    from . import model_team as _mt

    why = _mt.queued_refusal(platform, rec, provider=str(provider or ""), model=str(model or ""))
    if why:
        raise UnattendedRefusal(why)
    out.provider = out.provider or (rec.provider if (rec and rec.provider) else None)
    out.model = out.model or (rec.model if (rec and rec.model) else None)
    if _mt.runs_elsewhere(rec):
        out.member = rec
        out.provider = str(rec.provider or "").strip() or None
        out.model = _mt.pinned_model(platform, rec) or None
    elif _mt.has_own_account(rec):
        # v1.424.0 (D6): an agent that names an account of its own runs on
        # THAT account, refused in words first when the groups forbid it.
        why = _mt.account_refusal(platform, rec)
        if why:
            raise UnattendedRefusal(why)
        out.member = rec
    # The job card's posture and step budget (v1.295.0).
    out.max_steps = (rec.max_steps or None) if rec else None
    out.approval_mode = (rec.approval_mode or None) if rec else None
    if rec is not None and str(getattr(rec, "name", "") or "").strip():
        out.agent_name = f"custom:{rec.name}"
    return out
