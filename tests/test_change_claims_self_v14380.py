"""v1.438.0 (chat-first plan Wave 4 group C): Wave 5's honesty note learns
the new kinds — a kept PREFERENCE or lesson, a chat PERSONA, and the LASTING
PROMISES ("I'll answer in Spanish from now on", "Iron Jarvis will now start
with Windows").

A false claim gets the note when nothing changed this turn; a true one (the
tool that makes it ran, or the setting changed) never does. Every card is
built by the REAL ``config_cards_from_result``; the note is the REAL
``claimed_change_note`` both chat lanes append (MIRROR NOTE).
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.change_claims import claimed_change_note
from iron_jarvis.daemon.chat_turn import config_cards_from_result
from iron_jarvis.tools.base import ToolResult


def RC(tool: str, key: str) -> dict:
    (card,) = config_cards_from_result(
        tool,
        ToolResult(ok=True, output="", data={"record_change": {
            "title": "Changed", "key": key, "label": key, "old": None, "new": "x", "change_id": "rec_x"}}),
    )
    return card


def SC(key: str, new=True) -> dict:
    (card,) = config_cards_from_result(
        "config_set",
        ToolResult(ok=True, output="", data={"config_change": {
            "key": key, "label": key, "old": None, "new": new, "restart": False, "change_id": "cfg_x"}}),
    )
    return card


def note(reply: str, used=(), cards=()) -> str:
    return claimed_change_note(reply, list(used), list(cards))


MUST_NOTE: list[tuple[str, str]] = [
    ("I've forgotten that preference.", "No preference was forgotten"),
    ("Done — I removed the preference about short answers.", "No preference was removed"),
    ("I deleted the lesson about rounding.", "No preference was deleted"),
    ("I added a lesson: always cite the IRS section.", "No preference was created"),
    ("I've updated your preference to bullet points.", "No preference was updated"),
    # Moved here from test_change_claims_v14360 (v1.438.0): judged now.
    ("I saved your preference for shorter answers.", "No preference was created"),
    ("I created a persona called Auditor.", "No persona was created"),
    ("I deleted the CPA persona.", "No persona was deleted"),
    ("The pirate persona has been removed.", "No persona was removed"),
    ("I'll answer in Spanish from now on.", "“Answer in this language” was not changed"),
    ("From now on I'll reply in French.", "“Answer in this language” was not changed"),
    ("Sure! I'll always answer in German going forward.", "“Answer in this language”"),
    ("I'll use bullet points from now on.", "“Formatting” was not changed"),
    ("Iron Jarvis will now start with Windows.", "“Start Iron Jarvis with Windows” was not changed"),
    ("Done — Iron Jarvis will no longer start with Windows.", "“Start Iron Jarvis with Windows”"),
    ("Start with Windows is now on.", "“Start Iron Jarvis with Windows”"),
    ("I've set Iron Jarvis to start with Windows.", "“Start Iron Jarvis with Windows”"),
    ("Iron Jarvis will now keep running in the background when you close the window.",
     "“Keep running in the background when I close the window”"),
    ("It will now quit when you close the window.", "“Keep running in the background when I close the window”"),
]

#: (reply, tools that ran, cards they produced)
NO_NOTE: list[tuple[str, list[str], list[dict]]] = [
    # True claims: the tool that makes them ran this turn.
    ("I've forgotten that preference.", ["preference_forget"], [RC("preference_forget", "preference.lesson_1")]),
    ("I added a lesson: always cite the IRS section.", ["lesson_add"], []),
    ("I've saved that preference.", ["remember_preference"], []),
    ("I've saved your preference for bullet points.", ["config_set"], [SC("profile.formatting", "bullets")]),
    ("I updated your preference.", ["preference_edit"], []),
    ("I created a persona called Auditor.", ["persona_create"], [RC("persona_create", "persona.auditor")]),
    ("I deleted the CPA persona.", ["persona_delete"], []),
    ("I'll answer in Spanish from now on.", ["config_set"], [SC("profile.language", "es")]),
    ("From now on I'll answer in Spanish.", ["remember_preference"], []),
    ("I'll use bullet points from now on.", ["config_set"], [SC("profile.formatting", "bullets")]),
    ("Iron Jarvis will now start with Windows.", ["config_change_protected"], [SC("start_with_windows")]),
    ("Start with Windows is now on.", ["config_change_protected"], [SC("start_with_windows")]),
    ("Iron Jarvis will now keep running in the background when you close the window.", ["config_set"],
     [SC("keep_running_in_background")]),
    # Never claims: offers, futures with no lasting word, descriptions, someone
    # else's preference or persona, the user's own change.
    ("I can forget that preference if you like.", [], []),
    ("Shall I add a lesson about that?", [], []),
    ("I'll answer in Spanish.", [], []),
    ("I'll use bullet points in this summary.", [], []),
    ("Iron Jarvis will start with Windows once you turn it on in Settings.", [], []),
    ("If you want, Iron Jarvis can start with Windows.", [], []),
    ("You turned on start with Windows in the tray.", [], []),
    ("I won't start with Windows.", [], []),
    ("Your buyer persona was updated in the deck.", [], []),
    ("I updated the lesson plan for Tuesday.", [], []),
    ("Your seat preference was changed to aisle.", [], []),
    ("I forgot to attach the file.", [], []),
    ("Windows will now start updates at night.", [], []),
    ("I always answer in Spanish when you write Spanish.", [], []),
    ("I added a guitar lesson to your calendar.", [], []),
    ("I created a new agent persona for the story.", [], []),
    ("I created a new persona for your novel.", [], []),
    ("I made a persona for the marketing deck.", [], []),
    ("I saved your preference to memory.", ["memory_write"], []),
]


@pytest.mark.parametrize("reply, words", MUST_NOTE)
def test_a_false_claim_gets_the_note(reply, words):
    got = note(reply)
    assert words in got, (reply, got)


@pytest.mark.parametrize("reply, used, cards", NO_NOTE)
def test_a_true_claim_or_no_claim_gets_none(reply, used, cards):
    assert note(reply, used, cards) == "", reply


def test_the_language_promise_says_it_lasts_only_in_this_chat():
    got = note("I'll answer in Spanish from now on.")
    assert "lasts only while this chat's history does" in got


def test_the_table_is_both_ways():
    assert len(MUST_NOTE) >= 15 and len(NO_NOTE) >= 25
