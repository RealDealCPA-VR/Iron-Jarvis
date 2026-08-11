"""The draft card's spacing survives a paste into Outlook (v1.163.0).

REPORTED: "everything was easily copied and pasted but the formatting did not
persist in outlook and i needed to reapply the spacing".

The v1.161.0 clipboard HTML was semantically perfect and visually flat:
``<p>``, ``<ul>``, ``<strong>`` with every ``class`` and ``style`` stripped.
Outlook renders through WORD's engine, which gives a bare ``<p>`` a ZERO
margin — the blank lines a browser shows come from the BROWSER's default
stylesheet, and a stylesheet never crosses a clipboard. Measured under a
zero-margin reset: the gap between paragraphs was 0px before the fix and 13px
after (10pt).

Two separate losses, both fixed in `components/chat/DraftCard.tsx`:

* block margins — supplied inline, in POINTS, because that is the one form Word
  honours (`EMAIL_STYLES`);
* soft line breaks — a single newline is a SPACE in markdown, so "Best,\\n
  Valentino" pasted as one line. `hardenLineBreaks` converts those to hard
  breaks for draft bodies only.

WHY THESE ASSERTIONS LIVE IN PYTHON. The vitest suite covers both functions
directly, but nothing there imports `chat/page.tsx` — it is a 6,300-line Next
page with the whole app's dependency graph behind it. A mutation sweep proved
the gap: deleting the call site in that page left every frontend test green.
These are source-level checks on the CALL SITE, the same technique
`test_draft_card_v1161.py` uses for the ```email fence, and the same class of
silent wiring failure it exists to catch.
"""

from __future__ import annotations

import re
from pathlib import Path

_DASH = Path(__file__).resolve().parents[1] / "dashboard"
_CARD = _DASH / "components" / "chat" / "DraftCard.tsx"
_PAGE = _DASH / "app" / "chat" / "page.tsx"


def _card() -> str:
    return _CARD.read_text(encoding="utf-8")


def _page() -> str:
    return _PAGE.read_text(encoding="utf-8")


def test_the_chat_page_actually_calls_the_draft_helper():
    """Without this call the fence renders as a grey code block and every
    frontend test still passes."""
    assert re.search(r"draftFromFence\s*\(", _page()), (
        "chat/page.tsx no longer routes fences through draftFromFence — the "
        "card would never render"
    )


def test_the_chat_page_renders_the_HARDENED_markdown():
    """`draft.text` is the plain-text flavour; `draft.markdown` is the one with
    line breaks hardened. Rendering the wrong field is invisible in every test
    except this one, and costs the user their signature block."""
    page = _page()
    assert re.search(r"content=\{draft\.markdown\}", page), (
        "the draft body is not rendered from draft.markdown, so soft line "
        "breaks collapse and a signature pastes as one line"
    )


def test_block_margins_are_declared_in_points_not_pixels():
    """Word measures in points. A px margin is unreliable in that engine, which
    is the whole reason the paste came out flat."""
    card = _card()
    match = re.search(r"const EMAIL_STYLES[^=]*=\s*\{(.*?)\n\};", card, re.S)
    assert match, "EMAIL_STYLES is gone or no longer a literal map"
    styles = match.group(1)
    for tag in ("P:", "UL:", "OL:", "LI:"):
        assert tag in styles, f"{tag} lost its inline spacing"
    assert "pt" in styles
    assert not re.search(r"margin:[^\"]*\dpx", styles), (
        "a px margin crept in; Word does not honour it reliably"
    )


def test_the_styles_do_not_carry_colour_into_a_composer():
    """Spacing is supplied; THEME is not. A dark-theme colour pasted into a
    white composer is unreadable, and the text should adopt the mail client's
    own font."""
    match = re.search(r"const EMAIL_STYLES[^=]*=\s*\{(.*?)\n\};", _card(), re.S)
    assert match
    styles = match.group(1)
    assert "color:" not in styles.replace("border-left:1.5pt solid #cccccc", "")
    assert "background" not in styles


def test_line_break_hardening_skips_fenced_code():
    """Padding lines inside a fence would corrupt the content being quoted."""
    assert "fenced" in _card(), "the fenced-code guard in hardenLineBreaks is gone"
