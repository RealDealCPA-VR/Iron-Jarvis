"""v1.402.0 ("approve once, then off to the races"), part 2: AN APPROVED PLAN
RUNS.

The audit (verified): Allow on a Build session's plan door
(``exit_plan_mode``) told the model to END the turn and wait for the user's
next message (``harness/session_tools.PLAN_APPROVED``); act mode started only
on the next message, so the user approved the plan and then had to type "go".

Now Allow means "approve AND carry it out": when the plan turn ends CLEANLY
the daemon itself starts the act turn (``routes/build.run_approved_plan``):

* the hidden question is :data:`PLAN_RUN_TEXT`, saved ``continuation: true``
  (the screen hides it); same model, account, hold, branch;
* EXACTLY ONCE per approval;
* never after Deny, never when the plan turn errored / stopped, never when the
  user wrote something after the approval (a steer note, a waiting message,
  a newer message), never when the session went back to plan mode or was
  archived, never for a night task's turn;
* the session view (what the screen reads) says ``plan_run`` while it is due,
  then ``running`` with the new turn's id;
* ONE ``autonomy.nudged`` (``plan_run``).

Driven through the REAL app factory, routes and lanes; only the model is
scripted (``tests/test_build_sessions_v1367.Script``).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.core.nudges import PLAN_RUN
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as B
from iron_jarvis.harness import session_tools as T
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.providers.hold import current_hold
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    asgi,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    turn_frames,
    turn_log,
    until,
    wire,
)
from tests.test_chat_turn_stop_v1241 import _asgi_post

PLAN = "My plan:\n1. Change x in app.py\n2. Run the tests"


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    yield a
    B._PLAN_RUNS.clear()


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


class HeldScript(Script):
    """The scripted model, also recording the HOLD each call ran under (the
    act turn must run on the session's own model)."""

    def __init__(self, moves):
        super().__init__(moves)
        self.holds: list[tuple[str, str] | None] = []

    async def _next(self, system, messages, tools):
        h = current_hold()
        self.holds.append((h.provider, h.model) if h is not None else None)
        return await super()._next(system, messages, tools)


def _nudges(app) -> list[dict]:
    got: list[dict] = []
    bus = app.state.platform.event_bus
    original = bus.publish

    async def spy(type_, payload=None, *a, **kw):
        if type_ == EventType.AUTONOMY_NUDGED:
            got.append(dict(payload or {}))
        return await original(type_, payload, *a, **kw)

    bus.publish = spy  # type: ignore[method-assign]
    return got


async def _plan_turn(app, sid: str, turn_id: str, *, answer: str = "once", steer: str = "",
                     on_card=None):
    """Run one plan turn: the model presents its plan on the plan door, the
    user answers the card (optionally steering first), the turn ends. Returns
    the plan turn's log, its task done (its end callbacks have run)."""
    drop = asyncio.Event()
    task = asyncio.create_task(open_turn(app, sid, {"text": "plan it", "turn_id": turn_id}, drop=drop))
    card = (await until(lambda: [d for e, d in turn_frames(app, turn_id) if e == "approval"]))[0]
    assert card["tool"] == "exit_plan_mode"
    if steer:
        assert (await _asgi_post(app, f"/chat/turns/{turn_id}/steer", {"text": steer}))[0] == 200
    if on_card is not None:
        on_card()
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": answer}))[0] == 200
    lg = await until(lambda: turn_log(app, turn_id))
    await until(lambda: lg.status != "running", 30)
    await lg.task  # its done-callbacks are scheduled now
    for _ in range(5):
        await asyncio.sleep(0)
    drop.set()
    await task
    return lg


def _continuations(app, sid: str) -> list[dict]:
    return [m for m in messages_of(app, sid) if m["role"] == "user" and m.get("continuation")]


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #


def test_the_plan_door_says_the_plan_runs_at_once():
    assert "starts carrying the plan out" in T.PLAN_APPROVED
    assert "next message" not in T.PLAN_APPROVED.replace("another message from the user", "")
    assert "carrying the plan out in act mode at once" in T.ExitPlanModeTool.description
    assert "from the user's next message" not in T.ExitPlanModeTool.description
    from iron_jarvis.harness.components import plan_mode_text

    words = plan_mode_text(exit_name="ExitPlanMode", plan_name="TodoWrite")
    assert "carries the plan out in act mode at once" in words
    assert "from their next message" not in words


# --------------------------------------------------------------------------- #
# Allow: the act turn starts by itself, once
# --------------------------------------------------------------------------- #


async def test_an_approved_plan_runs_by_itself_once(app, work):
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    nudges = _nudges(app)
    gate = asyncio.Event()

    async def hold_then_edit(_script):
        await gate.wait()
        return [ToolCall(id="w1", name="Write", arguments={"file_path": "app.py", "content": "x = 2\n"})]

    script = wire(app, HeldScript([
        (PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]),
        "Starting on the plan.",
        hold_then_edit,
        "Changed x to 2.",
    ]))
    plan_lg = await _plan_turn(app, sid, "bp-1")
    assert plan_lg.status == "done"
    # The act turn started BY ITSELF: a hidden continuation question.
    cont = await until(lambda: _continuations(app, sid))
    assert len(cont) == 1
    assert cont[0]["content"] == B.PLAN_RUN_TEXT
    act_id = cont[0]["turnId"]
    assert act_id and act_id != "bp-1"
    # The screen-facing view: running, with the act turn's id (what the Build
    # page follows), nothing still "due".
    act_lg = await until(lambda: turn_log(app, act_id))
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert status == 200
    assert view["running"] is True and view["turn_id"] == act_id
    assert view["plan_run"] is False
    assert view["mode"] == "act"
    gate.set()
    await until(lambda: act_lg.status != "running", 30)
    assert act_lg.status == "done"
    # The act turn ran in ACT mode (it could edit), on the session's model,
    # and read the plan and then the hidden question.
    act_calls = [c for c in script.calls if "Write" in c["tools"]]
    assert act_calls, "the act turn armed the editors"
    first = act_calls[0]
    assert first["messages"][-1].role == "user" and first["messages"][-1].content == B.PLAN_RUN_TEXT
    assert any(PLAN in str(m.content) for m in first["messages"] if m.role == "assistant")
    assert set(script.holds) == {CLAUDE}
    assert (work / "app.py").read_text(encoding="utf-8") == "x = 2\n"
    # ONE nudge, naming the session and the act turn; nothing left due.
    assert nudges == [{"kind": PLAN_RUN, "thread_id": sid, "turn_id": act_id, "from_turn": "bp-1"}]
    assert sid not in B._PLAN_RUNS
    # Exactly once: no second act turn followed.
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(_continuations(app, sid)) == 1


async def test_deny_starts_nothing(app, work):
    sid = new_session(app, work, mode="plan")["id"]
    nudges = _nudges(app)
    wire(app, Script([(PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Revising."]))
    await _plan_turn(app, sid, "bp-deny", answer="deny")
    assert setup_of(app, sid)["mode"] == "plan"
    assert _continuations(app, sid) == []
    assert nudges == [] and sid not in B._PLAN_RUNS
    _, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["running"] is False and view["plan_run"] is False


async def test_a_steer_after_the_approval_starts_nothing(app, work):
    """The user wrote something after approving: the work waits for them."""
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    nudges = _nudges(app)
    wire(app, Script([(PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Noted."]))
    await _plan_turn(app, sid, "bp-steer", steer="wait, also rename the file")
    msgs = messages_of(app, sid)
    assert any(m.get("steer") and m["content"] == "wait, also rename the file" for m in msgs)
    assert "mode" not in setup_of(app, sid)  # still approved: act mode
    assert _continuations(app, sid) == []
    assert nudges == [] and sid not in B._PLAN_RUNS


async def test_a_plan_turn_that_fails_after_the_approval_starts_nothing(app, work):
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    nudges = _nudges(app)

    async def boom(_script):
        raise RuntimeError("the model fell over")

    wire(app, Script([(PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), boom]))
    lg = await _plan_turn(app, sid, "bp-err")
    assert lg.status == "error"
    assert _continuations(app, sid) == []
    assert nudges == [] and sid not in B._PLAN_RUNS


async def test_a_message_the_user_sent_first_wins(app, work):
    """The user's own message after the approval runs; the due plan is
    dropped (never started after it)."""
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    nudges = _nudges(app)
    wire(app, Script([(PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Ready."]))
    # Hold the plan run back: the user's message lands first.
    started: list[str] = []
    go = asyncio.Event()
    results: list = []
    real = B.run_approved_plan

    async def held(d, thread_id, from_turn):
        started.append(from_turn)
        await go.wait()
        got = await real(d, thread_id, from_turn)
        results.append(got)
        return got

    B.run_approved_plan = held  # type: ignore[assignment]
    try:
        await _plan_turn(app, sid, "bp-race")
        await until(lambda: started)
        assert B._PLAN_RUNS.get(sid, {}).get("from") == "bp-race"  # due, not yet taken
        wire(app, Script(["Did what you asked."]))
        status, _ = await open_turn(app, sid, {"text": "actually do only step 1", "turn_id": "bt-user"})
        assert status == 200
        # The user's own message DROPPED the due plan the moment it started:
        # the view no longer says one is coming.
        assert sid not in B._PLAN_RUNS
        _, view = await asgi(app, "GET", f"/build/sessions/{sid}")
        assert view["plan_run"] is False
        go.set()
        await until(lambda: results)
    finally:
        B.run_approved_plan = real  # type: ignore[assignment]
    assert results == [None]
    assert _continuations(app, sid) == []
    assert nudges == [] and sid not in B._PLAN_RUNS
    assert [m["content"] for m in messages_of(app, sid) if m["role"] == "user"][-1] == "actually do only step 1"


async def test_run_approved_plan_starts_once_even_when_asked_twice(app, work):
    sid = new_session(app, work)["id"]
    engine = app.state.platform.engine
    S.append_lines(engine, sid, [
        {"role": "user", "content": "plan it", "turnId": "p1"},
        {"role": "assistant", "content": PLAN + "\n\nReady.", "turnId": "p1"},
    ])
    wire(app, Script(["Carried out."]))
    nudges = _nudges(app)
    B._PLAN_RUNS[sid] = {"from": "p1", "taken": False}
    d = app.state.d
    got = await asyncio.gather(B.run_approved_plan(d, sid, "p1"), B.run_approved_plan(d, sid, "p1"))
    started = [g for g in got if g is not None]
    assert len(started) == 1
    await until(lambda: started[0].status != "running", 30)
    assert len(_continuations(app, sid)) == 1
    assert len(nudges) == 1
    assert sid not in B._PLAN_RUNS
    # A third call after it all: nothing is due, nothing starts.
    assert await B.run_approved_plan(d, sid, "p1") is None


async def test_a_turn_that_did_not_end_cleanly_forgets_its_due_plan():
    """The marker is set as the plan turn's clean `done` goes out; if its
    task then ends any other way (a shutdown cut it off), nothing starts and
    the view stops saying a plan run is coming."""
    started: list = []
    log = SimpleNamespace(status="interrupted")
    d = SimpleNamespace(platform=SimpleNamespace(turn_logs=SimpleNamespace(get=lambda _t: log)))
    real = B.run_approved_plan

    async def spy(*a):
        started.append(a)

    B.run_approved_plan = spy  # type: ignore[assignment]
    try:
        B._PLAN_RUNS["s-int"] = {"from": "p1", "taken": False}
        B._after_turn_task(d, "s-int", "p1", SimpleNamespace(cancelled=lambda: False))
        assert "s-int" not in B._PLAN_RUNS
        # Another turn's end never touches a plan due from a different turn.
        B._PLAN_RUNS["s-int"] = {"from": "p1", "taken": False}
        B._after_turn_task(d, "s-int", "p2", SimpleNamespace(cancelled=lambda: False))
        assert B._PLAN_RUNS["s-int"]["from"] == "p1"
        # A clean end starts it.
        log.status = "done"
        B._after_turn_task(d, "s-int", "p1", SimpleNamespace(cancelled=lambda: False))
        for _ in range(3):
            await asyncio.sleep(0)
        assert started == [(d, "s-int", "p1")]
    finally:
        B.run_approved_plan = real  # type: ignore[assignment]
        B._PLAN_RUNS.pop("s-int", None)


async def test_a_second_caller_that_arrives_after_the_first_run_ended_starts_nothing(app, work, monkeypatch):
    """EXACTLY ONCE is the TAKE, not the busy check: a second start of the
    same approval that reaches the session only after the first act turn has
    ENDED (the session is free again, and the act turn's own question is a
    continuation, which the refusal does not count as the user's) must still
    start nothing."""
    import contextvars
    import threading
    import time as _time

    sid = new_session(app, work)["id"]
    engine = app.state.platform.engine
    S.append_lines(engine, sid, [
        {"role": "user", "content": "plan it", "turnId": "p1"},
        {"role": "assistant", "content": PLAN + "\n\nReady.", "turnId": "p1"},
    ])
    wire(app, Script(["Carried out."]))
    who: contextvars.ContextVar[str] = contextvars.ContextVar("who", default="")
    first_done = threading.Event()
    real_refusal = B.plan_run_refusal

    def gated_refusal(engine_, thread_id, from_turn):
        if who.get() == "second":
            # to_thread copies the caller's context: the SECOND caller reaches
            # the session only once the first act turn has ended.
            while not first_done.is_set():
                _time.sleep(0.01)
        return real_refusal(engine_, thread_id, from_turn)

    monkeypatch.setattr(B, "plan_run_refusal", gated_refusal)
    B._PLAN_RUNS[sid] = {"from": "p1", "taken": False}
    d = app.state.d

    async def call(name):
        who.set(name)
        return await B.run_approved_plan(d, sid, "p1")

    t1 = asyncio.create_task(call("first"))
    t2 = asyncio.create_task(call("second"))
    first = await t1
    assert first is not None
    await until(lambda: first.status != "running", 30)
    first_done.set()
    second = await t2
    if second is not None:
        await until(lambda: second.status != "running", 30)
    assert second is None
    assert len(_continuations(app, sid)) == 1
    assert sid not in B._PLAN_RUNS


async def test_the_act_turn_is_started_outside_the_plan_turns_own_scope(app, work, monkeypatch):
    """The plan turn's end callback runs in the context of whoever STARTED
    the plan turn: the act turn is started the way any turn is, never inside
    the plan turn's own hold / pin / branch scope."""
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    wire(app, HeldScript([
        (PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]),
        "Starting on the plan.",
        "Done it.",
    ]))
    seen: list = []
    real_start = B.start_build_turn

    async def spy(d, thread_id, text, turn_id, **kw):
        seen.append((text, current_hold()))
        return await real_start(d, thread_id, text, turn_id, **kw)

    monkeypatch.setattr(B, "start_build_turn", spy)
    await _plan_turn(app, sid, "bp-ctx")
    await until(lambda: [x for x in seen if x[0] == B.PLAN_RUN_TEXT])
    assert [x for x in seen if x[0] == B.PLAN_RUN_TEXT] == [(B.PLAN_RUN_TEXT, None)]
    act = (await until(lambda: _continuations(app, sid)))[0]
    lg = await until(lambda: turn_log(app, act["turnId"]))
    await until(lambda: lg.status != "running", 30)


async def test_the_view_says_a_plan_run_is_due(app, work):
    sid = new_session(app, work)["id"]
    B._PLAN_RUNS[sid] = {"from": "p1", "taken": False}
    _, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["plan_run"] is True
    B._PLAN_RUNS.pop(sid)
    _, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert view["plan_run"] is False


# --------------------------------------------------------------------------- #
# the guards, one by one
# --------------------------------------------------------------------------- #


def _watch(**done):
    w = B._ReplyWatch()
    w.plan_approved = True
    w.done = {"reply": "Ready.", **done}
    return w


def test_plan_run_due_only_for_a_clean_end():
    assert B.plan_run_due(_watch()) is True
    for bad in ({"truncated": True}, {"step_limit": {"limit": 60}}, {"cap": {"kind": "time"}},
                {"unread_steers": ["also this"]}, {"route": {"cut_off": {"times": 1, "recovered": False}}}):
        assert B.plan_run_due(_watch(**bad)) is False, bad
    # A cut-off the turn recovered from is a clean end.
    assert B.plan_run_due(_watch(route={"cut_off": {"times": 1, "recovered": True}})) is True
    w = _watch()
    w.plan_approved = False
    assert B.plan_run_due(w) is False
    w = _watch()
    w.steered_after_plan = True
    assert B.plan_run_due(w) is False
    w = _watch()
    w.error = {"detail": "x"}
    assert B.plan_run_due(w) is False
    w = _watch()
    w.account_limit = {"account": "a"}
    assert B.plan_run_due(w) is False
    w = _watch()
    w.done = None
    assert B.plan_run_due(w) is False


def test_the_watch_marks_the_approval_and_a_steer_after_it():
    w = B._ReplyWatch()
    w.see("round", json.dumps({"round": 0, "steer": "before"}))
    w.see("tool_call", json.dumps({"id": "x1", "name": "exit_plan_mode", "status": "started", "args": {}}))
    w.see("tool_call", json.dumps({"id": "x1", "name": "exit_plan_mode", "status": "finished", "ok": True}))
    assert w.plan_approved is True and w.steered_after_plan is False
    w.see("round", json.dumps({"round": 1, "steer": "after"}))
    assert w.steered_after_plan is True
    # A denied door is no approval.
    w2 = B._ReplyWatch()
    w2.see("tool_call", json.dumps({"id": "x1", "name": "exit_plan_mode", "status": "started", "args": {}}))
    w2.see("tool_call", json.dumps({"id": "x1", "name": "exit_plan_mode", "status": "finished", "ok": False}))
    assert w2.plan_approved is False


def _session_with_plan_turn(app, work, *, extra_setup=None, lines=None, archived=False, mode=None):
    engine = app.state.platform.engine
    sid = new_session(app, work)["id"]
    S.append_lines(engine, sid, lines or [
        {"role": "user", "content": "plan it", "turnId": "p1"},
        {"role": "assistant", "content": "Ready.", "turnId": "p1"},
    ])
    if extra_setup:
        S.update(engine, sid, setup=extra_setup)
    if mode:
        S.update(engine, sid, setup={"mode": mode})
    if archived:
        from iron_jarvis.core.db import session_scope
        from iron_jarvis.core.ids import utcnow
        from iron_jarvis.core.models import ChatThreadRecord

        with session_scope(engine) as db:
            row = db.get(ChatThreadRecord, sid)
            row.archived_at = utcnow()
            db.add(row)
            db.commit()
    return sid


def test_plan_run_refusal_reads_the_session(app, work):
    engine = app.state.platform.engine
    ok = _session_with_plan_turn(app, work)
    assert B.plan_run_refusal(engine, ok, "p1") == ""
    assert B.plan_run_refusal(engine, "nope", "p1") == "its session is gone"
    assert B.plan_run_refusal(engine, _session_with_plan_turn(app, work, archived=True), "p1") == \
        "its session is archived"
    assert B.plan_run_refusal(engine, _session_with_plan_turn(app, work, mode="plan"), "p1") == \
        "the session is in plan mode"
    newer = _session_with_plan_turn(app, work, lines=[
        {"role": "user", "content": "plan it", "turnId": "p1"},
        {"role": "assistant", "content": "Ready.", "turnId": "p1"},
        {"role": "user", "content": "actually, wait", "turnId": "u2"},
    ])
    assert B.plan_run_refusal(engine, newer, "p1") == "a newer message was sent"
    failed = _session_with_plan_turn(app, work, lines=[
        {"role": "user", "content": "plan it", "turnId": "p1"},
        {"role": "assistant", "content": "", "turnId": "p1", "error": "boom"},
    ])
    assert B.plan_run_refusal(engine, failed, "p1") == "the plan turn did not finish"
    assert B.plan_run_refusal(engine, ok, "other") == "its plan is no longer in the session"
    waiting = _session_with_plan_turn(app, work, extra_setup={
        "account_wait": {"account": "a", "armed_at": "2026-10-28T10:00:00"}, "account": "a",
    })
    assert B.plan_run_refusal(engine, waiting, "p1") == "it waits on its account"


async def test_a_waiting_message_starts_nothing(app, work):
    """A message the user queued while the plan turn ran goes out instead."""
    sid = _session_with_plan_turn(app, work)
    status, _ = await asgi(app, "PUT", f"/chat/threads/{sid}/queue", {"items": [{"text": "then this"}]})
    assert status == 200
    assert B.plan_run_refusal(app.state.platform.engine, sid, "p1") == "a message is waiting to be sent"


async def test_a_night_task_turn_never_starts_one(app, work, monkeypatch):
    from iron_jarvis.harness import night as N

    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    wire(app, Script([(PLAN, [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "Ready."]))
    # The turn runs as a night task's from the moment its plan is answered
    # (the night's own card handling is not what this checks).
    await _plan_turn(app, sid, "bp-night", on_card=lambda: monkeypatch.setattr(
        N, "current", lambda: SimpleNamespace(task_id="n1"),
    ))
    assert _continuations(app, sid) == [] and sid not in B._PLAN_RUNS
