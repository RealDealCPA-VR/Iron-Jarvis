"""Sandbox Manager (§16) — selects and drives a runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import Sandbox, SandboxResult
from .docker_runtime import DockerSandbox
from .native import NativeSandbox
from .policy import SandboxPolicy


class SandboxManager:
    """Choose a sandbox runtime and run commands through it (§16)."""

    def __init__(self, policy: SandboxPolicy | None = None, prefer: str = "native") -> None:
        self.policy = policy or SandboxPolicy()
        self.prefer = prefer

    def get(self) -> Sandbox:
        """Return Docker when preferred and reachable, else the native runtime."""
        if self.prefer == "docker":
            docker = DockerSandbox(self.policy)
            if docker.available():
                return docker
        return NativeSandbox(self.policy)

    def run(
        self, command: str, cwd: Path, timeout: float | None = None
    ) -> SandboxResult:
        """Convenience: pick a runtime and run ``command``."""
        return self.get().run(command, cwd=Path(cwd), timeout=timeout)

    def run_check(
        self, command: str, cwd: Path, *, timeout: float, env: dict[str, str],
        on_spawn: Any = None, cancelled: Any = None,
    ) -> SandboxResult:
        """22-a (R1.9): Iron Jarvis's own run of a folder's tests after a
        coding tool's reply (``hosted/proof.run_checks``). ALWAYS the native
        runtime (the user's own tools and their sign-ins live on this PC),
        with the environment the caller built from ``native.build_env`` (no
        secret), bounded, killed as a tree through ``core/procs`` at its
        limit or on ``on_spawn`` / ``cancelled`` (a Stop), and nothing it
        started is kept running after it. BLOCKING."""
        return NativeSandbox(self.policy).run(
            command, cwd=Path(cwd), timeout=timeout, on_spawn=on_spawn, cancelled=cancelled,
            env=env, keep_windows=False,
        )
