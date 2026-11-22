"""18-a (build-out plan R2.8, idea 18 "Proof that a change helped"): AGENT
INSTRUCTIONS AND SKILLS SHOW THEIR RESULT.

Every change the owner accepts gets a before and an after, counted from the
records (``improve/results`` reads the stores and the runs, ``improve/
metrics`` splits and counts, by rules fixed in code). Through the real app
factory and the real stores; time is passed in (``now=``), never a sleep.

What is pinned, each with the way it would fail:

* the idea's own sentence comes from a seeded record: an agent that
  finished 6 of 10 tasks without help before the accept and 9 of 10 after
  reads exactly "Since you accepted this on 12 March, Research finished 9 of
  10 tasks without help. Before, it was 6 of 10."; a task with a nudge (a
  Continue follow-up), an answered card or a failed outcome is not "without
  help", a card that timed out or was let through is no help;
* three or four runs after the accept read "Not enough work yet";
* a fall of exactly 20 points is ``worse`` (the Fraction case) and a rise of
  exactly 20 is ``better``; 10 points is ``same``;
* a run that started before the accept counts before, however late it
  ended, and so does a follow-up of it that started after;
* an unreadable store names its kind and makes no row and no zeros; a
  record that cannot be read leaves a new result with no numbers and an old
  one as it was; the table that cannot be read is named on the page;
* a new skill reads "Used in 7 tasks …; 6 ended well."; an improved skill
  and a working habit compare their two sides (the habit with the Model
  team's own numbers);
* the next change to the same agent ends the earlier one's after side;
* ``GET /improvements`` carries ``results`` (newest first, at most 30) and
  chat answers "did that change help?" (``change_results``, and
  ``suggestion_list`` under its own heading);
* the night review's pass and the daily pass make the rows; nothing here
  changes the agent's instructions.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.coach.models import ACCEPTED, CoachProposalRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, EventRecord, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.autonomy import CONTINUE_MARKER
from iron_jarvis.improve import metrics as M
from iron_jarvis.improve import results as R
from iron_jarvis.improve.models import ChangeResultRecord
from iron_jarvis.improvement.models import OutcomeRecord
from iron_jarvis.skills.learning_models import SkillProposalRecord, SkillUseRecord

collect = importlib.import_module("iron_jarvis.inbox.collect")

ROSTER = "custom:Research"
#: 12 March of THIS year, at noon UTC (12 March in any time zone from -11 to
#: +11 hours), and a "now" 18 days later: the after side's two weeks are
#: over and settled.
YEAR = datetime.now(timezone.utc).year
ACCEPTED_AT = datetime(YEAR, 3, 12, 12, 0)
LATER = ACCEPTED_AT + timedelta(days=18)
IDEA_SENTENCE = (
    "Since you accepted this on 12 March, Research finished 9 of 10 tasks without help. Before, it was 6 of 10."
)


@pytest.fixture
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    s = SimpleNamespace(app=app, d=app.state.d, platform=p, engine=p.engine, client=TestClient(app))
    yield s


def _accept_coach(desk, at: datetime = ACCEPTED_AT, roster: str = ROSTER, pid: str = "") -> str:
    with session_scope(desk.engine) as db:
        row = CoachProposalRecord(agent=roster, target=roster.split(":", 1)[1], kind="instructions",
                                  before="old", after="new", status=ACCEPTED, decided_at=at)
        if pid:
            row.id = pid
        db.add(row)
        db.commit()
        return row.id


def _run(desk, at: datetime, *, ok: bool = True, roster: str = ROSTER, task: str = "",
         outcome: "str | None" = None, status: SessionStatus = SessionStatus.COMPLETED) -> str:
    with session_scope(desk.engine) as db:
        sess = Session(task=task or f"sort the reports ({at.isoformat()})", agent_type=AgentType.BUILDER,
                       agent_name=roster, status=status, origin="job:agents",
                       outcome=outcome if outcome is not None else ("completed" if ok else None))
        sess.created_at = at
        sess.finished_at = at + timedelta(minutes=20)
        db.add(sess)
        db.commit()
        return sess.id


def _answered(desk, session_id: str, *, actor: str = "app", decision: str = "once",
              at: "datetime | None" = None) -> None:
    payload = {"kind": "card", "tool": "write_file", "surface": "agent", "session_id": session_id,
               "decision": decision, "actor": actor, "ask_id": f"ask-{session_id}-{actor}-{decision}"}
    with session_scope(desk.engine) as db:
        ev = EventRecord(id=f"ev-{session_id}-{actor}-{decision}", type="interrupt.answered",
                         payload_json=json.dumps(payload))
        ev.created_at = at or ACCEPTED_AT
        db.add(ev)
        db.commit()


def _ten(desk, start: datetime, good: int, *, step: timedelta = timedelta(hours=10)) -> list[str]:
    """Ten runs from ``start``: the first ``good`` ended well with no help;
    the rest each fall short in a different way."""
    ids = []
    for i in range(10):
        at = start + step * i
        if i < good:
            sid = _run(desk, at)
            if i == 0:
                # Help that is no help: a card that timed out, a call Auto let through.
                _answered(desk, sid, actor="app", decision="timeout", at=at)
                _answered(desk, sid, actor="auto", decision="once", at=at)
        else:
            kind = (i - good) % 4
            if kind == 0:
                sid = _run(desk, at, ok=False, status=SessionStatus.FAILED)
            elif kind == 1:
                sid = _run(desk, at)
                _answered(desk, sid, at=at + timedelta(minutes=5))  # the person answered a card
            elif kind == 2:
                task = f"run number {i} from {start.isoformat()}"
                sid = _run(desk, at, task=task)
                # The Continue door's follow-up: a nudge on the run it continues.
                _run(desk, at + timedelta(minutes=30), task=f"{CONTINUE_MARKER} {task!r}. Prior result: stopped.]")
            else:
                sid = _run(desk, at, outcome="needs_you")
        ids.append(sid)
    return ids


def _result(desk, cid: str, now: datetime = LATER) -> dict:
    got = {r["id"]: r for r in R.page(desk.engine, now=now)}
    return got[cid]


# --------------------------------------------------------------------------- #
# 1. the idea's sentence, from a seeded record
# --------------------------------------------------------------------------- #


def test_a_coach_change_reads_the_ideas_own_sentence(desk):
    pid = _accept_coach(desk)
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    _ten(desk, ACCEPTED_AT + timedelta(hours=1), good=9)

    out = R.refresh(desk.engine, now=LATER)
    assert out["unreadable"] == [] and out["made"] == 1 and out["measured"] == 1, out

    got = _result(desk, f"coach:{pid}")
    assert got["words"] == IDEA_SENTENCE
    assert got["state"] == M.READY and got["verdict"] == M.BETTER
    assert got["before"]["n"] == 10 and got["before"]["good"] == 6
    assert got["after"]["n"] == 10 and got["after"]["good"] == 9
    assert got["title"] == "New instructions for Research"
    assert got["home"]["href"] == "/agents?view=team&agent=custom%3AResearch"


@pytest.mark.parametrize("after", [3, 4])
def test_too_few_runs_after_the_accept_read_not_enough_work_yet(desk, after):
    pid = _accept_coach(desk)
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    for i in range(after):
        _run(desk, ACCEPTED_AT + timedelta(hours=5 * (i + 1)))
    now = ACCEPTED_AT + timedelta(days=4)

    R.refresh(desk.engine, now=now)
    got = _result(desk, f"coach:{pid}", now)
    assert got["state"] == M.NOT_ENOUGH and got["verdict"] == ""
    assert got["words"] == f"Not enough work yet: {after} of 5 tasks since you accepted this on 12 March."
    # No verdict and no "before and after" sentence under 5 on a side.
    assert "Before," not in got["words"]

    # The two weeks pass with no more work: it says so, and stops waiting.
    over = ACCEPTED_AT + timedelta(days=16)
    R.refresh(desk.engine, now=over)
    got = _result(desk, f"coach:{pid}", over)
    assert got["words"] == (f"Not enough work to tell: {after} of 5 tasks in the two weeks after you accepted "
                            "this on 12 March.")


def test_too_little_earlier_work_says_there_is_nothing_to_compare(desk):
    pid = _accept_coach(desk)
    for i in range(3):
        _run(desk, ACCEPTED_AT - timedelta(days=2, hours=i))
    # Older than 30 days before the accept: never on the before side.
    _run(desk, ACCEPTED_AT - timedelta(days=31))
    _run(desk, ACCEPTED_AT - timedelta(days=45))
    _ten(desk, ACCEPTED_AT + timedelta(hours=1), good=9)
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"coach:{pid}")
    assert got["state"] == M.NOT_ENOUGH and got["verdict"] == ""
    assert got["words"] == ("Not enough earlier work to compare: 3 of 5 tasks in the 30 days before you accepted "
                            "this on 12 March.")


# --------------------------------------------------------------------------- #
# 2. the verdict, exactly
# --------------------------------------------------------------------------- #


def _sides(b: tuple[int, int], a: tuple[int, int]) -> tuple[M.Side, M.Side]:
    return M.Side(n=b[1], good=b[0]), M.Side(n=a[1], good=a[0])


def test_a_fall_of_exactly_twenty_points_is_worse():
    # The trap the Fraction avoids: in floating point 0.6 - 0.4 is under 0.2.
    assert 0.6 - 0.4 < 0.2
    assert M.verdict(*_sides((6, 10), (4, 10))) == M.WORSE
    assert M.verdict(*_sides((4, 10), (6, 10))) == M.BETTER
    assert M.verdict(*_sides((6, 10), (5, 10))) == M.SAME
    assert M.verdict(*_sides((5, 10), (6, 10))) == M.SAME
    # 3 of 5 to 8 of 10: no move at all.
    assert M.verdict(*_sides((3, 5), (6, 10))) == M.SAME
    # Under 5 on a side: no verdict at all.
    assert M.verdict(*_sides((0, 4), (10, 10))) == ""
    assert M.SHIFT == Fraction(1, 5) and M.MIN_TASKS == 5


def test_the_split_itself_keeps_its_bounds():
    """The pure split, on its own (the read in ``results`` stops at the same
    30 days, so each bound is pinned without the other): a task 31 days
    before is on no side, a task at the very moment of the accept is after,
    and the after side stops at the next change."""
    at = ACCEPTED_AT
    tasks = [M.Outcome(at - timedelta(days=31), True)]
    tasks += [M.Outcome(at - timedelta(days=2, hours=i), True) for i in range(3)]
    tasks += [M.Outcome(at, False)] + [M.Outcome(at + timedelta(hours=i + 1), True) for i in range(6)]
    got = M.count_coach(tasks, at, until=at + timedelta(hours=5, minutes=30))
    assert (got.before.n, got.before.good) == (3, 3)
    assert (got.after.n, got.after.good) == (6, 5)
    assert got.end == at + timedelta(hours=5, minutes=30) and got.ended == M.ENDED_NEXT
    plain = M.count_coach(tasks, at)
    assert plain.after.n == 7 and plain.end == at + timedelta(days=14) and plain.ended == M.ENDED_DAYS


def test_a_change_that_made_things_worse_is_only_shown(desk):
    """A worse result changes nothing but its row: the accepted suggestion
    stays accepted and nothing else is touched (18-b offers the undo)."""
    pid = _accept_coach(desk)
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    _ten(desk, ACCEPTED_AT + timedelta(hours=1), good=4)
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"coach:{pid}")
    assert got["verdict"] == M.WORSE
    assert got["words"] == ("Since you accepted this on 12 March, Research finished 4 of 10 tasks without help. "
                            "Before, it was 6 of 10.")
    with session_scope(desk.engine) as db:
        assert db.get(CoachProposalRecord, pid).status == ACCEPTED


# --------------------------------------------------------------------------- #
# 3. which side a task is on
# --------------------------------------------------------------------------- #


def test_a_run_that_started_before_the_accept_counts_before(desk):
    pid = _accept_coach(desk)
    for i in range(4):
        _run(desk, ACCEPTED_AT - timedelta(days=1, hours=i), ok=False, status=SessionStatus.FAILED)
    # Began a minute before the accept and finished long after it, well.
    task = "the long one that began before"
    straddler = _run(desk, ACCEPTED_AT - timedelta(minutes=1), task=task)
    with session_scope(desk.engine) as db:
        row = db.get(Session, straddler)
        row.finished_at = ACCEPTED_AT + timedelta(hours=3)
        db.add(row)
        db.commit()
    # Its Continue follow-up started AFTER the accept: still the same task,
    # on the before side, and a nudge on it.
    _run(desk, ACCEPTED_AT + timedelta(minutes=30), task=f"{CONTINUE_MARKER} {task!r}. Prior result: stopped.]")
    for i in range(5):
        _run(desk, ACCEPTED_AT + timedelta(hours=2 + i))
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"coach:{pid}")
    assert (got["after"]["n"], got["after"]["good"]) == (5, 5)
    # Before: the five just before, the straddler among them, helped (the nudge).
    assert (got["before"]["n"], got["before"]["good"]) == (5, 0)


def test_the_before_side_is_as_many_tasks_just_before_and_the_after_side_stops_at_ten(desk):
    pid = _accept_coach(desk)
    _run(desk, ACCEPTED_AT - timedelta(days=31))  # past the 30 days back: never counted
    for i in range(5):  # earlier: all failed
        _run(desk, ACCEPTED_AT - timedelta(days=20, hours=i), ok=False, status=SessionStatus.FAILED)
    for i in range(7):  # just before: all well
        _run(desk, ACCEPTED_AT - timedelta(days=2, hours=i))
    for i in range(12):  # after: 7 well, then 5 failed; only the first 10 count
        _run(desk, ACCEPTED_AT + timedelta(hours=3 * (i + 1)), ok=i < 7,
             status=SessionStatus.COMPLETED if i < 7 else SessionStatus.FAILED)
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"coach:{pid}")
    assert (got["after"]["n"], got["after"]["good"]) == (10, 7)
    # The ten just before the accept: the seven well, then three of the failed ones.
    assert (got["before"]["n"], got["before"]["good"]) == (10, 7)
    assert got["verdict"] == M.SAME

    # With only five after it, the five just before (all well), never older work.
    with session_scope(desk.engine) as db:
        rows = list(db.exec(select(Session).where(Session.created_at > ACCEPTED_AT + timedelta(hours=16))))
        for row in rows:
            db.delete(row)
        db.commit()
    with session_scope(desk.engine) as db:
        row = db.get(ChangeResultRecord, f"coach:{pid}")
        row.after_json = json.dumps({**json.loads(row.after_json), "final": False})
        db.add(row)
        db.commit()
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"coach:{pid}")
    assert (got["after"]["n"], got["after"]["good"]) == (5, 5)
    assert (got["before"]["n"], got["before"]["good"]) == (5, 5)


def test_the_next_change_to_the_same_agent_ends_the_earlier_ones_after_side(desk):
    first = _accept_coach(desk)
    second = _accept_coach(desk, at=ACCEPTED_AT + timedelta(days=3))
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    for i in range(6):  # under the first change
        _run(desk, ACCEPTED_AT + timedelta(hours=6 * (i + 1)))
    for i in range(6):  # under the second
        _run(desk, ACCEPTED_AT + timedelta(days=3, hours=2 * (i + 1)), ok=False, status=SessionStatus.FAILED)
    R.refresh(desk.engine, now=LATER)
    a, b = _result(desk, f"coach:{first}"), _result(desk, f"coach:{second}")
    assert (a["after"]["n"], a["after"]["good"]) == (6, 6)
    assert (b["after"]["n"], b["after"]["good"]) == (6, 0)


# --------------------------------------------------------------------------- #
# 4. a store or a record that cannot be read
# --------------------------------------------------------------------------- #


def _boom(*_a, **_k):
    raise RuntimeError("no such table")


def test_an_unreadable_store_names_its_kind_and_makes_no_zeros(desk, monkeypatch):
    pid = _accept_coach(desk)
    with session_scope(desk.engine) as db:
        db.add(SkillProposalRecord(kind="create", skill_name="weekly-report", status="approved",
                                   decided_at=ACCEPTED_AT))
        db.commit()
    stores = tuple((kinds, _boom if "skill" in kinds else reader) for kinds, reader in R.STORES)
    monkeypatch.setattr(R, "STORES", stores)

    out = R.refresh(desk.engine, now=LATER)
    assert out["unreadable"] == ["skill", "skill_refine"]
    with session_scope(desk.engine) as db:
        kinds = [r.kind for r in db.exec(select(ChangeResultRecord))]
    assert kinds == ["coach"], kinds  # no skill row at all, never one of zeros
    assert f"coach:{pid}" in {r["id"] for r in R.page(desk.engine, now=LATER)}


def test_a_record_that_cannot_be_read_leaves_no_numbers(desk, monkeypatch):
    pid = _accept_coach(desk)
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    _ten(desk, ACCEPTED_AT + timedelta(hours=1), good=9)
    monkeypatch.setattr(R, "coach_outcomes", _boom)
    out = R.refresh(desk.engine, now=ACCEPTED_AT + timedelta(days=5))
    assert out["unreadable"] == ["coach"]
    got = _result(desk, f"coach:{pid}", ACCEPTED_AT + timedelta(days=5))
    assert got["state"] == M.WAITING and got["before"] is None and got["after"] is None
    assert got["words"] == R.NOT_MEASURED_YET
    assert "0 of" not in got["words"]

    # Measured once, then unreadable again: it keeps what it had.
    monkeypatch.undo()
    R.refresh(desk.engine, now=LATER)
    assert _result(desk, f"coach:{pid}")["words"] == IDEA_SENTENCE
    with session_scope(desk.engine) as db:
        row = db.get(ChangeResultRecord, f"coach:{pid}")
        row.after_json = json.dumps({**json.loads(row.after_json), "final": False})
        db.add(row)
        db.commit()
    monkeypatch.setattr(R, "coach_outcomes", _boom)
    R.refresh(desk.engine, now=LATER)
    assert _result(desk, f"coach:{pid}")["words"] == IDEA_SENTENCE


def test_answered_questions_that_cannot_be_read_are_never_read_as_none(desk, monkeypatch):
    """The read of answered questions hit its bound: the runs would all look
    hands-off. It says nothing instead."""
    pid = _accept_coach(desk)
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    _ten(desk, ACCEPTED_AT + timedelta(hours=1), good=9)
    monkeypatch.setattr(R, "EVENTS_MAX", 1)
    out = R.refresh(desk.engine, now=LATER)
    assert out["unreadable"] == ["coach"]
    assert _result(desk, f"coach:{pid}")["state"] == M.WAITING

    # Calls that Auto let through are no help and never fill the bound: a
    # run on Auto with many of them is still read (the person's own three
    # answers fit a bound of three).
    some_run = _run(desk, ACCEPTED_AT + timedelta(days=5))
    for k in range(12):
        _answered(desk, some_run, actor="auto", decision=f"once{k}", at=ACCEPTED_AT + timedelta(days=5))
    monkeypatch.setattr(R, "EVENTS_MAX", 3)
    out = R.refresh(desk.engine, now=LATER)
    assert out["unreadable"] == [], out
    assert _result(desk, f"coach:{pid}")["words"] == IDEA_SENTENCE


def test_the_page_names_results_it_could_not_read(desk, monkeypatch):
    monkeypatch.setattr(R, "page", _boom)
    res = desk.client.get("/improvements")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["results"] == []
    assert {"kind": "results", "label": R.LABEL} in body["unreadable"]


# --------------------------------------------------------------------------- #
# 5. skills and working habits
# --------------------------------------------------------------------------- #


def _use(desk, name: str, at: datetime, ok: bool) -> None:
    with session_scope(desk.engine) as db:
        sess = Session(task=f"use {name}", agent_type=AgentType.BUILDER, status=SessionStatus.COMPLETED)
        sess.created_at = at
        db.add(sess)
        db.commit()
        db.add(SkillUseRecord(session_id=sess.id, skill_name=name, created_at=at + timedelta(minutes=10)))
        db.add(OutcomeRecord(session_id=sess.id, success=ok, score=1.0 if ok else 0.2))
        db.commit()


def _approve_skill(desk, name: str, kind: str = "create", at: datetime = ACCEPTED_AT) -> str:
    with session_scope(desk.engine) as db:
        row = SkillProposalRecord(kind=kind, skill_name=name, status="approved", decided_at=at,
                                  body_md="---\nname: x\n---\n", prev_body_md="old" if kind == "refine" else "")
        db.add(row)
        db.commit()
        return row.id


def test_a_new_skill_reads_how_many_tasks_used_it(desk):
    pid = _approve_skill(desk, "weekly-report")
    for i in range(7):
        _use(desk, "weekly-report", ACCEPTED_AT + timedelta(hours=3 * (i + 1)), ok=i != 3)
    _use(desk, "weekly-report", ACCEPTED_AT - timedelta(days=1), ok=False)  # before it existed: not counted
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"skill:{pid}")
    assert got["words"] == "Used in 7 tasks since you accepted it on 12 March; 6 ended well."
    assert got["state"] == M.READY and got["verdict"] == "" and got["before"] is None
    assert got["title"] == "New skill: weekly-report" and got["home"]["href"] == "/skills"


def test_an_improved_skill_compares_its_tasks_before_and_after(desk):
    pid = _approve_skill(desk, "sort-reports", kind="refine")
    for i in range(10):
        _use(desk, "sort-reports", ACCEPTED_AT - timedelta(days=5) + timedelta(hours=5 * i), ok=i < 6)
    for i in range(3):
        _use(desk, "sort-reports", ACCEPTED_AT + timedelta(hours=4 * (i + 1)), ok=True)
    R.refresh(desk.engine, now=ACCEPTED_AT + timedelta(days=2))
    got = _result(desk, f"skill_refine:{pid}", ACCEPTED_AT + timedelta(days=2))
    assert got["words"] == "Not enough work yet: 3 of 5 tasks since you accepted this on 12 March."
    for i in range(3, 10):
        _use(desk, "sort-reports", ACCEPTED_AT + timedelta(hours=4 * (i + 1)), ok=i != 5)
    R.refresh(desk.engine, now=LATER)
    got = _result(desk, f"skill_refine:{pid}")
    assert got["words"] == ("Since you accepted this on 12 March, tasks that used sort-reports ended well 9 of 10 "
                            "times. Before, it was 6 of 10.")
    assert got["verdict"] == M.BETTER and got["title"] == "Updated skill: sort-reports"


def test_a_working_habit_shows_the_numbers_model_team_has(desk):
    from iron_jarvis.agents import team_measure as TM
    from iron_jarvis.agents.team_notes import TeamNote
    from iron_jarvis.agents.team_record import CHECK_FAILED, CHECK_PASSED, REVIEW_BAD, REVIEW_GOOD, TeamRoleRun

    accepted = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)
    now = accepted + timedelta(days=10)

    def piece(at, turn, review="", check=""):
        with session_scope(desk.engine) as db:
            db.add(TeamRoleRun(member="builder-one", role="code", session_id=f"s-{turn}", scope="build:t",
                               turn=turn, finished=True, review=review, check_result=check, created_at=at))
            db.commit()

    for i in range(6):
        piece(accepted - timedelta(days=5, minutes=-i), f"b{i}", review=REVIEW_GOOD if i < 2 else REVIEW_BAD)
    for i in range(7):
        piece(accepted + timedelta(days=3, minutes=i), f"a{i}", review=REVIEW_GOOD if i < 5 else REVIEW_BAD)
    for i in range(5):
        piece(accepted + timedelta(days=4, minutes=i), f"c{i}", check=CHECK_PASSED if i < 4 else CHECK_FAILED)
    with session_scope(desk.engine) as db:
        note = TeamNote(member="builder-one", text="Run the tests before saying a piece is done",
                        proposal_id="coach_habit1", accepted_at=accepted)
        db.add(note)
        db.commit()
        db.refresh(note)
        db.expunge(note)

    R.refresh(desk.engine, now=now)
    got = _result(desk, "habit:coach_habit1", now)
    m = TM.measure(desk.engine, "builder-one", accepted, now=now)
    assert m is not None
    want = M.habit_numbers(M._team_side(m.view()["before"]), M._team_side(m.view()["after"]))
    assert want.startswith("first-pass approval 2 of 6 → 5 of 7"), want
    assert got["words"] == f"Since you accepted this on {M.day_words(accepted, now)}: {want}."
    assert got["title"] == "Working habit for builder-one: Run the tests before saying a piece is done"
    assert got["after"]["n"] == m.after.pieces and got["before"]["n"] == m.before.pieces


# --------------------------------------------------------------------------- #
# 6. where it reaches the owner: the page, chat, the passes
# --------------------------------------------------------------------------- #


def _recent(days: float) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def test_get_improvements_carries_the_results_newest_first(desk):
    older = _accept_coach(desk, at=_recent(20), roster="custom:alpha")
    newer = _accept_coach(desk, at=_recent(2), roster="custom:beta")
    _accept_coach(desk, at=_recent(90), roster="custom:gamma")  # past the look-back: no row
    R.refresh(desk.engine)
    body = desk.client.get("/improvements").json()
    ids = [r["id"] for r in body["results"]]
    assert ids == [f"coach:{newer}", f"coach:{older}"], ids
    first = body["results"][0]
    assert first["words"].startswith("Not enough work yet: 0 of 5 tasks since you accepted this on ")
    # The waiting suggestions are unchanged: a result is not a suggestion.
    assert body["total"] == 0 and body["groups"] == []


def test_at_most_thirty_results_are_shown(desk):
    for i in range(33):
        _accept_coach(desk, at=_recent(1 + i / 10), roster=f"custom:a{i}")
    R.refresh(desk.engine)
    assert len(R.page(desk.engine)) == R.SHOWN == 30


def test_chat_answers_did_that_change_help(desk):
    from iron_jarvis.inbox import tools as IT
    from iron_jarvis.settings.records import record_tools_for

    pid = _accept_coach(desk, at=_recent(18))
    base = _recent(18)
    _ten(desk, base - timedelta(days=6), good=6)
    _ten(desk, base + timedelta(hours=1), good=9)
    R.refresh(desk.engine)

    for words in ("did that change help?", "Did the change to my research agent help",
                  "has the new skill made a difference?", "show me the results of the changes I accepted"):
        assert IT.RESULTS in record_tools_for(words), words
    for plain in ("did that help you understand?", "what changed in the price list", "give me suggestions"):
        assert IT.RESULTS not in record_tools_for(plain), plain
    # The earlier pins hold: the waiting list's own question arms only the list.
    assert record_tools_for("What suggestions are waiting?") == ["suggestion_list"]

    tool = desk.platform.registry.get(IT.RESULTS)
    assert tool is not None and tool.returns_untrusted_content
    got = asyncio.run(tool.execute({}, SimpleNamespace()))
    assert got.ok, got.error
    assert "Results of changes you accepted" in got.output
    assert "finished 9 of 10 tasks without help. Before, it was 6 of 10." in got.output
    assert got.data["results"][0]["id"] == f"coach:{pid}"
    # Narrowed to one thing by its name.
    assert asyncio.run(tool.execute({"about": "nobody-like-this"}, SimpleNamespace())).output.startswith("No ")

    listed = asyncio.run(desk.platform.registry.get(IT.LIST).execute({}, SimpleNamespace()))
    assert listed.ok, listed.error
    head = listed.output.index("Results of changes you accepted")
    assert "finished 9 of 10 tasks without help" in listed.output[head:]


def test_change_results_never_says_none_when_it_could_not_read(desk, monkeypatch):
    from iron_jarvis.inbox import tools as IT

    monkeypatch.setattr(R, "page", _boom)
    got = asyncio.run(desk.platform.registry.get(IT.RESULTS).execute({}, SimpleNamespace()))
    assert not got.ok and "could not be read" in got.error


def test_the_night_review_pass_makes_the_rows(desk):
    from iron_jarvis.improve import budget as B
    from iron_jarvis.improve import night_review as NR

    p = desk.platform
    p.config.night_review = True
    pid = _accept_coach(desk, at=_recent(3))
    tomorrow = datetime.now() + timedelta(days=1)
    at = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 1, 30).astimezone()
    B.reset()
    NR.reset()
    try:
        got = asyncio.run(NR.review_pass(desk.d, now=at))
    finally:
        B.reset()
        NR.reset()
    assert got and got.get("state"), got
    with session_scope(desk.engine) as db:
        assert db.get(ChangeResultRecord, f"coach:{pid}") is not None


def test_the_daily_pass_makes_the_rows_unless_a_refresh_just_ran(desk):
    from iron_jarvis.daemon import app as A

    ticks: list[tuple] = []
    pid = _accept_coach(desk, at=_recent(3))
    got = asyncio.run(A._change_results_pass(desk.platform, lambda *a: ticks.append(a)))
    assert got and got["made"] == 1, got
    assert ticks[-1][:2] == ("change_results", True)
    with session_scope(desk.engine) as db:
        assert db.get(ChangeResultRecord, f"coach:{pid}") is not None
    # A refresh in the last 24 hours (the night review's): the day is left alone.
    assert R.refreshed_within(desk.engine, hours=24)
    assert asyncio.run(A._change_results_pass(desk.platform, lambda *a: ticks.append(a))) is None
    assert ticks[-1][:2] == ("change_results", True)


def test_a_settled_result_is_not_measured_again(desk):
    pid = _accept_coach(desk)
    _ten(desk, ACCEPTED_AT - timedelta(days=6), good=6)
    _ten(desk, ACCEPTED_AT + timedelta(hours=1), good=9)
    R.refresh(desk.engine, now=LATER)
    with session_scope(desk.engine) as db:
        checked = db.get(ChangeResultRecord, f"coach:{pid}").checked_at
    R.refresh(desk.engine, now=LATER + timedelta(days=1))
    with session_scope(desk.engine) as db:
        assert db.get(ChangeResultRecord, f"coach:{pid}").checked_at == checked


def test_results_change_no_instructions(desk):
    """No code path in the two modules changes, removes or undoes anything:
    they read the stores and write only their own table."""
    from pathlib import Path

    root = Path(R.__file__).parent
    for name in ("results.py", "metrics.py"):
        src = (root / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        for forbidden in ("registry.register", "team_notes.remove", "save_skill", ".accept(", ".decline(",
                          "archive", "/undo"):
            assert forbidden not in src, (name, forbidden)
