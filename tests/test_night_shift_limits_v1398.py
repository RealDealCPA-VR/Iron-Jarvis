"""v1.398.0 review (night shift N1): THE LIMITS HOLD.

* "At most N at once" and "one per folder" hold even when two passes of the
  night loop overlap (each counted the room before either started anything).
* A night task never spends pay-per-use money beyond the user's limit (the
  plan: "Pay-per-use never goes above your limit (default $0)"; decision 4's
  "spending" floor): a task whose model is billed per use does not start at
  a $0 limit, and with room its turn's dollar cap is what is left.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from iron_jarvis.agents import model_team as MT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.harness import reply_cap as RC
from tests.test_build_git_v1375 import make_repo
from tests.test_build_resume_v1391 import Hold
from tests.test_build_sessions_v1367 import wire
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


async def test_two_overlapping_passes_never_start_more_than_n_or_two_in_one_folder(tmp_path):
    repos = [make_repo(tmp_path / f"p{i}") for i in range(3)]
    app = create_app(str(tmp_path / "home"))
    holds = {k: Hold() for k in ("T-A", "T-B", "T-C", "T-D")}
    wire(app, NightScript({k: [h] for k, h in holds.items()}))
    for key, repo in (("T-A", repos[0]), ("T-B", repos[1]), ("T-C", repos[2]), ("T-D", repos[0])):
        await _queue(app, repo, f"{key}: work")
    d = app.state.d
    first, second = await asyncio.gather(night_routes.night_pass(d), night_routes.night_pass(d))
    started = first["started"] + second["started"]
    assert len(started) == 2, (first, second)
    running = N.rows_in(app.state.platform.engine, N.RUNNING)
    folders = [t["folder"] for t in running]
    assert len(running) == 2 and len(set(folders)) == 2, running
    for h in holds.values():
        h.gate.set()


async def test_emergency_stop_turned_on_while_a_task_starts_stops_it_never_fails_it(tmp_path, monkeypatch):
    """The window between "marked running" and its first turn (its session
    and worktree are being made): Emergency stop turned on there refused the
    turn, and the record said FAILED with the refusal as the error."""
    from iron_jarvis.daemon.routes import build as build_routes

    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    script = wire(app, NightScript({"TASK-MID": ["Never."]}))
    real_create = build_routes.create_session_row

    async def _then_the_stop(d, folder, **kw):
        out = await real_create(d, folder, **kw)
        d.platform.config.autonomy_kill_switch = True
        return out

    monkeypatch.setattr(build_routes, "create_session_row", _then_the_stop)
    task = await _queue(app, repo, "TASK-MID: work")
    await night_routes.night_pass(app.state.d)
    ended = _task(app, task["id"])
    assert ended["state"] == N.STOPPED, ended
    assert [n["kind"] for n in ended["notes"]] == ["emergency"], ended["notes"]
    assert script.calls == []


async def test_a_night_task_on_a_pay_per_use_model_never_starts_at_a_zero_limit(tmp_path, monkeypatch):
    real = MT.cost_type
    monkeypatch.setattr(MT, "cost_type", lambda platform, provider, model="": (
        MT.COST_PAY if provider == "mock" else real(platform, provider, model)))
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    script = wire(app, NightScript({"TASK-PAY": ["Never."]}))
    app.state.platform.config.team_paid_cap_usd = 0.0
    task = await _queue(app, repo, "TASK-PAY: spend")
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == []
    ended = _task(app, task["id"])
    assert ended["state"] == N.FAILED, ended
    assert "billed per use" in ended["notes"][-1]["text"] and "$0" in ended["notes"][-1]["text"]
    assert script.calls == []


async def test_with_room_a_pay_per_use_night_turn_is_capped_at_what_is_left(tmp_path, monkeypatch):
    real = MT.cost_type
    monkeypatch.setattr(MT, "cost_type", lambda platform, provider, model="": (
        MT.COST_PAY if provider == "mock" else real(platform, provider, model)))
    monkeypatch.setattr(MT, "paid_spend_this_month", lambda platform, now=None: 1.5)
    seen: list[dict] = []
    real_from = RC.from_setup

    def spy(setup, **kw):
        seen.append(dict((setup or {}).get(RC.SETUP_KEY) or {}))
        return real_from(setup, **kw)

    monkeypatch.setattr(RC, "from_setup", spy)
    repo = make_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    wire(app, NightScript({"TASK-ROOM": ["Done TASK-ROOM."]}))
    app.state.platform.config.team_paid_cap_usd = 5.0
    task = await _queue(app, repo, "TASK-ROOM: spend a little")
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]]
    assert seen and seen[-1].get("usd") == 3.5, seen
    assert seen[-1].get("minutes") == 90
    ended = await _ended(app, task["id"], 60)
    assert ended["state"] == N.DONE, ended
    assert Path(task["folder"]).is_dir()
