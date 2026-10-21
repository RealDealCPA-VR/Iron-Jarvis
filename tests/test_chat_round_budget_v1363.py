"""v1.363.0 — a caller can give ONE chat turn its own round budget and keep it
in chat at the last round (IJ harness plan, Phase 0; Phase 4 reuses it).

The two chat lanes cap a turn's tool rounds (``_round_budget``: 6, 12 for an
office turn, 24 for a browser-agent turn) and, at the cap, ESCALATE to an agent
run unless the turn is an office or browser one (``_stays_in_chat``). The
harness benchmark — and Build's coding turns after it — need many more rounds
and must never hand the work away. Both lanes now take ``round_budget`` and
``stay_in_chat`` as KEYWORD arguments (no HTTP field carries them), checked
before the turn starts; omitted, every existing caller is byte-identical.

Driven through the REAL lanes (``routes/chat.stream_chat_turn`` and
``chat_turn.run_chat_turn``) on a real platform; only the model (the router)
and the tool body (``registry.invoke``) are doubles.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    for chunk in chunks:
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


class _Model:
    """Asks for a tool on every round that offers tools; answers in words
    when called with none (the lane's final-answer nudge)."""

    def __init__(self) -> None:
        self.calls: list[bool] = []  # True = this call offered tools

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        self.calls.append(bool(tools))
        if tools:
            resp = LLMResponse(
                text="", usage={"input_tokens": 10, "output_tokens": 2},
                tool_calls=[ToolCall(id=f"c{len(self.calls)}", name="image_info", arguments={"path": "x.png"})],
            )
        else:
            resp = LLMResponse(text="Here is where it stands.", usage={"input_tokens": 10, "output_tokens": 2})
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    async def complete(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        self.calls.append(bool(tools))
        if tools:
            resp = LLMResponse(
                text="", usage={"input_tokens": 10, "output_tokens": 2},
                tool_calls=[ToolCall(id=f"c{len(self.calls)}", name="image_info", arguments={"path": "x.png"})],
            )
        else:
            resp = LLMResponse(text="Here is where it stands.", usage={"input_tokens": 10, "output_tokens": 2})
        return RouteResult(resp, "mock", "mock")


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    model = _Model()
    invoked = {"n": 0}

    async def fake_invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        invoked["n"] += 1
        return ToolResult(ok=True, output="an image, 10x10")

    monkeypatch.setattr(platform.router, "stream", model.stream)
    monkeypatch.setattr(platform.router, "complete", model.complete)
    monkeypatch.setattr(platform.registry, "invoke", fake_invoke)
    return platform, model, invoked


def _body() -> ChatBody:
    return ChatBody(messages=[ChatMessageBody(role="user", content="look at x.png")], tools=["image_info"])


async def _stream(platform, **kw) -> dict:
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, _body(), **kw)
    chunks = [c async for c in gen]
    frames = _frames(chunks)
    done = [d for e, d in frames if e == "done"]
    assert done, frames[-3:]
    return {"done": done[0], "rounds": sum(1 for e, _ in frames if e == "round")}


# --------------------------------------------------------------------------- #
# The default is untouched
# --------------------------------------------------------------------------- #


async def test_omitted_is_the_old_budget_and_the_old_escalation(wired):
    platform, model, invoked = wired
    got = await _stream(platform)
    assert len(model.calls) == chat_turn._MAX_TOOL_ROUNDS == 6
    assert invoked["n"] == 5
    assert got["done"]["escalate"] is True
    assert "stopped after 5 tool rounds" in got["done"]["reply"]


async def test_passing_the_defaults_explicitly_changes_nothing(wired):
    platform, model, invoked = wired
    got = await _stream(platform, round_budget=None, stay_in_chat=False)
    assert len(model.calls) == 6 and invoked["n"] == 5
    assert got["done"]["escalate"] is True


def test_the_helpers_answer_as_before_without_the_new_arguments():
    assert chat_turn._round_budget({"read_file"}) == 6
    assert chat_turn._round_budget({"write_document"}) == 12
    assert chat_turn._round_budget({"browser_click"}) == 24
    assert chat_turn._round_budget({"read_file"}, override=40) == 40
    assert chat_turn._stays_in_chat({"read_file"}) is False
    assert chat_turn._stays_in_chat({"read_file"}, force=True) is True
    assert chat_turn._stays_in_chat({"docx_edit"}) is True


def test_no_http_field_carries_the_knob():
    # An HTTP client cannot widen a turn: ChatBody has no such field and
    # silently drops one it is sent.
    assert "round_budget" not in ChatBody.model_fields
    assert "stay_in_chat" not in ChatBody.model_fields
    body = ChatBody.model_validate({
        "messages": [{"role": "user", "content": "hi"}], "round_budget": 50, "stay_in_chat": True,
    })
    assert not hasattr(body, "round_budget")


# --------------------------------------------------------------------------- #
# The stream lane
# --------------------------------------------------------------------------- #


async def test_stream_lane_runs_the_callers_budget_and_ends_in_chat(wired):
    platform, model, invoked = wired
    got = await _stream(platform, round_budget=9, stay_in_chat=True)
    # Nine tool rounds offered tools; the last one's calls were not run; then
    # ONE tool-less completion wrote the answer — no escalation.
    assert model.calls == [True] * 9 + [False]
    assert got["rounds"] == 9
    assert invoked["n"] == 8
    assert got["done"]["escalate"] is False
    assert "stopped after 8 tool rounds" in got["done"]["reply"]
    assert got["done"]["reply"].startswith("Here is where it stands.")


async def test_stream_lane_budget_without_stay_in_chat_still_escalates_at_its_cap(wired):
    platform, model, invoked = wired
    got = await _stream(platform, round_budget=3)
    assert model.calls == [True] * 3
    assert invoked["n"] == 2
    assert got["done"]["escalate"] is True


async def test_stream_lane_stay_in_chat_without_a_budget_keeps_six_rounds(wired):
    platform, model, invoked = wired
    got = await _stream(platform, stay_in_chat=True)
    assert model.calls == [True] * 6 + [False]
    assert got["done"]["escalate"] is False


# --------------------------------------------------------------------------- #
# The POST lane (lock-step)
# --------------------------------------------------------------------------- #


async def test_post_lane_runs_the_callers_budget_and_ends_in_chat(wired):
    platform, model, invoked = wired
    out = await run_chat_turn(platform, BUILTIN_PERSONAS, _body(), round_budget=9, stay_in_chat=True)
    assert model.calls == [True] * 9 + [False]
    assert invoked["n"] == 8
    assert out["escalate"] is False
    assert "stopped after 8 tool rounds" in out["reply"]


async def test_post_lane_default_is_untouched(wired):
    platform, model, invoked = wired
    out = await run_chat_turn(platform, BUILTIN_PERSONAS, _body())
    assert model.calls == [True] * 6
    assert invoked["n"] == 5
    assert out["escalate"] is True


# --------------------------------------------------------------------------- #
# Checked before anything runs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [0, -1, 201, True, "5", 2.5])
async def test_a_bad_budget_is_refused_before_the_turn_starts(wired, bad):
    platform, model, _invoked = wired
    with pytest.raises(ValueError, match="round_budget"):
        await stream_chat_turn(platform, BUILTIN_PERSONAS, _body(), round_budget=bad)
    with pytest.raises(ValueError, match="round_budget"):
        await run_chat_turn(platform, BUILTIN_PERSONAS, _body(), round_budget=bad)
    assert model.calls == []


def test_the_bound_is_inclusive():
    assert chat_turn.turn_round_budget(None) is None
    assert chat_turn.turn_round_budget(1) == 1
    assert chat_turn.turn_round_budget(chat_turn.MAX_TURN_ROUNDS) == 200


def test_both_lanes_take_the_same_two_arguments():
    import inspect

    from iron_jarvis.daemon.routes import chat as chat_routes

    for fn in (chat_routes.stream_chat_turn, chat_routes.chat_stream, inspect.unwrap(run_chat_turn)):
        params = inspect.signature(fn).parameters
        assert params["round_budget"].default is None, fn
        assert params["stay_in_chat"].default is False, fn
        assert params["round_budget"].kind is inspect.Parameter.KEYWORD_ONLY, fn
