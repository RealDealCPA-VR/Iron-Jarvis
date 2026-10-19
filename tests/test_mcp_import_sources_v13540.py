"""v1.354.0 (product-readiness Wave 4, lane 4C-imports): the import door
reads the files current tools use, respects "off", knows what is already
connected and says what each tool does.

G50 — ``GET /mcp/import/sources`` (``mcp/import_sources.scan``) also reads:
VS Code's ``User/mcp.json`` (current VS Code moved its servers out of
``settings.json``), a project's ``.vscode/mcp.json`` and ``.cursor/mcp.json``
(only while a project is active), Windsurf's ``mcp_config.json``, Gemini
CLI's ``settings.json`` and the servers of the Claude Code plugins the user
INSTALLED (``<Claude config>/plugins/installed_plugins.json``). A server its
app switched off (``"disabled": true`` / ``"enabled": false``, a Claude Code
project's ``disabledMcpServers``, Gemini's ``mcp.excluded``, a plugin off in
``enabledPlugins``) arrives with ``disabled: true``; a name already connected
here another way (a memory source, a live Directory connection, a pack under
a folded name) reads ``exists`` with ``exists_as`` saying how.

G49 — every row carries ``blurb``: the Directory catalog's one line for the
package, else the Tools catalog's first sentence, else "".

Everything runs through the REAL app factory under TEMP homes; the user's
real config files are compared before and after the module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import import_sources
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.import_config import PackSpec, parse_any

# --------------------------------------------------------------------------- #
# The REAL files the new sources name, read before any fixture moves HOME.
# --------------------------------------------------------------------------- #
_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_CLAUDE_DIR = Path(os.environ["CLAUDE_CONFIG_DIR"]) if os.environ.get("CLAUDE_CONFIG_DIR") else _REAL_HOME / ".claude"
_REAL_APPDATA = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else None
_REAL_FILES: list[Path] = [
    _REAL_CLAUDE_DIR / "plugins" / "installed_plugins.json",
    _REAL_CLAUDE_DIR / "settings.json",
    _REAL_HOME / ".codeium" / "windsurf" / "mcp_config.json",
    _REAL_HOME / ".gemini" / "settings.json",
]
if _REAL_APPDATA is not None:
    _REAL_FILES.append(_REAL_APPDATA / "Code" / "User" / "mcp.json")
    _REAL_FILES.append(_REAL_APPDATA / "Claude" / "claude_desktop_config.json")


def _bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except OSError:
        return None


_REAL_BEFORE = {p: _bytes(p) for p in _REAL_FILES}


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    yield
    changed = [str(p) for p in _REAL_FILES if _bytes(p) != _REAL_BEFORE[p]]
    assert not changed, f"a test touched the user's real config files: {changed}"


NO_SUCH = "ij-no-such-mcp-launcher-v13540"
TOKEN = "ghp_SECRET_v13540"


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
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "CLAUDE_CODE_PLUGIN_CACHE_DIR", "PIXIO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return {"user": user, "appdata": appdata}


@pytest.fixture
def client(tmp_path, homes):
    return TestClient(create_app(str(tmp_path / "ij")))


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sources(client) -> dict[str, dict]:
    res = client.get("/mcp/import/sources")
    assert res.status_code == 200, res.text
    rows = {r["source"]: r for r in res.json()["sources"]}
    assert len(rows) == len(res.json()["sources"]), "source keys stay unique"
    return rows


def _servers(row: dict) -> dict[str, dict]:
    return {s["name"]: s for s in row["servers"]}


def _activate_project(platform, root: Path) -> None:
    with session_scope(platform.engine) as db:
        db.add(Project(id="project-v13540", name="Import test", root=str(root), status="active"))
        db.commit()
    platform.config.active_project_id = "project-v13540"


# --------------------------------------------------------------------------- #
# G50: the files current tools use
# --------------------------------------------------------------------------- #
VSCODE_MCP_JSON = """{
  // current VS Code: servers live in User/mcp.json
  "inputs": [{"type": "promptString", "id": "gh-token", "password": true}],
  "servers": {
    "github": {"type": "stdio", "command": "%s", "args": ["-y", "@modelcontextprotocol/server-github"],
               "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "%s"}},
    "docs": {"type": "http", "url": "https://docs.example.com/mcp"},
  },
}
""" % (NO_SUCH, TOKEN)


def test_a_vs_code_user_with_servers_in_mcp_json_sees_them_listed(client, homes):
    """ACCEPTANCE (G50): a VS Code user with servers in mcp.json sees them."""
    path = _write(homes["appdata"] / "Code" / "User" / "mcp.json", VSCODE_MCP_JSON)
    res = client.get("/mcp/import/sources")
    assert TOKEN not in res.text, "a value never reaches the answer"
    row = {r["source"]: r for r in res.json()["sources"]}["vscode-mcp"]
    assert row["label"] == "VS Code" and row["origin"] == str(path)
    assert row["readable"] is True and row["note"] == ""
    servers = _servers(row)
    assert set(servers) == {"github", "docs"}
    gh = servers["github"]
    assert gh["source"] == "vscode" and gh["secret_keys"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert gh["disabled"] is False and gh["exists"] is False and gh["exists_as"] == ""
    assert servers["docs"]["transport"] == "http"
    # The older settings.json place is still read, under its own name now.
    assert _sources(client)["vscode"]["label"] == "VS Code settings"


def test_apply_from_vs_code_mcp_json_rereads_the_file_and_vaults_the_token(client, homes):
    _write(homes["appdata"] / "Code" / "User" / "mcp.json", VSCODE_MCP_JSON)
    platform = client.app.state.platform
    res = client.post("/mcp/import/apply", json={"source": "vscode-mcp", "names": ["github"]})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    assert [a["name"] for a in res.json()["added"]] == ["github"]
    assert platform.secrets.get("mcp.github.GITHUB_PERSONAL_ACCESS_TOKEN") == TOKEN


def test_project_files_of_vs_code_and_cursor_are_listed_only_while_a_project_is_active(client, homes, tmp_path):
    root = tmp_path / "proj"
    _write(root / ".vscode" / "mcp.json", json.dumps({"servers": {"proj-vs": {"command": NO_SUCH, "args": ["x"]}}}))
    _write(root / ".cursor" / "mcp.json", json.dumps({"mcpServers": {"proj-cursor": {"command": NO_SUCH}}}))
    rows = _sources(client)
    assert "vscode-project" not in rows and "cursor-project" not in rows, "no project: no file to name"
    _activate_project(client.app.state.platform, root)
    rows = _sources(client)
    assert rows["vscode-project"]["label"] == "VS Code (this project)"
    assert rows["vscode-project"]["origin"] == str(root / ".vscode" / "mcp.json")
    assert list(_servers(rows["vscode-project"])) == ["proj-vs"]
    assert rows["cursor-project"]["label"] == "Cursor (this project)"
    assert list(_servers(rows["cursor-project"])) == ["proj-cursor"]
    # The apply door finds them by key while the project is active.
    res = client.post("/mcp/import/apply", json={"source": "cursor-project", "names": ["proj-cursor"]})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["proj-cursor"]


def test_windsurf_and_gemini_cli_are_read_with_their_own_transport_words(client, homes):
    user = homes["user"]
    _write(
        user / ".codeium" / "windsurf" / "mcp_config.json",
        json.dumps(
            {
                "mcpServers": {
                    "wind-local": {"command": NO_SUCH, "args": ["a"]},
                    "wind-remote": {"serverUrl": "https://wind.example.com/mcp"},
                    "wind-off": {"command": NO_SUCH, "disabled": True},
                }
            }
        ),
    )
    _write(
        user / ".gemini" / "settings.json",
        json.dumps(
            {
                "theme": "Default",
                "mcpServers": {
                    "gem-sse": {"url": "https://gem.example.com/events"},
                    "gem-http": {"httpUrl": "https://gem.example.com/mcp"},
                    "gem-local": {"command": NO_SUCH},
                    "gem-skipped": {"command": NO_SUCH},
                },
                "mcp": {"excluded": ["gem-skipped"]},
            }
        ),
    )
    rows = _sources(client)
    wind = _servers(rows["windsurf"])
    assert rows["windsurf"]["label"] == "Windsurf"
    assert wind["wind-remote"]["transport"] == "http" and wind["wind-remote"]["url"] == "https://wind.example.com/mcp"
    assert wind["wind-off"]["disabled"] is True and wind["wind-local"]["disabled"] is False
    gem = _servers(rows["gemini-cli"])
    assert rows["gemini-cli"]["label"] == "Gemini CLI"
    # Gemini names the transport by the KEY: url is SSE, httpUrl is HTTP.
    assert gem["gem-sse"]["transport"] == "sse"
    assert gem["gem-http"]["transport"] == "http"
    assert gem["gem-skipped"]["disabled"] is True, "mcp.excluded switches it off"
    assert gem["gem-local"]["disabled"] is False
    assert all(s["source"] == "gemini-cli" for s in gem.values())


def test_gemini_allowed_list_switches_off_every_name_it_does_not_list():
    text = json.dumps(
        {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}}, "mcp": {"allowed": ["a"]}}
    )
    by = {s.name: s.disabled for s in parse_any(text)}
    assert by == {"a": False, "b": True}


# --------------------------------------------------------------------------- #
# G50: respects off
# --------------------------------------------------------------------------- #
def test_a_server_switched_off_in_claude_desktop_is_listed_disabled_and_added_only_when_chosen(client, homes):
    """ACCEPTANCE (G50, daemon half): the row says disabled; the door words
    it "turned off in Claude Desktop" and leaves it unticked."""
    _write(
        homes["appdata"] / "Claude" / "claude_desktop_config.json",
        json.dumps(
            {
                "mcpServers": {
                    "on-one": {"command": NO_SUCH, "args": ["a"]},
                    "off-one": {"command": NO_SUCH, "args": ["b"], "disabled": True},
                    "off-two": {"command": NO_SUCH, "args": ["c"], "enabled": False},
                    "said-false": {"command": NO_SUCH, "args": ["d"], "disabled": False},
                }
            }
        ),
    )
    platform = client.app.state.platform
    servers = _servers(_sources(client)["claude-desktop"])
    assert {n: s["disabled"] for n, s in servers.items()} == {
        "on-one": False,
        "off-one": True,
        "off-two": True,
        "said-false": False,
    }
    # The press is the choice: a disabled server ticked anyway is added like
    # any other, and "disabled" is not a key of the stored pack.
    res = client.post("/mcp/import/apply", json={"source": "claude-desktop", "names": ["off-one"]})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["off-one"]
    entry = next(s for s in platform.config.mcp_servers if s["name"] == "off-one")
    assert "disabled" not in entry and "enabled" not in entry


def test_codex_enabled_false_and_a_claude_code_project_disable_list_read_as_off(client, homes):
    user = homes["user"]
    _write(
        user / ".codex" / "config.toml",
        f'[mcp_servers.live]\ncommand = "{NO_SUCH}"\n\n[mcp_servers.parked]\ncommand = "{NO_SUCH}"\nenabled = false\n',
    )
    _write(
        user / ".claude.json",
        json.dumps(
            {
                "projects": {
                    "C:\\work\\app": {
                        "mcpServers": {
                            "kept": {"command": NO_SUCH},
                            "muted": {"command": NO_SUCH},
                        },
                        "disabledMcpServers": ["muted"],
                    }
                }
            }
        ),
    )
    rows = _sources(client)
    assert {n: s["disabled"] for n, s in _servers(rows["codex"]).items()} == {"live": False, "parked": True}
    assert {n: s["disabled"] for n, s in _servers(rows["claude-code"]).items()} == {"kept": False, "muted": True}


def test_packspec_disabled_is_a_preview_fact_never_a_stored_one():
    spec = PackSpec(name="x", command="npx", args=["y"], disabled=True)
    assert spec.preview_row()["disabled"] is True
    assert "disabled" not in spec.to_config()
    assert PackSpec(name="x", command="npx").preview_row()["disabled"] is False
    # Only a real True (or the word) counts.
    assert PackSpec(name="x", disabled="yes").disabled is False  # type: ignore[arg-type]
    assert parse_any(json.dumps({"mcpServers": {"s": {"command": "x", "disabled": "true"}}}))[0].disabled is True


# --------------------------------------------------------------------------- #
# G50: Claude Code plugins the user INSTALLED
# --------------------------------------------------------------------------- #
def _plant_plugins(user: Path) -> dict[str, Path]:
    claude = user / ".claude"
    plugins = claude / "plugins"
    on_root = plugins / "cache" / "mkt" / "helper" / "1.0.0"
    off_root = plugins / "cache" / "mkt" / "quiet" / "2.0.0"
    # The marketplace catalogue holds plugins NOT installed: never listed.
    _write(
        plugins / "marketplaces" / "mkt" / "plugins" / "not-installed" / ".mcp.json",
        json.dumps({"stranger": {"command": NO_SUCH}}),
    )
    # An installed plugin: a bare-map .mcp.json (as the official plugins ship
    # it) with a ${CLAUDE_PLUGIN_ROOT} path, and a manifest path that climbs
    # out of the plugin (never read).
    _write(
        on_root / ".mcp.json",
        json.dumps({"helper-tool": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]}}),
    )
    _write(plugins / "cache" / "mkt" / "escape.json", json.dumps({"mcpServers": {"escaped": {"command": NO_SUCH}}}))
    _write(on_root / ".claude-plugin" / "plugin.json", json.dumps({"name": "helper", "mcpServers": "../../escape.json"}))
    # A second installed plugin, switched off in Claude Code, its server
    # declared inline in the manifest.
    _write(
        off_root / ".claude-plugin" / "plugin.json",
        json.dumps({"name": "quiet", "mcpServers": {"quiet-tool": {"type": "http", "url": "https://quiet.example.com/mcp"}}}),
    )
    record = _write(
        plugins / "installed_plugins.json",
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "helper@mkt": [{"scope": "user", "installPath": str(on_root), "version": "1.0.0"}],
                    "quiet@mkt": [{"scope": "user", "installPath": str(off_root), "version": "2.0.0"}],
                },
            }
        ),
    )
    _write(claude / "settings.json", json.dumps({"enabledPlugins": {"helper@mkt": True, "quiet@mkt": False}}))
    return {"record": record, "on_root": on_root, "off_root": off_root}


def test_installed_claude_code_plugins_list_their_servers_and_respect_enabled_plugins(client, homes):
    planted = _plant_plugins(homes["user"])
    rows = _sources(client)
    row = rows["claude-code-plugins"]
    assert row["label"] == "Claude Code plugins" and row["origin"] == str(planted["record"])
    assert row["readable"] is True
    servers = _servers(row)
    assert set(servers) == {"helper-tool", "quiet-tool"}, "installed plugins only; nothing climbs out"
    helper = servers["helper-tool"]
    assert helper["disabled"] is False and helper["origin"] == str(planted["on_root"] / ".mcp.json")
    assert helper["args"] == [f"{planted['on_root']}/server.js"], "the plugin folder is written in"
    quiet = servers["quiet-tool"]
    assert quiet["disabled"] is True and quiet["transport"] == "http"


def test_apply_from_the_plugins_source_adds_by_name_with_the_plugin_folder(client, homes):
    planted = _plant_plugins(homes["user"])
    platform = client.app.state.platform
    res = client.post("/mcp/import/apply", json={"source": "claude-code-plugins", "names": ["helper-tool"]})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["helper-tool"]
    entry = next(s for s in platform.config.mcp_servers if s["name"] == "helper-tool")
    assert entry["command"] == "node" and entry["args"] == [f"{planted['on_root']}/server.js"]


def test_no_plugin_record_is_one_quiet_unreadable_row_and_a_broken_one_names_the_file(client, homes):
    row = _sources(client)["claude-code-plugins"]
    assert row["readable"] is False and row["note"] == "" and row["servers"] == []
    _write(homes["user"] / ".claude" / "plugins" / "installed_plugins.json", "{not json at all")
    row = _sources(client)["claude-code-plugins"]
    assert row["readable"] is False and "installed_plugins.json" in row["note"] and "{" not in row["note"]


def test_the_plugin_record_follows_claude_config_dir(client, homes, tmp_path, monkeypatch):
    cfg = tmp_path / "cc-override"
    root = cfg / "plugins" / "cache" / "m" / "p" / "1"
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"over-tool": {"command": NO_SUCH}}}))
    _write(
        cfg / "plugins" / "installed_plugins.json",
        json.dumps({"version": 1, "plugins": {"p@m": {"installPath": str(root), "version": "1"}}}),
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    row = _sources(client)["claude-code-plugins"]
    assert row["origin"] == str(cfg / "plugins" / "installed_plugins.json")
    assert list(_servers(row)) == ["over-tool"], "the v1 record shape (one dict per plugin) reads too"


# --------------------------------------------------------------------------- #
# G50: already connected another way
# --------------------------------------------------------------------------- #
def test_a_server_connected_another_way_reads_as_already_here(client, homes, monkeypatch):
    from iron_jarvis.ltm.sources import CustomSourceStore

    platform = client.app.state.platform
    # A memory source by the same name (a brain served by that tool).
    CustomSourceStore(platform.engine).add("basic-memory", "markdown", path=str(homes["user"]))
    # A live Directory connection (Pixio by its env key, the registry's own rule).
    monkeypatch.setenv("PIXIO_API_KEY", "pixio-env-v13540")
    # A pack already added under a differently spelled name.
    assert client.post("/mcp/servers", json={"name": "brave_search", "command": NO_SUCH}).status_code == 200
    paste = json.dumps(
        {
            "mcpServers": {
                "basic_memory": {"command": NO_SUCH},
                "pixio": {"command": NO_SUCH},
                "brave-search": {"command": NO_SUCH},
                "fresh": {"command": NO_SUCH},
            }
        }
    )
    res = client.post("/mcp/import/preview", json={"text": paste})
    assert res.status_code == 200, res.text
    rows = {r["name"]: (r["exists"], r["exists_as"]) for r in res.json()["servers"]}
    assert rows == {
        "basic_memory": (True, "memory"),
        "pixio": (True, "app"),
        "brave-search": (True, "pack"),
        "fresh": (False, ""),
    }


def test_the_sources_route_reads_what_is_connected_once_not_once_per_file(client, homes, monkeypatch):
    from iron_jarvis.connectors import service

    calls: list[int] = []
    real = service.list_connectors

    def _count(platform):
        calls.append(1)
        return real(platform)

    monkeypatch.setattr(service, "list_connectors", _count)
    _write(homes["appdata"] / "Code" / "User" / "mcp.json", VSCODE_MCP_JSON)
    _write(homes["user"] / ".claude.json", json.dumps({"mcpServers": {"a": {"command": NO_SUCH}}}))
    assert client.get("/mcp/import/sources").status_code == 200
    assert len(calls) == 1


def test_a_gallery_that_cannot_be_read_leaves_the_packs_answering(client, homes, monkeypatch):
    from iron_jarvis.connectors import service

    def _boom(_platform):
        raise RuntimeError("gallery down")

    monkeypatch.setattr(service, "list_connectors", _boom)
    assert client.post("/mcp/servers", json={"name": "fetch", "command": NO_SUCH}).status_code == 200
    paste = json.dumps({"mcpServers": {"fetch": {"command": NO_SUCH}, "other": {"command": NO_SUCH}}})
    res = client.post("/mcp/import/preview", json={"text": paste})
    assert res.status_code == 200, res.text
    rows = {r["name"]: (r["exists"], r["exists_as"]) for r in res.json()["servers"]}
    assert rows == {"fetch": (True, "pack"), "other": (False, "")}


# --------------------------------------------------------------------------- #
# G49: one line about what each tool does
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("line", "blurb"),
    [
        # The Directory catalog's blurb, matched by package (version cut).
        ("npx -y @modelcontextprotocol/server-github@1.2.3", "Repos, issues, and pull requests."),
        ("uvx mcp-server-fetch", "Fetch & clean web pages."),
        # A package only the Tools catalog lists: its first sentence.
        ("npx -y @playwright/mcp@latest", "Drive a real browser — open pages, click, fill forms, screenshot."),
        ("uvx mcp-server-git --repository C:/repo", "Read the history, diffs, and branches of a local git repo."),
        # Unknown package, and a server reached by address: no claim.
        ("npx -y @someone/unknown-server", ""),
        ("https://example.com/mcp", ""),
    ],
)
def test_preview_rows_carry_the_catalogs_line_for_the_package(client, line, blurb):
    res = client.post("/mcp/import/preview", json={"text": line})
    assert res.status_code == 200, res.text
    (row,) = res.json()["servers"]
    assert row["blurb"] == blurb


def test_a_catalog_line_that_names_the_mechanism_is_never_a_blurb(client):
    """The Tools catalog's demo server describes itself as exercising "every
    MCP feature": no row says MCP (the G42 rule), so it gets no blurb and the
    door says where it came from instead."""
    (row,) = client.post("/mcp/import/preview", json={"text": "npx -y @modelcontextprotocol/server-everything"}).json()[
        "servers"
    ]
    assert row["blurb"] == ""


def test_scan_reads_the_plugin_source_by_key_and_never_a_path(homes):
    _plant_plugins(homes["user"])
    item = import_sources.read_source(None, "claude-code-plugins")
    assert item is not None and item.readable is True
    assert {s.name for s in item.specs} == {"helper-tool", "quiet-tool"}
    assert import_sources.read_source(None, str(homes["user"] / ".claude" / "plugins" / "installed_plugins.json")) is None
