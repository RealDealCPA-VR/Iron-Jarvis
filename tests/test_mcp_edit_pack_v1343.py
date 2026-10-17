"""v1.343.0 ("add a pack in one paste"), lane J-edit: a connected pack is
EDITED in place, partially, and the npm door names and launches like the
daemon says.

``PATCH /mcp/servers/{name}`` (``McpServerPatchBody``) takes command, args,
env, cwd, transport, url, headers, auth, auto_approve, ``clear_secrets`` and
``clear_headers``. Omitted = keep; ``""`` is a value, never a clear (the
v1.164.0 rule); a secret-named env KEY or header NAME goes to the vault under
the add door's names (``mcp.<name>.<KEY>``, ``mcp.<name>.header.<H>``,
``mcp.<name>.auth.value``) and the row keeps names only; a clear removes the
vault row and the row entry; the result is validated like a POST; the pack
is unloaded (clients closed) and loaded again; the answer is the GET row
plus ``{reloaded, note}``; a rename is a 422. A body carrying nothing but
``auto_approve`` behaves exactly as v1.103.0 did (pinned here as well as in
``test_mcp_auto_approve.py``).

The npm/GitHub door (``POST /mcp/servers`` with a ``launcher`` or a
``package``): the name is ``safe_name``'d server-side (dots become "-"), a
name that still fails the rule is a 422 naming the rule, the launcher is one
of ``LAUNCHERS`` (422 otherwise), and with ``package`` and no ``command`` the
daemon builds the command line itself, guessing the launcher when none was
named (``guess_launcher``: ``pip:`` / a PyPI-looking name reads as uvx).

Every route is driven on the REAL app (``create_app``). The reload pin runs
the real stdio fixture (``tests/fixtures/stdio_mcp_server_v1291.py``) and
proves the pack was started AGAIN by asking its ``pid`` tool before and
after. The http pins run a tiny in-process MCP server that RECORDS the
headers it was sent, so "the vault value reached the pack" is asserted on
the wire. Every secret test asserts the VALUE is absent from the config
object, the config.toml bytes and every answer.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import McpServerBody, McpServerPatchBody
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.import_config import LAUNCHERS, build_launch, guess_launcher

TOKEN = "tok-SECRET-j7q2"
TOKEN2 = "tok-SECRET-second-j7q3"
API_KEY = "key-SECRET-j9z1"
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1343j"
FIXTURE = str(Path(__file__).parent / "fixtures" / "stdio_mcp_server_v1291.py")
SECRET_VALUES = (TOKEN, TOKEN2, API_KEY)


# --------------------------------------------------------------------------- #
# A tiny in-process HTTP MCP server that records the headers of every request.
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
            {"method": body.get("method"), "headers": {k.lower(): v for k, v in self.headers.items()}}
        )
        rid = body.get("id")
        if rid is None:
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
                        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
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
        self.send_header("Mcp-Session-Id", "sess-v1343j")
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


def _listed(server) -> list[dict]:
    return [req for req in server.seen if req["method"] == "tools/list"]


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
    c = TestClient(create_app(str(tmp_path)))
    try:
        yield c
    finally:
        # Close every pack child this test started.
        platform = c.app.state.platform
        for row in list(getattr(platform.config, "mcp_servers", None) or []):
            mcp_tools_mod.unload_pack_tools(platform.registry, str(row.get("name") or ""))


def _toml_text(platform) -> str:
    return (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")


def _entry(platform, name: str) -> dict:
    for s in platform.config.mcp_servers:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no config entry named {name!r}")


def _row(client: TestClient, name: str) -> dict:
    return next(r for r in client.get("/mcp/servers").json()["servers"] if r["name"] == name)


def _no_values(text: str) -> None:
    for value in SECRET_VALUES:
        assert value not in text, "a secret VALUE left the daemon"


def _add_stdio(client: TestClient, name: str = "gh", **extra) -> dict:
    body = {
        "name": name,
        "command": NO_SUCH_COMMAND,
        "args": ["-y", "pkg"],
        "env": {"GITHUB_TOKEN": TOKEN, "PORT": "1"},
        "cwd": "C:/work",
    }
    body.update(extra)
    res = client.post("/mcp/servers", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _add_http(client: TestClient, server, name: str = "hosted") -> dict:
    res = client.post(
        "/mcp/servers",
        json={
            "name": name,
            "transport": "http",
            "url": _url(server),
            "headers": {"X-API-Key": API_KEY, "X-Client": "iron-jarvis"},
            "auth": {"header": "Authorization", "format": "Bearer {value}", "value": TOKEN},
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# PATCH env: a plain key lands in env, a token in the vault; the config, the
# toml and the answer stay clean; omitted fields are kept.
# --------------------------------------------------------------------------- #
def test_patch_env_adds_a_plain_key_and_vaults_a_token_and_keeps_the_rest(client):
    platform = client.app.state.platform
    _add_stdio(client)
    res = client.patch("/mcp/servers/gh", json={"env": {"API_TOKEN": TOKEN2, "REGION": "eu"}})
    assert res.status_code == 200, res.text
    _no_values(res.text)
    # The stored row: the typed plain key beside the old one, the new secret
    # beside the old one as a vault NAME, command/args/cwd untouched.
    assert _entry(platform, "gh") == {
        "name": "gh",
        "command": NO_SUCH_COMMAND,
        "args": ["-y", "pkg"],
        "env": {"PORT": "1", "REGION": "eu"},
        "env_secrets": {
            "GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN",
            "API_TOKEN": "mcp.gh.API_TOKEN",
        },
        "cwd": "C:/work",
    }
    assert platform.secrets.get("mcp.gh.API_TOKEN") == TOKEN2
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN, "the omitted secret is kept"
    _no_values(json.dumps(platform.config.mcp_servers))
    _no_values(_toml_text(platform))
    assert "REGION" in _toml_text(platform), "the edit was persisted"
    # The answer IS the GET row plus {reloaded, note}.
    answer = res.json()
    assert answer["env_keys"] == ["PORT", "REGION"]
    assert answer["secret_keys"] == ["API_TOKEN", "GITHUB_TOKEN"]
    assert "env" not in answer and "env_secrets" not in answer
    assert answer["reloaded"] is False, "a command that does not exist cannot start"
    assert "did not start" in (answer["note"] or "")
    listed = _row(client, "gh")
    for key in ("name", "command", "args", "env_keys", "secret_keys", "transport", "header_names", "auth_set"):
        assert answer[key] == listed[key], key


def test_an_omitted_key_is_kept_and_an_empty_string_is_a_value(client):
    platform = client.app.state.platform
    _add_stdio(client)
    # Only PORT is sent, as "": the value becomes "", REGION-less env keeps
    # nothing else, GITHUB_TOKEN stays in the vault and on the row.
    res = client.patch("/mcp/servers/gh", json={"env": {"PORT": ""}})
    assert res.status_code == 200, res.text
    entry = _entry(platform, "gh")
    assert entry["env"] == {"PORT": ""}, '"" is a value, never a clear'
    assert entry["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN
    assert entry["command"] == NO_SUCH_COMMAND and entry["args"] == ["-y", "pkg"]
    # A secret key sent as "" changes nothing (an empty secret is not a secret).
    res = client.patch("/mcp/servers/gh", json={"env": {"GITHUB_TOKEN": ""}})
    assert res.status_code == 200, res.text
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN
    assert _entry(platform, "gh")["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}


def test_patch_command_args_and_cwd_replace_only_what_was_sent(client):
    platform = client.app.state.platform
    _add_stdio(client)
    res = client.patch("/mcp/servers/gh", json={"args": ["-y", "pkg@2"], "cwd": ""})
    assert res.status_code == 200, res.text
    entry = _entry(platform, "gh")
    assert entry["args"] == ["-y", "pkg@2"]
    assert "cwd" not in entry, 'cwd "" stores no folder, as the add door does'
    assert entry["command"] == NO_SUCH_COMMAND
    assert entry["env"] == {"PORT": "1"}
    res = client.patch("/mcp/servers/gh", json={"command": NO_SUCH_COMMAND + "-b"})
    assert res.status_code == 200, res.text
    assert _entry(platform, "gh")["command"] == NO_SUCH_COMMAND + "-b"
    assert _entry(platform, "gh")["args"] == ["-y", "pkg@2"], "args kept across a command edit"


def test_clear_secrets_removes_the_vault_row_and_the_env_secrets_entry(client):
    platform = client.app.state.platform
    _add_stdio(client)
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") == TOKEN
    res = client.patch("/mcp/servers/gh", json={"clear_secrets": ["GITHUB_TOKEN"]})
    assert res.status_code == 200, res.text
    _no_values(res.text)
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") is None, "the vault row is gone"
    entry = _entry(platform, "gh")
    assert "env_secrets" not in entry, "the last secret's entry goes with it"
    assert entry["env"] == {"PORT": "1"}
    assert res.json()["secret_keys"] == [] and res.json()["env_keys"] == ["PORT"]
    # Clearing a key that is not there is not an error.
    assert client.patch("/mcp/servers/gh", json={"clear_secrets": ["NOPE"]}).status_code == 200


# --------------------------------------------------------------------------- #
# Headers and auth on an http pack: the vault holds the values, the row the
# names, and the pack RECEIVES the new ones on the reload.
# --------------------------------------------------------------------------- #
def test_patch_headers_and_auth_on_an_http_pack_reach_the_pack_on_reload(client, mcp_http):
    platform = client.app.state.platform
    _add_http(client, mcp_http)
    before = len(_listed(mcp_http))
    res = client.patch(
        "/mcp/servers/hosted",
        json={
            "headers": {"X-Client": "jarvis-2", "X-Auth-Scope": API_KEY},
            "auth": {"header": "Authorization", "format": "Bearer {value}", "value": TOKEN2},
        },
    )
    assert res.status_code == 200, res.text
    _no_values(res.text)
    assert _entry(platform, "hosted") == {
        "name": "hosted",
        "transport": "http",
        "url": _url(mcp_http),
        "headers": {"X-Client": "jarvis-2"},
        "header_secrets": {
            "X-API-Key": "mcp.hosted.header.X-API-Key",
            "X-Auth-Scope": "mcp.hosted.header.X-Auth-Scope",
        },
        "auth": {"header": "Authorization", "format": "Bearer {value}", "secret": "mcp.hosted.auth.value"},
    }
    assert platform.secrets.get("mcp.hosted.auth.value") == TOKEN2, "the token was replaced"
    assert platform.secrets.get("mcp.hosted.header.X-Auth-Scope") == API_KEY
    _no_values(_toml_text(platform))
    answer = res.json()
    assert answer["reloaded"] is True and answer["note"] is None
    assert answer["header_names"] == ["X-API-Key", "X-Auth-Scope", "X-Client"]
    assert answer["auth_set"] is True
    assert answer["auth"] == {"header": "Authorization", "format": "Bearer {value}", "value_set": True}
    assert answer["tools_loaded"] == 1 and answer["tool_names"] == ["echo"]
    # On the wire: the reload's tools/list carried the NEW token and headers.
    listed = _listed(mcp_http)
    assert len(listed) == before + 1, "the pack was loaded again"
    sent = listed[-1]["headers"]
    assert sent.get("authorization") == f"Bearer {TOKEN2}"
    assert sent.get("x-auth-scope") == API_KEY
    assert sent.get("x-api-key") == API_KEY, "the untouched secret header is kept"
    assert sent.get("x-client") == "jarvis-2"


def test_clear_headers_and_auth_null_drop_their_vault_rows_and_the_pack_stops_sending_them(client, mcp_http):
    platform = client.app.state.platform
    _add_http(client, mcp_http)
    res = client.patch(
        "/mcp/servers/hosted",
        json={"clear_headers": ["X-API-Key", "X-Client"], "auth": None},
    )
    assert res.status_code == 200, res.text
    assert platform.secrets.get("mcp.hosted.auth.value") is None
    assert platform.secrets.get("mcp.hosted.header.X-API-Key") is None
    assert _entry(platform, "hosted") == {"name": "hosted", "transport": "http", "url": _url(mcp_http)}
    answer = res.json()
    assert answer["header_names"] == [] and answer["auth_set"] is False
    assert "auth" not in answer
    sent = _listed(mcp_http)[-1]["headers"]
    assert "authorization" not in sent and "x-api-key" not in sent and "x-client" not in sent


def test_auth_without_a_value_changes_the_header_and_keeps_the_token(client, mcp_http):
    platform = client.app.state.platform
    _add_http(client, mcp_http)
    res = client.patch("/mcp/servers/hosted", json={"auth": {"header": "X-Token", "format": "{value}"}})
    assert res.status_code == 200, res.text
    assert _entry(platform, "hosted")["auth"] == {
        "header": "X-Token",
        "format": "{value}",
        "secret": "mcp.hosted.auth.value",
    }
    assert platform.secrets.get("mcp.hosted.auth.value") == TOKEN
    assert _listed(mcp_http)[-1]["headers"].get("x-token") == TOKEN


def test_switching_a_stdio_pack_to_http_drops_its_env_secrets(client, mcp_http):
    platform = client.app.state.platform
    _add_stdio(client)
    res = client.patch("/mcp/servers/gh", json={"transport": "http", "url": _url(mcp_http)})
    assert res.status_code == 200, res.text
    assert _entry(platform, "gh") == {"name": "gh", "transport": "http", "url": _url(mcp_http)}
    assert platform.secrets.get("mcp.gh.GITHUB_TOKEN") is None, "no orphaned vault row"
    assert res.json()["reloaded"] is True and res.json()["transport"] == "http"


# --------------------------------------------------------------------------- #
# Validation: the RESULT is checked like a POST; a rename is refused.
# --------------------------------------------------------------------------- #
def test_a_rename_is_422_and_the_same_name_is_fine(client):
    platform = client.app.state.platform
    _add_stdio(client)
    res = client.patch("/mcp/servers/gh", json={"name": "gh2", "env": {"PORT": "2"}})
    assert res.status_code == 422, res.text
    assert "name" in res.json()["detail"]
    assert _entry(platform, "gh")["env"] == {"PORT": "1"}, "nothing changed"
    assert [s["name"] for s in platform.config.mcp_servers] == ["gh"]
    res = client.patch("/mcp/servers/gh", json={"name": "gh", "env": {"PORT": "2"}})
    assert res.status_code == 200, res.text
    assert _entry(platform, "gh")["env"] == {"PORT": "2"}


@pytest.mark.parametrize(
    "body, words",
    [
        ({"transport": "http"}, "needs a url"),
        ({"transport": "sse", "url": ""}, "needs a url"),
        ({"transport": "http", "url": "ws://127.0.0.1:9/"}, "http:// or https://"),
        ({"command": ""}, "needs a command"),
        ({"command": "   "}, "needs a command"),
        ({"transport": "ftp"}, "stdio, http or sse"),
        ({"auth": {"format": "Token", "value": "x"}}, "{value}"),
    ],
)
def test_a_result_that_fails_the_shape_is_422_and_changes_nothing(client, body, words):
    platform = client.app.state.platform
    _add_stdio(client)
    before = json.loads(json.dumps(platform.config.mcp_servers))
    res = client.patch("/mcp/servers/gh", json=body)
    assert res.status_code == 422, res.text
    assert words in res.text
    assert platform.config.mcp_servers == before


def test_unknown_pack_is_404(client):
    assert client.patch("/mcp/servers/nope", json={"env": {"A": "b"}}).status_code == 404


def test_a_vault_that_refuses_the_write_is_503_and_changes_nothing(client, monkeypatch):
    platform = client.app.state.platform
    _add_stdio(client)
    before = json.loads(json.dumps(platform.config.mcp_servers))
    toml_before = _toml_text(platform)

    def refuse(*a, **k):
        raise RuntimeError("vault closed")

    monkeypatch.setattr(platform.secrets, "set", refuse)
    res = client.patch("/mcp/servers/gh", json={"env": {"API_TOKEN": TOKEN2, "REGION": "eu"}})
    assert res.status_code == 503, res.text
    _no_values(res.text)
    assert "API_TOKEN" in res.json()["detail"] and "RuntimeError" in res.json()["detail"]
    assert platform.config.mcp_servers == before, "the plain key did not land either"
    assert _toml_text(platform) == toml_before


# --------------------------------------------------------------------------- #
# The pack is RELOADED: its old child is closed and a new one started.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
def test_the_pack_is_reloaded_with_a_new_child_after_an_edit(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={"name": "real", "command": sys.executable, "args": [FIXTURE], "env": {"PORT": "1"}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tools_loaded"] == 5, res.json()
    pid_tool = platform.registry.get("mcp__real__pid")
    first = asyncio.run(pid_tool.execute({}, None))
    assert first.ok, first.error
    old_client = getattr(pid_tool, "client", None)

    res = client.patch("/mcp/servers/real", json={"env": {"PORT": "2"}, "args": [FIXTURE, "--tag", "edited"]})
    assert res.status_code == 200, res.text
    answer = res.json()
    assert answer["reloaded"] is True and answer["note"] is None, answer
    assert answer["tools_loaded"] == 5 and answer["state"] == "ok"
    assert sorted(answer["tool_names"]) == ["accented", "die", "echo", "hang", "pid"]
    assert _entry(platform, "real")["args"] == [FIXTURE, "--tag", "edited"]
    # The tools are re-registered on a NEW client and the pid is a new child.
    pid_tool2 = platform.registry.get("mcp__real__pid")
    assert pid_tool2 is not None and pid_tool2 is not pid_tool
    assert getattr(pid_tool2, "client", None) is not old_client
    second = asyncio.run(pid_tool2.execute({}, None))
    assert second.ok, second.error
    assert second.output != first.output, "the edit started the pack again"
    assert sorted(platform.registry.mcp_names("real")) == [
        "mcp__real__accented",
        "mcp__real__die",
        "mcp__real__echo",
        "mcp__real__hang",
        "mcp__real__pid",
    ]


def test_an_edit_that_breaks_the_launch_says_so_and_unloads_the_old_tools(client):
    platform = client.app.state.platform
    res = client.post("/mcp/servers", json={"name": "real", "command": sys.executable, "args": [FIXTURE]})
    assert res.status_code == 200, res.text
    assert platform.registry.mcp_names("real")
    res = client.patch("/mcp/servers/real", json={"command": NO_SUCH_COMMAND})
    assert res.status_code == 200, res.text
    assert res.json()["reloaded"] is False
    assert "did not start" in res.json()["note"]
    assert res.json()["state"] == "failed"
    assert platform.registry.mcp_names("real") == [], "the old tools do not linger under a broken config"


# --------------------------------------------------------------------------- #
# The v1.103.0 auto-approve flip is byte for byte what it was.
# --------------------------------------------------------------------------- #
def test_an_auto_approve_only_patch_is_unchanged_no_reload(client, monkeypatch):
    platform = client.app.state.platform
    _add_stdio(client)
    import iron_jarvis.daemon.routes.agents as agents_routes

    monkeypatch.setattr(
        agents_routes, "update_mcp_pack", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reloaded"))
    )
    res = client.patch("/mcp/servers/gh", json={"auto_approve": True})
    assert res.status_code == 200, res.text
    assert res.json() == {
        "name": "gh",
        "auto_approve": True,
        "note": "saved — restart Iron Jarvis for autonomous agents to pick this up",
    }
    assert _entry(platform, "gh")["auto_approve"] is True
    res = client.patch("/mcp/servers/gh", json={"auto_approve": False})
    assert res.json() == {
        "name": "gh",
        "auto_approve": False,
        "note": "saved — restart Iron Jarvis for autonomous agents to pick this up",
    }
    assert "auto_approve" not in _entry(platform, "gh")
    res = client.patch("/mcp/servers/gh", json={})
    assert res.json() == {"name": "gh", "auto_approve": False}
    # The rest of the row never moved.
    assert _entry(platform, "gh")["env"] == {"PORT": "1"}
    assert _entry(platform, "gh")["env_secrets"] == {"GITHUB_TOKEN": "mcp.gh.GITHUB_TOKEN"}


def test_auto_approve_beside_other_fields_is_applied_and_said(client):
    platform = client.app.state.platform
    _add_stdio(client)
    res = client.patch("/mcp/servers/gh", json={"auto_approve": True, "env": {"PORT": "9"}})
    assert res.status_code == 200, res.text
    assert _entry(platform, "gh")["auto_approve"] is True
    assert _entry(platform, "gh")["env"] == {"PORT": "9"}
    assert "restart" in res.json()["note"].lower()
    assert res.json()["auto_approve"] is True


def test_only_auto_approve_reads_the_fields_that_were_sent():
    assert McpServerPatchBody().only_auto_approve() is True
    assert McpServerPatchBody(auto_approve=True).only_auto_approve() is True
    assert McpServerPatchBody(auto_approve=True, env={}).only_auto_approve() is False
    assert McpServerPatchBody.model_validate({"auth": None}).clears_auth() is True
    assert McpServerPatchBody().clears_auth() is False


# --------------------------------------------------------------------------- #
# The npm/GitHub door: the name rule server-side, the launcher choice.
# --------------------------------------------------------------------------- #
def test_npm_door_safe_names_the_pack_server_side(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={"name": "My.Pkg Server", "command": NO_SUCH_COMMAND, "args": ["-y", "my.pkg"], "env": {}, "launcher": "npx"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "my-pkg-server"
    assert res.json()["launcher"] == "npx"
    assert _entry(platform, "my-pkg-server")["command"] == NO_SUCH_COMMAND


def test_npm_door_name_that_still_fails_the_rule_is_422_naming_the_rule(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={"name": "123", "command": NO_SUCH_COMMAND, "args": [], "launcher": "npx"},
    )
    assert res.status_code == 422, res.text
    assert "starts with a letter" in res.json()["detail"]
    assert platform.config.mcp_servers == []


def test_a_catalog_add_without_a_launcher_keeps_the_old_400_for_a_bad_name(client):
    res = client.post("/mcp/servers", json={"name": "My.Pkg", "command": NO_SUCH_COMMAND})
    assert res.status_code == 400, res.text
    assert res.json()["detail"] == "invalid server name"


def test_npm_door_refuses_an_unknown_launcher(client):
    res = client.post(
        "/mcp/servers",
        json={"name": "x", "command": NO_SUCH_COMMAND, "launcher": "cargo"},
    )
    assert res.status_code == 422, res.text
    assert "launcher must be one of" in res.text
    for launcher in LAUNCHERS:
        body = McpServerBody(name="x", command=NO_SUCH_COMMAND, launcher=launcher.upper())
        assert body.launcher == launcher, "the launcher is normalised, not refused"


def test_npm_door_builds_the_command_from_a_package_and_guesses_the_launcher(client):
    platform = client.app.state.platform
    # No command, no launcher: a PyPI-looking name reads as uvx.
    res = client.post("/mcp/servers", json={"name": "", "package": "mcp-server-git"})
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "mcp-server-git"
    assert res.json()["launcher"] == "uvx"
    entry = _entry(platform, "mcp-server-git")
    assert entry["command"] == "uvx" and entry["args"] == ["mcp-server-git"]
    # A named launcher wins over the guess; the name falls back to the package tail.
    res = client.post("/mcp/servers", json={"name": "", "package": "@scope/my.pkg@1.2.3", "launcher": "node"})
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "my-pkg"
    assert res.json()["launcher"] == "node"
    entry = _entry(platform, "my-pkg")
    assert entry["command"] == "node" and entry["args"] == ["@scope/my.pkg@1.2.3"]
    # A pip: prefix is stripped and reads as uvx.
    res = client.post("/mcp/servers", json={"name": "fetcher", "package": "pip:basic-memory"})
    assert res.status_code == 200, res.text
    assert res.json()["launcher"] == "uvx"
    assert _entry(platform, "fetcher")["args"] == ["basic-memory"]


@pytest.mark.parametrize(
    "pkg, launcher, expected",
    [
        ("@scope/pkg", "npx", ("npx", ["-y", "@scope/pkg"])),
        ("npm:@scope/pkg", "npx", ("npx", ["-y", "@scope/pkg"])),
        ("pip:mcp-server-git", "uvx", ("uvx", ["mcp-server-git"])),
        ("basic-memory", "pipx", ("pipx", ["run", "basic-memory"])),
        ("server.js", "node", ("node", ["server.js"])),
        ("mcp_server_fetch", "python", ("python", ["-m", "mcp_server_fetch"])),
        ('docker run -i "my image"', "command", ("docker", ["run", "-i", "my image"])),
        ("", "npx", ("", [])),
        ("pkg", "unknown", ("npx", ["-y", "pkg"])),
    ],
)
def test_build_launch_table(pkg, launcher, expected):
    assert build_launch(pkg, launcher) == expected


@pytest.mark.parametrize(
    "pkg, expected",
    [
        ("pip:anything", "uvx"),
        ("pypi:anything", "uvx"),
        ("mcp-server-git", "uvx"),
        ("mcp_server_fetch", "uvx"),
        ("@modelcontextprotocol/server-github", "npx"),
        ("brave-search", "npx"),
        ("github.com/owner/repo", "npx"),
        ("server.js", "npx"),
    ],
)
def test_guess_launcher_table(pkg, expected):
    assert guess_launcher(pkg) == expected


def test_launcher_and_package_are_absent_from_the_stored_row_and_a_plain_add_answers_no_launcher(client):
    platform = client.app.state.platform
    res = client.post("/mcp/servers", json={"name": "plain", "command": NO_SUCH_COMMAND})
    assert res.status_code == 200, res.text
    assert "launcher" not in res.json(), "a catalog add's answer is what it was"
    assert _entry(platform, "plain") == {"name": "plain", "command": NO_SUCH_COMMAND, "args": [], "env": {}}
    res = client.post("/mcp/servers", json={"name": "built", "package": "mcp-server-git"})
    assert res.status_code == 200, res.text
    assert _entry(platform, "built") == {"name": "built", "command": "uvx", "args": ["mcp-server-git"], "env": {}}
