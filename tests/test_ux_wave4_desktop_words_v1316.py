"""v1.316.0 (UX wave 4, carry-desktop-ask-words): the desktop's "Jarvis is
waiting for you" toast describes a waiting ask in the SAME words as the
approval card in the app.

The card's words live in ``dashboard/lib/toolWords.ts`` ("rename or move a
file" since v1.314.0: ``rename_file`` also MOVES a file — a ``new_path`` in
another folder). ``desktop/main.js`` keeps its own small table for the tray
watcher (it runs in the Electron main process, which cannot import the
dashboard's TypeScript), and it still said "rename a file" — a toast that
under-describes what the user is being asked to allow.

These run the REAL ``plainAskSummary``/``planAskNotifications`` lifted out
of main.js (the v1249 harness) and read the REAL toolWords.ts phrase, so a
later edit to either side that drifts from the other goes red here.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_desktop_reliability_v1249 import _asks, requires_node

ROOT = Path(__file__).resolve().parents[1]


def _tool_words(tool: str) -> str:
    src = (ROOT / "dashboard" / "lib" / "toolWords.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(rf'^\s*{re.escape(tool)}:\s*"([^"]+)"', src, re.M)
    assert m, f"lib/toolWords.ts has no phrase for {tool}"
    return m.group(1)


def _bodies(approvals: list[dict], tmp_path: Path) -> list[str]:
    out = _asks({"steps": [{"approvals": approvals, "now": 0}]}, tmp_path)["out"]
    return [n["body"] for n in out[0]["notify"]]


@requires_node
def test_rename_file_reads_rename_or_move_like_the_approval_card(tmp_path):
    card = _tool_words("rename_file")
    assert card == "rename or move a file", card  # the card's own words, as shipped
    one, many = _bodies(
        [
            {"id": "a", "tool": "rename_file", "count": 1},
            {"id": "b", "tool": "rename_file", "count": 4},
        ],
        tmp_path,
    )
    assert one == f"{card} — click to open the job.", one
    assert many == "rename or move 4 files — click to open the job.", many


@requires_node
def test_anti_vacuity_the_words_that_already_agree_still_agree(tmp_path):
    """Control: shell already agreed with the card before this wave."""
    card = _tool_words("shell")
    (body,) = _bodies([{"id": "s", "tool": "shell", "count": 1}], tmp_path)
    assert body == f"{card} — click to open the job.", body


@requires_node
def test_anti_vacuity_no_argument_is_ever_quoted_and_unknown_tools_fall_back(tmp_path):
    """The v1.247.0 rule stands: a toast on a shared screen never reads out a
    client's file name, and a tool with no phrase is named as it is."""
    bodies = _bodies(
        [
            {"id": "a", "tool": "rename_file", "count": 1, "path": "C:/clients/Smith 1040.pdf",
             "new_path": "C:/clients/archive/Smith 1040.pdf"},
            {"id": "b", "tool": "excel_edit", "count": 3},
            {"id": "c", "tool": "some_new_tool", "count": 2},
        ],
        tmp_path,
    )
    assert not any("Smith" in b or ".pdf" in b or "archive" in b for b in bodies), bodies
    assert bodies[1].startswith("edit 3 workbooks — ")
    assert bodies[2].startswith("use some new tool (2 times) — ")


@requires_node
def test_every_phrase_in_the_desktop_table_agrees_with_the_card(tmp_path):
    """Coordinator (v1.316.0): write_file says what the card says, and a tool
    the card refuses to describe (an agent-made `rename_real_file`) is named
    as it is on the desktop too — never given a built-in's words."""
    card = _tool_words("write_file")
    save, real = _bodies(
        [
            {"id": "w", "tool": "write_file", "count": 1},
            {"id": "r", "tool": "rename_real_file", "count": 1},
        ],
        tmp_path,
    )
    assert save == f"{card} — click to open the job.", save
    assert real.startswith("use rename real file — "), real
