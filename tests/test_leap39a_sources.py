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
    c, p = desk
    chat = _chat(p, title="Intake")
    folder = tmp_path / "intake"
    folder.mkdir()
    svc = p.sentinels
    rec = svc.add("Intake", path=str(folder), task="sort them by sender", chat_thread_id=chat)
    clock = {"now": FRIDAY}
    svc.clock = lambda: clock["now"]
    first = svc.report_to_chat(rec, [{"path": str(folder / "a.pdf")}])
    assert first["reported"] is True
    soon = svc.report_to_chat(rec, [{"path": str(folder / "b.pdf")}, {"path": str(folder / "c.pdf")}])
    assert soon == {"reported": False, "waiting": 2}
    assert len(_reports(p, chat)) == 1
    clock["now"] = FRIDAY + timedelta(minutes=61)
    later = svc.report_to_chat(rec, [])  # nothing new: the waiting ones go out
    assert later["reported"] is True
    reports = _reports(p, chat)
    assert len(reports) == 2
    assert "2 new or changed files in Intake: b.pdf, c.pdf." in reports[1]["content"]
    assert "Suggested: sort them by sender." in reports[1]["content"]
    assert reports[1]["reportFrom"]["kind"] == "watcher"
    # Nothing waiting, nothing written.
    clock["now"] = FRIDAY + timedelta(hours=5)
    assert svc.report_to_chat(rec, [])["reported"] is False
    rows = {s["name"]: s for s in c.get("/sentinels").json()["sentinels"]}
    assert rows["Intake"]["reports_to"] == {"thread_id": chat, "title": "Intake"}


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


def test_a_night_task_queued_from_chat_reports_how_it_ended(desk):
    from iron_jarvis.harness import night as N

    c, p = desk
    chat = _chat(p, title="Night")
    task = N.add_task(p.engine, folder="C:/work/app", words="fix the flaky test")
    assert cd.set_report_target(p.engine, "night", task["id"], chat)["ok"]
    from sqlalchemy import update

    with session_scope(p.engine) as db:
        db.exec(update(N.NightTaskRecord).where(N.NightTaskRecord.id == task["id"])
                .values(state=N.RUNNING, branch="ij/fix-flaky"))
        db.commit()
    assert N.finish_task(p.engine, task["id"], N.DONE, event_bus=p.event_bus) is True
    reports = _reports(p, chat)
    assert len(reports) == 1
    assert reports[0]["reportFrom"]["kind"] == "night"
    assert reports[0]["reportFrom"]["outcome"] == "Done"
    assert "ij/fix-flaky" in reports[0]["content"]
    # Ending it again writes nothing more (it is no longer running).
    assert N.finish_task(p.engine, task["id"], N.DONE) is False
    assert len(_reports(p, chat)) == 1
