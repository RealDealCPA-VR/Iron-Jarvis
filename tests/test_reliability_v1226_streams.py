"""v1.226.0 reliability wave — real-time surfaces (audit items F-D-2, F-D-3,
F-D-5) and the 422 envelope (F-F-4; contracts C1, C3, C4).

``/events`` used to close EVERY dashboard socket the moment one payload
carried a datetime (send_json raised TypeError past the except); it had no
replay cursor, so a reconnect gap silently dropped approval cards and toasts;
and a StreamHub publish from the scheduler thread appended to the SSE queue
without waking its reader (every token 15s late). A pydantic 422 rendered as
"[object Object]" because its detail was a list, unlike every other envelope.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime

from fastapi.testclient import TestClient

from iron_jarvis.core.streams import StreamHub, _enqueue_drop_oldest
from iron_jarvis.daemon.app import create_app


# --- F-D-3 / contract C3: a non-JSON payload never closes the socket ---------


def test_events_socket_survives_a_datetime_payload(tmp_path):
    app = create_app(str(tmp_path))
    bus = app.state.platform.event_bus
    with TestClient(app) as c:
        with c.websocket_connect("/events") as ws:
            c.portal.call(bus.publish, "test.datetime", {"when": datetime(2026, 9, 3, 12, 0)})
            frame = ws.receive_json()
            assert frame["type"] == "test.datetime"
            assert frame["payload"]["when"] == "2026-09-03 12:00:00"
            # the socket is still open: the NEXT frame arrives on the same connection
            c.portal.call(bus.publish, "test.after", {"n": 1})
            assert ws.receive_json()["type"] == "test.after"


# --- F-D-2 / contract C1: ?since=<id> replays what the client missed ---------


def test_events_since_replays_later_history_then_goes_live(tmp_path):
    app = create_app(str(tmp_path))
    bus = app.state.platform.event_bus
    with TestClient(app) as c:
        e1 = c.portal.call(bus.publish, "test.one", {"n": 1})
        e2 = c.portal.call(bus.publish, "test.two", {"n": 2})
        e3 = c.portal.call(bus.publish, "test.three", {"n": 3})
        with c.websocket_connect(f"/events?since={e1.id}") as ws:
            # A live event is published right away so the receive loop below
            # always terminates (a TestClient WS receive has no timeout): with
            # no replay the FIRST frame is the live one and the pin fails fast.
            c.portal.call(bus.publish, "test.live", {"n": 4})
            before_live: list[dict] = []
            for _ in range(10):
                frame = ws.receive_json()
                if frame["type"] == "test.live":
                    break
                before_live.append(frame)
            else:
                raise AssertionError("the live frame never arrived")
            assert [f["id"] for f in before_live] == [e2.id, e3.id], before_live
            assert [f["type"] for f in before_live] == ["test.two", "test.three"]


def test_events_since_unknown_id_sends_no_history(tmp_path):
    app = create_app(str(tmp_path))
    bus = app.state.platform.event_bus
    with TestClient(app) as c:
        c.portal.call(bus.publish, "test.before", {"n": 0})
        with c.websocket_connect("/events?since=evt_evicted_or_unknown") as ws:
            c.portal.call(bus.publish, "test.live", {"n": 1})
            first = ws.receive_json()
            assert first["type"] == "test.live"  # nothing replayed


def test_events_without_since_is_unchanged(tmp_path):
    app = create_app(str(tmp_path))
    bus = app.state.platform.event_bus
    with TestClient(app) as c:
        c.portal.call(bus.publish, "test.before", {"n": 0})
        with c.websocket_connect("/events") as ws:
            c.portal.call(bus.publish, "test.live", {"n": 1})
            assert ws.receive_json()["type"] == "test.live"


# --- F-D-5: StreamHub wakes the owner loop from a foreign thread -------------


#: The waiter's deadline. Without the thread-safe hop the frame is appended
#: to the queue but the owner loop is never woken — its selector sleeps until
#: the NEXT timer, i.e. this whole deadline (in production: the 15s SSE
#: keepalive, so every token of a scheduled run landed 15s late). So the pin
#: is a RATIO of the test's own deadline, never an absolute duration: a real
#: wake-up is microseconds, the bug is ≈ 100% of it.
_DEADLINE_S = 2.0


def _spy_threadsafe(loop) -> list:
    calls: list = []
    real = loop.call_soon_threadsafe

    def _spy(cb, *args, **kw):
        calls.append(cb)
        return real(cb, *args, **kw)

    loop.call_soon_threadsafe = _spy
    return calls


async def _wait_published(hub: StreamHub, q, publish_in_thread) -> tuple[dict, float]:
    loop = asyncio.get_running_loop()
    th = threading.Thread(target=publish_in_thread)
    t0 = loop.time()
    th.start()
    try:
        got = await asyncio.wait_for(q.get(), _DEADLINE_S)
    finally:
        th.join()
    return got, loop.time() - t0


def test_stream_hub_publish_from_a_thread_wakes_the_waiter():
    frame = {"event": "token", "data": {"text": "x"}}

    async def body():
        calls = _spy_threadsafe(asyncio.get_running_loop())
        hub = StreamHub()
        q = hub.subscribe("s1")
        try:
            got, elapsed = await _wait_published(hub, q, lambda: hub.publish("s1", frame))
        finally:
            hub.unsubscribe("s1", q)
        return got, elapsed, calls

    got, elapsed, calls = asyncio.run(body())
    assert got == frame
    assert _enqueue_drop_oldest in calls, "the frame did not hop through the owner loop"
    assert elapsed < _DEADLINE_S / 4, f"woke only when the deadline timer fired ({elapsed:.2f}s)"


def test_stream_hub_publish_from_a_foreign_loop_wakes_the_waiter():
    """The real shape: a scheduled session runs under asyncio.run on the
    APScheduler thread and its RunSink publishes from THAT loop."""

    def _publish_from_other_loop(hub):
        async def _pub():
            hub.publish("s1", {"event": "done", "data": {"ok": True}})

        asyncio.run(_pub())

    async def body():
        calls = _spy_threadsafe(asyncio.get_running_loop())
        hub = StreamHub()
        q = hub.subscribe("s1")
        got, elapsed = await _wait_published(hub, q, lambda: _publish_from_other_loop(hub))
        return got, elapsed, calls

    got, elapsed, calls = asyncio.run(body())
    assert got["event"] == "done"
    assert _enqueue_drop_oldest in calls
    assert elapsed < _DEADLINE_S / 4, f"woke only when the deadline timer fired ({elapsed:.2f}s)"


def test_stream_hub_unsubscribe_forgets_the_loop():
    async def body():
        hub = StreamHub()
        q = hub.subscribe("s1")
        assert id(q) in hub._queue_loops
        hub.unsubscribe("s1", q)
        assert id(q) not in hub._queue_loops
        hub.publish("s1", {"event": "token", "data": {}})  # no subscriber: a no-op

    asyncio.run(body())


def test_stream_hub_same_loop_publish_is_still_inline():
    async def body():
        hub = StreamHub()
        q = hub.subscribe("s1")
        hub.publish("s1", {"event": "token", "data": {"text": "y"}})
        return q.get_nowait()  # delivered synchronously, no hop

    assert asyncio.run(body())["data"]["text"] == "y"


# --- F-F-4 / contract C4: 422 detail is a string --------------------------------


def test_request_validation_error_detail_is_a_flat_string(tmp_path):
    c = TestClient(create_app(str(tmp_path)))
    r = c.post("/workflows", json={"name": "w", "steps": "not-a-list"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str), detail
    assert detail.startswith("steps: "), detail
    assert "list" in detail.lower()


def test_request_validation_error_joins_every_field(tmp_path):
    c = TestClient(create_app(str(tmp_path)))
    r = c.post("/workflows", json={"steps": "x", "description": ["no"]})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    parts = detail.split("; ")
    assert len(parts) >= 2, detail
    assert any(p.startswith("name: ") for p in parts), detail
    assert any(p.startswith("steps: ") for p in parts), detail


def test_stream_hub_publish_survives_an_owner_loop_that_closed():
    """D2 (review): the owner loop can close between is_running() and the
    hop; publish is documented never-raises, so the frame falls back inline."""
    hub = StreamHub()
    loop = asyncio.new_event_loop()
    try:
        async def _sub():
            return hub.subscribe("s1")

        q = loop.run_until_complete(_sub())
    finally:
        loop.close()

    class _Closed:
        """Reports running (the racy read) but refuses the hop like a closed loop."""

        def is_running(self):
            return True

        def call_soon_threadsafe(self, *a, **kw):
            raise RuntimeError("Event loop is closed")

    hub._queue_loops[id(q)] = _Closed()
    hub.publish("s1", {"event": "token", "data": {"text": "z"}})  # must not raise
    assert q.get_nowait()["data"]["text"] == "z"
