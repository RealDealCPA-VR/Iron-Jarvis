"""v1.246.0 — a chat turn never hangs silently.

The user's report (2026-09-11): the chat "gets hung up from time to time", and
attaching a document and asking for work gave "a completed screen with
absolutely no output". Measured causes, each pinned here:

* the stream sent NOTHING while a model thought or a tool ran (keepalives
  existed only during an approval wait), so a working turn and a dead one
  looked identical — ``HeartbeatStreamingResponse``;
* a chat tool call had no deadline (an agent run's does) — both lanes now pass
  ``config.tool_call_timeout_s`` as ``deadline_s``;
* a model that ended its loop with an EMPTY message produced the raw tool dump
  ("Ran X. Result: …") or the bare "(no reply)" — it is now asked once, with
  no tools and a time bound, for the answer it never wrote;
* the usage ledger created the chat row at the END, so every turn recorded a
  duration of 0.0 s.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    out: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        event = None
        data: dict | None = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data))
    return out


def _chat_runs(platform) -> list[AgentRun]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)) if r.session_id == "chat"]


def _tool_call(i: int = 1) -> ToolCall:
    return ToolCall(id=f"c{i}", name="image_info", arguments={"path": "x.png"})


def _final(text: str, calls=None, usage=None) -> dict:
    return {
        "type": "final",
        "response": LLMResponse(
            text=text, tool_calls=calls or [], usage=usage or {},
        ),
        "provider": "mock",
        "model": "mock",
    }


def _spy_invoke(seen: list, output: str = "PNG 2x2"):
    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None, **kw):
        seen.append(kw.get("deadline_s", "absent"))
        return ToolResult(ok=True, output=output)

    return fake_invoke


# --- the heartbeat -----------------------------------------------------------


def test_a_quiet_turn_sends_keepalives_while_the_model_thinks(tmp_path, monkeypatch):
    """A cold local model thinking before its first token used to mean total
    silence on the wire. The heartbeat fills it with comments every parser
    ignores, and the frames themselves arrive whole."""
    monkeypatch.setattr(chat_routes, "_SSE_HEARTBEAT_S", 0.05)
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def slow_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None, **kw):
        await asyncio.sleep(0.5)  # thinking
        yield {"type": "text", "text": "hello there"}
        yield _final("hello there")

    monkeypatch.setattr(platform.router, "stream", slow_stream)
    r = client.post(
        "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    raw = r.text
    first_token = raw.index("event: token")
    assert raw.count(": keepalive", 0, first_token) >= 3, raw[:first_token]
    frames = _parse_sse(raw)
    assert frames[-1][0] == "done"
    assert frames[-1][1]["reply"].startswith("hello there")


def test_the_route_serves_the_heartbeat_response(tmp_path):
    """The class is only worth anything if the route actually uses it."""
    import inspect

    src = inspect.getsource(chat_routes)
    route = src[src.index('@app.post("/chat/stream")'):]
    route = route[: route.index("@app.post", 10)]
    assert "HeartbeatStreamingResponse(" in route
    assert "StreamingResponse(" not in route.replace("HeartbeatStreamingResponse(", "")


# --- the tool deadline -------------------------------------------------------


def test_both_chat_lanes_give_a_tool_the_agent_runs_deadline(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    monkeypatch.setattr(platform.config, "tool_call_timeout_s", 42)

    # Non-stream lane.
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(LLMResponse(text="", tool_calls=[_tool_call()]),
                               "mock", "mock")
        return RouteResult(LLMResponse(text="done"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    seen: list = []
    monkeypatch.setattr(platform.registry, "invoke", _spy_invoke(seen))
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "go"}], "tools": ["image_info"],
    })
    assert r.status_code == 200
    assert seen == [42.0]

    # Streaming lane.
    s = {"i": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None, **kw):
        s["i"] += 1
        if s["i"] == 1:
            yield _final("", [_tool_call()])
        else:
            yield {"type": "text", "text": "done"}
            yield _final("done")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    seen.clear()
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "go"}], "tools": ["image_info"],
    })
    assert r.status_code == 200
    assert seen == [42.0]


def test_a_hung_tool_ends_as_a_failed_result_not_a_hung_turn(tmp_path, monkeypatch):
    """The REAL registry with a tool that never returns in time: the call
    fails with the deadline, the model reads that, and the turn finishes."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    monkeypatch.setattr(platform.config, "tool_call_timeout_s", 1)  # whole seconds
    tool = platform.registry.get("image_info")

    async def hangs(*a, **k):
        await asyncio.sleep(30)
        return ToolResult(ok=True, output="late")

    monkeypatch.setattr(tool, "execute", hangs)
    s = {"i": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None, **kw):
        s["i"] += 1
        if s["i"] == 1:
            yield _final("", [_tool_call()])
        else:
            yield {"type": "text", "text": "The tool timed out."}
            yield _final("The tool timed out.")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    t0 = time.monotonic()
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "go"}], "tools": ["image_info"],
    })
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    frames = _parse_sse(r.text)
    finished = [d for e, d in frames if e == "tool_call" and d["status"] == "finished"]
    assert finished and finished[0]["ok"] is False
    assert frames[-1][0] == "done"
    assert frames[-1][1]["reply"].startswith("The tool timed out.")
    # Bounded by the deadline, nowhere near the tool's 30 s.
    assert elapsed < 15


# --- the missing answer ------------------------------------------------------


def test_a_model_that_stops_silent_is_asked_once_for_its_answer(tmp_path, monkeypatch):
    """Non-stream lane: round 1 runs a tool, round 2 says nothing — the turn
    asks ONCE, with NO tools, and the answer becomes the reply (billed)."""
    client = _client(tmp_path)
    platform = client.app.state.platform
    calls: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        calls.append({"tools": tools, "last": messages[-1].content})
        if len(calls) == 1:
            return RouteResult(LLMResponse(text="", tool_calls=[_tool_call()]),
                               "mock", "mock")
        if len(calls) == 2:
            return RouteResult(LLMResponse(text=""), "mock", "mock")
        return RouteResult(
            LLMResponse(text="It is a 2x2 PNG image.",
                        usage={"input_tokens": 7, "output_tokens": 3}),
            "mock", "mock",
        )

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _spy_invoke([]))
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "inspect x.png"}],
        "tools": ["image_info"],
    })
    assert r.status_code == 200
    assert r.json()["reply"].startswith("It is a 2x2 PNG image.")
    assert len(calls) == 3
    assert calls[2]["tools"] == []
    assert calls[2]["last"] == chat_turn.FINAL_ANSWER_INSTRUCTION
    runs = _chat_runs(platform)
    assert len(runs) == 1 and runs[0].steps == 3
    assert runs[0].input_tokens == 7 and runs[0].output_tokens == 3


def test_the_streaming_lane_asks_for_the_missing_answer_too(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform
    s = {"i": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None, **kw):
        s["i"] += 1
        if s["i"] == 1:
            yield _final("", [_tool_call()])
        else:
            yield _final("")  # the silent stop

    nudges: list[dict] = []

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        nudges.append({"tools": tools, "last": messages[-1].content})
        return RouteResult(LLMResponse(text="Saved the summary to report.xlsx."),
                           "mock", "mock")

    monkeypatch.setattr(platform.router, "stream", fake_stream)
    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.registry, "invoke", _spy_invoke([]))
    r = client.post("/chat/stream", json={
        "messages": [{"role": "user", "content": "make the report"}],
        "tools": ["image_info"],
    })
    assert r.status_code == 200
    done = next(d for e, d in _parse_sse(r.text) if e == "done")
    assert done["reply"].startswith("Saved the summary to report.xlsx.")
    assert nudges == [{"tools": [], "last": chat_turn.FINAL_ANSWER_INSTRUCTION}]


def test_the_nudge_is_bounded_and_the_fallback_says_what_happened(tmp_path, monkeypatch):
    """A nudge that hangs is abandoned at its bound; the reply then says in
    plain words that no answer was written — never an empty bubble, never
    the bare placeholder."""
    monkeypatch.setattr(chat_turn, "_FINAL_ANSWER_TIMEOUT_S", 0.2)
    client = _client(tmp_path)
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(LLMResponse(text=""), "mock", "mock")
        await asyncio.sleep(30)  # the nudge hangs
        return RouteResult(LLMResponse(text="never"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    t0 = time.monotonic()
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert time.monotonic() - t0 < 15
    reply = r.json()["reply"]
    assert reply != "(no reply)"
    assert "empty answer" in reply


def test_a_draft_or_a_handoff_is_an_answer_and_gets_no_nudge():
    wants = chat_turn._wants_final_answer
    assert wants("", None, False, 1) is True
    assert wants("  ", None, False, 2) is True
    assert wants("an answer", None, False, 1) is False
    assert wants("", {"name": "x"}, False, 1) is False     # the draft card
    assert wants("", None, True, 1) is False               # the hand-off
    assert wants("", None, False, 0) is False              # no model reached


def test_the_fallback_names_the_tool_and_quotes_its_output():
    assert chat_turn._no_text_reply(["image_info"], "PNG 2x2").endswith("PNG 2x2")
    assert "image_info" in chat_turn._no_text_reply(["image_info"], "PNG 2x2")
    assert "empty answer" in chat_turn._no_text_reply([], "")


# --- reading a big attachment ------------------------------------------------


def test_attachment_retrieval_runs_off_the_event_loop(tmp_path, monkeypatch):
    """Chunking a big attachment and embedding every chunk is synchronous
    work; on the loop it froze every request in the app while the chat said
    nothing. It runs on a worker thread now."""
    import base64

    from iron_jarvis.documents import attachment_rag

    seen: dict = {}
    real = attachment_rag.rag_block

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return real(*a, **k)

    monkeypatch.setattr(attachment_rag, "rag_block", spy)
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        return RouteResult(LLMResponse(text="read it"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    big = client.post("/documents/upload", json={
        "filename": "big.txt",
        "content_b64": base64.b64encode(b"x" * 7000).decode(),
    }).json()
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "summarize"}],
        "attachments": [big["path"]],
    })
    assert r.status_code == 200
    assert seen == {"on_loop": False}


# --- the ledger's duration ---------------------------------------------------


def test_a_chat_turn_records_how_long_it_took(tmp_path, monkeypatch):
    """Both lanes: the row's start is the turn's start, not its end. The
    fake sleeps, so the lower bound is set by the test, not the hardware."""
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def slow_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        await asyncio.sleep(0.3)
        return RouteResult(LLMResponse(text="hello"), "mock", "mock")

    async def slow_stream(*, provider=None, model=None, system, messages,
                          tools, task_class=None, **kw):
        await asyncio.sleep(0.3)
        yield {"type": "text", "text": "hello"}
        yield _final("hello")

    monkeypatch.setattr(platform.router, "complete", slow_complete)
    monkeypatch.setattr(platform.router, "stream", slow_stream)
    assert client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]},
    ).status_code == 200
    assert client.post(
        "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]},
    ).status_code == 200
    runs = _chat_runs(platform)
    assert len(runs) == 2
    for run in runs:
        assert run.finished_at - run.created_at >= timedelta(seconds=0.25)
