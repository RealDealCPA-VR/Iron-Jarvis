"""v1.422.0 (Wave 2, B3 + B5) REVIEW: what the independent review of Build ›
Code's carry-on found, each pinned RED on the builder's code first.

1. A reply cut off LONG AGO (a log whose newest turn a shutdown ended weeks
   ago — every reply an earlier update cut off and the user never came back
   to) was carried on BY ITSELF at the first start that read it: the boot
   reconcile recorded every such cut with no age, and the pass spent the
   user's plan on it. Chat › Code keeps a cut-off reply for
   ``harness/reply_record.KEEP_DAYS`` (7) only; Build › Code now carries on by
   itself only a cut that recent, and offers Continue (with the reason) for an
   older one.
2. A reply the USER STOPPED, when the app went before the coding tool
   confirmed the Stop (Stop, then Quit or Restart to update at once), was
   written as cut by Iron Jarvis's own stop and carried on by itself at the
   next start — "never for a reply the user stopped". The Stop press is now
   kept beside the log, and the reconcile never records that turn as cut.
3. With "Carry on by itself after an account's limit" turned OFF while a
   wait was already armed, the wait still carried the reply on (or moved it
   to another account) by itself. OFF means nothing carries on by itself: the
   loop now ends such a wait; the card and its press stay.
4. The restart pass judged "too many coding sessions at once" by PROCESSES
   running, so the replies it had just started (their tool not up yet)
   counted for nothing: more than six were started, and the seventh failed at
   once with its record already used up. It now counts every session that is
   working.
5. Words: a reply cut beside a limit card with NO wait said "it waits on its
   account's limit" (nothing waited).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.events import make

from ._build_app_helpers_v1409 import client, stop_all, until
from .test_build_app_carry_on_v14220 import _graceful, _log, _row, _view, _working
from .test_build_app_history_v1418 import FakeDriver, fake_tool
from .test_build_app_limits_v14220 import NOW, _limit, _session


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "root"))


@pytest.fixture
def home(app) -> Path:
    return Path(app.state.platform.config.home)


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _aged(events: list[dict], days: float) -> list[dict]:
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    return [{**e, "at": when} for e in events]


# --------------------------------------------------------------------------- #
# 1. a cut from long ago is offered, never carried on by itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("how", ["graceful", "crash"])
async def test_a_reply_cut_off_long_ago_is_offered_never_carried_on_by_itself(home, app, work, monkeypatch, how):
    row = _row(app, work)
    _log(home, row.id, _aged(_graceful() if how == "graceful" else _working(), days=30))
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == [], "a month-old reply was carried on by itself"
    assert drivers == {}
    assert CO.read_cut(home, row.id)["why_not"] == CO.WHY_OLD
    view = await _view(app, row.id)
    assert view["cut"]["why_not"] == CO.WHY_OLD and view["cut"]["resumable"] is True
    # The PRESS still carries it on (a person's choice).
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue")
        assert r.status_code == 200, r.text
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    assert drivers[row.id].sent[0][0] == CO.CONTINUE_AFTER_RESTART
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_a_reply_cut_off_days_ago_within_the_kept_week_still_carries_on(home, app, work, monkeypatch):
    """The bound is Chat › Code's own (a PC off over a weekend still carries on)."""
    from iron_jarvis.harness.reply_record import KEEP_DAYS

    assert CO.CUT_KEEP_DAYS == KEEP_DAYS
    row = _row(app, work)
    _log(home, row.id, _aged(_graceful(), days=3))
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == [row.id]
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_a_cut_whose_time_is_not_known_is_offered_never_carried_on(home, app, work, monkeypatch):
    """No time on the reply's events (or a record kept without one): its age
    is NOT known, so it is never carried on by itself."""
    row = _row(app, work)
    _log(home, row.id, [{k: v for k, v in e.items() if k != "at"} for e in _graceful()])
    S.reconcile_cut_turns(home)
    assert CO.read_cut(home, row.id)["cut_at"] == ""
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert drivers == {} and CO.read_cut(home, row.id)["why_not"] == CO.WHY_OLD


async def test_the_cuts_age_is_its_own_not_the_reconciles_closing_rows(home, app, work, monkeypatch):
    """A crash: the reconcile appends its own error + done NOW — the age is
    read from the reply's own events, before those rows."""
    row = _row(app, work)
    _log(home, row.id, _aged(_working(), days=30))
    S.reconcile_cut_turns(home)
    rec = CO.read_cut(home, row.id)
    assert CO._parse(rec["cut_at"]) < datetime.now(timezone.utc) - timedelta(days=29)


# --------------------------------------------------------------------------- #
# 2. a reply the user stopped is never carried on, even when the app went
#    before the tool confirmed the Stop
# --------------------------------------------------------------------------- #
class _SlowStop(FakeDriver):
    """A coding tool that has not confirmed a Stop yet when the app goes."""

    async def interrupt(self):
        self.interrupted = True


class _Proc:
    alive = True

    def stop(self):
        self.alive = False


def _slow_tool(svc, monkeypatch) -> dict:
    drivers: dict = {}

    async def ensure(live, row):
        if live.driver is None:
            live.driver = drivers.setdefault(live.sid, _SlowStop(svc, live))
            live.proc = _Proc()

    monkeypatch.setattr(svc, "_ensure_running", ensure)
    return drivers


@pytest.mark.parametrize("how", ["graceful", "crash"])
async def test_a_reply_the_user_stopped_is_never_carried_on_after_the_app_went(
        tmp_path, work, monkeypatch, how):
    root = tmp_path / "root2"
    app1 = create_app(str(root))
    home = Path(app1.state.platform.config.home)
    svc = S.service_for(app1.state.platform)
    drivers = _slow_tool(svc, monkeypatch)
    row = store.create(app1.state.platform.engine, harness="claude-code", folder=str(work),
                       harness_session_id="6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21")
    await svc.start_turn(row.id, "build the parser", "t1")
    await until(lambda: drivers.get(row.id) and drivers[row.id].sent)
    async with client(app1) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/interrupt")
        assert r.status_code == 200, r.text
    assert getattr(drivers[row.id], "interrupted", False)
    assert svc.live[row.id].turn is not None and svc.live[row.id].turn.running, "the tool has not confirmed yet"
    if how == "graceful":
        await stop_all(app1)  # Quit / Restart to update right after the Stop
    else:
        await asyncio.to_thread(svc.live[row.id].log.flush)  # a crash: no done
    S.reconcile_cut_turns(home)
    app2 = create_app(str(root))
    svc2 = S.service_for(app2.state.platform)
    drivers2 = fake_tool(svc2, monkeypatch)
    assert await CO.carry_on_after_restart(svc2) == [], "a reply the user stopped was carried on by itself"
    assert drivers2 == {}
    assert "cut" not in await _view(app2, row.id), "nothing to continue: the user stopped it"
    if how == "crash":
        svc._emit(svc.live[row.id], make("done", stop="interrupted"))
        await stop_all(app1)


# --------------------------------------------------------------------------- #
# 3. the setting turned off ends a wait that was already armed
# --------------------------------------------------------------------------- #
@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(CO, "CLOCK", lambda: NOW)
    return NOW


async def test_turning_the_setting_off_ends_a_wait_already_armed(app, home, work, monkeypatch, clock):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    assert CO.read_limits(home, row.id)["wait"]["resets_at"] == reset.isoformat()
    app.state.platform.config.build_auto_resume = False
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == [], "carried on with the setting off"
    assert len(drivers[row.id].sent) == 1
    st = CO.read_limits(home, row.id)
    assert st.get("wait") is None and st["card"], "the wait ends; the card (and its press) stays"
    async with client(app) as c:
        view = (await c.get(f"/build/app/sessions/{row.id}")).json()
    assert "account_wait" not in view
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 4. the restart pass never starts more replies than the pool can run
# --------------------------------------------------------------------------- #
async def test_the_restart_pass_never_starts_more_replies_than_can_run_at_once(home, app, work, monkeypatch):
    rows = [_row(app, work, hid=f"6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a{i:02d}") for i in range(S.MAX_LIVE + 2)]
    for r in rows:
        _log(home, r.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    carried = await CO.carry_on_after_restart(svc)
    assert len(carried) == S.MAX_LIVE, f"{len(carried)} replies started at once"
    offered = [r.id for r in rows if r.id not in carried]
    assert all(CO.read_cut(home, sid)["why_not"] == CO.WHY_POOL for sid in offered)
    await until(lambda: all(drivers.get(sid) and drivers[sid].sent for sid in carried))
    for sid in carried:
        svc._emit(svc.live[sid], make("done", stop="completed"))
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 5. words: a limit card with no wait is not "waiting"
# --------------------------------------------------------------------------- #
async def test_a_cut_beside_a_limit_card_with_no_wait_does_not_say_it_waits(home, app, work, monkeypatch):
    row = _row(app, work)
    _log(home, row.id, _working())
    S.reconcile_cut_turns(home)
    lim = home / "hosted" / row.id / CO.LIMITS_FILE
    lim.write_text('{"card": {"account": ""}, "wait": null, "chain": 1}', encoding="utf-8")
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == []
    assert drivers == {}
    assert CO.read_cut(home, row.id)["why_not"] == CO.WHY_LIMIT_CARD
    lim.write_text('{"card": {"account": ""}, "wait": {"id": "w1", "account": ""}}', encoding="utf-8")
    CO._amend_cut(home, row.id, "t1", auto="")
    assert await CO.carry_on_after_restart(svc) == []
    assert CO.read_cut(home, row.id)["why_not"] == CO.WHY_LIMIT
    await stop_all(app)


# --------------------------------------------------------------------------- #
# the builder's guarantees a mutation showed UNPINNED (each red when broken)
# --------------------------------------------------------------------------- #
from .test_build_app_limits_v14220 import _accounts, _conversation  # noqa: E402
from iron_jarvis.terminals.limit_state import PaneLimit  # noqa: E402


async def _limit_again(svc, drivers, sid, reset):
    """The carry-on Iron Jarvis just started hits the account's limit AT ONCE
    (the account served nothing)."""
    await until(lambda: svc.live[sid].turn is not None and drivers[sid].turn_open)
    drv = drivers[sid]
    drv.turn_open = False
    drv.limit = PaneLimit(line="You've hit your usage limit.", reset_at=reset)
    live = svc.live[sid]
    svc._emit(live, make("error", title="Claude Code hit this account's limit", fix="",
                         technical="You've hit your usage limit.", fix_kind="account_limit"))
    svc._emit(live, make("done", stop="completed"))
    await until(lambda: live.turn is None)


async def test_a_limit_that_comes_back_at_once_after_each_reset_never_loops(app, home, work, monkeypatch, clock):
    """The no-loop guarantee: a carry-on that the account refuses at once is
    COUNTED (before it starts); after MAX_AUTO_IN_A_ROW of them in a row the
    card waits for the user — never a carry-on at every reset forever."""
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    carried = 0
    for i in range(CO.MAX_AUTO_IN_A_ROW + 3):
        due = reset + timedelta(hours=i, minutes=1)
        got = await CO.resume_due(svc, now=due)
        if not got:
            break
        carried += 1
        await _limit_again(svc, drivers, row.id, reset + timedelta(hours=i + 1))
    assert carried == CO.MAX_AUTO_IN_A_ROW, f"{carried} carry-ons in a row on an account that never served"
    st = CO.read_limits(home, row.id)
    assert st.get("wait") is None and st["card"]["note"] == CO.CHAIN_NOTE.format(n=CO.MAX_AUTO_IN_A_ROW)
    await stop_all(app)


async def test_moves_within_a_group_that_are_refused_at_once_never_ping_pong(app, home, work, tmp_path, monkeypatch, clock):
    """Two accounts of a group both read as free in a snapshot a little
    behind, each at its limit in fact: the reply moves at most
    MAX_AUTO_IN_A_ROW times, then waits for the user."""
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups", {"Work": ["p1", "p2"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    _conversation(homes["p1"])
    row = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    await _limit(svc, drivers, row.id, reset=None)
    moves = 0
    for _ in range(CO.MAX_AUTO_IN_A_ROW + 3):
        if not await CO.resume_due(svc):
            break
        moves += 1
        await _limit_again(svc, drivers, row.id, None)
    assert moves == CO.MAX_AUTO_IN_A_ROW, f"{moves} moves in a row between accounts at their limit"
    assert CO.read_limits(home, row.id).get("wait") is None
    await stop_all(app)


async def test_a_refused_move_is_not_tried_again_on_every_pass(app, home, work, tmp_path, monkeypatch, clock):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups", {"Work": ["p1", "p2"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    conv = _conversation(homes["p1"])
    # p2 already holds a DIFFERENT copy of this conversation: the move is refused.
    other = homes["p2"] / conv.relative_to(homes["p1"])
    other.parent.mkdir(parents=True)
    other.write_text('{"type":"user","message":"something else entirely"}\n', encoding="utf-8")
    row = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    await _limit(svc, drivers, row.id, reset=None)
    calls: list = []
    real = svc.continue_on

    async def spy(*a, **kw):
        calls.append(a)
        return await real(*a, **kw)

    monkeypatch.setattr(svc, "continue_on", spy)
    for _ in range(4):
        assert await CO.resume_due(svc) == []
    assert len(calls) == 1, f"a refused move was tried {len(calls)} times"
    assert store.get(app.state.platform.engine, row.id).account == "p1"
    assert CO.read_limits(home, row.id)["wait"]["tried"] == ["p2"]
    await stop_all(app)


async def test_emergency_stop_holds_every_wait_and_keeps_it(app, home, work, monkeypatch, clock):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work)
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    app.state.platform.config.autonomy_kill_switch = True
    try:
        assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
        assert len(drivers[row.id].sent) == 1 and CO.read_limits(home, row.id)["wait"], "the wait stays"
    finally:
        app.state.platform.config.autonomy_kill_switch = False
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=2)) == [row.id]
    await until(lambda: len(drivers[row.id].sent) == 2)
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


async def test_a_wait_for_an_account_the_session_left_ends(app, home, work, tmp_path, monkeypatch, clock):
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    row = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, row.id, reset=reset)
    # The user chose another account for the session meanwhile.
    store.update(app.state.platform.engine, row.id, account="p3")
    assert await CO.resume_due(svc, now=reset + timedelta(minutes=1)) == []
    assert len(drivers[row.id].sent) == 1, "carried on with an account the session no longer runs on"
    assert CO.read_limits(home, row.id).get("wait") is None
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 6. a move made BY ITSELF never kills the tool's background work
# --------------------------------------------------------------------------- #
async def test_a_move_by_itself_waits_while_the_tool_still_runs_background_work(
        app, home, work, tmp_path, monkeypatch, clock):
    """A move stops the coding tool's process (a tree kill): a background
    command (a dev server, a long test run) or helper agent still running in
    it would die. Like the idle reaper, the pool and a restart (v1.417.0), a
    move Iron Jarvis makes by itself waits until that work is done."""
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups", {"Work": ["p1", "p2"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    _conversation(homes["p1"])
    row = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    await _limit(svc, drivers, row.id, reset=None)
    drivers[row.id].background_tasks = 1
    svc.live[row.id].last_heard = svc.clock()
    assert await CO.resume_due(svc) == [], "moved (a tree kill) while background work still ran"
    assert store.get(app.state.platform.engine, row.id).account == "p1"
    assert CO.read_limits(home, row.id)["wait"], "the wait stays for when the work is done"
    drivers[row.id].background_tasks = 0
    assert await CO.resume_due(svc) == [row.id]
    await until(lambda: len(drivers[row.id].sent) == 2)
    assert store.get(app.state.platform.engine, row.id).account == "p2"
    svc._emit(svc.live[row.id], make("done", stop="completed"))
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 7. Emergency stop stops a Build › Code reply Iron Jarvis carried on by itself
# --------------------------------------------------------------------------- #
async def test_emergency_stop_stops_a_carry_on_already_running_never_the_users_own(home, app, work, monkeypatch):
    """Chat › Code's rule since v1.398.0: engaging Emergency stop stops every
    coding turn Iron Jarvis started BY ITSELF. Build › Code starts such turns
    since v1.422.0 (a carry-on after a restart, a reset or a move); the
    user's own reply and a Continue the user pressed keep going."""
    from iron_jarvis.agents.orchestrator import Orchestrator

    carried_row = _row(app, work)
    _log(home, carried_row.id, _working())
    pressed = _row(app, work, hid="22222222-3333-4444-5555-666666666666")
    _log(home, pressed.id, _working())
    mine = _row(app, work, hid="11111111-2222-3333-4444-555555555555")
    S.reconcile_cut_turns(home)
    # `pressed` was already offered (the user's press carries it on).
    CO._amend_cut(home, pressed.id, "t1", auto="offered", why_not=CO.WHY_POOL)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    assert await CO.carry_on_after_restart(svc) == [carried_row.id]
    await CO.continue_cut(svc, pressed.id)
    await svc.start_turn(mine.id, "my own message", "t-mine")
    both = (carried_row.id, pressed.id, mine.id)
    await until(lambda: all(drivers.get(s) and drivers[s].sent for s in both))
    carry_turn = svc.live[carried_row.id].turn
    stopped = await asyncio.to_thread(Orchestrator(app.state.platform).stop_unattended)
    assert stopped == [f"build_app:{carry_turn.id}"]
    await until(lambda: not carry_turn.running)
    assert svc.live[mine.id].turn is not None and svc.live[mine.id].turn.running, "the user's own reply was stopped"
    assert svc.live[pressed.id].turn is not None and svc.live[pressed.id].turn.running, "a pressed Continue was stopped"
    # And the stopped carry-on is never carried on again at the next start.
    assert CO.read_cut(home, carried_row.id).get("stopped") == carry_turn.id
    for sid in (pressed.id, mine.id):
        svc._emit(svc.live[sid], make("done", stop="completed"))
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 8. Emergency stop turned on WHILE a pass runs: nothing starts after it
# --------------------------------------------------------------------------- #
async def test_emergency_stop_engaged_during_the_restart_pass_starts_nothing_more(home, app, work, monkeypatch):
    """The pass reads Emergency stop once at its top; the user can turn it
    on while it goes through the sessions (each start is awaited). Every
    start Iron Jarvis makes by itself checks it again — a reply after the
    switch is offered, never started (a Continue the user presses still
    goes)."""
    first, second = _row(app, work), _row(app, work, hid="22222222-3333-4444-5555-666666666666")
    for r in (first, second):
        _log(home, r.id, _working())
    S.reconcile_cut_turns(home)
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    real_start = svc.start_turn

    async def start_then_engage(*a, **kw):
        got = await real_start(*a, **kw)
        app.state.platform.config.autonomy_kill_switch = True  # the user turns it on now
        return got

    monkeypatch.setattr(svc, "start_turn", start_then_engage)
    try:
        carried = await CO.carry_on_after_restart(svc)
        assert len(carried) == 1, carried
        other = second.id if carried == [first.id] else first.id
        assert other not in drivers, "a carry-on started after Emergency stop was turned on"
        assert CO.read_cut(home, other)["why_not"] == CO.WHY_STOP
        # A person's press still carries it on.
        monkeypatch.setattr(svc, "start_turn", real_start)
        async with client(app) as c:
            r = await c.post(f"/build/app/sessions/{other}/continue")
            assert r.status_code == 200, r.text
        await until(lambda: drivers.get(other) and drivers[other].sent)
    finally:
        app.state.platform.config.autonomy_kill_switch = False
    for sid in (first.id, second.id):
        if svc.live.get(sid) and svc.live[sid].turn is not None:
            svc._emit(svc.live[sid], make("done", stop="completed"))
    await stop_all(app)


async def test_emergency_stop_engaged_during_a_wait_pass_leaves_the_other_waits_untouched(
        app, home, work, monkeypatch, clock):
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    a = _session(app, work)
    b = _session(app, work, hid="22222222-3333-4444-5555-666666666666")
    reset = NOW + timedelta(minutes=30)
    await _limit(svc, drivers, a.id, reset=reset)
    # (the shared helper names its first turn "t-0" — turn ids are the
    # service's, so the second session's limit turn is started by hand)
    await svc.start_turn(b.id, "carry on with the parser", "t-b0")
    await until(lambda: drivers.get(b.id) and drivers[b.id].turn_open)
    drivers[b.id].turn_open = False
    drivers[b.id].limit = PaneLimit(line="You've hit your usage limit.", reset_at=reset)
    svc._emit(svc.live[b.id], make("error", title="Claude Code hit this account's limit", fix="",
                                   technical="You've hit your usage limit.", fix_kind="account_limit"))
    svc._emit(svc.live[b.id], make("done", stop="completed"))
    await until(lambda: svc.live[b.id].turn is None)
    before = {sid: CO.read_limits(home, sid) for sid in (a.id, b.id)}
    assert all(before[sid]["wait"] for sid in (a.id, b.id))
    real_start = svc.start_turn

    async def start_then_engage(*args, **kw):
        got = await real_start(*args, **kw)
        app.state.platform.config.autonomy_kill_switch = True
        return got

    monkeypatch.setattr(svc, "start_turn", start_then_engage)
    try:
        done = await CO.resume_due(svc, now=reset + timedelta(minutes=1))
    finally:
        app.state.platform.config.autonomy_kill_switch = False
    assert len(done) == 1
    other = b.id if done == [a.id] else a.id
    assert CO.read_limits(home, other) == before[other], "a wait was taken (and counted) under Emergency stop"
    for sid in done:
        svc._emit(svc.live[sid], make("done", stop="completed"))
    await stop_all(app)


async def test_emergency_stop_engaged_during_a_wait_pass_moves_nothing_more(
        app, home, work, tmp_path, monkeypatch, clock):
    """The move half: with only the start's own check, a second session would
    still be MOVED to another account (its conversation copied, its account
    changed) and then left there with nothing started."""
    ip, homes = _accounts(tmp_path)
    monkeypatch.setattr(app.state.platform, "iron_proxy", ip, raising=False)
    monkeypatch.setattr(app.state.platform.config, "account_groups", {"Work": ["p1", "p2"]})
    svc = S.service_for(app.state.platform)
    drivers = fake_tool(svc, monkeypatch)
    _conversation(homes["p1"])
    a = _session(app, work, account="p1", title="Work", acc_home=homes["p1"])
    b = _session(app, work, account="p1", title="Work", acc_home=homes["p1"],
                 hid="6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a22")
    conv_b = homes["p1"] / "projects" / "C--work-demo" / "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a22.jsonl"
    conv_b.write_text('{"type":"user","message":"other"}\n', encoding="utf-8")
    await _limit(svc, drivers, a.id, reset=None)
    await svc.start_turn(b.id, "carry on", "t-b0")
    await until(lambda: drivers.get(b.id) and drivers[b.id].turn_open)
    drivers[b.id].turn_open = False
    drivers[b.id].limit = PaneLimit(line="You've hit your usage limit.")
    svc._emit(svc.live[b.id], make("error", title="Claude Code hit this account's limit", fix="",
                                   technical="You've hit your usage limit.", fix_kind="account_limit"))
    svc._emit(svc.live[b.id], make("done", stop="completed"))
    await until(lambda: svc.live[b.id].turn is None)
    real_start = svc.start_turn

    async def start_then_engage(*args, **kw):
        got = await real_start(*args, **kw)
        app.state.platform.config.autonomy_kill_switch = True
        return got

    monkeypatch.setattr(svc, "start_turn", start_then_engage)
    try:
        done = await CO.resume_due(svc)
    finally:
        app.state.platform.config.autonomy_kill_switch = False
    assert len(done) == 1
    other = b.id if done == [a.id] else a.id
    assert store.get(app.state.platform.engine, other).account == "p1", "moved under Emergency stop"
    assert CO.read_limits(home, other)["wait"], "its wait stays"
    for sid in done:
        svc._emit(svc.live[sid], make("done", stop="completed"))
    await stop_all(app)


# --------------------------------------------------------------------------- #
# 9. a usage window whose reset already passed never hides the words' reset
# --------------------------------------------------------------------------- #
def test_a_full_window_whose_reset_passed_does_not_hide_the_reset_the_words_name():
    """Codex's last ``rateLimits`` can be older than its limit message (a
    window full hours ago, already reset): that stale reset is in the PAST,
    so the reply would wait for nothing and the reset the message names
    ("try again in 2 hours") was never read."""
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    class Codex:
        limit = PaneLimit(line="You've hit your usage limit.")
        seen_windows = [{"key": "primary", "percent": 100, "resets_at": stale}]

    said = {"technical": "You've hit your usage limit. Upgrade to Plus to continue, or try again in 2 hours."}
    got = CO.reset_from(Codex(), said)
    assert got is not None and got > datetime.now(timezone.utc) + timedelta(hours=1, minutes=50), got
