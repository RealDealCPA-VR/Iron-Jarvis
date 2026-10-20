"""v1.343.0 ("add a pack in one paste"), lane G-paste: one parser for every
config dialect, a preview that never returns a value, an apply door that
adds through the ONE add path.

``mcp/import_config.parse_any`` turns pasted text into ``PackSpec`` rows:
a Claude Desktop / Claude Code block (``mcpServers``, per-project
``projects`` flattened with the project path as origin), a VS Code
``mcp.servers`` block (comments and trailing commas tolerated), a Codex
``[mcp_servers.<name>]`` TOML, a single server object, a command line, a
bare URL (``/sse`` → sse), an ``npx mcp-remote <url> --header`` line; and
``[]`` for prose. ``parse_file`` reads one file read-only and tells a
missing file from a malformed one by name, never by contents.

``POST /mcp/import/preview`` answers names only (env keys, secret keys,
header names) plus ``exists`` / ``runtime_ready`` (the catalog's own
resolver) / ``launcher_note``; ``POST /mcp/import/apply`` re-parses the
text server-side and adds every chosen pack through ``add_mcp_pack`` — the
function ``POST /mcp/servers`` delegates to — so the token lands in the
vault and never in config.toml, an existing name is skipped unless
``replace``, and the typed overrides fill the keys the paste did not carry.

Every secret test plants a VALUE and asserts it is absent from the answer
text, the config object and the config.toml bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.import_config import (
    NAME_MAX,
    NAME_RX,
    PackSpec,
    parse_any,
    parse_file,
    safe_name,
    strip_json_comments,
)

#: A launcher that cannot exist: the add door's best-effort live load fails
#: at once (no child, no network) and the row is persisted exactly as it
#: would be for a pack whose command is installed later.
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1343g"
TOKEN = "ghp_x"
HEADER_TOKEN = "hdr-SECRET-q4"


def _desktop_block(command: str = "npx") -> str:
    return json.dumps(
        {
            "mcpServers": {
                "github": {
                    "command": command,
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": TOKEN, "GITHUB_HOST": "github.com"},
                },
                "files": {
                    "command": command,
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users"],
                },
            }
        }
    )


CLAUDE_JSON = json.dumps(
    {
        "numStartups": 3,
        "mcpServers": {"top": {"command": "uvx", "args": ["mcp-server-git"]}},
        "projects": {
            "C:\\work\\app": {
                "allowedTools": [],
                "mcpServers": {
                    "notion": {
                        "type": "http",
                        "url": "https://mcp.notion.com/mcp",
                        "headers": {"Authorization": "Bearer " + TOKEN},
                    }
                },
            },
            "C:\\work\\other": {"allowedTools": []},
        },
    }
)

VSCODE_SETTINGS = """{
  // editor settings above
  "editor.fontSize": 14,
  "mcp": {
    "servers": {
      "fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"],},
      "docs": {"type": "sse", "url": "https://docs.example.com/sse"},
    },
  },
}
"""

CODEX_TOML = """model = "o3"

[mcp_servers.git]
command = "uvx"
args = ["mcp-server-git"]

[mcp_servers.git.env]
GIT_TOKEN = "abc"

[mcp_servers.hosted]
url = "https://example.com/mcp"
"""


# --------------------------------------------------------------------------- #
# parse_any: every door's shape, one table.
# --------------------------------------------------------------------------- #
def test_a_claude_desktop_block_yields_both_servers_with_the_env_in_memory():
    specs = parse_any(_desktop_block())
    assert [s.name for s in specs] == ["github", "files"]
    gh, fs = specs
    assert gh.transport == "stdio" and gh.command == "npx"
    assert gh.args == ["-y", "@modelcontextprotocol/server-github"]
    assert gh.env == {"GITHUB_TOKEN": TOKEN, "GITHUB_HOST": "github.com"}, "values ride the spec in memory"
    assert fs.env == {} and fs.args[-1] == "C:/Users"
    assert gh.source == "paste" and gh.origin == "paste"
    # The preview row of the same spec lists KEYS only.
    row = gh.preview_row()
    assert row["env_keys"] == ["GITHUB_HOST"] and row["secret_keys"] == ["GITHUB_TOKEN"]
    assert TOKEN not in json.dumps(row)


def test_a_claude_json_flattens_per_project_servers_with_the_project_path_as_origin():
    specs = parse_any(CLAUDE_JSON)
    by = {s.name: s for s in specs}
    assert set(by) == {"top", "notion"}
    assert by["top"].origin == "paste"
    assert by["notion"].origin == "C:\\work\\app"
    assert by["notion"].transport == "http"
    assert by["notion"].url == "https://mcp.notion.com/mcp"
    assert by["notion"].headers == {"Authorization": "Bearer " + TOKEN}
    assert by["notion"].preview_row()["header_names"] == ["Authorization"]


def test_a_vscode_settings_block_with_comments_and_trailing_commas_parses():
    specs = parse_any(VSCODE_SETTINGS)
    by = {s.name: s for s in specs}
    assert set(by) == {"fetch", "docs"}
    assert by["fetch"].command == "uvx" and by["fetch"].args == ["mcp-server-fetch"]
    assert by["docs"].transport == "sse" and by["docs"].url == "https://docs.example.com/sse"


def test_a_bare_vscode_servers_map_parses_too():
    specs = parse_any('{"servers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}}')
    assert [(s.name, s.command) for s in specs] == [("time", "uvx")]


def test_a_codex_toml_parses_stdio_and_url_tables():
    specs = parse_any(CODEX_TOML)
    by = {s.name: s for s in specs}
    assert set(by) == {"git", "hosted"}
    assert by["git"].command == "uvx" and by["git"].args == ["mcp-server-git"]
    assert by["git"].env == {"GIT_TOKEN": "abc"}
    assert by["hosted"].transport == "http" and by["hosted"].url == "https://example.com/mcp"


def test_a_single_server_object_is_one_pack_named_after_its_package():
    specs = parse_any('{"command": "npx", "args": ["-y", "@scope/pkg", "--flag"]}')
    assert len(specs) == 1
    assert specs[0].name == "pkg"
    assert specs[0].command == "npx" and specs[0].args == ["-y", "@scope/pkg", "--flag"]
    (url_spec,) = parse_any('{"url": "https://mcp.linear.app/mcp"}')
    assert url_spec.transport == "http" and url_spec.name == "linear"


def test_a_server_object_with_a_name_key_keeps_it():
    (spec,) = parse_any('{"name": "My Notes", "command": "node", "args": ["server.js"]}')
    assert spec.name == "my-notes"


@pytest.mark.parametrize(
    "line, name, command, args",
    [
        ("npx -y @scope/pkg --flag", "pkg", "npx", ["-y", "@scope/pkg", "--flag"]),
        ("npx -y @modelcontextprotocol/server-github", "server-github", "npx", ["-y", "@modelcontextprotocol/server-github"]),
        ("uvx mcp-server-time", "mcp-server-time", "uvx", ["mcp-server-time"]),
        (
            "docker run -i --rm -e GITHUB_TOKEN ghcr.io/github/github-mcp-server:latest",
            "github-mcp-server",
            "docker",
            ["run", "-i", "--rm", "-e", "GITHUB_TOKEN", "ghcr.io/github/github-mcp-server:latest"],
        ),
        ("node C:\\srv\\server.js", "server", "node", ["C:\\srv\\server.js"]),
        ("python -m my_server --port 3", "my_server", "python", ["-m", "my_server", "--port", "3"]),
        ('npx -y pkg "C:\\My Files"', "pkg", "npx", ["-y", "pkg", "C:\\My Files"]),
    ],
)
def test_a_command_line_is_a_stdio_pack(line, name, command, args):
    specs = parse_any(line)
    assert len(specs) == 1, specs
    spec = specs[0]
    assert (spec.name, spec.transport, spec.command, spec.args) == (name, "stdio", command, args)
    assert spec.env == {} and spec.url == ""


@pytest.mark.parametrize(
    "url, transport, name",
    [
        ("https://mcp.example.com/mcp", "http", "example"),
        ("https://example.com/sse", "sse", "example"),
        ("https://api.githubcopilot.com/mcp/", "http", "githubcopilot"),
        ("http://127.0.0.1:8123/mcp", "http", "host-127-0-0-1"),
    ],
)
def test_a_bare_url_is_an_http_pack_and_sse_when_the_path_says_so(url, transport, name):
    (spec,) = parse_any(url)
    assert spec.transport == transport
    assert spec.url == url
    assert spec.name == name
    assert spec.command == "" and spec.args == []


def test_an_mcp_remote_line_becomes_an_http_pack_with_its_headers():
    line = 'npx -y mcp-remote https://mcp.linear.app/mcp --header "Authorization: Bearer ' + TOKEN + '"'
    (spec,) = parse_any(line)
    assert spec.transport == "http"
    assert spec.url == "https://mcp.linear.app/mcp"
    assert spec.headers == {"Authorization": "Bearer " + TOKEN}
    assert spec.command == "" and spec.args == [], "mcp-remote is not launched; the URL is called"
    assert spec.name == "linear"
    assert spec.preview_row()["header_names"] == ["Authorization"]
    # Without -y, with --transport, with a bare URL ending in /sse.
    (sse,) = parse_any("npx mcp-remote https://x.example.com/sse")
    assert sse.transport == "sse"
    (forced,) = parse_any("npx mcp-remote https://x.example.com/mcp --transport sse-only")
    assert forced.transport == "sse"


def test_an_mcp_remote_entry_inside_a_config_block_stays_a_stdio_pack():
    """Inside a config the user chose mcp-remote on purpose (Claude Desktop
    cannot speak http itself); only a pasted LINE is rewritten."""
    block = json.dumps({"mcpServers": {"lin": {"command": "npx", "args": ["mcp-remote", "https://mcp.linear.app/mcp"]}}})
    (spec,) = parse_any(block)
    assert spec.transport == "stdio" and spec.command == "npx"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n  ",
        "hello there how are you",
        "{not json",
        "Install the server and then restart the app.",
        "see https://example.com for details",
        '{"editor.fontSize": 14}',
        '{"mcpServers": "nope"}',
        "[tool.poetry]\nname = 'x'\n",
        "42",
        "[]",
    ],
)
def test_garbage_is_an_empty_list_never_a_raise(text):
    assert parse_any(text) == []


def test_parse_any_never_raises_on_odd_input():
    assert parse_any(None) == []  # type: ignore[arg-type]
    assert parse_any(12) == []  # type: ignore[arg-type]
    assert parse_any("\ufeff" + _desktop_block()) != []


def test_iron_jarvis_own_server_entries_are_dropped():
    block = json.dumps(
        {
            "mcpServers": {
                "iron-jarvis": {"command": "C:/Apps/Iron Jarvis/resources/daemon/ironjarvis.exe", "args": ["mcp-stdio"]},
                "shim": {"command": "python", "args": ["-m", "iron_jarvis.mcpserver.stdio_shim"]},
                "inert": {"command": "ironjarvis", "args": ["claude-inert-mcp", "m.json"]},
                "keep": {"command": "npx", "args": ["-y", "pkg"]},
            }
        }
    )
    assert [s.name for s in parse_any(block)] == ["keep"]
    assert parse_any("ironjarvis.exe mcp-stdio") == []


def test_two_packs_from_one_paste_never_share_a_name():
    specs = parse_any("npx -y @a/server-x\nnpx -y @b/server-x\nnpx -y @c/server-x")
    assert [s.name for s in specs] == ["server-x", "server-x-2", "server-x-3"]
    long = "x" * NAME_MAX
    specs = parse_any(json.dumps({"mcpServers": {long: {"command": "npx"}, long + "y": {"command": "npx"}}}))
    assert specs[0].name == long and len(specs[1].name) <= NAME_MAX and specs[1].name.endswith("-2")


def test_a_claude_code_mcp_json_type_words_pick_the_transport():
    block = json.dumps(
        {
            "mcpServers": {
                "a": {"type": "sse", "url": "https://a.example.com/events"},
                "b": {"type": "streamable-http", "url": "https://b.example.com/mcp"},
                "c": {"type": "stdio", "command": "npx", "args": ["x"]},
                "d": {"url": "https://d.example.com/mcp"},
            }
        }
    )
    by = {s.name: s.transport for s in parse_any(block)}
    assert by == {"a": "sse", "b": "http", "c": "stdio", "d": "http"}


def test_strip_json_comments_keeps_a_slash_slash_inside_a_string():
    text = '{"url": "https://x.example.com/mcp", // comment\n "a": /* b */ 1}'
    cleaned = strip_json_comments(text)
    assert json.loads(cleaned) == {"url": "https://x.example.com/mcp", "a": 1}


# --------------------------------------------------------------------------- #
# safe_name: the one name rule.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, want",
    [
        ("@modelcontextprotocol/server-github", "modelcontextprotocol-server-github"),
        ("my.notes.server", "my-notes-server"),
        ("My Notes (local)", "my-notes-local"),
        ("123-brave", "brave"),
        ("  --9 lead  ", "lead"),
        ("x" * 60, "x" * NAME_MAX),
        ("a" * 39 + "-" + "b" * 20, "a" * 39),
        ("UPPER_case", "upper_case"),
        ("", ""),
        ("12345", ""),
        (None, ""),
    ],
)
def test_safe_name_cases(raw, want):
    got = safe_name(raw)
    assert got == want
    assert got == "" or NAME_RX.match(got), got


# --------------------------------------------------------------------------- #
# parse_file: read-only, by name, never by contents.
# --------------------------------------------------------------------------- #
def test_parse_file_missing_file_is_unreadable_with_no_note(tmp_path):
    assert parse_file(tmp_path / "nowhere.json") == ([], False, "")
    assert parse_file(tmp_path) == ([], False, "")  # a directory is not a file
    assert parse_file("") == ([], False, "")
    assert parse_file(None) == ([], False, "")


def test_parse_file_malformed_file_names_the_file_never_its_contents(tmp_path):
    bad = tmp_path / "claude_desktop_config.json"
    bad.write_text('{"mcpServers": {"gh": {"command": "npx", "env": {"GITHUB_TOKEN": "' + TOKEN + '"', encoding="utf-8")
    specs, readable, note = parse_file(bad)
    assert specs == [] and readable is False
    assert "claude_desktop_config.json" in note
    assert TOKEN not in note and "mcpServers" not in note
    assert "\n" not in note


def test_parse_file_reads_a_desktop_config_with_its_source_and_origin(tmp_path):
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()
    cfg.write_bytes(b"\xef\xbb\xbf" + _desktop_block().encode("utf-8"))
    specs, readable, note = parse_file(cfg)
    assert readable is True and note == ""
    assert [s.name for s in specs] == ["github", "files"]
    assert specs[0].source == "claude-desktop"
    assert specs[0].origin == str(cfg)
    assert specs[0].env["GITHUB_TOKEN"] == TOKEN


def test_parse_file_claude_json_per_project_origin_is_the_project_path(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(CLAUDE_JSON, encoding="utf-8")
    specs, readable, _ = parse_file(cfg)
    assert readable
    by = {s.name: s for s in specs}
    assert by["top"].origin == str(cfg) and by["top"].source == "claude-code"
    assert by["notion"].origin == "C:\\work\\app" and by["notion"].source == "claude-code"


def test_parse_file_codex_toml_and_vscode_settings(tmp_path):
    codex = tmp_path / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text(CODEX_TOML, encoding="utf-8")
    specs, readable, _ = parse_file(codex)
    assert readable and {s.name for s in specs} == {"git", "hosted"}
    assert specs[0].source == "codex"
    vscode = tmp_path / "Code" / "User" / "settings.json"
    vscode.parent.mkdir(parents=True)
    vscode.write_text(VSCODE_SETTINGS, encoding="utf-8")
    specs, readable, _ = parse_file(vscode)
    assert readable and {s.name for s in specs} == {"fetch", "docs"}
    assert specs[0].source == "vscode"
    cursor = tmp_path / ".cursor" / "mcp.json"
    cursor.parent.mkdir()
    cursor.write_text(_desktop_block(), encoding="utf-8")
    specs, readable, _ = parse_file(cursor)
    assert readable and specs[0].source == "cursor"


def test_parse_file_a_valid_file_with_no_servers_is_readable_and_empty(tmp_path):
    cfg = tmp_path / "settings.json"
    cfg.write_text('{\n  // nothing here\n  "editor.fontSize": 14,\n}\n', encoding="utf-8")
    assert parse_file(cfg) == ([], True, "")
    toml = tmp_path / "config.toml"
    toml.write_text('model = "o3"\n', encoding="utf-8")
    assert parse_file(toml) == ([], True, "")


def test_parse_file_refuses_a_file_over_the_cap_by_name(tmp_path, monkeypatch):
    """The cap's OWN outcome: the note says the file was not read. A removed
    cap check reads the truncated bytes and answers the malformed-file note
    instead (same ([], False, ...) shape, different words)."""
    import iron_jarvis.mcp.import_config as mod

    monkeypatch.setattr(mod, "FILE_CAP_BYTES", 64)
    big = tmp_path / "huge.json"
    big.write_text(_desktop_block(), encoding="utf-8")
    specs, readable, note = parse_file(big)
    assert specs == [] and readable is False
    assert "huge.json" in note and TOKEN not in note
    assert "was not read" in note and "larger than" in note
    # And a file whose first cap+1 bytes WOULD parse on their own is still
    # refused: the cap decides, not what the head happens to spell.
    head_parses = tmp_path / "padded.json"
    head_parses.write_text(json.dumps({"mcpServers": {"a": {"command": "npx"}}}) + "\n" * 200, encoding="utf-8")
    specs, readable, note = parse_file(head_parses)
    assert specs == [] and readable is False and "was not read" in note


def test_parse_file_decodes_bad_bytes_with_replacement_and_still_parses(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_bytes(b'{"mcpServers": {"a": {"command": "npx", "args": ["x\xff"]}}}')
    specs, readable, _ = parse_file(cfg)
    assert readable and specs[0].name == "a" and "\ufffd" in specs[0].args[0]


# --------------------------------------------------------------------------- #
# The routes, on the real app.
# --------------------------------------------------------------------------- #
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
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _toml_text(platform) -> str:
    return (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")


def _entry(platform, name: str) -> dict:
    for s in platform.config.mcp_servers:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no config entry named {name!r}")


def _names(platform) -> list[str]:
    return [s.get("name") for s in platform.config.mcp_servers]


def test_preview_lists_names_only_and_never_the_planted_token(client):
    res = client.post("/mcp/import/preview", json={"text": _desktop_block(NO_SUCH_COMMAND)})
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    body = res.json()
    assert body["unrecognised"] is False
    rows = {r["name"]: r for r in body["servers"]}
    assert set(rows) == {"github", "files"}
    gh = rows["github"]
    assert gh == {
        "name": "github",
        "transport": "stdio",
        "command": NO_SUCH_COMMAND,
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "url": "",
        "header_names": [],
        "env_keys": ["GITHUB_HOST"],
        "secret_keys": ["GITHUB_TOKEN"],
        "auth_set": False,
        "source": "paste",
        "origin": "paste",
        "exists": False,
        "runtime_ready": False,
        "launcher_note": f"needs {NO_SUCH_COMMAND}",
        # v1.354.0 (G49/G50, updated on purpose): switched off in its app?
        # connected another way? and the catalog's one line for the package.
        "disabled": False,
        "exists_as": "",
        "blurb": "Repos, issues, and pull requests.",
        # v1.360.0 (G49, added on purpose): what the row runs, read off its
        # masked command line (an unknown launcher is the program itself),
        # and an address row's host ("" for a stdio row).
        "program": NO_SUCH_COMMAND,
        "host": "",
    }
    assert "env" not in gh and "headers" not in gh
    # Nothing was added by a preview.
    assert client.app.state.platform.config.mcp_servers == []


def test_preview_header_values_never_come_back(client):
    line = 'npx -y mcp-remote https://mcp.linear.app/mcp --header "Authorization: Bearer ' + TOKEN + '"'
    res = client.post("/mcp/import/preview", json={"text": line})
    assert res.status_code == 200
    assert TOKEN not in res.text
    (row,) = res.json()["servers"]
    assert row["transport"] == "http" and row["url"] == "https://mcp.linear.app/mcp"
    assert row["header_names"] == ["Authorization"]
    assert row["runtime_ready"] is True and row["launcher_note"] == "", "an http pack launches nothing"


def test_preview_reports_exists_for_a_name_already_configured(client):
    assert client.post("/mcp/servers", json={"name": "github", "command": NO_SUCH_COMMAND}).status_code == 200
    res = client.post("/mcp/import/preview", json={"text": _desktop_block(NO_SUCH_COMMAND)})
    rows = {r["name"]: r["exists"] for r in res.json()["servers"]}
    assert rows == {"github": True, "files": False}


def test_preview_unrecognised_is_true_for_prose_and_false_for_empty(client):
    res = client.post("/mcp/import/preview", json={"text": "hello there how are you"})
    assert res.json() == {"servers": [], "unrecognised": True}
    res = client.post("/mcp/import/preview", json={"text": "   "})
    assert res.json() == {"servers": [], "unrecognised": False}


def test_preview_launcher_note_names_the_runtime_in_words(client, monkeypatch):
    monkeypatch.setattr(agents_routes, "launcher_ready", lambda cmd: False)
    res = client.post("/mcp/import/preview", json={"text": "npx -y pkg\nuvx mcp-server-git\ndocker run img"})
    notes = {r["name"]: r["launcher_note"] for r in res.json()["servers"]}
    assert notes == {"pkg": "needs npx (Node.js)", "mcp-server-git": "needs uvx (uv)", "img": "needs docker (Docker Desktop)"}


def test_runtime_ready_is_the_catalogs_own_resolver(client, monkeypatch):
    """One resolver for both doors: a swapped `launcher_ready` changes the
    preview AND the catalog, so neither can hold a private copy."""
    seen: list[str] = []

    def ready(command: str) -> bool:
        seen.append(command)
        return True

    monkeypatch.setattr(agents_routes, "launcher_ready", ready)
    res = client.post("/mcp/import/preview", json={"text": "uvx mcp-server-git"})
    (row,) = res.json()["servers"]
    assert row["runtime_ready"] is True and row["launcher_note"] == ""
    assert seen == ["uvx"]
    cat = client.get("/mcp/catalog").json()["catalog"]
    assert cat and all(r["runtime_ready"] is True for r in cat)
    assert len(seen) > 1, "the catalog read the same function"


def test_apply_adds_the_chosen_packs_with_the_token_in_the_vault(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/import/apply",
        json={"text": _desktop_block(NO_SUCH_COMMAND), "names": ["github"], "overrides": {}, "auto_approve": False, "replace": False},
    )
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    body = res.json()
    assert body["skipped"] == []
    (added,) = body["added"]
    assert added["name"] == "github"
    assert added["tools_loaded"] == 0
    assert added["state"] == "failed"
    assert "did not start" in (added["note"] or "")
    # Only the chosen pack was added, with its secret split exactly as the
    # POST door splits it.
    assert _names(platform) == ["github"]
    assert _entry(platform, "github") == {
        "name": "github",
        "command": NO_SUCH_COMMAND,
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_HOST": "github.com"},
        "env_secrets": {"GITHUB_TOKEN": "mcp.github.GITHUB_TOKEN"},
    }
    assert platform.secrets.get("mcp.github.GITHUB_TOKEN") == TOKEN
    assert TOKEN not in json.dumps(platform.config.mcp_servers)
    assert TOKEN not in _toml_text(platform)
    # And the GET row says key names only.
    row = next(r for r in client.get("/mcp/servers").json()["servers"] if r["name"] == "github")
    assert row["env_keys"] == ["GITHUB_HOST"] and row["secret_keys"] == ["GITHUB_TOKEN"]
    assert TOKEN not in json.dumps(row)


def test_apply_goes_through_the_one_add_door(client, monkeypatch):
    """The import door must not grow its own add path: a swapped
    `add_mcp_pack` is what the apply route calls, and its refusal is the
    skip's words."""
    calls: list[tuple[str, str, list[str]]] = []

    def spy(d, body):
        calls.append((body.name, body.command, list(body.args)))
        raise HTTPException(status_code=400, detail="spy refused")

    monkeypatch.setattr(agents_routes, "add_mcp_pack", spy)
    res = client.post("/mcp/import/apply", json={"text": "npx -y @scope/pkg", "names": ["pkg"]})
    assert res.status_code == 200, res.text
    assert res.json() == {"added": [], "skipped": [{"name": "pkg", "why": "spy refused"}]}
    assert calls == [("pkg", "npx", ["-y", "@scope/pkg"])]
    assert client.app.state.platform.config.mcp_servers == []


def test_apply_skips_an_existing_name_unless_replace(client):
    platform = client.app.state.platform
    assert client.post("/mcp/servers", json={"name": "github", "command": NO_SUCH_COMMAND, "args": ["old"]}).status_code == 200
    body = {"text": _desktop_block(NO_SUCH_COMMAND), "names": ["github", "files"]}
    res = client.post("/mcp/import/apply", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["skipped"] == [{"name": "github", "why": "already added"}]
    assert [a["name"] for a in out["added"]] == ["files"]
    assert _entry(platform, "github")["args"] == ["old"], "the original is kept"
    assert platform.secrets.get("mcp.github.GITHUB_TOKEN") is None
    # replace: the old row goes through the one remove path, the new one lands.
    res = client.post("/mcp/import/apply", json={**body, "names": ["github"], "replace": True})
    assert res.status_code == 200, res.text
    assert res.json()["skipped"] == []
    assert _entry(platform, "github")["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert platform.secrets.get("mcp.github.GITHUB_TOKEN") == TOKEN
    assert _names(platform) == ["files", "github"]


def test_apply_respects_the_typed_overrides_for_a_missing_secret(client):
    platform = client.app.state.platform
    block = json.dumps({"mcpServers": {"gh": {"command": NO_SUCH_COMMAND, "args": ["x"], "env": {"GITHUB_TOKEN": ""}}}})
    preview = client.post("/mcp/import/preview", json={"text": block}).json()["servers"][0]
    assert preview["secret_keys"] == ["GITHUB_TOKEN"], "the key is named so the user can fill it"
    typed = "ghp_typed_SECRET"
    res = client.post(
        "/mcp/import/apply",
        json={"text": block, "names": ["gh"], "overrides": {"gh": {"GITHUB_TOKEN": typed, "EXTRA": "plain"}}},
    )
    assert res.status_code == 200, res.text
    assert typed not in res.text
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == typed
    assert _entry(platform, "gh")["env"] == {"EXTRA": "plain"}
    assert _entry(platform, "gh")["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}
    assert typed not in _toml_text(platform)


def test_apply_http_pack_puts_its_secret_header_in_the_vault(client, monkeypatch):
    platform = client.app.state.platform
    # No network: the live load is stubbed to "connected, no tools".
    monkeypatch.setattr(mcp_tools_mod, "mcp_tools", lambda *a, **k: [])
    line = 'npx -y mcp-remote https://mcp.linear.app/mcp --header "Authorization: Bearer ' + HEADER_TOKEN + '" --header "X-Client: ij"'
    res = client.post("/mcp/import/apply", json={"text": line, "names": ["linear"]})
    assert res.status_code == 200, res.text
    assert HEADER_TOKEN not in res.text
    (added,) = res.json()["added"]
    assert added["name"] == "linear"
    assert _entry(platform, "linear") == {
        "name": "linear",
        "transport": "http",
        "url": "https://mcp.linear.app/mcp",
        "headers": {"X-Client": "ij"},
        "header_secrets": {"Authorization": "mcp.linear.header.Authorization"},
    }
    assert platform.secrets.get("mcp.linear.header.Authorization") == "Bearer " + HEADER_TOKEN
    assert HEADER_TOKEN not in _toml_text(platform)
    # An override for an http pack fills a header (or the auth value).
    res = client.post(
        "/mcp/import/apply",
        json={"text": "https://mcp.notion.com/mcp", "names": ["notion"], "overrides": {"notion": {"auth": "tok-SECRET"}}},
    )
    assert res.status_code == 200, res.text
    assert "tok-SECRET" not in res.text
    assert _entry(platform, "notion")["auth"] == {"header": "Authorization", "format": "Bearer {value}", "secret": "mcp.notion.auth.value"}
    assert platform.secrets.get("mcp.notion.auth.value") == "tok-SECRET"


def test_apply_names_not_in_the_paste_are_skipped_and_empty_bodies_refused(client):
    res = client.post("/mcp/import/apply", json={"text": "npx -y pkg", "names": ["ghost"]})
    assert res.status_code == 200
    assert res.json() == {"added": [], "skipped": [{"name": "ghost", "why": "not in the paste"}]}
    assert client.post("/mcp/import/apply", json={"text": "npx -y pkg", "names": []}).status_code == 400
    assert client.post("/mcp/import/apply", json={"text": "", "names": ["pkg"]}).status_code == 400
    assert client.app.state.platform.config.mcp_servers == []


def test_apply_auto_approve_rides_the_add(client):
    res = client.post("/mcp/import/apply", json={"text": "npx -y pkg", "names": ["pkg"], "auto_approve": True})
    assert res.status_code == 200, res.text
    assert _entry(client.app.state.platform, "pkg").get("auto_approve") is True


def test_post_mcp_servers_response_is_unchanged(client):
    """The one add door's answer, pinned byte for byte (the Tools page and
    the catalog door read it)."""
    res = client.post(
        "/mcp/servers",
        json={"name": "pinned", "command": NO_SUCH_COMMAND, "args": ["a"], "env": {"API_KEY": "k-SECRET", "MODE": "x"}},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {
        "name": "pinned",
        "added": True,
        "tools_loaded": 0,
        "auto_approve": False,
        "note": res.json()["note"],
        "env_keys": ["MODE"],
        "secret_keys": ["API_KEY"],
        "transport": "stdio",
        "header_names": [],
        "auth_set": False,
    }
    assert res.json()["note"].startswith("saved, but it did not start:")
    assert "k-SECRET" not in res.text
    # And the DELETE answer, which the import door's replace reuses.
    res = client.delete("/mcp/servers/pinned")
    assert res.json() == {"removed": "pinned", "tools_unloaded": 0, "secrets_removed": 1, "manifest_removed": False}
    assert client.delete("/mcp/servers/pinned").status_code == 404


def test_replace_with_a_shape_the_add_door_refuses_keeps_the_original_and_its_vault_value(client):
    """Reviewer round 1: `_apply_specs` removed the old pack BEFORE building
    the body, so a parsed entry with a url the add door refuses answered a
    'skipped' while config.mcp_servers was [] and the vault row was gone.
    A skip must leave the original untouched."""
    platform = client.app.state.platform
    old = "old-SECRET-v"
    res = client.post(
        "/mcp/servers",
        json={"name": "notion", "command": NO_SUCH_COMMAND, "args": ["keep"], "env": {"API_KEY": old}},
    )
    assert res.status_code == 200, res.text
    assert platform.secrets.get("mcp.notion.API_KEY") == old
    before = json.loads(json.dumps(platform.config.mcp_servers))
    block = json.dumps({"mcpServers": {"notion": {"url": "localhost:3000/mcp"}}})
    preview = client.post("/mcp/import/preview", json={"text": block}).json()["servers"]
    assert preview and preview[0]["exists"] is True and preview[0]["transport"] == "http"
    res = client.post("/mcp/import/apply", json={"text": block, "names": ["notion"], "replace": True})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["added"] == []
    assert out["skipped"] == [{"name": "notion", "why": "the url must start with http:// or https://"}]
    assert platform.config.mcp_servers == before, "the original row is kept"
    assert _entry(platform, "notion")["args"] == ["keep"]
    assert platform.secrets.get("mcp.notion.API_KEY") == old, "and its vault value"
    assert old not in res.text


def test_replace_whose_vault_refuses_the_new_values_puts_the_old_pack_back(client, monkeypatch):
    """The one refusal a replace cannot check ahead: the vault refusing the
    NEW values after the old pack is gone. The old row and its vault value
    come back, and the skip says so (key names only)."""
    platform = client.app.state.platform
    old = "old-SECRET-w"
    assert (
        client.post(
            "/mcp/servers",
            json={"name": "github", "command": NO_SUCH_COMMAND, "args": ["keep"], "env": {"GITHUB_TOKEN": old}},
        ).status_code
        == 200
    )
    real_set = platform.secrets.set

    def refusing_set(name, value, *a, **k):
        if value == TOKEN:
            raise RuntimeError("vault closed")
        return real_set(name, value, *a, **k)

    monkeypatch.setattr(platform.secrets, "set", refusing_set)
    res = client.post(
        "/mcp/import/apply",
        json={"text": _desktop_block(NO_SUCH_COMMAND), "names": ["github"], "replace": True},
    )
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text and old not in res.text
    out = res.json()
    assert out["added"] == []
    (skip,) = out["skipped"]
    assert skip["name"] == "github"
    assert skip["why"].startswith("the vault could not store GITHUB_TOKEN")
    assert "the earlier extension was put back" in skip["why"]  # v1.360.0 (G76, moved on purpose)
    assert "could not be" not in skip["why"], "the old value was re-stored"
    assert _entry(platform, "github")["args"] == ["keep"]
    assert _entry(platform, "github")["env_secrets"] == {"GITHUB_TOKEN": "mcp.github.GITHUB_TOKEN"}
    assert platform.secrets.get("mcp.github.GITHUB_TOKEN") == old
    assert _names(platform) == ["github"]
    # The put-back is persisted, not only in memory.
    assert "github" in _toml_text(platform) and old not in _toml_text(platform)


def test_replace_put_back_names_the_key_it_could_not_restore(client, monkeypatch):
    """A vault that refuses EVERY write after the remove: the row comes back,
    the value cannot, and the skip names the KEY (never the value)."""
    platform = client.app.state.platform
    old = "old-SECRET-x"
    assert (
        client.post(
            "/mcp/servers",
            json={"name": "github", "command": NO_SUCH_COMMAND, "env": {"GITHUB_TOKEN": old}},
        ).status_code
        == 200
    )

    def closed(*a, **k):
        raise RuntimeError("vault closed")

    monkeypatch.setattr(platform.secrets, "set", closed)
    res = client.post(
        "/mcp/import/apply",
        json={"text": _desktop_block(NO_SUCH_COMMAND), "names": ["github"], "replace": True},
    )
    assert res.status_code == 200, res.text
    (skip,) = res.json()["skipped"]
    assert "the earlier extension was put back" in skip["why"]  # v1.360.0 (G76, moved on purpose)
    assert "GITHUB_TOKEN could not be" in skip["why"]
    assert old not in res.text and TOKEN not in res.text
    assert _entry(platform, "github")["env_secrets"] == {"GITHUB_TOKEN": "mcp.github.GITHUB_TOKEN"}


def test_preview_masks_a_secret_carried_in_args_or_the_url(client):
    """A docker line with -e TOKEN=... and a url with ?api_key=... carry the
    value in the command text itself; the preview row masks it."""
    line = "docker run -e GITHUB_TOKEN=ghp_abcdef123456 ghcr.io/x/img"
    res = client.post("/mcp/import/preview", json={"text": line})
    assert res.status_code == 200, res.text
    assert "ghp_abcdef123456" not in res.text
    (row,) = res.json()["servers"]
    assert any("***" in a for a in row["args"])
    res = client.post("/mcp/import/preview", json={"text": "https://mcp.example.com/mcp?api_key=k-SECRET-99"})
    assert "k-SECRET-99" not in res.text
    (row,) = res.json()["servers"]
    assert row["url"].startswith("https://mcp.example.com/mcp?api_key=") and "***" in row["url"]


def test_the_spec_round_trips_through_to_config_and_back():
    spec = PackSpec(name="a", command="npx", args=["x"], env={"K": "v"}, cwd="C:/w")
    assert PackSpec.from_config(spec.to_config()) == PackSpec(
        name="a", command="npx", args=["x"], env={"K": "v"}, cwd="C:/w", source="form", origin="form"
    )
