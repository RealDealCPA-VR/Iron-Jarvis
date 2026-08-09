"""The agent transcript gets a context budget (v1.152.0).

Chat was budgeted in v1.146.0. The perceive→act loop was not: it appended the
assistant turn and every tool result on every step with no token accounting, so
the only limits were indirect — 16k chars per tool result, 12 steps — which on a
32k local model is tens of thousands of tokens of transcript before the system
prompt (profile + memory index + roster + grounding) is even counted. Agent runs
are where context actually gets big.

THE TEST THAT MATTERS is :func:`test_a_tool_result_never_outlives_its_request`.
Trimming a transcript that contains tool calls is not the same problem as
trimming a chat history: split a ``tool_use`` from its ``tool_result`` and
strict providers reject the ENTIRE conversation. A context fix that corrupts the
transcript is worse than the overflow it prevents, so that pairing is asserted
directly and at every budget size.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.context.agent_window import (
    STALE_TOOL_BLOCKS,
    blocks_of,
    plan_agent_transcript,
    recap_of,
)
from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall


def _task(text="do the thing"):
    return LLMMessage(role="user", content=text)


def _step(i: int, *, tool="read_file", out="RESULT", say="working"):
    """One assistant turn that called a tool, plus that tool's result."""
    call = ToolCall(id=f"call_{i}", name=tool, arguments={})
    return [
        LLMMessage(role="assistant", content=say, tool_calls=[call]),
        LLMMessage(role="tool", tool_call_id=f"call_{i}", name=tool, content=out),
    ]


def _transcript(steps: int, out_chars: int = 200, say_chars: int = 0):
    msgs = [_task()]
    for i in range(steps):
        say = ("thinking about step %d " % i) * max(1, say_chars // 24)
        msgs.extend(_step(i, out="x" * out_chars, say=say))
    return msgs


# --------------------------------------------------------------------------- #
# (1) THE CONSTRAINT: tool pairing is never broken.
# --------------------------------------------------------------------------- #
# TWO things make this test actually able to fail, both found by removing the
# pairing rule and watching it keep passing:
#   1. A FINE sweep, not a handful of round numbers — the orphan only appears
#      when the keep/drop boundary lands BETWEEN an assistant turn and its
#      result, and a 5-size sweep can miss every such boundary.
#   2. SUBSTANTIAL assistant text. With a 6-token "working" turn there is
#      almost always room to take the assistant once its result is in, so the
#      pair never splits. Real agent turns carry reasoning alongside the call;
#      a fixture that does not is a fixture that cannot catch this.
@pytest.mark.parametrize("window", list(range(900, 6000, 111)))
def test_a_tool_result_never_outlives_its_request(window):
    """Every kept tool result must still have the assistant turn that asked for
    it — at every budget, including ones tight enough to drop most of the run.
    Anthropic rejects the whole conversation otherwise."""
    plan = plan_agent_transcript(
        _transcript(12, out_chars=800, say_chars=700), window=window
    )
    requested = {
        c.id
        for m in plan.messages
        for c in (m.tool_calls or [])
        if m.role == "assistant"
    }
    answered = {m.tool_call_id for m in plan.messages if m.role == "tool"}
    assert answered <= requested, (
        f"orphaned tool results at window={window}: {sorted(answered - requested)}"
    )


def test_an_assistant_turn_and_its_results_are_one_block():
    blocks = blocks_of(_transcript(3))
    assert len(blocks) == 4  # the task + three steps
    assert [len(b) for b in blocks] == [1, 2, 2, 2]


def test_parallel_tool_calls_stay_with_their_turn():
    """One assistant turn can request several tools; all their results belong
    to that same block."""
    calls = [ToolCall(id=f"c{i}", name="read_file", arguments={}) for i in range(3)]
    msgs = [
        _task(),
        LLMMessage(role="assistant", content="fan out", tool_calls=calls),
        *[
            LLMMessage(role="tool", tool_call_id=f"c{i}", name="read_file", content="r")
            for i in range(3)
        ],
    ]
    blocks = blocks_of(msgs)
    assert [len(b) for b in blocks] == [1, 4]


def test_an_orphan_tool_message_does_not_attach_to_a_plain_turn():
    """Defensive: a tool message with no preceding tool-calling assistant turn
    is its own block, not silently glued to whatever came before."""
    msgs = [_task(), LLMMessage(role="tool", tool_call_id="x", content="stray")]
    assert [len(b) for b in blocks_of(msgs)] == [1, 1]


# --------------------------------------------------------------------------- #
# (2) THE TASK SURVIVES.
# --------------------------------------------------------------------------- #
def test_the_task_is_never_dropped():
    """A transcript that keeps the last three tool results and loses the goal
    produces confident work on the wrong problem.

    Sized with big ASSISTANT text rather than big tool output, because trimming
    tool results is tried FIRST and — as the sibling test shows — is usually
    enough on its own. Only unshrinkable content forces blocks to be dropped.
    """
    msgs = [_task()]
    for i in range(20):
        msgs.extend(_step(i, out="r", say="reasoning " * 200))
    plan = plan_agent_transcript(msgs, window=2500)
    assert plan.messages[0].role == "user"
    assert "do the thing" in plan.messages[0].content
    assert plan.dropped_blocks > 0


def test_a_task_too_big_for_the_model_is_clipped_and_flagged():
    msgs = [_task("z" * 100_000), *_step(0)]
    plan = plan_agent_transcript(msgs, window=1200)
    assert plan.clipped_task is True
    assert len(plan.messages) == 1
    assert plan.messages[0].content  # something of the task survives


# --------------------------------------------------------------------------- #
# (3) THE ORDER OF SACRIFICE: stale tool output before whole steps.
# --------------------------------------------------------------------------- #
def test_stale_tool_output_is_trimmed_before_steps_are_dropped():
    # Sized so trimming the stale results alone brings it under budget.
    plan = plan_agent_transcript(_transcript(6, out_chars=3000), window=4200)
    assert plan.tools_trimmed > 0
    assert plan.dropped_blocks == 0, (
        "steps should not be sacrificed while trimming works"
    )


def test_the_most_recent_tool_results_are_kept_intact():
    """The model is mid-task; the results it has NOT acted on yet are the ones
    it still needs verbatim."""
    plan = plan_agent_transcript(_transcript(8, out_chars=1200), window=7000)
    tools = [m for m in plan.messages if m.role == "tool"]
    assert tools, "no tool results survived at all"
    for m in tools[-STALE_TOOL_BLOCKS:]:
        assert "trimmed" not in m.content


def test_trimming_never_mutates_the_callers_messages():
    """The loop owns the real list and keeps appending to it — editing in place
    would rewrite the run's own history AND the persisted transcript."""
    msgs = _transcript(8, out_chars=1500)
    before = [m.content for m in msgs]
    plan_agent_transcript(msgs, window=4000)
    assert [m.content for m in msgs] == before


# --------------------------------------------------------------------------- #
# (4) NOTHING CHANGES when everything already fits.
# --------------------------------------------------------------------------- #
def test_a_short_run_on_a_big_window_is_untouched():
    msgs = _transcript(3)
    plan = plan_agent_transcript(msgs, window=128_000, system_text="sys")
    assert plan.messages == msgs
    assert plan.changed is False
    assert plan.recap == ""


def test_an_empty_transcript_is_not_a_crash():
    assert plan_agent_transcript([], window=8000).messages == []


def test_the_system_prompt_counts_against_the_budget():
    """A profile + memory index + roster + grounding prompt is not free, and
    ignoring it is how the budget passes while the request still overflows."""
    msgs = _transcript(6, out_chars=1200)
    roomy = plan_agent_transcript(msgs, window=9000, system_text="s")
    crowded = plan_agent_transcript(msgs, window=9000, system_text="s" * 20_000)
    # The observable is that the SAME transcript, on the SAME window, has to
    # give something up once the prompt is large — not the message count, since
    # trimming tool output (the first sacrifice) leaves the count unchanged.
    assert roomy.changed is False, "this transcript fits when the prompt is small"
    assert crowded.changed is True, "a 20k-char system prompt must eat into the budget"
    assert crowded.tools_trimmed > 0 or crowded.dropped_blocks > 0


# --------------------------------------------------------------------------- #
# (5) The recap describes only what happened.
# --------------------------------------------------------------------------- #
def test_the_recap_names_the_tools_that_actually_ran():
    dropped = [_step(0, tool="excel_query", say="checking the ledger")]
    text = recap_of(dropped)
    assert "excel_query" in text and "checking the ledger" in text
    assert "condensed" in text


def test_the_recap_is_bounded_and_empty_when_nothing_was_dropped():
    assert recap_of([]) == ""
    assert len(recap_of([_step(i, say="x" * 400) for i in range(40)])) < 1200


def test_the_recap_goes_to_the_system_prompt_not_the_transcript():
    """Injecting it as a message would put words in the model's own mouth that
    it never said — it would read them back as its own prior turns."""
    msgs = [_task()]
    for i in range(20):
        msgs.extend(_step(i, out="r", say="reasoning " * 200))
    plan = plan_agent_transcript(msgs, window=2500)
    assert plan.recap, "expected a recap at this budget"
    assert not any(plan.recap[:40] in (m.content or "") for m in plan.messages)


# --------------------------------------------------------------------------- #
# (6) Wired into a REAL run.
# --------------------------------------------------------------------------- #
def test_a_real_agent_run_survives_a_tiny_window(tmp_path):
    """End to end: a pinned 3k window must not break a session."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    assert (
        client.put(
            "/settings", json={"values": {"model_context_windows": {"mock": 3000}}}
        ).status_code
        == 200
    )
    r = client.post("/sessions", json={"task": "write a report", "wait": True})
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_the_loop_sends_the_planned_transcript_not_the_raw_one(tmp_path, monkeypatch):
    """Pins the WIRING: without it the planner is dead code. Captures what the
    router was actually handed on a run with a tiny window."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"model_context_windows": {"mock": 2000}}})
    platform = client.app.state.platform

    seen: list[tuple[str, list]] = []
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_stream = adapter.stream

        def stream(*, system, messages, tools):
            seen.append((system, list(messages)))
            return real_stream(system=system, messages=messages, tools=tools)

        adapter.stream = stream
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)
    # A task far larger than the pinned window forces the planner to engage on
    # the very first step, and to engage VISIBLY (it must clip the task).
    task = "report. " * 900
    client.post("/sessions", json={"task": task, "wait": True})

    assert seen, "the agent loop never reached the adapter"
    # Per REQUEST, not summed over the run — what has to fit the window is each
    # individual call. The raw transcript would carry the task verbatim, so if
    # any single call still does, the planner ran and its result was discarded.
    worst = max(sum(len(m.content or "") for m in msgs) for _, msgs in seen)
    assert worst < len(task), (
        f"the raw transcript reached the provider: {worst} chars in one call "
        f"for a {len(task)}-char task on a 2000-token window"
    )


def test_a_clipped_task_is_reported_as_such_not_as_ordinary_trimming(tmp_path):
    """When the goal itself does not fit, the run proceeds on a TRUNCATED task.
    That is a result the user has to be able to distrust, so it gets its own
    honest message rather than the generic "trimmed context" note."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"model_context_windows": {"mock": 1200}}})
    r = client.post("/sessions", json={"task": "audit. " * 4000, "wait": True})
    assert r.status_code == 200
    sid = r.json()["id"]  # POST /sessions returns the session FLAT

    # Read the PERSISTED record, not the live stream: the point is that someone
    # reviewing this session tomorrow can still see the task was cut.
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord

    with Session(client.app.state.platform.engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.type == "context.trimmed")
        ).all()
    assert rows, "no context.trimmed event was persisted"
    assert all(r.session_id == sid for r in rows), "not tagged to the session"
    assert len(rows) == 1, f"one notice per run, not per step; got {len(rows)}"
    payload = json.loads(rows[0].payload_json)
    assert payload["clipped_task"] is True
    assert "use a bigger model" in payload["detail"], (
        f"a task too big for the window must say so; got {payload['detail']!r}"
    )


def test_a_run_that_fits_reports_no_trimming_at_all(tmp_path):
    """The event is a signal, not noise — a normal run must not emit it."""
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select

    from iron_jarvis.core.models import EventRecord
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    client.put(
        "/settings", json={"values": {"model_context_windows": {"mock": 200000}}}
    )
    client.post("/sessions", json={"task": "write a short note", "wait": True})
    with Session(client.app.state.platform.engine) as db:
        rows = db.exec(
            select(EventRecord).where(EventRecord.type == "context.trimmed")
        ).all()
    assert rows == [], "a run that fits its window must not claim it was trimmed"
