"""v1.462.0: a missed edit with NOTHING close in the file says to read it again.

Read off the live ledger: 83 of 101 failed edits by team workers had no line
of the file close to ``old`` (text written from memory, or a file that had
changed). They were told "Copy the text exactly, including indentation" —
advice for a near miss, not for a file that does not hold the text at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.harness import textedit as T
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import EditFileTool

COPY = "Copy the text exactly, including indentation."


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )


async def test_nothing_close_says_read_the_file_again(tmp_path):
    (tmp_path / "config.ts").write_text("export const port = 3000;\n", encoding="utf-8")
    res = await EditFileTool().execute(
        {"path": "config.ts", "old": 'baseUrl: env("AI_BASE_URL", "http://x")', "new": "y"},
        _ctx(tmp_path),
    )
    assert res.ok is False
    assert res.error == "`old` text was not found in config.ts." + T.NOTHING_CLOSE
    assert "Read the file again" in res.error and COPY not in res.error
    assert (tmp_path / "config.ts").read_text(encoding="utf-8") == "export const port = 3000;\n"


async def test_a_near_miss_still_names_the_closest_line_and_says_copy(tmp_path):
    (tmp_path / "config.ts").write_text("export const port = 3000;\n", encoding="utf-8")
    res = await EditFileTool().execute(
        {"path": "config.ts", "old": "export const port = 3001;", "new": "y"}, _ctx(tmp_path)
    )
    assert "The closest line in the file is line 1" in res.error
    assert res.error.endswith(COPY) and "Read the file again" not in res.error


def test_a_file_past_the_scan_bound_never_claims_nothing_is_close(monkeypatch):
    """Only the first lines are compared: past the bound, "no line is close"
    would be a claim about lines nobody read."""
    monkeypatch.setattr(T, "_HINT_MAX_LINES", 3)
    text = "a\nb\nc\nd\nthe real line\n"
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit(text, "zzzzzzzzzzzz qqqqqqqq", "y", where="big.txt")
    assert str(err.value) == "`old` text was not found in big.txt. " + COPY


def test_a_short_file_with_nothing_close(monkeypatch):
    with pytest.raises(T.EditError) as err:
        T.apply_exact_edit("alpha\nbeta\n", "zzzzzzzzzzzz qqqqqqqq", "y", where="s.txt")
    assert str(err.value) == "`old` text was not found in s.txt." + T.NOTHING_CLOSE
