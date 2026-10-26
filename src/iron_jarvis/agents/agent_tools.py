"""Agent-management tools — "agents that add more agents" (§11/§12 extension).

Three tools, backed by a :class:`~iron_jarvis.agents.dynamic.DynamicAgentRegistry`,
let a user *or* an agent extend the platform at runtime:

* ``create_agent`` — register a new dynamic agent (name + prompt + tool allowlist).
* ``list_agents``  — enumerate built-in agent types and dynamic agents.
* ``spawn_agent``  — run a built-in OR dynamic agent as a child subagent.

``spawn_agent`` mirrors the ``delegate`` tool: it creates a child session, runs
the agent runtime to completion, links the child ``AgentRun`` to the caller via
``parent_id``, and returns the summarized result. Since v1.193.0 the mirror also
covers the three team behaviors delegate gained — the child inherits a DIRECT
workspace (the user's real folder) and is otherwise isolated as before, its
fan-out is bounded by the same per-parent cap, and the handoff is announced as
``delegation.started``/``delegation.completed`` with the child's address in the
tool output — and, because the child now works in the user's real folder, the
always-settle guard too (a crashed child left ACTIVE would block every later
turn in that folder). That mirror already included ``delegate``'s anti-fork-bomb guards — no
supervisor target, no target that can itself hand work out, and the shared
``_MAX_DELEGATION_DEPTH`` parent-chain cap. Orchestrator / runtime / definition
lookups are imported lazily inside ``execute`` to avoid an agents-package import
cycle at module load.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from ..core.models import AgentState, AgentType
from ..tools.base import Tool, ToolContext, ToolResult
from .roster import canonical_roster_name
from .types import _DEFINITIONS

if TYPE_CHECKING:  # type-only; avoids importing at module load
    from .dynamic import DynamicAgentRegistry


class CreateAgentTool(Tool):
    name = "create_agent"
    description = (
        "Define a new agent at runtime and persist it. The agent reuses a base "
        "agent type but carries its own system prompt and tool allowlist, so it "
        "can later be launched with `spawn_agent`. Args: name (unique), "
        "system_prompt, tools (list of tool names it may use), and an optional "
        "description and base_type (defaults to 'builder')."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "system_prompt": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "base_type": {"type": "string"},
        },
        "required": ["name", "system_prompt", "tools"],
    }
    permission_key = "create_agent"

    def __init__(self, platform, registry: "DynamicAgentRegistry") -> None:
        self.platform = platform
        self.registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = (args.get("name") or "").strip()
        if not name:
            return ToolResult(ok=False, error="`name` is required")
        system_prompt = args.get("system_prompt") or ""
        tools = args.get("tools") or []
        if not isinstance(tools, list):
            return ToolResult(ok=False, error="`tools` must be a list of tool names")
        if not tools:
            # AN EMPTY LIST FROM A MODEL IS NOT THE SAME AS AN EMPTY FORM FIELD
            # (v1.178.0). Since this release a stored empty roster means "not
            # specified" and resolves to the BASE TYPE's full roster — which is
            # right for the dashboard, where the field does not exist and the
            # user plainly did not mean "no tools". Reached from HERE the same
            # semantics would let a model mint an agent holding everything its
            # base type holds, `shell` included, by simply omitting the list.
            # So this path demands the list it already declares as required:
            # naming the tools is the authoring decision.
            return ToolResult(
                ok=False,
                error=(
                    "`tools` must name at least one tool — an agent's tool list "
                    "is its capability contract, so it has to be stated here "
                    "rather than inherited. Call tool_list to see what exists."
                ),
            )
        # AN UPSERT KEEPS WHAT THE CALL DOES NOT NAME (v1.395.0, the Phase 5
        # review's open item — the ``_KEEP`` rule ``register`` already applies
        # to the job card). This tool names a prompt and a tool list; on an
        # EXISTING agent it used to reset its description to "", its base type
        # to "builder" and its model to the default — so a model "updating" a
        # team member's prompt quietly moved it off the model, roles and
        # account the user gave it. Now only the fields the call names change;
        # roles and the account are never this tool's to touch.
        existing = None
        try:
            existing = self.registry.get(name)
        except Exception:  # noqa: BLE001 — no record: a create
            existing = None
        if args.get("description") is not None:
            description = str(args.get("description") or "")
        else:
            description = str(getattr(existing, "description", "") or "") if existing is not None else ""
        if args.get("base_type"):
            base_type = str(args.get("base_type"))
        else:
            base_type = str(getattr(existing, "base_type", "") or "builder") if existing is not None else "builder"
        provider = str(getattr(existing, "provider", "") or "") if existing is not None else ""
        model = str(getattr(existing, "model", "") or "") if existing is not None else ""

        record = self.registry.register(
            name,
            system_prompt,
            [str(t) for t in tools],
            base_type=base_type,
            description=description,
            provider=provider,
            model=model,
        )
        return ToolResult(
            ok=True,
            output=(
                f"Created dynamic agent '{record.name}' (base={record.base_type}) "
                f"with tools: {', '.join(json.loads(record.tools_json))}"
            ),
            data={
                "name": record.name,
                "base_type": record.base_type,
                "tools": json.loads(record.tools_json),
                "description": record.description,
            },
        )


#: Tool names shown per agent before the listing switches to "… +N more".
#: A supervisor deciding who to delegate to needs the SHAPE of a roster, not a
#: transcript of it: spelling out ~50 names per agent would cost more context
#: than the decision is worth, and the full list still rides in ``data``.
_ROSTER_PREVIEW = 12


class ListAgentsTool(Tool):
    name = "list_agents"
    description = (
        "List all agents available to launch: the built-in agent types plus any "
        "dynamic agents created at runtime with `create_agent`."
    )
    input_schema = {"type": "object", "properties": {}}
    permission_key = "list_agents"

    def __init__(self, platform, registry: "DynamicAgentRegistry") -> None:
        self.platform = platform
        self.registry = registry

    def _effective_tools(self, name: str) -> list[str] | None:
        """What this agent ACTUALLY holds, inheritance resolved (v1.185.0).

        The HTTP route has reported this since v1.178.0 and this tool did not,
        so an AGENT asking what a teammate holds saw only the base type — while
        the human looking at the same agent in the dashboard saw the resolved
        roster. Whoever is deciding whether to delegate needs the same answer,
        and here that decision is made by the caller most likely to act on it.

        `None` means UNKNOWN and `[]` means genuinely none, the same dialect the
        route speaks: an unknown must never be relayed to a model as "this agent
        can do nothing", because that reads as a reason not to delegate.
        """
        try:
            definition = self.registry.definition(name)
        except Exception:  # noqa: BLE001 — a listing never fails on one row
            return None
        return list(definition.tools) if definition is not None else None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from . import model_team as _model_team
        from .delegate_tool import _local_only_now

        builtin = sorted(t.value for t in _DEFINITIONS)
        dynamic = []
        # LOCAL ONLY (v1.394.0): an agent whose OWN model leaves the network
        # is not listed — the roster leaves it out too, and both doors refuse
        # it in words; listing it here would offer work nobody can take.
        local_only = _local_only_now()
        for r in self.registry.list():
            if local_only and _model_team.leaves_network(self.platform, r):
                continue
            row: dict[str, Any] = {
                "name": r.name,
                "base_type": r.base_type,
                "description": r.description,
            }
            tools = self._effective_tools(r.name)
            if tools is not None:
                row["effective_tools"] = tools
            # THE MODEL TEAM (v1.394.0): a member says its roles and the
            # model it runs on — the facts the planner's list carries.
            facts = _model_team.member_facts(self.platform, r)
            if facts is not None:
                row["team"] = _model_team.facts_words(self.platform, facts)
            dynamic.append(row)
        lines = ["Built-in agents: " + ", ".join(builtin)]
        if dynamic:
            # THE ROSTER GOES IN THE TEXT, not only in `data` (v1.185.0). The
            # runtime hands the model `result.output` and nothing else, so a
            # field added to `data` alone would satisfy the letter of "the tool
            # reports effective tools" while the model — the only caller that
            # acts on it — still could not see it.
            lines.append("Dynamic agents:")
            for row in dynamic:
                head = f"  - {row['name']} (base={row['base_type']})"
                if row.get("team"):
                    head += f" [team member: {row['team']}]"
                tools = row.get("effective_tools")
                if tools is None:
                    # UNKNOWN, said as unknown. "no tools" here would read as a
                    # reason not to delegate to a perfectly capable agent.
                    lines.append(f"{head} — roster unavailable")
                elif not tools:
                    lines.append(f"{head} — holds no tools")
                else:
                    shown = tools[: _ROSTER_PREVIEW]
                    tail = (
                        f" … +{len(tools) - len(shown)} more"
                        if len(tools) > len(shown)
                        else ""
                    )
                    # Cap, then SAY SO — a silently short roster reads as
                    # complete and the model concludes a tool is absent.
                    lines.append(
                        f"{head} — {len(tools)} tools: " + ", ".join(shown) + tail
                    )
        else:
            lines.append("Dynamic agents: (none)")
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"builtin": builtin, "dynamic": dynamic},
        )


#: A spawn target holding ANY of these can hand work out again — the
#: generalized anti-fork-bomb rule ``delegate_tool.py`` documents, read for this
#: door. ``spawn_agent`` is in the set as well as ``delegate``: this tool is the
#: literal recursion, so leaving it out would guard the other door only. Nothing
#: is lost by it — a coordinator that legitimately wants an ``automation`` (the
#: one non-supervisor builtin carrying ``spawn_agent``) child still reaches it
#: through ``delegate``, which does not treat it as a coordinator.
_SPAWN_COORDINATOR_TOOLS = ("delegate", "spawn_agent")


class SpawnAgentTool(Tool):
    name = "spawn_agent"
    description = (
        "Launch a built-in OR dynamic agent as a subagent. The subagent runs "
        "independently and returns a summarized result plus its agent_run_id "
        "(usable with message_agent); when you are working in the user's folder "
        "it works in that same folder. "
        "Args: agent (a built-in type like 'builder' or the name of a "
        "dynamic agent created with `create_agent`) and task (the self-contained "
        "instruction). Instead of an agent you may give role — read, code, "
        "review or test — and Iron Jarvis picks the member of the user's model "
        "team for it (a local one first when its record for that role is good; "
        "a cloud account near its usage limit gets no new work). Give piece (a "
        "short name for this piece of work) and use the same piece name if you "
        "hand it out again after it failed: after it fails twice on one member "
        "it moves to the next one up."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "agent": {"type": "string"},
            "task": {"type": "string"},
            "role": {"type": "string", "enum": ["read", "code", "review", "test"]},
            "piece": {"type": "string"},
        },
        # v1.395.0: ``agent`` OR ``role`` — checked by ``execute`` in words.
        "required": ["task"],
    }
    permission_key = "spawn_agent"
    #: It awaits a whole child run — outside the per-call deadline (v1.288.0),
    #: exactly like ``delegate``.
    deadline_exempt = True

    def __init__(self, platform, registry: "DynamicAgentRegistry") -> None:
        self.platform = platform
        self.registry = registry

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # Lazy imports: avoid an agents-package import cycle at module load.
        from ..tools.registry import tool_deadline_expired
        from .delegate_tool import (
            _MAX_DELEGATION_DEPTH,
            TIME_LIMIT_REASON,
            DelegateTool,
            _local_only_now,
            _with_handle,
            delegation_handle,
            publish_delegation_completed,
            publish_delegation_started,
            stopped_by_user_error,
            user_stopped_child,
        )
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

        agent_name = (args.get("agent") or "builder").strip()
        task = args.get("task") or ""
        # AN IJ HARNESS TURN'S HELPER (v1.372.0) — the same parent ``delegate``
        # reads (``harness/helpers``); None for every other caller.
        _hp = _helpers.parent_for(ctx)

        # A PROJECT MISSION'S TEAM, read before routing (v1.395.0) so the team
        # router only asks the project's team (the same walk ``delegate`` does).
        _root = await asyncio.to_thread(
            _team.mission_root, self.platform.engine, ctx.session_id, ctx.agent_run_id
        )
        _project_team = _team.mission_team(_root) if _root is not None else []
        # A ROLE INSTEAD OF A NAME (v1.395.0) — exactly as ``delegate`` reads
        # it (MIRROR NOTE, lock-step): ``role`` or ``agent: "role:<id>"`` asks
        # the TEAM ROUTER; a named agent runs as before; an unknown role or one
        # no usable member holds is refused in words.
        _route = None
        _team_role = ""
        _piece = ""
        _role_text = _model_team.role_named(agent_name)
        _role_arg = args.get("role")
        if _role_text is not None or str(_role_arg or "").strip():
            _asked = _role_text if _role_text is not None else _role_arg
            _team_role = _model_team.role_id(_asked)
            if not _team_role:
                _helpers.note_route(_hp, reason="not started: that is not a team role")
                return ToolResult(ok=False, output="", error=_model_team.unknown_role_refusal(_asked))
            _piece = _model_team.piece_key(args.get("piece"), task)
            if _role_text is not None or not str(args.get("agent") or "").strip():
                _route = await asyncio.to_thread(
                    _model_team.route_for_call, self.platform, role=_team_role, piece=_piece,
                    session_id=ctx.session_id, helper_parent=_hp, team=_project_team,
                )
                _helpers.note_route(_hp, role=_team_role, reason=_route.reason)
                if _route.record is None:
                    return ToolResult(
                        ok=False, output="", error=_route.refusal,
                        data={"role": _team_role, "reason": _route.reason, "state": "not_started",
                              "waited": _route.waited},
                    )
                agent_name = _route.record.name
        elif not str(args.get("agent") or "").strip():
            # v1.395.0: ``agent`` is no longer a required key (a role may
            # stand in for it) — with neither, it is said, as the registry's
            # required check said it before.
            return ToolResult(
                ok=False, output="",
                error="missing required: agent — spawn_agent needs agent (a built-in type or a "
                "custom agent's name) or role (read, code, review or test), and task",
            )

        # A harness helper never leaves this PC (a ``remote:`` name is
        # refused, as in ``delegate``).
        _remote_refusal = _helpers.target_refusal(_hp, raw=agent_name)
        if _remote_refusal:
            return ToolResult(ok=False, output="", error=_remote_refusal)

        # Prefer a dynamic agent of this name; otherwise treat the name as a
        # built-in AgentType.
        definition = self.registry.definition(agent_name)
        record = self.registry.get(agent_name) if definition is not None else None
        if definition is not None:
            base_type = definition.type
            # THE DAY OFF / THE ALLOWANCE (v1.295.0): a paused or exhausted
            # custom agent is refused HERE, before any session row exists, in
            # the words the user set — never a silent run as its base type.
            from . import allowance

            reason = allowance.refusal_for(record, self.platform.engine)
            if reason:
                return ToolResult(ok=False, output="", error=reason)
        else:
            try:
                base_type = AgentType(agent_name)
            except ValueError:
                return ToolResult(
                    ok=False, error=f"unknown agent '{agent_name}'"
                )
            definition = get_agent_definition(base_type)

        # ANTI-FORK-BOMB. The module docstring says "spawn_agent mirrors the
        # delegate tool" and it mirrored everything EXCEPT the safety: it
        # accepted agent='supervisor' (whose own roster carries `delegate` AND
        # `spawn_agent`), never walked the parent chain, and is auto-approved
        # headlessly — so a prompt-injected coordinator could recurse
        # supervisors into supervisors without any bound. Same three guards as
        # DelegateTool, in the same order.
        #
        # A dynamic agent BASED on the supervisor type counts as a supervisor
        # even when its own stored tool list is empty.
        # A PROJECT MISSION'S TEAM (v1.308.0): the same rule `delegate`
        # applies — spawn is the other door the coordinator holds — read off
        # the mission ROOT through the same walk (v1.309.0), so a teammate
        # spawning a helper is held to the team too. (Read above, v1.395.0.)
        if _project_team:
            _who = canonical_roster_name(self.platform, agent_name)
            if not _team.on_team(_project_team, _who):
                return ToolResult(
                    ok=False, output="", error=_team.off_team_refusal(_project_team, _who)
                )
        if base_type is AgentType.SUPERVISOR:
            return ToolResult(
                ok=False,
                output="",
                error="cannot spawn a 'supervisor' — spawn a specialist agent "
                "(builder/researcher/reviewer) instead",
            )
        if any(t in (definition.tools or []) for t in _SPAWN_COORDINATOR_TOOLS):
            return ToolResult(
                ok=False,
                output="",
                error=f"cannot spawn '{agent_name}' — it can hand work out "
                "itself, and coordinator-to-coordinator spawning could fan out "
                "without bound; spawn a specialist agent "
                "(builder/researcher/reviewer) instead",
            )
        # ONE chain, ONE cap: spawn and delegate both link the child by
        # parent_id, so the walk DelegateTool already implements counts a mixed
        # delegate→spawn→delegate chain correctly. Imported rather than copied —
        # two copies of a safety limit drift.
        if (
            DelegateTool(self.platform)._delegation_depth(ctx.agent_run_id)
            >= _MAX_DELEGATION_DEPTH
        ):
            return ToolResult(
                ok=False,
                output="",
                error=f"delegation depth limit ({_MAX_DELEGATION_DEPTH}) reached — "
                "do this subtask directly instead of spawning further",
            )

        orch = Orchestrator(self.platform)
        # Subagents INHERIT the parent session's provider/model (like `delegate`)
        # so the user's chosen model is used end-to-end, not the offline mock —
        # and its PROJECT, so a spawned child grounds in the parent's workspace
        # (not whatever project is globally active now).
        parent = orch.get_session(ctx.session_id)
        # v1.394.0 (the long-jobs plan's Phase 5): a custom agent's OWN pinned
        # model is honoured here exactly as ``delegate`` honours it (spawn used
        # to ignore the pin and run every custom agent on the parent's model).
        # An agent pinned to a provider runs that provider's model — its own,
        # else that provider's default; never the parent's model id.
        rec_provider = (record.provider or None) if record is not None else None
        rec_model = (_model_team.pinned_model(self.platform, record) or None) if record is not None else None
        provider = rec_provider or (parent.provider if parent else None)
        model = rec_model if rec_provider else (rec_model or (parent.model if parent else None))
        project_id = parent.project_id if parent else None
        # …and the FOLDER (v1.193.0), on the same terms as `delegate`: a parent
        # working directly in the user's real folder hands that folder to its
        # child, so the team's output lands in one place; a managed/worktree
        # parent keeps the child isolated. One predicate, both doors.
        workspace_root = inherited_workspace_root(self.platform.config, parent)
        # …and the GRANTS (v1.288.0), through the same predicate `delegate`
        # uses: what the user pre-approved on the parent.
        allow_tools, approval_mode = inherited_grants(parent)
        # …and, inside a MISSION only, the right to ASK (v1.309.0) — the same
        # predicate `delegate` uses; every other child keeps no origin.
        origin = inherited_origin(parent, _root)
        _trust, _trust_reason = inherited_trust(parent)
        # The job card's posture and step budget apply when the caller states
        # none (v1.295.0); ``create_session`` normalises the posture.
        max_steps = None
        if record is not None:
            approval_mode = approval_mode or (record.approval_mode or "")
            max_steps = record.max_steps or None
        # AN IJ HARNESS TURN'S HELPER (v1.372.0): no parent row — it inherits
        # from the turn, exactly as in ``delegate`` (one predicate, both
        # doors): the session's provider + model (spawn never used a custom
        # agent's own pin), folder, project, grants + posture, trust, Local
        # only, and the attended asking origin.
        # v1.372.0 review: a custom agent pinned to another model is REFUSED
        # in words, exactly as ``delegate`` refuses it — Claude Code's
        # background agents arrive through this door, and running the agent
        # on the session's model while its card names another was a silent
        # override of the agent's own setting.
        _pin_refusal = _helpers.target_refusal(
            _hp, raw=agent_name, record=record,
            manager=getattr(self.platform, "providers", None),
            platform=self.platform,
        )
        if _pin_refusal:
            return ToolResult(ok=False, output="", error=_pin_refusal)
        # LOCAL ONLY outside a coding session (v1.394.0), as in ``delegate``.
        if _hp is None and record is not None and _local_only_now():
            _lo = _model_team.local_only_refusal(self.platform, record, agent_name)
            if _lo:
                return ToolResult(ok=False, output="", error=_lo)
        # v1.394.0 review: and its account stays in the caller's group.
        if _hp is None and record is not None:
            _ar = _model_team.account_refusal(self.platform, record)
            if _ar:
                return ToolResult(ok=False, output="", error=_ar)
        # v1.395.0 review: a member NAMED directly runs on an account decision
        # 8's floor allows, in the work's account group, under the pay-per-use
        # limit — or is refused in words before any session exists
        # (``named_account``; ``delegate``'s rule, MIRROR NOTE).
        _member_pin, _member_pin_set = None, False
        if _route is None and record is not None and _model_team.runs_elsewhere(record):
            from ..iron_proxy.pin import current as _current_pin

            _named = await asyncio.to_thread(
                _model_team.named_account, self.platform, record,
                _hp.pin if _hp is not None else _current_pin(),
                scope=str(ctx.session_id or ""), coding=_hp is not None,
            )
            if _named.refusal:
                return ToolResult(ok=False, output="", error=_named.refusal)
            _member_pin, _member_pin_set = _named.pin, True
        elif _route is not None and _route.pin_set:
            _member_pin, _member_pin_set = _route.pin, True
        # v1.394.0: a member's helper runs on ITS OWN model and account.
        # v1.395.0: on the account the TEAM ROUTER chose, when it chose
        # (review: or ``named_account`` for a member named directly).
        _hp = (
            _helpers.for_member(_hp, self.platform, record, pin=_member_pin)
            if _member_pin_set
            else _helpers.for_member(_hp, self.platform, record)
        )
        # v1.395.0: a ROLE's piece is credited to the member's record for it
        # — only when the member HOLDS that role (``delegate``'s rule).
        _record_role = (
            _team_role
            if (record is not None and _team_role and _team_role in _model_team.roles_of(record))
            else ""
        )
        _child_task = _model_team.review_task(_record_role, task)
        _helper_options = None
        if _hp is not None:
            provider, model = _hp.provider or provider, _hp.model or model
            project_id = _hp.project_id or project_id
            workspace_root = _hp.folder or workspace_root
            allow_tools, approval_mode = _hp.grants()
            if record is not None:
                approval_mode = approval_mode or (record.approval_mode or "")
            _trust, _trust_reason = _hp.trust()
            origin = _helpers.HELPER_ORIGIN
            _helper_options = _hp.options()

        # BOUNDED FAN-OUT (v1.193.0), the same cap `delegate` takes — spawn is
        # the other door onto the identical hazard. Deliberately NOT
        # `spawn_managed`: this caller is blocked awaiting the child, so parking
        # the child behind the session governor deadlocks it (`child_slot`).
        # v1.372.0: plus the session's 3 helper slots in a harness turn.
        async with child_slot(
            self.platform.config,
            child_fanout_key(ctx.agent_run_id, ctx.session_id),
        ), _helpers.helper_slot(_hp):
            child_session = await orch.create_session(
                _child_task,
                base_type,
                provider=provider,
                model=model,
                project_id=project_id,
                workspace_root=workspace_root,
                allow_tools=allow_tools,
                approval_mode=approval_mode,
                max_steps=max_steps,
                # Credit the run to the teammate that actually ran (v1.193.0) —
                # the same stamp `delegate` makes, so attribution survives a
                # dropped or renamed event instead of falling back to the base
                # type. FOLDED FIRST: spawn is called with the BARE slug
                # ("tax-reader") while delegate publishes the roster name
                # ("custom:tax-reader"), and stamping the raw slug would split
                # one teammate's history across two keys — the exact defect
                # canonical_roster_name exists to prevent.
                agent_name=canonical_roster_name(self.platform, agent_name),
                # …and the TRUST posture (v1.298.0): a low parent's child is
                # low — trust only flows down (``inherited_trust``).
                trust=_trust,
                trust_reason=_trust_reason,
                # A mission's teammate may ask the user (v1.309.0).
                origin=origin,
                # v1.372.0: a harness turn's local-only helper (only then).
                **({"options": _helper_options} if _helper_options else {}),
            )
            await publish_delegation_started(
                self.platform,
                session_id=ctx.session_id,
                parent_run_id=ctx.agent_run_id,
                child_session_id=child_session.id,
                target=agent_name,
                task=task,
                role=_team_role if _route is not None else "",
                reason=_route.reason if _route is not None else "",
            )
            # v1.372.0: the harness row starts following the helper.
            _helpers.child_started(
                _hp, self.platform, child_session.id,
                canonical_roster_name(self.platform, agent_name), task,
            )
            # THE CHILD MUST ALWAYS SETTLE — the same guard `delegate` grew in
            # v1.167.0, and since v1.193.0 spawn cannot go without it. A bare
            # await left a crashed child ACTIVE forever; that used to strand a
            # disposable scratch dir and block nothing, but now the child
            # INHERITS the user's real folder, and
            # `Orchestrator.continue_session`'s busy check refuses every later
            # turn in a workspace that holds an ACTIVE session — so one provider
            # refusal (the v1.162.0 case) would wedge the user's own chat/continue
            # lane in that folder until a daemon restart ran reconcile. Worse, it
            # is SILENT: `registry.invoke` traps this exception, so the parent run
            # carries on and nothing surfaces the wedge. It would also leave
            # `delegation.started` with no `delegation.completed`.
            try:
                # v1.307.0: visibly working for its team + streamed to the team
                # root (display only — agents/team.py), exactly as `delegate`.
                with _team.working(
                    self.platform,
                    child_session.id,
                    ctx.session_id,
                    canonical_roster_name(self.platform, agent_name),
                    task,
                ):
                    # Its OWN task (v1.309.0), so Stop on this one child can
                    # cancel exactly it — the same shape as `delegate`.
                    # v1.372.0: created with the turn's account pin armed.
                    # v1.372.0 review: and it works with the SESSION's tools
                    # (``helpers.helper_definition``; unchanged off a turn).
                    # v1.394.0: a member's run is held to its own model
                    # (``model_team.member_scope``) — the same as ``delegate``.
                    # v1.394.0 review: and a non-member started from inside a
                    # member's run steps out of THAT member's hold.
                    # v1.395.0: on the account the TEAM ROUTER chose, when it chose
                    # (review: or ``named_account`` for a member named directly).
                    if _hp is not None:
                        _scope = _model_team.member_scope(self.platform, None)
                    elif _member_pin_set:
                        _scope = _model_team.member_scope(
                            self.platform, record, str(provider or ""), pin=_member_pin
                        )
                    else:
                        _scope = _model_team.member_scope(self.platform, record, str(provider or ""))
                    with _helpers.pinned(_hp), _scope:
                        child_task = asyncio.ensure_future(
                            AgentRuntime(self.platform).run(
                                child_session,
                                _helpers.helper_definition(
                                    _hp, definition, custom=_model_team.helper_is_custom(record)
                                ),
                                parent_id=ctx.agent_run_id,
                            )
                        )
                    _team.bind_task(child_session.id, child_task)
                    run = await child_task
            except asyncio.CancelledError:
                if await user_stopped_child(orch, child_session.id):
                    # The USER stopped this child (v1.309.0), not the caller:
                    # settle it CANCELLED, close the edge, tell the caller.
                    await orch._finalize_cancelled(child_session)
                    await publish_delegation_completed(
                        self.platform,
                        session_id=ctx.session_id,
                        parent_run_id=ctx.agent_run_id,
                        child_run_id=None,
                        child_session_id=child_session.id,
                        target=agent_name,
                        ok=False,
                        result="stopped by the user",
                    )
                    return ToolResult(
                        ok=False,
                        output="",
                        error=stopped_by_user_error(agent_name),
                        data={
                            "agent": agent_name,
                            "dynamic": self.registry.get(agent_name) is not None,
                            "child_session_id": child_session.id,
                            "state": "cancelled",
                        },
                    )
                # A deadline is not the user (v1.288.0) — as in `delegate`.
                by_deadline = tool_deadline_expired()
                await orch._finalize_cancelled(
                    child_session,
                    reason=TIME_LIMIT_REASON if by_deadline else None,
                )
                try:
                    await publish_delegation_completed(
                        self.platform,
                        session_id=ctx.session_id,
                        parent_run_id=ctx.agent_run_id,
                        child_run_id=None,
                        child_session_id=child_session.id,
                        target=agent_name,
                        ok=False,
                        result=TIME_LIMIT_REASON if by_deadline else "cancelled",
                    )
                except Exception:  # noqa: BLE001 - never block the unwind
                    pass
                raise  # the caller's cancellation keeps propagating
            except Exception as exc:  # noqa: BLE001
                from ..providers.failure_words import refusal_reason

                # v1.378.0 review: a REFUSED child tells its parent so in the
                # run's words (MIRROR NOTE, lock-step: delegate_tool).
                refused = refusal_reason(exc)
                error = (
                    f"spawned agent '{agent_name}' could not finish: {refused}"
                    if refused
                    else f"spawned agent '{agent_name}' crashed: "
                    f"{type(exc).__name__}: {exc}"
                )
                await orch._finalize_failed(child_session, exc)
                # v1.395.0 review: a run that RAISED writes no row on the
                # member's record (``delegate``'s rule: the provider, the
                # account, a policy — never the member failing at its role).
                await publish_delegation_completed(
                    self.platform,
                    session_id=ctx.session_id,
                    parent_run_id=ctx.agent_run_id,
                    child_run_id=None,  # the runtime raised before minting one
                    child_session_id=child_session.id,
                    target=agent_name,
                    ok=False,
                    result=error,
                )
                return ToolResult(
                    ok=False,
                    output="",
                    error=error,
                    data={
                        "agent": agent_name,
                        "dynamic": self.registry.get(agent_name) is not None,
                        "child_session_id": child_session.id,
                        "state": "failed",
                    },
                )

            # Reflect the run's outcome onto the child session and persist it —
            # the ONE child settle `delegate` uses too (v1.307.0). A child the
            # USER stopped (v1.309.0) settles CANCELLED and the caller is told.
            stopped = await orch.settle_child(child_session, run)
            _team.consume_stop(child_session.id)
            if stopped:
                await publish_delegation_completed(
                    self.platform,
                    session_id=ctx.session_id,
                    parent_run_id=ctx.agent_run_id,
                    child_run_id=run.id,
                    child_session_id=child_session.id,
                    target=agent_name,
                    ok=False,
                    result="stopped by the user",
                )
                return ToolResult(
                    ok=False,
                    output="",
                    error=stopped_by_user_error(agent_name),
                    data={
                        "agent": agent_name,
                        "dynamic": self.registry.get(agent_name) is not None,
                        "child_run_id": run.id,
                        "child_session_id": child_session.id,
                        "state": "cancelled",
                    },
                )

            # Close the learning loop for the child: spawned work teaches the system
            # too (evaluate -> record outcome -> reflect). Best-effort so a learning
            # failure never breaks the spawn.
            try:
                orch._post_run_learning(child_session)
            except Exception:  # noqa: BLE001
                pass
            await orch._post_run_allowance(child_session)

            ok = run.state is AgentState.COMPLETED
            # v1.395.0: the piece's row in the member's record for its role
            # (review: on the model it ran on, and the account it was pinned to).
            await _model_team.note_piece(
                self.platform, record=record, role=_record_role, session_id=child_session.id,
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
                target=agent_name,
                ok=ok,
                result=run.result,
            )
            return ToolResult(
                ok=ok,
                # The child's address rides in the OUTPUT, not only in `data` —
                # the runtime shows the model `output` and nothing else, so a
                # handle in `data` is a handle the caller can never use.
                output=_with_handle(
                    run.result,
                    delegation_handle(agent_name, run.id, child_session.id),
                ),
                error=None if ok else (run.result or "subagent failed"),
                data={
                    "agent": agent_name,
                    "dynamic": self.registry.get(agent_name) is not None,
                    "child_run_id": run.id,
                    "child_session_id": child_session.id,
                    "state": run.state.value,
                    "workspace": child_session.workspace_path,
                    # v1.395.0: the role asked for, and why this member took it.
                    **({"role": _team_role, "reason": _route.reason} if _route is not None else {}),
                },
            )


def agent_management_tools(
    platform, registry: "DynamicAgentRegistry"
) -> list[Tool]:
    """Build the agent-management tool set bound to ``platform`` + ``registry``."""
    return [
        CreateAgentTool(platform, registry),
        ListAgentsTool(platform, registry),
        SpawnAgentTool(platform, registry),
    ]
