"""FX-01 SSE endpoints: POST /chat/stream + GET /sessions/{id}/stream."""

from __future__ import annotations

import asyncio
import json

import httpx
from fastapi.testclient import TestClient
from httpx import ASGITransport

from iron_jarvis.daemon.app import create_app


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """Parse an SSE body into (event, data) tuples, skipping keepalive comments."""
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


def _install_router_stream(platform) -> None:
    """Stub ModelRouter.stream with the coordinator's agreed FX-01 interface:
    delegate to the resolved adapter's real token stream (the mock word-chunks
    offline) and enrich the terminal ``final`` frame with the resolved
    provider/model. Assigned as an instance attribute so the endpoint's
    ``getattr(router, "stream")`` prefers it over the complete()-based fallback."""

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        adapter = platform.providers.get(
            provider or platform.router.default_provider, model
        )
        async for frame in adapter.stream(system=system, messages=messages, tools=tools):
            if frame.get("type") == "final":
                yield {**frame, "provider": adapter.provider, "model": adapter.model}
            else:
                yield frame

    platform.router.stream = fake_stream


def test_chat_stream_yields_token_and_done(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    _install_router_stream(app.state.platform)

    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    assert "round" in events
    assert "token" in events          # incremental deltas streamed
    assert events[-1] == "done"       # terminal frame closes the turn

    tokens = [d["text"] for e, d in frames if e == "token"]
    done = next(d for e, d in frames if e == "done")
    # The mock word-chunks its reply into several deltas...
    assert len(tokens) > 1
    # ...and the aggregate the client would render equals the done reply.
    assert "".join(tokens) == done["reply"]
    assert done["provider"] == "mock"
    assert set(done) >= {"reply", "provider", "model", "tools_used", "denied_tools", "usage"}


def test_chat_stream_empty_messages_400(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/chat/stream", json={"messages": []}).status_code == 400


async def test_sessions_stream_forwards_hub_frame(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    sid = "sess-fx01-1"

    async def pump():
        # Wait until the endpoint has subscribed, then publish onto the hub the
        # way a RunSink would as an agent run produces output.
        for _ in range(2000):
            if platform.streams.has_subscribers(sid):
                break
            await asyncio.sleep(0.005)
        platform.streams.publish(sid, {"event": "token", "data": {"text": "hi"}})
        platform.streams.publish(
            sid, {"event": "done", "data": {"ok": True, "reply": "hi"}}
        )

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        pump_task = asyncio.create_task(pump())
        r = await client.get(f"/sessions/{sid}/stream")
        await pump_task

    assert r.status_code == 200
    frames = _parse_sse(r.text)
    events = [e for e, _ in frames]
    assert "token" in events and "done" in events
    tok = next(d for e, d in frames if e == "token")
    assert tok["text"] == "hi"                       # hub frame forwarded verbatim
    assert not platform.streams.has_subscribers(sid)  # unsubscribed in finally
