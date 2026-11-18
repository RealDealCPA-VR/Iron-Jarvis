"""Fourth review, group 29 (MS2, MS3, D7): connected apps keep working by
themselves.

* MS2: a connected app's entry names Iron Jarvis's own small bridge program,
  copied to ``<home>/bridge/`` (outside the install folder an update must
  replace, and with a file name the installer and the sweep never kill). The
  copy is replaced only when the shipped content id changed, only after the
  new copy answered ``--check``, and on Windows while the old one runs (it is
  renamed aside and removed once free). With no checked copy the entry stays
  the install folder's ``mcp-stdio`` (the way back). ``status_of`` says
  ``entry_stale`` when an app's entry names another program.
* MS3: on a Linux AppImage the install folder is a temporary mount; the Codex
  entry still names ``<home>/bridge/``.
* D7: at start, each connected app's own entry is moved to the bridge (a
  backup kept), under the Connect lock; the row then asks for one restart.

The ``--check`` stand-in is Windows' own ``sort.exe`` (a self-contained
program that blocks on its input and refuses ``--check``), never a copied
venv python.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.core import procs
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import outside
from tests.test_running_elsewhere_v1530 import FakeClaude

INSTALL = "install-bearer-for-the-whole-app"
ACCEPT = "application/json, text/event-stream"
URL = "http://127.0.0.1:8787/mcp"
REPO = Path(__file__).resolve().parents[1]
SORT = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "sort.exe"
FINDSTR = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "findstr.exe"
windows_only = pytest.mark.skipif(sys.platform != "win32" or not SORT.is_file(),
                                  reason="Windows' own programs and its rename-while-running rule")


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    monkeypatch.setattr(apps, "TEST_ROOT", str(tmp_path / "other-apps"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.delenv(apps.BRIDGE_DIR_ENV, raising=False)
    monkeypatch.setattr(outside, "READS_DISK", False)
    monkeypatch.setattr(apps, "_sleep", lambda s: None)  # no real waits, ever
    app = create_app(str(tmp_path / "home"))

    class Scene:
        pass

    s = Scene()
    s.app, s.platform = app, app.state.platform
    s.client = TestClient(app, headers={"Authorization": f"Bearer {INSTALL}"})
    s.tmp, s.home = tmp_path, Path(app.state.platform.config.home)
    yield s


def _install(app: str) -> Path:
    path = apps.config_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ship(scene, monkeypatch, program: Path | None = None, *, content: bytes = b"bridge one",
          cid: str = "a" * 64) -> Path:
    """A shipped ``resources/bridge/`` folder, found through the env var."""
    shipped = scene.tmp / "resources" / "bridge"
    shipped.mkdir(parents=True, exist_ok=True)
    dest = shipped / apps.bridge_name()
    if program is not None:
        shutil.copyfile(program, dest)
    else:
        dest.write_bytes(content)
    (shipped / apps.BRIDGE_ID_FILE).write_text(cid + "\n", encoding="utf-8")
    monkeypatch.setenv(apps.BRIDGE_DIR_ENV, str(shipped))
    return shipped


def _answers(monkeypatch) -> list[Path]:
    """The stand-in program cannot print the ok line, so the check answers
    yes here; the check itself is pinned through a real program below."""
    asked: list[Path] = []

    def ok(path):
        asked.append(Path(path))
        return True

    monkeypatch.setattr(apps, "_bridge_answers", ok)
    return asked


def _door_initialize(scene, token: str):
    return TestClient(scene.app).post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": CLIENT_PROTOCOL_VERSION,
                                 "clientInfo": {"name": "pytest", "version": "1"}}},
        headers={"Accept": ACCEPT, "Authorization": f"Bearer {token}"})


def _status(scene, app: str) -> dict:
    return {a["app"]: a for a in scene.client.get("/work-record/other-tools").json()["apps"]}[app]


# --------------------------------------------------------------------------- #
# the program and its id
# --------------------------------------------------------------------------- #
def test_the_content_id_is_the_sha256_of_the_source_with_lf_line_endings():
    assert apps.bridge_content_id(b"one\r\ntwo\n") == hashlib.sha256(b"one\ntwo\n").hexdigest()
    assert apps.bridge_content_id(b"one\ntwo\n") == apps.bridge_content_id(b"one\r\ntwo\r\n")
    shim = (REPO / "src" / "iron_jarvis" / "mcpserver" / "stdio_shim.py").read_bytes()
    cid = apps.bridge_content_id(shim)
    assert len(cid) == 64 and cid == cid.lower() and int(cid, 16) >= 0


@pytest.mark.parametrize("suffix", [".exe", ""])
def test_the_bridge_is_never_named_like_the_daemon(monkeypatch, suffix):
    monkeypatch.setattr(apps, "_exe_suffix", lambda: suffix)
    assert apps.bridge_name() == "ironjarvis-bridge" + suffix
    assert apps.bridge_name().lower() not in {"ironjarvis.exe", "ironjarvis"}


def test_the_installer_and_the_sweep_kill_only_the_daemon_by_its_name():
    nsh = (REPO / "desktop" / "installer.nsh").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "taskkill /F /T /IM ironjarvis.exe" in nsh and "bridge" not in nsh
    main = (REPO / "desktop" / "main.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "Name='ironjarvis.exe'" in main
    assert "ironjarvis-bridge" not in main  # the sweep never names the bridge


def test_the_shipped_copy_is_found_by_env_then_beside_the_frozen_daemon(tmp_path, monkeypatch):
    monkeypatch.delenv(apps.BRIDGE_DIR_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert apps.shipped_bridge_dir() is None  # from source: today's entry
    daemon = tmp_path / "install" / "resources" / "daemon" / "ironjarvis.exe"
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(b"x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon))
    assert apps.shipped_bridge_dir() == (tmp_path / "install" / "resources" / "bridge").resolve()
    monkeypatch.setenv(apps.BRIDGE_DIR_ENV, str(tmp_path / "dev-bridge"))
    assert apps.shipped_bridge_dir() == tmp_path / "dev-bridge"


# --------------------------------------------------------------------------- #
# MS2: the entry is outside the install folder
# --------------------------------------------------------------------------- #
def test_a_frozen_connect_names_the_bridge_outside_the_install_folder(scene, monkeypatch):
    install = scene.tmp / "install"
    daemon = install / "resources" / "daemon" / "ironjarvis.exe"
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(b"the daemon")
    shipped = install / "resources" / "bridge"
    shipped.mkdir(parents=True)
    (shipped / apps.bridge_name()).write_bytes(b"the bridge program")
    (shipped / apps.BRIDGE_ID_FILE).write_text("b" * 64 + "\n", encoding="utf-8")
    asked = _answers(monkeypatch)
    path = _install("claude-desktop")
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon))
    r = scene.client.post("/work-record/other-apps/claude-desktop/connect")
    assert r.status_code == 200, r.text
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["iron-jarvis"]
    bridge = scene.home / "bridge" / apps.bridge_name()
    assert entry["command"] == str(bridge)
    assert not Path(entry["command"]).resolve().is_relative_to(install.resolve())
    assert entry["args"] == [URL]
    assert bridge.read_bytes() == b"the bridge program"
    assert (scene.home / "bridge" / apps.BRIDGE_ID_FILE).read_text(encoding="utf-8").strip() == "b" * 64
    assert asked and asked[0].name.endswith(".new")  # checked BEFORE the swap
    assert r.json()["entry_stale"] is False


def test_with_no_checked_bridge_the_entry_falls_back_to_the_daemons_own_command(scene, monkeypatch):
    daemon = scene.tmp / "install" / "resources" / "daemon" / "ironjarvis.exe"
    daemon.parent.mkdir(parents=True)
    daemon.write_bytes(b"the daemon")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon))
    assert apps.launch_argv(URL, scene.home) == (str(daemon), ["mcp-stdio", URL])


# --------------------------------------------------------------------------- #
# MS3: the AppImage mount
# --------------------------------------------------------------------------- #
def test_an_appimage_mount_is_never_the_codex_entry(scene, monkeypatch):
    monkeypatch.setattr(apps, "_exe_suffix", lambda: "")  # Linux names
    _ship(scene, monkeypatch, content=b"#!linux bridge")
    _answers(monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_x/resources/daemon/ironjarvis")
    path = _install("codex")
    path.write_text('model = "m"\n', encoding="utf-8")
    r = scene.client.post("/work-record/other-apps/codex/connect")
    assert r.status_code == 200, r.text
    entry = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["iron-jarvis"]
    assert ".mount_" not in entry["command"]
    assert Path(entry["command"]) == scene.home / "bridge" / "ironjarvis-bridge"
    assert entry["args"] == [URL]


def test_an_entry_naming_a_program_that_is_gone_is_stale(scene, monkeypatch):
    path = _install("claude-desktop")
    path.write_text("{}", encoding="utf-8")
    assert scene.client.post("/work-record/other-apps/claude-desktop/connect").status_code == 200
    assert _status(scene, "claude-desktop")["entry_stale"] is False
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mcpServers"]["iron-jarvis"]["command"] = "/tmp/.mount_old/resources/daemon/ironjarvis"
    path.write_text(json.dumps(data), encoding="utf-8")
    row = _status(scene, "claude-desktop")
    assert row["connected"] is True and row["entry_stale"] is True


# --------------------------------------------------------------------------- #
# MS2: the copy, the check, the swap
# --------------------------------------------------------------------------- #
def test_the_same_content_id_is_left_alone(scene, monkeypatch):
    shipped = _ship(scene, monkeypatch, content=b"first build")
    _answers(monkeypatch)
    first = apps.ensure_bridge(scene.home)
    assert first.installed and first.changed
    # A new release re-signs the program (new bytes) with the same content.
    (shipped / apps.bridge_name()).write_bytes(b"first build, signed again")
    again = apps.ensure_bridge(scene.home)
    assert again.installed and not again.changed
    assert (scene.home / "bridge" / apps.bridge_name()).read_bytes() == b"first build"
    (shipped / apps.BRIDGE_ID_FILE).write_text("c" * 64, encoding="utf-8")
    third = apps.ensure_bridge(scene.home)
    assert third.changed and third.content_id == "c" * 64
    assert (scene.home / "bridge" / apps.bridge_name()).read_bytes() == b"first build, signed again"


def test_the_check_wants_exit_0_and_the_one_ok_line(tmp_path, monkeypatch):
    seen = []

    def fake_run(argv, *, timeout, **kw):
        seen.append((list(argv), timeout))
        return procs.Result(fake_run.code, fake_run.out, b"")

    monkeypatch.setattr(procs, "run", fake_run)
    prog = tmp_path / "ironjarvis-bridge.exe.new"
    fake_run.code, fake_run.out = 0, b"ironjarvis-bridge ok\r\n"
    assert apps._bridge_answers(prog) is True
    assert seen[-1][0] == [str(prog), "--check"] and 0 < seen[-1][1] <= 20
    fake_run.code, fake_run.out = 0, b"something else\n"
    assert apps._bridge_answers(prog) is False
    fake_run.code, fake_run.out = 1, b"ironjarvis-bridge ok\n"
    assert apps._bridge_answers(prog) is False


@windows_only
def test_a_new_copy_that_fails_its_check_is_never_swapped_in(scene, monkeypatch):
    """Through the real process helper: ``sort.exe --check`` exits 1."""
    shipped = _ship(scene, monkeypatch, SORT, cid="d" * 64)
    got = apps.ensure_bridge(scene.home)
    assert not got.installed and not got.changed
    bridge_dir = scene.home / "bridge"
    assert not (bridge_dir / apps.bridge_name()).exists()
    assert not list(bridge_dir.glob("*.new"))
    assert apps.launch_argv(URL, scene.home)[0] == sys.executable  # the way back
    # A checked copy already there stays when a newer one fails its check.
    old = b"a checked older copy"
    (bridge_dir / apps.bridge_name()).write_bytes(old)
    (bridge_dir / apps.BRIDGE_ID_FILE).write_text("e" * 64 + "\n", encoding="utf-8")
    got = apps.ensure_bridge(scene.home)
    assert got.installed and not got.changed and got.content_id == "e" * 64
    assert (bridge_dir / apps.bridge_name()).read_bytes() == old
    assert apps.launch_argv(URL, scene.home) == (str(bridge_dir / apps.bridge_name()), [URL])
    assert shipped.is_dir()


@windows_only
def test_a_running_copy_is_replaced_while_it_runs_and_removed_once_free(scene, monkeypatch):
    bridge_dir = scene.home / "bridge"
    bridge_dir.mkdir(parents=True)
    running = bridge_dir / apps.bridge_name()
    shutil.copyfile(SORT, running)
    (bridge_dir / apps.BRIDGE_ID_FILE).write_text("1" * 64 + "\n", encoding="utf-8")
    _ship(scene, monkeypatch, FINDSTR if FINDSTR.is_file() else SORT, cid="2" * 64)
    _answers(monkeypatch)
    proc = procs.spawn([str(running)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    try:
        got = apps.ensure_bridge(scene.home)
        assert got.changed and got.installed and got.content_id == "2" * 64
        assert running.read_bytes() == (FINDSTR if FINDSTR.is_file() else SORT).read_bytes()
        assert proc.poll() is None  # the app's bridge kept running
        aside = list(bridge_dir.glob("ironjarvis-bridge.old-*"))
        assert len(aside) == 1
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
        procs.release(proc)
    apps.ensure_bridge(scene.home)  # a later start sweeps what is free now
    assert list(bridge_dir.glob("ironjarvis-bridge.old-*")) == []


def test_a_swap_held_by_a_scanner_is_tried_again(scene, monkeypatch):
    _ship(scene, monkeypatch, content=b"new build")
    _answers(monkeypatch)
    real = os.replace
    held = {"n": 2}

    def busy(src, dst):
        if str(src).endswith(".new") and held["n"]:
            held["n"] -= 1
            raise PermissionError(13, "The file is being scanned")
        return real(src, dst)

    naps = []
    monkeypatch.setattr(apps.os, "replace", busy)
    monkeypatch.setattr(apps, "_sleep", naps.append)
    got = apps.ensure_bridge(scene.home)
    assert got.changed and got.installed and len(naps) == 2


# --------------------------------------------------------------------------- #
# D7: apps connected before the bridge move over at start
# --------------------------------------------------------------------------- #
@pytest.fixture()
def cc(scene, monkeypatch):
    monkeypatch.setattr(apps, "claude_cli", lambda: ["claude"])
    apps.claude_code_config_path().parent.mkdir(parents=True, exist_ok=True)
    fake = FakeClaude(apps.claude_code_config_path())
    monkeypatch.setattr(apps, "run_cli", fake)
    apps._cc_cache.clear()
    return fake


def _connect_three(scene) -> dict[str, str]:
    _install("claude-desktop").write_text('{"preferences": {"a": 1}}', encoding="utf-8")
    _install("codex").write_text('model = "m"\n', encoding="utf-8")
    for app in ("claude-desktop", "codex", "claude-code"):
        r = scene.client.post(f"/work-record/other-apps/{app}/connect")
        assert r.status_code == 200, r.text
    return {app: scene.platform.app_connections.key_path(app).read_text(encoding="utf-8")
            for app in ("claude-desktop", "codex", "claude-code")}


def _commands(scene) -> dict[str, tuple[str, list[str]]]:
    claude = json.loads(apps.claude_config_path().read_text(encoding="utf-8"))["mcpServers"]["iron-jarvis"]
    codex = tomllib.loads(apps.codex_config_path().read_text(encoding="utf-8"))["mcp_servers"]["iron-jarvis"]
    code = json.loads(apps.claude_code_config_path().read_text(encoding="utf-8"))["mcpServers"]["iron-jarvis"]
    return {"claude-desktop": (claude["command"], claude["args"]), "codex": (codex["command"], codex["args"]),
            "claude-code": (code["command"], code["args"])}


def test_apps_connected_before_the_bridge_move_to_it_at_start_and_ask_one_restart(scene, monkeypatch, cc):
    keys = _connect_three(scene)
    for app, token in keys.items():
        assert _door_initialize(scene, token).status_code == 200  # each app in use
        assert _status(scene, app)["waiting_for_app"] is False
    assert all(cmd == sys.executable for cmd, _a in _commands(scene).values())
    _ship(scene, monkeypatch, content=b"the bridge")
    _answers(monkeypatch)
    assert apps.ensure_bridge(scene.home).installed
    assert all(_status(scene, app)["entry_stale"] for app in keys)
    moved = apps.migrate_entries(scene.home, URL)
    assert sorted(moved) == sorted(keys)
    bridge = str(scene.home / "bridge" / apps.bridge_name())
    assert _commands(scene) == {app: (bridge, [URL]) for app in keys}
    for app, token in keys.items():
        row = _status(scene, app)
        assert row["connected"] is True and row["entry_stale"] is False
        assert row["waiting_for_app"] is True  # the row asks for one restart
        assert scene.platform.app_connections.resolve(token) is not None  # same key
    assert json.loads(apps.claude_config_path().read_text(encoding="utf-8"))["preferences"] == {"a": 1}
    assert len(list((scene.home / "backups" / "other-apps").iterdir())) >= 3
    assert apps.migrate_entries(scene.home, URL) == []  # nothing left to move


def test_no_move_without_a_checked_bridge(scene, monkeypatch, cc):
    _connect_three(scene)
    before = _commands(scene)
    assert apps.migrate_entries(scene.home, URL) == []
    assert _commands(scene) == before


def test_a_claude_code_entry_lost_in_the_move_says_connect_again(scene, monkeypatch, cc):
    _connect_three(scene)
    _ship(scene, monkeypatch, content=b"the bridge")
    _answers(monkeypatch)
    apps.ensure_bridge(scene.home)
    real = cc.__call__

    def add_fails(argv):
        if argv[1:3] == ["mcp", "add"]:
            cc.calls.append(list(argv))
            return 1, "Error: could not write the settings"
        return real(argv)

    monkeypatch.setattr(apps, "run_cli", add_fails)
    moved = apps.migrate_entries(scene.home, URL)
    assert "claude-code" not in moved and sorted(moved) == ["claude-desktop", "codex"]
    row = _status(scene, "claude-code")
    assert row["connected"] is False and row["entry_missing"] is True  # "Connect again"


def test_the_move_waits_for_a_connect_press_in_progress(scene, monkeypatch, cc):
    _connect_three(scene)
    _ship(scene, monkeypatch, content=b"the bridge")
    _answers(monkeypatch)
    apps.ensure_bridge(scene.home)
    lock = scene.platform.app_connections._change_lock
    done = []
    with lock:
        t = threading.Thread(target=lambda: done.append(apps.migrate_entries(scene.home, URL)))
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive() and done == []  # held by the press
    t.join(timeout=10)
    assert not t.is_alive() and len(done[0]) == 3
