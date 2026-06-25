"""Sandbox Manager (§16) — selects and drives a runtime."""

from __future__ import annotations

from pathlib import Path

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
