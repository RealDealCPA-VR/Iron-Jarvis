"""Terminal session backend — multiple live shells streamed to the dashboard.

Public surface:

* :class:`TerminalManager` — owns/creates/lists/kills live sessions (capped).
* :class:`TerminalSession` — one id'd shell wrapping a :class:`PtyBackend`.
* :class:`PtyBackend` — the backend Protocol (+ concrete implementations).
* :func:`available_shells` / :func:`default_shell` — shell discovery.
* :func:`default_backend` — OS-appropriate backend, no spawning.
"""

from __future__ import annotations

from .backend import (
    ConPtyBackend,
    FakeBackend,
    PipeBackend,
    PosixPtyBackend,
    PtyBackend,
    WinPtyBackend,
    default_backend,
)
from .manager import MAX_SESSIONS, TerminalManager
from .session import TerminalSession
from .shells import available_shells, default_shell, resolve_shell

__all__ = [
    "TerminalManager",
    "TerminalSession",
    "PtyBackend",
    "ConPtyBackend",
    "WinPtyBackend",
    "PosixPtyBackend",
    "PipeBackend",
    "FakeBackend",
    "default_backend",
    "available_shells",
    "default_shell",
    "resolve_shell",
    "MAX_SESSIONS",
]
