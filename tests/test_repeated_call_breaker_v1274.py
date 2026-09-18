"""v1.274.0 — the same failing call is not run a third time (both chat lanes).

THE EVIDENCE: the user's own ledger, 2026-09-18 14:26Z — `browser_read_page`
refused with the identical add-on error five times in twenty seconds, the model
retrying because a tool error reads as "try again". Every retry was a billed
round and a wait.

THE RULE: a tool called with the same arguments that has already failed
REPEATED_CALL_LIMIT times in ONE turn is ANSWERED by the lane, not run: the tool
message says so and asks for a different approach. A different argument is a
different call and runs. In the stream lane the refusal comes BEFORE the approval
card, because carding a call the lane will not run would make an Allow a lie.
Both lanes, lock-step.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    out: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        event, data = "", None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    data = None
        if event:
            out.append((event, data))
    return out


def _call(i: int, path: str = "x.png") -> ToolCall:
    return ToolCall(id=f"c{i}", name="image_info", arguments={"path": path})


def _final(text: str, calls=None) -> dict:
    return {
        "type": "final",
        "response": LLMResponse(text=text, tool_calls=calls or [], usage={}),
        "provider": "mock",
        "model": "mock",
    }


def _failing_invoke(seen: list):
    async def fake_invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        seen.append((name, dict(args)))
        return ToolResult(ok=False, output="", error="boom: the file is not an image")

    return fake_invoke


def test_the_constants_and_the_refusal_name_the_call():
    assert chat_turn.REPEATED_CALL_LIMIT == 2
    text = chat_turn.repeated_call_refusal("image_info", 2)
    assert text.startswith("NOT RUN: image_info") and "2 times" in text and "Change the approach" in text
    k1 = chat_turn._repeat_key("t", {"b": 1, "a": [2, 3]})
    k2 = chat_turn._repeat_key("t", {"a": [2, 3], "b": 1})
    assert k1 == k2, "argument order must not make two identical calls different"
    assert chat_turn._repeat_key("t", {"a": 1}) != chat_turn._repeat_key("t", {"a": 2})
    assert chat_turn._repeat_key("t", {"x": object()})[0] == "t"


# --------------------------------------------------------------------------- #
# The stream lane
# --------------------------------------------------------------------------- #


def test_the_stream_lane_runs_the_same_failing_call_twice_and_answers_the_third(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        rounds["n"] += 1
        if rounds["n"] <= 4:
            yield _final("", [_call(rounds["n"])])
        else:
            yield {"type": "text", "text": "I could not read that file."}
            yield _final("I could not read that file.")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    seen: list = []
    monkeypatch.setattr(platform.registry, "invoke", _failing_invoke(seen))
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "go"}], "tools": ["image_info"]})
    assert r.status_code == 200
    assert len(seen) == chat_turn.REPEATED_CALL_LIMIT, f"the registry ran the identical call {len(seen)} times: {seen}"
    finished = [d for e, d in _parse_sse(r.text) if e == "tool_call" and d and d.get("status") == "finished"]
    assert len(finished) == 4, [d.get("output") for d in finished]
    assert all(d["ok"] is False for d in finished)
    assert "boom" in finished[0]["output"] and "boom" in finished[1]["output"]
    assert finished[2]["output"].startswith("NOT RUN: image_info") and "2 times" in finished[2]["output"]
    assert finished[3]["output"].startswith("NOT RUN: image_info")
    assert any(e == "done" for e, _ in _parse_sse(r.text)), "the turn did not end normally"


def test_the_stream_lane_runs_a_different_argument_after_two_failures(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        rounds["n"] += 1
        if rounds["n"] <= 2:
            yield _final("", [_call(rounds["n"])])
        elif rounds["n"] == 3:
            yield _final("", [_call(3, path="y.png")])  # a DIFFERENT call
        else:
            yield {"type": "text", "text": "done"}
            yield _final("done")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    seen: list = []
    monkeypatch.setattr(platform.registry, "invoke", _failing_invoke(seen))
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "go"}], "tools": ["image_info"]})
    assert r.status_code == 200
    assert [a["path"] for _, a in seen] == ["x.png", "x.png", "y.png"], seen


# --------------------------------------------------------------------------- #
# The non-stream lane
# --------------------------------------------------------------------------- #


def test_the_non_stream_lane_does_the_same(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}
    tool_messages: list[str] = []

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class):
        n["i"] += 1
        # Keep every tool message the model was shown, so the refusal is observable.
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
            if role == "tool":
                content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
                if str(content) not in tool_messages:
                    tool_messages.append(str(content))
        if n["i"] <= 4:
            return RouteResult(LLMResponse(text="", tool_calls=[_call(n["i"])]), "mock", "mock")
        return RouteResult(LLMResponse(text="I could not read that file."), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    seen: list = []
    monkeypatch.setattr(platform.registry, "invoke", _failing_invoke(seen))
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "go"}], "tools": ["image_info"]})
    assert r.status_code == 200, r.text
    assert len(seen) == chat_turn.REPEATED_CALL_LIMIT, f"the registry ran the identical call {len(seen)} times"
    refusals = [m for m in tool_messages if m.startswith("NOT RUN: image_info")]
    assert refusals, f"the model never saw the refusal; tool messages: {tool_messages}"
    assert "2 times" in refusals[0]
    assert r.json().get("tools_used", []) == [], "a call that was not run must not count as used"


def test_the_two_lanes_hold_the_rule_lock_step():
    """Source pin: both loops count failures and answer the third with the same helper."""
    from pathlib import Path

    root = Path(chat_turn.__file__).resolve().parent
    turn = (root / "chat_turn.py").read_text(encoding="utf-8")
    lane = (root / "routes" / "chat.py").read_text(encoding="utf-8")
    for src, where in ((turn, "chat_turn.py"), (lane, "routes/chat.py")):
        assert "_failed_calls.get(_call_key, 0) >= REPEATED_CALL_LIMIT" in src, where
        assert "_failed_calls[_call_key] = _failed_calls.get(_call_key, 0) + 1" in src, where
        assert "repeated_call_refusal(tc.name, _failed_calls[_call_key])" in src, where
    # The stream lane refuses BEFORE its card logic, never after.
    loop = lane[lane.index('_call_key = _repeat_key(tc.name, tc.arguments)'):]
    assert loop.index("repeated_call_refusal(") < loop.index('_deny_reason = ""'), "the stream lane cards a call it will not run"
