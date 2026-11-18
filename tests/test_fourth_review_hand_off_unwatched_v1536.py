"""Fourth fix plan, group 28 (DT1; owner decisions D6 and D13): a reply that
hands off to the full agent is never lost when no window follows it.

Before: only the Chat page performed the hand-off. A reply that decided it
needed the full agent while no window followed it was written into its chat
by the daemon as its plain text, "(no response)" when it had none, the tray
said the answer was finished, and the run the user asked for never started.

Now the daemon performs it when the turn's reply goes home
(``durable_turns.TurnLogs._home_now``), through the SAME door code as
``POST /sessions`` (``routes.sessions.open_session_door``, guard included),
with the stricter of the turn's body and the chat's setup as stored now, and
writes the line the page's ``sendAgent`` saves BEFORE the run starts.

Every test runs the real app factory, the real ``POST /chat/stream`` route,
the real durable turn, the real doors and the real chat writer on a database
in ``tmp_path``. The model is a stand-in stream that calls the
``escalate_to_agent`` exit (as ``test_escalation_carries_results_v1344.py``
scripts it). "Started" is counted at the daemon's one launcher
(``spawn_managed``, as ``test_one_run_per_hand_off_v1529.py`` does): no run
executes.
"""

from __future__ import annotations

import asyncio
import json

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import ChatThreadRecord, Session
from iron_jarvis.daemon import chat_hand_off as CH
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_append import append_to_chat_thread
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools.permissions import PermissionEngine
from tests.test_durable_chat_turns_v1336 import _open, _until
from tests.test_escalation_carries_results_v1344 import _FakeTool
from tests.test_third_plan_g18_reply_home import _record, _replies, _req

LOOKUP = "notes_lookup_fake"
QUESTION = "rename the files in my folder"
REASON = "renaming many files needs the full agent"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _app(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.registry.register(_FakeTool(LOOKUP))
    platform.permissions = PermissionEngine({**platform.config.permissions, LOOKUP: "allow"})
    return app


def _record_starts(app, *, reply: bool = False) -> list[str]:
    """The daemon's one launcher, recorded (the run's coroutine is closed,
    never run). ``reply``: the run "ends at once" — its final line lands in
    its chat the moment it starts, as ``chat_delivery`` appends it."""
    started: list[str] = []
    engine = app.state.platform.engine

    def fake_spawn(session_id, coro):
        coro.close()
        started.append(session_id)
        if reply:
            with session_scope(engine) as db:
                thread = json.loads(db.get(Session, session_id).options_json or "{}").get("chat_thread_id")
            append_to_chat_thread(engine, thread, [{
                "role": "assistant", "content": "the run's reply", "sessionId": session_id,
            }])
        return object()  # launched, not parked

    app.state.orchestrator.spawn_managed = fake_spawn
    return started


def _escalating(gate: asyncio.Event):
    """A stand-in model: a few words, then (once ``gate`` opens) the
    ``escalate_to_agent`` exit."""
    async def stream(**kw):
        yield {"type": "text", "text": "Looking at it. "}
        await gate.wait()
        yield {
            "type": "final",
            "response": LLMResponse(
                tool_calls=[ToolCall(id="e1", name="escalate_to_agent", arguments={"reason": REASON})],
                finish_reason="tool_use",
            ),
            "provider": "mock",
            "model": "mock",
        }

    return stream


async def _chat(app, turn: str, setup: "dict | None" = None) -> str:
    """The page's at-send save: the question, carrying its turn's name."""
    payload: dict = {"messages": [{"role": "user", "content": QUESTION, "turnId": turn}]}
    if setup is not None:
        payload["setup"] = setup
    status, out = await _req(app, "PUT", "/chat/threads/new", payload)
    assert status == 200, out
    return out["id"]


def _body(turn: str, tid: str, **extra) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hello, what can I do?"},
            {"role": "user", "content": QUESTION},
        ],
        "turn_id": turn,
        "thread_id": tid,
        "tools": [LOOKUP],
        **extra,
    }


async def _unwatched(app, body: dict, *, before_end=None) -> "dt.TurnLog":
    """Start a named turn, drop its window after the first words, let it end
    with NOBODY following it (``before_end`` runs while it still runs)."""
    gate = asyncio.Event()
    app.state.platform.router.stream = _escalating(gate)
    drop = asyncio.Event()
    await _open(app, body, drop=drop, stop_after="Looking at it")
    lg = app.state.platform.turn_logs.get(body["turn_id"])
    assert lg is not None
    await _until(lambda: not lg.watched())
    if before_end is not None:
        await before_end()
    gate.set()
    await _until(lambda: lg.status != "running" and lg.task.done())
    return lg


def _rows(app) -> list[Session]:
    with session_scope(app.state.platform.engine) as db:
        rows = list(db.exec(select(Session)).all())
        for r in rows:
            db.expunge(r)
        return rows


async def _settled(app, tid: str, turn: str) -> list[dict]:
    """Wait for the turn's line in its chat (the hand-off runs as its own
    task), then give every hand-off task the chance to finish."""
    engine = app.state.platform.engine
    await _until(lambda: _replies(engine, tid, turn))
    logs = app.state.platform.turn_logs
    pending = [t for t in list(getattr(logs, "_hand_off_tasks", set())) if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=10)
    return _replies(engine, tid, turn)


# --------------------------------------------------------------------------- #
# 1. A turn that hands off with nobody watching starts ONE run
# --------------------------------------------------------------------------- #
async def test_an_unwatched_hand_off_starts_one_run_with_the_turns_name_and_consent(tmp_path):
    app = _app(tmp_path)
    started = _record_starts(app)
    engine = app.state.platform.engine
    tid = await _chat(app, "ho-1", {"tools": [LOOKUP], "local_only": True})
    lg = await _unwatched(app, _body("ho-1", tid, local_only=True))
    assert lg.status == "done"
    lines = await _settled(app, tid, "ho-1")

    rows = _rows(app)
    assert len(rows) == 1, [r.id for r in rows]
    run = rows[0]
    assert started == [run.id]
    assert run.handed_from_turn == "ho-1"
    assert run.origin == "chat"
    opts = json.loads(run.options_json or "{}")
    assert opts.get("chat_thread_id") == tid
    assert opts.get("local_only") is True
    assert LOOKUP in json.loads(run.allow_tools_json or "[]")
    # The task is the request under the ONE recap helper's block.
    assert run.task.startswith("Context from our recent conversation:\n")
    assert run.task.endswith(f"Request: {QUESTION}")

    # The chat holds the line sendAgent saves, naming that run.
    assert len(lines) == 1, lines
    line = lines[0]
    assert line["escalated"] == REASON
    assert line["awaitingSession"] == run.id
    assert line["savedBy"] == "daemon" and line["content"] == ""
    assert "handOff" not in line
    assert all(m.get("content") != dt.NO_RESPONSE for m in json.loads(
        _thread(engine, tid).messages_json))
    assert _record(engine, "ho-1").home == "home"


def _thread(engine, tid: str) -> ChatThreadRecord:
    with session_scope(engine) as db:
        row = db.get(ChatThreadRecord, tid)
        db.expunge(row)
        return row


async def test_the_pages_own_hand_off_then_gets_the_same_run_back(tmp_path):
    app = _app(tmp_path)
    started = _record_starts(app)
    tid = await _chat(app, "ho-2")
    await _unwatched(app, _body("ho-2", tid))
    await _settled(app, tid, "ho-2")
    [run] = _rows(app)
    # A window opened late posts its own hand-off for the same turn.
    status, out = await _req(app, "POST", "/sessions", {
        "task": QUESTION, "wait": False, "origin": "chat", "handed_from_turn": "ho-2",
    })
    assert status == 200, out
    assert out["id"] == run.id
    assert len(_rows(app)) == 1
    assert started == [run.id]


# --------------------------------------------------------------------------- #
# 2. A page that followed and saved within the grace: the daemon starts nothing
# --------------------------------------------------------------------------- #
async def test_a_page_that_followed_and_saved_in_the_grace_starts_nothing_from_the_daemon(tmp_path):
    app = _app(tmp_path)
    started = _record_starts(app)
    engine = app.state.platform.engine
    tid = await _chat(app, "ho-3")
    gate = asyncio.Event()
    gate.set()
    app.state.platform.router.stream = _escalating(gate)
    text = await _open(app, _body("ho-3", tid))  # this window follows to the end
    assert "event: done" in text
    lg = app.state.platform.turn_logs.get("ho-3")
    await _until(lambda: lg.task.done())
    logs = app.state.platform.turn_logs
    assert lg.followed_at_end and logs.home_pending("ho-3")
    # The page saves its hand-off line (sendAgent's at-send save) first.
    status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {"messages": [
        {"role": "user", "content": QUESTION, "turnId": "ho-3"},
        {"role": "assistant", "content": "", "escalated": REASON, "turnId": "ho-3"},
    ]})
    assert status == 200
    assert await logs.home_due_now("ho-3") == ""
    assert _rows(app) == [] and started == []
    assert [m.get("savedBy") for m in _replies(engine, tid, "ho-3")] == [None]


# --------------------------------------------------------------------------- #
# 3. Local only: never a cloud run, the line carries the door's refusal
# --------------------------------------------------------------------------- #
async def test_local_only_switched_on_in_the_stored_setup_after_send_gives_no_cloud_run(tmp_path):
    app = _app(tmp_path)
    started = _record_starts(app)
    setup = {"tools": [LOOKUP], "provider": "anthropic", "model": "claude-sonnet-4-5"}
    tid = await _chat(app, "ho-4", setup)

    async def local_only_on():
        # The user switches Local only on while the reply runs (the page
        # saves its setup at once), then closes the window.
        status, _ = await _req(app, "PUT", f"/chat/threads/{tid}", {
            "messages": [{"role": "user", "content": QUESTION, "turnId": "ho-4"}],
            "setup": {**setup, "local_only": True},
        })
        assert status == 200

    await _unwatched(app, _body("ho-4", tid, provider="anthropic", model="claude-sonnet-4-5"),
                     before_end=local_only_on)
    [line] = await _settled(app, tid, "ho-4")
    assert _rows(app) == [] and started == []
    assert line["content"].startswith(CH.REFUSED_LEAD)
    assert "local only" in line["content"]
    assert line["handOff"] == CH.NOT_STARTED and line["escalated"] == REASON
    assert "awaitingSession" not in line


async def test_a_local_only_turn_with_a_cloud_model_gives_no_run(tmp_path):
    app = _app(tmp_path)
    started = _record_starts(app)
    tid = await _chat(app, "ho-5")
    await _unwatched(app, _body("ho-5", tid, local_only=True, provider="anthropic",
                                model="claude-sonnet-4-5", allow_cloud_once="anthropic"))
    [line] = await _settled(app, tid, "ho-5")
    assert _rows(app) == [] and started == []
    assert line["content"].startswith(CH.REFUSED_LEAD)
    assert "local only" in line["content"]


# --------------------------------------------------------------------------- #
# 4. Order: the hand-off line is in the chat before the run's reply
# --------------------------------------------------------------------------- #
async def test_the_hand_off_line_comes_before_a_run_that_ends_at_once(tmp_path):
    app = _app(tmp_path)
    started = _record_starts(app, reply=True)
    engine = app.state.platform.engine
    tid = await _chat(app, "ho-6")
    await _unwatched(app, _body("ho-6", tid))
    await _settled(app, tid, "ho-6")
    await _until(lambda: len(started) == 1)
    msgs = json.loads(_thread(engine, tid).messages_json)
    assistant = [m for m in msgs if m.get("role") == "assistant"]
    assert [m.get("turnId") or m.get("sessionId") for m in assistant] == ["ho-6", started[0]]
    assert assistant[0]["awaitingSession"] == started[0]


# --------------------------------------------------------------------------- #
# 5. No "finished" notice for a turn that handed off
# --------------------------------------------------------------------------- #
async def test_no_finished_notice_for_a_turn_that_handed_off(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "CHAT_FINISH_NOTICE_AFTER_S", -1.0)  # every reply is "long"
    app = _app(tmp_path)
    _record_starts(app)
    seen: list = []
    app.state.platform.event_bus.add_handler(
        lambda e: seen.append(e.payload) if e.type == EventType.CHAT_TURN_FINISHED else None
    )
    dt.bind_event_bus(app.state.platform.engine, app.state.platform.event_bus)
    tid = await _chat(app, "ho-7")
    await _unwatched(app, _body("ho-7", tid))
    await _settled(app, tid, "ho-7")
    assert [p for p in seen if p.get("turn_id") == "ho-7"] == []


# --------------------------------------------------------------------------- #
# 6. The start-up pass: a record with no plan never says "(no response)"
# --------------------------------------------------------------------------- #
def _escalated_frames() -> list:
    return [
        ["token", json.dumps({"text": "Looking at it. "})],
        ["done", json.dumps({"reply": "", "escalate": True, "escalate_reason": REASON,
                             "escalate_agent": None, "prior_tool_results": [], "approved": []})],
    ]


def test_the_start_up_pass_writes_the_closed_line_when_no_run_started(tmp_path):
    from tests.test_third_plan_g18_reply_home import _seed_chat, _seed_record

    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = _seed_chat(engine, [{"role": "user", "content": QUESTION, "turnId": "ho-8"}])
    _seed_record(engine, "ho-8", tid, frames=_escalated_frames())
    assert dt.bring_home_record(engine, "ho-8") == dt.HOME_WRITTEN
    [line] = _replies(engine, tid, "ho-8")
    assert line["content"] == CH.CLOSED_LINE
    assert line["handOff"] == CH.NOT_STARTED and line["escalated"] == REASON


def test_the_start_up_pass_names_a_run_the_turn_already_started(tmp_path):
    from iron_jarvis.agents import hand_off_guard
    from tests.test_third_plan_g18_reply_home import _seed_chat, _seed_record

    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = _seed_chat(engine, [{"role": "user", "content": QUESTION, "turnId": "ho-9"}])
    with session_scope(engine) as db:
        db.add(Session(id="session_ho9", task=QUESTION))
        db.commit()
    hand_off_guard.stamp(engine, "session_ho9", "ho-9")
    _seed_record(engine, "ho-9", tid, frames=_escalated_frames())
    assert dt.bring_home_record(engine, "ho-9") == dt.HOME_WRITTEN
    [line] = _replies(engine, tid, "ho-9")
    assert line["awaitingSession"] == "session_ho9" and line["content"] == ""
    assert line["escalated"] == REASON and "handOff" not in line


async def test_no_hand_off_starts_once_shutdown_began(tmp_path):
    """Shutdown began: the hand-off is left to the start-up pass (the record
    stays "due"), never started for a run the orchestrator would cut."""
    from tests.test_third_plan_g18_reply_home import _seed_chat, _seed_record

    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    tid = _seed_chat(engine, [{"role": "user", "content": QUESTION, "turnId": "ho-11"}])
    _seed_record(engine, "ho-11", tid, status="running", frames=[])
    logs = dt.TurnLogs(engine)
    lg = dt.TurnLog("ho-11", tid)
    for event, data in _escalated_frames():
        lg.add(dt.sse(event, json.loads(data)))
    lg.status = "done"
    called: list = []

    async def recorder(*args):
        called.append(args)
        return ""

    lg.hand_off = recorder
    await logs.shutdown()
    assert await logs._home_now(lg) == ""
    assert called == [] and lg.home == dt.HOME_DUE
    assert _replies(engine, tid, "ho-11") == []


def test_reply_line_never_says_no_response_for_a_turn_that_handed_off():
    line = dt.reply_line("ho-10", _escalated_frames(), "done")
    assert line is not None and line["content"] != dt.NO_RESPONSE
    assert line["content"] == CH.CLOSED_LINE
    named = dt.reply_line("ho-10", _escalated_frames(), "done", handed_to="session_x")
    assert named["awaitingSession"] == "session_x" and named["escalated"] == REASON
