"""Terminal modes a program in a pane can leave switched ON (v1.333.0).

THE USER'S REPORT (overnight, 2026-10-11): "nonsense typed into a pane".
Claude Code turned on any-motion SGR mouse tracking (``ESC[?1000h``,
``?1002h``, ``?1003h``, ``?1006h``, plus bracketed paste ``?2004h``) and then
died without turning it off. xterm.js has no idea the program is gone, so it
kept sending ``ESC[<35;x;yM`` for every cell the pointer crossed, the console
stayed in VT-input mode, and PowerShell printed the reports as text
(``[555;10;5M``, ``digit-argument: 3555``). Reproduced on a real ConPTY.

Three cures share this module:

* RESTORE (``manager.restore``): a restored pane replays the dead program's
  scrollback, mode switches included, so xterm came back in mouse mode for
  good (the wheel sent reports instead of scrolling, selecting needed Shift).
  :func:`restore_reset` is appended after the replayed tail.
* INPUT (``TerminalSession.write_input``): a mouse report typed into a
  Windows shell that has no program running in it is dropped
  (:func:`strip_mouse_reports`) — no shell reads mouse reports.
* LIVE (``TerminalSession``): :class:`ModeTracker` follows the switches as
  they are printed; when the program that turned mouse tracking on is gone,
  :data:`MODE_RESET` is pushed once to the scrollback and every attached
  pane, so xterm stops reporting.
"""

from __future__ import annotations

import os
import re

#: The mouse TRACKING modes (X10, normal, button-event, any-event). The
#: encodings (1005 UTF-8, 1006 SGR, 1015 urxvt) only change how a report is
#: spelled; on their own they report nothing.
MOUSE_TRACKING = frozenset({9, 1000, 1001, 1002, 1003})

#: Bracketed paste: a paste wrapped in ``ESC[200~`` … ``ESC[201~``, which a
#: shell that never asked for it shows as text.
BRACKETED_PASTE = 2004

#: The alternate screen (``?1049`` and its older spellings).
ALT_SCREEN = frozenset({47, 1047, 1049})

#: Every mouse mode and encoding off, and bracketed paste off. Harmless when
#: nothing was on: xterm ignores a reset of a mode it is not in.
MODE_RESET = (
    b"\x1b[?9l\x1b[?1000l\x1b[?1002l\x1b[?1003l"
    b"\x1b[?1005l\x1b[?1006l\x1b[?1015l\x1b[?2004l"
)

# A DEC private mode set/reset, possibly several modes in one sequence
# (``ESC[?1000;1006h``), or a full reset (RIS, ``ESC c``).
_DECSET_RX = re.compile(rb"\x1b\[\?([0-9;]{1,40})([hl])|\x1bc")

# A switch can be cut in half by the boundary between two reads; this many
# bytes of the previous chunk are scanned again with the next one.
_CARRY_BYTES = 48

#: A mouse report as xterm.js sends it: SGR (``ESC[<b;x;yM`` / ``…m``), or
#: the legacy ``ESC[M`` followed by three bytes.
_MOUSE_REPORT_RX = re.compile(r"\x1b\[<\d{1,5};\d{1,5};\d{1,5}[Mm]|\x1b\[M[\s\S]{3}")
_MOUSE_REPORT_BYTES_RX = re.compile(rb"\x1b\[<\d{1,5};\d{1,5};\d{1,5}[Mm]|\x1b\[M[\s\S]{3}")

#: The Windows shells that never read a mouse report themselves. A pane
#: running anything else (wsl, ssh, Git Bash, a raw command) may be running a
#: program the daemon cannot see as a child process, so its input is never
#: filtered.
NATIVE_SHELLS = frozenset({"pwsh", "powershell", "cmd"})


def is_native_shell(argv: list[str] | None) -> bool:
    """Is this pane's command one of :data:`NATIVE_SHELLS`?"""
    if not argv:
        return False
    name = os.path.basename(str(argv[0])).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name in NATIVE_SHELLS


class ModeTracker:
    """Which of the modes above the program in a pane has switched on, read
    off the output as it is recorded. Cheap: a chunk with no ``ESC[?`` and no
    ``ESC c`` is never scanned."""

    __slots__ = ("tracking", "paste", "alt", "_carry")

    def __init__(self) -> None:
        self.tracking: set[int] = set()
        self.paste = False
        self.alt = False
        self._carry = b""

    @property
    def mouse_on(self) -> bool:
        return bool(self.tracking)

    def feed(self, data: bytes) -> None:
        if not data:
            return
        window = self._carry + data
        self._carry = window[-_CARRY_BYTES:]
        if b"\x1b[?" not in window and b"\x1bc" not in window:
            return
        # Re-applying a switch from the carried bytes is harmless: the
        # switches are applied IN ORDER, so the newest one always wins.
        for m in _DECSET_RX.finditer(window):
            if m.group(1) is None:  # RIS: everything back to the defaults
                self.tracking.clear()
                self.paste = False
                self.alt = False
                continue
            on = m.group(2) == b"h"
            for part in m.group(1).split(b";"):
                if not part.isdigit():
                    continue
                mode = int(part)
                if mode in MOUSE_TRACKING:
                    if on:
                        self.tracking.add(mode)
                    else:
                        self.tracking.discard(mode)
                elif mode == BRACKETED_PASTE:
                    self.paste = on
                elif mode in ALT_SCREEN:
                    self.alt = on

    def reset(self) -> None:
        """What :data:`MODE_RESET` does to a terminal, applied to the tracker."""
        self.tracking.clear()
        self.paste = False


def restore_reset(tail: bytes) -> bytes:
    """What to append after a RESTORED pane's replayed scrollback: every mouse
    mode and bracketed paste off, the cursor shown again, and the main screen
    back if the dead program left the terminal on the alternate one."""
    tracker = ModeTracker()
    tracker.feed(bytes(tail))
    out = MODE_RESET + b"\x1b[?25h"
    if tracker.alt:
        out += b"\x1b[?1049l"
    return out


def has_mouse_report(data: str | bytes) -> bool:
    if isinstance(data, bytes):
        return b"\x1b[M" in data or b"\x1b[<" in data
    return "\x1b[M" in data or "\x1b[<" in data


def strip_mouse_reports(data: str | bytes) -> str | bytes:
    """``data`` without any mouse report in it (everything else untouched)."""
    if isinstance(data, bytes):
        return _MOUSE_REPORT_BYTES_RX.sub(b"", data)
    return _MOUSE_REPORT_RX.sub("", data)
