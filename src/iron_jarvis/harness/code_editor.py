"""OPEN A FILE IN THE USER'S CODE EDITOR (v1.405.0) — VS Code or Cursor, at a
line, from a coding session's review pane, its steps and its reviewer's
findings.

The user's problem (docs/IJ-HARNESS-WALKTHROUGH.md): a coding session had no
way to open a file in the editor the user lives in. Both editors register a
URL protocol on install (``vscode://``, ``cursor://``) whose ``file`` route
opens a path at a line and column:

    vscode://file/C:/Users/me/proj/src/app.py:12:1

THREE RULES, each pinned (tests/test_code_editor_v1405.py):

* **The URL is built HERE, never taken from a client.** The route takes a
  PATH, a line and a column; :func:`editor_url` makes the URL from them. A
  client that sends ``vscode://...`` as its "path" is refused (not absolute).
* **The path is checked before the disk is touched by it.** A network
  spelling (``\\\\host\\share``, ``//host/share``) or a device path
  (``\\\\?\\``, ``\\\\.\\``) is refused FIRST: opening a model-written UNC path
  would make the EDITOR open an SMB connection to that host (the v1.379.0
  lesson — a sign-in hash leaves the PC). Then: absolute, the file policy
  (``core.fs_policy.fs_read_ok`` + the protected roots), an existing FILE.
* **Detection never raises and never guesses.** On Windows the editors are
  found by their URL protocol keys (``HKEY_CLASSES_ROOT\\vscode``,
  ``...\\cursor``) whose open command names a program that exists; the answer
  is cached for :data:`CACHE_S`. Elsewhere it is "not known" (``None``), and
  the dashboard shows no button unless the user's setting names an editor.

Every refusal is :class:`EditorRefusal` — one plain sentence and an HTTP
status for the route.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

#: The editors, in the order "auto" prefers them: (id, label, URL scheme).
EDITORS: tuple[tuple[str, str, str], ...] = (
    ("vscode", "VS Code", "vscode"),
    ("cursor", "Cursor", "cursor"),
)
EDITOR_IDS: tuple[str, ...] = tuple(e[0] for e in EDITORS)
LABELS: dict[str, str] = {e[0]: e[1] for e in EDITORS}
SCHEMES: dict[str, str] = {e[0]: e[2] for e in EDITORS}

#: The ``code_editor`` setting's values (settings/schema.py, core/config.py).
SETTING_VALUES: tuple[str, ...] = ("auto", *EDITOR_IDS)

#: How long a detection answer is kept (an editor installed while the app runs
#: is seen within this long).
CACHE_S = 300.0

#: The highest line / column a request may name (a sane bound, not a limit of
#: either editor: no source file the user reviews is ten million lines long).
MAX_LINE = 10_000_000
MAX_COLUMN = 100_000
#: The longest path accepted (Windows' long-path limit is 32,767).
MAX_PATH_CHARS = 4096

# --------------------------------------------------------------- the words
NOT_ABSOLUTE = "Iron Jarvis needs the file's full path to open it in your editor."
NETWORK_PATH = "Iron Jarvis opens files on this PC's own drives only, not on a network share."
NOT_ALLOWED = "Iron Jarvis is not allowed to read that file, so it will not open it in your editor."
MISSING = "That file is not on this PC any more."
A_FOLDER = "That is a folder, not a file."
BAD_LINE = "The line to open must be a whole number from 1 to 10,000,000."
BAD_COLUMN = "The column to open must be a whole number from 1 to 100,000."
UNKNOWN_EDITOR = "Iron Jarvis can open files in VS Code or Cursor only."
NO_EDITOR = "Iron Jarvis could not find VS Code or Cursor on this PC. Install one, then try again."
NOT_INSTALLED = "{label} is not installed on this PC."
NOT_KNOWN_NOTE = (
    "Iron Jarvis can find VS Code and Cursor on Windows only. Choose one under "
    "Settings › Agents & automation › Code editor to open files in it."
)


class EditorRefusal(Exception):
    """One plain sentence and the HTTP status the route answers with."""

    def __init__(self, sentence: str, status: int = 400) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status


# ------------------------------------------------------------- detection
def _winreg() -> Any:
    """The ``winreg`` module, or None off Windows. Module-level so a test hands
    in a fake registry instead of the real one."""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415 — Windows only

        return winreg
    except ImportError:  # pragma: no cover — every Windows Python has it
        return None


_COMMAND_RE = re.compile(r'^\s*(?:"([^"]+)"|(\S+))')


def _program_of(command: str) -> str:
    """The program a protocol's open command runs (its first word, quoted or
    not), or ""."""
    m = _COMMAND_RE.match(command or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _scheme_installed(reg: Any, scheme: str) -> bool:
    """True when ``HKEY_CLASSES_ROOT\\<scheme>`` is a URL protocol whose open
    command names a program that exists. Never raises: anything unreadable is
    "not installed" (a stale key left by an uninstall would be a dead press)."""
    try:
        key = reg.OpenKey(reg.HKEY_CLASSES_ROOT, scheme)
    except OSError:
        return False
    try:
        try:
            reg.QueryValueEx(key, "URL Protocol")
        except OSError:
            return False
        try:
            cmd_key = reg.OpenKey(key, r"shell\open\command")
            command, _kind = reg.QueryValueEx(cmd_key, "")
        except OSError:
            return False
        program = _program_of(str(command))
        return bool(program) and os.path.isfile(program)
    except Exception:  # noqa: BLE001 — detection never raises
        return False
    finally:
        try:
            reg.CloseKey(key)
        except Exception:  # noqa: BLE001
            pass


_lock = threading.Lock()
_cache: tuple[float, dict[str, bool | None]] | None = None


def forget() -> None:
    """Drop the cached answer (tests; nothing in the app needs it)."""
    global _cache
    with _lock:
        _cache = None


def detect(*, now: float | None = None) -> dict[str, bool | None]:
    """``{editor id: installed}`` — True / False on Windows, None ("not
    known") elsewhere. Cached for :data:`CACHE_S`; never raises."""
    global _cache
    t = time.monotonic() if now is None else now
    with _lock:
        if _cache is not None and t - _cache[0] < CACHE_S:
            return dict(_cache[1])
    try:
        reg = _winreg()
        if reg is None:
            found: dict[str, bool | None] = {eid: None for eid in EDITOR_IDS}
        else:
            found = {eid: _scheme_installed(reg, SCHEMES[eid]) for eid in EDITOR_IDS}
    except Exception:  # noqa: BLE001 — never raises
        found = {eid: None for eid in EDITOR_IDS}
    with _lock:
        _cache = (t, dict(found))
    return found


def clean_setting(value: Any) -> str:
    """The ``code_editor`` setting, read tolerantly: an unknown value is
    "auto" (a hand-edited config never stops a boot)."""
    v = str(value or "").strip().lower()
    return v if v in SETTING_VALUES else "auto"


def preferred(setting: Any, installed: dict[str, bool | None]) -> str | None:
    """The editor a press opens, or None (no button).

    * "auto": VS Code when installed, else Cursor when installed; with nothing
      known (not Windows) it guesses NOTHING.
    * an editor named in the setting: that editor, unless detection KNOWS it
      is not installed (off Windows, "not known" lets the user's choice
      stand — they said they have it)."""
    s = clean_setting(setting)
    if s == "auto":
        for eid in EDITOR_IDS:
            if installed.get(eid) is True:
                return eid
        return None
    return s if installed.get(s) is not False else None


def options(setting: Any) -> dict[str, Any]:
    """What ``GET /editor/options`` answers."""
    found = detect()
    pick = preferred(setting, found)
    known = any(v is not None for v in found.values())
    return {
        "editors": [
            {"id": eid, "label": LABELS[eid], "installed": found.get(eid)} for eid in EDITOR_IDS
        ],
        "preferred": pick,
        "preferred_label": LABELS.get(pick or "", ""),
        "setting": clean_setting(setting),
        "known": known,
        "note": "" if known else NOT_KNOWN_NOTE,
    }


def choose(requested: Any, setting: Any) -> str:
    """The editor for one open: the one the request names (when installed or
    not known), else the setting's pick. Raises :class:`EditorRefusal`."""
    found = detect()
    if requested is not None and requested != "":
        if not isinstance(requested, str) or requested not in EDITOR_IDS:
            raise EditorRefusal(UNKNOWN_EDITOR, 400)
        if found.get(requested) is False:
            raise EditorRefusal(NOT_INSTALLED.format(label=LABELS[requested]), 409)
        return requested
    pick = preferred(setting, found)
    if pick is None:
        raise EditorRefusal(NO_EDITOR, 409)
    return pick


# -------------------------------------------------------------- checking
def _network_or_device(raw: str) -> bool:
    """A UNC share, a device path or an extended-length spelling. Judged on
    the TEXT, before anything touches the disk."""
    s = raw.replace("/", "\\")
    return s.startswith("\\\\")


def check_position(value: Any, *, column: bool = False) -> int | None:
    """A line or column: None when not given, else a whole number in range.
    Raises :class:`EditorRefusal` (a bool is not a number here)."""
    if value is None:
        return None
    hi = MAX_COLUMN if column else MAX_LINE
    words = BAD_COLUMN if column else BAD_LINE
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        else:
            raise EditorRefusal(words, 400)
    if value < 1 or value > hi:
        raise EditorRefusal(words, 400)
    return int(value)


def check_path(raw: Any) -> Path:
    """The file to open: absolute, on this PC's own drives, readable under the
    file policy, an existing file. Returns the NORMALISED path as the user
    spelled it (a mapped drive keeps its letter). Raises :class:`EditorRefusal`."""
    from ..core.fs_policy import fs_read_ok, is_protected_path

    if not isinstance(raw, str):
        raise EditorRefusal(NOT_ABSOLUTE, 400)
    text = raw.strip()
    if not text or len(text) > MAX_PATH_CHARS or any(ord(c) < 32 for c in text):
        raise EditorRefusal(NOT_ABSOLUTE, 400)
    # FIRST, before the disk: a network or device spelling.
    if _network_or_device(text):
        raise EditorRefusal(NETWORK_PATH, 403)
    p = Path(text)
    if not p.is_absolute():
        raise EditorRefusal(NOT_ABSOLUTE, 400)
    norm = Path(os.path.normpath(text))
    if _network_or_device(str(norm)):
        raise EditorRefusal(NETWORK_PATH, 403)
    if is_protected_path(str(norm)):
        raise EditorRefusal(NOT_ALLOWED, 403)
    ok, reason = fs_read_ok(str(norm))
    if not ok:
        # A folder-limit refusal is the policy's own plain sentence (it says
        # where to change the limit).
        raise EditorRefusal(reason or NOT_ALLOWED, 403)
    try:
        if norm.is_dir():
            raise EditorRefusal(A_FOLDER, 400)
        if not norm.is_file():
            raise EditorRefusal(MISSING, 404)
    except OSError:
        raise EditorRefusal(MISSING, 404) from None
    return norm


# ------------------------------------------------------------- the URL
def url_path(path: str | Path) -> str:
    """The path part of the editor URL: forward slashes, a leading "/", and
    every character a URL would read as something else percent-encoded
    (space, ``#``, ``%``, ``?``, non-ASCII as UTF-8). The drive's colon stays."""
    s = str(path).replace("\\", "/")
    if not s.startswith("/"):
        s = "/" + s
    return quote(s, safe="/:")


def editor_url(editor: str, path: str | Path, line: int | None = None, column: int | None = None) -> str:
    """``<scheme>://file/<path>:<line>:<column>`` — always with a line and a
    column (1 when not given), so a file name that ends in ``:<digits>`` can
    never be read as a line number."""
    if editor not in SCHEMES:
        raise EditorRefusal(UNKNOWN_EDITOR, 400)
    ln = line if isinstance(line, int) and not isinstance(line, bool) and line >= 1 else 1
    col = column if isinstance(column, int) and not isinstance(column, bool) and column >= 1 else 1
    return f"{SCHEMES[editor]}://file{url_path(path)}:{ln}:{col}"
