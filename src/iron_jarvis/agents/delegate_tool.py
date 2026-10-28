"""Delegate tool (§12 Multi-Agent Orchestration).

A coordinator (the Supervisor, and since v1.166.0 the Planner) uses this tool
to hand a subtask to a freshly-spawned subagent.
Each delegation gets its *own* session and runs the agent runtime to
completion. The subagent operates independently and returns only a SUMMARIZED
result back to the supervisor — the WORK flows through the supervisor.

WHO A SUBAGENT MAY ASK changed in v1.309.0: a MISSION's teammate (its team
root carries ``job:mission``) is stamped ``job:mission-member`` and its
ask-tier calls WAIT for the user, under its own session id, on the mission
screen, the bell and the desktop ask watcher
(``orchestrator.inherited_origin``). Every other subagent still has no origin
and takes the instant headless refusal. A teammate the user STOPS ends at its
next step boundary, and the coordinator is told so in words and carries on.

WHERE the child works changed in v1.193.0: a parent running DIRECTLY in the
user's folder (a Projects in-folder task) hands that folder down, so the team
works in one place; every other parent's children keep the isolated, disposable
workspace (§15) they have always had. See
``orchestrator.inherited_workspace_root``.

The child ``AgentRun`` is linked to the caller via ``parent_id`` so the
supervisor → subagent hierarchy is reconstructable from persistence — and since
v1.193.0 the same edge is ANNOUNCED live as ``delegation.started`` /
``delegation.completed``, with the child's address in the tool OUTPUT so the
parent can ``message_agent`` it afterwards.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import new_id
from ..core.models import AgentRun, AgentState, AgentType
from ..tools.base import Tool, ToolContext, ToolResult
from ..tools.registry import tool_deadline_expired

#: Hardest cap on the coordinator→subagent delegation chain. Combined with
#: "no delegating to any target whose definition itself carries the delegate
#: tool" (supervisor AND planner — so a specialist child can't recurse), this
#: bounds a prompt-injected fork-bomb.
_MAX_DELEGATION_DEPTH = 3

#: How much of a child's result rides in ``delegation.completed``. The event is
#: a timeline entry, not a transcript — the full result is on the AgentRun.
_EVENT_RESULT_CHARS = 240


def _short(text: str | None, limit: int = _EVENT_RESULT_CHARS) -> str:
    """One-line, bounded version of a result for an event payload."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def delegation_handle(target: str, run_id: str, session_id: str) -> str:
    """The child's ADDRESS, for the parent's eyes (v1.193.0).

    ``delegate`` returned the child's ids in ``ToolResult.data`` — and the
    runtime hands the model ``result.output`` and nothing else (``agent_tools``
    says so outright), so a supervisor could never address a child it had just
    created: ``message_agent`` takes the teammate's agent_run_id. This line puts
    that handle in the OUTPUT. Kept to one bracketed line on purpose — it is
    prompt budget, paid on every delegation.
    """
    return f"[{target} agent_run_id={run_id} session={session_id}]"


def _with_handle(result: str | None, handle: str) -> str:
    """The child's report, with its address on the FIRST line.

    The handle leads DELIBERATELY. ``agents/runtime`` caps a tool result fed
    into the model context at ``_MAX_TOOL_CONTEXT_CHARS`` and it keeps the HEAD
    (``content[:16000]`` plus a truncation note), so a handle appended after the
    summary is precisely what gets cut whenever a child returns a long report —
    and a long report is exactly when a parent most wants to ask a follow-up.
    Trailing it would have made the handle survive only the cases that did not
    need it.
    """
    body = (result or "").rstrip()
    return f"{handle}\n{body}" if body else handle


async def publish_delegation_started(
    platform,
    *,
    session_id: str,
    parent_run_id: str | None,
    child_session_id: str,
    target: str,
    task: str,
    handoff_id: str | None = None,
    role: str = "",
    reason: str = "",
) -> None:
    """Announce a handoff on the PARENT's session (see ``EventType``).

    ``handoff_id`` (v1.309.0) rides only on a REMOTE handoff, which opens no
    child session: it is the key a reader groups that handoff's started and
    completed events by, so two handoffs to one remote are two cards — never
    one collapsed by agent name.

    ``role`` / ``reason`` (v1.395.0) ride only when the planner asked for a
    ROLE and the team router picked the member: the role and its one
    sentence of why."""
    await platform.event_bus.publish(
        EventType.DELEGATION_STARTED,
        {
            "parent_run_id": parent_run_id or None,
            "child_session_id": child_session_id,
            "agent": target,
            "task": _short(task),
            **({"handoff_id": handoff_id} if handoff_id else {}),
            **({"role": role, "reason": _short(reason, 300)} if role and reason else {}),
        },
        session_id=session_id,
    )


async def publish_delegation_completed(
    platform,
    *,
    session_id: str,
    parent_run_id: str | None,
    child_run_id: str | None,
    child_session_id: str,
    target: str,
    ok: bool,
    result: str | None,
    handoff_id: str | None = None,
) -> None:
    """Announce a handoff settling — including the crash path, where there is
    no child run id at all (the runtime raised before/while making one)."""
    await platform.event_bus.publish(
        EventType.DELEGATION_COMPLETED,
        {
            "parent_run_id": parent_run_id or None,
            "child_run_id": child_run_id or None,
            "child_session_id": child_session_id,
            "agent": target,
            "ok": ok,
            "result": _short(result),
            **({"handoff_id": handoff_id} if handoff_id else {}),
        },
        session_id=session_id,
    )


#: The child's summary when an outer tool time limit (not the user) stopped it
#: (v1.288.0). ``delegate`` and ``spawn_agent`` are exempt from the registry's
#: deadline, so this is the belt: if any future deadline wraps a sub-agent
#: again, its record still names the limit instead of blaming the user.
TIME_LIMIT_REASON = (
    "Stopped by the tool time limit before it finished (not by the user)."
)


async def user_stopped_child(orch, child_session_id: str) -> bool:
    """Was the ``CancelledError`` a delegating door just caught the USER
    stopping its child — and not a cancel of the door's OWN run (v1.309.0)?

    The parent's own cancellation (Stop on the coordinator, a deadline)
    raises its task's ``cancelling()`` count and must keep propagating: it is
    never swallowed. Only with that count at zero does a stop mark
    (``team.stop_child``) or the child's CANCELLED row — written BEFORE the
    child's task is cancelled — make it the user's Stop on this teammate."""
    from . import team as _team

    me = asyncio.current_task()
    if me is not None and me.cancelling():
        _team.consume_stop(child_session_id)
        return False
    if _team.consume_stop(child_session_id):
        return True
    try:
        return await asyncio.to_thread(orch._user_cancelled, child_session_id)
    except Exception:  # noqa: BLE001 — unknown = not the user; propagate
        return False


def stopped_by_user_error(target: str) -> str:
    """What the COORDINATOR reads when the user stopped one of its teammates
    (v1.309.0). It names who stopped it and what to do next, because the
    coordinator carries on: the rest of the mission is still its job."""
    return (
        f"'{target}' was stopped by the user before it finished — its part is "
        "NOT done. Do not hand the same work to it again; carry on with the "
        "rest, and say in your final message what was left undone."
    )


def _routed_member(route: Any, record: Any, provider: Any, model: Any) -> dict[str, str]:
    """v1.401.0: ``{member, provider, model}`` of the member the TEAM ROUTER
    picked for a role (the answer's ``data``) — {} for every other call, so
    its data is exactly what it was."""
    if route is None or record is None:
        return {}
    return {
        "member": str(getattr(record, "name", "") or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
    }


def _local_only_now() -> bool:
    """Is the run this tool call belongs to Local only? (The runtime sets the
    scope around a local-only run's whole body.) Never raises."""
    try:
        from ..providers.egress import current_scope

        return bool(current_scope().on)
    except Exception:  # noqa: BLE001
        return False


class DelegateTool(Tool):
    name = "delegate"
    description = (
        "Delegate a subtask to a subagent. The subagent runs independently and "
        "returns a summarized result plus its agent_run_id, which you can use "
        "with message_agent. When you are working in the user's folder the "
        "subagent works in that same folder, so its files land where you can "
        "see them. Use one "
        "delegate call per subtask. Args: agent_type — a name from the 'Who "
        "can take this work' roster when one is shown: builtin specialists "
        "('builder', 'researcher', 'reviewer'), a listed "
        "'custom:<name>' agent, or a listed 'remote:<name>' agent; defaults "
        "to 'builder' — and task (the self-contained instruction for the "
        "subagent). Instead of a name you may give role — read, code, review "
        "or test — and Iron Jarvis picks the member of the user's model team "
        "for it (a local one first when its record for that role is good; a "
        "cloud account near its usage limit gets no new work); the answer says "
        "who took it. Give piece (a short name for this piece of work) and "
        "hand it out again under the same piece name if it failed: after it "
        "fails twice on one member it moves to the next one up."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent_type": {"type": "string"},
            "task": {"type": "string"},
            "role": {"type": "string", "enum": ["read", "code", "review", "test"]},
            "piece": {"type": "string"},
        },
        "required": ["task"],
    }
    permission_key = "delegate"
    #: It awaits a whole child run — outside the per-call deadline (v1.288.0).
    deadline_exempt = True

    def __init__(self, platform) -> None:
        self.platform = platform

    def _delegation_depth(self, agent_run_id: str | None) -> int:
        """How deep the CALLER already is in the delegation chain (root = 0), by
        walking AgentRun.parent_id. Bounds the exponential fan-out of a
        prompt-injected 'delegate to a supervisor' loop."""
        depth = 0
        current = agent_run_id
        seen: set[str] = set()
        with session_scope(self.platform.engine) as db:
            while current and current not in seen and depth < 100:
                seen.add(current)
                row = db.get(AgentRun, current)
                parent = getattr(row, "parent_id", None) if row is not None else None
                if not parent:
                    break
                current = parent
                depth += 1
        return depth

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Lazy imports: avoid an agents-package import cycle at module load.
        from .orchestrator import (
            Orchestrator,
            child_fanout_key,
            child_slot,
            inherited_grants,
            inherited_origin,
            inherited_trust,
            inherited_workspace_root,
        )
        from . import team as _team
        from . import model_team as _model_team
        from ..harness import helpers as _helpers
        from .runtime import AgentRuntime
        from .types import get_agent_definition

        task = args.get("task") or ""
        raw_type = str(args.get("agent_type") or "builder").strip()
        # AN IJ HARNESS TURN'S HELPER (v1.372.0): the turn armed what its
        # helper inherits — the session's model, account, folder, Local only,
        # trust and approval posture (``harness/helpers``). None for every
        # other caller, whose behaviour below is unchanged.
        _hp = _helpers.parent_for(ctx)

        # A PROJECT MISSION'S TEAM (v1.308.0), read before routing (v1.395.0)
        # so the team router only asks the project's team — see below.
        _root = await asyncio.to_thread(
            _team.mission_root, self.platform.engine, ctx.session_id, ctx.agent_run_id
        )
        _project_team = _team.mission_team(_root) if _root is not None else []
        # A ROLE INSTEAD OF A NAME (v1.395.0, the long-jobs plan's Phase 6,
        # decision 7): ``role`` (or ``agent_type: "role:<id>"`` — the dialects'
        # own helper tools carry no role argument) asks the TEAM ROUTER for
        # the member (``model_team.route_role``, off the loop). A NAMED member
        # still runs exactly as before; a role beside the name only says what
        # the piece is (its record). An unknown role, or one no usable member
        # holds, is refused in words — never a silent run as anyone else.
        # A CODING SESSION'S TEAM (v1.403.0, ``harness/team``): the lead hands
        # pieces ONLY to the session's workers (code / test / read) and its
        # reviewer (a review). None for every other caller.
        _session_team = getattr(_hp, "team", None) if _hp is not None else None
        _route = None
        _team_role = ""
        _piece = ""
        _role_text = _model_team.role_named(raw_type)
        _role_arg = args.get("role")
        if _role_text is not None or str(_role_arg or "").strip():
            _asked = _role_text if _role_text is not None else _role_arg
            _team_role = _model_team.role_id(_asked)
            if not _team_role:
                _helpers.note_route(_hp, reason="not started: that is not a team role")
                return ToolResult(ok=False, output="", error=_model_team.unknown_role_refusal(_asked))
            _piece = _model_team.piece_key(args.get("piece"), task)
            if _role_text is not None or not str(args.get("agent_type") or "").strip():
                _refused = _helpers.team_role_refusal(_hp, _team_role)
                if _refused is not None:
                    return _refused
                _route = await asyncio.to_thread(
                    _model_team.route_for_call, self.platform, role=_team_role, piece=_piece,
                    session_id=ctx.session_id, helper_parent=_hp,
                    **_helpers.team_route_kwargs(_hp, _team_role, _project_team),
                )
                _helpers.note_route(_hp, role=_team_role, reason=_route.reason)
                if _route.record is None:
                    # v1.403.0: no WORKER of a coding session's team could take
                    # this piece — the lead may do it itself (unlocked at the
                    # lane's next round), and its answer says so.
                    _note = (
                        _session_team.no_worker(_team_role, _route.refusal, _route.reason)
                        if _session_team is not None else ""
                    )
                    return ToolResult(
                        ok=False, output="", error=_note or _route.refusal,
                        data={"role": _team_role, "reason": _route.reason, "state": "not_started",
                              "waited": _route.waited,
                              **({"lead_may_do": True} if _note else {})},
                    )
                raw_type = f"custom:{_route.record.name}"

        # CAPABILITY ROSTER (v1.139.0): resolve the target through the roster
        # first — it validates builtin names AND surfaces dynamic
        # ("custom:<slug>") and remote ("remote:<name>") agents when they are
        # actually delegable/healthy right now. getattr-defensive: with the
        # roster module absent or broken, ``entry`` stays None and every
        # builtin name keeps working exactly as before through the fallback
        # below.
        entry = None
        try:
            from .roster import resolve_target

            entry = resolve_target(self.platform, raw_type)
        except Exception:  # noqa: BLE001 — roster trouble must not break builtins
            entry = None

        _low = raw_type.lower()
        # A harness turn's helper never leaves this PC (another computer is
        # another provider): refused in words before anything is resolved.
        _remote_refusal = _helpers.target_refusal(_hp, entry=entry, raw=raw_type)
        if _remote_refusal:
            return ToolResult(ok=False, output="", error=_remote_refusal)
        if entry is None and not _low.startswith("remote:"):
            # THE DAY OFF / THE ALLOWANCE (v1.295.0): a paused agent is
            # ``healthy=False`` on the roster, so ``resolve_target`` answers
            # None for it — and a BARE slug ("scout") would then fall through
            # the ``AgentType(...)`` fallback below into a silent BUILDER run,
            # the exact degradation this wave forbids. Say WHY, in the user's
            # own words, before either generic path can hide the reason.
            try:
                from . import allowance

                _registry = getattr(self.platform, "agents_registry", None)
                _slug = (
                    raw_type.split(":", 1)[1] if _low.startswith("custom:") else raw_type
                ).strip()
                _rec = _registry.get(_slug) if (_registry is not None and _slug) else None
                _reason = allowance.refusal_for(_rec, self.platform.engine)
            except Exception:  # noqa: BLE001 — fall through to the generic refusal
                _reason = ""
            if _reason:
                return ToolResult(ok=False, output="", error=_reason)
        if entry is None and (_low.startswith("custom:") or _low.startswith("remote:")):
            # An explicitly prefixed roster target that does not resolve is
            # unknown, offline, or not spawnable — refuse HONESTLY instead of
            # silently coercing it to a builder (fake capability is worse
            # than a refusal).
            return ToolResult(
                ok=False,
                output="",
                error=f"'{raw_type}' is not delegable right now (unknown, "
                "offline, or chat-only) — pick a name from the 'Who can take "
                "this work' roster, or a builtin specialist "
                "(builder/researcher/reviewer)",
            )

        # A PROJECT MISSION'S TEAM (v1.308.0): the coordinator of a mission
        # started in a project may hand work only to that project's team —
        # the user picked it. Read off the mission ROOT's run options (the
        # snapshot ``POST /missions`` took), reached by walking AgentRun.
        # parent_id (v1.309.0): a teammate handing work on is held to the same
        # team, and children never inherit options. Every other caller has no
        # team and is unrestricted. Checked before any session exists, local
        # or remote. (``_root`` / ``_project_team`` were read above.)
        if _project_team:
            _who = entry.name if entry is not None else raw_type
            if not _team.on_team(_project_team, _who):
                return ToolResult(
                    ok=False, output="", error=_team.off_team_refusal(_project_team, _who)
                )
        # A CODING SESSION'S TEAM (v1.403.0): the lead hands work to its own
        # workers and reviewer only — a built-in helper would do the work on
        # the lead's model, around the lead's lock. Said before anything runs.
        if _session_team is not None:
            _off = _session_team.off_team(entry.name if entry is not None else raw_type)
            if _off:
                return ToolResult(ok=False, output="", error=_off)

        if entry is not None and entry.kind == "remote":
            # Remote target: no local session — the existing remote ask path.
            # Depth-capped like any other delegation (it is still fanned-out
            # work a prompt injection could multiply).
            if self._delegation_depth(ctx.agent_run_id) >= _MAX_DELEGATION_DEPTH:
                return ToolResult(
                    ok=False,
                    output="",
                    error=f"delegation depth limit ({_MAX_DELEGATION_DEPTH}) "
                    "reached — do this subtask directly instead of delegating "
                    "further",
                )
            return await self._handoff_remote(entry, task, ctx)

        # Local targets: a dynamic agent runs its OWN stored definition; a
        # builtin resolves to its canonical type; anything the roster does not
        # know falls back to today's behavior byte-for-byte (AgentType(...) or
        # the builder default).
        definition = None
        dyn_provider = dyn_model = None
        rec = None
        # v1.395.0 review: the account a member's piece is pinned to, when the
        # router or ``named_account`` decided it (None is a real answer: none).
        _member_pin, _member_pin_set = None, False
        if entry is not None and entry.kind == "dynamic":
            slug = entry.name.split(":", 1)[-1]
            registry = getattr(self.platform, "agents_registry", None)
            definition = registry.definition(slug) if registry is not None else None
            if definition is None:
                return ToolResult(
                    ok=False,
                    output="",
                    error=f"dynamic agent '{slug}' has no runnable definition "
                    "right now — delegate to a builtin specialist instead",
                )
            rec = registry.get(slug)
            # THE DAY OFF / THE ALLOWANCE (v1.295.0): refused here, before a
            # session exists, in plain words — never a silent builder run.
            from . import allowance

            reason = allowance.refusal_for(rec, self.platform.engine)
            if reason:
                return ToolResult(ok=False, output="", error=reason)
            # A harness turn's helper runs on the SESSION's model: a custom
            # agent pinned to another one is refused, never run on its pin —
            # unless it is a MODEL TEAM MEMBER (v1.394.0, decision 5), which
            # runs on its own model (refused only under Local only when that
            # model leaves the network).
            _pin_refusal = _helpers.target_refusal(
                _hp, entry=entry, record=rec,
                manager=getattr(self.platform, "providers", None),
                platform=self.platform,
            )
            if _pin_refusal:
                return ToolResult(ok=False, output="", error=_pin_refusal)
            # LOCAL ONLY outside a coding session (v1.394.0): a run that is
            # Local only never starts an agent whose own model leaves the
            # network — said here, before any session exists.
            if _hp is None and _local_only_now():
                _lo = _model_team.local_only_refusal(self.platform, rec, entry.name)
                if _lo:
                    return ToolResult(ok=False, output="", error=_lo)
            # v1.394.0 review: a member's account stays in the caller's
            # account GROUP — refused here, before any session exists.
            if _hp is None:
                _ar = _model_team.account_refusal(self.platform, rec)
                if _ar:
                    return ToolResult(ok=False, output="", error=_ar)
            # v1.395.0 review: a member NAMED by the planner runs on an account
            # decision 8's floor allows (never new work at 95%), in the WORK's
            # account group, under the pay-per-use limit — or is refused in
            # words here, before any session exists (``named_account``). The
            # router already did this for a role's pick.
            if _route is None and _model_team.runs_elsewhere(rec):
                from ..iron_proxy.pin import current as _current_pin

                _named = await asyncio.to_thread(
                    _model_team.named_account, self.platform, rec,
                    _hp.pin if _hp is not None else _current_pin(),
                    scope=str(ctx.session_id or ""), coding=_hp is not None,
                )
                if _named.refusal:
                    return ToolResult(ok=False, output="", error=_named.refusal)
                _member_pin, _member_pin_set = _named.pin, True
            elif _route is not None and _route.pin_set:
                _member_pin, _member_pin_set = _route.pin, True
            dyn_provider = (rec.provider or None) if rec is not None else None
            # v1.394.0: an agent pinned to a PROVIDER runs its own model, else
            # that provider's own default (``model_team.pinned_model``).
            dyn_model = (_model_team.pinned_model(self.platform, rec) or None) if rec is not None else None
            # v1.394.0: a member's helper inherits from the turn but runs on
            # ITS OWN model and account (no-op for everyone else).
            # v1.395.0: on the account the TEAM ROUTER chose, when it chose
            # (review: and the account ``named_account`` chose for a member
            # named directly).
            _hp = (
                _helpers.for_member(_hp, self.platform, rec, pin=_member_pin)
                if _member_pin_set
                else _helpers.for_member(_hp, self.platform, rec)
            )
            agent_type = definition.type
        else:
            _name = entry.name if entry is not None else raw_type
            try:
                # Case-insensitive on purpose: models capitalize role names
                # ("Planner", "SUPERVISOR") constantly, and AgentType values
                # are lowercase — without the fold, a capitalized coordinator
                # name would raise here and be silently coerced to BUILDER,
                # dodging the honest refusal below.
                agent_type = AgentType(_name.strip().lower())
            except ValueError:
                agent_type = AgentType.BUILDER

        # Anti-fork-bomb, GENERALIZED (v1.166.0): never delegate to a target
        # whose definition itself carries the `delegate` tool — the SUPERVISOR
        # always did, the PLANNER carries it now, and a dynamic agent listing
        # `delegate` counts the same way. A coordinator delegating to another
        # coordinator is exactly the prompt-injected loop that would fan out
        # exponentially into real LLM sessions; the depth cap below bounds the
        # rest. A dynamic agent BASED on the supervisor type still counts as a
        # supervisor even when its stored tool list is empty.
        if agent_type is AgentType.SUPERVISOR:
            return ToolResult(
                ok=False,
                output="",
                error="cannot delegate to a 'supervisor' — delegate to a specialist "
                "agent (builder/researcher/reviewer) instead",
            )
        target_def = definition or get_agent_definition(agent_type)
        if "delegate" in (target_def.tools or []):
            label = entry.name if entry is not None else agent_type.value
            return ToolResult(
                ok=False,
                output="",
                error=f"cannot delegate to '{label}' — it can delegate work "
                "itself, and coordinator-to-coordinator delegation could fan "
                "out without bound; delegate to a specialist agent "
                "(builder/researcher/reviewer) instead",
            )
        if self._delegation_depth(ctx.agent_run_id) >= _MAX_DELEGATION_DEPTH:
            return ToolResult(
                ok=False,
                output="",
                error=f"delegation depth limit ({_MAX_DELEGATION_DEPTH}) reached — "
                "do this subtask directly instead of delegating further",
            )

        orch = Orchestrator(self.platform)
        # Subagents INHERIT the parent session's provider/model so a real
        # multi-agent run uses the user's chosen model end-to-end (a Claude
        # supervisor delegates to Claude subagents — not the offline mock).
        # Fall back to the configured defaults when the parent is unknown.
        # A dynamic agent's own pinned provider/model (its record) wins over
        # the inheritance — pinning is the point of pinning.
        parent = orch.get_session(ctx.session_id)
        provider = dyn_provider or (parent.provider if parent else None)
        # v1.394.0: an agent pinned to a PROVIDER runs that provider's model —
        # its own, else that provider's default; never the parent's model id,
        # which belongs to another provider (``spawn_agent``: the same rule).
        model = dyn_model if dyn_provider else (dyn_model or (parent.model if parent else None))
        # Spine: the child stays in the PARENT's project (not whatever is globally
        # active now), so a delegated subtask grounds in the same workspace.
        project_id = parent.project_id if parent else None
        # …and the FOLDER (v1.193.0): when the parent runs directly in the
        # user's real folder, the child works THERE, not in a scratch dir the
        # parent and the user never look at. See ``inherited_workspace_root``
        # for why a worktree/managed parent keeps its isolation.
        workspace_root = inherited_workspace_root(self.platform.config, parent)
        # …and the GRANTS (v1.288.0): the tools the user pre-approved on the
        # parent's job, so a Team worker can use the shell the user already
        # allowed — see ``inherited_grants``.
        allow_tools, approval_mode = inherited_grants(parent)
        # …and, inside a MISSION only, the right to ASK (v1.309.0): the member
        # origin, from the parent or the team root. Every other child: None.
        origin = inherited_origin(parent, _root)
        _trust, _trust_reason = inherited_trust(parent)
        # The job card's posture and step budget apply when the caller states
        # none (v1.295.0); ``create_session`` normalises the posture.
        max_steps = None
        if rec is not None:
            approval_mode = approval_mode or (rec.approval_mode or "")
            max_steps = rec.max_steps or None
        target_name = entry.name if entry is not None else agent_type.value
        # v1.395.0: a ROLE's piece is credited to the member's record for it
        # (``agents/team_record``) — only when the member HOLDS that role.
        _record_role = (
            _team_role
            if (rec is not None and _team_role and _team_role in _model_team.roles_of(rec))
            else ""
        )
        # A reviewer member is asked for a verdict line (team_record), which
        # the record credits to the work it reviewed.
        _child_task = _model_team.review_task(_record_role, task)
        # AN IJ HARNESS TURN'S HELPER (v1.372.0) has no parent ROW (its turn
        # runs under ``build:<id>``): everything above read None and would
        # have handed it the configured default model in a scratch folder.
        # It inherits from the turn instead — the SESSION's provider + model
        # (never a pin of its own: refused above), folder, project, the
        # conversation's grants + posture (``yolo`` is normalised by
        # ``create_session``), trust, Local only, and the ATTENDED asking
        # origin so its asks reach the Build screen.
        _helper_options = None
        if _hp is not None:
            provider, model = _hp.provider or provider, _hp.model or model
            project_id = _hp.project_id or project_id
            workspace_root = _hp.folder or workspace_root
            allow_tools, approval_mode = _hp.grants()
            if rec is not None:
                approval_mode = approval_mode or (rec.approval_mode or "")
            _trust, _trust_reason = _hp.trust()
            origin = _helpers.HELPER_ORIGIN
            _helper_options = _hp.options()

        # BOUNDED FAN-OUT (v1.193.0). A coordinator emitting 8 delegate calls in
        # ONE assistant turn used to start 8 concurrent child runs — these calls
        # go straight to AgentRuntime.run and never touch the orchestrator's
        # governor. Routing them through ``spawn_managed`` would DEADLOCK (the
        # parent is blocked awaiting the child while holding a governed slot);
        # ``child_slot`` documents the cycle-free alternative in full.
        # v1.372.0: a harness turn's helpers also wait for one of the
        # session's 3 helper slots (``helpers.helper_slot``; no-op otherwise).
        async with child_slot(
            self.platform.config,
            child_fanout_key(ctx.agent_run_id, ctx.session_id),
        ), _helpers.helper_slot(_hp):
            child_session = await orch.create_session(
                _child_task,
                agent_type,
                provider=provider,
                model=model,
                project_id=project_id,
                workspace_root=workspace_root,
                allow_tools=allow_tools,
                approval_mode=approval_mode,
                max_steps=max_steps,
                # Credit the run to the teammate that actually ran (v1.193.0).
                # The event below carries the same name, but stamping the row
                # makes attribution survive a dropped or renamed event instead
                # of silently falling back to the base type.
                agent_name=target_name,
                # …and the TRUST posture (v1.298.0): a low parent's child is
                # low — trust only flows down (``inherited_trust``).
                trust=_trust,
                trust_reason=_trust_reason,
                # A mission's teammate may ask the user (v1.309.0).
                origin=origin,
                # v1.372.0: a harness turn's local-only helper (passed only
                # then — every other call keeps its exact kwargs).
                **({"options": _helper_options} if _helper_options else {}),
            )
            await publish_delegation_started(
                self.platform,
                session_id=ctx.session_id,
                parent_run_id=ctx.agent_run_id,
                child_session_id=child_session.id,
                target=target_name,
                task=task,
                role=_team_role if _route is not None else "",
                reason=_route.reason if _route is not None else "",
            )
            # v1.372.0: the harness row starts following the helper.
            _helpers.child_started(_hp, self.platform, child_session.id, target_name, task)
            # v1.403.0: a piece really started on a worker of the session's team.
            _helpers.note_team_piece(_hp, target_name)

            # The child must ALWAYS settle (v1.167.0). A bare await left the child
            # session ACTIVE forever when the parent was cancelled mid-delegation
            # (Stop on a supervisor) or the child's runtime raised (a provider
            # refusing per the v1.162.0 no-mock rule) — never finalized, never
            # learned from, lying on the kanban board.
            try:
                # v1.307.0: the child is VISIBLY working for its team (roster
                # liveness, the mission view's cards) and its stream mirrors to
                # the team root — display only, see agents/team.py.
                with _team.working(
                    self.platform, child_session.id, ctx.session_id, target_name, task
                ):
                    # The child runs as its OWN task (v1.309.0) so Stop on
                    # this one teammate can cancel exactly it
                    # (``team.stop_child``); awaiting it keeps the old
                    # semantics, and a cancel of THIS run still reaches it.
                    # v1.372.0: a harness helper's task is created with the
                    # turn's account pin armed (``helpers.pinned``).
                    # v1.372.0 review: and it works with the SESSION's tools
                    # (``helpers.helper_definition``; unchanged off a turn).
                    # v1.394.0: a MODEL TEAM MEMBER's run is HELD to its own
                    # model (and its own account pin) outside a coding session
                    # too (``model_team.member_scope``; a no-op for everyone
                    # else, and inside a session ``pinned`` does it).
                    # v1.394.0 review: and a non-member started from inside a
                    # member's run steps out of THAT member's hold.
                    # v1.395.0: on the account the TEAM ROUTER chose, when it chose
                    # (review: or ``named_account`` for a member named directly).
                    if _hp is not None:
                        _scope = _model_team.member_scope(self.platform, None)
                    elif _member_pin_set:
                        _scope = _model_team.member_scope(
                            self.platform, rec, str(provider or ""), pin=_member_pin
                        )
                    else:
                        _scope = _model_team.member_scope(self.platform, rec, str(provider or ""))
                    with _helpers.pinned(_hp), _scope:
                        child_task = asyncio.ensure_future(
                            AgentRuntime(self.platform).run(
                                child_session,
                                _helpers.helper_definition(
                                    _hp,
                                    definition or get_agent_definition(child_session.agent_type),
                                    custom=_model_team.helper_is_custom(rec),
                                ),
                                parent_id=ctx.agent_run_id,
                            )
                        )
                    _team.bind_task(child_session.id, child_task)
                    run = await child_task
            except asyncio.CancelledError:
                if await user_stopped_child(orch, child_session.id):
                    # THE USER STOPPED THIS TEAMMATE (v1.309.0), not the
                    # coordinator: settle it CANCELLED, close the edge, and
                    # hand the coordinator a result it can act on.
                    await orch._finalize_cancelled(child_session)
                    await publish_delegation_completed(
                        self.platform,
                        session_id=ctx.session_id,
                        parent_run_id=ctx.agent_run_id,
                        child_run_id=None,
                        child_session_id=child_session.id,
                        target=target_name,
                        ok=False,
                        result="stopped by the user",
                    )
                    return ToolResult(
                        ok=False,
                        output="",
                        error=stopped_by_user_error(target_name),
                        data={
                            "child_session_id": child_session.id,
                            "agent_type": child_session.agent_type.value,
                            "target": target_name,
                            "state": "cancelled",
                        },
                    )
                # A DEADLINE IS NOT THE USER (v1.288.0): only when the
                # registry's own deadline is what cancelled us do the words
                # change; every other cancel keeps "cancelled by the user".
                by_deadline = tool_deadline_expired()
                await orch._finalize_cancelled(
                    child_session,
                    reason=TIME_LIMIT_REASON if by_deadline else None,
                )
                # …and CLOSE THE ANNOUNCED EDGE. `delegation.started` is already
                # out; a Stop on the supervisor would otherwise leave that edge
                # open in the event stream FOREVER, so a timeline reader sees a
                # handoff that never settles even though the child is CANCELLED.
                # Awaiting a publish while unwinding a cancellation is what
                # `_finalize_cancelled` itself does with SESSION_COMPLETED —
                # guarded the same way, so nothing can displace the re-raise.
                try:
                    await publish_delegation_completed(
                        self.platform,
                        session_id=ctx.session_id,
                        parent_run_id=ctx.agent_run_id,
                        child_run_id=None,  # the run was cancelled mid-flight
                        child_session_id=child_session.id,
                        target=target_name,
                        ok=False,
                        # The timeline reads this event, not the child row:
                        # it must not say "cancelled" for a time limit either.
                        result=TIME_LIMIT_REASON if by_deadline else "cancelled",
                    )
                except Exception:  # noqa: BLE001 - never block the unwind
                    pass
                raise  # the parent's cancellation keeps propagating
            except Exception as exc:  # noqa: BLE001
                from ..providers.failure_words import refusal_reason

                # v1.378.0 review: a REFUSED child tells its parent so in the
                # run's words ("… running it again as it is will be declined
                # too") — never the vendor's raw words, and never a hint that
                # handing the same task out again could help.
                refused = refusal_reason(exc)
                # v1.394.0 review: named as the planner named it (a team
                # member's own name, never its base type — "builder crashed"
                # sent the planner looking at the wrong teammate).
                error = (
                    f"delegated agent '{target_name}' could not finish: {refused}"
                    if refused
                    else f"delegated agent '{target_name}' crashed: "
                    f"{type(exc).__name__}: {exc}"
                )
                await orch._finalize_failed(child_session, exc)
                # v1.395.0 review: a run that RAISED writes NO row on the
                # member's record. A run fails at its WORK by ending failed
                # (its steps ran out, it gave up — the settle path below); an
                # exception escaping it is the provider (down, busy, refusing),
                # the account (its limit, a sign-in), Local only, the hold — or
                # Iron Jarvis itself. None of that says the member is bad at
                # the role, and counting it demoted a local member whose box
                # was merely busy, or a cloud member whose account hit its limit.
                await publish_delegation_completed(
                    self.platform,
                    session_id=ctx.session_id,
                    parent_run_id=ctx.agent_run_id,
                    child_run_id=None,
                    child_session_id=child_session.id,
                    target=target_name,
                    ok=False,
                    result=error,
                )
                return ToolResult(
                    ok=False,
                    output="",
                    error=error,
                    data={
                        "child_session_id": child_session.id,
                        "agent_type": child_session.agent_type.value,
                        "state": "failed",
                        **({"role": _team_role, "reason": _route.reason} if _route is not None else {}),
                        **_routed_member(_route, rec, provider, model),
                    },
                )

            # Reflect the run's outcome onto the child session and persist it —
            # through the ONE child settle (v1.307.0): status, spend, the
            # worklist claims handed back, the honest outcome, the folder note.
            # A teammate the USER stopped (v1.309.0) is settled CANCELLED there,
            # and the coordinator is told in words — then carries on.
            stopped = await orch.settle_child(child_session, run)
            _team.consume_stop(child_session.id)
            if stopped:
                await publish_delegation_completed(
                    self.platform,
                    session_id=ctx.session_id,
                    parent_run_id=ctx.agent_run_id,
                    child_run_id=run.id,
                    child_session_id=child_session.id,
                    target=target_name,
                    ok=False,
                    result="stopped by the user",
                )
                return ToolResult(
                    ok=False,
                    output="",
                    error=stopped_by_user_error(target_name),
                    data={
                        "child_run_id": run.id,
                        "child_session_id": child_session.id,
                        "agent_type": agent_type.value,
                        "target": target_name,
                        "state": "cancelled",
                    },
                )

            # Close the learning loop for the child: delegated work teaches the
            # system too (evaluate -> record outcome -> reflect). Best-effort so a
            # learning failure never breaks delegation.
            try:
                orch._post_run_learning(child_session)
            except Exception:  # noqa: BLE001
                pass
            await orch._post_run_allowance(child_session)

            ok = run.state is AgentState.COMPLETED
            # v1.395.0: the piece's row in the member's record for its role
            # (and a reviewer's verdict on the work it reviewed). Review: on
            # the model it ran on, and the account it was pinned to.
            await _model_team.note_piece(
                self.platform, record=rec, role=_record_role, session_id=child_session.id,
                scope=ctx.session_id, piece=_piece, finished=ok, report=run.result,
                provider=str(provider or ""), model=str(model or ""),
                account=str(getattr(_hp.pin if _hp is not None else _member_pin, "profile_id", "") or ""),
                turn=str(getattr(_hp, "turn", "") or ""),
            )
            await publish_delegation_completed(
                self.platform,
                session_id=ctx.session_id,
                parent_run_id=ctx.agent_run_id,
                child_run_id=run.id,
                child_session_id=child_session.id,
                target=target_name,
                ok=ok,
                result=run.result,
            )
            return ToolResult(
                ok=ok,
                # The child's summary PLUS its address, so the parent can reach
                # it again (message_agent) instead of only reading its report.
                output=_with_handle(
                    run.result,
                    delegation_handle(target_name, run.id, child_session.id),
                ),
                error=None if ok else (run.result or "subagent failed"),
                data={
                    "child_run_id": run.id,
                    "child_session_id": child_session.id,
                    "agent_type": agent_type.value,
                    # The canonical roster name that was actually spawned —
                    # "custom:<slug>" for a dynamic agent, else the builtin type
                    # (additive; agent_type stays the base-type for compat).
                    "target": target_name,
                    "state": run.state.value,
                    # Where the child actually worked: the parent's real folder
                    # when it had one, else its own disposable workspace.
                    "workspace": child_session.workspace_path,
                    # v1.395.0: the role asked for, and why this member took it.
                    **({"role": _team_role, "reason": _route.reason} if _route is not None else {}),
                    # v1.401.0: and WHO took it, on which model (the finish
                    # check's team review names its reviewer from these).
                    **_routed_member(_route, rec, provider, model),
                },
            )

    async def _handoff_remote(self, entry, task: str, ctx: ToolContext) -> ToolResult:
        """``delegate``'s REMOTE branch, ANNOUNCED (v1.309.0).

        A remote teammate opens no local session, so the mission view knows it
        only from ``delegation.*`` events — and this branch published none, so
        a remote doing a mission's work had no card, no counter and no line in
        the activity log. Announced HERE, not inside :meth:`_delegate_remote`:
        ``consult`` reuses that helper and a consult hands over no work.
        ``child_session_id`` is empty (there is no session) and ``handoff_id``
        keys this handoff's two events together. The settled ``result`` is a
        shortening of the FENCED text only — never the raw reply, which is
        attacker-reachable."""
        handoff_id = new_id("handoff")
        edge = dict(
            session_id=ctx.session_id,
            parent_run_id=ctx.agent_run_id,
            child_session_id="",
            target=entry.name,
            handoff_id=handoff_id,
        )
        await publish_delegation_started(self.platform, task=task, **edge)
        try:
            res = await self._delegate_remote(entry, task)
        except asyncio.CancelledError:
            # Close the announced edge on the way out, as the local path does.
            try:
                await publish_delegation_completed(
                    self.platform,
                    child_run_id=None,
                    ok=False,
                    result=TIME_LIMIT_REASON if tool_deadline_expired() else "cancelled",
                    **edge,
                )
            except Exception:  # noqa: BLE001 - never block the unwind
                pass
            raise
        except Exception as exc:  # noqa: BLE001 — close the edge, then re-raise
            await publish_delegation_completed(
                self.platform,
                child_run_id=None,
                ok=False,
                result=f"the remote call failed ({type(exc).__name__})",
                **edge,
            )
            raise
        await publish_delegation_completed(
            self.platform,
            child_run_id=None,
            ok=bool(res.ok),
            # Both are already fenced by ``_delegate_remote``.
            result=res.output if res.ok else res.error,
            **edge,
        )
        return res

    async def _delegate_remote(self, entry, task: str) -> ToolResult:
        """A roster-validated ``remote:<name>`` target: the existing
        RemoteAgentRegistry ask path (the same machinery reflex ``/ask`` and
        the ``delegate_remote`` tool use) — no local session, the remote's
        reply is the result.

        SECURITY: the reply is externally sourced. ``DelegateTool`` itself is
        not flagged ``returns_untrusted_content`` (its normal output is a
        local subagent's summary), so routing remote work through it would
        bypass the runtime's injection fence — the fence is therefore applied
        HERE, exactly as the runtime applies it for ``delegate_remote``."""
        from ..computeruse.safety import detect_injection, wrap_untrusted
        from .remote import RemoteAgentRegistry

        name = entry.name.split(":", 1)[-1]
        registry = RemoteAgentRegistry(self.platform.engine)
        record = registry.get(name)
        if record is None or not record.enabled:
            return ToolResult(
                ok=False,
                output="",
                error=f"remote agent '{name}' is not available right now",
            )
        res = await registry.run(record, task, self.platform.secrets.get)
        if not res.get("ok"):
            # The FAILURE detail is fenced too: on a non-2xx the registry puts
            # a snippet of the remote's RAW response body into ``detail``, so
            # an attacker-controlled error page must ride back as fenced data
            # — the same v1.98.1 rule the runtime applies to delegate_remote
            # (gating the fence on ok let error text skip the scan).
            detail = str(res.get("detail") or "remote agent call failed")
            err_inj = detect_injection(detail)
            return ToolResult(
                ok=False,
                output="",
                error=wrap_untrusted(
                    f"[content withheld — suspected {err_inj['category']}: "
                    f"{err_inj['reason']}]"
                    if err_inj["flagged"]
                    else detail
                ),
                data={"target": entry.name, "kind": record.kind},
            )
        raw = str(res.get("result") or "")
        inj = detect_injection(raw)
        fenced = wrap_untrusted(
            f"[content withheld — suspected {inj['category']}: {inj['reason']}]"
            if inj["flagged"]
            else raw
        )
        return ToolResult(
            ok=True,
            output=fenced,
            data={"target": entry.name, "kind": record.kind},
        )
