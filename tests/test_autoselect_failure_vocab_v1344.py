"""v1.344.0 ("Jarvis looks after itself"), lane N-vocab: the failure/health
vocabulary reaches the one sentence->tools scorer, and a question never arms a
record writer.

THREE CLAIMS, each driven through the REAL selectors (never membership alone —
`AUTO_SAFE_TOOLS` is only the allowlist filter at the end of
`select_auto_tools`, and a name there with no scoring rule arms nothing, which
is how `history_search`, `view_image` and `rename_file` each shipped dead):

1. A failure/health sentence ("something is broken", "why did that fail",
   "is the app healthy", "what errors has the daemon logged", "my MCP pack is
   not loading", "the github pack keeps failing", "why is the app slow") arms
   the READERS `app_health` / `pack_status` / `run_doctor` through
   `select_auto_tools` — allow tier, auto. A named pack puts `pack_status`
   first.
2. A REPAIR sentence ("restart the github pack", "reload the pack",
   "re-detect providers", "rescan providers", "fix the pack") ASK-arms
   `pack_restart` / `provider_rescan` through `select_ask_tools` — and only
   in IMPERATIVE position: "why did the provider rescan fail" and "the memo
   says to restart the github pack" arm neither.
3. "what went wrong with my schedule" arms a READER over the user's records
   (`app_search` / `app_status`) and must NOT reach the record writers
   `schedule_update` / `schedule_delete` / `workflow_schedule` through
   `settings.records.record_tools_for` (today's defect: a question armed
   three writers). "delete the failed schedule" is an imperative and MAY arm
   the writer; it must not arm `app_health` ("failed" is an adjective there).

Every table row names the tool it expects, so a miss reads as a sentence and
a name, not as a count.
"""

from __future__ import annotations

import pytest

from iron_jarvis.settings import records
from iron_jarvis.tools.autoselect import (
    ASK_TIER_TOOLS,
    AUTO_SAFE_TOOLS,
    _CHANGE_TOOLS,
    select_ask_tools,
    select_auto_tools,
)

READERS = frozenset({"app_health", "pack_status", "run_doctor"})
REPAIRERS = frozenset({"pack_restart", "provider_rescan"})
SCHEDULE_WRITERS = frozenset({"schedule_update", "schedule_delete", "workflow_schedule"})

# =============================================================================
# 1. Failure / health sentences arm the readers (allow tier, auto)
# =============================================================================

#: (sentence, the reader that must LEAD the selection, every reader that must be present)
MUST_ARM_READERS: list[tuple[str, str, set[str]]] = [
    ("something is broken", "app_health", {"app_health", "run_doctor", "pack_status"}),
    ("why did that fail", "app_health", {"app_health", "run_doctor", "pack_status"}),
    ("is the app healthy", "app_health", {"app_health", "run_doctor"}),
    ("what errors has the daemon logged", "app_health", {"app_health", "run_doctor"}),
    ("my MCP pack is not loading", "pack_status", {"pack_status", "app_health"}),
    ("the tool failed, can you fix it", "app_health", {"app_health", "run_doctor"}),
    ("the github pack keeps failing", "pack_status", {"pack_status", "app_health"}),
    ("why is the app slow", "app_health", {"app_health", "run_doctor"}),
    ("jarvis crashed on me twice today", "app_health", {"app_health", "run_doctor"}),
    ("the model is not responding", "app_health", {"app_health", "run_doctor"}),
    ("is everything ok with the daemon", "app_health", {"app_health", "run_doctor"}),
    ("which packs are loaded right now", "pack_status", {"pack_status", "app_health"}),
    ("claude is down again, what happened", "app_health", {"app_health", "run_doctor"}),
    ("the filesystem pack died this morning", "pack_status", {"pack_status", "app_health"}),
    ("did my schedule run last night? it looks like it failed", "app_health", {"app_health"}),
]


@pytest.mark.parametrize("sentence,leader,present", MUST_ARM_READERS, ids=[r[0] for r in MUST_ARM_READERS])
def test_a_failure_or_health_sentence_arms_the_readers(sentence, leader, present):
    picked = select_auto_tools(sentence)
    assert picked, f"{sentence!r} armed nothing"
    assert picked[0] == leader, f"{sentence!r} led with {picked[0]!r}, not {leader!r}: {picked}"
    assert present <= set(picked), f"{sentence!r} missed {sorted(present - set(picked))}: {picked}"
    # A health question is consent to READ, never to change anything.
    assert not (set(picked) & _CHANGE_TOOLS), f"{sentence!r} armed a writer: {picked}"
    assert not select_ask_tools(sentence), f"{sentence!r} ask-armed {select_ask_tools(sentence)}"


def test_the_readers_are_auto_armable_and_the_repairers_are_not():
    """Membership, pinned AFTER the drive above proves the rules exist."""
    assert READERS <= AUTO_SAFE_TOOLS
    assert not (READERS & ASK_TIER_TOOLS)
    assert REPAIRERS <= ASK_TIER_TOOLS
    assert not (REPAIRERS & AUTO_SAFE_TOOLS), "a repair tool must never be a silent grant"
    assert not (READERS & _CHANGE_TOOLS)


# =============================================================================
# 2. Repair sentences ASK-arm the repairers, in imperative position only
# =============================================================================

MUST_ASK_ARM: list[tuple[str, str]] = [
    ("restart the github pack", "pack_restart"),
    ("reload the pack", "pack_restart"),
    ("fix the pack", "pack_restart"),
    ("retry the mcp server", "pack_restart"),
    ("can you restart the filesystem pack?", "pack_restart"),
    ("re-detect providers", "provider_rescan"),
    ("rescan providers", "provider_rescan"),
    ("please rescan the providers", "provider_rescan"),
    ("refresh the models list", "provider_rescan"),
    ("ollama is back up. re-detect the providers", "provider_rescan"),
]


@pytest.mark.parametrize("sentence,tool", MUST_ASK_ARM, ids=[r[0] for r in MUST_ASK_ARM])
def test_a_repair_sentence_ask_arms_the_repairer(sentence, tool):
    asked = select_ask_tools(sentence)
    assert tool in asked, f"{sentence!r} ask-armed {asked}, not {tool!r}"
    # The ask tier is VISIBLE, never a grant: the auto selector must not hand
    # either repairer out on the same sentence.
    assert not (set(select_auto_tools(sentence, cap=99)) & REPAIRERS)


def test_a_named_pack_repair_rides_with_its_reader():
    """A model that restarts a pack must be able to read what the restart
    said — `pack_status` rides from `_RULES` on the same sentence."""
    assert select_auto_tools("restart the github pack")[0] == "pack_status"


#: A repair verb inside a QUESTION or a REPORT is not a request for one. The
#: readers may still arm (a question about a failure wants them); the
#: repairers must not.
NOT_A_REPAIR_REQUEST = [
    "why did the provider rescan fail",
    "the memo says to restart the github pack",
    "should I restart the pack or leave it",
    "she wants to know if you can rescan the providers",
    "what happens when you reload a pack",
    "do not restart the pack, I am in the middle of something",
    # The verb reached POSITIONALLY (a coordinator, a colon) inside a question
    # or a report of someone else's words — the rows that only the imperative
    # gate refuses. Without `_rule_fires` on the ask side (a plain `rx.search`)
    # every one of these ask-arms a repairer; measured.
    "the client asked whether we should restart the pack and rescan the providers",
    "his note ended with: restart the github pack",
    "she asked if we had tried to reload the pack and rescan the providers yet",
    "the memo lists: rescan providers, then restart the pack",
]


@pytest.mark.parametrize("sentence", NOT_A_REPAIR_REQUEST)
def test_a_question_or_report_about_a_repair_arms_no_repairer(sentence):
    assert not (set(select_ask_tools(sentence)) & REPAIRERS), (
        f"{sentence!r} ask-armed a repairer: {select_ask_tools(sentence)}"
    )


# =============================================================================
# 3. Ordinary work arms none of it
# =============================================================================

MUST_NOT_ARM = [
    "what does this function do",
    "write me an email to the client about the invoice",
    "summarise this pdf",
    "delete the failed schedule",
    "retry the failed run",
    "fix the errors in this spreadsheet",
    "the client is slow to pay, draft a reminder",
    "merge these pdfs into one file",
    "what is the capital of France",
    "how do I turn on computer use",
    "remind me how to update cell B2 to 500",
    "move my nightly schedule to 7am",
    "pack up the Q3 binders for the client",
    "what's the difference between a 1065 and an 1120S",
    "make a spreadsheet from these invoices",
    "how are you today",
    "scroll down the page",
    "search the web for the latest IRS mileage rate",
    "fix the broken link in this doc",
]


@pytest.mark.parametrize("sentence", MUST_NOT_ARM)
def test_ordinary_work_arms_no_health_reader_and_no_repairer(sentence):
    picked = set(select_auto_tools(sentence, cap=99))
    assert not (picked & READERS), f"{sentence!r} armed {sorted(picked & READERS)}"
    assert not (set(select_ask_tools(sentence)) & REPAIRERS), sentence


# =============================================================================
# 4. A question about a record arms a reader, never the record writers
# =============================================================================

RECORD_QUESTIONS = [
    "what went wrong with my schedule",
    "what went wrong with my schedule?",
    "why did the nightly run fail last night",
    "is my invoice workflow broken",
    "why does my weekly schedule keep failing?",
]


@pytest.mark.parametrize("sentence", RECORD_QUESTIONS)
def test_a_record_failure_question_arms_a_reader_and_no_record_writer(sentence):
    picked = select_auto_tools(sentence)
    assert "app_search" in picked, f"{sentence!r} armed no record reader: {picked}"
    assert "app_health" in picked, picked
    assert not (set(picked) & _CHANGE_TOOLS), picked
    armed = records.record_tools_for(sentence)
    assert not (set(armed) & SCHEDULE_WRITERS), (
        f"{sentence!r} reached the record writers through record_tools_for: {armed}"
    )
    assert not any(records.RECORD_TOOL_TIERS.get(n) == "ask" for n in armed), armed
    assert records.is_question(sentence)


def test_a_question_keeps_only_the_record_readers():
    """`goal_list` reads; it may answer a question. Every ask-tier record tool
    and the allow-tier revoker (which would RUN with no card) may not."""
    assert records.record_tools_for("what goals do I have") == ["goal_list"]
    assert records.record_tools_for("is my telegram channel two-way?") == []
    assert records.record_tools_for("which grants are always allowed?") == []
    assert records.record_tools_for("what would undo that do?") == []


def test_an_imperative_still_reaches_the_record_writer():
    """The guard is on QUESTIONS, not on the noun: an instruction keeps every
    writer it had, including a polite one ending in a question mark."""
    for sentence in (
        "delete the failed schedule",
        "move my nightly schedule to 7am",
        "can you delete the Tidy workflow?",
        "Could you pause the deploys webhook, please?",
        "when a client email happens, start an intake session",
        "undo that, then pause the gh webhook",
    ):
        assert not records.is_question(sentence), sentence
    assert "schedule_delete" in records.record_tools_for("delete the failed schedule")
    assert "workflow_delete" in records.record_tools_for("can you delete the Tidy workflow?")
    assert "webhook_update" in records.record_tools_for("Could you pause the deploys webhook, please?")
    assert "reflex_create" in records.record_tools_for("when a client email happens, start an intake session")
    assert "config_undo" in records.record_tools_for("undo that, then pause the gh webhook")
    # ...and "failed" as an ADJECTIVE on a record is not a health sentence.
    assert "app_health" not in select_auto_tools("delete the failed schedule", cap=99)


def test_the_question_guard_reads_the_head_and_the_tail():
    """Both halves of `is_question`, driven: a wh-word or an inverted auxiliary
    at the head (after a discourse opener), or a trailing "?" with no polite
    request in the last clause."""
    assert records.is_question("ok, so why did my schedule fail")
    assert records.is_question("did the nightly schedule run")
    assert records.is_question("should I delete the schedule")
    assert records.is_question("the schedule looks wrong, did it run?")
    assert not records.is_question("can you fix the schedule?")
    assert not records.is_question("please delete the schedule?")
    assert not records.is_question("pause the schedule")
    assert not records.is_question("")
