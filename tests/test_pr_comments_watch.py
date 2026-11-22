"""26-a (R2.16): the pull-request watcher reads REVIEW COMMENTS on its own
clock (``daemon/routes/ci_watch._read_comments``), driven pass by pass
(``ci_pass``) with an INJECTED clock (``pr_comments.utcnow``), a real git
repository and a FAKE ``gh`` on PATH (never the real GitHub).

Pinned:

* the first read waits five minutes; then each wait grows × 1.5 up to an
  hour while nothing new comes, and a new comment starts it again;
* reading goes on after the checks pass (and while fixing checks is off);
* a merged or closed pull request ends the reading; so do 30 quiet days and
  five failed reads in a row (said once on the session);
* NOTHING is read under Emergency stop, nor while the session is busy;
* new comments: a line on the session in Iron Jarvis's own words (the names
  ride the structured field, never the history) and at most ONE quiet bell
  row per pull request a day; "Stop telling me about comments here" makes no
  bell row (the comments still arrive in the session, where they are
  answered); a Local only session's row carries no words;
* while fixing checks is off the checks' own streak has not begun, so a
  watch made for the comments long ago never ends the checks' watch as "still
  running after 6 hours" once fixing is turned on.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import ci_watch as W
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import ci_fix as CI
from iron_jarvis.harness import pr_comments as PC
from iron_jarvis.harness import sessions as S
from tests.pr_comments_fakes import PR_URL, install, line_comment
from tests.test_build_git_v1375 import make_repo, new_branch_session, no_real_gh
from tests.test_build_sessions_v1367 import asgi, messages_of, setup_of

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

T0 = datetime(2026, 11, 22, 9, 0, 0)


class Clock:
    def __init__(self, monkeypatch) -> None:
        self.at = T0
        monkeypatch.setattr(PC, "utcnow", lambda: self.at)

    def go(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    no_real_gh(monkeypatch)
    W._REVIEWS.clear()
    W._LOCKS.clear()
    yield
    W._REVIEWS.clear()
    W._LOCKS.clear()
    B.forget_gh_state()


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


class World:
    def __init__(self, app, sid: str, fake) -> None:
        self.app, self.sid, self.fake = app, sid, fake
        self.d = app.state.d
        self.engine = app.state.platform.engine
        self.events: list[tuple[str, dict]] = []

    def rec(self) -> dict:
        return CI.get(self.engine, self.sid) or {}

    def reads(self) -> int:
        return len([c for c in self.fake.calls("api") if c[-1].endswith("/comments?per_page=100")])

    def lines(self) -> list[dict]:
        return [m for m in messages_of(self.app, self.sid) if isinstance(m.get("prComments"), dict)]

    def rows(self) -> list[tuple[str, dict]]:
        return [e for e in self.events if e[0] == EventType.BUILD_PR_COMMENTS]

    async def step(self) -> dict:
        return await W.ci_pass(self.d)


async def _world(app, tmp_path: Path, monkeypatch, *, state: str = CI.PASSING, local_only: bool = False) -> World:
    fake = install(tmp_path, monkeypatch)
    repo = make_repo(tmp_path / "proj")
    view = await new_branch_session(app, repo)
    sid = view["id"]
    facts = setup_of(app, sid)["git"]
    S.update(app.state.platform.engine, sid, setup={
        "git": {**facts, "pr_url": PR_URL, "pr_number": "7"}, **({"local_only": True} if local_only else {})})
    # The checks' own watch rests (they passed): only the comments are read.
    CI.ensure(app.state.platform.engine, sid, {**facts, "pr_url": PR_URL})
    CI.save(app.state.platform.engine, sid, state=state)
    fake.answer(comments=[])
    w = World(app, sid, fake)
    bus = app.state.platform.event_bus
    real = bus.publish

    async def spy(etype, payload=None, *a, **k):
        if str(etype) == EventType.BUILD_PR_COMMENTS:
            w.events.append((str(etype), dict(payload or {})))
        return await real(etype, payload, *a, **k)

    bus.publish = spy  # type: ignore[assignment]
    return w


async def test_the_first_read_waits_then_backs_off_and_a_new_comment_starts_it_again(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    assert w.reads() == 0, "the first read waits"
    clock.go(PC.READ_FIRST_S - 1)
    await w.step()
    assert w.reads() == 0
    clock.go(1)
    await w.step()
    assert w.reads() == 1
    waits = []
    for _ in range(8):
        rec = w.rec()
        nxt = datetime.fromisoformat(rec["comments_next_at"])
        waits.append((nxt - clock.at).total_seconds())
        clock.go(waits[-1] - 1)
        await w.step()
        assert w.reads() == len(waits), "read before its time"
        clock.go(1)
        await w.step()
    assert waits[0] == PC.READ_FIRST_S * PC.READ_GROWTH
    assert all(b >= a for a, b in zip(waits, waits[1:])) and waits[-1] == PC.READ_MAX_S
    # A new comment: the wait starts again from the first.
    w.fake.answer(comments=[line_comment(1)])
    clock.go(PC.READ_MAX_S)
    await w.step()
    assert len(w.lines()) == 1
    rec = w.rec()
    assert (datetime.fromisoformat(rec["comments_next_at"]) - clock.at).total_seconds() == PC.READ_FIRST_S


async def test_reading_goes_on_after_the_checks_pass_and_says_who_in_the_field_only(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch, state=CI.PASSING)
    w.fake.answer(comments=[line_comment(1, login="sam"), line_comment(2, login="sam", created="2026-11-20T10:01:00Z"),
                            line_comment(3, login="alex", created="2026-11-20T10:02:00Z")])
    await w.step()
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert w.rec()["state"] == CI.PASSING
    (line,) = w.lines()
    assert line["content"] == "3 review comments arrived on this session's pull request."
    assert "sam" not in line["content"] and line["prComments"]["authors"] == ["sam", "alex"]
    assert line["prComments"]["ids"] == [f"{w.sid}:c1", f"{w.sid}:c2", f"{w.sid}:c3"]
    ((_t, payload),) = w.rows()
    assert payload["count"] == 3 and payload["title"] == W.COMMENTS_HEADLINE
    assert "3 review comments from sam and alex arrived." in payload["body"]
    assert not w.fake.calls("pr", "view"), "the checks' own watch rests"


async def test_one_bell_row_per_pull_request_a_day_and_stop_telling_me(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    w.fake.answer(comments=[line_comment(1)])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert len(w.lines()) == 1 and len(w.rows()) == 1
    w.fake.answer(comments=[line_comment(1), line_comment(2, created="2026-11-21T10:00:00Z")])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert len(w.lines()) == 2 and len(w.rows()) == 1, "a second bell row within a day"
    w.fake.answer(comments=[line_comment(1), line_comment(2, created="2026-11-21T10:00:00Z"),
                            line_comment(3, created="2026-11-21T11:00:00Z")])
    clock.go(PC.TOLD_EVERY_S)
    await w.step()
    assert len(w.lines()) == 3 and len(w.rows()) == 2, "a day later the bell may say it again"
    # Stop telling me about comments here: no bell row. The comments still
    # ARRIVE in the session (its line is where they are answered: without it
    # a comment that came while it was on could never be answered there).
    status, got = await asgi(app, "POST", f"/build/sessions/{w.sid}/pr-comments/quiet", {"quiet": True})
    assert status == 200 and got == {"quiet": True, "was": False}
    w.fake.answer(comments=[line_comment(1), line_comment(4, created="2026-11-22T08:00:00Z")])
    clock.go(PC.TOLD_EVERY_S)
    await w.step()
    assert len(w.rows()) == 2, "a bell row after Stop telling me about comments here"
    assert len(w.lines()) == 4 and w.lines()[-1]["prComments"]["ids"] == [f"{w.sid}:c4"]
    assert f"{w.sid}:c4" in [r["id"] for r in PC.listing(w.engine, w.sid, PR_URL)]
    assert not w.rec()["comments_told_at"].startswith((clock.at.isoformat()[:16]))
    status, view = await asgi(app, "GET", f"/build/sessions/{w.sid}/pr-comments")
    assert status == 200 and view["quiet"] is True
    # Its Undo: the bell tells again from the next new comment.
    status, got = await asgi(app, "POST", f"/build/sessions/{w.sid}/pr-comments/quiet", {"quiet": False})
    assert got == {"quiet": False, "was": True}
    w.fake.answer(comments=[line_comment(5, created="2026-11-22T09:00:00Z")])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert len(w.lines()) == 5 and len(w.rows()) == 3


async def test_a_local_only_sessions_row_carries_no_words(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch, local_only=True)
    await w.step()
    w.fake.answer(comments=[line_comment(1, login="sam", body="private words")])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    ((_t, payload),) = w.rows()
    assert payload["body"] == W.LOCAL_ONLY_NOTICE and payload["session_title"] == ""
    assert "sam" not in str(payload) and "private" not in str(payload)


async def test_merged_or_closed_ends_the_reading(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    w.fake.answer(pr={"state": "closed", "merged_at": "2026-11-22T09:01:00Z"}, comments=[line_comment(1)])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert w.rec()["comments_done"] == PC.DONE_MERGED and w.lines() == []
    calls = len(w.fake.calls("api"))
    clock.go(PC.READ_MAX_S * 5)
    await w.step()
    assert len(w.fake.calls("api")) == calls, "read after it was merged"


async def test_nothing_is_read_under_emergency_stop_or_while_the_session_is_busy(app, tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes.build import _STARTING

    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    clock.go(PC.READ_FIRST_S)
    app.state.platform.config.autonomy_kill_switch = True
    try:
        await w.step()
        assert w.fake.calls("api") == []
    finally:
        app.state.platform.config.autonomy_kill_switch = False
    _STARTING.add(w.sid)
    try:
        await w.step()
        assert w.fake.calls("api") == []
    finally:
        _STARTING.discard(w.sid)
    await w.step()
    assert w.reads() == 1, "read once the switch was off and the session free"


async def test_reading_goes_on_while_fixing_checks_is_off(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch, state=CI.WATCHING)
    app.state.platform.config.ci_autofix = False
    await w.step()
    w.fake.answer(comments=[line_comment(1)])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert len(w.lines()) == 1 and not w.fake.calls("pr", "view")


async def test_nothing_is_read_while_a_fix_turn_runs_or_after_the_checks_saw_it_closed(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch, state=CI.FIXING)
    app.state.platform.config.ci_autofix = False  # only the comments' own step runs
    w.fake.answer(comments=[line_comment(1)])
    for state in (CI.FIXING, CI.REVIEWING, CI.CLOSED):
        CI.save(w.engine, w.sid, state=state)
        for _ in range(2):
            clock.go(PC.READ_MAX_S)
            await w.step()
        assert w.fake.calls("api") == [] and w.lines() == [], state
    # Any other state of the checks' watch: read on the comments' own clock.
    CI.save(w.engine, w.sid, state=CI.GAVE_UP)
    await w.step()
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert w.reads() == 1 and len(w.lines()) == 1


async def test_the_checks_streak_begins_when_fixing_is_turned_on(app, tmp_path, monkeypatch):
    """The watch's record may be made for the comments while fixing checks is
    off. Its checks were never watched, so when fixing is turned on hours
    later their streak begins THEN: the first check is never judged "still
    running after 6 hours" from the day the record was made."""
    seen: list[dict] = []

    async def spy_check(d, thread_id, title, facts, rec, out, *, local_only):
        seen.append(dict(rec))

    Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch, state=CI.WATCHING)
    monkeypatch.setattr(W, "_check", spy_check)
    # Made seven hours ago, while fixing was off.
    CI.save(w.engine, w.sid, watch_since=CI.later(-7 * 3600))
    app.state.platform.config.ci_autofix = False
    await w.step()
    assert seen == [] and not w.rec()["watch_since"], "the checks' streak began while they were not watched"
    app.state.platform.config.ci_autofix = True
    await w.step()
    assert len(seen) == 1 and seen[0]["watch_since"], "fixing on: the streak begins"
    assert not CI.watched_too_long(seen[0]), "judged from the day the record was made"
    assert not CI.watched_too_long(seen[0], CI.later(5 * 3600)) and CI.watched_too_long(seen[0], CI.later(7 * 3600))


async def test_five_failed_reads_stop_the_reading_said_once_and_thirty_quiet_days_end_it(app, tmp_path, monkeypatch):
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    w.fake.set(fail=True)
    for _ in range(PC.READ_FAILS_MAX):
        clock.go(PC.READ_MAX_S)
        await w.step()
    assert w.rec()["comments_done"] == PC.DONE_FAILED
    stopped = [m for m in w.lines() if m["prComments"].get("stopped")]
    assert len(stopped) == 1 and "could not be read 5 times in a row" in stopped[0]["content"]
    clock.go(PC.READ_MAX_S)
    await w.step()
    assert len(w.lines()) == 1
    # A fresh pull request of the same session reads afresh, then 30 quiet days end it.
    W._LOCKS.clear()
    facts = setup_of(app, w.sid)["git"]
    S.update(w.engine, w.sid, setup={"git": {**facts, "pr_url": "https://github.com/acme/proj/pull/8"}})
    w.fake.set(fail=False)
    w.fake.answer(comments=[], api="repos/acme/proj/pulls/8")
    await w.step()
    assert w.rec()["comments_done"] == "" and w.rec()["comments_next_at"]
    for _ in range(PC.READ_FOR_DAYS // 5):  # a read every five quiet days: not yet
        clock.go(5 * 24 * 3600)
        await w.step()
    assert w.rec()["comments_done"] == ""
    clock.go(5 * 24 * 3600)
    await w.step()
    assert w.rec()["comments_done"] == PC.DONE_QUIET


async def test_failed_reads_rest_a_day_then_read_again_until_one_works(app, tmp_path, monkeypatch):
    """Five failed reads in a row (an hour-long Wi-Fi drop, a GitHub outage)
    REST the reading, never end it: it is tried again a day later, a day
    apart, said once; the first read that works goes on as before and the
    comments that came meanwhile arrive."""
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    w.fake.set(fail=True)
    for _ in range(PC.READ_FAILS_MAX):
        clock.go(PC.READ_MAX_S)
        await w.step()
    assert w.rec()["comments_done"] == PC.DONE_FAILED
    tries = len(w.fake.calls("api"))
    clock.go(PC.READ_AGAIN_S - 1)
    await w.step()
    assert len(w.fake.calls("api")) == tries, "read again before the day was over"
    clock.go(1)
    await w.step()
    assert len(w.fake.calls("api")) == tries + 1, "never read again after the failed reads"
    assert w.rec()["comments_done"] == PC.DONE_FAILED
    assert len([m for m in w.lines() if m["prComments"].get("stopped")]) == 1, "said again"
    # GitHub answers again: the next day's read works, reading goes on.
    w.fake.set(fail=False)
    w.fake.answer(comments=[line_comment(1, login="sam")])
    clock.go(PC.READ_AGAIN_S)
    await w.step()
    rec = w.rec()
    assert rec["comments_done"] == "" and rec["comments_read_fails"] == 0
    assert w.lines()[-1]["prComments"]["ids"] == [f"{w.sid}:c1"]
    assert (datetime.fromisoformat(rec["comments_next_at"]) - clock.at).total_seconds() == PC.READ_FIRST_S


async def test_a_reply_started_while_github_is_read_is_never_split_by_the_line(app, tmp_path, monkeypatch):
    """The session was free when the read began; the user started a reply
    while gh ran. Nothing is kept and no line is written (it would land
    between the question and its reply); the next free pass reads again."""
    from iron_jarvis.daemon.routes.build import _STARTING

    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    w.fake.answer(comments=[line_comment(1)])
    clock.go(PC.READ_FIRST_S)
    real = PC.read

    def read_then_busy(facts):
        got = real(facts)
        _STARTING.add(w.sid)
        return got

    monkeypatch.setattr(PC, "read", read_then_busy)
    try:
        await w.step()
        assert w.reads() == 1
        assert w.lines() == [] and PC.listing(w.engine, w.sid) == [], "a line while a reply was starting"
    finally:
        _STARTING.discard(w.sid)
    monkeypatch.setattr(PC, "read", real)
    await w.step()
    assert w.reads() == 2 and len(w.lines()) == 1


async def test_the_comments_are_made_into_rows_off_the_event_loop(app, tmp_path, monkeypatch):
    """Making GitHub's items into rows masks every kept comment's words with
    every rule (seconds on a busy pull request): never on the event loop."""
    import asyncio

    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    real = PC.rows_from
    on_loop: list[bool] = []

    def spy(*a, **k):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a, **k)

    monkeypatch.setattr(PC, "rows_from", spy)
    await w.step()
    w.fake.answer(comments=[line_comment(1)])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert on_loop == [False], "made into rows on the event loop"
    assert len(w.lines()) == 1


async def test_deleting_the_session_takes_its_comments_and_its_watch(app, tmp_path, monkeypatch):
    """"Delete" means all of it: a deleted session's review comments (what
    people wrote and the code they quote) and its pull request's watch go
    with it, through either door."""
    clock = Clock(monkeypatch)
    w = await _world(app, tmp_path, monkeypatch)
    await w.step()
    w.fake.answer(comments=[line_comment(1, body="private words"), line_comment(2, created="2026-11-20T10:01:00Z")])
    clock.go(PC.READ_FIRST_S)
    await w.step()
    assert len(PC.listing(w.engine, w.sid)) == 2 and CI.get(w.engine, w.sid) is not None
    status, _ = await asgi(app, "DELETE", f"/build/sessions/{w.sid}")
    assert status == 200
    assert PC.listing(w.engine, w.sid) == [], "a deleted session's comments were kept"
    assert CI.get(w.engine, w.sid) is None, "a deleted session's watch was kept"
    # The chat's own delete door goes through the same delete.
    from iron_jarvis.daemon.routes.chat import delete_thread

    row = S.create(w.engine, str(tmp_path / "proj"), "Another", {"provider": "anthropic", "model": "claude-sonnet-4-5"})
    S.update(w.engine, row.id, setup={"git": {"worktree": str(tmp_path / "proj"), "pr_url": PR_URL}})
    rows, _ = PC.rows_from(row.id, PR_URL, [{**line_comment(3), "_kind": "line"}])
    PC.store(w.engine, row.id, PR_URL, rows)
    CI.ensure(w.engine, row.id, {"pr_url": PR_URL})
    status, _ = await asgi(app, "DELETE", f"/chat/threads/{row.id}")
    assert status == 200
    assert PC.listing(w.engine, row.id) == [] and CI.get(w.engine, row.id) is None
    assert delete_thread(w.engine, row.id) is False
