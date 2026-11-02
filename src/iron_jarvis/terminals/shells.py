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

v1.422.2 (the owner's open question, decided the cautious way): Windows
PowerShell 5.1's script policy on a client PC is ``Restricted`` when NOBODY
set one, which refuses every ``.ps1`` — and npm puts ``claude.ps1``,
``codex.ps1``, ``opencode.ps1`` and ``pi.ps1`` first on ``PATH``, so the
coding CLIs failed to start in a new terminal on such a PC (the old default,
PowerShell 7, defaults to ``RemoteSigned`` and ran them). So a NEW Windows
PowerShell terminal gets ``-ExecutionPolicy RemoteSigned`` — PowerShell 7's
own default — ONLY when no script policy is set anywhere it could be
(:func:`script_policy_unset`: group policy for the machine or the user, the
machine's own, the user's own, or ``PSExecutionPolicyPreference`` in the
environment). It is the PROCESS scope: that terminal only, nothing on the PC
changes, and a group policy would still win. A policy anyone DID set (even
``Restricted``, even ``AllSigned``) is respected and the flag is not added.
Review: a RESTORED Windows PowerShell pane is a new process too, so its flag
is decided by the same rule when its saved argv is one Iron Jarvis gave
(:func:`restored_argv`) — a saved flag would beat a policy the user set since
(process scope wins over the user's and the machine's own); any other saved
argv is kept as it was.
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
    each absolute, local ``PATH`` folder's (``shutil.which`` stops at the
    first, which is often the Store alias), then the usual install folders."""
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
        # of a share path is an SMB connection (the v1.379.0 lesson). Nor a
        # RELATIVE one (".", "bin"): it would be read against the daemon's
        # current folder, and a pwsh.exe dropped there must never become
        # every new terminal's shell.
        if folder and os.path.isabs(folder) and not folder.startswith(("\\\\", "//")):
            add(os.path.join(folder, "pwsh.exe"))
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")):
        if base:
            for sub in ("7", "7-preview"):
                add(os.path.join(base, "PowerShell", sub, "pwsh.exe"))
    # v1.422.0 review: no ``shutil.which("pwsh")`` after the walk above. It
    # looked in EVERY ``PATH`` folder (shares included, under each PATHEXT
    # ending) and, on Windows, in the current folder FIRST — what the comment
    # above promises never happens. The walk already covers every ``PATH``
    # folder that may be read.
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


#: v1.422.2: where a Windows PowerShell script policy can be SET (registry
#: path under its hive, value name). Any of them present = someone chose.
_POLICY_KEYS: tuple[tuple[str, str, str], ...] = (
    ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "ExecutionPolicy"),
    ("HKLM", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "EnableScripts"),
    ("HKCU", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "ExecutionPolicy"),
    ("HKCU", r"SOFTWARE\Policies\Microsoft\Windows\PowerShell", "EnableScripts"),
    ("HKLM", r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell", "ExecutionPolicy"),
    ("HKCU", r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell", "ExecutionPolicy"),
)
#: PowerShell 7's own default policy, given to a new Windows PowerShell
#: terminal only when no policy is set (process scope).
NEW_TERMINAL_POLICY = "RemoteSigned"


def _registry_value(hive: str, path: str, name: str) -> object | None:
    """The value, or None when it is not there. Raises OSError when the
    registry cannot be read (the caller then changes nothing)."""
    import winreg

    root = winreg.HKEY_LOCAL_MACHINE if hive == "HKLM" else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    return value


def script_policy_unset() -> bool:
    """True only when NO Windows PowerShell script policy is set on this PC
    for this user (v1.422.2): no group policy, no machine or user policy, no
    ``PSExecutionPolicyPreference``. Reads the registry only (never starts
    PowerShell); any read it cannot do answers False — when unsure, the
    terminal is started exactly as before. Windows only."""
    if sys.platform != "win32":
        return False
    if (os.environ.get("PSExecutionPolicyPreference") or "").strip():
        return False
    try:
        for hive, path, name in _POLICY_KEYS:
            value = _registry_value(hive, path, name)
            if value is not None and str(value).strip() != "":
                return False
    except Exception:  # noqa: BLE001 — unsure: change nothing
        return False
    return True


def _windows_powershell_argv() -> list[str]:
    argv = ["powershell", "-NoLogo"]
    if script_policy_unset():
        argv += ["-ExecutionPolicy", NEW_TERMINAL_POLICY]
    return argv


def restored_argv(name: str | None, argv: list[str]) -> list[str]:
    """The argv a RESTORED pane starts with (v1.422.2 review): its saved one,
    except that for the two argvs Iron Jarvis itself gives Windows PowerShell
    (``powershell -NoLogo``, with or without ``-ExecutionPolicy
    RemoteSigned``) the policy flag is decided by today's rule, as for a new
    terminal. A restore is a NEW PowerShell process: a saved flag would beat a
    CurrentUser / LocalMachine policy the user set since (process scope wins
    over both) — "a policy anyone set is respected" — and a pane saved before
    the flag existed, on a PC with no policy, would still refuse the coding
    CLIs' ``.ps1`` files. Any other saved argv (the Store copy's path, a
    user's own flags) is kept exactly as it was."""
    saved = list(argv)
    plain = ["powershell", "-NoLogo"]
    if name != "powershell" or saved not in (plain, [*plain, "-ExecutionPolicy", NEW_TERMINAL_POLICY]):
        return saved
    return _windows_powershell_argv()


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
        shells.append({"name": "powershell", "argv": _windows_powershell_argv(), "label": LABELS["powershell"]})
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
