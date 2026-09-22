"""A Team worker gets the tools the user already approved for the job (v1.288.0).

`delegate` / `spawn_agent` created the child session with the parent's folder
and project but never its GRANTS: a Supervisor job started with the shell
pre-approved handed the work to a child with ``allow_tools=[]``. The child has
no origin, so it cannot pause to ask, and every ask-tier call died on the
headless resolver with "grant it with allow_tools" — which the user had done.

Driven through the real platform, orchestrator, `delegate`/`spawn_agent`,
runtime, registry and PermissionEngine; only the LLM is scripted. The
widening is exactly what the user granted the parent, never more:

* a base ``deny`` on the tool still refuses the child (the safety floor);
* a tool the parent was NOT granted is still refused;
* ``yolo`` lands on the child as ``approve_for_me`` (never yolo);
* an ``always_ask`` parent hands down NO grant list — the child cannot ask, so
  the list must not silently pre-approve what the parent would have asked;
* the ORIGIN is not forwarded (card routing for a child's ask is unresolved).
"""

from __future__ import annotations

from sqlmodel import select

from iron_jarvis.agents.agent_tools import SpawnAgentTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

_CMD = "echo hello-from-worker"


class _Scripted(MockLLMAdapter):
    """Supervisor: delegate one shell task, then finish. Worker: call the
    shell once, then report what it saw."""

    async def complete(self, *, system, messages, tools, **kw):
        tool_msgs = [m for m in messages if m.role == "tool"]
        if "As the Supervisor" in system:
            if tool_msgs:
                return LLMResponse(
                    text="team done: " + tool_msgs[-1].content[:200],
                    finish_reason="stop",
                )
            return LLMResponse(
                tool_calls=[ToolCall(id="d1", name="delegate", arguments={
                    "agent_type": "builder",
                    "task": f"Use the shell tool to run: {_CMD}",
                })],
                finish_reason="tool_use",
            )
        if tool_msgs:
            return LLMResponse(
                text="worker saw: " + tool_msgs[-1].content[:200],
                finish_reason="stop",
            )
        return LLMResponse(
            tool_calls=[ToolCall(id="s1", name="shell", arguments={"command": _CMD})],
            finish_reason="tool_use",
        )


def _platform(tmp_path, **perm_overrides):
    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: _Scripted())
    if perm_overrides:
        p.permissions = PermissionEngine({**p.config.permissions, **perm_overrides})
    return p


def _child(p, parent_id: str):
    with session_scope(p.engine) as db:
        kids = list(db.exec(select(Session).where(Session.id != parent_id)))
        assert len(kids) == 1, [k.id for k in kids]
        kid = kids[0]
        rows = list(
            db.exec(select(ToolInvocation).where(ToolInvocation.session_id == kid.id))
        )
        shell = [(r.ok, r.output or "") for r in rows if r.tool == "shell"]
        return kid, shell


async def _run_team(p, **create_kw):
    orch = Orchestrator(p)
    s = await orch.create_session(
        "have a worker run echo in the shell",
        agent_type=AgentType.SUPERVISOR,
        provider="mock",
        model="mock-1",
        **create_kw,
    )
    await orch.run_session(s.id)
    return s.id


async def _spawn_from(p, tmp_path, **create_kw):
    orch = Orchestrator(p)
    parent = await orch.create_session(
        "run the job", AgentType.AUTOMATION, provider="mock", model="mock-1",
        **create_kw,
    )
    res = await SpawnAgentTool(p, p.agents_registry).execute(
        {"agent": "builder", "task": f"Use the shell tool to run: {_CMD}"},
        ToolContext(
            workspace=tmp_path,
            session_id=parent.id,
            agent_run_id="auto-run",
            config=p.config,
            event_bus=p.event_bus,
            engine=p.engine,
        ),
    )
    assert res.ok is True, res.error
    return parent.id


# --------------------------------------------------------------------------- #
# the symptom, through both doors
# --------------------------------------------------------------------------- #
async def test_delegated_worker_can_use_the_shell_the_user_granted_the_job(tmp_path):
    p = _platform(tmp_path)
    sid = await _run_team(p, allow_tools=["delegate", "shell"], origin="job:review")

    kid, shell = _child(p, sid)
    assert "shell" in kid.allow_tools_json
    assert shell and all(ok for ok, _ in shell), shell
    assert "hello-from-worker" in shell[0][1]
    # NARROW: the right to ASK is not forwarded — only the grant.
    assert kid.origin is None


async def test_the_delegate_door_forwards_the_capped_posture_too(tmp_path):
    """Review: dropping ``approval_mode`` at the delegate door alone survived
    every pin — the parent's default posture and the child's default are both
    ''. A yolo parent tells them apart: the child must read approve_for_me."""
    p = _platform(tmp_path)
    sid = await _run_team(
        p, allow_tools=["delegate", "shell"], origin="job:review", approval_mode="yolo"
    )
    kid, _ = _child(p, sid)
    assert kid.approval_mode == "approve_for_me", kid.approval_mode


async def test_spawned_worker_inherits_the_grant_and_a_yolo_posture_is_capped(
    tmp_path,
):
    p = _platform(tmp_path)
    sid = await _spawn_from(p, tmp_path, allow_tools=["shell"], approval_mode="yolo")

    kid, shell = _child(p, sid)
    assert kid.approval_mode == "approve_for_me"  # never yolo, at any door
    assert kid.origin is None
    assert shell and all(ok for ok, _ in shell), shell


# --------------------------------------------------------------------------- #
# exactly what the user granted, never more
# --------------------------------------------------------------------------- #
async def test_a_base_deny_still_refuses_the_worker_despite_the_grant(tmp_path):
    p = _platform(tmp_path, shell="deny")
    sid = await _run_team(p, allow_tools=["delegate", "shell"], origin="job:review")

    kid, shell = _child(p, sid)
    assert "shell" in kid.allow_tools_json  # the grant DID ride down…
    assert shell and not any(ok for ok, _ in shell), shell  # …and did not lift it
    assert "denied by policy" in shell[0][1]


async def test_a_tool_the_parent_was_not_granted_is_still_refused(tmp_path):
    p = _platform(tmp_path)
    sid = await _run_team(p, allow_tools=["delegate"], origin="job:review")

    kid, shell = _child(p, sid)
    assert "shell" not in kid.allow_tools_json
    assert shell and not any(ok for ok, _ in shell), shell


async def test_an_always_ask_parent_hands_down_no_silent_pre_approval(tmp_path):
    p = _platform(tmp_path)
    sid = await _spawn_from(
        p, tmp_path, allow_tools=["shell"], approval_mode="always_ask"
    )

    kid, shell = _child(p, sid)
    assert kid.approval_mode == "always_ask"
    assert kid.allow_tools_json == "[]"
    assert shell and not any(ok for ok, _ in shell), shell
