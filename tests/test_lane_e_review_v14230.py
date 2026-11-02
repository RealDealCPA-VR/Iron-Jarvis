"""v1.423.0 (Wave 3 lane E, REVIEW) — defects the independent review found in
the lane's work, each pinned RED on the lane's code first.

THE MEASURE (``agents/team_measure``) split a member's record by each
PIECE's own time. But a member's working habits are read ONCE per reply —
at the member's FIRST team piece of that reply (``team_notes.habits_for_run``'s
cache), and accepting a habit does not drop that cache — so:

1. A REPLY THAT BEGAN BEFORE THE ACCEPTANCE ran WITHOUT the habit to its
   end, yet its later pieces were counted AFTER — and its first judged piece
   after the acceptance (a fix after a "changes needed", i.e. a SECOND pass)
   was counted as an after-side FIRST-PASS approval. One such reply made a
   habit look better than it was. A reply now belongs WHOLE to the side of
   its member's first piece (its findings too).
2. A PIECE IS RECORDED WHEN IT ENDS (``TeamRoleRun.created_at``) but reads
   the habits when it STARTS (its own session's ``created_at``): a piece that
   started before the acceptance and ended after it ran without the habit,
   and was counted after.
3. A HABIT ACCEPTED AGAIN (removed, then accepted again from new findings)
   was compared with a "before" span that reached back into the time the
   SAME habit was in force: that is not "without the habit". BEFORE now ends
   its reach at the earlier copy's removal.
4. ``attach`` capped the habits it MEASURED, not the ones it TRIED: a record
   that could not be read was retried for every habit, unbounded.

THE LEFTOVERS:

5. A TEAM-NOTES.md write that failed WHILE WRITING the temp file (a full
   disk) left the half-written ``.tmp`` beside the user's file.
6. THE LIBRARY'S SWITCH, again: a reflection that was already asking its
   model when the owner turned "learn from Build sessions" off minted its
   suggestion anyway once the model answered — a library suggestion made
   with the switch off.
7. TEAM NOTES' OWN SWITCH, the same race: a distill already asking the
   reviewer's model when the owner turned team notes off minted the habits
   it answered.
"""

from __future__ import annotations

import errno
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import team_findings as TF
from iron_jarvis.agents import team_measure as TM
from iron_jarvis.agents import team_notes as TN
from iron_jarvis.agents.team_record import REVIEW_BAD, REVIEW_GOOD, TeamRoleRun
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from tests.test_team_measure_v14230 import _habit, _member, _side
from tests.test_team_notes_v14230 import WORKER, _add, _app

DAY = timedelta(days=1)


@pytest.fixture
def world(tmp_path):
    return _app(tmp_path)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _piece(p, at: datetime, *, turn: str, review: str = "", check: str = "", started: "datetime | None" = None,
           sid: str = "") -> None:
    sid = sid or f"s-{turn}-{at.timestamp()}"
    if started is not None:
        _add(p, Session(id=sid, task="seeded", agent_name=f"custom:{WORKER}", created_at=started))
    _add(p, TeamRoleRun(member=WORKER, role="code", session_id=sid, scope="build:t", turn=turn,
                        finished=True, review=review, check_result=check, created_at=at))


# --------------------------------------------------------------------------- #
# 1. a reply that began before the acceptance belongs to "before", whole
# --------------------------------------------------------------------------- #


def test_a_reply_that_began_before_the_acceptance_is_counted_before_whole(world):
    _app_, p = world
    now = _now()
    t = now - 6 * DAY
    _side(p, t - 3 * DAY, approved=5, judged=5, tag="b")
    _side(p, t + 2 * DAY, approved=5, judged=5, tag="a")
    # ONE reply ("S") straddles the acceptance: its first piece (an hour
    # before) was sent back, its fix (half an hour after) approved — a second
    # pass of a reply that never had the habit (read once per reply).
    _piece(p, t - timedelta(hours=1), turn="S", review=REVIEW_BAD)
    _piece(p, t + timedelta(minutes=30), turn="S", review=REVIEW_GOOD)
    # Its finding (reviewed and confirmed after the acceptance): before too.
    _add(p, TF.TeamFinding(scope="build:t", turn="S", review_row="r", review_session="rs", reviewer="rev",
                           number=1, path="a.py", what="w", change="c", member=WORKER, status=TF.FIXED,
                           asked=True, created_at=t + timedelta(minutes=20)))
    m = TM.measure(p.engine, WORKER, t, now=now)
    assert m is not None
    assert (m.after.pieces, m.after.first_approved, m.after.first_of, m.after.confirmed) == (5, 5, 5, 0)
    assert (m.before.pieces, m.before.first_approved, m.before.first_of, m.before.confirmed) == (7, 5, 6, 1)


def test_with_no_reply_across_the_acceptance_the_numbers_are_the_member_cards(world):
    """The control: when every reply sits on one side, first-pass approval
    and confirmed findings are exactly the member card's own numbers
    (``team_findings.member_findings``) over each side's span."""
    _app_, p = world
    now = _now()
    t = now - 6 * DAY
    _side(p, t - 3 * DAY, approved=2, judged=6, checks=3, checks_passed=2, tag="b")
    _side(p, t + 2 * DAY, approved=4, judged=5, checks=2, checks_passed=2, tag="a")
    for at, n in ((t - 2 * DAY, 2), (t + 3 * DAY, 1)):
        for k in range(n):
            _add(p, TF.TeamFinding(scope="build:t", turn=f"f{at.timestamp()}-{k}", review_row="r",
                                   review_session="rs", reviewer="rev", number=k + 1, path="a.py", what="w",
                                   change="c", member=WORKER, status=TF.FIXED, asked=True, created_at=at))
    m = TM.measure(p.engine, WORKER, t, now=now)
    (bl, bh), (al, ah) = TM.windows(t, None, now=now)
    for side, lo, hi in ((m.before, bl, bh), (m.after, al, ah)):
        card = TF.member_findings(p.engine, WORKER, since=lo, until=hi)
        assert (side.first_approved, side.first_of) == (card["first_pass"]["approved"], card["first_pass"]["of"])
        assert side.confirmed == card["confirmed"]
    assert (m.before.checks_passed, m.before.checks_of, m.after.checks_passed, m.after.checks_of) == (2, 3, 2, 2)


# --------------------------------------------------------------------------- #
# 2. a piece that started before the acceptance ran without the habit
# --------------------------------------------------------------------------- #


def test_a_piece_that_started_before_the_acceptance_is_counted_before(world):
    _app_, p = world
    now = _now()
    t = now - 6 * DAY
    _side(p, t - 3 * DAY, approved=5, judged=5, tag="b")
    _side(p, t + 2 * DAY, approved=5, judged=5, tag="a")
    # Started ten minutes before the acceptance, recorded (ended) ten after.
    _piece(p, t + timedelta(minutes=10), turn="P", review=REVIEW_BAD, started=t - timedelta(minutes=10))
    m = TM.measure(p.engine, WORKER, t, now=now)
    assert (m.after.pieces, m.after.first_approved, m.after.first_of) == (5, 5, 5)
    assert (m.before.pieces, m.before.first_approved, m.before.first_of) == (6, 5, 6)


def test_a_piece_that_started_after_the_removal_is_not_after(world):
    """REMOVING a habit drops the habits' cache (``forget_member``): a piece
    of a reply begun while it was in force, that STARTED after its removal,
    read the habits afresh — without it — and is no part of the after side
    (nor is a finding on it)."""
    _app_, p = world
    now = _now()
    t = now - 20 * DAY
    gone = t + 5 * DAY
    _side(p, t - 4 * DAY, approved=5, judged=5, tag="b")
    _side(p, t + 1 * DAY, approved=5, judged=5, tag="a")
    _piece(p, gone - timedelta(hours=2), turn="R", review=REVIEW_GOOD, started=gone - timedelta(hours=3))
    _piece(p, gone + timedelta(hours=2), turn="R", review=REVIEW_BAD, check="failed", started=gone + timedelta(hours=1),
           sid="late")
    _add(p, TF.TeamFinding(scope="build:t", turn="R", review_row="r", review_session="rs", reviewer="rev",
                           number=1, path="a.py", what="w", change="c", member=WORKER, worker_session="late",
                           status=TF.FIXED, asked=True, created_at=gone + timedelta(hours=3)))
    m = TM.measure(p.engine, WORKER, t, gone, now=now)
    assert (m.after.pieces, m.after.first_approved, m.after.first_of, m.after.confirmed) == (6, 6, 6, 0)
    assert m.after.checks_of == 0


# --------------------------------------------------------------------------- #
# 3. a habit accepted again: "before" is never a time it was in force
# --------------------------------------------------------------------------- #


def test_a_habit_accepted_again_is_compared_with_time_it_was_not_in_force(world):
    app, p = world
    now = _now()
    t = now - 30 * DAY
    text = "Run the tests before reporting a piece as done"
    # The SAME habit was in force from t-40d to t-20d, then removed.
    first = _habit(p, t - 40 * DAY, text=text)
    with session_scope(p.engine) as db:
        live = db.get(TN.TeamNote, first.id)
        live.removed_at = t - 20 * DAY
        db.add(live)
        db.commit()
    # While it was in force: 5 pieces sent back. Without it: 5 approved.
    _side(p, t - 25 * DAY, approved=0, judged=5, tag="was")
    _side(p, t - 10 * DAY, approved=5, judged=5, tag="without")
    _side(p, t + 5 * DAY, approved=5, judged=5, tag="again")
    again = _habit(p, t, text=text)
    row = next(n for n in _member(TestClient(app))["notes"]["accepted"] if n["id"] == again.id)
    before = row["measure"]["before"]
    assert before["pieces"] == 5 and before["first_pass"] == {"approved": 5, "of": 5}, before
    # The span ends at the earlier copy's removal; the after side is untouched.
    (bl, _bh), (al, ah) = TM.windows(t, None, now=now, not_before=t - 20 * DAY)
    assert bl == t - 20 * DAY and (al, ah) == (t, now)


def test_an_unrelated_earlier_habit_does_not_shorten_the_before_span(world):
    app, p = world
    now = _now()
    t = now - 10 * DAY
    other = _habit(p, t - 40 * DAY, text="Name every file you changed in the report")
    with session_scope(p.engine) as db:
        live = db.get(TN.TeamNote, other.id)
        live.removed_at = t - 5 * DAY
        db.add(live)
        db.commit()
    _side(p, t - 8 * DAY, approved=5, judged=5, tag="b")
    note = _habit(p, t)
    row = next(n for n in _member(TestClient(app))["notes"]["accepted"] if n["id"] == note.id)
    assert row["measure"]["before"]["pieces"] == 5


def test_when_the_habits_history_cannot_be_read_no_habit_is_measured(world, monkeypatch):
    """Whether a habit was given before decides how far back "before" may
    reach: when that cannot be read, no before can be trusted — no measure
    (never one that may compare the habit with itself)."""
    app, p = world
    t = _now() - 10 * DAY
    _side(p, t - 5 * DAY, approved=5, judged=5, tag="b")
    _side(p, t + 3 * DAY, approved=5, judged=5, tag="a")
    note = _habit(p, t)

    def boom(*a, **kw):
        raise RuntimeError("the table is locked")

    monkeypatch.setattr(TM, "_copies", boom)
    row = next(n for n in _member(TestClient(app))["notes"]["accepted"] if n["id"] == note.id)
    assert "measure" not in row


# --------------------------------------------------------------------------- #
# 4. attach is bounded by what it TRIES
# --------------------------------------------------------------------------- #


def test_attach_tries_at_most_max_measured_habits(world, monkeypatch):
    _app_, p = world
    calls: list[str] = []

    def unreadable(engine, member, accepted_at, removed_at=None, **kw):
        calls.append(member)
        return None

    monkeypatch.setattr(TM, "measure", unreadable)
    at = (_now() - DAY).isoformat()
    notes = {WORKER: {"accepted": [{"id": f"n{i}", "text": f"habit {i}", "accepted_at": at}
                                   for i in range(TM.MAX_MEASURED + 40)]}}
    assert TM.attach(p.engine, notes) == 0
    assert len(calls) == TM.MAX_MEASURED


# --------------------------------------------------------------------------- #
# 5. a failed temp write leaves nothing behind
# --------------------------------------------------------------------------- #


def test_a_team_notes_write_that_fails_mid_write_leaves_no_temp_file(tmp_path, monkeypatch):
    from iron_jarvis.platform import build_platform
    from tests.test_team_notes_v14230 import FakeRouter, _accepted, _local_ollama, _members

    p = build_platform(str(tmp_path / "home"))
    _local_ollama(p)
    _members(p)
    p.router = FakeRouter()
    _accepted(p)
    path = TN.notes_path(p, WORKER)
    real = pathlib.Path.write_text

    def disk_full(self, data, *a, **kw):
        if self.name.startswith(f".{TN.NOTES_FILE}.") and self.name.endswith(".tmp"):
            real(self, data[: len(data) // 2], *a, **kw)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", disk_full)
    row = _add(p, TN_proposal(WORKER, "Keep a test beside every new function"))
    out = TN.accept(p, row.id)
    assert out.rendered is not None and out.rendered.state == "failed"
    assert [f.name for f in path.parent.iterdir() if f.name.endswith(".tmp")] == []


def TN_proposal(member: str, text: str):
    from iron_jarvis.coach.models import CoachProposalRecord

    return CoachProposalRecord(agent=f"custom:{member}", kind="team_note", target=member, after=text)


# --------------------------------------------------------------------------- #
# 6. a reflection that answers after the switch went off mints nothing
# --------------------------------------------------------------------------- #


async def test_a_reflection_answered_after_the_switch_went_off_suggests_nothing(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.harness import library as L
    from tests.test_harness_library_v1370 import GOOD, _TURN, _rows, _worked

    L.shutdown()
    try:
        app = create_app(str(tmp_path / "home"))
        work = tmp_path / "work"
        work.mkdir()
        (work / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        sid, script = await _worked(app, work, GOOD)
        platform = app.state.platform
        # The owner turns "learn from Build sessions" off while the model is
        # still writing its answer.
        script.on_reflect = lambda: setattr(platform.config, "build_reflect", False)
        out = await L.reflect(platform, sid, _TURN[sid])
        assert len(script.reflections) == 1
        assert out.minted == [] and out.reason == L.REASON_OFF
        assert _rows(app) == []
    finally:
        L.shutdown()


# --------------------------------------------------------------------------- #
# 7. the same for team notes' own switch: a distill answered after "off"
# --------------------------------------------------------------------------- #


async def test_a_distill_answered_after_team_notes_went_off_suggests_nothing(tmp_path):
    """Team notes' own switch, the same race: the reviewer's model was
    already writing the habits when the owner turned team notes off — off
    means no habit is suggested, so nothing is minted from that answer."""
    from iron_jarvis.platform import build_platform
    from tests.test_team_notes_v14230 import FakeRouter, _local_ollama, _members, _seed

    p = build_platform(str(tmp_path / "home"))
    _local_ollama(p)
    _members(p)

    class TurnedOffMeanwhile(FakeRouter):
        async def complete(self, **kw):
            out = await super().complete(**kw)
            p.config.team_notes = False
            return out

    p.router = TurnedOffMeanwhile()
    _seed(p, tmp_path)
    out = await TN.distill(p, WORKER)
    assert len(p.router.calls) == 1
    assert out.minted == [] and out.reason == TN.REASON_OFF
    assert TN.notes_for(p, [WORKER])[WORKER]["suggested"] == 0
