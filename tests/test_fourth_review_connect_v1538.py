"""Fourth review, group 29 (MS5, MS7, OT4, OT6, OT10, C-OT-1, C-OT-2): what
Connect and Disconnect do to another app's settings, and what the row says.

* MS5: one after-Connect sentence per app (Claude Code is not told about
  Codex).
* MS7: a Connect press that waits on Claude Code's own command never holds up
  a connected app's calls (two locks: one for the press, one for the files).
* OT4: a Disconnect that cut the key but could not change the other app's file
  says what DID change, keeps the reason, and the row says ``entry_left``.
* OT6: Disconnect and Connect again remove Iron Jarvis's own Codex tables by
  table; anything else written inside the marked block stays, or nothing is
  written and the press says where the copy is.
* OT10: the newest 3 copies of another app's settings file are kept.
* C-OT-1: a busy settings file is tried 3 times before the press fails.
* C-OT-2: the row says whether the app has used Iron Jarvis since Connect.
"""

from __future__ import annotations

import json
import threading
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import outside
from tests.test_running_elsewhere_v1530 import FakeClaude

INSTALL = "install-bearer-for-the-whole-app"
ACCEPT = "application/json, text/event-stream"
CODEX_CONFIG = """# my Codex settings
model = "gpt-5.3-codex"

[mcp_servers.node_repl]
command = "node"
args = ["repl.js"]
"""


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


@pytest.fixture()
def cc(scene, monkeypatch):
    monkeypatch.setattr(apps, "claude_cli", lambda: ["claude"])
    apps.claude_code_config_path().parent.mkdir(parents=True, exist_ok=True)
    fake = FakeClaude(apps.claude_code_config_path())
    monkeypatch.setattr(apps, "run_cli", fake)
    apps._cc_cache.clear()
    return fake


def _install(app: str) -> Path:
    path = apps.config_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect(scene, app: str):
    return scene.client.post(f"/work-record/other-apps/{app}/connect")


def _disconnect(scene, app: str):
    return scene.client.post(f"/work-record/other-apps/{app}/disconnect")


def _key(scene, app: str) -> str:
    return scene.platform.app_connections.key_path(app).read_text(encoding="utf-8")


def _status(scene, app: str) -> dict:
    return {a["app"]: a for a in scene.client.get("/work-record/other-tools").json()["apps"]}[app]


def _initialize(scene, token: str):
    return TestClient(scene.app).post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": CLIENT_PROTOCOL_VERSION,
                                 "clientInfo": {"name": "pytest", "version": "1"}}},
        headers={"Accept": ACCEPT, "Authorization": f"Bearer {token}"})


def _lock_on(monkeypatch, path: Path, times: int = 99) -> dict:
    """A stand-in for the OS holding ONE file (the other app writing it): the
    write of that path fails ``times`` times; every other write is real."""
    real = apps._atomic_write
    left = {"n": times}

    def write(p, text):
        if Path(p) == path and left["n"] > 0:
            left["n"] -= 1
            raise PermissionError(13, "the file is in use")
        return real(p, text)

    monkeypatch.setattr(apps, "_atomic_write", write)
    return left


# --------------------------------------------------------------------------- #
# MS5: one sentence per app
# --------------------------------------------------------------------------- #
def test_each_app_has_its_own_after_connect_sentence():
    assert set(apps.AFTER_CONNECT) == set(apps.APPS)
    words = apps.AFTER_CONNECT[apps.APP_CLAUDE_CODE]
    assert words == "New Claude Code sessions use it; one that is open now needs a restart."
    assert "Codex" not in words
    assert apps.AFTER_CONNECT[apps.APP_CLAUDE] == "Restart Claude desktop to start using it."
    assert apps.AFTER_CONNECT[apps.APP_CODEX] == "New Codex chats use it; restart Codex if it is open."
    assert all(label in apps.UNKNOWN_APP for label in apps.LABELS.values())


# --------------------------------------------------------------------------- #
# MS7: a slow Connect never holds up the calls
# --------------------------------------------------------------------------- #
def test_a_slow_claude_code_connect_never_holds_up_a_connected_apps_calls(scene, monkeypatch, cc):
    _install("codex").write_text(CODEX_CONFIG, encoding="utf-8")
    assert _connect(scene, "codex").status_code == 200
    codex_key = _key(scene, "codex")
    entered, release = threading.Event(), threading.Event()
    real = cc.__call__

    def slow(argv):
        entered.set()
        release.wait(timeout=20)
        return real(argv)

    monkeypatch.setattr(apps, "run_cli", slow)
    conns = scene.platform.app_connections
    a = threading.Thread(target=lambda: conns.connect("claude-code", "http://127.0.0.1:8787/mcp"))
    a.start()
    try:
        assert entered.wait(timeout=10)
        got = {}
        b = threading.Thread(target=lambda: got.update(grant=conns.resolve(codex_key), apps=conns.connected()))
        b.start()
        b.join(timeout=5)
        assert not b.is_alive(), "a call waited on the Connect press"
        assert got["grant"] is not None and got["grant"].pane_id == "app:codex"
        assert got["apps"] == {"codex"}
    finally:
        release.set()
        a.join(timeout=20)
    assert not a.is_alive()
    assert conns.connected() == {"codex", "claude-code"}


# --------------------------------------------------------------------------- #
# OT4: a Disconnect that cannot change the file says what did change
# --------------------------------------------------------------------------- #
def test_a_disconnect_that_cannot_change_the_settings_says_what_changed_and_can_be_finished(scene, monkeypatch):
    path = _install("claude-desktop")
    before = {"mcpServers": {"other": {"command": "x"}}, "preferences": {"a": 1}}
    path.write_text(json.dumps(before), encoding="utf-8")
    assert _connect(scene, "claude-desktop").status_code == 200
    token = _key(scene, "claude-desktop")
    held = _lock_on(monkeypatch, path)
    r = _disconnect(scene, "claude-desktop")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Claude desktop can no longer reach Iron Jarvis, but its settings still list it "
        "(the file is in use). Close Claude desktop, then press Remove.")
    assert "nothing was changed" not in r.json()["detail"]
    assert _initialize(scene, token).status_code == 401  # the key is refused
    row = _status(scene, "claude-desktop")
    assert row["entry_left"] is True and row["connected"] is False
    held["n"] = 0  # the file is free again; Remove is the same press
    r = _disconnect(scene, "claude-desktop")
    assert r.status_code == 200, r.text
    assert json.loads(path.read_text(encoding="utf-8")) == before
    assert r.json()["entry_left"] is False


def test_a_codex_disconnect_keeps_the_reason_it_was_given(scene, monkeypatch):
    path = _install("codex")
    path.write_text(CODEX_CONFIG, encoding="utf-8")
    assert _connect(scene, "codex").status_code == 200
    _lock_on(monkeypatch, path)
    r = _disconnect(scene, "codex")
    assert r.status_code == 409
    assert r.json()["detail"].startswith("Codex can no longer reach Iron Jarvis, but its settings still list it (")
    assert r.json()["detail"].endswith("Close Codex, then press Remove.")
    err = apps.AppConnectError("x", why="the file is in use")
    assert err.why == "the file is in use" and str(err) == "x"


def test_a_claude_code_disconnect_it_refuses_says_press_remove(scene, monkeypatch, cc):
    assert _connect(scene, "claude-code").status_code == 200
    token = _key(scene, "claude-code")
    monkeypatch.setattr(apps, "run_cli", lambda argv: (1, "Working...\nError: the settings are busy"))
    r = _disconnect(scene, "claude-code")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Claude Code can no longer reach Iron Jarvis, but Claude Code did not take it off its list "
        "(Error: the settings are busy). Press Remove to try again.")
    assert _initialize(scene, token).status_code == 401
    assert _status(scene, "claude-code")["entry_left"] is True
    monkeypatch.setattr(apps, "run_cli", cc)
    assert _disconnect(scene, "claude-code").status_code == 200
    assert _status(scene, "claude-code")["entry_left"] is False


def test_a_claude_code_connect_again_that_removed_but_did_not_add_says_so(scene, monkeypatch, cc):
    assert _connect(scene, "claude-code").status_code == 200
    real = cc.__call__

    def add_fails(argv):
        if argv[1:3] == ["mcp", "add"]:
            return 1, "Error: could not write the settings"
        return real(argv)

    monkeypatch.setattr(apps, "run_cli", add_fails)
    r = _connect(scene, "claude-code")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Claude Code took Iron Jarvis off its list but did not add it back "
        "(Error: could not write the settings). Press Connect Claude Code again.")
    assert _status(scene, "claude-code")["entry_missing"] is True


# --------------------------------------------------------------------------- #
# OT6: Codex's own tables inside the marked block stay
# --------------------------------------------------------------------------- #
TRUST = "[projects.'C:\\work']\ntrust_level = \"trusted\"\n"


def _insert_before_end(path: Path, text: str) -> None:
    body = path.read_text(encoding="utf-8")
    assert body.count(apps.CODEX_END) == 1
    path.write_text(body.replace(apps.CODEX_END, text + apps.CODEX_END), encoding="utf-8")


def test_disconnect_keeps_codex_settings_written_inside_the_marked_block(scene):
    path = _install("codex")
    path.write_text(CODEX_CONFIG, encoding="utf-8")
    assert _connect(scene, "codex").status_code == 200
    _insert_before_end(path, TRUST)  # what Codex's own editor does
    assert _disconnect(scene, "codex").status_code == 200
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["projects"]["C:\\work"]["trust_level"] == "trusted"
    assert "iron-jarvis" not in data.get("mcp_servers", {})
    assert data["mcp_servers"]["node_repl"] == {"command": "node", "args": ["repl.js"]}
    assert apps.CODEX_BEGIN not in path.read_text(encoding="utf-8")


def test_connect_again_keeps_codex_settings_written_inside_the_marked_block(scene):
    path = _install("codex")
    path.write_text(CODEX_CONFIG, encoding="utf-8")
    assert _connect(scene, "codex").status_code == 200
    _insert_before_end(path, TRUST)
    assert _connect(scene, "codex").status_code == 200
    text = path.read_text(encoding="utf-8")
    assert text.count(apps.CODEX_BEGIN) == 1
    data = tomllib.loads(text)
    assert data["projects"]["C:\\work"]["trust_level"] == "trusted"
    assert data["mcp_servers"]["iron-jarvis"]["env"] == {
        "IRONJARVIS_MCP_TOKEN_FILE": str(scene.platform.app_connections.key_path("codex"))}


def test_a_codex_file_that_cannot_be_safely_undone_is_left_as_it_is(scene):
    path = _install("codex")
    path.write_text(CODEX_CONFIG, encoding="utf-8")
    assert _connect(scene, "codex").status_code == 200
    # A key written after the end marker belongs to OUR last table today;
    # without it, it would land in the user's own table instead.
    path.write_text(path.read_text(encoding="utf-8") + "startup_timeout_sec = 30\n", encoding="utf-8")
    held = path.read_bytes()
    r = _disconnect(scene, "codex")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail.startswith("Codex can no longer reach Iron Jarvis")
    assert "cannot safely undo" in detail and "backups folder" in detail
    assert path.read_bytes() == held
    r = _connect(scene, "codex")
    assert r.status_code == 409
    assert r.json()["detail"] == (
        "Codex's settings changed in a way Iron Jarvis cannot safely undo, so its file was left as it is. "
        "Remove \"iron-jarvis\" in Codex's settings yourself; a copy of the file is in Iron Jarvis's "
        "backups folder.")
    assert path.read_bytes() == held
    copies = list((scene.home / "backups" / "other-apps").glob("*config.toml"))
    assert any(c.read_bytes() == held for c in copies)


# --------------------------------------------------------------------------- #
# OT10: the newest 3 copies
# --------------------------------------------------------------------------- #
def test_only_the_newest_three_copies_of_another_apps_file_are_kept(scene, monkeypatch):
    t0 = datetime(2026, 11, 18, 9, 0, 0, tzinfo=timezone.utc)
    tick = {"n": 0}

    def clock():
        tick["n"] += 1
        return t0 + timedelta(seconds=tick["n"])

    monkeypatch.setattr(apps, "_utcnow", clock)
    _install("claude-desktop").write_text("{}", encoding="utf-8")
    for _ in range(5):
        assert _connect(scene, "claude-desktop").status_code == 200
        assert _disconnect(scene, "claude-desktop").status_code == 200
    copies = sorted(p.name for p in (scene.home / "backups" / "other-apps").glob("Claude-*-claude_desktop_config.json"))
    assert len(copies) == 3
    assert copies == [f"Claude-{(t0 + timedelta(seconds=n)).strftime('%Y%m%d-%H%M%S')}-claude_desktop_config.json"
                      for n in (8, 9, 10)]


# --------------------------------------------------------------------------- #
# C-OT-1: a busy file is tried again
# --------------------------------------------------------------------------- #
def test_a_settings_file_busy_for_a_moment_is_tried_again(scene, monkeypatch):
    path = _install("claude-desktop")
    path.write_text("{}", encoding="utf-8")
    assert _connect(scene, "claude-desktop").status_code == 200
    naps: list[float] = []
    monkeypatch.setattr(apps, "_sleep", naps.append)
    left = _lock_on(monkeypatch, path, times=2)
    r = _disconnect(scene, "claude-desktop")
    assert r.status_code == 200, r.text
    assert left["n"] == 0 and len(naps) == 2 and 2.0 <= sum(naps) <= 4.0
    assert "iron-jarvis" not in json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})


# --------------------------------------------------------------------------- #
# C-OT-2: has the app used it yet?
# --------------------------------------------------------------------------- #
def test_the_row_says_whether_the_app_has_used_iron_jarvis_since_connect(scene):
    _install("claude-desktop").write_text("{}", encoding="utf-8")
    _install("codex").write_text(CODEX_CONFIG, encoding="utf-8")
    assert _connect(scene, "claude-desktop").json()["waiting_for_app"] is True
    assert _status(scene, "claude-desktop")["waiting_for_app"] is True
    assert _initialize(scene, _key(scene, "claude-desktop")).status_code == 200
    assert _status(scene, "claude-desktop")["waiting_for_app"] is False
    state_path = scene.home / "secrets" / "app-connections.json"
    first = json.loads(state_path.read_text(encoding="utf-8"))["claude-desktop"]["first_used_at"]
    assert _initialize(scene, _key(scene, "claude-desktop")).status_code == 200
    assert json.loads(state_path.read_text(encoding="utf-8"))["claude-desktop"]["first_used_at"] == first  # once
    # Another app's Connect keeps it (the state is read back whole).
    assert _connect(scene, "codex").status_code == 200
    assert _status(scene, "claude-desktop")["waiting_for_app"] is False
    assert _status(scene, "codex")["waiting_for_app"] is True
    # A second Connect is a new key: waiting again until the app uses it.
    assert _connect(scene, "claude-desktop").status_code == 200
    assert _status(scene, "claude-desktop")["waiting_for_app"] is True
    # Not connected: nothing to wait for.
    assert _status(scene, "claude-code")["waiting_for_app"] is False
