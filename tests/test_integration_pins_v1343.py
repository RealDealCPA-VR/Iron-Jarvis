"""v1.343.0 integration pins — the seams BETWEEN the five lanes ("add a pack
in one paste"), each proven on the real app.

* Every key ``GET /mcp/import/sources`` answers in ``source`` is a key
  ``POST /mcp/import/apply {source}`` reads (``read_source``), the keys are
  unique (the dashboard keys its groups on them) and ``AddPack.tsx`` posts
  the row's own key back.
* The dashboard's ``safeName`` twin (``dashboard/lib/mcpImport.ts``) agrees
  with the server's ``mcp.import_config.safe_name`` on a table of names —
  run under node when node is present, so the name the user READS before
  the press is the name the row carries.
* The NO-VALUE rule across every v1.343.0 door at once: distinct sentinel
  values planted through the paste preview, the paste apply, the sources
  scan, the sources apply, ``POST /mcp/servers`` (http) and the partial
  ``PATCH`` appear in NO answer, not in ``config.toml`` and not in the log.
* The contract's answer shapes: the preview row, the sources row, the apply
  answer, the GET row's ``transport`` / ``header_names`` / ``auth_set`` on
  a stdio row, and the PATCH answer = the GET row plus ``reloaded`` /
  ``note``.
* The dashboard editor's exact PATCH bodies (``editPatch``'s shapes —
  ``{clear_secrets}``, ``{clear_headers, auth: null}``, a bare
  ``{auto_approve}``) are accepted and do what the editor says.

Every test runs under a TEMP home (USERPROFILE/HOME/APPDATA swapped,
CLAUDE_CONFIG_DIR/CODEX_HOME cleared); the real config files are read as
bytes at import and proven byte-identical by a module teardown.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import import_sources
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.import_config import safe_name

REPO = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# The REAL files, read at import time, before any fixture moves HOME.
# --------------------------------------------------------------------------- #
from tests.fixtures.real_config_view import real_view  # noqa: E402

_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_CLAUDE_DIR = Path(os.environ["CLAUDE_CONFIG_DIR"]) if os.environ.get("CLAUDE_CONFIG_DIR") else None
_REAL_CODEX_HOME = Path(os.environ.get("CODEX_HOME") or _REAL_HOME / ".codex")
_REAL_APPDATA = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None
_REAL_FILES: list[Path] = [
    (_REAL_CLAUDE_DIR / ".claude.json") if _REAL_CLAUDE_DIR else (_REAL_HOME / ".claude.json"),
    (_REAL_CLAUDE_DIR / "settings.json") if _REAL_CLAUDE_DIR else (_REAL_HOME / ".claude" / "settings.json"),
    _REAL_CODEX_HOME / "config.toml",
    _REAL_HOME / ".cursor" / "mcp.json",
]
if _REAL_APPDATA is not None:
    _REAL_FILES.append(_REAL_APPDATA / "Claude" / "claude_desktop_config.json")
    _REAL_FILES.append(_REAL_APPDATA / "Code" / "User" / "settings.json")


def _bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except OSError:
        return None


def _view(p: Path) -> bytes | str | None:
    """What this guard compares: one shared view (tests/fixtures/real_config_view)
    so every real-files guard treats the app-rewritten files alike."""
    return real_view(p)


_REAL_BEFORE: dict[Path, bytes | str | None] = {p: _view(p) for p in _REAL_FILES}


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    yield
    after = {p: _view(p) for p in _REAL_FILES}
    changed = [str(p) for p in _REAL_FILES if after[p] != _REAL_BEFORE[p]]
    assert not changed, f"a test touched the user's real config files: {changed}"


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
NO_SUCH = "ij-no-such-mcp-launcher-v1343int"
PASTE_TOKEN = "ghp_SECRET_paste_v1343int"
PASTE_HEADER = "hdr-SECRET-paste-v1343int"
FILE_TOKEN = "ghp_SECRET_file_v1343int"
HTTP_HEADER = "hdr-SECRET-http-v1343int"
HTTP_AUTH = "bearer-SECRET-http-v1343int"
PATCH_TOKEN = "ghp_SECRET_patch_v1343int"
PATCH_AUTH = "bearer-SECRET-patch-v1343int"
PATCH_HEADER = "hdr-SECRET-patch-v1343int"
SENTINELS = (PASTE_TOKEN, PASTE_HEADER, FILE_TOKEN, HTTP_HEADER, HTTP_AUTH, PATCH_TOKEN, PATCH_AUTH, PATCH_HEADER)


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _fresh_records():
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()


@pytest.fixture
def homes(tmp_path, monkeypatch) -> dict[str, Path]:
    user = tmp_path / "user"
    appdata = tmp_path / "appdata"
    user.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return {"user": user, "appdata": appdata}


@pytest.fixture
def client(tmp_path, homes):
    return TestClient(create_app(str(tmp_path / "ij")))


def _toml_text(platform) -> str:
    return (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")


def _entry(platform, name: str) -> dict:
    for s in platform.config.mcp_servers:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no config entry named {name!r}")


def _read_ts(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# Lane I <-> lane K: the sources door's keys round-trip
# --------------------------------------------------------------------------- #
def test_every_scan_key_is_one_the_apply_door_reads_and_the_dashboard_posts_it_back(homes):
    keys = [d.source for d in import_sources.KNOWN_SOURCES]
    assert len(keys) == len(set(keys)), "the dashboard keys its groups on `source`; keys must be unique"
    # The scan's rows and the apply door's reader agree on every key, with
    # NO file planted (a missing file is still a known source).
    scanned = [item.source for item in import_sources.scan(None)]
    assert set(scanned) <= set(keys)
    for key in scanned:
        got = import_sources.read_source(None, key)
        assert got is not None and got.source == key, key
    # The dashboard posts the row's own key, never a path or a label.
    tsx = _read_ts("dashboard/components/tools/AddPack.tsx")
    assert "source: source.source" in tsx, "AddPack posts the sources row's `source` key back"
    assert "source: source.origin" not in tsx and "source: source.label" not in tsx


# --------------------------------------------------------------------------- #
# Lane H/J <-> lane K: the name rule, server and twin
# --------------------------------------------------------------------------- #
NAME_TABLE = [
    "@modelcontextprotocol/server-github",
    "My Notes (local)",
    "123-brave",
    "@scope/my.pkg",
    "--leading--dashes--",
    "UPPER_snake_Case",
    "a" * 60,
    "trailing-dash-",
    "api.example.com",
    "",
    "!!!",
    "x",
]


def test_the_dashboards_safe_name_twin_agrees_with_the_server_on_the_table():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH; the twin is pinned by the dashboard suite")
    ts = _read_ts("dashboard/lib/mcpImport.ts")
    start = ts.index("export function safeName(")
    end = ts.index("\n}\n", start) + 3
    fn = ts[start:end].replace("export function", "function").replace("raw: string): string", "raw)")
    script = (
        fn
        + "\nconst inputs = JSON.parse(process.env.IJ_NAMES);"
        + "\nprocess.stdout.write(JSON.stringify(inputs.map(safeName)));"
    )
    out = subprocess.run(
        [node, "-e", script],
        env={**os.environ, "IJ_NAMES": json.dumps(NAME_TABLE)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    twin = json.loads(out.stdout)
    server = [safe_name(raw) for raw in NAME_TABLE]
    assert twin == server, list(zip(NAME_TABLE, twin, server))
    assert server[3] == "scope-my-pkg" and server[6] == "a" * 40 and server[9] == ""


# --------------------------------------------------------------------------- #
# The no-value rule across EVERY v1.343.0 door at once
# --------------------------------------------------------------------------- #
def _assert_clean(text: str, where: str) -> None:
    for value in SENTINELS:
        assert value not in text, f"{where} carries a secret value ({value.split('-')[-1] if '-' in value else value})"


def test_no_value_reaches_any_answer_the_config_or_the_log(client, homes, caplog):
    caplog.set_level(logging.DEBUG)
    platform = client.app.state.platform
    user = homes["user"]

    # 1. The paste door: a config block with a token, then a bare mcp-remote
    #    LINE with a secret header (a line becomes an http pack; an
    #    mcp-remote entry INSIDE a block stays the stdio pack it spells).
    paste = json.dumps(
        {"mcpServers": {"git": {"command": NO_SUCH, "args": ["mcp-server-git"], "env": {"GIT_TOKEN": PASTE_TOKEN, "GIT_HOST": "plain"}}}}
    )
    res = client.post("/mcp/import/preview", json={"text": paste})
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the preview")
    (git_row,) = res.json()["servers"]
    assert git_row["secret_keys"] == ["GIT_TOKEN"] and git_row["env_keys"] == ["GIT_HOST"]

    res = client.post("/mcp/import/apply", json={"text": paste, "names": ["git"], "overrides": {}, "auto_approve": False, "replace": False})
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the paste apply")
    assert [a["name"] for a in res.json()["added"]] == ["git"], res.text
    assert platform.secrets.get("mcp.git.GIT_TOKEN") == PASTE_TOKEN

    line = f'npx -y mcp-remote https://remote.example/mcp --header "X-API-Key: {PASTE_HEADER}"'
    res = client.post("/mcp/import/preview", json={"text": line})
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the mcp-remote preview")
    (remote_row,) = res.json()["servers"]
    assert remote_row["transport"] == "http" and remote_row["header_names"] == ["X-API-Key"]
    remote_name = remote_row["name"]
    res = client.post("/mcp/import/apply", json={"text": line, "names": [remote_name], "overrides": {}, "auto_approve": False, "replace": False})
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the mcp-remote apply")
    assert [a["name"] for a in res.json()["added"]] == [remote_name], res.text
    assert platform.secrets.get(f"mcp.{remote_name}.header.X-API-Key") == PASTE_HEADER

    # 2. The sources door: a planted Cursor file with a token.
    cursor = user / ".cursor" / "mcp.json"
    cursor.parent.mkdir(parents=True)
    cursor.write_text(json.dumps({"mcpServers": {"cur": {"command": NO_SUCH, "args": ["x"], "env": {"CUR_TOKEN": FILE_TOKEN}}}}), encoding="utf-8")
    res = client.get("/mcp/import/sources")
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the sources scan")
    row = next(s for s in res.json()["sources"] if s["source"] == "cursor")
    assert row["readable"] is True and row["servers"][0]["secret_keys"] == ["CUR_TOKEN"]
    res = client.post("/mcp/import/apply", json={"source": "cursor", "names": ["cur"], "overrides": {}, "auto_approve": False, "replace": False})
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the sources apply")
    assert platform.secrets.get("mcp.cur.CUR_TOKEN") == FILE_TOKEN

    # 3. The http door of POST /mcp/servers (a closed port: refused at once).
    url = f"http://127.0.0.1:{_closed_port()}/mcp"
    res = client.post(
        "/mcp/servers",
        json={
            "name": "hosted",
            "transport": "http",
            "url": url,
            "headers": {"X-API-Key": HTTP_HEADER, "X-Plain": "visible"},
            "auth": {"header": "Authorization", "format": "Bearer {value}", "value": HTTP_AUTH},
        },
    )
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the http add")
    assert platform.secrets.get("mcp.hosted.header.X-API-Key") == HTTP_HEADER
    assert platform.secrets.get("mcp.hosted.auth.value") == HTTP_AUTH

    # 4. The partial PATCH: a new env secret, a new header secret, a new token.
    res = client.patch("/mcp/servers/git", json={"env": {"GIT_TOKEN": PATCH_TOKEN}})
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the stdio PATCH")
    assert platform.secrets.get("mcp.git.GIT_TOKEN") == PATCH_TOKEN
    res = client.patch(
        "/mcp/servers/hosted",
        json={"headers": {"X-Auth-Token": PATCH_HEADER}, "auth": {"header": "Authorization", "format": "Bearer {value}", "value": PATCH_AUTH}},
    )
    assert res.status_code == 200, res.text
    _assert_clean(res.text, "the http PATCH")
    assert platform.secrets.get("mcp.hosted.header.X-Auth-Token") == PATCH_HEADER
    assert platform.secrets.get("mcp.hosted.auth.value") == PATCH_AUTH

    # 5. Everything the user or a log reader could see.
    res = client.get("/mcp/servers")
    assert res.status_code == 200
    _assert_clean(res.text, "GET /mcp/servers")
    _assert_clean(_toml_text(platform), "config.toml")
    _assert_clean(json.dumps(platform.config.mcp_servers), "the config object")
    _assert_clean(caplog.text, "the daemon log")
    # And a second preview of the same paste still shows names only, while
    # the rows now say `exists`.
    res = client.post("/mcp/import/preview", json={"text": paste})
    _assert_clean(res.text, "the second preview")
    assert all(r["exists"] for r in res.json()["servers"])


# --------------------------------------------------------------------------- #
# The contract's answer shapes
# --------------------------------------------------------------------------- #
PREVIEW_KEYS = {"name", "transport", "command", "args", "url", "header_names", "env_keys", "secret_keys", "exists", "runtime_ready", "launcher_note", "source", "origin"}
SOURCE_KEYS = {"source", "label", "origin", "readable", "note", "servers"}


def test_the_contracts_answer_shapes(client, homes):
    text = json.dumps({"mcpServers": {"fs": {"command": NO_SUCH, "args": ["."], "env": {"API_TOKEN": "t"}}}})
    res = client.post("/mcp/import/preview", json={"text": text})
    assert res.status_code == 200
    assert set(res.json()) == {"servers", "unrecognised"}
    assert PREVIEW_KEYS <= set(res.json()["servers"][0]), set(res.json()["servers"][0])

    res = client.get("/mcp/import/sources")
    assert res.status_code == 200 and set(res.json()) == {"sources"}
    assert res.json()["sources"], "a list, even with nothing planted"
    for row in res.json()["sources"]:
        assert set(row) == SOURCE_KEYS, row
        assert row["readable"] is False and row["note"] == ""

    res = client.post("/mcp/import/apply", json={"text": text, "names": ["fs", "ghost"], "overrides": {}, "auto_approve": False, "replace": False})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"added", "skipped"}
    assert set(body["added"][0]) == {"name", "tools_loaded", "note", "state"}
    assert body["skipped"] == [{"name": "ghost", "why": "not in the paste"}]

    res = client.get("/mcp/servers")
    row = next(s for s in res.json()["servers"] if s["name"] == "fs")
    assert row["transport"] == "stdio" and row["header_names"] == [] and row["auth_set"] is False
    assert row["secret_keys"] == ["API_TOKEN"] and "env" not in row and "env_secrets" not in row

    res = client.patch("/mcp/servers/fs", json={"cwd": ""})
    assert res.status_code == 200, res.text
    assert set(res.json()) == set(row) | {"reloaded", "note"}, "the PATCH answer is the GET row plus reloaded/note"
    assert res.json()["reloaded"] is False and "did not start" in (res.json()["note"] or "")


# --------------------------------------------------------------------------- #
# Lane K's editor bodies, byte for byte, on the real PATCH
# --------------------------------------------------------------------------- #
def test_the_editors_exact_patch_bodies_do_what_the_editor_says(client, homes):
    platform = client.app.state.platform
    assert client.post("/mcp/servers", json={"name": "brave", "command": NO_SUCH, "args": [], "env": {"BRAVE_API_KEY": "k1", "REGION": "us"}}).status_code == 200
    url = f"http://127.0.0.1:{_closed_port()}/mcp"
    assert (
        client.post(
            "/mcp/servers",
            json={"name": "remote", "transport": "http", "url": url, "headers": {"X-Api-Key": "h1"}, "auth": {"header": "Authorization", "format": "Bearer {value}", "value": "t1"}},
        ).status_code
        == 200
    )
    assert platform.secrets.get("mcp.brave.BRAVE_API_KEY") == "k1"

    # editPatch: Clear on a secret -> {clear_secrets: [K]} and nothing else.
    res = client.patch("/mcp/servers/brave", json={"clear_secrets": ["BRAVE_API_KEY"]})
    assert res.status_code == 200, res.text
    assert res.json()["secret_keys"] == [] and res.json()["env_keys"] == ["REGION"]
    assert platform.secrets.get("mcp.brave.BRAVE_API_KEY") in (None, "")
    assert "env_secrets" not in _entry(platform, "brave")

    # editPatch: Clear on a header and on the token -> {clear_headers, auth: null}.
    res = client.patch("/mcp/servers/remote", json={"clear_headers": ["X-Api-Key"], "auth": None})
    assert res.status_code == 200, res.text
    assert res.json()["header_names"] == [] and res.json()["auth_set"] is False
    assert platform.secrets.get("mcp.remote.header.X-Api-Key") in (None, "")
    assert platform.secrets.get("mcp.remote.auth.value") in (None, "")
    assert "auth" not in _entry(platform, "remote")

    # An omitted auth KEEPS a token (null and omitted are different things).
    assert client.patch("/mcp/servers/remote", json={"auth": {"header": "Authorization", "format": "Bearer {value}", "value": "t2"}}).status_code == 200
    res = client.patch("/mcp/servers/remote", json={"url": url})
    assert res.status_code == 200 and res.json()["auth_set"] is True
    assert platform.secrets.get("mcp.remote.auth.value") == "t2"

    # The Tools page's flip: a bare {auto_approve} keeps the v1.103.0 answer.
    res = client.patch("/mcp/servers/brave", json={"auto_approve": True})
    assert res.status_code == 200
    assert res.json() == {"name": "brave", "auto_approve": True, "note": "saved — restart Iron Jarvis for autonomous agents to pick this up"}
    assert _entry(platform, "brave").get("auto_approve") is True
