"""v1.282.0 — what a turn REMEMBERED about the user rides its reply, both lanes.

A ``remember_preference`` call used to show as "1 tool" and a door; the
sentence itself was a click away. Now the tool's data carries the text, both
chat lanes collect it inside their ``if ran:`` block (the tools_used gate —
a failed or denied call keeps nothing), and the done frame / POST response
carry ``remembered`` ALWAYS (possibly empty), like doors, lock-step.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import remembered_from_result
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult
from tests.test_chat_turn_stop_v1241 import _drive_stream

_KEPT = "Prefers short answers with numbered steps"
_BODY = {
    "messages": [{"role": "user", "content": "from now on keep answers short"}],
    "tools": ["remember_preference"],
    "auto_tools": False,
}


# --------------------------------------------------------------------------- #
# 1. The helper
# --------------------------------------------------------------------------- #


def test_remembered_from_result_reads_the_data_then_the_output_and_nothing_else():
    ok = ToolResult(ok=True, output="remembered preference: from output", data={"text": _KEPT})
    assert remembered_from_result("remember_preference", ok) == _KEPT
    older = ToolResult(ok=True, output="remembered preference: from output", data={})
    assert remembered_from_result("remember_preference", older) == "from output"
    # A FAILED call that still carries a text (a refused write reporting what
    # it would have kept) must not be reported as kept — only `ok` decides.
    failed = ToolResult(ok=False, error="denied", output="remembered preference: nope", data={"text": "nope"})
    assert remembered_from_result("remember_preference", failed) == ""
    assert remembered_from_result("read_file", ok) == ""
    assert remembered_from_result("remember_preference", None) == ""
    long = ToolResult(ok=True, output="", data={"text": "x" * 500})
    assert len(remembered_from_result("remember_preference", long)) == 240


# --------------------------------------------------------------------------- #
# 2. The non-stream lane (POST /chat)
# --------------------------------------------------------------------------- #


def _two_round_complete(kept: str):
    rounds = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        rounds["n"] += 1
        if rounds["n"] == 1 and any(getattr(t, "name", t.get("name") if isinstance(t, dict) else "") == "remember_preference" for t in tools or []):
            return RouteResult(
                LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="remember_preference", arguments={"text": kept})]),
                "mock", "mock",
            )
        return RouteResult(LLMResponse(text="Noted."), "mock", "mock")

    return fake_complete


def test_the_post_lane_says_what_it_kept_and_the_lesson_exists(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.router, "complete", _two_round_complete(_KEPT))
    r = client.post("/chat", json=_BODY)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tools_used"] == ["remember_preference"]
    assert data["remembered"] == [_KEPT]
    assert any(l.text == _KEPT and l.source == "preference" for l in platform.learning.lessons())
    # A turn that kept nothing says so with an EMPTY list, never an absent key.
    r2 = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}], "auto_tools": False})
    assert r2.status_code == 200
    assert r2.json()["remembered"] == []


# --------------------------------------------------------------------------- #
# 3. The stream lane (POST /chat/stream)
# --------------------------------------------------------------------------- #


def _two_round_stream(kept: str):
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None, task_class=None, **kw):
        rounds["n"] += 1
        if rounds["n"] == 1 and tools:
            yield {
                "type": "final",
                "response": LLMResponse(text="", tool_calls=[ToolCall(id="c1", name="remember_preference", arguments={"text": kept})]),
                "provider": "mock", "model": "mock",
            }
        else:
            yield {"type": "text", "text": "Noted."}
            yield {"type": "final", "response": LLMResponse(text="Noted."), "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_the_stream_lane_carries_the_kept_sentence_on_its_done_frame(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _two_round_stream(_KEPT)
    frames = await _drive_stream(app, _BODY)
    done = [d for ev, d in frames if ev == "done"]
    assert len(done) == 1, frames
    assert done[0]["tools_used"] == ["remember_preference"]
    assert done[0]["remembered"] == [_KEPT]
    # And nothing kept → an empty list, lock-step with the POST lane.
    app2 = create_app(str(tmp_path / "two"))
    app2.state.platform.router.stream = _two_round_stream(_KEPT)
    frames2 = await _drive_stream(app2, {"messages": [{"role": "user", "content": "hi"}], "auto_tools": False})
    done2 = [d for ev, d in frames2 if ev == "done"]
    assert done2 and done2[0]["remembered"] == []
