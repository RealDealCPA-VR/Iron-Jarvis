"""v1.327.0 (calm chat B2) — a saved chat knows if it is RUNNING or WAITING.

The chat list could not tell a chat that is still answering, or one parked on
a question for the user, from an idle one. ``GET /chat/threads`` rows now carry

* ``running`` — a turn is in flight for that chat (``ChatBody.thread_id``),
  in EITHER lane, named (``turn_id``) or not;
* ``waiting`` — that turn is parked on the user: an approval card in the
  stream lane, or an app's question / model request (``mcp_turn.PENDING``).

Both come from ONE snapshot of the live turn registry
(``core.turns.TURNS.thread_states``) — no database read per row, nothing
persisted. A turn sent with no ``thread_id`` counts for nothing.

Pinned through the REAL app (``create_app``) and the real lanes:

1. the registry's per-chat state (enter/leave by identity, park/unpark, a
   probe, a broken probe, the id cut);
2. the route's rows (always present, False by default, read from the
   registry, one chat never lights another);
3. the stream lane: running while it streams, not after; a card makes it
   waiting, an answer clears it while the turn still runs; an app's question
   makes it waiting; a turn with no thread id lights nothing;
4. the POST lane: running while the model answers, cleared after a reply AND
   after a failure.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.core.turns import TURNS, ThreadTurn, TurnRegistry, thread_key
from iron_jarvis.daemon import mcp_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult
from tests.test_chat_approvals_v1187 import _ASK_MSG, _shell_call_stream
from tests.test_chat_approvals_v1187 import _drive_stream as _drive_with_cards
from tests.test_chat_turn_stop_v1241 import _asgi_post, _drive_stream


@pytest.fixture(autouse=True)
def _clean_registry():
    TURNS._threads.clear()
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    TURNS._threads.clear()
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    mcp_turn.PENDING.clear()


def _save_thread(app, title: str = "a chat") -> str:
    with session_scope(app.state.platform.engine) as db:
        row = ChatThreadRecord(title=title, messages_json="[]")
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


async def _asgi_get(app, path: str) -> tuple[int, dict]:
    """One buffered in-process GET — a second connection, like the page's
    list refresh while a turn streams on another."""
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    status, chunks = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"127.0.0.1:8787")],
        "server": ("127.0.0.1", 8787), "client": ("127.0.0.1", 51234),
    }
    await app(scope, receive, send)
    return status, json.loads(b"".join(chunks) or b"{}")


async def _row(app, tid: str) -> dict:
    status, data = await _asgi_get(app, "/chat/threads")
    assert status == 200, data
    return next(t for t in data["threads"] if t["id"] == tid)


def _state(row: dict) -> tuple[bool, bool]:
    return row["running"], row["waiting"]


# --------------------------------------------------------------------------- #
# 1. The registry
# --------------------------------------------------------------------------- #


def test_a_turn_with_no_saved_chat_counts_for_nothing():
    reg = TurnRegistry()
    assert reg.new_thread_turn("") is None
    assert reg.new_thread_turn("   ") is None
    assert reg.new_thread_turn(None) is None
    with reg.thread_turn("") as entry:
        assert entry is None
        assert reg.thread_states() == {}


def test_running_lasts_until_the_LAST_turn_for_the_chat_leaves():
    reg = TurnRegistry()
    a, b = reg.new_thread_turn("chat_1"), reg.new_thread_turn("chat_1")
    assert reg.thread_states() == {}, "made is not entered"
    reg.enter_thread(a)
    reg.enter_thread(b)
    assert reg.thread_states() == {"chat_1": {"running": True, "waiting": False}}
    reg.leave_thread(a)
    assert reg.thread_states() == {"chat_1": {"running": True, "waiting": False}}, (
        "a second window's turn on the same chat is still running"
    )
    reg.leave_thread(a)  # leaving twice removes nothing else
    assert "chat_1" in reg.thread_states()
    reg.leave_thread(b)
    assert reg.thread_states() == {}


def test_the_context_manager_leaves_even_when_the_turn_raises():
    reg = TurnRegistry()
    with pytest.raises(RuntimeError):
        with reg.thread_turn("chat_x") as entry:
            assert isinstance(entry, ThreadTurn)
            assert reg.thread_states()["chat_x"]["running"] is True
            raise RuntimeError("boom")
    assert reg.thread_states() == {}


def test_a_card_parks_the_chat_and_its_answer_unparks_it():
    reg = TurnRegistry()
    entry = reg.new_thread_turn("chat_p")
    reg.enter_thread(entry)
    entry.park()
    entry.park()  # two asks of one batch
    assert reg.thread_states()["chat_p"]["waiting"] is True
    entry.unpark()
    assert reg.thread_states()["chat_p"]["waiting"] is True, "one ask is still open"
    entry.unpark()
    assert reg.thread_states()["chat_p"] == {"running": True, "waiting": False}
    entry.unpark()  # unbalanced: never below zero
    entry.park()
    assert reg.thread_states()["chat_p"]["waiting"] is True


def test_a_probe_is_read_live_and_a_broken_probe_reads_not_waiting():
    reg = TurnRegistry()
    entry = reg.new_thread_turn("chat_q")
    reg.enter_thread(entry)
    asked = {"open": True}
    entry.watch(lambda: asked["open"])
    assert reg.thread_states()["chat_q"]["waiting"] is True
    asked["open"] = False
    assert reg.thread_states()["chat_q"]["waiting"] is False

    def broken():
        raise ValueError("registry gone")

    entry.watch(broken)
    assert reg.thread_states() == {"chat_q": {"running": True, "waiting": False}}


def test_the_key_is_the_same_cut_the_body_makes():
    long_id = "c" * 120
    assert thread_key(long_id) == "c" * 80
    assert thread_key("  chat_9  ") == "chat_9"


# --------------------------------------------------------------------------- #
# 2. The route
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rows_always_carry_both_flags_read_from_the_live_registry(tmp_path):
    app = create_app(str(tmp_path))
    mine, other = _save_thread(app, "mine"), _save_thread(app, "other")
    assert _state(await _row(app, mine)) == (False, False)
    assert _state(await _row(app, other)) == (False, False)

    entry = TURNS.new_thread_turn(mine)
    TURNS.enter_thread(entry)
    assert _state(await _row(app, mine)) == (True, False)
    assert _state(await _row(app, other)) == (False, False), "one chat never lights another"
    entry.park()
    assert _state(await _row(app, mine)) == (True, True)
    entry.unpark()
    TURNS.leave_thread(entry)
    assert _state(await _row(app, mine)) == (False, False)


# --------------------------------------------------------------------------- #
# 3. The stream lane
# --------------------------------------------------------------------------- #


def _gated_two_rounds(gate: asyncio.Event):
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None, **kw):
        if rounds["n"] == 0:
            rounds["n"] += 1
            yield {"type": "final", "response": LLMResponse(
                text="", tool_calls=[ToolCall(id="c1", name="read_file",
                                              arguments={"path": "notes.txt"})],
                usage={"input_tokens": 3, "output_tokens": 1},
            ), "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": "first half"}
            await gate.wait()
            yield {"type": "text", "text": " second half"}
            yield {"type": "final", "response": LLMResponse(
                text="first half second half", usage={}),
                "provider": "mock", "model": "mock"}

    return fake_stream


def _fake_invoke(app, seen: list | None = None):
    async def fake_invoke(name, args, ctx, permissions, overrides=None, *,
                          session_allow=None, **kw):
        if seen is not None:
            seen.append(TURNS.thread_states())
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = fake_invoke


_READ = {"messages": [{"role": "user", "content": "read my notes"}],
         "tools": ["read_file"], "auto_tools": False}


@pytest.mark.asyncio
async def test_a_streaming_turn_reads_running_until_it_ends(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    _fake_invoke(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_two_rounds(gate)
    seen: dict = {}

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == "first half":
            seen["during"] = _state(await _row(app, tid))
            gate.set()

    frames = await _drive_stream(app, {**_READ, "thread_id": tid, "turn_id": "t-run"}, on_frame)
    assert any(ev == "done" for ev, _ in frames)
    assert seen["during"] == (True, False)
    assert _state(await _row(app, tid)) == (False, False), "a finished turn is not running"
    assert TURNS.thread_states() == {}


@pytest.mark.asyncio
async def test_an_unnamed_streaming_turn_still_counts_for_its_chat(tmp_path):
    """No ``turn_id`` (nothing addressable) — the chat is still running."""
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    _fake_invoke(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_two_rounds(gate)
    seen: dict = {}

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == "first half":
            seen["during"] = _state(await _row(app, tid))
            seen["named"] = TURNS.running_ids()
            gate.set()

    await _drive_stream(app, {**_READ, "thread_id": tid}, on_frame)
    assert seen["during"] == (True, False)
    assert seen["named"] == [], "nothing became addressable"
    assert TURNS.thread_states() == {}


@pytest.mark.asyncio
async def test_a_turn_with_no_thread_id_lights_nothing(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    _fake_invoke(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_two_rounds(gate)
    seen: dict = {}

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == "first half":
            seen["states"] = TURNS.thread_states()
            seen["row"] = _state(await _row(app, tid))
            gate.set()

    await _drive_stream(app, {**_READ, "turn_id": "t-none"}, on_frame)
    assert seen["states"] == {}
    assert seen["row"] == (False, False)


@pytest.mark.asyncio
async def test_a_stopped_turn_stops_reading_running(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    _fake_invoke(app)
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_two_rounds(gate)

    async def on_frame(ev, data):
        if ev == "token" and data.get("text") == "first half":
            assert _state(await _row(app, tid)) == (True, False)
            status, _ = await _asgi_post(app, "/chat/turns/t-stop/stop")
            assert status == 200
            gate.set()

    frames = await _drive_stream(app, {**_READ, "thread_id": tid, "turn_id": "t-stop"}, on_frame)
    assert "done" not in [ev for ev, _ in frames]
    assert _state(await _row(app, tid)) == (False, False)


@pytest.mark.asyncio
async def test_a_dropped_connection_stops_reading_running(tmp_path):
    """The response closes the stream mid-turn (a closed tab): the chat must
    not read as running forever."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody

    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    _fake_invoke(app)
    gate = asyncio.Event()  # never set: the turn is held mid-answer
    platform = app.state.platform
    platform.router.stream = _gated_two_rounds(gate)
    gen = await stream_chat_turn(platform, {}, ChatBody(**{**_READ, "thread_id": tid}))
    assert TURNS.thread_states() == {}, "not running before the stream starts"
    async for chunk in gen:
        if "first half" in chunk:
            break
    assert TURNS.thread_states() == {tid: {"running": True, "waiting": False}}
    await gen.aclose()
    assert TURNS.thread_states() == {}


@pytest.mark.asyncio
async def test_an_approval_card_makes_the_chat_wait_and_the_answer_clears_it(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    app.state.platform.router.stream = _shell_call_stream()
    ran: list = []
    _fake_invoke(app, ran)
    seen: dict = {}

    async def decide(data):
        seen["on_card"] = _state(await _row(app, tid))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_with_cards(
        app,
        {"messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
         "thread_id": tid},
        decide,
    )
    assert "approval" in [ev for ev, _ in frames], "the turn never asked"
    assert seen["on_card"] == (True, True), "a chat parked on a card reads waiting"
    # The answered call ran while the turn was still going — and no longer waiting.
    assert ran and ran[0].get(tid) == {"running": True, "waiting": False}, ran
    assert any(ev == "done" for ev, _ in frames)
    assert _state(await _row(app, tid)) == (False, False)


@pytest.mark.asyncio
async def test_an_apps_question_makes_the_chat_wait(tmp_path, monkeypatch):
    from tests.test_mcp_chat_lane_v1324 import (
        ELICIT, FakePack, _app, _run, _stream_router,
    )
    from tests.test_mcp_chat_lane_v1324 import _body as _mcp_body

    pack = FakePack([ELICIT])
    app = _app(tmp_path, monkeypatch, pack)
    _stream_router(app)
    tid = _save_thread(app)
    seen: dict = {}

    async def on_frame(ev, data):
        if ev == "mcp_elicitation":
            seen["asked"] = _state(await _row(app, tid))
            seen["answer"] = await _asgi_post(
                app, f"/chat/mcp/elicitations/{data['id']}",
                {"action": "accept", "content": {"year": 2024}},
            )
            seen["answered"] = _state(await _row(app, tid))

    frames = await _run(app, _mcp_body(thread_id=tid), on_frame)
    assert seen["answer"][0] == 200, seen
    assert seen["asked"] == (True, True), "a chat parked on an app's question reads waiting"
    # Answered: no longer waiting. Whether it still reads RUNNING is a race
    # (the fake turn can finish before this read lands), so only the
    # waiting flag is pinned here; running-while-working is pinned above.
    assert seen["answered"][1] is False, "answered, the chat no longer waits"
    assert any(ev == "done" for ev, _ in frames)
    assert _state(await _row(app, tid)) == (False, False)


@pytest.mark.asyncio
async def test_pack_asks_are_matched_to_their_own_turn_only():
    from types import SimpleNamespace

    mine = mcp_turn.TurnInteractions(SimpleNamespace(platform=None))
    theirs = mcp_turn.TurnInteractions(SimpleNamespace(platform=None))
    assert chat_routes._pack_asks_open(mine) is False
    ask = theirs._open("elicitation", "pack", "c1")
    assert chat_routes._pack_asks_open(mine) is False, "another turn's ask is not mine"
    assert chat_routes._pack_asks_open(theirs) is True
    ask.resolve("decline")
    assert chat_routes._pack_asks_open(theirs) is False, "an answered ask is not waiting"
    mine._open("sampling", "pack", "c2")
    assert chat_routes._pack_asks_open(mine) is True
    mine.close()
    theirs.close()
    assert chat_routes._pack_asks_open(mine) is False


# --------------------------------------------------------------------------- #
# 4. The POST lane
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_post_lane_runs_for_its_chat_and_clears_after_a_reply(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)
    gate, started = asyncio.Event(), asyncio.Event()

    async def fake_complete(**kw):
        started.set()
        await gate.wait()
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    app.state.platform.router.complete = fake_complete
    post = asyncio.create_task(_asgi_post(app, "/chat", {
        "messages": [{"role": "user", "content": "hello"}], "thread_id": tid,
    }))
    await asyncio.wait_for(started.wait(), 10)
    assert _state(await _row(app, tid)) == (True, False)
    gate.set()
    status, data = await post
    assert status == 200, data
    assert _state(await _row(app, tid)) == (False, False)


@pytest.mark.asyncio
async def test_the_post_lane_clears_after_a_failure_too(tmp_path):
    app = create_app(str(tmp_path))
    tid = _save_thread(app)

    seen: list = []

    async def fake_complete(**kw):
        seen.append(TURNS.thread_states().get(tid))
        raise RuntimeError("model fell over")

    app.state.platform.router.complete = fake_complete
    status, _ = await _asgi_post(app, "/chat", {
        "messages": [{"role": "user", "content": "hello"}], "thread_id": tid,
    })
    assert status >= 400
    assert seen and seen[0] == {"running": True, "waiting": False}, seen
    assert TURNS.thread_states() == {}
    assert _state(await _row(app, tid)) == (False, False)
