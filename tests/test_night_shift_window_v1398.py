"""v1.398.0 review (night shift N1): A NIGHT TASK CARRIES ON BY ITSELF ONLY
INSIDE THE WINDOW AND WITHIN ITS OWN TIME LIMIT.

The first cut carried a night task's work on whenever Iron Jarvis could:

* a reply a restart cut off was carried on at the next boot — at 9 AM too,
  under the NIGHT posture (its record still says "running");
* "Resume when ready" carried a night task on when its account's limit
  reset — at 9 AM too;
* the time limit was per TURN: every carry-on got a fresh 90 minutes;
* a cut-off reply the boot's pass did NOT carry on held its task "running"
  (its slot, the PC's keep-awake) for good.

Decision (item 6 of the review): a night task's cut-off reply IS carried on
once after a restart — it is daemon-started work the user asked for, and
v1.391.0 already does it safely — but only inside the night window, with
Emergency stop off and time left on the TASK's limit; outside, it is not, its
record says why, and it settles "stopped". A reply that was waiting on a card
when the restart came is never carried on (v1.391.0's rule: the card it waited
on is gone) and settles "stopped" too.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import HTTPException

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import reply_record as RR
from iron_jarvis.harness import sessions as S
from tests.test_build_git_v1375 import make_repo
from tests.test_build_resume_v1391 import Hold, _reboot, _write
from tests.test_build_sessions_v1367 import wire
from tests.test_night_shift_review_v1398 import (  # noqa: F401 — fixtures
    NightScript,
    _ended,
    _no_gh,
    _queue,
    _task,
)

pytestmark = pytest.mark.usefixtures("no_network")


def _local(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 27, h, m).astimezone()


@pytest.fixture()
def clock(monkeypatch):
    now = {"at": _local(23, 30)}
    monkeypatch.setattr(N, "CLOCK", lambda: now["at"])
    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 0.4)
    return now


async def _cut_off_night_task(tmp_path, clock) -> tuple[str, dict]:
    """A night task whose reply wrote one file and is asking the model for
    its next step when Iron Jarvis stops (the update's tidy stop)."""
    repo = make_repo(tmp_path / "proj")
    home = str(tmp_path / "home")
    first = create_app(home)
    hold = Hold()
    wire(first, NightScript({"TASK-CUT": [_write("c1", "one.txt", "1"), hold]}))
    task = await _queue(first, repo, "TASK-CUT: make files")
    out = await night_routes.night_pass(first.state.d)
    assert out["started"] == [task["id"]]
    await asyncio.wait_for(hold.reached.wait(), 60)
    await first.state.platform.turn_logs.shutdown()
    running = _task(first, task["id"])
    assert running["state"] == N.RUNNING
    assert RR.kept(first.state.platform.engine), "the cut-off reply was not kept"
    return home, running


async def test_a_cut_off_night_reply_is_not_carried_on_after_the_window_closed(tmp_path, clock):
    home, task = await _cut_off_night_task(tmp_path, clock)
    second = _reboot(home)
    script = wire(second, NightScript({"TASK-CUT": ["Never."]}))
    clock["at"] = _local(9, 0)  # the morning: the window is closed
    assert await build_routes.resume_after_restart(second.state.d) == []
    assert not RR.kept(second.state.platform.engine)
    ended = await _ended(second, task["id"], 60)
    assert ended["state"] == N.STOPPED, ended
    restart = " ".join(n["text"] for n in ended["notes"] if n["kind"] == "restart")
    assert "night window had closed" in restart, ended["notes"]
    # Nothing of it ran in the morning under the night's posture.
    assert script.calls == []


async def test_a_cut_off_night_reply_is_carried_on_once_inside_the_window(tmp_path, clock, monkeypatch):
    home, task = await _cut_off_night_task(tmp_path, clock)
    second = _reboot(home)
    script = wire(second, NightScript({"TASK-CUT": ["Done TASK-CUT after the restart."]}))
    clock["at"] = _local(2, 0)
    seen: list[str] = []
    real = build_routes.start_build_turn

    async def spy(*a, **kw):
        seen.append(kw.get("started_by"))
        return await real(*a, **kw)

    monkeypatch.setattr(build_routes, "start_build_turn", spy)
    assert await build_routes.resume_after_restart(second.state.d) == [task["thread_id"]]
    assert seen == ["restart"]
    ended = await _ended(second, task["id"], 60)
    assert ended["state"] == N.DONE, ended
    # It ran under the NIGHT's rule (never the user's own posture).
    system = next(c["system"] for c in script.calls if c["task"] == "TASK-CUT")
    assert "This task runs at night with nobody watching." in system


async def test_a_night_reply_cut_off_while_it_waited_on_a_card_is_not_carried_on(tmp_path, clock, monkeypatch):
    """Decision (item 6): the card it waited on is gone with the restart, so
    the reply is never carried on by itself — even inside the window — and
    the task settles "stopped", its record saying a restart cut it off."""
    from iron_jarvis.daemon.routes import chat as chat_routes
    from iron_jarvis.providers.adapters.base import ToolCall

    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 600.0)
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    repo = make_repo(tmp_path / "proj")
    home = str(tmp_path / "home")
    first = create_app(home)
    wire(first, NightScript({"TASK-CARD": [
        [ToolCall(id="w1", name="WebFetch", arguments={"url": "https://example.com/a"})],
    ]}))
    task = await _queue(first, repo, "TASK-CARD: read the page")
    await night_routes.night_pass(first.state.d)
    turn = _task(first, task["id"])["turn_id"]
    from tests.test_build_sessions_v1367 import turn_frames, until

    assert await until(lambda: [f for e, f in turn_frames(first, turn) if e == "approval"], 30)
    await first.state.platform.turn_logs.shutdown()
    second = _reboot(home)
    script = wire(second, NightScript({"TASK-CARD": ["Never."]}))
    clock["at"] = _local(2, 0)  # inside the window
    assert await build_routes.resume_after_restart(second.state.d) == []
    ended = await _ended(second, task["id"], 60)
    assert ended["state"] == N.STOPPED, ended
    assert any(n["kind"] == "restart" for n in ended["notes"]), ended["notes"]
    assert script.calls == []


async def test_a_reply_the_boots_pass_did_not_carry_on_never_holds_its_task(tmp_path, clock, monkeypatch):
    """A carry-on that failed for any reason left the task "running" (its
    slot, the PC kept awake) for good, and a later boot could carry it on a
    day late under the user's own posture."""
    home, task = await _cut_off_night_task(tmp_path, clock)
    second = _reboot(home)
    clock["at"] = _local(2, 0)

    async def broken(*a, **kw):
        raise RuntimeError("the carry-on could not start")

    monkeypatch.setattr(build_routes, "start_build_turn", broken)
    # Before the pass has run, the task is held (the pass may carry it on).
    assert await night_routes.settle_running(second.state.d) == []
    assert _task(second, task["id"])["state"] == N.RUNNING
    assert await build_routes.resume_after_restart(second.state.d) == []
    ended = await _ended(second, task["id"], 60)
    assert ended["state"] == N.STOPPED, ended
    assert not RR.kept(second.state.platform.engine), "a later boot would carry it on"


async def test_resume_when_ready_never_carries_a_night_task_on_after_the_window(tmp_path, clock):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({"TASK-WAIT": ["Part one done."]}))
    task = await _queue(app, repo, "TASK-WAIT: do it")
    await night_routes.night_pass(app.state.d)
    first = _task(app, task["id"])
    lg = app.state.platform.turn_logs.get(first["turn_id"])
    for _ in range(600):
        if lg is not None and lg.status != "running":
            break
        await asyncio.sleep(0.05)
    # Its account reached a limit that resets at 8 AM: the wait is armed.
    engine = app.state.platform.engine
    S.update(engine, first["thread_id"], setup={"account_wait": {
        "account": "acct-1", "account_name": "Work", "resets_at": "", "armed_at": "x", "next_check_at": "",
    }, "account": "acct-1"})
    # Inside the window the task waits for it.
    assert await night_routes.settle_running(app.state.d) == []
    # The window closes before the reset: the carry-on is refused...
    clock["at"] = _local(7, 30)
    with pytest.raises(HTTPException) as refused:
        await build_routes.start_build_turn(app.state.d, first["thread_id"], "Carry on.", "bt-w2",
                                            continuation=True, started_by="wait")
    assert refused.value.detail == N.WINDOW_CLOSED
    # ...and the task is settled, its wait cleared (nothing carries it on by day).
    ended = await _ended(app, task["id"], 60)
    assert ended["state"] == N.STOPPED, ended
    assert ended["notes"][0]["text"] == N.WAIT_WINDOW_CLOSED
    assert "account_wait" not in build_routes._setup_of(S.get_row(engine, first["thread_id"]))


async def test_the_time_limit_is_the_tasks_across_its_turns(tmp_path, clock):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({"TASK-TIME": ["Part one done."]}))
    task = await _queue(app, repo, "TASK-TIME: do it")
    await night_routes.night_pass(app.state.d)
    first = _task(app, task["id"])
    lg = app.state.platform.turn_logs.get(first["turn_id"])
    for _ in range(600):
        if lg is not None and lg.status != "running":
            break
        await asyncio.sleep(0.05)
    for _ in range(100):  # the turn's done callbacks run after its status
        await asyncio.sleep(0.02)
        N.flush_notes()
        if _task(app, task["id"])["ran_s"] > 0:
            break
    assert _task(app, task["id"])["ran_s"] > 0, "the turn's running time was not kept"
    # The task has used up its 90 minutes over its turns: no carry-on.
    N.add_ran(app.state.platform.engine, task["id"], 90 * 60)
    with pytest.raises(HTTPException) as refused:
        await build_routes.start_build_turn(app.state.d, first["thread_id"], "Carry on.", "bt-t2",
                                            continuation=True, started_by="wait")
    assert refused.value.detail == N.TIME_USED_UP


def test_a_queued_task_keeps_the_pc_awake_from_the_evening_not_only_once_the_window_opens(tmp_path):
    """The plan: "keep-awake holds while a night task is queued or running".
    The first cut held only inside the window, so a PC that sleeps after an
    idle half hour slept through 11 PM with tonight's tasks queued."""
    app = create_app(str(tmp_path / "home"))
    engine = app.state.platform.engine
    cfg = app.state.platform.config
    assert N.awake_needed(engine, cfg, _local(22, 30)) is False, "nothing queued"
    N.add_task(engine, folder=str(tmp_path), words="tonight")
    assert N.awake_needed(engine, cfg, _local(22, 30)) is True
    assert N.awake_needed(engine, cfg, _local(18, 0)) is True
    assert N.awake_needed(engine, cfg, _local(23, 30)) is True
    assert N.awake_needed(engine, cfg, _local(9, 0)) is False, "not all day"
    cfg.autonomy_kill_switch = True
    assert N.awake_needed(engine, cfg, _local(22, 30)) is False


def test_a_reply_that_ended_on_its_accounts_limit_is_never_done():
    state, notes = N.outcome_of({"notes": []}, {
        "role": "assistant", "content": "Part of it.", "accountLimit": {"account": "a1"},
    })
    assert state == N.NEEDS_YOU and ("account", "Its account reached a limit.") in notes


def test_a_carry_on_turn_is_capped_at_what_is_left_of_the_tasks_minutes(tmp_path):
    cfg = Config(home=tmp_path, project_root=tmp_path, night_task_minutes=90)
    assert N.posture_setup({}, cfg, {"ran_s": 60 * 60})["cap"] == {"minutes": 30}
    assert N.posture_setup({}, cfg, {"ran_s": 0})["cap"] == {"minutes": 90}
    assert N.minutes_left({"ran_s": 95 * 60}, cfg) == 0.0
