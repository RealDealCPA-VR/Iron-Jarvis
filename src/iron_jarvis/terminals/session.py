"""A single live terminal session — an id'd wrapper around a :class:`PtyBackend`."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from ..core.ids import new_id, utcnow
from .backend import PtyBackend, default_backend
from .shells import resolve_shell

#: How much recent output a session retains — doubles as the scrollback replayed
#: to a RE-ATTACHING pane (tab switch / navigation) so it shows its history
#: instead of a blank screen, and as the context for the per-terminal AI assist.
TAIL_MAX_BYTES = 256 * 1024

#: ANSI escape sequences (CSI + OSC) — stripped from the AI-facing tail so the
#: model reads clean text instead of color/cursor noise.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    r"|\x1b[()#%*+][0-9A-Za-z]"  # charset designation (ESC ( B …) — ConPTY
    # interleaves these MID-STRING, which used to split mode banners like
    # "auto-accept edits on" so detection missed them (live-hit 2026-07-07)
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
        # Serializes write(): the WS handler, the studio /say endpoint, and the
        # studio automode background thread all type into the same PTY — without
        # a lock a Shift+Tab keystroke can land in the middle of a typed brief.
        self._write_lock = threading.Lock()
        # Bounded tail of recent output — context for the per-terminal AI assist.
        self._tail = bytearray()
        # True when we fell back to a pipe-based shell (no real TTY) because the
        # PTY backend spawned a shell that died immediately (e.g. a frozen build
        # missing the ConPTY host exe). Commands still run; fancy TTY apps don't.
        self.degraded = False
        # --- Background auto-drain (for the Creative Studio) ------------------
        # A Build-page pane drains the PTY through its WebSocket; the Studio,
        # by contrast, drives the CLI purely over HTTP with NO socket attached.
        # Without a reader the output is never consumed: the tail stays blank,
        # auto-mode detection is blind, and a chatty full-screen TUI (Claude
        # Code) STALLS the moment the OS output buffer fills. start_autodrain()
        # spawns a reader that keeps the PTY flowing regardless. It steps aside
        # whenever a live pane is attached (see add_consumer) so the two never
        # race for the same bytes.
        self._consumers = 0
        self._consumer_lock = threading.Lock()
        self._drain_thread: threading.Thread | None = None
        self._drain_stop = threading.Event()
        # monotonic timestamp of the last NON-EMPTY read — "is the CLI actively
        # printing?" signal for the studio's phase detection (a running TUI
        # repaints its status bar about once a second).
        self.last_output_at: float = 0.0

    def start(self, env: dict | None = None) -> "TerminalSession":
        """Spawn the shell (idempotent)."""
        if not self._started:
            self.backend.start(self.argv, self.cwd, env, self.cols, self.rows)
            self._started = True
        return self

    def write(self, data: str | bytes) -> None:
        with self._write_lock:  # one writer at a time — keystrokes never interleave
            self.backend.write(data)

    def read(self, max_bytes: int = 65536) -> bytes:
        """Non-blocking read of pending output (``b""`` if nothing ready)."""
        data = self.backend.read_nonblocking(max_bytes)
        if data:
            self._tail += data
            if len(self._tail) > TAIL_MAX_BYTES:
                del self._tail[: len(self._tail) - TAIL_MAX_BYTES]
            self.last_output_at = time.monotonic()
        return data

    # --- Live consumers + background auto-drain --------------------------

    def add_consumer(self) -> None:
        """Register a live output consumer (a Build-page WebSocket pane). While
        any consumer is attached it does the reading and fills the tail itself,
        so the background auto-drain steps aside to avoid stealing its bytes."""
        with self._consumer_lock:
            self._consumers += 1

    def remove_consumer(self) -> None:
        """Drop a previously-registered live consumer."""
        with self._consumer_lock:
            if self._consumers > 0:
                self._consumers -= 1

    @property
    def has_consumer(self) -> bool:
        with self._consumer_lock:
            return self._consumers > 0

    def start_autodrain(self) -> None:
        """Begin draining output in the background so it's captured even when no
        WebSocket is attached (the Creative Studio case). Idempotent — safe to
        call more than once on the same session."""
        if self._drain_thread is not None:
            return
        self._drain_stop.clear()
        thread = threading.Thread(
            target=self._drain_loop, name=f"drain-{self.id}", daemon=True
        )
        self._drain_thread = thread
        thread.start()

    def _drain_loop(self) -> None:
        """Keep the PTY flowing into the tail. Yields to a live pane (which
        reads and fills the tail itself); when alone, reads and discards the
        live bytes — the scrollback captured in the tail is what the Studio
        tail endpoint serves and what auto-mode detection reads."""
        while not self._drain_stop.is_set():
            if not self.alive:
                break
            if self.has_consumer:  # a WS pane is reading — don't steal its bytes
                time.sleep(0.05)
                continue
            try:
                data = self.read()  # into _tail; live bytes discarded (nobody watching)
            except Exception:  # pragma: no cover - a dying backend just ends the loop
                break
            if not data:
                time.sleep(0.03)

    def output_tail(self) -> str:
        """Recent output as CLEAN text (ANSI stripped) for the AI assist.

        Only the last ~32KB is decoded — the AI needs a short window, and the
        full scrollback can be up to :data:`TAIL_MAX_BYTES`."""
        text = bytes(self._tail[-32 * 1024:]).decode("utf-8", "replace")
        return _ANSI_RE.sub("", text)

    def scrollback_bytes(self) -> bytes:
        """The raw recent output (with ANSI intact) to REPLAY into a re-attaching
        pane so it renders its history instead of a blank screen."""
        return bytes(self._tail)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.backend.resize(cols, rows)

    def kill(self) -> None:
        self._drain_stop.set()  # stop the background reader before the PTY dies
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
            "degraded": self.degraded,
            "created_at": self.created_at.isoformat(),
        }
