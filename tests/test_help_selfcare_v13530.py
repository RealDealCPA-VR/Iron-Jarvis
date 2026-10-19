"""v1.353.0 (G66): Help's "Something not working?" card opens a chat with a
question the self-care tools really answer.

Since v1.344.0 a failure/health sentence in chat arms `app_health`,
`pack_status` and `run_doctor` (tools/autoselect), but nothing a new user
reads ever said so. Help's grid now has a card that opens
`/chat?ask=<HEALTH_QUESTION>` (typed, never sent). A card whose words the
vocabulary did NOT recognise would open a chat with no health tools armed, so
this pin reads the EXACT sentence out of the page and drives the real
selector with it.
"""

from __future__ import annotations

import re
from pathlib import Path

from iron_jarvis.tools.autoselect import _CHANGE_TOOLS, select_ask_tools, select_auto_tools

ROOT = Path(__file__).resolve().parents[1]
HELP = ROOT / "dashboard" / "app" / "help" / "page.tsx"


def _help_source() -> str:
    # Windows CI checks out with autocrlf: normalise at the reader.
    return HELP.read_text(encoding="utf-8").replace("\r\n", "\n")


def _health_question() -> str:
    m = re.search(r'const HEALTH_QUESTION = "([^"]+)";', _help_source())
    assert m, "help/page.tsx no longer declares HEALTH_QUESTION"
    return m.group(1)


def test_the_card_links_to_chat_with_the_question():
    src = _help_source()
    assert "href: `/chat?ask=${encodeURIComponent(HEALTH_QUESTION)}`" in src
    assert 'title: "Something not working?"' in src


def test_the_question_arms_the_health_readers_and_nothing_that_changes_things():
    question = _health_question()
    picked = select_auto_tools(question)
    assert picked, f"{question!r} armed nothing"
    assert picked[0] == "app_health", picked
    assert {"app_health", "run_doctor", "pack_status"} <= set(picked), picked
    # A health question is consent to READ: no writer, no repair ask-armed.
    assert not (set(picked) & _CHANGE_TOOLS), picked
    assert not select_ask_tools(question), select_ask_tools(question)


def test_a_sentence_the_vocabulary_does_not_know_would_arm_nothing():
    # Anti-vacuity: the pin above can fail. A friendlier-sounding question
    # outside the failure vocabulary arms no reader.
    assert "app_health" not in select_auto_tools("Could you help me with my day?")
