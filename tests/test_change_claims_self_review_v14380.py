"""v1.438.0 (Wave 4 group C) — the independent review's honesty-note pins.

Fresh sentences, none in the builder's table. A false claim of a kept
preference, lesson or persona, or a LASTING promise, gets the note; an
OFFER or a condition ("If you want, I'll always reply in German — confirm
and I'll set it") and a teacher's lesson ("I added a lesson about
depreciation to your CPE course") never do. Each was red before the review's
fix where it failed.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.change_claims import claimed_change_note


def note(reply: str) -> str:
    return claimed_change_note(reply, [], [])


MUST_NOTE = [
    "I've removed your preference for formal sign-offs.",
    "Got it, I'll write in Spanish from now on.",
    "I changed the Auditor persona so it is gentler.",
    "I've reset the default persona.",
    "From now on, I'll use numbered lists.",
    "Iron Jarvis now starts with Windows.",
    "Done! Jarvis will now keep running in the background when you close the window.",
    "I made a new persona called Tax Partner.",
    "I've added that as a lesson.",
    "Okay, I'll answer in Korean in all my replies.",
    "I erased the lesson about K-1 timing.",
    "I updated your formatting preference to tables.",
    "Iron Jarvis will now quit when you close its window.",
    "I will now always reply in Arabic.",
    "Noted — I'll always respond in Italian.",
    "I added a lesson: always cite the section.",
]

NEVER_NOTE = [
    "I noted the client's preference for paper statements in the memo.",
    "I updated the client's preference in the CRM.",
    "I removed Mrs. Lopez's preference from the file.",
    "I removed Sam's preference about ink color.",
    "I deleted the partner's lesson about filing.",
    "I updated Mrs. Lopez's preference.",
    "I added a lesson about depreciation to your CPE course.",
    "I added a new lesson to the onboarding course.",
    "I removed the buyer persona slide from the deck.",
    "Here is the reply in Spanish: Estimada Sra. Lopez...",
    "I'll answer in Spanish if you'd like — just say so.",
    "If you want, I'll always reply in German going forward — confirm and I'll set it.",
    "Once you confirm, I will answer in Spanish from now on.",
    "Sure, I will answer in Spanish from now on if you confirm.",
    "You can start Iron Jarvis with Windows from the tray.",
    "I won't answer in French from now on unless you ask.",
    "The client always replies in Spanish.",
    "Your laptop now starts with Windows 11 instead of Windows 10.",
    "The persona of the narrator is sarcastic.",
    "I created a user persona for the onboarding study.",
    "Sure, I'll reply in Spanish to this email.",
]


@pytest.mark.parametrize("reply", MUST_NOTE)
def test_a_false_claim_gets_the_note(reply):
    assert note(reply), reply


@pytest.mark.parametrize("reply", NEVER_NOTE)
def test_an_offer_someone_elses_or_a_teachers_never_does(reply):
    assert note(reply) == "", (reply, note(reply))


def test_a_real_change_still_answers_the_promise():
    assert claimed_change_note("Got it, I'll write in Spanish from now on.", ["remember_preference"], []) == ""
