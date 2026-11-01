"""Shell discovery — which real shells we can offer the user on this OS.

v1.422.0 (audit gap B6): a NEW terminal no longer defaults to the Microsoft
Store copy of PowerShell 7. The Store install puts ``pwsh.exe`` in
``%LOCALAPPDATA%\\Microsoft\\WindowsApps`` as an App Execution Alias
(``terminals.app_alias``), and starting it goes through package activation —
the very program whose process creation never returned in the v1.407.0,
v1.409.1 and v1.419.1 freezes (pane restores parked in ``CreateProcessW``).
So on Windows the order is:

* a REAL PowerShell 7 install (not packaged — an MSI under Program Files, a
  dotnet tool, scoop …), found on ``PATH`` past any alias or in the usual
  install folders: ``pwsh``, first, the default;
* else Windows PowerShell 5.1 (``powershell``, part of Windows): the default;
* the Store copy stays in the menu (the user can still pick it) as ``pwsh``
  when it is the only PowerShell 7, labelled as the Microsoft Store copy and
  listed after Windows PowerShell; a PC with both keeps the real install only
  under that name;
* ``cmd`` last.

The NAMES are unchanged (``pwsh``, ``powershell``, ``cmd``): a pane's saved
``shell`` and every reader that keys on the name (``terminals.modes``, the
quoting in ``routes/iron_proxy.shell_line``, ``terminals.handoff``) mean the
same thing. A restored pane keeps the argv it was saved with
(``TerminalManager.restore``) — only a NEW terminal follows the new default.
Each entry carries a ``label`` for the menu; the route also says which shell
is the ``default``. Not cached: a PowerShell installed after the daemon
started is found at the next terminal (why this has always been a fresh look).
"""

from __future__ import annotations

import os
import shutil
import sys

#: The menu's words for each shell name (POSIX shells are their own name).
LABELS = {
    "pwsh": "PowerShell 7",
    "powershell": "Windows PowerShell",
    "cmd": "Command Prompt",
}
#: The Store copy's label (it stays pickable, said for what it is).
STORE_LABEL = "PowerShell 7 (Microsoft Store copy)"


def _pwsh_candidates() -> list[str]:
    """Every ``pwsh.exe`` this PC offers, in the order a user would expect:
    each ``PATH`` folder's (``shutil.which`` stops at the first, which is often
    the Store alias), then the usual install folders."""
    found: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.lexists(path):
            seen.add(key)
            found.append(path)

    for folder in (os.environ.get("PATH") or "").split(os.pathsep):
        folder = folder.strip().strip('"')
        # A PATH folder on a network share is never looked in here: a stat
        # of a share path is an SMB connection (the v1.379.0 lesson).
        if folder and not folder.startswith(("\\\\", "//")):
            add(os.path.join(folder, "pwsh.exe"))
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")):
        if base:
            for sub in ("7", "7-preview"):
                add(os.path.join(base, "PowerShell", sub, "pwsh.exe"))
    first = shutil.which("pwsh")
    if first:
        add(first)
    return found


def _powershell_7() -> tuple[str | None, str | None]:
    """``(real install, Store copy)`` of PowerShell 7 — either may be None.
    The Store test is :func:`app_alias.is_packaged` (an App Execution Alias,
    or a path under a ``WindowsApps`` folder)."""
    from .app_alias import is_packaged

    real = store = None
    for path in _pwsh_candidates():
        if is_packaged(path):
            store = store or path
        elif os.path.isfile(path):
            real = real or path
    return real, store


def available_shells() -> list[dict]:
    """Return the shells available on this machine as ``{name, argv, label}``
    dicts, the default FIRST.

    Windows offers a real PowerShell 7 (when installed), Windows PowerShell,
    the Store copy of PowerShell 7 (when it is the only one), and ``cmd``;
    POSIX offers whichever of bash / zsh / sh are on ``PATH`` (with ``sh`` as
    a guaranteed fallback).
    """
    shells: list[dict] = []
    if sys.platform == "win32":
        # Resolve pwsh to its FULL path: spawning by bare name through ConPTY
        # is flaky for an alias installed AFTER the daemon started, and a
        # spawn that dies instantly leaves a zombie PTY behind the pane.
        real, store = _powershell_7()
        if real:
            shells.append({"name": "pwsh", "argv": [real, "-NoLogo"], "label": LABELS["pwsh"]})
        shells.append({"name": "powershell", "argv": ["powershell", "-NoLogo"], "label": LABELS["powershell"]})
        if store and not real:
            shells.append({"name": "pwsh", "argv": [store, "-NoLogo"], "label": STORE_LABEL, "store": True})
        shells.append({"name": "cmd", "argv": ["cmd"], "label": LABELS["cmd"]})
    else:
        for name in ("bash", "zsh", "sh"):
            if shutil.which(name):
                shells.append({"name": name, "argv": [name], "label": name})
        if not shells:
            shells.append({"name": "sh", "argv": ["sh"], "label": "sh"})
    return shells


def default_shell() -> dict:
    """Return the preferred shell for this OS (the first available — never
    the Store copy of PowerShell, v1.422.0)."""
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
