"""v1.343.0 ("add a pack in one paste"), lane H-http: an http/sse pack goes
through the SAME door as a stdio pack, and its secrets go to the vault.

``POST /mcp/servers`` gains ``transport`` / ``url`` / ``headers`` / ``auth``
(every one optional; a stdio body is stored byte for byte as before). A
header whose NAME matches ``SECRET_KEY_RX`` is kept in the vault as
``mcp.<name>.header.<H>`` (the row keeps ``header_secrets[H]``), the auth
value as ``mcp.<name>.auth.value`` (the row keeps ``auth.secret``), and
``_build_transport`` resolves both at launch. ``GET /mcp/servers`` rows carry
``transport`` / ``url`` / ``header_names`` / ``auth_set`` and never a header
or auth value. Test and Retry work for an http pack through the one loader.

Every route here is driven on the REAL app (``create_app``) against a tiny
in-process HTTP MCP server that RECORDS the headers it was sent — so "the
vault value reached the pack" is asserted on the wire, not on a dict.
Every secret test asserts the VALUE is absent from the config object, the
config.toml bytes and the answer.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import McpServerBody
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.client import HttpTransport
from iron_jarvis.mcp.import_config import (
    NAME_RX,
    PackSpec,
    guess_launcher,
    parse_any,
    parse_file,
    safe_name,
)
from iron_jarvis.mcp.tools import _build_transport

TOKEN = "tok-SECRET-h7q2"
API_KEY = "key-SECRET-9z1"
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1343h"


# --------------------------------------------------------------------------- #
# A tiny in-process HTTP MCP server: initialize + tools/list + tools/call,
# plain JSON answers, records the headers of every request it sees.
# --------------------------------------------------------------------------- #
class _McpHandler(BaseHTTPRequestHandler):
    server_version = "ij-fixture/1"

    def log_message(self, *args):  # noqa: D401 — keep the test output quiet
        pass

    def do_POST(self):  # noqa: N802 — http.server's name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        self.server.seen.append(
            {
                "method": body.get("method"),
                "headers": {k.lower(): v for k, v in self.headers.items()},
            }
        )
        rid = body.get("id")
        if rid is None:  # a notification (notifications/initialized)
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        method = body.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the text back.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ]
            }
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
        else:
            result = {}
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "sess-v1343")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def mcp_http():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    server.seen = []  # type: ignore[attr-defined]
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _url(server) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/mcp"


def _seen(server, method: str) -> list[dict]:
    return [req for req in server.seen if req["method"] == method]


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


def _http_body(server, **extra) -> dict:
    body = {
        "name": "hosted",
        "transport": "http",
        "url": _url(server),
        "headers": {"X-API-Key": API_KEY, "X-Client": "iron-jarvis"},
        "auth": {"header": "Authorization", "format": "Bearer {value}", "value": TOKEN},
    }
    body.update(extra)
    return body


# --------------------------------------------------------------------------- #
# POST /mcp/servers: an http pack — the vault holds the values, the row the
# names, and the pack RECEIVES them at launch.
# --------------------------------------------------------------------------- #
def test_post_http_pack_puts_auth_and_secret_header_in_the_vault_and_loads_it(client, mcp_http):
    platform = client.app.state.platform
    res = client.post("/mcp/servers", json=_http_body(mcp_http))
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text and API_KEY not in res.text, "the add answer must not echo a value"
    answer = res.json()
    assert answer["added"] is True
    assert answer["tools_loaded"] == 1, answer
    assert answer["transport"] == "http"
    assert answer["header_names"] == ["X-API-Key", "X-Client"]
    assert answer["auth_set"] is True
    assert answer["env_keys"] == [] and answer["secret_keys"] == []

    # The vault holds both values under the contract's names.
    assert platform.secrets.get("mcp.hosted.auth.value") == TOKEN
    assert platform.secrets.get("mcp.hosted.header.X-API-Key") == API_KEY

    # The stored row: names only, pinned exactly.
    assert _entry(platform, "hosted") == {
        "name": "hosted",
        "transport": "http",
        "url": _url(mcp_http),
        "headers": {"X-Client": "iron-jarvis"},
        "header_secrets": {"X-API-Key": "mcp.hosted.header.X-API-Key"},
        "auth": {
            "header": "Authorization",
            "format": "Bearer {value}",
            "secret": "mcp.hosted.auth.value",
        },
    }
    dumped = json.dumps(platform.config.mcp_servers)
    assert TOKEN not in dumped and API_KEY not in dumped
    text = _toml_text(platform)
    assert "hosted" in text
    assert TOKEN not in text and API_KEY not in text, "config.toml must hold neither value"

    # And the pack RECEIVED them: the live load's tools/list carried the
    # resolved Authorization header and the resolved secret header.
    listed = _seen(mcp_http, "tools/list")
    assert listed, [r["method"] for r in mcp_http.seen]
    sent = listed[-1]["headers"]
    assert sent.get("authorization") == f"Bearer {TOKEN}"
    assert sent.get("x-api-key") == API_KEY
    assert sent.get("x-client") == "iron-jarvis"
    # The tool is live in the registry under the pack's name.
    assert platform.registry.mcp_names("hosted") == ["mcp__hosted__echo"]


def test_an_sse_transport_is_accepted_and_stored_as_sse(client, mcp_http):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={"name": "streamed", "transport": "sse", "url": _url(mcp_http)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tools_loaded"] == 1
    assert _entry(platform, "streamed") == {
        "name": "streamed",
        "transport": "sse",
        "url": _url(mcp_http),
    }


def test_streamable_http_is_normalised_to_http():
    body = McpServerBody(name="x", transport="streamable-http", url="http://127.0.0.1:9/mcp")
    assert body.transport == "http"


# --------------------------------------------------------------------------- #
# GET /mcp/servers: the http row shape, never a value.
# --------------------------------------------------------------------------- #
def test_get_row_for_an_http_pack_lists_header_names_and_auth_set_never_values(client, mcp_http):
    assert client.post("/mcp/servers", json=_http_body(mcp_http)).status_code == 200
    res = client.get("/mcp/servers")
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text and API_KEY not in res.text
    assert "mcp.hosted.auth.value" not in res.text, "a vault name is not a row field"
    row = next(r for r in res.json()["servers"] if r["name"] == "hosted")
    assert row["transport"] == "http"
    assert row["url"] == _url(mcp_http)
    assert row["header_names"] == ["X-API-Key", "X-Client"]
    assert row["auth_set"] is True
    assert row["auth"] == {"header": "Authorization", "format": "Bearer {value}", "value_set": True}
    assert "headers" not in row and "header_secrets" not in row
    assert row["tools_loaded"] == 1 and row["tool_names"] == ["echo"]
    assert row["state"] == "ok"


def test_get_row_masks_a_header_value_planted_in_an_older_row(client):
    """A row an older build (or a hand edit) stored with header VALUES is
    masked the same way — the key is listed, the value never leaves."""
    platform = client.app.state.platform
    platform.config.mcp_servers = [
        {
            "name": "old",
            "transport": "http",
            "url": "http://127.0.0.1:9/mcp",
            "headers": {"X-API-Key": API_KEY},
            "auth": {"header": "Authorization", "format": "Bearer {value}", "value": TOKEN},
        }
    ]
    res = client.get("/mcp/servers")
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text and API_KEY not in res.text
    row = res.json()["servers"][0]
    assert row["header_names"] == ["X-API-Key"]
    assert row["auth_set"] is True
    assert row["auth"]["value_set"] is True and "value" not in row["auth"]


def test_a_stdio_row_reads_transport_stdio_with_no_headers(client):
    res = client.post("/mcp/servers", json={"name": "plain", "command": NO_SUCH_COMMAND})
    assert res.status_code == 200, res.text
    row = next(r for r in client.get("/mcp/servers").json()["servers"] if r["name"] == "plain")
    assert row["transport"] == "stdio"
    assert row["header_names"] == []
    assert row["auth_set"] is False


# --------------------------------------------------------------------------- #
# Test and Retry go through the same loader for an http pack.
# --------------------------------------------------------------------------- #
def test_test_route_answers_ok_with_the_tool_list_for_an_http_pack(client, mcp_http):
    assert client.post("/mcp/servers", json=_http_body(mcp_http)).status_code == 200
    before = len(_seen(mcp_http, "tools/list"))
    res = client.post("/mcp/servers/hosted/test")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert res.json()["tools"] == ["echo"]
    assert res.json()["error"] is None
    assert TOKEN not in res.text and API_KEY not in res.text
    listed = _seen(mcp_http, "tools/list")
    assert len(listed) == before + 1, "the probe connects for real"
    assert listed[-1]["headers"].get("authorization") == f"Bearer {TOKEN}"
    assert listed[-1]["headers"].get("x-api-key") == API_KEY


def test_reload_route_reconnects_an_http_pack(client, mcp_http):
    platform = client.app.state.platform
    assert client.post("/mcp/servers", json=_http_body(mcp_http)).status_code == 200
    res = client.post("/mcp/servers/hosted/reload")
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "tools_loaded": 1, "last_error": None}
    assert platform.registry.mcp_names("hosted") == ["mcp__hosted__echo"]
    assert TOKEN not in res.text and API_KEY not in res.text


# --------------------------------------------------------------------------- #
# A stdio body is stored exactly as before.
# --------------------------------------------------------------------------- #
def test_a_stdio_post_body_is_stored_byte_for_byte_as_before(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={
            "name": "gh",
            "command": NO_SUCH_COMMAND,
            "args": ["-y", "pkg"],
            "env": {"GITHUB_TOKEN": TOKEN, "PORT": "1"},
            "cwd": "C:/work",
        },
    )
    assert res.status_code == 200, res.text
    assert _entry(platform, "gh") == {
        "name": "gh",
        "command": NO_SUCH_COMMAND,
        "args": ["-y", "pkg"],
        "env": {"PORT": "1"},
        "env_secrets": {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"},
        "cwd": "C:/work",
    }
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN
    assert TOKEN not in _toml_text(platform)


def test_a_minimal_stdio_post_body_is_stored_byte_for_byte_as_before(client):
    platform = client.app.state.platform
    res = client.post("/mcp/servers", json={"name": "bare", "command": NO_SUCH_COMMAND})
    assert res.status_code == 200, res.text
    assert _entry(platform, "bare") == {
        "name": "bare",
        "command": NO_SUCH_COMMAND,
        "args": [],
        "env": {},
    }
    # The additive answer fields read as "not an http pack".
    assert res.json()["transport"] == "stdio"
    assert res.json()["header_names"] == []
    assert res.json()["auth_set"] is False


def test_headers_and_auth_on_a_stdio_body_are_ignored_not_stored(client):
    """The stdio shape does not take headers; a body that carries them (a
    form that kept stale fields) stores none and puts nothing in the vault."""
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={
            "name": "mixed",
            "command": NO_SUCH_COMMAND,
            "headers": {"X-API-Key": API_KEY},
            "auth": {"value": TOKEN},
        },
    )
    assert res.status_code == 200, res.text
    assert _entry(platform, "mixed") == {
        "name": "mixed",
        "command": NO_SUCH_COMMAND,
        "args": [],
        "env": {},
    }
    assert platform.secrets.get("mcp.mixed.auth.value") is None
    assert platform.secrets.get("mcp.mixed.header.X-API-Key") is None
    assert TOKEN not in res.text and API_KEY not in res.text


# --------------------------------------------------------------------------- #
# Validation: 422 with one plain sentence; nothing stored.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "body, words",
    [
        ({"name": "h", "transport": "http"}, "needs a url"),
        ({"name": "h", "transport": "sse"}, "needs a url"),
        ({"name": "s", "transport": "stdio"}, "needs a command"),
        ({"name": "s"}, "needs a command"),
        ({"name": "s", "command": "   "}, "needs a command"),
        ({"name": "h", "transport": "ftp", "url": "http://127.0.0.1:9/"}, "stdio, http or sse"),
        ({"name": "h", "transport": "http", "url": "ws://127.0.0.1:9/"}, "http:// or https://"),
        (
            {"name": "h", "transport": "http", "url": "http://127.0.0.1:9/", "auth": {"format": "Token", "value": "x"}},
            "{value}",
        ),
    ],
)
def test_a_mismatched_body_is_422_and_stores_nothing(client, body, words):
    platform = client.app.state.platform
    res = client.post("/mcp/servers", json=body)
    assert res.status_code == 422, res.text
    assert words in res.text
    assert platform.config.mcp_servers == []


def test_the_vault_refusing_a_write_refuses_the_add_naming_keys_only(client, mcp_http, monkeypatch):
    platform = client.app.state.platform

    def refuse(*args, **kwargs):
        raise RuntimeError("vault closed")

    monkeypatch.setattr(platform.secrets, "set", refuse)
    res = client.post("/mcp/servers", json=_http_body(mcp_http))
    assert res.status_code == 503, res.text
    assert "header X-API-Key" in res.text
    assert "the auth value" in res.text
    assert TOKEN not in res.text and API_KEY not in res.text
    assert platform.config.mcp_servers == [], "a pack without its secret is not added"
    assert platform.secrets.get("mcp.hosted.auth.value") is None


def test_delete_removes_the_http_packs_vault_rows(client, mcp_http):
    platform = client.app.state.platform
    assert client.post("/mcp/servers", json=_http_body(mcp_http)).status_code == 200
    res = client.delete("/mcp/servers/hosted")
    assert res.status_code == 200, res.text
    assert res.json()["secrets_removed"] == 2
    assert platform.secrets.get("mcp.hosted.auth.value") is None
    assert platform.secrets.get("mcp.hosted.header.X-API-Key") is None
    assert platform.registry.mcp_names("hosted") == []


def test_an_empty_auth_value_sets_no_auth(client, mcp_http):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={"name": "open", "transport": "http", "url": _url(mcp_http), "auth": {"value": ""}},
    )
    assert res.status_code == 200, res.text
    assert "auth" not in _entry(platform, "open")
    assert res.json()["auth_set"] is False
    assert platform.secrets.get("mcp.open.auth.value") is None


# --------------------------------------------------------------------------- #
# _build_transport resolves header_secrets beside auth.secret.
# --------------------------------------------------------------------------- #
def test_build_transport_resolves_header_secrets_through_the_vault():
    vault = {"mcp.h.header.X-API-Key": API_KEY, "mcp.h.auth.value": TOKEN}
    cfg = {
        "name": "h",
        "transport": "http",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {"X-Client": "ij"},
        "header_secrets": {"X-API-Key": "mcp.h.header.X-API-Key", "X-Missing": "mcp.h.header.X-Missing"},
        "auth": {"header": "Authorization", "format": "Bearer {value}", "secret": "mcp.h.auth.value"},
    }
    transport = _build_transport(cfg, vault.get)
    assert isinstance(transport, HttpTransport)
    assert transport.headers == {
        "X-Client": "ij",
        "X-API-Key": API_KEY,
        "Authorization": f"Bearer {TOKEN}",
    }
    assert "X-Missing" not in transport.headers, "a vault miss omits the header"


def test_build_transport_with_no_resolver_sends_no_secret_header():
    cfg = {
        "name": "h",
        "transport": "http",
        "url": "http://127.0.0.1:9/mcp",
        "header_secrets": {"X-API-Key": "mcp.h.header.X-API-Key"},
    }
    transport = _build_transport(cfg, None)
    assert transport.headers == {}


# --------------------------------------------------------------------------- #
# PackSpec: the one shape.
# --------------------------------------------------------------------------- #
def test_packspec_stdio_to_config_is_todays_stored_shape():
    spec = PackSpec(name="gh", command="npx", args=["-y", "pkg"], env={"GITHUB_TOKEN": TOKEN})
    assert spec.to_config() == {
        "name": "gh",
        "command": "npx",
        "args": ["-y", "pkg"],
        "env": {"GITHUB_TOKEN": TOKEN},
    }
    with_cwd = PackSpec(name="gh", command="npx", cwd="C:/work")
    assert with_cwd.to_config()["cwd"] == "C:/work"


def test_packspec_http_to_config_carries_url_headers_and_auth():
    spec = PackSpec(
        name="hosted",
        transport="streamable-http",
        url="http://127.0.0.1:9/mcp",
        headers={"X-API-Key": API_KEY},
        auth={"value": TOKEN},
        source="paste",
    )
    assert spec.transport == "http"
    assert spec.to_config() == {
        "name": "hosted",
        "transport": "http",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {"X-API-Key": API_KEY},
        "auth": {"header": "Authorization", "format": "Bearer {value}", "value": TOKEN},
    }
    # No auth value → no auth block.
    assert "auth" not in PackSpec(name="x", transport="http", url="http://h/").to_config()


def test_packspec_round_trips_through_from_config_without_vault_names():
    stored = {
        "name": "hosted",
        "transport": "http",
        "url": "http://127.0.0.1:9/mcp",
        "headers": {"X-Client": "ij"},
        "header_secrets": {"X-API-Key": "mcp.hosted.header.X-API-Key"},
        "auth": {"header": "Authorization", "format": "Bearer {value}", "secret": "mcp.hosted.auth.value"},
    }
    spec = PackSpec.from_config(stored, source="form", origin="form")
    assert spec.headers == {"X-Client": "ij"}
    assert spec.auth == {"header": "Authorization", "format": "Bearer {value}", "value": ""}
    assert "mcp.hosted" not in json.dumps(spec.to_config())
    plain = PackSpec.from_config({"name": "gh", "command": "npx", "args": ["x"], "env": {"PORT": "1"}})
    assert plain.to_config() == {"name": "gh", "command": "npx", "args": ["x"], "env": {"PORT": "1"}}


def test_packspec_preview_row_carries_names_and_never_values():
    spec = PackSpec(
        name="hosted",
        transport="http",
        url="http://127.0.0.1:9/mcp",
        headers={"X-API-Key": API_KEY, "X-Client": "ij"},
        auth={"value": TOKEN},
        env={"GITHUB_TOKEN": TOKEN, "PORT": "1"},
        origin="paste",
    )
    row = spec.preview_row()
    assert TOKEN not in json.dumps(row) and API_KEY not in json.dumps(row)
    assert row["header_names"] == ["X-API-Key", "X-Client"]
    assert row["env_keys"] == ["PORT"]
    assert row["secret_keys"] == ["GITHUB_TOKEN"]
    assert row["auth_set"] is True
    assert row["source"] == "paste" and row["origin"] == "paste"


def test_packspec_unknown_transport_and_bad_auth_format_fall_back():
    spec = PackSpec(name="x", transport="carrier-pigeon", auth={"format": "Token", "value": "v"})
    assert spec.transport == "stdio"
    assert spec.auth == {"header": "Authorization", "format": "Bearer {value}", "value": "v"}
    assert PackSpec(name="x", auth="nope").auth is None


# --------------------------------------------------------------------------- #
# safe_name / guess_launcher.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, want",
    [
        ("@modelcontextprotocol/server-github", "modelcontextprotocol-server-github"),
        ("My Notes (local)", "my-notes-local"),
        ("123-brave", "brave"),
        ("  Brave Search  ", "brave-search"),
        ("a" * 60, "a" * 40),
        ("brave---search", "brave-search"),
        ("___", ""),
        ("", ""),
        (None, ""),
        ("x" * 39 + "--y", "x" * 39),
        ("pkg.v2", "pkg-v2"),
    ],
)
def test_safe_name_normalises_to_the_name_rule(raw, want):
    got = safe_name(raw)
    assert got == want
    if got:
        assert NAME_RX.match(got), got


@pytest.mark.parametrize(
    "pkg, want",
    [
        ("pip:mcp-server-git", "uvx"),
        ("pypi:basic-memory", "uvx"),
        ("mcp-server-git", "uvx"),
        ("mcp_server_fetch", "uvx"),
        ("@modelcontextprotocol/server-github", "npx"),
        ("npm:brave-search", "npx"),
        ("brave-search", "npx"),
        ("mcp-remote", "npx"),
        ("https://github.com/x/y", "npx"),
        ("server.js", "npx"),
        ("", "npx"),
    ],
)
def test_guess_launcher(pkg, want):
    assert guess_launcher(pkg) == want


def test_parser_stubs_answer_nothing_and_never_raise():
    assert parse_any("{not json") == []
    assert parse_file("C:/nowhere/mcp.json") == ([], False, "")
