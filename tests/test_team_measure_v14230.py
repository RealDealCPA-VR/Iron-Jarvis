"""v1.423.0 (Wave 3 lane E, Team notes Phase 3) — DOES A WORKING HABIT HELP?

The owner's design: "Each member's record is split at the moment a note was
accepted: first-pass approval, confirmed findings per piece, check pass rate.
… No numbers are shown under 5 pieces, and a note that makes things worse is
flagged for removal, never removed automatically."

What these pins hold (``agents/team_measure``), with records SEEDED on both
sides of an acceptance on the real platform:

1. THE SPLIT: AFTER runs from the acceptance to now (or the removal); BEFORE
   is the same span just before it, at most 30 days; a piece at the very
   moment of acceptance is counted once (after).
2. THE NUMBERS per side: pieces (code / test pieces a check or a review
   judged), first-pass approval (the member card's own rule —
   ``team_findings.member_findings``), confirmed findings per piece (ASKED
   findings only), check pass rate.
3. UNDER 5 PIECES no numbers ("not enough work yet") and never worse.
4. THE FLAG — exact (a fall of exactly 20 points flags; floats would not),
   each of the three rules, and the rise that is 50% but under half a finding
   a piece is not worse.
5. GET /team carries ``measure {before, after, worse}`` on each accepted
   habit, read OFF the loop; a measure that cannot be read is ABSENT (never
   zeros) — a findings read that fails is not read as zeros.
6. NEVER REMOVED AUTOMATICALLY: a worse habit stays in force, its
   TEAM-NOTES.md keeps it, and nothing calls ``team_notes.remove``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import team_findings as TF
from iron_jarvis.agents import team_measure as TM
from iron_jarvis.agents import team_notes as TN
from iron_jarvis.agents.team_record import CHECK_FAILED, CHECK_PASSED, REVIEW_BAD, REVIEW_GOOD, TeamRoleRun
from iron_jarvis.core.db import session_scope
from tests.test_team_notes_v14230 import WORKER, _add, _app

DAY = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _piece(p, at: datetime, *, turn: str, review: str = "", check: str = "", role: str = "code",
           member: str = WORKER) -> None:
    _add(p, TeamRoleRun(member=member, role=role, session_id=f"s-{turn}", scope="build:t", turn=turn,
                        finished=True, review=review, check_result=check, created_at=at))


def _confirmed(p, at: datetime, n: int, *, asked: bool = True, member: str = WORKER) -> None:
    for k in range(n):
        _add(p, TF.TeamFinding(scope="build:t", turn=f"f{at.timestamp()}-{k}", review_row="r", review_session="rs",
                               reviewer="rev", number=k + 1, path=f"a{k}.py", what="w", change="c",
                               member=member, status=TF.FIXED, asked=asked, created_at=at))


def _habit(p, accepted_at: datetime, text: str = "Run the tests before reporting a piece as done",
           member: str = WORKER) -> TN.TeamNote:
    return _add(p, TN.TeamNote(member=member, text=text, finding_ids_json="[]", proposal_id="pp",
                               accepted_at=accepted_at))


def _side(p, base: datetime, *, approved: int, judged: int, checks_passed: int = 0, checks: int = 0,
          extra: int = 0, tag: str = "") -> None:
    """``judged`` replies (one reviewed piece each, ``approved`` of them good),
    ``checks`` more pieces the finish check judged (``checks_passed`` of them
    passed), ``extra`` pieces nothing judged (never counted)."""
    for i in range(judged):
        _piece(p, base + timedelta(minutes=i), turn=f"{tag}r{i}", review=REVIEW_GOOD if i < approved else REVIEW_BAD)
    for i in range(checks):
        _piece(p, base + timedelta(minutes=100 + i), turn=f"{tag}c{i}",
               check=CHECK_PASSED if i < checks_passed else CHECK_FAILED)
    for i in range(extra):
        _piece(p, base + timedelta(minutes=200 + i), turn=f"{tag}x{i}")


@pytest.fixture
def world(tmp_path):
    app, p = _app(tmp_path)
    return app, p


# --------------------------------------------------------------------------- #
# 1-2. the split and the numbers
# --------------------------------------------------------------------------- #


def test_the_record_is_split_at_the_acceptance_over_the_same_span(world):
    _app_, p = world
    now = _now()
    t = now - 10 * DAY
    # BEFORE (the 10 days before): 6 replies judged, 2 approved; 2 confirmed
    # findings (one more NOT asked — never counted); a piece no check or
    # review judged (never counted); a piece 15 days before (outside the
    # same span) never counted.
    _side(p, t - 5 * DAY, approved=2, judged=6, extra=1, tag="b")
    _confirmed(p, t - 5 * DAY, 2)
    _confirmed(p, t - 5 * DAY, 1, asked=False)
    _piece(p, t - 15 * DAY, turn="old", review=REVIEW_BAD)
    # A READING piece a review judged: not code or test, never counted.
    _piece(p, t - 4 * DAY, turn="reading", review=REVIEW_BAD, role="read")
    # AFTER: 7 replies judged, 5 approved, and a piece AT the acceptance.
    _side(p, t + 3 * DAY, approved=5, judged=6, tag="a")
    _piece(p, t, turn="edge", review=REVIEW_GOOD)
    _confirmed(p, t + 3 * DAY, 1)
    note = _habit(p, t)

    m = TM.measure(p.engine, WORKER, note.accepted_at, now=now)
    assert m is not None
    assert (m.before.pieces, m.before.first_approved, m.before.first_of, m.before.confirmed) == (6, 2, 6, 2)
    assert (m.after.pieces, m.after.first_approved, m.after.first_of, m.after.confirmed) == (7, 6, 7, 1)
    assert m.before.findings_per_piece == Fraction(2, 6) and m.after.findings_per_piece == Fraction(1, 7)
    assert m.worse is False
    view = m.view()
    assert view["before"] == {"enough": True, "pieces": 6, "first_pass": {"approved": 2, "of": 6},
                              "confirmed": 2, "confirmed_per_piece": 0.33, "checks": None}
    assert view["after"]["first_pass"] == {"approved": 6, "of": 7} and view["worse"] is False


def test_the_before_span_is_capped_at_thirty_days_and_a_removal_ends_the_after_span(world):
    _app_, p = world
    now = _now()
    t = now - 60 * DAY
    # 60 days since the acceptance: the before side is the 30 days before.
    _side(p, t - 20 * DAY, approved=5, judged=5, tag="in")
    _side(p, t - 40 * DAY, approved=5, judged=5, tag="out")
    (bl, bh), (al, ah) = TM.windows(t, None, now=now)
    assert al == t and ah == now and t - bl == timedelta(days=TM.BEFORE_CAP_DAYS)
    assert bh == t - timedelta(microseconds=1)
    m = TM.measure(p.engine, WORKER, t, now=now)
    assert m.before.pieces == 5
    # A REMOVED habit: after = acceptance → removal; before = the same span.
    _side(p, t + 2 * DAY, approved=5, judged=5, tag="kept")
    _side(p, t + 8 * DAY, approved=0, judged=5, tag="gone")
    gone = TM.measure(p.engine, WORKER, t, t + 5 * DAY, now=now)
    assert gone.after.pieces == 5 and gone.after.first_approved == 5
    (bl, _bh), _a = TM.windows(t, t + 5 * DAY, now=now)
    assert t - bl == 5 * DAY


def test_the_check_pass_rate_is_read_from_the_finish_checks_verdicts(world):
    _app_, p = world
    now = _now()
    t = now - 4 * DAY
    _side(p, t - 2 * DAY, approved=0, judged=0, checks=5, checks_passed=4, tag="b")
    _side(p, t + 1 * DAY, approved=0, judged=0, checks=6, checks_passed=6, tag="a")
    m = TM.measure(p.engine, WORKER, t, now=now)
    assert (m.before.checks_passed, m.before.checks_of) == (4, 5)
    assert (m.after.checks_passed, m.after.checks_of) == (6, 6)
    assert m.view()["before"]["first_pass"] is None and m.worse is False


# --------------------------------------------------------------------------- #
# 3. under five pieces: no numbers
# --------------------------------------------------------------------------- #


def test_a_side_under_five_pieces_has_no_numbers_and_is_never_worse(world):
    _app_, p = world
    now = _now()
    t = now - 6 * DAY
    _side(p, t - 3 * DAY, approved=5, judged=5, tag="b")      # 100% before
    _side(p, t + 2 * DAY, approved=0, judged=4, tag="a")      # 0% after — but only 4 pieces
    m = TM.measure(p.engine, WORKER, t, now=now)
    assert m.after.enough is False and m.before.enough is True
    assert m.view()["after"] == {"enough": False, "pieces": 4, "words": TM.NOT_ENOUGH}
    assert m.worse is False
    # One more piece after: now enough, and the fall flags.
    _piece(p, t + 3 * DAY, turn="a-fifth", review=REVIEW_BAD)
    assert TM.measure(p.engine, WORKER, t, now=now).worse is True


# --------------------------------------------------------------------------- #
# 4. the flag
# --------------------------------------------------------------------------- #


def _s(**kw) -> TM.Side:
    base = {"pieces": 5}
    base.update(kw)
    return TM.Side(**base)


@pytest.mark.parametrize("before, after, want", [
    # first-pass approval fell by exactly 20 points (0.8 → 0.6): floats read
    # 0.19999999999999996 and would miss it.
    (_s(first_approved=4, first_of=5), _s(first_approved=3, first_of=5), True),
    (_s(first_approved=3, first_of=5), _s(first_approved=2, first_of=5), True),    # 0.6 - 0.4 in floats
    (_s(checks_passed=3, checks_of=5), _s(checks_passed=2, checks_of=5), True),
    (_s(first_approved=4, first_of=5), _s(first_approved=7, first_of=10), False),  # 10 points
    (_s(first_approved=2, first_of=6), _s(first_approved=5, first_of=7), False),   # better
    # confirmed findings per piece: 0 → 0.6 (risen by half a finding and more)
    (_s(confirmed=0), _s(confirmed=3), True),
    (_s(confirmed=0), _s(confirmed=2), False),                    # 0 → 0.4: under half a finding
    (_s(confirmed=1, pieces=10), _s(confirmed=2, pieces=10), False),  # doubled, 0.1 → 0.2: noise
    (_s(confirmed=2), _s(confirmed=5), True),                     # 0.4 → 1.0
    (_s(confirmed=4), _s(confirmed=6), False),                    # 0.8 → 1.2: +0.4 only
    (_s(confirmed=4, pieces=10), _s(confirmed=11, pieces=10), True),  # 0.4 → 1.1
    (_s(confirmed=6, pieces=10), _s(confirmed=8, pieces=10), False),  # +0.2, under 50%
    # the check pass rate fell by exactly 20 points
    (_s(checks_passed=5, checks_of=5), _s(checks_passed=4, checks_of=5), True),
    (_s(checks_passed=5, checks_of=5), _s(checks_passed=9, checks_of=10), False),
    # a rule with no number on one side says nothing
    (_s(first_approved=5, first_of=5), _s(), False),
    # under five pieces on either side: never worse
    (_s(pieces=4, first_approved=4, first_of=4), _s(first_approved=0, first_of=5), False),
    (_s(first_approved=5, first_of=5), _s(pieces=4, first_approved=0, first_of=4), False),
])
def test_worse_is_exact_and_needs_enough_work_on_both_sides(before, after, want):
    assert TM.worse(before, after) is want


# --------------------------------------------------------------------------- #
# 5. GET /team: the measure on each accepted habit, off the loop, never zeros
# --------------------------------------------------------------------------- #


def _member(client) -> dict:
    return next(m for m in client.get("/team").json()["members"] if m["name"] == WORKER)


def test_get_team_carries_each_accepted_habits_measure(world):
    app, p = world
    now = _now()
    t = now - 10 * DAY
    _side(p, t - 5 * DAY, approved=2, judged=6, tag="b")
    _side(p, t + 3 * DAY, approved=5, judged=7, tag="a")
    better = _habit(p, t)
    young = _habit(p, now - timedelta(minutes=5), text="Name every file you changed in the report")
    client = TestClient(app)
    rows = {n["id"]: n for n in _member(client)["notes"]["accepted"]}
    assert rows[better.id]["measure"]["before"]["first_pass"] == {"approved": 2, "of": 6}
    assert rows[better.id]["measure"]["after"]["first_pass"] == {"approved": 5, "of": 7}
    assert rows[better.id]["measure"]["worse"] is False
    # A habit accepted five minutes ago: no work on either side yet.
    assert rows[young.id]["measure"] == {
        "before": {"enough": False, "pieces": 0, "words": TM.NOT_ENOUGH},
        "after": {"enough": False, "pieces": 0, "words": TM.NOT_ENOUGH},
        "worse": False,
    }


def test_the_measure_is_read_off_the_loop(world, monkeypatch):
    """GET /team is a sync route (FastAPI's threadpool): the six queries a
    habit costs never run on the event loop."""
    app, p = world
    _habit(p, _now() - DAY)
    seen: list[bool] = []
    real = TM.attach

    def spy(engine, notes, **kw):
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return real(engine, notes, **kw)

    monkeypatch.setattr(TM, "attach", spy)
    assert TestClient(app).get("/team").status_code == 200
    assert seen == [False]


def test_a_measure_that_cannot_be_read_is_absent_never_zeros(world, monkeypatch):
    app, p = world
    now = _now()
    t = now - 10 * DAY
    _side(p, t - 5 * DAY, approved=5, judged=5, tag="b")
    _side(p, t + 3 * DAY, approved=5, judged=5, tag="a")
    note = _habit(p, t)
    # No engine at all: absent.
    assert TM.measure(None, WORKER, t, now=now) is None
    # The findings table cannot be read: the before/after are NOT zeros (a
    # zero first-pass after 5 of 5 would read as a fall) — absent.
    with session_scope(p.engine) as db:
        db.exec(__import__("sqlalchemy").text("ALTER TABLE teamfinding RENAME TO teamfinding_gone"))
        db.commit()
    assert TM.measure(p.engine, WORKER, t, now=now) is None
    row = next(n for n in _member(TestClient(app))["notes"]["accepted"] if n["id"] == note.id)
    assert "measure" not in row
    # The member card's own line keeps its old rule (zeros, never a 500).
    assert TF.member_findings(p.engine, WORKER) == TF.zeros()
    with pytest.raises(Exception):
        TF.member_findings(p.engine, WORKER, strict=True)


def test_the_pieces_read_failing_is_absent_too(world, monkeypatch):
    _app_, p = world
    t = _now() - 10 * DAY

    def boom(*a, **kw):
        raise RuntimeError("the table is locked")

    monkeypatch.setattr(TM, "_pieces", boom)
    assert TM.measure(p.engine, WORKER, t) is None
    notes = {WORKER: {"accepted": [{"id": "x", "accepted_at": t.isoformat()}]}}
    assert TM.attach(p.engine, notes) == 0 and "measure" not in notes[WORKER]["accepted"][0]


# --------------------------------------------------------------------------- #
# 6. a worse habit is FLAGGED, never removed
# --------------------------------------------------------------------------- #


def test_a_worse_habit_is_flagged_and_never_removed_by_itself(world, monkeypatch):
    app, p = world
    now = _now()
    t = now - 10 * DAY
    _side(p, t - 5 * DAY, approved=5, judged=6, tag="b")
    _side(p, t + 3 * DAY, approved=2, judged=6, tag="a")
    # Accepted through the real path (TEAM-NOTES.md written), then back-dated.
    from iron_jarvis.coach.models import CoachProposalRecord

    row = _add(p, CoachProposalRecord(agent=f"custom:{WORKER}", kind="team_note", target=WORKER,
                                      after="Run the tests before reporting a piece as done"))
    TN.accept(p, row.id)
    (note,) = TN.active_notes(p.engine, WORKER)
    with session_scope(p.engine) as db:
        live = db.get(TN.TeamNote, note.id)
        live.accepted_at = t
        db.add(live)
        db.commit()
    notes_file = TN.notes_path(p, WORKER).read_text(encoding="utf-8")
    removed: list[str] = []
    monkeypatch.setattr(TN, "remove", lambda *a, **kw: removed.append("called") or {})
    client = TestClient(app)
    for _ in range(2):
        (shown,) = _member(client)["notes"]["accepted"]
        assert shown["id"] == note.id and shown["measure"]["worse"] is True
    assert removed == []
    (still,) = TN.active_notes(p.engine, WORKER)
    assert still.id == note.id and still.removed_at is None
    assert TN.notes_path(p, WORKER).read_text(encoding="utf-8") == notes_file
    # The owner's press is still the only way: Remove takes it back.
    monkeypatch.undo()
    assert client.post(f"/team/notes/{note.id}/remove").status_code == 200
    assert TN.active_notes(p.engine, WORKER) == []


def test_attach_reads_the_views_stamps_and_writes_nothing(world):
    _app_, p = world
    now = _now()
    t = now - 3 * DAY
    note = _habit(p, t)
    view = TN.note_view(note)
    notes = {WORKER: {"accepted": [view], "suggested": 0, "hand_edited": False}}
    assert TM.attach(p.engine, notes, now=now) == 1
    assert view["measure"]["worse"] is False
    # A view with no readable acceptance stamp gets no measure.
    bad = {WORKER: {"accepted": [{"id": "y", "accepted_at": "not a time"}]}}
    assert TM.attach(p.engine, bad) == 0 and "measure" not in bad[WORKER]["accepted"][0]
    assert json.dumps(notes)  # plain JSON for the route


# --------------------------------------------------------------------------- #
# 7. the contract the dashboard reads (a fixture from the REAL views)
# --------------------------------------------------------------------------- #

FIXTURE = __import__("pathlib").Path(__file__).resolve().parent.parent / "dashboard" / "__tests__" / "fixtures" / (
    "habit-measure-v1423-0.json")


def _contract() -> dict:
    better = TM.Measure(
        before=TM.Side(pieces=6, first_approved=2, first_of=6, confirmed=3, checks_passed=3, checks_of=5),
        after=TM.Side(pieces=7, first_approved=5, first_of=7, confirmed=1, checks_passed=6, checks_of=6),
    )
    worse = TM.Measure(
        before=TM.Side(pieces=6, first_approved=5, first_of=6, confirmed=1),
        after=TM.Side(pieces=6, first_approved=2, first_of=6, confirmed=1),
    )
    thin = TM.Measure(
        before=TM.Side(pieces=8, first_approved=7, first_of=8),
        after=TM.Side(pieces=3, first_approved=0, first_of=3, confirmed=2),
    )
    return {"better": better.view(), "worse": worse.view(), "not_enough": thin.view()}


def test_the_dashboards_fixture_is_what_the_daemon_sends():
    """``dashboard/__tests__/habit-measure-v1423-0.test.tsx`` decodes these
    REAL views (regenerate with ``IJ_REGEN_HABIT_MEASURE=1``)."""
    import os

    got = _contract()
    assert got["worse"]["worse"] is True and got["better"]["worse"] is False
    assert got["not_enough"]["after"] == {"enough": False, "pieces": 3, "words": TM.NOT_ENOUGH}
    if os.environ.get("IJ_REGEN_HABIT_MEASURE") == "1":
        FIXTURE.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == got
