"""Read-only history of the user's OTHER coding agents (v1.290.0).

Claude Code (``~/.claude/projects/<slug>/<uuid>.jsonl``, honours
``CLAUDE_CONFIG_DIR``) and Codex CLI (``~/.codex/sessions/YYYY/MM/DD/*.jsonl``,
honours ``CODEX_HOME``) sessions, listed cheaply and loaded as the normalized
EVENT shape the detections engine scans. Nothing under those directories is
ever written, moved or deleted. Record shapes were learned from agent-beacon's
``claudesession`` / ``codexsession`` mappers (MIT, Asymptote Labs) and checked
against real local files (structure only).
"""

from __future__ import annotations

from .index import (
    HARNESSES,
    SessionNotFound,
    find_session,
    list_sessions,
    load_session,
    session_events,
)

__all__ = [
    "HARNESSES",
    "SessionNotFound",
    "find_session",
    "list_sessions",
    "load_session",
    "session_events",
]
