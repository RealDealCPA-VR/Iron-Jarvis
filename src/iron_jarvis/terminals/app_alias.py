"""Start a Microsoft Store app's program directly, not through its alias.

A Store install of PowerShell puts ``pwsh.exe`` in
``%LOCALAPPDATA%\\Microsoft\\WindowsApps`` as an *App Execution Alias*: a
zero-byte reparse point (tag ``IO_REPARSE_TAG_APPEXECLINK``) that Windows
resolves at ``CreateProcessW`` time by activating the package. On the user's
PC that activation sometimes never returns (v1.407.1, a USER BUG: "Build gets
stuck at attaching to sessions and then Iron Jarvis isn't responding"). The
daemon log showed both restored panes parked in ``CreateProcessW``; the v1.406.0
forensics showed no AppX activation event at all in a hung start, while a good
start logs the Store license and AppModel container lines at the spawn. A stuck
process creation inside the daemon then starves every later one, and the
daemon stops answering until the desktop watchdog kills it.

The alias's reparse data names the real program
(``C:\\Program Files\\WindowsApps\\<package>\\pwsh.exe``), which starts
directly with no activation step (measured on the user's PC: 0.7-1.8 s, the
same ``[Environment]::ProcessPath`` the alias gives). :func:`resolve` reads it
at EVERY spawn, never cached across spawns' callers: a package update changes
the folder name, and the saved pane keeps the alias, so the next spawn reads
the new target.

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
