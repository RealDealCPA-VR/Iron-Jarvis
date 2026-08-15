"""v1.175.0 — the command-running tools must not block the event loop.

THE MEASURED SHAPE OF THIS BUG: ``tools/builtins.ShellTool`` has carried the
``asyncio.to_thread`` offload (and a comment explaining why) since v1.153.1 —
but ``platform.py`` registers ``sandbox/shell_tool.SandboxedShellTool`` under
the SAME tool name, and that one called ``sandbox.run`` inline. The protection
lived in the shadowed copy, so every real ``shell`` call ran
``subprocess.run(shell=True)`` on the daemon's single event loop. Same shape in
``tools/dynamic.CommandTool`` (every ``custom:*`` tool).

That freeze does not look like a freeze — it looks like "Daemon offline" (the
dashboard's fetch times out, ``lib/api.ts`` maps a dead fetch to status 0, and
Retry lands another request on the same blocked loop). It cost four hours once,
on a ``pathlib.is_file`` walk far cheaper than an arbitrary shell command.

Each test asserts BOTH halves, so deleting the offload cannot leave it green:
  * STRUCTURAL — the blocking call ran on a worker thread, not the main thread
    (the event loop's thread under pytest-asyncio).
  * BEHAVIOURAL — the loop actually serviced other work while it ran.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

from iron_jarvis.core.config import load_config
from iron_jarvis.core.models import DynamicToolRecord
from iron_jarvis.sandbox.base import SandboxResult
from iron_jarvis.sandbox.manager import SandboxManager
from iron_jarvis.sandbox.native import NativeSandbox
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.dynamic import CommandTool

#: How long the faked blocking call sleeps, and how often the watcher ticks.
#: The sleep must dwarf the tick so an offloaded run yields many ticks while an
#: inline one yields none.
_BLOCK_S = 0.30
_TICK_S = 0.01
_MIN_TICKS = 5


def _ctx(tmp_path: Path) -> ToolContext:
    config = load_config(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=config,
        event_bus=None,
        engine=None,
    )


async def _ticks_during(coro):
    """Await ``coro`` while counting how many times the event loop got control.

    Returns ``(result, ticks)``. An inline blocking call starves the ticker and
    returns ~0; a properly offloaded one lets it run throughout.
    """
    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(_TICK_S)
            ticks += 1

    task = asyncio.ensure_future(_ticker())
    try:
        result = await coro
    finally:
        stop = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, ticks


class _BlockingSandbox(NativeSandbox):
    """A native-runtime stand-in whose run() blocks like a real command."""

    def __init__(self) -> None:
        super().__init__()
        self.thread_name: str | None = None

    def run(self, command, *, cwd, timeout=None):  # noqa: ANN001, ARG002
        self.thread_name = threading.current_thread().name
        time.sleep(_BLOCK_S)
        return SandboxResult(stdout="done", returncode=0, duration_s=_BLOCK_S)


async def test_sandboxed_shell_runs_off_the_event_loop(tmp_path, monkeypatch):
    """The REGISTERED shell tool offloads its blocking run (v1.175.0)."""
    fake = _BlockingSandbox()
    monkeypatch.setattr(SandboxManager, "get", lambda self: fake)

    tool = SandboxedShellTool()
    result, ticks = await _ticks_during(
        tool.execute({"command": "echo hi"}, _ctx(tmp_path))
    )

    assert result.ok is True
    assert "done" in result.output
    # STRUCTURAL: not the loop's own thread.
    assert fake.thread_name is not None
    assert fake.thread_name != threading.main_thread().name
    # BEHAVIOURAL: the loop kept serving other work throughout.
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_docker_availability_probe_runs_off_the_event_loop(
    tmp_path, monkeypatch
):
    """``manager.get()`` is offloaded TOO — the Docker probe is a socket
    round-trip (ping + info) that hangs for seconds when Docker Desktop is
    starting or wedged, so leaving only ``run()`` in the thread would still
    block the loop before a single command is executed."""
    seen: dict[str, str] = {}

    def _from_env():
        seen["thread"] = threading.current_thread().name
        time.sleep(_BLOCK_S)
        raise RuntimeError("no docker daemon")  # -> native fallback

    mod = types.ModuleType("docker")
    mod.from_env = _from_env  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", mod)

    ctx = _ctx(tmp_path)
    # Default policy is isolating (workspace_only + internet=ask), so the tool
    # prefers Docker and therefore probes it.
    tool = SandboxedShellTool()
    (_result, ticks) = await _ticks_during(
        tool.execute({"command": "echo hi"}, ctx)
    )

    assert seen.get("thread") is not None, "the Docker probe never ran"
    assert seen["thread"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_custom_command_tool_runs_off_the_event_loop(tmp_path, monkeypatch):
    """Every ``custom:*`` tool built by an agent offloads too (v1.175.0)."""
    seen: dict[str, str] = {}
    real_run = subprocess.run

    def _slow_run(*args, **kwargs):
        seen["thread"] = threading.current_thread().name
        time.sleep(_BLOCK_S)
        return real_run(
            [sys.executable, "-c", "print('hi')"],
            capture_output=True,
            text=True,
        )

    monkeypatch.setattr("iron_jarvis.tools.dynamic.subprocess.run", _slow_run)

    record = DynamicToolRecord(
        name="echoer",
        description="echo something",
        params_json=json.dumps([{"name": "word", "type": "string", "required": True}]),
        argv_json=json.dumps([sys.executable, "-c", "print('{word}')"]),
        timeout_seconds=30,
    )
    tool = CommandTool(record)
    result, ticks = await _ticks_during(
        tool.execute({"word": "hi"}, _ctx(tmp_path))
    )

    assert result.ok is True
    assert seen.get("thread") is not None
    assert seen["thread"] != threading.main_thread().name
    assert ticks >= _MIN_TICKS, f"event loop was starved (only {ticks} ticks)"


async def test_offload_preserves_confinement_reporting(tmp_path, monkeypatch):
    """The thread hop must not lose the native-fallback warning: the runtime is
    resolved INSIDE the thread now, so the isinstance check that drives
    ``confinement``/``confinement_warning`` has to travel back out with it."""

    def _boom():
        raise RuntimeError("no docker daemon")

    mod = types.ModuleType("docker")
    mod.from_env = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", mod)

    tool = SandboxedShellTool()
    result = await tool.execute(
        {"command": f'"{sys.executable}" -c "print(1)"'}, _ctx(tmp_path)
    )

    assert result.data["confinement"] == "none"
    assert "confinement_warning" in result.data
    assert result.output.startswith("[warning]")
