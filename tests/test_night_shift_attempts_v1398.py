"""v1.398.0 review (night shift N1): A NIGHT TASK THAT TRIES ITS WORK ON
SEVERAL MODELS ("try on") RUNS EACH ATTEMPT UNDER THE NIGHT'S RULES, AND
EMERGENCY STOP STOPS THEM.

The first cut's only pin for this path stubbed ``maybe_start`` away, so no
attempt ever ran at night in a test. Here the REAL attempts start (the real
stream lane per attempt, real git worktrees, scripted models — the attempts
suite's own doubles): an attempt's web call is CARDED at night and declined
after the card wait (never run), and Emergency stop stops the attempts turn
by its name. Nothing reaches a real service.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import night as N
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.base import ToolResult
from tests.test_build_attempts_v1397 import CODEX, LOCAL, World, make_calc_repo, wire_models
from tests.test_build_accounts_v1368 import isolate
from tests.test_build_git_v1375 import no_real_gh
from tests.test_build_sessions_v1367 import turn_frames, until
from tests.test_night_shift_review_v1398 import _ended, _task

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    no_real_gh(monkeypatch)
    yield from isolate(tmp_path, monkeypatch)


async def test_attempts_started_by_a_night_task_card_the_web_and_stop_on_emergency_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "CLOCK", lambda: datetime(2026, 10, 26, 23, 30).astimezone())
    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 0.5)
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    repo = make_calc_repo(tmp_path / "proj")
    app = create_app(str(tmp_path / "home"))
    ran: list[str] = []

    async def _fetch(args, ctx):  # noqa: ANN001
        ran.append(str((args or {}).get("url")))
        return ToolResult(ok=True, output="page")

    app.state.platform.registry.get("web_fetch").execute = _fetch
    held = asyncio.Event()
    reached = asyncio.Event()

    async def _hold(world):
        reached.set()
        await held.wait()
        return "Never said."

    world = wire_models(app, monkeypatch, World({
        "ollama": [[ToolCall(id="w1", name="web_fetch", arguments={"url": "https://example.com/docs"})],
                   "Done without the page."],
        "codex-cli": [_hold],
    }))
    task = N.add_task(app.state.platform.engine, folder=str(repo), words="fix add in calc.py",
                      try_on=[{"provider": CODEX[0], "model": CODEX[1]}, {"provider": LOCAL[0], "model": LOCAL[1]}])
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [task["id"]], out
    running = _task(app, task["id"])
    turn = running["turn_id"]
    assert turn in N.unattended_turns(), "the attempts turn is not listed for Emergency stop"
    await asyncio.wait_for(reached.wait(), 60)
    # The local attempt's web call: CARDED at night (on its row) ...
    from iron_jarvis.harness import attempts as A

    asked = await until(lambda: [d["ask"] for e, d in turn_frames(app, turn)
                                 if e == A.EVENT and isinstance(d.get("ask"), dict)], 30)
    assert asked and asked[0]["tool"] == "web_fetch", asked
    # ... and declined after the wait: the attempt carries on without it.
    await until(lambda: [c for c in world.calls if c["provider"] == "ollama"][1:], 30)
    assert ran == [], "a web call ran at night with nobody asked"
    # EMERGENCY STOP: the attempts turn stops by its name.
    app.state.platform.config.autonomy_kill_switch = True
    stopped = await asyncio.to_thread(app.state.platform.orchestrator.stop_unattended)
    assert f"turn:{turn}" in stopped, stopped
    held.set()
    ended = await _ended(app, task["id"], 60)
    assert ended["state"] == N.STOPPED and any(n["kind"] == "emergency" for n in ended["notes"]), ended
    assert any(n["kind"] == "declined" for n in ended["notes"]), ended["notes"]
    assert world.calls, "no attempt ran"
