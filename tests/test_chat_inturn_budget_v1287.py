"""v1.287.0 (chat-04) — a chat turn's TOOL ROUNDS are budgeted, not just its history.

THE DEFECT: `_plan_context` fits the history to the model's window ONCE, before
round 0. Every tool round then appended an assistant turn plus its tool results
(up to 12,000 chars each) and re-sent the whole list, so a 16k local model was
sent [535, 3869, 7203, 10537, 13871, 17205] estimated tokens across six rounds —
over the window by round 6, and over the planner's own output reserve by round 3.
The agent lane re-plans every step with `plan_agent_transcript`; the chat lanes
did not.

THE RULE: both lanes send every completion through ONE helper,
`chat_turn._fit_turn_transcript`, which reuses `plan_agent_transcript`'s ladder
ONLY when the window is known and the transcript overflows it: older rounds' tool
output becomes the trimmed marker first; an assistant `tool_use` and its results
move as one unit; the user's question and the newest round's results always
arrive whole. An unknown window sends exactly what it sent before.

Driven through the REAL app (`create_app`) and the real routes; only the model
(router) and the tool body (`registry.invoke`) are doubles.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.context.agent_window import _TOOL_TRIMMED
from iron_jarvis.context.budget import estimate_tokens, output_reserve
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

WINDOW = 16_000
ROUNDS = 5            # tool rounds before the model answers
BIG = "w" * 12_000    # a document read at the lane's own 12,000-char cap
QUESTION = "read these pages and summarise them for me"


def _snapshot(system: str, messages) -> dict:
    """What the model was sent, frozen at call time (the lane keeps appending)."""
    return {
        "system": system,
        "msgs": [
            {
                "role": m.role,
                "content": m.content or "",
                "calls": [c.id for c in (getattr(m, "tool_calls", None) or [])],
                "call_id": getattr(m, "tool_call_id", None),
            }
            for m in messages
        ],
    }


def _cost(snap: dict) -> int:
    # The planner's own accounting: +4 per message, the SAME estimator.
    return estimate_tokens(snap["system"]) + sum(
        estimate_tokens(m["content"]) + 4 for m in snap["msgs"]
    )


def _call(i: int) -> ToolCall:
    return ToolCall(id=f"c{i}", name="image_info", arguments={"path": f"page{i}.png"})


async def _big_invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
    return ToolResult(ok=True, output=BIG)


def _client(tmp_path, monkeypatch, *, window: int | None):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    monkeypatch.setattr(
        platform.config, "model_context_windows", {"mock": window} if window else {}
    )
    monkeypatch.setattr(platform.registry, "invoke", _big_invoke)
    return client, platform


def _stream_turn(tmp_path, monkeypatch, *, window: int | None) -> list[dict]:
    client, platform = _client(tmp_path, monkeypatch, window=window)
    sent: list[dict] = []

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          task_class=None, **kw):
        sent.append(_snapshot(system, messages))
        i = len(sent)
        if i <= ROUNDS:
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=[_call(i)], usage={}),
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "summary"}
            yield {"type": "final", "response": LLMResponse(text="summary", usage={}),
                   "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": QUESTION}], "tools": ["image_info"],
    })
    assert r.status_code == 200, r.text
    assert "event: done" in r.text, r.text[-800:]
    return sent


def _plain_turn(tmp_path, monkeypatch, *, window: int | None) -> list[dict]:
    client, platform = _client(tmp_path, monkeypatch, window=window)
    sent: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class=None, **kw):
        sent.append(_snapshot(system, messages))
        i = len(sent)
        if i <= ROUNDS:
            return RouteResult(LLMResponse(text="", tool_calls=[_call(i)]), "mock", "mock")
        return RouteResult(LLMResponse(text="summary"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": QUESTION}], "tools": ["image_info"],
    })
    assert r.status_code == 200, r.text
    return sent


def _assert_fitted(sent: list[dict]) -> None:
    assert len(sent) == ROUNDS + 1, f"expected {ROUNDS + 1} completions, got {len(sent)}"
    budget = WINDOW - output_reserve(WINDOW)
    costs = [_cost(s) for s in sent]
    assert max(costs) <= budget, f"round inputs {costs} exceed the {budget}-token budget of a {WINDOW} window"
    trimmed_any = False
    for n, snap in enumerate(sent):
        msgs = snap["msgs"]
        # The user's question is always there, whole.
        assert any(m["role"] == "user" and m["content"] == QUESTION for m in msgs), f"round {n} lost the question"
        # Every tool_use has ALL its results right after it, and no result is orphaned.
        for i, m in enumerate(msgs):
            if m["calls"]:
                got = [x["call_id"] for x in msgs[i + 1 : i + 1 + len(m["calls"])]]
                assert got == m["calls"], f"round {n}: tool_use {m['calls']} split from its results {got}"
            if m["role"] == "tool":
                owners = [x for x in msgs[:i] if m["call_id"] in x["calls"]]
                assert owners, f"round {n}: tool result {m['call_id']} sent without its tool_use"
        if n:
            # The newest round's result arrives WHOLE — it is what the model acts on.
            assert msgs[-1]["role"] == "tool" and msgs[-1]["call_id"] == f"c{n}"
            assert msgs[-1]["content"] == BIG, f"round {n}: the current round's result was trimmed"
        trimmed_any = trimmed_any or any(m["content"] == _TOOL_TRIMMED for m in msgs)
    # Anti-vacuity: the turn really overflowed, and the FIRST rung is what acted.
    assert trimmed_any, "nothing was trimmed — the turn never overflowed, so this proved nothing"


def test_the_stream_lane_fits_every_round_to_the_window(tmp_path, monkeypatch):
    _assert_fitted(_stream_turn(tmp_path, monkeypatch, window=WINDOW))


def test_the_plain_lane_fits_every_round_the_same_way(tmp_path, monkeypatch):
    _assert_fitted(_plain_turn(tmp_path, monkeypatch, window=WINDOW))


def test_the_two_lanes_send_the_same_transcript(tmp_path, monkeypatch):
    """Lock-step by construction: identical turns → identical completions."""
    a = _stream_turn(tmp_path / "a", monkeypatch, window=WINDOW)
    b = _plain_turn(tmp_path / "b", monkeypatch, window=WINDOW)
    assert [s["msgs"] for s in a] == [s["msgs"] for s in b]


def test_an_unknown_window_sends_exactly_what_it_sent_before(tmp_path, monkeypatch):
    """Control: no window known ⇒ no fitting — every result whole, nothing dropped."""
    monkeypatch.setattr(chat_turn, "_context_window", lambda d, p, m: None)
    sent = _stream_turn(tmp_path, monkeypatch, window=None)
    assert len(sent) == ROUNDS + 1
    for n, snap in enumerate(sent):
        msgs = snap["msgs"]
        assert len(msgs) == 1 + 2 * n, f"round {n} sent {len(msgs)} messages"
        assert [m["content"] for m in msgs if m["role"] == "tool"] == [BIG] * n
    # And the growth the fix exists for is really there without it.
    assert _cost(sent[-1]) > WINDOW


def test_a_turn_that_fits_is_untouched(tmp_path, monkeypatch):
    """Control: a big window never engages the ladder (cloud turns see no change)."""
    sent = _stream_turn(tmp_path, monkeypatch, window=200_000)
    for n, snap in enumerate(sent):
        assert len(snap["msgs"]) == 1 + 2 * n
        assert not any(m["content"] == _TOOL_TRIMMED for m in snap["msgs"])


def _nudged_turn(tmp_path, monkeypatch, lane: str) -> dict:
    """A turn whose last round writes NOTHING, so the lane asks once more
    (`_final_answer_after_tools`) — that completion re-sends the transcript too."""
    client, platform = _client(tmp_path, monkeypatch, window=WINDOW)
    rounds: list[dict] = []
    nudges: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools,
                            task_class=None, **kw):
        if tools == []:  # the final-answer nudge: no tools, by contract
            nudges.append(_snapshot(system, messages))
            return RouteResult(LLMResponse(text="summary"), "mock", "mock")
        rounds.append(_snapshot(system, messages))
        i = len(rounds)
        return RouteResult(
            LLMResponse(text="", tool_calls=[_call(i)] if i <= ROUNDS else []), "mock", "mock"
        )

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          task_class=None, **kw):
        route = await fake_complete(system=system, messages=messages, tools=tools)
        yield {"type": "final", "response": route.response, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    body = {"messages": [{"role": "user", "content": QUESTION}], "tools": ["image_info"]}
    r = client.post("/chat/stream" if lane == "stream" else "/chat", json=body)
    assert r.status_code == 200, r.text
    assert len(nudges) == 1, f"expected one final-answer completion, got {len(nudges)}"
    return nudges[0]


def test_the_final_answer_completion_is_fitted_in_both_lanes(tmp_path, monkeypatch):
    budget = WINDOW - output_reserve(WINDOW)
    for lane in ("stream", "plain"):
        snap = _nudged_turn(tmp_path / lane, monkeypatch, lane)
        msgs = snap["msgs"]
        assert _cost(snap) <= budget, f"{lane}: the nudge sent {_cost(snap)} tokens"
        assert any(m["role"] == "user" and m["content"] == QUESTION for m in msgs), lane
        assert any(m["content"] == _TOOL_TRIMMED for m in msgs), f"{lane}: nothing trimmed"
        # The newest round's result is intact just before the nudge itself.
        assert msgs[-2]["role"] == "tool" and msgs[-2]["content"] == BIG, lane
