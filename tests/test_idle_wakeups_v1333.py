"""Idle loops sleep instead of polling (v1.333.0, D6).

The fleet sampler's cadence sleep and the browser socket's idle read both woke
every 0.25 s for their whole life (~345,000 wakeups a day each) to re-read a
value that changes a few times a day. Each now sleeps until something it is
waiting on happens. These pins COUNT wakeups; no wall-clock bar is asserted.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import ExtensionBackend
from iron_jarvis.browser.identity import PINNED_EXTENSION_ID
from iron_jarvis.browser.pairing import PairingStore
from iron_jarvis.browser.service import ACCESS_INTERACTIVE, ACCESS_OFF, BrowserRuntime
from iron_jarvis.core.config import load_config
from iron_jarvis.core.db import open_db
from iron_jarvis.core.events import EventBus, EventType
from iron_jarvis.daemon.routes import browser as browser_routes
from iron_jarvis.fleet.sampler import FleetSampler
from tests._fakes.browser_peer import BrowserPeer


def _counted_sampler(**kw) -> tuple[FleetSampler, list[float]]:
    sampler = FleetSampler(SimpleNamespace(), **kw)
    waits: list[float] = []
    real = sampler._wait_for_wake

    async def counted(timeout: float) -> None:
        waits.append(timeout)
        await real(timeout)

    sampler._wait_for_wake = counted
    return sampler, waits


@pytest.mark.asyncio
async def test_the_fleet_sampler_sleeps_an_idle_interval_in_one_wait():
    sampler, waits = _counted_sampler(interval_idle=1.0, interval_active=0.05)
    sampler._loop_ref = asyncio.get_running_loop()
    await sampler._sleep_cadence()
    assert len(waits) == 1  # was one wake per 0.25 s slice: 4 for this interval


@pytest.mark.asyncio
async def test_a_fleet_touch_from_another_thread_ends_the_idle_sleep_at_once():
    sampler, waits = _counted_sampler(interval_idle=30.0, interval_active=0.0)
    sampler._loop_ref = asyncio.get_running_loop()
    sleeping = asyncio.create_task(sampler._sleep_cadence())
    while not waits:  # the sleep is parked in its one wait
        await asyncio.sleep(0)
    threading.Thread(target=sampler.touch).start()  # GET /fleet runs in the threadpool
    await asyncio.wait_for(sleeping, timeout=10.0)  # a liveness cap, not a speed bar
    assert len(waits) == 1  # woken once and the active interval had already passed


@pytest.mark.asyncio
async def test_a_touch_during_an_active_lease_does_not_wake_the_loop():
    sampler, _ = _counted_sampler(interval_idle=30.0, interval_active=2.0)
    sampler._loop_ref = asyncio.get_running_loop()
    sampler.touch()  # starts the watch: one wake
    await asyncio.sleep(0)
    assert sampler._wake.is_set()
    sampler._wake.clear()
    sampler.touch()  # already watched: nothing to wake for
    await asyncio.sleep(0)
    assert not sampler._wake.is_set()


class _App:
    """The real ``/browser/ws`` over a real backend, store, config AND event bus."""

    def __init__(self, tmp_path, *, access: str) -> None:
        self.config = load_config(tmp_path)
        self.config.browser_access = access
        self.store = PairingStore(open_db(tmp_path / "idle.db"))
        self.backend = ExtensionBackend()
        self.runtime = BrowserRuntime(backend=self.backend, config=self.config, pairing=self.store)
        self.bus = EventBus()
        record = self.store.open_request(extension_id=PINNED_EXTENSION_ID)
        self.token = self.store.mint(record.request_id)
        self.app = FastAPI()
        deps = SimpleNamespace(platform=SimpleNamespace(browser=self.runtime, event_bus=self.bus))
        browser_routes.register(self.app, deps)


def _wait_for(predicate, what: str, cap_s: float = 10.0) -> None:
    deadline = time.monotonic() + cap_s
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def _wait_quiet(ticks: list, *, quiet_s: float, cap_s: float) -> None:
    """Return once ``ticks`` has not grown for ``quiet_s``, or after ``cap_s``
    (never an assertion: the caller's own count decides)."""
    deadline = time.monotonic() + cap_s
    seen, since = len(ticks), time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.02)
        if len(ticks) != seen:
            seen, since = len(ticks), time.monotonic()
        elif time.monotonic() - since >= quiet_s:
            return


def test_an_idle_paired_browser_socket_does_not_wake_between_keepalives(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_routes, "KEEPALIVE_S", 60.0)
    monkeypatch.setattr(browser_routes, "_ACCESS_POLL_S", 60.0)
    app = _App(tmp_path, access=ACCESS_INTERACTIVE)
    ticks: list[int] = []
    real_word = app.backend.access_word
    app.backend.access_word = lambda: (ticks.append(1), real_word())[1]
    with TestClient(app.app) as client:
        with BrowserPeer(client, token=app.token) as peer:
            peer.expect_ready()
            _wait_for(lambda: app.backend.connected, "the socket became authoritative")
            # v1.400.5: the read loop SEEDS itself with one read just after it
            # starts (`announced_access`); on a loaded runner that lands after
            # `connected` flips, inside the counted second (CI read [1]). Wait
            # for a quiet half second first. A loop that keeps re-reading never
            # goes quiet, so the cap runs out and the count below still fails.
            _wait_quiet(ticks, quiet_s=0.5, cap_s=3.0)
            ticks.clear()
            time.sleep(1.0)  # an idle second: the old loop re-read the word 4 times
            assert ticks == []


def test_a_settings_change_wakes_an_inert_socket_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_routes, "KEEPALIVE_S", 60.0)
    monkeypatch.setattr(browser_routes, "_ACCESS_POLL_S", 60.0)  # the backstop never fires
    app = _App(tmp_path, access=ACCESS_OFF)
    with TestClient(app.app) as client:
        with BrowserPeer(client, token=app.token) as peer:
            assert peer.expect_ready()["active"] is False
            app.config.browser_access = ACCESS_INTERACTIVE
            # What the settings writer publishes after it changed the value.
            client.portal.call(
                app.bus.publish, EventType.CONFIG_CHANGED, {"keys": ["browser_access"]}
            )
            promoted = peer.expect_frame(P.FRAME_READY, what="the promotion after the change")
            assert promoted["active"] is True
            _wait_for(lambda: app.backend.connected, "the promoted socket became authoritative")
