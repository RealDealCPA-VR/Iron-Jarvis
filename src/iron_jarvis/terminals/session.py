"""A single live terminal session — an id'd wrapper around a :class:`PtyBackend`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.ids import new_id, utcnow
from .backend import PtyBackend, default_backend
from .shells import resolve_shell

#: How much recent output a session retains for the per-terminal AI assist.
TAIL_MAX_BYTES = 16 * 1024

#: ANSI escape sequences (CSI + OSC) — stripped from the AI-facing tail so the
#: model reads clean text instead of color/cursor noise.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    r"|\x1b[@-_]"  # lone two-byte escapes
)


class TerminalSession:
    """One real shell the user can type into, streamed over a WebSocket.

    The backend is injectable (tests pass a :class:`FakeBackend`); when omitted
    the best backend for the current OS is built via :func:`default_backend`.
    """

    def __init__(
        self,
        cwd: str | None = None,
        shell: str | None = None,
        *,
        argv: list[str] | None = None,
        cols: int = 80,
        rows: int = 24,
        backend: PtyBackend | None = None,
    ) -> None:
        if argv is None:
            shell, argv = resolve_shell(shell)
        self.id = new_id("term")
        self.cwd = cwd or str(Path.home())
        self.shell = shell or "shell"
        self.argv = list(argv)
        self.cols = cols
        self.rows = rows
        self.created_at = utcnow()
        self.backend: PtyBackend = backend if backend is not None else default_backend()
        self._started = False
        # Bounded tail of recent output — context for the per-terminal AI assist.
        self._tail = bytearray()

    def start(self, env: dict | None = None) -> "TerminalSession":
        """Spawn the shell (idempotent)."""
        if not self._started:
            self.backend.start(self.argv, self.cwd, env, self.cols, self.rows)
            self._started = True
        return self

    def write(self, data: str | bytes) -> None:
        self.backend.write(data)

    def read(self, max_bytes: int = 65536) -> bytes:
        """Non-blocking read of pending output (``b""`` if nothing ready)."""
        data = self.backend.read_nonblocking(max_bytes)
        if data:
            self._tail += data
            if len(self._tail) > TAIL_MAX_BYTES:
                del self._tail[: len(self._tail) - TAIL_MAX_BYTES]
        return data

    def output_tail(self) -> str:
        """Recent output as CLEAN text (ANSI stripped) for the AI assist."""
        text = bytes(self._tail).decode("utf-8", "replace")
        return _ANSI_RE.sub("", text)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.backend.resize(cols, rows)

    def kill(self) -> None:
        self.backend.kill()

    @property
    def alive(self) -> bool:
        return self._started and self.backend.is_alive()

    @property
    def exit_code(self) -> int | None:
        return self.backend.exit_code

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "shell": self.shell,
            "argv": list(self.argv),
            "cols": self.cols,
            "rows": self.rows,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "created_at": self.created_at.isoformat(),
        }
