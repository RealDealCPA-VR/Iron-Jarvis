"""v1.417.0 (C1.5) — AUTO'S JUDGE OFF THE CRITICAL PATH.

Each undecided command was a SERIAL model call of up to 6 s, and the judge
saw only the newest user message ("yes, go ahead" says nothing alone). Now a
round's judgements run together (one wait), each is bounded by 3 s, and the
judge reads the turn's FIRST request and the assistant's last words beside the
newest request. Driven through the REAL stream lane (only the model, the
judge's model call and each tool's execute are doubles).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.core import auto_approve as A
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from tests.test_auto_approve_lanes_v14060 import (  # noqa: F401 — `env` is a fixture
    CLAUDE,
    Model,
    _executes,
    _stream,
    env,
)

UNDECIDED = ["npm run build", "npm run lint", "make docs", "cargo build"]
DELAY_S = 1.0


def _wire_slow_judge(env, monkeypatch, moves):
    model = Model(moves)
    seen: list[dict] = []
    state = {"now": 0, "peak": 0}

    async def complete(**kw):
        if kw.get("system") == A.JUDGE_SYSTEM:
            seen.append(kw)
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            try:
                await asyncio.sleep(DELAY_S)
            finally:
                state["now"] -= 1
            return RouteResult(LLMResponse(text="allow", usage={"input_tokens": 5, "output_tokens": 1}), *CLAUDE)
        return await model.complete(**kw)

    monkeypatch.setattr(env.platform.router, "stream", model.stream)
    monkeypatch.setattr(env.platform.router, "complete", complete)
    return seen, state


def _body(ws, messages):
    return ChatBody(
        messages=[ChatMessageBody(role=r, content=c) for r, c in messages],
        provider=CLAUDE[0], model=CLAUDE[1], workspace_dir=str(ws), approval_mode="auto",
    )


async def test_a_rounds_undecided_commands_are_judged_together(env, monkeypatch):
    ran = _executes(env.platform, monkeypatch, "shell")
    calls = [ToolCall(id=f"b{i}", name="Bash", arguments={"command": c}) for i, c in enumerate(UNDECIDED)]
    seen, state = _wire_slow_judge(env, monkeypatch, [calls])
    out = await _stream(env.platform, _body(env.ws, [("user", "build the project")]), harness=True)
    assert out["cards"] == []
    assert sorted(a["command"] for a in ran["shell"]) == sorted(UNDECIDED)
    assert len(seen) == len(UNDECIDED)
    # ALL FOUR were in flight at once — the round waited one judgement, not
    # four (measured by overlap, never by a wall clock: a loaded runner).
    assert state["peak"] == len(UNDECIDED), state


def test_one_judgement_is_bounded_by_three_seconds():
    assert A.JUDGE_TIMEOUT_S == 3.0
    assert A.AutoJudge(router=None, provider="x", model="y", request="").timeout_s == 3.0


async def test_the_judge_reads_the_first_request_and_the_assistants_last_words(env, monkeypatch):
    _executes(env.platform, monkeypatch, "shell")
    words_then_call = LLMResponse(
        text="I will build the docs site now.",
        tool_calls=[ToolCall(id="b1", name="Bash", arguments={"command": "make docs"})],
        usage={"input_tokens": 3, "output_tokens": 1},
    )
    seen, _state = _wire_slow_judge(env, monkeypatch, [])

    async def stream(**kw):
        resp = words_then_call if kw.get("tools") and not getattr(stream, "done", False) else LLMResponse(
            text="Done.", usage={"input_tokens": 3, "output_tokens": 1})
        stream.done = True
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": CLAUDE[0], "model": CLAUDE[1]}

    monkeypatch.setattr(env.platform.router, "stream", stream)
    body = _body(env.ws, [
        ("user", "set up the documentation site for the cart service"),
        ("assistant", "Here is the plan: install the generator, then build."),
        ("user", "yes, go ahead"),
    ])
    out = await _stream(env.platform, body, harness=True)
    assert out["cards"] == []
    (call,) = seen
    prompt = call["messages"][0].content
    assert "set up the documentation site for the cart service" in prompt  # the FIRST request
    assert "yes, go ahead" in prompt  # the newest
    assert "I will build the docs site now." in prompt  # what the assistant last said
    assert prompt.index("<<<FIRST") < prompt.index("<<<REQUEST")


def test_the_prompt_is_byte_identical_without_the_new_context():
    plain = A.judge_prompt("do it", "shell", {"command": "make"})
    assert A.judge_prompt("do it", "shell", {"command": "make"}, first="do it", reply="") == plain
    assert "<<<FIRST" not in plain and "<<<REPLY" not in plain


def test_first_request_and_last_reply_readers():
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "earlier words"}]},
        {"role": "tool", "content": "a tool result"},
        {"role": "user", "content": "second"},
    ]
    assert A.first_request_of(msgs) == "first"
    assert A.request_of(msgs) == "second"
    assert A.last_reply_of(msgs) == "earlier words"
    assert A.last_reply_of(msgs, "this round's words") == "this round's words"
    assert A.last_reply_of([{"role": "tool", "content": "x"}]) == ""
