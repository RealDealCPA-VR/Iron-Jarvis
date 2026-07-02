"""Manager that owns every live terminal session for the dashboard."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .backend import PipeBackend, PtyBackend
from .session import TerminalSession
from .shells import resolve_shell

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
    ) -> None:
        self.max_sessions = max_sessions
        self.max_dead_retained = max_dead_retained
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
        return True

    def kill_all(self) -> None:
        with self._lock:
            items = list(self._sessions.values())
        for session in items:
            try:
                session.kill()
            except Exception:  # pragma: no cover - defensive
                pass
