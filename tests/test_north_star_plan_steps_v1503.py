"""North-star plan 3.3 (v1.503.0): a planned run with failed or unattempted
steps is not Completed.

``agents/decompose.run_decomposed`` returned TEXT only, so the runtime ended a
planned (decomposed) run COMPLETED whatever its steps did: that a step failed
its check or was never attempted lived only as prose appended to the answer.

Now ``run_decomposed`` hands back its step results too (``results_out``) and:

* a step NEVER ATTEMPTED (the run's budget ran out) ends the run FAILED —
  exactly how the flat loop ends at its step limit;
* every attempted step failing ends it FAILED (nothing the plan set out to
  do was done);
* some steps done and some failed: the run COMPLETED, and its verdict is
  ``completed_with_failures`` ("Part failed"), read off the run's kept plan
  (``RunPlan``) — a fact on the ledger, so the receipt agrees.

The v1.132.0 end-to-end tests (``tests/test_decompose_v1132.py``) keep their
COMPLETED status and now also pin the "part failed" verdict.
"""

from __future__ import annotations

from iron_jarvis.agents import decompose
from iron_jarvis.agents.decompose import StepResult
from iron_jarvis.agents.outcome import OUTCOME_WITH_FAILURES, session_result
from iron_jarvis.core.models import AgentType, SessionStatus
from tests.test_decompose_v1132 import _LONG_TASK, _PLAN_JSON, _TextOnly


def _results(*shapes):
    out = []
    for i, shape in enumerate(shapes):
        if shape == "ok":
            out.append(StepResult(index=i, goal=f"g{i}", ok=True, output="done", verified="deterministic"))
        elif shape == "failed":
            out.append(StepResult(index=i, goal=f"g{i}", ok=False, output="no", reason="file missing",
                                  verified="deterministic"))
        else:
            out.append(StepResult(index=i, goal=f"g{i}", ok=False, output="",
                                  reason="not attempted — the session's 4-step budget was spent by step 1",
                                  verified="none", attempted=False))
    return out


def _fake_execute(monkeypatch, results):
    async def fake(runtime, run, session, agent_def, plan, *, on_step=None, **kw):
        if on_step is not None:
            await on_step(results)
        return results

    monkeypatch.setattr(decompose, "execute_plan", fake)


async def _run(platform, orchestrator, name):
    inner = _TextOnly([_PLAN_JSON, "Both steps are done.", "spare", "spare"], provider=name)
    platform.providers.register(name, lambda model=None: inner)
    return await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider=name)


async def test_a_plan_with_a_step_never_attempted_ends_failed(platform, orchestrator, monkeypatch):
    _fake_execute(monkeypatch, _results("ok", "unattempted"))
    session = await _run(platform, orchestrator, "local-budget")
    assert session.status is SessionStatus.FAILED
    final = orchestrator.transcript(session.id)["runs"][0]["result"]
    assert "NEVER ATTEMPTED" in final  # the answer still says which step


async def test_a_plan_whose_every_step_failed_ends_failed(platform, orchestrator, monkeypatch):
    _fake_execute(monkeypatch, _results("failed", "failed"))
    session = await _run(platform, orchestrator, "local-allfail")
    assert session.status is SessionStatus.FAILED


async def test_a_plan_with_some_steps_failed_is_part_failed(platform, orchestrator, monkeypatch):
    _fake_execute(monkeypatch, _results("ok", "failed"))
    session = await _run(platform, orchestrator, "local-part")
    assert session.status is SessionStatus.COMPLETED
    row = orchestrator.get_session(session.id)
    assert row.outcome == OUTCOME_WITH_FAILURES
    result = session_result(platform.engine, session.id)
    assert result["plan_steps_failed"] == 1 and result["outcome"] == OUTCOME_WITH_FAILURES


async def test_a_plan_whose_steps_all_passed_is_done(platform, orchestrator, monkeypatch):
    _fake_execute(monkeypatch, _results("ok", "ok"))
    session = await _run(platform, orchestrator, "local-good")
    assert session.status is SessionStatus.COMPLETED
    assert orchestrator.get_session(session.id).outcome == "completed"


async def test_run_decomposed_hands_back_its_step_results(platform, orchestrator, monkeypatch):
    from iron_jarvis.agents.types import get_agent_definition
    from tests.test_decompose_v1132 import _direct_setup

    results = _results("ok", "failed")
    _fake_execute(monkeypatch, results)
    inner = _TextOnly([_PLAN_JSON, "assembled"], provider="local-direct")
    runtime, run, session = await _direct_setup(platform, orchestrator, inner)
    got: list = []
    text = await decompose.run_decomposed(
        runtime, run, session, get_agent_definition(AgentType.BUILDER), system_prompt="", tool_specs=[], session_allow=set(),
        sink=None, results_out=got,
    )
    assert isinstance(text, str) and "FAILED verification" in text
    assert got == results
