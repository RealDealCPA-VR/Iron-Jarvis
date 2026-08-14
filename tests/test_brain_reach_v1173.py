"""The shared brain answers to anyone's words, and every agent can reach it
(v1.173.0).

From the user's report: *"multiple agents are to utilize this centralized
brain, so much of what long-term details are needed should be ALWAYS
REACHABLE. Also for calling it, terms like 'search your memory' or 'look into
the history' would be more general for all the users, not just myself."*

Two defects, both silent:

1. THE CALLING WORDS WERE THIS FIRM'S. v1.172.x grew nouns like "firm docs"
   and "hermes brain" — real phrasings, but only *one* user's. The general
   asks ("search your memory", "look it up", "what do we have on X", "look
   into the history") matched no rule, so a question aimed squarely at the
   brain armed nothing and got answered from thin air.
2. THE BRAIN DID NOT REACH EVERY AGENT. REVIEWER and SUPERVISOR carried
   ``recall`` but not ``ltm_search``; MAINTAINER the same. The two are not
   interchangeable — recall ranks by meaning across every store, ltm_search
   asks the knowledge base itself — and ``agents/types.py``'s own header
   records the lesson: a tool absent from a definition reaches NO session.

Precision is half the job here. Every negative below was decided case by case
and is pinned so a later widening of the vocabulary has to face it: a lookup
verb pointed at the web, at a file, or at a history that is not OUR record
must keep its own sentence.

Offline: pure regex + definition data, plus one real ``build_platform``.
"""

from __future__ import annotations

# Register the workflow tables on SQLModel.metadata before init_db builds the
# schema (build_platform below creates a real DB). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401

from collections import Counter

import pytest

from iron_jarvis.agents.types import _BRAIN_TOOLS, _DEFINITIONS, get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.autoselect import _RULES, AUTO_SAFE_TOOLS, select_auto_tools

# The two read paths into the shared brain.
RECALL = "recall"
LTM = "ltm_search"
HISTORY = "history_search"
MEMORY_TOOLS = {RECALL, LTM, HISTORY}


def _armed(message: str) -> list[str]:
    return select_auto_tools(message)


# --------------------------------------------------------------------------- #
# 1. the general calling vocabulary
# --------------------------------------------------------------------------- #
GENERAL_MEMORY_ASKS = [
    # the user's headline example, and its neighbours
    "search your memory for the Henderson fee schedule",
    "look in your memory for that engagement letter",
    "look into your memory before you answer",
    "check your memory for the filing deadline",
    # notes / knowledge / records — the same store, other people's nouns
    "check your notes for the retention policy",
    "search your notes for the fee schedule",
    "answer from your knowledge of the client",
    "check our records for the EIN",
    "look in the team's notes for the onboarding steps",
    # verb-shaped asks that name no store at all
    "look it up",
    "look that up for me",
    "look this up in your notes",
    "what do we have on the antique mall client",
    "what do you have on the Henderson engagement",
    "dig up everything on that entity",
    "pull up what we have on the S-corp election",
]


@pytest.mark.parametrize("message", GENERAL_MEMORY_ASKS)
def test_general_memory_vocabulary_arms_both_read_paths(message):
    """Both, not either: ``recall`` federates every store while ``ltm_search``
    asks the knowledge base directly (and decomposes a multi-term question
    there since v1.173.0). One without the other is half a brain."""
    armed = _armed(message)
    assert RECALL in armed, f"no recall for: {message!r} -> {armed}"
    assert LTM in armed, f"no knowledge-base search for: {message!r} -> {armed}"


@pytest.mark.parametrize("message", GENERAL_MEMORY_ASKS)
def test_recall_keeps_the_first_slot_on_a_memory_question(message):
    """The cap is 6 and the new rules add weight to the same sentences the old
    ones scored — so the pin is ORDER, not mere presence. A memory question
    that arms recall in slot 5 has already spent its budget on the web."""
    armed = _armed(message)
    assert armed[0] == RECALL, f"recall was crowded out of: {message!r} -> {armed}"


def test_the_bare_idiom_arms_exactly_the_brain_and_nothing_else():
    """A value assertion, not a membership one: "look it up" carries no other
    signal at all, so the whole list is the contract. A rule that leaked
    web_search in here would be inventing an external subject the user never
    named."""
    assert _armed("look it up") == [RECALL, LTM]
    assert _armed("look that up") == [RECALL, LTM]


# --------------------------------------------------------------------------- #
# 2. "the history" — and the two surfaces it can mean
# --------------------------------------------------------------------------- #
HISTORY_ASKS = [
    "look into the history",
    "search the history for that decision",
    "check the history for the fee we quoted",
    "look back at our chat history",
    "go back through our conversation history",
    # The two asymmetries that used to send a question about the user's OWN
    # record to the internet: `look` could take a preposition and `search`
    # could not, and the conversation nouns were singular-only. Both sentences
    # armed ['web_search', 'web_fetch'] and nothing else, because the web rule
    # owns the bare verb `search`.
    "search through the history",
    "search back through the history",
    "search our messages history",
]


@pytest.mark.parametrize("message", HISTORY_ASKS)
def test_history_vocabulary_arms_the_conversation_index_and_recall(message):
    armed = _armed(message)
    assert armed[0] == HISTORY, f"{message!r} -> {armed}"
    assert RECALL in armed, f"the federated reader was dropped: {message!r} -> {armed}"
    assert LTM in armed, (
        "a shared brain's long-term record can live in the knowledge base too — "
        f"offering only this app's threads guesses: {message!r} -> {armed}"
    )


def test_the_two_surfaces_are_never_blurred():
    """``history_search`` reads THIS APP's own threads; ``ltm_search`` reads the
    knowledge bases. A question about what WE STORE is not a question about
    what WE SAID, and answering one with the other reads to the user as "we
    never discussed that"."""
    stored = _armed("search your memory for the fee schedule")
    assert HISTORY not in stored, f"a memory ask armed the thread index: {stored}"

    said = _armed("look into the history")
    assert HISTORY in said

    # A sentence naming BOTH concepts arms both — that is not blurring, it is
    # the user having asked for two things.
    both = _armed("search your memory and our chat history for the fee schedule")
    assert {RECALL, LTM, HISTORY} <= set(both), both


# --------------------------------------------------------------------------- #
# 3. precision — every one of these was decided deliberately
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("message", "must_keep"),
    [
        # A lookup verb aimed at the WEB keeps its own sentence.
        ("search the web for the latest Python release", "web_search"),
        ("look up the IRS phone number online", "web_search"),
        ("google the filing deadline", "web_search"),
        # ...aimed at a FILE, likewise.
        ("search this file for the total", "file_search"),
        ("find the invoice files in my folder", "file_search"),
    ],
)
def test_a_lookup_verb_pointed_elsewhere_never_arms_the_brain(message, must_keep):
    armed = _armed(message)
    assert not (MEMORY_TOOLS & set(armed)), f"over-armed: {message!r} -> {armed}"
    assert must_keep in armed, (
        f"the new rules displaced the tool this sentence needs: {message!r} -> {armed}"
    )


@pytest.mark.parametrize(
    ("message", "must_keep"),
    [
        # An unambiguous web marker after the anaphor. Both readings are live
        # ("it" may well be something we hold), so the brain still arms — but
        # the tool the user NAMED leads. `recall` used to tie web_search at 8
        # and win the alphabetical tiebreak, so "look it up on google" answered
        # from memory first.
        ("look it up on google", "web_search"),
        ("look it up online", "web_search"),
        # And the one with no web_search at all: `\bweb\b` does not match
        # inside "website", so this armed the brain and NOTHING that can reach
        # the internet.
        ("look that up on the IRS website", "web_search"),
    ],
)
def test_a_named_web_marker_outranks_the_anaphoric_idiom(message, must_keep):
    armed = _armed(message)
    assert armed[0] == must_keep, f"the brain led an explicit web ask: {message!r} -> {armed}"


# --------------------------------------------------------------------------- #
# 3b. the cap is 6 — a store noun in passing must not evict the user's verb
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("message", "must_keep"),
    [
        # The v1.153.2 class of failure, reached through the auto-arm budget
        # instead of through a path: the user says REDACT and only
        # `redact_scan` (which writes nothing) is armed, so the reply announces
        # work no armed tool can do. `memor(y|ies)` and `notes?` are owned by
        # the v1.141.0 rule, so a full-weight possessive rule double-scored the
        # same words and pushed the sentence's own tool past slot 6.
        ("redact the pii in my notes pdf and save a new file in the folder", "redact_pii"),
        ("our records show a $500 payment; add it to the spreadsheet and total by client",
         "excel_edit"),
        ("our records show a $500 payment; add it to the spreadsheet and total by client",
         "read_document"),
    ],
)
def test_a_store_noun_in_passing_never_evicts_the_sentences_own_tool(message, must_keep):
    """The verb names the task; the store noun is incidental. Membership is the
    assertion because the cap is 6 and these sentences saturate it — a tool at
    rank 7 reaches the model exactly as often as one that was never written.

    Every case here is mutation-proven: restoring the possessive rule to full
    weight drops each of these names past slot 6. Cases that survive the mutant
    are deliberately NOT listed — a pin that cannot fail is decoration.

    What this test does NOT claim: the cap is still 6, so widening the v1.141.0
    rule to reach the knowledge bases genuinely costs one slot on a saturated
    sentence. "extract the tables from the pdf of my notes and check the
    formulas in the sheet" now ranks `excel_formula_check` 7th, and "convert my
    notes to a pdf and save the report in my folder" ranks `list_files` 7th.
    Those are the price of the widening, paid where a second tool for the same
    job is already armed (`excel_query`, `file_search`) — not the double-scoring
    bug, which cost sentences the ONLY tool that could do what was asked."""
    armed = _armed(message)
    assert must_keep in armed, f"crowded out of the cap: {message!r} -> {armed}"
    assert len(armed) <= 6


@pytest.mark.parametrize(
    "message",
    [
        # A history that is not OUR record. The article must sit directly
        # against the noun, so an intervening word drops the match.
        "check the browser history for that site",
        "look through the git history for that commit",
        "search the revision history of the file",
        "review the payment history on that account",
        # "history" as a TOPIC, not a place to look — the `of` lookahead.
        "the history of the S-corp election",
        "look into the history of the S-corp election",
        "tell me about the history of the firm",
        # No possessive: the RAM sense, and plain prose.
        "memory usage of the process is high",
        "brain surgery is scheduled for next week",
        "the doctor appointment is tuesday",
        "hey, how are you today?",
    ],
)
def test_the_new_vocabulary_does_not_hijack_unrelated_messages(message):
    armed = _armed(message)
    assert not (MEMORY_TOOLS & set(armed)), f"over-armed: {message!r} -> {armed}"


def test_the_of_lookahead_is_the_only_difference_between_two_sentences():
    """The adversarial pair, one word apart. If the lookahead is ever dropped,
    every "history of X" question starts searching conversations."""
    assert HISTORY in _armed("look into the history")
    assert HISTORY not in _armed("look into the history of the S-corp election")


# --------------------------------------------------------------------------- #
# 4. budget + safe-set discipline
# --------------------------------------------------------------------------- #
def test_recall_survives_the_tightest_cap():
    assert select_auto_tools("search your memory for the fee schedule", cap=1) == [
        RECALL
    ]


def test_a_kitchen_sink_memory_question_still_leads_with_recall():
    """Every memory-ish rule firing at once (memory noun + wiki noun + history
    + a conversation question) must not push recall down the list, and must
    stay inside the 6-tool budget."""
    message = (
        "look in your memory and the firm docs, and search the history, for "
        "what we decided about the S-corp election"
    )
    armed = _armed(message)
    assert armed[0] == RECALL, armed
    assert LTM in armed and HISTORY in armed, armed
    assert len(armed) <= 6
    assert set(armed) <= AUTO_SAFE_TOOLS


def test_every_rule_stays_inside_the_curated_safe_set():
    """The module's whole safety story: candidates come EXCLUSIVELY from
    AUTO_SAFE_TOOLS. A new rule naming shell/mcp/pixio would be filtered out
    silently at selection time, so assert it at the source instead."""
    for rx, weights in _RULES:
        for name in weights:
            assert name in AUTO_SAFE_TOOLS, f"{name} armed by {rx.pattern[:60]!r}"


def test_the_v1172_vocabulary_still_arms_after_the_widening():
    """No regression: the nouns that shipped in v1.172.x keep working."""
    for message in (
        "what does our wiki say about the S-corp election?",
        "look in the firm docs for the client template",
        "check the brain for that client's onboarding steps",
        "is that in the knowledge base?",
    ):
        armed = _armed(message)
        assert RECALL in armed, f"{message!r} -> {armed}"
        assert LTM in armed or "file_search" in armed, f"{message!r} -> {armed}"


def test_the_oldest_memory_rule_now_reaches_the_knowledge_bases_too():
    """This sentence fires ONE rule — the v1.141.0 memory-vocabulary rule — and
    that rule armed only the federated reader and the note WRITER until
    v1.173.0. The tool that goes straight at the wiki was missing from the
    plainest memory question in the app."""
    armed = _armed("what do you know about the Henderson engagement?")
    assert armed[0] == RECALL
    assert LTM in armed, armed
    assert HISTORY not in armed, armed  # not a question about what we SAID


# --------------------------------------------------------------------------- #
# 5. always reachable: every agent holds both read paths
# --------------------------------------------------------------------------- #
def test_every_builtin_agent_type_has_a_definition():
    """A type missing from _DEFINITIONS falls back to BUILDER's loadout, which
    would make the reach test below pass without the type ever being audited."""
    assert set(_DEFINITIONS) == set(AgentType)


@pytest.mark.parametrize("agent_type", list(AgentType))
def test_every_agent_type_carries_both_brain_tools(agent_type):
    """The "multiple agents, one brain" requirement, stated per type. Both
    names, on every definition, no exceptions — the file's own lesson is that a
    tool absent here reaches no session at all."""
    tools = get_agent_definition(agent_type).tools
    assert RECALL in tools, f"{agent_type.value} cannot recall"
    assert LTM in tools, (
        f"{agent_type.value} cannot search the knowledge bases — it holds only "
        "half the brain"
    )


def test_the_brain_pair_is_exactly_the_two_read_paths():
    """Mutation guard on the shared constant itself: dropping a name from it
    would otherwise only surface as a definition that quietly lost a tool."""
    assert _BRAIN_TOOLS == ["recall", "ltm_search"]


@pytest.mark.parametrize("agent_type", list(AgentType))
def test_no_definition_lists_a_tool_twice(agent_type):
    """``decompose.py`` renders ``', '.join(agent_def.tools)`` into a prompt, so
    a duplicate is not merely untidy — the model is told about it twice."""
    dupes = [name for name, n in Counter(get_agent_definition(agent_type).tools).items()
             if n > 1]
    assert dupes == [], f"{agent_type.value} repeats {dupes}"


def test_the_brain_tools_resolve_to_real_registered_tools(tmp_path):
    """The half of the lesson a name check cannot cover: the definition may
    list a tool the registry never registers, and the agent still sees nothing.
    Ask the REAL registry what each type would be offered."""
    platform = build_platform(str(tmp_path))
    for agent_type in AgentType:
        offered = {
            spec["name"]
            for spec in platform.registry.specs(get_agent_definition(agent_type).tools)
        }
        assert {RECALL, LTM} <= offered, (
            f"{agent_type.value} is offered {sorted(offered & MEMORY_TOOLS)}"
        )


def test_the_mcp_reach_decision_is_deliberate_per_type():
    """``mcp:*`` expands to EVERY connected external tool (mail send, Drive
    write, GitHub), so it is an INTEGRATION grant, not a brain fix — a store
    registered as an LTM source is already reached through ltm_search/recall.
    The knowledge-shaped types therefore do NOT get it: the case for widening
    the curator was an MCP-served store that was never registered as an LTM
    source, but the fail-closed claim that justified it does not hold. A server
    the user once saved with ``auto_approve`` makes ``mcp_call`` allowed at the
    NEXT boot for every holder of the sentinel (proved by
    tests/test_mcp_execution.py::test_permission_gating_and_restart_survival),
    so a headless curation run would gain unattended send/write reach across
    every connected server. Registering the store is the narrow fix."""
    for narrow in (AgentType.MEMORY, AgentType.REVIEWER, AgentType.MAINTAINER):
        assert "mcp:*" not in get_agent_definition(narrow).tools, (
            f"{narrow.value} was widened to every external tool — if that is "
            "intended, say so where the definition lives, and do not call it "
            "fail-closed while auto_approve exists"
        )
    # Whatever the mcp verdict, the brain pair is never what pays for it.
    for agent_type in AgentType:
        tools = get_agent_definition(agent_type).tools
        assert {RECALL, LTM} <= set(tools)
