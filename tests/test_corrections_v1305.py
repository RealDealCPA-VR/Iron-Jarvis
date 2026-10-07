"""v1.305.0 — the correction detector (learning/corrections.py), table-driven.

Conservative by contract: a false positive puts a question under a reply the
user did not ask for. Every must-not-match row is a real shape that the
detector would cost the user attention on if it fired: task requests,
questions, stop/cancel, a correction of WHAT (not HOW), code.
"""

from __future__ import annotations

import pytest

from iron_jarvis.learning.corrections import (
    MAX_CHARS,
    is_correction,
    proposal_text,
    signature,
    similar,
)

SHOULD_MATCH = [
    "shorter please",
    "make it shorter",
    "too long, shorten it",
    "no tables",
    "don't use tables",
    "stop using emojis",
    "no emoji please",
    "please don't use bullet points",
    "I said no bullet points",
    "again, no headings",
    "you keep using tables",
    "stop apologizing",
    "use plain english instead",
    "plain words please",
    "less jargon",
    "way too long",
    "this answer is too long",
    "be more concise",
    "keep it short",
    "can you stop using emojis?",
    "could you keep your answers shorter?",
    "No, shorter.",
    "too verbose",
    "skip the preamble",
    "get to the point",
    "stop asking me follow-up questions",
    "don't ask before running the tests",
    "answer in english",
    "no more markdown",
    "never use em dashes",
    "you keep apologizing",
    "quit with the caveats",
    "too formal",
    "a bit shorter please",
    "more detail please",
    "stop rambling",
    "Ugh, too wordy again",
    "don't explain everything",
    "Please, no exclamation marks.",
    "I told you, no tables",
    "stop being so formal",
    "you still use emojis",
]

MUST_NOT_MATCH = [
    "write a short story about a dragon",
    "make a table of last month's sales",
    "stop the job",
    "no, the other file",
    "cancel",
    "stop",
    "never mind",
    "which route is shorter?",
    "how long is the flight to Denver?",
    "is this too long for a tweet?",
    "don't forget to email Dana tomorrow",
    "don't delete the file",
    "stop the server",
    "no thanks",
    "try again",
    "again please",
    "I said Tuesday, not Monday",
    "you keep crashing",
    "the meeting was too long",
    "make the email shorter",
    "shorten this paragraph of the report",
    "def shorten(text): return text[:10]",
    "`no tables` is a flag in the config",
    "can you summarize this article?",
    "what's a bullet point?",
    "use the blue one instead",
    "list the files in my downloads folder",
    "find me a shorter route home",
    "I want the shorter one",
    "thanks, that's perfect",
    "sounds good, go ahead",
    "it's been a long day",
    "please translate this to spanish",
    "add a table to the spreadsheet with totals",
    "stop the music",
    "no, use the 2024 numbers",
    "why did you use a table?",
    "Draft a brief reply to Priya",
    "I said the long-term plan",
    "too many cooks",
    "keep it up",
    "stop the build and run the tests",
    "",
    "   ",
    "x" * (MAX_CHARS - 20) + " shorter please",
    "shorter please\n\nline two\nline three\nline four",
    "see https://example.com/no-tables for the spec",
    "stop, the tables are wrong",
    "no problem",
    "don't worry about it",
    "you always forget the attachment",
    # reviewer: a correction SCOPED to this one case is not a standing one
    "answer in spanish for this client",
    "too long for the client — cut the intro",
    "less detail on depreciation, more on the credit",
    "can you keep it brief? I'm on my phone",
    "no markdown, this goes into outlook",
    "shorter this time",
    "no tables for now",
]


@pytest.mark.parametrize("text", SHOULD_MATCH)
def test_should_match(text):
    assert is_correction(text), text


@pytest.mark.parametrize("text", MUST_NOT_MATCH)
def test_must_not_match(text):
    assert not is_correction(text), text


def test_tables_are_big_enough():
    assert len(SHOULD_MATCH) >= 30
    assert len(MUST_NOT_MATCH) >= 30


@pytest.mark.parametrize(
    "a,b",
    [
        ("shorter please", "make it shorter"),
        ("make it shorter", "too long, shorten it"),
        ("shorter please", "too long, shorten it"),
        ("no tables", "stop using tables"),
        ("I said no tables", "don't use tables"),
        ("you keep using tables", "no tables"),
        ("stop apologizing", "you keep apologizing"),
        ("use plain english", "less jargon"),
        ("no emoji please", "stop using emojis"),
        ("be more concise", "way too long"),
        ("stop rambling", "keep it short"),
        ("again with the bullets", "no bullet points"),
    ],
)
def test_same_correction_meets(a, b):
    assert similar(signature(a), signature(b)), (signature(a), signature(b))


@pytest.mark.parametrize(
    "a,b",
    [
        ("make it longer", "make it shorter"),
        ("no tables", "no emoji"),
        ("use tables", "no tables"),
        ("no bullet points", "no headings"),
        ("stop apologizing", "no tables"),
        ("ask me first", "don't ask me first"),
        ("", "shorter"),
    ],
)
def test_different_corrections_do_not_meet(a, b):
    assert not similar(signature(a), signature(b)), (signature(a), signature(b))


def test_signature_is_normalised_and_sorted():
    assert signature("Shorter please") == "short"
    assert signature("no tables") == "not table"
    assert signature("Stop. Using. TABLES!") == "not table"
    assert signature("no, shorter") == "short"  # "no," is an interjection


def test_proposal_text_uses_the_users_own_words():
    assert proposal_text(["no, shorter please"]) == "Shorter."
    assert proposal_text(["I said no tables"]) == "No tables."
    assert proposal_text(["please don't use bullet points again"]) == "Don't use bullet points."
    # an instruction beats a complaint
    assert proposal_text(["you keep using tables", "no tables"]) == "No tables."
    assert proposal_text(["too long", "make it shorter"]) == "Make it shorter."
    assert proposal_text([]) == ""
    assert proposal_text(["please", "again"]) == ""
    long = proposal_text(["don't " + "really " * 80 + "use tables"])
    assert len(long) <= 280 and long.endswith(".")


def test_detector_never_raises():
    for bad in (None, 12, object()):
        assert is_correction(bad) is False  # type: ignore[arg-type]
        assert isinstance(signature(bad), str)  # type: ignore[arg-type]
