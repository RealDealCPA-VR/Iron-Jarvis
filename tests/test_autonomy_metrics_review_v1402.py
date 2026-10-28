"""v1.402.0 part A — the REVIEW of the autonomy metrics.

Each a defect the first cut shipped, each red on that cut (mutation-checked):

1. A NEW chat's first question goes out before its first save names the
   chat (the page links the turn a moment later), so every card of that
   turn was asked with no ``thread_id`` and counted under no task — the
   commonest card of all read as "unattributed", and its task as hands-off.
   The lane now learns the chat when the ask settles, and ``compute`` places
   an unthreaded ask by its answer, by its turn's other asks, or by the turn
   record (``ChatTurnRecord``).
2. An ANSWER was placed by its own time, so a card answered after the
   person's next request helped the wrong task. It lands on its ask's task.
3. A run's sibling woken by "Allow for this run" (and the sidebar's switch)
   counted as a second interruption the person never acted on.
4. The weekly rows dropped the first day or two of a window that began late
   in a week, so they did not add up to the totals beside them.
5. A Build session writes its steer notes when the turn ENDS, so a note typed
   while a plan card waited never read as "between" — the plan was called
   avoidable. Any note from the moment the plan was asked keeps it off.
6. The chat's Try again and an edited-and-resent question fork the
   conversation (``branch.tails``) instead of re-sending the question: no
   retry was counted, and a task that needed three tries read hands-off.
7. Truncation (a read that hit its bound) was silent.
8. A mission TEAMMATE's ask was put under its own session — no task —
   instead of the mission's.
9. The browser sidebar's risk card was not counted at all.
10. The benchmark counted one ask per CALL where the app shows one card per
    round's same-kind calls; a question was not an ask; a run whose record
    hit its bound left calls out silently.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.approvals import flush_interrupt_notes, resolve_as
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_uid
from iron_jarvis.core.models import (
    AgentRun,
    AgentType,
    ChatThreadRecord,
    ChatTurnRecord,
    EventRecord,
)
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import autonomy as A
from iron_jarvis.harness_bench import asks as B
from tests.test_chat_approvals_v1187 import _ASK_MSG, _asgi_post, _drive_stream, _shell_call_stream

NOW = datetime.now(timezone.utc).timestamp()


def _events(platform, type_: str) -> list:
    return [e.payload for e in platform.event_bus.history if e.type == type_]


def _thread(tid: str, lines: list, *, surface: str = "") -> dict:
    return {"id": tid, "surface": surface, "messages": lines, "created_at": lines[0]["at"] if lines else NOW}


def _ev(type_: str, **p) -> dict:
    return {"type": type_, "payload": p}


# --------------------------------------------------------------------------- #
# 1. a new chat's first turn
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_new_chats_first_turn_card_learns_its_chat_when_it_settles(tmp_path):
    """Through the REAL stream lane: the turn starts with no thread (a new
    chat), the page links it after its first save, the person answers."""
    app = create_app(str(tmp_path / "home"))
    app.state.platform.router.stream = _shell_call_stream()
    seen: list[dict] = []

    async def link_then_allow(data):
        seen.append(data)
        status, _ = await _asgi_post(app, "/chat/turns/turn-new-chat/thread", {"thread_id": "chat_new"})
        assert status == 200
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    await _drive_stream(app, {
        "messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
        "turn_id": "turn-new-chat",
    }, link_then_allow)
    assert seen, "anti-vacuity: the card must come"
    await flush_interrupt_notes()
    p = app.state.platform
    asked, answered = _events(p, EventType.INTERRUPT_ASKED), _events(p, EventType.INTERRUPT_ANSWERED)
    assert len(asked) == 1 and len(answered) == 1
    assert "thread_id" not in asked[0], "the ask went out before the chat had a name"
    assert answered[0]["thread_id"] == "chat_new" and answered[0]["turn_id"] == "turn-new-chat"
    # …and the metrics place BOTH on the chat's task.
    lines = [{"role": "user", "content": _ASK_MSG, "at": NOW - 600},
             {"role": "assistant", "content": "done.", "at": NOW - 500}]
    ev = [{"type": EventType.INTERRUPT_ASKED, "payload": {**asked[0], "at": NOW - 590}},
          {"type": EventType.INTERRUPT_ANSWERED, "payload": {**answered[0], "at": NOW - 580}}]
    got = A.compute(threads=[_thread("chat_new", lines)], sessions=[], events=ev, days=30, now=NOW)
    assert got["interruptions"]["total"] == 1 and got["interruptions"]["unattributed"] == 0
    assert got["hands_off"] == 0, "the person answered a card: not hands-off"


def test_a_questions_turn_learns_its_chat_too():
    """The question channel (``ask_user`` / an app's question) of a new
    chat's first turn learns its chat the same way the cards do."""
    from iron_jarvis.daemon.mcp_turn import PENDING, TurnInteractions

    sent: list = []

    class Bus:
        async def publish(self, type_, payload=None, session_id=None):
            sent.append((type_, dict(payload or {})))

    async def go():
        ti = TurnInteractions(
            SimpleNamespace(platform=SimpleNamespace(event_bus=Bus())), asks_user=True,
            interrupt_facts={"surface": "chat", "thread_id": "", "turn_id": "turn_q",
                             "thread_for": lambda: "chat_q"},
        )
        task = asyncio.ensure_future(ti.ask_user("c1", [{"question": "Which one?"}]))
        for _ in range(200):
            if ti._mine:
                break
            await asyncio.sleep(0.01)
        ask = next(iter(ti._mine.values()))
        assert PENDING[ask.id].resolve("answered", {"answer": "the blue one"})
        await task
        await flush_interrupt_notes()

    asyncio.run(go())
    assert [t for t, _p in sent] == ["interrupt.asked", "interrupt.answered"]
    assert "thread_id" not in sent[0][1] and sent[1][1]["thread_id"] == "chat_q"
    assert "blue" not in json.dumps(sent) and "Which" not in json.dumps(sent)


def test_an_unthreaded_ask_is_placed_by_its_answer_its_turn_or_the_turn_record():
    lines = [{"role": "user", "content": "Rename the files", "at": NOW - 600},
             {"role": "assistant", "content": "Done", "at": NOW - 100}]
    th = [_thread("chat_x", lines)]
    # (a) its own answer knows the chat.
    ev = [_ev("interrupt.asked", kind="card", tool="shell", ask_id="a1", turn_id="t1", at=NOW - 590),
          _ev("interrupt.answered", kind="card", tool="shell", ask_id="a1", turn_id="t1", thread_id="chat_x",
              decision="once", actor="app", at=NOW - 580)]
    got = A.compute(threads=th, sessions=[], events=ev, days=30, now=NOW)
    assert got["interruptions"]["total"] == 1 and got["interruptions"]["unattributed"] == 0
    # (b) another ask of the same turn knows it.
    ev = [_ev("interrupt.asked", kind="card", tool="shell", ask_id="b1", turn_id="t2", at=NOW - 590),
          _ev("interrupt.answered", kind="card", tool="shell", ask_id="b1", turn_id="t2",
              decision="once", actor="app", at=NOW - 580),
          _ev("interrupt.asked", kind="card", tool="web_fetch", ask_id="b2", turn_id="t2",
              thread_id="chat_x", at=NOW - 570)]
    got = A.compute(threads=th, sessions=[], events=ev, days=30, now=NOW)
    assert got["interruptions"]["total"] == 2 and got["interruptions"]["unattributed"] == 0
    # (c) only the turn's record knows it.
    ev = [_ev("interrupt.asked", kind="card", tool="shell", ask_id="c1", turn_id="t3", at=NOW - 590)]
    got = A.compute(threads=th, sessions=[], events=ev, days=30, now=NOW, turn_threads={"t3": "chat_x"})
    assert got["interruptions"]["total"] == 1 and got["interruptions"]["unattributed"] == 0
    assert A.compute(threads=th, sessions=[], events=ev, days=30, now=NOW)["interruptions"]["unattributed"] == 1


def test_read_metrics_reads_the_turn_record_for_an_unthreaded_ask(tmp_path):
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine

    def naive(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)

    lines = [{"role": "user", "content": "Rename the files", "at": NOW - 600},
             {"role": "assistant", "content": "Done", "at": NOW - 100}]
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(id="chat_r", messages_json=json.dumps(lines)))
        db.add(ChatTurnRecord(id="turn_r", thread_id="chat_r", status="done"))
        db.add(EventRecord(id=new_uid("evt"), type="interrupt.asked", created_at=naive(NOW - 590),
                           payload_json=json.dumps({"kind": "card", "tool": "shell", "ask_id": "r1",
                                                    "turn_id": "turn_r", "at": NOW - 590})))
        db.commit()
    got = TestClient(app).get("/autonomy/metrics").json()
    assert got["interruptions"]["total"] == 1 and got["interruptions"]["unattributed"] == 0
    assert got["truncated"] == []


def test_an_ask_never_put_to_the_person_has_no_answer_either():
    """A cancel that lands before the card went out (the runtime awaits the
    WAITING save first) settles the note — but nothing was asked."""
    from iron_jarvis.core.approvals import InterruptNote

    sent: list = []

    class Bus:
        async def publish(self, type_, payload=None, session_id=None):
            sent.append(type_)

    async def go():
        n = InterruptNote(Bus(), kind="card", tool="shell", surface="agent", ask_id="apr_x")
        n.answered("stopped", "app")
        m = InterruptNote(Bus(), kind="card", tool="shell", surface="agent", ask_id="apr_y")
        m.asked()
        m.answered("stopped", "app")
        await flush_interrupt_notes()

    asyncio.run(go())
    assert sent == ["interrupt.asked", "interrupt.answered"]


# --------------------------------------------------------------------------- #
# 2-6. compute
# --------------------------------------------------------------------------- #


def test_an_answer_lands_on_the_task_its_ask_was_put_to():
    """An escalated run's card answered AFTER the person's next request helped
    the FIRST task, not the second."""
    lines = [
        {"role": "user", "content": "Rename the files", "at": NOW - 900},
        {"role": "assistant", "content": "Handing this to an agent", "at": NOW - 890,
         "agentRun": {"status": "failed"}},
        {"role": "user", "content": "Write a poem about the sea", "at": NOW - 500},
        {"role": "assistant", "content": "The sea…", "at": NOW - 490},
    ]
    ev = [_ev("interrupt.asked", kind="card", tool="shell", thread_id="c", ask_id="a", at=NOW - 880),
          _ev("interrupt.answered", kind="card", tool="shell", thread_id="c", ask_id="a",
              decision="once", actor="app", wait_ms=1, at=NOW - 400)]
    got = A.compute(threads=[_thread("c", lines)], sessions=[], events=ev, days=30, now=NOW)
    assert got["tasks"] == 2 and got["successful"] == 1
    # The poem (the one that finished) was never helped; the renaming was.
    assert got["hands_off"] == 1 and got["interruptions"]["total"] == 1


def test_a_sibling_settled_by_a_grant_is_one_interruption_not_two():
    lines = [{"role": "user", "content": "Rename the files", "at": NOW - 900},
             {"role": "assistant", "content": "Done", "at": NOW - 100}]
    ev = [_ev("interrupt.asked", kind="card", tool="shell", thread_id="c", ask_id="a", at=NOW - 800),
          _ev("interrupt.asked", kind="card", tool="shell", thread_id="c", ask_id="b", at=NOW - 799),
          _ev("interrupt.answered", kind="card", tool="shell", thread_id="c", ask_id="a",
              decision="conversation", actor="app", at=NOW - 700),
          _ev("interrupt.answered", kind="card", tool="shell", thread_id="c", ask_id="b",
              decision="conversation", actor="grant", at=NOW - 700)]
    got = A.compute(threads=[_thread("c", lines)], sessions=[], events=ev, days=30, now=NOW)
    i = got["interruptions"]
    assert i["total"] == 1 and i["by_kind"]["card"] == 1 and i["by_tool"] == [{"tool": "shell", "count": 1}]
    assert got["weekly"][-1]["interruptions"] == 1


def test_the_weekly_rows_add_up_to_the_window_even_when_it_starts_late_in_a_week():
    # A Monday noon: the 30-day window starts on a Saturday, a week the old
    # ceil(30 / 7) rows never reached.
    monday = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc).timestamp()
    start = monday - 29.9 * 86400
    lines = [{"role": "user", "content": "Plan the trip", "at": start},
             {"role": "assistant", "content": "Planned.", "at": start + 60},
             {"role": "user", "content": "Book the hotel", "at": monday - 3600},
             {"role": "assistant", "content": "Booked.", "at": monday - 3000}]
    got = A.compute(threads=[_thread("c", lines)], sessions=[], events=[], days=30, now=monday)
    assert got["tasks"] == 2
    assert sum(w["tasks"] for w in got["weekly"]) == got["tasks"]
    weeks = [w["week"] for w in got["weekly"]]
    assert weeks == sorted(weeks) and weeks[-1] == "2026-10-26"
    assert datetime.fromisoformat(weeks[0]).date() <= datetime.fromtimestamp(start, tz=timezone.utc).date()
    assert len(set(weeks)) == len(weeks)
    # Every window size keeps the same promise.
    for days in (1, 7, 8, 14, 30, 31, 90, 365):
        got = A.compute(threads=[_thread("c", lines)], sessions=[], events=[], days=days, now=monday)
        assert sum(w["tasks"] for w in got["weekly"]) == got["tasks"], days


def test_a_steer_written_when_the_turn_ended_keeps_a_plan_from_reading_untouched():
    """A Build session writes its steer notes at the turn's END — after the
    plan card was answered — so "between asked and answered" never saw them."""
    t0 = NOW - 3600
    lines = [{"role": "user", "content": "Plan the refactor", "at": t0},
             {"role": "user", "content": "use pathlib", "steer": True, "at": t0 + 300},
             {"role": "assistant", "content": "Planned and done.", "at": t0 + 300}]
    plan = {"kind": "plan", "tool": "exit_plan_mode", "thread_id": "b", "once_only": True}
    ev = [_ev("interrupt.asked", ask_id="p", at=t0 + 10, **plan),
          _ev("interrupt.answered", ask_id="p", decision="once", actor="app", at=t0 + 20, **plan)]
    got = A.compute(threads=[_thread("b", lines, surface="build")], sessions=[], events=ev, days=30, now=NOW)
    assert got["interruptions"]["avoidable"] == 0
    # v1.402.0 integrate (moved on purpose): with no steer at all an untouched
    # plan is STILL not avoidable — approving a plan is the one "approve once".
    plain = [lines[0], lines[2]]
    got = A.compute(threads=[_thread("b", plain, surface="build")], sessions=[], events=ev, days=30, now=NOW)
    assert got["interruptions"]["avoidable"] == 0 and got["interruptions"]["total"] == 1


def test_a_try_again_or_an_edited_question_is_a_retry():
    t0 = NOW - 3600
    old_tail = [{"role": "assistant", "content": "A bad answer", "at": t0 + 10}]
    regenerated = [
        {"role": "user", "content": "Summarize my notes", "at": t0},
        {"role": "assistant", "content": "A good answer", "at": t0 + 30,
         "branch": {"tails": [old_tail, [{"role": "assistant", "content": "worse", "at": t0 + 20}], None]}},
    ]
    got = A.compute(threads=[_thread("c", regenerated)], sessions=[], events=[], days=30, now=NOW)
    assert got["tasks"] == 1 and got["successful"] == 1
    assert got["nudges"]["retries"] == 2 and got["nudges"]["human"] == 2
    assert got["hands_off"] == 0, "two Try agains are help"
    edited = [
        {"role": "user", "content": "Summarize my notes in French", "at": t0 + 40,
         "branch": {"tails": [[{"role": "user", "content": "Summarize my notes", "at": t0},
                               {"role": "assistant", "content": "A summary", "at": t0 + 10}], None]}},
        {"role": "assistant", "content": "Un résumé", "at": t0 + 60},
    ]
    got = A.compute(threads=[_thread("c", edited)], sessions=[], events=[], days=30, now=NOW)
    assert got["nudges"]["retries"] == 1 and got["hands_off"] == 0
    # A malformed fork (two live slots, an empty stored tail) is no fork.
    for bad in ({"tails": [None, None]}, {"tails": [[], None]}, {"tails": [None]}, {"tails": "x"}):
        line = {"role": "assistant", "content": "x", "at": t0 + 5, "branch": bad}
        got = A.compute(threads=[_thread("c", [regenerated[0], line])], sessions=[], events=[], days=30, now=NOW)
        assert got["nudges"]["retries"] == 0, bad


def test_a_bare_ok_is_a_nudge_only_when_it_answers_an_offer():
    """ "ok" / "yes" / "sure, thanks" after a reply that asked nothing is an
    acknowledgement; after "Want me to …?" it moves the work on."""
    for line in ("ok", "OK.", "yes please", "sure, thanks", "ok cool", "please"):
        assert A.classify_line(line, after="Done. The file is saved.") == "ack", line
        assert A.classify_line(line, after="Saved. Want me to add tests too?") == "nudge", line
        assert A.classify_line(line) == "nudge", line  # the reply not known: as before
    for line in ("ok continue", "yes do it", "please do", "keep going", "try again"):
        assert A.classify_line(line, after="Done.") == "nudge", line
    t0 = NOW - 3600
    acked = [{"role": "user", "content": "Rename the files", "at": t0},
             {"role": "assistant", "content": "Done — all 40 renamed.", "at": t0 + 10},
             {"role": "user", "content": "ok", "at": t0 + 20}]
    got = A.compute(threads=[_thread("c", acked)], sessions=[], events=[], days=30, now=NOW)
    assert got["nudges"]["nudge_lines"] == 0 and got["hands_off"] == 1
    offered = [*acked[:1], {"role": "assistant", "content": "Done. Shall I zip them?", "at": t0 + 10},
               acked[2], {"role": "assistant", "content": "Zipped.", "at": t0 + 30}]
    got = A.compute(threads=[_thread("c", offered)], sessions=[], events=[], days=30, now=NOW)
    assert got["nudges"]["nudge_lines"] == 1 and got["hands_off"] == 0


def test_a_read_that_hit_its_bound_says_so(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    with session_scope(app.state.platform.engine) as db:
        for i in range(2):
            db.add(ChatThreadRecord(id=f"chat_{i}", messages_json=json.dumps(
                [{"role": "user", "content": "hello", "at": NOW - 60}])))
        db.commit()
    client = TestClient(app)
    assert client.get("/autonomy/metrics").json()["truncated"] == []
    monkeypatch.setattr(A, "MAX_THREADS", 2)
    got = client.get("/autonomy/metrics").json()
    assert got["truncated"] == ["threads"] and got["tasks"] == 2
    assert A.empty(30)["truncated"] == []


# --------------------------------------------------------------------------- #
# 8. a mission's teammate asks for the mission
# --------------------------------------------------------------------------- #


async def test_a_mission_teammates_ask_belongs_to_the_mission(tmp_path):
    from iron_jarvis.agents.team import MISSION_MEMBER_ORIGIN, MISSION_ORIGIN

    app = create_app(str(tmp_path / "rt"))
    p = app.state.platform
    with session_scope(p.engine) as db:
        db.add(SessionRow(id="session_root", origin=MISSION_ORIGIN, task="Ship it"))
        db.add(SessionRow(id="session_member", origin=MISSION_MEMBER_ORIGIN, task="a piece"))
        db.add(AgentRun(id="run_root", session_id="session_root"))
        db.add(AgentRun(id="run_member", session_id="session_member", parent_id="run_root"))
        db.commit()
    runtime = AgentRuntime(p)
    member = SimpleNamespace(id="session_member", origin=MISSION_MEMBER_ORIGIN, options_json="{}")
    tc = SimpleNamespace(name="shell", arguments={"command": "echo hi"})

    async def answer():
        for _ in range(400):
            rows = list(p.approvals._meta.values())
            if rows:
                resolve_as(p.approvals, rows[0]["approval_id"], "once", actor="app")
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the pause never filed its ask")

    answerer = asyncio.create_task(answer())
    await runtime._pause_for_approval(member, tc, get_agent_definition(AgentType.BUILDER), set())
    await answerer
    await flush_interrupt_notes()
    asked = _events(p, EventType.INTERRUPT_ASKED)
    answered = _events(p, EventType.INTERRUPT_ANSWERED)
    assert asked[0]["session_id"] == "session_root" and answered[0]["session_id"] == "session_root"
    sessions = [{"id": "session_root", "origin": MISSION_ORIGIN, "task": "Ship it", "outcome": "completed",
                 "created_at": NOW - 600, "options_json": "{}"}]
    ev = [{"type": EventType.INTERRUPT_ASKED, "payload": {**asked[0], "at": NOW - 500}},
          {"type": EventType.INTERRUPT_ANSWERED, "payload": {**answered[0], "at": NOW - 490}}]
    got = A.compute(threads=[], sessions=sessions, events=ev, days=30, now=NOW)
    assert got["interruptions"]["total"] == 1 and got["interruptions"]["unattributed"] == 0
    assert got["hands_off"] == 0


async def test_a_follow_up_lands_on_the_run_it_continues_not_the_newest_of_its_origin(tmp_path):
    """Every mission has the origin ``job:mission``: a follow-up asked of
    mission A after mission B started belongs to A (the Continue door's own
    text names A's task) — it used to land on B and overwrite B's outcome."""
    from iron_jarvis.agents.team import MISSION_ORIGIN
    from iron_jarvis.core.models import SessionStatus

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    with session_scope(p.engine) as db:
        db.add(SessionRow(id="session_a", origin=MISSION_ORIGIN, task="Write the brief",
                          status=SessionStatus.COMPLETED, outcome="failed", summary="no"))
        db.add(SessionRow(id="session_b", origin=MISSION_ORIGIN, task="Plan the launch",
                          status=SessionStatus.COMPLETED, outcome="completed", summary="ok"))
        db.commit()
    follow = await app.state.orchestrator.continue_session("session_a", "Shorter, please")
    with session_scope(p.engine) as db:
        rows = [
            {"id": r.id, "origin": r.origin, "task": r.task, "outcome": r.outcome,
             "created_at": NOW - 600 + i * 60, "options_json": r.options_json}
            for i, r in enumerate(db.get(SessionRow, sid) for sid in ("session_a", "session_b", follow.id))
        ]
    rows[2]["outcome"] = "completed"  # the follow-up fixed A
    tasks, by_session = A.agent_tasks(rows)
    assert len(tasks) == 2
    assert by_session[follow.id] is by_session["session_a"]
    a_task, b_task = by_session["session_a"], by_session["session_b"]
    assert (a_task.nudges, a_task.success) == (1, True)
    assert (b_task.nudges, b_task.success) == (0, True)
    # A follow-up of the follow-up still finds A.
    with session_scope(p.engine) as db:
        row = db.get(SessionRow, follow.id)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()
    again = await app.state.orchestrator.continue_session(follow.id, "And a title")
    with session_scope(p.engine) as db:
        rows.append({"id": again.id, "origin": MISSION_ORIGIN, "task": db.get(SessionRow, again.id).task,
                     "outcome": "failed", "created_at": NOW - 100, "options_json": "{}"})
    tasks, by_session = A.agent_tasks(rows)
    assert by_session[again.id] is by_session["session_a"] and by_session["session_a"].nudges == 2
    assert by_session["session_b"].nudges == 0


# --------------------------------------------------------------------------- #
# 9. the sidebar's risk card
# --------------------------------------------------------------------------- #


def test_the_sidebars_risk_card_is_counted_as_a_once_only_safety_card():
    from iron_jarvis.browser import protocol as P
    from iron_jarvis.browser.panel import PanelTurns

    sent: list = []

    class Bus:
        async def publish(self, type_, payload=None, session_id=None):
            sent.append((type_, dict(payload or {})))

    class Conn:
        def __init__(self):
            self.frames: list = []

        async def send(self, frame):
            self.frames.append(frame)

    async def drive():
        turns = PanelTurns(SimpleNamespace(browser=None, event_bus=Bus()))
        conn = Conn()
        turns._conn, turns._turn_id = conn, "panel_t1"
        turns._task = asyncio.ensure_future(asyncio.sleep(30))
        try:
            ask = asyncio.ensure_future(turns.resolve_risk_ask(
                SimpleNamespace(id="appr_1", reason="a payment field", run_id="chat", status="pending"),
                SimpleNamespace(turn_id="panel_t1"), name="browser_type",
                args={"target": {"name": "Card number"}, "text": "4111 1111"},
            ))
            for _ in range(200):
                cards = [f["payload"] for f in conn.frames if f.get("event") == P.PANEL_EVENT_APPROVAL]
                if cards:
                    break
                await asyncio.sleep(0.01)
            await turns._decide(conn, P.PANEL_ACTION_APPROVE, cards[0]["id"])
            assert await ask is True
            await flush_interrupt_notes()
        finally:
            turns._task.cancel()

    asyncio.run(drive())
    assert [t for t, _p in sent] == ["interrupt.asked", "interrupt.answered"]
    asked, answered = sent[0][1], sent[1][1]
    assert asked["once_only"] is True and asked["reason"] == "browser_risk" and asked["turn_id"] == "panel_t1"
    assert answered["decision"] == "once" and answered["actor"] == "app"
    assert "4111" not in json.dumps(sent) and "Card number" not in json.dumps(sent)


# --------------------------------------------------------------------------- #
# 10. the benchmark
# --------------------------------------------------------------------------- #


def test_one_card_per_rounds_kind_and_a_question_is_an_ask(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    jarvis = [("shell", {}, 1), ("shell", {}, 1), ("read_file", {}, 1), ("shell", {}, 2), ("shell", {}, None),
              ("ask_user", {}, 2), ("ask_user", {}, 2)]
    got = B.would_have_asked(jarvis, runner="jarvis", permissions=p.permissions, registry=p.registry)
    # round 1: one shell card; round 2: one shell card; no round: one; two questions.
    assert got == {"calls": 7, "asked": 5, "not_known": 0, "by_tool": {"shell": 3, "ask_user": 2}}
    # Two-tuples (no round) keep one ask per call.
    got = B.would_have_asked([("shell", {}), ("shell", {})], runner="jarvis", permissions=p.permissions,
                             registry=p.registry)
    assert got["asked"] == 2


def test_claude_codes_parallel_calls_in_one_message_are_one_round():
    from iron_jarvis.harness_bench.runners import parse_claude_stream

    lines = [
        {"type": "assistant", "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "assistant", "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pwd"}}]}},
        {"type": "assistant", "message": {"id": "msg_2", "content": [
            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "t4", "name": "AskUserQuestion", "input": {}}]}},
    ]
    parsed = parse_claude_stream("\n".join(json.dumps(x) for x in lines))
    assert [u["round"] for u in parsed["tool_uses"]] == [0, 0, 1, 1]
    vocab, calls = B.calls_of(SimpleNamespace(extra={"tool_uses": parsed["tool_uses"]}))
    got = B.would_have_asked(calls, runner=vocab)
    assert got["asked"] == 3 and got["by_tool"] == {"shell": 2, "ask_user": 1}


def test_a_run_whose_record_hit_its_bound_says_what_it_could_not_replay():
    from iron_jarvis.harness_bench.bench import _would_have_asked
    from iron_jarvis.harness_bench.runners import TRAJECTORY_ENTRIES

    traj = [{"kind": "call", "tool": "read_file", "args": {}, "round": i} for i in range(TRAJECTORY_ENTRIES)]
    got = _would_have_asked(SimpleNamespace(), SimpleNamespace(extra={}, tool_calls=TRAJECTORY_ENTRIES + 40), traj)
    assert got["calls"] == TRAJECTORY_ENTRIES + 40 and got["not_known"] == 40
    # Not capped: the run's own steps beyond its calls (a finish check) are
    # never called "not known".
    got = _would_have_asked(SimpleNamespace(), SimpleNamespace(extra={}, tool_calls=5), traj[:3])
    assert got["calls"] == 3 and got["not_known"] == 0


def test_the_reports_say_allow_once_and_one_card_per_round():
    from iron_jarvis.harness_bench.report import asks_words

    words = asks_words({"would_ask_runs": 2, "would_ask_per_task": 1.5, "would_ask_by_tool": {"shell": 3}})
    assert "Allow once" in words and "one card per round" in words and "Ask when risky" in words
    assert "approve-for-me" not in words
