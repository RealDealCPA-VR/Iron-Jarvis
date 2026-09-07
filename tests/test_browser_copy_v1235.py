"""The Browser copy describes the version that is actually installed.

Named for v1.235.0, the ship whose review found the defect, and MAINTAINED every ship
after it -- the file is the moving boundary between what the browser can do and what it
is merely going to do, so it is edited in the same change as the ability, never after.

AT v1.239.0 the browser pairs, reports connection state, lists tabs, names the active
tab, reads the text of a page, takes a screenshot, ACTS ON THE PAGE (click, type, press
a key, scroll, activate, open and close a tab, navigate), IS REACHABLE FROM AN OUTSIDE
HARNESS (a coding CLI launched in a Build pane whose Browser capability is ticked
drives the same browser, through the same gates), AND SHIPS INSIDE THE INSTALLER: the
add-on is a folder in the installed app, so Load unpacked no longer needs a source
checkout. Fourteen tools, two callers. A completed download is reported with its real
path.

NOTHING IN THE FIVE-SHIP PLAN IS STILL AHEAD. The add-on inside the installer was the
last dated claim, so at this ship the file's forbidden half stops holding a future
version and starts holding the OPPOSITE risk: a surface still telling a user to build
from a checkout, or still dating v1.239.0 as something to come, on the version they
are reading it on. Every date that named v1.239.0 had to move in the same change as
the packaging, and `DATED_AS_FUTURE` below is the pin that says so.

THE BOUNDARY MOVED IN THIS FILE IN THE SAME CHANGE AS THE ABILITY, which is the only
discipline that works. At v1.237.0 three acting claims moved; at v1.238.0 the harness
claim moved; at v1.239.0 the card's "from your Iron Jarvis install" moved -- out of the
forbidden list, into the required half below. A claim deleted from BOTH halves is a
claim nothing checks, so nothing was deleted -- it moved.

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
BROWSER_DOC = REPO / "docs" / "BROWSER.md"
TODO = REPO / "docs" / "TODO.md"


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
# UPDATED AT v1.239.0. THE ADD-ON NOW SHIPS INSIDE THE INSTALLER, so the card's
# "from your Iron Jarvis install" left this list and became a REQUIRED phrase below --
# the same direction of travel as the harness at v1.238.0 and acting at v1.237.0.
# NOTHING IN THE FIVE-SHIP PLAN IS STILL AHEAD, so nothing is dated here any more; what
# remains is copy that contradicts an ability the app HAS.
OVERSOLD = [
    # v1.237.0: the two sentences docs/COMPUTER-USE.md kept while a Ship 3
    # paragraph was inserted ABOVE them, so the file contradicted itself. The
    # second is the file's own one-sentence answer to "can Jarvis click in my
    # own Chrome?", which is the sentence the Guide is most likely to retrieve
    # verbatim -- and it said no on the version where the answer is yes.
    (COMPUTER_USE, "may only look at it"),
    (COMPUTER_USE, "the half that actually"),
    # v1.239.0 review, S1. Both guides told the user the Browser page "names the
    # exact folder on this machine and gives you a button to copy it". The card
    # names and copies the bare folder NAME ("browser-addon"); Chrome's Load
    # unpacked wants a directory, so the sentence sent a packaged user to a picker
    # with eight characters on the clipboard and nowhere to put them. The guides
    # now name the folder's PLACE as well (asserted below) -- and neither may go
    # back to promising a copyable machine path the product does not carry.
    (BROWSER_DOC, "names the exact folder on this machine"),
    (HANDBOOK, "names the exact folder on this machine"),
    # v1.239.0 review, S2. No iframe content is read AT ALL: tabs.ts injects the
    # content script with no `allFrames` and IFRAME is in snapshot.ts SKIP_TAGS.
    # "read where they are reachable" told a user a same-origin embedded form was
    # in the snapshot, so the model answers confidently about a page it half read.
    (BROWSER_DOC, "shadow DOM are read where they are reachable"),
    (HANDBOOK, "shadow DOM are read where reachable"),
    # v1.239.0 review, S3. The deny floor drops an `allow` from an AGENT
    # DEFINITION override (tools/permissions.py) and from a capability proposal.
    # The base permissions dict in core/config.py is not floor-guarded and
    # PermissionPolicy.mode_for honours a base "allow", so the absolute reading of
    # this sentence is false and a reader trusting it would set browser_click to
    # allow believing the app would refuse the setting.
    (BROWSER_DOC, "can never be switched all the way off"),
    # docs/TODO.md is where the next doc sentence gets written from, and its
    # Phase 2 entry carried the same optimistic wording that produced limitation
    # 9. Correcting only the guides leaves the source of the error in place.
    (TODO, "The MVP reads what it can reach"),
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
#  Nothing may still be dated as arriving in the version being read           #
# --------------------------------------------------------------------------- #

# NEW AT v1.239.0, and it is the mirror image of OVERSOLD. Until this ship four
# surfaces told the user, correctly, that the add-on would arrive inside the installer
# in v1.239.0. Left alone, each of those sentences becomes a lie the instant the user
# is READING v1.239.0 -- the exact failure the README row hit at v1.238.0, where a
# required phrase held "arrives in v1.238.0" in place on v1.238.0.
#
# Each entry is a sentence that had to go in the same change as the packaging. They are
# whole clauses, so a legitimate mention of the version elsewhere ("shipped in
# v1.239.0") is not caught.
DATED_AS_FUTURE = [
    (CARD, "inside the app from v1.239.0"),
    (README, "ships inside the installer in v1.239.0"),
    (README, "until then you load it yourself"),
    (HANDBOOK, "ships inside the installer in **v1.239.0**"),
    (HANDBOOK, "Until then it is loaded from a source checkout"),
    # v1.239.0 review, S1. desktop/package.json's extraResources filter carries
    # "README.md" into resources/browser-addon, so this file is now the document
    # sitting beside manifest.json in a user's installation -- and it was frozen at
    # Ship 1, denying three abilities the product has had since v1.237.0. A user who
    # follows the card to the folder reads it there.
    (ADDON_README, "What it can do in this version (v1.235.0)"),
    (ADDON_README, "Read-only, and only the outside of a page"),
    (ADDON_README, "arrive in later versions"),
    (ADDON_README, "Later versions report the real absolute path"),
]


@pytest.mark.parametrize(
    ("path", "sentence"),
    DATED_AS_FUTURE,
    ids=[f"{p.name}::{c[:34]}" for p, c in DATED_AS_FUTURE],
)
def test_no_surface_dates_the_shipped_add_on_as_still_to_come(path: Path, sentence: str):
    assert sentence.lower() not in _text(path).lower(), (
        f"{path.name} still says {sentence!r}. The add-on ships inside the installer "
        "in the version the reader is running, so this sentence tells them a feature "
        "they already have is still to come -- and it sends them to build from a "
        "source checkout they do not have. Say what the app does now."
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


def test_the_readme_row_sells_what_ships_and_dates_nothing():
    """MOVED AT v1.239.0, and this is the last move the file has to make.

    Until this ship the row closed "The add-on ships inside the installer in v1.239.0;
    until then you load it yourself" -- true when written, and a sentence that tells a
    v1.239.0 reader to go and find a source checkout. The add-on is IN the installer
    now, so the closing clause states that instead, and NOTHING in this row may still
    be dated: there is no sixth ship.
    """
    row = _readme_browser_row()
    assert "ask about the page in front of you" in row
    assert "add-on" in row and "installer" in row, (
        "the README browser row no longer says where the add-on comes from. It ships "
        "inside the installer as of v1.239.0 -- say so, because 'load a browser "
        "add-on' otherwise reads as something the user must go and obtain."
    )
    for stale in ("ships inside the installer in v1.239.0", "until then you load it yourself"):
        assert stale.lower() not in row.lower(), (
            f"the README browser row still says {stale!r} on the version that ships "
            "it -- a required phrase holding a lie in place is the failure mode this "
            "file exists to prevent"
        )
    for version in ("v1.236.0", "v1.237.0", "v1.238.0", "v1.239.0"):
        assert f"arrive in {version}" not in row and f"arrives in {version}" not in row, (
            f"the README browser row still dates something as arriving in {version}. "
            "All five ships have landed; nothing in this row is ahead of the reader."
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


def test_the_card_names_the_folder_inside_the_installed_app():
    """MOVED AT v1.239.0, both halves of the same edit.

    "from your Iron Jarvis install" was the FIRST entry of `OVERSOLD`: the card said
    the folder was part of the install while it existed only in a source checkout.
    Ship 5 makes it true, so the phrase moves here and becomes owed -- the card is the
    surface a user stands on with Chrome's Load unpacked dialog already open, and
    "build it from a checkout first" is a dead end for someone running the installer.

    The build step is NOT deleted: a source checkout still needs it, and the card
    still carries it for that reader (asserted by the test above). What must be gone
    is the sentence that dated the packaged folder as a later version.
    """
    card = _text(CARD)
    assert "from your Iron Jarvis install" in card, (
        "YourBrowserCard.tsx does not tell a packaged user the add-on folder is part "
        'of their install. The owed phrase is "from your Iron Jarvis install" -- it '
        "was on the forbidden list until this version and moves here in the same "
        "change as the packaging."
    )
    assert "inside the app from v1.239.0" not in card, (
        "the card still dates the packaged add-on as arriving in v1.239.0, which is "
        "the version the reader is running"
    )


# --------------------------------------------------------------------------- #
#  Ship 5: the add-on ships, and the feature finally has its own guide         #
# --------------------------------------------------------------------------- #


def test_the_handbook_says_the_add_on_comes_with_the_app():
    """The Guide answers out of the Handbook, so "where do I get the add-on?" is
    answered there or it is answered wrongly.

    Until this ship the Handbook said the add-on "ships inside the installer in
    v1.239.0. Until then it is loaded from a source checkout" -- read on v1.239.0
    that is an instruction to go and clone the repository. The packaged path is the
    one nearly every reader is on, so it is the one the Handbook states.
    """
    section = _handbook_browser_section()
    assert "installer ships it" in section or "installer ships the add-on" in section, (
        "the Handbook's Browser section never says the installer ships the add-on. "
        "That is Ship 5's user-visible change: the folder is inside the installed "
        "app and the card names it."
    )
    assert "Load unpacked" in section, (
        "the Handbook does not name the Chrome step the folder is for"
    )


def test_the_handbook_names_the_supported_browsers():
    """Limitation 2 of the plan's §20, and the first question a Firefox user asks."""
    section = _handbook_browser_section()
    assert "Supported browsers" in section, (
        "the Handbook's Browser section never says which browsers work"
    )
    assert "Manifest V3" in section
    assert "Firefox and Safari are not supported" in section, (
        "the Handbook does not say which browsers do NOT work, which is the half a "
        "user needs before they go looking for a Firefox add-on that is not coming"
    )


def test_the_handbook_states_the_security_model_and_the_limits():
    """Ship 5's named Handbook deliverable: the security model and the MVP limits.

    Both are stated rather than discovered. The credential separation is the one
    thing a user has to trust, and each limit below is a place where silence reads
    as a bug report rather than a boundary.
    """
    section = _handbook_browser_section()
    for needle, why in (
        ("five separate credentials", "the credential separation is not stated"),
        ("none of them substitutes", "nothing says the credentials do not interchange"),
        ("loopback", "the Handbook does not say who can reach the daemon"),
        ("one paired browser is connected", "the one-connection rule is not stated"),
    ):
        assert needle in section, why
    for needle, why in (
        ("Chrome and Edge only", "limit 2, supported browsers"),
        ("unpacked", "limit 3, load unpacked"),
        ("all-or-nothing", "limit 4, site access is granted whole"),
        ("save-a-copy", "limits 5 and 6, downloads and the workspace"),
        ("No clicking by coordinates", "limit 7"),
        ("no drag and drop", "limit 8"),
        ("shadow DOM", "limit 9"),
        ("one reading", "limit 10, element ids"),
        ("visible part of the tab", "limit 11, screenshots"),
        ("credential dies with the daemon", "limit 13, pane tokens"),
        ("only the Browser capability is enforced", "limit 14"),
        ("best effort", "limit 15, harness isolation"),
        ("no Jarvis browser yet", "limit 16"),
    ):
        assert needle.lower() in section.lower(), (
            f"the Handbook's Browser section does not state {why}: {needle!r}. Ship 5 "
            "owes the MVP limitations in plain words -- a user meets each of these "
            "either in the docs or in the middle of something."
        )


def test_the_handbook_points_at_the_browser_guide():
    section = _handbook_browser_section()
    assert "docs/BROWSER.md" in section, (
        "the Handbook does not point at the full Browser guide, so a user (and the "
        "Guide, which retrieves across both) never learns it exists"
    )


def test_the_computer_use_doc_points_at_the_browser_guide():
    """Ship 5's cross-reference. Both files are in the Guide's corpus and both open
    by telling the two browsers apart, so the one that is NOT about your browser has
    to say where the one that is lives."""
    doc = _text(COMPUTER_USE)
    assert "docs/BROWSER.md" in doc, (
        "docs/COMPUTER-USE.md does not cross-reference the Browser guide. A reader "
        "who arrives here asking about their own Chrome is one link from the right "
        "file, or is left reading the wrong feature"
    )


# The implementation plan's §20 limitations, each pinned by a phrase a user would
# search for. This is the one place they are all required to exist: a limit that is
# real, invisible and undocumented is indistinguishable from a bug, and every one of
# these was written down BEFORE a user could hit it.
#
# SEVENTEEN AT v1.239.0, not the plan's sixteen. The review found a real, deliberate
# boundary documented only in maintainer-facing material -- Manifest V3 unloads an idle
# add-on, so a long-quiet bridge reads as "Paired -- not running" with the browser open
# in front of the user. `extensions/chrome/src/bridge/socket.ts` records that a timer
# keepalive was rejected because it would need the `alarms` permission, so this is a
# choice, not a bug, and the user meets it either here or by pressing Forget browser on
# a bridge that was about to reconnect on its own.
MVP_LIMITS = [
    ("one connection", "1 -- one browser at a time, newer replaces older"),
    ("Chrome and Edge only", "2 -- supported browsers"),
    ("Loaded unpacked", "3 -- no Web Store listing"),
    ("Site access is all or nothing", "4 -- granted whole, narrowed in Chrome"),
    ("Downloads land where Chrome puts them", "5 -- Chrome owns the destination"),
    ("Writing files stays inside the workspace", "6 -- the copy goes through save-a-copy"),
    ("No clicking by coordinates", "7 -- semantics, not pixels"),
    ("no drag and drop", "8 -- upload, select, hover, drag"),
    # CORRECTED at v1.239.0. The old needle passed on a sentence that said frames
    # "are read where they are reachable", which is false of EVERY frame: the
    # content script is injected with no `allFrames` and IFRAME is a SKIP_TAG. The
    # forbidden half of that move is in OVERSOLD above.
    ("Nothing inside an embedded frame is read", "9 -- no frame content at all"),
    ("shadow DOM", "9b -- an open root is walked, a closed one cannot be counted"),
    ("belong to one reading", "10 -- element ids are not durable"),
    ("Screenshots capture the visible part", "11 -- no full-page stitching"),
    ("Page content is always untrusted", "12 -- a flagged page constrains, never ends"),
    ("credential dies with the daemon", "13 -- relaunch the harness after a restart"),
    ("Only the Browser capability is enforced", "14 -- the other four are display only"),
    ("best effort", "15 -- harness isolation is reported honestly"),
    ("There is no Jarvis browser yet", "16 -- the managed browser is the next phase"),
    ("Chrome unloads an idle add-on", "17 -- Manifest V3 evicts a quiet service worker"),
]


@pytest.mark.parametrize(
    ("needle", "limit"),
    MVP_LIMITS,
    ids=[limit.split(" ", 1)[0] for _n, limit in MVP_LIMITS],
)
def test_the_browser_guide_states_every_mvp_limitation(needle: str, limit: str):
    doc = _text(BROWSER_DOC)
    assert needle.lower() in doc.lower(), (
        f"docs/BROWSER.md does not state MVP limitation {limit}. All sixteen belong "
        "in this file in plain words (implementation plan §20): the user meets them "
        "either here or in the middle of their work, and an undocumented boundary is "
        "indistinguishable from a defect."
    )


# What §36 of the decision record requires the user-facing documentation to explain.
BROWSER_DOC_TOPICS = [
    ("Jarvis browser", "your browser versus the Jarvis-owned one"),
    ("Load unpacked", "how to load the add-on"),
    ("Pair", "pairing"),
    ("Read only", "the access modes"),
    ("Interactive", "the access modes"),
    ("Capabilities", "the Build pane's Browser capability"),
    ("security model", "the security model"),
    ("Supported browsers", "supported browsers"),
    ("Known limits", "the known MVP limitations"),
    ("harness", "how an external harness receives Jarvis capabilities"),
    ("Phase 2", "the roadmap, and that Phase 3 asks for its own consent"),
]


@pytest.mark.parametrize(
    ("needle", "topic"),
    BROWSER_DOC_TOPICS,
    ids=[t.replace(" ", "-")[:40] for _n, t in BROWSER_DOC_TOPICS],
)
def test_the_browser_guide_covers_every_required_topic(needle: str, topic: str):
    assert needle.lower() in _text(BROWSER_DOC).lower(), (
        f"docs/BROWSER.md does not cover {topic} (looked for {needle!r}). The "
        "decision record's §36 lists what user-facing documentation must explain, "
        "and this is the file that carries it."
    )


def test_the_browser_guide_is_bundled_for_the_guide_and_the_plans_are_not():
    """A doc the Guide cannot read is a doc that does not exist for most users.

    Registration in ``BUNDLED_DOCS`` is what puts the file into the frozen build
    (``packaging/ironjarvis.spec`` imports this list rather than globbing docs/) and
    what gives ``doctor.check_guide_docs`` something to miss. The two planning files
    stay OUT on purpose: they are maintainer material, and the spec's own comment
    forbids shipping them.
    """
    from iron_jarvis.guide.corpus import _DOC_PRIOR, BUNDLED_DOCS

    entry = next((e for e in BUNDLED_DOCS if e[1] == "docs/BROWSER.md"), None)
    assert entry is not None, (
        "docs/BROWSER.md is not in guide/corpus.BUNDLED_DOCS, so it is not in the "
        "frozen build and the Guide answers 'not covered' about the browser"
    )
    assert entry[0] == "browser" and entry[2] == "Your browser", entry
    assert _DOC_PRIOR.get("browser") == 1.15, (
        "docs/BROWSER.md has no doc prior matching the other feature guides "
        "(reflex, computer-use, local-models and recommended-settings all sit at "
        "1.15), so browser questions rank it below the maintainer-facing files"
    )
    bundled = {rel for _s, rel, _t in BUNDLED_DOCS}
    for maintainer in ("docs/BROWSER-PLAN.md", "docs/BROWSER-IMPLEMENTATION-PLAN.md"):
        assert maintainer not in bundled, (
            f"{maintainer} is bundled to users. It is maintainer material -- the "
            "plan and audit files in docs/ are deliberately excluded"
        )


def test_the_guide_retrieves_the_browser_doc_for_a_browser_question():
    """Registration is not reach: the file has to WIN a browser question.

    The Guide's answer is whatever retrieval returns, so a bundled doc that never
    surfaces is the same to the user as a missing one. This drives the real index
    over the real repository files.
    """
    from iron_jarvis.guide.corpus import GuideIndex

    idx = GuideIndex()
    hits = idx.search("how do I load the browser add-on and pair my own Chrome", k=6)
    assert any(section.doc == "browser" for _score, section in hits), (
        "docs/BROWSER.md never surfaces for a question it is the answer to -- "
        f"retrieval returned {[s.doc for _sc, s in hits]}"
    )


def test_the_browser_guide_never_calls_the_add_on_an_extension():
    """The vocabulary rule, applied to the newest user-facing file.

    ``chrome://extensions`` is Chrome's own name for Chrome's own page, and the pane
    capability named **Extensions** is this product's word for an MCP server -- which
    is exactly why the add-on may never borrow it.
    """
    doc = _text(BROWSER_DOC).replace("chrome://extensions", "").lower()
    for forbidden in (
        "browser extension",
        "chrome extension",
        "the extension",
        "an extension",
        "this extension",
    ):
        assert forbidden not in doc, (
            f"docs/BROWSER.md calls it {forbidden!r}. In this product 'extension' "
            'already means an MCP server -- say "browser add-on".'
        )


# --------------------------------------------------------------------------- #
#  v1.239.0 review: the guides have to leave a packaged user somewhere real    #
# --------------------------------------------------------------------------- #

# The default per-user NSIS location. desktop/package.json sets oneClick:false with
# allowToChangeInstallationDirectory:true and no perMachine, so electron-builder's
# default root is %LOCALAPPDATA%\Programs\<productName>, and the add-on is the
# extraResources entry whose `to:` is "browser-addon", written under resources\.
DEFAULT_ADDON_PATH = r"%LOCALAPPDATA%\Programs\Iron Jarvis\resources\browser-addon"


def test_the_default_addon_path_the_guides_print_is_the_one_the_installer_writes():
    """The path in the copy is derived here, not trusted.

    A guide that prints a path is only useful if the path is the one the installer
    actually produces, so this reads desktop/package.json rather than restating it:
    the product name, the extraResources `to:` for the add-on, and the fact that the
    NSIS config is per-user (no `perMachine`), which is what makes %LOCALAPPDATA% the
    right root rather than %PROGRAMFILES%.
    """
    import json

    pkg = json.loads((REPO / "desktop" / "package.json").read_text(encoding="utf-8"))
    build = pkg["build"]
    nsis = build.get("nsis", {})
    assert nsis.get("perMachine") is not True, (
        "the installer became per-machine, so the add-on no longer lives under "
        "%LOCALAPPDATA% and every guide printing DEFAULT_ADDON_PATH is now wrong"
    )
    entry = next(
        e for e in build["extraResources"] if "extensions/chrome" in str(e.get("from"))
    )
    sep = chr(92)
    tail = sep.join(("Programs", build["productName"], "resources", entry["to"]))
    assert DEFAULT_ADDON_PATH.endswith(tail), (
        "the product name or the add-on's extraResources target moved, so the path "
        f"the guides print is stale: package.json resolves to ...{sep}{tail}"
    )


@pytest.mark.parametrize(
    "path", [BROWSER_DOC, HANDBOOK, ADDON_README], ids=lambda p: p.name
)
def test_every_guide_says_where_the_addon_folder_IS_not_only_what_it_is_called(path: Path):
    """S1 of the v1.239.0 review, and the reason Ship 5 has a fix wave.

    Both guides said the Browser page "names the exact folder on this machine and
    gives you a button to copy it". It names and copies the bare string
    "browser-addon" (dashboard/__tests__/browser-test-button-v1239.test.tsx pins
    exactly that), and Chrome's Load unpacked takes a DIRECTORY. A user who ran only
    the installer was sent to a folder picker with a folder name nobody had located
    for them -- and the card's own fallback, "the setup checks on the Overview print
    the full path", is false on a healthy install, where OnboardingWelcome filters
    passing rows out and renders nothing at all.

    So the guides carry the location themselves. This is deliberately NOT a pin on
    the card: whatever the card ends up copying, a document a user reads has to
    resolve on its own. The path is checked against desktop/package.json above.
    """
    text = _text(path)
    assert DEFAULT_ADDON_PATH.lower() in text.lower(), (
        f"{path.name} never tells the reader WHERE the add-on folder is -- only what "
        f"it is called. Chrome's Load unpacked wants a directory. Print "
        f"{DEFAULT_ADDON_PATH!r}, and say that a custom install location moves it."
    )
    assert "chose" in text.lower() or "different location" in text.lower(), (
        f"{path.name} prints the default install path without saying the installer "
        "lets you choose another one -- allowToChangeInstallationDirectory is true, "
        "so for those users the printed path is simply wrong"
    )


def test_the_addon_readme_is_written_for_the_user_who_finds_it_in_their_install():
    """S1 of the v1.239.0 review. This file is now SHIPPED, not maintainer material.

    desktop/package.json's extraResources filter lists "README.md", so from this ship
    the file sits beside manifest.json inside the installed app -- the folder the
    Browser card sends the user to. It was frozen at Ship 1 and told that reader the
    add-on was read-only, that clicking and typing "arrive in later versions", and to
    run `pnpm install` in a folder that ships no package.json. The forbidden half is
    in DATED_AS_FUTURE above; this is what it must say instead.
    """
    text = _text(ADDON_README)
    for needle, why in (
        ("Load unpacked", "the Chrome step the folder exists for"),
        ("chrome://extensions", "where Load unpacked lives"),
        ("Developer mode", "the toggle Load unpacked needs"),
        ("Interactive", "that the add-on can act on a page, not only read it"),
        ("asks you first", "that acting is gated -- the Ship 3 promise"),
        ("real path", "that a completed download's path IS reported, since v1.237.0"),
        ("120", "the Chrome version floor its own manifest declares"),
    ):
        assert needle.lower() in text.lower(), (
            f"extensions/chrome/README.md does not state {why} ({needle!r}). It ships "
            "inside the installer now and is the document a user finds beside "
            "manifest.json, so it is written for them first and for the source tree "
            "second."
        )
    # The build commands do not exist in the packaged folder -- the extraResources
    # filter carries only manifest.json, README.md and dist/ -- so the file must say
    # whose instructions they are rather than handing them to a reader who has no
    # package.json and no scripts/ to run them against.
    build_at = text.find("pnpm install && pnpm run check")
    assert build_at != -1, "the source build step is gone; a fresh checkout needs it"
    load_at = text.lower().find("load unpacked")
    assert -1 < load_at < build_at, (
        "extensions/chrome/README.md puts the source build step before the loading "
        "steps. Nearly every reader of the shipped copy cannot run it, so loading "
        "comes first and the build section names the folder it is for."
    )
    assert "repository" in text.lower(), (
        "the README never says the build half applies to the source repository rather "
        "than to the folder the reader is standing in"
    )


def test_the_addon_readme_never_calls_the_add_on_an_extension():
    """The vocabulary rule follows the file into the installer.

    It was maintainer material when Ship 1 wrote the rule down in it; it is a
    user-facing document now, so the rule applies exactly as it does to
    docs/BROWSER.md. `chrome://extensions` is Chrome's own name for Chrome's page.
    """
    doc = _text(ADDON_README).replace("chrome://extensions", "").lower()
    for forbidden in (
        "browser extension",
        "chrome extension",
        "the extension",
        "an extension",
        "this extension",
    ):
        assert forbidden not in doc, (
            f"extensions/chrome/README.md calls it {forbidden!r}. In this product "
            "'extension' already means an MCP server -- say 'browser add-on'."
        )


def _slice(path: Path, start: str, end: str) -> str:
    """One-space text between two anchors, both of which must exist.

    Anchored to headings and to the copy's own lead phrases, never to a byte
    window -- the v1.232.0 lesson: a fixed-size window silently stops reaching the
    thing it was meant to contain the moment a sentence grows.
    """
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    i = raw.find(start)
    assert i != -1, f"{path.name} lost the anchor {start!r}"
    rest = raw[i + len(start) :]
    j = rest.find(end)
    return re.sub(r"\s+", " ", rest if j == -1 else rest[:j])


def test_both_guides_state_the_chrome_version_floor_the_manifest_declares():
    """Limitation 2 understated the bar: MV3 support long predates Chrome 120, and
    manifest.json sets `minimum_chrome_version` to 120. A user on a managed Chrome 115
    read that MV3 support was the bar, got Chrome's "requires a newer version of
    Chrome", and had no number anywhere in Iron Jarvis's documentation to check.

    The floor is asserted IN EACH PLACE that answers "which browsers work", not
    merely somewhere in the file: a reader who lands on Supported browsers and one
    who lands on limitation 2 must both get the number, and a whole-file search
    passes on either alone. (Measured: dropping it from Supported browsers left a
    whole-file assertion green, because limitation 2 still carried it.)
    """
    import json

    manifest = json.loads(
        (REPO / "extensions" / "chrome" / "manifest.json").read_text(encoding="utf-8")
    )
    floor = str(manifest["minimum_chrome_version"])
    places = (
        (BROWSER_DOC, "## Supported browsers", "\n## ", "the Supported browsers section"),
        (BROWSER_DOC, "**Chrome and Edge only**", "\n3. ", "limitation 2"),
        (HANDBOOK, "**Supported browsers.**", "\n\n", "the Handbook's Supported browsers line"),
        (HANDBOOK, "Chrome and Edge only", "Loaded unpacked", "the Handbook's limits paragraph"),
    )
    for path, start, end, where in places:
        assert floor in _slice(path, start, end), (
            f"{where} in {path.name} never gives the Chrome version floor ({floor}) "
            "that manifest.json declares. 'Manifest V3' is a lower bar than the "
            "add-on actually requires, and a user on an older managed Chrome has no "
            "number to check against."
        )


def test_both_guides_name_the_fifth_credential():
    """"Five credentials, and none of them substitutes for another" was followed by
    four: install bearer, pairing credential, pane credential, provider secrets. The
    fifth in the plan's section 7 table is MCP client auth. A reader counts the list,
    finds four, and doubts the sentence; the Guide, asked "what are the five
    credentials?", answers from this passage and can name only four."""
    for path in (BROWSER_DOC, HANDBOOK):
        text = _text(path).lower()
        assert "mcp server" in text and "their own configuration" in text, (
            f"{path.name} promises five credentials and names four. The fifth is what "
            "the user's own MCP servers authenticate with, which stays in their own "
            "configuration and which Jarvis never borrows."
        )


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
