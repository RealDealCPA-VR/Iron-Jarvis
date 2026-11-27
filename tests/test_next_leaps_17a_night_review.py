"""17-a (build-out plan R1.8, idea 17): THE REVIEW RUNS AT NIGHT, AND THE
BELL SAYS WHAT IT FOUND.

One pass of ``improve/night_review.review_pass`` looks at yesterday's work
(from the records only), runs the right suggestion maker for each problem
through the night budget, keeps a record of the night (``nightreview``) and
rings ONE bell row a night when it made something. Through the real app
factory, the real coach, the real budget, the real inbox and the real event
bus; the model is a router that records every call it is sent, and time is
an injected clock (no sleep).

What is pinned, each with the way it would fail:

* a pass does nothing outside the night window, under Emergency stop, while
  a run, a chat reply, a recent chat or a night task is live, and while the
  setting is off or not yet answered (D-17.1: offered once, nothing runs
  until yes); work parked on the owner with no clock (a workflow run waiting
  for an answer, an attended run on its card, a terminal showing its
  question) does not hold it;
* a custom agent that ran out of steps four times yesterday gets exactly ONE
  coach request, pinned to its own model, marked as background work, and the
  suggestion says why it was made;
* a built-in agent with the same trouble is only named in the night's record
  (its instructions are the app's);
* a model that bills per use, an account at 70% of its window and the demo
  model are each refused with their reason, and nothing is sent;
* a Local only run's request is made inside the Local only scope, and a
  model off this network is refused before anything is sent;
* the cap stops the night at 6, and a restart does not reset it;
* two passes ring one notice (claim first; a failed publish gives the claim
  back), and a night that made nothing rings nothing;
* the Improvements page's line and the one-time offer (``GET
  /improvements/night``), and the reason on each item (``GET /improvements``).
"""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.coach.models import CoachProposalRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import (
    AgentRun,
    AgentType,
    ChatThreadRecord,
    EventRecord,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.improve import budget as B
from iron_jarvis.improve import night_review as NR
from iron_jarvis.improve.models import NightReviewRecord
from iron_jarvis.providers import background_call, egress

collect = importlib.import_module("iron_jarvis.inbox.collect")

PROMPT = (
    "You are Research, a careful helper that reads the files in a project folder.\n"
    "Never send what you read to anyone outside this PC.\n"
    "Always cite the file you read a number from.\n"
    "Keep answers short and name the file you changed, and say plainly when a "
    "page was unreadable instead of guessing what it said."
)
AFTER = PROMPT + "\nPlan the steps first and stop to report before the step budget runs out."
#: A skill draft the skill learner accepts (its own SKILL.md format).
SKILL_MD = (
    "---\nname: sort-reports-folder\ndescription: Sort a folder of reports by date.\n---\n\n"
    "# Sort reports\n\n1. List the folder.\n2. Read each report's date.\n"
)


def _local(day: datetime, h: int, mi: int = 0) -> datetime:
    """An aware LOCAL time on ``day`` (the night window is in this PC's own time)."""
    return datetime(day.year, day.month, day.day, h, mi).astimezone()


#: The injected clock: 01:30 TOMORROW, inside the 23:00 to 07:00 window of
#: tonight. Ahead of the real clock on purpose, so a row the app stamps with
#: the real time (a chat it makes, a failure) is never "in the future" of it.
_TOMORROW = datetime.now() + timedelta(days=1)
IN_WINDOW = _local(_TOMORROW, 1, 30)
#: The night it belongs to: the date the window opened (today, at 23:00).
NIGHT = (IN_WINDOW - timedelta(days=1)).date().isoformat()
#: The afternoon of that day: "yesterday" for that night.
YESTERDAY = _local(IN_WINDOW - timedelta(days=1), 14, 0).astimezone(timezone.utc).replace(tzinfo=None)


class Router:
    """The router's ``complete``: records every call it is SENT (provider,
    model, pinned, the Local only scope and the background mark it was made
    in) and answers with a small instructions edit."""

    def __init__(self, text: str = f"```\n{AFTER}\n```") -> None:
        self.text = text
        #: When set, the answer to a skill draft's request (its system
        #: prompt names SKILL.md), so one pass can coach AND draft a skill.
        self.skill_text: str | None = None
        #: When set, called after each request is answered (a chat is used,
        #: the clock moves on).
        self.after_call = None
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools, provider=None, model=None, pin=False, **kw):
        self.calls.append({
            "provider": provider, "model": model, "pin": pin,
            "local_only": egress.current_scope().on, "background": background_call.what(),
        })
        if self.after_call is not None:
            self.after_call()
        text = self.skill_text if self.skill_text is not None and "SKILL.md" in str(system) else self.text
        return SimpleNamespace(response=SimpleNamespace(text=text), provider=provider, model=model)


@pytest.fixture
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    # A model server on this PC (the recording router answers for it).
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434")
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    p.config.night_review = True
    router = Router()
    p.router = router
    B.reset()
    NR.reset()
    s = SimpleNamespace(app=app, d=app.state.d, platform=p, router=router, client=TestClient(app),
                        engine=p.engine, home=p.config.home)
    yield s
    B.reset()
    NR.reset()


def _agent(desk, name: str = "research") -> str:
    desk.platform.agents_registry.register(name, PROMPT, ["read_file", "write_file"], description=name)
    return f"custom:{name}"


def _ran_out_of_steps(desk, roster: str, n: int = 4, *, provider: str = "claude-cli",
                      model: str = "claude-sonnet-4-6", at: datetime = YESTERDAY,
                      options: dict | None = None) -> list[str]:
    """``n`` runs of ``roster`` that used all 10 of their steps and were not
    finished (the coach's "scope-creep")."""
    ids = []
    with session_scope(desk.engine) as db:
        for i in range(n):
            sess = Session(task=f"sort the reports folder ({i})", agent_type=AgentType.BUILDER, agent_name=roster,
                           status=SessionStatus.FAILED, max_steps=10, outcome=None, summary="out of steps",
                           provider=provider, model=model, options_json=json.dumps(options or {}))
            sess.created_at = at + timedelta(minutes=i)
            sess.finished_at = sess.created_at + timedelta(minutes=1)
            db.add(sess)
            run = AgentRun(session_id=sess.id, steps=10, agent_type=AgentType.BUILDER,
                           provider=provider, model=model)
            db.add(run)
            # It did use its tools (so this is not "failed without using a tool").
            db.add(ToolInvocation(session_id=sess.id, agent_run_id=run.id, tool="read_file", ok=True,
                                  output="ok", args_json=json.dumps({"path": f"report-{i}.pdf"})))
            ids.append(sess.id)
        db.commit()
    return ids


def _pass(desk, now: datetime = IN_WINDOW):
    return asyncio.run(NR.review_pass(desk.d, now=now))


def _row(desk, night: str = NIGHT) -> NightReviewRecord | None:
    with session_scope(desk.engine) as db:
        row = db.get(NightReviewRecord, night)
        if row is not None:
            db.expunge(row)
        return row


def _coach_rows(desk) -> list[CoachProposalRecord]:
    with session_scope(desk.engine) as db:
        return list(db.exec(select(CoachProposalRecord)))


def _notices(desk) -> list[dict]:
    with session_scope(desk.engine) as db:
        rows = list(db.exec(select(EventRecord).where(EventRecord.type == EventType.IMPROVEMENT_NIGHT_REVIEW)))
    return [json.loads(r.payload_json or "{}") for r in rows]


def _night_line(desk, now: datetime = IN_WINDOW) -> dict:
    NR.CLOCK = lambda: now
    try:
        res = desk.client.get("/improvements/night")
    finally:
        NR.CLOCK = None
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# 1. when a pass runs at all
# --------------------------------------------------------------------------- #


def test_a_pass_does_nothing_unless_every_condition_holds(desk):
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster)
    cfg = desk.platform.config

    # Not answered yet (D-17.1: offered once, nothing spent before yes), or off.
    for value in (None, False):
        cfg.night_review = value
        assert _pass(desk) is None
    cfg.night_review = True
    # Outside the night window.
    assert _pass(desk, now=_local(IN_WINDOW, 14, 0))["skipped"] == NR.SKIP_WINDOW
    # Emergency stop.
    cfg.autonomy_kill_switch = True
    assert _pass(desk)["skipped"] == NR.SKIP_STOPPED
    cfg.autonomy_kill_switch = False
    # A live run.
    with session_scope(desk.engine) as db:
        live = Session(task="working", agent_type=AgentType.BUILDER, status=SessionStatus.ACTIVE)
        db.add(live)
        db.commit()
        live_id = live.id
    assert _pass(desk)["skipped"] == NR.SKIP_BUSY
    with session_scope(desk.engine) as db:
        row = db.get(Session, live_id)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()
    # A chat turn in the last 30 minutes.
    with session_scope(desk.engine) as db:
        chat = ChatThreadRecord(title="a chat")
        chat.updated_at = IN_WINDOW.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
        db.add(chat)
        db.commit()
        chat_id = chat.id
    assert _pass(desk)["skipped"] == NR.SKIP_RECENT_CHAT
    with session_scope(desk.engine) as db:
        row = db.get(ChatThreadRecord, chat_id)
        row.updated_at = IN_WINDOW.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=45)
        db.add(row)
        db.commit()
    # A night task running.
    from iron_jarvis.harness.night import RUNNING, NightTaskRecord

    with session_scope(desk.engine) as db:
        task = NightTaskRecord(folder=str(desk.home), words="fix it", state=RUNNING)
        db.add(task)
        db.commit()
        task_id = task.id
    assert _pass(desk)["skipped"] == NR.SKIP_NIGHT_TASK
    assert desk.router.calls == [] and _row(desk) is None and _coach_rows(desk) == []
    with session_scope(desk.engine) as db:
        db.delete(db.get(NightTaskRecord, task_id))
        db.commit()
    # Everything holds: now it runs.
    out = _pass(desk)
    assert out["state"] == NR.DONE, out
    assert len(desk.router.calls) == 1


def test_work_parked_on_the_owner_does_not_hold_the_night_review(desk, monkeypatch):
    """The pass waits on work that RUNS BY ITSELF (``activity_snapshot``'s
    ``working``), never on work parked on the owner with no clock: a workflow
    run waiting for an answer, an attended run on its card, a Build terminal
    showing its question. ``busy`` counts those too (an update would cut them
    off), so one card left open overnight held every pass of the night, and
    nothing said why (no row, no line, an OK health row). A workflow run that
    is running and a terminal at work still hold it."""
    from iron_jarvis.workflows.models import WorkflowRunRecord

    _ran_out_of_steps(desk, _agent(desk), 4)
    panes = {"state": "working"}
    monkeypatch.setattr(desk.platform.terminals, "list",
                        lambda: [{"id": "p1", "alive": True, "state": panes["state"], "agent_cli": "claude"}])

    async def scenario():
        with session_scope(desk.engine) as db:
            flow = WorkflowRunRecord(workflow_name="month end", status="running")
            db.add(flow)
            db.add(Session(id="s-card", task="sort the folder", agent_type=AgentType.BUILDER,
                           origin="job:mission", status=SessionStatus.ACTIVE))
            db.commit()
            flow_id = flow.id
        ask, _fut = desk.platform.approvals.request("write_file", {"path": "a.txt"}, session_id="s-card")
        try:
            # A terminal at work holds the pass.
            assert (await NR.review_pass(desk.d, now=IN_WINDOW))["skipped"] == NR.SKIP_BUSY
            panes["state"] = "blocked"
            # A workflow run that is running holds it.
            assert (await NR.review_pass(desk.d, now=IN_WINDOW))["skipped"] == NR.SKIP_BUSY
            assert desk.router.calls == [] and _row(desk) is None
            with session_scope(desk.engine) as db:
                row = db.get(WorkflowRunRecord, flow_id)
                row.status = "waiting"
                db.add(row)
                db.commit()
            # Left: a workflow run waiting for an answer, an attended run on
            # its card and a terminal showing its question. Busy, not working.
            from iron_jarvis.daemon.routes.system import activity_snapshot

            act = await asyncio.to_thread(activity_snapshot, desk.d)
            assert act["busy"] is True and act["working"] is False, act
            return await NR.review_pass(desk.d, now=IN_WINDOW)
        finally:
            desk.platform.approvals.resolve(ask, "deny")

    out = asyncio.run(scenario())
    assert out.get("state") == NR.DONE and out["made"] == 1, out
    assert len(desk.router.calls) == 1
    assert _row(desk).state == NR.DONE


def test_a_chat_from_before_owners_were_kept_holds_the_pass(desk):
    """A thread whose owner column is NULL reads as the user's
    (``core/models.ChatThreadRecord.owner``): a database older than v1.136.0
    got the column added as NULLABLE (``core/db`` adds a missing column that
    way), and its rows read NULL there. In SQL ``owner != 'daemon'`` is NULL
    for such a row, so it must be named."""
    from sqlalchemy import update

    _ran_out_of_steps(desk, _agent(desk), 4)
    # The table as such a database has it: the same columns, none NOT NULL
    # (CREATE TABLE ... AS SELECT keeps no constraints).
    with desk.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE chat_old AS SELECT * FROM chatthreadrecord")
        conn.exec_driver_sql("DROP TABLE chatthreadrecord")
        conn.exec_driver_sql("ALTER TABLE chat_old RENAME TO chatthreadrecord")
    with session_scope(desk.engine) as db:
        old = ChatThreadRecord(title="an older chat")
        old.updated_at = IN_WINDOW.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        db.add(old)
        db.commit()
        old_id = old.id
        db.exec(update(ChatThreadRecord).where(ChatThreadRecord.id == old_id).values(owner=None))
        db.commit()
    with desk.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT owner FROM chatthreadrecord").fetchall() == [(None,)]
    assert _pass(desk)["skipped"] == NR.SKIP_RECENT_CHAT
    assert desk.router.calls == []


def test_a_pass_whose_records_cannot_be_read_reads_the_tally_off_the_loop(desk, monkeypatch):
    """The tally file is read under a lock a worker may hold: never on the
    event loop, the path where the records cannot be read included."""
    import threading

    _ran_out_of_steps(desk, _agent(desk), 4)
    where: list[bool] = []
    real = B.requests_tonight

    def tally(home, night):
        where.append(threading.current_thread() is threading.main_thread())
        return real(home, night)

    def unreadable(*a, **k):
        raise RuntimeError("the records cannot be read")

    monkeypatch.setattr(B, "requests_tonight", tally)
    monkeypatch.setattr(NR, "match", unreadable)
    with pytest.raises(RuntimeError):
        _pass(desk)
    assert where and not any(where), where
    assert _row(desk).state == NR.WAITING


# --------------------------------------------------------------------------- #
# 2. the coach, pinned to the agent's own model
# --------------------------------------------------------------------------- #


def test_a_custom_agent_that_ran_out_of_steps_four_times_gets_one_pinned_request(desk):
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster, 4)
    out = _pass(desk)
    assert out["state"] == NR.DONE and out["made"] == 1, out
    assert desk.router.calls == [{
        "provider": "claude-cli", "model": "claude-sonnet-4-6", "pin": True,
        "local_only": False, "background": "night_review",
    }]
    (made,) = _coach_rows(desk)
    assert made.agent == roster and made.after.strip() == AFTER.strip()
    row = _row(desk)
    assert row.state == NR.DONE and row.requests == 1
    why = "Your agent research ran out of steps 4 times yesterday."
    assert json.loads(row.made_json) == [{"kind": "coach", "ref": made.id, "why": why}]
    ran = json.loads(row.ran_json)
    assert {"proposer": "coach", "target": roster, "result": "made", "reason": ""} in [
        {k: r[k] for k in ("proposer", "target", "result", "reason")} for r in ran]

    # The suggestion says why it was made, on the inbox's own item.
    inbox = desk.client.get("/improvements").json()
    (item,) = [i for i in inbox["items"] if i["kind"] == "coach"]
    assert item["extra"]["night"] == {"night": NIGHT, "why": why}

    # The bell's one row (no suggestion's words), and the page's line.
    (notice,) = _notices(desk)
    assert notice["night"] == NIGHT and notice["made"] == 1 and notice["by_kind"] == {"coach": 1}
    assert notice["title"] == "1 suggestion from yesterday's review"
    assert AFTER not in json.dumps(notice) and "research" not in json.dumps(notice)
    line = _night_line(desk)
    assert line["offer"] is None
    assert line["night"]["night"] == NIGHT and line["night"]["waiting"] == 1
    assert line["night"]["line"] == "From last night's review: 1"

    # The same night never runs twice, and never rings twice.
    again = _pass(desk)
    assert again["skipped"] == NR.SKIP_DONE
    assert len(desk.router.calls) == 1 and len(_notices(desk)) == 1 and len(_coach_rows(desk)) == 1


def test_a_built_in_agent_is_only_named_in_the_record(desk):
    _ran_out_of_steps(desk, "builder", 4)
    out = _pass(desk)
    assert out["made"] == 0 and desk.router.calls == []
    ran = json.loads(_row(desk).ran_json)
    (named,) = [r for r in ran if r["proposer"] == "coach"]
    assert named["target"] == "builder" and named["result"] == NR.NAMED
    assert named["reason"] == NR.WHY_BUILT_IN
    # Nothing was made: nothing rings, and the page shows no line.
    assert _notices(desk) == []
    assert _night_line(desk)["night"] is None


# --------------------------------------------------------------------------- #
# 3. the budget refuses, and nothing is sent
# --------------------------------------------------------------------------- #


class FakeProxy:
    enabled = True

    def __init__(self, used: float) -> None:
        self.used = used

    def cached_accounts(self):
        return (
            [{"id": "acc_work", "title": "Work Max", "provider": "anthropic", "lane": "cli", "order": 0}],
            {"acc_work": {"status": "ready", "windows": [{"usedPercent": self.used, "windowMins": 300}]}},
        )


@pytest.mark.parametrize("case", ["pay", "account", "demo"])
def test_each_refusal_sends_nothing_and_says_why(desk, case):
    roster = _agent(desk)
    if case == "pay":
        _ran_out_of_steps(desk, roster, provider="openai", model="gpt-5")
    elif case == "account":
        desk.platform.iron_proxy = FakeProxy(82)
        _ran_out_of_steps(desk, roster, options={"account": "acc_work"})
    else:
        _ran_out_of_steps(desk, roster, provider="mock", model="")
    out = _pass(desk)
    assert desk.router.calls == [] and _coach_rows(desk) == [] and out["made"] == 0
    row = _row(desk)
    (ran,) = [r for r in json.loads(row.ran_json) if r["proposer"] == "coach"]
    assert ran["result"] == NR.REFUSED
    want = {
        "pay": "OpenAI bills per use, and the night review never spends money",
        "account": "your Claude Code account was at 82% of its window",
        "demo": B.WHY_DEMO,
    }[case]
    assert ran["reason"] == want
    assert row.state == NR.SKIPPED and row.skipped_why == want and row.requests == 0
    assert _notices(desk) == []
    # The page's line shows a skip only when it needs you (master plan R1.8).
    night = _night_line(desk)["night"]
    if case == "account":
        assert night is None
    else:
        assert night["line"] == f"Skipped: {want[0].upper()}{want[1:]}."


def test_a_night_that_asked_a_model_is_done_even_when_another_agent_was_refused(desk):
    """The record's contract (``improve/models``): skipped = it made nothing
    because EVERY request it would have sent was refused. One agent refused
    (its runs were on a model that bills per use) and one reviewed on a plan
    model that found nothing small enough to suggest is a night that RAN:
    done, no "Skipped:" line, and the record still says why the first one was
    not reviewed."""
    alpha, bravo = _agent(desk, "alpha"), _agent(desk, "bravo")
    _ran_out_of_steps(desk, alpha, provider="openai", model="gpt-5")
    _ran_out_of_steps(desk, bravo)
    desk.router.text = "no change"  # the coach refuses its own draft
    out = _pass(desk)
    assert [(c["provider"], c["pin"]) for c in desk.router.calls] == [("claude-cli", True)]
    assert out["state"] == NR.DONE and out["made"] == 0 and out["requests"] == 1, out
    row = _row(desk)
    assert (row.state, row.skipped_why, row.needs_you) == (NR.DONE, "", False)
    coach = {r["target"]: r for r in json.loads(row.ran_json) if r["proposer"] == "coach"}
    assert coach[alpha]["result"] == NR.REFUSED
    assert coach[alpha]["reason"] == "OpenAI bills per use, and the night review never spends money"
    assert coach[bravo]["result"] == NR.NOTHING
    assert _night_line(desk)["night"] is None
    assert _notices(desk) == []


PAY = "OpenAI bills per use, and the night review never spends money"


@pytest.mark.parametrize("ran, want", [
    # Every request it would have sent was refused: skipped, and it needs you.
    ([{"proposer": "tool_failures", "result": NR.NOTHING, "reason": ""},
      {"proposer": "coach", "result": NR.REFUSED, "reason": PAY}], (NR.SKIPPED, PAY, True)),
    # A built-in agent only named sends no request: still every request refused.
    ([{"proposer": "coach", "result": NR.NAMED, "reason": NR.WHY_BUILT_IN},
      {"proposer": "skill", "result": NR.REFUSED, "reason": PAY}], (NR.SKIPPED, PAY, True)),
    # A suggestion maker that is not there sends no request either.
    ([{"proposer": "coach", "result": NR.NOTHING, "reason": NR.WHY_NO_COACH},
      {"proposer": "skill", "result": NR.REFUSED, "reason": PAY}], (NR.SKIPPED, PAY, True)),
    # One refused, one asked: the night ran.
    ([{"proposer": "coach", "result": NR.REFUSED, "reason": PAY},
      {"proposer": "skill", "result": NR.NOTHING, "reason": NR.WHY_TIMEOUT}], (NR.DONE, "", False)),
    ([{"proposer": "skill", "result": NR.REFUSED, "reason": PAY},
      {"proposer": "coach", "result": NR.NOTHING, "reason": "the coach had nothing small enough to say"}],
     (NR.DONE, "", False)),
    # Nothing would have asked a model at all: done, nothing to say.
    ([{"proposer": "tool_failures", "result": NR.NOTHING, "reason": ""}], (NR.DONE, "", False)),
], ids=["all refused", "named + refused", "no coach + refused", "refused + timed out", "refused + coached",
        "scan only"])
def test_skipped_means_every_request_it_would_have_sent_was_refused(ran, want):
    assert NR._finish(ran, []) == want
    assert NR._finish(ran, [{"kind": "coach", "ref": "c1", "why": "x"}]) == (NR.DONE, "", False)


SIGNED_OUT = "Work Max is signed out"


@pytest.mark.parametrize("maker", ["coach", "skill"])
def test_an_account_that_cannot_be_used_counts_no_request(desk, monkeypatch, maker):
    """The work's own account is held BEFORE the night's tally counts a
    request: one Iron-Proxy will not hand over (after the budget passed it)
    sends nothing, counts nothing, and says Iron-Proxy's own reason."""
    from iron_jarvis.iron_proxy import choice

    desk.platform.iron_proxy = FakeProxy(10)  # well under 70%: the budget passes it
    monkeypatch.setattr(choice, "pin_for", lambda *a, **k: (None, SIGNED_OUT))
    if maker == "coach":
        _ran_out_of_steps(desk, _agent(desk), 4, options={"account": "acc_work"})
    else:
        (sid,) = _ran_out_of_steps(desk, "builder", 1, options={"account": "acc_work"})
        _skill_candidate(desk, sid, "sort the reports folder")
    _pass(desk)
    assert desk.router.calls == []
    assert B.requests_tonight(desk.home, NIGHT) == 0 and _row(desk).requests == 0
    (ran,) = [r for r in json.loads(_row(desk).ran_json) if r["proposer"] == maker]
    assert ran["reason"] == SIGNED_OUT, ran


def test_a_local_only_run_is_reviewed_only_inside_the_scope(desk):
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster, provider="ollama", model="qwen3:8b", options={"local_only": True})
    assert _pass(desk)["made"] == 1
    (call,) = desk.router.calls
    assert call["provider"] == "ollama" and call["local_only"] is True


def test_older_local_only_evidence_is_refused_before_a_request_is_counted(desk):
    """The coach's evidence reaches past yesterday (its own recent runs): a
    Local only run from before yesterday makes the request Local only even
    though yesterday's runs were not. The budget hears that reading BEFORE it
    counts a request, so an off-network model is refused, nothing is sent and
    the night's tally stays at 0."""
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster, 1, at=YESTERDAY - timedelta(days=2), options={"local_only": True})
    _ran_out_of_steps(desk, roster, 4)
    _pass(desk)
    assert desk.router.calls == []
    assert B.requests_tonight(desk.home, NIGHT) == 0 and _row(desk).requests == 0
    (ran,) = [r for r in json.loads(_row(desk).ran_json) if r["proposer"] == "coach"]
    assert ran["result"] == NR.REFUSED and ran["reason"].startswith("this work was Local only"), ran


def test_a_local_only_run_with_a_model_off_this_network_sends_nothing(desk):
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster, options={"local_only": True})
    _pass(desk)
    assert desk.router.calls == []
    (ran,) = [r for r in json.loads(_row(desk).ran_json) if r["proposer"] == "coach"]
    assert ran["result"] == NR.REFUSED and ran["reason"].startswith("this work was Local only")


def test_without_the_budget_s_check_the_call_still_carries_the_scope(desk, monkeypatch):
    """Two layers: the budget refuses an off-network model for Local only
    work up front, AND the request is made inside ``local_only_scope(True)``,
    the scope the router's own gate refuses on (``providers/egress``). With
    the budget's check taken away, the call reaches the router inside the
    scope, so the router's gate still holds."""
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster, options={"local_only": True})
    monkeypatch.setattr(B, "_local_only_refusal", lambda *a, **k: "")
    _pass(desk)
    (call,) = desk.router.calls
    assert call["provider"] == "claude-cli" and call["local_only"] is True


# --------------------------------------------------------------------------- #
# 4. the cap
# --------------------------------------------------------------------------- #


def test_the_cap_stops_the_night_at_six(desk):
    for i in range(7):
        _ran_out_of_steps(desk, _agent(desk, f"agent{i}"), 3, at=YESTERDAY + timedelta(minutes=10 * i))
    out = _pass(desk)
    assert len(desk.router.calls) == 6
    assert out["made"] == 6
    row = _row(desk)
    assert row.requests == 6
    refused = [r for r in json.loads(row.ran_json) if r["result"] == NR.REFUSED]
    assert [r["reason"] for r in refused] == ["tonight's limit of 6 requests was reached"]


def test_the_cap_survives_a_restart(desk):
    _ran_out_of_steps(desk, _agent(desk), 4)
    desk.platform.config.night_review_requests = 2
    B.count(desk.home, NIGHT)
    B.count(desk.home, NIGHT)
    B.reset()  # a restart: this process forgets, the file does not
    out = _pass(desk)
    assert desk.router.calls == [] and out["made"] == 0
    assert _row(desk).skipped_why == "tonight's limit of 2 requests was reached"


# --------------------------------------------------------------------------- #
# 5. one notice
# --------------------------------------------------------------------------- #


def test_two_passes_ring_one_notice_and_a_failed_publish_is_tried_again(desk, monkeypatch):
    _ran_out_of_steps(desk, _agent(desk), 4)
    bus = desk.platform.event_bus
    real = bus.publish
    fails = {"n": 1}

    async def flaky(etype, payload=None, **kw):
        if etype == EventType.IMPROVEMENT_NIGHT_REVIEW and fails["n"]:
            fails["n"] -= 1
            raise RuntimeError("the bus hiccuped")
        return await real(etype, payload, **kw)

    monkeypatch.setattr(bus, "publish", flaky)
    _pass(desk)
    assert _notices(desk) == [] and _row(desk).notified_at is None  # given back
    _pass(desk)
    _pass(desk)
    assert len(_notices(desk)) == 1 and _row(desk).notified_at is not None
    assert len(desk.router.calls) == 1


def test_a_notice_still_owed_is_rung_the_next_morning(desk, monkeypatch):
    _ran_out_of_steps(desk, _agent(desk), 4)
    bus = desk.platform.event_bus
    real = bus.publish

    async def down(etype, payload=None, **kw):
        if etype == EventType.IMPROVEMENT_NIGHT_REVIEW:
            raise RuntimeError("the bus is down")
        return await real(etype, payload, **kw)

    monkeypatch.setattr(bus, "publish", down)
    _pass(desk)
    assert _notices(desk) == []
    monkeypatch.setattr(bus, "publish", real)
    # 08:00, the window closed: the owed notice rings, once.
    assert _pass(desk, now=_local(IN_WINDOW, 8, 0))["skipped"] == NR.SKIP_WINDOW
    assert _pass(desk, now=_local(IN_WINDOW, 8, 5))["skipped"] == NR.SKIP_WINDOW
    assert len(_notices(desk)) == 1


def _chat_used(desk, at: datetime) -> None:
    """A chat used five minutes before ``at`` (the PC is not idle)."""
    with session_scope(desk.engine) as db:
        chat = ChatThreadRecord(title="a chat")
        chat.updated_at = at.astimezone(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        db.add(chat)
        db.commit()


def test_a_review_cut_off_by_a_busy_pc_ends_when_the_window_closes_and_rings_once(desk):
    """A pass that made something and was cut off (a chat was used) while the
    PC stayed busy until the window closed: the first look after the window
    closes ends the night with what it ran (the cut-off said in its record),
    and rings its ONE bell row. The rest of that night's work is not run."""
    for i in range(2):
        _ran_out_of_steps(desk, _agent(desk, f"agent{i}"), 4, at=YESTERDAY + timedelta(minutes=10 * i))
    desk.router.after_call = lambda: _chat_used(desk, IN_WINDOW)
    first = _pass(desk)
    assert first["stopped"] == NR.SKIP_RECENT_CHAT and first["made"] == 1 and first["requests"] == 1, first
    assert _row(desk).state == NR.WAITING and _notices(desk) == []
    # 08:00, the window closed: the night ends here, and rings once.
    assert _pass(desk, now=_local(IN_WINDOW, 8, 0))["skipped"] == NR.SKIP_WINDOW
    row = _row(desk)
    assert row.state == NR.DONE and row.ended_at is not None and row.requests == 1
    ran = json.loads(row.ran_json)
    assert [(r["proposer"], r["result"]) for r in ran] == [
        ("tool_failures", NR.NOTHING), ("coach", NR.MADE), ("night", NR.CUT_OFF)], ran
    assert ran[-1]["reason"] == f"{NR.WHY_CUT_OFF} ({NR.SKIP_RECENT_CHAT})"
    (notice,) = _notices(desk)
    assert notice["night"] == NIGHT and notice["made"] == 1
    _pass(desk, now=_local(IN_WINDOW, 8, 5))
    assert len(_notices(desk)) == 1 and len(desk.router.calls) == 1
    assert _night_line(desk, now=_local(IN_WINDOW, 10, 0))["night"]["line"] == "From last night's review: 1"
    # The next night's review runs on its own night, untouched by the last.
    assert _pass(desk, now=IN_WINDOW + timedelta(days=1))["state"] == NR.DONE
    assert _row(desk).state == NR.DONE and len(_notices(desk)) == 1


def test_a_pass_stops_when_the_window_closes_and_the_next_look_ends_it(desk):
    """The window is read again before each suggestion maker, on the clock
    (never only once at the start): a pass begun at 06:58 that reaches 07:01
    runs nothing more, and the next look ends the night and rings."""
    for i in range(2):
        _ran_out_of_steps(desk, _agent(desk, f"agent{i}"), 4, at=YESTERDAY + timedelta(minutes=10 * i))
    clock = {"at": _local(IN_WINDOW, 6, 58)}
    NR.CLOCK = lambda: clock["at"]

    def later() -> None:
        clock["at"] = _local(IN_WINDOW, 7, 1)

    desk.router.after_call = later
    first = asyncio.run(NR.review_pass(desk.d))
    assert first["stopped"] == NR.SKIP_WINDOW and first["made"] == 1, first
    assert len(desk.router.calls) == 1 and _row(desk).state == NR.WAITING
    clock["at"] = _local(IN_WINDOW, 7, 5)
    assert asyncio.run(NR.review_pass(desk.d))["skipped"] == NR.SKIP_WINDOW
    assert _row(desk).state == NR.DONE and len(_notices(desk)) == 1
    assert len(desk.router.calls) == 1


def test_a_night_cut_off_before_the_scan_leaves_the_day_to_the_daily_scan(desk):
    """A night that ended without running the tool-failure scan (cut off
    before it) did not cover the day: the daily scan still runs it."""
    with session_scope(desk.engine) as db:
        db.add(NightReviewRecord(night=NIGHT, state=NR.WAITING, skipped_why=NR.SKIP_RECENT_CHAT))
        db.commit()
    assert _pass(desk, now=_local(IN_WINDOW, 8, 0))["skipped"] == NR.SKIP_WINDOW
    row = _row(desk)
    assert row.state == NR.DONE and [r["result"] for r in json.loads(row.ran_json)] == [NR.CUT_OFF]
    assert _notices(desk) == []
    assert NR.reviewed_within(desk.engine, hours=24) is False


def test_a_night_cut_off_after_a_refusal_is_done_not_skipped(desk):
    """A night the window closed on never reached its last jobs, so it cannot
    say EVERY request it would have sent was refused: one agent refused, then
    a busy PC until morning, ends ``done`` with no "Skipped:" line, and its
    record still holds the refusal and the cut-off."""
    ran = [
        {"proposer": "tool_failures", "target": "", "result": NR.NOTHING, "reason": "", "key": "tool_failures"},
        {"proposer": "coach", "target": "custom:alpha", "result": NR.REFUSED, "reason": PAY,
         "key": "coach:custom:alpha"},
    ]
    with session_scope(desk.engine) as db:
        db.add(NightReviewRecord(night=NIGHT, state=NR.WAITING, ran_json=json.dumps(ran),
                                 skipped_why=NR.SKIP_RECENT_CHAT))
        db.commit()
    assert _pass(desk, now=_local(IN_WINDOW, 8, 0))["skipped"] == NR.SKIP_WINDOW
    row = _row(desk)
    assert (row.state, row.skipped_why, row.needs_you) == (NR.DONE, "", False)
    got = json.loads(row.ran_json)
    assert [(r["proposer"], r["result"]) for r in got] == [
        ("tool_failures", NR.NOTHING), ("coach", NR.REFUSED), ("night", NR.CUT_OFF)], got
    assert got[1]["reason"] == PAY
    assert _night_line(desk, now=_local(IN_WINDOW, 10, 0))["night"] is None
    assert _notices(desk) == [] and desk.router.calls == []


def test_the_notice_switch_off_rings_nothing(desk):
    desk.platform.config.night_review_notice = False
    _ran_out_of_steps(desk, _agent(desk), 4)
    assert _pass(desk)["made"] == 1
    _pass(desk, now=_local(IN_WINDOW, 8, 0))
    assert _notices(desk) == [] and _row(desk).notified_at is not None
    # The suggestion still waits, and the page still says so.
    assert _night_line(desk)["night"]["waiting"] == 1


def test_the_claim_is_a_compare_and_set_and_a_told_night_is_not_owed(desk):
    """Each of the two guards on its own: a second claim (another pass, or
    another process on the same home) loses, and a night already told is not
    owed again even before anyone claims."""
    _ran_out_of_steps(desk, _agent(desk), 4)
    desk.platform.config.night_review_notice = False  # the pass claims, rings nothing
    _pass(desk)
    assert _row(desk).notified_at is not None
    assert NR._to_tell(desk.engine, NIGHT) is None
    assert NR._claim(desk.engine, NIGHT) is False
    NR._release(desk.engine, NIGHT)
    assert NR._to_tell(desk.engine, NIGHT) == json.loads(_row(desk).made_json)
    assert NR._claim(desk.engine, NIGHT) is True
    assert NR._claim(desk.engine, NIGHT) is False


def _bell_types() -> set[str]:
    """The event types the dashboard's bell turns into a row
    (``NotificationBell.BELL_ACTIVITY_TYPES``), read from its own source."""
    from pathlib import Path
    import re

    src = (Path(__file__).resolve().parents[1] / "dashboard" / "components" / "NotificationBell.tsx").read_text(
        encoding="utf-8").replace("\r\n", "\n")
    block = re.search(r"export const BELL_ACTIVITY_TYPES: readonly string\[\] = \[\n(.*?)\n\];", src, re.S)
    assert block, "BELL_ACTIVITY_TYPES moved"
    types = set(re.findall(r'^\s*"([a-z_]+\.[a-z_.]+)",', block.group(1), re.M))
    assert {"coach.proposal", "improvement.night_review", "session.completed"} <= types
    return types


def _user_skill(desk, name: str = "sort-reports-folder") -> str:
    from iron_jarvis.skills.loader import save_skill, slugify

    p = desk.platform
    if not (p.config.home / "skills" / slugify(name)).exists():
        save_skill(p.config.home / "skills", name, "Sort a folder of reports.", "1. List the folder.")
        p.skills.repopulate(p.config.home, None)
    return name


def _skill_candidate(desk, sid: str, task: str, *, at: datetime | None = None,
                     skill: str = "sort-reports-folder") -> str:
    """A waiting skill draft of the run lane: an IMPROVEMENT to a skill the
    owner has (the intelligence plan, release 10: a NEW skill comes only
    from the same kind of task on three days, the ``repeat`` row's)."""
    from iron_jarvis.skills.learning_models import SkillCandidateRecord

    _user_skill(desk, skill)
    with session_scope(desk.engine) as db:
        cand = SkillCandidateRecord(session_id=sid, task=task, kind="refine", skill_name=skill,
                                    signature=f"refine::{skill}")
        if at is not None:
            cand.created_at = at
        db.add(cand)
        db.commit()
        return cand.id


@pytest.mark.parametrize("notice", [True, False], ids=["notice on", "notice off"])
def test_one_pass_rings_one_bell_row_and_nothing_reaches_a_phone(desk, monkeypatch, notice):
    """The cap is ONE bell row a night (the master plan's table of notices),
    and its stop answer stops it all: the suggestion makers the review runs
    publish nothing of their own. ``coach.proposal`` is a bell row that
    pings, ``skill.proposal_created`` a default phone alert (D-17.3: nothing
    from the night review reaches a phone in 17-a)."""
    from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS

    desk.platform.config.night_review_notice = notice
    for i in range(3):
        _ran_out_of_steps(desk, _agent(desk, f"agent{i}"), 4, at=YESTERDAY + timedelta(minutes=10 * i))
    (sid,) = _ran_out_of_steps(desk, "builder", 1, at=YESTERDAY + timedelta(hours=1))
    _skill_candidate(desk, sid, "sort the reports folder")
    desk.router.skill_text = SKILL_MD
    bus = desk.platform.event_bus
    real = bus.publish
    published: list[str] = []

    def record(etype, payload=None, **kw):
        # Called when the publish is MADE (a fire-and-forget task included).
        published.append(str(etype))
        return real(etype, payload, **kw)

    monkeypatch.setattr(bus, "publish", record)
    out = _pass(desk)
    assert out["made"] == 4 and len(desk.router.calls) == 4, out
    kinds = sorted(m["kind"] for m in json.loads(_row(desk).made_json))
    assert kinds == ["coach", "coach", "coach", "skill"]
    rings = [t for t in published if t in _bell_types() or t in DEFAULT_ALERT_EVENTS]
    assert rings == (["improvement.night_review"] if notice else []), published
    assert not [t for t in published if t in DEFAULT_ALERT_EVENTS], published
    # The next morning nothing more rings either.
    _pass(desk, now=_local(IN_WINDOW, 8, 0))
    assert [t for t in published if t in _bell_types() or t in DEFAULT_ALERT_EVENTS] == rings


def test_three_suggestions_are_one_row_in_the_bell_words():
    assert NR.notice_title(3) == "3 suggestions from yesterday's review"
    assert NR.notice_title(1) == "1 suggestion from yesterday's review"


# --------------------------------------------------------------------------- #
# 6. the tool-failure scan and skill drafts
# --------------------------------------------------------------------------- #


def _web_fetch_failed(desk, n: int = 3) -> None:
    """``n`` failed web fetches this week. The scan reads the week before the
    REAL clock (its own rule)."""
    from iron_jarvis.core.ids import utcnow

    real = utcnow().replace(tzinfo=None)
    with session_scope(desk.engine) as db:
        for i in range(n):
            db.add(ToolInvocation(session_id="chat", agent_run_id="r", tool="web_fetch", ok=False,
                                  output="boom", created_at=real - timedelta(hours=3, minutes=i)))
        db.commit()


def test_the_tool_failure_scan_runs_in_the_review_and_says_why(desk):
    _web_fetch_failed(desk)
    out = _pass(desk)
    assert out["made"] == 1 and desk.router.calls == []
    (made,) = json.loads(_row(desk).made_json)
    assert made["kind"] == "wants" and made["why"] == "Web fetch failed 3 times in the last week."
    (notice,) = _notices(desk)
    assert notice["by_kind"] == {"wants": 1}
    # The day's own scan then leaves that day alone.
    app = importlib.import_module("iron_jarvis.daemon.app")
    assert NR.reviewed_within(desk.engine, hours=24) is True
    ticks: list = []
    assert asyncio.run(app._tool_failure_pass(desk.platform, lambda *a: ticks.append(a))) == []
    assert ticks == [("tool_failures", True)]


@pytest.mark.parametrize("answer", ["rejected", "approved", "pending"])
def test_a_tool_failure_suggestion_made_earlier_this_week_is_not_counted_again(desk, answer):
    """The scan hands back the id of a suggestion made (or answered) earlier
    this week for the same failures (``_mint_failure_proposal``: one per week
    of failures). Only one THIS scan made is the night's: one already
    answered, or still waiting from the daily scan, gives made 0, rings
    nothing and puts no line on Improvements."""
    from iron_jarvis.motivation.models import ProposalRecord

    _web_fetch_failed(desk)
    (found,) = desk.platform.improvement.scan_tool_failures()
    with session_scope(desk.engine) as db:
        rec = db.get(ProposalRecord, found["proposal_id"])
        rec.status = answer
        db.add(rec)
        db.commit()
    out = _pass(desk)
    assert out["state"] == NR.DONE and out["made"] == 0, out
    row = _row(desk)
    assert json.loads(row.made_json) == []
    (scan,) = [r for r in json.loads(row.ran_json) if r["proposer"] == "tool_failures"]
    assert scan["result"] == NR.NOTHING
    assert _notices(desk) == [] and _night_line(desk)["night"] is None
    _pass(desk, now=_local(IN_WINDOW, 8, 0))
    assert _notices(desk) == []
    with session_scope(desk.engine) as db:
        assert len(list(db.exec(select(ProposalRecord)))) == 1


def test_a_waiting_skill_draft_is_written_on_the_run_s_own_model(desk):
    from iron_jarvis.skills.learning_models import SkillCandidateRecord, SkillProposalRecord

    (sid,) = _ran_out_of_steps(desk, "builder", 1, provider="claude-cli", model="claude-haiku-4-5")
    _skill_candidate(desk, sid, "sort the reports folder")
    assert SkillCandidateRecord  # the run lane's waiting draft, made above
    desk.router.text = SKILL_MD
    out = _pass(desk)
    calls = [c for c in desk.router.calls]
    assert [(c["provider"], c["model"], c["pin"], c["background"]) for c in calls] == [
        ("claude-cli", "claude-haiku-4-5", True, "night_review")]
    with session_scope(desk.engine) as db:
        props = list(db.exec(select(SkillProposalRecord)))
    assert len(props) == 1 and out["made"] == 1
    (made,) = [m for m in json.loads(_row(desk).made_json) if m["kind"] == "skill"]
    assert made["ref"] == props[0].id
    assert made["why"] == ("A run that used the skill “sort-reports-folder” did not go well, and an "
                           "improvement to it was waiting.")


def test_a_skill_draft_kept_by_the_owner_s_own_setting_is_not_a_suggestion(desk):
    """With "Approve learned skills automatically" on (the owner's own
    choice), the draft the review writes is kept unasked: nothing waits on
    Improvements, so it is not counted, nothing rings and no line shows. The
    night's record says it was kept."""
    from iron_jarvis.skills.learning_models import SkillProposalRecord

    desk.platform.config.skill_learning_auto_approve = True
    (sid,) = _ran_out_of_steps(desk, "builder", 1)
    _skill_candidate(desk, sid, "sort the reports folder")
    desk.router.text = SKILL_MD
    out = _pass(desk)
    assert len(desk.router.calls) == 1 and out["made"] == 0, out
    with session_scope(desk.engine) as db:
        (prop,) = list(db.exec(select(SkillProposalRecord)))
    assert prop.status == "approved"
    (ran,) = [r for r in json.loads(_row(desk).ran_json) if r["proposer"] == "skill"]
    assert ran["result"] == NR.NOTHING and ran["reason"] == NR.WHY_KEPT, ran
    assert _notices(desk) == [] and _night_line(desk)["night"] is None


def test_a_draft_the_budget_refuses_never_holds_up_the_newer_ones(desk):
    """The oldest waiting draft's run was on a model that bills per use: the
    budget refuses it every night, so the skill slot takes the oldest one it
    allows, and the refused one still waits."""
    from iron_jarvis.skills.learning_models import SkillCandidateRecord

    (paid,) = _ran_out_of_steps(desk, "builder", 1, provider="openai", model="gpt-5")
    (plan,) = _ran_out_of_steps(desk, "builder", 1, at=YESTERDAY + timedelta(hours=1))
    old_id = _skill_candidate(desk, paid, "an older paid task", at=YESTERDAY - timedelta(days=3),
                              skill="older-paid-skill")
    new_id = _skill_candidate(desk, plan, "sort the reports folder")
    desk.router.text = SKILL_MD
    out = _pass(desk)
    assert out["made"] == 1, out
    assert [(c["provider"], c["model"]) for c in desk.router.calls] == [("claude-cli", "claude-sonnet-4-6")]
    with session_scope(desk.engine) as db:
        assert db.get(SkillCandidateRecord, old_id).status == "pending"
        assert db.get(SkillCandidateRecord, new_id).status == "distilled"
    # With every waiting draft refused, the oldest is still the job, so the
    # night's record says why.
    assert [j["target"] for j in NR._skill_jobs(desk.platform, NIGHT)] == [old_id]


def test_a_skill_request_never_switches_local_only_off(desk, monkeypatch):
    """The sweep sets the Local only scope from the candidate's run; the
    request inside it may only keep it on, never turn it off."""
    (sid,) = _ran_out_of_steps(desk, "builder", 1, provider="ollama", model="qwen3:8b")
    _skill_candidate(desk, sid, "sort the reports folder")
    monkeypatch.setattr(desk.platform.skill_learning, "_candidate_local_only", lambda cand: True)
    desk.router.text = SKILL_MD
    _pass(desk)
    (call,) = desk.router.calls
    assert call["provider"] == "ollama" and call["local_only"] is True


def test_a_skill_sweep_named_to_one_draft_never_takes_another(desk):
    """``distill_candidates(ids=[...])``: a complete pinned to ONE run's model
    is never handed another run's waiting draft (an older one included)."""
    from iron_jarvis.skills.learning_models import SkillCandidateRecord

    older_id = _skill_candidate(desk, "s_old", "an older task", at=YESTERDAY - timedelta(days=3),
                                skill="an-older-skill")
    mine_id = _skill_candidate(desk, "s_mine", "my task", skill="my-own-skill")
    assert older_id and SkillCandidateRecord
    asked: list[str] = []

    async def complete(system, prompt):
        asked.append(prompt)
        return ""

    out = asyncio.run(desk.platform.skill_learning.distill_candidates(complete, limit=1, ids=[mine_id]))
    assert out["reviewed"] == 1 and len(asked) == 1
    assert "my-own-skill" in asked[0] and "an-older-skill" not in asked[0]


# --------------------------------------------------------------------------- #
# 7. the offer and the page's line
# --------------------------------------------------------------------------- #


def test_the_review_is_offered_once_with_its_cost(desk):
    desk.platform.config.night_review = None
    got = _night_line(desk)
    assert got["offer"] == {"requests": 6, "words": "up to 6 requests a night on your plan"}
    assert got["night"] is None
    # Saying no is an answer: it is never offered again.
    res = desk.client.put("/settings", json={"values": {"night_review": False}})
    assert res.status_code == 200, res.text
    assert _night_line(desk)["offer"] is None
    # And yes turns it on.
    res = desk.client.put("/settings", json={"values": {"night_review": True}})
    assert res.status_code == 200, res.text
    assert desk.platform.config.night_review is True and _night_line(desk)["offer"] is None


def test_the_line_counts_only_what_still_waits_and_only_last_night(desk):
    _ran_out_of_steps(desk, _agent(desk), 4)
    _pass(desk)
    (made,) = _coach_rows(desk)
    assert _night_line(desk)["night"]["waiting"] == 1
    # The next morning it is still last night's; two nights later it is not.
    assert _night_line(desk, now=_local(IN_WINDOW, 10, 0))["night"]["waiting"] == 1
    assert _night_line(desk, now=_local(IN_WINDOW + timedelta(days=2), 10, 0))["night"] is None
    # The next night's window opens: until ITS review finishes, last night
    # is still the night before.
    assert _night_line(desk, now=_local(IN_WINDOW, 23, 30))["night"]["night"] == NIGHT
    # The morning after that next night, which had no review at all (the PC
    # was off or busy all night; 17-a catches nothing up): the page speaks of
    # that night only, never of the one before it.
    assert _night_line(desk, now=_local(IN_WINDOW + timedelta(days=1), 10, 0))["night"] is None
    # That next night's review runs and makes nothing: it is last night now,
    # in the window and the morning after (never two nights back).
    nxt = _pass(desk, now=IN_WINDOW + timedelta(days=1))
    assert nxt["state"] == NR.DONE and nxt["made"] == 0, nxt
    assert _night_line(desk, now=IN_WINDOW + timedelta(days=1))["night"] is None
    assert _night_line(desk, now=_local(IN_WINDOW + timedelta(days=1), 10, 0))["night"] is None
    # Answered: the line goes.
    desk.platform.coach.decline(made.id)
    assert _night_line(desk)["night"] is None


def test_the_setting_rows_and_their_words():
    from iron_jarvis.settings import schema

    on = schema.get("night_review")
    assert on.group == "memory" and on.type == "bool" and on.unset_ok is True
    cap = schema.get("night_review_requests")
    assert cap.type == "number" and cap.group == "memory"
    with pytest.raises(ValueError):
        cap.check(21)
    with pytest.raises(ValueError):
        cap.check(0)
    cap.check(6)
