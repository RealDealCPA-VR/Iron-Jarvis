"""v1.203.0 Wave C3 — the verify-every-step envelope's retry ladder.

THE SEAM FINDING THIS WAVE IS BUILT ON (read before editing): the one-retry
ladder itself has existed since v1.132.0 — ``decompose.execute_plan`` already
re-runs a verify-failed step ONCE with the verifier's reason prepended
("Your previous attempt failed verification: …"), for EVERY decomposed run,
envelope or none, and ``test_decompose_v1132`` pins that. What C3 closes is
the SILENT SKIP that made the ladder unreachable for exactly the steps a
measured-weak model fumbles: a step that declared no success criteria passed
with ZERO verification (``method == "none"`` — including a mini-loop that ran
out of budget mid-step), so its failure was never seen and the retry never
earned. Under a ``verify_every_step()`` envelope (measured-weak only —
``verify_all_enveloped`` carries the same load-bearing ``is_measured()`` gate
as Wave B), the GOAL itself becomes the gate, the existing ladder absorbs the
now-visible failure with the error fed back, the retry outcome is final
(never a third attempt, budgets respected), and the run narrates:

* ``plan.step_completed`` gains an ADDITIVE ``attempts: 2`` key for a retried
  step — a field, never a second event (stepLabel.ts renders one line per
  event; a duplicate would double-render), and only under verify_all (the
  pre-envelope payload is pinned by exact equality in test_decompose_v1132).
* ``envelope.adapted`` gains ``"step_retry"`` — GOAL-GATED retries only. A
  criteria-carrying step's retry is v1.132.0 behavior the run performs with
  no envelope at all, and attributing it would repeat the confirmed Wave-B
  defect (narrating a bend that was going to happen anyway).
* each ATTEMPTED step's FINAL verdict feeds ``step_outcome_recorder`` — the
  C3→C4 outcome-ledger seam (``envelope/outcomes.py`` lands in parallel; the
  recorder resolves the import per run, so the seam self-activates).

All offline: scripted fake adapters (the test_decompose_v1132 idiom), fake
profiles (the test_runtime_envelope_v1202 idiom), no network, no model calls.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from iron_jarvis.agents import decompose
from iron_jarvis.agents.decompose import PlanStep, execute_plan, run_decomposed, verify_step
from iron_jarvis.agents.runtime import (
    ENVELOPE_ADAPTED,
    AgentRuntime,
    step_outcome_recorder,
    verify_all_enveloped,
)
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.envelope.profile import CapabilityProfile, trusted_profile
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse


# --------------------------------------------------------------- scripted fakes
class _Native(LLMAdapter):
    """Natively tool-capable scripted adapter (never wrapped). Records calls."""

    def __init__(self, replies=(), provider="native-x", model="m1"):
        self.provider, self.model = provider, model
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


class _TextOnly(_Native):
    """Text-only (tool_use False → prompted wrap → the BASE gate decomposes
    multi-step tasks for it today, envelope or none)."""

    def capabilities(self):
        caps = super().capabilities()
        caps["tool_use"] = False
        return caps


# ------------------------------------------------------------------- profiles
_STAMP = "2026-08-22T00:00:00+00:00"


def _weak(provider="native-x", model="m1") -> CapabilityProfile:
    """Measured and the native rung was LOST → needs_decomposition() True →
    verify_every_step() True. The measured-weak shape C3 exists for."""
    return CapabilityProfile(
        model_id=model,
        provider=provider,
        source="probed",
        probed_at=_STAMP,
        tool_protocols={"native": 0.5, "strict_json": 0.95},
        json_adherence=0.95,
        coherence_horizon=8,
        measured_fields=[
            "tool_protocols.native",
            "tool_protocols.strict_json",
            "json_adherence",
        ],
    )


def _strong(provider="local-x", model="llama3") -> CapabilityProfile:
    """Measured and the model HELD every bar → verify_every_step() False."""
    return CapabilityProfile(
        model_id=model,
        provider=provider,
        source="probed",
        probed_at=_STAMP,
        tool_protocols={"native": 0.98, "strict_json": 0.97},
        json_adherence=0.96,
        coherence_horizon=10,
        measured_fields=["tool_protocols.native", "json_adherence"],
    )


def _sloppy_json(provider="native-x", model="m1") -> CapabilityProfile:
    """Holds the native rung and a long horizon, but MEASURED json_adherence
    below the 0.90 bar — verify_every_step()'s OTHER trigger."""
    return CapabilityProfile(
        model_id=model,
        provider=provider,
        source="probed",
        probed_at=_STAMP,
        tool_protocols={"native": 0.98, "strict_json": 0.97},
        json_adherence=0.50,
        coherence_horizon=10,
        measured_fields=["tool_protocols.native", "json_adherence"],
    )


def _floor(provider="native-x", model="m1") -> CapabilityProfile:
    """The unmeasured default floor — nothing was ever asked of this model."""
    return CapabilityProfile(model_id=model, provider=provider)


# --------------------------------------------------------------------- harness
#: Two imperative clauses, NOT bulk — the v1202 idiom: flat today on a native
#: adapter (the precondition for envelope attribution), decomposed today on a
#: prompted one (the precondition for the byte-identical pins).
_MULTI = "Read notes.txt then write a summary to out.md"

#: A 2-step all-goal plan (no criteria anywhere) — the silent-skip population.
_GOAL_PLAN_JSON = json.dumps(
    {"steps": [{"goal": "read notes.txt"}, {"goal": "write a summary of the notes"}]}
)


async def _direct_setup(platform, orchestrator, adapter, task=_MULTI):
    """A real session + AgentRun + AgentRuntime with ``adapter`` registered —
    the test_decompose_v1132 direct harness for execute_plan/verify_step."""
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
    return AgentRuntime(platform), run, session


def _step_events(events, run_id):
    return [
        e.payload
        for e in events
        if e.type == EventType.PLAN_STEP_COMPLETED and e.payload.get("run_id") == run_id
    ]


def _fake_outcomes(monkeypatch):
    """Install a fake ``iron_jarvis.envelope.outcomes`` (the C4 module landing
    in parallel) and return its call list — future-proof in BOTH directions:
    the sys.modules entry wins whether or not the real file exists yet."""
    calls: list[tuple] = []
    fake = ModuleType("iron_jarvis.envelope.outcomes")
    fake.record_outcome = lambda home, provider, model, ok: calls.append(
        (home, provider, model, ok)
    )
    monkeypatch.setitem(sys.modules, "iron_jarvis.envelope.outcomes", fake)
    return calls


# =============================================================================
# 1. The consult — which envelopes gate every step
# =============================================================================
def test_verify_all_consult_bends_on_evidence_only():
    """THE load-bearing pin (the Wave-B is_measured lesson, again): the
    unmeasured floor answers verify_every_step() True by conservative
    construction — asserted, so this test dies with any softening — and the
    consult still refuses it, or every prompted-mode local decomposition (the
    lane's whole pre-envelope population) would flip to goal-gated
    verification on day one, with a judge one-shot spent per step."""
    floor = _floor()
    assert floor.verify_every_step() is True  # the trap
    assert verify_all_enveloped(floor) is False  # the gate
    assert verify_all_enveloped(_weak()) is True
    assert _sloppy_json().needs_decomposition() is False  # json bar alone trips it
    assert verify_all_enveloped(_sloppy_json()) is True
    assert verify_all_enveloped(_strong()) is False
    assert verify_all_enveloped(trusted_profile("openai", "gpt-5.2")) is False
    # Never raises — a stub profile answers the lane's safe default.
    assert verify_all_enveloped(SimpleNamespace()) is False


# =============================================================================
# 2. verify_step — the goal gate exists ONLY under verify_all
# =============================================================================
async def test_no_criteria_still_passes_ungated_without_verify_all(
    platform, orchestrator
):
    """The v1.132.0 silent skip, deliberately preserved for every non-envelope
    caller (byte-identical pin: same verdict, same method, ZERO model calls).
    Mutation-sensitive: hardcoding verify_all=True inside verify_step spends a
    judge call here and goes red on the empty replies list."""
    adapter = _Native([])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    ok, reason, method = await verify_step(
        runtime, run, session, Path(session.workspace_path),
        PlanStep(goal="anything"), "output",
    )
    assert ok and method == "none" and reason == ""
    assert adapter.calls == []


async def test_verify_all_gates_a_criteria_less_step_on_its_goal(
    platform, orchestrator
):
    """Under verify_all the GOAL is the gate, judged by the MODEL — never the
    deterministic file gate (a goal names INPUT files as often as outputs, and
    a phantom-file FAIL would burn the one retry): the goal here contains
    'notes.txt', which must NOT be required to exist in the workspace."""
    adapter = _Native(['{"pass": false, "reason": "nothing was actually read"}'])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    ok, reason, method = await verify_step(
        runtime, run, session, Path(session.workspace_path),
        PlanStep(goal="read notes.txt and note the totals"), "did it (allegedly)",
        verify_all=True,
    )
    assert (ok, method) == (False, "model")
    assert reason == "nothing was actually read"
    judge_prompt = adapter.calls[0][1][0].content
    assert "read notes.txt and note the totals" in judge_prompt
    assert "actually accomplished" in judge_prompt  # the goal-as-criteria wording


async def test_declared_criteria_keep_the_deterministic_gate_under_verify_all(
    platform, orchestrator
):
    """verify_all changes NOTHING for a step that declared criteria: the file
    gate still speaks first, cheap and unfoolable, zero model calls."""
    adapter = _Native([])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    (Path(session.workspace_path) / "out.txt").write_text("hi", encoding="utf-8")
    ok, _reason, method = await verify_step(
        runtime, run, session, Path(session.workspace_path),
        PlanStep(goal="write it", success_criteria="workspace contains out.txt"),
        "wrote out.txt",
        verify_all=True,
    )
    assert ok and method == "files"
    assert adapter.calls == []


# =============================================================================
# 3. execute_plan — the ladder under verify_all: one retry, error fed back,
#    honest events, budgets respected
# =============================================================================
async def test_goal_gated_step_fails_once_then_succeeds_with_error_fed_back(
    platform, orchestrator
):
    adapter = _Native(
        [
            "claimed done, did nothing",  # step attempt 1 (mini-loop)
            '{"pass": false, "reason": "the numbers were never summarized"}',
            "actually summarized: total 42",  # attempt 2 — the ONE retry
            '{"pass": true, "reason": "ok"}',
        ]
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="summarize the numbers")],  # NO criteria — the skip population
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
        verify_all=True,
    )
    assert results[0].ok and results[0].retried and results[0].goal_gated
    assert results[0].verified == "model"
    # The retry's transcript OPENS with the fed-back error, then the narrowing.
    retry_prompt = adapter.calls[2][1][0].content
    assert retry_prompt.startswith(
        "Your previous attempt failed verification: the numbers were never summarized"
    )
    assert "Fix that in this attempt." in retry_prompt
    # Exactly 4 calls: attempt, judge, retry, judge — nothing more.
    assert len(adapter.calls) == 4 and adapter._replies == []
    # The event says so, additively: {index, ok} plus attempts — no 2nd event.
    assert _step_events(events, run.id) == [
        {"run_id": run.id, "index": 0, "ok": True, "attempts": 2}
    ]


async def test_goal_gated_step_failing_twice_is_an_honest_failure_never_a_third(
    platform, orchestrator
):
    adapter = _Native(
        [
            "attempt one",
            '{"pass": false, "reason": "no summary produced"}',
            "attempt two",
            '{"pass": false, "reason": "still no summary"}',
        ]
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    recorded: list[bool] = []
    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="summarize the numbers")],
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
        verify_all=True, record_outcome=recorded.append,
    )
    # The retry outcome is FINAL: recorded failed, reason = the second verdict.
    assert not results[0].ok and results[0].retried and results[0].attempted
    assert results[0].reason == "still no summary"
    assert len(adapter.calls) == 4 and adapter._replies == []  # no third attempt
    assert _step_events(events, run.id) == [
        {"run_id": run.id, "index": 0, "ok": False, "attempts": 2}
    ]
    # The ledger heard the FINAL verdict, once.
    assert recorded == [False]


async def test_non_verify_lane_is_byte_identical_zero_envelope_retries(
    platform, orchestrator
):
    """The frontier/strong/unmeasured pin at the ladder itself, mutation-
    sensitive in three directions: (a) hardcode verify_all=True → the judge
    call pops an empty replies list, red; (b) leak `attempts` outside
    verify_all → the EXACT payload equality goes red (the same equality
    test_decompose_v1132 pins for a retried criteria-step); (c) goal_gated
    stamped without the envelope → red here."""
    adapter = _Native(["claimed done, did nothing"])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="summarize the numbers")],
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
    )
    # v1.132.0 verbatim: the criteria-less step passes UNGATED, no retry, no
    # judge call, and the payload carries not one extra byte.
    assert results[0].ok and not results[0].retried and not results[0].goal_gated
    assert results[0].verified == "none"
    assert len(adapter.calls) == 1
    assert _step_events(events, run.id) == [{"run_id": run.id, "index": 0, "ok": True}]


async def test_the_retry_is_denied_before_it_can_blow_the_step_budget(
    platform, orchestrator
):
    """The budget edge: a 1-step session budget is spent by attempt 1, so the
    retry is REFUSED with the reason saying so — a retry that overruns the
    number the user typed is worse than the failure it would fix. No attempts
    key either: a retry that never ran must not be disclosed as one."""
    adapter = _Native(
        ["claimed done", '{"pass": false, "reason": "nope, nothing happened"}']
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    session.max_steps = 1  # the user's typed budget (Contract 4)
    agent_def = get_agent_definition(AgentType.BUILDER)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    recorded: list[bool] = []
    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="summarize the numbers")],
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
        verify_all=True, record_outcome=recorded.append,
    )
    assert not results[0].ok and not results[0].retried and results[0].attempted
    assert "no step budget left to retry" in results[0].reason
    assert "nope, nothing happened" in results[0].reason  # the verdict survives
    # One mini-loop round + one judge one-shot; the retry round NEVER ran, and
    # the run spent exactly the budget the user typed.
    assert len(adapter.calls) == 2
    assert run.steps == 1
    assert _step_events(events, run.id) == [{"run_id": run.id, "index": 0, "ok": False}]
    assert recorded == [False]


async def test_outcomes_recorded_for_attempted_steps_only(platform, orchestrator):
    """A budget-skipped step is NOT evidence about the model — the ledger
    hears the attempted step's verdict and nothing about the one nobody ran."""
    adapter = _Native(["did step one", '{"pass": true, "reason": "ok"}'])
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    session.max_steps = 1
    agent_def = get_agent_definition(AgentType.BUILDER)
    recorded: list[bool] = []
    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="do the first thing"), PlanStep(goal="do the second thing")],
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
        verify_all=True, record_outcome=recorded.append,
    )
    assert results[0].ok and results[0].attempted
    assert not results[1].attempted and not results[1].retried
    assert recorded == [True]


# =============================================================================
# 4. run_decomposed — "step_retry" attribution (the Wave-B lesson applied)
# =============================================================================
async def _fixed_plan(monkeypatch, plan):
    async def fake_plan_task(*a, **k):
        return plan

    monkeypatch.setattr(decompose, "plan_task", fake_plan_task)


async def test_a_goal_gated_retry_is_narrated_as_step_retry_once(
    platform, orchestrator, monkeypatch
):
    adapter = _Native(
        [
            "step one, allegedly",  # step 1 attempt 1
            '{"pass": false, "reason": "goal not met"}',
            "step one, actually done",  # the retry
            '{"pass": true, "reason": "ok"}',
            "step two done",  # step 2 (passes first try)
            '{"pass": true, "reason": "ok"}',
            "All steps landed.",  # assemble
        ]
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    await _fixed_plan(
        monkeypatch, [PlanStep(goal="first thing"), PlanStep(goal="second thing")]
    )
    agent_def = get_agent_definition(AgentType.BUILDER)
    notes: list[str] = []
    recorded: list[bool] = []
    final = await run_decomposed(
        runtime, run, session, agent_def,
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
        verify_all=True, adaptations=notes, record_outcome=recorded.append,
    )
    assert final == "All steps landed."
    assert notes == ["step_retry"]  # once, however many steps retried
    assert recorded == [True, True]  # final verdicts, post-retry


async def test_a_criteria_steps_retry_is_never_attributed_to_the_envelope(
    platform, orchestrator, monkeypatch
):
    """The attribution pin. A step that DECLARED criteria retries today with
    no envelope anywhere (test_decompose_v1132 pins it) — so under verify_all
    its retry is disclosed as a fact (attempts: 2) but NEVER narrated as an
    envelope bend: 'adapted' claiming a retry that was going to happen anyway
    is the exact confirmed Wave-B defect class."""
    adapter = _Native(
        [
            "wrote it (but did not)",  # attempt 1 — file gate fails, no judge call
            "still did not write it",  # the retry — file gate fails again
            "Honest: the file never appeared.",  # assemble
        ]
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    await _fixed_plan(
        monkeypatch,
        [PlanStep(goal="write the report", success_criteria="workspace contains gone.txt")],
    )
    agent_def = get_agent_definition(AgentType.BUILDER)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    notes: list[str] = []
    recorded: list[bool] = []
    final = await run_decomposed(
        runtime, run, session, agent_def,
        system_prompt="sys",
        tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None,
        verify_all=True, adaptations=notes, record_outcome=recorded.append,
    )
    assert "never appeared" in final
    assert notes == []  # no envelope bend to narrate
    assert recorded == [False]
    # ...while the event still discloses the fact of the two attempts.
    assert _step_events(events, run.id) == [
        {"run_id": run.id, "index": 0, "ok": False, "attempts": 2}
    ]


# =============================================================================
# 5. End-to-end through AgentRuntime.run — the full wiring
# =============================================================================
async def test_e2e_weak_envelope_retries_narrates_and_feeds_the_ledger(
    platform, orchestrator, monkeypatch
):
    """The whole C3 story on the runtime's real call sites: a native adapter
    (flat today) + measured-weak envelope → decomposed lane, goal-gated step
    fails once, ONE retry with the error fed back, run completes, the step
    event says attempts: 2, envelope.adapted carries step_retry AND
    decomposed, and every step's final verdict reached the outcome ledger
    with the run's resolved (home, provider, model)."""
    outcome_calls = _fake_outcomes(monkeypatch)
    adapter = _Native(
        [
            _GOAL_PLAN_JSON,  # 1: the planner one-shot
            "I read them",  # 2: step 1 attempt 1
            '{"pass": false, "reason": "notes.txt was never actually opened"}',
            "opened notes.txt and read the contents",  # 4: the retry
            '{"pass": true, "reason": "ok"}',
            "summary written",  # 6: step 2
            '{"pass": true, "reason": "ok"}',
            "Read the notes and produced the summary.",  # 8: assemble
        ]
    )
    platform.providers.register("native-x", lambda model=None: adapter)
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _weak(p, m)
    )
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    sess = await orchestrator.create_session(
        _MULTI, AgentType.BUILDER, provider="native-x", model="m1"
    )
    run = await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.BUILDER))

    assert run.state is AgentState.COMPLETED
    assert run.result == "Read the notes and produced the summary."
    assert adapter._replies == []  # every scripted round consumed, none extra
    # The retry transcript opened with the fed-back error.
    assert adapter.calls[3][1][0].content.startswith(
        "Your previous attempt failed verification: notes.txt was never actually opened"
    )
    # Honest step events: the retried step says attempts: 2, the clean one
    # carries not one extra byte.
    assert _step_events(events, run.id) == [
        {"run_id": run.id, "index": 0, "ok": True, "attempts": 2},
        {"run_id": run.id, "index": 1, "ok": True},
    ]
    # ONE adapted event; the lane's bend composes with Wave B's, in resolve
    # order (step_retry lands inside the lane, decomposed after it resolves).
    adapted = [e for e in events if e.type == ENVELOPE_ADAPTED]
    assert len(adapted) == 1 and adapted[0].session_id == sess.id
    narrated = adapted[0].payload["adaptations"]
    assert "step_retry" in narrated and "decomposed" in narrated
    assert narrated.index("step_retry") < narrated.index("decomposed")
    # The ledger heard both final verdicts, addressed to the resolved run pair.
    assert outcome_calls == [
        (platform.config.home, "native-x", "m1", True),
        (platform.config.home, "native-x", "m1", True),
    ]


async def _e2e_prompted_baseline(platform, orchestrator):
    """A prompted-mode decomposed run with two criteria-less steps — the
    byte-identical baseline both zero-change e2e pins drive: exactly 4 model
    calls (plan, step, step, assemble), ZERO judge one-shots, v1.132.0 event
    payloads, no adapted event."""
    adapter = _TextOnly(
        [_GOAL_PLAN_JSON, "did step one", "did step two", "Both steps done."],
        provider="local-x",
        model="llama3",
    )
    platform.providers.register("local-x", lambda model=None: adapter)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    sess = await orchestrator.create_session(
        _MULTI, AgentType.BUILDER, provider="local-x", model="llama3"
    )
    run = await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.BUILDER))
    assert run.state is AgentState.COMPLETED and run.result == "Both steps done."
    assert len(adapter.calls) == 4 and adapter._replies == []
    assert _step_events(events, run.id) == [
        {"run_id": run.id, "index": 0, "ok": True},
        {"run_id": run.id, "index": 1, "ok": True},
    ]
    assert not [e for e in events if e.type == ENVELOPE_ADAPTED]


async def test_e2e_unmeasured_floor_keeps_the_decomposed_lane_byte_identical(
    platform, orchestrator
):
    """The lane's whole pre-envelope population rides the UNMEASURED floor —
    whose verify_every_step() is True by conservative construction (asserted
    in the consult pin above). This run must not gain a single judge call,
    payload byte, or event: the envelope bends on evidence only."""
    prof = platform.providers.capability_profile("local-x", "llama3")
    assert not prof.is_measured() and prof.verify_every_step() is True  # the trap
    await _e2e_prompted_baseline(platform, orchestrator)


async def test_e2e_measured_strong_envelope_changes_nothing(
    platform, orchestrator, monkeypatch
):
    """Evidence of STRENGTH bends nothing either: a measured profile that held
    every bar (verify_every_step() False) leaves the base-gate decomposition
    exactly as v1.132.0 shipped it — zero retries, zero disclosure keys."""
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _strong(p, m)
    )
    await _e2e_prompted_baseline(platform, orchestrator)


# =============================================================================
# 6. step_outcome_recorder — the C3→C4 seam
# =============================================================================
def test_recorder_feeds_the_parallel_module_with_home_provider_model(
    platform, monkeypatch
):
    calls = _fake_outcomes(monkeypatch)
    record = step_outcome_recorder(platform, "local-x", "llama3")
    assert record is not None
    record(True)
    record(False)
    assert calls == [
        (platform.config.home, "local-x", "llama3", True),
        (platform.config.home, "local-x", "llama3", False),
    ]


def test_recorder_refuses_trusted_providers(platform, monkeypatch):
    """Frontier sees ZERO envelope behavior — ledger writes included. Gated on
    the manager's single trusted oracle, never a private provider list."""
    _fake_outcomes(monkeypatch)
    for name in ("openai", "anthropic", "mock", "claude-cli"):
        assert platform.providers.is_trusted_provider(name)  # the oracle itself
        assert step_outcome_recorder(platform, name, "m") is None


def test_recorder_is_none_when_the_module_is_absent_and_never_raises(
    platform, monkeypatch
):
    # Absent module (simulated — outcomes.py landed in parallel with this
    # seam, so absence here means a partial checkout, not today's tree):
    monkeypatch.setitem(sys.modules, "iron_jarvis.envelope.outcomes", None)
    assert step_outcome_recorder(platform, "local-x", "llama3") is None
    # A stub platform records nothing rather than raising.
    fake = ModuleType("iron_jarvis.envelope.outcomes")
    fake.record_outcome = lambda *a: None
    monkeypatch.setitem(sys.modules, "iron_jarvis.envelope.outcomes", fake)
    assert step_outcome_recorder(SimpleNamespace(), "local-x", "llama3") is None
    # Blank ids: nowhere to file the evidence.
    assert step_outcome_recorder(platform, "", "llama3") is None
    assert step_outcome_recorder(platform, "local-x", "") is None
    # And the returned callable swallows a raising ledger (belt-and-braces —
    # record_outcome is never-raising by contract, but a run must not depend
    # on the parallel module honoring it).
    def boom(*a):
        raise RuntimeError("ledger exploded")

    fake.record_outcome = boom
    record = step_outcome_recorder(platform, "local-x", "llama3")
    record(True)  # must not raise


# =============================================================================
# 7. Wave C5 wiring — the agent lane's budget planner hears the measured ratio
# =============================================================================
def _ratio_profile(provider="native-x", model="m1") -> CapabilityProfile:
    """Measured-STRONG with a measured token ratio: bends nothing in the loop
    (native rung held, verify_every_step False) — the ratio is the ONLY thing
    the envelope contributes to this run."""
    return CapabilityProfile(
        model_id=model,
        provider=provider,
        source="probed",
        probed_at=_STAMP,
        tool_protocols={"native": 0.98, "strict_json": 0.97},
        json_adherence=0.96,
        coherence_horizon=10,
        chars_per_token=3.2,
        measured_fields=["tool_protocols.native", "json_adherence", "chars_per_token"],
    )


async def test_measured_ratio_reaches_the_agent_lanes_budget_planner(
    platform, orchestrator, monkeypatch
):
    """perceive_act passes the SAME provenance-gated ratio the chat lanes
    resolve (`_history_ratio` — one resolver, never a copy) into
    plan_agent_transcript: 3.2 when the profile MEASURED chars_per_token,
    None on the unmeasured floor (whose 4.0 is a default wearing no
    evidence — the byte-identical path pinned in test_budget_ratio_v1203)."""
    import iron_jarvis.context.agent_window as _aw

    real = _aw.plan_agent_transcript
    seen: list = []

    def spy(messages, *, window, system_text="", chars_per_token=None):
        seen.append(chars_per_token)
        return real(
            messages,
            window=window,
            system_text=system_text,
            chars_per_token=chars_per_token,
        )

    monkeypatch.setattr(_aw, "plan_agent_transcript", spy)
    # Run 1: a measured ratio on the answering pair.
    platform.providers.register("native-x", lambda model=None: _Native(["hello"]))
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _ratio_profile(p, m)
    )
    sess = await orchestrator.create_session(
        "Say hello", AgentType.BUILDER, provider="native-x", model="m1"
    )
    run = await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.BUILDER))
    assert run.state is AgentState.COMPLETED
    assert seen and all(cpt == 3.2 for cpt in seen)
    # Run 2: the unmeasured floor (the REAL capability_profile) → None, the
    # planner's pinned byte-identical default.
    monkeypatch.undo()
    monkeypatch.setattr(_aw, "plan_agent_transcript", spy)
    platform.providers.register("native-y", lambda model=None: _Native(["hi"], provider="native-y"))
    seen.clear()
    sess2 = await orchestrator.create_session(
        "Say hello", AgentType.BUILDER, provider="native-y", model="m1"
    )
    run2 = await AgentRuntime(platform).run(sess2, get_agent_definition(AgentType.BUILDER))
    assert run2.state is AgentState.COMPLETED
    assert seen and all(cpt is None for cpt in seen)
