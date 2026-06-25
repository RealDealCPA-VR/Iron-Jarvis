"""Native subprocess sandbox (§16).

Best-effort isolation: enforces ``timeout`` and, when ``modify_env == 'deny'``
(§17), a scrubbed minimal environment. Hard network/CPU/memory isolation
requires the Docker runtime — see :mod:`docker_runtime`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .base import Sandbox, SandboxResult
from .policy import SandboxPolicy


def _as_text(value: object) -> str:
    """Coerce subprocess stdout/stderr (bytes | str | None) to text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def scrubbed_env() -> dict[str, str]:
    """Minimal environment for ``modify_env == 'deny'`` (§17).

    Drops every inherited variable but keeps the bare minimum required to launch
    an interpreter: on Windows ``SystemRoot``/``COMSPEC``/``PATHEXT`` + a small
    System32 PATH; on POSIX a minimal PATH. The running interpreter's directory
    is prepended so a bare ``python`` still resolves.
    """
    env: dict[str, str] = {}
    if os.name == "nt":
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        for key in ("SystemRoot", "COMSPEC", "PATHEXT"):
            val = os.environ.get(key)
            if val:
                env[key] = val
        env.setdefault("SystemRoot", windir)
        base_path = os.pathsep.join([os.path.join(windir, "System32"), windir])
    else:
        base_path = "/usr/bin:/bin"
    py_dir = str(Path(sys.executable).parent) if sys.executable else ""
    env["PATH"] = (py_dir + os.pathsep + base_path) if py_dir else base_path
    return env


class NativeSandbox(Sandbox):
    """Run commands via ``subprocess.run`` on the host (§16, best-effort)."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def available(self) -> bool:
        """Native execution is always available."""
        return True

    def run(
        self, command: str, *, cwd: Path, timeout: float | None = None
    ) -> SandboxResult:
        limit = timeout if timeout is not None else self.policy.timeout_s
        env = scrubbed_env() if self.policy.modify_env == "deny" else None
        start = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=limit,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxResult(
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr) or f"timed out after {limit}s",
                returncode=-1,
                timed_out=True,
                duration_s=time.monotonic() - start,
            )
        return SandboxResult(
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            returncode=proc.returncode,
            timed_out=False,
            duration_s=time.monotonic() - start,
        )
