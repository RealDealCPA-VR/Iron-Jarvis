"""v1.463.0: a plain "is anything broken?" question reaches the health reader
however it is worded.

Found by asking the running app, as a user would, "Is anything broken in
Jarvis right now? Keep it short.": no tool was armed, and the model answered
from what happened to be in its prompt.
"""

from __future__ import annotations

import pytest

from iron_jarvis.tools.autoselect import select_auto_tools

ASKS = [
    "Is anything broken in Jarvis right now? Keep it short.",
    "is anything broken in the app",
    "Is something broken with my setup today",
    "is anything else broken",
    "are there any problems with the app",
    "any issues with Jarvis this morning?",
    "I'm having trouble with the chat",
    "is anything broken?",  # the older shape still works
]

NOT_ABOUT_THE_APP = [
    "The client has cash flow problems with their lender",
    "Write a memo about issues with the 2025 depreciation method",
    "fix the broken link in this document",
    "summarize the trouble with section 179 for a new client",
]


@pytest.mark.parametrize("text", ASKS)
def test_a_health_question_arms_the_health_reader(text):
    assert "app_health" in select_auto_tools(text)


@pytest.mark.parametrize("text", NOT_ABOUT_THE_APP)
def test_office_sentences_do_not_arm_it(text):
    assert "app_health" not in select_auto_tools(text)


def test_the_new_shapes_are_quick_on_a_very_long_message():
    import time

    text = ("anything " * 20_000) + ("problems with " * 20_000)
    began = time.perf_counter()
    select_auto_tools(text)
    assert time.perf_counter() - began < 20
