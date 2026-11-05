"""v1.433.0 (Wave 1, group C — A15, C1): the POST chat lane, lock-step with
the stream lane.

A15. ``chat_turn.run_chat_turn`` filled ``denied_tools`` (one entry per call
the permission engine refused) and never put it in its answer; the stream
lane's done frame has carried it since v1.165.0, and the chat page's POST
fallback reads it. A test hid the gap with ``out.get("denied_tools",
[DENIED, DENIED])``.

C1. A mistyped "/" skill was refused only AFTER the grounding hops, the
memory-profile search and the harness build had run — each paid for, for a
turn that never ran. The stream lane refuses it before its response opens.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_chat_recovery_v1344 import (
    DENIED,
    _app,
    _body,
    _calls_a_denied_tool_twice,
    _FakeTool,
    _run_post,
    _run_stream,
)


async def test_the_post_lane_answers_denied_tools_like_the_stream_lane(tmp_path):
    """MUTATION: drop the key from the POST response -> KeyError here."""
    post_platform = _app(tmp_path / "post", _FakeTool(DENIED), engine_denies=DENIED)
    out, _ = await _run_post(post_platform, _body(DENIED), _calls_a_denied_tool_twice())
    stream_platform = _app(tmp_path / "stream", _FakeTool(DENIED), engine_denies=DENIED)
    done, _, _ = await _run_stream(stream_platform, _body(DENIED), _calls_a_denied_tool_twice())
    assert out["denied_tools"] == done["denied_tools"] == [DENIED, DENIED]


async def test_a_turn_with_no_refusal_still_carries_an_empty_list(tmp_path):
    platform = _app(tmp_path)
    out, _ = await _run_post(platform, _body(), [LLMResponse(text="hello")])
    assert out["denied_tools"] == []
    assert isinstance(out["denied_tools"], list)


async def test_an_unknown_skill_is_refused_before_any_grounding(tmp_path, monkeypatch):
    """MUTATION: put the check back after `_gather_grounding` -> the spies
    record the grounding, the memory-profile search and (for a harness turn)
    the harness build."""
    platform = create_app(str(tmp_path)).state.platform
    ran: list[str] = []

    async def grounding(*a, **k):
        ran.append("grounding")
        raise AssertionError("the grounding ran for a refused turn")

    async def brain(*a, **k):
        ran.append("brain")
        raise AssertionError("the memory-profile search ran for a refused turn")

    async def harness(*a, **k):
        ran.append("harness")
        raise AssertionError("the harness was built for a refused turn")

    monkeypatch.setattr(chat_turn, "_gather_grounding", grounding)
    monkeypatch.setattr(chat_turn, "brain_turn", brain)
    monkeypatch.setattr(chat_turn, "_build_harness", harness)
    body = ChatBody(messages=[{"role": "user", "content": "hello"}], skill="no-such-skill-anywhere")
    with pytest.raises(HTTPException) as err:
        await run_chat_turn(platform, {}, body)
    assert err.value.status_code == 404
    assert err.value.detail == "no such skill: no-such-skill-anywhere"
    with pytest.raises(HTTPException) as err:
        await run_chat_turn(platform, {}, body, harness=True)
    assert err.value.status_code == 404
    assert ran == [], ran


async def test_a_real_skill_still_reaches_the_grounding(tmp_path, monkeypatch):
    """The control: the move refused only what it should — a turn with no
    skill (and an empty skill) runs its grounding as before."""
    platform = create_app(str(tmp_path)).state.platform
    real = chat_turn._gather_grounding
    ran: list[str] = []

    async def grounding(*a, **k):
        ran.append("grounding")
        return await real(*a, **k)

    async def complete(*, system, messages, tools, **kw):
        from iron_jarvis.providers.router import RouteResult

        return RouteResult(LLMResponse(text="hi"), "mock", "mock")

    monkeypatch.setattr(chat_turn, "_gather_grounding", grounding)
    platform.router.complete = complete
    out = await run_chat_turn(platform, {}, ChatBody(messages=[{"role": "user", "content": "hello"}], skill=""))
    assert ran == ["grounding"] and out["reply"].startswith("hi")
