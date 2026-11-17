"""Third north-star fix plan: four small truths (RB10, CS8, SB8, SB9).

- RB10: the bell, the tray and the phone said "Your coding session finished"
  for a reply cut at its step limit, at its reply limit (minutes or dollars),
  at the model's length limit, or cut off mid-answer. The notice's outcome now
  says what happened: "done" only for a reply that really ended; "needs_you"
  for one that stopped at a limit and waits for the user's Keep going;
  "failed" ("could not finish") for one that was cut off. A reply the user
  stopped still sends no notice at all.
- CS8: a chat turn's usage row was written on the event loop, and a failed
  write was dropped with no log line. The write now runs on a worker thread
  (the turn waits for it, bounded), and a failure is logged once at WARNING
  with the turn's id; the turn itself ends normally. Both chat lanes.
- SB8: the sidebar ended the turn on screen for ANY error frame that was not
  about voice, while the daemon's reply kept running. The daemon now marks an
  error that does not end the reply (``ends: false``); the reply's own end and
  a refused Send carry no mark and end it as before (the panel's runtime test
  in ``dashboard/__tests__/sidepanel-runtime-v1242.test.ts`` pins the panel).
- SB9: a click whose answer was lost read "Could not click …" followed by the
  sentence written for the model ("ACTION_TIMEOUT: … Read the page before
  doing it again."). The sidebar's line now has its own plain sentence; the
  model still reads its own text.

Driven through the REAL reply watch (``routes/build.recorded_turn``) with a
stand-in frame stream, the REAL chat lanes on the mock model (in process, on
this test's own event loop), and the REAL ``PanelTurns`` with a stand-in lane
at its seam. Nothing reaches a real model, service or browser, and no value
is shaped like a real credential.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time as _real_time
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import UNKNOWN_OUTCOME_MESSAGE, BrowserError, BrowserErrorCode
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.browser.tools import BrowserClickTool
from iron_jarvis.comm.notifier import build_finished_title, format_event
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn as chat_turn_mod
from iron_jarvis.daemon import durable_turns
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.hosted import news as hosted_news
from tests.test_build_sessions_v1367 import new_session, until
from tests.test_chat_turn_stop_v1241 import _asgi_post, _scope

# --------------------------------------------------------------------------- #
# RB10: the finished notice says how the reply ended
# --------------------------------------------------------------------------- #


class Clock:
    """The build routes' ``time``, ``offset`` seconds ahead of the real one
    (a long reply is never waited out for real)."""

    def __init__(self) -> None:
        self.offset = 0.0

    def time(self) -> float:
        return _real_time.time() + self.offset

    def __getattr__(self, name):
        return getattr(_real_time, name)


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(build_routes, "time", c)
    return c


@pytest.fixture()
def coding(tmp_path):
    app = create_app(str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    sid = new_session(app, work, title="Tidy the notes")["id"]
    return app, sid


async def _notices_after(app, sid: str, turn_id: str, clock: Clock, end: "tuple[str, dict] | None") -> list[dict]:
    """One reply through the real reply watch that 'takes' ninety seconds and
    ends on ``end`` (None: no end frame at all — the user's Stop)."""

    async def frames():
        yield durable_turns.sse("token", {"text": "Working on the notes."})
        clock.offset += 90
        if end is not None:
            yield durable_turns.sse(*end)

    platform = app.state.platform
    async for _ in build_routes.recorded_turn(platform.engine, sid, turn_id, frames(), platform=platform):
        pass
    return [
        dict(e.payload) for e in platform.event_bus.history
        if e.type == EventType.BUILD_TURN_FINISHED and (e.payload or {}).get("turn_id") == turn_id
    ]


FINISHED = "Your coding session finished"
LIMIT_TITLE = "Your coding session stopped at its limit and needs you"


@pytest.mark.parametrize(
    ("outcome", "title"),
    [
        ("done", FINISHED),
        ("failed", "Your coding session could not finish"),
        # The reply ended; its own finish check found work still open.
        ("needs_you", "Your coding session finished, but something needs you"),
        # Third plan RB10: stopped at its step or reply limit, waiting for Keep going.
        ("limit", LIMIT_TITLE),
    ],
)
def test_rb10_the_bell_and_the_phone_say_each_ending_in_its_own_words(outcome, title):
    assert build_finished_title({"outcome": outcome}) == title
    line = format_event({"type": EventType.BUILD_TURN_FINISHED, "session_id": "build:bs_1",
                         "payload": {"thread_id": "bs_1", "title": "Tidy the notes", "seconds": 90,
                                     "outcome": outcome}})
    assert line.startswith(title), line


@pytest.mark.parametrize(
    ("stop", "outcome"),
    [
        ("completed", "done"),
        ("done", "done"),
        # Cut at its turn limit: stopped at its limit, never "finished".
        ("max_turns", "limit"),
        ("failed", "failed"),
        ("error", "failed"),
        # Stopped by the user or cut off by a shutdown: no notice, as in Chat › Code.
        ("interrupted", ""),
        ("", ""),
    ],
)
def test_rb10_build_code_replies_are_judged_by_the_same_rule(stop, outcome):
    assert hosted_news.outcome_of(stop) == outcome


async def test_rb10_a_reply_that_really_ended_is_finished(coding, clock):
    app, sid = coding
    [notice] = await _notices_after(app, sid, "bt-ok", clock, ("done", {"reply": "The notes are tidy."}))
    assert notice["outcome"] == "done"
    assert build_finished_title(notice) == FINISHED


@pytest.mark.parametrize(
    "done",
    [
        {"reply": "Tidied the first half.", "step_limit": {"limit": 30}},
        {"reply": "Tidied the first half.", "cap": {"kind": "time", "limit": 20}},
        {"reply": "Tidied the first half.", "cap": {"kind": "cost", "limit": 2.0}},
    ],
    ids=["step-limit", "minutes-limit", "dollars-limit"],
)
async def test_rb10_a_reply_stopped_at_its_limit_needs_you_never_finished(coding, clock, done):
    app, sid = coding
    [notice] = await _notices_after(app, sid, "bt-limit", clock, ("done", done))
    assert notice["outcome"] == "limit", notice
    assert build_finished_title(notice) == LIMIT_TITLE
    assert "finished" not in build_finished_title(notice)


@pytest.mark.parametrize(
    "done",
    [
        {"reply": "Tidied the first", "truncated": True},
        {"reply": "Tidied the first", "route": {"provider": "mock", "model": "mock", "cut_off": {"recovered": False}}},
    ],
    ids=["length-limit", "cut-off-mid-answer"],
)
async def test_rb10_a_reply_that_was_cut_off_could_not_finish(coding, clock, done):
    app, sid = coding
    [notice] = await _notices_after(app, sid, "bt-cut", clock, ("done", done))
    assert notice["outcome"] == "failed", notice
    assert "finished" not in build_finished_title(notice)


async def test_rb10_a_cut_off_that_was_recovered_is_finished(coding, clock):
    app, sid = coding
    done = {"reply": "The notes are tidy.", "route": {"provider": "mock", "model": "mock",
                                                      "cut_off": {"recovered": True}}}
    [notice] = await _notices_after(app, sid, "bt-rec", clock, ("done", done))
    assert notice["outcome"] == "done"


async def test_rb10_a_reply_the_user_stopped_sends_no_finished_notice(coding, clock):
    app, sid = coding
    assert await _notices_after(app, sid, "bt-stop", clock, None) == []


# --------------------------------------------------------------------------- #
# CS8: the usage row is written off the loop, and a failed write is said
# --------------------------------------------------------------------------- #


class _UsageSpy:
    """``chat_turn.session_scope`` with its ``add`` watched: the thread a
    chat usage row is added on, and (``fail``) a write that breaks."""

    def __init__(self, fail: bool = False, gate: "threading.Event | None" = None) -> None:
        self.fail = fail
        self.gate = gate
        self.threads: list[int] = []
        real = chat_turn_mod.session_scope
        spy = self

        class _Db:
            def __init__(self, db) -> None:
                self._db = db

            def add(self, obj):
                if isinstance(obj, AgentRun) and obj.session_id == "chat":
                    spy.threads.append(threading.get_ident())
                    if spy.gate is not None:
                        spy.gate.wait(30)  # a slow disk (bounded: never hangs the run)
                    if spy.fail:
                        raise RuntimeError("the database file is locked")
                return self._db.add(obj)

            def __getattr__(self, name):
                return getattr(self._db, name)

        @contextlib.contextmanager
        def scope(engine):
            with real(engine) as db:
                yield _Db(db)

        self.scope = scope


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def chat_log():
    handler = _Records()
    logger = chat_turn_mod.log
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old)


async def _stream(app, body: dict) -> str:
    """``POST /chat/stream`` in process, the client staying for the whole
    answer; returns the raw SSE text."""
    raw = json.dumps(body).encode()
    sent = {"done": False}
    gone = asyncio.Event()

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    chunks: list[bytes] = []

    async def send(msg):
        if msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    try:
        await asyncio.wait_for(app(_scope("/chat/stream", raw), receive, send), timeout=60)
    finally:
        gone.set()
    return b"".join(chunks).decode("utf-8", "replace")


def _chat_rows(app) -> list[AgentRun]:
    with session_scope(app.state.platform.engine) as db:
        return [r for r in db.exec(select(AgentRun)).all() if r.session_id == "chat"]


MSGS = [{"role": "user", "content": "hello"}]


async def test_cs8_both_lanes_write_the_usage_row_off_the_event_loop(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    spy = _UsageSpy()
    monkeypatch.setattr(chat_turn_mod, "session_scope", spy.scope)
    loop_thread = threading.get_ident()

    status, out = await _asgi_post(app, "/chat", {"messages": MSGS, "thread_id": "t-post", "turn_id": "cs8-post"})
    assert status == 200, out
    # The POST lane answers only once its row is written (the turn waits for it).
    assert len(spy.threads) == 1, spy.threads
    raw = await _stream(app, {"messages": MSGS, "thread_id": "t-stream", "turn_id": "cs8-stream"})
    assert "event: done" in raw, raw[-400:]
    assert await until(lambda: len(spy.threads) == 2, 30), spy.threads
    assert loop_thread not in spy.threads, "a chat usage row was written on the event loop's thread"
    monkeypatch.undo()
    assert sorted(str(r.thread_id) for r in _chat_rows(app)) == ["t-post", "t-stream"]


async def test_cs8_the_turn_ends_only_once_its_usage_row_is_written(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    gate = threading.Event()
    spy = _UsageSpy(gate=gate)
    monkeypatch.setattr(chat_turn_mod, "session_scope", spy.scope)
    try:
        for path, turn in (("/chat", "cs8-post"), ("/chat/stream", "cs8-stream")):
            gate.clear()
            body = {"messages": MSGS, "thread_id": f"t-{turn}", "turn_id": turn}
            call = _asgi_post(app, path, body) if path == "/chat" else _stream(app, body)
            task = asyncio.ensure_future(call)
            n = len(spy.threads)
            assert await until(lambda: len(spy.threads) > n, 30), f"{path}: no usage row was started"
            # The write is held (a slow disk) on its own thread: the loop runs
            # on (this wait is served by it), and the turn waits for its row.
            ended, _ = await asyncio.wait({task}, timeout=1.0)
            assert not ended, f"{path}: the turn ended before its usage row was written"
            gate.set()
            await asyncio.wait_for(task, 30)
            # (``_chat_rows`` reads through the real session_scope.)
            assert [r for r in _chat_rows(app) if r.thread_id == f"t-{turn}"], path
    finally:
        gate.set()


async def test_cs8_a_failed_usage_write_is_logged_once_and_the_turn_ends_normally(tmp_path, monkeypatch, chat_log):
    app = create_app(str(tmp_path / "home"))
    spy = _UsageSpy(fail=True)
    monkeypatch.setattr(chat_turn_mod, "session_scope", spy.scope)

    status, out = await _asgi_post(app, "/chat", {"messages": MSGS, "thread_id": "t-post", "turn_id": "cs8-post"})
    assert status == 200 and out.get("reply"), out
    raw = await _stream(app, {"messages": MSGS, "thread_id": "t-stream", "turn_id": "cs8-stream"})
    assert "event: done" in raw and "event: error" not in raw, raw[-400:]

    def warned(turn_id: str) -> list[logging.LogRecord]:
        return [r for r in chat_log.records if r.levelno == logging.WARNING and turn_id in r.getMessage()]

    assert await until(lambda: warned("cs8-stream"), 30), [r.getMessage() for r in chat_log.records]
    assert len(warned("cs8-post")) == 1, [r.getMessage() for r in chat_log.records]
    assert len(warned("cs8-stream")) == 1, [r.getMessage() for r in chat_log.records]
    monkeypatch.undo()
    assert _chat_rows(app) == []


# --------------------------------------------------------------------------- #
# SB8 (the daemon's side) and SB9, through the real PanelTurns
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=False)
def clean_turns():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


class _Conn:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    async def send(self, frame: dict) -> None:
        self.frames.append((str(frame.get("event") or ""), dict(frame.get("payload") or {})))

    def of(self, event: str) -> list[dict[str, Any]]:
        return [p for e, p in self.frames if e == event]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _lane_of(frames: list[str], hold: "asyncio.Event | None" = None):
    async def lane(platform, personas, body, **kw):
        async def gen():
            for f in frames:
                yield f
            if hold is not None:
                await hold.wait()
                yield _sse("done", {"reply": "Done."})

        return gen()

    return lane


async def _turn(monkeypatch, frames: list[str]) -> _Conn:
    import iron_jarvis.daemon.chat_stream as chat_stream

    monkeypatch.setattr(chat_stream, "stream_chat_turn", _lane_of(frames))
    turns = PanelTurns(SimpleNamespace(browser=None))
    conn = _Conn()
    await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "press Search on this page"})
    task = turns._task
    assert task is not None, "the send started no turn"
    await asyncio.wait_for(task, 10)
    return conn


def _lost_click_output() -> str:
    """What the REAL click tool answers the model when the browser's answer
    was lost after the click went out."""
    exc = BrowserError(BrowserErrorCode.ACTION_TIMEOUT.value)
    return BrowserClickTool(None)._refusal_from(exc, outcome_unknown=True).error


async def test_sb9_a_click_whose_answer_was_lost_reads_in_plain_words(monkeypatch, clean_turns):
    output = _lost_click_output()
    assert UNKNOWN_OUTCOME_MESSAGE in output  # the model's own sentence
    args = {"target": {"role": "button", "name": "Search"}}
    conn = await _turn(monkeypatch, [
        _sse("tool_call", {"id": "c1", "name": "browser_click", "args": args}),
        _sse("tool_call", {"id": "c1", "name": "browser_click", "status": "finished", "ok": False,
                           "output": output}),
        _sse("done", {"reply": "I could not tell whether Search was pressed."}),
    ])
    step = [p for p in conn.of(P.PANEL_EVENT_TOOL) if p.get("status") == "finished"][-1]
    # Never the model's sentence, its code, or "Could not" over a step that may have happened.
    assert UNKNOWN_OUTCOME_MESSAGE not in step["text"], step["text"]
    assert "ACTION_TIMEOUT" not in step["text"] and not step["text"].startswith("Could not"), step["text"]
    # The finished frame carries no arguments (the lane's own shape).
    what = panel_mod.describe_browser_call("browser_click", None)
    assert step["text"] == panel_mod.ANSWER_LOST_STEP.format(what=what), step
    assert step["ok"] is False


async def test_sb9_an_ordinary_failed_step_still_says_why(monkeypatch, clean_turns):
    conn = await _turn(monkeypatch, [
        _sse("tool_call", {"id": "c1", "name": "browser_click", "status": "finished", "ok": False,
                           "output": "That button is not on the page any more."}),
        _sse("done", {"reply": "Search is gone."}),
    ])
    step = [p for p in conn.of(P.PANEL_EVENT_TOOL) if p.get("status") == "finished"][-1]
    what = panel_mod.describe_browser_call("browser_click", None)
    assert step["text"] == f"Could not {what}. That button is not on the page any more.", step


async def test_sb8_the_replys_own_error_ends_the_turn_on_screen(monkeypatch, clean_turns):
    conn = await _turn(monkeypatch, [
        _sse("token", {"text": "Looking"}),
        _sse("error", {"detail": "Iron Jarvis could not reach that page."}),
    ])
    [err] = conn.of(P.PANEL_EVENT_ERROR)
    assert "ends" not in err, "the reply's own end must read as the end of the turn"


async def test_a_reply_that_ended_on_its_error_is_never_also_called_stopped(monkeypatch, clean_turns):
    conn = await _turn(monkeypatch, [
        _sse("token", {"text": "Looking"}),
        _sse("error", {"detail": "Iron Jarvis could not reach that page."}),
    ])
    events = [e for e, _ in conn.frames]
    at = events.index(P.PANEL_EVENT_ERROR)
    after = [p for e, p in conn.frames[at + 1:] if e == P.PANEL_EVENT_DONE]
    assert all(p.get("reason") != "stopped" for p in after), conn.frames[at:]


async def test_a_reply_that_ended_with_no_end_frame_is_still_stopped(monkeypatch, clean_turns):
    conn = await _turn(monkeypatch, [_sse("token", {"text": "Looking"})])
    assert conn.of(P.PANEL_EVENT_DONE)[-1].get("reason") == "stopped"


async def test_a_send_right_after_done_waits_for_the_tidy_up_and_is_not_refused(monkeypatch, clean_turns):
    """Found by the lead in group 20's local run: since the lane waits for its
    usage row's write AFTER its `done` frame, the reply's runner is still
    tidying up for an instant after the panel was told it ended, and a Send
    in that instant was refused as "still working on the last message"
    (``test_browser_panel_v1242``'s flood test met it under load). A Send
    after `done` now waits for the tidy-up. Pins ``_send``'s ``_end_sent``
    wait: without it the second Send below is an error frame."""
    import iron_jarvis.daemon.chat_stream as chat_stream

    tidy = asyncio.Event()
    starts: list[str] = []

    async def lane(platform, personas, body, **kw):
        asked = body.messages[-1].content if getattr(body, "messages", None) else ""
        starts.append(str(asked))

        async def gen():
            yield _sse("token", {"text": "Done."})
            yield _sse("done", {"reply": "Done."})
            if len(starts) == 1:
                await tidy.wait()  # the first reply's tidy-up, after its `done`

        return gen()

    monkeypatch.setattr(chat_stream, "stream_chat_turn", lane)
    turns = PanelTurns(SimpleNamespace(browser=None))
    conn = _Conn()
    await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "first"})
    first = turns._task
    assert first is not None
    await until(lambda: conn.of(P.PANEL_EVENT_DONE), 10)
    assert not first.done(), "the stand-in tidy-up should still be running"
    # The next Send arrives while the first reply is still tidying up.
    second = asyncio.ensure_future(turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "second"}))
    for _ in range(50):
        await asyncio.sleep(0)
    assert conn.of(P.PANEL_EVENT_ERROR) == [], "a Send right after `done` was refused"
    tidy.set()
    await asyncio.wait_for(second, 10)
    await asyncio.wait_for(first, 10)
    assert conn.of(P.PANEL_EVENT_ERROR) == []
    assert len(starts) == 2, "the second message never started its reply"
    if turns._task is not None:
        await asyncio.wait_for(turns._task, 10)


async def test_a_send_while_the_reply_is_still_answering_is_refused_as_before(monkeypatch, clean_turns):
    """The control: before `done`, a second Send is still refused at once."""
    import iron_jarvis.daemon.chat_stream as chat_stream

    hold = asyncio.Event()
    monkeypatch.setattr(chat_stream, "stream_chat_turn", _lane_of([_sse("token", {"text": "Looking"})], hold))
    turns = PanelTurns(SimpleNamespace(browser=None))
    conn = _Conn()
    await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "first"})
    task = turns._task
    try:
        await until(lambda: conn.of(P.PANEL_EVENT_DELTA), 10)
        await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "second"})
        [err] = conn.of(P.PANEL_EVENT_ERROR)
        assert "still working on the last message" in err.get("text", "")
    finally:
        hold.set()
        await asyncio.wait_for(task, 10)


async def test_sb8_an_error_while_the_reply_runs_is_marked_not_the_end(monkeypatch, clean_turns):
    import iron_jarvis.daemon.chat_stream as chat_stream

    hold = asyncio.Event()
    monkeypatch.setattr(chat_stream, "stream_chat_turn", _lane_of([_sse("token", {"text": "Looking"})], hold))
    turns = PanelTurns(SimpleNamespace(browser=None))
    conn = _Conn()
    await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "press Search on this page"})
    task = turns._task
    assert task is not None
    try:
        await until(lambda: conn.of(P.PANEL_EVENT_DELTA), 10)
        # An answer to a card this sidebar never showed, and a second Send.
        await turns.handle(conn, P.PANEL_ACTION_APPROVE, {"id": "not-asked-here"})
        await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "and the next one"})
        errors = conn.of(P.PANEL_EVENT_ERROR)
        assert len(errors) == 2, errors
        assert all(e.get("ends") is False for e in errors), errors
        assert not task.done(), "the reply is still running"
    finally:
        hold.set()
        await asyncio.wait_for(task, 10)


async def test_sb8_a_send_the_daemon_refuses_ends_the_turn_the_panel_started(monkeypatch, clean_turns):
    turns = PanelTurns(SimpleNamespace(browser=None))
    turns._over_budget = lambda: "Too many messages in a minute. Try again in a moment."
    conn = _Conn()
    await turns.handle(conn, P.PANEL_ACTION_SEND, {"text": "press Search on this page"})
    [err] = conn.of(P.PANEL_EVENT_ERROR)
    assert err.get("reason") == "rate_limited" and "ends" not in err, err
    assert turns._task is None
