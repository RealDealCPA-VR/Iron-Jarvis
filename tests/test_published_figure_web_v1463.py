"""v1.463.0: a question about a published figure reaches the web.

Found by asking the running app, as a user would, "What is the IRS standard
mileage rate for business use in 2026? Give me the number and the page you
got it from." No web tool was armed, so the model said it could not look it
up, and the user was left to do it by hand.

Both ways: the questions that need the web, and the look-alikes that are
about the user's own data and must stay off it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from iron_jarvis.tools.autoselect import select_auto_tools

NEEDS_THE_WEB = [
    "What is the IRS standard mileage rate for business use in 2026? Give me the number and the page you got it from.",
    "What is the IRS standard mileage rate for 2026?",
    "what's the current federal funds rate",
    "what is the 2026 401k contribution limit",
    "When is the 2026 Form 1065 deadline?",
    "How much is the Social Security wage base this year?",
    "what are the 2027 tax brackets for married filing jointly?",
    "Is the Florida minimum wage going up next year?",
    "what is today's prime rate",
    "What's the standard deduction for 2026?",
    "what is the per diem for Orlando right now?",
]

THE_USERS_OWN = [
    "What rate did we charge in 2026?",
    "What is my 2026 billing rate?",
    "What is the deadline for this project in 2026?",
    "What is the 2026 rate in the attached file?",
    "which clients hit the 2026 contribution limit?",
    "Is the rate in this document current?",
    "What's the current rate on the client's lease?",
    "what is the penalty in the contract? It was signed in 2026.",
    "What is our 2026 deadline for the Smith engagement?",
]

NOT_A_FIGURE_QUESTION = [
    "How are you today?",
    "Write a memo about the 2026 mileage rate change.",
    "Summarize this: the rate rose in 2026.",
    "what did I do in 2026",
    "Rate this draft from 1 to 10.",
    "Limit the summary to 2026 items.",
]


@pytest.mark.parametrize("text", NEEDS_THE_WEB)
def test_a_published_figure_question_arms_web_search(text):
    assert "web_search" in select_auto_tools(text)


@pytest.mark.parametrize("text", THE_USERS_OWN)
def test_a_question_about_the_users_own_figures_stays_off_the_web(text):
    armed = select_auto_tools(text)
    assert "web_search" not in armed and "web_fetch" not in armed


@pytest.mark.parametrize("text", NOT_A_FIGURE_QUESTION)
def test_other_sentences_do_not_arm_the_web(text):
    armed = select_auto_tools(text)
    assert "web_search" not in armed


def test_the_pattern_is_quick_on_a_very_long_message():
    """In a child process with a deadline: a slow pattern would stall the app."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.tools.autoselect import _PUBLISHED_FIGURE_RX as RX
        worst = 0.0
        for text in (
            "what " + "2026 " * 30000,
            "what " + "rate " * 30000,
            "?" + "current " * 30000,
            "what is " + "2026 rate " * 20000 + " my",
            "x" * 150000 + "?",
        ):
            t = time.perf_counter(); RX.search(text); worst = max(worst, time.perf_counter() - t)
        print(worst)
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert float(out.stdout.strip()) < 5.0
