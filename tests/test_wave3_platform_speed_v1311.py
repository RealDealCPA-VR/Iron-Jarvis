"""Wave 3 (SPEED), Track B — the backend platform does less work, and none of it
on the event loop (v1.311.0).

Five verified findings, each pinned by COUNTING work (hops, queries, PATH
probes, walks, the thread a statement ran on) or by ORDERING (what had
finished when /health answered) — never by an absolute wall-clock bar, which
measures the hardware and goes red on CI.

* boot-blocked-by-mcp-and-pane-respawn — the DAEMON answers /health without
  waiting on a pack handshake; the pack is reported as "starting" (never
  silently missing), its tools register ON THE LOOP once ready, the quarantine
  still applies, ``mcp.loaded`` is published, the boot breakdown names
  ``platform.mcp``. ``build_platform`` called WITHOUT a lifespan (the ~40 CLI
  entrypoints) keeps the synchronous contract. Terminal panes restore
  concurrently, with ONE snapshot after the join.
* fs-files-newest-first-is-false-and-polled-4s — the walk keeps the newest N
  for real, is bounded by an entry budget AND a deadline, reports
  ``scan_truncated`` separately; /fs/files answers a weak ETag + 304; the access
  log stays quiet for it (and for /sessions/interrupted, and for 304s on quiet
  paths only).
* cli-binary-probe-uncached-on-loop — PATH probes are memoised (bounded TTL,
  invalidation), health() in steady state probes nothing, and the boot warm-up
  probes on its own thread.
* eventbus-serial-hops-and-webhook-select-per-event — one executor hop per
  publish (sequential, per-handler isolation kept); the outbound-webhook
  handler issues ZERO queries when no webhook can match, yet sees every ORM
  write on the next event; the events log line clips long payload strings.
* undo-revert-db-writes-on-loop — every undo/revert DB statement, and the
  config restore, run on a worker thread.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event

from iron_jarvis.mcp import FakeTransport, mcp_tools
from iron_jarvis.mcp import tools as mcp_tools_mod


def _on_a_loop() -> bool:
    """True when the CURRENT thread is running an asyncio event loop."""
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _wait_for(pred, timeout: float = 15.0, step: float = 0.02) -> bool:
    """Wait for the THING asserted (a generous liveness bound, not a perf bar)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return bool(pred())


@pytest.fixture(autouse=True)
def _isolated_home_and_records(monkeypatch):
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    mcp_tools_mod._LOAD_STATUS.clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()


# ==========================================================================
# 1) boot-blocked-by-mcp-and-pane-respawn
# ==========================================================================

SEARCH = {
    "name": "search",
    "description": "Search.",
    "inputSchema": {"type": "object", "properties": {}},
    "annotations": {"readOnlyHint": True},
}
#: write-like (no readOnlyHint) and NEW since the seeded manifest -> quarantined.
DELETE_ALL = {
    "name": "delete_all",
    "description": "Delete everything.",
    "inputSchema": {"type": "object", "properties": {}},
}
PACK = "slowpack"
#: How long the stubbed handshake parks when nobody releases it. On the OLD
#: code create_app() waits this out before /health can exist; on the new code
#: the test releases it itself, long before.
GATE_S = 3.0


def _write_pack_config(root: Path) -> Path:
    home = root / ".ironjarvis"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        "[[mcp_servers]]\n"
        f'name = "{PACK}"\n'
        'command = "ij-fake-launcher-w3-v1311"\n'
        "args = []\n",
        encoding="utf-8",
    )
    return home


def _seed_manifest(home: Path) -> None:
    """The user has seen this pack before with ONLY ``search`` — so a
    write-like tool that appears now must come back quarantined."""
    mcp_tools(
        [{"name": PACK, "transport_obj": FakeTransport({"tools/list": {"tools": [SEARCH]}})}],
        home=home,
    )
    mcp_tools_mod._LOAD_STATUS.clear()


def _gated_transport(monkeypatch):
    gate = threading.Event()
    entered = threading.Event()
    finished = threading.Event()

    def _build(cfg, _resolver):
        entered.set()
        gate.wait(GATE_S)
        finished.set()
        return FakeTransport({"tools/list": {"tools": [SEARCH, DELETE_ALL]}})

    monkeypatch.setattr(mcp_tools_mod, "_build_transport", _build)
    return gate, entered, finished


def test_daemon_answers_health_before_a_slow_pack_handshake_finishes(tmp_path, monkeypatch):
    """THE BOOT PIN, by ORDERING: the stubbed handshake parks until the test
    releases it, so if /health can answer at all while it is parked, boot did
    not wait for it. Old code: create_app() -> build_platform() -> mcp_tools()
    sits in the handshake for GATE_S before the app even exists."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.mcp.tools import load_status

    home = _write_pack_config(tmp_path)
    _seed_manifest(home)
    gate, _entered, finished = _gated_transport(monkeypatch)
    try:
        app = create_app(str(tmp_path))
        platform = app.state.platform
        # Registrations are recorded with the thread they ran on: the registry
        # is iterated by agent runs ON THE LOOP, so a worker thread must never
        # mutate it (verifier adjustment b).
        mcp_regs: list[tuple[str, bool]] = []
        real_register = platform.registry.register

        def _recording_register(tool, *args, **kw):
            if kw.get("mcp"):
                mcp_regs.append((tool.name, _on_a_loop()))
            return real_register(tool, *args, **kw)

        platform.registry.register = _recording_register  # instance attribute

        with TestClient(app) as c:
            health = c.get("/health")
            handshake_done_at_health = finished.is_set()
            status_while_parked = load_status(PACK)
            diag_rows = c.get("/diagnostics").json().get("mcp_servers") or []

            gate.set()  # the pack answers now
            full = f"mcp__{PACK}__search"
            appeared = _wait_for(lambda: full in platform.registry.mcp_names(PACK))
            steps = c.get("/diagnostics").json()["startup"]["steps_ms"]
            status_after = load_status(PACK)
            held = platform.registry.get(f"mcp__{PACK}__delete_all")
            loaded_events = [e for e in platform.event_bus.history if e.type == "mcp.loaded"]
    finally:
        gate.set()

    assert health.status_code == 200
    assert not handshake_done_at_health, (
        "/health answered only after the MCP handshake finished: the daemon "
        "boot still waits on pack handshakes (build_platform -> mcp_tools in create_app)"
    )
    # A slow pack is REPORTED, never silently missing.
    assert status_while_parked is not None and status_while_parked.get("state") == "starting", (
        status_while_parked
    )
    row = next((r for r in diag_rows if r.get("name") == PACK), None)
    assert row is not None and row.get("state") == "starting", diag_rows
    # ...and its tools DO arrive, registered on the loop thread.
    assert appeared, "the pack's tools never reached the registry after the handshake"
    assert mcp_regs and all(on_loop for _, on_loop in mcp_regs), mcp_regs
    assert status_after is not None and status_after.get("state") != "starting", status_after
    assert status_after.get("tools_loaded") == 2 and status_after.get("last_error") is None
    # Quarantine runs AFTER the background load (verifier adjustment c).
    assert held is not None and getattr(held, "quarantined", False) is True
    assert loaded_events, "no mcp.loaded event was published when the packs finished"
    # The boot breakdown names the MCP phase instead of one opaque "platform".
    assert "platform.mcp" in steps, steps


def test_build_platform_without_a_lifespan_keeps_the_synchronous_mcp_contract(tmp_path, monkeypatch):
    """ANTI-REGRESSION CONTROL (green today, must stay green): ~40 CLI
    entrypoints call build_platform() with no lifespan. Their MCP tools must be
    registered when build_platform RETURNS — deferring the load for them would
    silently drop every pack tool (CLAUDE.md, v1.257.0)."""
    from iron_jarvis.platform import build_platform

    _write_pack_config(tmp_path)
    monkeypatch.setattr(
        mcp_tools_mod,
        "_build_transport",
        lambda cfg, _r: FakeTransport({"tools/list": {"tools": [SEARCH, DELETE_ALL]}}),
    )
    platform = build_platform(str(tmp_path))
    names = platform.registry.mcp_names(PACK)
    assert f"mcp__{PACK}__search" in names and f"mcp__{PACK}__delete_all" in names, names
    st = mcp_tools_mod.load_status(PACK)
    assert st is not None and st.get("tools_loaded") == 2
    assert st.get("state", "ready") != "starting"


class _ClosingFake(FakeTransport):
    """A FakeTransport that records being closed — an MCP client the app
    decided not to register must be CLOSED (a real one owns a stdio child)."""

    def __init__(self, label: str, *a, **kw):
        super().__init__(*a, **kw)
        self.label = label
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def _one_gated_then_instant(monkeypatch):
    """The FIRST handshake (the boot's background load) parks until released;
    every later one (a Retry pressed in the window) answers at once."""
    gate = threading.Event()
    built: list[_ClosingFake] = []

    def _build(cfg, _resolver):
        first = not built
        t = _ClosingFake("boot" if first else "retry", {"tools/list": {"tools": [SEARCH]}})
        built.append(t)
        if first:
            gate.wait(GATE_S)
        return t

    monkeypatch.setattr(mcp_tools_mod, "_build_transport", _build)
    return gate, built


def _loaded(platform) -> bool:
    return any(e.type == "mcp.loaded" for e in platform.event_bus.history)


def test_a_pack_deleted_while_starting_never_registers_its_tools(tmp_path, monkeypatch):
    """REVIEW (v1.311.0): DELETE /mcp/servers/{name} during the background
    load used to be undone when the handshake landed — the removed pack's
    tools (and its live child) reached the registry, with no surface left
    that could unload them. The late result is dropped, its client CLOSED,
    and the pack's load record forgotten."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.mcp.tools import load_status

    _write_pack_config(tmp_path)
    gate, built = _one_gated_then_instant(monkeypatch)
    try:
        app = create_app(str(tmp_path))
        platform = app.state.platform
        with TestClient(app) as c:
            assert _wait_for(lambda: bool(built)), "the background handshake never started"
            gone = c.delete(f"/mcp/servers/{PACK}")
            gate.set()
            finished = _wait_for(lambda: _loaded(platform))
            names = platform.registry.mcp_names(PACK)
            listed = [s.get("name") for s in c.get("/mcp/servers").json().get("servers", [])]
            record = load_status(PACK)
    finally:
        gate.set()

    assert gone.status_code == 200
    assert finished, "mcp.loaded never arrived"
    assert names == [], f"a deleted pack's tools were registered after its load landed: {names}"
    assert PACK not in listed
    assert built[0].closed >= 1, "the dropped load's client was never closed (a live child leaks)"
    assert record is None, record


def test_deleting_a_pack_that_already_loaded_closes_its_client(tmp_path, monkeypatch):
    """v1.328.1: DELETE /mcp/servers/{name} on a pack that had FINISHED
    loading unregistered its tools but never closed their client, so the
    pack's stdio child ran on until the app restarted. CI found it when the
    test above lost its 3 s gate on a slow runner (the load landed first)."""
    from iron_jarvis.daemon.app import create_app

    _write_pack_config(tmp_path)
    gate, built = _one_gated_then_instant(monkeypatch)
    gate.set()  # no window: the boot load lands before the delete
    try:
        app = create_app(str(tmp_path))
        platform = app.state.platform
        with TestClient(app) as c:
            assert _wait_for(lambda: _loaded(platform)), "mcp.loaded never arrived"
            assert platform.registry.mcp_names(PACK), "the pack's tools never registered"
            assert built[0].closed == 0
            gone = c.delete(f"/mcp/servers/{PACK}")
            names = platform.registry.mcp_names(PACK)
    finally:
        gate.set()

    assert gone.status_code == 200 and gone.json()["tools_unloaded"] >= 1
    assert names == []
    assert built[0].closed >= 1, "the deleted pack's client is still open (a live child leaks)"


def test_a_retry_pressed_while_starting_wins_over_the_late_boot_load(tmp_path, monkeypatch):
    """REVIEW (v1.311.0): POST /mcp/servers/{name}/reload during the window
    registers a fresh connection; the boot load landing afterwards used to
    overwrite it and orphan the Retry's client. The Retry WINS: its tools stay
    registered (same client), and the boot load's client is closed."""
    from iron_jarvis.daemon.app import create_app

    _write_pack_config(tmp_path)
    gate, built = _one_gated_then_instant(monkeypatch)
    full = f"mcp__{PACK}__search"
    try:
        app = create_app(str(tmp_path))
        platform = app.state.platform
        with TestClient(app) as c:
            assert _wait_for(lambda: bool(built)), "the background handshake never started"
            retry = c.post(f"/mcp/servers/{PACK}/reload").json()
            after_retry = platform.registry.get(full)
            gate.set()
            finished = _wait_for(lambda: _loaded(platform))
            final = platform.registry.get(full)
    finally:
        gate.set()

    assert retry.get("ok") is True and retry.get("tools_loaded") == 1, retry
    assert finished, "mcp.loaded never arrived"
    assert len(built) == 2, [t.label for t in built]
    boot, again = built
    assert after_retry is not None and after_retry.client.transport is again
    assert final is not None and final.client.transport is again, (
        "the late boot load replaced the tools a Retry had already registered"
    )
    assert boot.closed >= 1, "the superseded boot load's client was never closed"
    assert again.closed == 0, "the Retry's live client was closed"


def test_terminal_panes_are_restored_concurrently_with_one_snapshot(tmp_path, monkeypatch):
    """Serial respawn costs ~0.3 s per pane on every boot. Proven by
    CONSTRUCTION: each spawn waits at a 3-party barrier that only releases when
    all three restores are in flight at once. A serial loop parks the first
    spawn alone until the barrier times out (BrokenBarrierError -> that pane
    is not restored)."""
    from iron_jarvis.terminals import TerminalManager
    from iron_jarvis.terminals import manager as term_mgr
    from iron_jarvis.terminals.backend import FakeBackend

    barrier = threading.Barrier(3, timeout=3.0)
    spawn_threads: list[int] = []

    class _BarrierBackend(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):  # noqa: D401 — test double
            spawn_threads.append(threading.get_ident())
            barrier.wait()
            return super().start(argv, cwd, env, cols, rows)

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: _BarrierBackend())

    sp = tmp_path / "terminals.json"
    entries = [
        {"id": f"term_w3_{i}", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path), "cols": 80, "rows": 24}
        for i in range(3)
    ]
    sp.write_text(json.dumps({"terminals": entries}), encoding="utf-8")

    m = TerminalManager(state_path=sp)
    m._pty_ok = True  # skip the first-spawn liveness verify; not what this pins
    snapshots = {"n": 0}
    real_snapshot = m.snapshot

    def _counting_snapshot():
        snapshots["n"] += 1
        return real_snapshot()

    m.snapshot = _counting_snapshot  # type: ignore[method-assign]

    restored = m.rehydrate()
    assert restored == 3, (
        f"only {restored}/3 panes restored: the restores did not run concurrently "
        "(the barrier needs all three spawns in flight at once)"
    )
    assert len(set(spawn_threads)) == 3, spawn_threads
    for e in entries:
        assert m.get(e["id"]) is not None
    # The session dict + persisted file are written ONCE after the join.
    assert snapshots["n"] == 1, snapshots
    assert term_mgr is not None


# ==========================================================================
# 2) fs-files-newest-first-is-false-and-polled-4s
# ==========================================================================


def _touch(p: Path, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def test_files_listing_is_truly_newest_first_past_the_limit(tmp_path):
    """700 old files in a_dir/ are found first in scandir order; the ONE newest
    file lives in z_dir/. 'First 600 then sort' never visits z_dir."""
    from iron_jarvis.fsbrowser import list_files_recursive

    old = time.time() - 10_000
    for i in range(700):
        _touch(tmp_path / "a_dir" / f"f{i:04d}.txt", old + i)
    _touch(tmp_path / "z_dir" / "new.txt", time.time())

    out = list_files_recursive(str(tmp_path), limit=600)
    assert len(out["files"]) == 600
    assert out["files"][0]["rel"] == "z_dir/new.txt", out["files"][0]
    mt = [f["mtime"] for f in out["files"]]
    assert mt == sorted(mt, reverse=True)
    # 701 files, 600 shown -> the RESULT is truncated (a separate fact from
    # whether the SCAN was cut short).
    assert out["truncated"] is True
    assert out["scan_truncated"] is False


def test_files_walk_reports_an_entry_budget_cut_as_scan_truncated(tmp_path, monkeypatch):
    from iron_jarvis.fsbrowser import browser, list_files_recursive

    for i in range(200):
        _touch(tmp_path / f"d{i // 50}" / f"f{i}.txt", time.time() - i)
    monkeypatch.setattr(browser, "WALK_ENTRY_BUDGET", 50)
    out = list_files_recursive(str(tmp_path), limit=600)
    assert out["scan_truncated"] is True, out.get("scan_truncated")
    assert len(out["files"]) <= 50
    assert isinstance(out.get("scanned"), int) and 0 < out["scanned"] <= 60


def test_files_walk_has_a_deadline_and_reports_it(tmp_path, monkeypatch):
    """An entry budget alone can run for seconds on OneDrive/AV-loaded trees
    (CLAUDE.md: _walk_files caps entries AND enforces a deadline)."""
    from iron_jarvis.fsbrowser import browser, list_files_recursive

    for i in range(1500):
        _touch(tmp_path / f"d{i // 100:02d}" / f"f{i}.txt", time.time() - i)
    monkeypatch.setattr(browser, "WALK_DEADLINE_S", 0.0)
    out = list_files_recursive(str(tmp_path), limit=600)
    assert out["scan_truncated"] is True
    assert out["scanned"] < 1500


def test_files_walk_small_tree_is_complete_control(tmp_path):
    from iron_jarvis.fsbrowser import list_files_recursive

    for i in range(5):
        _touch(tmp_path / f"f{i}.txt", time.time() - i)
    out = list_files_recursive(str(tmp_path), limit=600)
    assert out["truncated"] is False and out["scan_truncated"] is False
    assert [f["rel"] for f in out["files"]] == [f"f{i}.txt" for i in range(5)]


def test_fs_files_answers_a_weak_etag_and_a_bodiless_304_off_the_loop(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    tree = tmp_path / "tree"
    for i in range(10):
        _touch(tree / f"f{i}.txt", time.time() - 100 - i)

    # Every directory scan under the tree is recorded with whether it ran on
    # the event-loop thread (the walk must never; the route is sync today).
    scans: list[bool] = []
    real_scandir = os.scandir

    def _scandir(path=".", *a, **kw):
        if str(path).startswith(str(tree)):
            scans.append(_on_a_loop())
        return real_scandir(path, *a, **kw)

    monkeypatch.setattr(os, "scandir", _scandir)

    c = TestClient(create_app(str(tmp_path / "root")))
    url = "/fs/files"
    params = {"path": str(tree), "depth": 4, "limit": 600}
    first = c.get(url, params=params)
    assert first.status_code == 200
    tag = first.headers.get("etag")
    assert tag and tag.startswith('W/"'), dict(first.headers)

    second = c.get(url, params=params, headers={"If-None-Match": tag})
    assert second.status_code == 304
    assert second.content == b""

    # ANTI-VACUITY: a new file changes the answer (a short memo may delay it;
    # wait for the thing asserted, bounded).
    _touch(tree / "brand_new.txt", time.time())
    got: dict[str, Any] = {}

    def _changed() -> bool:
        r = c.get(url, params=params, headers={"If-None-Match": tag})
        got["r"] = r
        return r.status_code == 200

    assert _wait_for(_changed, timeout=10.0, step=0.25), got["r"].status_code
    assert got["r"].json()["files"][0]["rel"] == "brand_new.txt"
    assert got["r"].headers.get("etag") != tag
    assert scans and not any(scans), "the /fs/files walk ran on the event loop"


def _access(method: str, path: str, status: int) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5", method, path, "1.1", status), None,
    )


def test_access_log_is_quiet_for_the_files_poll_and_quiet_304s():
    from iron_jarvis.core.logging import QUIET_PATHS, PolledRouteAccessFilter

    assert "/fs/files" in QUIET_PATHS and "/sessions/interrupted" in QUIET_PATHS
    f = PolledRouteAccessFilter()
    # Quiet: polled reads, and a 304 on a QUIET path is a 200 that saved bytes.
    assert f.filter(_access("GET", "/fs/files?path=C%3A%5Cx&depth=4&limit=600", 200)) is False
    assert f.filter(_access("GET", "/sessions/interrupted", 200)) is False
    assert f.filter(_access("GET", "/sessions?limit=50", 304)) is False
    assert f.filter(_access("GET", "/fs/files?path=x", 304)) is False
    # Still logged (controls): failures, writes, 304s on NON-quiet paths.
    assert f.filter(_access("GET", "/fs/files?path=x", 500)) is True
    assert f.filter(_access("GET", "/fs/files?path=x", 403)) is True
    assert f.filter(_access("GET", "/projects", 304)) is True
    assert f.filter(_access("POST", "/sessions", 200)) is True


# ==========================================================================
# 3) cli-binary-probe-uncached-on-loop
# ==========================================================================


def _real_cli_binary_present():
    """The CURRENT source of ``ProviderManager._cli_binary_present``, compiled
    against the real module's globals. tests/conftest.py replaces the
    staticmethod with ``lambda b: False`` for the whole session (hermetic
    suite), so the real one is not reachable as an attribute — this rebuilds
    it from the file, whatever its body is now."""
    from iron_jarvis.providers import manager as mgr

    src = Path(mgr.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ProviderManager")
    fn = next(
        n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_cli_binary_present"
    )
    fn.decorator_list = []
    mod = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    code = compile(mod, mgr.__file__, "exec")
    fcode = next(c for c in code.co_consts if isinstance(c, types.CodeType))
    return types.FunctionType(fcode, mgr.__dict__, "_cli_binary_present")


@pytest.fixture
def find_counter(monkeypatch):
    """Count ``terminals.ai_clis._find`` (the PATH scan: shutil.which + per-dir
    is_file probes) and record the thread each ran on. Every binary is
    "installed" unless listed in ``absent``. The real CLI is never run: the
    sign-in probe is stubbed (conftest) and its warm/refresh are no-ops."""
    from iron_jarvis.providers import cli_auth
    from iron_jarvis.providers import manager as mgr
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.terminals import ai_clis

    state: dict[str, Any] = {"calls": [], "absent": set()}

    def _find(command: str):
        exe = command.strip().split()[0] if command.strip() else command
        state["calls"].append((exe, threading.get_ident()))
        return None if exe in state["absent"] else f"C:/fake/bin/{exe}.exe"

    monkeypatch.setattr(ai_clis, "_find", _find)
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(_real_cli_binary_present()))
    monkeypatch.setattr(cli_auth.DEFAULT_PROBE, "warm", lambda *a, **k: None)
    monkeypatch.setattr(cli_auth.DEFAULT_PROBE, "refresh", lambda *a, **k: None)
    inval = getattr(mgr, "invalidate_cli_presence", None)
    if inval is not None:
        inval()
    yield state
    if inval is not None:
        inval()


def test_health_in_steady_state_scans_path_zero_times(find_counter):
    """Measured on the user's install: 9 _find calls per health(), each a
    shutil.which + per-dir is_file probes. The second call must reuse them."""
    from iron_jarvis.providers.manager import ProviderManager

    pm = ProviderManager(inherit_cli_logins=True)
    pm.health()  # warm
    before = len(find_counter["calls"])
    assert before > 0, "anti-vacuity: the first health() must actually probe"
    pm.health()
    pm.available("claude-cli")
    pm.available("anthropic")  # keyless inheritance -> claude-cli
    extra = len(find_counter["calls"]) - before
    assert extra == 0, f"steady-state health()/available() re-scanned PATH {extra} times"


def test_presence_memo_is_invalidated_and_bounded(find_counter, monkeypatch):
    from iron_jarvis.providers import manager as mgr

    assert 0 < mgr.CLI_PRESENCE_TTL_S <= 60
    assert 0 <= mgr.CLI_ABSENT_TTL_S <= 5
    calls = find_counter["calls"]

    assert mgr.cli_binary_present("claude") is True
    assert mgr.cli_binary_present("claude") is True
    assert [c[0] for c in calls].count("claude") == 1
    mgr.invalidate_cli_presence()
    assert mgr.cli_binary_present("claude") is True
    assert [c[0] for c in calls].count("claude") == 2
    # The TTL is read at call time: 0 means "every answer is stale". Since the
    # review round an expired entry is answered from the memo and re-probed
    # on a background thread (stale-while-revalidate), so the third probe is
    # waited for — and it must NOT be on the calling thread.
    monkeypatch.setattr(mgr, "CLI_PRESENCE_TTL_S", 0)
    caller = threading.get_ident()
    assert mgr.cli_binary_present("claude") is True
    assert _wait_for(lambda: [c[0] for c in calls].count("claude") >= 3, timeout=5.0)
    assert calls[-1][1] != caller, "an expired entry re-probed on the calling thread"


def test_a_freshly_installed_cli_is_seen_without_a_restart(find_counter, monkeypatch):
    """ANTI-VACUITY: a cache that hides 'I just installed claude' is worse than
    no cache. Absent results expire fast; a rescan invalidates at once."""
    from iron_jarvis.providers import manager as mgr

    find_counter["absent"].add("codex")
    assert mgr.cli_binary_present("codex") is False
    find_counter["absent"].discard("codex")  # the user installs it
    mgr.invalidate_cli_presence()  # what a rescan does
    assert mgr.cli_binary_present("codex") is True

    find_counter["absent"].add("claude")
    mgr.invalidate_cli_presence()
    assert mgr.cli_binary_present("claude") is False
    find_counter["absent"].discard("claude")
    monkeypatch.setattr(mgr, "CLI_ABSENT_TTL_S", 0)  # the absent TTL lapses
    # Stale-while-revalidate: the call that notices the lapse starts ONE
    # background refresh; the install is seen once that refresh lands.
    mgr.cli_binary_present("claude")
    assert _wait_for(lambda: mgr.cli_binary_present("claude") is True, timeout=5.0)


def test_router_snapshot_after_a_memo_expiry_scans_path_off_the_loop(find_counter, monkeypatch):
    """Review round (BLOCKING): a plain TTL memo still scanned PATH ON THE
    LOOP once an entry expired — ``ModelRouter._snapshot()`` runs inline in
    ``complete()``/``stream()``. Reviewer's probe: 'after expiry, scans ON THE
    LOOP thread: 2'. Now an expired entry answers from the memo and ONE
    background refresh per binary re-probes it. ANTI-VACUITY: a CLI installed
    after the entry expired is reported True once that refresh lands, so the
    refresh is real work, not a no-op that keeps the stale answer forever."""
    import asyncio

    from iron_jarvis.core.events import EventBus
    from iron_jarvis.providers import manager as mgr
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.providers.router import ModelRouter

    calls = find_counter["calls"]
    find_counter["absent"].add("codex")  # codex "not installed" yet
    pm = ProviderManager(inherit_cli_logins=True)
    router = ModelRouter(pm, "claude-cli", EventBus())
    router._snapshot()  # fills the memo (first probes may run anywhere)
    assert {"claude", "codex"} <= {c[0] for c in calls}, "anti-vacuity: the snapshot must ask"
    assert "codex-cli" not in router._snapshot()

    find_counter["absent"].discard("codex")  # the user installs codex ...
    with mgr._CLI_PRESENCE_LOCK:  # ... and every memo entry ages past its TTL
        for k, (present, at) in list(mgr._CLI_PRESENCE.items()):
            mgr._CLI_PRESENCE[k] = (present, at - 3600.0)

    seen: dict[str, Any] = {}
    # HOLD the background refresh while the on-loop snapshot runs (review
    # v1.311.0: one snapshot asks about codex more than once — codex-cli and
    # the keyless openai inheritance — and an instant refresh could land
    # between those asks, so "served stale" raced; 6 of 9 runs went red).
    gate = threading.Event()
    real_probe = mgr._probe_cli_binary

    def _held(binary: str) -> bool:
        gate.wait(5.0)
        return real_probe(binary)

    monkeypatch.setattr(mgr, "_probe_cli_binary", _held)

    async def _on_loop() -> None:
        seen["loop"] = threading.get_ident()
        seen["n0"] = len(calls)
        seen["snap"] = router._snapshot()

    asyncio.run(_on_loop())
    gate.set()
    on_loop = [b for b, t in calls[seen["n0"]:] if t == seen["loop"]]
    assert not on_loop, f"expired memo entries scanned PATH on the loop thread: {on_loop}"
    # The stale answer was served (codex still absent in THIS snapshot) ...
    assert "codex-cli" not in seen["snap"]
    # ... and the background refresh ran on another thread and lands the install.
    assert _wait_for(lambda: mgr.cli_binary_present("codex") is True, timeout=5.0)
    refreshed = [t for b, t in calls[seen["n0"]:] if b == "codex"]
    assert refreshed and all(t != seen["loop"] for t in refreshed)
    assert "codex-cli" in router._snapshot()


def test_an_expired_entry_starts_at_most_one_refresh(find_counter, monkeypatch):
    """Single-flight: many callers that see the same expired entry start ONE
    background probe, not one each (the v1.251.0 warm-up lesson)."""
    from iron_jarvis.providers import manager as mgr

    gate = threading.Event()
    calls = find_counter["calls"]
    assert mgr.cli_binary_present("claude") is True  # fills the memo
    real = mgr._probe_cli_binary

    def _held(binary: str) -> bool:
        gate.wait(5.0)
        return real(binary)

    monkeypatch.setattr(mgr, "_probe_cli_binary", _held)
    monkeypatch.setattr(mgr, "CLI_PRESENCE_TTL_S", 0)
    for _ in range(25):
        assert mgr.cli_binary_present("claude") is True
    gate.set()
    assert _wait_for(lambda: [c[0] for c in calls].count("claude") >= 2, timeout=5.0)
    assert _wait_for(lambda: "claude" not in mgr._CLI_REFRESHING, timeout=5.0)
    assert [c[0] for c in calls].count("claude") == 2, "more than one refresh ran"


def test_boot_warm_up_probes_on_its_own_thread_and_fills_the_memo(find_counter):
    """warm_cli_logins runs inside the lifespan, i.e. ON THE LOOP. Its PATH
    scans must happen on a worker thread, and leave the memo warm so the first
    /health and the router's on-loop snapshot probe nothing."""
    from iron_jarvis.providers.cli_auth import CLI_BINARIES
    from iron_jarvis.providers.manager import ProviderManager

    pm = ProviderManager(inherit_cli_logins=True)
    caller = threading.get_ident()
    pm.warm_cli_logins()
    wanted = set(CLI_BINARIES.values())
    assert _wait_for(lambda: wanted <= {c[0] for c in find_counter["calls"]}, timeout=5.0)
    on_caller = [b for b, t in find_counter["calls"] if t == caller]
    assert not on_caller, f"warm_cli_logins scanned PATH on the calling (loop) thread: {on_caller}"
    n = len(find_counter["calls"])
    pm.available("claude-cli")
    pm.available("codex-cli")
    assert len(find_counter["calls"]) == n


# ==========================================================================
# 4) eventbus-serial-hops-and-webhook-select-per-event
# ==========================================================================


def test_publish_runs_every_sync_handler_in_one_executor_hop():
    from iron_jarvis.core.events import EventBus

    bus = EventBus()
    ran: list[tuple[str, int, bool]] = []
    active = {"n": 0, "max": 0}

    def _mk(name: str, boom: bool = False):
        def _h(ev):
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            try:
                ran.append((name, threading.get_ident(), _on_a_loop()))
                if boom:
                    raise RuntimeError("bad consumer")
            finally:
                active["n"] -= 1

        return _h

    for i in range(5):
        bus.add_handler(_mk(f"h{i}", boom=(i == 1)))

    async def _main() -> tuple[int, int]:
        loop = asyncio.get_running_loop()
        hops = {"n": 0}
        real = loop.run_in_executor

        def _counting(executor, func, *args):
            hops["n"] += 1
            return real(executor, func, *args)

        loop.run_in_executor = _counting  # type: ignore[method-assign]
        try:
            await bus.publish("w3.ping", {"a": 1})
            ran_at_return = len(ran)
        finally:
            del loop.run_in_executor
        return hops["n"], ran_at_return

    hops, ran_at_return = asyncio.run(_main())
    assert hops == 1, f"publish took {hops} executor hops for 5 handlers (want 1)"
    # Controls: every handler ran, in order, off the loop, one at a time, a
    # raising handler did not stop the rest, and publish returned after all.
    assert [r[0] for r in ran] == [f"h{i}" for i in range(5)]
    assert ran_at_return == 5
    assert not any(on_loop for _, _, on_loop in ran)
    assert active["max"] == 1


def _webhook_statements(engine) -> list[str]:
    seen: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):
        if "webhookrecord" in statement.lower():
            seen.append(statement)

    sa_event.listen(engine, "before_cursor_execute", _before)
    seen_remove = lambda: sa_event.remove(engine, "before_cursor_execute", _before)  # noqa: E731
    return seen, seen_remove  # type: ignore[return-value]


def test_no_outbound_webhook_means_zero_webhook_queries_per_event(tmp_path):
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    seen, remove = _webhook_statements(platform.engine)
    try:
        async def _burst():
            for i in range(100):
                await platform.event_bus.publish("w3.tick", {"i": i})

        asyncio.run(_burst())
    finally:
        remove()
    assert len(seen) == 0, f"{len(seen)} webhookrecord queries for 100 events with no webhook"


@pytest.fixture
def outbound(tmp_path):
    from iron_jarvis.core.db import open_db
    from iron_jarvis.webhooks.outbound import OutboundWebhooks

    engine = open_db(tmp_path / "w3.db")
    posts: list[tuple[str, dict]] = []
    ob = OutboundWebhooks(engine, lambda url, payload, headers: posts.append((url, payload)) or {"status": 200}, allow_internal=True)
    return ob, engine, posts


def test_a_non_matching_event_type_issues_no_webhook_query(outbound):
    from iron_jarvis.core.events import Event

    ob, engine, posts = outbound
    ob.register("hook1", "http://127.0.0.1:9/a", ["session.completed"])
    ob.on_event(Event(type="session.completed"))  # warm whatever the index is
    assert len(posts) == 1
    seen, remove = _webhook_statements(engine)
    try:
        for _ in range(20):
            ob.on_event(Event(type="tool.executed"))
    finally:
        remove()
    assert len(seen) == 0, f"{len(seen)} queries for 20 events no webhook subscribes to"
    assert len(posts) == 1


def test_every_orm_write_reaches_the_next_event(outbound):
    """CONTROL (green today, must stay green): the in-memory subscription
    index must see a row written by ANY ORM session (a route, an import,
    inbound.register flipping a slug) — not only OutboundWebhooks' own
    methods — on the very next event."""
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.events import Event
    from iron_jarvis.webhooks.models import WebhookRecord

    ob, engine, posts = outbound
    ob.on_event(Event(type="session.completed"))  # warm: nothing registered
    assert posts == []

    with session_scope(engine) as db:  # a direct ORM insert, bypassing ob
        db.add(
            WebhookRecord(
                slug="direct",
                direction="outbound",
                target_url="http://127.0.0.1:9/direct",
                event_types_json=json.dumps(["session.completed"]),
                secret_name="",
                enabled=True,
            )
        )
        db.commit()
    ob.on_event(Event(type="session.completed"))
    assert [u for u, _ in posts] == ["http://127.0.0.1:9/direct"]

    ob.register("direct", "http://127.0.0.1:9/moved", ["session.completed"])  # live update
    ob.on_event(Event(type="session.completed"))
    assert posts[-1][0] == "http://127.0.0.1:9/moved"

    with session_scope(engine) as db:  # a direct ORM disable
        row = db.exec(select(WebhookRecord).where(WebhookRecord.slug == "direct")).first()
        row.enabled = False
        db.add(row)
        db.commit()
    n = len(posts)
    ob.on_event(Event(type="session.completed"))
    assert len(posts) == n


def test_the_events_log_line_clips_long_payload_strings(tmp_path):
    """daemon.log carried multi-KB agent.completed/session.completed bodies
    (private memory-note text) on every event."""
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path))
    lg = logging.getLogger("ironjarvis.events")
    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    h = _H(level=logging.INFO)
    prev = lg.level
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    secret_tail = "TAIL-" + "z" * 40
    big = "y" * 5000 + secret_tail
    try:
        asyncio.run(platform.event_bus.publish("session.completed", {"summary": big, "ok": True}))
    finally:
        lg.removeHandler(h)
        lg.setLevel(prev)
    mine = [ln for ln in lines if ln.startswith("session.completed")]
    assert mine, "anti-vacuity: the events log handler did not log the event"
    assert secret_tail not in mine[0] and len(mine[0]) < 1000, len(mine[0])
    assert "'ok': True" in mine[0]  # short values are untouched


# ==========================================================================
# 5) undo-revert-db-writes-on-loop
# ==========================================================================


_UNDO_TABLES = re.compile(r"\b(toolinvocation|undojournal|session)\b", re.IGNORECASE)


def _seed_setting_action(engine, session_id: str, prior: dict) -> str:
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import new_id
    from iron_jarvis.core.models import PermissionMode, ToolInvocation, UndoJournal

    inv_id = new_id("tool")
    with session_scope(engine) as db:
        db.add(
            ToolInvocation(
                id=inv_id, session_id=session_id, agent_run_id="", tool="update_settings",
                args_json="{}", verdict=PermissionMode.ALLOW, ok=True, output="changed",
                reversibility="reversible",
            )
        )
        db.add(
            UndoJournal(
                action_id=inv_id, session_id=session_id, agent_run_id="", tool="update_settings",
                kind="setting_restore", reversible=True, pre_inline=json.dumps({"prior": prior}),
            )
        )
        db.commit()
    return inv_id


@pytest.fixture
def undo_app(tmp_path, monkeypatch):
    """The REAL app factory. Records every undo-table statement with whether
    it executed on an event-loop thread, and every config restore /
    provider re-point likewise."""
    import iron_jarvis.core.config as config_mod
    import iron_jarvis.daemon.routes.undo as undo_mod
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    platform = app.state.platform
    rec: dict[str, Any] = {"armed": False, "stmts": [], "restores": [], "repoints": []}

    def _before(conn, cursor, statement, params, context, executemany):
        if rec["armed"] and _UNDO_TABLES.search(statement):
            rec["stmts"].append((statement.split()[0].upper(), _on_a_loop()))

    sa_event.listen(platform.engine, "before_cursor_execute", _before)

    real_restore = config_mod.restore_config_values

    def _restore(cfg, prior):
        rec["restores"].append(_on_a_loop())
        return real_restore(cfg, prior)

    monkeypatch.setattr(config_mod, "restore_config_values", _restore)
    monkeypatch.setattr(undo_mod, "restore_config_values", _restore)
    real_conf = platform.providers.configure_local

    def _conf(*a, **kw):
        rec["repoints"].append(_on_a_loop())
        return real_conf(*a, **kw)

    monkeypatch.setattr(platform.providers, "configure_local", _conf)
    yield TestClient(app), platform, rec
    sa_event.remove(platform.engine, "before_cursor_execute", _before)


def test_undo_runs_its_db_transactions_and_config_restore_off_the_loop(undo_app):
    client, platform, rec = undo_app
    platform.config.max_agent_steps = 42
    action = _seed_setting_action(
        platform.engine, "settings", {"max_agent_steps": 7, "ollama_model": "llama-w3"}
    )
    rec["armed"] = True
    r = client.post(f"/undo/{action}")
    rec["armed"] = False
    assert r.status_code == 200, r.text
    assert platform.config.max_agent_steps == 7
    # ANTI-VACUITY: the request reached step 5 (the finalize WRITE), so the
    # lock-sensitive transaction is in the record.
    kinds = [k for k, _ in rec["stmts"]]
    assert "UPDATE" in kinds and "INSERT" in kinds, kinds
    on_loop = [k for k, loop in rec["stmts"] if loop]
    assert not on_loop, f"undo ran {len(on_loop)} DB statements on the event loop: {on_loop}"
    assert rec["restores"] == [False], rec["restores"]
    assert rec["repoints"] == [False], rec["repoints"]


def test_session_revert_runs_its_candidate_query_and_undos_off_the_loop(undo_app):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session

    client, platform, rec = undo_app
    sid = "session_w3_revert"
    with session_scope(platform.engine) as db:
        db.add(Session(id=sid, task="w3"))
        db.commit()
    _seed_setting_action(platform.engine, sid, {"max_agent_steps": 9})
    _seed_setting_action(platform.engine, sid, {"max_agent_steps": 8})
    rec["armed"] = True
    r = client.post(f"/sessions/{sid}/revert")
    rec["armed"] = False
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body.get("reverted") or []) == 2, body
    kinds = [k for k, _ in rec["stmts"]]
    assert kinds.count("UPDATE") >= 2, kinds  # two finalize writes happened
    on_loop = [k for k, loop in rec["stmts"] if loop]
    assert not on_loop, f"revert ran {len(on_loop)} DB statements on the event loop: {on_loop}"
    assert rec["restores"] and not any(rec["restores"])


def test_a_type_listed_twice_is_still_one_delivery(outbound):
    """Review (v1.311.0): the subscription index appended a record once per
    LISTED type, so a webhook whose event_types named a type twice got two
    POSTs per event; the pre-index code checked membership once per record."""
    from iron_jarvis.core.events import Event

    ob, _engine, posts = outbound
    ob.register("dup", "http://127.0.0.1:9/d", ["session.completed", "session.completed"])
    ob.on_event(Event(type="session.completed"))
    assert len(posts) == 1, posts
