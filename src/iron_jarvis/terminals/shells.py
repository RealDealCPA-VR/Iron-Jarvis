"""Shell discovery — which real shells we can offer the user on this OS."""

from __future__ import annotations

import shutil
import sys


def available_shells() -> list[dict]:
    """Return the shells available on this machine as ``{name, argv}`` dicts.

    Windows offers PowerShell (and ``pwsh`` if installed) plus ``cmd``; POSIX
    offers whichever of bash / zsh / sh are on ``PATH`` (with ``sh`` as a
    guaranteed fallback).
    """
    shells: list[dict] = []
    if sys.platform == "win32":
        # Resolve pwsh to its FULL path: it's often a WindowsApps execution
        # alias (a reparse point) installed AFTER the daemon started — spawning
        # by bare name through ConPTY is flaky there, and a spawn that dies
        # instantly leaves a zombie PTY behind the pane.
        pwsh = shutil.which("pwsh")
        if pwsh:
            shells.append({"name": "pwsh", "argv": [pwsh, "-NoLogo"]})
        shells.append({"name": "powershell", "argv": ["powershell", "-NoLogo"]})
        shells.append({"name": "cmd", "argv": ["cmd"]})
    else:
        for name in ("bash", "zsh", "sh"):
            if shutil.which(name):
                shells.append({"name": name, "argv": [name]})
        if not shells:
            shells.append({"name": "sh", "argv": ["sh"]})
    return shells


def default_shell() -> dict:
    """Return the preferred shell for this OS (the first available)."""
    return available_shells()[0]


def resolve_shell(shell: str | None) -> tuple[str, list[str]]:
    """Resolve a shell name (or ``None``) to a ``(name, argv)`` pair.

    A known shell name maps to its canonical ``argv``; an unknown / explicit
    value is treated as a raw command; ``None`` selects :func:`default_shell`.
    """
    shells = available_shells()
    if shell:
        for sh in shells:
            if sh["name"] == shell:
                return sh["name"], list(sh["argv"])
        return shell, [shell]
    chosen = default_shell()
    return chosen["name"], list(chosen["argv"])
