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
    from iron_jarvis.settings.records import ScheduleUpdateTool
    from iron_jarvis.settings.records import restore as restore_record

    c, p = desk
    chat = _chat(p)
    _make_schedule(p, tmp_path, chat)
    tool = ScheduleUpdateTool(p)
    stop = {"name": "Friday prices", "report_here": False}
    undo = asyncio.run(tool.capture_undo(stop, _ctx(p, tmp_path, chat)))
    r = asyncio.run(tool.execute(stop, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.scheduler.get("Friday prices").chat_thread_id == ""
    # The Undo puts it back.
    restore_record(p, json.loads(undo["pre_inline"]))
    assert p.scheduler.get("Friday prices").chat_thread_id == chat
    # Review (round 3): the Undo of a pause alone never re-points reports the
    # user stopped since (the goal's rule).
    pause = {"name": "Friday prices", "enabled": False}
    pause_undo = asyncio.run(tool.capture_undo(pause, _ctx(p, tmp_path, chat)))
    assert asyncio.run(tool.execute(pause, _ctx(p, tmp_path, chat))).ok
    rec = p.scheduler.get("Friday prices")
    assert c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": ""}).status_code == 200
    restore_record(p, json.loads(pause_undo["pre_inline"]))
    assert p.scheduler.get("Friday prices").enabled is True, "the pause is undone"
    assert p.scheduler.get("Friday prices").chat_thread_id == "", "the Stop reporting stands"
    # The Undo of a delete brings it back reporting where it did.
    assert c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": chat}).status_code == 200
    from iron_jarvis.settings.records import ScheduleDeleteTool

    gone = ScheduleDeleteTool(p)
    del_undo = asyncio.run(gone.capture_undo({"name": "Friday prices"}, _ctx(p, tmp_path, chat)))
    assert asyncio.run(gone.execute({"name": "Friday prices"}, _ctx(p, tmp_path, chat))).ok
    assert p.scheduler.get("Friday prices") is None
    restore_record(p, json.loads(del_undo["pre_inline"]))
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
    assert "receipt" not in reports[0], "only a step at low trust marks it"
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
    from iron_jarvis.settings.records import SentinelUpdateTool
    from iron_jarvis.settings.records import restore as restore_record

    c, p = desk
    chat = _chat(p, title="Intake")
    folder = tmp_path / "in"
    folder.mkdir()
    r = asyncio.run(SentinelAddTool(p).execute({"name": "In", "path": str(folder)}, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.sentinels.get("In").chat_thread_id == chat
    tool = SentinelUpdateTool(p)
    stop = {"name": "In", "report_here": False}
    undo = asyncio.run(tool.capture_undo(stop, _ctx(p, tmp_path, chat)))
    r = asyncio.run(tool.execute(stop, _ctx(p, tmp_path, chat)))
    assert r.ok, r.error
    assert p.sentinels.get("In").chat_thread_id == ""
    restore_record(p, json.loads(undo["pre_inline"]))
    assert p.sentinels.get("In").chat_thread_id == chat
    # Review (round 3): the Undo of a pause alone keeps a Stop reporting
    # pressed since.
    pause = {"name": "In", "enabled": False}
    pause_undo = asyncio.run(tool.capture_undo(pause, _ctx(p, tmp_path, chat)))
    assert asyncio.run(tool.execute(pause, _ctx(p, tmp_path, chat))).ok
    rid = p.sentinels.get("In").id
    assert c.post("/reports/target", json={"kind": "watcher", "id": rid, "thread_id": ""}).status_code == 200
    restore_record(p, json.loads(pause_undo["pre_inline"]))
    assert p.sentinels.get("In").enabled is True
    assert p.sentinels.get("In").chat_thread_id == ""


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
    # Review (round 3): the settle keeps the morning facts BEFORE the task
    # ends (routes/night.py), and the report carries its checks.
    N.set_morning(p.engine, tid, {"tests": "passed", "tests_trusted": True, "files": 3, "local_only": False})
    assert N.finish_task(p.engine, tid, N.DONE, event_bus=p.event_bus) is True
    reports = _reports(p, chat)
    assert len(reports) == 1
    assert reports[0]["reportFrom"]["kind"] == "night"
    assert reports[0]["reportFrom"]["outcome"] == "Done"
    assert "ij/fix-flaky" in reports[0]["content"]
    assert "Its tests passed." in reports[0]["content"]
    assert "3 files changed." in reports[0]["content"]
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


# --------------------------------------------------------------------------- #
# Review (39-a, round 3): a goal's and a workflow's report carry low trust,
# and a run the user stopped writes nothing
# --------------------------------------------------------------------------- #


def _runs_end_at_low_trust(p, monkeypatch) -> None:
    """Every run ends at LOW trust, as a run that read a flagged web page does
    (the real run first; then its row is marked, as the runtime marks it)."""
    from iron_jarvis.core.models import Session

    real = p.orchestrator.run_session

    async def run_session(session_id, *a, **kw):
        out = await real(session_id, *a, **kw)
        with session_scope(p.engine) as db:
            row = db.get(Session, session_id)
            row.trust, row.trust_reason = "low", "it read a web page"
            db.add(row)
            db.commit()
        return out

    monkeypatch.setattr(p.orchestrator, "run_session", run_session)


def _run_workflow_from_chat(p, tmp_path, chat, name="Weekly tidy") -> str:
    from iron_jarvis.workflows.store import WorkflowStore
    from iron_jarvis.workflows.tools import WorkflowRunTool

    WorkflowStore(p.engine).save(name, [{"name": "Tidy", "agent": "builder", "task": "tidy up"}], "")
    launched: list = []
    tasks: list = []

    class _Tool(WorkflowRunTool):
        def _launch(self, coro, run_id):
            launched.append(run_id)
            tasks.append(asyncio.get_running_loop().create_task(coro))
            return tasks[-1]

    async def go():
        r = await _Tool(p).execute({"name": name}, _ctx(p, tmp_path, chat))
        assert r.ok, r.error
        await asyncio.gather(*tasks)

    asyncio.run(go())
    assert launched
    return launched[0]


def _run_status(p, run_id) -> str:
    from iron_jarvis.workflows.models import WorkflowRunRecord

    with session_scope(p.engine) as db:
        return db.get(WorkflowRunRecord, run_id).status


def test_a_goal_try_at_low_trust_keeps_the_chat_out_of_the_recap(desk, monkeypatch):
    from iron_jarvis.workmap import recap as R

    c, p = desk
    chat = _chat(p, title="Inbox")
    plain = _goal(c, chat, name="Inbox zero")
    assert c.post(f"/goals/{plain['id']}/run").json().get("session_id")
    flagged = _goal(c, chat, name="Read the supplier page")
    _runs_end_at_low_trust(p, monkeypatch)
    tried = c.post(f"/goals/{flagged['id']}/run").json()
    assert tried.get("session_id"), tried
    by_goal = {m["reportFrom"]["id"]: m for m in _reports(p, chat)}
    assert set(by_goal) == {plain["id"], flagged["id"]}
    # The try's own receipt, as a schedule's run report carries its run's.
    assert by_goal[flagged["id"]]["receipt"]["trust"] == "low"
    assert by_goal[flagged["id"]]["receipt"]["session_id"] == tried["session_id"]
    assert by_goal[plain["id"]]["receipt"]["trust"] == "full"
    low = {ln["content"].split("\n", 1)[0]: ln["low_trust"] for ln in R.lines_of(_messages(p, chat))
           if ln["report"] == "goal"}
    assert sorted(low.values()) == [False, True], low


def test_a_workflow_step_at_low_trust_keeps_the_chat_out_of_the_recap(desk, tmp_path, monkeypatch):
    from iron_jarvis.workmap import recap as R

    c, p = desk
    chat = _chat(p, title="Weekly")
    _runs_end_at_low_trust(p, monkeypatch)
    run_id = _run_workflow_from_chat(p, tmp_path, chat)
    (report,) = _reports(p, chat)
    assert report["reportFrom"]["id"] == run_id
    assert report["receipt"] == {"found": True, "trust": "low", "trust_reason": "it read a web page"}
    assert "session_id" not in report["receipt"], "no run card is drawn for it"
    (line,) = [ln for ln in R.lines_of(_messages(p, chat)) if ln["report"] == "workflow"]
    assert line["low_trust"] is True


def test_a_workflow_run_the_user_stopped_reports_nothing(desk, tmp_path, monkeypatch):
    c, p = desk
    chat = _chat(p, title="Weekly")

    async def stopped(session_id, *a, **kw):  # Stop pressed while the step ran
        raise asyncio.CancelledError()

    monkeypatch.setattr(p.orchestrator, "run_session", stopped)
    run_id = _run_workflow_from_chat(p, tmp_path, chat)
    assert _run_status(p, run_id) == "cancelled"
    assert _reports(p, chat) == []


def test_a_reports_own_stop_never_stops_its_reports_to_another_chat(desk, tmp_path):
    """Review (39-a, round 3, note): Stop reporting on an OLD report in chat A
    sends ``only_if`` A, so a schedule pointed at chat B since keeps
    reporting there, and the press says why in words."""
    c, p = desk
    a = _chat(p, title="A")
    b = _chat(p, title="B")
    _make_schedule(p, tmp_path, a)
    rec = p.scheduler.get("Friday prices")
    assert c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": b}).status_code == 200
    old = c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": "", "only_if": a})
    assert old.status_code == 400 and old.json()["detail"] == "It reports to another chat now."
    assert p.scheduler.get("Friday prices").chat_thread_id == b
    here = c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": "", "only_if": b})
    assert here.status_code == 200 and here.json()["previous"] == b
    assert p.scheduler.get("Friday prices").chat_thread_id == ""


def test_a_reports_own_stop_on_a_record_that_reports_nowhere_says_stopped(desk, tmp_path):
    """Review (39-a, round 4): the user stops a schedule's reports on its row
    (or in chat), then presses Stop reporting on an older report in chat A.
    The schedule reports to no chat, so the press answers "Reports stopped."
    (``previous`` "", so the page offers no Undo), never the false "It
    reports to another chat now."."""
    c, p = desk
    a = _chat(p, title="A")
    _make_schedule(p, tmp_path, a)
    rec = p.scheduler.get("Friday prices")
    row = c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": ""})
    assert row.status_code == 200 and row.json()["previous"] == a
    old = c.post("/reports/target", json={"kind": "schedule", "id": rec.id, "thread_id": "", "only_if": a})
    assert old.status_code == 200, old.json()
    assert old.json()["previous"] == "" and old.json()["thread_id"] == ""
    assert p.scheduler.get("Friday prices").chat_thread_id == ""


def test_a_goal_try_the_user_stopped_reports_nothing(desk, monkeypatch):
    from iron_jarvis.goals import engine as GE

    c, p = desk
    chat = _chat(p, title="Inbox")
    goal = _goal(c, chat)

    async def stopped(self, session):  # Stop pressed while the try ran
        raise asyncio.CancelledError()

    monkeypatch.setattr(GE.GoalEngine, "_run_session", stopped)
    tried = c.post(f"/goals/{goal['id']}/run").json()
    assert tried.get("status") == "cancelled" and tried.get("session_id"), tried
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


# --------------------------------------------------------------------------- #
# Review (39-a, round 3): a NEW chat's first message carries no chat id
# --------------------------------------------------------------------------- #
#
# The page sends ``thread_id`` only once the chat's first save has named it,
# so the first question of a new chat reaches the tools with ``thread_id ""``
# and its ``turn_id``; the page links the turn a moment later
# (``POST /chat/turns/{id}/thread``). What that turn sets up still reports in
# the chat: read through the turn when the link already landed, stamped by the
# link route when it lands after.


def _turn_ctx(p, tmp_path, turn_id: str) -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine, thread_id="", turn_id=turn_id)


def _with_card(tool, args, ctx):
    """capture_undo then execute in ONE context, as the registry runs them
    (the create card rides the captured Undo)."""
    async def go():
        await tool.capture_undo(args, ctx)
        return await tool.execute(args, ctx)

    return asyncio.run(go())


def _link(c, turn_id, chat):
    out = c.post(f"/chat/turns/{turn_id}/thread", json={"thread_id": chat})
    assert out.status_code == 200, out.text
    return out.json()


def test_a_new_chats_first_message_makes_a_schedule_that_reports_there(desk, tmp_path, monkeypatch):
    """The page's first save named the chat and linked the turn before the
    model called the tool: the schedule is stamped, its card says Reports
    here, its fire leaves a report, and report_here works on that turn too."""
    from iron_jarvis.settings.records import ScheduleUpdateTool

    c, p = desk
    chat = _chat(p)
    _link(c, "turn_new_chat_1", chat)
    ctx = _turn_ctx(p, tmp_path, "turn_new_chat_1")
    r = _with_card(ScheduleCreateTool(p), {"name": "Friday prices", "cron": "0 9 * * 5", "task": PRICES}, ctx)
    assert r.ok, r.error
    assert r.data["reports_here"] is True
    assert "Reports here" in r.data["record_change"]["new"]
    assert "reports each result in this chat" in r.output
    rec = p.scheduler.get("Friday prices")
    assert rec.chat_thread_id == chat
    monkeypatch.setattr(cd, "_now", lambda: FRIDAY)
    assert c.post("/schedules/Friday prices/run").status_code == 200
    (only,) = _reports(p, chat)
    assert only["reportFrom"]["id"] == rec.id
    # "Stop reporting here" and "report to this chat" on the same turn.
    upd = ScheduleUpdateTool(p)
    assert asyncio.run(upd.execute({"name": "Friday prices", "report_here": False}, ctx)).ok
    assert p.scheduler.get("Friday prices").chat_thread_id == ""
    back = asyncio.run(upd.execute({"name": "Friday prices", "report_here": True}, ctx))
    assert back.ok, back.error
    assert p.scheduler.get("Friday prices").chat_thread_id == chat


def test_what_a_first_turn_set_up_before_the_link_reports_once_the_page_links_it(desk, tmp_path, monkeypatch):
    """The tool ran before the page's first save landed: nothing is promised
    then, and the link route points the turn's schedule, watcher and night
    task at the chat now named; one pointed elsewhere since keeps it."""
    from iron_jarvis.harness import night as N
    from iron_jarvis.sentinels.tools import SentinelAddTool
    from iron_jarvis.settings.records_build import NightTaskAddTool
    from tests.test_build_git_v1375 import make_repo

    c, p = desk
    chat = _chat(p)
    other = _chat(p, title="Other")
    ctx = _turn_ctx(p, tmp_path, "turn_new_chat_2")
    r = _with_card(ScheduleCreateTool(p), {"name": "Friday prices", "cron": "0 9 * * 5", "task": PRICES}, ctx)
    assert r.ok, r.error
    assert r.data["reports_here"] is False, "nothing is promised before the chat is named"
    assert "Reports here" not in (r.data.get("record_change") or {}).get("new", "")
    assert p.scheduler.get("Friday prices").chat_thread_id == ""
    moved = _with_card(ScheduleCreateTool(p), {"name": "Moved", "cron": "0 9 * * 5", "task": PRICES}, ctx)
    assert moved.ok, moved.error
    cd.set_report_target(p.engine, "schedule", p.scheduler.get("Moved").id, other)
    folder = tmp_path / "in"
    folder.mkdir()
    w = asyncio.run(SentinelAddTool(p).execute({"name": "In", "path": str(folder)}, ctx))
    assert w.ok, w.error
    repo = make_repo(tmp_path / "app")
    n = asyncio.run(NightTaskAddTool(p).execute({"words": "fix the flaky test", "folder": str(repo)}, ctx))
    assert n.ok, n.error
    linked = _link(c, "turn_new_chat_2", chat)
    assert linked["reports_linked"] == 3
    assert p.scheduler.get("Friday prices").chat_thread_id == chat
    assert p.scheduler.get("Moved").chat_thread_id == other, "pointed elsewhere since: kept"
    assert p.sentinels.get("In").chat_thread_id == chat
    with session_scope(p.engine) as db:
        assert db.get(N.NightTaskRecord, n.data["task_id"]).chat_thread_id == chat
    # A second link of the same turn stamps nothing again.
    assert _link(c, "turn_new_chat_2", chat)["reports_linked"] == 0
    monkeypatch.setattr(cd, "_now", lambda: FRIDAY)
    assert c.post("/schedules/Friday prices/run").status_code == 200
    assert [m["reportFrom"]["name"] for m in _reports(p, chat)] == ["Friday prices"]
    rows = {r["name"]: r for r in c.get("/schedules").json()["schedules"]}
    assert rows["Friday prices"]["reports_to"] == {"thread_id": chat, "title": "Supplier prices"}


def test_a_workflow_run_from_a_new_chats_first_turn_reports_when_it_ends(desk, tmp_path):
    """Its end reads the run's record, so the chat the page names while the
    run goes is where its one report lands."""
    from iron_jarvis.workflows.store import WorkflowStore
    from iron_jarvis.workflows.tools import WorkflowRunTool

    c, p = desk
    chat = _chat(p, title="Weekly")
    WorkflowStore(p.engine).save("Tidy", [{"name": "Tidy", "agent": "builder", "task": "tidy up"}], "")
    held: list = []

    class _Tool(WorkflowRunTool):
        def _launch(self, coro, run_id):
            held.append(coro)
            return object()

    async def go():
        r = await _Tool(p).execute({"name": "Tidy"}, _turn_ctx(p, tmp_path, "turn_new_chat_3"))
        assert r.ok, r.error
        linked = await asyncio.to_thread(_link, c, "turn_new_chat_3", chat)
        assert linked["reports_linked"] == 1
        await held[0]  # the run goes on to its end with the chat now named

    asyncio.run(go())
    (only,) = _reports(p, chat)
    assert only["reportFrom"]["kind"] == "workflow" and only["reportFrom"]["name"] == "Tidy"


def test_a_link_landing_while_the_tool_runs_is_not_missed(desk, tmp_path, monkeypatch):
    """The link route ran between the tool's read of the chat and its record:
    the record does not wait for a link that already came, it is stamped by
    its own tool and its card says so."""
    c, p = desk
    chat = _chat(p)
    monkeypatch.setattr(cd, "report_chat_for", lambda platform, ctx: "")  # read before the link
    assert cd.link_turn_reports("turn_new_chat_4", chat) == []  # ... which lands now
    r = _with_card(ScheduleCreateTool(p), {"name": "Friday prices", "cron": "0 9 * * 5", "task": PRICES},
                   _turn_ctx(p, tmp_path, "turn_new_chat_4"))
    assert r.ok, r.error
    assert r.data["reports_here"] is True
    assert "Reports here" in r.data["record_change"]["new"]
    assert p.scheduler.get("Friday prices").chat_thread_id == chat


@pytest.mark.parametrize("tests_seen, words", [
    ("failed", "Its tests failed."),
    ("", "No tests were checked."),
])
def test_a_night_tasks_report_says_how_its_tests_ended(tests_seen, words):
    from iron_jarvis.harness import night as N

    said = N.report_checks({"tests": tests_seen, "files": 0})
    assert said == [words], "no files count when the branch says none"
    assert N.report_checks({}) == [], "no morning facts, no words about checks"
    assert N.report_checks({"tests": "passed", "tests_trusted": False, "files": 1}) == [
        "Its tests passed, but the result is not trusted: open the session to see why.", "1 file changed."]


def test_a_local_only_night_tasks_report_keeps_its_branch_and_notes_in_its_session(desk, tmp_path):
    """The morning notice's Local only rule: in a chat that is not Local only,
    the report gives how it ended and its checks, never its branch or notes,
    and the bell names it only as a Local only task."""
    from sqlalchemy import update

    from iron_jarvis.core.events import EventType
    from iron_jarvis.harness import night as N
    from iron_jarvis.harness.night_morning import LOCAL_ONLY_TITLE

    c, p = desk
    chat = _chat(p, title="Night", archived_at=datetime(2026, 11, 1))
    seen: list[dict] = []
    real = p.event_bus.publish

    async def publish(etype, payload=None, **kw):
        if str(etype) == EventType.REPORT_ARRIVED:
            seen.append(dict(payload or {}))
        return await real(etype, payload, **kw)

    p.event_bus.publish = publish
    task = N.add_task(p.engine, folder=str(tmp_path), words="fix the secret parser")
    tid = task["id"]
    cd.set_report_target(p.engine, "night", tid, chat)
    with session_scope(p.engine) as db:
        db.exec(update(N.NightTaskRecord).where(N.NightTaskRecord.id == tid)
                .values(state=N.RUNNING, branch="ij/secret-parser"))
        db.commit()
    N.add_note(p.engine, tid, "info", "the parser reads the vault file")
    N.set_morning(p.engine, tid, {"tests": "passed", "files": 2, "local_only": True})
    assert N.finish_task(p.engine, tid, N.DONE, event_bus=p.event_bus) is True
    (only,) = _reports(p, chat)
    assert "Its tests passed." in only["content"] and "2 files changed." in only["content"]
    assert N.LOCAL_ONLY_REPORT_WORDS in only["content"]
    assert "ij/secret-parser" not in only["content"]
    assert "vault file" not in only["content"]
    assert [e.get("name") for e in seen] == [LOCAL_ONLY_TITLE]
