"""Delegate tool (§12 Multi-Agent Orchestration).

The Supervisor uses this tool to hand a subtask to a freshly-spawned subagent.
Each delegation gets its *own* session with an isolated, disposable workspace
(§15) and runs the agent runtime to completion. The subagent operates
independently, never contacts the user, and returns only a SUMMARIZED result
back to the supervisor — everything flows through the supervisor.

The child ``AgentRun`` is linked to the caller via ``parent_id`` so the
supervisor → subagent hierarchy is reconstructable from persistence.
"""

from __future__ import annotations

from typing import Any

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.models import AgentRun, AgentState, AgentType, SessionStatus
from ..tools.base import Tool, ToolContext, ToolResult

#: Hardest cap on the supervisor→subagent delegation chain. Combined with
#: "no delegating to a SUPERVISOR" (only supervisors carry the delegate tool, so a
#: specialist child can't recurse), this bounds a prompt-injected fork-bomb.
_MAX_DELEGATION_DEPTH = 3


class DelegateTool(Tool):
    name = "delegate"
    description = (
        "Delegate a subtask to a subagent. The subagent runs independently in "
        "its own isolated workspace and returns a summarized result. Use one "
        "delegate call per subtask. Args: agent_type — a name from the 'Who "
        "can take this work' roster when one is shown: builtin specialists "
        "('builder', 'researcher', 'reviewer', 'planner'), a listed "
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
        from .orchestrator import Orchestrator
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
                "(builder/researcher/reviewer/planner)",
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
                agent_type = AgentType(_name)
            except ValueError:
                agent_type = AgentType.BUILDER

        # Anti-fork-bomb: never delegate to another SUPERVISOR (only supervisors
        # carry the delegate tool, so a specialist child can't recurse), and cap the
        # chain depth. A prompt-injected 'delegate this to a supervisor' loop would
        # otherwise fan out exponentially into real LLM sessions. A dynamic agent
        # BASED on the supervisor type counts as a supervisor here.
        if agent_type is AgentType.SUPERVISOR:
            return ToolResult(
                ok=False,
                output="",
                error="cannot delegate to a 'supervisor' — delegate to a specialist "
                "agent (builder/researcher/reviewer/planner) instead",
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
        child_session = await orch.create_session(
            task, agent_type, provider=provider, model=model, project_id=project_id
        )

        run = await AgentRuntime(self.platform).run(
            child_session,
            definition or get_agent_definition(child_session.agent_type),
            parent_id=ctx.agent_run_id,
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
        return ToolResult(
            ok=ok,
            output=run.result,
            error=None if ok else (run.result or "subagent failed"),
            data={
                "child_run_id": run.id,
                "child_session_id": child_session.id,
                "agent_type": agent_type.value,
                # The canonical roster name that was actually spawned —
                # "custom:<slug>" for a dynamic agent, else the builtin type
                # (additive; agent_type stays the base-type for compat).
                "target": entry.name if entry is not None else agent_type.value,
                "state": run.state.value,
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
