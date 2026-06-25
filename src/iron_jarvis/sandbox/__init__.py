"""Sandbox Manager (§16) + Sandbox Security Policies (§17).

Provides policy parsing, a native subprocess runtime, an optional Docker
runtime, a runtime-selecting manager, and a sandboxed ``shell`` tool that
replaces the Phase 0–3 placeholder ShellTool.
"""

from __future__ import annotations

from .base import Sandbox, SandboxResult
from .docker_runtime import DockerSandbox
from .manager import SandboxManager
from .native import NativeSandbox
from .policy import SandboxPolicy
from .shell_tool import SandboxedShellTool

__all__ = [
    "Sandbox",
    "SandboxResult",
    "SandboxPolicy",
    "NativeSandbox",
    "DockerSandbox",
    "SandboxManager",
    "SandboxedShellTool",
]
