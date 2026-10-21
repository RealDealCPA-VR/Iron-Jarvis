"""v1.364.0 (IJ harness Phase 1): ``shell`` takes ``timeout_s`` and ``cwd``.

Claude Code's Bash takes a per-call timeout (max 600 s); Codex's
exec_command a working folder. The REGISTERED shell is
``sandbox/shell_tool.SandboxedShellTool`` (``tools/builtins.ShellTool`` is the
shadowed placeholder — CLAUDE.md v1.175.0), so the arguments live there:

* no ``timeout_s`` = the sandbox policy's timeout, exactly as before; given,
  it is clamped to 1..600 s;
* ``cwd`` must be a folder INSIDE the workspace (``safe_path``); an
  isolating runtime (Docker) mounts the workspace and changes into it, so
  ``..`` still reaches the session's other files.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.sandbox.base import Sandbox, SandboxResult
from iron_jarvis.sandbox.manager import SandboxManager
from iron_jarvis.sandbox.native import NativeSandbox
from iron_jarvis.sandbox.policy import SandboxPolicy
from iron_jarvis.sandbox.shell_tool import MAX_TIMEOUT_S, SandboxedShellTool
from iron_jarvis.tools.base import ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    config = load_config(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1", config=config, event_bus=None, engine=None,
    )


class _RecordingNative(NativeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Path, float | None]] = []

    def run(self, command, *, cwd, timeout=None):  # noqa: ANN001
        self.calls.append((command, Path(cwd), timeout))
        return SandboxResult(stdout="ok", returncode=0, duration_s=0.01)


class _RecordingDocker(Sandbox):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, float | None]] = []

    def available(self) -> bool:
        return True

    def run(self, command, *, cwd, timeout=None):  # noqa: ANN001
        self.calls.append((command, Path(cwd), timeout))
        return SandboxResult(stdout="ok", returncode=0, duration_s=0.01)


def _with(monkeypatch, sandbox):
    monkeypatch.setattr(SandboxManager, "get", lambda self: sandbox)
    return SandboxedShellTool()


async def test_no_new_argument_runs_exactly_as_before(tmp_path, monkeypatch):
    """The policy's timeout and the workspace, as before (the CONTROL)."""
    ctx = _ctx(tmp_path)
    fake = _RecordingNative()
    res = await _with(monkeypatch, fake).execute({"command": "echo hi"}, ctx)
    assert res.ok
    policy = SandboxPolicy.from_config(getattr(ctx.config, "sandbox", {}))
    assert fake.calls == [("echo hi", ctx.workspace, policy.timeout_s)]


@pytest.mark.parametrize(
    "given, used",
    [(5, 5.0), ("12.5", 12.5), (0.2, 1.0), (10_000, MAX_TIMEOUT_S), (600, 600.0)],
)
async def test_timeout_s_is_used_and_clamped(tmp_path, monkeypatch, given, used):
    """MUTATION: ignore ``timeout_s`` (pass the policy's) -> red."""
    fake = _RecordingNative()
    res = await _with(monkeypatch, fake).execute({"command": "x", "timeout_s": given}, _ctx(tmp_path))
    assert res.ok
    assert fake.calls[0][2] == used


@pytest.mark.parametrize("given", [0, -1, "soon", True])
async def test_a_bad_timeout_is_refused_and_nothing_runs(tmp_path, monkeypatch, given):
    fake = _RecordingNative()
    res = await _with(monkeypatch, fake).execute({"command": "x", "timeout_s": given}, _ctx(tmp_path))
    assert res.ok is False and "timeout_s" in res.error
    assert fake.calls == []


async def test_cwd_runs_in_a_folder_inside_the_workspace(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "app" / "web").mkdir(parents=True)
    fake = _RecordingNative()
    res = await _with(monkeypatch, fake).execute({"command": "dir", "cwd": "app/web"}, ctx)
    assert res.ok
    assert fake.calls[0][1] == (ctx.workspace / "app" / "web").resolve()


@pytest.mark.parametrize("cwd", ["..", "../other", "C:/Windows" if sys.platform == "win32" else "/etc"])
async def test_a_cwd_outside_the_workspace_is_refused_and_nothing_runs(tmp_path, monkeypatch, cwd):
    """MUTATION: use the cwd without ``safe_path`` -> the command runs
    outside -> red."""
    fake = _RecordingNative()
    res = await _with(monkeypatch, fake).execute({"command": "dir", "cwd": cwd}, _ctx(tmp_path))
    assert res.ok is False and "outside the session's folder" in res.error
    assert fake.calls == []


async def test_a_cwd_that_is_not_a_folder_is_refused(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "f.txt").write_text("x", encoding="utf-8")
    fake = _RecordingNative()
    for cwd in ("f.txt", "missing"):
        res = await _with(monkeypatch, fake).execute({"command": "dir", "cwd": cwd}, ctx)
        assert res.ok is False and "is not a folder" in res.error
    assert fake.calls == []


async def test_an_isolating_runtime_mounts_the_workspace_and_changes_into_the_folder(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (ctx.workspace / "my app").mkdir()
    fake = _RecordingDocker()
    res = await _with(monkeypatch, fake).execute({"command": "ls", "cwd": "my app"}, ctx)
    assert res.ok
    command, cwd, _t = fake.calls[0]
    assert cwd == ctx.workspace
    assert command == "cd 'my app' && ls"


async def test_a_real_command_is_stopped_at_its_timeout(tmp_path, monkeypatch):
    """A REAL native run: a command that would sleep 60 s is stopped at its
    1 s timeout. Bound generously (well below the 60 s the regression takes)."""
    monkeypatch.setattr(
        SandboxManager, "get", lambda self: NativeSandbox(SandboxPolicy(modify_env="allow"))
    )
    ctx = _ctx(tmp_path)
    started = time.monotonic()
    res = await SandboxedShellTool().execute(
        {"command": f'"{sys.executable}" -c "import time; time.sleep(60)"', "timeout_s": 1},
        ctx,
    )
    elapsed = time.monotonic() - started
    assert res.ok is False and res.error == "command timed out"
    assert res.data["timed_out"] is True
    assert elapsed < 40, elapsed


async def test_a_real_command_runs_in_its_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(
        SandboxManager, "get", lambda self: NativeSandbox(SandboxPolicy(modify_env="allow"))
    )
    ctx = _ctx(tmp_path)
    (ctx.workspace / "sub").mkdir()
    res = await SandboxedShellTool().execute(
        {"command": f'"{sys.executable}" -c "import os; print(os.getcwd())"', "cwd": "sub"}, ctx
    )
    assert res.ok, res.error
    # The last line is the program's (an advisory line may come first).
    assert Path(res.output.strip().splitlines()[-1]).resolve() == (ctx.workspace / "sub").resolve()


def test_the_schema_offers_the_two_arguments():
    props = SandboxedShellTool().spec()["input_schema"]["properties"]
    assert props["timeout_s"]["type"] == "number" and props["cwd"]["type"] == "string"
