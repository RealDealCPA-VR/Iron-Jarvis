"""Sandbox runtime abstractions (§16 Sandbox Manager)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    """Outcome of a sandboxed command run (§16)."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False
    duration_s: float = 0.0

    @property
    def combined(self) -> str:
        """stdout followed by stderr (convenience for callers)."""
        if self.stderr:
            return f"{self.stdout}\n{self.stderr}" if self.stdout else self.stderr
        return self.stdout


class Sandbox(ABC):
    """A command-execution sandbox (§16)."""

    @abstractmethod
    def run(
        self, command: str, *, cwd: Path, timeout: float | None = None
    ) -> SandboxResult:
        """Execute ``command`` with ``cwd`` as working directory."""
        ...

    @abstractmethod
    def available(self) -> bool:
        """Whether this runtime can actually be used right now."""
        ...
