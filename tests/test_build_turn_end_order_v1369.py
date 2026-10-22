"""v1.369.0 review (IJ harness Phase 6): a Build turn's END is written on the
session BEFORE its terminal frame reaches the client.

The screen re-reads ``GET /build/sessions/{id}`` the moment it sees ``done`` /
``error``. ``routes/build.recorded_turn`` used to write the reply line and the
account's limit card in its ``finally`` — AFTER the terminal frame had been
passed on — so the re-read could find neither, and the limit card the stream
had just shown vanished (the screen carried a retry loop + a frame-kept card
to paper over it). Now the record lands first.

Driven through the REAL app factory and the real ``POST
/build/sessions/{id}/turn``. The session record is read INSIDE the ASGI
``send`` that carries the terminal frame — the exact moment the client gets
it. Every session write is slowed (a worker-thread sleep), so a write that
runs after the frame is caught every time, not only on a slow machine.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from tests.test_build_accounts_v1368 import Behaviour, enable, iso, isolate, make_fake, wire_doubles
from tests.test_build_sessions_v1367 import Script, asgi, frames_of, messages_of, setup_of, wire
from tests.test_chat_turn_stop_v1241 import _scope

#: How long each slowed session write takes (in its worker thread).
SLOW_S = 0.3


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _slow_session_writes(monkeypatch) -> None:
    real_append, real_update = S.append_lines, S.update

    def slow_append(*a, **kw):
        time.sleep(SLOW_S)
        return real_append(*a, **kw)

    def slow_update(*a, **kw):
        time.sleep(SLOW_S)
        return real_update(*a, **kw)

    monkeypatch.setattr(S, "append_lines", slow_append)
    monkeypatch.setattr(S, "update", slow_update)


async def _turn_watching_the_end(app, sid: str, body: dict) -> tuple[str, dict]:
    """POST the turn; when the chunk carrying the terminal frame is SENT,
    read the session record right then. Answers (frames, what was on the
    record at that moment)."""
    raw = json.dumps(body).encode()
    sent = {"done": False}
    got: list[str] = []
    seen: dict = {}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] != "http.response.body":
            return
        text = msg.get("body", b"").decode("utf-8", "replace")
        got.append(text)
        if not seen and any(e in ("done", "error") for e, _ in frames_of(text)):
            seen["messages"] = messages_of(app, sid)
            seen["setup"] = setup_of(app, sid)

    task = asyncio.create_task(app(_scope(f"/build/sessions/{sid}/turn", raw), receive, send))
    try:
        await asyncio.wait_for(task, timeout=30)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    return "".join(got), seen


async def test_the_reply_line_is_on_the_session_before_done_reaches_the_client(app, work, monkeypatch):
    wire(app, Script(["Wrote it."]))
    status, view = await asgi(app, "POST", "/build/sessions", {
        "folder": str(work), "provider": "anthropic", "model": "claude-sonnet-4-5",
    })
    assert status == 201, view
    sid = view["id"]
    _slow_session_writes(monkeypatch)
    raw, seen = await _turn_watching_the_end(app, sid, {"text": "write it", "turn_id": "end-1"})
    assert frames_of(raw)[-1][0] == "done", frames_of(raw)[-3:]
    assert seen, "the terminal frame was never sent"
    replies = [m for m in seen["messages"] if m.get("role") == "assistant" and m.get("turnId") == "end-1"]
    assert replies and replies[0]["content"] == "Wrote it.", seen["messages"]
    # Written once, never twice (the finally waits on the same write).
    after = [m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == "end-1"]
    assert len(after) == 1, after


async def test_the_limit_card_is_on_the_session_before_the_error_reaches_the_client(
    app, fake, work, monkeypatch,
):
    await enable(app)
    wire_doubles(app, monkeypatch, Behaviour(["never sent"]))
    status, view = await asgi(app, "POST", "/build/sessions", {
        "folder": str(work), "provider": "claude-cli", "model": "claude-sonnet-4-5",
    })
    assert status == 201, view
    sid = view["id"]
    fake.states["c-work"].update({"status": "parked", "parkedUntil": iso(5400),
                                  "parkedReason": {"kind": "quota-exhausted"}})
    _slow_session_writes(monkeypatch)
    raw, seen = await _turn_watching_the_end(app, sid, {"text": "build the thing", "turn_id": "end-lim"})
    kinds = [e for e, _ in frames_of(raw)]
    assert kinds[-2:] == ["account_limit", "error"], kinds[-4:]
    assert seen, "the terminal frame was never sent"
    card = seen["setup"].get("account_limit")
    assert card and card["kind"] == "usage" and card["turn_id"] == "end-lim", seen["setup"]
    line = [m for m in seen["messages"] if m.get("role") == "assistant" and m.get("turnId") == "end-lim"]
    assert line and line[0]["accountLimit"]["kind"] == "usage", seen["messages"]
    # The news still goes out (after the frame): the bell's event.
    async def _published() -> bool:
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if any(e.type == "build.account_limit" for e in app.state.platform.event_bus.history):
                return True
            await asyncio.sleep(0.05)
        return False

    assert await _published(), "build.account_limit was never published"
