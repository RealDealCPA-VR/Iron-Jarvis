"""Docker container sandbox (§16) — strong isolation, degrades gracefully.

``import docker`` is done lazily inside methods so the module imports even when
the SDK or daemon is absent; :meth:`available` never raises.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Sandbox, SandboxResult
from .policy import SandboxPolicy

#: How this runtime's result reads when NO program was started: Docker could
#: not be reached, or the container could not be made. The words are the
#: whole of the result's output and stand first in it; a reader that must
#: tell "the command failed" from "nothing ran" looks for them
#: (``harness/finish``), since the return code alone cannot say (127 and 1
#: are also a program's own).
UNAVAILABLE = "docker unavailable: "
RUN_FAILED = "docker run failed: "
NOT_STARTED = (UNAVAILABLE, RUN_FAILED)


class DockerSandbox(Sandbox):
    """Execute commands in a one-shot container with ``cwd`` mounted at /workspace."""

    def __init__(
        self, policy: SandboxPolicy | None = None, image: str = "python:3.12-slim"
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self.image = image

    def available(self) -> bool:
        """True iff a LINUX-container Docker daemon is reachable; swallows all
        errors (§16). A Windows-containers daemon (Docker Desktop in Windows
        mode, GitHub windows runners) answers pings but cannot run our Linux
        sandbox image — every create fails ("Windows does not support
        PidsLimit", no linux/amd64 manifest) — so it honestly reports
        unavailable instead of failing at run time."""
        client = None
        try:
            import docker  # lazy import

            client = docker.from_env()
            client.ping()
            os_type = str((client.info() or {}).get("OSType", "")).lower()
            return os_type == "linux"
        except Exception:
            return False
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def run(
        self, command: str, *, cwd: Path, timeout: float | None = None
    ) -> SandboxResult:
        start = time.monotonic()
        try:
            import docker  # lazy import

            client = docker.from_env()
        except Exception as exc:  # SDK missing or daemon down
            return SandboxResult(
                stderr=f"{UNAVAILABLE}{exc}",
                returncode=127,
                duration_s=time.monotonic() - start,
            )

        limit = timeout if timeout is not None else self.policy.timeout_s
        host_dir = str(Path(cwd).resolve())
        # Fail-closed network: only an explicit 'allow' opens egress; both
        # 'deny' and the unattended 'ask' keep the container offline (F13).
        network_disabled = self.policy.internet != "allow"
        # CPU cap (F13): translate the CPU-seconds budget over the wall-clock
        # timeout into a nano-cpus quota, clamped to at least 1 nano-cpu.
        timeout_basis = max(float(self.policy.timeout_s), 1.0)
        nano_cpus = max(1, int(self.policy.cpu_seconds / timeout_basis * 1_000_000_000))
        container = None
        try:
            container = client.containers.run(
                image=self.image,
                command=["sh", "-c", command],
                working_dir="/workspace",
                volumes={host_dir: {"bind": "/workspace", "mode": "rw"}},
                mem_limit=f"{self.policy.memory_mb}m",
                network_disabled=network_disabled,
                nano_cpus=nano_cpus,
                pids_limit=512,
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
                stderr=f"{RUN_FAILED}{exc}",
                returncode=1,
                duration_s=time.monotonic() - start,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                client.close()  # F9: don't leak the docker client
            except Exception:
                pass
