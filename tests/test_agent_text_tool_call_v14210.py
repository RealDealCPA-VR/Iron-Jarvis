"""v1.421.0 (Wave 1, A5) — A TOOL CALL A MODEL WRITES AS TEXT IS HANDLED IN
AGENT RUNS, NOT ONLY IN A CODING SESSION'S TURN.

The owner's helpers run on a local model (glm-5.3-flash). When such a model
writes its call as TEXT (GLM's ``<tool_call>read_file …``, a Qwen / Hermes
``<tool_call>{"name": …}``, a Claude-style ``<invoke name=…>``, a JSON blob)
instead of making it, the chat lanes' harness turn tells it ONCE to call the
tool itself (``HarnessTurn.wants_nudge``, v1.366.0) — but the agent runtime
had nothing: a helper, a teammate, any agent run on such a model ENDED with
the markup as its "report". Now the perceive→act loop (``agents/runtime``)
gives the same note: the text is never executed, the answer and the note go
on the transcript as plain turns, and the run goes on — only for a tool the
step OFFERED, with a step left, never twice in a row, at most
``TEXT_CALL_NUDGES_PER_RUN`` (2) a run. A text-only run and a normal final
answer are exactly as before. The decomposed lane's mini-loops run the same
method, and their count spans the run through the caller's breaker state.

Driven through the REAL orchestrator + runtime on a real platform; only the
model is scripted.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents import runtime as RT
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, ToolInvocation
from iron_jarvis.core.nudges import NUDGE_KINDS, TEXT_TOOL_CALL
from iron_jarvis.harness.mode import TEXT_TOOL_CALL_NUDGE, named_text_tool_call, text_tool_call
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

ROOT = Path(__file__).resolve().parents[1]

#: The four shapes a model writes a call in (the chat lanes' reading).
GLM = "<tool_call>list_files\n<arg_key>path</arg_key>\n<arg_value>.</arg_value>\n</tool_call>"
HERMES = '<tool_call>\n{"name": "list_files", "arguments": {"path": "."}}\n</tool_call>'
INVOKE = '<function_calls>\n<invoke name="list_files">\n<parameter name="path">.</parameter>\n</invoke>\n</function_calls>'
JSON_BLOB = 'I will look first. {"name": "list_files", "arguments": {"path": "."}}'

LIST = [ToolCall(id="l1", name="list_files", arguments={"path": "."})]
DONE = "There is one file, notes.txt."


def _agent(tmp_path, steps: list, **session_kw):
    """Run one agent job on a scripted model. ``steps``: per model call, a
    reply text or a list of ToolCalls. Returns (session, calls, events,
    platform, offered) — ``calls`` the messages each model call got,
    ``offered`` the tool names each call was shown."""
    p = build_platform(str(tmp_path / "home"))
    calls: list[list] = []
    offered: list[set] = []
    events: list[dict] = []
    real = p.event_bus.publish

    async def spy(event_type, payload=None, *a, **kw):
        if str(getattr(event_type, "value", event_type)) == str(EventType.AUTONOMY_NUDGED):
            events.append({**dict(payload or {}), "_session": kw.get("session_id")})
        return await real(event_type, payload, *a, **kw)

    p.event_bus.publish = spy  # type: ignore[method-assign]
    script = list(steps)

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            calls.append(list(messages))
            offered.append({str(t.get("name") or "") for t in (tools or []) if isinstance(t, dict)})
            move = script.pop(0) if script else "never reached"
            if isinstance(move, list):
                return LLMResponse(tool_calls=move, finish_reason="tool_use")
            return LLMResponse(text=move, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session(
            "list the files and say what is there", provider="mock", model="mock-1", **session_kw
        )
        return await orch.run_session(s.id)

    s = asyncio.run(go())
    return s, calls, events, p, offered


def _tool_rows(p, session_id: str) -> list[str]:
    with session_scope(p.engine) as db:
        return [r.tool for r in db.exec(select(ToolInvocation).where(ToolInvocation.session_id == session_id)).all()]


def _nudges_in(messages) -> int:
    return sum(1 for m in messages if m.role == "user" and m.content == TEXT_TOOL_CALL_NUDGE)


# --------------------------------------------------------------------------- #
# the reading
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [GLM, HERMES, INVOKE, JSON_BLOB, "<function=list_files>\n<parameter=path>.</parameter>\n</function>"])
def test_every_shape_a_model_writes_a_call_in_names_its_offered_tool(text):
    assert named_text_tool_call(text, {"list_files", "read_file"}) == "list_files"


@pytest.mark.parametrize(
    "text",
    [
        # A report ABOUT the format, naming no offered tool (a reviewer of
        # tool-call parsing code, notes on a model's quirks).
        "GLM writes its calls as `<tool_call>name <arg_key>…` tags; the parser now reads them.",
        "The fix handles `<invoke name=...>` blocks too.",
        # A call to a tool this step was NOT offered.
        "<tool_call>bash\n<arg_key>command</arg_key>\n<arg_value>ls</arg_value>\n</tool_call>",
        '<tool_call>{"name": "shell", "arguments": {"command": "ls"}}</tool_call>',
        # Ordinary answers.
        DONE,
        "",
    ],
)
def test_a_report_about_the_format_or_a_call_to_a_tool_not_offered_is_not_a_call(text):
    assert named_text_tool_call(text, {"list_files", "read_file"}) == ""


def test_the_runtimes_reading_is_stricter_than_the_lanes_but_agrees_on_every_real_call():
    """The lanes' ``text_tool_call`` reads a tag whatever it names; the agent
    runtime's reading also needs the named tool to be offered — so on every
    real call shape both say yes, and on a quoted tag only the lanes do."""
    for text in (GLM, HERMES, INVOKE, JSON_BLOB):
        assert text_tool_call(text, {"list_files"}) and named_text_tool_call(text, {"list_files"})
    quoted = "GLM writes `<tool_call>` tags."
    assert text_tool_call(quoted, {"list_files"}) == "tag"
    assert named_text_tool_call(quoted, {"list_files"}) == ""


def test_the_bound_never_twice_in_a_row_two_a_run_and_a_step_left():
    nudge = LLMMessage(role="user", content=TEXT_TOOL_CALL_NUDGE)
    task = LLMMessage(role="user", content="task")
    said = LLMMessage(role="assistant", content=GLM)
    offered = {"list_files"}

    def wants(messages, state=None, steps_left=5, off=offered):
        return RT._text_call_nudge(GLM, off, messages, steps_left=steps_left,
                                   state={} if state is None else state)

    assert wants([task]) == "list_files"  # the control
    assert wants([task], steps_left=0) == ""  # the call needs a step to happen in
    assert wants([task], off=set()) == ""  # a text-only run cannot call a tool
    assert wants([task, said, nudge]) == ""  # never twice in a row
    # Two a run: counted in the caller's state...
    assert wants([task], state={RT._TEXT_CALL_KEY: 2}) == ""
    assert wants([task], state={RT._TEXT_CALL_KEY: 1}) == "list_files"
    # ...AND in the run's own messages (a resumed transcript carries them).
    tool_turn = LLMMessage(role="assistant", content="", tool_calls=LIST)
    result = LLMMessage(role="tool", content="notes.txt", tool_call_id="l1", name="list_files")
    resumed = [task, said, nudge, tool_turn, result, said, nudge, tool_turn, result]
    assert wants(resumed) == ""
    assert RT.TEXT_CALL_NUDGES_PER_RUN == 2
    # The count is taken only for a nudge that is given.
    st: dict = {}
    RT._text_call_nudge(DONE, offered, [task], steps_left=5, state=st)
    assert st == {}
    RT._text_call_nudge(GLM, offered, [task], steps_left=5, state=st)
    assert st == {RT._TEXT_CALL_KEY: 1}


def test_the_new_kind_is_a_system_nudge_and_the_autonomy_card_words_it():
    """``core/nudges`` publishes only its own kinds, and the Autonomy card
    (``dashboard/lib/autonomyMetrics.ts`` SYSTEM_WORDS) words every one — a
    kind with no words reads "1 other" there."""
    assert TEXT_TOOL_CALL in NUDGE_KINDS
    src = (ROOT / "dashboard" / "lib" / "autonomyMetrics.ts").read_bytes().decode("utf-8").replace("\r\n", "\n")
    block = src.split("const SYSTEM_WORDS", 1)[1].split("};", 1)[0]
    for kind in NUDGE_KINDS:
        assert f"  {kind}:" in block, kind


# --------------------------------------------------------------------------- #
# the agent runtime (flat lane), end to end
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("written", [GLM, HERMES, INVOKE, JSON_BLOB])
def test_a_call_written_as_text_gets_one_note_and_the_run_does_the_work(tmp_path, written):
    s, calls, events, p, offered = _agent(tmp_path, [written, LIST, DONE])
    assert "list_files" in offered[0]
    assert len(calls) == 3
    # The answer and ONE note go on the transcript as plain turns...
    assert calls[1][-2].role == "assistant" and calls[1][-2].content == written
    assert not calls[1][-2].tool_calls  # no tool_use left without its result (v1.152.0)
    assert calls[1][-1].role == "user" and calls[1][-1].content == TEXT_TOOL_CALL_NUDGE
    # ...and the next step made the call for real; the run's answer is the
    # final one, never the markup.
    assert any(m.role == "tool" for m in calls[2])
    assert s.summary == DONE and s.status.value == "completed"
    # The text was NEVER executed: the ledger holds the one real call.
    assert _tool_rows(p, s.id) == ["list_files"]
    assert [(e["kind"], e["surface"], e["tool"], e["_session"]) for e in events] == [
        (TEXT_TOOL_CALL, "agent", "list_files", s.id)
    ]


def test_never_twice_in_a_row_the_second_text_call_ends_the_run_as_before(tmp_path):
    again = "<tool_call>list_files\n</tool_call>"
    s, calls, events, p, _ = _agent(tmp_path, [GLM, again, "never reached"])
    assert len(calls) == 2
    assert s.summary == again  # the old ending: the reply is the answer
    assert len(events) == 1 and _tool_rows(p, s.id) == []


def test_at_most_two_a_run(tmp_path):
    s, calls, events, p, _ = _agent(tmp_path, [GLM, LIST, HERMES, LIST, INVOKE, "never reached"])
    assert len(calls) == 5
    assert _nudges_in(calls[-1]) == 2  # the third written call got no note
    assert s.summary == INVOKE
    assert len(events) == 2 and _tool_rows(p, s.id) == ["list_files", "list_files"]


def test_no_note_on_the_last_step(tmp_path):
    s, calls, events, _, _ = _agent(tmp_path, [GLM], max_steps=1)
    assert len(calls) == 1 and not events and s.summary == GLM


def test_one_step_left_is_enough(tmp_path):
    s, calls, events, _, _ = _agent(tmp_path, [GLM, DONE], max_steps=2)
    assert len(calls) == 2 and len(events) == 1 and s.summary == DONE


def test_a_normal_final_answer_is_exactly_as_before(tmp_path):
    s, calls, events, p, _ = _agent(tmp_path, [DONE])
    assert len(calls) == 1 and not events and s.summary == DONE
    assert _nudges_in(calls[0]) == 0


def test_a_report_quoting_the_format_is_its_answer(tmp_path):
    report = "Reviewed: the parser now reads GLM's `<tool_call>` tags and Claude's `<invoke>` blocks."
    s, calls, events, _, _ = _agent(tmp_path, [report])
    assert len(calls) == 1 and not events and s.summary == report


def test_a_text_only_run_is_exactly_as_before(tmp_path):
    """A run with no tools armed cannot call one: "call the tool itself"
    would name a tool it does not have. One model call, the text as its
    answer — the pre-v1.421.0 run byte for byte."""
    run, calls, offered = _direct_run(
        tmp_path, [GLM, "never reached"], lambda d: dataclasses.replace(d, tools=[]),
    )
    assert offered == [set()]
    assert len(calls) == 1 and run.result == GLM
    assert _nudges_in(calls[0]) == 0


def _direct_run(tmp_path, steps: list, shape=None, parent_id=None):
    """``AgentRuntime.run`` on a scripted model, under the session's own
    definition (``shape`` may change it). Returns (run, calls, offered)."""
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition

    p = build_platform(str(tmp_path / "home"))
    calls: list = []
    offered: list = []
    script = list(steps)

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            calls.append(list(messages))
            offered.append({str(t.get("name") or "") for t in (tools or []) if isinstance(t, dict)})
            move = script.pop(0) if script else "never reached"
            if isinstance(move, list):
                return LLMResponse(tool_calls=move, finish_reason="tool_use")
            return LLMResponse(text=move, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("list the files", provider="mock", model="mock-1")
        defn = get_agent_definition(s.agent_type)
        return await AgentRuntime(p).run(s, shape(defn) if shape else defn, parent_id=parent_id)

    return asyncio.run(go()), calls, offered


def test_a_child_run_gets_the_note_too(tmp_path):
    """A helper, a teammate, a delegated child — the runs this is FOR (the
    closing offer skips them; this does not)."""
    run, calls, _ = _direct_run(tmp_path, [GLM, LIST, DONE], parent_id="parent-run")
    assert len(calls) == 3 and run.result == DONE
    assert calls[1][-1].content == TEXT_TOOL_CALL_NUDGE


def test_the_flat_lanes_kept_conversation_holds_the_note_before_the_real_call(tmp_path):
    """``agents/transcript.save`` (the flat lane): the step that asks for
    tools saves the conversation — with the written call and its note in it,
    as plain turns, so a resume sees the record (and never nudges again past
    the cap)."""
    from iron_jarvis.agents.transcript import load_for_resume

    s, calls, events, p, _ = _agent(tmp_path, [GLM, LIST, DONE])
    with session_scope(p.engine) as db:
        run_id = db.exec(select(AgentRun.id).where(AgentRun.session_id == s.id)).first()
    kept = load_for_resume(p.engine, run_id)
    assert kept is not None
    roles = [(m.role, m.content == TEXT_TOOL_CALL_NUDGE, bool(m.tool_calls)) for m in kept]
    i = roles.index(("user", True, False))
    assert roles[i - 1] == ("assistant", False, False)
    assert roles[i + 1] == ("assistant", False, True)
    assert roles[i + 2][0] == "tool"


# --------------------------------------------------------------------------- #
# the decomposed lane shares the method; its count spans the run
# --------------------------------------------------------------------------- #


def test_the_decomposed_lanes_mini_loops_share_the_rule_and_one_count(tmp_path):
    """``agents/decompose`` runs each step (and its retry) through the SAME
    ``perceive_act`` with ONE breaker-state dict for the whole run and a fresh
    message list per step. The rule applies in a mini-loop, and the two-a-run
    cap holds across them: a later step's written call ends that step."""
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition

    p = build_platform(str(tmp_path / "home"))
    script = [GLM, LIST, HERMES, LIST, DONE, INVOKE]
    seen: list = []

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append(list(messages))
            move = script.pop(0) if script else "never reached"
            if isinstance(move, list):
                return LLMResponse(tool_calls=move, finish_reason="tool_use")
            return LLMResponse(text=move, finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("list the files", provider="mock", model="mock-1")
        rt = AgentRuntime(p)
        defn = get_agent_definition(s.agent_type)
        run = AgentRun(session_id=s.id, agent_type=s.agent_type)
        with session_scope(p.engine) as db:
            db.add(run)
        specs = p.registry.specs(["list_files"])
        breaker: dict = {}
        out = []
        for step in ("step one", "step two"):
            out.append(await rt.perceive_act(
                run, s, defn, system_prompt="sys", messages=[LLMMessage(role="user", content=step)],
                tool_specs=specs, session_allow=set(), sink=None, max_steps=6,
                breaker_state=breaker,
            ))
        return out, breaker

    (first, second), breaker = asyncio.run(go())
    assert first == (True, DONE)  # step one: two notes, two real calls, an answer
    assert second == (True, INVOKE)  # step two: the cap is reached — its answer as before
    assert breaker[RT._TEXT_CALL_KEY] == 2
    assert _nudges_in(seen[-1]) == 0  # step two's fresh messages never got a note


def test_decompose_runs_its_steps_through_perceive_act_with_the_runs_breaker_state():
    """The seam the test above stands in for (source pin): the mini-loop is
    the runtime's own ``perceive_act`` with the run's one ``breaker_state``."""
    src = (ROOT / "src" / "iron_jarvis" / "agents" / "decompose.py").read_bytes().decode("utf-8")
    src = src.replace("\r\n", "\n")
    body = src.split("finished, text = await runtime.perceive_act(", 1)[1][:1200]
    body = body.split("\n            )\n", 1)[0]
    assert "breaker_state=breaker_state," in body


# --------------------------------------------------------------------------- #
# the chat lanes count their nudge on the Autonomy card too
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_harness_turns_nudge_is_counted_like_the_runtimes(tmp_path, monkeypatch, lane):
    """Both chat lanes publish ONE ``autonomy.nudged`` of the same kind at
    their ``wants_nudge`` seam (lock-step), so the card counts a coding
    session's nudge and an agent run's alike."""
    from tests._fakes import harness_lane as H

    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("x = 1", encoding="utf-8")
    H.wire(platform, monkeypatch, ["<tool_call>list_filespath</tool_call>", [H.call("list_files", "l1", path=".")]])
    events: list[dict] = []
    real = platform.event_bus.publish

    async def spy(event_type, payload=None, *a, **kw):
        if str(getattr(event_type, "value", event_type)) == str(EventType.AUTONOMY_NUDGED):
            events.append({**dict(payload or {}), "_session": kw.get("session_id")})
        return await real(event_type, payload, *a, **kw)

    monkeypatch.setattr(platform.event_bus, "publish", spy)
    b = H.body(ws, "list the files")
    await (H.stream if lane == "stream" else H.post)(platform, b)
    assert [(e["kind"], e["surface"], e["call"], e["round"], e["_session"]) for e in events] == [
        (TEXT_TOOL_CALL, "build", "tag", 1, H.SESSION)
    ]


def test_the_streamed_markup_is_cleared_for_whoever_watches_the_run(tmp_path):
    """The live view had the written call streamed into it; the note clears
    it (``reset``, reason ``text_tool_call`` — the stream lane's frame) so the
    run's next words do not land after the markup."""
    from iron_jarvis.agents.runtime import AgentRuntime
    from iron_jarvis.agents.types import get_agent_definition

    p = build_platform(str(tmp_path / "home"))
    script = [GLM, DONE]

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            move = script.pop(0) if script else "never reached"
            return LLMResponse(text=move, finish_reason="stop")

    class Sink:
        def __init__(self) -> None:
            self.calls: list = []

        def __getattr__(self, name):
            return lambda *a, **k: self.calls.append((name, a))

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)
    sink = Sink()

    async def go():
        s = await orch.create_session("list the files", provider="mock", model="mock-1")
        run = AgentRun(session_id=s.id, agent_type=s.agent_type)
        with session_scope(p.engine) as db:
            db.add(run)
        return await AgentRuntime(p).perceive_act(
            run, s, get_agent_definition(s.agent_type), system_prompt="sys",
            messages=[LLMMessage(role="user", content="list the files")],
            tool_specs=p.registry.specs(["list_files"]), session_allow=set(), sink=sink, max_steps=4,
        )

    assert asyncio.run(go()) == (True, DONE)
    names = [n for n, _ in sink.calls]
    assert ("reset", ("text_tool_call",)) in sink.calls
    assert names.index("reset") < len(names) - 1  # before the next step's words
