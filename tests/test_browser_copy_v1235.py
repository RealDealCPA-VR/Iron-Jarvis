"""Ship 1's user-facing copy describes Ship 1 (v1.235.0, Browser capability, S1).

The implementation of Ship 1 pairs a browser, reports connection state, lists
tabs, names the active tab, and runs a read-only Test round trip. Three tools:
`browser_get_status`, `browser_list_tabs`, `browser_get_active_tab`. Reading a
page, screenshots, clicking, typing, scrolling, navigating, downloads, password
scrubbing and injection flagging DO NOT EXIST -- `BrowserRuntime` raises
`NotImplementedError` for every one of them.

The shipped copy said otherwise: the Handbook's capability table offered "read
the current page, take a screenshot" as a Read only ability, the page subtitle
and the Help tile promised "read the page you are looking at", the card's
Interactive hint said "Jarvis can also click, type and navigate", and the README
promised the Ship 4 harness. The Guide answers out of `docs/HANDBOOK.md`
(`guide/corpus.py`), so an overselling Handbook is a Guide that lies with
confidence -- the failure this repository has been burned by before.

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
OVERSOLD = [
    # The page subtitle, the Help tile and the Help glossary.
    (BROWSER_PAGE, "read the page you are looking at"),
    (HELP_PAGE, "read the page you are looking at"),
    (HELP_PAGE, "so it can read your tabs"),
    # The nav blurb: "drive" is Ship 3.
    (NAV, "read and drive your own browser"),
    # The Handbook capability table.
    (HANDBOOK, "read the current page, take a screenshot"),
    (HANDBOOK, "Also act: click, type, scroll, navigate"),
    # The card's access hints.
    (CARD, "Jarvis can read tabs and pages"),
    (CARD, "Jarvis can also click, type and navigate"),
    # The README highlights row: reading the page, and the Ship 4 harness.
    (README, "ask about the page in front of you"),
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
        f"{path.name} still tells the user {claim!r}. Nothing in v1.235.0 does "
        "that -- BrowserRuntime raises NotImplementedError. Say what this version "
        "does and name the version the rest arrives in."
    )


# --------------------------------------------------------------------------- #
#  What every surface must say instead                                        #
# --------------------------------------------------------------------------- #


def test_the_handbook_table_describes_this_version_and_dates_the_rest():
    section = _handbook_browser_section()
    # The Read only row is the tab list, and only that.
    assert "list your open tabs" in section
    assert "name the tab you are looking at" in section
    # Interactive grants nothing extra yet, and the Handbook says so.
    assert "Nothing more than **Read only** yet" in section
    # Later abilities are named as later, with the version that brings them.
    assert "Reading the text of a page and taking a screenshot arrive in **v1.236.0**" in section
    assert "navigating and downloads arrive in **v1.237.0**" in section


def test_the_handbook_keeps_the_privacy_commitments_as_commitments():
    """The password-stripping and injection-flagging paragraph is a real DESIGN
    decision, so it stays -- dated, not deleted."""
    section = _handbook_browser_section()
    assert "design commitments" in section
    for promise, version in (
        ("Page content is untrusted data, always", "v1.236.0"),
        ("Passwords are never read", "v1.236.0"),
        ("Changing a page asks first", "v1.237.0"),
    ):
        idx = section.find(promise)
        assert idx != -1, f"the Handbook dropped the commitment {promise!r}"
        # The version that brings it is named in the same breath, so the line
        # cannot read as a live guarantee.
        assert version in section[idx : idx + len(promise) + 40], (
            f"{promise!r} does not name the version it arrives in -- as written it "
            "reads as something v1.235.0 already guarantees"
        )


def test_the_handbook_does_not_claim_a_jarvis_browser_notice_no_page_shows():
    """'...and the page says so rather than pretending' described a row that no
    dashboard component renders; 'Jarvis browser' appears in no surface at all."""
    assert "the page says so rather than pretending" not in _handbook_browser_section()


def test_the_tab_wording_is_the_same_on_the_page_the_tile_and_the_glossary():
    for path in (BROWSER_PAGE, HELP_PAGE):
        assert "see your open tabs" in _text(path), (
            f"{path.name} should say 'see your open tabs' -- the one thing this "
            "version can actually do"
        )


def test_the_nav_blurb_describes_the_tab_list():
    assert "See the tabs open in your own browser" in _text(NAV)


def test_the_card_hints_date_the_abilities_they_describe():
    src = _text(CARD)
    assert "Jarvis can see your open tabs and which one you are looking at" in src
    assert "Reading a page's text arrives in v1.236.0" in src
    assert "Nothing more than Read only in this version" in src
    assert "Clicking, typing and navigating arrive in v1.237.0" in src


def test_the_readme_row_sells_the_tab_list_and_dates_the_rest():
    row = next(
        line
        for line in README.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()
        if "Your own browser, as a capability" in line
    )
    assert "see your open tabs" in row
    assert "v1.236.0" in row and "v1.237.0" in row


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
