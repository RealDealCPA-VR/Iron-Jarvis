"""v1.336.0 — a chat answer survives a reload, a closed window and a crash.

From pi-durable (ideas only): "conversations and model turns are committed
before anything is shown; if the process dies mid-turn, reopening picks the
work up", and "cancelling a wait only cancels that wait, never the work".

Before this, ``POST /chat/stream`` ran the turn inside the HTTP response and
stopped it when the connection dropped; a reload lost the answer. Now a NAMED
turn (``turn_id``) runs as the daemon's own task (``daemon/durable_turns``):
the response only follows it, any window can rejoin it, the same turn id is
never started twice, its frames are kept on disk, and a restart leaves what it
had written readable, ending in an "interrupted" error.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from tests.test_chat_turn_stop_v1241 import _scope


def _frames(raw: str) -> list[tuple[str, dict]]:
    out = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        ev, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        out.append((ev, json.loads(data or "{}")))
    return out


def _gated_stream(gate: asyncio.Event, calls: list):
    async def stream(**kw):
        calls.append(kw)
        yield {"type": "text", "text": "first half, "}
        await gate.wait()
        yield {"type": "text", "text": "second half."}
        yield {
            "type": "final",
            "response": LLMResponse(text="first half, second half.", finish_reason="stop"),
            "provider": "mock",
            "model": "m",
        }

    return stream


async def _open(app, body: dict, *, drop: asyncio.Event | None = None, stop_after=None):
    """POST /chat/stream in-process; the CONNECTION drops when ``drop`` is set
    (a reload / closed window). Returns the text this window received."""
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        if drop is not None:
            await drop.wait()
        else:
            await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    got: list[str] = []

    async def send(msg):
        if msg["type"] == "http.response.body":
            got.append(msg.get("body", b"").decode("utf-8", "replace"))
            if stop_after and stop_after in "".join(got) and drop is not None:
                drop.set()

    task = asyncio.create_task(app(_scope("/chat/stream", raw), receive, send))
    try:
        await asyncio.wait_for(task, timeout=20)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    return "".join(got)


async def _get(app, path: str) -> tuple[int, str]:
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    status, chunks = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b"").decode("utf-8", "replace"))

    scope = {**_scope(path, b""), "method": "GET"}
    if "?" in path:
        p, q = path.split("?", 1)
        scope = {**scope, "path": p, "raw_path": p.encode(), "query_string": q.encode()}
    await asyncio.wait_for(app(scope, receive, send), timeout=20)
    return status, "".join(chunks)


async def _until(fn, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        await asyncio.sleep(0.02)
    return fn()


def _record(app, tid):
    with session_scope(app.state.platform.engine) as db:
        row = db.get(ChatTurnRecord, tid)
        if row is not None:
            db.expunge(row)
        return row


MSG = [{"role": "user", "content": "explain it"}]


@pytest.mark.asyncio
async def test_a_dropped_window_does_not_stop_the_turn_and_a_new_one_rejoins(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    first = await _open(
        app, {"messages": MSG, "turn_id": "turn-1", "thread_id": "th-1"},
        drop=drop, stop_after="first half",
    )
    assert "first half" in first and "event: done" not in first
    # The window is gone; the turn is still the daemon's.
    lg = app.state.platform.turn_logs.get("turn-1")
    assert lg is not None and lg.status == "running"
    gate.set()
    await _until(lambda: lg.status != "running")
    assert lg.status == "done"
    # On disk: the whole answer, done.
    rec = await _until(lambda: (r := _record(app, "turn-1")) and r.status == "done" and r)
    assert rec.thread_id == "th-1"
    # A new window rejoins and gets the WHOLE turn, ending in done.
    status, text = await _get(app, "/chat/turns/turn-1/stream")
    assert status == 200
    frames = _frames(text)
    tokens = "".join(d.get("text", "") for e, d in frames if e == "token")
    assert tokens == "first half, second half."
    assert frames[-1][0] == "done"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_window_that_joins_mid_turn_sees_what_was_written_then_the_rest(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "turn-2"}, drop=drop, stop_after="first half")
    joined = asyncio.create_task(_get(app, "/chat/turns/turn-2/stream"))
    await asyncio.sleep(0.1)
    assert not joined.done()  # following, not finished
    gate.set()
    status, text = await joined
    frames = _frames(text)
    assert "".join(d.get("text", "") for e, d in frames if e == "token") == "first half, second half."
    assert frames[-1][0] == "done"


@pytest.mark.asyncio
async def test_the_same_turn_id_is_never_run_twice(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    body = {"messages": MSG, "turn_id": "turn-3"}
    a = asyncio.create_task(_open(app, body))
    await _until(lambda: calls)
    b = asyncio.create_task(_open(app, body))  # a retried send
    await asyncio.sleep(0.1)
    gate.set()
    ta, tb = await a, await b
    assert len(calls) == 1
    assert "event: done" in ta and "event: done" in tb


@pytest.mark.asyncio
async def test_stop_still_ends_it_and_the_record_says_stopped(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "turn-4"}, drop=drop, stop_after="first half")
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    assert (await _asgi_post(app, "/chat/turns/turn-4/stop"))[0] == 200
    gate.set()
    lg = app.state.platform.turn_logs.get("turn-4")
    await _until(lambda: lg.status != "running")
    assert lg.status == "stopped"
    assert "second half" not in lg.snapshot()


def test_a_restart_keeps_what_was_written_and_says_it_was_cut_off(tmp_path):
    home = str(tmp_path)
    p = build_platform(home)
    with session_scope(p.engine) as db:
        db.add(ChatTurnRecord(
            id="turn-5", thread_id="th-5", status="running",
            frames_json=json.dumps([["token", json.dumps({"text": "half an answ"})]]),
        ))
        db.commit()
    p.engine.dispose()
    app = create_app(home)
    with TestClient(app) as client:  # the lifespan runs the boot reconcile
        listed = client.get("/chat/turns", params={"thread_id": "th-5"}).json()["turns"]
        assert [(t["turn_id"], t["status"], t["running"]) for t in listed] == [
            ("turn-5", "interrupted", False)
        ]
        r = client.get("/chat/turns/turn-5/stream")
        frames = _frames(r.text)
        assert frames[0] == ("token", {"text": "half an answ"})
        assert frames[-1][0] == "error" and "stopped while answering" in frames[-1][1]["detail"]
        assert client.get("/chat/turns/nope/stream").status_code == 404


@pytest.mark.asyncio
async def test_shutdown_cuts_running_turns_off_and_keeps_them(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "turn-6"}, drop=drop, stop_after="first half")
    await app.state.platform.turn_logs.shutdown()
    rec = _record(app, "turn-6")
    assert rec.status == "interrupted"
    frames = json.loads(rec.frames_json)
    assert ["token", json.dumps({"text": "first half, "})] in frames
    assert frames[-1][0] == "error"


def test_tokens_are_merged_on_disk_and_comments_are_not_frames():
    lg = dt.TurnLog("t")
    lg.add(dt.sse("token", {"text": "a"}))
    lg.add(dt.sse("token", {"text": "b"}))
    lg.add(": keepalive\n\n")
    lg.add(dt.sse("round", {"round": 1}))
    lg.add(dt.sse("token", {"text": "c"}))
    assert [(e, json.loads(d)) for e, d in lg.frames] == [
        ("token", {"text": "ab"}), ("round", {"round": 1}), ("token", {"text": "c"}),
    ]


def test_only_a_long_unwatched_turn_is_stopped_by_the_guard(monkeypatch):
    lg = dt.TurnLog("t")
    pred = dt.stop_predicate(lambda: lg)
    assert pred() is False
    lg._last_seen -= dt.ORPHAN_STOP_S + 1
    assert pred() is True
    q: asyncio.Queue = asyncio.Queue()
    lg._followers.add(q)  # someone is watching again
    assert pred() is False


def test_the_waiting_messages_are_kept_with_the_chat_without_moving_its_stamp(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    saved = client.put("/chat/threads/new", json={"messages": [{"role": "user", "content": "hi"}]}).json()
    tid = saved["id"]
    stamp = client.get(f"/chat/threads/{tid}").json()["updated_at"]
    items = [{"id": f"q{i}", "text": f"next {i}", "files": []} for i in range(5)]
    r = client.put(f"/chat/threads/{tid}/queue", json={"items": items})
    assert r.json() == {"ok": True, "queued": 3}  # the page's own limit
    got = client.get(f"/chat/threads/{tid}").json()
    assert [q["text"] for q in got["queued"]] == ["next 0", "next 1", "next 2"]
    # A message save carrying the stamp it loaded still lands (no 409).
    assert got["updated_at"] == stamp
    ok = client.put(f"/chat/threads/{tid}", json={
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        "if_updated_at": stamp,
    })
    assert ok.status_code == 200
    assert client.put(f"/chat/threads/{tid}/queue", json={"items": []}).json()["queued"] == 0
    assert client.get(f"/chat/threads/{tid}").json()["queued"] == []
    assert client.put("/chat/threads/nope/queue", json={"items": []}).status_code == 404


@pytest.mark.asyncio
async def test_a_new_chats_first_turn_is_linked_after_the_fact(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    # Linked BEFORE the turn started (the save landed first)...
    assert (await _asgi_post(app, "/chat/turns/turn-7/thread", {"thread_id": "th-7"}))[0] == 200
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "turn-7"}, drop=drop, stop_after="first half")
    assert app.state.platform.turn_logs.get("turn-7").thread_id == "th-7"
    gate.set()
    # ...and AFTER a turn ended.
    gate2, calls2 = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate2, calls2)
    gate2.set()
    await _open(app, {"messages": MSG, "turn_id": "turn-8"})
    lg = app.state.platform.turn_logs.get("turn-8")
    await _until(lambda: lg.status == "done")
    await asyncio.sleep(0.05)
    app.state.platform.turn_logs._logs.pop("turn-8")  # as if long gone from memory
    assert (await _asgi_post(app, "/chat/turns/turn-8/thread", {"thread_id": "th-8"}))[0] == 200
    assert _record(app, "turn-8").thread_id == "th-8"
