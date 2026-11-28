"""Third north-star fix plan, group 19 (STOP): Stop is written down.

A Stop used to be a flag in memory only (``core/turns.TurnHandle.stop``):

* (RB1) a restart in the seconds after Stop lost it, and the coding resume
  pass then carried the stopped reply on by itself;
* (CS4) a Stop pressed right after Send could reach the daemon before the
  turn had registered: the route answered 404 "no such turn", the page saved
  "Stopped." and the turn ran to its end with nobody watching, steps included.

Now ``POST /chat/turns/{id}/stop`` writes WHEN the stop was asked on the
turn's record (``ChatTurnRecord.stop_asked_at``) before it acts on it, the
live log carries it on every later write (``TurnLog.stop_asked``),
``durable_turns.stop_asked`` reads it after a restart, and a Stop for an id
not yet known is KEPT for it (bounded, with a time-to-live by the registry's
injected clock) and applied when the turn registers — in both chat lanes.
404 is left only for a reply that already finished (``finished: true``).

Real ``TurnLogs``, real routes through the app factory, real SQLite in
``tmp_path``; the model and the tools are stand-ins that count their calls.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.core import turns as T
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult
from tests.test_durable_chat_turns_v1336 import _gated_stream, _open, _until
from tests.test_third_plan_g18_reply_home import _new_chat, _replies, _req

MSG = [{"role": "user", "content": "explain the notes"}]


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-wide: no test may stop another's turn."""

    def _clear():
        for tid in TURNS.running_ids():
            TURNS.release(tid)
        with TURNS._lock:
            getattr(TURNS, "_pending", {}).clear()
            getattr(TURNS, "_ended", {}).clear()
            getattr(TURNS, "_refused", {}).clear()

    _clear()
    yield
    _clear()


def _record(engine, turn_id: str):
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        if row is not None:
            db.expunge(row)
        return row


def _counting_tools(app) -> list:
    ran: list = []

    async def invoke(name, args, ctx, permissions, overrides=None, *, session_allow=None, **kw):
        ran.append(name)
        return ToolResult(ok=True, output="ok")

    app.state.platform.registry.invoke = invoke
    return ran


# --------------------------------------------------------------------------- #
# 1. Stop on a running turn: written BEFORE it is acted on, kept for ever after
# --------------------------------------------------------------------------- #
async def test_stop_writes_the_mark_before_it_answers_and_every_later_write_keeps_it(
    tmp_path, monkeypatch,
):
    """Fourth plan G27 (DT6): the turn is stopped in memory AT ONCE, then the
    mark is written (bounded); the route answers only after the write, so the
    mark is on the record when it does — and on every later write."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "run-1"}, drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("run-1")
    assert lg is not None and lg.status == "running"
    handle = TURNS.handle("run-1")

    # WHAT THE TURN WAS at the instant the mark was written.
    seen: dict = {}
    real_write = dt.write_stop_mark

    def write_spy(engine_, turn_id, when, *a, **kw):
        seen["stopped_at_write"] = handle.stopped
        return real_write(engine_, turn_id, when, *a, **kw)

    monkeypatch.setattr(dt, "write_stop_mark", write_spy)
    status, out = await _req(app, "POST", "/chat/turns/run-1/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True})
    assert seen["stopped_at_write"] is True, "the stop waited for the disk"
    rec = _record(engine, "run-1")
    assert rec is not None and rec.stop_asked_at is not None, "the route answered before the mark was written"
    assert lg.stop_asked is True
    mark = rec.stop_asked_at

    gate.set()
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped"
    rec = _record(engine, "run-1")
    assert rec.status == "stopped"
    assert rec.stop_asked_at == mark, "the turn's own end rewrote the mark"
    # A later write of the record — even one from a writer that never heard
    # of the stop — never clears it.
    await lg.persist_now(engine)
    dt._write_record(engine, "run-1", lg.started_at, {"status": "stopped", "stop_asked_at": None})
    assert _record(engine, "run-1").stop_asked_at == mark
    assert "second half" not in lg.snapshot()


async def test_a_stop_from_any_path_rides_the_record(tmp_path):
    """Stop and archive, the night, an attempt stop a turn through the
    registry, not the route: the turn's record still says a stop was asked
    (the live log reads its handle)."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "any-1"}, drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("any-1")
    assert TURNS.stop("any-1")
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped" and lg.stop_asked
    assert _record(engine, "any-1").stop_asked_at is not None
    # Fourth plan G27 (CS6): a caller that did not say who is not "you".
    assert _record(engine, "any-1").stop_author == ""


# --------------------------------------------------------------------------- #
# 2. The reader, after a restart
# --------------------------------------------------------------------------- #
async def test_stop_asked_reads_the_record_after_a_restart(tmp_path):
    home = str(tmp_path)
    app = create_app(home)
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "rs-stopped"}, drop=drop, stop_after="first half")
    assert (await _req(app, "POST", "/chat/turns/rs-stopped/stop"))[0] == 200
    lg = app.state.platform.turn_logs.get("rs-stopped")
    gate.set()
    await _until(lambda: lg.task.done())
    # A turn that ran to its end, never stopped.
    gate2, calls2 = asyncio.Event(), []
    gate2.set()
    app.state.platform.router.stream = _gated_stream(gate2, calls2)
    await _open(app, {"messages": MSG, "turn_id": "rs-done"})
    done = app.state.platform.turn_logs.get("rs-done")
    await _until(lambda: done.task.done())
    assert done.status == "done"

    # "A new process": nothing in memory, a fresh engine on the same database.
    dt._LIVE.pop("rs-stopped", None)
    dt._LIVE.pop("rs-done", None)
    p = build_platform(home)
    assert await asyncio.to_thread(dt.stop_asked, p.engine, "rs-stopped") is True
    assert await asyncio.to_thread(dt.stop_asked, p.engine, "rs-done") is False
    assert await asyncio.to_thread(dt.stop_asked, p.engine, "never-was") is False
    assert await asyncio.to_thread(dt.stop_asked, p.engine, "") is False


async def test_a_running_turn_with_no_record_yet_gets_one_with_the_mark(tmp_path):
    """No moment in which the stop is in memory only: a registered turn whose
    log has not written its first record (a sidebar turn has none at all)
    gets a record carrying the mark."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    handle = TURNS.register("no-rec-1")
    assert _record(engine, "no-rec-1") is None
    status, out = await _req(app, "POST", "/chat/turns/no-rec-1/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True})
    assert handle.stopped
    rec = _record(engine, "no-rec-1")
    assert rec is not None and rec.stop_asked_at is not None
    assert await asyncio.to_thread(dt.stop_asked, engine, "no-rec-1") is True


async def test_a_failed_write_is_logged_and_the_stop_still_happens(tmp_path, monkeypatch, caplog):
    app = create_app(str(tmp_path))
    handle = TURNS.register("bad-disk-1")

    def broken(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(dt, "write_stop_mark", broken)
    status, out = await _req(app, "POST", "/chat/turns/bad-disk-1/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True})
    assert handle.stopped
    assert any("was not written" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# 3. CS4 — a Stop that arrives before its turn is known
# --------------------------------------------------------------------------- #
async def test_stream_lane_a_stop_before_the_turn_registers_is_kept_and_nothing_runs(
    tmp_path, monkeypatch,
):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "early-1")
    model_calls: list = []
    ran = _counting_tools(app)
    gate = asyncio.Event()
    gate.set()
    app.state.platform.router.stream = _gated_stream(gate, model_calls)

    # Hold the stream route BEFORE the turn registers: the lane awaits its
    # local-only scope first (one of the awaits before ``TURNS.register``).
    held, release = asyncio.Event(), asyncio.Event()
    real_scope = CT.local_only_scope_for

    async def held_scope(platform, body):
        held.set()
        await release.wait()
        return await real_scope(platform, body)

    monkeypatch.setattr(CT, "local_only_scope_for", held_scope)
    window = asyncio.create_task(_open(app, {
        "messages": MSG, "turn_id": "early-1", "thread_id": tid,
        "tools": ["read_file"], "auto_tools": False,
    }))
    await asyncio.wait_for(held.wait(), 10)
    assert not TURNS.is_running("early-1")

    status, out = await _req(app, "POST", "/chat/turns/early-1/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True, "pending": True})
    # A second press for the same pending id is harmless.
    assert (await _req(app, "POST", "/chat/turns/early-1/stop"))[0] == 200

    release.set()
    text = await asyncio.wait_for(window, 20)
    lg = app.state.platform.turn_logs.get("early-1")
    await _until(lambda: lg.task.done())
    assert model_calls == [], "the turn called the model after its Stop"
    assert ran == [], "the turn ran a step after its Stop"
    assert lg.status == "stopped"
    assert "event: done" not in text and "event: token" not in text
    # Not even its preparation began (no recall, no reading files).
    assert "event: phase" not in text, text[:200]
    rec = _record(engine, "early-1")
    assert rec.status == "stopped" and rec.stop_asked_at is not None
    assert not TURNS.pending_stop("early-1"), "the kept stop was not used up"
    # No words: nothing is written into the chat (group 18's writer).
    assert _replies(engine, tid, "early-1") == []


async def test_post_lane_a_stop_before_the_turn_is_kept_and_nothing_runs(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    model_calls: list = []
    ran = _counting_tools(app)

    async def complete(**kw):
        model_calls.append(kw)
        return RouteResult(LLMResponse(text="the whole answer"), "mock", "mock")

    app.state.platform.router.complete = complete
    status, out = await _req(app, "POST", "/chat/turns/early-post/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True, "pending": True})

    status, out = await _req(app, "POST", "/chat", {
        "messages": MSG, "turn_id": "early-post", "tools": ["read_file"], "auto_tools": False,
    })
    assert status == 200, out
    assert out.get("stopped") is True and out.get("reply") == ""
    assert model_calls == [] and ran == []
    assert not TURNS.is_running("early-post") and not TURNS.pending_stop("early-post")
    # The POST lane keeps no turn record: nothing was written for it.
    assert _record(engine, "early-post") is None


async def test_post_lane_a_stop_while_it_runs_ends_it(tmp_path):
    """Registered means stoppable: a Stop while the POST lane waits on the
    model ends the turn with the stopped answer (never "stopped" against a
    turn that goes on)."""
    app = create_app(str(tmp_path))
    in_call, never = asyncio.Event(), asyncio.Event()

    async def complete(**kw):
        in_call.set()
        await never.wait()
        return RouteResult(LLMResponse(text="too late"), "mock", "mock")  # pragma: no cover

    app.state.platform.router.complete = complete
    turn = asyncio.create_task(_req(app, "POST", "/chat", {"messages": MSG, "turn_id": "mid-post"}))
    await asyncio.wait_for(in_call.wait(), 10)
    assert TURNS.is_running("mid-post")
    assert await _req(app, "POST", "/chat/turns/mid-post/stop") == (200, {"ok": True, "stopped": True})
    status, out = await asyncio.wait_for(turn, 10)
    assert status == 200 and out.get("stopped") is True and out.get("reply") == ""
    assert not TURNS.is_running("mid-post")


async def test_post_lane_with_no_turn_id_registers_nothing(tmp_path):
    app = create_app(str(tmp_path))
    seen: dict = {}

    async def complete(**kw):
        seen["running"] = TURNS.running_ids()
        return RouteResult(LLMResponse(text="hello"), "mock", "mock")

    app.state.platform.router.complete = complete
    status, out = await _req(app, "POST", "/chat", {"messages": MSG})
    assert status == 200 and out["reply"].startswith("hello")
    assert seen["running"] == [] and "stopped" not in out


# --------------------------------------------------------------------------- #
# 4. A kept Stop for an id never used is forgotten; the store is bounded
# --------------------------------------------------------------------------- #
def test_a_kept_stop_is_forgotten_at_its_time_and_the_store_is_bounded():
    now = [1000.0]
    reg = T.TurnRegistry(clock=lambda: now[0])
    assert reg.stop_or_keep("x") == "pending"
    assert reg.stop_or_keep("x") == "pending"  # a second press: still one
    assert reg.pending_count() == 1
    now[0] += T.PENDING_STOP_TTL_S - 1
    assert reg.pending_stop("x")
    now[0] += 2  # past its time
    assert not reg.pending_stop("x")
    assert not reg.register("x").stopped, "a stop past its time stopped a later turn"

    for i in range(T.PENDING_STOP_MAX + 5):
        reg.stop_or_keep(f"id-{i}")
    assert reg.pending_count() == T.PENDING_STOP_MAX
    assert not reg.pending_stop("id-0") and reg.pending_stop(f"id-{T.PENDING_STOP_MAX + 4}")


def test_a_kept_stop_is_handed_to_the_turn_with_the_first_press_time():
    now = [5.0]
    reg = T.TurnRegistry(clock=lambda: now[0])
    reg.stop_or_keep("y")
    first = reg._pending["y"][1]
    reg.stop_or_keep("y")
    handle = reg.register("y")
    assert handle.stopped and handle.stopped_at == first
    assert not reg.pending_stop("y")
    reg.release("y", handle)
    assert reg.stop_or_keep("y") == "ended"  # ran here and ended: nothing kept
    assert not reg.pending_stop("y")


async def test_a_turn_stopped_at_registration_reads_stopped_and_wakes_its_waiters():
    """What the sidebar relies on (``TURNS.handle``, ``stopped``,
    ``wait_stopped``) holds for a stop kept before the turn registered."""
    reg = T.TurnRegistry()
    reg.stop_or_keep("z")
    handle = reg.register("z")
    assert reg.handle("z") is handle and handle.stopped
    await asyncio.wait_for(handle.wait_stopped(), 1)


async def test_the_route_forgets_a_kept_stop_by_the_injected_clock(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    now = [50.0]
    monkeypatch.setattr(TURNS, "clock", lambda: now[0])
    assert (await _req(app, "POST", "/chat/turns/never-used/stop"))[1].get("pending") is True
    assert TURNS.pending_stop("never-used")
    now[0] += T.PENDING_STOP_TTL_S + 1
    assert not TURNS.pending_stop("never-used")


# --------------------------------------------------------------------------- #
# 5. A turn that already finished: 404, said as finished
# --------------------------------------------------------------------------- #
async def test_a_stop_for_a_finished_reply_is_404_finished_from_memory_and_from_its_record(tmp_path):
    app = create_app(str(tmp_path))
    gate, calls = asyncio.Event(), []
    gate.set()
    app.state.platform.router.stream = _gated_stream(gate, calls)
    await _open(app, {"messages": MSG, "turn_id": "fin-1"})
    lg = app.state.platform.turn_logs.get("fin-1")
    await _until(lambda: lg.task.done())
    assert lg.status == "done"
    status, out = await _req(app, "POST", "/chat/turns/fin-1/stop")
    assert status == 404 and out == {"detail": dt.STOP_FINISHED_DETAIL, "finished": True}
    # Known only from its record (as after a restart): still finished, and
    # no stop was kept or written for it.
    app.state.platform.turn_logs._logs.pop("fin-1")
    with TURNS._lock:
        TURNS._ended.clear()
    status, out = await _req(app, "POST", "/chat/turns/fin-1/stop")
    assert status == 404 and out.get("finished") is True
    assert not TURNS.pending_stop("fin-1")
    assert _record(app.state.platform.engine, "fin-1").stop_asked_at is None


async def test_a_stop_for_a_turn_refused_before_it_ran_says_nothing_ran(tmp_path):
    """Fourth plan G27 (DT3): it used to answer 404 "That reply had already
    finished." for a reply that never ran. Now 200, ``ran: false``, nothing
    written or kept (the page's 200 path says "Stopped.")."""
    app = create_app(str(tmp_path))
    status, _ = await _req(app, "POST", "/chat/stream", {
        "messages": MSG, "skill": "no-such-skill-g19", "turn_id": "refused-1",
    })
    assert status == 404
    status, out = await _req(app, "POST", "/chat/turns/refused-1/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True, "ran": False})
    assert _record(app.state.platform.engine, "refused-1") is None
    assert not TURNS.pending_stop("refused-1")


# --------------------------------------------------------------------------- #
# Contract point 3 (SB2, chat side): Allow pressed after Stop does nothing
# --------------------------------------------------------------------------- #
async def test_allow_after_stop_on_a_waiting_card_never_runs_the_call(tmp_path, monkeypatch):
    """A turn parked on an ordinary card; Stop, then Allow inside the same
    poll (or from another window): the call is refused through the real
    registry (recorded, never run) and the reply ends as stopped."""
    from sqlmodel import select

    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.daemon.routes import chat as chat_routes
    from tests.test_chat_approvals_v1187 import _drive_stream
    from tests.test_wave4_chat_recovery_v1312 import _one_call_stream, _stub_execute

    # "Inside the same poll" is made true, not hoped for: with the 1 s poll,
    # a slow runner let the slice end between Stop and Allow, the parked
    # wait took the Stop first and the Allow answered 404 (it passed only on
    # CI's retry). A poll far longer than the test keeps both in one slice.
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 60.0)

    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    ran = _stub_execute(app.state.platform, monkeypatch, "shell")
    stream, state = _one_call_stream("shell", {"command": "python -c \"print('stop-test')\""})
    app.state.platform.router.stream = stream
    answers: list = []

    async def stop_then_allow(data):
        answers.append(await _req(app, "POST", "/chat/turns/card-1/stop"))
        answers.append(await _req(app, "POST", f"/chat/approvals/{data['id']}", {"decision": "once"}))

    # Arming by sentence ASK-arms the shell (a named tool would be granted);
    # a command that runs Python cards (a read-only `echo` would not).
    frames = await _drive_stream(app, {
        "messages": [{"role": "user", "content": "run the command `python -c print` in the terminal for me"}],
        "auto_tools": True, "turn_id": "card-1",
    }, stop_then_allow)
    assert [a[0] for a in answers] == [200, 200], (answers, [f[0] for f in frames], ran)
    kinds = [ev for ev, _ in frames]
    assert "approval" in kinds
    assert ran == [], "the Allow pressed after Stop ran the call"
    assert "done" not in kinds and state["n"] == 1  # no second model call
    lg = app.state.platform.turn_logs.get("card-1")
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped"
    with session_scope(engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)) if r.tool == "shell"]
    assert len(rows) == 1 and rows[0].ok is False, [(r.ok, r.output) for r in rows]
    assert "stopped the reply" in rows[0].output


# --------------------------------------------------------------------------- #
# Group 18 kept: a stopped reply with words is written home as what was said
# --------------------------------------------------------------------------- #
async def test_a_stopped_reply_with_words_is_written_home_with_its_mark(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "home-1")
    gate = asyncio.Event()
    app.state.platform.router.stream = _gated_stream(gate, [])
    drop = asyncio.Event()
    await _open(app, {"messages": MSG, "turn_id": "home-1", "thread_id": tid},
                drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get("home-1")
    await _until(lambda: not lg.watched())
    assert (await _req(app, "POST", "/chat/turns/home-1/stop"))[0] == 200
    gate.set()
    await _until(lambda: lg.task.done() and _replies(engine, tid, "home-1"))
    replies = _replies(engine, tid, "home-1")
    assert [(m["content"], m.get("interrupted")) for m in replies] == [("first half,", True)]
    assert _record(engine, "home-1").stop_asked_at is not None
    assert json.loads(_record(engine, "home-1").frames_json)[-1][0] != "done"
