"""North-star plan 3.5 (v1.503.0): a lead gets the CHECKED result.

``delegate`` (and ``spawn_agent``, which shares the child settle) handed the
lead its worker's closing words (``run.result``) with ``ok = COMPLETED``: a
worker whose ask went unanswered or whose change failed read as a clean
success, and a save the ledger never saw was passed on unmarked.

Now the tool result carries the SETTLED summary (the words with the ledger's
note, as the child's own row stores them) and ``outcome`` in its data, and a
``needs_you`` / part-failed worker is not ``ok``: the lead reads why, in
words it can act on.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.agents.agent_tools import SpawnAgentTool
from iron_jarvis.agents.delegate_tool import DelegateTool
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import AgentState, EventRecord, ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

REPORT = "I updated ledger.xlsx and saved report.docx."
DECLINED = "Permission denied: the user declined write_file."


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.registry.register(DelegateTool(p))
    p.permissions = PermissionEngine({**p.config.permissions, "delegate": "allow"})
    return p


def _ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="parent-session", agent_run_id="parent1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


def _worker(platform, shape):
    """The child's run: its ledger is written as ``shape`` says."""

    async def run(self, session, agent_def, parent_id=None, **kw):
        with session_scope(platform.engine) as db:
            if shape in ("partial", "declined"):
                db.add(ToolInvocation(session_id=session.id, agent_run_id="child-run",
                                      tool="write_file", ok=False, reversibility="reversible",
                                      output=DECLINED if shape == "declined" else "disk is full"))
            if shape == "asked":
                db.add(EventRecord(id=new_id("evt"), type=EventType.APPROVAL_RESOLVED,
                                   session_id=session.id,
                                   payload_json=json.dumps({"decision": "timeout"})))
            if shape == "clean":
                db.add(ToolInvocation(session_id=session.id, agent_run_id="child-run",
                                      tool="edit_file", ok=True, reversibility="reversible"))
            db.commit()
        return SimpleNamespace(
            id="child-run", state=AgentState.COMPLETED, result=REPORT, provider=session.provider,
            model=session.model, input_tokens=0, output_tokens=0, cost_usd=0.0, steps=1,
        )

    return run


async def _delegate(platform, tmp_path, monkeypatch, shape):
    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", _worker(platform, shape))
    return await platform.registry.invoke(
        "delegate", {"agent_type": "builder", "task": "update the ledger"},
        _ctx(platform, tmp_path), platform.permissions,
    )


async def test_a_part_failed_worker_is_not_ok_and_says_why(platform, tmp_path, monkeypatch):
    res = await _delegate(platform, tmp_path, monkeypatch, "partial")
    assert res.ok is False
    assert res.data["outcome"] == "completed_with_failures"
    assert res.data["state"] == "completed"  # the RUN completed; the job did not
    # What the lead reads (a failed result's ``error``): the verdict first,
    # then the worker's own report, then its address to follow up.
    assert res.error.startswith("Part failed:"), res.error
    assert REPORT in res.error
    assert f"agent_run_id=child-run" in res.error


async def test_a_worker_whose_ask_went_unanswered_needs_you(platform, tmp_path, monkeypatch):
    res = await _delegate(platform, tmp_path, monkeypatch, "asked")
    assert res.ok is False
    assert res.data["outcome"] == "needs_you"
    assert res.error.startswith("Needs you:"), res.error


async def test_the_lead_gets_the_settled_summary_with_the_ledger_note(platform, tmp_path, monkeypatch):
    res = await _delegate(platform, tmp_path, monkeypatch, "declined")
    from iron_jarvis.core.models import Session

    with session_scope(platform.engine) as db:
        child_summary = db.get(Session, res.data["child_session_id"]).summary
    assert child_summary.startswith(REPORT) and len(child_summary) > len(REPORT), child_summary
    # The lead reads exactly what the child's row says, note included.
    assert child_summary in res.error


async def test_a_clean_worker_is_ok_with_its_report(platform, tmp_path, monkeypatch):
    res = await _delegate(platform, tmp_path, monkeypatch, "clean")
    assert res.ok is True
    assert res.data["outcome"] == "completed"
    assert REPORT in res.output and "agent_run_id=child-run" in res.output


async def test_spawn_agent_shares_the_checked_result(platform, tmp_path, monkeypatch):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import AgentType

    monkeypatch.setattr(runtime_mod.AgentRuntime, "run", _worker(platform, "partial"))
    orch = Orchestrator(platform)
    parent = await orch.create_session("run the job", AgentType.AUTOMATION)
    ctx = ToolContext(workspace=tmp_path, session_id=parent.id, agent_run_id="auto-run",
                      config=platform.config, event_bus=platform.event_bus,
                      engine=platform.engine)
    res = await SpawnAgentTool(platform, platform.agents_registry).execute(
        {"agent": "builder", "task": "update the ledger"}, ctx,
    )
    assert res.ok is False
    assert res.data["outcome"] == "completed_with_failures"
    assert res.error.startswith("Part failed:") and REPORT in res.error
