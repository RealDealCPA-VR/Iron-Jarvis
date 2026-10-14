"""v1.337.2 — typing into a Build pane restored after an update or a restart.

The user: "the terminals i was working in are still there, but typing into
them is choppy and will either work where i can only see few characters or
not work at all and i need to close the terminal and open a new one".

Two defects, both reproduced on a scratch daemon (8797) and rendered in a real
xterm.js 6.0 terminal before the fix:

1. ``restore`` spawned the new shell FIRST and then ASSIGNED the saved
   scrollback to the tail. A pushing backend (ConPTY) had already recorded the
   new console's first bytes and the shell's prompt, and the assignment threw
   them away: the pane never saw the prompt, and the first key typed landed at
   column 1.
2. The new pseudoconsole draws as if it started on a BLANK screen with the
   cursor at the top (after its prompt it moves to absolute rows, e.g.
   ``ESC[1;49H``), while the pane still showed the dead program's last screen.
   Every key the user typed was drawn over old lines near the top. The reset
   after the replay now ends with a fresh page (``modes.fresh_page``): the old
   screen scrolled up into the scrollback, the rest cleared, the cursor home.
"""

from __future__ import annotations

import base64
import sys
import time

import pytest

from iron_jarvis.terminals import backend as B
from iron_jarvis.terminals import modes as M
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.manager import TerminalManager

OLD = b"old claude line 1\r\nold claude line 2\r\n\x1b[?1000h\x1b[?1006h\x1b[12;5H> type here"


class EarlyPromptBackend(FakeBackend):
    """Pushes the new shell's first output from INSIDE start(), the way
    ConPTY's reader thread delivers it while ``restore`` is still running."""

    pushes_output = True

    def __init__(self) -> None:
        super().__init__()
        self._on_output = None

    def set_output_handler(self, on_output, on_eof=None) -> None:
        self._on_output = on_output

    @property
    def output_finished(self) -> bool:
        return False

    def start(self, argv, cwd, env, cols, rows) -> None:
        super().start(argv, cwd, env, cols, rows)
        self._on_output(b"\x1b[?9001hPS C:\\new> ")


def _entry(tmp_path, *, argv=None, rows=30) -> dict:
    return {
        "id": "term_restored",
        "shell": "pwsh",
        "argv": argv or ["pwsh"],
        "cwd": str(tmp_path),
        "cols": 120,
        "rows": rows,
        "scrollback_b64": base64.b64encode(OLD).decode("ascii"),
    }


def test_what_the_new_shell_printed_while_starting_is_kept_after_the_old_screen(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    s = m.restore(_entry(tmp_path), backend=EarlyPromptBackend())
    try:
        tail = s.scrollback_bytes()
        reset = M.restore_reset(OLD, 30)
        assert tail == OLD + reset + b"\x1b[?9001hPS C:\\new> "
    finally:
        s.kill()


def test_the_reset_ends_on_a_fresh_page_sized_to_the_pane():
    page = M.fresh_page(30)
    assert page.startswith(M.FRESH_PAGE_RESET)
    assert b"\x1b[30;1H" + b"\n" * 30 + b"\x1b[2J\x1b[H" in page
    assert page.endswith(b"\x1b[2J\x1b[H")  # cursor home on a cleared screen
    reset = M.restore_reset(OLD, 30)
    assert reset.startswith(M.MODE_RESET + b"\x1b[?25h")
    assert reset.endswith(page)


def test_the_alternate_screen_is_left_before_the_page_is_turned():
    reset = M.restore_reset(b"\x1b[?1049h full screen app", 24)
    assert reset.index(b"\x1b[?1049l") < reset.index(M.FRESH_PAGE_RESET)


def test_a_silly_saved_height_is_bounded():
    assert M.fresh_page(0) == M.fresh_page(24)  # unknown height: the default
    assert M.fresh_page(-3).count(b"\n") == 1
    assert M.fresh_page(100_000).count(b"\n") == 500


def test_the_manager_turns_the_page_at_the_saved_height(tmp_path):
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    s = m.restore(_entry(tmp_path, rows=41), backend=FakeBackend())
    try:
        assert bytes(s.scrollback_bytes()).endswith(M.fresh_page(41))
    finally:
        s.kill()


@pytest.mark.skipif(
    sys.platform != "win32" or not B.conpty_available(), reason="needs a real Windows ConPTY"
)
def test_a_real_console_restored_pane_keeps_its_first_output(tmp_path):
    """The real thing: a cmd.exe under ConPTY restored over a saved screen.
    The new console's first bytes and the prompt must follow the reset."""
    m = TerminalManager(state_path=tmp_path / "terminals.json")
    s = m.restore(_entry(tmp_path, argv=["cmd.exe"], rows=30))
    try:
        reset = M.restore_reset(OLD, 30)
        deadline = time.monotonic() + 20
        after = b""
        while time.monotonic() < deadline:
            tail = bytes(s.scrollback_bytes())
            assert tail.startswith(OLD + reset)
            after = tail[len(OLD + reset):]
            if b">" in after:
                break
            time.sleep(0.1)
        assert b">" in after, f"no prompt after the reset: {after[:200]!r}"
        # ConPTY's own start-up sequence is the very first thing it prints.
        assert b"\x1b[?9001h" in after or b"\x1b[?1004h" in after
    finally:
        s.kill()
