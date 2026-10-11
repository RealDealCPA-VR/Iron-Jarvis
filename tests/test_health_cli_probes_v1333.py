"""/health never launches a subscription CLI (v1.333.0, D4 + D5).

``/health`` -> ``providers.health()`` -> ``cli_login_status`` -> the shared
``cli_auth.DEFAULT_PROBE.verdict()``, which started ``claude auth status`` /
``codex login status`` on a thread whenever the 120 s cache was stale. The
desktop watchdog (30 s) and the dashboard keep /health busy, so that was
~1,100-1,400 Node CLI launches a day. A status view now reads the cached
verdict and never refreshes; the router's own ``available()`` still does.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers import manager as mgr
from iron_jarvis.providers.manager import ProviderManager


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def probe(monkeypatch):
    """A probe whose runner COUNTS launches, with a clock the test moves, put
    where the manager reads it; CLIs installed (conftest says not)."""
    launches: list[list[str]] = []
    lock = threading.Lock()

    def runner(argv: list[str]):
        with lock:
            launches.append(list(argv))
        if "auth" in argv:  # claude auth status
            return 0, '{"loggedIn": true, "authMethod": "claude.ai"}', ""
        return 0, "Logged in using ChatGPT", ""  # codex login status

    clock = _Clock()
    p = cli_auth.CliAuthProbe(runner=runner, which=lambda b: f"/fake/{b}", clock=clock)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", p)
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: True))
    # What the real `_cli_signed_in` does (conftest stubs it to None).
    monkeypatch.setattr(
        ProviderManager,
        "_cli_signed_in",
        staticmethod(lambda b: cli_auth.DEFAULT_PROBE.verdict(b)),
    )
    p.launches = launches
    p.fake_clock = clock
    return p


def _wait_for(predicate, cap_s: float = 10.0) -> None:
    deadline = time.monotonic() + cap_s
    while not predicate():
        assert time.monotonic() < deadline, "condition never held"
        time.sleep(0.01)


def test_a_hundred_health_calls_start_no_sign_in_probe(probe, tmp_path):
    probe.refresh("claude")
    probe.refresh("codex")
    assert len(probe.launches) == 2
    with TestClient(create_app(str(tmp_path))) as client:
        _wait_for(lambda: not probe._inflight)  # any boot warm-up has landed
        probe.launches.clear()
        for _ in range(100):
            probe.fake_clock.now += 1000.0  # every cached verdict is stale
            res = client.get("/health")
            assert res.status_code == 200
        rows = {r["provider"]: r for r in res.json()["providers"]}
        assert probe.launches == []
        assert not probe._inflight
        # The cached verdict is still reported.
        assert rows["claude-cli"]["signed_in"] is True
        assert rows["codex-cli"]["signed_in"] is True
        # Anti-vacuity: the ROUTING path refreshes the same stale verdict.
        assert client.app.state.platform.providers.available("claude-cli") is True
        _wait_for(lambda: len(probe.launches) == 1)
        assert probe.launches[0][0] == "/fake/claude"


def test_health_with_nothing_cached_says_unknown_and_finds_out_once(probe):
    pm = ProviderManager(inherit_cli_logins=True)
    rows = {r["provider"]: r for r in pm.health()}
    assert rows["claude-cli"]["installed"] is True
    # Unknown (or, if the fake probe already landed, its answer): never a
    # guessed "signed out", and unknown keeps it available.
    assert rows["claude-cli"]["signed_in"] is not False
    assert rows["claude-cli"]["available"] is True
    # One probe per CLI finds out (a CLI installed after boot has no verdict
    # until something asks), however many status reads arrive meanwhile.
    for _ in range(20):
        pm.health()
    _wait_for(lambda: not probe._inflight)
    assert sorted(a[0] for a in probe.launches) == ["/fake/claude", "/fake/codex"]
    rows = {r["provider"]: r for r in pm.health()}
    assert rows["claude-cli"]["signed_in"] is True


def test_a_signed_out_cli_turns_green_by_itself_after_the_user_signs_in(probe):
    # Review (v1.333.0): the sign-in door says "this turns green by itself once
    # you're signed in" and nothing but a status read re-asks then. A signed
    # OUT verdict is re-checked by /health after UNSIGNED_RECHECK_S; a signed
    # IN one never is (the first test).
    pm = ProviderManager(inherit_cli_logins=True)
    probe.mark_signed_out("claude")
    probe.refresh("codex")
    probe.launches.clear()
    probe.fake_clock.now += cli_auth.UNSIGNED_RECHECK_S - 1  # not yet due
    rows = {r["provider"]: r for r in pm.health()}
    assert rows["claude-cli"]["signed_in"] is False
    assert probe.launches == []
    probe.fake_clock.now += 2  # due: the user finished /login meanwhile
    pm.health()
    _wait_for(lambda: not probe._inflight)
    assert [a[0] for a in probe.launches] == ["/fake/claude"]
    rows = {r["provider"]: r for r in pm.health()}
    assert rows["claude-cli"]["signed_in"] is True
    assert cli_auth.UNSIGNED_RECHECK_S <= 120


def test_the_verdict_and_presence_caches_last_minutes_not_seconds():
    assert cli_auth.CliAuthProbe().ttl_s == cli_auth.VERDICT_TTL_S >= 600
    assert mgr.CLI_PRESENCE_TTL_S >= 600
    assert mgr.CLI_ABSENT_TTL_S >= 60
