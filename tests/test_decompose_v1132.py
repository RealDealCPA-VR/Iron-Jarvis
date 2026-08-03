"""v1.132.0 — plan → execute → verify decomposition for short-horizon models.

A local model served through the prompted-tools scaffold (v1.131.0,
``tool_use_mode: "prompted"``) loses the thread over the runtime's long flat
loop. ``agents/decompose.py`` compensates: a plausibly multi-step task is split
into 2–8 small verifiable steps, each run in a fresh bounded mini-loop through
the runtime's extracted ``perceive_act`` seam, gated by a per-step verifier
(deterministic file check first, model judge otherwise), retried once on
failure, and assembled into one HONEST final answer. Native tool-callers,
simple tasks, and the flag-off case keep the flat loop byte-for-byte
unchanged. All offline: scripted fake adapters, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from iron_jarvis.agents import decompose
from iron_jarvis.agents.decompose import (
    MAX_PLAN_STEPS,
    PlanStep,
    execute_plan,
    is_plausibly_multi_step,
    plan_task,
    should_decompose,
    verify_step,
)
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.config import Config, load_config
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, SessionStatus
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ToolCall,
)
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter


class _TextOnly(LLMAdapter):
    """Text-only scripted inner adapter (tool_use False → the router wraps it
    in the prompted scaffold). Records every call."""

    def __init__(self, replies, provider="local-x", model="llama3"):
        self.provider = provider
        self.model = model
        self._replies = list(replies)
        self.calls: list[tuple[str, list[LLMMessage], list]] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        text = self._replies.pop(0)
        return LLMResponse(text=text, usage={"input_tokens": 2, "output_tokens": 3})


class _Native(LLMAdapter):
    """Natively tool-capable scripted adapter (never wrapped). Replies may be
    plain strings (a final answer) or full LLMResponse objects (tool calls)."""

    def __init__(self, replies, provider="native-x", model="m1"):
        self.provider = provider
        self.model = model
        self._replies = list(replies)
        self.calls: list[tuple[str, list[LLMMessage], list]] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": True,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        r = self._replies.pop(0)
        if isinstance(r, LLMResponse):
            return r
        return LLMResponse(text=r, usage={"input_tokens": 2, "output_tokens": 3})


#: A task long enough (> 200 chars) that the engage heuristic reads it as
#: multi-step regardless of clause structure.
_LONG_TASK = (
    "Create a file named hello.txt containing a friendly greeting for the "
    "user, and after that produce a second file named missing.txt holding a "
    "short summary of everything done so far, keeping both files inside the "
    "session workspace so they can be reviewed later."
)

_PLAN_JSON = json.dumps(
    {
        "steps": [
            {
                "goal": "write hello.txt",
                "success_criteria": "workspace contains hello.txt",
                "tools": ["write_file"],
            },
            {
                "goal": "produce missing.txt",
                "success_criteria": "workspace contains missing.txt",
                "tools": ["write_file"],
            },
        ]
    }
)


def _bare_run() -> SimpleNamespace:
    """A minimal run object for DIRECT one-shot tests (no DB round-trip)."""
    return SimpleNamespace(id="run-test", steps=0, input_tokens=0, output_tokens=0)


def _stub_runtime(adapter) -> tuple[SimpleNamespace, EventBus]:
    """A runtime-shaped stub over a REAL ProviderManager + ModelRouter — the
    exact seam the production one-shots travel."""
    manager = ProviderManager()
    manager.register(adapter.provider, lambda model=None: adapter)
    bus = EventBus()
    router = ModelRouter(manager, adapter.provider, bus)
    return SimpleNamespace(p=SimpleNamespace(router=router, event_bus=bus)), bus


def _session_stub(task=_LONG_TASK, provider="local-x") -> SimpleNamespace:
    return SimpleNamespace(id="sess-test", task=task, provider=provider, model=None)


# ------------------------------------------------------------- (a) heuristic --
def test_heuristic_long_task_is_multi_step():
    assert is_plausibly_multi_step(_LONG_TASK)
    assert is_plausibly_multi_step("x" * 201)


def test_heuristic_two_imperative_clauses_is_multi_step():
    assert is_plausibly_multi_step("Read notes.txt then write a summary to out.md")
    assert is_plausibly_multi_step("Create a report and email it to Bob")


def test_heuristic_simple_task_is_not():
    assert not is_plausibly_multi_step("Say hello")
    assert not is_plausibly_multi_step("Summarize notes.txt")
    # "and" joining OBJECTS (not actions) does not count as a second clause.
    assert not is_plausibly_multi_step("Write a file listing milk and eggs")
    assert not is_plausibly_multi_step("")


# ---------------------------------------------------- (b) plan parse + repair --
async def test_plan_parses_and_repairs_with_error_fed_back():
    inner = _TextOnly(["this is definitely not a plan", _PLAN_JSON])
    runtime, _ = _stub_runtime(inner)
    plan = await plan_task(
        runtime, _bare_run(), _session_stub(), get_agent_definition(AgentType.BUILDER)
    )
    assert plan is not None and [s.goal for s in plan] == [
        "write hello.txt",
        "produce missing.txt",
    ]
    assert plan[0].tools == ["write_file"]
    assert len(inner.calls) == 2
    # The repair round replays the failed reply and states the exact error.
    _, repair_msgs, _ = inner.calls[1]
    assert repair_msgs[-2].role == "assistant"
    assert repair_msgs[-2].content == "this is definitely not a plan"
    assert repair_msgs[-1].role == "user"
    assert "no JSON object" in repair_msgs[-1].content
    assert '"steps"' in repair_msgs[-1].content


async def test_plan_fenced_json_and_field_validation_repair():
    fenced = "```json\n" + _PLAN_JSON + "\n```"
    inner = _TextOnly(['{"steps": [{"goal": ""}]}', fenced])
    runtime, _ = _stub_runtime(inner)
    plan = await plan_task(
        runtime, _bare_run(), _session_stub(), get_agent_definition(AgentType.BUILDER)
    )
    assert plan is not None and len(plan) == 2
    assert 'non-empty string "goal"' in inner.calls[1][1][-1].content


async def test_degenerate_plans_signal_no_decomposition():
    # 0 steps and 1 step both mean "no decomposition needed" (→ flat loop).
    for reply in ('{"steps": []}', '{"steps": [{"goal": "only one"}]}'):
        inner = _TextOnly([reply])
        runtime, _ = _stub_runtime(inner)
        plan = await plan_task(
            runtime, _bare_run(), _session_stub(), get_agent_definition(AgentType.BUILDER)
        )
        assert plan is None
        assert len(inner.calls) == 1  # a VALID degenerate plan burns no repair


async def test_unrepairable_plan_and_planner_error_return_none():
    inner = _TextOnly(["garbage", "more garbage"])
    runtime, _ = _stub_runtime(inner)
    assert (
        await plan_task(
            runtime, _bare_run(), _session_stub(), get_agent_definition(AgentType.BUILDER)
        )
        is None
    )
    assert len(inner.calls) == 2  # initial + exactly ONE repair round

    class _Boom(_TextOnly):
        async def complete(self, *, system, messages, tools):
            raise ValueError("permanently broken")

    runtime, _ = _stub_runtime(_Boom([], provider="boom-x"))
    assert (
        await plan_task(
            runtime,
            _bare_run(),
            _session_stub(provider="boom-x"),
            get_agent_definition(AgentType.BUILDER),
        )
        is None
    )


async def test_oversized_plan_clipped_to_max():
    big = json.dumps({"steps": [{"goal": f"g{i}"} for i in range(12)]})
    inner = _TextOnly([big])
    runtime, _ = _stub_runtime(inner)
    plan = await plan_task(
        runtime, _bare_run(), _session_stub(), get_agent_definition(AgentType.BUILDER)
    )
    assert plan is not None and len(plan) == MAX_PLAN_STEPS


# ------------------------------------------------------- (c) engage decision --
def test_should_decompose_conditions(platform):
    platform.providers.register("local-x", lambda model=None: _TextOnly([]))
    platform.providers.register("native-x", lambda model=None: _Native([]))
    # Engaged: flag on (default) + prompted adapter + multi-step task.
    assert should_decompose(platform, _session_stub())
    # Simple task → flat loop.
    assert not should_decompose(platform, _session_stub(task="Say hello"))
    # Natively tool-capable adapter → flat loop.
    assert not should_decompose(platform, _session_stub(provider="native-x"))
    # Mock / unknown provider → flat loop (never engage on the offline stub).
    assert not should_decompose(platform, _session_stub(provider="mock"))
    assert not should_decompose(platform, _session_stub(provider="no-such"))
    # Strict model pin: the router offers tools RAW to a pinned pick (no
    # prompted wrap), so decomposition must not engage either.
    platform.config.strict_model_pin = True
    assert not should_decompose(platform, _session_stub())
    platform.config.strict_model_pin = False
    # Flag off → never engages, regardless of adapter/task.
    platform.config.decompose_local_tasks = False
    assert not should_decompose(platform, _session_stub())


def test_config_flag_defaults_true_and_absent_key_is_clean(tmp_path, monkeypatch):
    # Absent from the model → True (pydantic default).
    cfg = Config(project_root=tmp_path, home=tmp_path / ".ironjarvis")
    assert cfg.decompose_local_tasks is True
    # A persisted config WITHOUT the key loads cleanly to the default; an
    # explicit false is honored.
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    root = tmp_path / "proj"
    home = root / ".ironjarvis"
    home.mkdir(parents=True)
    (home / "config.toml").write_text('default_provider = "mock"\n', encoding="utf-8")
    assert load_config(root).decompose_local_tasks is True
    (home / "config.toml").write_text(
        "decompose_local_tasks = false\n", encoding="utf-8"
    )
    assert load_config(root).decompose_local_tasks is False


# ----------------------------------------------------- (d) step tool subsets --
async def _direct_setup(platform, orchestrator, adapter, task=_LONG_TASK):
    """A real session + AgentRun + AgentRuntime with ``adapter`` registered —
    the direct harness for execute_plan/verify_step tests."""
    platform.providers.register(adapter.provider, lambda model=None: adapter)
    session = await orchestrator.create_session(
        task, AgentType.BUILDER, provider=adapter.provider
    )
    run = AgentRun(
        session_id=session.id,
        agent_type=AgentType.BUILDER,
        provider=session.provider,
        model=session.model,
        state=AgentState.RUNNING,
    )
    runtime = AgentRuntime(platform)
    return runtime, run, session


async def test_step_gets_only_its_hinted_tool_subset(platform, orchestrator):
    adapter = _Native(["did step one", "did step two"])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    full_specs = platform.registry.specs(agent_def.tools)
    plan = [
        PlanStep(goal="hinted subset", tools=["read_file"]),
        PlanStep(goal="bogus hint", tools=["no_such_tool"]),
    ]
    results = await execute_plan(
        runtime, run, session, agent_def, plan,
        system_prompt="sys", tool_specs=full_specs, session_allow=set(), sink=None,
    )
    assert [r.ok for r in results] == [True, True]
    # Step 1 saw ONLY its hinted subset...
    names_step1 = [t["name"] for t in adapter.calls[0][2]]
    assert names_step1 == ["read_file"]
    # ...while an invalid hint (not in the agent's set) falls back to the FULL set.
    names_step2 = {t["name"] for t in adapter.calls[1][2]}
    assert names_step2 == {t["name"] for t in full_specs}


# ------------------------------------------------------------ (e) verify gate --
async def test_verify_gate_deterministic_pass_no_judge_call(platform, orchestrator):
    write = LLMResponse(
        tool_calls=[ToolCall(id="t1", name="write_file", arguments={"path": "out.txt", "content": "hi"})],
        finish_reason="tool_use",
    )
    adapter = _Native([write, "wrote out.txt"])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    plan = [PlanStep(goal="write it", success_criteria="workspace contains out.txt")]
    results = await execute_plan(
        runtime, run, session, agent_def, plan,
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
    )
    assert results[0].ok and not results[0].retried
    assert results[0].verified == "files"
    assert (Path(session.workspace_path) / "out.txt").exists()
    # 2 mini-loop rounds, ZERO judge one-shots — file existence was the gate.
    assert len(adapter.calls) == 2


async def test_verify_fail_retries_with_reason_then_passes(platform, orchestrator):
    write = LLMResponse(
        tool_calls=[ToolCall(id="t1", name="write_file", arguments={"path": "late.txt", "content": "x"})],
        finish_reason="tool_use",
    )
    # Attempt 1 claims done without writing; the retry actually writes.
    adapter = _Native(["done (but wrote nothing)", write, "wrote late.txt"])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    plan = [PlanStep(goal="write late.txt", success_criteria="workspace contains late.txt")]
    results = await execute_plan(
        runtime, run, session, agent_def, plan,
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
    )
    assert results[0].ok and results[0].retried
    # The retry mini-loop opened with the VERIFIER'S reason prepended.
    retry_prompt = adapter.calls[1][1][0].content
    assert retry_prompt.startswith("Your previous attempt failed verification")
    assert "late.txt" in retry_prompt
    done = [e for e in events if e.type == EventType.PLAN_STEP_COMPLETED]
    assert done and done[-1].payload == {"run_id": run.id, "index": 0, "ok": True}


async def test_verify_model_judge_pass_with_one_repair(platform, orchestrator):
    # Criteria naming NO files → the model judge gates, with 1 JSON repair.
    adapter = _Native(
        [
            "Revenue grew 12% year over year.",  # the step's mini-loop answer
            "sounds fine to me",  # judge reply 1: invalid (not JSON)
            '{"pass": true, "reason": "mentions revenue"}',  # judge repair
        ]
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    plan = [PlanStep(goal="summarize", success_criteria="the summary mentions revenue")]
    results = await execute_plan(
        runtime, run, session, agent_def, plan,
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
    )
    assert results[0].ok and results[0].verified == "model"
    assert len(adapter.calls) == 3
    # The judge repair round carried the parse error back.
    assert "invalid" in adapter.calls[2][1][-1].content
    assert '"pass"' in adapter.calls[2][1][-1].content


async def test_verify_judge_false_verdict_fails_step(platform, orchestrator):
    adapter = _Native(
        [
            "attempt one",
            '{"pass": false, "reason": "no revenue figure given"}',
            "attempt two",
            '{"pass": true, "reason": "ok now"}',
        ]
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    plan = [PlanStep(goal="summarize", success_criteria="mentions the revenue figure")]
    results = await execute_plan(
        runtime, run, session, agent_def, plan,
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
    )
    assert results[0].ok and results[0].retried
    # The judge's reason rode into the retry prompt verbatim.
    assert "no revenue figure given" in adapter.calls[2][1][0].content


async def test_unverified_step_surfaced_in_final_answer():
    """VERIFIER: a step that passed only because the judge was unparseable
    ('unverified') must not read like a verified pass — the code-appended note
    names it even when the assemble model omits it."""
    from iron_jarvis.agents.decompose import StepResult, assemble

    inner = _TextOnly(["Both steps completed."], provider="local-uv")
    runtime, _ = _stub_runtime(inner)
    results = [
        StepResult(index=0, goal="summarize revenue", ok=True, output="done", verified="model"),
        StepResult(index=1, goal="draft the email", ok=True, output="done", verified="unverified"),
    ]
    final = await assemble(runtime, _bare_run(), _session_stub(provider="local-uv"), results)
    assert "Both steps completed." in final
    assert "could NOT be independently verified" in final
    assert "- step 2: draft the email" in final
    assert "- step 1" not in final
    # The assemble prompt itself marked the step honestly for the model too.
    prompt = inner.calls[0][1][0].content
    assert "OK — not independently verified" in prompt


def test_criteria_files_extraction_edges():
    """VERIFIER: the deterministic gate's extraction — known extensions only,
    and URL paths never read as workspace files (a phantom file would force a
    false deterministic FAIL and burn the step's one retry)."""
    from iron_jarvis.agents.decompose import criteria_files

    assert criteria_files("produce report.xlsx and summary.docx") == [
        "report.xlsx",
        "summary.docx",
    ]
    assert criteria_files("verify example.com is cited") == []
    assert criteria_files("pi is approximately 3.14") == []
    assert criteria_files("references v1.132 and section 3.14159") == []
    # URLs are not workspace files — but a real target name alongside one is.
    assert criteria_files(
        "download https://example.com/data.csv and save it as data.csv"
    ) == ["data.csv"]
    assert criteria_files("save the page from www.site.org/index.html") == []
    # Relative paths inside the workspace still count.
    assert criteria_files("write out/report.xlsx with totals") == ["out/report.xlsx"]


async def test_verify_no_criteria_passes_without_any_call(platform, orchestrator):
    adapter = _Native([])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    ok, reason, method = await verify_step(
        runtime, run, session, Path(session.workspace_path),
        PlanStep(goal="anything"), "output",
    )
    assert ok and method == "none"
    assert adapter.calls == []


# ------------------------------------- (f) end-to-end: honest double-fail ----
async def test_decomposed_run_end_to_end_double_fail_surfaced(
    platform, orchestrator
):
    """The FULL decomposed path on a text-only (prompted-wrapped) provider:
    plan → step 1 succeeds via a real write_file → step 2 fails verification
    twice (retry included) → assemble; the final answer mentions the failure
    HONESTLY and the plan.* events narrate in order."""
    inner = _TextOnly(
        [
            _PLAN_JSON,  # 1: the plan one-shot
            # 2: step 1, round 1 — a fenced write_file call
            "```tool_call\n"
            '{"name": "write_file", "arguments": {"path": "hello.txt",'
            ' "content": "Hello there!"}}\n'
            "```",
            "Wrote hello.txt with the greeting.",  # 3: step 1, round 2 (final)
            "I could not create missing.txt.",  # 4: step 2, attempt 1
            "Still unable to create missing.txt.",  # 5: step 2, attempt 2 (retry)
            # 6: the assemble one-shot
            "hello.txt was written; missing.txt could not be produced.",
        ],
        provider="local-e2e",
    )
    platform.providers.register("local-e2e", lambda model=None: inner)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-e2e")
    assert session.status is SessionStatus.COMPLETED
    assert (Path(session.workspace_path) / "hello.txt").exists()
    assert not (Path(session.workspace_path) / "missing.txt").exists()

    transcript = orchestrator.transcript(session.id)
    final = transcript["runs"][0]["result"]
    # HONESTY: the failed step is surfaced — the deterministic footer names it.
    assert "FAILED" in final
    assert "produce missing.txt" in final
    assert "hello.txt was written" in final

    # Every scripted reply was consumed in order; the text-only inner never saw
    # raw tool specs (the prompted contract carried them).
    assert len(inner.calls) == 6
    assert all(tools == [] for _, _, tools in inner.calls)
    # The retry prompt carried the verifier's reason (the missing file).
    retry_prompt = inner.calls[4][1][0].content
    assert "failed verification" in retry_prompt and "missing.txt" in retry_prompt

    # plan.* events, in order, all tagged with the session.
    plan_events = [e for e in events if e.type.startswith("plan.")]
    assert [e.type for e in plan_events] == [
        EventType.PLAN_CREATED,
        EventType.PLAN_STEP_STARTED,
        EventType.PLAN_STEP_COMPLETED,
        EventType.PLAN_STEP_STARTED,
        EventType.PLAN_STEP_COMPLETED,
    ]
    assert all(e.session_id == session.id for e in plan_events)
    assert plan_events[0].payload["steps"] == ["write hello.txt", "produce missing.txt"]
    assert plan_events[1].payload["index"] == 0
    assert plan_events[2].payload["ok"] is True
    assert plan_events[3].payload["goal"] == "produce missing.txt"
    assert plan_events[4].payload == {
        "run_id": transcript["runs"][0]["id"],
        "index": 1,
        "ok": False,
    }
    # The run record aggregates the mini-loop rounds AND completes normally.
    assert transcript["runs"][0]["steps"] == 4  # 2 + 1 + 1 mini-loop rounds
    assert any(t["tool"] == "write_file" and t["ok"] for t in transcript["tools"])


# --------------------------------- (g) degenerate plan → flat-loop fallback --
async def test_degenerate_plan_falls_back_to_flat_loop(platform, orchestrator):
    inner = _TextOnly(['{"steps": []}', "Flat answer."], provider="local-flat")
    platform.providers.register("local-flat", lambda model=None: inner)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-flat")
    assert session.status is SessionStatus.COMPLETED
    assert orchestrator.transcript(session.id)["runs"][0]["result"] == "Flat answer."
    # The planner WAS consulted (call 1 = the plan contract)...
    assert "task planner" in inner.calls[0][0]
    # ...but declined, so no plan.* event was ever published.
    assert not [e for e in events if e.type.startswith("plan.")]
    assert len(inner.calls) == 2


# ------------------------------------------- (h) flat path stays untouched ---
def _spy_planner(monkeypatch):
    calls: list = []

    async def _spy(*args, **kwargs):
        calls.append(args)
        return None

    monkeypatch.setattr(decompose, "plan_task", _spy)
    return calls


async def test_simple_task_never_calls_planner(platform, orchestrator, monkeypatch):
    calls = _spy_planner(monkeypatch)
    inner = _TextOnly(["Hello!"], provider="local-simple")
    platform.providers.register("local-simple", lambda model=None: inner)
    session = await orchestrator.run("Say hello", AgentType.BUILDER, provider="local-simple")
    assert session.status is SessionStatus.COMPLETED
    assert orchestrator.transcript(session.id)["runs"][0]["result"] == "Hello!"
    assert calls == []  # heuristic said simple → planner never invoked
    assert len(inner.calls) == 1


async def test_native_adapter_never_calls_planner(platform, orchestrator, monkeypatch):
    calls = _spy_planner(monkeypatch)
    adapter = _Native(["All done."], provider="native-e2e")
    platform.providers.register("native-e2e", lambda model=None: adapter)
    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="native-e2e")
    assert session.status is SessionStatus.COMPLETED
    assert calls == []  # native tool_use → flat loop even for a long task
    assert len(adapter.calls) == 1


async def test_middle_step_double_fail_rest_runs_and_lying_assemble_corrected(
    platform, orchestrator
):
    """VERIFIER e2e (different plan shape): 3 steps, the MIDDLE one fails
    verification twice — the remaining step still runs, and even a LYING
    assemble reply ("all steps completed") gets the deterministic failure
    footer appended, naming EXACTLY the one failed step."""
    plan_json = json.dumps(
        {
            "steps": [
                {"goal": "write a.txt", "success_criteria": "workspace contains a.txt", "tools": ["write_file"]},
                {"goal": "produce b.txt", "success_criteria": "workspace contains b.txt"},
                {"goal": "write c.txt", "success_criteria": "workspace contains c.txt", "tools": ["write_file"]},
            ]
        }
    )
    inner = _TextOnly(
        [
            plan_json,  # 1: plan
            # 2: step 1, round 1 — fenced write_file call
            "```tool_call\n"
            '{"name": "write_file", "arguments": {"path": "a.txt", "content": "A"}}\n'
            "```",
            "Wrote a.txt.",  # 3: step 1, round 2 (final)
            "Could not create b.txt.",  # 4: step 2, attempt 1
            "Still no b.txt.",  # 5: step 2, attempt 2 (retry)
            # 6: step 3, round 1 — fenced write_file call
            "```tool_call\n"
            '{"name": "write_file", "arguments": {"path": "c.txt", "content": "C"}}\n'
            "```",
            "Wrote c.txt.",  # 7: step 3, round 2 (final)
            "All three steps completed successfully.",  # 8: assemble LIES
        ],
        provider="local-mid",
    )
    platform.providers.register("local-mid", lambda model=None: inner)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-mid")
    assert session.status is SessionStatus.COMPLETED
    ws = Path(session.workspace_path)
    assert (ws / "a.txt").exists() and (ws / "c.txt").exists()
    assert not (ws / "b.txt").exists()
    assert len(inner.calls) == 8  # step 3 ran despite step 2's double failure

    final = orchestrator.transcript(session.id)["runs"][0]["result"]
    # The model's lie is preserved verbatim BUT the code-appended footer
    # contradicts it honestly, listing exactly ONE failed step.
    assert "All three steps completed successfully." in final
    assert "FAILED verification" in final
    failure_lines = [ln for ln in final.splitlines() if ln.startswith("- step ")]
    assert len(failure_lines) == 1
    assert "step 2" in failure_lines[0] and "produce b.txt" in failure_lines[0]
    assert "b.txt" in failure_lines[0]

    # Step 3's mini-loop opened with step 2's FAILURE in its prior context.
    step3_open = inner.calls[5][1][0].content
    assert "Step 2 (FAILED)" in step3_open
    # Events narrate all three steps with honest per-step outcomes.
    done = [e for e in events if e.type == EventType.PLAN_STEP_COMPLETED]
    assert [e.payload["ok"] for e in done] == [True, False, True]


async def test_flag_off_never_engages(platform, orchestrator, monkeypatch):
    calls = _spy_planner(monkeypatch)
    platform.config.decompose_local_tasks = False
    inner = _TextOnly(["Done the flat way."], provider="local-off")
    platform.providers.register("local-off", lambda model=None: inner)
    session = await orchestrator.run(_LONG_TASK, AgentType.BUILDER, provider="local-off")
    assert session.status is SessionStatus.COMPLETED
    assert orchestrator.transcript(session.id)["runs"][0]["result"] == "Done the flat way."
    assert calls == []
