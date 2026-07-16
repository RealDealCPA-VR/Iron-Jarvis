"""Manager that owns every live terminal session for the dashboard."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .backend import PipeBackend, PtyBackend
from .session import TerminalSession
from .shells import resolve_shell

log = logging.getLogger(__name__)

#: Cap on how much per-session scrollback is persisted (bytes). Enough to show
#: meaningful history after a restart without bloating the snapshot file.
_SNAPSHOT_SCROLLBACK = 64 * 1024

#: Default cap on concurrent live sessions to prevent runaway shell spawning.
MAX_SESSIONS = 20

#: How many recently-killed (dead) sessions to retain for queryability before
#: evicting them, so a long-lived daemon's ``_sessions`` dict stays bounded.
MAX_DEAD_RETAINED = 10

#: A PTY shell that's going to die (e.g. a frozen build whose ConPTY has no
#: OpenConsole.exe host) dies within a fraction of a second; give it this long
#: to reveal itself before trusting it. Only paid ONCE per daemon run (the
#: result is cached in ``_pty_ok``), so steady-state creates have no extra cost.
_PTY_VERIFY_SECONDS = 0.7


class TerminalManager:
    """Create, look up, list, and kill multiple live terminal sessions.

    Caps the number of *live* sessions (``max_sessions``) so a misbehaving UI
    can't spawn unbounded shells. Killed sessions stay queryable (with
    ``alive=False``) but no longer count against the cap.
    """

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        max_dead_retained: int = MAX_DEAD_RETAINED,
        state_path: Path | None = None,
    ) -> None:
        self.max_sessions = max_sessions
        self.max_dead_retained = max_dead_retained
        #: Where the live-session snapshot is persisted so terminals survive a
        #: daemon restart / app update (None = persistence disabled, e.g. tests).
        self.state_path = Path(state_path) if state_path else None
        self._sessions: dict[str, TerminalSession] = {}
        # Adaptive backend health: None = unverified, True = the real PTY works
        # in this environment, False = it spawns dead shells (frozen build
        # missing the ConPTY host) so go straight to a pipe-based shell. Set once
        # by the first verified create; skips the verify wait thereafter.
        self._pty_ok: bool | None = None
        # The /terminals endpoints are sync `def`, so Starlette runs them on
        # concurrent threadpool threads. Guard the dict so a list() iteration can't
        # race a create/kill ("dictionary changed size during iteration" -> 500) and
        # the cap check-then-act can't overshoot. Reentrant: create() calls
        # purge_dead() while holding it. Per-element syscalls (info()/start()/kill())
        # run on a SNAPSHOT outside the lock so polling can't be blocked by a spawn.
        self._lock = threading.RLock()

    def create(
        self,
        cwd: str | None = None,
        shell: str | None = None,
        cols: int = 80,
        rows: int = 24,
        *,
        backend: PtyBackend | None = None,
        env: dict | None = None,
    ) -> TerminalSession:
        """Create, start, and register a new session.

        ``cwd`` defaults to the user's home; ``shell`` defaults via
        :func:`resolve_shell`. Raises :class:`RuntimeError` at the cap.
        """
        cwd = cwd or str(Path.home())
        name, argv = resolve_shell(shell)
        with self._lock:
            # Evict stale dead sessions first so the dict can't grow without bound,
            # then enforce the cap. (Registration happens after the possibly-slow
            # spawn+verify below; a rare concurrent create may overshoot the cap
            # by one, which is harmless for a human-driven, bounded action.)
            self.purge_dead()
            live = sum(1 for s in self._sessions.values() if s.alive)
            if live >= self.max_sessions:
                raise RuntimeError(
                    f"terminal session cap reached ({self.max_sessions})"
                )

        session = self._spawn(cwd, name, argv, cols, rows, backend, env)
        with self._lock:
            self._sessions[session.id] = session
        self._persist()  # keep the restart-survival snapshot current
        return session

    def _spawn(
        self, cwd, name, argv, cols, rows, backend, env
    ) -> TerminalSession:
        """Spawn a session, transparently falling back to a pipe-based shell if
        the real PTY spawns a shell that dies immediately.

        A test-injected ``backend`` is trusted as-is. Otherwise, the FIRST
        production spawn is liveness-verified: if the shell dies within
        :data:`_PTY_VERIFY_SECONDS` (the ConPTY-host-missing failure mode), we
        remember it (``_pty_ok = False``) and use :class:`PipeBackend` for this
        and all future terminals — commands still run, just without a full TTY.
        """
        if backend is not None:  # explicit backend (tests) — no verify/fallback
            session = TerminalSession(
                cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, backend=backend
            )
            session.start(env=env)
            return session

        # Known-bad PTY in this environment → pipe shell straight away (no wait).
        if self._pty_ok is False:
            return self._pipe_session(cwd, name, argv, cols, rows, env)

        session = TerminalSession(cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows)
        session.start(env=env)

        if self._pty_ok is True:  # already verified healthy — trust it, no wait
            return session

        # First spawn of the daemon's life: verify the shell STAYS alive.
        deadline = time.monotonic() + _PTY_VERIFY_SECONDS
        while time.monotonic() < deadline:
            if not session.alive:
                break
            time.sleep(0.04)
        if session.alive:
            self._pty_ok = True
            return session

        # The real PTY produced a dead shell — this environment can't host one.
        self._pty_ok = False
        try:
            session.kill()
        except Exception:  # pragma: no cover - defensive
            pass
        return self._pipe_session(cwd, name, argv, cols, rows, env)

    @staticmethod
    def _pipe_session(cwd, name, argv, cols, rows, env) -> TerminalSession:
        session = TerminalSession(
            cwd=cwd, shell=name, argv=argv, cols=cols, rows=rows, backend=PipeBackend()
        )
        session.start(env=env)
        session.degraded = True  # UI hint: basic shell, no full TTY
        return session

    def get(self, id: str) -> TerminalSession | None:
        with self._lock:
            return self._sessions.get(id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:  # snapshot under the lock; query alive/info outside it
            items = list(self._sessions.values())
        return [s.info() for s in items]

    def purge_dead(self) -> int:
        """Evict all but the most recently added dead sessions.

        Retains the last ``max_dead_retained`` dead sessions (insertion order)
        so just-killed sessions stay queryable for a bounded window, while
        older dead entries are dropped. Returns the number evicted.
        """
        with self._lock:
            dead = [sid for sid, s in self._sessions.items() if not s.alive]
            stale = dead[: -self.max_dead_retained] if self.max_dead_retained else dead
            for sid in stale:
                self._sessions.pop(sid, None)  # tolerate an already-removed key
            return len(stale)

    def kill(self, id: str) -> bool:
        with self._lock:
            session = self._sessions.get(id)
        if session is None:
            return False
        session.kill()
        self.purge_dead()
        self._persist()  # drop the closed session from the snapshot
        return True

    def kill_all(self) -> None:
        with self._lock:
            items = list(self._sessions.values())
        for session in items:
            try:
                session.kill()
            except Exception:  # pragma: no cover - defensive
                pass

    # --- Restart / update survival ---------------------------------------
    # A live shell is a child of the daemon, so an update (which restarts the
    # daemon) necessarily kills it — running programs can't be resurrected. But
    # we persist each session's identity, directory, size, and recent scrollback
    # so that on the next boot the PANES come back: a fresh shell in the same
    # cwd, under the SAME id (so the dashboard's saved layout matches), with the
    # prior history shown above the new prompt.

    def _persist(self) -> None:
        """Best-effort snapshot after a mutation; never raises."""
        try:
            self.snapshot()
        except Exception:  # pragma: no cover - persistence must never break terminals
            log.debug("terminal snapshot failed", exc_info=True)

    def snapshot(self) -> None:
        """Persist all LIVE sessions to ``state_path`` (atomic write; no-op when
        persistence is disabled)."""
        if not self.state_path:
            return
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.alive]
        out: list[dict[str, Any]] = []
        for s in sessions:
            try:
                sb = s.scrollback_bytes()[-_SNAPSHOT_SCROLLBACK:]
                out.append(
                    {
                        "id": s.id,
                        "shell": s.shell,
                        "argv": list(s.argv),
                        "cwd": s.cwd,
                        "cols": s.cols,
                        "rows": s.rows,
                        "scrollback_b64": base64.b64encode(sb).decode("ascii"),
                    }
                )
            except Exception:  # pragma: no cover - skip an odd session, keep the rest
                continue
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"terminals": out}), encoding="utf-8")
        tmp.replace(self.state_path)

    def restore(
        self, entry: dict[str, Any], *, env: dict | None = None, backend: PtyBackend | None = None
    ) -> TerminalSession | None:
        """Re-open ONE persisted session under its original id, with its prior
        scrollback preloaded. Returns None at the session cap."""
        cwd = entry.get("cwd") or str(Path.home())
        if not Path(cwd).is_dir():  # the folder may have moved/been deleted
            cwd = str(Path.home())
        name = entry.get("shell")
        argv = entry.get("argv")
        if not argv:
            name, argv = resolve_shell(name)
        try:
            cols = int(entry.get("cols") or 80)
            rows = int(entry.get("rows") or 24)
        except (TypeError, ValueError):
            cols, rows = 80, 24
        with self._lock:
            self.purge_dead()
            if sum(1 for s in self._sessions.values() if s.alive) >= self.max_sessions:
                return None
        session = self._spawn(cwd, name or "shell", list(argv), cols, rows, backend, env)
        rid = entry.get("id") or session.id
        session.id = rid
        sb = entry.get("scrollback_b64")
        if sb:
            try:
                session._tail = bytearray(base64.b64decode(sb))
                # A snapshot that FILLED its cap was sliced at a raw byte
                # offset — serve its replay from a safe boundary, not the cut.
                session._tail_truncated = len(session._tail) >= _SNAPSHOT_SCROLLBACK
            except Exception:  # pragma: no cover - bad data, keep the fresh shell
                pass
        with self._lock:
            self._sessions[rid] = session
        return session

    def rehydrate(self, *, env: dict | None = None, backend: PtyBackend | None = None) -> int:
        """On boot, re-open every persisted session. Best-effort per entry;
        returns how many were restored. No-op without a snapshot file."""
        if not self.state_path or not self.state_path.is_file():
            return 0
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        entries = data.get("terminals") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            return 0
        restored = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                session = self.restore(entry, env=env, backend=backend)
                if session is not None:
                    # No pane is attached at boot, so without a reader the fresh
                    # shell's output (banner + prompt) never reaches the tail —
                    # a studio session resumed against the STALE replayed tail
                    # would then type briefs into a bare shell. Drain from the
                    # start; it yields whenever a Build pane attaches.
                    session.start_autodrain()
                    restored += 1
            except Exception:  # pragma: no cover - one bad entry mustn't skip the rest
                log.debug("failed to restore a terminal", exc_info=True)
        if restored:
            self._persist()  # rewrite with the freshly-restored (same) set
        return restored
