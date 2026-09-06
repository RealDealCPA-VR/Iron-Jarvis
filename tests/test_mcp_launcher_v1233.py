"""A stdio MCP server's launcher resolves the way the doctor's check does
(v1.233.0). Live defect: ``brave_search`` (``command = "npx"``) failed with
``FileNotFoundError: [WinError 2]`` on the packaged daemon although npx was
installed — the bare name never resolves through CreateProcess (no PATHEXT)
and the per-user Node dirs were not on the GUI daemon's PATH."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.mcp import tools as mcp_tools
from iron_jarvis.mcp.tools import resolve_launcher


def _fake_find(mapping):
    def _find(cmd):
        return mapping.get(cmd)
    return _find


def test_a_bare_launcher_resolves_through_the_doctors_search(monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.terminals.ai_clis._find",
        _fake_find({"npx": r"C:\Users\me\AppData\Local\pi-node\current\npx.cmd"}),
    )
    assert resolve_launcher("npx").endswith("npx.cmd")


def test_an_explicit_path_is_never_rewritten(monkeypatch):
    monkeypatch.setattr("iron_jarvis.terminals.ai_clis._find", _fake_find({}))
    explicit = str(Path("tools") / "my-server.exe")
    assert resolve_launcher(explicit) == explicit


def test_a_missing_launcher_says_what_to_install(monkeypatch):
    monkeypatch.setattr("iron_jarvis.terminals.ai_clis._find", _fake_find({}))
    with pytest.raises(FileNotFoundError) as exc:
        resolve_launcher("npx")
    msg = str(exc.value)
    assert "npx" in msg and "Node.js" in msg and "Retry" in msg
    assert "WinError" not in msg
    with pytest.raises(FileNotFoundError) as exc2:
        resolve_launcher("uvx")
    assert "uv" in str(exc2.value)


def test_the_stdio_transport_is_built_with_the_resolved_path(monkeypatch):
    seen = {}

    class _Stdio:
        def __init__(self, command, args=None, *, env=None, cwd=None):
            seen["command"] = command
            seen["args"] = list(args or [])

    monkeypatch.setattr(mcp_tools, "StdioTransport", _Stdio)
    monkeypatch.setattr(
        "iron_jarvis.terminals.ai_clis._find",
        _fake_find({"npx": r"C:\nodejs\npx.cmd"}),
    )
    mcp_tools._build_transport(
        {"name": "brave_search", "command": "npx", "args": ["-y", "pkg"]},
        None,
    )
    assert seen["command"] == r"C:\nodejs\npx.cmd"
    assert seen["args"] == ["-y", "pkg"]


@pytest.mark.skipif(os.name != "nt", reason="the .cmd launcher shape is Windows-only")
def test_a_resolved_cmd_launcher_really_spawns_without_a_shell(tmp_path):
    """The whole point: Popen([<full path to a .cmd>, ...]) works on Windows
    (CreateProcess runs batch files by extension) — the BARE name does not."""
    launcher = tmp_path / "hello.cmd"
    launcher.write_text("@echo off" + chr(10) + "echo launched %1" + chr(10), encoding="utf-8")
    out = subprocess.run([str(launcher), "ok"], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0 and "launched ok" in out.stdout
    with pytest.raises(FileNotFoundError):
        subprocess.run(["hello", "ok"], capture_output=True, text=True, timeout=20, cwd=tmp_path)
