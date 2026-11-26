"""Intelligence plan, release 4: "not checked" is on the result line.

When a step of a larger task could not be double-checked (the checking model
failed, or its answer could not be read), the step is let through and the
run's summary says so in words added by code. The result line a person looks
at first still read as finished: the verdict was a plain "completed", the
badge a plain "Done", the plan's checklist a plain tick, and the step was
written down as a pass for that model.

These tests run a REAL split task (plan, steps, check, write-up) on a
scripted model whose check fails, and read every place the run is recorded:
the kept plan, the result payload, the run's row, the finished-run event, the
checklist and the bell's line.
"""

from __future__ import annotations

import json
from pathlib import Path

from iron_jarvis.agents import transcript as plan_record
from iron_jarvis.agents.decompose import (
    PlanStep,
    StepResult,
    assemble,
    execute_plan,
    step_unchecked,
)
from iron_jarvis.agents.orchestrator import _completion_payload
from iron_jarvis.agents.outcome import result_line, session_result, unchecked_words
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.comm import notifier
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, SessionStatus
from tests.test_decompose_v1132 import (
    _bare_run,
    _direct_setup,
    _Native,
    _session_stub,
    _stub_runtime,
    _TextOnly,
)

#: Long enough (> 200 characters) to be split into steps.
TASK = (
    "Create a file named hello.txt containing a friendly greeting for the "
    "user, and after that write a short summary in your answer of everything "
    "done so far, so the person who asked for this work can review it later "
    "today without opening the file."
)

PLAN = json.dumps(
    {
        "steps": [
            {
                "goal": "write hello.txt",
                "success_criteria": "workspace contains hello.txt",
                "tools": ["write_file"],
            },
            {
                "goal": "summarize what was done",
                "success_criteria": "the summary says a greeting file was written",
                "tools": [],
            },
        ]
    }
)

WRITE_CALL = (
    "```tool_call\n"
    '{"name": "write_file", "arguments": {"path": "hello.txt", "content": "Hello there!"}}\n'
    "```"
)


def _script(*check_replies: str) -> list[str]:
    """The whole run's replies; ``check_replies`` are what the checking model
    says about step 2 (one, or two when the first cannot be read)."""
    return [
        PLAN,
        WRITE_CALL,
        "Wrote hello.txt with the greeting.",
        "A greeting file named hello.txt was written.",
        *check_replies,
        "hello.txt was written and the work was summarized.",
    ]


async def _run(platform, orchestrator, provider: str, *check_replies: str):
    inner = _TextOnly(_script(*check_replies), provider=provider)
    platform.providers.register(provider, lambda model=None: inner)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    session = await orchestrator.run(TASK, AgentType.BUILDER, provider=provider)
    assert session.status is SessionStatus.COMPLETED, session.summary
    assert inner._replies == [], "the scripted run did not go as written"
    return orchestrator.get_session(session.id), events


# --------------------------------------------------------------------------- #
# The run whose check could not be read.
# --------------------------------------------------------------------------- #
async def test_a_check_that_could_not_be_read_is_on_the_record_and_on_the_result_line(
    platform, orchestrator
):
    row, events = await _run(
        platform, orchestrator, "local-r4", "looks good to me", "yes, it passes"
    )
    assert (Path(row.workspace_path) / "hello.txt").exists()

    # THE RECORD: the kept plan says which step, the result payload counts it.
    result = session_result(platform.engine, row.id)
    assert result["plan_steps_unchecked"] == 1
    assert result["unchecked_steps"] == [{"step": 2, "goal": "summarize what was done"}]
    assert result["plan_steps_failed"] == 0

    # THE RESULT LINE: never a plain "Done" over it.
    assert result_line(result) == "Done. 1 step could not be checked."

    # The verdict on the JOB is unchanged (the work was done), and the row
    # carries the count for every list that reads only the row.
    assert row.outcome == "completed"
    assert row.unchecked_steps == 1

    # The run's own write-up says it too, in the same words.
    assert "could not be checked" in row.summary
    assert "- step 2: summarize what was done" in row.summary
    assert "- step 1" not in row.summary
    assert "verified" not in row.summary.lower()


async def test_the_step_is_marked_as_it_finishes_and_on_the_checklist(platform, orchestrator):
    row, events = await _run(
        platform, orchestrator, "local-r4b", "looks good to me", "yes, it passes"
    )
    done = [e.payload for e in events if e.type == EventType.PLAN_STEP_COMPLETED]
    run_id = done[0]["run_id"]
    # A checked step's payload is exactly what it always was.
    assert done[0] == {"run_id": run_id, "index": 0, "ok": True}
    # The unchecked one carries ONE more key.
    assert done[1] == {"run_id": run_id, "index": 1, "ok": True, "checked": False}

    stored = plan_record.latest_plan(platform.engine, row.id)
    steps = plan_record.plan_checklist(stored)
    assert [s["state"] for s in steps] == ["done", "unchecked"]
    assert "unchecked" in plan_record.STEP_STATES


async def test_the_finished_run_event_and_the_bells_line_say_it(platform, orchestrator):
    row, _events = await _run(
        platform, orchestrator, "local-r4c", "looks good to me", "yes, it passes"
    )
    payload = _completion_payload(row)
    assert payload["unchecked_steps"] == 1 and payload["outcome"] == "completed"

    payload["origin"] = "schedule:Monday report"
    line = notifier._session_line(payload)
    assert "finished. 1 step could not be checked" in line, line

    payload["origin"] = "job:mission"
    assert notifier._session_line(payload).startswith(
        "Your objective is done. 1 step could not be checked"
    )


# --------------------------------------------------------------------------- #
# The run whose check passed: nothing changes.
# --------------------------------------------------------------------------- #
async def test_a_run_whose_check_passed_reads_done_as_before(platform, orchestrator):
    row, events = await _run(
        platform, orchestrator, "local-r4d", '{"pass": true, "reason": "it says so"}'
    )
    result = session_result(platform.engine, row.id)
    assert result["plan_steps_unchecked"] == 0 and result["unchecked_steps"] == []
    assert result_line(result) == "Done"
    assert row.outcome == "completed" and row.unchecked_steps is None
    assert "could not be checked" not in row.summary

    done = [e.payload for e in events if e.type == EventType.PLAN_STEP_COMPLETED]
    assert all("checked" not in p for p in done)
    assert "unchecked_steps" not in _completion_payload(row)

    stored = plan_record.latest_plan(platform.engine, row.id)
    assert [s["state"] for s in plan_record.plan_checklist(stored)] == ["done", "done"]
    payload = {**_completion_payload(row), "origin": "schedule:Monday report"}
    assert notifier._session_line(payload).split(" — ")[0].endswith("finished")


# --------------------------------------------------------------------------- #
# The check that FAILED to run at all, and what is written down for the model.
# --------------------------------------------------------------------------- #
class _CheckRaises(_Native):
    """Answers the step, then fails when asked to check it."""

    async def complete(self, *, system, messages, tools):
        if "Does the output satisfy the criteria?" in (messages[0].content or ""):
            self.calls.append((system, list(messages), list(tools)))
            raise RuntimeError("the checking model is not reachable")
        return await super().complete(system=system, messages=messages, tools=tools)


async def test_a_check_that_fails_to_run_is_not_written_down_as_a_pass(platform, orchestrator):
    adapter = _CheckRaises(["Revenue grew 12% year over year."], provider="native-r4")
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    noted: list[bool] = []
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="summarize", success_criteria="the summary mentions revenue")],
        system_prompt="sys", tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None, record_outcome=noted.append,
    )

    assert results[0].ok and results[0].verified == "unverified"
    assert step_unchecked(results[0])
    assert noted == [], "a step nobody could check was written down as a pass for this model"
    done = [e.payload for e in events if e.type == EventType.PLAN_STEP_COMPLETED]
    assert done[-1].get("checked") is False


async def test_a_checked_step_is_still_written_down_for_the_model(platform, orchestrator):
    adapter = _Native(
        ["Revenue grew 12%.", '{"pass": true, "reason": "mentions revenue"}'],
        provider="native-r4b",
    )
    runtime, run, session = await _direct_setup(platform, orchestrator, adapter)
    agent_def = get_agent_definition(AgentType.BUILDER)
    noted: list[bool] = []
    results = await execute_plan(
        runtime, run, session, agent_def,
        [PlanStep(goal="summarize", success_criteria="the summary mentions revenue")],
        system_prompt="sys", tool_specs=platform.registry.specs(agent_def.tools),
        session_allow=set(), sink=None, record_outcome=noted.append,
    )
    assert results[0].verified == "model" and not step_unchecked(results[0])
    assert noted == [True]


# --------------------------------------------------------------------------- #
# The words, and what counts.
# --------------------------------------------------------------------------- #
def test_the_words_count_steps_and_say_nothing_for_none():
    assert unchecked_words(1) == "1 step could not be checked."
    assert unchecked_words(3) == "3 steps could not be checked."
    assert unchecked_words(0) == "" and unchecked_words(None) == "" and unchecked_words("x") == ""


def test_the_result_line_adds_the_words_only_to_a_run_that_is_done():
    base = {"found": True, "plan_steps_unchecked": 2}
    assert result_line({**base, "status": "completed", "outcome": "completed"}) == (
        "Done. 2 steps could not be checked."
    )
    # A run that fell short already says the more important thing first.
    assert result_line({**base, "status": "completed", "outcome": "completed_with_failures"}) == "Part failed"
    assert result_line({**base, "status": "completed", "outcome": "needs_you"}) == "Needs you"
    assert result_line({**base, "status": "failed", "outcome": None}) == "Failed"
    assert result_line({**base, "status": "cancelled", "outcome": None}) == "Stopped"
    assert result_line({"found": False}) == "" and result_line(None) == ""


def test_what_counts_as_done_but_not_checked():
    assert step_unchecked({"ok": True, "verified": "unverified"})
    assert step_unchecked(StepResult(index=0, goal="g", ok=True, verified="unverified"))
    # Checked by a model or by its files, or nothing to check: a plain pass.
    for how in ("model", "files", "none", ""):
        assert not step_unchecked({"ok": True, "verified": how})
    # A step that failed, or never ran, is said as that.
    assert not step_unchecked({"ok": False, "verified": "unverified"})
    assert not step_unchecked({"ok": True, "verified": "unverified", "attempted": False})


async def test_the_write_up_says_could_not_be_checked_even_when_the_model_leaves_it_out():
    inner = _TextOnly(["Both steps completed."], provider="local-r4e")
    runtime, _ = _stub_runtime(inner)
    results = [
        StepResult(index=0, goal="summarize revenue", ok=True, output="done", verified="model"),
        StepResult(index=1, goal="draft the email", ok=True, output="done", verified="unverified"),
    ]
    final = await assemble(runtime, _bare_run(), _session_stub(provider="local-r4e"), results)
    assert "Both steps completed." in final
    assert "were done but could not be checked" in final
    assert "- step 2: draft the email" in final and "- step 1" not in final
