"""v1.402.0 part A — the autonomy metrics: how hands-off Jarvis is.

The user's three numbers ("give Jarvis a goal, approve an authority boundary
once, let it work autonomously until completion"):

1. unnecessary interruptions per task,
2. human nudges per successful task,
3. the share of tasks completed without human intervention.

None was computable before this release: a chat/Build card published no event
at all (only SSE frames), an answer recorded neither who gave it nor how long
it took, nothing marked a typed line as a nudge, and "task" was undefined.

What must hold, each pinned and mutation-checked:

* EVERY asking site publishes ``interrupt.asked`` / ``interrupt.answered``
  (counting only): the stream lane's cards (ordinary and once-only), the
  Build plan door (kind plan), ``ask_user`` (kind question, with the
  ``deferred`` flag), the agent runtime's pause (and ``approve_plan`` → plan).
  ``actor`` says who settled it: app / phone / grant / timeout / bench.
* Never an alert: not a default notifier event, not on the bell, not in the
  desktop's audit replay, not on the audit timeline.
* ``classify_line`` (≥ 30 nudge rows, ≥ 30 not), ``question_deferred``,
  the avoidable rules (never once-only, never a denial), and the endpoint over
  a seeded database computing all three numbers EXACTLY.
* The bench counts the cards it answered (``facts.asks``) and replays every
  run's calls through the real permission engine (``would_have_asked``),
  agreeing with the REAL stream lane.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.agents.runtime as runtime_mod
import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS, Notifier, bell_pings
from iron_jarvis.core.approvals import (
    ChatApprovals,
    InterruptNote,
    flush_interrupt_notes,
    resolve_as,
    run_place,
    settled_actor,
    surface_for,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_uid
from iron_jarvis.core.models import AgentType, ChatThreadRecord, EventRecord
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import autonomy as A
from iron_jarvis.harness_bench import asks as B
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_plan_ask_v1367 import _finish, _start
from tests.test_build_sessions_v1367 import Script, new_session, turn_frames, until, wire
from tests.test_chat_turn_stop_v1241 import _asgi_post

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _events(platform, type_: str) -> list:
    return [e for e in platform.event_bus.history if e.type == type_]


async def _card(app, turn_id: str) -> dict:
    return (await until(lambda: [d for e, d in turn_frames(app, turn_id) if e == "approval"]))[0]


# --------------------------------------------------------------------------- #
# 1. the events, from the REAL lanes
# --------------------------------------------------------------------------- #


async def test_a_card_answered_once_is_one_asked_and_one_answered_event(app, work):
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop, task = await _start(app, sid, "run it", "am-once")
    card = await _card(app, "am-once")
    await asyncio.sleep(0.05)  # the person reads the card
    assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "once"}))[0] == 200
    await _finish(app, "am-once", drop, task)
    await flush_interrupt_notes()
    p = app.state.platform
    asked, answered = _events(p, EventType.INTERRUPT_ASKED), _events(p, EventType.INTERRUPT_ANSWERED)
    assert len(asked) == 1 and len(answered) == 1, (asked, answered)
    a, b = asked[0].payload, answered[0].payload
    assert a["kind"] == "card" and a["tool"] == "shell" and a["surface"] == "build"
    assert a["thread_id"] == sid and a["turn_id"] == "am-once" and a["ask_id"] == card["id"]
    assert a["args_hash"] == card["args_hash"] and "once_only" not in a
    assert b["decision"] == "once" and b["actor"] == "app"
    assert isinstance(b["wait_ms"], int) and b["wait_ms"] >= 40
    # Counting only: no arguments, no session tag (a run's live list shows
    # every event tagged with its session — it must not grow a second row).
    for e in (*asked, *answered):
        assert e.session_id is None
        assert "args" not in e.payload and "echo hi" not in json.dumps(e.payload)


async def test_a_phone_answer_is_settled_by_the_phone(app, work):
    from tests.test_comm_approvals_v1200 import ChatMockChannel, CHAT_CFG, _msg, _poller, _register_approval

    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop, task = await _start(app, sid, "run it", "am-phone")
    card = await _card(app, "am-phone")
    platform = app.state.platform
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, _t, pstore, _n = _poller(platform, ch)
    _register_approval(pstore, approval_id=card["id"])
    res = await poller._handle("tg", ch, _msg("approve"))
    assert res["status"] == "approval_answered"
    await _finish(app, "am-phone", drop, task)
    await flush_interrupt_notes()
    answered = _events(platform, EventType.INTERRUPT_ANSWERED)
    assert [x.payload["actor"] for x in answered] == ["phone"]
    assert answered[0].payload["decision"] == "once"


async def test_a_card_cut_off_while_it_waits_is_settled_stopped(tmp_path, monkeypatch):
    """A wait that never ends by itself (the turn's task is cancelled — a
    shutdown, a dropped unnamed turn) still settles its ask: "stopped"."""
    from tests._fakes import harness_lane as H

    p = create_app(str(tmp_path / "home")).state.platform
    ws = tmp_path / "ws"
    ws.mkdir()
    H.wire(p, monkeypatch, [[H.call("shell", "c1", command="echo hi")], "done"])
    gen = await H.stream_chat_turn(
        p, H.BUILTIN_PERSONAS, H.body(ws, approval_mode="approve_for_me"), harness="generic",
        session_id=H.SESSION, round_budget=10, stay_in_chat=True,
    )
    seen: list[str] = []

    async def consume():
        async for chunk in gen:
            seen.extend(e for e, _d in H.frames_of([chunk]))

    task = asyncio.create_task(consume())
    await until(lambda: "approval" in seen)
    await asyncio.sleep(0.2)  # the lane is now parked inside its wait
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await flush_interrupt_notes()
    answered = [e.payload for e in _events(p, EventType.INTERRUPT_ANSWERED)]
    assert [(a["decision"], a["actor"]) for a in answered] == [("stopped", "app")]


async def test_a_card_nobody_answers_is_settled_by_the_clock(app, work, monkeypatch):
    monkeypatch.setattr(chat_routes, "CHAT_ASK_TIMEOUT_S", 0.3)
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop, task = await _start(app, sid, "run it", "am-clock")
    await _finish(app, "am-clock", drop, task)
    await flush_interrupt_notes()
    answered = _events(app.state.platform, EventType.INTERRUPT_ANSWERED)
    assert len(answered) == 1
    assert answered[0].payload["decision"] == "timeout" and answered[0].payload["actor"] == "timeout"


async def test_the_plan_door_is_a_plan(app, work):
    sid = new_session(app, work, mode="plan")["id"]
    wire(app, Script([("Plan: 1. a", [ToolCall(id="x1", name="ExitPlanMode", arguments={})]), "ok"]))
    drop, task = await _start(app, sid, "plan it", "am-plan")
    card = await _card(app, "am-plan")
    await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "always"})
    await _finish(app, "am-plan", drop, task)
    await flush_interrupt_notes()
    p = app.state.platform
    asked, answered = _events(p, EventType.INTERRUPT_ASKED), _events(p, EventType.INTERRUPT_ANSWERED)
    assert [x.payload["kind"] for x in asked] == ["plan"]
    assert asked[0].payload["tool"] == "exit_plan_mode" and asked[0].payload["once_only"] is True
    # The answer as it took effect: a wider answer on the door is "once".
    assert answered[0].payload["decision"] == "once" and answered[0].payload["kind"] == "plan"


_QUESTION = {"question": "Which database?", "header": "DB",
             "options": [{"label": "sqlite", "description": "local file"},
                         {"label": "postgres", "description": "a server"}]}


async def _ask_user(app, work, turn_id: str, answer: dict) -> list:
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="q1", name="AskUserQuestion", arguments=_QUESTION)], "ok"]))
    drop, task = await _start(app, sid, "set up storage", turn_id)
    ask = (await until(lambda: [d for e, d in turn_frames(app, turn_id) if e == "ask_user"]))[0]
    assert (await _asgi_post(app, f"/build/sessions/{sid}/answer", {"ask_id": ask["id"], **answer}))[0] == 200
    await _finish(app, turn_id, drop, task)
    await flush_interrupt_notes()
    p = app.state.platform
    asked = [e.payload for e in _events(p, EventType.INTERRUPT_ASKED)]
    answered = [e.payload for e in _events(p, EventType.INTERRUPT_ANSWERED)]
    assert len(asked) == 1 and asked[0]["kind"] == "question" and asked[0]["tool"] == "ask_user"
    assert asked[0]["thread_id"] == sid and asked[0]["surface"] == "build"
    assert len(answered) == 1 and answered[0]["actor"] == "app"
    # Never the question's words or the answer's.
    assert "database" not in json.dumps(asked + answered).lower()
    return answered


async def test_a_question_answered_in_the_users_own_words_is_not_deferred(app, work):
    answered = await _ask_user(app, work, "am-q1", {"answer": "postgres, on the NAS"})
    assert answered[0]["decision"] == "answered" and "deferred" not in answered[0]


async def test_a_question_handed_back_is_deferred(app, work):
    answered = await _ask_user(app, work, "am-q2", {"answer": "you decide"})
    assert answered[0]["deferred"] is True


async def test_a_skipped_question_is_deferred(app, work):
    answered = await _ask_user(app, work, "am-q3", {"skip": True})
    assert answered[0]["decision"] == "skipped" and answered[0]["deferred"] is True


@pytest.fixture
def rt(tmp_path):
    app = create_app(str(tmp_path / "rt"))
    return SimpleNamespace(runtime=AgentRuntime(app.state.platform), platform=app.state.platform)


async def _answer_first(platform, decision: str, *, actor: str = "app") -> None:
    for _ in range(400):
        rows = [m for m in list(platform.approvals._meta.values())]
        if rows:
            resolve_as(platform.approvals, rows[0]["approval_id"], decision, actor=actor)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the pause never filed its ask")


async def test_the_agent_runtimes_pause_publishes_the_events(rt):
    session = SimpleNamespace(id="session_metric", origin="chat",
                              options_json=json.dumps({"chat_thread_id": "chat_abc"}))
    tc = SimpleNamespace(name="shell", arguments={"command": "echo hi"})
    answerer = asyncio.create_task(_answer_first(rt.platform, "once"))
    deny, _extra = await rt.runtime._pause_for_approval(
        session, tc, get_agent_definition(AgentType.BUILDER), set()
    )
    await answerer
    await flush_interrupt_notes()
    assert deny == ""
    asked = [e.payload for e in _events(rt.platform, EventType.INTERRUPT_ASKED)]
    answered = [e.payload for e in _events(rt.platform, EventType.INTERRUPT_ANSWERED)]
    assert asked == [{**asked[0]}] and asked[0]["kind"] == "card" and asked[0]["tool"] == "shell"
    # A run a chat started reports to that chat; its session rides the payload.
    assert asked[0]["surface"] == "agent" and asked[0]["thread_id"] == "chat_abc"
    assert asked[0]["session_id"] == "session_metric"
    assert answered[0]["decision"] == "once" and answered[0]["actor"] == "app"


async def test_the_runtimes_plan_approval_is_a_plan_and_a_timeout_is_the_clocks(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "ATTENDED_APPROVAL_TIMEOUT_S", 0.2)
    session = SimpleNamespace(id="session_plan", origin="chat")
    refusal = await rt.runtime.ask_plan_approval(
        None, session, get_agent_definition(AgentType.BUILDER), set(), ["read it", "fix it"]
    )
    await flush_interrupt_notes()
    assert refusal  # nobody answered
    asked = [e.payload for e in _events(rt.platform, EventType.INTERRUPT_ASKED)]
    answered = [e.payload for e in _events(rt.platform, EventType.INTERRUPT_ANSWERED)]
    assert asked[0]["kind"] == "plan" and asked[0]["tool"] == "approve_plan"
    assert answered[0]["decision"] == "timeout" and answered[0]["actor"] == "timeout"


def test_a_sibling_woken_by_a_grant_is_the_grants():
    async def go():
        reg = ChatApprovals()
        a, fa = reg.request("shell", {}, session_id="s1")
        b, fb = reg.request("shell", {}, session_id="s1")
        assert reg.resolve(a, "conversation") is True  # the person, in the app
        assert reg.resolve_where("s1", "shell", "conversation") == 1  # the sibling
        await asyncio.sleep(0)
        return reg.actor_of(a), reg.actor_of(b), reg.pop(b)

    first, sibling, popped = asyncio.run(go())
    assert first == "app" and sibling == "grant" and popped["actor"] == "grant"


def test_settled_actor_and_where_an_ask_was_put():
    reg = ChatApprovals()
    assert settled_actor(reg, "nope", "timeout") == "timeout"
    assert settled_actor(SimpleNamespace(), "x", "once") == "app"  # a registry double
    assert surface_for("chat") == "chat" and surface_for("build:abc") == "build"
    assert surface_for("build:bench-1f") == "bench" and surface_for("build:abc", night=object()) == "night"
    assert run_place(SimpleNamespace(options_json='{"build_session": "build:t1"}')) == ("build", "t1")
    assert run_place(SimpleNamespace(options_json='{"chat_thread_id": "c1"}')) == ("agent", "c1")
    assert run_place(SimpleNamespace()) == ("agent", "")


def test_a_note_says_once_and_carries_no_text():
    sent: list = []

    class Bus:
        async def publish(self, type_, payload=None, session_id=None):
            sent.append((type_, payload, session_id))

    async def go():
        n = InterruptNote(Bus(), kind="weird", tool="shell", surface="mars", ask_id="apr_1",
                          once_only=True, reason="git; rm -rf /")
        n.asked()
        n.answered("once", "nobody", deferred=True, edited="yes")
        n.answered("deny", "app")  # a second settle is a no-op
        await flush_interrupt_notes()

    asyncio.run(go())
    assert [t for t, _p, _s in sent] == ["interrupt.asked", "interrupt.answered"]
    asked, answered = sent[0][1], sent[1][1]
    assert asked["kind"] == "card" and asked["surface"] == "chat" and "reason" not in asked
    assert answered["actor"] == "app" and answered["deferred"] is True and "edited" not in answered
    assert all(s is None for _t, _p, s in sent)


# --------------------------------------------------------------------------- #
# 2. never an alert
# --------------------------------------------------------------------------- #


def test_the_metric_events_are_never_an_alert_a_bell_row_a_desktop_notice_or_a_timeline_row(tmp_path):
    metric = (EventType.INTERRUPT_ASKED, EventType.INTERRUPT_ANSWERED, EventType.AUTONOMY_NUDGED)
    for t in metric:
        assert t not in DEFAULT_ALERT_EVENTS
        assert bell_pings({"type": t, "payload": {}}) is False
        assert Notifier().on_event({"type": t, "payload": {}}) is None
    bell = (ROOT / "dashboard" / "components" / "NotificationBell.tsx").read_text(encoding="utf-8")
    bridge = (ROOT / "dashboard" / "components" / "DesktopNotifyBridge.tsx").read_text(encoding="utf-8")
    main_js = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    for src in (bell, bridge):
        assert "interrupt." not in src and "autonomy.nudged" not in src
    m = re.search(r"const RUN_OUTCOME_AUDIT_TYPES =\s*\"([^\"]+)\"", main_js)
    assert m and "interrupt" not in m.group(1) and "autonomy" not in m.group(1)
    # The audit timeline (the Activity page) leaves them out.
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    with session_scope(p.engine) as db:
        for t in (*metric, "approval.requested"):
            db.add(EventRecord(id=new_uid("evt"), type=t, payload_json="{}"))
        db.commit()
    entries = TestClient(app).get("/audit").json()["entries"]
    types = {e.get("type") for e in entries}
    assert "approval.requested" in types and not types & set(metric)


# --------------------------------------------------------------------------- #
# 3. the classifier
# --------------------------------------------------------------------------- #

NUDGES = [
    "continue", "Continue.", "keep going", "go on", "carry on", "proceed", "go ahead",
    "yes do it", "Yes, do it!", "try again", "retry", "fix it", "fix that", "fix this",
    "check it", "check that", "finish it", "still broken", "still failing", "didn't work",
    "same error", "ok continue", "please continue", "continue please", "keep going please",
    "yes", "yes please", "sure", "go for it", "do it", "that didn't work", "it's still broken",
    "tests still failing", "the tests are still failing", "not done yet", "you're not done",
    "don't stop", "carry on where you left off", "continue where you left off", "try it again",
    "nope still broken", "same error again", "ok, keep going", "alright go ahead",
    "it failed again", "still not working", "doesn't work", "do the rest", "finish up", "next",
]

NOT_NUDGES = [
    "continue the essay with a paragraph about tax", "fix the bug in line 4",
    "write a poem about cats", "What does this function do?",
    "fix it by adding a semicolon to line 4", "keep going and add a test for parse()",
    "check the logs for errors", "go on vacation planning: list 5 beaches",
    "try a different approach using pandas", "retry the upload with a smaller file",
    "add tests", "make the button blue", "still need the README updated", "why did it fail?",
    "continue with the next chapter and add a villain", "yes but use the other file instead",
    "please refactor auth.py", "run the tests in the api folder", "summarize my latest emails",
    "next, write the docs for the parser module and publish them", "no, use the other file",
    "proceed with caution: back up the database before migrating",
    "go ahead and email the report to Dana", "do it in TypeScript instead",
    "check that the invoices total 4,200", "finish the slide about Q3 revenue",
    "it's still broken on Safari only when the window is narrow and the menu is open",
    "same error but now in the login page",
    "keep going until every file in the folder has a header comment and a license line",
    "Draft a reply to the landlord", "Translate this into Spanish", "thanks!", "great job",
    # Every clause a nudge phrase, but past the 12-word limit: a request.
    "continue, keep going, carry on, go ahead, try again, fix it, finish it",
]


@pytest.mark.parametrize("line", NUDGES)
def test_a_nudge_is_a_nudge(line):
    assert A.classify_line(line) == "nudge"


@pytest.mark.parametrize("line", NOT_NUDGES)
def test_a_request_or_a_thanks_is_not_a_nudge(line):
    assert A.classify_line(line) in ("request", "ack")


def test_the_tables_are_big_enough_and_a_thanks_is_neither():
    assert len(NUDGES) >= 30 and len([x for x in NOT_NUDGES if A.classify_line(x) == "request"]) >= 30
    assert A.classify_line("thanks!") == "ack" and A.classify_line("great job") == "ack"
    assert A.classify_line("continue the essay with a paragraph about tax") == "request"


def test_the_same_request_again_is_a_retry():
    assert A.classify_line("Write a poem.", ["write a poem"]) == "retry"
    assert A.classify_line("write a poem  ", ["Write a poem!"]) == "retry"
    assert A.classify_line("write a song", ["write a poem"]) == "request"


QUESTIONS = [{"id": "q1", "question": "Which DB?", "options": [
    {"label": "sqlite", "description": "local"}, {"label": "postgres (recommended)", "description": ""},
    {"label": "mysql", "description": ""}]}]


@pytest.mark.parametrize("answer,deferred", [
    ("you decide", True), ("Up to you.", True), ("your call", True), ("whatever", True),
    ("either", True), ("doesn't matter", True), ("sqlite", True), ("postgres (recommended)", True),
    ("mysql", False), ("postgres, on the NAS", False), (["sqlite"], True), (["mysql", "sqlite"], False),
    ({"q1": "you decide"}, True), ({"q1": "mysql"}, False), ("", False),
])
def test_a_question_handed_back_is_read_without_its_words(answer, deferred):
    assert A.question_deferred(QUESTIONS, "answered", answer) is deferred


def test_a_skip_is_deferred_and_no_answer_is_not():
    assert A.question_deferred(QUESTIONS, "skipped", None) is True
    assert A.question_deferred(QUESTIONS, "stopped", None) is False
    assert A.question_deferred(QUESTIONS, "unanswered", None) is False


# --------------------------------------------------------------------------- #
# 4. the avoidable rules (pure)
# --------------------------------------------------------------------------- #

NOW = datetime.now(timezone.utc).timestamp()


def _thread(tid: str, start: float, *, surface: str = "", lines: list | None = None) -> dict:
    msgs = lines or [
        {"role": "user", "content": "Rename every file", "at": start},
        {"role": "assistant", "content": "Done.", "at": start + 500},
    ]
    return {"id": tid, "surface": surface, "messages": msgs, "created_at": start}


def _ask(tid: str, at: float, ask_id: str, *, kind="card", tool="shell", h="h1", **kw) -> dict:
    p = {"kind": kind, "tool": tool, "surface": "chat", "thread_id": tid, "ask_id": ask_id,
         "args_hash": h, "at": at, **kw}
    return {"type": "interrupt.asked", "payload": p}


def _ans(tid: str, at: float, ask_id: str, decision="once", actor="app", *, kind="card", tool="shell",
         h="h1", **kw) -> dict:
    p = {"kind": kind, "tool": tool, "surface": "chat", "thread_id": tid, "ask_id": ask_id,
         "args_hash": h, "decision": decision, "actor": actor, "wait_ms": 1000, "at": at, **kw}
    return {"type": "interrupt.answered", "payload": p}


def _avoidable(events: list, threads: list | None = None) -> int:
    t0 = NOW - 3600
    out = A.compute(threads=threads or [_thread("t", t0)], sessions=[], events=events, days=30, now=NOW)
    return out["interruptions"]["avoidable"]


def test_the_same_call_approved_twice_in_a_task_is_avoidable_once():
    t0 = NOW - 3600
    ev = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a"), _ask("t", t0 + 3, "b"), _ans("t", t0 + 4, "b")]
    assert _avoidable(ev) == 1
    other = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a"),
             _ask("t", t0 + 3, "b", h="h2"), _ans("t", t0 + 4, "b", h="h2")]
    assert _avoidable(other) == 0


def test_a_tool_allowed_for_the_chat_earlier_is_avoidable():
    t0 = NOW - 3600
    ev = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a", "conversation"),
          _ask("t", t0 + 3, "b", h="h2"), _ans("t", t0 + 4, "b", h="h2")]
    assert _avoidable(ev) == 1


def test_once_only_safety_cards_denials_grants_and_timeouts_are_never_avoidable():
    t0 = NOW - 3600
    once = [_ask("t", t0 + 1, "a", tool="git", once_only=True, reason="git"),
            _ans("t", t0 + 2, "a", tool="git", once_only=True, reason="git"),
            _ask("t", t0 + 3, "b", tool="git", once_only=True, reason="git"),
            _ans("t", t0 + 4, "b", tool="git", once_only=True, reason="git")]
    assert _avoidable(once) == 0
    deny = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a", "deny"),
            _ask("t", t0 + 3, "b"), _ans("t", t0 + 4, "b", "deny")]
    assert _avoidable(deny) == 0
    # The same call approved first and then refused: a "no" is never
    # avoidable, even for a call the person had allowed before.
    deny_after = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a", "conversation"),
                  _ask("t", t0 + 3, "b"), _ans("t", t0 + 4, "b", "deny")]
    assert _avoidable(deny_after) == 0
    grant = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a"),
             _ask("t", t0 + 3, "b"), _ans("t", t0 + 4, "b", actor="grant")]
    assert _avoidable(grant) == 0
    clock = [_ask("t", t0 + 1, "a"), _ans("t", t0 + 2, "a"),
             _ask("t", t0 + 3, "b"), _ans("t", t0 + 4, "b", "timeout", actor="timeout")]
    assert _avoidable(clock) == 0


def test_a_question_counts_only_when_handed_back():
    t0 = NOW - 3600
    q = dict(kind="question", tool="ask_user", h="")
    assert _avoidable([_ask("t", t0 + 1, "a", **q), _ans("t", t0 + 2, "a", "answered", **q)]) == 0
    assert _avoidable([_ask("t", t0 + 1, "a", **q), _ans("t", t0 + 2, "a", "answered", deferred=True, **q)]) == 1
    assert _avoidable([_ask("t", t0 + 1, "a", **q), _ans("t", t0 + 2, "a", "skipped", deferred=True, **q)]) == 1


def test_a_plan_approval_is_never_avoidable_untouched_edited_or_steered():
    """v1.402.0 integrate (moved on purpose): approving a plan IS the one
    "approve once" the user's goal asks for — never an avoidable interruption."""
    t0 = NOW - 3600
    plan = dict(kind="plan", tool="exit_plan_mode", h="", once_only=True)
    ev = [_ask("t", t0 + 10, "p", **plan), _ans("t", t0 + 20, "p", **plan)]
    assert _avoidable(ev) == 0
    assert _avoidable([_ask("t", t0 + 10, "p", **plan), _ans("t", t0 + 20, "p", edited=True, **plan)]) == 0
    steered = [
        {"role": "user", "content": "Plan the refactor", "at": t0},
        {"role": "user", "content": "use pathlib", "steer": True, "at": t0 + 15},
        {"role": "assistant", "content": "Planned.", "at": t0 + 500},
    ]
    assert _avoidable(ev, [_thread("t", t0, lines=steered)]) == 0


# --------------------------------------------------------------------------- #
# 5. the endpoint, over a seeded database, all three numbers EXACTLY
# --------------------------------------------------------------------------- #


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _naive(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _seed(engine) -> None:
    t0 = NOW - 5 * 86400
    t1 = NOW - 4 * 86400
    t2 = NOW - 3 * 86400

    def m(role, content, at, **kw):
        return {"role": role, "content": content, "at": _iso(at), **kw}

    chat_a = [  # task1 (nudge + a Continue press, ends clean) · task2 (interrupted)
        m("user", "Write a poem about the sea", t0),
        m("assistant", "The sea…", t0 + 10),
        m("user", "continue", t0 + 20),
        m("assistant", "…more sea.", t0 + 30, continuedPresses=1),
        m("user", "Summarize my notes", t0 + 100),
        m("assistant", "Here", t0 + 110, interrupted=True),
    ]
    chat_b = [  # task3 (a retry, cards, ends clean)
        m("user", "Rename the files", t1),
        m("assistant", "Done", t1 + 50),
        m("user", "Rename the files", t1 + 60),
        m("assistant", "Done again", t1 + 70),
    ]
    build = [  # task4 (a steer; tests failed then passed) · task5 (tests failed)
        m("user", "Fix the failing test", t2),
        m("user", "use pytest -x", t2 + 5, steer=True),
        m("assistant", "Fixed", t2 + 50, finish={"tests": "failed", "after": "passed"}),
        m("user", "Add a README", t2 + 100),
        m("user", "Carry on with the task above", t2 + 105, continuation=True),
        m("assistant", "Added", t2 + 150, finish={"tests": "failed"}),
    ]
    events = [
        # task3: a card, the SAME call again (avoidable), a git once-only, a deny.
        ("interrupt.asked", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "a1",
                             "args_hash": "h1", "surface": "chat", "at": t1 + 1}),
        ("interrupt.answered", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "a1",
                                "args_hash": "h1", "decision": "once", "actor": "app", "wait_ms": 4000,
                                "surface": "chat", "at": t1 + 5}),
        ("interrupt.asked", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "a2",
                             "args_hash": "h1", "surface": "chat", "at": t1 + 6}),
        ("interrupt.answered", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "a2",
                                "args_hash": "h1", "decision": "once", "actor": "phone", "wait_ms": 2000,
                                "surface": "chat", "at": t1 + 8}),
        ("interrupt.asked", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "a3",
                             "args_hash": "h9", "once_only": True, "reason": "git", "surface": "chat",
                             "at": t1 + 9}),
        ("interrupt.answered", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "a3",
                                "args_hash": "h9", "once_only": True, "reason": "git", "decision": "once",
                                "actor": "app", "wait_ms": 1000, "surface": "chat", "at": t1 + 10}),
        ("interrupt.asked", {"kind": "card", "tool": "web_fetch", "thread_id": "chat_b", "ask_id": "a4",
                             "args_hash": "h4", "surface": "chat", "at": t1 + 11}),
        ("interrupt.answered", {"kind": "card", "tool": "web_fetch", "thread_id": "chat_b", "ask_id": "a4",
                                "args_hash": "h4", "decision": "deny", "actor": "app", "wait_ms": 1000,
                                "surface": "chat", "at": t1 + 12}),
        # task5: a plan approved untouched (never avoidable — v1.402.0 integrate),
        # a question handed back (avoidable).
        ("interrupt.asked", {"kind": "plan", "tool": "exit_plan_mode", "thread_id": "build_c",
                             "ask_id": "p1", "once_only": True, "surface": "build", "at": t2 + 101}),
        ("interrupt.answered", {"kind": "plan", "tool": "exit_plan_mode", "thread_id": "build_c",
                                "ask_id": "p1", "once_only": True, "decision": "once", "actor": "app",
                                "wait_ms": 1000, "surface": "build", "at": t2 + 102}),
        ("interrupt.asked", {"kind": "question", "tool": "ask_user", "thread_id": "build_c",
                             "ask_id": "q1", "surface": "build", "at": t2 + 103}),
        ("interrupt.answered", {"kind": "question", "tool": "ask_user", "thread_id": "build_c",
                                "ask_id": "q1", "decision": "answered", "deferred": True, "actor": "app",
                                "wait_ms": 1000, "surface": "build", "at": t2 + 104}),
        # S2 (a goal's run): one card the person allowed for the run.
        ("interrupt.asked", {"kind": "card", "tool": "shell", "session_id": "session_s2", "ask_id": "r1",
                             "args_hash": "h1", "surface": "agent", "at": t2 + 301}),
        ("interrupt.answered", {"kind": "card", "tool": "shell", "session_id": "session_s2",
                                "ask_id": "r1", "args_hash": "h1", "decision": "conversation",
                                "actor": "app", "wait_ms": 1000, "surface": "agent", "at": t2 + 302}),
        # Never counted: the benchmark's, and an ask whose chat is gone.
        ("interrupt.asked", {"kind": "card", "tool": "shell", "thread_id": "chat_b", "ask_id": "x1",
                             "surface": "bench", "at": t1 + 20}),
        ("interrupt.asked", {"kind": "card", "tool": "shell", "thread_id": "gone", "ask_id": "x2",
                             "surface": "chat", "at": t1 + 21}),
        # The system's own nudges.
        ("autonomy.nudged", {"kind": "plan_run", "thread_id": "build_c", "at": t2 + 110}),
        ("autonomy.nudged", {"kind": "plan_run", "thread_id": "build_c", "at": t2 + 111}),
        ("autonomy.nudged", {"kind": "closing_question", "thread_id": "chat_a", "at": t0 + 11}),
    ]
    with session_scope(engine) as db:
        db.add(ChatThreadRecord(id="chat_a", messages_json=json.dumps(chat_a)))
        db.add(ChatThreadRecord(id="chat_b", messages_json=json.dumps(chat_b)))
        db.add(ChatThreadRecord(id="build_c", surface="build", messages_json=json.dumps(build)))
        for sid, origin, outcome, at, task, opts in (
            ("session_s1", "schedule:nightly", "completed", t2 + 200, "Tidy the inbox", {}),
            ("session_s2", "goal:g1", "needs_you", t2 + 300, "Grow the list", {}),
            ("session_s4", "assignment:a1", "failed", t2 + 400, "Write the brief", {}),
            ("session_s5", "assignment:a1", "completed", t2 + 500,
             "go on\n\n[Continuing an earlier session. Original task: Write the brief]", {}),
            # Never tasks of their own:
            ("session_x1", "chat", "completed", t2 + 600, "from a chat", {}),
            ("session_x2", "job:mission-member", "completed", t2 + 601, "a teammate", {}),
            ("session_x3", "schedule:nightly", "completed", t2 + 602, "a helper",
             {"build_session": "build:build_c"}),
            ("session_x4", "schedule:nightly", "completed", t2 + 603, "told a chat",
             {"chat_thread_id": "chat_a"}),
            ("session_old", "schedule:nightly", "completed", NOW - 60 * 86400, "long ago", {}),
        ):
            db.add(SessionRow(id=sid, origin=origin, outcome=outcome, task=task,
                              options_json=json.dumps(opts), created_at=_naive(at)))
        for t, p in events:
            db.add(EventRecord(id=new_uid("evt"), type=t, payload_json=json.dumps(p)))
        db.commit()


def test_the_endpoint_computes_all_three_numbers_exactly(tmp_path):
    app = create_app(str(tmp_path / "home"))
    _seed(app.state.platform.engine)
    got = TestClient(app).get("/autonomy/metrics?days=30").json()
    # 8 tasks: chat_a ×2, chat_b ×1, build_c ×2, S1, S2, S4(+S5).
    assert got["tasks"] == 8
    # task1, task3, task4 (tests passed after), S1, S4 (its follow-up completed).
    assert got["successful"] == 5
    # Only S1 had no help at all.
    assert got["hands_off"] == 1 and got["hands_off_pct"] == 12.5
    i = got["interruptions"]
    assert i["total"] == 7 and i["avoidable"] == 2
    assert i["per_task"] == 0.88 and i["avoidable_per_task"] == 0.25
    assert i["by_kind"] == {"card": 5, "question": 1, "plan": 1}
    assert i["by_tool"][0] == {"tool": "shell", "count": 4}
    assert i["unattributed"] == 1
    n = got["nudges"]
    # task1: "continue" + 1 press; task3: 1 retry; S4: 1 follow-up.
    assert n["human"] == 4 and n["retries"] == 1 and n["presses"] == 1 and n["steers"] == 1
    assert n["per_successful_task"] == 0.8
    assert n["system"] == {"total": 3, "by_kind": {"closing_question": 1, "plan_run": 2}}
    s = got["by_surface"]
    assert (s["chat"]["tasks"], s["chat"]["successful"], s["chat"]["interruptions"], s["chat"]["avoidable"],
            s["chat"]["human_nudges"]) == (3, 2, 4, 1, 3)
    assert (s["build"]["tasks"], s["build"]["successful"], s["build"]["interruptions"],
            s["build"]["avoidable"]) == (2, 1, 2, 1)
    assert (s["agent"]["tasks"], s["agent"]["successful"], s["agent"]["hands_off"],
            s["agent"]["interruptions"], s["agent"]["human_nudges"]) == (3, 2, 1, 1, 1)
    weekly = got["weekly"]
    # (review) every week the 30 days touch: 5, or 6 when the window began
    # late in a week (a Monday or a Tuesday "now").
    first = datetime.fromisoformat(A._week_start(NOW - 30 * 86400)).date()
    last = datetime.fromisoformat(A._week_start(NOW)).date()
    assert len(weekly) == (last - first).days // 7 + 1 and sum(w["tasks"] for w in weekly) == 8
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", w["week"]) for w in weekly)


def test_no_data_answers_zeros_and_a_failed_read_never_500s(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    got = client.get("/autonomy/metrics").json()
    assert got["tasks"] == 0 and got["hands_off_pct"] == 0.0
    assert got["interruptions"]["per_task"] == 0.0 and got["nudges"]["per_successful_task"] == 0.0
    assert "error" not in got

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(A, "read_metrics", boom)
    r = client.get("/autonomy/metrics?days=7")
    assert r.status_code == 200 and r.json()["tasks"] == 0 and r.json()["days"] == 7
    assert r.json()["error"] and "fire" not in r.json()["error"]


def test_a_task_succeeds_only_when_its_last_reply_ended_cleanly():
    ok = {"role": "assistant", "content": "x"}
    assert A.reply_succeeded(ok)
    for bad in ({"error": "boom"}, {"stepLimit": {"limit": 60}}, {"capMet": {"kind": "time"}},
                {"interrupted": True}, {"truncated": True}, {"refused": True}, {"accountLimit": {"kind": "limit"}},
                {"finish": {"tests": "failed"}}, {"agentRun": {"status": "failed"}},
                {"panelKind": "progress"}):
        assert not A.reply_succeeded({**ok, **bad}), bad
    assert A.reply_succeeded({**ok, "finish": {"tests": "failed", "after": "passed"}})
    assert not A.reply_succeeded(None)


# --------------------------------------------------------------------------- #
# 6. the bench: what it answered, and how often it WOULD HAVE asked
# --------------------------------------------------------------------------- #


def test_would_card_agrees_with_the_permission_engine_and_the_doors():
    from iron_jarvis.core.config import default_permissions
    from iron_jarvis.tools.permissions import PermissionEngine

    perms = PermissionEngine(default_permissions())
    assert A.would_card("shell", permissions=perms) is True
    assert A.would_card("read_file", permissions=perms) is False
    assert A.would_card("exit_plan_mode", permissions=perms) is True
    assert A.would_card("delegate", permissions=perms) is True
    assert A.would_card("delegate", permissions=perms, harness=True) is False
    assert A.would_card("", permissions=perms) is None
    assert A.would_card("shell", permissions=PermissionEngine({"shell": "deny"})) is False


async def test_would_card_matches_the_real_stream_lane(app, work):
    """The bench's replay and the lane decide alike: the same Build turn under
    the default posture cards exactly the calls ``would_card`` says."""
    sid = new_session(app, work)["id"]
    calls = [
        ToolCall(id="r1", name="Read", arguments={"file_path": str(work / "app.py")}),
        ToolCall(id="w1", name="Write", arguments={"file_path": str(work / "new.py"), "content": "y = 2\n"}),
        ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"}),
    ]
    wire(app, Script([calls[:1], calls[1:2], calls[2:], "ok"]))
    drop, task = await _start(app, sid, "go", "am-agree")
    card = await _card(app, "am-agree")
    await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "once"})
    await _finish(app, "am-agree", drop, task)
    carded = {d["tool"] for e, d in turn_frames(app, "am-agree") if e == "approval"}
    ran = {d["name"] for e, d in turn_frames(app, "am-agree") if e == "tool_call"}
    assert ran == {"read_file", "write_file", "shell"}
    p = app.state.platform
    for name in ran:
        verdict = A.would_card(name, permissions=p.permissions, registry=p.registry, harness=True)
        assert verdict is (name in carded), name


def test_each_runners_calls_are_replayed_and_an_unmappable_one_is_not_known(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    jarvis = [("shell", {}), ("read_file", {}), ("exit_plan_mode", {}), ("delegate", {}), ("made_up", {})]
    got = B.would_have_asked(jarvis, runner="jarvis", permissions=p.permissions, registry=p.registry)
    assert got == {"calls": 5, "asked": 2, "not_known": 0, "by_tool": {"shell": 1, "exit_plan_mode": 1}}
    claude = [("Bash", {"command": "x"}), ("Read", {}), ("Write", {}),
              ("Bash", {"command": "y", "run_in_background": True}), ("mcp__gh__issues", {}), ("Agent", {})]
    got = B.would_have_asked(claude, runner="claude")
    assert got["asked"] == 2 and got["not_known"] == 1
    assert got["by_tool"] == {"shell": 1, "process_start": 1}
    codex = [("command_execution", None), ("file_change", None), ("web_search", None), ("mcp_tool_call", None)]
    got = B.would_have_asked(codex, runner="codex")
    assert got == {"calls": 4, "asked": 1, "not_known": 1, "by_tool": {"shell": 1}}


def test_the_cli_parsers_keep_names_never_long_inputs():
    from iron_jarvis.harness_bench.runners import parse_claude_stream, parse_codex_events

    claude = parse_claude_stream(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Write",
         "input": {"file_path": "a.py", "content": "x" * 5000}},
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {}},  # the same id once
    ]}}))
    # (review) each call keeps the model message it came in (one round).
    assert claude["tool_uses"] == [{"name": "Write", "input": {"file_path": "a.py"}, "round": 0}]
    codex = parse_codex_events("\n".join(json.dumps(x) for x in (
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}},
    )))
    assert codex["tool_items"] == ["command_execution"]


async def test_a_bench_run_records_the_cards_it_answered_and_what_it_would_have_asked(tmp_path, monkeypatch):
    from iron_jarvis.daemon import chat_stream
    from iron_jarvis.daemon.routes.chat import _approvals, _sse
    from iron_jarvis.harness_bench.bench import run_bench
    from iron_jarvis.harness_bench.catalog import load_tasks
    from iron_jarvis.harness_bench.report import config_report, summarize
    from iron_jarvis.harness_bench.runners import JarvisRunner
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path / "root"))
    decided: list[str] = []

    async def fake_turn(platform_, personas, body, **kw):
        async def frames():
            yield _sse("tool_call", {"id": "c1", "name": "read_file", "status": "started", "args": {}})
            yield _sse("tool_call", {"id": "c1", "name": "read_file", "status": "finished", "ok": True})
            yield _sse("tool_call", {"id": "c2", "name": "shell", "status": "started", "args": {}})
            approval_id, fut = _approvals(SimpleNamespace(platform=platform_)).request("shell", {})
            yield _sse("approval", {"id": approval_id, "tool": "shell", "args": {}})
            decided.append(await asyncio.wait_for(fut, timeout=10))
            decided.append(_approvals(SimpleNamespace(platform=platform_)).actor_of(approval_id))
            yield _sse("tool_call", {"id": "c2", "name": "shell", "status": "finished", "ok": True})
            yield _sse("done", {"reply": "ok", "provider": "scripted", "model": "s", "usage": {
                "input_tokens": 0, "output_tokens": 0}, "route": {"reason": "explicit"}})
        return frames()

    monkeypatch.setattr(chat_stream, "stream_chat_turn", fake_turn)
    runner = JarvisRunner(platform, provider="scripted", model="s")
    res = await run_bench(runner=runner, home=platform.config.home, tasks=load_tasks(), names=["bugfix-leap-year"])
    rec = res.records[0]
    assert decided == ["once", "bench"]
    assert rec["facts"]["asks"] == {"cards": 1, "questions": 0, "by_tool": {"shell": 1}}
    assert rec["facts"]["would_have_asked"] == {"calls": 2, "asked": 1, "not_known": 0, "by_tool": {"shell": 1}}
    s = summarize([rec])
    assert s["would_ask_per_task"] == 1.0 and s["auto_answered"] == 1
    assert "Would have asked you 1.0 times per task" in config_report({"runner": "jarvis"}, [rec])
