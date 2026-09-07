"""The Browser copy describes the version that is actually installed.

Named for v1.235.0, the ship whose review found the defect, and MAINTAINED every ship
after it -- the file is the moving boundary between what the browser can do and what it
is merely going to do, so it is edited in the same change as the ability, never after.

AT v1.238.0 the browser pairs, reports connection state, lists tabs, names the active
tab, reads the text of a page, takes a screenshot, ACTS ON THE PAGE (click, type, press
a key, scroll, activate, open and close a tab, navigate), AND IS REACHABLE FROM AN
OUTSIDE HARNESS: a coding CLI launched in a Build pane whose Browser capability is
ticked drives the same browser, through the same gates. Fourteen tools, two callers. A
completed download is reported with its real path. What still DOES NOT EXIST is the
add-on bundled into the installer (v1.239.0).

THE BOUNDARY MOVED IN THIS FILE IN THE SAME CHANGE AS THE ABILITY, which is the only
discipline that works. At v1.237.0 three acting claims moved; at v1.238.0 the harness
claim moved -- out of the forbidden list, into the required half below -- and the
README row that DATED the harness as future had to stop, because it was dating the
version it was being read on. A claim deleted from BOTH halves is a claim nothing
checks, so nothing was deleted -- it moved.

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
COMPUTER_USE = REPO / "docs" / "COMPUTER-USE.md"
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
# UPDATED AT v1.238.0. THE OUTWARD HARNESS SHIPPED, so its README entry left this list
# and became a REQUIRED phrase below -- that direction of travel is the whole point of
# the file. What stays is what is still not true:
#
#   the add-on inside the installer -> v1.239.0
#
# When that ships, move its entries the same way: delete here, assert there.
# A claim deleted from BOTH halves is a claim nothing checks any more.
OVERSOLD = [
    # The card claimed a folder only a source checkout has is part of the
    # install the user runs.
    (CARD, "from your Iron Jarvis install"),
    # v1.237.0: the two sentences docs/COMPUTER-USE.md kept while a Ship 3
    # paragraph was inserted ABOVE them, so the file contradicted itself. The
    # second is the file's own one-sentence answer to "can Jarvis click in my
    # own Chrome?", which is the sentence the Guide is most likely to retrieve
    # verbatim -- and it said no on the version where the answer is yes.
    (COMPUTER_USE, "may only look at it"),
    (COMPUTER_USE, "the half that actually"),
]


@pytest.mark.parametrize(
    ("path", "claim"),
    OVERSOLD,
    ids=[f"{p.name}::{c[:34]}" for p, c in OVERSOLD],
)
def test_no_surface_promises_an_ability_this_version_lacks(path: Path, claim: str):
    assert claim.lower() not in _text(path).lower(), (
        f"{path.name} still tells the user {claim!r}, which is not true of this "
        "version. Say what this version does and name the version the rest arrives "
        "in. If the ability just shipped, move this entry into the required-phrase "
        "half below rather than deleting it."
    )


# --------------------------------------------------------------------------- #
#  What every surface must say instead                                        #
# --------------------------------------------------------------------------- #


def test_the_handbook_table_describes_this_version_and_dates_the_rest():
    """MOVED AT v1.237.0, both halves of the same edit.

    "Also act: click, type, scroll, navigate" was FORBIDDEN here until this ship and
    is now REQUIRED, because the Interactive row is the one place a user decides
    whether to grant acting -- and it read "Nothing more than Read only yet" on the
    version where Interactive grants eight tools that touch their logged-in browser.
    The two sentences that dated acting as future are gone from the copy, so they are
    gone from the assertions; what replaces them is the ability, stated.
    """
    section = _handbook_browser_section()
    # The Read only row is looking, which as of v1.236.0 includes the page itself.
    assert "list your open tabs" in section
    assert "name the tab you are looking at" in section
    assert "read the text of a page" in section
    # The Interactive row now GRANTS something, and says what.
    assert "Also act: click, type, scroll, navigate" in section, (
        "the Handbook's Interactive row does not say what Interactive grants -- it "
        "is the row a user reads before turning acting on"
    )
    assert "Nothing more than **Read only** yet" not in section, (
        "the Interactive row still says it grants nothing, on the version where it "
        "grants eight tools that act in the user's logged-in browser"
    )
    assert "navigating and downloads arrive in **v1.237.0**" not in section, (
        "the Handbook still dates acting as future on the version that ships it"
    )


def test_the_handbook_says_what_gets_asked_before_an_action():
    """The plan's named Ship 3 docs deliverable, asserted as content.

    Both layers, because either alone misleads: "everything asks by default" without
    the deny floor understates what protects a user who later sets click to allow,
    and the destructive vocabulary alone reads as though an ordinary click proceeds
    unattended. The third clause is the one the risk lane closed this ship: a target
    Jarvis cannot identify stops rather than proceeding.
    """
    section = _handbook_browser_section()
    assert "deny floor" in section, "the Handbook does not name the floor"
    assert "destructive or transactional" in section
    for needle, why in (
        ("ask", "the Handbook never says an action is asked about at all"),
        ("could not identify", "an unidentifiable target is not named as an ask"),
        ("REDACTED", "the Handbook does not say typed text is redacted"),
    ):
        assert needle in section, why


def test_the_handbook_states_the_download_limitation_in_plain_words():
    """Plan 10.3 asks for this in the docs, in these words, because the refusal text
    is otherwise the only place the fact exists: a user who says "put that statement
    in my project" meets `rename_file` refusing an out-of-workspace source with no
    idea why. Jarvis can read a completed download and copy it into a project; it
    cannot change where Chrome saves it."""
    section = _handbook_browser_section()
    assert "Downloads" in section, "the Handbook Browser section never mentions downloads"
    assert "copy it into a project" in section
    assert "cannot" in section and "where Chrome saves" in section, (
        "the Handbook does not state the limitation -- that Jarvis cannot change or "
        "redirect where Chrome saves a download"
    )


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
    # v1.237.0: ALL THREE are live now and say so. "Changing a page asks first" was
    # the design commitment fixed before the code that needed it existed; the code
    # exists, so the date comes off. A promise in force must not keep a future date
    # (that understates what the user already has), and one that is not in force must
    # never read as live.
    for promise, marker in (
        ("Page content is untrusted data, always", "(live)"),
        ("Passwords are never read", "(live)"),
        ("Changing a page asks first", "(live)"),
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


def test_the_nav_blurb_describes_reading_AND_driving():
    """MOVED AT v1.237.0. "read and drive your own browser" was forbidden here on
    every version that could not drive; it is required now, because the nav blurb is
    where a user learns the capability exists at all."""
    assert "read and drive your own browser" in _text(NAV).lower(), (
        "dashboard/lib/nav.ts still describes the Browser page as reading only. "
        'The owed sentence is "Read and drive your own browser" -- acting shipped '
        "in v1.237.0, and the nav blurb is the first place a user reads what the "
        "page is for."
    )


def test_the_card_hints_describe_the_abilities_this_version_has():
    """MOVED AT v1.237.0, and this is the surface the user is actually standing on.

    The card renders next to the access selector: it is read at the moment someone
    decides between Read only and Interactive. Until this ship it told them
    Interactive granted nothing and that Jarvis "cannot click or type" -- which was
    true, and became the reason the copy stayed that way, because the required half
    of this file was holding it there.
    """
    src = _text(CARD)
    owed = (
        ("read the text of the page you are looking at", "the Read only hint"),
        (
            "Jarvis can also click, type and navigate",
            "the Interactive hint -- acting shipped in v1.237.0",
        ),
    )
    for needle, what in owed:
        assert needle in src, (
            f"YourBrowserCard.tsx is missing {what}: {needle!r}. The card is read at "
            "the moment the user chooses an access level, so it must describe the "
            "level they are choosing."
        )
    for stale in (
        "It cannot click or type.",
        "Nothing more than Read only in this version",
        "Clicking, typing and navigating arrive in v1.237.0",
    ):
        assert stale not in src, (
            f"YourBrowserCard.tsx still says {stale!r} on the version that ships "
            "acting -- the user is told the feature they just enabled does not exist"
        )


def _readme_browser_row() -> str:
    return next(
        line
        for line in README.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()
        if "Your own browser, as a capability" in line
    )


def test_the_readme_row_sells_the_tab_list_and_dates_the_rest():
    row = _readme_browser_row()
    assert "ask about the page in front of you" in row
    # v1.238.0: the only thing left to date is the add-on inside the installer.
    # Requiring "v1.238.0" here (as this test did until this ship) forced the row to
    # keep saying the harness "arrives in v1.238.0" ON v1.238.0 -- a required phrase
    # holding a lie in place, which is the failure mode this file exists to prevent.
    assert "v1.239.0" in row, (
        "the README browser row no longer dates anything as future. The one thing "
        "still ahead is the add-on inside the installer: say it arrives in v1.239.0."
    )
    assert "v1.238.0" not in row, (
        "the README still dates something as arriving in v1.238.0 -- which is the "
        "version the reader is running"
    )
    assert "Clicking and typing arrive in v1.237.0" not in row, (
        "the README still dates clicking and typing as future on the version that "
        "ships them"
    )


def test_the_readme_row_says_a_build_harness_drives_the_same_browser():
    """MOVED AT v1.238.0, both halves of the same edit.

    This exact clause was FORBIDDEN in `OVERSOLD` on every version that could not
    do it, and the row instead closed with "An external harness driving the same
    browser arrives in v1.238.0" -- a sentence that, unchanged, tells a user
    running v1.238.0 that the feature they are running is still to come. Ship 4
    delivers it, so the clause becomes owed.
    """
    row = _readme_browser_row()
    assert "harness you pick in **Build** drives the *same* browser" in row, (
        "the README browser row does not say the harness ships. The owed clause is "
        'exactly: "harness you pick in **Build** drives the *same* browser" -- it '
        "was on the forbidden list until this version and moved here in the same "
        "change as the ability. Replace the closing clause 'An external harness "
        "driving the same browser arrives in v1.238.0'."
    )


def test_the_handbook_tells_a_user_how_a_build_harness_gets_the_browser():
    """The Handbook is the Guide's corpus, so the harness path has to be IN it.

    Before this ship the Browser section did not mention a harness at all except
    to date one as future, and "Capabilities" appeared nowhere in the file -- so a
    user asking the Guide "can Claude Code use my browser through Jarvis?" was
    told, with confidence, that it arrives in a later version.
    """
    section = _handbook_browser_section()
    assert "Capabilities" in section, (
        "the Handbook never names the Capabilities list a user has to tick"
    )
    assert "Launch" in section, "the Handbook does not say where the harness starts"
    for needle, why in (
        ("only Browser is enforced", "the four boxes that gate nothing are not owned"),
        ("no** credential", "the config file's credential-free promise is not stated"),
        ("closing the pane", "the credential's lifetime is not stated"),
    ):
        assert needle in section, why
    assert "driving this same browser arrives" not in section, (
        "the Handbook still dates the harness as future on the version that ships it"
    )


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
