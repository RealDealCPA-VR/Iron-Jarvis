"""v1.402.0 ("approve once, then off to the races"), part 3c: a document or
browser-agent chat turn that runs out of rounds gets ONE more allotment.

The audit (verified): a turn that stays in chat (an office turn,
``_DOC_TOOL_ROUNDS`` 12; a browser-agent turn, ``_BROWSER_TOOL_ROUNDS`` 24)
ended at its last round with ``OUT_OF_ROUNDS_INSTRUCTION`` ("…what is still
left to do") and no button: the user typed "continue".

Now BOTH chat lanes (lock-step, ``chat_turn.more_rounds`` +
``chat_turn.turn_rounds``): at the last round, with calls still to make, such
a turn gets the same number of rounds ONCE more and publishes ONE
``autonomy.nudged`` (``rounds_extended``); after the second allotment it ends
exactly as before. A plain turn (it escalates), a turn whose CALLER set its
budget (a Build reply, the benchmark) and a harness turn are unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventType
from iron_jarvis.core.nudges import ROUNDS_EXTENDED
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult
from tests.test_approvals_office_v1247 import _ANSWER, _fake_invoke_ok, _frames, _write_call

DOC = {"write_document"}
BROWSER = {"browser_read_page", "browser_click"}
PLAIN = {"read_file"}


# --------------------------------------------------------------------------- #
# the helpers
# --------------------------------------------------------------------------- #


def test_more_rounds_gives_one_allotment_of_the_same_size():
    assert chat_turn.more_rounds(DOC) == chat_turn._DOC_TOOL_ROUNDS == 12
    assert chat_turn.more_rounds(BROWSER) == chat_turn._BROWSER_TOOL_ROUNDS == 24
    # Once per turn.
    assert chat_turn.more_rounds(DOC, used=True) == 0
    # A plain turn escalates as before.
    assert chat_turn.more_rounds(PLAIN) == 0
    # A caller-set budget, a forced stay-in-chat or a harness turn: unchanged.
    assert chat_turn.more_rounds(DOC, override=12) == 0
    assert chat_turn.more_rounds(DOC, force=True) == 0
    assert chat_turn.more_rounds(DOC, harness=True) == 0


def test_turn_rounds_is_range_until_the_budget_grows():
    budget = {"n": 3}
    assert list(chat_turn.turn_rounds(lambda: budget["n"])) == [0, 1, 2]
    seen = []
    for i in chat_turn.turn_rounds(lambda: budget["n"]):
        seen.append(i)
        if i == 2 and len(seen) == 3:
            budget["n"] += 3
    assert seen == [0, 1, 2, 3, 4, 5]


# --------------------------------------------------------------------------- #
# the POST lane
# --------------------------------------------------------------------------- #


def _spy_nudges(platform) -> list[dict]:
    got: list[dict] = []
    original = platform.event_bus.publish

    async def spy(type_, payload=None, *a, **kw):
        if type_ == EventType.AUTONOMY_NUDGED:
            got.append(dict(payload or {}))
        return await original(type_, payload, *a, **kw)

    platform.event_bus.publish = spy  # type: ignore[method-assign]
    return got


def test_the_post_lane_extends_an_office_turn_once_then_ends_as_before(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    nudges = _spy_nudges(platform)
    calls: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        calls.append({"tools": tools, "last": messages[-1].content})
        if tools == []:
            return RouteResult(LLMResponse(text=_ANSWER), "mock", "mock")
        return RouteResult(LLMResponse(text="", tool_calls=[_write_call(len(calls))]), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok())
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "make the report"}],
        "tools": ["write_document"],
        "thread_id": "thr-1",
    })
    assert r.status_code == 200
    body = r.json()
    assert len([c for c in calls if c["tools"]]) == 24
    assert [c["last"] for c in calls if c["tools"] == []] == [chat_turn.OUT_OF_ROUNDS_INSTRUCTION]
    assert body["escalate"] is False and body["reply"].startswith(_ANSWER)
    assert nudges == [{"kind": ROUNDS_EXTENDED, "rounds": 24, "thread_id": "thr-1"}]


def test_a_turn_that_finishes_in_its_extra_rounds_ends_with_its_own_answer(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    nudges = _spy_nudges(platform)
    calls: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        calls.append({"tools": tools, "last": messages[-1].content})
        if len(calls) >= 15:
            return RouteResult(LLMResponse(text="Done: report.xlsx is ready."), "mock", "mock")
        return RouteResult(LLMResponse(text="", tool_calls=[_write_call(len(calls))]), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok())
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "make the report"}], "tools": ["write_document"],
    })
    body = r.json()
    assert body["reply"].startswith("Done: report.xlsx is ready.")
    assert "stopped after" not in body["reply"]
    assert all(c["last"] != chat_turn.OUT_OF_ROUNDS_INSTRUCTION for c in calls)
    assert len(nudges) == 1 and nudges[0]["kind"] == ROUNDS_EXTENDED


def test_a_plain_turn_still_escalates_at_six(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    nudges = _spy_nudges(platform)
    rounds = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        rounds["n"] += 1
        return RouteResult(LLMResponse(text="", tool_calls=[
            ToolCall(id=f"r{rounds['n']}", name="read_file", arguments={"path": f"f{rounds['n']}.txt"}),
        ]), "mock", "mock")

    async def fake_invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        return ToolResult(ok=True, output="text")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", fake_invoke)
    body = client.post("/chat", json={
        "messages": [{"role": "user", "content": "look around"}], "tools": ["read_file"],
    }).json()
    assert rounds["n"] == chat_turn._MAX_TOOL_ROUNDS == 6
    assert body["escalate"] is True
    assert nudges == []


async def test_a_caller_set_budget_is_never_extended(tmp_path, monkeypatch):
    """The benchmark / a Build reply sets its own budget: byte-identical."""
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    nudges = _spy_nudges(platform)
    rounds = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class, **kw):
        if tools == []:
            return RouteResult(LLMResponse(text=_ANSWER), "mock", "mock")
        rounds["n"] += 1
        return RouteResult(LLMResponse(text="", tool_calls=[_write_call(rounds["n"])]), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok())
    body = ChatBody(messages=[ChatMessageBody(role="user", content="make the report")], tools=["write_document"])
    out = await run_chat_turn(platform, BUILTIN_PERSONAS, body, round_budget=5, stay_in_chat=True)
    assert rounds["n"] == 5
    assert out["escalate"] is False
    assert nudges == []


# --------------------------------------------------------------------------- #
# the stream lane (lock-step)
# --------------------------------------------------------------------------- #


def test_the_stream_lane_extends_an_office_turn_once_too(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    nudges = _spy_nudges(platform)
    rounds = {"n": 0}
    asked: list[str] = []

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        if not tools:
            asked.append(messages[-1].content)
            yield {"type": "final", "response": LLMResponse(text=_ANSWER), "provider": "mock", "model": "mock"}
            return
        rounds["n"] += 1
        yield {"type": "final", "response": LLMResponse(text="", tool_calls=[_write_call(rounds["n"])]),
               "provider": "mock", "model": "mock"}

    async def fake_complete(**kw):
        raise AssertionError("the stream lane streams")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _fake_invoke_ok())
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "make the report"}],
        "tools": ["write_document"],
        "turn_id": "tr-more",
    })
    done = next(d for ev, d in _frames(r.text) if ev == "done")
    assert rounds["n"] == 24
    assert asked == [chat_turn.OUT_OF_ROUNDS_INSTRUCTION]
    assert done["escalate"] is False and done["reply"].startswith(_ANSWER)
    assert "stopped after 23 tool rounds" in done["reply"]
    assert nudges == [{"kind": ROUNDS_EXTENDED, "rounds": 24, "turn_id": "tr-more"}]
