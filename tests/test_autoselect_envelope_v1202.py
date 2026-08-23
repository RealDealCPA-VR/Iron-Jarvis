"""The envelope arming budget in the selector (v1.202.0, Wave B item B1).

v1.201.0 shipped the Capability Envelope; ``CapabilityProfile.max_tools()``
returns ``None`` (no envelope cap) or a measured band (6/4/3). This file pins
the PURE half of B1 — ``select_auto_tools`` / ``tools_named_in_playbook``
taking that band as ``max_tools`` — before the lanes start passing it:

  §1 ``max_tools=None`` is BYTE-IDENTICAL to not passing it, pinned against a
     RECORDED selection (not a same-call comparison, which would stay green if
     both sides drifted together). Trusted/unmeasured profiles and every
     pre-envelope caller take this path, so it is the frontier-sees-zero-change
     guarantee at this layer.
  §2 an int band shrinks the auto slots at the SAME ranked truncation site —
     result == today's list cut short, order preserved. A re-rank under the
     band would be a different (and wrong) selector.
  §3 the explicit-picks contract: a "+" pick is a CONSENT statement and the
     envelope is a SKILL measurement — skill evidence never overrides consent.
     Structurally the selector returns auto slots only, so the band cannot
     touch a pick; the caller-side composition (remaining budget =
     ``max_tools - len(explicit)``) yields ZERO auto slots when the picks
     alone spend the band, and every pick survives.
  §4 min-semantics: a band WIDER than today's cap changes nothing — the
     envelope only ever narrows, it never widens (the same only-narrow rule
     ``envelope/profile.py`` applies to provenance).
  §5 the same contract for the playbook lane, which fills the same armed list
     under the same ``_MAX_ARMED_TOOLS`` — a band the "/"-skill pass ignored
     would be a cap a skill invocation silently walks around.
"""

from __future__ import annotations

import inspect

from iron_jarvis.tools.autoselect import select_auto_tools, tools_named_in_playbook

# A rich request that fills today's 6-slot cap exactly, with the overflow
# recorded too: at cap=99 the same sentence ranks 12 tools. RECORDED from the
# real selector at v1.202.0 (v1.201.0-identical — the fold is inert at None);
# if a scoring change moves these, re-record BOTH lists deliberately.
_RICH = (
    "extract the tables from the pdf of my notes and check the formulas in "
    "the sheet"
)
_RICH_TODAY = [
    "read_document",
    "excel_profile",
    "file_search",
    "recall",
    "excel_query",
    "ltm_search",
]

_PLAYBOOK = (
    "First call redact_scan on the file, then read_document, then excel_read, "
    "then file_search, then web_search, then recall, then history_search."
)
_PLAYBOOK_TODAY = [
    "redact_scan",
    "read_document",
    "excel_read",
    "file_search",
    "web_search",
    "recall",
]


# --- §1 None -> byte-identical (the frontier / pre-envelope path) -----------


def test_no_envelope_is_byte_identical_to_the_recorded_selection():
    assert select_auto_tools(_RICH) == _RICH_TODAY
    assert select_auto_tools(_RICH, max_tools=None) == _RICH_TODAY


def test_the_parameter_is_optional_with_a_none_default():
    # Every pre-envelope caller (both chat lanes, `arm_for_task`, and the
    # UNCAPPED consent gate in `attachment_rag.change_verbs_wanted`) reaches
    # the selector without the argument; the default must be the no-envelope
    # path or this change is not additive.
    for fn in (select_auto_tools, tools_named_in_playbook):
        param = inspect.signature(fn).parameters["max_tools"]
        assert param.default is None
        assert param.kind is inspect.Parameter.KEYWORD_ONLY


# --- §2 a band shrinks the SAME truncation, order preserved ------------------


def test_a_band_of_4_is_todays_list_cut_to_4_in_todays_order():
    assert select_auto_tools(_RICH, max_tools=4) == _RICH_TODAY[:4]


def test_every_band_is_a_prefix_of_todays_ranking():
    # The band truncates at the one slice `cap` has always truncated; it never
    # re-ranks. A selector that re-scored under pressure would break the
    # calibrated orderings the v1.196.0 comments pin sentence by sentence.
    for band in (1, 2, 3, 4, 5, 6):
        assert select_auto_tools(_RICH, max_tools=band) == _RICH_TODAY[:band]


def test_the_band_composes_with_cap_as_a_min_not_a_replacement():
    # A caller that already shrank `cap` for filled slots keeps that shrink:
    # cap=3 (three slots left under _MAX_ARMED_TOOLS) with a band of 5 arms 3.
    assert select_auto_tools(_RICH, cap=3, max_tools=5) == _RICH_TODAY[:3]
    # ...and the band bites when IT is the smaller one.
    assert select_auto_tools(_RICH, cap=5, max_tools=2) == _RICH_TODAY[:2]


# --- §3 explicit picks are never the envelope's to drop ----------------------


def test_picks_spending_the_whole_band_leave_zero_auto_slots_and_all_picks():
    # The chat-lane composition: the user hand-armed five tools, the envelope
    # band is 4. Consent wins — all five picks survive untouched (they never
    # pass through the selector) and the REMAINING budget the caller passes,
    # max_tools - len(explicit) <= 0, yields zero auto slots.
    explicit = ["shell", "excel_edit", "pdf_arrange", "write_document", "repl"]
    band = 4
    remaining = band - len(explicit)  # -1: the band is already overspent
    auto = select_auto_tools(
        _RICH, exclude=set(explicit), max_tools=remaining
    )
    assert auto == []
    # The picks were never the selector's to shrink: nothing above mutated the
    # list, and the armed set the caller assembles is exactly picks + auto.
    assert explicit + auto == explicit


def test_a_zero_or_negative_remainder_selects_nothing():
    for spent in (0, -1, -5):
        assert select_auto_tools(_RICH, max_tools=spent) == []


def test_a_partial_remainder_fills_only_what_the_band_left():
    # Two picks under a band of 4 leave two auto slots — the top two of
    # today's ranking (minus the picks, which exclude already removes).
    explicit = ["shell", "repl"]
    auto = select_auto_tools(_RICH, exclude=set(explicit), max_tools=4 - len(explicit))
    assert auto == _RICH_TODAY[:2]


# --- §4 min semantics: the envelope only ever narrows ------------------------


def test_a_band_wider_than_todays_cap_changes_nothing():
    assert select_auto_tools(_RICH, max_tools=10) == _RICH_TODAY
    assert select_auto_tools(_RICH, max_tools=99) == _RICH_TODAY


# --- §5 the playbook lane takes the same band --------------------------------


def test_playbook_none_is_byte_identical_and_a_band_truncates_in_order():
    assert tools_named_in_playbook(_PLAYBOOK) == _PLAYBOOK_TODAY
    assert tools_named_in_playbook(_PLAYBOOK, max_tools=None) == _PLAYBOOK_TODAY
    assert tools_named_in_playbook(_PLAYBOOK, max_tools=3) == _PLAYBOOK_TODAY[:3]
    assert tools_named_in_playbook(_PLAYBOOK, max_tools=99) == _PLAYBOOK_TODAY
    assert tools_named_in_playbook(_PLAYBOOK, max_tools=0) == []


def test_playbook_band_composes_with_cap_as_a_min():
    assert (
        tools_named_in_playbook(_PLAYBOOK, cap=2, max_tools=5)
        == _PLAYBOOK_TODAY[:2]
    )
    assert (
        tools_named_in_playbook(_PLAYBOOK, cap=5, max_tools=2)
        == _PLAYBOOK_TODAY[:2]
    )
