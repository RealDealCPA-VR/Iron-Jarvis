"""v1.423.0 (review of Wave 3 lane C, Team notes Phase 2) — the defects the
independent review found, each pinned red on the builder's code first.

1. THE DAILY LIMIT HELD ONLY ONE DISTILL AT A TIME. ``_distill`` checked
   ``attempted_recently`` and only saved the attempt several awaits later,
   right before the call: two coding sessions going idle together (or a
   session idling while another one is archived) each ran ``distill`` for the
   same member, both passed the check, and BOTH paid calls went out. The
   attempt is now CLAIMED atomically (one lock, re-read and written in one
   step) right before the call; a claim another distill already holds is the
   daily reason, and nothing is sent.
2. "THE ATTEMPT IS SAVED BEFORE THE CALL" did not hold when the save failed:
   ``_save_state`` swallowed the error and the call went out anyway — a
   database that refused the write gave no daily limit at all (a paid call on
   every idle period). A claim that was not written now sends nothing.
3. A ROLE BRIEF'S FOLDER THAT IS A LINK was written THROUGH whenever the link
   led somewhere inside the project (``_link_problem`` only refused a target
   outside the folder: a junction ``sub_agents`` → ``<project>/src`` put the
   brief into ``src``), and the brief was READ through any link before the
   link was checked (a junction to a network share is a sign-in sent to that
   host — the v1.379.0 lesson). A link or junction anywhere on the way
   (``.ironjarvis``, ``harness``, ``sub_agents``, the file) is now refused by
   ``lstat`` — never resolved, never read through — before anything is read.
4. The name rule took a trailing newline (``re.match`` with ``$``):
   ``sub_agents/code\\n.md`` passed ``editable_file``.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

from iron_jarvis.agents import team_notes as TN
from tests.test_team_notes_v14230 import (
    HABIT,
    WORKER,
    FakeRouter,
    _app,
    _local_ollama,
    _members,
    _seed,
)
from iron_jarvis.platform import build_platform


@pytest.fixture
def plat(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    _local_ollama(p)
    _members(p)
    p.router = FakeRouter()
    return p


class SlowRouter(FakeRouter):
    """A reviewer model that takes a moment to answer (a real call does)."""

    async def complete(self, **kw):
        await asyncio.sleep(0.2)
        return await super().complete(**kw)


# --------------------------------------------------------------------------- #
# 1. two distills of one member at once: ONE call
# --------------------------------------------------------------------------- #


async def test_two_distills_of_one_member_at_once_make_one_call(plat, tmp_path, monkeypatch):
    _seed(plat, tmp_path)
    plat.router = SlowRouter()
    # Both distills are held together past the early checks (the order two
    # sessions idling at the same moment produce on a busy loop).
    barrier = threading.Barrier(2, timeout=10)
    real = TN.reviewer_model

    def held(platform, findings):
        barrier.wait()
        return real(platform, findings)

    monkeypatch.setattr(TN, "reviewer_model", held)
    a, b = await asyncio.gather(TN.distill(plat, WORKER), TN.distill(plat, WORKER))
    assert len(plat.router.calls) == 1, [c["provider"] for c in plat.router.calls]
    reasons = sorted([a.reason, b.reason])
    assert reasons[0] in ("", TN.REASON_NOTHING) and reasons[1] == TN.REASON_DAILY, reasons
    assert len(a.minted) + len(b.minted) == 1


async def test_two_sessions_idling_together_distill_a_member_once(plat, tmp_path, monkeypatch):
    """The pacing's own way in: two sessions where the member worked go idle
    at once — one paid call, never two."""
    threads = _seed(plat, tmp_path)
    plat.router = SlowRouter()
    barrier = threading.Barrier(2, timeout=10)
    real = TN.reviewer_model

    def held(platform, findings):
        barrier.wait()
        return real(platform, findings)

    monkeypatch.setattr(TN, "reviewer_model", held)
    out = await asyncio.gather(*(TN.distill_session(plat, t) for t in threads))
    assert len(plat.router.calls) == 1
    assert sum(len(d.minted) for run in out for d in run) == 1


def test_the_claim_reads_and_writes_in_one_step(plat, monkeypatch):
    """Two claims of a member whose last attempt is older than a day, their
    READS held together: one claims, the other finds the day taken. (Without
    one lock around the read and the write, both read the old stamp and both
    calls go out.)"""
    from datetime import timedelta

    from iron_jarvis.core.ids import utcnow

    TN._save_state(plat.engine, WORKER, last_attempt_at=utcnow() - timedelta(days=2))
    together = threading.Barrier(2)
    real = TN._naive

    def held(at):
        if sys._getframe(1).f_code.co_name == "claim_attempt" and at is not None:
            try:
                together.wait(timeout=1.0)
            except threading.BrokenBarrierError:
                pass
        return real(at)

    monkeypatch.setattr(TN, "_naive", held)
    answers: list[str] = []
    workers = [threading.Thread(target=lambda: answers.append(TN.claim_attempt(plat.engine, WORKER)))
               for _ in range(2)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(10)
    assert sorted(answers) == ["", TN.REASON_DAILY], answers


async def test_the_reviewers_own_account_near_its_limit_is_not_spent(plat, tmp_path, monkeypatch):
    """The account the distill would lease for the reviewer (its member
    account) is at 85% of a window: nothing is sent, and the day is NOT
    taken (nothing was spent — a later idle period may try)."""
    from iron_jarvis.agents import model_team as MT
    from iron_jarvis.harness import build_accounts as BA
    from iron_jarvis.harness import library
    from iron_jarvis.iron_proxy.pin import AccountPin

    _seed(plat, tmp_path)
    pin = AccountPin(profile_id="acc-busy", ij_provider="claude-cli", title="Work")
    monkeypatch.setattr(MT, "member_account", lambda platform, record, caller=None, **kw: (pin, ""))
    monkeypatch.setattr(BA, "find", lambda svc, account_id: (
        {"id": account_id, "status": BA.USAGE, "usage": {"percent": 85}} if account_id == "acc-busy" else None))
    out = await TN.distill(plat, WORKER)
    assert plat.router.calls == [] and out.minted == []
    assert out.reason == library.REASON_ACCOUNT_BUSY.format(percent=85)
    assert not TN.attempted_recently(plat.engine, WORKER)


# --------------------------------------------------------------------------- #
# 2. an attempt that could not be saved sends nothing
# --------------------------------------------------------------------------- #


def _refuse_state_writes(home: Path) -> None:
    db = sqlite3.connect(str(home / "ironjarvis.db"))
    try:
        for when in ("INSERT", "UPDATE"):
            db.execute(
                f"CREATE TRIGGER no_state_{when.lower()} BEFORE {when} ON teamnotestate "
                "BEGIN SELECT RAISE(ABORT, 'the disk refused the write'); END"
            )
        db.commit()
    finally:
        db.close()


async def test_an_attempt_that_could_not_be_saved_sends_nothing(plat, tmp_path):
    _seed(plat, tmp_path)
    _refuse_state_writes(Path(plat.config.home))
    out = await TN.distill(plat, WORKER)
    assert plat.router.calls == [], "the call went out with no daily limit kept"
    assert out.minted == [] and out.reason == TN.REASON_NOT_SAVED
    # Again and again: still nothing (no paid call per idle period).
    assert (await TN.distill(plat, WORKER)).reason == TN.REASON_NOT_SAVED
    assert plat.router.calls == []


# --------------------------------------------------------------------------- #
# 3. a link on the way to a role brief: never read through, never written
# --------------------------------------------------------------------------- #

_win = pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows link")


@_win
def test_a_role_briefs_folder_that_is_a_junction_inside_the_project_is_never_written(tmp_path, monkeypatch):
    import _winapi

    from iron_jarvis.harness import library

    app, p = _app(tmp_path)
    threads = _seed(p, tmp_path)
    work = tmp_path / "ws1"
    inner = work / "src"
    inner.mkdir()
    (inner / "code.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")
    hdir = work / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    _winapi.CreateJunction(str(inner), str(hdir / "sub_agents"))
    reads: list[str] = []
    real_read = library.read_layer_file

    def spy(folder, name):
        reads.append(name)
        return real_read(folder, name)

    monkeypatch.setattr(library, "read_layer_file", spy)
    row, why = library.propose_role_brief_edit(p, thread_id=threads[1], role="code", line=HABIT, why="w",
                                                evidence={})
    assert row is None and "leads outside the folder" in why, why
    assert reads == [], f"read through the link before it was checked: {reads}"
    assert (inner / "code.md").read_text(encoding="utf-8") == "---\ndescription: d\n---\n"


@_win
def test_an_accept_never_writes_through_a_junction_that_points_inside(tmp_path, monkeypatch):
    import _winapi

    from iron_jarvis.harness import library

    app, p = _app(tmp_path)
    threads = _seed(p, tmp_path)
    work = tmp_path / "ws1"
    hdir = work / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    row, why = library.propose_role_brief_edit(p, thread_id=threads[1], role="code", line=HABIT, why="w",
                                                evidence={})
    assert row is not None, why
    inner = work / "docs"
    inner.mkdir()
    _winapi.CreateJunction(str(inner), str(hdir / "sub_agents"))
    seen: list = []
    real = library._atomic_write

    def spy(path, text):
        seen.append(path)
        return real(path, text)

    monkeypatch.setattr(library, "_atomic_write", spy)
    with pytest.raises(library.LibraryRefused, match="leads outside the folder"):
        library.accept(p.engine, row.id)
    assert seen == [] and list(inner.iterdir()) == []
    assert library.get(p.engine, row.id).status == "proposed"


@_win
def test_a_harness_folder_junction_inside_the_project_is_refused_too(tmp_path):
    import _winapi

    from iron_jarvis.harness import library

    work = tmp_path / "proj"
    (work / "real").mkdir(parents=True)
    _winapi.CreateJunction(str(work / "real"), str(work / ".ironjarvis"))
    assert "leads outside the folder" in library._link_problem(str(work), "memory.md")
    assert "leads outside the folder" in library._link_problem(str(work), "sub_agents/code.md")


def test_a_role_nouns_brief_that_is_a_link_is_never_read_through(tmp_path, monkeypatch):
    """``sub_agents`` is a plain folder, but the role NOUN's brief
    (``coder.md``) is a file link to a file elsewhere: it is neither read nor
    written through."""
    import os

    from iron_jarvis.harness import library

    app, p = _app(tmp_path)
    threads = _seed(p, tmp_path)
    agents = tmp_path / "ws1" / ".ironjarvis" / "harness" / "sub_agents"
    agents.mkdir(parents=True)
    away = tmp_path / "away.md"
    away.write_text("---\ndescription: d\n---\n", encoding="utf-8")
    try:
        os.symlink(away, agents / "coder.md")
    except OSError:
        pytest.skip("no symlink can be made here")
    reads: list[str] = []
    real_read = library.read_layer_file

    def spy(folder, name):
        reads.append(name)
        return real_read(folder, name)

    monkeypatch.setattr(library, "read_layer_file", spy)
    row, why = library.propose_role_brief_edit(p, thread_id=threads[1], role="code", line=HABIT, why="w",
                                                evidence={})
    assert row is None and "leads outside the folder" in why, why
    assert "sub_agents/coder.md" not in reads, reads
    assert away.read_text(encoding="utf-8") == "---\ndescription: d\n---\n"


def test_a_plain_folder_is_not_a_link_problem(tmp_path):
    from iron_jarvis.harness import library

    work = tmp_path / "proj"
    (work / ".ironjarvis" / "harness" / "sub_agents").mkdir(parents=True)
    assert library._link_problem(str(work), "sub_agents/code.md") == ""
    assert library._link_problem(str(work), "memory.md") == ""
    assert library._link_problem(str(tmp_path / "fresh"), "sub_agents/code.md") == ""


async def test_a_habit_from_a_local_only_sessions_findings_never_becomes_a_role_brief(tmp_path):
    """A role brief rides EVERY helper of that role in its folder — cloud
    ones too — so a habit distilled (on this network) from a Local only
    session's findings is never proposed into one. The habit itself is kept
    for its member as accepted."""
    from fastapi.testclient import TestClient

    from iron_jarvis.harness import library

    app, p = _app(tmp_path)
    p.agents_registry.register("kimi", "You are kimi.", [], provider="ollama", model="kimi", roles=["code"])
    threads = _seed(p, tmp_path)
    from iron_jarvis.harness import sessions as S

    S.update(p.engine, threads[0], setup={"local_only": True})
    from tests.test_team_notes_v14230 import _accepted

    _accepted(p, "kimi", "Run the tests before reporting a piece as done")
    out = await TN.distill(p, WORKER)
    assert out.minted, out.reason
    r = TestClient(app).post(f"/coach/proposals/{out.minted[0].id}/accept")
    assert r.status_code == 200, r.text
    assert [n.text for n in TN.active_notes(p.engine, WORKER)] == [HABIT]
    for t in threads:
        assert library.view(p.engine, str(tmp_path / f"ws{threads.index(t)}"))["proposals"] == []


async def test_with_team_notes_off_an_accept_suggests_no_role_brief(tmp_path):
    """Off means "no habits are suggested or shown": accepting a habit that
    was waiting from before keeps it for its member, and suggests NO edit of
    a role's brief (which every helper of the role would be shown)."""
    from fastapi.testclient import TestClient

    from iron_jarvis.harness import library
    from tests.test_team_notes_v14230 import _accepted

    app, p = _app(tmp_path)
    p.agents_registry.register("kimi", "You are kimi.", [], provider="ollama", model="kimi", roles=["code"])
    _seed(p, tmp_path)
    _accepted(p, "kimi", "Run the tests before reporting a piece as done")
    (row,) = (await TN.distill(p, WORKER)).minted
    p.config.team_notes = False
    r = TestClient(app).post(f"/coach/proposals/{row.id}/accept")
    assert r.status_code == 200, r.text
    assert TN.OFF_SENTENCE in r.json().get("note", "")
    assert [n.text for n in TN.active_notes(p.engine, WORKER)] == [HABIT]
    for i in range(2):
        assert library.view(p.engine, str(tmp_path / f"ws{i}"))["proposals"] == []


def test_a_team_notes_file_larger_than_any_iron_jarvis_writes_is_never_read_whole(plat, monkeypatch):
    """TEAM-NOTES.md is read on every team piece's start (once a reply), on
    GET /team and on each render. A file far larger than any Iron Jarvis
    writes (a helper's shell can write there) is not read whole: it is the
    user's (kept, never rewritten) and no section is built from it."""
    import json as _json
    from pathlib import Path as _Path

    from iron_jarvis.core.models import Session
    from iron_jarvis.harness.helpers import BUILD_SESSION_OPTION, HELPER_ORIGIN
    from tests.test_team_notes_v14230 import _accepted

    _accepted(plat)
    path = TN.notes_path(plat, WORKER)
    path.write_text(TN.NOTES_TITLE + "\n\n- " + ("work carefully " * 40000) + "\n", encoding="utf-8")
    biggest: list[int] = []
    real = _Path.read_bytes

    def spy(self):
        data = real(self)
        if self == path:
            biggest.append(len(data))
        return data

    monkeypatch.setattr(_Path, "read_bytes", spy)
    helper = Session(agent_name=f"custom:{WORKER}", origin=HELPER_ORIGIN,
                     options_json=_json.dumps({BUILD_SESSION_OPTION: "t-big"}))
    assert TN.habits_for_run(plat, helper) == ""
    assert TN.hand_edited(plat, WORKER) is True
    assert TN.render(plat, WORKER).state == "kept"
    assert biggest == [], f"read the whole file: {biggest}"


def test_a_declined_role_brief_edit_rests_fourteen_days_and_the_open_limit_holds(tmp_path):
    """The library's own rules hold for a role brief: a declined edit is not
    suggested again for 14 days (then it may be), one already waiting is not
    suggested twice, and a folder with its open limit reached gets none."""
    from datetime import timedelta

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import utcnow
    from iron_jarvis.harness import library
    from iron_jarvis.harness.library_models import HarnessLibraryRecord

    app, p = _app(tmp_path)
    threads = _seed(p, tmp_path)

    def propose():
        return library.propose_role_brief_edit(p, thread_id=threads[1], role="code", line=HABIT, why="w",
                                               evidence={})

    row, why = propose()
    assert row is not None, why
    assert propose() == (None, library.REASON_SUPPRESSED)  # waiting already
    library.decline(p.engine, row.id)
    assert propose() == (None, library.REASON_SUPPRESSED)  # declined: rests
    with session_scope(p.engine) as db:
        live = db.get(HarnessLibraryRecord, row.id)
        live.decided_at = utcnow() - timedelta(days=15)
        db.add(live)
        db.commit()
    again, why = propose()
    assert again is not None, why  # 14 days later it may be suggested again
    for i in range(library.OPEN_LIMIT - 1):
        library._mint(p.engine, HarnessLibraryRecord(
            folder_key=again.folder_key, folder=again.folder, kind=library.KIND_LESSON, text=f"l{i}",
            signature=f"s{i}", thread_id=threads[1], turn_id="",
        ))
    library.decline(p.engine, again.id)
    library._mint(p.engine, HarnessLibraryRecord(
        folder_key=again.folder_key, folder=again.folder, kind=library.KIND_LESSON, text="last",
        signature="s-last", thread_id=threads[1], turn_id="",
    ))
    assert propose() == (None, library.REASON_FULL)


async def test_a_habit_the_owner_removed_is_not_suggested_again_from_the_same_findings(plat, tmp_path):
    """Remove is the owner saying "not this": its findings rest
    :data:`SUPPRESS_DAYS` from the REMOVAL, like a decline — not from the
    old acceptance (a habit accepted three weeks ago and removed today was
    suggested again from the same findings at the next idle period)."""
    from datetime import timedelta

    from iron_jarvis.coach.models import CoachProposalRecord
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import utcnow

    _seed(plat, tmp_path)
    (row,) = (await TN.distill(plat, WORKER)).minted
    TN.accept(plat, row.id)
    with session_scope(plat.engine) as db:
        live = db.get(CoachProposalRecord, row.id)
        live.decided_at = utcnow() - timedelta(days=21)
        db.add(live)
        db.commit()
    (note,) = TN.active_notes(plat.engine, WORKER)
    TN.remove(plat, note.id)
    TN._save_state(plat.engine, WORKER, last_attempt_at=utcnow() - timedelta(days=2))
    plat.router.calls.clear()
    out = await TN.distill(plat, WORKER)
    assert out.minted == [], [r.after for r in out.minted]
    # Once the rest is over (14 days after the removal), it may come back.
    with session_scope(plat.engine) as db:
        gone = db.get(TN.TeamNote, note.id)
        gone.removed_at = utcnow() - timedelta(days=TN.SUPPRESS_DAYS + 1)
        db.add(gone)
        db.commit()
    TN._save_state(plat.engine, WORKER, last_attempt_at=utcnow() - timedelta(days=2))
    assert len((await TN.distill(plat, WORKER)).minted) == 1


async def test_the_newest_folder_is_chosen_when_findings_share_a_clock_tick(tmp_path):
    """On Windows ``utcnow`` ticks every 15.6 ms, so findings recorded
    together share ``created_at`` (the v1.286.0 rule: break a tie on the
    rowid). The role brief goes to the folder of the NEWEST finding — the
    second session's here — never the first one's because the tie fell the
    database's way (the builder's own pin failed so under a loaded run)."""
    from fastapi.testclient import TestClient

    from iron_jarvis.agents.team_findings import TeamFinding
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.harness import library
    from tests.test_team_notes_v14230 import _accepted

    app, p = _app(tmp_path)
    p.agents_registry.register("kimi", "You are kimi.", [], provider="ollama", model="kimi", roles=["code"])
    threads = _seed(p, tmp_path)
    with session_scope(p.engine) as db:
        rows = list(db.exec(__import__("sqlmodel").select(TeamFinding)))
        tick = rows[0].created_at
        for r in rows:
            r.created_at = tick
            db.add(r)
        db.commit()
    _accepted(p, "kimi", "Run the tests before reporting a piece as done")
    (row,) = (await TN.distill(p, WORKER)).minted
    r = TestClient(app).post(f"/coach/proposals/{row.id}/accept")
    assert r.status_code == 200, r.text
    assert library.view(p.engine, str(tmp_path / "ws0"))["proposals"] == []
    (edit,) = library.view(p.engine, str(tmp_path / "ws1"))["proposals"]
    assert edit["thread_id"] == threads[1]


def test_a_reviewer_with_no_model_of_its_own_uses_its_newest_review_on_a_clock_tick(plat, tmp_path):
    """The model a reviewer's newest cited review ran on — the newest by
    rowid when two reviews share a ``created_at`` tick."""
    from iron_jarvis.agents.team_record import TeamRoleRun
    from iron_jarvis.core.db import session_scope

    _seed(plat, tmp_path)
    plat.agents_registry.register("rev", "You are rev.", [], roles=["review"])  # no provider
    with session_scope(plat.engine) as db:
        runs = list(db.exec(__import__("sqlmodel").select(TeamRoleRun).where(TeamRoleRun.role == "review")))
        tick = runs[0].created_at
        for i, r in enumerate(sorted(runs, key=lambda r: r.session_id)):
            r.created_at = tick
            r.model = f"qwen3:{i}"
            db.add(r)
        db.commit()
    found = TN.eligibility(plat.engine, WORKER).findings
    rev = TN.reviewer_model(plat, found)
    assert (rev.provider, rev.model, rev.own) == ("ollama", "qwen3:1", False)


# --------------------------------------------------------------------------- #
# 4. the name rule is the WHOLE name
# --------------------------------------------------------------------------- #


def test_the_role_brief_name_rule_takes_no_trailing_newline():
    from iron_jarvis.harness.library import editable_file

    assert editable_file("sub_agents/code.md")
    for bad in ("sub_agents/code\n.md", "sub_agents/code\r.md", "memory.md\n"):
        assert not editable_file(bad), repr(bad)
