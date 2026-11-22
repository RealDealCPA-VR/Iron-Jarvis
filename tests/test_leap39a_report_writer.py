"""Idea 39, phase 39-a: ONE field and ONE writer, so results come home.

``agents/chat_delivery.report_to_chat`` is the one writer a schedule, a
watcher, a goal, a workflow or a night task uses to put its result into the
chat it was set up from. Pinned here on a real database:

* one line per report key, whoever writes it and however often (two
  finishers, a restart);
* every key the line carries is one the chat writer keeps (``LINE_KEYS``);
* a record that reports more than once a day keeps ONE line a day in the
  chat (master plan R1.1, D-39.1): the day's line is replaced, its count goes
  up, and a late repeat of a replaced report is still a repeat;
* a phone conversation and a coding session refuse in words;
* a chat that is gone clears the record's link and rings the bell;
* an archived chat stays archived and the bell says a report arrived (D-39.2);
* the words are composed by code and cut at ``REPORT_MAX_CHARS``; the
  verdict is the ledger's, never the model's closing words;
* a run its source set up writes the SOURCE's report (no agent's name on
  it), and a goal's try (``by: source``) writes nothing itself;
* a report carrying a low-trust run's receipt keeps the chat out of the
  recap pass.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import chat_delivery as cd
from iron_jarvis.core.db import open_db, session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.daemon.chat_append import LINE_KEYS
from iron_jarvis.goals.models import GoalContractRecord  # noqa: F401 — its table
from iron_jarvis.harness.night import NightTaskRecord  # noqa: F401 — its table
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.sentinels.models import SentinelRecord  # noqa: F401 — its table
from iron_jarvis.workflows.models import WorkflowRunRecord  # noqa: F401 — its table

FRIDAY = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, etype, payload=None, **_kw):
        self.events.append((str(etype), dict(payload or {})))

    def of(self, etype: str) -> list[dict]:
        return [p for t, p in self.events if t == etype]


@pytest.fixture()
def p(tmp_path):
    engine = open_db(tmp_path / "ij.db")
    return SimpleNamespace(engine=engine, event_bus=_Bus())


def _chat(p, messages=None, **cols) -> str:
    rec = ChatThreadRecord(title=cols.pop("title", "Supplier prices"),
                           messages_json=json.dumps(messages or []), **cols)
    with session_scope(p.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _messages(p, tid) -> list[dict]:
    with session_scope(p.engine) as db:
        return json.loads(db.get(ChatThreadRecord, tid).messages_json or "[]")


def _schedule(p, chat="") -> ScheduledTaskRecord:
    rec = ScheduledTaskRecord(name="Friday prices", cron="0 9 * * 5", kind="task",
                              payload_json=json.dumps({"task": "check the prices"}),
                              chat_thread_id=chat)
    with session_scope(p.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec


def _report(rec, key, words="Prices are up 3%.", **more) -> dict:
    return {"kind": "schedule", "name": rec.name, "id": rec.id, "outcome": "Done",
            "words": words, "key": key, **more}


def test_one_line_per_key_across_two_writers_and_a_restart(p):
    tid = _chat(p)
    rec = _schedule(p, tid)
    first = cd.report_to_chat(p, tid, _report(rec, "schedule:1"), now=FRIDAY)
    assert first["ok"] and first["appended"] == 1, first
    # A second finisher with the same key (the run's finalizer AND the boot
    # reconcile, say): nothing more.
    again = cd.report_to_chat(p, tid, _report(rec, "schedule:1"), now=FRIDAY)
    assert again["ok"] and again["appended"] == 0 and again["skipped"] == 1, again
    # "A restart": a new engine on the same database writes it once too.
    p2 = SimpleNamespace(engine=open_db(p.engine.url.database), event_bus=_Bus())
    later = cd.report_to_chat(p2, tid, _report(rec, "schedule:1"), now=FRIDAY)
    assert later["appended"] == 0 and later["skipped"] == 1, later
    msgs = _messages(p, tid)
    assert len(msgs) == 1
    assert msgs[0]["reportKey"] == "schedule:1"
    assert msgs[0]["reportFrom"]["kind"] == "schedule"
    assert msgs[0]["reportFrom"]["id"] == rec.id
    assert msgs[0]["reportFrom"]["href"] == "/schedules"


def test_every_key_the_report_line_carries_is_one_the_chat_writer_keeps(p):
    tid = _chat(p)
    rec = _schedule(p, tid)
    receipt = {"found": True, "trust": "full", "documents": ["C:/out/prices.xlsx"]}
    line = cd.report_line(_report(rec, "run:sess_1", run_id="sess_1", receipt=receipt), now=FRIDAY)
    assert line is not None
    assert set(line) - {"at"} <= set(LINE_KEYS), set(line) - set(LINE_KEYS)
    for key in ("reportFrom", "reportKey", "reportFold", "reportCount", "reportSeen"):
        assert key in LINE_KEYS
    cd.report_to_chat(p, tid, _report(rec, "run:sess_1", run_id="sess_1", receipt=receipt), now=FRIDAY)
    stored = _messages(p, tid)[0]
    for key in ("reportFrom", "reportKey", "reportFold", "receipt", "documents", "sessionId"):
        assert key in stored, key
    assert stored["documents"] == ["C:/out/prices.xlsx"]
    assert "panelWho" not in stored, "a report is never drawn as an agent's reply"


def test_more_than_daily_folds_into_one_line_a_day(p):
    tid = _chat(p, [{"role": "user", "content": "check this supplier's prices every hour"}])
    rec = _schedule(p, tid)
    for hour in range(3):
        out = cd.report_to_chat(p, tid, _report(rec, f"schedule:{hour}", words=f"fire {hour}"),
                                now=FRIDAY + timedelta(hours=hour))
        assert out["ok"], out
    msgs = _messages(p, tid)
    reports = [m for m in msgs if m.get("reportFrom")]
    assert len(reports) == 1, "three fires on one day are ONE line in the chat"
    day = reports[0]
    assert day["reportCount"] == 3
    assert "fire 2" in day["content"], "the day's line says the newest result"
    assert msgs.index(day) == 1, "the day's line keeps its place"
    # A late repeat of a replaced report is still a repeat.
    late = cd.report_to_chat(p, tid, _report(rec, "schedule:0"), now=FRIDAY)
    assert late["appended"] == 0 and late["skipped"] == 1
    # The next day starts a new line.
    cd.report_to_chat(p, tid, _report(rec, "schedule:3", words="next day"), now=FRIDAY + timedelta(days=1))
    assert len([m for m in _messages(p, tid) if m.get("reportFrom")]) == 2


def test_a_days_line_the_user_answered_is_never_rewritten(p):
    """Review: the 9 am report was talked about; the 2 pm one must not swap
    the words above that exchange. It is a new line, and later reports that
    day fold into THAT one."""
    tid = _chat(p, [{"role": "user", "content": "check this supplier's prices every hour"}])
    rec = _schedule(p, tid)
    cd.report_to_chat(p, tid, _report(rec, "run:s9", run_id="s9", words="Prices up 3%."), now=FRIDAY)
    with session_scope(p.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        msgs = json.loads(row.messages_json)
        msgs += [{"role": "user", "content": "why up 3%?"},
                 {"role": "assistant", "content": "Because of the new tariff."}]
        row.messages_json = json.dumps(msgs)
        db.add(row)
        db.commit()
    cd.report_to_chat(p, tid, _report(rec, "run:s14", run_id="s14", words="Prices flat."),
                      now=FRIDAY + timedelta(hours=5))
    cd.report_to_chat(p, tid, _report(rec, "run:s15", run_id="s15", words="Prices down 1%."),
                      now=FRIDAY + timedelta(hours=6))
    msgs = _messages(p, tid)
    reports = [m for m in msgs if m.get("reportFrom")]
    assert len(reports) == 2, [m.get("content") for m in msgs]
    first, second = reports
    assert "Prices up 3%." in first["content"] and first["sessionId"] == "s9", "what was discussed stays"
    assert first.get("reportCount", 1) == 1
    assert msgs.index(first) == 1 and msgs.index(second) == len(msgs) - 1
    assert "Prices down 1%." in second["content"] and second["reportCount"] == 2


def test_a_watcher_is_never_folded_it_paces_itself(p):
    tid = _chat(p)
    line = cd.report_line({"kind": "watcher", "name": "Intake", "id": "sentinel_1",
                           "words": "3 new files", "key": "watcher:1"}, now=FRIDAY)
    assert "reportFold" not in line


def test_a_phone_and_a_coding_thread_are_refused_in_words(p):
    phone = _chat(p, owner="daemon")
    code = _chat(p, surface="build")
    rec = _schedule(p, phone)
    for tid in (phone, code):
        out = cd.report_to_chat(p, tid, _report(rec, "schedule:x"), now=FRIDAY)
        assert out["ok"] is False and out.get("refused") is True, out
        assert out["why"] and " " in out["why"], "refused in words"
        assert _messages(p, tid) == []
    # A refusal is not "the chat is gone": the record keeps its link (39-c
    # sends a phone conversation its reports).
    with session_scope(p.engine) as db:
        assert db.get(ScheduledTaskRecord, rec.id).chat_thread_id == phone


def test_a_deleted_chat_clears_the_field_and_rings_the_bell(p):
    rec = _schedule(p, "chat_deleted_meanwhile")
    out = cd.report_to_chat(p, "chat_deleted_meanwhile", _report(rec, "schedule:1"), now=FRIDAY)
    assert out["ok"] is False and out["gone"] is True and out["cleared"] is True
    with session_scope(p.engine) as db:
        assert db.get(ScheduledTaskRecord, rec.id).chat_thread_id == ""
    rung = p.event_bus.of(EventType.REPORT_CHAT_GONE)
    assert len(rung) == 1
    assert rung[0]["words"] == "The chat this schedule reported to is gone. Its results now show here."
    assert rung[0]["href"] == "/schedules" and rung[0]["name"] == "Friday prices"
    # Review: the record's own row says why it reports nowhere now.
    with session_scope(p.engine) as db:
        assert cd.GONE_ROW_WORDS in db.get(ScheduledTaskRecord, rec.id).last_detail


def test_a_workflow_run_whose_chat_is_gone_says_so_in_its_own_notes(p):
    """Review: the Workflows run row shows its notes; the reason is there too."""
    run = WorkflowRunRecord(workflow_name="Weekly tidy", status="completed",
                            chat_thread_id="chat_gone_wf", notes_json=json.dumps(["an earlier note"]))
    with session_scope(p.engine) as db:
        db.add(run)
        db.commit()
        db.refresh(run)
    report = {"kind": "workflow", "name": "Weekly tidy", "id": run.id, "outcome": "Done",
              "words": "Tidied.", "key": f"workflow:{run.id}"}
    out = cd.report_to_chat(p, "chat_gone_wf", report, now=FRIDAY)
    assert out["gone"] is True and out["cleared"] is True
    cd.report_to_chat(p, "chat_gone_wf", report, now=FRIDAY)  # once only
    with session_scope(p.engine) as db:
        row = db.get(WorkflowRunRecord, run.id)
        assert row.chat_thread_id == ""
        assert json.loads(row.notes_json) == ["an earlier note", cd.GONE_ROW_WORDS]


def test_a_gone_chat_clears_only_a_link_that_still_names_it(p):
    """Review: a run fired for chat A ends after the schedule was pointed at
    chat B and A was deleted. B's link stays, and no bell says it is gone."""
    b = _chat(p, title="Chat B")
    rec = _schedule(p, b)
    out = cd.report_to_chat(p, "chat_a_deleted", _report(rec, "run:sA", run_id="sA"), now=FRIDAY)
    assert out["gone"] is True and out["cleared"] is False
    with session_scope(p.engine) as db:
        assert db.get(ScheduledTaskRecord, rec.id).chat_thread_id == b
    assert p.event_bus.of(EventType.REPORT_CHAT_GONE) == []


def test_a_stop_on_a_record_that_reports_nowhere_is_ok_and_changes_nothing(p):
    """Review (39-a, round 4): a report's own Stop reporting (``only_if`` the
    chat it is in) on a record that already reports nowhere answers ok — it
    IS stopped — with ``changed`` False. So a run whose chat is gone after the
    user stopped its reports clears nothing: no gone bell, no gone words on
    the row (the user stopped it; nothing went missing)."""
    rec = _schedule(p, "")
    out = cd.set_report_target(p.engine, "schedule", rec.id, "", only_if="chat_a")
    assert out["ok"] is True and out["previous"] == "" and out["thread_id"] == ""
    assert out["changed"] is False
    # A record that reports to ANOTHER chat still refuses in words.
    b = _chat(p, title="Chat B")
    other = ScheduledTaskRecord(name="Monday stock", cron="0 9 * * 1", kind="task",
                                payload_json=json.dumps({"task": "count the stock"}), chat_thread_id=b)
    with session_scope(p.engine) as db:
        db.add(other)
        db.commit()
        db.refresh(other)
    refused = cd.set_report_target(p.engine, "schedule", other.id, "", only_if="chat_a")
    assert refused["ok"] is False and refused["why"] == "It reports to another chat now."
    moved = cd.set_report_target(p.engine, "schedule", other.id, "", only_if=b)
    assert moved["ok"] is True and moved["previous"] == b and moved["changed"] is True
    gone = cd.report_to_chat(p, "chat_a_deleted", _report(rec, "run:sA", run_id="sA"), now=FRIDAY)
    assert gone["gone"] is True and gone["cleared"] is False
    assert p.event_bus.of(EventType.REPORT_CHAT_GONE) == []
    with session_scope(p.engine) as db:
        assert cd.GONE_ROW_WORDS not in (db.get(ScheduledTaskRecord, rec.id).last_detail or "")


def test_stop_reporting_while_a_run_is_going_holds_its_report(p):
    """Review: the run carries its chat from fire time; Stop reporting pressed
    meanwhile wins (the record's field is read when the run ends)."""
    tid = _chat(p)
    rec = _schedule(p, tid)
    run = SimpleNamespace(
        id="sess_s", status="completed", outcome="completed", agent_type="builder", agent_name="",
        summary="done", options_json=json.dumps({"chat_thread_id": tid, "report_from": {
            "kind": "schedule", "name": rec.name, "id": rec.id, "href": "/schedules"}}),
    )
    assert cd.set_report_target(p.engine, "schedule", rec.id, "")["ok"]
    out = cd.deliver_session_line(p.engine, run, event_bus=p.event_bus)
    assert out["appended"] == 0, out
    assert _messages(p, tid) == []
    # Pointed back, the next run reports again.
    assert cd.set_report_target(p.engine, "schedule", rec.id, tid)["ok"]
    assert cd.deliver_session_line(p.engine, run, event_bus=p.event_bus)["appended"] == 1


def test_a_phone_conversation_is_never_stamped(p):
    """Review: nothing can report to a phone thread until 39-c, so nothing made
    there says it reports there."""
    phone = _chat(p, owner="daemon", title="Slack DM")
    code = _chat(p, surface="build")
    plain = _chat(p)
    assert cd.stampable_chat(p.engine, phone) == ""
    assert cd.stampable_chat(p.engine, code) == ""
    assert cd.stampable_chat(p.engine, plain) == plain


def test_an_archived_chat_stays_archived_and_the_bell_says_a_report_arrived(p):
    tid = _chat(p, archived_at=datetime(2026, 11, 1))
    rec = _schedule(p, tid)
    out = cd.report_to_chat(p, tid, _report(rec, "schedule:1", words="secret supplier figures"), now=FRIDAY)
    assert out["ok"] and out["appended"] == 1
    with session_scope(p.engine) as db:
        assert db.get(ChatThreadRecord, tid).archived_at is not None, "D-39.2: it stays archived"
    arrived = p.event_bus.of(EventType.REPORT_ARRIVED)
    assert arrived == [{"thread_id": tid, "title": "Supplier prices", "kind": "schedule",
                        "name": "Friday prices"}], "titles only, never the report's words"
    # A repeat rings nothing.
    cd.report_to_chat(p, tid, _report(rec, "schedule:1"), now=FRIDAY)
    assert len(p.event_bus.of(EventType.REPORT_ARRIVED)) == 1


def test_an_open_chat_rings_no_bell(p):
    tid = _chat(p)
    rec = _schedule(p, tid)
    cd.report_to_chat(p, tid, _report(rec, "schedule:1"), now=FRIDAY)
    assert p.event_bus.of(EventType.REPORT_ARRIVED) == []
    assert [t for t, _ in p.event_bus.events] == [EventType.CHAT_THREAD_UPDATED]


def test_the_words_are_composed_by_code_and_cut(p):
    tid = _chat(p)
    rec = _schedule(p, tid)
    long = "x" * (cd.REPORT_MAX_CHARS * 3)
    line = cd.report_line(_report(rec, "k", words=long, outcome="Needs you",
                                  reason="It asked for your OK and nobody answered in time."), now=FRIDAY)
    lead, _, body = line["content"].partition("\n\n")
    assert lead == ("From your schedule \u201cFriday prices\u201d. "
                    "It asked for your OK and nobody answered in time.")
    assert len(body) == cd.REPORT_MAX_CHARS and body.endswith(cd.REPORT_CUT_WORDS)
    assert line["reportFrom"]["outcome"] == "Needs you"


def test_a_run_its_source_set_up_writes_the_sources_report(p):
    """``line_for_session``: the schedule's report, verdict from the ledger."""
    tid = _chat(p)
    rec = _schedule(p, tid)
    run = SimpleNamespace(
        id="sess_9", status="completed", outcome="needs_you", agent_type="builder",
        agent_name="", summary="All done! Everything worked perfectly.",
        options_json=json.dumps({"chat_thread_id": tid, "report_from": {
            "kind": "schedule", "name": rec.name, "id": rec.id, "href": "/schedules"}}),
    )
    line = cd.line_for_session(run, {"found": True, "trust": "full"}, "builtin:builder", now=FRIDAY)
    assert line["reportFrom"]["outcome"] == "Needs you", "the ledger's verdict, not the closing words"
    assert line["content"].startswith("From your schedule \u201cFriday prices\u201d. It asked for your OK")
    assert line["sessionId"] == "sess_9" and line["reportKey"] == "run:sess_9"
    assert "panelWho" not in line
    out = cd.deliver_session_line(p.engine, run, event_bus=p.event_bus)
    assert out["ok"] and out["appended"] == 1, out
    again = cd.deliver_session_line(p.engine, run, event_bus=p.event_bus)
    assert again["appended"] == 0, "the finalizer and the boot reconcile write it once"
    # A plain chat run (no source) still writes an agent's reply.
    plain = SimpleNamespace(**{**run.__dict__, "id": "sess_10",
                               "options_json": json.dumps({"chat_thread_id": tid})})
    assert cd.line_for_session(plain, None, "builtin:builder").get("panelWho") == "builtin:builder"


def test_a_goal_try_writes_nothing_itself(p):
    tid = _chat(p)
    run = SimpleNamespace(
        id="sess_g", status="completed", outcome="completed", agent_type="builder", agent_name="",
        summary="did it", options_json=json.dumps({"chat_thread_id": tid, "report_from": {
            "kind": "goal", "name": "Inbox zero", "id": "goal_1", "by": "source"}}),
    )
    out = cd.deliver_session_line(p.engine, run, event_bus=p.event_bus)
    assert out["ok"] and out["appended"] == 0
    assert _messages(p, tid) == []


def test_stop_reporting_and_its_undo(p):
    tid = _chat(p)
    rec = _schedule(p, tid)
    out = cd.set_report_target(p.engine, "schedule", rec.id, "")
    assert out == {"ok": True, "why": "", "previous": tid, "thread_id": "", "changed": True}
    back = cd.set_report_target(p.engine, "schedule", rec.id, out["previous"])
    assert back["ok"] and back["thread_id"] == tid
    # Never a coding session or a chat that is not saved.
    code = _chat(p, surface="build")
    assert cd.set_report_target(p.engine, "schedule", rec.id, code)["ok"] is False
    assert cd.set_report_target(p.engine, "schedule", rec.id, "chat_nope")["ok"] is False
    assert cd.set_report_target(p.engine, "run", "x", tid)["ok"] is False


def test_a_low_trust_report_keeps_the_chat_out_of_the_recap(p):
    from iron_jarvis.workmap.recap import lines_of

    tid = _chat(p)
    rec = _schedule(p, tid)
    cd.report_to_chat(p, tid, _report(rec, "run:s", run_id="s",
                                      receipt={"found": True, "trust": "low"}), now=FRIDAY)
    lines = lines_of(_messages(p, tid))
    assert lines and lines[0]["low_trust"] is True


# The plan's "(index)": every table that holds the field indexes it, a fresh
# database and an older one alike (the reconciler adds a column, never its
# index; ``core.db._HOT_INDEXES`` does that).
_FIELD_TABLES = ("scheduledtaskrecord", "sentinelrecord", "goalcontract", "workflowrunrecord", "nighttaskrecord")


def test_the_field_is_indexed_on_a_fresh_and_an_older_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    open_db(path).dispose()

    def _state() -> tuple[set[str], dict[str, set[str]]]:
        con = sqlite3.connect(str(path))
        try:
            idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            cols = {t: {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')} for t in _FIELD_TABLES}
        finally:
            con.close()
        return idx, cols

    idx, _cols = _state()
    for t in _FIELD_TABLES:
        assert f"ix_{t}_chat_thread_id" in idx, t
    # An older install: the tables without the column (and so its index).
    con = sqlite3.connect(str(path))
    try:
        for t in _FIELD_TABLES:
            con.execute(f"DROP INDEX IF EXISTS ix_{t}_chat_thread_id")
            con.execute(f'ALTER TABLE "{t}" DROP COLUMN chat_thread_id')
        con.commit()
    finally:
        con.close()
    open_db(path).dispose()
    idx, cols = _state()
    for t in _FIELD_TABLES:
        assert "chat_thread_id" in cols[t], t
        assert f"ix_{t}_chat_thread_id" in idx, t
