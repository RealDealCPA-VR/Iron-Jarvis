"""v1.291.0 (io-04): the Codex Build-pane recipe's stdio bridge must START in the
installed app, not only in a developer checkout.

``CodexRecipe.config_text`` writes ``command = sys.executable`` and, before this
fix, ``args = ["-m", "iron_jarvis.mcpserver.stdio_shim"]``. Frozen,
``sys.executable`` is ``ironjarvis.exe`` — the Typer CLI — which rejects ``-m``
("No such option") before any bridge exists. ``_shim_available()`` still said
yes because the module IS bundled, so the pane reported "configured" and Codex
got nothing. Both halves are pinned here:

* the config shape follows ``sys.frozen`` (parsed as real TOML, both shapes);
* frozen availability is the hidden ``mcp-stdio`` subcommand being registered,
  with an anti-vacuity control that unregisters it and watches the answer flip;
* the subcommand really bridges a JSON-RPC ``initialize`` over stdio when run
  through the FROZEN ENTRY POINT'S OWN CODE (``packaging/ironjarvis_entry.py``)
  under the development interpreter, against a fake daemon — the closest a
  test can get to the exe without a PyInstaller build. Stdout is asserted to be
  EXACTLY the one relayed line: Typer/Rich must never print onto the wire.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from iron_jarvis.daemon import cli as daemon_cli
from iron_jarvis.mcpserver import stdio_shim
from iron_jarvis.terminals import recipes
from iron_jarvis.terminals.recipes import CodexRecipe

ROOT = Path(__file__).resolve().parents[1]
FROZEN_ENTRY = ROOT / "packaging" / "ironjarvis_entry.py"
URL = "http://127.0.0.1:1/mcp"  # never contacted by the config-shape pins
FAKE_EXE = "C:\\Program Files\\Iron Jarvis\\resources\\daemon\\ironjarvis.exe"


def _server(text: str) -> dict:
    cfg = tomllib.loads(text)
    return cfg["mcp_servers"][recipes.SERVER_NAME]


# --------------------------------------------------------------------------- #
# The config shape follows sys.frozen
# --------------------------------------------------------------------------- #
def test_frozen_config_launches_the_exe_with_the_mcp_stdio_subcommand(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", FAKE_EXE)
    server = _server(CodexRecipe().config_text(URL, "tok"))
    assert server["command"] == FAKE_EXE
    assert server["args"] == ["mcp-stdio"], "the exe is the Typer CLI: it has no -m"
    assert server["env"][recipes.MCP_URL_ENV] == URL
    assert "tok" not in CodexRecipe().config_text(URL, "tok")


def test_dev_config_still_runs_the_shim_module_with_dash_m(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "C:\\venv\\Scripts\\python.exe")
    server = _server(CodexRecipe().config_text(URL, "tok"))
    assert server["command"] == "C:\\venv\\Scripts\\python.exe"
    assert server["args"] == ["-m", CodexRecipe.shim_module]


# --------------------------------------------------------------------------- #
# Frozen availability = the subcommand is registered (not "the module is bundled")
# --------------------------------------------------------------------------- #
def _registered_names() -> set[str]:
    return {
        (info.name or info.callback.__name__.replace("_", "-"))
        for info in daemon_cli.app.registered_commands
    }


def test_frozen_availability_is_the_registered_subcommand(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    codex = CodexRecipe()
    assert codex.shim_command in _registered_names()
    assert codex._shim_available() is True

    # Anti-vacuity control: unregister the subcommand and the SAME build, with
    # the module still importable, must now answer "unavailable" — because
    # what the frozen config launches is the subcommand, not the module.
    without = [
        info
        for info in daemon_cli.app.registered_commands
        if (info.name or info.callback.__name__.replace("_", "-")) != codex.shim_command
    ]
    assert len(without) == len(daemon_cli.app.registered_commands) - 1
    monkeypatch.setattr(daemon_cli.app, "registered_commands", without)
    import importlib.util

    assert importlib.util.find_spec(codex.shim_module) is not None
    assert codex._shim_available() is False


def test_the_subcommand_is_hidden_from_help():
    info = next(
        i for i in daemon_cli.app.registered_commands if i.name == CodexRecipe.shim_command
    )
    assert info.hidden is True, "a protocol endpoint, not a command for humans"


# --------------------------------------------------------------------------- #
# The subcommand really bridges JSON-RPC over stdio, through the frozen entry
# point's own code, run by the development interpreter
# --------------------------------------------------------------------------- #
class _FakeDaemon(BaseHTTPRequestHandler):
    """Answers ``initialize`` like ``POST /mcp`` does, recording what it saw."""

    seen: list[dict] = []
    auth: list[str] = []

    def do_POST(self):  # noqa: N802 - http.server's spelling
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = json.loads(body)
        _FakeDaemon.seen.append(msg)
        _FakeDaemon.auth.append(self.headers.get("Authorization", ""))
        out = json.dumps(
            {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"serverInfo": {"name": "fake"}}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.send_header(stdio_shim.SESSION_HEADER, "sess-1")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def fake_daemon():
    _FakeDaemon.seen = []
    _FakeDaemon.auth = []
    httpd = HTTPServer(("127.0.0.1", 0), _FakeDaemon)  # ephemeral port, never 8787/8788
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _frozen_argv(monkeypatch, url: str) -> tuple[list[str], dict[str, str]]:
    """The argv Codex would run, from the FROZEN-shaped config, with the exe
    replaced by the dev interpreter running the exe's own entry file."""
    real_python = sys.executable  # captured BEFORE the fake exe is patched in
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", FAKE_EXE)
    server = _server(CodexRecipe().config_text(url, "tok"))
    assert server["command"] == FAKE_EXE
    argv = [real_python, str(FROZEN_ENTRY), *server["args"]]
    return argv, dict(server.get("env", {}))


def test_mcp_stdio_bridges_initialize_over_stdio_through_the_frozen_entry_point(
    monkeypatch, fake_daemon
):
    argv, cfg_env = _frozen_argv(monkeypatch, fake_daemon)
    assert argv[2:] == ["mcp-stdio"]
    env = {**os.environ, **cfg_env, recipes.MCP_TOKEN_ENV: "tok"}
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    proc = subprocess.run(
        argv,
        input=(json.dumps(request) + "\n").encode("utf-8"),
        capture_output=True,
        env=env,
        cwd=str(ROOT),
        timeout=120,  # a guard against a hung child, not a performance assertion
    )
    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")
    assert proc.returncode == 0, stderr[-800:]
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout carries the wire and nothing else: {stdout!r}"
    reply = json.loads(lines[0])
    assert reply == {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "fake"}}}
    assert _FakeDaemon.seen == [request]
    assert _FakeDaemon.auth == ["Bearer tok"]


def test_mcp_stdio_relays_the_shim_exit_code_and_says_why_on_stderr(monkeypatch, fake_daemon):
    argv, cfg_env = _frozen_argv(monkeypatch, fake_daemon)
    env = {**os.environ, **cfg_env}
    env.pop(recipes.MCP_TOKEN_ENV, None)  # started outside a Build pane
    proc = subprocess.run(
        argv, input=b"", capture_output=True, env=env, cwd=str(ROOT), timeout=120
    )
    assert proc.returncode == 2, "the shim's own code, relayed unchanged"
    assert proc.stdout == b"", "never a byte on the wire from the CLI itself"
    assert stdio_shim.NO_TOKEN_MESSAGE in proc.stderr.decode("utf-8", "replace")
    assert _FakeDaemon.seen == []
