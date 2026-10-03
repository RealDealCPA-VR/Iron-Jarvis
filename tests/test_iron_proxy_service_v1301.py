"""Iron-Proxy service + client (v1.301.0): find it, start it, stop it, talk to it.

Driven against a REAL fake Iron-Proxy (``tests/fixtures/fake_iron_proxy_v1301.py``)
— in-process for "a proxy the user already runs", and as a spawned CHILD
PROCESS (``IRONJARVIS_IRON_PROXY_NODE`` = this interpreter,
``IRONJARVIS_IRON_PROXY_BUNDLE`` = the fake script) for "the bundle Iron Jarvis
starts itself". The load-bearing behaviours, each mutation-checked:

* a running Iron-Proxy is REUSED, never respawned;
* ``stop()`` kills only an OWNED child and leaves a user-started one alone;
* the token never appears in ``status()``, an error, or a repr;
* the enabled flag is persisted to config.toml.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from iron_jarvis.iron_proxy import service as svc_mod
from iron_jarvis.iron_proxy.client import IronProxyClient, IronProxyError
from iron_jarvis.iron_proxy.service import IronProxyService, current, watch

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FakeIronProxy  # noqa: E402


def _reap_children_of(data_dir: Path) -> None:
    """A test that fails mid-way must not leave an Iron-Proxy child running:
    kill any process whose command line names this test's data dir."""
    needle = str(data_dir)
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if needle in " ".join(proc.info.get("cmdline") or []):
                proc.kill()
        except (psutil.Error, OSError):
            pass


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never touch the user's real ~/.iron-proxy or a real bundle."""
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("FAKE_IRON_PROXY_EXIT", "FAKE_IRON_PROXY_START_DELAY"):
        monkeypatch.delenv(k, raising=False)
    yield
    _reap_children_of(tmp_path / "ipd")


def _cfg(tmp_path, enabled=True):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return SimpleNamespace(home=home, iron_proxy_enabled=enabled)


def _use_fake_bundle(monkeypatch):
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_NODE", sys.executable)
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(FIXTURE))


@pytest.fixture
def running_fake(tmp_path):
    """An Iron-Proxy the USER started (in-process, proxy.json names this pid)."""
    fake = FakeIronProxy(tmp_path / "ipd").start()
    yield fake
    fake.stop()


def _descriptor(tmp_path) -> dict:
    return json.loads((tmp_path / "ipd" / "proxy.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #
def test_client_round_trips_every_control_call(running_fake):
    c = IronProxyClient(running_fake.url, running_fake.token)
    assert c.health()["ok"] is True
    a = c.create_profile("anthropic", "Work Claude")
    b = c.create_profile("anthropic", "Home Claude")
    assert a["lane"] == "cli" and a["provider"] == "anthropic"
    assert [p["id"] for p in c.profiles()] == [a["id"], b["id"]]
    assert c.update_profile(a["id"], title="Work")["title"] == "Work"
    c.reorder("anthropic", [b["id"], a["id"]])
    assert [p["id"] for p in c.profiles()] == [b["id"], a["id"]]
    picked = c.pick("anthropic")
    assert picked["profile"]["id"] == b["id"]
    assert picked["env"]["set"]["CLAUDE_CONFIG_DIR"] == b["cli"]["home"]
    sig = c.signal(b["id"], status=429, headers={"Retry-After": "60"}, text="slow down")
    assert sig["parked"] is True and sig["state"]["status"] == "parked"
    # the header names travel lower-cased, as the contract says
    assert running_fake.calls[-1][2]["headers"] == {"retry-after": "60"}
    assert c.pick("anthropic")["profile"]["id"] == a["id"]
    fin = c.finished(a["id"], usage={"inputTokens": 3, "outputTokens": 4}, duration_ms=12, model="m")
    assert fin["ok"] and fin["state"]["status"] == "active"
    assert running_fake.calls[-1][2] == {
        "usage": {"inputTokens": 3, "outputTokens": 4}, "durationMs": 12, "model": "m"
    }
    c.unpark(b["id"])
    assert c.states()[b["id"]]["status"] == "ready"
    assert c.usage()[0]["profileId"] in (a["id"], b["id"])
    assert c.usage(a["id"])[0]["profileId"] == a["id"]
    assert c.login_command(a["id"])["binary"] == "claude"
    c.logout(a["id"])
    assert c.states()[a["id"]]["status"] == "unauthenticated"
    adopted = c.adopt("openai", "C:/Users/x/.codex", title="This PC")
    assert adopted["cli"]["adopted"] is True and adopted["title"] == "This PC"
    assert c.discover() == []
    c.delete_profile(adopted["id"])
    assert adopted["id"] not in {p["id"] for p in c.profiles()}


def test_client_update_is_partial_and_refuses_unknown_fields(running_fake):
    c = IronProxyClient(running_fake.url, running_fake.token)
    p = c.create_profile("anthropic", "A")
    c.update_profile(p["id"], enabled=False)
    c.update_profile(p["id"], title="B")
    assert running_fake.calls[-1][2] == {"title": "B"}
    assert c.profiles()[0]["enabled"] is False
    with pytest.raises(ValueError):
        c.update_profile(p["id"], cli={"home": "x"})


def test_client_parses_the_json_dialect_errors(running_fake):
    """NO_PROFILE / ALL_PROFILES_EXHAUSTED / AUTH_REQUIRED are distinguishable,
    with Iron-Proxy's details and hint — the server.ts ``errorBody`` json shape."""
    c = IronProxyClient(running_fake.url, running_fake.token)
    with pytest.raises(IronProxyError) as e:
        c.pick("anthropic")
    assert (e.value.code, e.value.status) == ("NO_PROFILE", 404)

    a = c.create_profile("anthropic", "A")
    c.signal(a["id"], status=429)
    with pytest.raises(IronProxyError) as e:
        c.pick("anthropic")
    assert (e.value.code, e.value.status) == ("ALL_PROFILES_EXHAUSTED", 429)
    assert e.value.details["resetAt"]

    c.unpark(a["id"])
    c.signal(a["id"], status=401)
    with pytest.raises(IronProxyError) as e:
        c.pick("anthropic")
    assert e.value.code == "AUTH_REQUIRED"
    assert e.value.details == {"profileId": a["id"], "title": "A"}

    with pytest.raises(IronProxyError) as e:
        c.update_profile("prof_nope", title="x")
    assert e.value.code == "PROFILE_NOT_FOUND"
    assert e.value.sentence.startswith("Run iron-proxy profiles list")  # raw; routes rewrite


def test_transport_failure_is_unreachable_and_names_no_token(running_fake):
    url, token = running_fake.url, running_fake.token
    running_fake.stop()
    hits = []
    c = IronProxyClient(url, token, timeout=1.0, on_unreachable=lambda: hits.append(1))
    with pytest.raises(IronProxyError) as e:
        c.profiles()
    assert e.value.code == "UNREACHABLE" and e.value.status is None
    assert hits == [1]
    for text in (str(e.value), repr(e.value), repr(c), e.value.sentence):
        assert token not in text


def test_a_refused_token_is_an_error_not_a_crash(running_fake):
    c = IronProxyClient(running_fake.url, "wrong-token")
    with pytest.raises(IronProxyError) as e:
        c.profiles()
    assert e.value.status == 401 and e.value.code == "AUTH_REQUIRED"
    assert e.value.details == {}  # never read as an account needing sign-in
    assert e.value.sentence == "Iron-Proxy refused Iron Jarvis's access token."


# --------------------------------------------------------------------------- #
# locate / reuse
# --------------------------------------------------------------------------- #
def test_locate_finds_a_running_proxy_and_ignores_stale_or_dead_ones(tmp_path, running_fake):
    s = IronProxyService(_cfg(tmp_path), register=False)
    assert s.locate() == (running_fake.url, running_fake.token, os.getpid())

    # a pid that is not alive -> not running, whatever the file says
    desc = _descriptor(tmp_path)
    dead = 999_999
    while psutil.pid_exists(dead):
        dead += 1
    (tmp_path / "ipd" / "proxy.json").write_text(json.dumps({**desc, "pid": dead}))
    assert s.locate() is None

    # alive pid but nothing answers health at the url -> not running
    (tmp_path / "ipd" / "proxy.json").write_text(
        json.dumps({**desc, "url": "http://127.0.0.1:9"})
    )
    assert s.locate() is None

    (tmp_path / "ipd" / "proxy.json").write_text("{torn")
    assert s.locate() is None


def test_start_REUSES_a_running_proxy_and_never_spawns(tmp_path, monkeypatch, running_fake):
    """A tray-app / `iron-proxy serve` instance is used as-is: owned=False and no
    child is started (MUTATION: skip the locate in start -> a child spawns)."""
    _use_fake_bundle(monkeypatch)
    spawned = []
    real_popen = svc_mod.subprocess.Popen
    monkeypatch.setattr(
        svc_mod.subprocess, "Popen", lambda *a, **k: spawned.append(a) or real_popen(*a, **k)
    )
    s = IronProxyService(_cfg(tmp_path), register=False)
    try:
        st = s.start()
        assert spawned == []
        assert st["running"] is True and st["owned"] is False
        assert st["url"] == running_fake.url
        assert s.client() is not None and s.client().profiles() == []
        # a second start is still a reuse
        s.start()
        assert spawned == []
    finally:
        s.stop()


# --------------------------------------------------------------------------- #
# spawn / stop (a REAL child process)
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(60)
def test_start_spawns_the_bundle_and_stop_kills_only_that_child(tmp_path, monkeypatch):
    _use_fake_bundle(monkeypatch)
    monkeypatch.setenv("IRONJARVIS_TOKEN", "daemon-secret-xyz")
    s = IronProxyService(_cfg(tmp_path), register=False)
    st = s.start()
    try:
        assert st["running"] is True, st
        assert st["owned"] is True and st["error"] is None
        desc = _descriptor(tmp_path)
        child_pid = desc["pid"]
        assert psutil.pid_exists(child_pid)
        assert st["url"] == desc["url"]
        # the bundle got the contract's argv; Electron's switch only for a non-node exe;
        # the daemon's own credential never reaches the child
        child = json.loads((tmp_path / "ipd" / "fake-child-env.json").read_text())
        assert child["argv"] == ["serve", "--port", "0", "--data-dir", str(tmp_path / "ipd")]
        assert child["ELECTRON_RUN_AS_NODE"] == "1"
        assert child["IRONJARVIS_TOKEN"] is None
        # the client talks to the child with the token it wrote
        c = s.client()
        assert c is not None
        assert c.create_profile("anthropic", "A")["title"] == "A"
        assert (tmp_path / "home" / "logs" / "iron-proxy.log").exists()
        # a second start REUSES our own child (no second spawn)
        st2 = s.start()
        assert st2["owned"] is True and _descriptor(tmp_path)["pid"] == child_pid
    finally:
        s.stop()
    deadline = time.monotonic() + 10
    while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid)
    assert not (tmp_path / "ipd" / "proxy.json").exists()
    assert s.status()["running"] is False and s.client() is None


def test_stop_never_touches_a_proxy_the_user_started(tmp_path, running_fake):
    """OWNED-ONLY stop (MUTATION: drop the owned guard -> the user's proxy is
    killed / its descriptor removed)."""
    s = IronProxyService(_cfg(tmp_path), register=False)
    assert s.start()["owned"] is False
    s.stop()
    assert psutil.pid_exists(os.getpid())  # we are the "user's" proxy process
    assert (tmp_path / "ipd" / "proxy.json").exists()
    c = IronProxyClient(running_fake.url, running_fake.token)
    assert c.health()["ok"] is True
    assert s.status()["running"] is False


@pytest.mark.timeout(60)
def test_a_child_that_exits_at_once_is_one_sentence(tmp_path, monkeypatch):
    _use_fake_bundle(monkeypatch)
    monkeypatch.setenv("FAKE_IRON_PROXY_EXIT", "3")
    s = IronProxyService(_cfg(tmp_path), register=False)
    st = s.start()
    assert st["running"] is False
    assert "exit code 3" in st["error"] and "iron-proxy.log" in st["error"]
    assert st["error"].count(".") >= 1 and "\n" not in st["error"]


def test_no_bundle_and_nothing_running_says_so(tmp_path):
    s = IronProxyService(_cfg(tmp_path), register=False)
    st = s.start()
    assert st["running"] is False and st["bundled"] is False
    assert "does not include Iron-Proxy" in st["error"]
    assert "start Iron-Proxy yourself" in st["error"]


def test_frozen_build_has_no_dev_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_BUNDLE", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    s = IronProxyService(_cfg(tmp_path), register=False)
    assert s._bundle_path() is None and s.bundled() is False


def test_dev_fallback_is_the_vendored_bundle_in_the_repo(tmp_path, monkeypatch):
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_BUNDLE", raising=False)
    s = IronProxyService(_cfg(tmp_path), register=False)
    p = s._bundle_path()
    assert p is not None
    assert p.parts[-4:] == ("desktop", "vendor", "iron-proxy", "iron-proxy.mjs")
    assert (p.parents[3] / "pyproject.toml").exists()


# --------------------------------------------------------------------------- #
# status / client / flag
# --------------------------------------------------------------------------- #
def test_status_never_carries_the_token(tmp_path, running_fake):
    s = IronProxyService(_cfg(tmp_path), register=False)
    st = s.start()
    assert set(st) >= {"enabled", "running", "owned", "url", "version", "error", "bundled"}
    assert running_fake.token not in json.dumps(st)
    assert running_fake.token not in repr(s.client())


def test_version_comes_from_health_when_reported(tmp_path):
    fake = FakeIronProxy(tmp_path / "ipd", health_version="1.4.0").start()
    try:
        s = IronProxyService(_cfg(tmp_path), register=False)
        assert s.start()["version"] == "1.4.0"
    finally:
        fake.stop()


def test_client_is_none_when_off_and_relocates_after_unreachable(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    first = FakeIronProxy(tmp_path / "ipd").start()
    s = IronProxyService(cfg, register=False)
    assert s.client() is None  # off: never handed out, even with one running
    cfg.iron_proxy_enabled = True
    c1 = s.client()
    assert c1 is not None and c1.url == first.url
    first.stop()
    with pytest.raises(IronProxyError):
        c1.profiles()  # UNREACHABLE drops the cache
    assert s.client() is None  # nothing running now
    second = FakeIronProxy(tmp_path / "ipd").start()  # e.g. the tray app restarted
    try:
        c2 = s.client()
        assert c2 is not None and c2.url == second.url
        assert c2.profiles() == []
    finally:
        second.stop()


def test_enabled_flag_is_persisted_to_config_toml(tmp_path):
    """MUTATION: drop the persist -> a restart forgets the switch."""
    from iron_jarvis.core.config import load_config

    root = tmp_path / "proj"
    root.mkdir()
    cfg = load_config(root)
    assert cfg.iron_proxy_enabled is False
    s = IronProxyService(cfg, register=False)
    s.set_enabled(True)
    assert load_config(root).iron_proxy_enabled is True
    s.set_enabled(False)
    assert load_config(root).iron_proxy_enabled is False


def test_current_is_the_service_the_platform_built(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    assert isinstance(platform.iron_proxy, IronProxyService)
    assert current() is platform.iron_proxy
    assert platform.iron_proxy.client() is None  # off by default


# --------------------------------------------------------------------------- #
# the lifespan loop
# --------------------------------------------------------------------------- #
def test_watch_reports_each_cycle_off_the_loop_and_clears_when_off():
    loop_thread: list[int] = []
    ran_on: list[int] = []
    ticks: list[tuple[bool, str]] = []
    cleared: list[int] = []

    class _Svc:
        enabled = True
        fail = False

        def ensure_running(self):
            ran_on.append(threading.get_ident())
            if self.fail:
                raise RuntimeError("Iron-Proxy did not answer.")
            return True

    svc = _Svc()

    async def main():
        loop_thread.append(threading.get_ident())
        task = asyncio.create_task(
            watch(svc, lambda ok, exc: ticks.append((ok, str(exc or ""))),
                  clear=lambda: cleared.append(1), interval=0.01, max_interval=0.02)
        )
        while len(ticks) < 1:
            await asyncio.sleep(0.005)
        svc.fail = True
        while not any(not ok for ok, _ in ticks):
            await asyncio.sleep(0.005)
        svc.enabled = False
        while not cleared:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert ticks[0] == (True, "")
    assert (False, "Iron-Proxy did not answer.") in ticks
    assert ran_on and all(t != loop_thread[0] for t in ran_on)
    assert cleared


# --------------------------------------------------------------------------- #
# the REAL vendored bundle through the service (skipped when not vendored / no node)
# --------------------------------------------------------------------------- #
_BUNDLE = Path(__file__).resolve().parents[1] / "desktop" / "vendor" / "iron-proxy" / "iron-proxy.mjs"


@pytest.mark.timeout(60)
@pytest.mark.skipif(not _BUNDLE.is_file(), reason="Iron-Proxy bundle not vendored")
@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node is not on PATH")
def test_the_real_bundle_through_the_service(tmp_path, monkeypatch):
    """Dev fallback: `node` on PATH + desktop/vendor/iron-proxy/iron-proxy.mjs,
    data dir a tmp dir (never ~/.iron-proxy). Health answers, the profile list
    is empty, and stop() leaves no process behind."""
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_BUNDLE", raising=False)
    s = IronProxyService(_cfg(tmp_path), register=False)
    assert s.bundled() is True
    st = s.start()
    try:
        assert st["running"] is True and st["owned"] is True, st
        assert st["version"] == s._bundle_version() and st["version"]
        assert st["error"] is None and s._outdated is False  # executor-v1 detected
        desc = _descriptor(tmp_path)
        family = svc_mod._family(desc["pid"]) | {desc["pid"]}
        c = s.client()
        assert c is not None
        assert c.health()["ok"] is True
        assert c.profiles() == []
        assert desc["token"] not in json.dumps(st)
    finally:
        s.stop()
    deadline = time.monotonic() + 10
    while any(psutil.pid_exists(p) for p in family) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(psutil.pid_exists(p) for p in family)
    assert not (tmp_path / "ipd" / "proxy.json").exists()


# --------------------------------------------------------------------------- #
# review round: lease_client, the account snapshot, old proxies, crash orphans
# --------------------------------------------------------------------------- #
from iron_jarvis.iron_proxy.service import (  # noqa: E402
    NOT_ANSWERING,
    OUTDATED,
    TOKEN_REFUSED,
    IronProxyUnavailable,
)


def test_lease_client_is_none_only_when_off(tmp_path, running_fake):
    cfg = _cfg(tmp_path, enabled=False)
    s = IronProxyService(cfg, register=False)
    assert s.lease_client() is None  # off: the call runs on this PC's login
    cfg.iron_proxy_enabled = True
    c = s.lease_client()
    assert c is not None and c.url == running_fake.url


def test_lease_client_refuses_in_one_sentence_when_on_but_not_answering(tmp_path):
    """On + nothing running + nothing to start: a sentence, never None (None
    would silently run the call on this PC's own login)."""
    s = IronProxyService(_cfg(tmp_path), register=False)
    with pytest.raises(IronProxyUnavailable) as e:
        s.lease_client(timeout_s=1.0)
    assert e.value.sentence == NOT_ANSWERING


def test_lease_client_waits_for_a_start_in_progress(tmp_path, monkeypatch):
    """A lease that arrives while the lifespan is starting Iron-Proxy waits for
    it (MUTATION: lock timeout 0 -> NOT_ANSWERING)."""
    _use_fake_bundle(monkeypatch)
    monkeypatch.setenv("FAKE_IRON_PROXY_START_DELAY", "1.5")
    s = IronProxyService(_cfg(tmp_path), register=False)
    started = threading.Event()
    real_spawn = s._spawn

    def spawn_and_signal():
        started.set()
        real_spawn()

    s._spawn = spawn_and_signal  # type: ignore[method-assign]
    t = threading.Thread(target=s.start)
    t.start()
    try:
        assert started.wait(5)
        c = s.lease_client(timeout_s=15.0)
        assert c is not None and c.health()["ok"] is True
        assert s.status()["owned"] is True
    finally:
        t.join(15)
        s.stop()


def test_lease_client_starts_iron_proxy_itself_when_nothing_runs(tmp_path, monkeypatch):
    _use_fake_bundle(monkeypatch)
    s = IronProxyService(_cfg(tmp_path), register=False)
    try:
        c = s.lease_client()
        assert c is not None and s.status()["owned"] is True
    finally:
        s.stop()


def test_an_older_iron_proxy_is_used_for_nothing_and_says_so(tmp_path):
    """A pre-executor Iron-Proxy (no `features` on health; /iron/pick 404) at
    the same version: status names it, a lease refuses it (MUTATION: ignore
    features -> the lease hands out a client whose pick 404s)."""
    old = FakeIronProxy(tmp_path / "ipd", features=None).start()
    try:
        s = IronProxyService(_cfg(tmp_path), register=False)
        st = s.start()
        assert st["running"] is True and st["error"] == OUTDATED
        with pytest.raises(IronProxyUnavailable) as e:
            s.lease_client()
        assert e.value.sentence == OUTDATED
        assert s.client() is not None  # Connections can still manage accounts
        # a usable-looking account in the snapshot still does not count on an
        # older proxy (MUTATION F3b: drop the outdated gate -> True)
        old.add_profile("anthropic", "A")
        s.refresh_accounts()
        assert s._usable.get("anthropic") is True
        assert s.has_usable_account("claude-cli") is False
        with pytest.raises(RuntimeError, match="older than this Iron Jarvis"):
            s.ensure_running()
    finally:
        old.stop()


def test_a_refused_token_makes_the_lease_refuse(tmp_path, running_fake):
    s = IronProxyService(_cfg(tmp_path), register=False)
    c = s.lease_client()
    running_fake.add_profile("anthropic", "A")
    s.refresh_accounts()
    assert s.has_usable_account("claude-cli") is True
    running_fake.token = "rotated"  # proxy.json still names the old one
    with pytest.raises(IronProxyError):
        c.profiles()
    assert s.status()["error"] == TOKEN_REFUSED
    # the snapshot still says "usable", but a refused token is not (MUTATION
    # F3b: drop the token gate -> True)
    assert s._usable.get("anthropic") is True
    assert s.has_usable_account("claude-cli") is False
    with pytest.raises(IronProxyUnavailable) as e:
        s.lease_client()
    assert e.value.sentence == TOKEN_REFUSED
    # the proxy restarts with a fresh token -> the lease recovers by itself
    running_fake.write_descriptor(os.getpid())
    assert s.lease_client() is not None
    assert s.status()["error"] is None


def test_two_threads_refused_at_once_reread_proxy_json_once(running_fake):
    """The rebind is serialised: one re-read, both calls succeed on it
    (MUTATION: drop the bind lock -> two re-reads)."""
    old_token = running_fake.token
    running_fake.token = "new-token"
    reads: list[int] = []

    def refresh():
        reads.append(1)
        time.sleep(0.3)
        return running_fake.url, "new-token"

    c = IronProxyClient(running_fake.url, old_token, refresh=refresh)
    out: list = []
    gate = threading.Barrier(2)

    def call():
        gate.wait()
        out.append(c.profiles())

    ts = [threading.Thread(target=call) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert out == [[], []]
    assert reads == [1]


def test_usable_account_snapshot_is_cached_and_honest(tmp_path, running_fake):
    s = IronProxyService(_cfg(tmp_path), register=False)
    s.start()
    assert s.has_usable_account("claude-cli") is False  # no accounts yet
    a = running_fake.add_profile("anthropic", "A")
    running_fake.add_profile("openai", "Off", enabled=False)
    running_fake.add_profile("xai", "Needs sign-in")
    running_fake.states[[p for p in running_fake.profiles if running_fake.profiles[p]["provider"] == "xai"][0]]["status"] = "unauthenticated"
    running_fake.add_profile("openai", "API", "api-key")
    assert s.has_usable_account("claude-cli") is False  # not refreshed yet: cached
    s.refresh_accounts()
    before = len(running_fake.calls)
    assert s.has_usable_account("claude-cli") is True
    assert s.has_usable_account("codex-cli") is False  # disabled CLI + API-key only
    assert s.has_usable_account("grok-cli") is False  # needs sign-in
    assert s.has_usable_account("mock") is False
    assert len(running_fake.calls) == before, "availability must never call Iron-Proxy"
    running_fake.signal(a["id"], {"status": 401})
    s.refresh_accounts()
    assert s.has_usable_account("claude-cli") is False
    s._config.iron_proxy_enabled = False
    assert s.has_usable_account("claude-cli") is False


def test_status_reads_no_disk_on_the_loop(tmp_path, monkeypatch):
    """status() runs in route handlers ON the loop: the bundle facts are read at
    construction / off-loop refresh only (MUTATION: status() calls bundled())."""
    s = IronProxyService(_cfg(tmp_path), register=False)

    def boom(*_a, **_k):
        raise AssertionError("status() touched the disk")

    monkeypatch.setattr(s, "bundled", boom)
    monkeypatch.setattr(s, "_bundle_version", boom)
    st = s.status()
    assert st["bundled"] is False


@pytest.mark.timeout(60)
def test_a_crash_orphan_is_still_ours_to_stop(tmp_path, monkeypatch):
    """The daemon that started Iron-Proxy crashed; the next one finds it by the
    recorded pid + create time and stops it on disable (MUTATION: ignore the
    record -> owned=False and the child survives)."""
    _use_fake_bundle(monkeypatch)
    first = IronProxyService(_cfg(tmp_path), register=False)
    assert first.start()["owned"] is True
    record = tmp_path / "home" / "iron-proxy-owned.json"
    assert record.exists()
    pid = _descriptor(tmp_path)["pid"]
    # "crash": the first service is never stopped; a new daemon comes up
    second = IronProxyService(_cfg(tmp_path), register=False)
    st = second.start()
    assert st["running"] is True and st["owned"] is True
    second.stop()
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)
    assert not record.exists()
    assert not (tmp_path / "ipd" / "proxy.json").exists()


@pytest.mark.timeout(60)
def test_a_reused_pid_is_not_mistaken_for_our_child(tmp_path, monkeypatch, running_fake):
    """A record whose pid matches but whose create time does not is someone
    else's process (pid reuse): never owned, never killed."""
    record = tmp_path / "home" / "iron-proxy-owned.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    me = psutil.Process(os.getpid())
    record.write_text(json.dumps({
        "root_pid": os.getpid(), "root_create_time": me.create_time() - 100,
        "server_pid": os.getpid(), "server_create_time": me.create_time() - 100,
    }))
    s = IronProxyService(_cfg(tmp_path), register=False)
    assert s.start()["owned"] is False
    s.stop()
    assert running_fake.server is not None
    assert IronProxyClient(running_fake.url, running_fake.token).health()["ok"] is True
