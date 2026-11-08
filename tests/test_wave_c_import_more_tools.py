"""Wave C (readiness plan 0h.5, item 30): the Tools page's import door reads
the four coding tools Build › Code drives too — OpenCode, Grok, Antigravity
and Pi — read-only, names only, placeholders honoured, through the same
``PackSpec`` path the other sources take, and the dashboard lists each by
the tool's name.

Every file location and shape here was VERIFIED on the installed program (a
dev check, never a test): OpenCode's ``"mcp": {name: {type local|remote,
command: [..], environment, url, headers, enabled}}`` (its binary's own
schema doc), Grok's ``~/.grok/config.toml`` ``[mcp_servers.<name>]`` plus a
top-level ``disabled_mcp_servers`` list (its bundled user guide),
Antigravity's ``~/.gemini/config/mcp_config.json`` ``mcpServers`` (agy's
embedded doc) and Pi's ``<agent dir>/mcp.json`` / a project's ``.pi/mcp.json``
(its bundle: "Reads ~/.pi/agent/mcp.json and, in trusted projects,
.pi/mcp.json").

Isolation: the suite points OpenCode's config folder, Grok's and
Antigravity's homes and the import's Pi fallback at scratch places
(tests/conftest.py); every test here writes under those or under a scratch
HOME. No test reads the developer's real ``~/.config/opencode``, ``~/.grok``,
``~/.gemini`` or ``~/.pi``.
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
from iron_jarvis.hosted import antigravity as hosted_agy
from iron_jarvis.hosted import grok as hosted_grok
from iron_jarvis.hosted import pi as hosted_pi
from iron_jarvis.mcp import import_sources
from iron_jarvis.mcp.import_config import SOURCES, parse_any
from iron_jarvis.providers import opencode as providers_opencode

NO_SUCH = "ij-no-such-mcp-launcher-wave-c"
TOKEN = "ghp_SECRET_wave_c_never_in_an_answer"


@pytest.fixture
def homes(tmp_path, monkeypatch) -> dict[str, Path]:
    """A scratch HOME for every tool, plus each tool's own override pointed
    INSIDE it (the suite's session isolation already names scratch places;
    these make the test's own files the ones read)."""
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "CLAUDE_CODE_PLUGIN_CACHE_DIR",
                "GROK_HOME", "PI_CODING_AGENT_DIR", "OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    opencode_dir = user / ".config" / "opencode"
    grok_home = user / ".grok"
    agy_home = user / ".gemini" / "antigravity-cli"
    pi_dir = user / ".pi" / "agent"
    monkeypatch.setenv(providers_opencode.CONFIG_DIR_ENV, str(opencode_dir))
    monkeypatch.setenv(hosted_grok.HOME_ENV, str(grok_home))
    monkeypatch.setenv(hosted_agy.HOME_ENV, str(agy_home))
    monkeypatch.setenv(import_sources.PI_IMPORT_DIR_ENV, str(pi_dir))
    return {"user": user, "opencode": opencode_dir, "grok": grok_home, "agy": agy_home, "pi": pi_dir}


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
    assert TOKEN not in res.text, "a value never reaches the answer"
    rows = {r["source"]: r for r in res.json()["sources"]}
    assert len(rows) == len(res.json()["sources"]), "source keys stay unique"
    return rows


def _servers(row: dict) -> dict[str, dict]:
    return {s["name"]: s for s in row["servers"]}


def _activate_project(platform, root: Path) -> None:
    with session_scope(platform.engine) as db:
        db.add(Project(id="project-wave-c", name="Import test", root=str(root), status="active"))
        db.commit()
    platform.config.active_project_id = "project-wave-c"


# --------------------------------------------------------------------------- #
# The four files are known places, each named by the tool
# --------------------------------------------------------------------------- #
def test_the_four_tools_are_known_sources_named_by_the_tool(client, homes):
    rows = _sources(client)
    assert rows["opencode"]["label"] == "OpenCode"
    assert rows["grok"]["label"] == "Grok"
    assert rows["antigravity"]["label"] == "Antigravity"
    assert rows["pi"]["label"] == "Pi"
    # Each names its file even when it is missing (the user knows where to put one).
    assert rows["opencode"]["origin"] == str(homes["opencode"] / "opencode.jsonc")
    assert rows["grok"]["origin"] == str(homes["grok"] / "config.toml")
    assert rows["antigravity"]["origin"] == str(homes["user"] / ".gemini" / "config" / "mcp_config.json")
    assert rows["pi"]["origin"] == str(homes["pi"] / "mcp.json")
    for key in ("opencode", "grok", "antigravity", "pi"):
        assert rows[key]["readable"] is False and rows[key]["note"] == "" and rows[key]["servers"] == []
    # The dialect words are part of the parser's own list.
    assert {"opencode", "grok", "antigravity", "pi"} <= set(SOURCES)


# --------------------------------------------------------------------------- #
# OpenCode: its own shape — a command LIST, "environment", type local/remote
# --------------------------------------------------------------------------- #
OPENCODE_JSONC = """{
  // OpenCode's own file: comments, a $schema, "mcp" keyed by server name
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "playwright": {
      "type": "local",
      "command": ["%s", "-y", "@playwright/mcp"],
      "enabled": true,
      "environment": {"BROWSER": "chromium", "PLAYWRIGHT_TOKEN": "%s"}
    },
    "github": {
      "type": "remote",
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {"Authorization": "Bearer {env:GITHUB_TOKEN}"}
    },
    "parked": {"type": "local", "command": ["%s", "x"], "enabled": false},
    "nowhere": {"type": "remote"}
  }
}
""" % (NO_SUCH, TOKEN, NO_SUCH)


def test_opencode_servers_are_read_in_their_own_shape(client, homes):
    path = _write(homes["opencode"] / "opencode.jsonc", OPENCODE_JSONC)
    row = _sources(client)["opencode"]
    assert row["readable"] is True and row["origin"] == str(path)
    servers = _servers(row)
    assert set(servers) == {"playwright", "github", "parked"}, "a remote server with no address is nothing to reach"
    pw = servers["playwright"]
    assert pw["source"] == "opencode" and pw["transport"] == "stdio"
    # The command LIST became command + args; "environment" became the env
    # (names only on the wire: the token's KEY is listed, its value never).
    assert pw["env_keys"] == ["BROWSER"] and pw["secret_keys"] == ["PLAYWRIGHT_TOKEN"]
    assert pw["disabled"] is False
    gh = servers["github"]
    assert gh["transport"] == "http" and gh["header_names"] == ["Authorization"]
    # OpenCode's own {env:NAME} placeholder is ASKED for, never copied.
    assert gh["asks"] == ["GITHUB_TOKEN"], gh
    assert servers["parked"]["disabled"] is True


def test_opencode_apply_rereads_the_file_and_vaults_the_token(client, homes):
    _write(homes["opencode"] / "opencode.jsonc", OPENCODE_JSONC)
    platform = client.app.state.platform
    res = client.post("/mcp/import/apply", json={"source": "opencode", "names": ["playwright"]})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    assert [a["name"] for a in res.json()["added"]] == ["playwright"]
    assert platform.secrets.get("mcp.playwright.PLAYWRIGHT_TOKEN") == TOKEN
    entry = next(s for s in platform.config.mcp_servers if s.get("name") == "playwright")
    assert entry["command"] == NO_SUCH and entry["args"] == ["-y", "@playwright/mcp"]
    assert entry["env"] == {"BROWSER": "chromium"}, "the token is in the vault, not the config"


def test_opencode_reads_the_sibling_config_json_too_and_a_project_file_while_a_project_is_active(
    client, homes, tmp_path
):
    _write(homes["opencode"] / "opencode.json", json.dumps({"mcp": {"global-one": {"type": "local", "command": [NO_SUCH]}}}))
    _write(homes["opencode"] / "config.json", json.dumps({"mcp": {"global-two": {"type": "local", "command": [NO_SUCH]}}}))
    rows = _sources(client)
    assert set(_servers(rows["opencode"])) == {"global-one", "global-two"}
    assert "opencode-project" not in rows, "no project: no file to name"
    root = tmp_path / "proj"
    _write(root / ".opencode" / "opencode.json", json.dumps({"mcp": {"proj-oc": {"type": "local", "command": [NO_SUCH]}}}))
    _activate_project(client.app.state.platform, root)
    rows = _sources(client)
    assert rows["opencode-project"]["label"] == "OpenCode (this project)"
    assert list(_servers(rows["opencode-project"])) == ["proj-oc"]
    res = client.post("/mcp/import/apply", json={"source": "opencode-project", "names": ["proj-oc"]})
    assert res.status_code == 200, res.text
    assert [a["name"] for a in res.json()["added"]] == ["proj-oc"]


def test_the_parser_reads_a_pasted_opencode_block_too():
    specs = {s.name: s for s in parse_any(OPENCODE_JSONC)}
    assert specs["playwright"].command == NO_SUCH and specs["playwright"].args == ["-y", "@playwright/mcp"]
    assert specs["github"].transport == "http" and specs["github"].url.startswith("https://api.githubcopilot.com")
    # {env:X} is a fill (asked for), the header blanked.
    assert specs["github"].headers == {"Authorization": ""}
    assert [f["where"] for f in specs["github"].fills] == ["header"]
    assert list(specs["github"].fills[0]["names"].values()) == ["GITHUB_TOKEN"]


def test_opencodes_file_placeholder_asks_by_the_files_name_and_reads_no_file(tmp_path):
    secret_file = tmp_path / "gh-token.txt"
    secret_file.write_text(TOKEN, encoding="utf-8")
    text = json.dumps({"mcp": {"gh": {"type": "remote", "url": "https://x.example/mcp",
                                      "headers": {"Authorization": "Bearer {file:%s}" % secret_file.as_posix()}}}})
    [spec] = parse_any(text)
    assert spec.headers == {"Authorization": ""}
    assert list(spec.fills[0]["names"].values()) == ["gh-token"]
    assert TOKEN not in json.dumps(spec.fills), "the file's contents are never read"


# --------------------------------------------------------------------------- #
# Grok: Codex-shaped TOML plus its own disabled list
# --------------------------------------------------------------------------- #
GROK_TOML = """
# a top-level key (above the first table, as Grok's docs show it)
disabled_mcp_servers = ["linear"]

[cli]
use_leader = false

[mcp_servers.github]
command = "%s"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_PERSONAL_ACCESS_TOKEN = "%s", GITHUB_HOST = "github.com" }

[mcp_servers.linear]
url = "https://mcp.linear.app/mcp"
headers = { Authorization = "Bearer ${LINEAR_TOKEN}" }

[mcp_servers.off]
command = "%s"
enabled = false
""" % (NO_SUCH, TOKEN, NO_SUCH)


def test_grok_servers_are_read_from_its_config_toml_with_its_off_list(client, homes):
    path = _write(homes["grok"] / "config.toml", GROK_TOML)
    row = _sources(client)["grok"]
    assert row["readable"] is True and row["origin"] == str(path)
    servers = _servers(row)
    assert set(servers) == {"github", "linear", "off"}
    gh = servers["github"]
    assert gh["source"] == "grok" and gh["secret_keys"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert gh["env_keys"] == ["GITHUB_HOST"] and gh["disabled"] is False
    # ``grok mcp disable`` writes the name into disabled_mcp_servers; an
    # ``enabled = false`` block is off too.
    assert servers["linear"]["disabled"] is True and servers["off"]["disabled"] is True
    # Grok's ${VAR} placeholders are asked for.
    assert servers["linear"]["asks"] == ["LINEAR_TOKEN"]


def test_grok_apply_and_a_projects_own_grok_file(client, homes, tmp_path):
    _write(homes["grok"] / "config.toml", GROK_TOML)
    platform = client.app.state.platform
    res = client.post("/mcp/import/apply", json={"source": "grok", "names": ["github"]})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    assert platform.secrets.get("mcp.github.GITHUB_PERSONAL_ACCESS_TOKEN") == TOKEN
    root = tmp_path / "repo"
    _write(root / ".grok" / "config.toml", '[mcp_servers.proj-grok]\ncommand = "%s"\n' % NO_SUCH)
    assert "grok-project" not in _sources(client)
    _activate_project(platform, root)
    rows = _sources(client)
    assert rows["grok-project"]["label"] == "Grok (this project)"
    assert list(_servers(rows["grok-project"])) == ["proj-grok"]


def test_the_grok_home_is_the_code_views_own(homes):
    """The import reads Grok's folder the way Build › Code does: the same
    ``hosted.grok.default_home`` (IRONJARVIS_GROK_HOME → GROK_HOME → ~/.grok)."""
    assert import_sources.grok_home() == Path(hosted_grok.default_home())
    assert import_sources.grok_home() == homes["grok"]


# --------------------------------------------------------------------------- #
# Antigravity: ~/.gemini/config/mcp_config.json
# --------------------------------------------------------------------------- #
AGY_MCP = json.dumps({
    "mcpServers": {
        "sqlite-helper": {"command": NO_SUCH, "args": ["/path/to/db"], "env": {"DB_READONLY": "true", "DB_KEY": TOKEN}},
        "remote-service": {"url": "https://mcp.example.com/mcp"},
        "legacy": {"serverUrl": "https://old.example.com/mcp", "disabled": True},
    }
})


def test_antigravity_servers_are_read_from_its_mcp_config(client, homes):
    path = _write(homes["user"] / ".gemini" / "config" / "mcp_config.json", AGY_MCP)
    row = _sources(client)["antigravity"]
    assert row["readable"] is True and row["origin"] == str(path)
    servers = _servers(row)
    assert set(servers) == {"sqlite-helper", "remote-service", "legacy"}
    assert servers["sqlite-helper"]["source"] == "antigravity"
    assert servers["sqlite-helper"]["secret_keys"] == ["DB_KEY"] and servers["sqlite-helper"]["env_keys"] == ["DB_READONLY"]
    assert servers["remote-service"]["transport"] == "http"
    # agy accepts the legacy ``serverUrl`` key too.
    assert servers["legacy"]["transport"] == "http" and servers["legacy"]["disabled"] is True
    res = client.post("/mcp/import/apply", json={"source": "antigravity", "names": ["sqlite-helper"]})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    assert client.app.state.platform.secrets.get("mcp.sqlite-helper.DB_KEY") == TOKEN


def test_the_antigravity_config_folder_is_the_sibling_of_the_code_views_home(client, homes, tmp_path, monkeypatch):
    """agy's ``antigravity-cli`` home and its ``config`` folder are siblings
    under ``.gemini``; the suite isolates the home, so the config folder
    follows the home's PARENT — never the developer's real ``~/.gemini``.
    The isolated home sits OUTSIDE the scratch HOME here, so a reading that
    fell back to ``~/.gemini/config`` would be told apart."""
    elsewhere = tmp_path / "elsewhere" / ".gemini"
    monkeypatch.setenv(hosted_agy.HOME_ENV, str(elsewhere / "antigravity-cli"))
    assert import_sources.antigravity_config_dir() == Path(hosted_agy.default_home()).parent / "config"
    assert import_sources.antigravity_config_dir() == elsewhere / "config"
    assert import_sources.antigravity_config_dir() != homes["user"] / ".gemini" / "config"
    _write(elsewhere / "config" / "mcp_config.json", json.dumps({"mcpServers": {"side": {"command": NO_SUCH}}}))
    _write(homes["user"] / ".gemini" / "config" / "mcp_config.json",
           json.dumps({"mcpServers": {"home-copy": {"command": NO_SUCH}}}))
    row = _sources(client)["antigravity"]
    assert row["origin"] == str(elsewhere / "config" / "mcp_config.json")
    assert list(_servers(row)) == ["side"], "the isolated home's sibling is read, never HOME's"


# --------------------------------------------------------------------------- #
# Pi: <agent dir>/mcp.json and a project's .pi/mcp.json
# --------------------------------------------------------------------------- #
PI_MCP = json.dumps({
    "mcpServers": {
        "filesystem": {"command": NO_SUCH, "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                       "env": {"FS_TOKEN": TOKEN}},
        "docs": {"url": "https://docs.example.com/mcp", "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"}},
        "quiet": {"command": NO_SUCH, "enabled": False},
    }
})


def test_pi_servers_are_read_from_its_agent_folder(client, homes):
    path = _write(homes["pi"] / "mcp.json", PI_MCP)
    row = _sources(client)["pi"]
    assert row["readable"] is True and row["origin"] == str(path)
    servers = _servers(row)
    assert set(servers) == {"filesystem", "docs", "quiet"}
    assert servers["filesystem"]["source"] == "pi" and servers["filesystem"]["secret_keys"] == ["FS_TOKEN"]
    assert servers["docs"]["transport"] == "http" and servers["docs"]["asks"] == ["DOCS_TOKEN"]
    assert servers["quiet"]["disabled"] is True
    res = client.post("/mcp/import/apply", json={"source": "pi", "names": ["filesystem"]})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    assert client.app.state.platform.secrets.get("mcp.filesystem.FS_TOKEN") == TOKEN


def test_a_projects_pi_file_is_listed_only_while_a_project_is_active(client, homes, tmp_path):
    root = tmp_path / "proj"
    _write(root / ".pi" / "mcp.json", json.dumps({"mcpServers": {"proj-pi": {"command": NO_SUCH}}}))
    assert "pi-project" not in _sources(client)
    _activate_project(client.app.state.platform, root)
    rows = _sources(client)
    assert rows["pi-project"]["label"] == "Pi (this project)"
    assert list(_servers(rows["pi-project"])) == ["proj-pi"]


def test_pis_folder_is_the_drivers_own_unless_only_the_imports_isolation_names_one(monkeypatch, tmp_path):
    """Pi's own variable wins (the hosted driver's rule, ``hosted.pi.agent_dir``);
    the import-only fallback stands in only when Pi's is unset — so the suite's
    isolation never changes what a Pi user's real setting means."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "theirs"))
    monkeypatch.setenv(import_sources.PI_IMPORT_DIR_ENV, str(tmp_path / "ours"))
    assert import_sources.pi_agent_dir() == hosted_pi.agent_dir() == tmp_path / "theirs"
    monkeypatch.delenv("PI_CODING_AGENT_DIR")
    assert import_sources.pi_agent_dir() == tmp_path / "ours"
    monkeypatch.delenv(import_sources.PI_IMPORT_DIR_ENV)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert import_sources.pi_agent_dir() == hosted_pi.agent_dir() == tmp_path / "home" / ".pi" / "agent"


def test_the_suite_never_points_the_pi_import_at_the_real_home():
    """The session fixture (tests/conftest.py) names a scratch folder for the
    import's Pi fallback — the one guard between ``scan()`` in any test and
    the developer's real ``~/.pi/agent/mcp.json``."""
    assert os.environ.get(import_sources.PI_IMPORT_DIR_ENV), "conftest sets the import's Pi folder"
    real = Path(os.path.expanduser("~")) / ".pi" / "agent"
    assert import_sources.pi_agent_dir() != real


# --------------------------------------------------------------------------- #
# A file pasted by its path is read in its own dialect
# --------------------------------------------------------------------------- #
def test_a_files_path_names_its_dialect():
    src = import_sources.parse_file.__module__  # the same module's rule
    from iron_jarvis.mcp.import_config import _source_for_path

    assert src == "iron_jarvis.mcp.import_config"
    assert _source_for_path(r"C:\Users\x\.grok\config.toml") == "grok"
    assert _source_for_path(r"C:\Users\x\.codex\config.toml") == "codex"
    assert _source_for_path(r"C:\Users\x\.gemini\config\mcp_config.json") == "antigravity"
    assert _source_for_path(r"C:\Users\x\.codeium\windsurf\mcp_config.json") == "windsurf"
    assert _source_for_path(r"C:\Users\x\.pi\agent\mcp.json") == "pi"
    assert _source_for_path(r"C:\Users\x\.config\opencode\opencode.jsonc") == "opencode"
    assert _source_for_path(r"C:\repo\.opencode\opencode.json") == "opencode"
