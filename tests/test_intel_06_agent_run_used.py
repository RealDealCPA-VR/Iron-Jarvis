"""The intelligence plan, release 6: an agent run's own record says what the
run USED, step by step.

Before this release an agent run's row had tokens and cost only: no cache
counts, no tool list, no time. Its row (``AgentRun``) now keeps, written by
the save each step already makes: the provider's cache counts summed over its
model calls, the NAMES of the tools sent and their estimated size, the size
of each part of its prompt when it began, how long the model took and how
many calls answered, and the time to its first word. Each step's
``llm.completed`` event carries that step's own numbers.

Numbers and names only: never the text of any part. A REAL run through the
real orchestrator, runtime and router on a scripted model; time is an
injected clock (nothing here sleeps).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents import run_facts
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.platform import build_platform
from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.context import breakdown as B
from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, AgentType, SessionStatus
from iron_jarvis.eval import call_ledger, reply_facts
from iron_jarvis.profile import ProfileStore
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from tests.test_intel_04_not_checked_on_the_result_line import PLAN, TASK, WRITE_CALL

CACHED = {"input_tokens": 900, "output_tokens": 20,
          "cache_read_input_tokens": 600, "cache_creation_input_tokens": 100}
PLAIN = {"input_tokens": 900, "output_tokens": 20}
CALL_S = 1.5
CALL_MS = int(CALL_S * 1000)

MARK_TASK = "ZQXTASKMARK"
MARK_MEMORY = "ZQXMEMORYMARK"
MARK_ARGUMENT = "ZQXARGUMENTMARK"
MARKS = (MARK_TASK, MARK_MEMORY, MARK_ARGUMENT)


class Clock:
    def __init__(self) -> None:
        self.t = 9_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def clock(monkeypatch, platform) -> Clock:
    c = Clock()
    monkeypatch.setattr(reply_facts, "clock", c)
    platform.router._clock = c
    return c


class _Scripted(LLMAdapter):
    """A scripted model: each call takes ``CALL_S`` on the injected clock."""

    def __init__(self, replies, clock: Clock, *, usage: dict, provider="native-r6", tool_use=True):
        self.provider, self.model = provider, "m1"
        self._replies, self._clock, self._usage, self._tool_use = list(replies), clock, usage, tool_use
        self.calls: list[dict] = []

    def capabilities(self):
        return {"provider": self.provider, "model": self.model, "tool_use": self._tool_use, "vision": False}

    async def complete(self, *, system, messages, tools):
        self.calls.append({
            "system": system,
            "messages": [(m.role, m.content) for m in messages],
            "tools": list(tools or []),
        })
        self._clock.t += CALL_S
        r = self._replies.pop(0)
        if isinstance(r, LLMResponse):
            return r
        return LLMResponse(text=r, usage=dict(self._usage))


def _write(usage: dict, content: str = "Hello there!") -> LLMResponse:
    return LLMResponse(
        text="", finish_reason="tool_use", usage=dict(usage),
        tool_calls=[ToolCall(id="c1", name="write_file", arguments={"path": "hello.txt", "content": content})],
    )


async def _run(platform, orchestrator, clock, replies, *, usage=CACHED, task="Write hello.txt.",
               provider="native-r6", tool_use=True):
    inner = _Scripted(replies, clock, usage=usage, provider=provider, tool_use=tool_use)
    platform.providers.register(provider, lambda model=None: inner)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    session = await orchestrator.run(task, AgentType.BUILDER, provider=provider)
    assert session.status is SessionStatus.COMPLETED, session.summary
    assert inner._replies == [], "the scripted run did not go as written"
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(AgentRun)).all() if r.session_id == session.id]
    assert len(rows) == 1, rows
    steps = [e.payload for e in events if e.type == EventType.LLM_COMPLETED]
    return rows[0], steps, inner, session


# --------------------------------------------------------------------------- #
# The run's row
# --------------------------------------------------------------------------- #
async def test_a_run_saves_what_it_used_on_its_own_row(platform, orchestrator, clock):
    row, steps, inner, _ = await _run(platform, orchestrator, clock, [_write(CACHED), "Wrote hello.txt."])
    assert len(inner.calls) == 2 and row.steps == 2

    # The provider's cache counts, summed over the two steps (parts of the input).
    assert (row.cache_read_input_tokens, row.cache_creation_input_tokens) == (1200, 200)
    assert row.input_tokens == 1800

    # The NAMES of the tools the model was really sent, in that order, and their size.
    sent_names = [t["name"] for t in inner.calls[0]["tools"]]
    assert "write_file" in sent_names
    assert json.loads(row.tools_sent_json) == sent_names
    one_call = estimate_tokens(json.dumps(inner.calls[0]["tools"], ensure_ascii=False, sort_keys=True, default=str))
    assert row.tool_spec_tokens == 2 * one_call > 0
    assert inner.calls[0]["tools"][0]["description"] not in row.tools_sent_json

    # How long the model took, from the injected clock, and the first word.
    assert (row.model_calls, row.model_ms) == (2, 2 * CALL_MS)
    assert row.first_word_ms == 2 * CALL_MS, "the first step said no word; the second did"

    # The size of each part when the run began, and the sums over its steps.
    record = json.loads(row.parts_json)
    assert record["v"] == B.RECORD_VERSION
    assert record["parts"]["messages"] > 0 and record["parts"]["system"] > 0
    assert record["used"] == sum(record["parts"].values())
    assert record["parts"]["system"] <= estimate_tokens(inner.calls[0]["system"])
    assert record["tools"]["builtin"][0] == len(sent_names) and record["tools"]["extensions"] == {}
    assert record["cache_input"] == row.input_tokens, "every call reported a cache figure"
    sent = record["sent"]
    assert sent["steps"] == 2 and sent["tools"] == row.tool_spec_tokens
    assert sent["system"] == sum(estimate_tokens(c["system"]) for c in inner.calls)
    assert sent["messages"] > 0


async def test_each_step_event_carries_that_steps_own_numbers(platform, orchestrator, clock):
    row, steps, inner, _ = await _run(platform, orchestrator, clock, [_write(CACHED), "Wrote hello.txt."])
    assert [s["step"] for s in steps] == [1, 2]
    for step, call in zip(steps, inner.calls):
        assert step["model_ms"] == CALL_MS
        assert (step["cache_read_input_tokens"], step["cache_creation_input_tokens"]) == (600, 100)
        assert step["tools"] == len(call["tools"])
        assert step["tool_spec_tokens"] == row.tool_spec_tokens // 2
        assert step["sent"]["system"] == estimate_tokens(call["system"])
        assert step["sent"]["messages"] > 0
        # What the event always carried is unchanged.
        assert {"run_id", "step", "provider", "model", "input_tokens", "output_tokens", "cost_usd",
                "task_class"} <= set(step)
    # The second step sends more: the first step's call and its result came back.
    assert steps[1]["sent"]["messages"] > steps[0]["sent"]["messages"]


async def test_no_cache_figure_reported_is_not_known_never_zero(platform, orchestrator, clock):
    row, steps, _, _ = await _run(platform, orchestrator, clock, [_write(PLAIN), "Wrote hello.txt."], usage=PLAIN)
    assert row.cache_read_input_tokens is None and row.cache_creation_input_tokens is None
    assert all("cache_read_input_tokens" not in s for s in steps)
    assert json.loads(row.tools_sent_json), "the tools are saved whatever the provider reports"
    assert row.model_ms == 2 * CALL_MS


async def test_no_text_of_any_part_is_on_the_row_or_in_a_step_event(platform, orchestrator, clock):
    about = f"My codeword is {MARK_MEMORY}. " + "I keep the books for a small bakery in town. " * 30
    ProfileStore(platform.engine).save({"about": about})
    row, steps, inner, _ = await _run(
        platform, orchestrator, clock,
        [_write(CACHED, content=MARK_ARGUMENT), "Wrote hello.txt."],
        task=f"Write hello.txt for {MARK_TASK}.",
    )
    # The marks really were sent to the model...
    first = inner.calls[0]["system"] + json.dumps(inner.calls[0]["messages"])
    assert MARK_TASK in first and MARK_MEMORY in first
    assert (Path(orchestrator.get_session(row.session_id).workspace_path) / "hello.txt").read_text(
        encoding="utf-8") == MARK_ARGUMENT
    # ...and the row's new columns and every step event hold none of them.
    used = json.dumps({k: getattr(row, k) for k in (
        "parts_json", "tools_sent_json", "tool_spec_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens", "model_ms", "model_calls", "first_word_ms")})
    for mark in MARKS:
        assert mark not in used and mark not in json.dumps(steps, default=str), mark
    record = json.loads(row.parts_json)
    # The profile is named as what was remembered (its whole size, never its words)...
    assert record["parts"]["memory"] >= estimate_tokens(about.strip())
    # ...and is not counted a second time as instructions.
    assert record["parts"]["system"] <= estimate_tokens(inner.calls[0]["system"]) - estimate_tokens(about.strip())
    assert set(record) <= {"v", "parts", "used", "window", "window_known", "percent", "left_out", "tools",
                           "sent", "cache_input"}
    for step in steps:
        added = set(step) - {"run_id", "step", "provider", "model", "input_tokens", "output_tokens",
                             "cost_usd", "task_class", "list_price_equivalent", "role"}
        assert added <= {"model_ms", "cache_read_input_tokens", "cache_creation_input_tokens", "tools",
                         "tool_spec_tokens", "sent"}, added
        assert set(step["sent"]) == {"system", "messages"}
        assert all(isinstance(v, int) for v in step["sent"].values())


async def test_a_split_task_counts_its_planning_checking_and_write_up_calls(platform, orchestrator, clock):
    """A split task's one-shot calls (the plan, a step's check, the write-up)
    add their tokens to the run: their cache counts and the model's time are
    added too. This model is served through the step-by-step tool scaffold,
    which passes no cache figure on for the loop's own steps: the record says
    how much input the counts are parts of, so a share is never read against
    calls that reported nothing."""
    script = [PLAN, WRITE_CALL, "Wrote hello.txt with the greeting.",
              "A greeting file named hello.txt was written.", '{"pass": true, "reason": "it says so"}',
              "hello.txt was written and the work was summarized."]
    row, steps, inner, _ = await _run(platform, orchestrator, clock, script, task=TASK,
                                      provider="local-r6", tool_use=False)
    n = len(inner.calls)
    assert n == len(script)
    assert row.input_tokens == n * 900
    one_shots = [s for s in steps if "tools" not in s]      # the plan, the check, the write-up
    loop_steps = [s for s in steps if "tools" in s]
    assert (len(one_shots), len(loop_steps)) == (3, 3)
    assert all(s["cache_read_input_tokens"] == 600 for s in one_shots)
    assert all("cache_read_input_tokens" not in s for s in loop_steps)
    assert (row.cache_read_input_tokens, row.cache_creation_input_tokens) == (3 * 600, 3 * 100)
    assert json.loads(row.parts_json)["cache_input"] == 3 * 900
    # Every call of the run was timed, the ones after its last loop step too.
    assert (row.model_calls, row.model_ms) == (n, n * CALL_MS)


# --------------------------------------------------------------------------- #
# It only looks; and it never costs the run
# --------------------------------------------------------------------------- #
async def test_what_the_model_is_sent_is_the_same_with_and_without_the_record(tmp_path, clock, monkeypatch):
    async def once(record: bool):
        # Each run in a home of its own: a second run in one home is told
        # about the first.
        platform = build_platform(str(tmp_path / ("on" if record else "off")))
        platform.router._clock = clock
        orchestrator = Orchestrator(platform)
        ProfileStore(platform.engine).save({"about": "I keep the books for a bakery."})
        with monkeypatch.context() as m:
            if not record:
                m.setattr(run_facts, "note_step", lambda *a, **k: {})
                m.setattr(run_facts, "note_call", lambda *a, **k: {})
                m.setattr(run_facts, "note_prompt", lambda *a, **k: None)
                m.setattr(run_facts, "settle", lambda *a, **k: None)
                m.setattr(run_facts.PromptParts, "claim", lambda *a, **k: None)
                m.setattr(run_facts.PromptParts, "skip", lambda *a, **k: None)
            row, _steps, inner, session = await _run(
                platform, orchestrator, clock, [_write(CACHED), "Wrote hello.txt."],
            )
            assert (row.parts_json is not None) is record
            return inner.calls, session, str(platform.config.home)

    (on, s_on, h_on), (off, s_off, h_off) = await once(True), await once(False)
    assert len(on) == len(off) == 2

    def same(text: str, session, home: str) -> str:
        # Two runs differ only in their own ids and folders.
        return (text.replace(str(session.workspace_path), "<folder>").replace(home, "<home>")
                .replace(str(tmp_path / "on"), "<root>").replace(str(tmp_path / "off"), "<root>")
                .replace(session.id, "<session>"))

    for a, b in zip(on, off):
        assert same(a["system"], s_on, h_on) == same(b["system"], s_off, h_off)
        assert [m[0] for m in a["messages"]] == [m[0] for m in b["messages"]]
        assert ([same(m[1], s_on, h_on) for m in a["messages"]]
                == [same(m[1], s_off, h_off) for m in b["messages"]])
        assert json.dumps(a["tools"], sort_keys=True, default=str) == json.dumps(b["tools"], sort_keys=True,
                                                                              default=str)


async def test_a_measure_that_breaks_does_not_fail_the_run(platform, orchestrator, clock, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the measure broke")

    monkeypatch.setattr(B, "sent_record", boom)
    monkeypatch.setattr(B, "tool_origins", boom)
    row, steps, _, _ = await _run(platform, orchestrator, clock, [_write(CACHED), "Wrote hello.txt."])
    assert row.steps == 2 and row.input_tokens == 1800
    # What could not be measured is ABSENT; what could be is still there.
    record = json.loads(row.parts_json)
    assert "parts" not in record and "tools" not in record
    assert record["sent"]["steps"] == 2
    assert len(steps) == 2 and all(s["model_ms"] == CALL_MS for s in steps)


def test_note_step_never_raises_and_says_nothing_it_could_not_measure():
    class Run:
        id = "run-x"

    assert run_facts.note_step(None, None, usage={}, tool_specs=[], system="", messages=[]) == {}
    with reply_facts.scope() as facts:
        out = run_facts.note_step(Run(), facts, usage=object(), tool_specs=None, system=None, messages=None)
    assert "cache_read_input_tokens" not in out and "model_ms" not in out
    assert out["tools"] == 0 and out["tool_spec_tokens"] == 0
    # A chat reply's facts are never a run's: a step is not noted on them.
    reply = reply_facts.begin()
    assert run_facts.note_step(Run(), reply, usage=CACHED, tool_specs=[], system="s", messages=[]) == {}
    assert reply.parts is None and reply.run_state is None
    assert run_facts.note_call(None, CACHED) == {} and run_facts.note_call(Run(), {"input_tokens": 3}) == {}


# --------------------------------------------------------------------------- #
# Naming the parts of a prompt as it is built
# --------------------------------------------------------------------------- #
def test_prompt_parts_names_what_was_appended_and_only_that():
    p = run_facts.PromptParts("ROLE")
    prompt = "ROLE" + "\n\nskill text"
    p.claim("skills", prompt)
    prompt += "\n\nrule lines"
    p.skip(prompt)                     # instructions: claimed by nobody
    prompt += "\n\nwhat is remembered"
    p.claim("memory", prompt)
    p.claim("memory", prompt)          # nothing new: nothing claimed
    assert p.pieces == {"skills": ["\n\nskill text"], "memory": ["\n\nwhat is remembered"]}
    # A rewrite (not an append) is claimed by nobody, and naming goes on after it.
    prompt = "REWRITTEN " + prompt
    p.claim("memory", prompt)
    prompt += "\n\nproject brief"
    p.claim("project", prompt)
    assert p.pieces["memory"] == ["\n\nwhat is remembered"] and p.pieces["project"] == ["\n\nproject brief"]
    # An unknown part name and a non-string never raise and name nothing.
    p.claim("nonsense", prompt + "x")
    p.claim("memory", None)
    assert "nonsense" not in p.pieces


# --------------------------------------------------------------------------- #
# Whose model call is it
# --------------------------------------------------------------------------- #
def test_a_model_call_made_inside_a_tool_is_not_the_replys_own_time():
    facts = reply_facts.begin()
    reply_facts.note_model_time(2.0)
    with call_ledger.tool_scope("view_image", None):
        reply_facts.note_model_time(5.0)          # the tool's call: its own row
    with call_ledger.tool_scope("mcp__ledger__post", None):
        reply_facts.note_model_time(7.0)          # an app's tool: it bills itself
    assert facts.fields() == {"model_calls": 1, "model_ms": 2000}


def test_a_run_inside_a_reply_has_its_own_facts_and_gives_the_replys_back():
    outer = reply_facts.begin()
    with call_ledger.counted_scope(), reply_facts.scope() as inner:
        reply_facts.note_model_time(1.0)          # the run's own step
        with call_ledger.tool_scope("mcp__ledger__post", None):
            reply_facts.note_model_time(9.0)      # an app's tool inside the run
        with call_ledger.tool_scope("view_image", None):
            reply_facts.note_model_time(9.0)
        assert reply_facts.current() is inner
    assert reply_facts.current() is outer
    assert inner.fields() == {"model_calls": 1, "model_ms": 1000}
    assert outer.fields() == {}


def test_settle_writes_only_on_the_run_the_facts_belong_to():
    class Run:
        def __init__(self, rid):
            self.id, self.model_ms, self.model_calls, self.first_word_ms = rid, None, None, None

    facts = reply_facts.ReplyFacts()
    facts.note_model_time(0.25)
    mine, other = Run("run-a"), Run("run-b")
    run_facts.note_prompt(facts, None, mine)
    run_facts.settle(other, facts)
    run_facts.settle(mine, facts)
    assert (mine.model_ms, mine.model_calls) == (250, 1) and mine.first_word_ms is None
    assert (other.model_ms, other.model_calls) == (None, None)


# --------------------------------------------------------------------------- #
# The seams (source pins)
# --------------------------------------------------------------------------- #
def test_the_runtime_keeps_the_step_before_its_own_save_and_opens_the_facts_inside_the_counted_scope():
    src = Path(runtime_mod.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert src.count("_call_ledger.counted_scope(), \\\n                        _reply_facts.scope():") == 1
    step = src.index("_step_used = _run_facts.note_step(")
    save = src.index("await asyncio.to_thread(self._save, run)", step)
    event = src.index("EventType.LLM_COMPLETED,", step)
    assert step < save < event, "the step is on the row before the step's own save, and on its event"
    assert src.count("**_step_used,") == 1
    router = (Path(runtime_mod.__file__).resolve().parents[1] / "providers" / "router.py").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    assert router.count("_note_time(self._clock() - t0)") == 1          # a completion
    assert router.count("_note_time(self._clock() - _t0)") == 2         # a stream's final frame, both paths
