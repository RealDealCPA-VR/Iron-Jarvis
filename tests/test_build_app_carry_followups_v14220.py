"""v1.422.0 (Wave 2, B3 + B5) review FOLLOW-UPS — the coordinator's three:

1. UPGRADE DAY (decided: conservative). A reply cut off BEFORE the first
   start of a daemon that can carry replies on — every cut v1.421.0 and
   earlier left, the cut the update to v1.422.0 itself made included — is
   only OFFERED (Continue, with a plain reason), never carried on by itself.
   The first carry pass writes ``<home>/hosted/carry_on_since.json`` (the
   time it began); a cut is carried on by itself only when its own time is at
   or after it. A marker that cannot be read is written again with the time
   now — it never makes an older cut carry on.
2. A session that is ALIVE at pass time is never carried on: a running
   process (the tool survived the restart — another part's host still runs
   it) or a running turn. ``reconcile_cut_turns(home, keep=…)`` clears an
   undecided cut record of a kept session, so no later pass acts on it. The
   wait pass never starts a carry-on for a session whose tool is still
   working by itself.
3. Retry on a carry-on that FAILED re-runs the carry-on — hidden, the same
   ``carry`` (marked pressed) — never the carry-on's own words sent as the
   user's visible message.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted.events import make

from ._build_app_helpers_v1409 import client, stop_all, until
from .test_build_app_carry_on_v14220 import _ev, _graceful, _log, _row, _view, _working
from .test_build_app_history_v1418 import fake_tool
from .test_build_app_limits_v14220 import NOW, _limit, _session


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "fresh"))


@pytest.fixture
def home(app) -> Path:
    return Path(app.state.platform.config.home)


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


class _Proc:
    """A tool process that is still running (it survived the restart)."""

    alive = True

    def stop(self):
        self.alive = False


# --------------------------------------------------------------------------- #
# 1. upgrade day
# --------------------------------------------------------------------------- #
async def test_a_cut_left_before_the_first_carry_pass_is_offered_and_the_marker_is_written(
        home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _graceful())  # the update to this version cut it
    S.reconcile_cut_turns(home)
    assert not CO.since_path(home).exists(), "no daemon that carries replies on has run here"
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == [], "a cut from before this version carried on by itself"
    assert drivers == {}
    assert CO.read_cut(home, row.id)["why_not"] == CO.WHY_BEFORE
    since = CO.read_since(home)
    assert since is not None and since >= CO._parse(CO.read_cut(home, row.id)["cut_at"])
    view = await _view(app, row.id)
    assert view["cut"]["why_not"] == CO.WHY_BEFORE and view["cut"]["resumable"] is True
    # The NEXT cut (this version running, then cut again) carries on by itself.
    with open(Path(home) / "hosted" / row.id / "events.jsonl", "a", encoding="utf-8") as fh:
        for e in _working("t2") + [{**S.W.error(S.STOPPED_TITLE, S.STOPPED_FIX, ""), "turn": "t2"},
                                   _ev("done", "t2", stop="interrupted")]:
            fh.write(json.dumps({**e, "at": (since + timedelta(seconds=5)).isoformat()}) + "\n")
    S.reconcile_cut_turns(home)
    assert await CO.carry_on_after_restart(svc) == [row.id]
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    assert CO.read_since(home) == since, "the marker is written once"
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_an_unreadable_marker_never_lets_an_older_cut_carry_on(home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _graceful())
    S.reconcile_cut_turns(home)
    CO.since_path(home).parent.mkdir(parents=True, exist_ok=True)
    CO.since_path(home).write_text("{not json", encoding="utf-8")
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert drivers == {} and CO.read_cut(home, row.id)["why_not"] == CO.WHY_BEFORE
    assert CO.read_since(home) is not None, "the marker is written again"


async def test_the_loop_writes_the_marker_even_when_nothing_was_cut(home, app, monkeypatch):
    """Else the first cut after the upgrade (the next update) would meet no
    marker, write one then, and be offered instead of carried on."""
    ticks: list = []
    task = asyncio.ensure_future(CO.carry_loop(app.state.platform, lambda ok, exc: ticks.append((ok, exc))))
    try:
        await until(lambda: CO.since_path(home).exists())
        assert CO.read_since(home) is not None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --------------------------------------------------------------------------- #
# 2. a session alive at pass time is never carried on
# --------------------------------------------------------------------------- #
def _marker_before(home: Path) -> None:
    CO.since_path(home).parent.mkdir(parents=True, exist_ok=True)
    CO.since_path(home).write_text(
        json.dumps({"since": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}), encoding="utf-8")


async def test_a_session_whose_tool_still_runs_is_never_carried_on_and_its_record_goes(
        home, app, work, monkeypatch):
    _marker_before(home)
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    live = svc._live(S.store.get(app.state.platform.engine, row.id))
    live.proc = _Proc()  # the tool is alive (another part's host kept it)
    assert await CO.carry_on_after_restart(svc) == [], "carried on a session whose tool is still running"
    assert drivers == {}
    assert CO.read_cut(home, row.id) == {}, "the record of a session that was never cut stays"
    await stop_all(app)


async def test_reconcile_clears_an_undecided_record_of_a_kept_session(home, app, work, monkeypatch):
    """An older start found the cut (and, say, Emergency stop kept it
    undecided); now a surviving host still runs that session (``keep``)."""
    _marker_before(home)
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    assert CO.read_cut(home, row.id)["auto"] == ""
    S.reconcile_cut_turns(home, keep=[row.id])
    assert CO.read_cut(home, row.id) == {}, "a kept session's undecided record survived the reconcile"
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert drivers == {}
    await stop_all(app)


async def test_reconcile_keeps_a_kept_sessions_decided_record(home, app, work, monkeypatch):
    """A record already DECIDED (carried, with the turn it started) is never
    acted on again by a pass; it stays — its ``to`` is how a later cut of that
    carry-on is known to be one."""
    _marker_before(home)
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    CO._amend_cut(home, row.id, "t1", auto="carried", to="t2")
    S.reconcile_cut_turns(home, keep=[row.id])
    assert CO.read_cut(home, row.id)["to"] == "t2"


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(CO, "CLOCK", lambda: NOW)
    return NOW


@pytest.mark.parametrize("how", ["working_alone", "replying_alone"])
async def test_the_wait_pass_never_starts_a_carry_on_while_the_tool_works_by_itself(
        app, home, work, monkeypatch, clock, how):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    before = CO.read_limits(home, row.id)
    live = svc.live[row.id]
    live.proc = _Proc()
    setattr(drivers[row.id], "busy_alone" if how == "working_alone" else "replying_alone", True)
    live.last_heard = svc.clock()
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
    assert len(drivers[row.id].sent) == 1
    assert CO.read_limits(home, row.id) == before, "the wait was taken (and counted) while the tool worked"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 3. Retry on a carry-on that failed re-runs the carry-on, hidden
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("why,text", [
    ("restart", CO.CONTINUE_AFTER_RESTART),
    ("reset", CO.CONTINUE_AFTER_RESET),
    ("move", CO.CONTINUE_AFTER_MOVE),
])
async def test_retry_on_a_failed_carry_on_reruns_it_hidden(home, app, work, monkeypatch, why, text):
    row = _row(app, work)
    _log(home, row.id, _working("t1") + [
        _ev("user", "t2", text=text, carry={"why": why}),
        _ev("error", "t2", title="Claude Code couldn't finish this answer", fix="Press Retry.", technical=""),
        _ev("done", "t2", stop="failed"),
    ])
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue")
        assert r.status_code == 200, r.text
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    assert drivers[row.id].sent[0][0] == text
    view = await _view(app, row.id)
    users = [e for e in view["events"] if e["type"] == "user"]
    assert users[-1]["carry"]["why"] == why and users[-1]["carry"]["pressed"] is True
    assert users[-1]["carry"]["retry"] is True
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_continue_still_refuses_when_the_newest_reply_was_the_users_own(home, app, work):
    row = _row(app, work)
    _log(home, row.id, _working() + [
        _ev("error", "t1", title="Claude Code couldn't finish this answer", fix="Press Retry.", technical=""),
        _ev("done", "t1", stop="failed"),
    ])
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue")
    assert r.status_code == 409 and r.json()["detail"] == CO.NOTHING_CUT


@pytest.mark.parametrize("ending", ["completed", "stopped_by_user", "account_limit"])
async def test_continue_never_reruns_a_carry_on_that_did_not_fail(home, app, work, monkeypatch, ending):
    """Only a carry-on that FAILED is run again: one that finished, one the
    user stopped, and one that stopped on an account's limit (its own card
    and press) are not."""
    tail = {
        "completed": [_ev("text_done", "t2", item="a1", text="Done."), _ev("done", "t2", stop="completed")],
        "stopped_by_user": [_ev("done", "t2", stop="interrupted")],
        "account_limit": [_ev("error", "t2", title="Claude Code hit this account's limit", fix="", technical="",
                              fix_kind="account_limit"), _ev("done", "t2", stop="failed")],
    }[ending]
    row = _row(app, work)
    _log(home, row.id, _working("t1") + [_ev("user", "t2", text=CO.CONTINUE_AFTER_RESTART, carry={"why": "restart"})] + tail)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue")
    assert r.status_code == 409 and r.json()["detail"] == CO.NOTHING_CUT
    assert drivers == {}
