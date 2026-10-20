"""v1.343.0 ("add a pack in one paste"), lane I-sources: the servers the
user's OTHER apps already have, read from their config files read-only.

``GET /mcp/import/sources`` (``mcp/import_sources.scan``) lists one row per
known file — ``~/.claude.json`` (``CLAUDE_CONFIG_DIR`` honoured), Claude
Code's ``settings.json``, the active project's ``.mcp.json``, Claude
Desktop's ``claude_desktop_config.json`` under ``%APPDATA%``, Codex's
``config.toml`` (``CODEX_HOME`` honoured), Cursor's ``mcp.json`` and VS
Code's user ``settings.json`` — with ``readable`` / ``note`` / the paste
door's preview rows, and NEVER a value. ``POST /mcp/import/apply {source}``
re-reads that file server-side and adds the chosen packs through the one
add door, so a token goes from the disk into the vault without ever
crossing the browser.

Every test runs under a TEMP home (``USERPROFILE``/``HOME``/``APPDATA``
swapped; ``CLAUDE_CONFIG_DIR``/``CODEX_HOME`` cleared or pointed at temp
folders). The user's REAL ``~/.claude.json``, ``~/.codex/config.toml`` and
Claude Desktop config are read as bytes when this module is imported and a
module-level teardown proves them byte-identical afterwards.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import import_sources
from iron_jarvis.mcp import tools as mcp_tools_mod

# --------------------------------------------------------------------------- #
# The REAL files, read at import time, before any fixture moves HOME.
# --------------------------------------------------------------------------- #
from tests.fixtures.real_config_view import real_view  # noqa: E402

_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "") if os.environ.get("CLAUDE_CONFIG_DIR") else None
_REAL_CODEX_HOME = Path(os.environ.get("CODEX_HOME") or _REAL_HOME / ".codex")
_REAL_APPDATA = Path(os.environ.get("APPDATA") or "") if os.environ.get("APPDATA") else None
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
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None


def _view(p: Path) -> bytes | str | None:
    """What this guard compares: one shared view (tests/fixtures/real_config_view)
    so every real-files guard treats the app-rewritten files alike."""
    return real_view(p)


_REAL_BEFORE: dict[Path, bytes | str | None] = {p: _view(p) for p in _REAL_FILES}


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    """Module-level teardown: the user's real config files are byte-identical
    after every test in this file ran (a missing file stays missing)."""
    yield
    after = {p: _view(p) for p in _REAL_FILES}
    changed = [str(p) for p in _REAL_FILES if after[p] != _REAL_BEFORE[p]]
    assert not changed, f"a test touched the user's real config files: {changed}"


# --------------------------------------------------------------------------- #
# Harness: temp homes, planted files, the real app.
# --------------------------------------------------------------------------- #
#: A launcher that cannot exist: the add door's live load fails at once
#: (no child, no network) and the row is persisted all the same.
NO_SUCH = "ij-no-such-mcp-launcher-v1343i"
TOKEN = "ghp_SECRET_v1343i"
TOKEN_2 = "ghp_SECRET_second_v1343i"
HEADER_VALUE = "Bearer hdr-SECRET-v1343i"
PLAIN_VALUE = "plain-env-value-v1343i"
OWN_EXE = "C:/Apps/Iron Jarvis/resources/daemon/ironjarvis.exe"


def _claude_json(token: str = TOKEN) -> str:
    return json.dumps(
        {
            "numStartups": 7,
            "mcpServers": {
                "iron-jarvis": {"command": OWN_EXE, "args": ["mcp-stdio"]},
                "git": {"command": NO_SUCH, "args": ["mcp-server-git"], "env": {"GIT_TOKEN": token, "GIT_HOST": PLAIN_VALUE}},
            },
            "projects": {
                "C:\\work\\app": {
                    "allowedTools": [],
                    "mcpServers": {
                        "notion": {"type": "http", "url": "https://mcp.notion.com/mcp", "headers": {"Authorization": HEADER_VALUE}}
                    },
                },
                "C:\\work\\other": {"allowedTools": []},
            },
        }
    )


CLAUDE_SETTINGS = json.dumps(
    {
        "permissions": {"allow": []},
        "mcpServers": {"memory": {"command": NO_SUCH, "args": ["-y", "@modelcontextprotocol/server-memory"]}},
    }
)

CODEX_TOML = f"""model = "o3"

[mcp_servers.git]
command = "{NO_SUCH}"
args = ["mcp-server-git"]

[mcp_servers.git.env]
GIT_TOKEN = "{TOKEN}"
GIT_HOST = "{PLAIN_VALUE}"

[mcp_servers.hosted]
url = "https://example.com/mcp"
"""

VSCODE_SETTINGS = f"""{{
  // editor settings above
  "editor.fontSize": 14,
  "mcp": {{
    "servers": {{
      "fetch": {{"type": "stdio", "command": "{NO_SUCH}", "args": ["mcp-server-fetch"],}},
      "docs": {{"type": "sse", "url": "https://docs.example.com/sse"}},
    }},
  }},
}}
"""

MALFORMED = '{"mcpServers": {"brave": {"command": "' + NO_SUCH + '", "env": {"BRAVE_API_KEY": "' + TOKEN + '"}}'  # no closing braces


@pytest.fixture(autouse=True)
def _fresh_records():
    """The load record is process-wide (the v1.325.2 lesson): clear it so
    another worker's pack never answers for ours."""
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()


@pytest.fixture
def homes(tmp_path, monkeypatch) -> dict[str, Path]:
    """A temp user home and a temp APPDATA; the override vars cleared."""
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


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plant_all(homes: dict[str, Path]) -> dict[str, Path]:
    """Every source but Cursor (left missing) and with Claude Desktop's file
    malformed. Returns the planted paths by source key."""
    user, appdata = homes["user"], homes["appdata"]
    return {
        "claude-code": _write(user / ".claude.json", _claude_json()),
        "claude-code-settings": _write(user / ".claude" / "settings.json", CLAUDE_SETTINGS),
        "claude-desktop": _write(appdata / "Claude" / "claude_desktop_config.json", MALFORMED),
        "codex": _write(user / ".codex" / "config.toml", CODEX_TOML),
        "cursor": user / ".cursor" / "mcp.json",
        "vscode": _write(appdata / "Code" / "User" / "settings.json", VSCODE_SETTINGS),
    }


@pytest.fixture
def client(tmp_path, homes):
    return TestClient(create_app(str(tmp_path / "ij")))


def _by_source(body: dict) -> dict[str, dict]:
    rows = {r["source"]: r for r in body["sources"]}
    assert len(rows) == len(body["sources"]), "source keys must be unique (the dashboard keys its groups on them)"
    return rows


def _toml_text(platform) -> str:
    return (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")


def _entry(platform, name: str) -> dict:
    for s in platform.config.mcp_servers:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no config entry named {name!r}")


def _activate_project(platform, root: Path, *, status: str = "active") -> str:
    with session_scope(platform.engine) as db:
        row = Project(id="project-v1343i", name="Import test", root=str(root), status=status)
        db.add(row)
        db.commit()
    platform.config.active_project_id = "project-v1343i"
    return "project-v1343i"


# --------------------------------------------------------------------------- #
# GET /mcp/import/sources
# --------------------------------------------------------------------------- #
def test_sources_lists_every_known_file_with_readable_flags_and_never_a_value(client, homes):
    planted = _plant_all(homes)
    res = client.get("/mcp/import/sources")
    assert res.status_code == 200, res.text
    for secret in (TOKEN, HEADER_VALUE, PLAIN_VALUE, "mcpServers"):
        assert secret not in res.text, f"a value or file contents reached the answer: {secret!r}"
    body = res.json()
    # v1.354.0 (G50, updated on purpose): the files current tools use joined
    # the list (Claude Code plugins, VS Code's mcp.json, Windsurf, Gemini CLI);
    # the project-only rows stay out while no project is active.
    assert [r["source"] for r in body["sources"]] == [
        "claude-code",
        "claude-code-settings",
        "claude-code-plugins",
        "claude-desktop",
        "codex",
        "cursor",
        "vscode-mcp",
        "vscode",
        "windsurf",
        "gemini-cli",
    ], "no project is active, so no project row; the rest in the known order"
    rows = _by_source(body)
    for key, path in planted.items():
        assert rows[key]["origin"] == str(path), key
        assert set(rows[key]) == {"source", "label", "origin", "readable", "note", "servers"}
    # Claude Code: its own entry dropped, the top-level and the per-project
    # server listed with the project path as the row's origin.
    cc = rows["claude-code"]
    assert cc["readable"] is True and cc["note"] == "" and cc["label"] == "Claude Code"
    by_name = {r["name"]: r for r in cc["servers"]}
    assert set(by_name) == {"git", "notion"}, "iron-jarvis (our own server) is never listed"
    assert by_name["git"] == {
        "name": "git",
        "transport": "stdio",
        "command": NO_SUCH,
        "args": ["mcp-server-git"],
        "url": "",
        "header_names": [],
        "env_keys": ["GIT_HOST"],
        "secret_keys": ["GIT_TOKEN"],
        "auth_set": False,
        "source": "claude-code",
        "origin": str(planted["claude-code"]),
        "exists": False,
        "runtime_ready": False,
        "launcher_note": f"needs {NO_SUCH}",
        # v1.354.0 (G49/G50, updated on purpose).
        "disabled": False,
        "exists_as": "",
        "blurb": "Read the history, diffs, and branches of a local git repo.",
        # v1.360.0 (G49, added on purpose): what the row runs, read off its
        # masked command line (an unknown launcher is the program itself),
        # and an address row's host ("" for a stdio row).
        "program": NO_SUCH,
        "host": "",
    }
    assert by_name["notion"]["transport"] == "http"
    assert by_name["notion"]["origin"] == "C:\\work\\app"
    assert by_name["notion"]["header_names"] == ["Authorization"]
    assert by_name["notion"]["runtime_ready"] is True and by_name["notion"]["launcher_note"] == ""
    # Claude Code settings.json "mcpServers".
    st = rows["claude-code-settings"]
    assert st["readable"] is True and [r["name"] for r in st["servers"]] == ["memory"]
    # Claude Desktop: malformed -> unreadable, a one-line note naming the
    # file and never its contents.
    cd = rows["claude-desktop"]
    assert cd["readable"] is False and cd["servers"] == []
    assert "claude_desktop_config.json" in cd["note"]
    assert "\n" not in cd["note"] and "brave" not in cd["note"] and "{" not in cd["note"]
    # Codex TOML.
    cx = rows["codex"]
    assert cx["readable"] is True and {r["name"] for r in cx["servers"]} == {"git", "hosted"}
    git_row = next(r for r in cx["servers"] if r["name"] == "git")
    assert git_row["secret_keys"] == ["GIT_TOKEN"] and git_row["env_keys"] == ["GIT_HOST"]
    # Cursor: missing -> unreadable with an EMPTY note.
    cu = rows["cursor"]
    assert cu == {"source": "cursor", "label": "Cursor", "origin": str(planted["cursor"]), "readable": False, "note": "", "servers": []}
    # VS Code: comments and trailing commas tolerated.
    vs = rows["vscode"]
    assert vs["readable"] is True and {r["name"] for r in vs["servers"]} == {"fetch", "docs"}
    docs = next(r for r in vs["servers"] if r["name"] == "docs")
    assert docs["transport"] == "sse" and docs["source"] == "vscode"


def test_sources_with_nothing_planted_are_all_unreadable_with_empty_notes(client):
    body = client.get("/mcp/import/sources").json()
    # v1.354.0 (G50, updated on purpose): ten files are looked for now.
    assert len(body["sources"]) == 10
    for row in body["sources"]:
        assert row["readable"] is False and row["note"] == "" and row["servers"] == [], row
        assert row["origin"], "even a missing file is named, so the user knows where to put one"


def test_sources_honour_claude_config_dir_and_codex_home(client, homes, tmp_path, monkeypatch):
    """With the override set, Claude Code reads ``$CLAUDE_CONFIG_DIR/.claude.json``
    and ``$CLAUDE_CONFIG_DIR/settings.json`` (the v1.302.0 measurement) and
    Codex reads ``$CODEX_HOME/config.toml`` — so does the scan, and the
    files in the plain home are NOT read."""
    user = homes["user"]
    _write(user / ".claude.json", json.dumps({"mcpServers": {"home-one": {"command": NO_SUCH}}}))
    _write(user / ".claude" / "settings.json", json.dumps({"mcpServers": {"home-two": {"command": NO_SUCH}}}))
    _write(user / ".codex" / "config.toml", '[mcp_servers.home-three]\ncommand = "' + NO_SUCH + '"\n')
    cc_dir = tmp_path / "cc-override"
    codex_dir = tmp_path / "codex-override"
    _write(cc_dir / ".claude.json", json.dumps({"mcpServers": {"over-one": {"command": NO_SUCH}}}))
    _write(cc_dir / "settings.json", json.dumps({"mcpServers": {"over-two": {"command": NO_SUCH}}}))
    _write(codex_dir / "config.toml", '[mcp_servers.over-three]\ncommand = "' + NO_SUCH + '"\n')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cc_dir))
    monkeypatch.setenv("CODEX_HOME", str(codex_dir))
    rows = _by_source(client.get("/mcp/import/sources").json())
    assert rows["claude-code"]["origin"] == str(cc_dir / ".claude.json")
    assert [r["name"] for r in rows["claude-code"]["servers"]] == ["over-one"]
    assert rows["claude-code-settings"]["origin"] == str(cc_dir / "settings.json")
    assert [r["name"] for r in rows["claude-code-settings"]["servers"]] == ["over-two"]
    assert rows["codex"]["origin"] == str(codex_dir / "config.toml")
    assert [r["name"] for r in rows["codex"]["servers"]] == ["over-three"]
    assert "home-one" not in json.dumps(rows) and "home-three" not in json.dumps(rows)


def test_sources_read_the_home_the_platform_really_uses(client, tmp_path, monkeypatch):
    """``Path.home`` reads ``USERPROFILE`` on Windows and ``HOME`` elsewhere;
    the scan must follow the one that wins on this platform, so a test that
    swaps only the other variable would still read the REAL home."""
    a = tmp_path / "home-a"
    b = tmp_path / "home-b"
    _write(a / ".claude.json", json.dumps({"mcpServers": {"from-a": {"command": NO_SUCH}}}))
    _write(b / ".claude.json", json.dumps({"mcpServers": {"from-b": {"command": NO_SUCH}}}))
    monkeypatch.setenv("USERPROFILE", str(a))
    monkeypatch.setenv("HOME", str(b))
    winner = "from-a" if sys.platform == "win32" else "from-b"
    expected_dir = a if sys.platform == "win32" else b
    assert Path.home() == expected_dir, "the platform's own rule, pinned so the assertion below means something"
    rows = _by_source(client.get("/mcp/import/sources").json())
    assert rows["claude-code"]["origin"] == str(expected_dir / ".claude.json")
    assert [r["name"] for r in rows["claude-code"]["servers"]] == [winner]


def test_the_active_projects_mcp_json_is_listed_only_while_a_project_is_active(client, homes, tmp_path):
    platform = client.app.state.platform
    root = tmp_path / "proj"
    root.mkdir()
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"proj-tool": {"command": NO_SUCH, "args": ["x"], "env": {"API_KEY": TOKEN}}}}))
    # No active project: no row at all (there is no file to name).
    assert "claude-code-project" not in _by_source(client.get("/mcp/import/sources").json())
    _activate_project(platform, root)
    res = client.get("/mcp/import/sources")
    assert TOKEN not in res.text
    rows = _by_source(res.json())
    pr = rows["claude-code-project"]
    assert pr["origin"] == str(root / ".mcp.json")
    assert pr["readable"] is True and pr["label"] == "Claude Code (this project)"
    assert [r["name"] for r in pr["servers"]] == ["proj-tool"]
    assert pr["servers"][0]["secret_keys"] == ["API_KEY"] and pr["servers"][0]["source"] == "claude-code"
    # The project row sits right after the two Claude Code rows.
    assert [r["source"] for r in res.json()["sources"]][:3] == ["claude-code", "claude-code-settings", "claude-code-project"]
    # Deactivated: gone again.
    platform.config.active_project_id = None
    assert "claude-code-project" not in _by_source(client.get("/mcp/import/sources").json())


def test_an_archived_or_unknown_active_project_lists_no_project_file(client, homes, tmp_path):
    platform = client.app.state.platform
    root = tmp_path / "proj"
    root.mkdir()
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"proj-tool": {"command": NO_SUCH}}}))
    _activate_project(platform, root, status="archived")
    assert "claude-code-project" not in _by_source(client.get("/mcp/import/sources").json())
    platform.config.active_project_id = "project-that-does-not-exist"
    assert "claude-code-project" not in _by_source(client.get("/mcp/import/sources").json())


def test_a_config_that_only_lists_iron_jarvis_reads_as_readable_and_empty(client, homes):
    _write(
        homes["user"] / ".claude.json",
        json.dumps(
            {
                "mcpServers": {
                    "iron-jarvis": {"command": OWN_EXE, "args": ["mcp-stdio"]},
                    "shim": {"command": "python", "args": ["-m", "iron_jarvis.mcpserver.stdio_shim"]},
                    "inert": {"command": "ironjarvis", "args": ["claude-inert-mcp", "m.json"]},
                }
            }
        ),
    )
    row = _by_source(client.get("/mcp/import/sources").json())["claude-code"]
    assert row["readable"] is True and row["servers"] == [] and row["note"] == ""


def test_sources_rows_say_exists_for_a_pack_already_added(client, homes):
    _plant_all(homes)
    res = client.post("/mcp/servers", json={"name": "git", "command": NO_SUCH, "args": ["mcp-server-git"]})
    assert res.status_code == 200, res.text
    rows = _by_source(client.get("/mcp/import/sources").json())
    cc = {r["name"]: r for r in rows["claude-code"]["servers"]}
    assert cc["git"]["exists"] is True and cc["notion"]["exists"] is False
    cx = {r["name"]: r for r in rows["codex"]["servers"]}
    assert cx["git"]["exists"] is True


def test_scan_never_raises_with_no_home_and_no_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)

    def _no_home():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(import_sources.Path, "home", staticmethod(_no_home))
    out = import_sources.scan(platform=None)
    # v1.354.0 (G50, updated on purpose): the new sources, no project rows.
    assert [s.source for s in out] == [
        "claude-code",
        "claude-code-settings",
        "claude-code-plugins",
        "claude-desktop",
        "codex",
        "cursor",
        "vscode-mcp",
        "vscode",
        "windsurf",
        "gemini-cli",
    ]
    for item in out:
        assert item.readable is False and item.note == "" and item.specs == [] and item.origin == ""
    # A resolver that faults is "no such place", never a raise.
    bad = import_sources.SourceDef("x", "paste", "X", lambda _p: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(import_sources, "KNOWN_SOURCES", (bad,))
    (only,) = import_sources.scan(platform=None)
    assert only.readable is False and only.origin == ""


def test_scan_specs_keep_values_in_memory_but_the_row_never_carries_them(homes):
    _plant_all(homes)
    (codex,) = [s for s in import_sources.scan(platform=None) if s.source == "codex"]
    git = next(s for s in codex.specs if s.name == "git")
    assert git.env == {"GIT_TOKEN": TOKEN, "GIT_HOST": PLAIN_VALUE}, "the apply door needs the values"
    row = codex.row([s.preview_row() for s in codex.specs])
    assert TOKEN not in json.dumps(row) and PLAIN_VALUE not in json.dumps(row)
    assert "specs" not in row


# --------------------------------------------------------------------------- #
# POST /mcp/import/apply {source}
# --------------------------------------------------------------------------- #
def test_apply_from_a_source_puts_the_token_in_the_vault_and_never_in_the_answer(client, homes):
    _plant_all(homes)
    platform = client.app.state.platform
    res = client.post("/mcp/import/apply", json={"source": "codex", "names": ["git"], "overrides": {}, "auto_approve": False, "replace": False})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text and PLAIN_VALUE not in res.text
    body = res.json()
    assert body["skipped"] == []
    (added,) = body["added"]
    assert added["name"] == "git" and added["tools_loaded"] == 0 and added["state"] == "failed"
    assert "did not start" in (added["note"] or "")
    assert _entry(platform, "git") == {
        "name": "git",
        "command": NO_SUCH,
        "args": ["mcp-server-git"],
        "env": {"GIT_HOST": PLAIN_VALUE},
        "env_secrets": {"GIT_TOKEN": "mcp.git.GIT_TOKEN"},
    }
    assert platform.secrets.get("mcp.git.GIT_TOKEN") == TOKEN
    assert TOKEN not in json.dumps(platform.config.mcp_servers)
    assert TOKEN not in _toml_text(platform)
    # Only the chosen pack; "hosted" was not named.
    assert [s["name"] for s in platform.config.mcp_servers] == ["git"]
    # And the GET row lists key names only.
    row = next(r for r in client.get("/mcp/servers").json()["servers"] if r["name"] == "git")
    assert row["secret_keys"] == ["GIT_TOKEN"] and TOKEN not in json.dumps(row)


def test_apply_from_a_source_rereads_the_file_server_side(client, homes):
    """The GET is not a cache: the value in the vault is whatever the file
    holds at APPLY time, read by the daemon, never sent by the browser."""
    planted = _plant_all(homes)
    platform = client.app.state.platform
    assert client.get("/mcp/import/sources").status_code == 200
    _write(planted["claude-code"], _claude_json(token=TOKEN_2))
    res = client.post("/mcp/import/apply", json={"source": "claude-code", "names": ["git", "notion"]})
    assert res.status_code == 200, res.text
    assert TOKEN_2 not in res.text and HEADER_VALUE not in res.text
    assert {a["name"] for a in res.json()["added"]} == {"git", "notion"}
    assert platform.secrets.get("mcp.git.GIT_TOKEN") == TOKEN_2
    # The http pack's Authorization header is secret-named: vault, not row.
    notion = _entry(platform, "notion")
    assert notion["transport"] == "http" and notion["url"] == "https://mcp.notion.com/mcp"
    assert notion.get("header_secrets") == {"Authorization": "mcp.notion.header.Authorization"}
    assert "headers" not in notion
    assert platform.secrets.get("mcp.notion.header.Authorization") == HEADER_VALUE
    assert HEADER_VALUE not in _toml_text(platform)


def test_apply_from_a_source_goes_through_the_one_add_door(client, homes, monkeypatch):
    from iron_jarvis.daemon.routes import agents as agents_routes

    _plant_all(homes)
    seen: list[str] = []
    real = agents_routes.add_mcp_pack

    def _spy(d, body):
        seen.append(body.name)
        return real(d, body)

    monkeypatch.setattr(agents_routes, "add_mcp_pack", _spy)
    res = client.post("/mcp/import/apply", json={"source": "vscode", "names": ["fetch", "docs"]})
    assert res.status_code == 200, res.text
    assert seen == ["fetch", "docs"]
    assert {a["name"] for a in res.json()["added"]} == {"fetch", "docs"}


def test_apply_from_a_source_respects_overrides_skips_existing_and_names_not_in_the_file(client, homes):
    _plant_all(homes)
    platform = client.app.state.platform
    # Typed override for a key the file does not carry; a name the file lacks.
    res = client.post(
        "/mcp/import/apply",
        json={"source": "claude-code-settings", "names": ["memory", "ghost"], "overrides": {"memory": {"MEMORY_TOKEN": "typed-SECRET-v1343i"}}},
    )
    assert res.status_code == 200, res.text
    assert "typed-SECRET-v1343i" not in res.text
    body = res.json()
    assert [a["name"] for a in body["added"]] == ["memory"]
    assert body["skipped"] == [{"name": "ghost", "why": "not in the paste"}]
    assert platform.secrets.get("mcp.memory.MEMORY_TOKEN") == "typed-SECRET-v1343i"
    # Again without replace: skipped as already added.
    again = client.post("/mcp/import/apply", json={"source": "claude-code-settings", "names": ["memory"]}).json()
    assert again == {"added": [], "skipped": [{"name": "memory", "why": "already added"}]}


def test_apply_refuses_an_unknown_source_an_unreadable_one_and_a_bare_path(client, homes):
    planted = _plant_all(homes)
    platform = client.app.state.platform
    # Unknown key.
    res = client.post("/mcp/import/apply", json={"source": "notepad", "names": ["git"]})
    assert res.status_code == 404, res.text
    # A PATH is not a key: the daemon never reads a file the browser names,
    # even one it would have read under its own key.
    res = client.post("/mcp/import/apply", json={"source": str(planted["codex"]), "names": ["git"]})
    assert res.status_code == 404, res.text
    # Missing file (Cursor): refused, plain words, no contents.
    res = client.post("/mcp/import/apply", json={"source": "cursor", "names": ["x"]})
    assert res.status_code == 409, res.text
    assert "Cursor" in res.json()["detail"]
    # Malformed file (Claude Desktop): refused naming the file only.
    res = client.post("/mcp/import/apply", json={"source": "claude-desktop", "names": ["brave"]})
    assert res.status_code == 409, res.text
    assert "claude_desktop_config.json" in res.json()["detail"]
    assert TOKEN not in res.text and "brave" not in res.json()["detail"]
    # No names.
    assert client.post("/mcp/import/apply", json={"source": "codex", "names": []}).status_code == 400
    assert platform.config.mcp_servers == []


def test_apply_prefers_text_over_source_when_both_are_sent(client, homes):
    """The paste door's branch is unchanged: text wins, the file is not read."""
    _plant_all(homes)
    res = client.post("/mcp/import/apply", json={"text": "npx -y pkg-from-text", "source": "codex", "names": ["pkg-from-text"]})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["pkg-from-text"]
    assert [s["name"] for s in client.app.state.platform.config.mcp_servers] == ["pkg-from-text"]


def test_apply_from_the_active_projects_mcp_json(client, homes, tmp_path):
    platform = client.app.state.platform
    root = tmp_path / "proj"
    root.mkdir()
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"proj-tool": {"command": NO_SUCH, "args": ["x"], "env": {"API_KEY": TOKEN}}}}))
    # Not active: the key is unknown-by-state (no file to name) -> 409.
    res = client.post("/mcp/import/apply", json={"source": "claude-code-project", "names": ["proj-tool"]})
    assert res.status_code == 409, res.text
    _activate_project(platform, root)
    res = client.post("/mcp/import/apply", json={"source": "claude-code-project", "names": ["proj-tool"]})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    assert platform.secrets.get("mcp.proj-tool.API_KEY") == TOKEN
    assert _entry(platform, "proj-tool")["env_secrets"] == {"API_KEY": "mcp.proj-tool.API_KEY"}


def test_read_source_knows_only_the_scan_keys(homes):
    _plant_all(homes)
    assert import_sources.read_source(None, "") is None
    assert import_sources.read_source(None, "nope") is None
    assert import_sources.read_source(None, str(homes["user"] / ".codex" / "config.toml")) is None
    got = import_sources.read_source(None, "codex")
    assert got is not None and got.readable and {s.name for s in got.specs} == {"git", "hosted"}
