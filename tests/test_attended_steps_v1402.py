"""v1.402.0 ("approve once, then off to the races"), parts 3a/3b: a run a
PERSON started and is watching gets a real step budget, and at that budget it
carries on ONCE by itself before it stops.

The audit (verified): every chat hand-off got ``config.max_agent_steps`` (12),
the background doors' safety ceiling; at it the run ended FAILED "stopped:
reached max steps before completion" with no answer, and the chat's Try again
only put the task text back — the job started over.

Now (``agents/runtime``):

* ``resolve_max_steps`` is THE one place the default is decided: an ATTENDED
  origin (chat / job / project / user) with no budget of its own gets
  ``config.attended_run_steps`` ("Steps for runs you start", default 60,
  12..200, read live); every unattended door keeps ``max_agent_steps``; an
  explicit ``max_steps`` from any caller wins. The decomposed lane reads the
  same number.
* At its limit an attended run with no typed budget CARRIES ON ONCE: the SAME
  run, its whole conversation kept (no step repeated), one note, one more
  budget of the same size, ONE ``autonomy.nudged`` (``step_limit_continue``).
  A second limit stops for real. The run ends with ONE ``agent.completed`` —
  a chat following the session never sees an early "stopped at its step
  limit".
* "Keep going" (``POST /sessions/{id}/continue`` with ``resume``) on a run
  that stopped at its step limit resumes its conversation and tells the model
  the true reason (never "a restart or a crash").

Driven through the REAL ``AgentRuntime`` / ``Orchestrator``; only the model is
scripted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.agents import decompose as D
from iron_jarvis.agents import runtime as R
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import AgentRuntime, resolve_max_steps
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, Session, SessionStatus
from iron_jarvis.core.nudges import STEP_LIMIT_CONTINUE
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import Reversibility, Tool, ToolResult

ATTENDED = ("chat", "job:mission", "job:build-helper", "project:p1", "user")
UNATTENDED = ("schedule:nightly", "goal:g1", "workflow:w", "reflex:r", "comm:telegram",
              "autonomy", "assignment:a1", None, "")


def _cfg(max_agent_steps: int = 12, **extra):
    return SimpleNamespace(max_agent_steps=max_agent_steps, **extra)


# --------------------------------------------------------------------------- #
# 3a: the budget
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("origin", ATTENDED)
def test_an_attended_run_with_no_budget_gets_the_attended_steps(origin):
    s = SimpleNamespace(max_steps=None, origin=origin)
    assert resolve_max_steps(s, _cfg(12, attended_run_steps=60)) == 60
    # The decomposed lane reads the same one number.
    assert D.session_step_budget(_cfg(12, attended_run_steps=60), s) == 60


@pytest.mark.parametrize("origin", UNATTENDED)
def test_an_unattended_run_keeps_max_agent_steps(origin):
    s = SimpleNamespace(max_steps=None, origin=origin)
    assert resolve_max_steps(s, _cfg(12, attended_run_steps=60)) == 12
    assert resolve_max_steps(s, _cfg(7, attended_run_steps=60)) == 7


@pytest.mark.parametrize("origin", ATTENDED + UNATTENDED)
def test_an_explicit_budget_from_any_caller_wins(origin):
    s = SimpleNamespace(max_steps=5, origin=origin)
    assert resolve_max_steps(s, _cfg(12, attended_run_steps=60)) == 5


def test_the_setting_is_read_live_and_kept_in_its_bounds():
    s = SimpleNamespace(max_steps=None, origin="chat")
    cfg = _cfg(12, attended_run_steps=30)
    assert resolve_max_steps(s, cfg) == 30
    cfg.attended_run_steps = 90  # changed in Settings: the next run reads it
    assert resolve_max_steps(s, cfg) == 90
    for raw, want in ((5, 12), (500, 200), ("junk", 60), (True, 60), (None, 60)):
        cfg.attended_run_steps = raw
        assert resolve_max_steps(s, cfg) == want, raw
    # A config with no such field (an older double) reads the default.
    assert resolve_max_steps(s, _cfg(12)) == R.ATTENDED_RUN_STEPS == 60


def test_the_real_config_and_the_settings_row_agree():
    from iron_jarvis.core.config import Config
    from iron_jarvis.settings.schema import SETTINGS

    assert Config.model_fields["attended_run_steps"].default == R.ATTENDED_RUN_STEPS
    row = next(d for d in SETTINGS if d.key == "attended_run_steps")
    assert row.group == "automation" and row.tier == "allow" and row.type == "number"
    row.check(R.ATTENDED_RUN_STEPS_MIN)
    row.check(R.ATTENDED_RUN_STEPS_MAX)
    for bad in (R.ATTENDED_RUN_STEPS_MIN - 1, R.ATTENDED_RUN_STEPS_MAX + 1):
        with pytest.raises(ValueError):
            row.check(bad)


def test_who_carries_on():
    assert R.carries_on_at_step_limit(SimpleNamespace(max_steps=None, origin="chat"))
    assert R.carries_on_at_step_limit(SimpleNamespace(max_steps=None, origin="project:p"))
    # A typed budget is the caller's ceiling (the bench's team members set one).
    assert not R.carries_on_at_step_limit(SimpleNamespace(max_steps=20, origin="chat"))
    for origin in UNATTENDED:
        assert not R.carries_on_at_step_limit(SimpleNamespace(max_steps=None, origin=origin)), origin


# --------------------------------------------------------------------------- #
# 3b: the carry-on, on the real runtime
# --------------------------------------------------------------------------- #


class _CountTool(Tool):
    """Allow-tier (borrows read_file's key); each call says which step it was."""

    name = "count_step"
    permission_key = "read_file"
    description = "test double"
    reversibility = Reversibility.READONLY
    input_schema = {"type": "object", "properties": {"n": {"type": "integer"}}}

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def execute(self, args, ctx):  # noqa: D102
        n = int((args or {}).get("n", -1))
        self.calls.append(n)
        return ToolResult(ok=True, output=f"did step {n}")


class _Worker(MockLLMAdapter):
    """A model that keeps working (one new step per call) and, when told its
    budget ran out, either keeps working too (``finish_after_note=False``) or
    finishes with a summary."""

    provider = "stepfake"
    model = "stepfake-1"

    def __init__(self, finish_after_note: bool) -> None:
        super().__init__([])
        self.finish_after_note = finish_after_note
        self.seen: list[list] = []

    async def complete(self, *, system, messages, tools):  # type: ignore[override]
        self.seen.append(list(messages))
        told = any(
            m.role == "user" and "You used your step budget" in str(m.content or "") for m in messages
        )
        if told and self.finish_after_note:
            return LLMResponse(text="All done: steps finished.", finish_reason="stop")
        n = len(self.seen)
        return LLMResponse(
            tool_calls=[ToolCall(f"c{n}", "count_step", {"n": n})], finish_reason="tool_use",
        )


def _nudges(platform) -> list[dict]:
    got: list[dict] = []
    original = platform.event_bus.publish

    async def spy(type_, payload=None, *a, **kw):
        if type_ == EventType.AUTONOMY_NUDGED:
            got.append({**(payload or {}), "_session": kw.get("session_id")})
        return await original(type_, payload, *a, **kw)

    platform.event_bus.publish = spy  # type: ignore[method-assign]
    return got


def _completions(platform) -> list[dict]:
    got: list[dict] = []
    original = platform.event_bus.publish

    async def spy(type_, payload=None, *a, **kw):
        if type_ == EventType.AGENT_COMPLETED:
            got.append(dict(payload or {}))
        return await original(type_, payload, *a, **kw)

    platform.event_bus.publish = spy  # type: ignore[method-assign]
    return got


async def _run(platform, *, origin, finish_after_note=False, max_steps=None, budget=12):
    platform.config.attended_run_steps = budget
    platform.config.max_agent_steps = 3
    tool = _CountTool()
    platform.registry.register(tool)
    model = _Worker(finish_after_note)
    platform.providers.register("stepfake", lambda: model)
    session = await Orchestrator(platform).create_session(
        "do the long job", AgentType.BUILDER, provider="stepfake",
        allow_tools=[tool.perm_key()], origin=origin, max_steps=max_steps,
    )
    run = await AgentRuntime(platform).run(
        session, AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=[tool.name]),
    )
    return run, model, tool, session


async def test_an_attended_run_at_its_limit_carries_on_once_with_its_conversation(platform):
    nudges = _nudges(platform)
    done = _completions(platform)
    run, model, tool, session = await _run(platform, origin="chat", finish_after_note=True, budget=12)
    # It FINISHED in the carry-on, with its own answer.
    assert run.state is AgentState.COMPLETED
    assert run.result == "All done: steps finished."
    # 12 steps, then the note, then the answer: 13 model calls, 12 tool calls,
    # every step once (no step repeated).
    assert len(model.seen) == 13
    assert tool.calls == list(range(1, 13))
    # The carried-on call saw the WHOLE conversation: the task, every step's
    # result, then the note last.
    last = model.seen[-1]
    results = [m for m in last if m.role == "tool"]
    assert len(results) == 12 and "did step 12" in str(results[-1].content)
    assert last[-1].role == "user"
    assert last[-1].content == R.STEP_LIMIT_CARRY_ON_NOTE.format(steps=12)
    assert sum(1 for m in last if m.role == "user" and "You used your step budget" in str(m.content)) == 1
    # ONE nudge, named; ONE completion (the chat never saw an early stop).
    assert [(n["kind"], n["_session"], n.get("steps")) for n in nudges] == [
        (STEP_LIMIT_CONTINUE, session.id, 12),
    ]
    assert len(done) == 1 and done[0]["ok"] is True


async def test_a_second_limit_stops_for_real(platform):
    nudges = _nudges(platform)
    done = _completions(platform)
    run, model, tool, _ = await _run(platform, origin="job:mission", finish_after_note=False, budget=12)
    assert run.state is AgentState.FAILED
    assert run.result == R.STEP_LIMIT_RESULT
    assert R.stopped_at_step_limit(run.result)
    # Two allotments of 12, never a third; every step once.
    assert len(model.seen) == 24
    assert tool.calls == list(range(1, 25))
    assert len(nudges) == 1
    assert len(done) == 1 and done[0]["ok"] is False


async def test_an_unattended_run_stops_at_max_agent_steps_with_no_carry_on(platform):
    nudges = _nudges(platform)
    run, model, tool, _ = await _run(platform, origin="schedule:nightly", finish_after_note=True)
    assert run.state is AgentState.FAILED and run.result == R.STEP_LIMIT_RESULT
    assert len(model.seen) == 3 and tool.calls == [1, 2, 3]
    assert nudges == []


async def test_a_typed_budget_is_a_ceiling_even_for_an_attended_run(platform):
    nudges = _nudges(platform)
    run, model, tool, _ = await _run(platform, origin="chat", finish_after_note=True, max_steps=4)
    assert run.state is AgentState.FAILED and run.result == R.STEP_LIMIT_RESULT
    assert len(model.seen) == 4
    assert nudges == []


async def test_the_whole_session_follows_one_run_to_its_result(platform):
    """Every surface that follows the SESSION (the chat's hand-off, the run
    page) follows the carry-on: it is the same session and the same run, and
    the session ends once, with the carried-on answer."""
    platform.config.attended_run_steps = 12
    tool = _CountTool()
    platform.registry.register(tool)
    model = _Worker(finish_after_note=True)
    platform.providers.register("stepfake", lambda: model)
    orch = Orchestrator(platform)
    session = await orch.create_session(
        "do the long job", AgentType.BUILDER, provider="stepfake",
        allow_tools=[tool.perm_key()], origin="chat",
    )
    seen_status: list[str] = []
    original = platform.event_bus.publish

    async def spy(type_, payload=None, *a, **kw):
        if type_ == EventType.AGENT_COMPLETED:
            row = orch.get_session(session.id)
            seen_status.append(row.status.value if row else "")
        return await original(type_, payload, *a, **kw)

    platform.event_bus.publish = spy  # type: ignore[method-assign]
    final = await orch.run_session(session.id)
    assert final.status is SessionStatus.COMPLETED
    assert "All done" in (final.summary or "")
    with session_scope(platform.engine) as db:
        runs = list(db.exec(select(AgentRun).where(AgentRun.session_id == session.id)))
    assert len(runs) == 1 and runs[0].steps >= 13
    # One agent.completed for the whole job (the chat finalizes on it).
    assert len(seen_status) == 1


# --------------------------------------------------------------------------- #
# Keep going: a run that stopped for real resumes, told the true reason
# --------------------------------------------------------------------------- #


async def test_keep_going_resumes_a_step_limit_run_and_says_why(platform):
    run, model, tool, session = await _run(platform, origin="chat", finish_after_note=False, budget=12)
    assert run.state is AgentState.FAILED
    orch = Orchestrator(platform)
    # The session row as the orchestrator finalizes a failed run.
    with session_scope(platform.engine) as db:
        row = db.get(Session, session.id)
        row.status = SessionStatus.FAILED
        row.summary = run.result
        db.add(row)
        db.commit()
    nxt = await orch.continue_session(session.id, "Keep going: finish the task from where it stopped.", resume=True)
    opts = json.loads(nxt.options_json or "{}")
    assert opts["resume_run"] == run.id
    assert opts[R.RESUME_REASON_OPTION] == R.RESUME_REASON_STEP_LIMIT
    # The continuation resumes the conversation, with the TRUE reason.
    model2 = _Worker(finish_after_note=True)
    model2.seen = []

    async def answer(*, system, messages, tools):
        model2.seen.append(list(messages))
        return LLMResponse(text="Finished it.", finish_reason="stop")

    model2.complete = answer  # type: ignore[method-assign]
    platform.providers.register("stepfake", lambda: model2)
    run2 = await AgentRuntime(platform).run(
        orch.get_session(nxt.id),
        AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=[tool.name]),
    )
    assert run2.state is AgentState.COMPLETED
    first = model2.seen[0]
    assert sum(1 for m in first if m.role == "tool") == 24  # every earlier step, once
    assert first[-1].content.startswith(R.KEEP_GOING_RESUME_NOTE)
    assert "a restart or a crash" not in first[-1].content
    # A further plain continuation never inherits the reason.
    with session_scope(platform.engine) as db:
        row = db.get(Session, nxt.id)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()
    plain = await orch.continue_session(nxt.id, "and one more thing")
    assert R.RESUME_REASON_OPTION not in json.loads(plain.options_json or "{}")


async def test_a_restart_continue_keeps_the_restart_note(platform):
    """Continue on a run a RESTART cut off is unchanged: the restart note."""
    run, _, _, session = await _run(platform, origin="chat", finish_after_note=False, budget=12)
    from iron_jarvis.core.ids import utcnow

    with session_scope(platform.engine) as db:
        row = db.get(Session, session.id)
        row.status = SessionStatus.FAILED
        row.summary = "Stopped when Iron Jarvis restarted."
        row.interrupted_at = utcnow()
        db.add(row)
        db.commit()
    nxt = await Orchestrator(platform).continue_session(session.id, "Continue", resume=True)
    opts = json.loads(nxt.options_json or "{}")
    assert opts["resume_run"] == run.id
    assert R.RESUME_REASON_OPTION not in opts
