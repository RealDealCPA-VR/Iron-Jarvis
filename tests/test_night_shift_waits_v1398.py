"""v1.398.0 (night shift N1): Emergency stop holds "Resume when ready".

A wait armed on an account's limit (by the press, or by itself since
v1.393.0) is carried on by the wait loop's pass (``routes/build.resume_due``)
— a coding turn Iron Jarvis starts BY ITSELF. While Emergency stop is on, the
pass starts nothing and does not even try (the turn's own refusal is the
second guard); the wait stays, and once the stop is off the next pass carries
the reply on as before. Driven through the v1.368.0 harness (the real app, a
fake Iron-Proxy, the scripted model).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.daemon.routes import build as build_routes
from tests.test_build_account_turns_v1368 import (  # noqa: F401 — fixtures
    _isolated,
    _limit_then_arm,
    app,
    fake,
    work,
)
from tests.test_build_sessions_v1367 import setup_of, until


async def test_emergency_stop_holds_resume_when_ready_and_the_wait_stays(app, fake, work, monkeypatch):  # noqa: F811
    beh, sid, _card, _view = await _limit_then_arm(app, fake, work, monkeypatch, park_s=0.5)
    await asyncio.sleep(0.7)
    calls = len(beh.calls)
    app.state.platform.config.autonomy_kill_switch = True
    tried: list = []
    real_start = build_routes.start_build_turn

    async def spy(*a, **kw):
        tried.append(kw.get("started_by"))
        return await real_start(*a, **kw)

    monkeypatch.setattr(build_routes, "start_build_turn", spy)
    assert await build_routes.resume_due(app.state.d) == []
    assert tried == [] and len(beh.calls) == calls, "nothing was started, or even tried"
    assert "account_wait" in setup_of(app, sid), "the wait stays"
    # Emergency stop off: the next pass carries it on, as Iron Jarvis's own.
    app.state.platform.config.autonomy_kill_switch = False
    assert await build_routes.resume_due(app.state.d) == [sid]
    assert tried == ["wait"]
    logs = app.state.platform.turn_logs
    await until(lambda: not logs.running(), 30)
