"""Delegate tool (§12 Multi-Agent Orchestration).

A coordinator (the Supervisor, and since v1.166.0 the Planner) uses this tool
to hand a subtask to a freshly-spawned subagent.
Each delegation gets its *own* session and runs the agent runtime to
completion. The subagent operates independently, never contacts the user, and
returns only a SUMMARIZED result back to the supervisor — everything flows
through the supervisor.

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
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, AgentType, SessionStatus
from ..tools.base import Tool, ToolContext, ToolResult

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
) -> None:
    """Announce a handoff on the PARENT's session (see ``EventType``)."""
    await platform.event_bus.publish(
        EventType.DELEGATION_STARTED,
        {
            "parent_run_id": parent_run_id or None,
            "child_session_id": child_session_id,
            "agent": target,
            "task": _short(task),
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
        },
        session_id=session_id,
    )


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
        "subagent)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent_type": {"type": "string"},
            "task": {"type": "string"},
        },
        "required": ["task"],
    }
    permission_key = "delegate"

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
            inherited_workspace_root,
        )
        from .runtime import AgentRuntime
        from .types import get_agent_definition

        task = args.get("task") or ""
        raw_type = str(args.get("agent_type") or "builder").strip()

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
            return await self._delegate_remote(entry, task)

        # Local targets: a dynamic agent runs its OWN stored definition; a
        # builtin resolves to its canonical type; anything the roster does not
        # know falls back to today's behavior byte-for-byte (AgentType(...) or
        # the builder default).
        definition = None
        dyn_provider = dyn_model = None
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
            dyn_provider = (rec.provider or None) if rec is not None else None
            dyn_model = (rec.model or None) if rec is not None else None
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
        model = dyn_model or (parent.model if parent else None)
        # Spine: the child stays in the PARENT's project (not whatever is globally
        # active now), so a delegated subtask grounds in the same workspace.
        project_id = parent.project_id if parent else None
        # …and the FOLDER (v1.193.0): when the parent runs directly in the
        # user's real folder, the child works THERE, not in a scratch dir the
        # parent and the user never look at. See ``inherited_workspace_root``
        # for why a worktree/managed parent keeps its isolation.
        workspace_root = inherited_workspace_root(self.platform.config, parent)
        target_name = entry.name if entry is not None else agent_type.value

        # BOUNDED FAN-OUT (v1.193.0). A coordinator emitting 8 delegate calls in
        # ONE assistant turn used to start 8 concurrent child runs — these calls
        # go straight to AgentRuntime.run and never touch the orchestrator's
        # governor. Routing them through ``spawn_managed`` would DEADLOCK (the
        # parent is blocked awaiting the child while holding a governed slot);
        # ``child_slot`` documents the cycle-free alternative in full.
        async with child_slot(
            self.platform.config,
            child_fanout_key(ctx.agent_run_id, ctx.session_id),
        ):
            child_session = await orch.create_session(
                task,
                agent_type,
                provider=provider,
                model=model,
                project_id=project_id,
                workspace_root=workspace_root,
                # Credit the run to the teammate that actually ran (v1.193.0).
                # The event below carries the same name, but stamping the row
                # makes attribution survive a dropped or renamed event instead
                # of silently falling back to the base type.
                agent_name=target_name,
            )
            await publish_delegation_started(
                self.platform,
                session_id=ctx.session_id,
                parent_run_id=ctx.agent_run_id,
                child_session_id=child_session.id,
                target=target_name,
                task=task,
            )

            # The child must ALWAYS settle (v1.167.0). A bare await left the child
            # session ACTIVE forever when the parent was cancelled mid-delegation
            # (Stop on a supervisor) or the child's runtime raised (a provider
            # refusing per the v1.162.0 no-mock rule) — never finalized, never
            # learned from, lying on the kanban board.
            try:
                run = await AgentRuntime(self.platform).run(
                    child_session,
                    definition or get_agent_definition(child_session.agent_type),
                    parent_id=ctx.agent_run_id,
                )
            except asyncio.CancelledError:
                await orch._finalize_cancelled(child_session)
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
                        result="cancelled",
                    )
                except Exception:  # noqa: BLE001 - never block the unwind
                    pass
                raise  # the parent's cancellation keeps propagating
            except Exception as exc:  # noqa: BLE001
                error = (
                    f"delegated agent '{child_session.agent_type.value}' crashed: "
                    f"{type(exc).__name__}: {exc}"
                )
                await orch._finalize_failed(child_session, exc)
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
                    },
                )

            # Reflect the run's outcome onto the child session and persist it.
            child_session.status = (
                SessionStatus.COMPLETED
                if run.state is AgentState.COMPLETED
                else SessionStatus.FAILED
            )
            child_session.provider, child_session.model = run.provider, run.model
            child_session.summary = run.result
            child_session.finished_at = utcnow()
            orch._save(child_session)

            # Close the learning loop for the child: delegated work teaches the
            # system too (evaluate -> record outcome -> reflect). Best-effort so a
            # learning failure never breaks delegation.
            try:
                orch._post_run_learning(child_session)
            except Exception:  # noqa: BLE001
                pass

            ok = run.state is AgentState.COMPLETED
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
                },
            )

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
