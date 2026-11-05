"""v1.433.0 (Wave 1, A11 + L2) — background loops tell the truth.

A11. ``_tick`` keeps a loop's failing row in ``loop_health`` until a later
pass succeeds. A loop the user turned OFF never passes again, so its row
stayed for ever and the bell and Everything › Status said "isn't working"
about something switched off. Each disarm path now drops its own row:
autonomy, sentinels, calendar, the fleet sampler, the Slack socket and the
inbound poll (its loop keeps running; ``enabled()`` going false is its off).

L2. Three loops failed in silence:

* the 30-second terminal-history snapshot logged at DEBUG and reported
  nothing — a pane whose history file could not be written was retried for
  ever with no word. It now reports through ``_tick("terminal_snapshot", …)``
  (the manager keeps the last write failure: ``snapshot_problem()``), and the
  first failure of a pane is a WARNING;
* the daily event-log prune and the REPL idle sweep log failures at WARNING.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.app as app_module
from iron_jarvis.daemon.app import create_app


def _failing(name: str) -> dict:
    return {"ok": False, "last_error": f"RuntimeError: {name} exploded", "at": "2026-11-04T00:00:00+00:00",
            "since": "2026-11-04T00:00:00+00:00"}


def _on_loop(app, fn) -> None:
    """Run a (sync) re-arm function on the daemon's own loop, as the settings
    and channels routes do (they hop with call_soon_threadsafe)."""
    loop = app.state.d._live_rearm["loop"]

    async def _call() -> None:
        fn()

    asyncio.run_coroutine_threadsafe(_call(), loop).result(10)


def _until(pred, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.mark.parametrize(
    ("row", "rearm"),
    [
        ("autonomy", "autonomy"),
        ("sentinels", "sentinels"),
        ("calendar", "calendar"),
        ("fleet", "fleet"),
        ("slack_socket", "slack"),
    ],
)
def test_a_loop_turned_off_drops_its_failing_row(tmp_path, row, rearm):
    """Every loop here is off on a fresh install (no autonomy, no sentinels,
    no calendar, no model server, no Slack channel), so a live re-arm takes
    the DISARM branch — exactly what the settings and channels pages do when
    the user switches one off."""
    app = create_app(str(tmp_path))
    d = app.state.d
    with TestClient(app) as c:
        assert rearm in d._live_rearm, f"no live re-arm for {rearm}"
        d.loop_health[row] = _failing(row)
        # Anti-vacuity: the planted row IS what the bell and Status read.
        assert c.get("/diagnostics").json()["background_loops"][row]["ok"] is False
        _on_loop(app, d._live_rearm[rearm])
        assert _until(lambda: row not in d.loop_health), d.loop_health.get(row)
        assert row not in c.get("/diagnostics").json()["background_loops"]


def test_a_loop_that_is_rearmed_on_keeps_reporting(tmp_path, monkeypatch):
    """Control: only the OFF branch forgets. An autonomy loop re-armed ON
    keeps its row until its own next pass replaces it."""
    app = create_app(str(tmp_path))
    d = app.state.d
    platform = app.state.platform
    monkeypatch.setenv("IRONJARVIS_AUTONOMY", "on")
    with TestClient(app):
        if platform.intent is None:
            pytest.skip("no motivation layer in this build")
        # The loop waits 30 s before its first pulse, so nothing ticks here.
        platform.config.autonomy_enabled = True
        d.loop_health["autonomy"] = _failing("autonomy")
        _on_loop(app, d._live_rearm["autonomy"])
        time.sleep(0.1)
        assert d.loop_health.get("autonomy", {}).get("ok") is False, "an armed loop's row was dropped"
        platform.config.autonomy_enabled = False
        _on_loop(app, d._live_rearm["autonomy"])
        assert _until(lambda: "autonomy" not in d.loop_health)


def test_the_inbound_poll_forgets_its_failure_when_nothing_listens_any_more(tmp_path, monkeypatch):
    import threading

    app = create_app(str(tmp_path))
    poller = app.state.inbound_poller
    turned_off = threading.Event()

    def _enabled():
        if not turned_off.is_set():
            raise RuntimeError("poll exploded")
        return False  # the channel was turned off

    monkeypatch.setattr(poller, "enabled", _enabled)
    real_sleep = asyncio.sleep

    async def _fast_sleep(delay, *a, **kw):
        # Only the inbound loop's sleeps collapse (its 20 s settle + interval).
        task = asyncio.current_task()
        coro = task.get_coro() if task is not None else None
        name = getattr(coro, "__qualname__", "") or ""
        if name.endswith("_inbound_loop"):
            return await real_sleep(0.01)
        return await real_sleep(delay, *a, **kw)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    with TestClient(app) as c:
        def _row():
            return c.get("/diagnostics").json()["background_loops"].get("inbound")

        # Anti-vacuity: the failing pass IS recorded first.
        assert _until(lambda: (_row() or {}).get("ok") is False, 10), _row()
        turned_off.set()
        assert _until(lambda: _row() is None, 5), _row()


# --------------------------------------------------------------------------
# L2 — the terminal-history snapshot reports its own passes
# --------------------------------------------------------------------------


def test_the_terminal_snapshot_loop_reports_a_write_failure_and_its_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_TERMINAL_SNAPSHOT_S", 0.05)
    app = create_app(str(tmp_path))
    terminals = app.state.platform.terminals
    problem = {"err": OSError(28, "No space left on device")}
    monkeypatch.setattr(terminals, "snapshot_if_changed", lambda: True)
    monkeypatch.setattr(terminals, "snapshot_problem", lambda: problem["err"])
    with TestClient(app) as c:
        def _row():
            return c.get("/diagnostics").json()["background_loops"].get("terminal_snapshot")

        assert _until(lambda: (_row() or {}).get("ok") is False), _row()
        assert "No space left on device" in _row()["last_error"]
        problem["err"] = None
        assert _until(lambda: (_row() or {}).get("ok") is True), _row()


def test_a_snapshot_that_raises_is_a_failing_pass_not_a_silent_one(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(app_module, "_TERMINAL_SNAPSHOT_S", 0.05)
    app = create_app(str(tmp_path))
    terminals = app.state.platform.terminals

    def _boom():
        raise RuntimeError("index unwritable")

    monkeypatch.setattr(terminals, "snapshot_if_changed", _boom)
    with caplog.at_level(logging.WARNING):
        with TestClient(app) as c:
            assert _until(
                lambda: (c.get("/diagnostics").json()["background_loops"].get("terminal_snapshot") or {}).get("ok")
                is False
            )
    assert any("periodic terminal snapshot failed" in r.getMessage() and r.levelno >= logging.WARNING
               for r in caplog.records)


def _fake_session(sid: str) -> SimpleNamespace:
    return SimpleNamespace(id=sid, output_seq=1, _tail=b"hello", scrollback_bytes=lambda: b"hello world")


def test_the_manager_keeps_the_last_pane_write_failure_and_clears_it_on_success(tmp_path, monkeypatch, caplog):
    from iron_jarvis.terminals import manager as M

    fake_self = SimpleNamespace(state_path=tmp_path / "terminals.json")
    s = _fake_session("t_one")
    calls = {"n": 0}
    real = M._write_atomic

    def _flaky(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "Access is denied")
        real(path, data)

    monkeypatch.setattr(M, "_write_atomic", _flaky)
    with caplog.at_level(logging.DEBUG, logger=M.log.name):
        assert M.TerminalManager._write_scrollbacks(fake_self, [s]) == 0
        assert isinstance(M.TerminalManager.snapshot_problem(fake_self), PermissionError)
        # Retried next pass (not advanced), still failing: still reported...
        assert M.TerminalManager._write_scrollbacks(fake_self, [s]) == 0
        assert isinstance(M.TerminalManager.snapshot_problem(fake_self), PermissionError)
        # ...and once it writes, the problem is gone.
        assert M.TerminalManager._write_scrollbacks(fake_self, [s]) == 1
        assert M.TerminalManager.snapshot_problem(fake_self) is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    # The FIRST failure of a pane is a warning; the retries are not a flood.
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "history" in warnings[0].getMessage()
    assert (tmp_path / "terminals-scrollback" / "t_one.bin").read_bytes() == b"hello world"


# --------------------------------------------------------------------------
# L2 — the prune and the sweep are WARNINGs
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_event_prune_is_a_warning(monkeypatch, caplog):
    import iron_jarvis.core.db as db

    def _boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "prune_events", _boom)
    platform = SimpleNamespace(config=SimpleNamespace(event_retention_days=30), engine=None)
    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(app_module._event_prune_loop(platform, interval_s=0))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any("retention sweep failed" in r.getMessage() for r in caplog.records):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    hits = [r for r in caplog.records if "retention sweep failed" in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits), hits


@pytest.mark.asyncio
async def test_a_failing_repl_sweep_is_a_warning(caplog):
    class _Repl:
        async def sweep(self):
            raise RuntimeError("child would not die")

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(app_module._repl_sweep_loop(_Repl(), interval_s=0))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any("repl idle sweep failed" in r.getMessage() for r in caplog.records):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    hits = [r for r in caplog.records if "repl idle sweep failed" in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits), hits
