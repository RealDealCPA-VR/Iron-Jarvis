"""Harness gaps, fix 4: Iron Jarvis's own sentences say the everyday names.

The v1.388.0 re-audit (0g.6): a Build terminal's coding tool was told to
"relaunch the harness" (the MCP door's 503, the expired and missing terminal
tokens, the capability refusals, the stdio shim), the Settings row for the
library spoke of "the folder's harness files", and the session's summary
press refused with "Compact needs a real model" while the screen (and the
chat) say "Summarize". "harness" is a retired word now (the scanners read it
from dashboard/__tests__/helpers/retiredWords.ts); these pin the sentences.
"""

from __future__ import annotations

import re

from iron_jarvis.browser import panetokens
from iron_jarvis.daemon.routes import mcpserver as mcp_routes
from iron_jarvis.harness import compact as C
from iron_jarvis.mcpserver import server as mcp_server
from iron_jarvis.mcpserver import stdio_shim
from iron_jarvis.settings.schema import BY_KEY

HARNESS = re.compile(r"\bharness(?:es)?\b", re.I)


def test_the_terminal_refusals_name_the_coding_tool():
    for text in (
        mcp_routes.NOT_READY_MESSAGE,
        panetokens.PANE_TOKEN_EXPIRED_MESSAGE,
        panetokens.MISSING_TOKEN_MESSAGE,
        mcp_server.NO_CAPABILITY_MESSAGE,
        stdio_shim.NO_TOKEN_MESSAGE,
    ):
        assert not HARNESS.search(text), text
        assert "coding tool" in text, text


def test_a_capability_refusal_names_the_terminal_and_the_coding_tool():
    refusals = [r for r in (mcp_server.capability_refused_message(t) for t in ("browser_click",)) if r]
    assert refusals, "the capability refusal is worded"
    for text in refusals:
        assert not HARNESS.search(text) and "pane" not in text, text
        assert text.endswith("Tick it in Build and start the coding tool again."), text


def test_the_library_setting_speaks_of_coding_session_instructions():
    row = BY_KEY["build_reflect"]
    assert not HARNESS.search(row.help), row.help
    assert "coding-session instructions" in row.help


def test_the_summary_press_refuses_in_the_chats_word():
    for text in (C.ARCHIVED, C.NO_REAL_MODEL):
        assert "ompact" not in text, text
        assert "ummariz" in text, text
