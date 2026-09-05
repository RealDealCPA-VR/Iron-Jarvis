"""Native subprocess sandbox (§16).

Best-effort isolation: enforces ``timeout`` and, when ``modify_env == 'deny'``
(§17), a scrubbed minimal environment. Hard network/CPU/memory isolation
requires the Docker runtime — see :mod:`docker_runtime`.
"""

from __future__ import annotations

import os
import signal
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


def host_os_line(system: str | None = None) -> str:
    """One model-facing line naming the OS this install runs on (v1.228.0,
    audit T6). ``platform.system()`` by default; on Windows it also says what
    a command will and will not resolve, because a tool authored around
    POSIX ``mv`` on the dev box (Git's ``mv.EXE`` is on ITS PATH) died 22/22
    on the packaged install. Lives beside :func:`scrubbed_env_description`
    for the same reason it does: the words and the truth must not drift."""
    import platform as _platform

    name = system or _platform.system() or os.name
    if name.lower().startswith("win"):
        return (
            "Windows (cmd.exe; no POSIX mv/ls/cp/rm/cat/grep — use the "
            "built-in file tools or Windows commands)"
        )
    return f"{name} (POSIX shell)"


def scrubbed_env_description(os_name: str | None = None) -> str:
    """Model-facing truth about what :func:`scrubbed_env` leaves resolvable
    (v1.205.0).

    A live task burned 14 failed shell calls guessing ``mv``/``python``/
    ``powershell`` because nothing told the model what the scrubbed native
    environment actually contains. This one-liner is baked into the shell
    tool's registered description; it lives NEXT TO ``scrubbed_env`` so the
    description and the scrub cannot drift apart silently. Deliberately
    conservative (dev-mode ``python`` sometimes resolves via the interpreter
    dir; a packaged install's does not) — never overpromise.
    """
    if (os_name or os.name) == "nt":
        return (
            "Windows cmd.exe with a minimal scrubbed PATH — no python, no "
            "powershell, no POSIX tools (mv/ls/cp/rm/cat/grep do not "
            "resolve); py.exe is available. Prefer the built-in tools "
            "(rename_file, list_files, grep, read_document/write_document) "
            "for file work."
        )
    return (
        "POSIX sh with a minimal scrubbed PATH (/usr/bin:/bin) — system "
        "utilities resolve, but user-installed tools, shell profiles, and "
        "virtualenvs do not. Prefer the built-in tools (rename_file, "
        "list_files, grep, read_document/write_document) for file work."
    )


def _kill_tree(proc: "subprocess.Popen[str]") -> None:
    """Kill ``proc`` AND every descendant (v1.228.0, RT3).

    ``subprocess.run(shell=True, timeout=...)`` killed only the shell
    (cmd.exe / sh): the command it had started kept running to completion,
    held the pipes open, and ``communicate()`` blocked until it exited on its
    own — measured 6 s for a 1 s timeout, marker file written after the
    "timeout". Windows: ``taskkill /T /F`` walks the process tree. POSIX: the
    child was started in its own session (``start_new_session=True``), so the
    whole process group is one ``killpg`` away. Best-effort — a kill that
    fails falls back to ``proc.kill()`` so the drain below can never hang on a
    dead handle.
    """
    try:
        if os.name == "nt":
            windir = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = os.path.join(windir, "System32", "taskkill.exe")
            if not os.path.exists(taskkill):
                taskkill = "taskkill"
            subprocess.run(
                [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — fall through to the plain kill
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


class NativeSandbox(Sandbox):
    """Run commands via ``subprocess.Popen`` on the host (§16, best-effort)."""

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
        # Popen + communicate(timeout) instead of subprocess.run (v1.228.0,
        # RT3): on a timeout the WHOLE tree is killed (see `_kill_tree`) and
        # the pipes are then drained, so the tool returns at ~timeout and the
        # command is genuinely gone — not merely reported as timed out while
        # it keeps running.
        popen_kw: dict = {}
        if os.name != "nt":
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            **popen_kw,
        )
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=15)
            except Exception:  # noqa: BLE001 — a wedged drain still returns
                out, err = "", ""
            return SandboxResult(
                stdout=_as_text(out),
                stderr=_as_text(err) or f"timed out after {limit}s",
                returncode=-1,
                timed_out=True,
                duration_s=time.monotonic() - start,
            )
        return SandboxResult(
            stdout=out or "",
            stderr=err or "",
            returncode=proc.returncode,
            timed_out=False,
            duration_s=time.monotonic() - start,
        )
