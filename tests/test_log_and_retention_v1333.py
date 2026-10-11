"""Quieter log, daily retention (v1.333.0, D12).

* ``GET /ui/status-line`` (the home line's ~20 s poll) was 337 of ~550 lines
  in the user's daemon.log since boot: it joins the quiet polled routes.
* ``event_retention_days`` was applied only at boot, so a daemon up for weeks
  never pruned again: the lifespan now runs the sweep once a day, off the loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import db as db_mod
from iron_jarvis.core.logging import PolledRouteAccessFilter
from iron_jarvis.daemon import app as app_mod


def _access(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5", method, path, "1.1", status), None,
    )


def test_the_status_line_poll_leaves_the_access_log():
    quiet = PolledRouteAccessFilter()
    assert quiet.filter(_access("GET", "/ui/status-line", 200)) is False
    assert quiet.filter(_access("GET", "/ui/status-line?x=1", 200)) is False
    # A failing poll is still logged.
    assert quiet.filter(_access("GET", "/ui/status-line", 500)) is True


@pytest.mark.asyncio
async def test_the_retention_sweep_runs_again_off_the_loop_reading_the_setting_live(
    platform, monkeypatch
):
    calls: list[tuple[int, int]] = []

    def fake_prune(engine, days, vacuum=False):
        calls.append((days, threading.get_ident()))
        return 0

    monkeypatch.setattr(db_mod, "prune_events", fake_prune)
    platform.config.event_retention_days = 30
    loop_thread = threading.get_ident()
    task = asyncio.create_task(app_mod._event_prune_loop(platform, interval_s=0))
    try:
        deadline = time.monotonic() + 10  # a liveness cap, not a speed bar
        while len(calls) < 2:
            assert time.monotonic() < deadline, "the sweep never ran twice"
            await asyncio.sleep(0.01)
        platform.config.event_retention_days = 7
        seen = len(calls)
        while len(calls) <= seen:
            assert time.monotonic() < deadline, "the sweep stopped"
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
    assert {d for d, _ in calls[:2]} == {30}
    assert calls[-1][0] == 7
    assert loop_thread not in {t for _, t in calls}


async def _task_names() -> set[str]:
    return {getattr(t.get_coro(), "__name__", "") for t in asyncio.all_tasks()}


def test_the_daemon_arms_the_daily_sweep(tmp_path):
    with TestClient(app_mod.create_app(str(tmp_path))) as client:
        assert "_event_prune_loop" in client.portal.call(_task_names)
