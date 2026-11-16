"""Second review T1 (v1.512.0): a night task cancelled while it is STARTING
never runs.

Inside the night window ``start_task`` marks the task running, then makes its
worktree, the main-folder snapshot and its turn. A Cancel in that gap found
the task no longer queued and with no turn yet, so it only wrote "You
stopped it." and stopped nothing; ``start_task`` then started the turn, and
``settle_running`` read the stop note only once the turn was over. The task
edited files and ran commands unasked until it ended or hit its limit.

Now ``start_task`` reads the task again right before the start (a stop note:
nothing starts, the task ends stopped) and once more right after it (a stop
that landed in between stops the new turn), and the settle pass stops a
running turn whose task carries a stop note.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from tests.test_build_git_v1375 import make_repo
from tests.test_build_resume_v1391 import Hold
from tests.test_build_sessions_v1367 import asgi, messages_of, until, wire
from tests.test_night_shift_review_v1398 import (  # noqa: F401 — fixtures
    NightScript,
    _ended,
    _no_gh,
    _queue,
    _task,
)

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _night(monkeypatch):
    monkeypatch.setattr(N, "CLOCK", lambda: datetime(2026, 10, 26, 23, 30).astimezone())
    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 0.4)


def _held_slots(app) -> set[str]:
    orch = app.state.platform.orchestrator
    return {k for k in (getattr(orch, "_governed", None) or set()) if str(k).startswith("night:")}


async def test_a_cancel_while_the_worktree_is_made_starts_nothing(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    script = wire(app, NightScript({"TASK-GAP": ["Never said."]}))
    reached, release = asyncio.Event(), asyncio.Event()
    real_create = build_routes.create_session_row

    async def _held_create(d_, folder, **kw):
        out = await real_create(d_, folder, **kw)  # the worktree is made here
        reached.set()
        await release.wait()
        return out

    monkeypatch.setattr(build_routes, "create_session_row", _held_create)
    task = await _queue(app, repo, "TASK-GAP: work")
    passing = asyncio.create_task(night_routes.night_pass(d))
    await asyncio.wait_for(reached.wait(), 60)
    assert _task(app, task["id"])["state"] == N.RUNNING

    status, out = await asgi(app, "POST", f"/build/night/{task['id']}/cancel", {})
    assert status == 200 and out["stopped_turn"] == "", out
    release.set()
    result = await asyncio.wait_for(passing, 60)

    assert result["started"] == [], result
    ended = _task(app, task["id"])
    assert ended["state"] == N.STOPPED, ended
    assert [(n["kind"], n["text"]) for n in ended["notes"]] == [("stopped", N.CANCELLED_RUNNING)], ended["notes"]
    assert script.calls == [], "the model was asked although the task was cancelled"
    assert not N.unattended_turns()
    assert not _held_slots(app), _held_slots(app)
    thread = ended["thread_id"]
    assert thread and not build_routes._running_turn(d, thread)
    assert thread not in build_routes._STARTING
    assert task["id"] not in night_routes._STARTING_TASKS
    # A later settle pass leaves it as it is.
    await night_routes.settle_running(d)
    assert _task(app, task["id"])["state"] == N.STOPPED


async def test_a_cancel_landing_just_after_the_start_stops_the_new_turn(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    hold = Hold()
    script = wire(app, NightScript({"TASK-LATE": [hold]}))
    real_start = build_routes.start_build_turn
    seen: dict[str, str] = {}

    async def _start_then_cancel(d_, thread_id, text, turn_id, **kw):
        # The re-read before the start has passed: the Cancel lands while the
        # turn is being made, when the route finds no turn to stop yet.
        task_id = N.running_task_for(d_.platform.engine, thread_id)["id"]
        out = await asyncio.to_thread(night_routes.cancel_night_task, d_, task_id)
        seen["stopped_turn"] = out["stopped_turn"]
        lg = await real_start(d_, thread_id, text, turn_id, **kw)
        seen["turn"] = turn_id
        return lg

    monkeypatch.setattr(build_routes, "start_build_turn", _start_then_cancel)
    task = await _queue(app, repo, "TASK-LATE: work")
    await night_routes.night_pass(d)
    assert seen.get("stopped_turn") == "" and seen.get("turn"), seen
    try:
        # The new turn is stopped by the start itself, not left to run.
        assert await until(lambda: not TURNS.is_running(seen["turn"]), 30), "the turn kept running"
        ended = await _ended(app, task["id"], 60)
    finally:
        hold.gate.set()
    assert ended["state"] == N.STOPPED, ended
    assert any(n["kind"] == "stopped" and n["text"] == N.CANCELLED_RUNNING for n in ended["notes"])
    assert "Never said." not in json.dumps(messages_of(app, ended["thread_id"]))
    assert not _held_slots(app)
    assert len(script.calls) <= 1


async def test_the_settle_pass_stops_a_running_turn_whose_task_has_a_stop_note(tmp_path):
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    d = app.state.d
    hold = Hold()
    wire(app, NightScript({"TASK-NOTE": [hold]}))
    task = await _queue(app, repo, "TASK-NOTE: work")
    out = await night_routes.night_pass(d)
    assert out["started"] == [task["id"]]
    await asyncio.wait_for(hold.reached.wait(), 60)
    started = _task(app, task["id"])
    turn = started["turn_id"]
    assert TURNS.is_running(turn)
    # Any path that wrote the stop note without stopping the turn.
    N.add_note(app.state.platform.engine, task["id"], "stopped", N.CANCELLED_RUNNING)
    try:
        await night_routes.settle_running(d)
        assert await until(lambda: not TURNS.is_running(turn), 30), "the settle pass left the turn running"
        ended = await _ended(app, task["id"], 60)
    finally:
        hold.gate.set()
    assert ended["state"] == N.STOPPED, ended
    assert "Never said." not in json.dumps(messages_of(app, ended["thread_id"]))
