"""Docker container sandbox (§16) — strong isolation, degrades gracefully.

``import docker`` is done lazily inside methods so the module imports even when
the SDK or daemon is absent; :meth:`available` never raises.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Sandbox, SandboxResult
from .policy import SandboxPolicy


class DockerSandbox(Sandbox):
    """Execute commands in a one-shot container with ``cwd`` mounted at /workspace."""

    def __init__(
        self, policy: SandboxPolicy | None = None, image: str = "python:3.12-slim"
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self.image = image

    def available(self) -> bool:
        """True iff the Docker daemon is reachable; swallows all errors (§16)."""
        try:
            import docker  # lazy import

            client = docker.from_env()
            client.ping()
            return True
        except Exception:
            return False

    def run(
        self, command: str, *, cwd: Path, timeout: float | None = None
    ) -> SandboxResult:
        start = time.monotonic()
        try:
            import docker  # lazy import

            client = docker.from_env()
        except Exception as exc:  # SDK missing or daemon down
            return SandboxResult(
                stderr=f"docker unavailable: {exc}",
                returncode=127,
                duration_s=time.monotonic() - start,
            )

        limit = timeout if timeout is not None else self.policy.timeout_s
        host_dir = str(Path(cwd).resolve())
        container = None
        try:
            container = client.containers.run(
                image=self.image,
                command=["sh", "-c", command],
                working_dir="/workspace",
                volumes={host_dir: {"bind": "/workspace", "mode": "rw"}},
                mem_limit=f"{self.policy.memory_mb}m",
                network_disabled=self.policy.internet == "deny",
                detach=True,
            )
            timed_out = False
            try:
                status = container.wait(timeout=limit)
                returncode = int(status.get("StatusCode", 0))
            except Exception:  # wait timed out (or transport error) -> kill
                timed_out = True
                returncode = -1
                try:
                    container.kill()
                except Exception:
                    pass
            logs = container.logs(stdout=True, stderr=True)
            output = (
                logs.decode("utf-8", "replace")
                if isinstance(logs, (bytes, bytearray))
                else str(logs)
            )
            return SandboxResult(
                stdout=output,
                returncode=returncode,
                timed_out=timed_out,
                duration_s=time.monotonic() - start,
            )
        except Exception as exc:  # never raise out of run()
            return SandboxResult(
                stderr=f"docker run failed: {exc}",
                returncode=1,
                duration_s=time.monotonic() - start,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
