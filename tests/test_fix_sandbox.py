"""Regression tests for sandbox audit fixes F9, F11, F13.

All Docker interaction is faked via a stub ``docker`` module injected into
``sys.modules`` so these run with no live daemon.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from iron_jarvis.core.config import load_config
from iron_jarvis.sandbox.docker_runtime import DockerSandbox
from iron_jarvis.sandbox.policy import SandboxPolicy
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
from iron_jarvis.tools.base import ToolContext


class _FakeContainer:
    def wait(self, timeout=None):
        return {"StatusCode": 0}

    def logs(self, stdout=True, stderr=True):
        return b"ok\n"

    def kill(self):
        pass

    def remove(self, force=False):
        pass


class _FakeContainers:
    def __init__(self):
        self.run_kwargs: dict | None = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return _FakeContainer()


class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainers()
        self.closed = False
        self.pinged = False

    def ping(self):
        self.pinged = True
        return True

    def info(self):
        # available() now checks the daemon's container OS — this fake models a
        # working LINUX daemon (a Windows-containers daemon can't run the
        # sandbox image and must report unavailable; see test_project_tasks).
        return {"OSType": "linux"}

    def close(self):
        self.closed = True


def _install_fake_docker(monkeypatch, client: _FakeClient) -> None:
    mod = types.ModuleType("docker")
    mod.from_env = lambda: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", mod)


def test_docker_run_network_disabled_for_default_ask_policy(tmp_path, monkeypatch):
    client = _FakeClient()
    _install_fake_docker(monkeypatch, client)
    sb = DockerSandbox(SandboxPolicy())  # internet defaults to "ask"
    res = sb.run('echo hi', cwd=tmp_path)
    assert res.returncode == 0
    kwargs = client.containers.run_kwargs
    assert kwargs is not None
    # F13: unattended 'ask' must keep the container offline (fail-closed).
    assert kwargs["network_disabled"] is True


def test_docker_run_network_enabled_only_when_allow(tmp_path, monkeypatch):
    client = _FakeClient()
    _install_fake_docker(monkeypatch, client)
    sb = DockerSandbox(SandboxPolicy(internet="allow"))
    sb.run('echo hi', cwd=tmp_path)
    assert client.containers.run_kwargs["network_disabled"] is False


def test_docker_run_passes_cpu_limit(tmp_path, monkeypatch):
    client = _FakeClient()
    _install_fake_docker(monkeypatch, client)
    sb = DockerSandbox(SandboxPolicy(cpu_seconds=30, timeout_s=60))
    sb.run('echo hi', cwd=tmp_path)
    kwargs = client.containers.run_kwargs
    # F13: a non-None CPU cap is passed and is a positive nano-cpus value.
    assert kwargs.get("nano_cpus") is not None
    assert kwargs["nano_cpus"] >= 1
    assert kwargs["nano_cpus"] == int(30 / 60 * 1_000_000_000)
    assert kwargs.get("pids_limit") is not None


def test_docker_run_closes_client(tmp_path, monkeypatch):
    client = _FakeClient()
    _install_fake_docker(monkeypatch, client)
    sb = DockerSandbox(SandboxPolicy())
    sb.run('echo hi', cwd=tmp_path)
    assert client.closed is True  # F9: client must be closed


def test_docker_available_closes_client(monkeypatch):
    client = _FakeClient()
    _install_fake_docker(monkeypatch, client)
    sb = DockerSandbox(SandboxPolicy())
    assert sb.available() is True
    assert client.pinged is True
    assert client.closed is True  # F9: available() must not leak the client


def _ctx(tmp_path) -> ToolContext:
    config = load_config(str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir()
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=config,
        event_bus=None,
        engine=None,
    )


async def test_shell_tool_annotates_confinement_none_on_native_fallback(
    tmp_path, monkeypatch
):
    # Force Docker to be unreachable so the manager falls back to native.
    def _boom():
        raise RuntimeError("no docker daemon")

    mod = types.ModuleType("docker")
    mod.from_env = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", mod)

    tool = SandboxedShellTool()
    # default policy => filesystem=workspace_only, internet=ask => isolating
    res = await tool.execute({"command": 'python -c "print(1+1)"'}, _ctx(tmp_path))
    assert res.ok
    assert "2" in res.output
    # F11: native fallback under an isolating policy is flagged unconfined.
    assert res.data["confinement"] == "native-unconfined"
    assert "confinement_warning" in res.data
    assert "warning" in res.output.lower()


async def test_shell_tool_reports_docker_confinement_when_available(
    tmp_path, monkeypatch
):
    client = _FakeClient()
    _install_fake_docker(monkeypatch, client)
    tool = SandboxedShellTool()
    res = await tool.execute({"command": 'echo hi'}, _ctx(tmp_path))
    # F11: when Docker is reachable an isolating policy runs confined.
    assert res.data["confinement"] == "sandbox"
    assert "confinement_warning" not in res.data
