"""v1.229.0 (audit Wave 3, OBS2) — loop health that is TRUE.

``/diagnostics`` → ``background_loops`` recorded ``fleet`` and ``slack_socket``
as ``ok`` when the daemon ARMED them: ``FleetSampler._loop`` swallowed cycle
errors at DEBUG and never reported, and the Slack pump reconnects internally,
so a sampler whose every cycle blew up and a socket that never once connected
both read healthy forever. Both loops now report their own cycles through an
``on_tick`` seam, and the boot rehydration steps write the same failure key
(``last_error`` + ``at``) as every other loop — they wrote ``error`` while the
dashboard read ``error`` and every loop wrote ``last_error``, so a failing
loop rendered with no reason at all. ``error`` stays as an alias for one
release.

Converted from tests/_audit_20260904/test_obs_lane.py (the two F-OBS-3
repros) plus unit tests on the seams themselves.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


# --- 1. fleet: the loop reports its own cycles --------------------------------


def test_fleet_loop_reads_failed_after_failing_cycles(tmp_path, monkeypatch):
    from iron_jarvis.fleet.sampler import FleetSampler

    app = create_app(str(tmp_path))
    platform = app.state.platform
    calls = {"n": 0}

    async def _boom(self):
        calls["n"] += 1
        await asyncio.sleep(0)
        raise RuntimeError("fleet cycle exploded")

    monkeypatch.setattr(FleetSampler, "_sample_all_async", _boom)
    monkeypatch.setattr(FleetSampler, "_current_interval", lambda self: 0.05)
    # one truthy node so _arm_fleet arms the sampler
    monkeypatch.setattr(platform.fleet, "nodes", lambda: [object()])

    with TestClient(app) as c:
        deadline = time.time() + 5
        while calls["n"] < 3 and time.time() < deadline:
            time.sleep(0.05)
        assert calls["n"] >= 3, "the sampler never cycled"
        loops = c.get("/diagnostics").json()["background_loops"]
    assert "fleet" in loops, loops
    assert loops["fleet"]["ok"] is False, loops["fleet"]
    assert "RuntimeError: fleet cycle exploded" in loops["fleet"]["last_error"]
    assert loops["fleet"]["at"]


def test_fleet_loop_reads_ok_only_after_a_cycle_ran(tmp_path, monkeypatch):
    """Arming is not health: ``fleet`` appears in loop_health once a cycle has
    actually completed, and then says when it last succeeded."""
    from iron_jarvis.fleet.sampler import FleetSampler

    app = create_app(str(tmp_path))
    platform = app.state.platform
    calls = {"n": 0}

    async def _fine(self):
        calls["n"] += 1
        await asyncio.sleep(0)

    monkeypatch.setattr(FleetSampler, "_sample_all_async", _fine)
    monkeypatch.setattr(FleetSampler, "_current_interval", lambda self: 0.05)
    monkeypatch.setattr(platform.fleet, "nodes", lambda: [object()])

    with TestClient(app) as c:
        deadline = time.time() + 5
        while calls["n"] < 1 and time.time() < deadline:
            time.sleep(0.02)
        assert calls["n"] >= 1, "the sampler never cycled"
        loops = c.get("/diagnostics").json()["background_loops"]
    assert loops["fleet"]["ok"] is True, loops.get("fleet")
    assert loops["fleet"]["last_success_at"]


def test_fleet_is_absent_from_loop_health_until_its_first_cycle_completes(tmp_path, monkeypatch):
    """Arm-time is observed directly: the first cycle is held at a gate while
    /diagnostics is read, so a ``_tick("fleet", True)`` re-added at arm time
    shows up as ``fleet`` present before any cycle has run."""
    from iron_jarvis.fleet.sampler import FleetSampler

    app = create_app(str(tmp_path))
    platform = app.state.platform
    entered = threading.Event()
    release = threading.Event()
    done = {"n": 0}

    async def _gated(self):
        entered.set()
        while not release.is_set():  # hold the FIRST cycle open
            await asyncio.sleep(0.01)
        done["n"] += 1

    monkeypatch.setattr(FleetSampler, "_sample_all_async", _gated)
    monkeypatch.setattr(FleetSampler, "_current_interval", lambda self: 0.05)
    monkeypatch.setattr(platform.fleet, "nodes", lambda: [object()])

    with TestClient(app) as c:
        assert entered.wait(5), "the sampler never started its first cycle"
        loops = c.get("/diagnostics").json()["background_loops"]
        assert "fleet" not in loops, f"armed is not healthy: {loops.get('fleet')}"
        release.set()
        deadline = time.time() + 5
        while done["n"] < 1 and time.time() < deadline:
            time.sleep(0.02)
        assert done["n"] >= 1, "the first cycle never completed after release"
        loops = c.get("/diagnostics").json()["background_loops"]
    assert loops["fleet"]["ok"] is True, loops.get("fleet")
    assert loops["fleet"]["last_success_at"]


@pytest.mark.asyncio
async def test_sampler_on_tick_reports_each_cycle_and_survives_a_bad_reporter():
    from iron_jarvis.fleet.sampler import FleetSampler

    class _Registry:
        def nodes(self):
            return []

    ticks: list[tuple[bool, str | None]] = []
    mode = {"fail": True}

    def _on_tick(ok, exc):
        ticks.append((ok, None if exc is None else f"{type(exc).__name__}: {exc}"))
        raise ValueError("reporter itself is broken")  # must not kill the loop

    sampler = FleetSampler(_Registry(), on_tick=_on_tick)
    sampler._current_interval = lambda: 0.01  # type: ignore[method-assign]

    async def _cycle():
        await asyncio.sleep(0)
        if mode["fail"]:
            raise RuntimeError("cycle exploded")

    sampler._sample_all_async = _cycle  # type: ignore[method-assign]
    await sampler.start()
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(not ok for ok, _ in ticks):
                break
        assert (False, "RuntimeError: cycle exploded") in ticks, ticks
        mode["fail"] = False
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(ok for ok, _ in ticks):
                break
        assert (True, None) in ticks, ticks
    finally:
        await sampler.stop()


# --- 2. slack socket: ok on a real connect, failed on every refused dial ------


def _slack_socket_config(tmp_path, monkeypatch) -> None:
    """A platform whose config arms the Slack socket pump at boot."""
    from iron_jarvis.platform import build_platform

    monkeypatch.setenv("IRONJARVIS_INBOUND", "off")
    p = build_platform(str(tmp_path))
    (p.config.home / "config.toml").write_text(
        'default_provider = "mock"\n'
        "[comm.channels.slack]\n"
        'type = "slack"\n'
        "inbound_enabled = true\n"
        'allowed_senders = ["U123"]\n'
        'app_token_secret = "slack_app_token"\n',
        encoding="utf-8",
    )
    p.secrets.set("slack_app_token", "xapp-1-abc")
    p.engine.dispose()


def _skip_slack_boot_sleep(monkeypatch) -> None:
    """The socket loop sleeps 15s before its first dial; skip ONLY that sleep."""
    real_sleep = asyncio.sleep

    async def _fast_sleep(delay, *a, **kw):
        task = asyncio.current_task()
        coro = task.get_coro() if task is not None else None
        name = getattr(coro, "__qualname__", "") or ""
        if name.endswith("_slack_socket_loop"):
            return await real_sleep(0)
        return await real_sleep(delay, *a, **kw)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)


def test_slack_socket_reads_failed_while_it_never_connects(tmp_path, monkeypatch):
    _slack_socket_config(tmp_path, monkeypatch)
    calls = {"n": 0}

    def _boom(token):
        calls["n"] += 1
        raise ConnectionError("apps.connections.open refused")

    # SlackSocketMode binds ``open_ws or _default_open_ws`` in __init__, and the
    # lifespan constructs the instance, so patch the module default beforehand.
    import iron_jarvis.comm.slack_socket as slack_mod

    monkeypatch.setattr(slack_mod, "_default_open_ws", _boom)
    _skip_slack_boot_sleep(monkeypatch)
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        deadline = time.time() + 5
        loops = {}
        while time.time() < deadline:
            loops = c.get("/diagnostics").json()["background_loops"]
            if calls["n"] >= 1 and "slack_socket" in loops:
                break
            time.sleep(0.05)
    assert calls["n"] >= 1, "the socket never tried to connect"
    assert loops.get("slack_socket", {}).get("ok") is False, loops.get("slack_socket")
    assert "ConnectionError: apps.connections.open refused" in loops["slack_socket"]["last_error"]


def test_slack_socket_is_absent_from_loop_health_until_its_first_dial_returns(tmp_path, monkeypatch):
    """Arm-time is observed directly: the first ``apps.connections.open`` dial
    is held at a gate while /diagnostics is read, so a ``_tick("slack_socket",
    True)`` re-added before ``_socket.run`` shows up as ``slack_socket``
    present before the pump has connected or failed even once."""
    _slack_socket_config(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def _gated(token):  # runs in to_thread; holds the FIRST dial open
        entered.set()
        release.wait(10)
        raise ConnectionError("apps.connections.open refused")

    import iron_jarvis.comm.slack_socket as slack_mod

    monkeypatch.setattr(slack_mod, "_default_open_ws", _gated)
    _skip_slack_boot_sleep(monkeypatch)
    app = create_app(str(tmp_path))
    loops: dict = {}
    try:
        with TestClient(app) as c:
            assert entered.wait(5), "the socket never started its first dial"
            loops = c.get("/diagnostics").json()["background_loops"]
            assert "slack_socket" not in loops, (
                f"armed is not connected: {loops.get('slack_socket')}"
            )
            release.set()
            deadline = time.time() + 5
            while time.time() < deadline:
                loops = c.get("/diagnostics").json()["background_loops"]
                if "slack_socket" in loops:
                    break
                time.sleep(0.05)
    finally:
        release.set()  # never leave the dial thread parked on shutdown
    assert loops.get("slack_socket", {}).get("ok") is False, loops.get("slack_socket")
    assert "ConnectionError: apps.connections.open refused" in loops["slack_socket"]["last_error"]


@pytest.mark.asyncio
async def test_slack_pump_ticks_ok_on_connect_and_failed_on_drop():
    from iron_jarvis.comm.slack_socket import SlackSocketMode

    ticks: list[tuple[bool, str | None]] = []
    stop = asyncio.Event()
    opens = {"n": 0}

    def _open_ws(token):
        opens["n"] += 1
        if opens["n"] == 1:
            return "wss://fake"
        stop.set()  # the retry wait returns at once
        raise ConnectionError("dial refused")

    class _FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration  # Slack closed the socket: reconnect

        async def send(self, data):
            pass

    sock = SlackSocketMode(
        None,
        None,
        lambda name: None,
        lambda: {},
        open_ws=_open_ws,
        ws_connect=lambda url: _FakeWS(),
        on_tick=lambda ok, exc: ticks.append(
            (ok, None if exc is None else f"{type(exc).__name__}: {exc}")
        ),
    )
    await asyncio.wait_for(sock.run_channel("hq", "xapp-x", stop=stop), timeout=5)
    assert ticks == [(True, None), (False, "ConnectionError: dial refused")], ticks


# --- 3. one failure key for every loop ----------------------------------------


def test_rehydrate_step_failure_writes_last_error_with_the_error_alias(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    platform = app.state.platform

    def _boom():
        raise RuntimeError("goals exploded")

    monkeypatch.setattr(platform.goal_engine, "rehydrate", _boom)
    with TestClient(app) as c:
        loops = c.get("/diagnostics").json()["background_loops"]
    entry = loops["rehydrate_goals"]
    assert entry["ok"] is False
    assert entry["last_error"] == "RuntimeError: goals exploded"
    assert entry["error"] == entry["last_error"]  # alias, one release
    assert entry["at"]
    # the other steps still ran and report the same shape as any other loop
    assert loops["reconcile_sessions"]["ok"] is True
    assert loops["reconcile_sessions"]["last_success_at"]
