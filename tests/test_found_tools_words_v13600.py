"""v1.360.0 lane 10C (2), G49 — a found server says what it IS.

A server the catalog does not know read only "A tool from <app>" on the
import door (dashboard lib/addPackWords.rowAbout), because the preview row
carried nothing a person could read about it. The import already knows two
facts, and the preview row now carries them (``mcp/import_config``):

* ``program`` — a stdio row: what its command line runs, by its own name with
  its case kept (the package an npx/uvx line fetches, the image a docker line
  starts, the script node/python runs, else the program itself), read off the
  MASKED line so it never shows more than the row already does;
* ``host`` — an http/sse row: its address's host.

The pack NAME rule (``_name_from_command``) shares the one reader and must
give exactly the names it gave before.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.mcp import import_config as ic
from iron_jarvis.mcp.import_config import PackSpec, host_of, program_of


# The names the pre-v1.360.0 reader gave for these lines (checked against the
# HEAD implementation when the shared reader was introduced): unchanged.
NAME_TABLE = [
    ("npx", ["-y", "@modelcontextprotocol/server-filesystem@1.2.3", "."], "server-filesystem"),
    ("npx.cmd", ["-y", "-p", "x", "@scope/Thing"], "thing"),
    ("uvx", ["--from", "git+https://x", "mcp-server-git"], "mcp-server-git"),
    ("uv", ["run", "--directory", "C:/a", "server.py"], "server-py"),
    ("docker", ["run", "-i", "--rm", "-e", "TOKEN", "ghcr.io/github/github-mcp-server:latest"], "github-mcp-server"),
    ("node", ["C:/srv/Server.JS"], "server-js"),
    ("python", ["-m", "my_server"], "my_server"),
    ("C:\\Tools\\QuickBooks-Desktop-MCP.EXE", [], "quickbooks-desktop-mcp"),
    ("/usr/local/bin/weird.server.cmd", [], "weird-server"),
    ("SERVER.CMD", [], "server"),
    ("server.js.cmd", [], "server"),
    ("bunx", ["Some-Pkg@latest"], "some-pkg-latest"),
]


@pytest.mark.parametrize("command, args, name", NAME_TABLE)
def test_the_pack_name_rule_is_unchanged(command, args, name):
    assert ic._name_from_command(command, args) == name


@pytest.mark.parametrize(
    "command, args, program",
    [
        ("C:\\Tools\\QuickBooks-Desktop-MCP.exe", [], "QuickBooks-Desktop-MCP"),
        ("npx", ["-y", "@modelcontextprotocol/server-filesystem@1.2.3", "."], "server-filesystem"),
        ("uvx", ["mcp-server-git"], "mcp-server-git"),
        ("docker", ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server:latest"], "github-mcp-server"),
        ("node", ["C:/srv/ledger.js"], "ledger"),
    ],
)
def test_program_is_what_the_line_runs_with_its_case_kept(command, args, program):
    assert program_of(command, args) == program


def test_a_masked_piece_or_nothing_is_never_a_name():
    from iron_jarvis.detections.redact import mask

    secret = "ghp_" + "a" * 36
    # The preview reads the MASKED line: a token in the package position is
    # masked there, and a masked piece is never shown as the program.
    assert program_of("npx", [mask(secret)]) == ""
    assert program_of("", []) == ""
    assert program_of("npx", ["-y"]) in ("", "npx")


def test_host_is_the_host_only():
    assert host_of("https://user:pw@MCP.Linear.app:8443/sse?key=1") == "mcp.linear.app"
    assert host_of("not a url") == ""
    assert host_of("") == ""


def test_the_preview_row_carries_program_and_host_and_still_no_value():
    secret = "ghp_" + "b" * 36
    stdio = PackSpec(
        name="quickbooks-desktop",
        command="C:\\Tools\\quickbooks-desktop-mcp.exe",
        args=["--company", "C:\\Books\\acme.qbw"],
        env={"QB_TOKEN": secret},
    ).preview_row()
    assert stdio["program"] == "quickbooks-desktop-mcp"
    assert stdio["host"] == ""
    assert secret not in json.dumps(stdio)

    web = PackSpec(name="linear", transport="http", url="https://mcp.linear.app/mcp?api_key=" + secret).preview_row()
    assert web["host"] == "mcp.linear.app"
    assert web["program"] == ""
    assert secret not in json.dumps(web)


def test_the_real_preview_route_answers_them(tmp_path):
    """Through the real app factory: POST /mcp/import/preview."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    text = json.dumps(
        {
            "mcpServers": {
                "quickbooks-desktop": {"command": "C:\\Tools\\quickbooks-desktop-mcp.exe", "args": []},
                "linear": {"url": "https://mcp.linear.app/sse"},
            }
        }
    )
    res = TestClient(app).post("/mcp/import/preview", json={"text": text})
    assert res.status_code == 200, res.text
    rows = {r["name"]: r for r in res.json()["servers"]}
    assert rows["quickbooks-desktop"]["program"] == "quickbooks-desktop-mcp"
    assert rows["linear"]["host"] == "mcp.linear.app"
    assert rows["linear"]["transport"] == "sse"
