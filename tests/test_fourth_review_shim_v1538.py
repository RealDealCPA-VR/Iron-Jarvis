"""The bridge a connected app starts (``mcpserver/stdio_shim.py``), fourth fix
plan, group 29, part C: MS4b, MS6, C-MS-1 and ``--check``.

* ``--check``: the program says it can start, with no network call and no key
  read, so Iron Jarvis can try a new copy before swapping it in. Run here as a
  real child process of the file itself (``python stdio_shim.py --check``),
  which is also how the frozen ``ironjarvis-bridge`` runs it.
* **MS4b.** When ``IRONJARVIS_MCP_TOKEN_FILE`` is set, the key file wins over
  an inherited ``IRONJARVIS_MCP_TOKEN`` (an app started from a Build terminal
  inherits that terminal's token).
* **MS6.** A key Iron Jarvis no longer knows (401, ``reason: "unknown_token"``)
  and a key file with no NEW key: the app's model reads one plain sentence
  naming the remedy, and the call is made once.
* **C-MS-1.** The key file holds a NEW key (Connect pressed again): the bridge
  takes it, starts its session again and repeats the call ONCE. No restart.
* The file imports only the standard library and nothing from ``iron_jarvis``
  (it becomes its own frozen program).

The 401 comes from the app's REAL ``/mcp`` route and token check on a
``TestClient``; the bridge is driven through its real ``main``. Nothing here
reaches the owner's daemon: the one child process points at a closed port.
"""

from __future__ import annotations

import ast
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import REASON_UNKNOWN_TOKEN, PaneTokenStore
from iron_jarvis.daemon.routes import mcpserver as mcp_routes
from iron_jarvis.mcp.client import PROTOCOL_VERSION as CLIENT_PROTOCOL_VERSION
from iron_jarvis.mcpserver import stdio_shim
from iron_jarvis.mcpserver.session import McpSessionRegistry

SHIM_PATH = Path(stdio_shim.__file__)
#: A pane-store key stands in for an app's key: the door's token check (the
#: layer that answers 401 ``unknown_token``) is the same for every store, and
#: this bare door serves the browser box, not the apps' "record" box.
PANE_ID = "term_g29c_pane"
URL = "http://127.0.0.1:1/mcp"

#: The exact sentence (MS6). Spelled here, not imported, so a change of words
#: is a decision this test sees.
KEY_ENDED = (
    "Iron Jarvis no longer accepts this app's key (it was disconnected, or connected "
    "again): connect this app in Iron Jarvis again (Other tools, in the chat list). "
    "The call was not made."
)

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": CLIENT_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "pytest-app", "version": "1"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


class FakePane:
    def __init__(self, pane_id: str) -> None:
        self.id = pane_id
        self.capabilities = {"browser": True}
        self.cwd = ""
        self.alive = True


class RoutePoster:
    """The bridge's transport, pointed at the app's REAL ``/mcp`` route."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[dict] = []

    def post(self, url, body, headers, timeout):
        self.calls.append({"body": json.loads(body.decode("utf-8")), "headers": dict(headers)})
        response = self.client.post("/mcp", content=body, headers=headers)
        return stdio_shim.HttpReply(
            status=response.status_code,
            text=response.text,
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
        )

    def methods(self) -> list[str]:
        return [str(c["body"].get("method")) for c in self.calls]


@pytest.fixture()
def door(platform, monkeypatch):
    """A bare app with the real ``/mcp`` route, token store and session registry."""
    monkeypatch.setenv("IRONJARVIS_TOKEN", "install-bearer-for-the-whole-app")
    platform.config.browser_access = "interactive"
    pane = FakePane(PANE_ID)
    tokens = PaneTokenStore(pane_lookup=lambda pid: pane if pid == PANE_ID else None)
    platform.pane_tokens = tokens
    platform.mcp_sessions = McpSessionRegistry()
    app = FastAPI()
    mcp_routes.register(app, SimpleNamespace(platform=platform))
    return SimpleNamespace(tokens=tokens, client=TestClient(app))


def _run_bridge(door, key: Path, between) -> tuple[RoutePoster, list[dict]]:
    """``main`` with the key file, the real route behind it; ``between`` runs
    after the handshake and before the one ``tools/list``."""
    poster = RoutePoster(door.client)

    def lines():
        yield json.dumps(INIT) + "\n"
        yield json.dumps(INITIALIZED) + "\n"
        between()
        yield json.dumps(LIST) + "\n"

    out = io.StringIO()
    code = stdio_shim.main(
        ["ironjarvis-bridge", URL],
        env={stdio_shim.MCP_TOKEN_FILE_ENV: str(key)},
        stdin=lines(), stdout=out, stderr=io.StringIO(),
        shim_factory=lambda url, token: stdio_shim.StdioShim(url, token, poster=poster),
    )
    assert code == 0
    answers = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert len(answers) == 2, answers  # initialize, tools/list (no reply to a notification)
    assert answers[0]["result"]["serverInfo"]["name"] == "iron-jarvis", answers[0]
    return poster, answers


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# --------------------------------------------------------------------------- #
# --check
# --------------------------------------------------------------------------- #
def test_check_says_ok_on_one_line_with_no_key_and_no_network(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("IRONJARVIS_MCP")}
    env["IRONJARVIS_MCP_URL"] = f"http://127.0.0.1:{_closed_port()}/mcp"
    # A key file that is not there: reading it would end in exit 2.
    env["IRONJARVIS_MCP_TOKEN_FILE"] = str(tmp_path / "no-such-app.key")
    proc = subprocess.run(
        [sys.executable, str(SHIM_PATH), "--check"],
        cwd=str(tmp_path), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout == "ironjarvis-bridge ok\n", repr(proc.stdout)
    assert proc.stderr == "", proc.stderr


# --------------------------------------------------------------------------- #
# MS4b: the key file wins over an inherited token
# --------------------------------------------------------------------------- #
def test_ms4b_the_key_file_wins_over_an_inherited_terminal_token(tmp_path):
    key = tmp_path / "app-codex.key"
    from_file = "app-" + "k" * 39
    inherited = "terminal-" + "t" * 34
    key.write_text(from_file + "\n", encoding="utf-8")
    seen: dict = {}

    class Shim:
        def __init__(self, url, token):
            seen["token"] = token

        def run(self, stdin, stdout):
            return 0

    env = {stdio_shim.MCP_TOKEN_ENV: inherited, stdio_shim.MCP_TOKEN_FILE_ENV: str(key)}
    io_kw = {"stdin": io.StringIO(), "stdout": io.StringIO()}
    assert stdio_shim.main(["ironjarvis-bridge", URL], env=env, stderr=io.StringIO(),
                           shim_factory=Shim, **io_kw) == 0
    assert seen["token"] == from_file

    # The key file gone: the app is told to connect again; the terminal's
    # token (a different grant) is never used in its place.
    key.unlink()
    seen.clear()
    err = io.StringIO()
    assert stdio_shim.main(["ironjarvis-bridge", URL], env=env, stderr=err,
                           shim_factory=Shim, **io_kw) == 2
    assert seen == {}
    assert "connect this app again in Iron Jarvis" in err.getvalue()


# --------------------------------------------------------------------------- #
# MS6: a refused key with no new key in the file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("file_after", ["unchanged", "gone"])
def test_ms6_a_refused_key_with_no_new_key_names_the_remedy_and_calls_once(door, tmp_path, file_after):
    key = tmp_path / "app-codex.key"
    key.write_text(door.tokens.mint(PANE_ID, {"browser": True}), encoding="utf-8")

    def disconnect():
        door.tokens.revoke_pane(PANE_ID)
        if file_after == "gone":
            key.unlink()

    poster, answers = _run_bridge(door, key, disconnect)

    assert answers[1]["id"] == 2
    assert answers[1]["error"]["message"] == KEY_ENDED, answers[1]
    assert "Build terminal" not in json.dumps(answers[1])
    assert poster.methods().count("tools/list") == 1, poster.methods()
    assert poster.methods()[2:] == ["tools/list"], poster.methods()


# --------------------------------------------------------------------------- #
# C-MS-1: Connect again reaches the running app
# --------------------------------------------------------------------------- #
def test_c_ms_1_a_new_key_in_the_file_is_taken_and_the_call_repeated_once(door, tmp_path):
    key = tmp_path / "app-codex.key"
    key.write_text(door.tokens.mint(PANE_ID, {"browser": True}), encoding="utf-8")
    fresh: dict = {}

    def connect_again():
        # A new key for the same app; the old one stops working at once.
        fresh["key"] = door.tokens.mint(PANE_ID, {"browser": True})
        key.write_text(fresh["key"], encoding="utf-8")

    poster, answers = _run_bridge(door, key, connect_again)

    assert "error" not in answers[1], answers[1]
    assert answers[1]["id"] == 2
    names = [tool["name"] for tool in answers[1]["result"]["tools"]]
    assert "browser_get_status" in names, names
    assert poster.methods()[2:] == [
        "tools/list", "initialize", "notifications/initialized", "tools/list",
    ], poster.methods()
    assert poster.methods().count("tools/list") == 2
    refused, repeated = poster.calls[2], poster.calls[5]
    assert refused["headers"]["Authorization"] != f"Bearer {fresh['key']}"
    assert repeated["headers"]["Authorization"] == f"Bearer {fresh['key']}"
    assert repeated["body"] == LIST


# --------------------------------------------------------------------------- #
# The file stays a standalone, standard-library-only program
# --------------------------------------------------------------------------- #
def test_the_bridge_imports_only_the_standard_library_and_nothing_of_iron_jarvis():
    tree = ast.parse(SHIM_PATH.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"a relative import: line {node.lineno}"
            found.append(node.module or "")
    assert found, "no imports found: the scan is broken"
    tops = {name.split(".")[0] for name in found}
    assert "iron_jarvis" not in tops, sorted(tops)
    outside = sorted(t for t in tops if t not in sys.stdlib_module_names)
    assert outside == [], outside


def test_the_unknown_key_code_is_spelled_as_the_door_spells_it():
    assert stdio_shim.UNKNOWN_TOKEN_REASON == REASON_UNKNOWN_TOKEN
