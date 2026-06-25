"""Sandbox Manager / policy tests (§16, §17). Offline + cross-platform.

Commands use ``python -c "..."`` so they work identically on Windows and POSIX
(no reliance on bash-only builtins like echo/sleep).
"""

from __future__ import annotations

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.sandbox.docker_runtime import DockerSandbox
from iron_jarvis.sandbox.manager import SandboxManager
from iron_jarvis.sandbox.native import NativeSandbox
from iron_jarvis.sandbox.policy import SandboxPolicy
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
from iron_jarvis.tools.base import ToolContext

# Cross-platform single-quote-safe python snippets.
PRINT_42 = 'python -c "print(42)"'
SLEEP_5 = 'python -c "import time;time.sleep(5)"'
READ_SECRET = 'python -c "import os;print(os.environ.get(\'IJ_SECRET\',\'MISSING\'))"'
PRINT_2 = 'python -c "print(1+1)"'
PRINT_7 = 'python -c "print(7)"'


def test_policy_from_config_roundtrips():
    policy = SandboxPolicy.from_config(
        {"filesystem": "workspace_only", "modify_env": "deny", "memory_mb": 256}
    )
    assert policy.modify_env == "deny"
    assert policy.memory_mb == 256
    # missing keys fall back to §17 defaults
    assert policy.internet == "ask"


async def test_native_runs_basic_command(tmp_path):
    sb = NativeSandbox(SandboxPolicy())
    res = sb.run(PRINT_42, cwd=tmp_path)
    assert res.returncode == 0
    assert not res.timed_out
    assert "42" in res.stdout


async def test_native_times_out(tmp_path):
    sb = NativeSandbox(SandboxPolicy())
    res = sb.run(SLEEP_5, cwd=tmp_path, timeout=1)
    assert res.timed_out is True


async def test_native_scrubs_env_when_modify_env_deny(tmp_path, monkeypatch):
    monkeypatch.setenv("IJ_SECRET", "leak")
    sb = NativeSandbox(SandboxPolicy())  # modify_env defaults to "deny"
    res = sb.run(READ_SECRET, cwd=tmp_path)
    assert "MISSING" in res.stdout  # the inherited var was scrubbed


async def test_native_does_not_scrub_when_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("IJ_SECRET", "leak")
    sb = NativeSandbox(SandboxPolicy(modify_env="allow"))
    res = sb.run(READ_SECRET, cwd=tmp_path)
    assert "leak" in res.stdout  # env inherited when modify_env != "deny"


def test_manager_defaults_to_native():
    mgr = SandboxManager(SandboxPolicy(), prefer="native")
    assert isinstance(mgr.get(), NativeSandbox)


async def test_docker_available_is_bool_and_safe(tmp_path):
    sb = DockerSandbox(SandboxPolicy())
    avail = sb.available()  # must never raise
    assert isinstance(avail, bool)
    if not avail:
        pytest.skip("docker daemon not available")
    res = sb.run(PRINT_7, cwd=tmp_path)
    assert "7" in res.stdout


async def test_sandboxed_shell_tool_executes(tmp_path):
    config = load_config(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=config,
        event_bus=None,  # execute() does not touch the bus/engine
        engine=None,
    )
    tool = SandboxedShellTool()
    res = await tool.execute({"command": PRINT_2}, ctx)
    assert res.ok
    assert "2" in res.output
    assert res.data["returncode"] == 0
    assert res.data["timed_out"] is False
