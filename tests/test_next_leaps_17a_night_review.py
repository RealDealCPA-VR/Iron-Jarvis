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
  until yes);
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
    "You are Research, a careful helper that reads the firm's files.\n"
    "Never send client data to anyone outside the firm.\n"
    "Always cite the file you read a number from.\n"
    "Keep answers short and name the file you changed, and say plainly when a "
    "page was unreadable instead of guessing what it said."
)
AFTER = PROMPT + "\nPlan the steps first and stop to report before the step budget runs out."


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
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools, provider=None, model=None, pin=False, **kw):
        self.calls.append({
            "provider": provider, "model": model, "pin": pin,
            "local_only": egress.current_scope().on, "background": background_call.what(),
        })
        return SimpleNamespace(response=SimpleNamespace(text=self.text), provider=provider, model=model)


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
            sess = Session(task=f"sort the K-1 folder ({i})", agent_type=AgentType.BUILDER, agent_name=roster,
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
                                  output="ok", args_json=json.dumps({"path": f"k1-{i}.pdf"})))
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


def test_a_local_only_run_is_reviewed_only_inside_the_scope(desk):
    roster = _agent(desk)
    _ran_out_of_steps(desk, roster, provider="ollama", model="qwen3:8b", options={"local_only": True})
    assert _pass(desk)["made"] == 1
    (call,) = desk.router.calls
    assert call["provider"] == "ollama" and call["local_only"] is True


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


def test_three_suggestions_are_one_row_in_the_bell_words():
    assert NR.notice_title(3) == "3 suggestions from yesterday's review"
    assert NR.notice_title(1) == "1 suggestion from yesterday's review"


# --------------------------------------------------------------------------- #
# 6. the tool-failure scan and skill drafts
# --------------------------------------------------------------------------- #


def test_the_tool_failure_scan_runs_in_the_review_and_says_why(desk):
    # The scan reads the week before the REAL clock (its own rule).
    from iron_jarvis.core.ids import utcnow

    real = utcnow().replace(tzinfo=None)
    with session_scope(desk.engine) as db:
        for i in range(3):
            db.add(ToolInvocation(session_id="chat", agent_run_id="r", tool="web_fetch", ok=False,
                                  output="boom", created_at=real - timedelta(hours=3, minutes=i)))
        db.commit()
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


def test_a_waiting_skill_draft_is_written_on_the_run_s_own_model(desk):
    from iron_jarvis.skills.learning_models import SkillCandidateRecord, SkillProposalRecord

    (sid,) = _ran_out_of_steps(desk, "builder", 1, provider="claude-cli", model="claude-haiku-4-5")
    with session_scope(desk.engine) as db:
        db.add(SkillCandidateRecord(session_id=sid, task="sort the K-1 folder", kind="create",
                                    signature="create::sort the k-1 folder"))
        db.commit()
    desk.router.text = (
        "---\nname: sort-k1-folder\ndescription: Sort a folder of K-1s by partner.\n---\n\n"
        "# Sort K-1s\n\n1. List the folder.\n2. Read each K-1.\n"
    )
    out = _pass(desk)
    calls = [c for c in desk.router.calls]
    assert [(c["provider"], c["model"], c["pin"], c["background"]) for c in calls] == [
        ("claude-cli", "claude-haiku-4-5", True, "night_review")]
    with session_scope(desk.engine) as db:
        props = list(db.exec(select(SkillProposalRecord)))
    assert len(props) == 1 and out["made"] == 1
    (made,) = [m for m in json.loads(_row(desk).made_json) if m["kind"] == "skill"]
    assert made["ref"] == props[0].id
    assert made["why"] == "A run did something worth keeping as a skill, and its draft was waiting: “sort the K-1 folder”."


def test_a_skill_sweep_named_to_one_draft_never_takes_another(desk):
    """``distill_candidates(ids=[...])``: a complete pinned to ONE run's model
    is never handed another run's waiting draft (an older one included)."""
    from iron_jarvis.skills.learning_models import SkillCandidateRecord

    with session_scope(desk.engine) as db:
        older = SkillCandidateRecord(session_id="s_old", task="an older task", signature="create::older")
        older.created_at = YESTERDAY - timedelta(days=3)
        mine = SkillCandidateRecord(session_id="s_mine", task="my task", signature="create::mine")
        db.add(older)
        db.add(mine)
        db.commit()
        mine_id = mine.id
    asked: list[str] = []

    async def complete(system, prompt):
        asked.append(prompt)
        return ""

    out = asyncio.run(desk.platform.skill_learning.distill_candidates(complete, limit=1, ids=[mine_id]))
    assert out["reviewed"] == 1 and len(asked) == 1
    assert "my task" in asked[0] and "an older task" not in asked[0]


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
