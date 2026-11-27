"""Is the PC idle? Seconds since the last keyboard or mouse input on THIS PC.

The intelligence plan, release 9: a model the user has not measured is tried
only while the PC is idle, and the try stops the moment the user types. The
one reading both need is the time since the last input.

Windows: ``user32.GetLastInputInfo`` against ``kernel32.GetTickCount`` (both
32-bit millisecond counters, so the difference is taken modulo 2**32). A plain
user32 call that returns at once: not COM, not WinRT, nothing that can wait
inside Windows. Every other system answers ``None`` ("cannot tell"), which
the trial reads as NOT idle, so on Linux and Mac no model is tried unasked
(Measure still works there).
"""

from __future__ import annotations

import sys


def input_idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input on this PC, or ``None``
    when this system cannot tell. Never raises."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = _LastInput()
        info.cbSize = ctypes.sizeof(_LastInput)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF  # type: ignore[attr-defined]
        return ((now - int(info.dwTime)) % 0x100000000) / 1000.0
    except Exception:  # noqa: BLE001 — cannot tell
        return None
