"""A sub-agent is not a wedged tool call (v1.288.0, review agents-02).

The per-call tool deadline (``config.tool_call_timeout_s``, v1.228.0) wrapped
``delegate`` and ``spawn_agent`` too — tools whose ``execute`` awaits a WHOLE
child agent run. A worker still doing real work at the deadline was killed
part-way, its session recorded "Session cancelled by the user." although nobody
pressed anything, and the supervisor was told only "did not finish within N s".

Driven through the REAL platform, Orchestrator, runtime, registry and tools;
only the model is scripted. The deadline is shrunk and the child made to work
work well past it — the assertions are about outcome and wording,
never about how long anything took.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from iron_jarvis.agents.agent_tools import SpawnAgentTool
from iron_jarvis.agents.delegate_tool import TIME_LIMIT_REASON, DelegateTool
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.permissions import SAFE_HEADLESS_TOOLS

DEADLINE_S = 1  # the Setting is whole seconds
#: The child works this many deadlines long — far past the limit.
CHILD_WORK_S = DEADLINE_S * 2.5


def _scripted(tool_name: str, tool_args: dict, child_work):
    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            if "As the Supervisor" in system:
                seen = [m for m in messages if getattr(m, "role", "") == "tool"]
                if seen:
                    return LLMResponse(
                        text=f"supervisor saw: {seen[-1].content}", finish_reason="stop"
                    )
                return LLMResponse(
                    tool_calls=[ToolCall(id="c1", name=tool_name, arguments=tool_args)],
                    finish_reason="tool_use",
                )
            await child_work()
            return LLMResponse(text="renamed all 26 files", finish_reason="stop")

    return Scripted


def _platform(tmp_path, adapter_cls):
    p = build_platform(str(tmp_path))
    p.providers.register("mock", lambda model=None: adapter_cls())
    p.config.tool_call_timeout_s = DEADLINE_S
    return p


def _children(p, parent_id):
    with session_scope(p.engine) as db:
        return [
            (k.status, k.summary or "")
            for k in db.exec(select(Session).where(Session.id != parent_id))
        ]


async def _run_supervisor(orch, tool_name):
    s = await orch.create_session(
        "rename every client file", agent_type=AgentType.SUPERVISOR,
        provider="mock", model="mock-1", allow_tools=[tool_name],
    )
    await orch.run_session(s.id)
    return s.id


def _long_child():
    return asyncio.sleep(CHILD_WORK_S)


def test_delegated_child_outlives_the_tool_deadline_and_completes(tmp_path):
    p = _platform(tmp_path, _scripted(
        "delegate", {"agent_type": "builder", "task": "rename the 26 client files"},
        _long_child,
    ))
    orch = Orchestrator(p)
    sid = asyncio.run(_run_supervisor(orch, "delegate"))

    kids = _children(p, sid)
    assert [k[0] for k in kids] == [SessionStatus.COMPLETED], kids
    assert kids[0][1] == "renamed all 26 files"
    summary = orch.get_session(sid).summary or ""
    assert "renamed all 26 files" in summary, summary
    assert "did not finish within" not in summary, summary


def test_spawned_child_outlives_the_tool_deadline_and_completes(tmp_path):
    p = _platform(tmp_path, _scripted(
        "spawn_agent", {"agent": "builder", "task": "rename the 26 client files"},
        _long_child,
    ))
    orch = Orchestrator(p)
    sid = asyncio.run(_run_supervisor(orch, "spawn_agent"))

    kids = _children(p, sid)
    assert [k[0] for k in kids] == [SessionStatus.COMPLETED], kids
    summary = orch.get_session(sid).summary or ""
    assert "renamed all 26 files" in summary, summary
    assert "did not finish within" not in summary, summary


def test_the_exemption_names_exactly_the_sub_agent_tools(tmp_path):
    """ONE declaration, no drift: the registered tools that step outside the
    deadline are exactly the ones that run a whole sub-agent (the same pair
    the headless allowlist names) — never a shell/MCP/HTTP tool."""
    p = build_platform(str(tmp_path))
    exempt = {
        name for name in p.registry.names()
        if getattr(p.registry.get(name), "deadline_exempt", False)
    }
    assert exempt == set(SAFE_HEADLESS_TOOLS) == {"delegate", "spawn_agent"}


def test_a_real_parent_cancel_still_cancels_the_child_as_the_user(tmp_path):
    started = asyncio.Event()

    async def _forever():
        started.set()
        await asyncio.Event().wait()

    p = _platform(tmp_path, _scripted(
        "delegate", {"agent_type": "builder", "task": "rename the 26 client files"},
        _forever,
    ))
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session(
            "rename every client file", agent_type=AgentType.SUPERVISOR,
            provider="mock", model="mock-1", allow_tools=["delegate"],
        )
        task = asyncio.create_task(orch.run_session(s.id))
        orch.register_running(s.id, task)
        await started.wait()  # the child is genuinely mid-run
        orch.cancel_session(s.id)
        try:
            await task
        except asyncio.CancelledError:
            pass
        return s.id

    sid = asyncio.run(go())
    kids = _children(p, sid)
    assert [k[0] for k in kids] == [SessionStatus.CANCELLED], kids
    assert "cancelled by the user" in kids[0][1], kids
    assert TIME_LIMIT_REASON not in kids[0][1], kids


@pytest.mark.parametrize(
    "tool_cls, tool_name, tool_args",
    [
        (DelegateTool, "delegate",
         {"agent_type": "builder", "task": "rename the 26 client files"}),
        (SpawnAgentTool, "spawn_agent",
         {"agent": "builder", "task": "rename the 26 client files"}),
    ],
)
def test_if_a_deadline_ever_wraps_a_sub_agent_again_it_is_not_blamed_on_the_user(
    tmp_path, monkeypatch, tool_cls, tool_name, tool_args
):
    """The belt: with the exemption lifted (a future regression), the child is
    still stopped — but its record names the time limit, not the user."""
    monkeypatch.setattr(tool_cls, "deadline_exempt", False)
    p = _platform(tmp_path, _scripted(tool_name, tool_args, _long_child))
    seen: list[dict] = []
    p.event_bus.add_handler(lambda e: seen.append(e.to_dict()))
    orch = Orchestrator(p)
    sid = asyncio.run(_run_supervisor(orch, tool_name))

    kids = _children(p, sid)
    assert [k[0] for k in kids] == [SessionStatus.CANCELLED], kids
    assert kids[0][1].startswith(TIME_LIMIT_REASON), kids
    assert "cancelled by the user" not in kids[0][1], kids
    assert "did not finish within" in (orch.get_session(sid).summary or "")
    # The timeline reads the EVENT, not the child row (review): it must not
    # say "cancelled" for a time limit either.
    done = [e["payload"] for e in seen if e["type"] == "delegation.completed"]
    assert done and done[-1]["ok"] is False, done
    assert done[-1]["result"].startswith(TIME_LIMIT_REASON[:40]), done
