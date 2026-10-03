"""v1.300.0 follow-ups found by the live qualification of the native claude-cli adapter.

1. The outer bound on one call is NOT the old 240 s whole-call cap: the CLI
   streams, the idle timeout is the watchdog (as in the reference plugin), and
   a long answer still streaming must not be cut off.
2. The frozen entry point serves ``claude-inert-mcp`` WITHOUT importing the
   daemon CLI (the Claude CLI waits for this server on every model call).
3. The suite never runs the REAL ``claude`` picker handshake (a background
   refresh used to land this machine's live catalog inside a later test).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from iron_jarvis.providers import claude_models
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.claude_native import transport

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "packaging" / "ironjarvis_entry.py"


def test_a_streaming_call_is_bounded_by_idleness_not_by_the_old_whole_call_cap(monkeypatch):
    monkeypatch.setattr(claude_models, "native_model", lambda m: "claude-haiku-4-5-20251001")
    adapter = sc.ClaudeCliAdapter(model="claude-haiku-4-5", which=lambda _n: "claude")
    call = adapter._native_call("sys", [LLMMessage("user", "hi")], [], "")
    assert call.total_timeout_s == sc._CLAUDE_TOTAL_TIMEOUT_S
    # The outer cap is a safety net far beyond the idle watchdog and far beyond
    # the old 240 s cap that killed long answers mid-stream.
    assert call.total_timeout_s >= 1800
    assert call.total_timeout_s > 4 * transport.DEFAULT_IDLE_TIMEOUT_S
    assert call.idle_timeout_s == transport.DEFAULT_IDLE_TIMEOUT_S
    # Codex keeps its own whole-call cap; only claude moved.
    assert sc._TIMEOUT_S == 240


def _load_entry():
    spec = importlib.util.spec_from_file_location("ij_entry_under_test", ENTRY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_the_entry_fast_path_only_claims_the_inert_server():
    entry = _load_entry()
    assert entry.INERT_MCP_SUBCOMMAND == "claude-inert-mcp"
    # Not the inert server -> the Typer app handles it (None = no fast path).
    for argv in ([], ["serve"], ["mcp-stdio"], ["claude-inert-mcp"], ["claude-inert-mcp", "a", "b"]):
        assert entry._fast_path(argv) is None, argv
    # The subcommand name agrees with what the adapter writes into --mcp-config.
    from iron_jarvis.providers.adapters.claude_native import inert_mcp

    assert inert_mcp.SUBCOMMAND == entry.INERT_MCP_SUBCOMMAND


def test_the_entry_serves_the_inert_inventory_without_importing_the_daemon(tmp_path):
    manifest = tmp_path / "tools.json"
    tool = {"name": "get_weather", "description": "Weather — °C", "inputSchema": {"type": "object"}}
    manifest.write_text(json.dumps([tool]), encoding="utf-8")
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('e', {str(ENTRY)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"sys.argv = ['ironjarvis.exe', 'claude-inert-mcp', {str(manifest)!r}]\n"
        "try:\n"
        "    m.main()\n"
        "except SystemExit as exc:\n"
        "    code = exc.code\n"
        "heavy = sorted(n for n in sys.modules if n.startswith(('iron_jarvis.daemon', 'fastapi', 'sqlalchemy')))\n"
        "sys.stderr.write('EXIT=%r HEAVY=%r' % (code, heavy))\n"
    )
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_weather"}},
    ]
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        input="".join(json.dumps(x) + "\n" for x in lines).encode("utf-8"),
        capture_output=True,
        timeout=60,
        cwd=str(ROOT),
    )
    err = proc.stderr.decode("utf-8", "replace")
    assert "EXIT=0" in err, err
    assert "HEAVY=[]" in err, err
    replies = [json.loads(x) for x in proc.stdout.decode("ascii").splitlines() if x.strip()]
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[1]["result"]["tools"] == [tool]
    assert replies[2]["result"]["isError"] is True


def test_the_suite_never_runs_the_real_claude_picker():
    # The session fixture in conftest replaces the subprocess chokepoint; a test
    # that exercises discovery installs its own fake over it.
    try:
        claude_models._run_handshake(1.0)
    except FileNotFoundError as exc:
        assert "disabled in the test suite" in str(exc)
    else:  # pragma: no cover — the guard is gone
        raise AssertionError("the real claude handshake is reachable from the test suite")
