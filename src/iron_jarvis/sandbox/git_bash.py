"""Git for Windows' bash, for a command written in BASH (v1.386.0).

The IJ harness's Claude dialect shows Claude its trained command tool,
``Bash`` (``harness/dialects.py``), so Claude writes bash: ``a; b``,
heredocs, ``$(...)``, ``2>/dev/null``, ``cmd &``. On Windows the shell tool
ran that in cmd.exe, and the Phase 1 benchmark lost seven of its eleven
failures to it -- once by DELETING the project's ``server.py``
(``rm -f server.port; python server.py``: cmd has no ``;``, so ``rm`` got the
rest of the line as file names). Claude Code itself runs its Bash tool through
Git for Windows' bash on Windows (read off Claude Code 2.1.310's own code:
``CLAUDE_CODE_GIT_BASH_PATH``, then ``C:\\Program Files\\Git\\bin\\bash.exe``
and ``C:\\Program Files (x86)\\Git\\bin\\bash.exe``, then the bash beside
``where git``); this module finds the same bash for Iron Jarvis.

FINDING IT (:func:`find`, BLOCKING -- callers on the event loop use
``asyncio.to_thread``; remembered per process):

1. ``CLAUDE_CODE_GIT_BASH_PATH`` when set and valid (a ``bash.exe`` /
   ``sh.exe`` that is not WSL and answers ``--version`` as GNU bash);
2. the Git for Windows folder ``git.exe`` is installed in (``<Git>\\cmd\\git.exe``,
   ``<Git>\\mingw64\\bin\\git.exe``, ``<Git>\\bin\\git.exe``): its
   ``bin\\bash.exe``, else its ``usr\\bin\\bash.exe``;
3. the standard install folders: Program Files, Program Files (x86),
   ``%LOCALAPPDATA%\\Programs\\Git``, a scoop install.

NEVER ``C:\\Windows\\System32\\bash.exe`` or a ``WindowsApps`` ``bash.exe``
(both start WSL: another filesystem, another PATH) -- refused by path, even
through the setting -- and NEVER a bare ``bash`` on PATH (on the user's normal
PATH it can be WSL's). A file found is verified once (it exists and answers
``--version`` within :data:`VERSION_TIMEOUT_S`); a miss is looked for again
after :data:`MISS_RECHECK_S` (Git installed later is found without a
restart); a changed setting or PATH, or :func:`forget` (Re-detect), looks
again at once.

RUNNING IN IT (:func:`invocation`): the model's command reaches bash EXACTLY.
It is NOT put on the command line: the MSYS2 runtime parses a Windows command
line its own way -- measured on this PC's Git 5.2.37, ``\\\\`` inside quotes
became ``\\`` and an unquoted word is glob-expanded -- so the command rides an
environment variable (:data:`COMMAND_VAR`) and the command line is the
constant :data:`RUNNER`, which takes the variable out of the environment its
children see and ``eval``s it (Claude Code ``eval``s its commands too).
``<Git>\\bin\\bash.exe`` puts Git's ``/mingw64/bin`` and ``/usr/bin`` first on
PATH itself; for a ``usr\\bin\\bash.exe`` this module prepends them.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Claude Code's own setting for the bash it runs (honoured the same way).
ENV_OVERRIDE = "CLAUDE_CODE_GIT_BASH_PATH"
#: The environment variable the command rides in (never on the command line).
COMMAND_VAR = "IJ_BASH_COMMAND"
#: bash's whole command line: take the command out of the environment the
#: command's own children get, then run it.
RUNNER = f'export -n {COMMAND_VAR}; eval "${COMMAND_VAR}"'
#: The longest command an environment variable can carry on Windows.
MAX_COMMAND_CHARS = 32_000
#: How long ``bash --version`` may take when a file is first verified.
VERSION_TIMEOUT_S = 10.0
#: How long "not found" is believed before looking again.
MISS_RECHECK_S = 60.0
#: The names a bash may have (Claude Code's own list).
BASH_NAMES = frozenset({"bash.exe", "sh.exe", "bash", "sh"})


@dataclass(frozen=True)
class GitBash:
    """A verified bash: the file, what ``--version`` said (first line), where
    it was found (``setting`` / ``git`` / ``standard``), the folders it puts
    first on PATH (``mingw64\\bin``, ``usr\\bin``) and whether it does that
    itself (``<Git>\\bin\\bash.exe``, the launcher Claude Code's docs name) or
    needs them prepended (a ``usr\\bin\\bash.exe``)."""

    path: str
    version: str
    source: str
    path_dirs: tuple[str, ...]
    sets_path: bool


_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"key": None, "value": None, "at": 0.0}
#: (normcase path, mtime_ns, size) -> version line: a file is run once.
_VERIFIED: dict[tuple[str, int, int], str] = {}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _windows_dir() -> str:
    return os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"


def is_wsl_launcher(path: str) -> bool:
    """Is ``path`` a ``bash.exe`` that starts WSL (System32 / SysWOW64 /
    Sysnative, or a ``WindowsApps`` alias) -- the path itself or what it
    links to?"""
    names = {_norm(path)}
    try:
        names.add(_norm(os.path.realpath(path)))
    except (OSError, ValueError):
        pass
    win = _norm(_windows_dir())
    blocked = {os.path.join(win, sub) for sub in ("system32", "syswow64", "sysnative")}
    for name in names:
        folder = os.path.dirname(name)
        if folder in blocked:
            return True
        parts = name.replace("/", "\\").split("\\")
        if "windowsapps" in parts:
            return True
        if os.path.basename(name) in ("wsl.exe", "wslhost.exe"):
            return True
    return False


def _git_root_of(bash_path: str) -> str:
    """The Git for Windows folder a bash belongs to, or "" (it holds
    ``usr\\bin\\bash.exe``)."""
    folder = os.path.dirname(os.path.abspath(bash_path))
    for candidate in (os.path.dirname(folder), os.path.dirname(os.path.dirname(folder))):
        if candidate and os.path.isfile(os.path.join(candidate, "usr", "bin", "bash.exe")):
            return candidate
    return ""


def _shape(path: str, version: str, source: str) -> GitBash:
    """The PATH facts of a verified bash."""
    root = _git_root_of(path)
    folder = os.path.dirname(os.path.abspath(path))
    if root and _norm(folder) == _norm(os.path.join(root, "bin")):
        # Git's launcher puts /mingw64/bin and /usr/bin first itself.
        dirs = tuple(d for d in (os.path.join(root, "mingw64", "bin"), os.path.join(root, "usr", "bin"))
                     if os.path.isdir(d))
        return GitBash(path=path, version=version, source=source, path_dirs=dirs, sets_path=True)
    dirs: list[str] = []
    if root and os.path.isdir(os.path.join(root, "mingw64", "bin")):
        dirs.append(os.path.join(root, "mingw64", "bin"))
    dirs.append(folder)
    return GitBash(path=path, version=version, source=source, path_dirs=tuple(dirs), sets_path=False)


def _verify(path: str) -> str:
    """The first line ``path --version`` prints when it is GNU bash, else "".
    BLOCKING (bounded by :data:`VERSION_TIMEOUT_S`); remembered per file."""
    from ..core import procs

    try:
        st = os.stat(path)
    except OSError:
        return ""
    key = (_norm(path), st.st_mtime_ns, st.st_size)
    with _LOCK:
        hit = _VERIFIED.get(key)
    if hit is not None:
        return hit
    try:
        res = procs.run([path, "--version"], timeout=VERSION_TIMEOUT_S, no_window=True)
    except (OSError, ValueError):
        return ""
    out = bytes(res.stdout or b"")
    version = ""
    if res.returncode == 0 and not res.timed_out and b"GNU bash" in out:
        version = out.decode("utf-8", "replace").strip().splitlines()[0].strip()
    with _LOCK:
        _VERIFIED[key] = version
        if len(_VERIFIED) > 32:
            _VERIFIED.pop(next(iter(_VERIFIED)), None)
    return version


def _acceptable(path: str) -> bool:
    return (
        bool(path)
        and os.path.basename(path).lower() in BASH_NAMES
        and os.path.isfile(path)
        and not is_wsl_launcher(path)
    )


def _roots_of_git(git_path: str) -> list[str]:
    """The Git for Windows folders ``git.exe`` may live under (its folder and
    up to two above it) that hold ``usr\\bin\\bash.exe``."""
    out: list[str] = []
    try:
        real = os.path.realpath(git_path)
    except (OSError, ValueError):
        real = git_path
    folder = os.path.dirname(real)
    for _ in range(3):
        if os.path.isfile(os.path.join(folder, "usr", "bin", "bash.exe")):
            out.append(folder)
        parent = os.path.dirname(folder)
        if not parent or parent == folder:
            break
        folder = parent
    return out


def _standard_roots() -> list[str]:
    env = os.environ
    roots = [
        os.path.join(env.get(v, ""), "Git") for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
        if env.get(v)
    ]
    roots += [r"C:\Program Files\Git", r"C:\Program Files (x86)\Git"]
    if env.get("LOCALAPPDATA"):
        roots.append(os.path.join(env["LOCALAPPDATA"], "Programs", "Git"))
    if env.get("USERPROFILE"):
        roots.append(os.path.join(env["USERPROFILE"], "scoop", "apps", "git", "current"))
    return roots


def candidates() -> list[tuple[str, str]]:
    """``(path, source)`` in the order they are tried. Never a bare PATH
    lookup for bash; ``git`` is looked up on the daemon's PATH (Claude Code's
    ``where git``) only to find the folder Git is installed in."""
    out: list[tuple[str, str]] = []
    override = os.environ.get(ENV_OVERRIDE, "").strip().strip('"')
    if override:
        out.append((override, "setting"))
    try:
        git = shutil.which("git", path=os.environ.get("PATH", ""))
    except Exception:  # noqa: BLE001
        git = None
    if git:
        for root in _roots_of_git(git):
            out.append((os.path.join(root, "bin", "bash.exe"), "git"))
            out.append((os.path.join(root, "usr", "bin", "bash.exe"), "git"))
    for root in _standard_roots():
        out.append((os.path.join(root, "bin", "bash.exe"), "standard"))
        out.append((os.path.join(root, "usr", "bin", "bash.exe"), "standard"))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for path, source in out:
        key = _norm(path)
        if key not in seen:
            seen.add(key)
            unique.append((path, source))
    return unique


def _search() -> "GitBash | None":
    for path, source in candidates():
        if not _acceptable(path):
            if source == "setting":
                logger.warning(
                    "%s does not name a usable bash.exe (missing, not bash, or WSL's); "
                    "looking for Git for Windows' bash instead", ENV_OVERRIDE,
                )
            continue
        version = _verify(path)
        if version:
            return _shape(os.path.abspath(path), version, source)
        if source == "setting":
            logger.warning("%s names a bash that did not answer --version; looking further", ENV_OVERRIDE)
    return None


def find() -> "GitBash | None":
    """The bash a bash-written command runs in on THIS PC, or None (none
    found, or not Windows -- elsewhere the platform's own shell is POSIX sh
    already). BLOCKING; remembered: a found bash while its file is there and
    the setting / PATH are unchanged, a miss for :data:`MISS_RECHECK_S`."""
    if os.name != "nt":
        return None
    key = (os.environ.get(ENV_OVERRIDE, ""), os.environ.get("PATH", ""))
    now = time.monotonic()
    with _LOCK:
        if _STATE["key"] == key:
            value = _STATE["value"]
            if value is not None and os.path.isfile(value.path):
                return value
            if value is None and now - float(_STATE["at"]) < MISS_RECHECK_S:
                return None
    found = _search()
    with _LOCK:
        _STATE.update(key=key, value=found, at=time.monotonic())
    if found is not None:
        logger.info("bash-written commands run in %s (%s)", found.path, found.version)
    return found


def forget() -> None:
    """Look again on the next :func:`find` (Re-detect)."""
    with _LOCK:
        _STATE.update(key=None, value=None, at=0.0)
        _VERIFIED.clear()


def invocation(found: GitBash, command: str, env: "dict[str, str] | None") -> tuple[list[str], dict[str, str]]:
    """``(argv, env)`` to run ``command`` in ``found``: the command in
    :data:`COMMAND_VAR`, the constant :data:`RUNNER` on the command line, and
    -- for a bash that does not set PATH itself -- Git's tool folders first on
    PATH. ``env`` None = this process's own environment (the policy lets the
    command inherit it). ``ValueError`` for a command an environment variable
    cannot carry."""
    text = str(command)
    if "\x00" in text:
        raise ValueError("the command contains a NUL character")
    if len(text) > MAX_COMMAND_CHARS:
        raise ValueError(
            f"the command is {len(text):,} characters; at most {MAX_COMMAND_CHARS:,} -- "
            "write it to a script file and run that"
        )
    out = dict(os.environ if env is None else env)
    out[COMMAND_VAR] = text
    if not found.sets_path and found.path_dirs:
        key = next((k for k in out if k.upper() == "PATH"), "PATH")
        current = out.get(key, "")
        out[key] = os.pathsep.join([*found.path_dirs, *([current] if current else [])])
    return [found.path, "-c", RUNNER], out


def bash_path_for(found: GitBash, path: str) -> str:
    """The PATH (Windows form) bash's commands look programs up on: Git's
    tool folders first, then ``path`` -- for naming the tools found on it."""
    return os.pathsep.join([*found.path_dirs, *([path] if path else [])])


def hidden_console() -> Any:
    """The STARTUPINFO ``subprocess`` gives a ``shell=True`` child on Windows
    (``STARTF_USESHOWWINDOW`` + ``SW_HIDE``): a console bash gets is hidden,
    exactly as cmd.exe's was. None elsewhere."""
    if os.name != "nt":
        return None
    import subprocess

    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return info


__all__ = [
    "COMMAND_VAR", "ENV_OVERRIDE", "GitBash", "RUNNER", "bash_path_for", "candidates", "find",
    "forget", "hidden_console", "invocation", "is_wsl_launcher",
]
