"""v1.367.0 (IJ harness Phase 4) — the REVIEW's pins. Each one names the
defect it closes; every one was checked by putting the defect back (the
mutation) and watching the pin go red.

Driven through the REAL app factory / lanes; only the model is scripted.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, ChatTurnRecord
from iron_jarvis.daemon import mcp_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import session_tools as T
from iron_jarvis.harness import sessions as S
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    turn_log,
    until,
    wire,
)


def _record_status(app, turn_id: str) -> str:
    with session_scope(app.state.platform.engine) as db:
        row = db.get(ChatTurnRecord, turn_id)
        return row.status if row is not None else ""


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


# --------------------------------------------------------------------------- #
# 1. ask_user armed by name in a CHAT turn never parks the turn
# --------------------------------------------------------------------------- #


async def test_ask_user_armed_by_name_in_a_chat_turn_refuses_instead_of_hanging(tmp_path):
    """The chat page's "+" menu lists every registered tool, so a user (or a
    "/" skill whose playbook names it) can arm ``ask_user`` in a plain chat
    turn — whose page draws NO ask_user card. Before the review every stream
    turn's interaction scope carried ``ask``, so the call parked the turn on a
    question nobody could see, with no clock: a watched chat waited forever.
    Now only a harness turn that armed it gets the question channel; anywhere
    else the tool refuses at once, in words."""
    platform = build_platform(str(tmp_path / "home"))
    script = Script([[ToolCall(id="q1", name="ask_user", arguments={"question": "Which one?"})], "Fine."])
    platform.router.stream = script.stream
    platform.router.complete = script.complete
    body = ChatBody(
        messages=[ChatMessageBody(role="user", content="ask me something")],
        provider=CLAUDE[0], model=CLAUDE[1], tools=["ask_user"], mcp_cards=True,
    )

    async def drain() -> list[str]:
        out: list[str] = []
        async for chunk in await stream_chat_turn(platform, BUILTIN_PERSONAS, body):
            out.append(chunk)
        return out

    raw = "".join(await asyncio.wait_for(drain(), timeout=20))
    frames = frames_of(raw)
    assert "ask_user" not in [e for e, _ in frames], "a question no page renders was sent"
    assert frames[-1][0] == "done"
    tool_msgs = [m for m in script.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs and tool_msgs[0].content.startswith(T.NOBODY_TO_ASK[:40]), tool_msgs
    assert mcp_turn.PENDING == {} or all(a.kind != "ask_user" for a in mcp_turn.PENDING.values())


# --------------------------------------------------------------------------- #
# 2. a turn id already RUN is never run again — after the in-memory window
#    or a restart, its record answers
# --------------------------------------------------------------------------- #


async def test_a_turn_id_this_process_forgot_is_replayed_from_its_record_never_run_twice(app, work):
    """``TurnLogs`` keeps a finished turn ``FINISHED_KEEP_S`` (10 min) and a
    restart keeps none — before the review, the same ``turn_id`` POSTed after
    that (a screen resending a request it lost) RAN THE TURN AGAIN: the same
    edits and commands a second time and the question twice in the
    transcript. The record (7 days) now answers it."""
    sid = new_session(app, work)["id"]
    other = new_session(app, work)["id"]
    script = wire(app, Script([
        [ToolCall(id="c1", name="Write", arguments={"file_path": "made.py", "content": "y = 1\n"})],
        "Made it.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "make made.py", "turn_id": "rv-once"})
    assert status == 200 and frames_of(raw)[-1][0] == "done"
    calls_after_first = len(script.calls)
    lg = await until(lambda: turn_log(app, "rv-once"))
    await until(lambda: lg.status != "running")
    await until(lambda: any(m.get("role") == "assistant" for m in messages_of(app, sid)))
    await until(lambda: _record_status(app, "rv-once") == "done")
    # The process forgets it (the 10-minute window passed, or a restart).
    app.state.platform.turn_logs._logs.pop("rv-once")
    (work / "made.py").unlink()

    status, raw = await open_turn(app, sid, {"text": "make made.py", "turn_id": "rv-once"})
    assert status == 200
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", "the record's frames are replayed"
    assert len(script.calls) == calls_after_first, "the model was asked again: the turn RAN TWICE"
    assert not (work / "made.py").exists(), "the turn's edit was applied a second time"
    questions = [m for m in messages_of(app, sid) if m.get("role") == "user" and m.get("turnId") == "rv-once"]
    assert len(questions) == 1, "the question was appended twice"
    # Another session cannot claim that turn id.
    status, body = await open_turn(app, other, {"text": "x", "turn_id": "rv-once"})
    assert status == 409, body


# --------------------------------------------------------------------------- #
# 3. a Build session is never a chat's preference evidence
# --------------------------------------------------------------------------- #


def test_a_build_session_is_never_counted_or_linked_as_chat_preference_evidence(tmp_path):
    """The correction look-back (v1.305.0) read EVERY ChatThreadRecord, so a
    Build session's "no tables" made a chat's second "stop using tables" a
    suggestion, quoted "where: chat" with a ``/chat?thread=<build id>`` link —
    the Memory page opened a Build session in Chat. Both readers now keep to
    chats (``chat_surface_only``)."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from iron_jarvis.learning import preferences as P
    from tests.test_preferences_v1305 import _app, _body, _capture_complete, _thread

    app, platform = _app(tmp_path)
    _capture_complete(platform, [])
    tid = _thread(platform, ["no tables"], days_ago=3)
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        row.surface = "build"  # the same lines, in a Build session
        db.add(row)
        db.commit()
    r = TestClient(app).post("/chat", json=_body("stop using tables"))
    assert r.status_code == 200, r.text
    assert r.json()["suggestion"] is None, "a Build session's line was counted as chat evidence"
    found = P._recent_chat_corrections(platform.engine, datetime.now(timezone.utc))
    assert all(f"/chat?thread={tid}" != e.get("link") for e in found), found
    # Control: the same lines in a CHAT do count (the pin is not vacuous).
    _thread(platform, ["no tables"], days_ago=3)
    r = TestClient(app).post("/chat", json=_body("stop using tables"))
    assert r.json()["suggestion"] is not None


# --------------------------------------------------------------------------- #
# 4. the chat writer never writes into a Build session
# --------------------------------------------------------------------------- #


def test_the_daemons_chat_writer_refuses_a_build_session(tmp_path, work):
    """``chat_append.append_to_chat_thread`` is how an @-panel reply, an
    agent's delivery (``options.chat_thread_id``), a finished media job and a
    remote's inbound line land in a chat. Given a Build session's id it used
    to write a chat-shaped line into the session's transcript (and
    un-archive it) — a second writer the session's design rules out. It now
    refuses in words and touches nothing."""
    from iron_jarvis.daemon.chat_append import append_to_chat_thread
    from iron_jarvis.daemon.routes.chat import set_thread_archived

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    S.append_lines(engine, sid, [{"role": "user", "content": "the session's own line", "turnId": "t1"}])
    set_thread_archived(engine, sid, True)
    before = messages_of(app, sid)
    out = append_to_chat_thread(engine, sid, [{
        "role": "assistant", "content": "an @-agent's reply", "panelWho": "builtin:builder",
    }])
    assert out["ok"] is False and "Build session" in out["why"], out
    assert messages_of(app, sid) == before, "a chat line landed in a Build session"
    with session_scope(engine) as db:
        assert db.get(ChatThreadRecord, sid).archived_at is not None, "the append un-archived it"


# --------------------------------------------------------------------------- #
# 5. the boot reconcile keeps only what the reply really says
# --------------------------------------------------------------------------- #


def test_the_boot_reconcile_drops_text_the_lane_reset(tmp_path, work):
    """A ``reset`` frame means the lane threw away what it had streamed (the
    text-call nudge, a language rewrite) — the live writer (``_ReplyWatch``)
    drops it. The crash reconcile concatenated every token frame instead, so a
    reply cut off after a nudge kept the discarded ``<tool_call>`` text."""
    from iron_jarvis.core.ids import utcnow

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    S.append_lines(engine, sid, [{"role": "user", "content": "fix it", "turnId": "rc-x"}])
    frames = [
        ["token", json.dumps({"text": "<tool_call>Write{...}</tool_call>"})],
        ["reset", json.dumps({})],
        ["token", json.dumps({"text": "Fixing app.py now"})],
        ["error", json.dumps({"detail": "cut off"})],
    ]
    with session_scope(engine) as db:
        db.add(ChatTurnRecord(id="rc-x", thread_id=sid, status="interrupted",
                              frames_json=json.dumps(frames), started_at=utcnow()))
        db.commit()
    assert S.reconcile_interrupted_turns(engine) == 1
    reply = messages_of(app, sid)[-1]
    assert reply["role"] == "assistant" and reply["interrupted"] is True
    assert reply["content"] == "Fixing app.py now", reply["content"]
    assert S.reconcile_interrupted_turns(engine) == 0, "a second boot wrote a second line"


# --------------------------------------------------------------------------- #
# 6. a lane that breaks outside its stream still leaves a reply line
# --------------------------------------------------------------------------- #


async def test_a_lane_that_raises_leaves_an_error_line_not_a_bare_question(app, work, monkeypatch):
    """The lane yields its own failures as ``error`` frames; an exception
    that escapes it (a bug) was caught only by ``TurnLogs._drive`` — the log
    got an error frame, but ``recorded_turn`` saw neither ``done`` nor
    ``error`` and, with nothing streamed, wrote NO line: the session ended on
    the question with no reply and no reason."""
    from iron_jarvis.daemon.routes import build as build_routes

    async def broken(platform, personas, body, **kw):
        async def gen():
            yield "event: phase\ndata: {\"phase\": \"recalling\"}\n\n"
            raise RuntimeError("boom")

        return gen()

    monkeypatch.setattr(build_routes, "stream_chat_turn", broken)
    sid = new_session(app, work)["id"]
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "rv-boom"})
    assert status == 200
    lg = await until(lambda: turn_log(app, "rv-boom"))
    await until(lambda: lg.status != "running")
    msgs = await until(lambda: [m for m in messages_of(app, sid) if m.get("role") == "assistant"])
    assert msgs, "the session ended on the question with no reply line"
    assert "internal error (RuntimeError)" in msgs[-1].get("error", ""), msgs[-1]
    assert msgs[-1]["turnId"] == "rv-boom"


# --------------------------------------------------------------------------- #
# 7. an out-of-range build_round_budget is clamped, never a 400 on every turn
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value, used", [(5000, 1000), (-3, 1), (0, 1000), (500, 500)])
async def test_a_hand_edited_round_budget_is_clamped_not_refused(app, work, monkeypatch, value, used):
    """The settings writer checks the range, a hand-edited config.toml does
    not: ``int(... or 60)`` handed 500 (or -3) to the lane, whose eager check
    raised — every turn of every session answered 400. v1.384.0: 0 is NO
    LIMIT (the 1,000-step safety stop) and the range is 0..1000."""
    from iron_jarvis.daemon.routes import build as build_routes

    seen: list[int] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(kw["round_budget"])
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    app.state.platform.config.build_round_budget = value
    wire(app, Script(["Hello."]))
    sid = new_session(app, work)["id"]
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": f"rv-budget-{value}"})
    assert status == 200, raw
    assert seen == [used]


# --------------------------------------------------------------------------- #
# 8. nothing blocking on the loop; the turn registry read safely off it
# --------------------------------------------------------------------------- #


async def test_the_sessions_transcript_is_parsed_off_the_event_loop(app, work, monkeypatch):
    """Up to 200 transcript lines (replies carry steps and thinking) were
    JSON-parsed ON the loop for every turn — the v1.153.1 rule."""
    import threading

    loop_thread = threading.current_thread()
    where: list[bool] = []
    real = S.history

    def spy(row):
        where.append(threading.current_thread() is loop_thread)
        return real(row)

    monkeypatch.setattr(S, "history", spy)
    wire(app, Script(["Hello."]))
    sid = new_session(app, work)["id"]
    status, _ = await open_turn(app, sid, {"text": "hi", "turn_id": "rv-offloop"})
    assert status == 200
    assert where == [False], "the transcript was parsed on the event loop"


def test_running_reads_a_snapshot_so_an_insert_from_another_thread_cannot_break_it():
    """``TurnLogs.running()`` is read from FastAPI's threadpool by Build's
    sync routes while the loop starts turns. A comprehension straight over
    the dict raises "dictionary changed size during iteration" when an insert
    lands between two items — FORCED here: reading a log's status inserts."""
    from iron_jarvis.daemon.durable_turns import TurnLog, TurnLogs

    logs = TurnLogs(engine=None)

    class Racy(TurnLog):
        @property
        def status(self):  # the other thread's insert, at the worst moment
            logs._logs.setdefault("late", TurnLog("late", "t2"))
            return "running"

        @status.setter
        def status(self, value):
            pass

    logs._logs["a"] = Racy("a", "t1")
    logs._logs["b"] = TurnLog("b", "t1")
    got = logs.running()
    assert [lg.turn_id for lg in got] == ["a", "b"]



# --------------------------------------------------------------------------- #
# 9. the session list never reads the transcripts
# --------------------------------------------------------------------------- #


def test_the_session_list_reads_no_transcript(tmp_path, work):
    """``GET /build/sessions`` selected WHOLE rows — every session's
    transcript (up to 200 replies with steps and thinking) — to print titles
    grouped by folder. It now selects the five columns a row shows."""
    from sqlalchemy import event
    from fastapi.testclient import TestClient

    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    sid = client.post("/build/sessions", json={"folder": str(work), "title": "one"}).json()["id"]
    S.append_lines(app.state.platform.engine, sid, [{"role": "user", "content": "x" * 5000}])
    seen: list[str] = []
    engine = app.state.platform.engine

    def capture(conn, cursor, statement, params, context, executemany):
        seen.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        r = client.get("/build/sessions")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert r.status_code == 200
    sessions = r.json()["folders"][0]["sessions"]
    assert [s["id"] for s in sessions] == [sid] and sessions[0]["title"] == "one"
    thread_reads = [s for s in seen if "from chatthreadrecord" in s]
    assert thread_reads, seen
    assert not any("messages_json" in s for s in thread_reads), thread_reads


# --------------------------------------------------------------------------- #
# 10. a waiting Build session is not announced as "a chat"
# --------------------------------------------------------------------------- #


async def test_a_waiting_build_session_is_marked_on_the_pending_listing(app, work):
    """A build turn parked on its approval card with no window following it
    joins ``GET /chat/approvals/pending`` as a ``kind: "chat"`` row (the bell
    and the desktop toast announce it). It was indistinguishable from a
    chat's — whose bell link opens ``/chat?thread=<id>``. The row now says
    ``surface: "build"`` (LAST; a chat's row is unchanged) so the screen that
    opens it can tell (Phase 6 routes it to Build)."""
    from tests.test_build_sessions_v1367 import asgi

    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="b1", name="Bash", arguments={"command": "echo hi"})], "Ran it."]))
    drop = asyncio.Event()
    status, _raw = await open_turn(app, sid, {"text": "run echo", "turn_id": "rv-wait"},
                                   drop=drop, stop_after="event: approval")
    assert status == 200
    lg = await until(lambda: turn_log(app, "rv-wait"))
    await until(lambda: not lg.watched())
    status, body = await asgi(app, "GET", "/chat/approvals/pending")
    assert status == 200
    mine = [r for r in body["approvals"] if r.get("thread_id") == sid]
    assert len(mine) == 1, body
    row = mine[0]
    assert row["kind"] == "chat" and row["surface"] == "build"
    assert list(row)[-1] == "surface"
    await asgi(app, "POST", "/chat/turns/rv-wait/stop", {})
    await until(lambda: lg.status != "running")


# --------------------------------------------------------------------------- #
# 11. a busy database while keeping a grant never ends the live turn
# --------------------------------------------------------------------------- #


async def test_a_failed_read_while_keeping_a_grant_never_ends_the_turn(app, work, monkeypatch):
    """``_apply_session_changes`` says it never raises, but its read of the
    session (to merge an "allow for this conversation" grant) sat outside its
    try: a busy database there escaped into ``recorded_turn`` and ended the
    live turn mid-stream, after the user had just allowed the call."""
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ran it"]))
    real = S.get_row
    calls = {"n": 0}

    def flaky(engine, thread_id):
        calls["n"] += 1
        if calls["n"] >= 2:  # the turn route's own read passes; the grant merge's fails
            raise RuntimeError("database is locked")
        return real(engine, thread_id)

    monkeypatch.setattr(S, "get_row", flaky)
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "rv-busy"}, drop=drop))
    from tests.test_build_sessions_v1367 import turn_frames

    card = await until(lambda: [d for e, d in turn_frames(app, "rv-busy") if e == "approval"])
    assert (await _asgi_post(app, f"/chat/approvals/{card[0]['id']}", {"decision": "conversation"}))[0] == 200
    lg = await until(lambda: turn_log(app, "rv-busy"))
    await until(lambda: lg.status != "running")
    drop.set()
    await task
    assert lg.status == "done", lg.status
    events = [e for e, _ in turn_frames(app, "rv-busy")]
    assert events[-1] == "done", events[-5:]
    monkeypatch.setattr(S, "get_row", real)
    assert messages_of(app, sid)[-1]["content"] == "ran it"
