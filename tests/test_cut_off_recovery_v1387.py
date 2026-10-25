"""v1.387.0 — A REPLY CUT OFF AT THE OUTPUT LIMIT IS RECOVERED, NOT ENDED.

The benchmark's glm runs (three, two versions): the model read the files, its
next reply spent the whole output budget and came back with NO text and NO
call (``finish_reason: "length"``), and the lane — reading that as "stopped
without writing a reply" — asked for a NO-TOOLS final answer
(``FINAL_ANSWER_INSTRUCTION``), so the model pasted the code into chat and the
task failed. Now such a round gets ONE plain note and the turn goes on (at most
two notes, then an honest end); a reply cut with TEXT and no call keeps the
``truncated`` + Continue behaviour.

Driven through the REAL lanes (both: stream and POST, lock-step), the REAL
agent runtime, and — for the replay — the REAL adapter against a fake
OpenAI-compatible server with the task's own hidden check. Only the model is
scripted. Every "recovered" pin is RED on the code before v1.387.0 (the
no-tools nudge was sent instead of the note; the agent run ended).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import FINAL_ANSWER_INSTRUCTION, run_chat_turn
from iron_jarvis.daemon.routes.chat import _approvals
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

WHO = ("anthropic", "claude-sonnet-4-5")
SESSION = "build:cut-off-test"
NOTE_HEAD = "Your last reply was cut off at the output limit"


def _cut(**kw) -> LLMResponse:
    """A round that ended at the output limit with nothing usable (the glm
    evidence: no text, no call)."""
    return LLMResponse(text="", finish_reason="max_tokens",
                       usage={"input_tokens": 3, "output_tokens": 4096}, **kw)


class Model:
    """A scripted model on the router's ``stream()`` / ``complete()``: a move is
    a list of ToolCalls, a reply text, an ``LLMResponse``, or a callable
    ``(model) -> move``. Past the script (or on a tool-less call) it answers
    "Done."."""

    def __init__(self, moves: list) -> None:
        self.moves = list(moves)
        self.calls: list[dict] = []

    async def _next(self, system, messages, tools) -> LLMResponse:
        self.calls.append({"tools": [t["name"] for t in tools or []], "messages": list(messages)})
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if callable(move):
            move = move(self)
            if inspect.isawaitable(move):
                move = await move
        if isinstance(move, LLMResponse):
            return move
        usage = {"input_tokens": 3, "output_tokens": 1}
        if isinstance(move, str):
            return LLMResponse(text=move, usage=usage)
        return LLMResponse(text="", tool_calls=list(move), finish_reason="tool_use", usage=usage)

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        resp = await self._next(system, messages, tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        return RouteResult(await self._next(system, messages, tools), WHO[0], WHO[1])

    def users(self) -> list[str]:
        """Every distinct user-role line any call was sent, in first-seen order."""
        seen: list[str] = []
        for call in self.calls:
            for m in call["messages"]:
                if m.role == "user" and m.content not in seen:
                    seen.append(m.content)
        return seen

    def notes(self) -> list[str]:
        return [u for u in self.users() if str(u).startswith(NOTE_HEAD)]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_bytes(b"the spec\n")

    def wire(moves: list) -> Model:
        model = Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


def _body(ws: Path, text: str = "write calc.py from the spec in notes.txt", **kw) -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider=WHO[0], model=WHO[1], workspace_dir=str(ws),
        approval_mode=kw.pop("approval_mode", "yolo"), **kw,
    )


def _frames(chunk: str) -> list[tuple[str, dict]]:
    out = []
    for block in chunk.split("\n\n"):
        event, data = "", []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if event:
            out.append((event, json.loads("".join(data)) if data else {}))
    return out


async def _lane(lane: str, platform, body, *, harness="generic", **kw) -> dict:
    if lane == "stream":
        approvals = _approvals(SimpleNamespace(platform=platform))
        gen = await stream_chat_turn(
            platform, BUILTIN_PERSONAS, body, harness=harness, session_id=SESSION,
            round_budget=kw.pop("round_budget", 30), stay_in_chat=True, **kw,
        )
        done = None
        async for chunk in gen:
            for event, data in _frames(chunk):
                if event == "approval":
                    approvals.resolve(str(data["id"]), "once")
                if event == "done":
                    done = data
        return done
    return await run_chat_turn(
        platform, BUILTIN_PERSONAS, body, harness=harness, session_id=SESSION,
        round_budget=kw.pop("round_budget", 30), stay_in_chat=True, **kw,
    )


def _write(path: str, content: str, cid: str = "w1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="write_file", arguments={"path": path, "content": content})]


def _edit(path: str, old: str, new: str, cid: str = "e1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="edit_file", arguments={"path": path, "old": old, "new": new})]


def _read(path: str, cid: str = "r1") -> list[ToolCall]:
    return [ToolCall(id=cid, name="read_file", arguments={"path": path})]


def _never_the_no_tools_answer(model: Model) -> None:
    assert FINAL_ANSWER_INSTRUCTION not in model.users()
    assert all(c["tools"] for c in model.calls), "a tool-less call was made"


# --------------------------------------------------------------------------- #
# 1. a cut-off round gets ONE note and the turn goes on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_round_cut_off_with_nothing_gets_one_note_and_the_turn_goes_on(env, lane):
    """RED before v1.387.0: the lane sent FINAL_ANSWER_INSTRUCTION with NO
    tools after the cut round, the model answered in words, and calc.py was
    never written."""
    platform, ws, wire = env
    model = wire([_read("notes.txt"), _cut(), _write("calc.py", "X = 1\n"), "calc.py is written."])
    done = await _lane(lane, platform, _body(ws))
    notes = model.notes()
    assert len(notes) == 1
    assert "before it wrote any text or a complete tool call, so nothing was run" in notes[0]
    assert "Continue the task with your tools. Write a large file in parts" in notes[0]
    # the note reached the model WITH its tools, as the newest message
    third = model.calls[2]
    assert third["tools"] and third["messages"][-1].content == notes[0]
    _never_the_no_tools_answer(model)
    # (read as text: write_file writes this OS's newlines)
    assert (ws / "calc.py").read_text("utf-8") == "X = 1\n"
    assert done["reply"].startswith("calc.py is written.")
    assert done["truncated"] is False
    # the receipt says so, LAST in the route object
    assert list(done["route"])[-1] == "cut_off"
    assert done["route"]["cut_off"] == {"times": 1, "recovered": True}


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_cut_write_call_is_named_and_the_note_says_how_to_write_in_parts(env, lane):
    """The adapter names the call it never finished (``cut_calls``); the note
    names it, says nothing was run, and names the ARMED tools for the rest."""
    platform, ws, wire = env
    model = wire([
        _cut(cut_calls=["write_file"]),
        _write("calc.py", "PART = 1\n# more below\n"),
        _edit("calc.py", "# more below\n", "PART2 = 2\n"),
        "calc.py is written in two parts.",
    ])
    done = await _lane(lane, platform, _body(ws))
    (note,) = model.notes()
    assert "before its write_file call was complete, so nothing was run." in note
    assert "then add the rest with edit_file or apply_patch" in note
    _never_the_no_tools_answer(model)
    assert (ws / "calc.py").read_text("utf-8") == "PART = 1\nPART2 = 2\n"
    assert done["route"]["cut_off"] == {"times": 1, "recovered": True}


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_complete_calls_beside_a_cut_one_run_and_the_note_follows_their_results(env, lane):
    platform, ws, wire = env
    model = wire([
        _cut(tool_calls=_read("notes.txt"), cut_calls=["write_file"]),
        _write("calc.py", "X = 1\n"),
        "Done writing.",
    ])
    done = await _lane(lane, platform, _body(ws))
    msgs = model.calls[1]["messages"]
    # assistant (the read) → its result → the note, in that order
    assert msgs[-3].role == "assistant" and [c.name for c in msgs[-3].tool_calls] == ["read_file"]
    assert msgs[-2].role == "tool" and "the spec" in msgs[-2].content
    assert msgs[-1].role == "user" and msgs[-1].content.startswith(NOTE_HEAD)
    assert "so that call was not run (the other calls in it ran" in msgs[-1].content
    assert (ws / "calc.py").exists()
    assert done["route"]["cut_off"] == {"times": 1, "recovered": True}


# --------------------------------------------------------------------------- #
# 2. bounded: two notes, then the honest end
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_two_notes_then_the_turn_ends_saying_what_happened(env, lane):
    """RED before v1.387.0: the first cut already ended the turn with the
    no-tools answer. Now: two notes, and the third cut ends the turn with a
    sentence that says what happened — no extra model call, no Continue."""
    platform, ws, wire = env
    model = wire([_cut(), _cut(cut_calls=["write_file"]), _cut(cut_calls=["write_file"]), "never reached"])
    done = await _lane(lane, platform, _body(ws))
    assert len(model.notes()) == 2
    assert len(model.calls) == 3, "no fourth call: not a nudge, not a retry"
    _never_the_no_tools_answer(model)
    assert done["reply"].startswith(
        "The model's reply was cut off at its output limit 3 times in a row before its "
        "write_file call was complete, so nothing from that reply was run."
    )
    assert "Ask for the work in smaller pieces, or choose a model with a larger output limit." in done["reply"]
    assert done["truncated"] is False
    assert done["route"]["cut_off"] == {"times": 3, "recovered": False}


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_cut_on_the_last_round_ends_honestly(env, lane):
    platform, ws, wire = env
    model = wire([_read("notes.txt"), _cut()])
    done = await _lane(lane, platform, _body(ws), round_budget=2)
    assert model.notes() == []
    _never_the_no_tools_answer(model)
    assert "cut off at its output limit once before it wrote any text" in done["reply"]
    assert done["route"]["cut_off"] == {"times": 1, "recovered": False}


# --------------------------------------------------------------------------- #
# 3. everything else unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_long_text_answer_cut_at_the_limit_keeps_truncated_and_continue(env, lane):
    """v1.323.0, unchanged: a reply cut with TEXT and no call is a long
    answer — ``truncated`` true (the page's Continue), no note, no key."""
    platform, ws, wire = env
    model = wire([LLMResponse(text="Here is the long answer, part one …", finish_reason="max_tokens")])
    done = await _lane(lane, platform, _body(ws, "explain the spec at length"))
    assert model.notes() == []
    assert done["truncated"] is True
    assert "cut_off" not in done["route"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_turn_with_no_tools_keeps_the_final_answer_nudge(env, lane):
    """Not a tool turn (a plain chat that armed nothing): an empty reply at
    the limit still gets the v1.246.0 final-answer request, as before."""
    platform, _ws, _wire = env
    calls: list = []

    async def stream(*, provider=None, model=None, system="", messages=(), tools=None, **kw):
        calls.append({"tools": tools, "messages": list(messages)})
        resp = _cut() if len(calls) == 1 else LLMResponse(text="Hello!")
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}

    async def complete(*, provider=None, model=None, system="", messages=(), tools=None, **kw):
        calls.append({"tools": tools, "messages": list(messages)})
        return RouteResult(_cut() if len(calls) == 1 else LLMResponse(text="Hello!"), WHO[0], WHO[1])

    platform.router.stream = stream
    platform.router.complete = complete
    body = ChatBody(messages=[ChatMessageBody(role="user", content="hi")], provider=WHO[0], model=WHO[1])
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
        done = None
        async for chunk in gen:
            for event, data in _frames(chunk):
                if event == "done":
                    done = data
    else:
        done = await run_chat_turn(platform, BUILTIN_PERSONAS, body)
    # nothing ARMED — only the exit tools every chat turn offers
    names = {t["name"] for t in calls[0]["tools"] or []}
    assert names <= {"escalate_to_agent", "workflow_draft"}, names
    assert calls[1]["messages"][-1].content == FINAL_ANSWER_INSTRUCTION
    assert not calls[1]["tools"]
    assert done["reply"].startswith("Hello!")
    assert "cut_off" not in done["route"]


# --------------------------------------------------------------------------- #
# 4. the agent runtime has the same branch
# --------------------------------------------------------------------------- #
def _agent_run(tmp_path, script: list):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    p = build_platform(str(tmp_path / "home"))
    seen: list[list] = []

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            seen.append(list(messages))
            move = script.pop(0) if script else LLMResponse(text="Done.")
            return move

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("write out.txt", provider="mock", model="mock-1")
        return await orch.run_session(s.id)

    return asyncio.run(go()), seen


def test_an_agent_run_recovers_a_cut_off_step(tmp_path):
    """RED before v1.387.0: the empty cut step ended the run ("The model
    finished without writing a summary…") with out.txt never written."""
    session, seen = _agent_run(tmp_path, [
        LLMResponse(tool_calls=[ToolCall(id="l1", name="list_files", arguments={"path": "."})],
                    finish_reason="tool_use"),
        _cut(),
        LLMResponse(tool_calls=[ToolCall(id="w1", name="write_file",
                                         arguments={"path": "out.txt", "content": "hi"})],
                    finish_reason="tool_use"),
        LLMResponse(text="Wrote out.txt."),
    ])
    assert (session.summary or "").startswith("Wrote out.txt."), session.summary
    notes = [m.content for m in seen[2] if m.role == "user" and m.content.startswith(NOTE_HEAD)]
    assert len(notes) == 1 and "nothing was run" in notes[0]


def test_an_agent_run_ends_honestly_after_two_notes(tmp_path):
    """RED before v1.387.0: the first cut finished the run COMPLETED with
    "The model returned an empty answer". Now: two notes, then the run ends
    NOT finished, saying what happened."""
    from iron_jarvis.core.models import SessionStatus

    session, seen = _agent_run(tmp_path, [_cut(), _cut(), _cut(), LLMResponse(text="never")])
    assert len(seen) == 3
    assert session.status == SessionStatus.FAILED
    assert "cut off at its output limit 3 times in a row" in (session.summary or "")


# --------------------------------------------------------------------------- #
# 5. THE REPLAY — the glm evaluator run, through the real adapter and lane
# --------------------------------------------------------------------------- #
TASK_ID = "hard-expression-evaluator"
SOLUTION = (Path(__file__).parent / "fixtures" / "harness_bench_solutions" / TASK_ID / "calc.py.sol")
MARKER = "# (the parser and evaluate() follow)\n"


def _parts() -> tuple[str, str]:
    text = SOLUTION.read_text("utf-8")
    cut = text.index("class _Parser:")
    return text[:cut] + MARKER, text[cut:]


def _called(body: dict) -> list[str]:
    return [tc["function"]["name"] for m in body.get("messages") or [] if m.get("tool_calls")
            for tc in m["tool_calls"]]


def _replay_handler(first_try):
    """The glm run's shape as a server answers it: read the two files; then
    ``first_try(body)`` (the whole module in one go); after a cut-off note,
    the module in two parts; then the answer. The old lane's NO-TOOLS request
    is answered the way glm answered it: the code pasted into chat."""
    from tests.fixtures.fake_openai_endpoint_v1387 import calls_reply, text_reply

    part1, part2 = _parts()

    def handler(body: dict):
        if not body.get("tools"):
            return text_reply("I hadn't written the file yet — here is what I would create:\n\n"
                              "```python\n" + SOLUTION.read_text("utf-8") + "```")
        called = _called(body)
        noted = any(NOTE_HEAD in str(m.get("content") or "")
                    for m in body["messages"] if m.get("role") == "user")
        if "edit_file" in called or ("write_file" in called and not noted):
            return text_reply("calc.py is written and the tests pass.")
        if "write_file" in called:
            return calls_reply(("edit_file", {"path": "calc.py", "old": MARKER, "new": part2}))
        if noted:
            return calls_reply(("write_file", {"path": "calc.py", "content": part1}))
        if "read_file" in called:
            return first_try(body)
        return calls_reply(("read_file", {"path": "calc.py"}), ("read_file", {"path": "test_calc.py"}))

    return handler


async def _replay(tmp_path, first_try) -> tuple[dict, object, list[dict]]:
    from iron_jarvis.harness_bench.catalog import load_tasks
    from iron_jarvis.harness_bench.grading import grade
    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint

    task = {t.id: t for t in load_tasks()}[TASK_ID]
    ws = tmp_path / "work"
    shutil.copytree(task.repo_dir, ws)
    with FakeEndpoint(_replay_handler(first_try)) as srv:
        platform = build_platform(str(tmp_path / "home"))
        platform.config.custom_base_url = srv.url
        platform.config.custom_model = "glm-5.3-flash"
        platform.providers.configure_local(custom_base_url=srv.url, custom_model="glm-5.3-flash")
        body = ChatBody(
            messages=[ChatMessageBody(role="user", content=task.prompt)],
            provider="custom", model="glm-5.3-flash", workspace_dir=str(ws), approval_mode="yolo",
        )
        done = await _lane("stream", platform, body, round_budget=60)
        chats = srv.chats()
    result = grade(task, ws, tmp_path / "grade")
    return done, result, chats


async def test_replay_the_glm_evaluator_run_cut_off_then_recovered(tmp_path):
    """THE REPLAY (the brief's): the first attempt at the module is cut off
    with nothing usable — the evidence's shape — whatever the budget. RED
    before v1.387.0 (the control): the lane asked for a no-tools answer, the
    model pasted the code into chat, calc.py stayed the stub, and the task's
    hidden check FAILED. Now: one note, calc.py is written in two parts, and
    the hidden check PASSES."""
    from tests.fixtures.fake_openai_endpoint_v1387 import swallowed_reply

    done, result, chats = await _replay(tmp_path, swallowed_reply)
    assert all(c.get("tools") for c in chats), "a no-tools request was sent"
    assert result.passed is True, result.output[-1500:]
    assert done["route"]["cut_off"] == {"times": 1, "recovered": True}


async def test_replay_the_glm_evaluator_run_with_the_budget_alone(tmp_path):
    """The same run where the first attempt is the WHOLE module in one call
    after ~3,000 tokens of thinking (the measured ~4,000-token reply). RED
    before v1.387.0: the 4,096 budget cut it, and the turn ended in a
    no-tools answer. Now the budget holds it: no cut at all, the check passes."""
    from tests.fixtures.fake_openai_endpoint_v1387 import write_call_reply

    def whole_module(body):
        return write_call_reply(body, "calc.py", SOLUTION.read_text("utf-8"), spent_tokens=3_000)

    done, result, chats = await _replay(tmp_path, whole_module)
    assert result.passed is True, result.output[-1500:]
    assert all(c.get("tools") for c in chats)
    assert "cut_off" not in done["route"]
    assert chats[1]["max_tokens"] > 4_096
