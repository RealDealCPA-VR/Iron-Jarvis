"""Arming an acting browser tool arms the reader it structurally requires.

WHY THIS FILE EXISTS. Ship 3 registered eight acting tools, put four of them on
the deny floor, gave each an ask-tier rule, and pinned that a real sentence
reaches the acting tool. Every one of those pins passed, and the ship's headline
flow still dead-ended.

`browser_click`, `browser_type` and `browser_press_key` call `prepare_action`
with ``need_snapshot=True`` (implementation plan section 8.6 -- "acting on a page
nobody has read is acting blind"), so on a tab nothing has read they refuse:

    STALE_SNAPSHOT: No current snapshot for this tab. Call browser_read_page and
    retry with the new element ID.

The model follows that remedy, calls ``browser_read_page``, and the v1.227.0
roster gate refuses it -- the reader was never armed, so it "is not one of this
agent's tools". Two refusals, nothing done, no move left. That is the whole
failure, and it is invisible to a test that asserts the ACTING tool is armed.

So this file drives the two selectors together and asserts the PAIR, on the
plan's own live-drive sentence and on every element-addressed sentence the ship
pins. The other five acting rules pass ``need_snapshot=False``, cannot dead-end
this way, and are asserted NOT to arm a reader -- a false arm on "go to
example.com" costs a schema slot for a page nobody needs read.
"""

from __future__ import annotations

import pytest

from iron_jarvis.tools.autoselect import (
    ASK_TIER_TOOLS,
    AUTO_SAFE_TOOLS,
    select_ask_tools,
    select_auto_tools,
)

#: The tool the STALE_SNAPSHOT remedy names, verbatim.
READER = "browser_read_page"

#: Sentence -> the acting tool it asks for. The first is the implementation
#: plan's own Ship 3 live drive, step 2, written the way the plan writes it.
ELEMENT_ADDRESSED = [
    ("type a phrase into the search field and press Enter", "browser_type"),
    ("click the sign in button", "browser_click"),
    ("submit the form on that page", "browser_click"),
    ("type my email into the form", "browser_type"),
    ("type my email into the search box on this page", "browser_type"),
    ("fill in the search box with acme corp", "browser_type"),
    ("press Enter", "browser_press_key"),
    ("hit escape", "browser_press_key"),
]


@pytest.mark.parametrize(
    ("sentence", "acting"),
    ELEMENT_ADDRESSED,
    ids=[s[:38].replace(" ", "_") for s, _ in ELEMENT_ADDRESSED],
)
def test_an_acting_sentence_arms_the_reader_the_action_depends_on(sentence, acting):
    """DRIVEN through both selectors, never asserted as membership.

    Membership is exactly what `browser_read_page` already had when this
    dead-ended: it has been in `AUTO_SAFE_TOOLS` since Ship 2, with rules of its
    own that these sentences do not match.
    """
    ask = select_ask_tools(sentence)
    auto = select_auto_tools(sentence)
    assert acting in ask, f"{sentence!r} armed {ask} -- not the acting tool it asks for"
    assert READER in auto, (
        f"{sentence!r} arms {acting} with no page reader (auto={auto}). The tool "
        "refuses STALE_SNAPSHOT and the remedy it hands the model names "
        f"{READER}, which the roster gate then refuses because nothing armed it."
    )


def test_the_reader_is_granted_and_the_acting_tool_is_only_visible():
    """The pairing must not be bought by loosening the gate.

    `browser_read_page` rides the AUTO tier (granted, `allow` by default, no
    card); the acting tool rides the ASK tier (visible, ungranted, pauses for
    the approval card the deny floor exists to raise). If the reader ever joins
    `ASK_TIER_TOOLS` the user gets a card in front of looking, and if an acting
    tool ever joins `AUTO_SAFE_TOOLS` a click on their bank runs with nobody
    asked.
    """
    assert READER in AUTO_SAFE_TOOLS and READER not in ASK_TIER_TOOLS
    for _sentence, acting in ELEMENT_ADDRESSED:
        assert acting in ASK_TIER_TOOLS and acting not in AUTO_SAFE_TOOLS, acting


@pytest.mark.parametrize(
    "sentence",
    [
        "go to example.com in my browser",
        "navigate to https://portal.example/login",
        "open a new tab for the client portal",
        "scroll down on this page",
        "switch to the tab with the invoice",
        "close that tab",
    ],
)
def test_an_action_that_addresses_no_element_arms_no_page_reader(sentence):
    """`navigate`, `create_tab`, `scroll`, `activate_tab` and `close_tab` all
    pass ``need_snapshot=False``, so none of them can refuse STALE_SNAPSHOT --
    and a reader armed for them would be a wasted slot of the arming cap on a
    page nobody asked to have read."""
    assert READER not in select_auto_tools(sentence), select_auto_tools(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "click through the numbers with me",
        "type up a memo for the client",
        "the client clicked accept in Karbon",
        "enter the amount in cell B4",
        "scroll through the client list in the spreadsheet",
    ],
)
def test_office_chatter_arms_neither_half(sentence):
    """The pairing rides the SAME patterns as the acting rules, so it inherits
    their browser-noun requirement -- and this asserts it did not smuggle a
    browser tool onto office sentences through the auto tier, where there is no
    card to catch it."""
    armed = [n for n in select_ask_tools(sentence) + select_auto_tools(sentence)
             if n.startswith("browser_")]
    assert not armed, armed
