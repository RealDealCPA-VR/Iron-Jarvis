"""v1.421.0 (Wave 1, part C review) — AN AGENT RUN ON A MODEL TOO SMALL FOR
THE JOB SAYS SO, AND ITS TOOL DEFINITIONS COUNT AGAINST A KNOWN WINDOW.

D3 made a local model's REAL window known (the fleet probe: Ollama's 4,096, a
mid-size server's 44k-70k). Against honest windows two older weaknesses of the
agent loop's budget (``agents/runtime.perceive_act`` →
``context/agent_window.plan_agent_transcript``) bite:

* every step sends the run's ``tool_specs`` (~15k tokens for a builder's 67
  tools) and the planner priced only the system prompt and the messages, so
  a step it "fitted" overflowed by the tools. Now the tools are priced —
  ONLY at a KNOWN window: counted against the 32k GUESS for an unknown model
  they would over-trim every such run;
* when the planner could not send the newest block (the latest tool results
  the model is about to act on) or had to clip the task, the step went out
  anyway: the model asked for the same results again, step after step, and
  the run ended at its step limit having done nothing, for a reason nobody
  was told. Now it ends FAILED, before the model call, with one plain
  sentence naming the window and the way out. Only at a known window.

Driven through the REAL orchestrator + runtime on a real platform; only the
model is scripted.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

from sqlmodel import select

import iron_jarvis.context.agent_window as AW
from iron_jarvis.agents import decompose
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    CONTEXT_TOO_SMALL_FIX,
    STEP_LIMIT_RESULT,
    context_too_small_result,
)
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, SessionStatus, ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

TASK = "list the files and say what is there"


def _list_call(i: int) -> list[ToolCall]:
    return [ToolCall(id=f"l{i}", name="list_files", arguments={"path": "."})]


def _world(tmp_path, steps, *, window: int | None):
    """A real platform whose model is scripted: ``steps`` per model call — a
    reply text, a list of ToolCalls, or ``"loop"`` (a tool call every time,
    the model that never learns its results arrived). Returns (platform,
    calls, tools_seen, planner_kwargs)."""
    p = build_platform(str(tmp_path / "home"))
    p.config.model_context_windows = {"mock": window} if window else {}
    calls: list[list] = []
    tools_seen: list[list] = []
    planned: list[dict] = []
    script = list(steps)

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            calls.append(list(messages))
            tools_seen.append(list(tools or []))
            move = script[0] if script and script[0] == "loop" else (script.pop(0) if script else "done")
            if move == "loop":
                return LLMResponse(text="I will look again.", tool_calls=_list_call(len(calls)),
                                   finish_reason="tool_use")
            if isinstance(move, list):
                return LLMResponse(tool_calls=move, finish_reason="tool_use")
            return LLMResponse(text=move, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    real = AW.plan_agent_transcript

    def spy(messages, *, window, system_text="", **kw):
        planned.append({"window": window, **kw})
        return real(messages, window=window, system_text=system_text, **kw)

    return p, calls, tools_seen, planned, spy


def _run(p, *, task=TASK, definition=None):
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session(task, provider="mock", model="mock-1")
        return await orch.run_session(s.id, definition=definition)

    return asyncio.run(go())


def _runs(p, session_id: str) -> list[AgentRun]:
    with session_scope(p.engine) as db:
        return list(db.exec(select(AgentRun).where(AgentRun.session_id == session_id)).all())


def _name(event_type) -> str:
    return str(getattr(event_type, "value", event_type))


def _tool_rows(p, session_id: str) -> list[str]:
    with session_scope(p.engine) as db:
        return [r.tool for r in db.exec(select(ToolInvocation).where(ToolInvocation.session_id == session_id)).all()]


# --------------------------------------------------------------------------- #
# the sentence
# --------------------------------------------------------------------------- #


def test_the_sentence_names_the_window_and_the_way_out():
    results = context_too_small_result(4096, task_clipped=False, has_tools=True)
    assert results == (
        "This model's context window (4,096 tokens) is too small for this job: "
        "its latest results do not fit beside its instructions and tools. "
        + CONTEXT_TOO_SMALL_FIX
    )
    assert "OLLAMA_CONTEXT_LENGTH" in CONTEXT_TOO_SMALL_FIX
    task = context_too_small_result(70_000, task_clipped=True, has_tools=False)
    assert task.startswith("This model's context window (70,000 tokens) is too small for this job: "
                           "the task does not fit beside its instructions. ")


# --------------------------------------------------------------------------- #
# (3) a model too small for the job says so instead of looping
# --------------------------------------------------------------------------- #


def test_a_model_too_small_for_its_results_ends_the_run_after_one_step(tmp_path, monkeypatch):
    """4,096 tokens (Ollama's default), a builder's real system prompt and its
    ~15k tokens of tool definitions, and a model that would ask for the same
    listing forever: the first step runs, the second cannot carry the result
    the model must act on — the run ends FAILED there, saying why, never at
    its step limit."""
    p, calls, _tools, _planned, spy = _world(tmp_path, ["loop"], window=4096)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    s = _run(p)
    expected = context_too_small_result(4096, task_clipped=False, has_tools=True)
    assert len(calls) == 1, f"the run made {len(calls)} model calls (it looped)"
    assert s.status is SessionStatus.FAILED
    assert s.summary == expected
    assert s.summary != STEP_LIMIT_RESULT
    runs = _runs(p, s.id)
    assert len(runs) == 1
    assert runs[0].state is AgentState.FAILED and runs[0].steps == 1
    assert runs[0].result == expected
    # The one step really ran its tool; the second never reached the model.
    assert _tool_rows(p, s.id) == ["list_files"]


def test_a_task_that_cannot_fit_ends_the_run_before_any_model_call(tmp_path, monkeypatch):
    p, calls, _tools, _planned, spy = _world(tmp_path, ["loop"], window=4096)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    s = _run(p, task="Reconcile every line of this statement. " * 120)
    assert calls == [], "a step whose task was clipped must never be sent"
    assert s.status is SessionStatus.FAILED
    assert s.summary == context_too_small_result(4096, task_clipped=True, has_tools=True)
    assert _runs(p, s.id)[0].steps == 0


def test_the_ending_is_announced_like_any_failed_run(tmp_path, monkeypatch):
    """``agent.completed`` ok=False with the sentence, and the session's own
    completion — the timeline and the bell read these, never a crash."""
    p, _calls, _tools, _planned, spy = _world(tmp_path, ["loop"], window=4096)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    seen: list[tuple[str, dict]] = []
    real = p.event_bus.publish

    async def tap(event_type, payload=None, *a, **kw):
        seen.append((_name(event_type), dict(payload or {})))
        return await real(event_type, payload, *a, **kw)

    p.event_bus.publish = tap  # type: ignore[method-assign]
    s = _run(p)
    done = [pl for t, pl in seen if t == _name(EventType.AGENT_COMPLETED)]
    assert done and done[-1]["ok"] is False and done[-1]["result"] == s.summary
    finished = [pl for t, pl in seen if t == _name(EventType.SESSION_COMPLETED)]
    assert finished and finished[-1]["status"] == "failed"


def test_a_guessed_window_never_ends_a_run(tmp_path, monkeypatch):
    """No pin, no probe: the planner plans against the 32k GUESS, clips a
    task larger than it and goes on — exactly as before. A guess is not a
    measurement, so it never ends a run."""
    p, calls, _tools, planned, spy = _world(tmp_path, [_list_call(1), "done"], window=None)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    s = _run(p, task="Summarise this long statement for me. " * 4_000)
    assert planned and all(row["window"] is None for row in planned)
    assert s.status is SessionStatus.COMPLETED, s.summary
    assert len(calls) == 2
    # The planner really had to clip it: what was sent is shorter than the task.
    assert len(calls[0][0].content) < len("Summarise this long statement for me. " * 4_000)


def test_the_decomposed_lane_ends_the_whole_run_too(tmp_path, monkeypatch):
    """A decomposed run's mini-loops run the SAME step body; a too-small step
    ends the whole run — the plan's next step never starts and nothing is
    assembled (a per-step failure would have walked the plan to the end and
    asked the model to write up nothing)."""
    p, calls, _tools, _planned, spy = _world(tmp_path, ["loop"], window=4096)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    monkeypatch.setattr(decompose, "should_decompose", lambda *a, **k: True)

    async def plan(*a, **k):
        return [decompose.PlanStep(goal="list the files"), decompose.PlanStep(goal="say what is there")]

    async def assemble(*a, **k):  # pragma: no cover — must not be reached
        raise AssertionError("a run whose model is too small must not be assembled")

    monkeypatch.setattr(decompose, "plan_task", plan)
    monkeypatch.setattr(decompose, "assemble", assemble)
    started: list[int] = []
    real = p.event_bus.publish

    async def tap(event_type, payload=None, *a, **kw):
        if _name(event_type) == _name(EventType.PLAN_STEP_STARTED):
            started.append(int((payload or {}).get("index", -1)))
        return await real(event_type, payload, *a, **kw)

    p.event_bus.publish = tap  # type: ignore[method-assign]
    s = _run(p)
    assert s.status is SessionStatus.FAILED
    assert s.summary.startswith("This model's context window (4,096 tokens) is too small for this job:")
    assert started == [0], f"the plan went on past the step that could not fit: {started}"
    assert len(calls) <= 1


# --------------------------------------------------------------------------- #
# (2) the tool definitions are priced — only at a known window
# --------------------------------------------------------------------------- #


def test_the_tool_definitions_are_priced_at_a_known_window(tmp_path, monkeypatch):
    p, _calls, tools_seen, planned, spy = _world(tmp_path, [_list_call(1), "done"], window=200_000)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    s = _run(p)
    assert s.status is SessionStatus.COMPLETED, s.summary
    sent = tools_seen[0]
    assert len(sent) > 20, "a builder carries its whole roster"
    wire = json.dumps(sent, ensure_ascii=False, sort_keys=True, default=str)
    assert planned and all(row.get("fixed_tokens") == estimate_tokens(wire) for row in planned)
    assert estimate_tokens(wire) > 5_000  # the cost the planner used to ignore


def test_an_unknown_window_prices_no_tools(tmp_path, monkeypatch):
    p, _calls, tools_seen, planned, spy = _world(tmp_path, [_list_call(1), "done"], window=None)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    s = _run(p)
    assert s.status is SessionStatus.COMPLETED, s.summary
    assert tools_seen[0], "the run had tools to price"
    assert planned and all("fixed_tokens" not in row for row in planned)


def test_a_run_with_no_tools_plans_exactly_as_before(tmp_path, monkeypatch):
    p, _calls, tools_seen, planned, spy = _world(tmp_path, ["done"], window=200_000)
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    bare = dataclasses.replace(get_agent_definition(AgentType.BUILDER), tools=[])
    s = _run(p, task="say hello", definition=bare)
    assert s.status is SessionStatus.COMPLETED, s.summary
    assert tools_seen == [[]], tools_seen
    assert planned and all("fixed_tokens" not in row for row in planned)


def test_known_window_tools_trim_what_an_unpriced_plan_would_have_sent(tmp_path, monkeypatch):
    """The point of pricing them: at a known window whose room the tools eat,
    the step carries FEWER earlier results than the same plan without them
    would — so the request fits the window it was planned for."""
    window = 30_000
    # Each tool step's result is about 2,700 tokens: eight of them fit beside
    # the system prompt alone, not beside ~15k tokens of tool definitions.
    big = "line of the listing\n" * 500
    p, calls, tools_seen, planned, spy = _world(
        tmp_path, [_list_call(i) for i in range(1, 9)] + ["done"], window=window,
    )
    monkeypatch.setattr(AW, "plan_agent_transcript", spy)
    from iron_jarvis.tools import builtins as _b

    async def big_listing(self, args, ctx):
        from iron_jarvis.tools.base import ToolResult

        return ToolResult(ok=True, output=big)

    monkeypatch.setattr(_b.ListFilesTool, "execute", big_listing)
    s = _run(p)
    assert s.status is SessionStatus.COMPLETED, s.summary
    wire = estimate_tokens(json.dumps(tools_seen[-1], ensure_ascii=False, sort_keys=True, default=str))
    for msgs in calls:
        sent = sum(estimate_tokens(m.content or "") + 4 for m in msgs)
        # The system prompt is ~600 tokens; the reply room is 4,096.
        assert sent + wire + 4_096 <= window, (sent, wire)
    # …and the planner really had to work for it.
    last = calls[-1]
    assert sum(1 for m in last if m.role == "tool" and "trimmed" not in (m.content or "")) < 8

