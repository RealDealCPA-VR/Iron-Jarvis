"""R0.2 (build-out plan, Stage 0): "Copy as a fix request" leaves out Local
only work (``agents/fix_request``, ``GET /team/fix-request``).

The report is written for a coding agent working on Iron Jarvis and pasted
into its prompt. A coding session the user keeps Local only ("nothing leaves
this PC or your own network without asking") must not reach that model
through it: not an error, not an argument, not a reviewer's finding, not even
its id. Before R0.2 the module had no Local only check at all.

What marks a coding session Local only (any one is enough, and the WHOLE
session is then left out — a session switched off later still ran Local only
work earlier):

* its setup says so now (``setup.local_only``, or an app holds it local);
* one of its turns says it ran Local only: the ``localOnly`` mark that
  ``routes/build`` writes on the question and the reply line of every Local
  only turn, whatever its end (the one record of a reply that failed or was
  stopped before it ran a call: such a line keeps no route — the second
  review's finding);
* one of its replies ran under the toggle (the line's ``route.local_only``)
  or was refused for it (``errorKind: "local_only"``);
* one of its helpers ran Local only (the run's ``options.local_only``);
* the event log stamped one of its calls Local only (the bus's stamp on
  ``tool.executed`` / ``tool.denied`` / ``autonomy.nudged``) — a failed
  reply's calls, also from before the line mark.

A helper or an objective's teammate that ran Local only is left out on its
own. A setting that cannot be read is left out too (fail closed) and makes
the report incomplete. What was left out is said, never silently dropped.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from iron_jarvis.agents import fix_request as FR
from iron_jarvis.agents.team import MISSION_MEMBER_ORIGIN
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    ChatThreadRecord,
    ChatTurnRecord,
    PermissionMode,
    Session,
    SessionStatus,
)
from iron_jarvis.harness import helpers as H
from tests.test_fix_request_v14230 import (
    NOW,
    T0,
    THREAD,
    _call,
    _engine,
    _event,
    _failure,
    _report,
    _seed_done_means,
)

LOCAL = "chat_10ca10ca10ca"
MARK = "LOCALMARK"
AT = T0 + timedelta(hours=2)


def _ms(dt) -> int:
    return int(dt.timestamp() * 1000)


def _reply(start, minutes: int = 30, **extra) -> dict:
    end = start + timedelta(minutes=minutes)
    line = {
        "role": "assistant", "content": "Done.", "turnId": f"turn_{_ms(start)}", "at": end.isoformat(),
        "route": {"provider": "custom", "model": "glm-5.3-flash"},
        "timing": {"startedAt": _ms(start), "endedAt": _ms(end), "ms": 1},
    }
    line.update(extra)
    return line


def _local_thread(db, *, tid: str = LOCAL, setup: "dict | str | None" = None, lines: "list | None" = None,
                  question: "dict | None" = None) -> None:
    """A coding session with its own failures, each carrying the marker."""
    msgs = [{"role": "user", "content": f"Fix the ledger {MARK}", "at": AT.isoformat(), **(question or {})}]
    msgs += lines if lines is not None else [_reply(AT)]
    raw = setup if isinstance(setup, str) else json.dumps(
        setup if setup is not None else {"provider": "custom", "model": "glm-5.3-flash"})
    db.add(ChatThreadRecord(
        id=tid, title=f"Client ledger {MARK}", surface="build", messages_json=json.dumps(msgs),
        setup_json=raw, created_at=AT, updated_at=AT + timedelta(minutes=40),
    ))


def _local_work(db, *, tid: str = LOCAL, helper: str = "session_10ca10ca0001",
                helper_options: "dict | None" = None) -> None:
    """The session's lead calls, its helper, a text tool call and a finding —
    and calls that WORKED by the same lead model and the same helper member as
    the other session's (``_seed_done_means``), so a Local only call counted
    in an "N of M" shows in that session's problems."""
    sid = f"build:{tid}"
    db.add(AgentRun(
        id=f"run_{tid[-8:]}", session_id="chat", thread_id=tid, provider="claude-cli",
        model="claude-opus-5-5", created_at=AT, finished_at=AT + timedelta(minutes=40),
    ))
    _call(db, iid=f"tool_{tid[-6:]}_ok1", sid=sid, tool="delegate", ok=True, verdict=PermissionMode.ALLOW,
          args={"role": "test", "task": f"{MARK} tests"}, output="started", at=AT + timedelta(minutes=1))
    _call(db, iid=f"tool_{tid[-6:]}_ok2", sid=helper, tool="shell", ok=True,
          args={"command": f"type {MARK}.txt"}, output="ok", at=AT + timedelta(minutes=5))
    # A reply of it still running (after its calls): a Local only session is
    # never "still running, read once it ends" — it is never read.
    db.add(ChatTurnRecord(id=f"turn_{tid[-6:]}_run", thread_id=tid, status="running",
                          started_at=AT + timedelta(minutes=50)))
    _call(db, iid=f"tool_{tid[-6:]}_1", sid=sid, tool="shell", ok=False,
          args={"command": f"type {MARK}-ledger.csv | head -5"},
          output=_failure("exit 255", "'head' is not recognized as an internal or external command,"),
          at=AT + timedelta(minutes=2))
    _call(db, iid=f"tool_{tid[-6:]}_2", sid=sid, tool="read_file", ok=False, verdict=PermissionMode.ALLOW,
          args={"path": f"{MARK}-notes.md"}, output=f"no such file: {MARK}-notes.md",
          at=AT + timedelta(minutes=3))
    opts = {H.BUILD_SESSION_OPTION: tid, **(helper_options or {})}
    db.add(Session(
        id=helper, origin=H.HELPER_ORIGIN, agent_name="custom:glm-worker", provider="custom",
        model="glm-5.3-flash", status=SessionStatus.FAILED, trust="full",
        summary=f"The run failed: the {MARK} worker dropped mid-answer.",
        options_json=json.dumps(opts), created_at=AT + timedelta(minutes=4),
    ))
    _call(db, iid=f"tool_{tid[-6:]}_3", sid=helper, tool="edit_file", ok=False, verdict=PermissionMode.ALLOW,
          args={"path": f"{MARK}.py"}, output=f"old text not found in {MARK}.py",
          at=AT + timedelta(minutes=5))
    _event(db, eid=f"evt_{tid[-6:]}_nudge", type_="autonomy.nudged", sid=sid,
           payload={"kind": "text_tool_call", "round": 2, "call": f"<{MARK}>"},
           at=AT + timedelta(minutes=6))
    from iron_jarvis.agents import team_findings as TF

    db.add(TF.TeamFinding(
        scope=sid, turn="reply_x", review_row="", review_session="", reviewer="opus-reviewer", number=1,
        path=f"{MARK}.py", what=f"{MARK} is never checked", change="check it", member="glm-worker",
        status=TF.DID_NOT_APPLY, answer=f"{MARK} is checked elsewhere", asked=True,
        created_at=AT + timedelta(minutes=10),
    ))


def _seed(engine, **thread_kw) -> None:
    _seed_done_means(engine)
    with session_scope(engine) as db:
        _local_thread(db, **thread_kw)
        _local_work(db)
        db.commit()


LEFT_OUT_ONE = "- Left out: 1 Local only coding session (Local only work never goes into a fix request)"


def _assert_left_out(out: dict) -> None:
    text = out["text"]
    assert MARK not in text, text
    assert "chat_10ca10ca" not in text and "session_10ca10ca" not in text, text
    # The other session's three problems are untouched, and it is the only
    # session the report covers.
    assert out["problems"] == 3 and out["sessions"] == 1, text
    assert "- Covers: 1 coding session, 1 piece handed to helpers or teammates," in text
    # No call of the Local only session is counted in any "N of M".
    assert "3 of 6 shell calls by this member failed this way" in text, text
    assert "1 of 1 delegate calls by this lead failed this way" in text, text
    assert LEFT_OUT_ONE in text, text
    assert "still running" not in text, text
    assert out["local_only_left_out"] == 1
    assert out["complete"] is True


# --------------------------------------------------------------------------- #
# the red-then-green test: a Local only coding session's content
# --------------------------------------------------------------------------- #


def test_a_local_only_coding_session_is_left_out_whole(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True},
          lines=[_reply(AT, error=f"The {MARK} model dropped mid-answer.", content="",
                        route={"provider": "custom", "model": "glm-5.3-flash",
                               "cut_off": {"times": 1, "recovered": False}})])
    _assert_left_out(_report(engine))


def test_the_sessions_own_setup_is_enough_without_the_saved_list(tmp_path, monkeypatch):
    """The session's own setup is read with its replies; the list of every
    saved Local only chat is a second source, not the only one."""
    from iron_jarvis.providers import egress

    monkeypatch.setattr(egress, "local_only_thread_ids", lambda engine: frozenset())
    engine = _engine(tmp_path)
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
    _assert_left_out(_report(engine))


def test_asking_for_a_local_only_session_reads_nothing_else(tmp_path, monkeypatch):
    """One Local only session asked for: its calls, runs, events and findings
    are not even read (not merely left out of the text)."""
    engine = _engine(tmp_path)
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
    real = FR._rows
    asked: list[str] = []

    def spy(engine_, sql, **params):
        asked.append(sql)
        return real(engine_, sql, **params)

    monkeypatch.setattr(FR, "_rows", spy)
    assert _report(engine, session=LOCAL)["text"] == FR.SCOPE_LOCAL_ONLY
    # (The one read of the event log's Local only stamps is how it knows.)
    for table in ("FROM toolinvocation", "FROM teamfinding", "FROM chatturnrecord", "FROM agentrun",
                  "type = 'autonomy.nudged'", "type = 'tool.denied'"):
        assert not any(table in sql for sql in asked), (table, asked)
    assert any("'$.local_only'" in sql for sql in asked)


def test_a_local_only_sessions_finding_is_left_out_beside_a_reviewed_reply(tmp_path):
    """Another session's reply with a team review is read in the same pass
    as the Local only session's finding (the findings come last)."""
    engine = _engine(tmp_path)
    other = "chat_0be0be0be0be"
    with session_scope(engine) as db:
        _local_thread(db, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
        _local_work(db)
        _local_thread(db, tid=other, lines=[_reply(AT, finish={"review": {
            "state": "failed", "member": "opus-reviewer", "model": "Opus 5.5", "why": "the reviewer crashed"}})])
        db.commit()
    with session_scope(engine) as db:
        rec = db.get(ChatThreadRecord, other)
        rec.title = "Other"
        db.add(rec)
        db.commit()
    out = _report(engine)
    assert "The team review did not run or did not finish" in out["text"]
    assert "R1:" not in out["text"] and "chat_10ca10ca" not in out["text"] and MARK not in out["text"]
    assert LEFT_OUT_ONE in out["text"]


def test_the_same_session_not_local_only_is_read_the_control(tmp_path):
    """The control: the very same records, not Local only, ARE in the report —
    so the test above fails for the right reason."""
    engine = _engine(tmp_path)
    _seed(engine)
    out = _report(engine)
    assert MARK in out["text"]
    assert "chat_10ca10ca" in out["text"]
    assert out["problems"] > 3 and out["sessions"] == 2
    assert "Local only" not in out["text"]
    assert out["local_only_left_out"] == 0


def _held_for_app() -> dict:
    from iron_jarvis.providers import egress

    return {"provider": "custom", "model": "glm-5.3-flash", egress.HOLDS_KEY: ["gmail"]}


def _failed_before_any_call(**extra) -> dict:
    """A reply whose own model server failed before it ran a call: no route,
    no refusal, no call (so no stamp) — its words come from that server."""
    line = _reply(AT, content="", error=f"Your model server stopped answering {MARK}.",
                  errorDetail=f"connection reset by 192.168.1.20:11434 {MARK}", **extra)
    line.pop("route")
    return line


@pytest.mark.parametrize("signal", ["setup", "held_for_app", "route", "refusal", "helper", "event_tool",
                                    "event_denied", "failed_reply_mark", "question_mark"])
def test_every_mark_of_local_only_work_leaves_the_session_out(tmp_path, signal):
    """Each record that says the session ran Local only is enough on its own,
    even when the session is switched off now."""
    engine = _engine(tmp_path)
    plain = {"provider": "custom", "model": "glm-5.3-flash"}
    setup: "dict" = plain
    lines = [_reply(AT)]
    helper_options: dict = {}
    question: dict = {}
    if signal == "failed_reply_mark":
        # The second review's case: the reply failed before any call (its
        # line keeps no route, no call was stamped) and the session was
        # switched off since — its line's own mark is the record.
        lines = [_failed_before_any_call(localOnly=True), _reply(AT + timedelta(minutes=31))]
    elif signal == "question_mark":
        # The same reply rebuilt at a start after a crash (from its frames:
        # the turn's question, written before it started, keeps the mark).
        question = {"localOnly": True}
        lines = [_failed_before_any_call(), _reply(AT + timedelta(minutes=31))]
    elif signal == "setup":
        setup = {**plain, "local_only": True}
    elif signal == "held_for_app":
        setup = _held_for_app()
    elif signal == "route":
        lines = [_reply(AT, route={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})]
    elif signal == "refusal":
        lines = [_reply(AT, content="", error="Claude Opus is not on this network.", errorKind="local_only")]
    elif signal == "helper":
        helper_options = {"local_only": True}
    _seed_done_means(engine)
    with session_scope(engine) as db:
        if signal == "refusal":
            # The refused reply kept no route; the next one ran (switched off).
            lines.append(_reply(AT + timedelta(minutes=31)))
        _local_thread(db, setup=setup, lines=lines, question=question)
        _local_work(db, helper_options=helper_options)
        if signal == "event_tool":
            # A reply that FAILED keeps no route: the bus's stamp on its call
            # is the record that it ran Local only.
            _event(db, eid="evt_stamp", type_="tool.executed", sid=f"build:{LOCAL}",
                   payload={"tool": "shell", "ok": False, "invocation_id": f"tool_{LOCAL[-6:]}_1",
                            "local_only": True}, at=AT + timedelta(minutes=2))
        if signal == "event_denied":
            _event(db, eid="evt_stamp2", type_="tool.denied", sid=f"build:{LOCAL}~try1",
                   payload={"tool": "web_fetch", "kind": "local only", "invocation_id": "tool_x",
                            "local_only": True}, at=AT + timedelta(minutes=2))
        db.commit()
    _assert_left_out(_report(engine))


def test_a_reply_that_failed_before_any_call_with_no_mark_is_read_the_control(tmp_path):
    """The control for the two line marks above: the same failed reply in a
    session that never ran Local only is read, its server's words included —
    so those cases fail for the right reason."""
    engine = _engine(tmp_path)
    _seed(engine, lines=[_failed_before_any_call(), _reply(AT + timedelta(minutes=31))])
    out = _report(engine)
    assert f"connection reset by 192.168.1.20:11434 {MARK}" in out["text"], out["text"]
    assert "chat_10ca10ca" in out["text"]
    assert out["local_only_left_out"] == 0 and out["complete"] is True


def test_a_local_only_teammate_or_helper_run_is_left_out_on_its_own(tmp_path):
    engine = _engine(tmp_path)
    _seed_done_means(engine)
    member = "session_10camember01"
    with session_scope(engine) as db:
        db.add(Session(
            id=member, origin=MISSION_MEMBER_ORIGIN, agent_name="custom:writer", provider="custom",
            model="glm-5.3-flash", status=SessionStatus.FAILED, trust="full",
            summary=f"The run failed: {MARK} could not be read.",
            options_json=json.dumps({"local_only": True}), created_at=AT,
        ))
        _call(db, iid="tool_member_l1", sid=member, tool="read_file", ok=False, verdict=PermissionMode.ALLOW,
              args={"path": f"{MARK}.md"}, output=f"no such file: {MARK}.md", at=AT + timedelta(minutes=1))
        _event(db, eid="evt_member_nudge", type_="autonomy.nudged", sid=member,
               payload={"kind": "text_tool_call", "round": 1, "call": f"<{MARK}>"},
               at=AT + timedelta(minutes=2))
        db.commit()
    out = _report(engine)
    text = out["text"]
    assert MARK not in text and "an objective's teammate" not in text, text
    assert out["problems"] == 3
    assert ("- Left out: 1 helper or teammate run that ran Local only (Local only work never goes into "
            "a fix request)") in text
    assert out["local_only_left_out"] == 1
    # Its call is not counted in any "N of M" either.
    assert "3 of 6 shell calls by this member" in text


def test_a_stamped_text_tool_call_is_left_out_even_when_the_stamps_cannot_be_listed(tmp_path, monkeypatch):
    """The event's own stamp is read too: when the pass over the stamps
    cannot run, a stamped event is still not handed on — and the report says
    what it could not read."""
    engine = _engine(tmp_path)
    _seed_done_means(engine)
    member = "session_10camember02"
    with session_scope(engine) as db:
        db.add(Session(id=member, origin=MISSION_MEMBER_ORIGIN, agent_name="custom:writer", provider="custom",
                       model="glm-5.3-flash", status=SessionStatus.COMPLETED, trust="full",
                       options_json="{}", created_at=AT))
        _event(db, eid="evt_member_nudge2", type_="autonomy.nudged", sid=member,
               payload={"kind": "text_tool_call", "round": 1, "call": f"<{MARK}>", "local_only": True},
               at=AT + timedelta(minutes=2))
        db.commit()
    real = FR._rows

    def flaky(engine_, sql, **params):
        if "'$.local_only'" in sql and "FROM eventrecord" in sql:
            raise RuntimeError("database is locked")
        return real(engine_, sql, **params)

    monkeypatch.setattr(FR, "_rows", flaky)
    out = _report(engine)
    assert MARK not in out["text"], out["text"]
    assert "which work ran Local only" in out["text"]
    assert out["complete"] is False


def test_a_helper_of_a_session_whose_replies_cannot_be_read_still_knows_local_only(tmp_path, monkeypatch):
    """The sessions' replies cannot be read, so a helper's own session is
    looked up in the list of Local only chats; a Local only one is left out."""
    engine = _engine(tmp_path)
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
    real = FR._rows

    def flaky(engine_, sql, **params):
        if "FROM chatthreadrecord" in sql:
            raise RuntimeError("database is locked")
        return real(engine_, sql, **params)

    monkeypatch.setattr(FR, "_rows", flaky)
    out = _report(engine)
    assert MARK not in out["text"], out["text"]
    # The other session's helper is still read (its session is not Local only).
    assert "'head' is not recognized" in out["text"]


def test_a_helper_is_not_known_when_neither_its_session_nor_the_saved_list_can_be_read(tmp_path, monkeypatch):
    """FAIL CLOSED: the sessions' replies cannot be read AND the saved Local
    only list cannot be read. A helper with no Local only flag of its own
    (made before its session was switched to Local only) cannot be known: it
    is left out, said, and the report is incomplete."""
    from iron_jarvis.providers import egress

    engine = _engine(tmp_path)
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
    monkeypatch.setattr(egress, "local_only_thread_ids", lambda engine: None)
    real = FR._rows

    def flaky(engine_, sql, **params):
        if "FROM chatthreadrecord" in sql:
            raise RuntimeError("database is locked")
        return real(engine_, sql, **params)

    monkeypatch.setattr(FR, "_rows", flaky)
    out = _report(engine)
    text = out["text"]
    assert MARK not in text, text
    # Both helpers (this session's and the other one's) are not known; with
    # nothing else left to report, the "Nothing to report" line says so.
    assert out["problems"] == 0, text
    assert "(2 coding sessions or helper runs whose Local only setting could not be read were left out)" in text, text
    assert out["complete"] is False and out["local_only_left_out"] == 2


def test_a_teammate_run_the_event_log_stamped_local_only_is_left_out_on_its_own(tmp_path):
    """A run whose own options carry no Local only flag, but whose call the
    bus stamped Local only (a session seen under the scope): the one pass over
    the stamps is enough, and its calls are in no "N of M"."""
    engine = _engine(tmp_path)
    _seed_done_means(engine)
    member = "session_10camember04"
    with session_scope(engine) as db:
        # The same member and model as the other session's helper, so its
        # shell call that WORKED would read "3 of 7" if it were counted.
        db.add(Session(id=member, origin=MISSION_MEMBER_ORIGIN, agent_name="custom:glm-worker", provider="custom",
                       model="glm-5.3-flash", status=SessionStatus.COMPLETED, trust="full",
                       options_json="{}", created_at=AT))
        _call(db, iid="tool_member_l4", sid=member, tool="read_file", ok=False, verdict=PermissionMode.ALLOW,
              args={"path": f"{MARK}.md"}, output=f"no such file: {MARK}.md", at=AT + timedelta(minutes=1))
        _call(db, iid="tool_member_ok4", sid=member, tool="shell", ok=True, args={"command": "git status"},
              output="ok", at=AT + timedelta(minutes=2))
        _event(db, eid="evt_member_stamp", type_="tool.executed", sid=member,
               payload={"tool": "read_file", "ok": False, "invocation_id": "tool_member_l4", "local_only": True},
               at=AT + timedelta(minutes=1))
        db.commit()
    out = _report(engine)
    text = out["text"]
    assert MARK not in text, text
    assert ("- Left out: 1 helper or teammate run that ran Local only (Local only work never goes into "
            "a fix request)") in text, text
    assert "3 of 6 shell calls by this member failed this way" in text, text
    assert out["local_only_left_out"] == 1 and out["complete"] is True


def test_a_failed_reply_is_not_known_when_the_stamps_cannot_be_listed(tmp_path, monkeypatch):
    """A reply that FAILED keeps no route, so the bus's stamp is the one
    record that it ran Local only. When the stamps cannot be listed, a session
    with such a reply (and nothing else saying Local only) is not known: left
    out whole, said, and the report is incomplete."""
    engine = _engine(tmp_path)
    failed = _reply(AT, content="", error="The model dropped mid-answer.")
    failed.pop("route")
    _seed(engine, lines=[failed])
    # The control: with the stamps listed and none on it, the session is read.
    assert MARK in _report(engine)["text"]
    real = FR._rows

    def flaky(engine_, sql, **params):
        if "'$.local_only'" in sql and "FROM eventrecord" in sql:
            raise RuntimeError("database is locked")
        return real(engine_, sql, **params)

    monkeypatch.setattr(FR, "_rows", flaky)
    out = _report(engine)
    text = out["text"]
    assert MARK not in text and "chat_10ca10ca" not in text, text
    assert "- Left out: 1 coding session or helper run whose Local only setting could not be read" in text, text
    assert "which work ran Local only" in text
    # None of its calls (nor its helper's) is counted in an "N of M".
    assert "3 of 6 shell calls by this member failed this way" in text, text
    assert "1 of 1 delegate calls by this lead failed this way" in text, text
    assert out["complete"] is False and out["local_only_left_out"] == 1
    # The other session's reply kept its route: it is read as before.
    assert "'head' is not recognized" in text
    asked = _report(engine, session=LOCAL)
    assert asked["text"] == FR.SCOPE_LOCAL_ONLY_UNKNOWN and asked["complete"] is False


# --------------------------------------------------------------------------- #
# one session asked for, and fail closed
# --------------------------------------------------------------------------- #


def test_asking_for_a_local_only_session_says_so_and_names_nothing(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
    out = _report(engine, session=LOCAL)
    assert out["text"] == FR.SCOPE_LOCAL_ONLY
    assert MARK not in out["text"] and "Client ledger" not in out["text"]
    assert out["problems"] == 0 and out["listed"] == 0 and out["sessions"] == 0
    assert out["local_only_left_out"] == 1 and out["complete"] is True
    assert _report(engine, session=f"build:{LOCAL}")["text"] == FR.SCOPE_LOCAL_ONLY
    # The other session asked for alone is read as before.
    assert _report(engine, session=THREAD)["problems"] == 3


def test_a_helper_that_ran_local_only_makes_its_session_local_only_when_asked_alone(tmp_path):
    engine = _engine(tmp_path)
    _seed_done_means(engine)
    with session_scope(engine) as db:
        _local_thread(db)
        _local_work(db, helper_options={"local_only": True})
        db.commit()
    assert _report(engine, session=LOCAL)["text"] == FR.SCOPE_LOCAL_ONLY


def test_a_setting_that_cannot_be_read_is_left_out_and_said(tmp_path):
    engine = _engine(tmp_path)
    _seed(engine, setup="{not json")
    member = "session_10camember03"
    with session_scope(engine) as db:
        db.add(Session(id=member, origin=MISSION_MEMBER_ORIGIN, agent_name="custom:writer", provider="custom",
                       model="glm-5.3-flash", status=SessionStatus.FAILED, trust="full",
                       summary=f"The run failed: {MARK}.", options_json="{broken", created_at=AT))
        _call(db, iid="tool_member_l3", sid=member, tool="read_file", ok=False, verdict=PermissionMode.ALLOW,
              args={"path": f"{MARK}.md"}, output=f"no such file: {MARK}.md", at=AT + timedelta(minutes=1))
        db.commit()
    out = _report(engine)
    text = out["text"]
    assert MARK not in text and "chat_10ca10ca" not in text, text
    assert out["problems"] == 3
    assert ("- Left out: 2 coding sessions or helper runs whose Local only setting could not be read") in text
    assert out["complete"] is False and out["local_only_left_out"] == 2
    asked = _report(engine, session=LOCAL)
    assert asked["text"] == FR.SCOPE_LOCAL_ONLY_UNKNOWN and asked["complete"] is False


def test_nothing_else_to_report_still_says_what_was_left_out(tmp_path):
    engine = _engine(tmp_path)
    with session_scope(engine) as db:
        _local_thread(db, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
        _local_work(db)
        db.commit()
    out = _report(engine)
    assert out["problems"] == 0 and MARK not in out["text"]
    assert out["text"] == (
        "Nothing to report: Iron Jarvis ran into no problems in the coding sessions and their model team in "
        "the last 7 days (1 Local only coding session was left out: Local only work never goes into a fix "
        "request)."
    )
    assert out["local_only_left_out"] == 1


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #


def test_the_route_leaves_local_only_work_out(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.setattr(FR, "_now", lambda: NOW)
    app = create_app(str(tmp_path / "home"))
    client, engine = TestClient(app), app.state.d.platform.engine
    _seed(engine, setup={"provider": "custom", "model": "glm-5.3-flash", "local_only": True})
    body = client.get("/team/fix-request", params={"days": 7}).json()
    assert MARK not in body["text"] and body["local_only_left_out"] == 1 and body["problems"] == 3
    one = client.get("/team/fix-request", params={"session": LOCAL})
    assert one.status_code == 200
    assert one.json()["text"] == FR.SCOPE_LOCAL_ONLY and one.json()["problems"] == 0


# --------------------------------------------------------------------------- #
# the record: a Local only turn's own lines say so, whatever its end
# (the second review: a reply that failed before any call kept no record)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("end", ["failed", "stopped", "done"])
def test_the_reply_writer_marks_a_local_only_turns_line_whatever_its_end(end):
    from iron_jarvis.daemon.routes.build import _ReplyWatch

    def line_of(local_only: bool) -> dict:
        w = _ReplyWatch()
        w.local_only = local_only
        if end == "failed":
            w.see("error", json.dumps({"title": "Your model server stopped answering.",
                                       "technical": "connection reset"}))
        elif end == "done":
            w.see("done", json.dumps({"reply": "Done.", "route": {"provider": "custom", "model": "glm"}}))
        else:
            w.steps = [{"name": "shell", "ok": True}]  # stopped after a call: a line is kept
        [line] = [m for m in w.lines("turn_x", end) if m.get("role") == "assistant"]
        return line

    marked = line_of(True)
    assert marked["localOnly"] is True, marked
    if end != "done":
        assert not isinstance(marked.get("route"), dict), "the case the mark is for: no route"
    assert "localOnly" not in line_of(False)


async def test_a_local_only_reply_that_failed_before_any_call_is_left_out_after_the_switch_off(tmp_path):
    """The second review's failure, through the REAL turn route and reply
    writer: a Local only session's reply fails before any call (its model
    server stops answering), the user switches Local only off, and "Copy as
    a fix request" still leaves the session out whole — its question and its
    reply line say the turn ran Local only. The control: the same failure in
    a session that never ran Local only is in the report."""
    from datetime import datetime, timezone

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.harness import sessions as S
    from iron_jarvis.providers.adapters.base import ProviderError
    from tests.test_build_sessions_v1367 import messages_of, new_session, open_turn, wire
    from tests.test_harness_gaps_failure_receipt_v1389 import _Failing

    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine

    async def failed_turn(folder_name: str, turn_id: str, marker: str, **extra) -> tuple[str, dict, dict]:
        folder = tmp_path / folder_name
        folder.mkdir()
        sid = new_session(app, folder, **extra)["id"]
        wire(app, _Failing(ProviderError(f"connection reset by 192.168.1.20:11434 {marker}")))
        status, raw = await open_turn(app, sid, {"text": "Fix the ledger", "turn_id": turn_id})
        assert status == 200, raw
        msgs = messages_of(app, sid)
        [question] = [m for m in msgs if m.get("role") == "user" and m.get("turnId") == turn_id]
        [line] = [m for m in msgs if m.get("role") == "assistant" and m.get("turnId") == turn_id]
        assert line.get("error") and not isinstance(line.get("route"), dict), line
        assert line.get("errorKind") != "local_only", "a server that failed, not a refusal"
        return sid, question, line

    local_sid, question, line = await failed_turn("local", "lo-fail-1", MARK, local_only=True)
    assert question["localOnly"] is True and line["localOnly"] is True, (question, line)
    S.update(engine, local_sid, setup={"local_only": None})  # switched off since
    plain_sid, plain_q, plain_line = await failed_turn("plain", "plain-fail-1", "PLAINMARK")
    assert "localOnly" not in plain_q and "localOnly" not in plain_line

    out = FR.build_report(engine, now=datetime.now(timezone.utc), version="1.546.1", os_name="Windows 11")
    text = out["text"]
    assert MARK not in text and FR.short_id(local_sid) not in text, text
    # The control: the plain session's same failure IS read, its words included.
    assert "PLAINMARK" in text and FR.short_id(plain_sid) in text, text
    assert out["local_only_left_out"] == 1 and out["complete"] is True, text
    assert LEFT_OUT_ONE in text, text
    # Asked for alone, the switched-off session says it is Local only.
    asked = FR.build_report(engine, session=local_sid, now=datetime.now(timezone.utc), version="1.546.1",
                            os_name="Windows 11")
    assert asked["text"] == FR.SCOPE_LOCAL_ONLY
