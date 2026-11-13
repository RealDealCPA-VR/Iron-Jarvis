"""Four everyday wordings chat missed (v1.487.0), each seen in the live runs.

* "keep this chat JUST on my computer" — the extra word broke the Local only
  match, and the model answered "confirmed, it stays on your computer" with
  Local only never turned on (a false privacy claim).
* "PUT BACK what you did last" — never reached the undo tools.
* "save what we just did … so we can do it the same way next time" — answered
  with a workflow card three times; a way of working saved to do again is a
  skill (the skill brief now says so).
* "14 rounds at 47 minutes each … how long is the whole thing?" — worked out
  in the model's head; a quantity question with two or more numbers now
  offers the calculator.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.settings.records import record_tools_for
from iron_jarvis.skills.trigger import wants_skill_create
from iron_jarvis.tools.autoselect import wants_calculator


@pytest.mark.parametrize("text,tool", [
    ("keep this chat just on my computer dont send it anywhere", "chat_local_only"),
    ("keep this conversation only on my laptop", "chat_local_only"),
    ("make this chat entirely local", "chat_local_only"),
    ("put back what you did last it messed up my spreadsheet", "change_undo"),
    ("take back what you just changed", "change_undo"),
    ("put my spreadsheet back how it was", "change_undo"),
    ("put it back the way it was please", "change_undo"),
])
def test_the_request_offers_its_tool(text, tool):
    assert tool in record_tools_for(text)


@pytest.mark.parametrize("text", [
    "keep the chat going, just on my computer is fine",
    "put back the chairs after the meeting",
    "how do I put it back the way it was?",
    "put the kettle on",
])
def test_ordinary_sentences_offer_neither(text):
    out = record_tools_for(text)
    assert "chat_local_only" not in out and "change_undo" not in out


@pytest.mark.parametrize("text,asks", [
    ("Save what we just did with the tenant list as a thing you can repeat, call it Monthly Arrears.", True),
    ("remember how we just did the invoice thing and save that so we can do it the same way next time", True),
    ("turn how we handled the Jones audit into something we can reuse", True),
    ("save what we just did to the spreadsheet", False),
    ("do you remember how we made the quote? do it again for Smith", False),
    ("keep doing what we did yesterday, again tomorrow", False),
    ("how did we save the file last time? do the same again", False),
])
def test_saving_the_way_we_did_it_to_do_again_is_a_skill(text, asks):
    assert wants_skill_create(text) is asks


def test_the_skill_brief_says_that_request_is_a_skill():
    assert "save the way a piece of work was just done" in CT.SKILL_BLOCK
    assert "Do not answer a request for a skill with workflow_draft" in CT.SKILL_BLOCK


@pytest.mark.parametrize("text,wants", [
    ("Quick: 14 rounds at 47 minutes each plus three 10-minute breaks, how long is the whole thing?", True),
    ("if i charge 45 for a cut and 60 for colour and do 12 of each how much is that", True),
    ("how many hours is 3 shifts of 7.5 hours", True),
    ("how much is the 2026 plan", False),
    ("how long have you been running", False),
    ("I have 2 cats and 3 dogs", False),
])
def test_a_quantity_question_with_two_numbers_offers_the_calculator(text, wants):
    assert wants_calculator(text, []) is wants


def test_the_new_shapes_are_linear_on_pathological_text():
    """A child process with a deadline (a hang detector, never a speed bar)."""
    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.settings.records import record_tools_for
        from iron_jarvis.skills.trigger import _THE_WAY_WE_DID, wants_skill_create
        from iron_jarvis.tools.autoselect import wants_calculator
        N = 200_000
        cases = [
            lambda: record_tools_for("keep this chat just " * (N // 20)),
            lambda: record_tools_for("put back what you " * (N // 18)),
            lambda: record_tools_for("put my " + "x " * (N // 2) + "back"),
            lambda: _THE_WAY_WE_DID.search("save " * (N // 5)),
            lambda: _THE_WAY_WE_DID.search("how we did " * (N // 11)),
            lambda: wants_skill_create("save how we " * (N // 12)),
            lambda: wants_calculator("how much 1 " * (N // 11), []),
        ]
        for fn in cases:
            t0 = time.perf_counter(); fn()
            print(round(time.perf_counter() - t0, 3), flush=True)
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.count("\n") == 7, out.stdout
