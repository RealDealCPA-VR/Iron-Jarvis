"""The Browser copy describes the version that is actually installed.

Named for v1.235.0, the ship whose review found the defect, and MAINTAINED every ship
after it -- the file is the moving boundary between what the browser can do and what it
is merely going to do, so it is edited in the same change as the ability, never after.

AT v1.236.0 the browser pairs, reports connection state, lists tabs, names the active
tab, READS THE TEXT OF A PAGE and takes a screenshot. Nine tools. Clicking, typing,
scrolling, navigating and downloads still DO NOT EXIST -- `BrowserRuntime` raises
`NotImplementedError` for each -- and neither does an external harness driving the same
browser, nor the add-on bundled into the installer.

WHY IT EXISTS. At v1.235.0 the shipped copy said otherwise: the Handbook's capability
table offered "read the current page, take a screenshot" as a Read only ability, the
page subtitle and the Help tile promised "read the page you are looking at", the card's
Interactive hint said "Jarvis can also click, type and navigate", and the README
promised the Ship 4 harness. The Guide answers out of `docs/HANDBOOK.md`
(`guide/corpus.py`), so an overselling Handbook is a Guide that lies with confidence --
the failure this repository has been burned by before.

It cuts BOTH ways, which is why the required half matters as much as the forbidden one:
once an ability ships, copy that still hedges it understates what the user paid for, and
they never find the feature. So a ship moves a claim from one list to the other. A claim
deleted from both is a claim nothing checks.

These are content pins, on purpose: the defect was words, so the test reads the
words. Each assertion names the sentence it forbids, so a later ship that
legitimately gains the ability deletes the pin in the same change as the copy --
and cannot re-add a promise a version early by accident.

CRLF is normalised at the reader, no needle contains a newline, and every slice
is anchored to text rather than a byte count.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.browser.errors import REMEDIES

REPO = Path(__file__).resolve().parents[1]
HANDBOOK = REPO / "docs" / "HANDBOOK.md"
README = REPO / "README.md"
CARD = REPO / "dashboard" / "components" / "browser" / "YourBrowserCard.tsx"
BROWSER_PAGE = REPO / "dashboard" / "app" / "computeruse" / "page.tsx"
HELP_PAGE = REPO / "dashboard" / "app" / "help" / "page.tsx"
NAV = REPO / "dashboard" / "lib" / "nav.ts"
ADDON_README = REPO / "extensions" / "chrome" / "README.md"


def _text(p: Path) -> str:
    """One-space text: Markdown and JSX both wrap mid-sentence, so a needle must
    never carry a newline and the reader must not care where the wrap fell."""
    return re.sub(r"\s+", " ", p.read_text(encoding="utf-8").replace("\r\n", "\n"))


def _handbook_browser_section() -> str:
    """The Browser section, sliced by its own headings -- never by a byte window."""
    raw = HANDBOOK.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = raw.find("### Browser — your own Chrome")
    assert start != -1, "the Handbook lost its Browser section heading"
    rest = raw[start + 10 :]
    end = rest.find("\n## ")
    section = rest if end == -1 else rest[:end]
    return re.sub(r"\s+", " ", section)


# --------------------------------------------------------------------------- #
#  What no surface may claim in this version                                   #
# --------------------------------------------------------------------------- #

# Each entry: the file, and a phrase describing an ability this version lacks.
# Phrased as whole clauses so a legitimate mention of the FUTURE ("reading a
# page's text arrives in v1.236.0") is not caught.
# UPDATED AT v1.236.0. Reading a page SHIPPED, so the six read-related claims left
# this list and became REQUIRED phrases below -- that direction of travel is the whole
# point of the file. What stays is what is still not true:
#
#   acting on a page                -> v1.237.0  (click, type, scroll, navigate)
#   an outward harness              -> v1.238.0  (a Build harness, same browser)
#   the add-on inside the installer -> v1.239.0
#
# When one of those ships, move its entries the same way: delete here, assert there.
# A claim deleted from BOTH halves is a claim nothing checks any more.
OVERSOLD = [
    # The nav blurb: "drive" is acting.
    (NAV, "read and drive your own browser"),
    # The Handbook capability table.
    (HANDBOOK, "Also act: click, type, scroll, navigate"),
    # The card's access hints.
    (CARD, "Jarvis can also click, type and navigate"),
    # The README highlights row: the Ship 4 harness.
    (README, "harness you pick in **Build** drives the *same* browser"),
    # The card claimed a folder only a source checkout has is part of the
    # install the user runs.
    (CARD, "from your Iron Jarvis install"),
]


@pytest.mark.parametrize(
    ("path", "claim"),
    OVERSOLD,
    ids=[f"{p.name}::{c[:34]}" for p, c in OVERSOLD],
)
def test_no_surface_promises_an_ability_this_version_lacks(path: Path, claim: str):
    assert claim.lower() not in _text(path).lower(), (
        f"{path.name} still tells the user {claim!r}, which this version cannot do "
        "-- BrowserRuntime raises NotImplementedError for it. Say what this version "
        "does and name the version the rest arrives in. If the ability just shipped, "
        "move this entry into the required-phrase half below rather than deleting it."
    )


# --------------------------------------------------------------------------- #
#  What every surface must say instead                                        #
# --------------------------------------------------------------------------- #


def test_the_handbook_table_describes_this_version_and_dates_the_rest():
    section = _handbook_browser_section()
    # The Read only row is looking, which as of v1.236.0 includes the page itself.
    assert "list your open tabs" in section
    assert "name the tab you are looking at" in section
    assert "read the text of a page" in section
    # Interactive grants nothing extra yet, and the Handbook says so.
    assert "Nothing more than **Read only** yet" in section
    # Acting is still named as later, with the version that brings it.
    assert "navigating and downloads arrive in **v1.237.0**" in section


def test_the_handbook_says_what_reading_a_page_does_not_cover():
    """The three limits a user would otherwise discover by being misled.

    Each is real and each is invisible unless stated: a bounded snapshot can look like
    a short page, a cross-origin frame is absent rather than empty, and an element id
    that changes between readings looks like a bug until you know it describes one
    reading and not the page forever.
    """
    section = _handbook_browser_section()
    assert "bounded" in section
    assert "another site" in section, "the cross-origin frame limit is not stated"
    assert "the ids change" in section


def test_the_handbook_keeps_the_privacy_commitments_as_commitments():
    """The password-stripping and injection-flagging paragraph is a real DESIGN
    decision, so it stays -- dated, not deleted."""
    section = _handbook_browser_section()
    # v1.236.0: two of the three are LIVE now and say so; the third still names its
    # version. A promise in force must not keep a future date (that understates what
    # the user already has), and one that is not in force must never read as live.
    for promise, marker in (
        ("Page content is untrusted data, always", "(live)"),
        ("Passwords are never read", "(live)"),
        ("Changing a page asks first", "v1.237.0"),
    ):
        idx = section.find(promise)
        assert idx != -1, f"the Handbook dropped the commitment {promise!r}"
        assert marker in section[idx : idx + len(promise) + 40], (
            f"{promise!r} is not marked {marker!r} -- a commitment must say whether it "
            "is in force now or which version brings it"
        )


def test_the_handbook_does_not_claim_a_jarvis_browser_notice_no_page_shows():
    """'...and the page says so rather than pretending' described a row that no
    dashboard component renders; 'Jarvis browser' appears in no surface at all."""
    assert "the page says so rather than pretending" not in _handbook_browser_section()


def test_the_tab_wording_is_the_same_on_the_page_the_tile_and_the_glossary():
    for path in (BROWSER_PAGE, HELP_PAGE):
        text = _text(path)
        assert "see your open tabs" in text, f"{path.name} lost the tab-list wording"
        assert "read the page" in text, (
            f"{path.name} does not mention reading a page, which v1.236.0 added -- "
            "understating a shipped ability is its own kind of wrong copy"
        )


def test_the_nav_blurb_describes_the_tab_list():
    assert "Read the tabs and pages open in your own browser" in _text(NAV)


def test_the_card_hints_date_the_abilities_they_describe():
    src = _text(CARD)
    assert "read the text of the page you are looking at" in src
    assert "It cannot click or type." in src
    assert "Nothing more than Read only in this version" in src
    assert "Clicking, typing and navigating arrive in v1.237.0" in src


def test_the_readme_row_sells_the_tab_list_and_dates_the_rest():
    row = next(
        line
        for line in README.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()
        if "Your own browser, as a capability" in line
    )
    assert "ask about the page in front of you" in row
    assert "v1.237.0" in row and "v1.238.0" in row


# --------------------------------------------------------------------------- #
#  The install steps have to actually work                                     #
# --------------------------------------------------------------------------- #


def test_the_card_gives_the_build_step_the_readme_gives():
    """manifest.json points at dist/background.js and extensions/chrome/.gitignore
    ignores dist/, so the four steps without the build produce Chrome's "Could not
    load background script" on any fresh checkout. The add-on README has always
    carried this step; the card's mirror dropped it."""
    card = _text(CARD)
    assert "pnpm install &amp;&amp; pnpm run check" in card, (
        "the card's install steps have no build step -- Load unpacked fails on a "
        "fresh checkout because dist/ is gitignored"
    )
    assert "pnpm install && pnpm run check" in _text(ADDON_README)
    # And it names the folder Load unpacked wants, not the build output.
    assert "manifest.json" in card


def test_the_card_names_the_folder_as_a_source_checkout_and_dates_the_package():
    card = _text(CARD)
    assert "source checkout" in card
    assert "The installer ships the add-on inside the app from v1.239.0" in card


# --------------------------------------------------------------------------- #
#  The word "extension" means an MCP server in this product                    #
# --------------------------------------------------------------------------- #


def test_no_remedy_string_calls_the_add_on_an_extension():
    """A remedy reaches the user, so the copy rule applies to every one of them.

    `POST /browser/test` puts a remedy straight into `detail`, which the card
    renders verbatim, and a tool returns one to the model as its error. (Ship 1's
    fix wave narrowed `last_error` to real faults rather than every refusal, so
    that field is no longer the main path — this pin does not depend on which
    field carries the string, only on the strings themselves.)

    `chrome://extensions` is Chrome's own name for Chrome's own page and is the
    one permitted use of the word."""
    for code, text in REMEDIES.items():
        scrubbed = text.replace("chrome://extensions", "")
        assert not re.search(r"\bextension\b", scrubbed, re.I), (
            f"REMEDIES[{code}] calls it an extension: {text!r}. In this product "
            '"extension" already means an MCP server -- say "browser add-on".'
        )
