"""Store (packaged) programs in a terminal pane (v1.407.1).

A Store install of PowerShell puts ``pwsh.exe`` in
``%LOCALAPPDATA%\\Microsoft\\WindowsApps`` as an *App Execution Alias*: a
zero-byte reparse point (tag ``IO_REPARSE_TAG_APPEXECLINK``) naming the real
program under ``C:\\Program Files\\WindowsApps\\<package>``. The USER BUG
("Build gets stuck at attaching to sessions and then Iron Jarvis isn't
responding"): restored panes parked in ``CreateProcessW`` and the daemon
stopped answering until the desktop watchdog killed it.

What the review MEASURED (correcting the first cut's premise): starting the
real program directly goes through the SAME package activation as the alias
(AppModel events 8001/219/211/201, the same package identity), so resolving
the alias alone does not avoid activation. Every caught hang was instead in
the pane's FIRST spawn attempt, the one asking Windows to put the program in
the daemon's job: Windows always refuses that for a packaged program
(0x80070005, "Cannot add process to Desktop AppX container") and the backend
retried without the job, so two activations per pane, two panes at once.
``backend.ConPtyBackend.start`` therefore never asks for a job for a packaged
program (:func:`is_packaged`) and starts packaged programs one at a time.

:func:`resolve` / :func:`direct_argv` stay: the target is read at EVERY spawn
(a package update changes the folder; the saved pane keeps the alias), only a
desktop-type alias (app type "0") is resolved, and it saves reading the
reparse point inside ``CreateProcessW``.

Never raises. Anything that is not exactly a readable App Execution Alias
whose target is an existing ``.exe`` returns the path unchanged, so the old
behaviour is the fallback.
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

#: ``IO_REPARSE_TAG_APPEXECLINK`` (winnt.h).
APPEXECLINK_TAG = 0x8000001B
_FSCTL_GET_REPARSE_POINT = 0x000900A8
_MAX_REPARSE = 16 * 1024


def parse_reparse_data(data: bytes) -> str | None:
    """The target path in an APPEXECLINK reparse buffer, or None.

    Layout: ReparseTag (4) | DataLength (2) | Reserved (2) | Version (4) |
    then NUL-terminated UTF-16 strings: package id, app user model id,
    target path (and, in newer versions, an app type)."""
    try:
        if len(data) < 12 or int.from_bytes(data[:4], "little") != APPEXECLINK_TAG:
            return None
        length = int.from_bytes(data[4:6], "little")
        body = data[8 : 8 + length]
        if len(body) < 4:
            return None
        parts = body[4:].decode("utf-16-le", errors="strict").split("\x00")
        if len(parts) < 3:
            return None
        target = parts[2]
        # The fourth string (when present) is the app type: "0" a desktop
        # program, "1" a UWP app (e.g. MediaPlayer.exe -> SystemUWPLauncher),
        # which must keep its activation and is never started directly.
        if len(parts) > 3 and parts[3] not in ("", "0"):
            return None
        return target or None
    except Exception:  # noqa: BLE001 — a malformed buffer is just "not an alias"
        return None


def _read_reparse(path: str) -> bytes | None:
    """Raw reparse data of ``path`` (Windows only), or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        k.DeviceIoControl.restype = wintypes.BOOL
        k.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        open_existing = 3
        open_reparse_point = 0x00200000
        backup_semantics = 0x02000000
        share_all = 0x7
        handle = k.CreateFileW(
            path, 0, share_all, None, open_existing,
            open_reparse_point | backup_semantics, None,
        )
        if handle in (None, wintypes.HANDLE(-1).value):
            return None
        try:
            buf = ctypes.create_string_buffer(_MAX_REPARSE)
            got = wintypes.DWORD(0)
            ok = k.DeviceIoControl(
                handle, _FSCTL_GET_REPARSE_POINT, None, 0, buf, _MAX_REPARSE,
                ctypes.byref(got), None,
            )
            if not ok:
                return None
            return buf.raw[: got.value]
        finally:
            k.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def is_app_alias(path: str) -> bool:
    """True when ``path`` is an App Execution Alias (by its reparse tag)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return getattr(st, "st_reparse_tag", 0) == APPEXECLINK_TAG


def is_packaged(path: str) -> bool:
    """True for a Store (packaged) program: an alias, or a path under a
    ``WindowsApps`` folder. Windows refuses to put such a program into a job
    the daemon made (it joins its own Desktop AppX container instead), so a
    pane never asks for one (``ConPtyBackend.start``). Never raises."""
    try:
        if not path:
            return False
        return is_app_alias(path) or "\\windowsapps\\" in os.path.normcase(os.path.abspath(path))
    except Exception:  # noqa: BLE001
        return False


def resolve(path: str) -> str:
    """The real program behind an App Execution Alias, else ``path``."""
    try:
        if not path or not is_app_alias(path):
            return path
        target = parse_reparse_data(_read_reparse(path) or b"")
        if (
            not target
            or not os.path.isabs(target)
            or not target.lower().endswith(".exe")
            or not os.path.isfile(target)
        ):
            return path
        return target
    except Exception:  # noqa: BLE001 — the alias is the fallback
        return path


def direct_argv(argv: list[str]) -> list[str]:
    """``argv`` with an aliased program replaced by its real path (a new
    list; the caller's list — the pane's saved argv — keeps the alias)."""
    if not argv:
        return list(argv)
    first = resolve(str(argv[0]))
    if first == argv[0]:
        return list(argv)
    log.info("starting %s directly (its Store alias can hang at start)", os.path.basename(first))
    return [first, *argv[1:]]
