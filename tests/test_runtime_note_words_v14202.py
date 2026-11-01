"""v1.420.2: the dashboard keeps the shell tool's sandbox notes to the model
off the screen by matching them EXACTLY (dashboard/lib/stepDetail.ts
`RUNTIME_NOTE_LINES`). Reword a note here and the screen would show it again,
silently — so the two are held equal."""

from __future__ import annotations

import json
import re
from pathlib import Path

from iron_jarvis.sandbox.shell_tool import _COMPACT_ADVISORY, _NO_CONFINEMENT_WARNING

ROOT = Path(__file__).resolve().parents[1]


def _dashboard_lines() -> list[str]:
    src = (ROOT / "dashboard" / "lib" / "stepDetail.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"export const RUNTIME_NOTE_LINES[^=]*=\s*\[(.*?)\];", src, re.S)
    assert m, "RUNTIME_NOTE_LINES not found in dashboard/lib/stepDetail.ts"
    return [json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', m.group(1))]


def test_the_dashboard_knows_both_sandbox_notes_word_for_word() -> None:
    assert _dashboard_lines() == [_COMPACT_ADVISORY, f"[warning] {_NO_CONFINEMENT_WARNING}"]


def test_the_note_the_shell_tool_writes_first_is_the_one_matched() -> None:
    from iron_jarvis.sandbox.shell_tool import SandboxedShellTool

    tool = SandboxedShellTool()
    first = tool._advisory_line("s1")
    later = tool._advisory_line("s1")
    assert [later, first] == _dashboard_lines()
