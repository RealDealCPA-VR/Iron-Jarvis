"""v1.455.0 (readiness Wave F, item 18): Build › Code's receipt names where a
reply ran from the tool alone — the dashboard's ``CODE_CLOUD_TOOLS``
(``dashboard/lib/buildApp.ts``) is the daemon's ``hosted/panes.CLOUD_TOOLS``,
and the word it says is the daemon's ``WHERE_CLOUD``. A tool added to one and
not the other fails here."""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.hosted.panes import CLOUD_TOOLS, WHERE_CLOUD

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "dashboard" / "lib" / "buildApp.ts").read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_cloud_tools_are_the_daemons():
    m = re.search(r"export const CODE_CLOUD_TOOLS: readonly string\[\] = \[([^\]]*)\]", SRC)
    assert m, "CODE_CLOUD_TOOLS not found in lib/buildApp.ts"
    tools = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert tools == set(CLOUD_TOOLS)


def test_the_word_is_the_daemons():
    m = re.search(r'export function codeReceiptWhere\(tool: string\): string \{\n\s*return .*\? "([^"]+)" : "";', SRC)
    assert m, "codeReceiptWhere not found"
    assert m.group(1) == WHERE_CLOUD
