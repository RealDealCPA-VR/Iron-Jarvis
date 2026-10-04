"""The environment markers a running Claude Code session gives its children.

v1.303.0, found by a LIVE end-to-end run: a daemon started from inside a Claude
Code session (a dev shell, an agent) passed these to every Build pane and every
`claude` child it spawned. Claude Code in the pane then said "Transcript saving is
off — inherited CLAUDE_CODE_CHILD_SESSION marker" (no conversation file, so
Continue on the next account had nothing to carry), and the parent's messaging
TOKEN travelled into processes it was never meant for. A Build pane and the chat
lane's `claude` child are always TOP-LEVEL sessions, so these never cross.
The user's own Claude settings (CLAUDE_CONFIG_DIR, …) are not markers.
"""

from __future__ import annotations

CLAUDE_SESSION_MARKERS: tuple[str, ...] = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_PID",
)


def drop_claude_session_markers(env: dict) -> dict:
    """Remove the markers from ``env`` in place (case-insensitive, as Windows is)
    and return it."""
    wanted = {m.upper() for m in CLAUDE_SESSION_MARKERS}
    for name in [k for k in env if str(k).upper() in wanted]:
        env.pop(name, None)
    return env
