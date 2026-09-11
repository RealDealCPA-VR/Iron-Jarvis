"""Where the user's own folders are (v1.244.0).

One question, answered once: which folder does this person call "Documents"?
On Windows that is a *Known Folder*, not a fixed path — OneDrive's "folder
backup" moves it to ``OneDrive\\Documents`` and leaves ``~\\Documents`` behind
as a folder nobody looks in — so the shell is asked first. Everything here
falls back quietly and never raises: a caller that wanted Documents and got the
home folder still has somewhere visible to put a file.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: FOLDERID_Documents — {FDD39AD0-238F-46AF-ADB4-6C85480369C7}.
_FOLDERID_DOCUMENTS = (
    0xFDD39AD0,
    0x238F,
    0x46AF,
    (0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
)


def _known_documents_windows() -> Path | None:
    """The Documents Known Folder via ``SHGetKnownFolderPath``, or None."""
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        d1, d2, d3, d4 = _FOLDERID_DOCUMENTS
        fid = _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))
        out = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(fid), 0, None, ctypes.byref(out)
        )
        try:
            if hr != 0 or not out.value:
                return None
            path = Path(out.value)
            return path if path.is_dir() else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:  # noqa: BLE001 — no shell, no answer; the fallback decides
        return None


def documents_dir() -> Path:
    """The user's Documents folder; ``~/Documents``; else the home folder."""
    if sys.platform == "win32":
        known = _known_documents_windows()
        if known is not None:
            return known
    home = Path.home()
    docs = home / "Documents"
    return docs if docs.is_dir() else home
