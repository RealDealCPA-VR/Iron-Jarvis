"""Sandbox security policy (§17), parsed from ``Config.sandbox``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SandboxPolicy:
    """The six §17 security toggles plus §16 resource limits.

    Defaults mirror :func:`iron_jarvis.core.config.default_sandbox_policy`.
    Resource limits (cpu/memory/timeout) are enforced by isolating runtimes
    (Docker); the native runtime only enforces ``timeout_s`` and ``modify_env``.
    """

    filesystem: str = "workspace_only"
    internet: str = "ask"
    process_spawn: str = "allow"
    delete_files: str = "ask"
    modify_env: str = "deny"
    host_access: str = "deny"
    # resource limits (§16 Sandbox Manager)
    cpu_seconds: int = 30
    memory_mb: int = 1024
    timeout_s: float = 60.0

    @classmethod
    def from_config(cls, sandbox: dict[str, Any] | None) -> "SandboxPolicy":
        """Build a policy from a ``Config.sandbox`` dict (§17)."""
        data = dict(sandbox or {})
        return cls(
            filesystem=str(data.get("filesystem", "workspace_only")),
            internet=str(data.get("internet", "ask")),
            process_spawn=str(data.get("process_spawn", "allow")),
            delete_files=str(data.get("delete_files", "ask")),
            modify_env=str(data.get("modify_env", "deny")),
            host_access=str(data.get("host_access", "deny")),
            cpu_seconds=int(data.get("cpu_seconds", 30)),
            memory_mb=int(data.get("memory_mb", 1024)),
            timeout_s=float(data.get("timeout_s", 60.0)),
        )
