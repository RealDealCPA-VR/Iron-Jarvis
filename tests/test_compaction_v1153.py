"""Compaction: a model writes it, the ledger has to agree with it (v1.153.0).

The deterministic recap that shipped in v1.146.0/v1.152.0 cannot fabricate,
because it says almost nothing — a 20-step run that drops 15 steps got fifteen
lines of "working on X; ran read_file". This module lets a model write a real
summary and then makes the RECORD the authority on whether it may be shown.

THE TEST THAT MATTERS is :func:`test_an_invented_file_is_stripped_from_the_
summary`. Everything else here is threshold arithmetic and plumbing; that one is
the entire reason a model-written summary is allowed near the prompt at all.
"""

from __future__ import annotations

import pytest

from iron_jarvis.context.compaction import (
    AUTO_AT,
    MIN_COVERED,
    SUGGEST_AT,
    Compaction,
    compact_messages,
    level,
    prefix_key,
    pressure,
    render,
    verify,
)


def _complete_returning(text: str):
    """A fake one-shot completion — no provider, no network."""

    async def _c(system: str, user: str):
        return text, "mock", "mock-1"

    return _c


def _covered(n: int = 8):
    return [("user" if i % 2 == 0 else "assistant", f"message number {i}") for i in range(n)]


# --------------------------------------------------------------------------- #
# (1) THE GUARANTEE: the ledger and the transcript decide what may be said.
# --------------------------------------------------------------------------- #
def test_an_invented_file_is_stripped_from_the_summary():
    """A model that reports work on a file nobody ever touched must not have
    that claim reach the next prompt — the agent would build on it."""
    summary = (
        "DONE:\n"
        "- Updated src/real_thing.py with the new parser\n"
        "- Rewrote src/totally_invented.py to match\n"
    )
    clean, stripped = verify(
        summary,
        transcript_text="I edited src/real_thing.py just now",
        ledger_paths={"src/real_thing.py"},
    )
    assert "real_thing.py" in clean
    assert "totally_invented.py" not in clean
    assert any("totally_invented" in s for s in stripped)


def test_a_file_the_ledger_knows_survives_even_if_the_prose_never_named_it():
    """The ledger is derived from what the tools DID, so it outranks the
    transcript's wording — a path it recorded is real by definition."""
    clean, stripped = verify(
        "DONE:\n- wrote reports/q3.xlsx\n",
        transcript_text="I created the spreadsheet you asked for",
        ledger_paths={"C:/work/reports/q3.xlsx"},
    )
    assert "q3.xlsx" in clean
    assert stripped == []


def test_an_invented_quotation_is_stripped():
    """Quoting is how a summary smuggles in words nobody said."""
    clean, stripped = verify(
        'FACTS:\n- The user said "ship it on Friday no matter what"\n',
        transcript_text="the user asked when we should ship",
    )
    assert "Friday" not in clean
    assert stripped


def test_a_real_quotation_survives():
    clean, stripped = verify(
        'FACTS:\n- The user said "use the staging database"\n',
        transcript_text="please use the staging database for this",
    )
    assert "staging database" in clean
    assert stripped == []


def test_section_headers_are_never_stripped():
    clean, _ = verify("GOAL:\nDONE:\nOPEN:", transcript_text="")
    assert "GOAL:" in clean and "DONE:" in clean


def test_a_summary_that_survives_nothing_is_not_shown():
    """Better no summary than a summary the record refuses to support."""

    async def run():
        return await compact_messages(
            [("user", "hello there friend")] * 8,
            complete=_complete_returning("- I rewrote /etc/nonexistent/thing.py\n"),
        )

    import asyncio

    out = asyncio.run(run())
    assert out.ok is False
    assert out.summary == ""
    assert out.stripped > 0


# --------------------------------------------------------------------------- #
# (2) FAILURE IS NOT AN ERROR — the caller keeps the deterministic recap.
# --------------------------------------------------------------------------- #
def test_a_failed_model_call_degrades_instead_of_raising():
    async def boom(system, user):
        raise RuntimeError("provider down")

    import asyncio

    out = asyncio.run(compact_messages(_covered(), complete=boom))
    assert isinstance(out, Compaction)
    assert out.ok is False and out.summary == ""


def test_an_empty_reply_is_not_presented_as_a_summary():
    import asyncio

    out = asyncio.run(compact_messages(_covered(), complete=_complete_returning("   ")))
    assert out.ok is False


def test_too_little_to_compact_makes_no_model_call():
    called = False

    async def _c(system, user):
        nonlocal called
        called = True
        return "x", "mock", "mock-1"

    import asyncio

    out = asyncio.run(compact_messages(_covered(MIN_COVERED - 1), complete=_c))
    assert called is False, "a model call for three messages is pure waste"
    assert out.ok is False


def test_a_good_summary_is_headed_as_a_condensation():
    """It goes in the SYSTEM prompt; it must read as a note ABOUT the
    conversation, never as something a participant said."""
    import asyncio

    out = asyncio.run(
        compact_messages(
            _covered(),
            complete=_complete_returning("GOAL:\n- finish the parser\n"),
        )
    )
    assert out.ok is True
    assert out.summary.startswith("# Earlier in this conversation (compacted")
    assert "finish the parser" in out.summary


def test_render_is_empty_for_an_empty_summary():
    assert render("") == "" and render("   ") == ""


# --------------------------------------------------------------------------- #
# (3) THE THRESHOLDS the user asked for: tell me at 70, act around 92.
# --------------------------------------------------------------------------- #
def test_the_thresholds_are_where_they_were_specified():
    assert SUGGEST_AT == 0.70
    assert 0.90 <= AUTO_AT <= 0.95


@pytest.mark.parametrize(
    "ratio,expected",
    [
        (0.0, "ok"),
        (0.69, "ok"),
        (0.70, "suggest"),
        (0.85, "suggest"),
        (0.919, "suggest"),
        (0.92, "auto"),
        (2.0, "auto"),
    ],
)
def test_the_level_boundaries(ratio, expected):
    assert level(ratio) == expected


def test_pressure_can_exceed_one_hundred_percent():
    """The whole point of measuring RAW demand: a conversation that has outgrown
    its model reports 130%, not a saturated 100%."""
    assert pressure(13_000, 10_000) == pytest.approx(1.3)


def test_pressure_is_safe_on_an_unknown_window():
    assert pressure(5000, 0) == 0.0


# --------------------------------------------------------------------------- #
# (4) CONTENT ADDRESSING — paid for once, shared by forks.
# --------------------------------------------------------------------------- #
def test_the_same_prefix_is_the_same_key():
    assert prefix_key(["a", "b", "c"]) == prefix_key(["a", "b", "c"])


def test_a_different_prefix_is_a_different_key():
    assert prefix_key(["a", "b"]) != prefix_key(["a", "b", "c"])


def test_the_key_cannot_be_confused_by_concatenation():
    """Without a separator, ["ab","c"] and ["a","bc"] would collide and a thread
    would silently inherit another conversation's summary."""
    assert prefix_key(["ab", "c"]) != prefix_key(["a", "bc"])


# --------------------------------------------------------------------------- #
# (5) RE-COMPACTION absorbs the summary it replaces.
# --------------------------------------------------------------------------- #
def test_a_second_compaction_is_given_the_summary_it_replaces():
    """Coverage always restarts from the beginning, and the caller holds ONE
    summary string. Without the prior riding along, the second compaction would
    silently drop everything the first one said while its messages stayed
    covered — history in neither the transcript nor the summary."""
    import asyncio

    seen: list[str] = []

    async def capture(system, user):
        seen.append(user)
        return "GOAL:\n- carry on\n", "mock", "mock-1"

    asyncio.run(
        compact_messages(_covered(), complete=capture, prior="EARLIER: vault code is 42")
    )
    assert seen
    assert "vault code is 42" in seen[0], "the prior summary never reached the model"


def test_a_fact_carried_from_the_prior_summary_is_not_stripped():
    """The prior was verified when it was written. Judging it again against a
    transcript that no longer contains its source would delete true facts the
    moment they were carried forward — verification eating its own output."""
    clean, stripped = verify(
        "FACTS:\n- the vault code is 42\n",
        transcript_text="unrelated recent chatter\nEARLIER: the vault code is 42",
    )
    assert "42" in clean
    assert stripped == []
