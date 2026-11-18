"""Fourth fix plan, group 27 (the turn half): Stop says who, and is quick.

* CS6/DT4: who stopped a reply is a recorded fact — "you", "time_limit",
  "emergency", or "" (not recorded) — on the registry handle, the live log,
  the turn's record (``ChatTurnRecord.stop_author``) and the reader
  ``durable_turns.stop_fact``. No stop function defaults to "you": only the
  user's own presses name it. ``stop_asked`` stays true for EVERY stop.
* DT3: a Stop for a turn refused before it ran answers 200
  ``{stopped: true, ran: false}`` and writes nothing (it used to answer
  "That reply had already finished.").
* DT6: the stop route stops the turn in memory AT ONCE, then writes the mark
  (bounded), then answers — a slow disk never lets another step start.
* DT5: a turn whose chat was deleted writes no record, on a normal end and on
  a shutdown alike, and a stop mark written after the delete leaves no row.
* C-DT-2 (owner decision D5): deleting a chat stops its running reply, as
  the user's own stop; an agent run it handed off to keeps going.
* A scan: every stop call in ``src/`` names who stops (``by=``).

Real ``TurnLogs``, real routes through the app factory, real SQLite in
``tmp_path``; the model is a stand-in stream that counts its calls. The
disk-write hold (DT6) is a ``threading.Event`` on ``write_stop_mark`` — the
disk stand-in, not the layer that decides.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path

import pytest

from iron_jarvis.core import turns as T
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatTurnRecord, Session
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from tests.test_durable_chat_turns_v1336 import _gated_stream, _open, _until
from tests.test_third_plan_g18_reply_home import _logs, _new_chat, _req

MSG = [{"role": "user", "content": "summarise the report in my folder"}]


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-wide: no test may stop another's turn."""

    def _clear():
        for tid in TURNS.running_ids():
            TURNS.release(tid)
        with TURNS._lock:
            TURNS._pending.clear()
            TURNS._ended.clear()
            getattr(TURNS, "_refused", {}).clear()

    _clear()
    yield
    _clear()


def _naive(when):
    """A time as the database gives it back (SQLite keeps no time zone)."""
    return when.replace(tzinfo=None) if when is not None else None


def _fact(fact):
    return None if fact is None else (_naive(fact[0]), fact[1])


def _record(engine, turn_id: str):
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        if row is not None:
            db.expunge(row)
        return row


async def _running_turn(app, turn_id: str, *, thread_id: str = "") -> tuple:
    """A named turn that has written its first words and waits on its model."""
    gate, calls = asyncio.Event(), []
    app.state.platform.router.stream = _gated_stream(gate, calls)
    drop = asyncio.Event()
    body = {"messages": MSG, "turn_id": turn_id}
    if thread_id:
        body["thread_id"] = thread_id
    await _open(app, body, drop=drop, stop_after="first half")
    lg = app.state.platform.turn_logs.get(turn_id)
    assert lg is not None and lg.status == "running"
    return lg, gate, calls


# --------------------------------------------------------------------------- #
# CS6 / DT4 — who stopped it is a recorded fact
# --------------------------------------------------------------------------- #
async def test_the_users_stop_through_the_route_is_recorded_as_you(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    lg, gate, _calls = await _running_turn(app, "who-you")
    assert await _req(app, "POST", "/chat/turns/who-you/stop") == (200, {"ok": True, "stopped": True})
    gate.set()
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped" and lg.stop_author == "you"
    rec = _record(engine, "who-you")
    assert rec.stop_author == "you" and rec.stop_asked_at is not None
    live = await asyncio.to_thread(dt.stop_fact, engine, "who-you")
    assert _fact(live) == (_naive(rec.stop_asked_at), "you")
    # After a restart: nothing in memory, the record answers.
    dt._LIVE.pop("who-you", None)
    after = await asyncio.to_thread(dt.stop_fact, engine, "who-you")
    assert _fact(after) == (_naive(rec.stop_asked_at), "you")
    assert await asyncio.to_thread(dt.stop_asked, engine, "who-you") is True


@pytest.mark.parametrize("who", ["time_limit", "emergency", ""])
async def test_a_stop_through_the_registry_records_its_own_author(tmp_path, who):
    """Iron Jarvis's own stops (the night's time limit, Emergency stop) and a
    caller that does not say: recorded as such, never as "you" — and still a
    stop (``stop_asked``), so nothing carries the reply on."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = f"who-{who or 'unknown'}"
    lg, gate, _calls = await _running_turn(app, tid)
    assert TURNS.stop(tid, by=who)
    gate.set()
    await _until(lambda: lg.task.done())
    assert lg.status == "stopped" and lg.stop_author == who
    rec = _record(engine, tid)
    assert rec.stop_asked_at is not None and rec.stop_author == who
    dt._LIVE.pop(tid, None)
    fact = await asyncio.to_thread(dt.stop_fact, engine, tid)
    assert fact is not None and fact[1] == who
    assert await asyncio.to_thread(dt.stop_asked, engine, tid) is True


async def test_a_users_stop_after_iron_jarviss_own_keeps_the_first(tmp_path):
    """The first stop wins, in memory and on the record: a Stop pressed after
    the time limit stopped the reply does not make it "you"."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    lg, gate, _calls = await _running_turn(app, "who-first")
    assert TURNS.stop("who-first", by="time_limit")
    first = TURNS.handle("who-first").stopped_at
    assert (await _req(app, "POST", "/chat/turns/who-first/stop"))[0] == 200
    assert lg.stop_author == "time_limit" and lg.stop_asked_at == first
    gate.set()
    await _until(lambda: lg.task.done())
    rec = _record(engine, "who-first")
    assert (_naive(rec.stop_asked_at), rec.stop_author) == (_naive(first), "time_limit")


async def test_stop_and_archive_is_the_users_stop(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "who-arch")
    lg, gate, _calls = await _running_turn(app, "who-arch", thread_id=tid)
    status, out = await _req(app, "POST", f"/chat/threads/{tid}/archive", {"stop": True})
    assert status == 200, out
    gate.set()
    await _until(lambda: lg.task.done())
    assert lg.stop_author == "you"
    assert _record(engine, "who-arch").stop_author == "you"


def test_no_stop_function_defaults_to_you(tmp_path):
    """The registry, the handle, a kept Stop and the record writer: a caller
    that does not say who gets "" (not recorded), never "you"."""
    reg = T.TurnRegistry(clock=lambda: 10.0)
    h = reg.register("a")
    assert reg.stop("a") and h.stopped and h.stop_author == ""
    h.stop(by="you")
    assert h.stop_author == "", "a second stop rewrote who stopped it"
    reg.stop_or_keep("kept-unknown")
    assert reg.register("kept-unknown").stop_author == ""
    reg.stop_or_keep("kept-you", by="you")
    assert reg.register("kept-you").stop_author == "you"
    assert reg.stop_or_keep("b", by="no-such-author") == "pending"
    assert reg.register("b").stop_author == "", "an unknown author was recorded"
    lg = dt.TurnLog("c")
    lg.ask_stop(dt.utcnow())
    assert lg.stop_asked and lg.stop_author == ""

    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    when = dt.utcnow()
    assert dt.write_stop_mark(engine, "plain-mark", when)
    assert _fact(dt.stop_fact(engine, "plain-mark")) == (_naive(when), "")
    # A record older than the column (no author) reads as not recorded.
    assert dt.write_stop_mark(engine, "old-mark", when, "time_limit")
    with session_scope(engine) as db:
        row = db.get(ChatTurnRecord, "old-mark")
        row.stop_author = None
        db.add(row)
        db.commit()
    assert _fact(dt.stop_fact(engine, "old-mark")) == (_naive(when), "")
    assert dt.stop_fact(engine, "never-stopped") is None


# --------------------------------------------------------------------------- #
# DT3 — a Stop for a turn refused before it ran
# --------------------------------------------------------------------------- #
async def test_a_stop_for_a_turn_refused_before_it_ran_says_nothing_ran(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    status, _ = await _req(app, "POST", "/chat/stream", {
        "messages": MSG, "skill": "no-such-skill-g27", "turn_id": "refused-27",
    })
    assert status == 404
    status, out = await _req(app, "POST", "/chat/turns/refused-27/stop")
    assert (status, out) == (200, {"ok": True, "stopped": True, "ran": False})
    assert _record(engine, "refused-27") is None, "a record was written for a turn that never ran"
    assert not TURNS.pending_stop("refused-27"), "a Stop was kept for a turn that never ran"

    # The same id sent again (the user sends once more) runs normally.
    gate, calls = asyncio.Event(), []
    gate.set()
    app.state.platform.router.stream = _gated_stream(gate, calls)
    await _open(app, {"messages": MSG, "turn_id": "refused-27"})
    lg = app.state.platform.turn_logs.get("refused-27")
    await _until(lambda: lg.task.done())
    assert lg.status == "done" and len(calls) == 1
    assert not lg.stop_asked
    # ...and once it has finished, a Stop is "already finished" again.
    status, out = await _req(app, "POST", "/chat/turns/refused-27/stop")
    assert status == 404 and out == {"detail": dt.STOP_FINISHED_DETAIL, "finished": True}


def test_the_registry_files_a_refused_turn_apart_from_an_ended_one():
    now = [100.0]
    reg = T.TurnRegistry(clock=lambda: now[0])
    assert reg.stop_or_keep("r1", by="you") == "pending"
    reg.note_refused("r1")
    assert not reg.pending_stop("r1"), "the kept Stop survived the refusal"
    assert reg.stop_or_keep("r1", by="you") == "refused"
    assert not reg.pending_stop("r1") and not reg.recently_ended("r1")
    # The same id registering again clears it.
    h = reg.register("r1")
    assert not h.stopped
    reg.release("r1", h)
    assert reg.stop_or_keep("r1", by="you") == "ended"
    # Same lifetime as the ended ids.
    reg.note_refused("r2")
    now[0] += T.ENDED_KEEP_S + 1
    assert reg.stop_or_keep("r2", by="you") == "pending"


# --------------------------------------------------------------------------- #
# DT6 — stopped at once, written right after, answered after the write
# --------------------------------------------------------------------------- #
async def test_stop_acts_before_the_disk_write_and_answers_after_it(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    lg, gate, calls = await _running_turn(app, "quick-1")
    entered, release = threading.Event(), threading.Event()
    real_write = dt.write_stop_mark

    def held_write(*args, **kwargs):
        entered.set()
        release.wait(10)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(dt, "write_stop_mark", held_write)
    handle = TURNS.handle("quick-1")
    assert handle is not None and not handle.stopped
    press = asyncio.create_task(_req(app, "POST", "/chat/turns/quick-1/stop"))
    await _until(entered.is_set)
    assert entered.is_set()
    try:
        assert not press.done()
        assert handle.stopped, "the turn waited for the disk before it stopped"
        gate.set()  # the model would go on: the stopped turn must not
        await _until(lambda: lg.task.done())
        assert lg.task.done() and lg.status == "stopped"
        assert len(calls) == 1 and "second half" not in lg.snapshot()
    finally:
        release.set()
    assert await press == (200, {"ok": True, "stopped": True})
    rec = _record(engine, "quick-1")
    assert _naive(rec.stop_asked_at) == _naive(lg.stop_asked_at) and rec.stop_author == "you"


# --------------------------------------------------------------------------- #
# DT5 — a deleted chat's turn leaves no record
# --------------------------------------------------------------------------- #
async def _deaf_turn(app, turn_id: str, thread_id: str) -> "dt.TurnLog":
    """A daemon-owned turn of a saved chat whose frames do not end on Stop
    (a step still running): the delete's stop does not end it before the
    test is done with it. Real ``TurnLogs``; the lane is a stand-in."""
    forever = asyncio.Event()

    async def frames():
        yield dt.sse("token", {"text": "first words "})
        await forever.wait()

    logs = _logs(app)
    lg = logs.start(turn_id, thread_id, frames())
    await _until(lambda: _record(app.state.platform.engine, turn_id) is not None)
    return lg


async def test_a_deleted_chats_turn_writes_no_record_at_its_end(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "gone-1")
    lg = await _deaf_turn(app, "gone-1", tid)
    assert _record(engine, "gone-1") is not None
    assert (await _req(app, "DELETE", f"/chat/threads/{tid}"))[0] == 200
    assert _record(engine, "gone-1") is None
    lg.add(dt.sse("token", {"text": "more words "}))
    lg.maybe_persist(engine)
    await lg.persist_now(engine)
    assert _record(engine, "gone-1") is None, "the deleted chat's turn wrote its record back"
    lg.task.cancel()


async def test_a_deleted_chats_turn_cut_by_a_shutdown_leaves_no_record(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "gone-2")
    lg = await _deaf_turn(app, "gone-2", tid)
    assert (await _req(app, "DELETE", f"/chat/threads/{tid}"))[0] == 200
    assert lg.status == "running"
    await _logs(app).shutdown()
    assert lg.status == "interrupted"
    assert _record(engine, "gone-2") is None, "an 'interrupted' record stayed for a deleted chat"


async def test_a_stop_mark_written_after_the_delete_leaves_no_bare_row(tmp_path, monkeypatch):
    """Stop pressed, the turn ends, the chat is deleted, THEN the mark write
    lands (DT6 makes this order possible): no record."""
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "gone-3")
    lg, gate, _calls = await _running_turn(app, "gone-3", thread_id=tid)
    entered, release = threading.Event(), threading.Event()
    real_write = dt.write_stop_mark

    def held_write(*args, **kwargs):
        entered.set()
        release.wait(10)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(dt, "write_stop_mark", held_write)
    press = asyncio.create_task(_req(app, "POST", "/chat/turns/gone-3/stop"))
    await _until(entered.is_set)
    try:
        gate.set()
        await _until(lambda: lg.task.done())
        assert lg.task.done()
        assert (await _req(app, "DELETE", f"/chat/threads/{tid}"))[0] == 200
        assert _record(engine, "gone-3") is None
    finally:
        release.set()
    assert (await press)[0] == 200
    assert _record(engine, "gone-3") is None, "the late stop mark left a bare record"


async def test_a_stop_pressed_after_the_delete_writes_no_record(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "gone-4")
    lg = await _deaf_turn(app, "gone-4", tid)
    TURNS.register("gone-4")  # the deaf lane's registry handle
    lg.handle = TURNS.handle("gone-4")
    assert (await _req(app, "DELETE", f"/chat/threads/{tid}"))[0] == 200
    assert _record(engine, "gone-4") is None
    assert (await _req(app, "POST", "/chat/turns/gone-4/stop"))[0] == 200
    assert _record(engine, "gone-4") is None, "the stop route wrote a record for a deleted chat"
    lg.task.cancel()


# --------------------------------------------------------------------------- #
# C-DT-2 (D5) — deleting a chat stops its running reply; a hand-off run goes on
# --------------------------------------------------------------------------- #
async def test_deleting_a_chat_stops_its_running_reply_as_the_users_stop(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = await _new_chat(app, "del-stop")
    lg, gate, calls = await _running_turn(app, "del-stop", thread_id=tid)
    assert (await _req(app, "DELETE", f"/chat/threads/{tid}"))[0] == 200
    # The gate stays shut: only the delete's stop can end the turn.
    await _until(lambda: lg.task.done(), seconds=5)
    assert lg.task.done(), "deleting the chat did not stop its reply"
    assert lg.status == "stopped" and lg.stop_author == "you"
    assert len(calls) == 1 and "second half" not in lg.snapshot()
    assert _record(engine, "del-stop") is None
    gate.set()


async def test_deleting_a_chat_does_not_stop_the_agent_run_it_handed_off_to(tmp_path):
    app = create_app(str(tmp_path))
    tid = await _new_chat(app, "del-hand")
    started: list = []

    def fake_spawn(session_id, coro):
        coro.close()
        started.append(session_id)
        return object()

    orch = app.state.orchestrator
    orch.spawn_managed = fake_spawn
    ended: list = []
    orch.stop_with_reason = lambda *a, **k: ended.append(("stop", a)) or True
    orch.cancel_session = lambda *a, **k: ended.append(("cancel", a))
    status, out = await _req(app, "POST", "/sessions", {
        "task": "rename the files in my folder", "wait": False, "origin": "chat",
        "chat_thread_id": tid, "handed_from_turn": "del-hand",
    })
    assert status == 200, out
    assert started == [out["id"]]
    lg, gate, _calls = await _running_turn(app, "del-hand-2", thread_id=tid)
    assert (await _req(app, "DELETE", f"/chat/threads/{tid}"))[0] == 200
    await _until(lambda: lg.task.done(), seconds=5)
    assert lg.status == "stopped"  # the chat's own reply stopped...
    assert ended == [], "the delete stopped the hand-off's agent run"
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, out["id"])
        assert row is not None and str(row.status).lower().find("cancel") < 0
    gate.set()


# --------------------------------------------------------------------------- #
# The scan — every stop names who stops
# --------------------------------------------------------------------------- #
SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"

#: Call sites that do not name ``by=`` YET, per file: the count allowed and
#: who closes it. "27B": the night backstop, Emergency stop and the step stop
#: (``harness/stall.stop_step`` gains ``by=``); "27C": Build's stop-step and
#: the attempts; "27D": the sidebar. The lead removes each entry as its part
#: lands. "bench" is permanent: the benchmark runner's stops are its own and
#: are recorded as "" (not recorded), which is true.
UNNAMED_ALLOWED: dict[str, tuple[int, str]] = {
    "harness_bench/runners.py": (3, "bench"),
}

#: Receivers whose ``.stop(`` is a turn handle's (``TurnHandle.stop``).
_HANDLE_NAMES = {"h", "handle"}


def _is_stop_call(node: ast.Call) -> bool:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in ("stop_or_keep", "stop_step")
    if not isinstance(f, ast.Attribute):
        return False
    if f.attr in ("stop_or_keep", "stop_step"):
        return True
    if f.attr != "stop":
        return False
    v = f.value
    if isinstance(v, ast.Name) and (v.id == "TURNS" or v.id in _HANDLE_NAMES):
        return True
    # ``TURNS.handle(x).stop()`` and the like.
    return isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "handle"


def _unnamed_stop_calls() -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_stop_call(node):
                if not any(k.arg == "by" for k in node.keywords):
                    out.setdefault(rel, []).append(node.lineno)
    return out


def test_every_stop_call_names_who_stops():
    found = _unnamed_stop_calls()
    over = {
        rel: lines for rel, lines in found.items()
        if len(lines) > UNNAMED_ALLOWED.get(rel, (0, ""))[0]
    }
    assert not over, (
        "a stop call does not say who stops (pass by=\"you\" / \"time_limit\" /"
        f" \"emergency\", or \"\" only when it truly is not known): {over}"
    )


def test_the_scan_sees_the_stops_it_guards():
    """The scan is not blind: the user's own stops are found AND named."""
    named = 0
    for rel in ("daemon/routes/chat.py", "core/turns.py"):
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_stop_call(node):
                assert any(k.arg == "by" for k in node.keywords), (rel, node.lineno)
                named += 1
    assert named >= 7, named
