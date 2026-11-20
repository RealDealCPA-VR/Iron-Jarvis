"""Idea 39, phase 39-a: every source that is set up from a chat reports there.

Through the real app (``create_app``), its tools, its scheduler and its
orchestrator; the model is the demo model the test app runs on, and the clock
for the report's day is injected (``chat_delivery._now``):

* a schedule made by a chat sentence (``schedule_create`` with the chat's id
  on the tool call) keeps the chat, fires twice on two days and leaves exactly
  two reports in that chat, each the run's own line (one per run);
* two fires on ONE day leave one line that holds both (master plan R1.1);
* a schedule made on the Schedules page reports nowhere;
* a fire refused before any run is reported in words;
* a goal made from the chat's goal card reports each try once, after its
  checks; its run writes nothing itself;
* a workflow run from chat reports once when it ends;
* a watcher made in chat reports what it noticed, at most once an hour, the
  rest joining the next report (D-39.3);
* a night task queued from chat reports how it ended;
* ``schedule_update`` / ``sentinel_update`` take ``report_here`` with Undo,
  and ``POST /reports/target`` is Stop reporting.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import chat_delivery as cd
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling.tools import ScheduleCreateTool
from iron_jarvis.tools.base import ToolContext

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

FRIDAY = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
PRICES = "check this supplier's prices and say what changed"


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    yield c, p
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _chat(p, title="Supplier prices", **cols) -> str:
    rec = ChatThreadRecord(title=title, messages_json=json.dumps(
        [{"role": "user", "content": "Check this supplier's prices every Friday"}]), **cols)
    with session_scope(p.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _messages(p, tid) -> list[dict]:
    with session_scope(p.engine) as db:
        return json.loads(db.get(ChatThreadRecord, tid).messages_json or "[]")


def _reports(p, tid) -> list[dict]:
    return [m for m in _messages(p, tid) if m.get("reportFrom")]


def _ctx(p, tmp_path, thread_id: str) -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                       config=p.config, event_bus=p.event_bus, engine=p.engine, thread_id=thread_id)


def _make_schedule(p, tmp_path, chat, name="Friday prices"):
    tool = ScheduleCreateTool(p)
    r = asyncio.run(tool.execute({"name": name, "cron": "0 9 * * 5", "task": PRICES},
                                 _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    return r


def test_a_chat_made_schedule_fires_twice_and_leaves_two_reports(desk, tmp_path, monkeypatch):
    c, p = desk
    chat = _chat(p)
    r = _make_schedule(p, tmp_path, chat)
    assert r.data["reports_here"] is True
    assert "reports each result in this chat" in r.output
    rec = p.scheduler.get("Friday prices")
    assert rec.chat_thread_id == chat
    days = iter([FRIDAY, FRIDAY + timedelta(days=7)])
    for _ in range(2):
        day = next(days)
        monkeypatch.setattr(cd, "_now", lambda day=day: day)
        out = c.post("/schedules/Friday prices/run")
        assert out.status_code == 200, out.text
    reports = _reports(p, chat)
    assert len(reports) == 2, [m.get("content") for m in _messages(p, chat)]
    sids = {m["sessionId"] for m in reports}
    assert len(sids) == 2 and all(sids), "each report is that run's own line"
    for m in reports:
        assert m["reportFrom"]["kind"] == "schedule"
        assert m["reportFrom"]["name"] == "Friday prices"
        assert m["reportFrom"]["id"] == rec.id
        assert m["content"].startswith("From your schedule \u201cFriday prices\u201d.")
        assert m["reportFrom"]["outcome"], "the ledger's verdict word"
        assert "panelWho" not in m
    # The schedules list says where it reports.
    rows = {r["name"]: r for r in c.get("/schedules").json()["schedules"]}
    assert rows["Friday prices"]["reports_to"] == {"thread_id": chat, "title": "Supplier prices"}


def test_two_fires_on_one_day_are_one_line(desk, tmp_path, monkeypatch):
    c, p = desk
    chat = _chat(p)
    _make_schedule(p, tmp_path, chat)
    monkeypatch.setattr(cd, "_now", lambda: FRIDAY)
    for _ in range(2):
        assert c.post("/schedules/Friday prices/run").status_code == 200
    reports = _reports(p, chat)
    assert len(reports) == 1 and reports[0]["reportCount"] == 2


def test_a_page_made_schedule_reports_nowhere(desk):
    c, p = desk
    chat = _chat(p)
    made = c.post("/schedules", json={"name": "Page prices", "cron": "0 9 * * 5", "kind": "task",
                                      "payload": {"task": PRICES}})
    assert made.status_code in (200, 201), made.text
    assert (p.scheduler.get("Page prices").chat_thread_id or "") == ""
    assert c.post("/schedules/Page prices/run").status_code == 200
    assert _reports(p, chat) == []
    from sqlmodel import select

    with session_scope(p.engine) as db:
        rows = db.exec(select(ChatThreadRecord)).all()
        assert all(not any(m.get("reportFrom") for m in json.loads(r.messages_json or "[]")) for r in rows)


def test_a_refused_fire_is_reported(desk, tmp_path):
    c, p = desk
    chat = _chat(p)
    _make_schedule(p, tmp_path, chat)
    p.scheduler.update_task("Friday prices", payload={})  # its task was taken away
    assert c.post("/schedules/Friday prices/run").status_code == 200
    reports = _reports(p, chat)
    assert len(reports) == 1
    assert reports[0]["reportFrom"]["outcome"] == "Failed"
    assert "no task to run" in reports[0]["content"]
    assert "sessionId" not in reports[0], "no run was made"


def test_schedule_update_reports_here_and_stops_with_undo(desk, tmp_path):
    from iron_jarvis.settings.records import ScheduleUpdateTool, restore_schedule, snap_schedule

    c, p = desk
    chat = _chat(p)
    _make_schedule(p, tmp_path, chat)
    before = snap_schedule(p, "Friday prices")
    tool = ScheduleUpdateTool(p)
    r = asyncio.run(tool.execute({"name": "Friday prices", "report_here": False}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.scheduler.get("Friday prices").chat_thread_id == ""
    # The Undo puts it back.
    restore_schedule(p, "Friday prices", before)
    assert p.scheduler.get("Friday prices").chat_thread_id == chat
    # "Report to this chat" from an unsaved chat changes nothing, in words.
    r = asyncio.run(tool.execute({"name": "Friday prices", "report_here": True}, _ctx(p, tmp_path, "")))
    assert not r.ok and "saved chat" in r.error
    # The row's Stop reporting, and its Undo.
    rec = p.scheduler.get("Friday prices")
    stop = c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": ""})
    assert stop.status_code == 200 and stop.json()["previous"] == chat
    assert p.scheduler.get("Friday prices").chat_thread_id == ""
    back = c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": chat})
    assert back.status_code == 200 and p.scheduler.get("Friday prices").chat_thread_id == chat
    bad = c.post("/reports/target", json={"kind": "run", "id": rec.id, "thread_id": chat})
    assert bad.status_code == 400


def test_a_goal_from_the_chat_card_reports_each_try_once(desk):
    c, p = desk
    chat = _chat(p, title="Inbox")
    made = c.post("/goals", json={"name": "Inbox zero", "contract_text": "Reply to every email",
                                  "budget": {"max_dollars": 1}, "verifier": {"kind": "manual"},
                                  "chat_thread_id": chat})
    assert made.status_code == 200, made.text
    goal = made.json()["goal"]
    assert goal["chat_thread_id"] == chat
    assert goal["reports_to"] == {"thread_id": chat, "title": "Inbox"}
    tried = c.post(f"/goals/{goal['id']}/run").json()
    assert tried.get("session_id"), tried
    reports = _reports(p, chat)
    assert len(reports) == 1, [m.get("content") for m in _messages(p, chat)]
    assert reports[0]["reportFrom"]["kind"] == "goal"
    assert reports[0]["reportFrom"]["outcome"] in ("Satisfied", "Not yet", "Failed")
    assert reports[0]["sessionId"] == tried["session_id"]
    # A goal made with no chat (its page) reports nowhere.
    plain = c.post("/goals", json={"name": "Quiet", "contract_text": "x", "budget": {"max_dollars": 1}})
    assert plain.json()["goal"]["chat_thread_id"] == ""
    # A coding session is never stamped.
    code = _chat(p, surface="build")
    coded = c.post("/goals", json={"name": "Code", "contract_text": "y", "budget": {"max_dollars": 1},
                                   "chat_thread_id": code})
    assert coded.json()["goal"]["chat_thread_id"] == ""


def test_a_workflow_run_from_chat_reports_once_when_it_ends(desk, tmp_path):
    from iron_jarvis.workflows.engine import WorkflowEngine
    from iron_jarvis.workflows.store import WorkflowStore
    from iron_jarvis.workflows.tools import WorkflowRunTool

    c, p = desk
    chat = _chat(p, title="Weekly")
    WorkflowStore(p.engine).save("Weekly tidy", [{"name": "Tidy", "agent": "builder", "task": "tidy up"}], "")
    launched: list = []
    tasks: list = []

    class _Tool(WorkflowRunTool):
        def _launch(self, coro, run_id):  # run it here; the test awaits its end
            launched.append(run_id)
            tasks.append(asyncio.get_running_loop().create_task(coro))
            return tasks[-1]

    async def go():
        r = await _Tool(p).execute({"name": "Weekly tidy"}, _ctx(p, tmp_path, chat))
        assert r.ok, r.error
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert launched
    reports = _reports(p, chat)
    assert len(reports) == 1
    assert reports[0]["reportFrom"]["kind"] == "workflow"
    assert reports[0]["reportFrom"]["id"] == launched[0]
    # A second end of the same run (a resume racing the driver) writes nothing more.
    asyncio.run(WorkflowEngine(p, p.orchestrator)._report_end(launched[0], "Weekly tidy", "completed"))
    assert len(_reports(p, chat)) == 1


def test_a_watcher_reports_at_most_once_an_hour_and_the_rest_join(desk, tmp_path):
    """Through the watcher's own sweep (``poll_once``), with a scanner and a
    clock injected (review: the seam that calls the report is pinned too)."""
    c, p = desk
    chat = _chat(p, title="Intake")
    folder = tmp_path / "intake"
    folder.mkdir()
    svc = p.sentinels
    svc.add("Intake", path=str(folder), task="sort them by sender", chat_thread_id=chat)
    clock = {"now": FRIDAY}
    svc.clock = lambda: clock["now"]
    seen: dict[str, float] = {}

    def scan(path, glob=None):
        return dict(seen)

    assert svc.poll_once(p.intent, scanner=scan) == []  # the baseline
    assert _reports(p, chat) == []
    seen[str(folder / "a.pdf")] = 1.0
    made = svc.poll_once(p.intent, scanner=scan)
    assert len(made) == 1, "the suggestion it leaves"
    reports = _reports(p, chat)
    assert len(reports) == 1
    assert "1 new or changed file in Intake: a.pdf." in reports[0]["content"]
    assert ("Suggested: sort them by sender. It waits for your approval on the Autonomy page."
            in reports[0]["content"])
    seen[str(folder / "b.pdf")] = 2.0
    seen[str(folder / "c.pdf")] = 2.0
    svc.poll_once(p.intent, scanner=scan)
    assert len(_reports(p, chat)) == 1, "at most one report an hour"
    clock["now"] = FRIDAY + timedelta(minutes=61)
    svc.poll_once(p.intent, scanner=scan)  # nothing new: the waiting ones go out
    reports = _reports(p, chat)
    assert len(reports) == 2
    assert "2 new or changed files in Intake: b.pdf, c.pdf." in reports[1]["content"]
    assert reports[1]["reportFrom"]["kind"] == "watcher"
    # Review: a held-back report never says a suggestion waits (it may be
    # settled by now); only a report of this sweep's own suggestion does.
    assert "waits for your approval" not in reports[1]["content"]
    # Nothing waiting, nothing written.
    clock["now"] = FRIDAY + timedelta(hours=5)
    svc.poll_once(p.intent, scanner=scan)
    assert len(_reports(p, chat)) == 2
    rows = {s["name"]: s for s in c.get("/sentinels").json()["sentinels"]}
    assert rows["Intake"]["reports_to"] == {"thread_id": chat, "title": "Intake"}


def test_a_watchers_report_says_no_suggestion_when_none_was_made(desk, tmp_path):
    c, p = desk
    chat = _chat(p, title="Intake")
    folder = tmp_path / "in2"
    folder.mkdir()
    rec = p.sentinels.add("In2", path=str(folder), task="sort them", chat_thread_id=chat)
    p.sentinels.clock = lambda: FRIDAY
    p.sentinels.report_to_chat(rec, [{"path": str(folder / "a.pdf")}], suggested=False)
    (only,) = _reports(p, chat)
    assert "Suggested" not in only["content"] and "approval" not in only["content"]


def test_a_watcher_made_in_chat_keeps_the_chat_and_sentinel_update_stops_it(desk, tmp_path):
    from iron_jarvis.sentinels.tools import SentinelAddTool
    from iron_jarvis.settings.records import SentinelUpdateTool, restore_sentinel, snap_sentinel

    c, p = desk
    chat = _chat(p, title="Intake")
    folder = tmp_path / "in"
    folder.mkdir()
    r = asyncio.run(SentinelAddTool(p).execute({"name": "In", "path": str(folder)}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.sentinels.get("In").chat_thread_id == chat
    before = snap_sentinel(p, "In")
    r = asyncio.run(SentinelUpdateTool(p).execute({"name": "In", "report_here": False}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.sentinels.get("In").chat_thread_id == ""
    restore_sentinel(p, "In", before)
    assert p.sentinels.get("In").chat_thread_id == chat


def test_a_night_task_queued_from_chat_reports_how_it_ended(desk, tmp_path):
    """Through ``night_task_add`` with the chat on the call (review: the seam
    that stamps the chat is pinned too), then the task's own ending."""
    from sqlalchemy import update

    from iron_jarvis.harness import night as N
    from iron_jarvis.settings.records_build import NightTaskAddTool
    from tests.test_build_git_v1375 import make_repo

    c, p = desk
    chat = _chat(p, title="Night")
    repo = make_repo(tmp_path / "app")
    r = asyncio.run(NightTaskAddTool(p).execute({"words": "fix the flaky test", "folder": str(repo)},
                                                _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    tid = r.data["task_id"]
    with session_scope(p.engine) as db:
        assert db.get(N.NightTaskRecord, tid).chat_thread_id == chat
    with session_scope(p.engine) as db:
        db.exec(update(N.NightTaskRecord).where(N.NightTaskRecord.id == tid)
                .values(state=N.RUNNING, branch="ij/fix-flaky"))
        db.commit()
    assert N.finish_task(p.engine, tid, N.DONE, event_bus=p.event_bus) is True
    reports = _reports(p, chat)
    assert len(reports) == 1
    assert reports[0]["reportFrom"]["kind"] == "night"
    assert reports[0]["reportFrom"]["outcome"] == "Done"
    assert "ij/fix-flaky" in reports[0]["content"]
    # Ending it again writes nothing more (it is no longer running).
    assert N.finish_task(p.engine, tid, N.DONE) is False
    assert len(_reports(p, chat)) == 1
    # Queued with no chat on the call, it reports nowhere.
    plain = asyncio.run(NightTaskAddTool(p).execute({"words": "tidy", "folder": str(repo)},
                                                    _ctx(p, tmp_path, "")))
    assert plain.ok, plain.error
    with session_scope(p.engine) as db:
        assert (db.get(N.NightTaskRecord, plain.data["task_id"]).chat_thread_id or "") == ""


# --------------------------------------------------------------------------- #
# Review (39-a, round 2)
# --------------------------------------------------------------------------- #


def test_a_schedule_made_from_a_phone_conversation_promises_no_reports(desk, tmp_path):
    """Nothing can report to a phone thread until 39-c: the tool, the card and
    the row must not say it reports there."""
    c, p = desk
    phone = _chat(p, title="Slack DM", owner="daemon")
    r = _make_schedule(p, tmp_path, phone, name="Phone prices")
    assert r.data["reports_here"] is False
    assert "reports each result in this chat" not in r.output
    assert (p.scheduler.get("Phone prices").chat_thread_id or "") == ""
    rows = {r["name"]: r for r in c.get("/schedules").json()["schedules"]}
    assert rows["Phone prices"]["reports_to"] is None


def test_schedule_update_with_a_bad_cron_changes_nothing_at_all(desk, tmp_path):
    from iron_jarvis.settings.records import ScheduleUpdateTool

    c, p = desk
    chat = _chat(p)
    _make_schedule(p, tmp_path, chat, name="Prices")
    r = asyncio.run(ScheduleUpdateTool(p).execute(
        {"name": "Prices", "report_here": False, "cron": "not a cron at all"}, _ctx(p, tmp_path, chat)))
    assert not r.ok
    assert p.scheduler.get("Prices").chat_thread_id == chat, "the answer says it failed: nothing moved"
    assert p.scheduler.get("Prices").cron == "0 9 * * 5"
    # A good call changes both.
    r = asyncio.run(ScheduleUpdateTool(p).execute(
        {"name": "Prices", "report_here": False, "cron": "0 10 * * 5"}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.scheduler.get("Prices").chat_thread_id == ""
    assert p.scheduler.get("Prices").cron == "0 10 * * 5"


def _goal(c, chat, name="Inbox zero"):
    made = c.post("/goals", json={"name": name, "contract_text": "Reply to every email",
                                  "budget": {"max_dollars": 1}, "verifier": {"kind": "manual"},
                                  "chat_thread_id": chat})
    assert made.status_code == 200, made.text
    return made.json()["goal"]


def test_goal_update_report_here_from_chat_with_undo(desk, tmp_path):
    """Review (G9): a goal from chat can stop reporting, and be pointed back,
    from chat, with the usual card and Undo."""
    from iron_jarvis.goals.models import GoalContractRecord
    from iron_jarvis.settings.records import GoalUpdateTool
    from iron_jarvis.settings.records import restore as restore_record

    c, p = desk
    chat = _chat(p, title="Inbox")
    goal = _goal(c, chat)
    tool = GoalUpdateTool(p)
    ctx = _ctx(p, tmp_path, chat)

    def _row():
        with session_scope(p.engine) as db:
            row = db.get(GoalContractRecord, goal["id"])
            return row.state, row.chat_thread_id

    args = {"goal": goal["id"], "report_here": False}
    undo = asyncio.run(tool.capture_undo(args, ctx))
    r = asyncio.run(tool.execute(args, ctx))
    assert r.ok, r.error
    assert "stopped reporting" in r.output
    assert r.data["record_change"]["old"].endswith("reports to a chat")
    assert _row() == ("active", "")
    # The Undo puts the chat back.
    restore_record(p, json.loads(undo["pre_inline"]))
    assert _row() == ("active", chat)
    # "Report to this chat" from a chat that cannot take reports changes nothing.
    phone = _chat(p, title="Slack DM", owner="daemon")
    r = asyncio.run(tool.execute({"goal": goal["id"], "report_here": True, "status": "paused"},
                                 _ctx(p, tmp_path, phone)))
    assert not r.ok and "saved chat" in r.error
    assert _row() == ("active", chat), "nothing changed"
    # An Undo of a pause alone never re-points reports stopped since.
    pause = {"goal": goal["id"], "status": "paused"}
    pause_undo = asyncio.run(tool.capture_undo(pause, ctx))
    assert asyncio.run(tool.execute(pause, ctx)).ok
    assert asyncio.run(tool.execute({"goal": goal["id"], "report_here": False}, ctx)).ok
    restore_record(p, json.loads(pause_undo["pre_inline"]))
    assert _row() == ("active", "")
    # A standing goal does not report to a chat: said in words.
    standing = p.intent.add_goal("Keep the books tidy")
    std = asyncio.run(tool.execute({"goal": standing.id, "report_here": True}, ctx))
    assert not std.ok and "Only a goal from chat reports to a chat" in std.error


def test_a_refused_goal_try_is_reported_once_a_day(desk):
    from iron_jarvis.goals.models import GoalContractRecord

    c, p = desk
    chat = _chat(p, title="Inbox")
    goal = _goal(c, chat)
    with session_scope(p.engine) as db:
        row = db.get(GoalContractRecord, goal["id"])
        row.spent_json = json.dumps({"tokens": 0, "dollars": 1, "wallclock_s": 0, "iterations": 1})
        db.add(row)
        db.commit()
    for _ in range(2):
        out = c.post(f"/goals/{goal['id']}/run").json()
        assert out.get("refused") is True, out
    reports = _reports(p, chat)
    assert len(reports) == 1, [m.get("content") for m in reports]
    assert reports[0]["reportFrom"]["outcome"] == "Needs you"
    # Plain words in the chat; the engine's own reason stays on the event.
    from iron_jarvis.goals import engine as GE

    assert GE.REFUSED_BUDGET_WORDS in reports[0]["content"]
    assert "max_dollars" not in reports[0]["content"]
    assert GE.refused_try_words("goal is tripped (circuit breaker: x) — reopen it") == GE.REFUSED_TRIPPED_WORDS
    assert GE.refused_try_words("allowed_grants are not runnable: shell") == GE.REFUSED_TOOLS_WORDS
    assert GE.refused_try_words("something new") == "It did not try: something new."


def test_stop_reporting_during_a_goal_try_holds_its_report(desk, monkeypatch):
    from iron_jarvis.goals import engine as GE

    c, p = desk
    chat = _chat(p, title="Inbox")
    goal = _goal(c, chat)
    real = GE.GoalEngine._iterate

    async def _iterate(self, g, iteration):
        out = await real(self, g, iteration)
        cd.set_report_target(p.engine, "goal", g.id, "")  # pressed while it ran
        return out

    monkeypatch.setattr(GE.GoalEngine, "_iterate", _iterate)
    tried = c.post(f"/goals/{goal['id']}/run").json()
    assert tried.get("session_id"), tried
    assert _reports(p, chat) == []


def test_sentinel_add_says_watchers_are_off_and_the_one_step(desk, tmp_path):
    from iron_jarvis.sentinels import tools as ST

    c, p = desk
    chat = _chat(p, title="Intake")
    folder = tmp_path / "w"
    folder.mkdir()
    assert p.config.sentinels_enabled is False
    r = asyncio.run(ST.SentinelAddTool(p).execute({"name": "W", "path": str(folder)}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert r.data["reports_here"] is True and r.data["watching"] is False
    assert ST.WATCHERS_OFF_WORDS in r.output and "Turn watchers on" in r.output
    p.config.sentinels_enabled = True
    r = asyncio.run(ST.SentinelAddTool(p).execute({"name": "W2", "path": str(folder)}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert ST.REPORTS_HERE_WATCHER in r.output and ST.WATCHERS_OFF_WORDS not in r.output


def test_a_workflow_run_from_chat_names_its_chat_on_its_ending(desk, tmp_path):
    """Review: ``workflow.completed`` carries the chat, so the bell and the
    tray open it (an ordinary run's payload is as before)."""
    from iron_jarvis.workflows.store import WorkflowStore
    from iron_jarvis.workflows.tools import WorkflowRunTool

    c, p = desk
    chat = _chat(p, title="Weekly")
    WorkflowStore(p.engine).save("Tidy", [{"name": "Tidy", "agent": "builder", "task": "tidy up"}], "")
    seen: list[dict] = []
    real = p.event_bus.publish

    async def publish(etype, payload=None, **kw):
        if str(etype) == "workflow.completed":
            seen.append(dict(payload or {}))
        return await real(etype, payload, **kw)

    p.event_bus.publish = publish
    tasks: list = []

    class _Tool(WorkflowRunTool):
        def _launch(self, coro, run_id):
            tasks.append(asyncio.get_running_loop().create_task(coro))
            return tasks[-1]

    async def go(thread):
        r = await _Tool(p).execute({"name": "Tidy"}, _ctx(p, tmp_path, thread))
        assert r.ok, r.error
        await asyncio.gather(*tasks)
        tasks.clear()

    asyncio.run(go(chat))
    asyncio.run(go(""))
    assert [e.get("chat_thread_id") for e in seen] == [chat, None]
    assert "chat_thread_id" not in seen[1]
