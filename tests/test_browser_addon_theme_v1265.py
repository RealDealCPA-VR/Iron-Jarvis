"""v1.265.0 — the add-on's pages follow the browser's theme.

THE REPORT: the sidebar was a dark panel in a light Edge. Every colour on
``sidepanel.html`` and ``setup.html`` was a dark-theme hex value on ``:root``
under ``color-scheme: dark``, so a user whose browser is light got a black
window docked beside white pages.

WHAT IS PINNED, and why each pin is a property rather than a string:

* **Two palettes, and only two places a colour is written.** The light set is
  the default ``:root`` block; the dark set is a ``:root`` block under
  ``@media (prefers-color-scheme: dark)``. Every other rule in the stylesheet
  must reach a colour through ``var(--token)``: a rule that hard-codes a hex or
  ``rgba()`` value is a rule that is wrong in one of the two themes, which is
  exactly how the panel was dark-only before. The scan strips the two token
  blocks and the comments and then asserts NO colour literal remains.
* **The same tokens in both.** A token defined in one block only would take a
  light value in the dark theme (or vice versa) — one colour with one
  definition is the bug in a smaller form.
* **Light is light and dark is dark**, measured: the relative luminance of
  ``--bg`` (WCAG's formula, not a guess at the hex) is high in the light block
  and low in the dark one, and ``--ink`` the reverse.
* **Text stays readable in BOTH themes**, measured: each text-bearing token
  (ink, muted, amber, cyan, green) against its own theme's ground clears the
  WCAG AA ratio for normal text (4.5:1). The light accents are deliberately
  darker than their dark-theme cousins for this reason — the dark theme's
  cyan on white is 2:1, decoration rather than text — and the check is what
  keeps a future "let's use the brand cyan everywhere" edit honest.
* **The page tells the browser it supports both.** ``color-scheme: light dark``
  on ``:root``, never ``dark`` alone: that declaration is what makes the
  browser paint the text box's native parts and the scrollbar to match.

The signal is ``prefers-color-scheme``, which is what Edge's Appearance setting
and Chrome's colour mode (or the operating system, when either is left on
System) hand an extension page. Nothing here renders in a browser: these are
properties of the stylesheet, and the stylesheet is the only thing that decides
them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome" / "src"
PAGES = {
    "sidepanel": ADDON / "sidepanel" / "sidepanel.html",
    "setup": ADDON / "setup" / "setup.html",
    "mic": ADDON / "mic" / "mic.html",  # v1.272.0
}

#: Tokens that carry TEXT (or a text-weight accent) somewhere on the page, and
#: so must clear AA against the page ground in their own theme.
TEXT_TOKENS = ("--ink", "--muted", "--amber", "--cyan", "--green")

AA_NORMAL_TEXT = 4.5

COLOUR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def _stylesheet(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"<style>(.*?)</style>", text, flags=re.S)
    assert match, f"{path.name} has no <style> block"
    return match.group(1)


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _root_blocks(css: str) -> tuple[str, str]:
    """The light ``:root`` body and the dark one, as raw declaration text."""
    css = _strip_comments(css)
    dark = re.search(
        r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{\s*:root\s*\{([^}]*)\}\s*\}",
        css,
        flags=re.S,
    )
    assert dark, "no `@media (prefers-color-scheme: dark) { :root { … } }` block"
    without_dark = css[: dark.start()] + css[dark.end() :]
    light = re.search(r":root\s*\{([^}]*)\}", without_dark, flags=re.S)
    assert light, "no default `:root { … }` block"
    return light.group(1), dark.group(1)


def _tokens(block: str) -> dict[str, str]:
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block)
    }


def _srgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    assert len(h) == 6, f"not an opaque hex colour: {hex_colour!r}"
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


def _luminance(hex_colour: str) -> float:
    """WCAG 2 relative luminance."""

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _srgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: str, bg: str) -> float:
    lf, lb = _luminance(fg), _luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture(params=sorted(PAGES), ids=sorted(PAGES))
def page(request) -> tuple[str, Path]:
    return request.param, PAGES[request.param]


def test_the_page_declares_both_schemes_and_never_dark_alone(page):
    name, path = page
    light, _dark = _root_blocks(_stylesheet(path))
    assert re.search(r"color-scheme\s*:\s*light\s+dark\s*;", light), (
        f"{name}: `:root` must declare `color-scheme: light dark` so the browser paints "
        "native controls and the scrollbar to match"
    )
    css = _strip_comments(_stylesheet(path))
    assert not re.search(r"color-scheme\s*:\s*dark\s*;", css), (
        f"{name}: `color-scheme: dark` alone is the dark-only panel this ship removes"
    )


def test_every_token_is_defined_in_both_themes(page):
    name, path = page
    light, dark = _root_blocks(_stylesheet(path))
    lt, dt = _tokens(light), _tokens(dark)
    assert lt, f"{name}: the light `:root` defines no tokens"
    missing_dark = sorted(set(lt) - set(dt))
    missing_light = sorted(set(dt) - set(lt))
    assert not missing_dark and not missing_light, (
        f"{name}: a token with one definition is one colour that is wrong in the other theme "
        f"— missing in dark: {missing_dark}; missing in light: {missing_light}"
    )


def test_light_is_light_and_dark_is_dark_by_luminance(page):
    name, path = page
    light, dark = _root_blocks(_stylesheet(path))
    lt, dt = _tokens(light), _tokens(dark)
    assert _luminance(lt["--bg"]) > 0.8, f"{name}: the light ground {lt['--bg']} is not light"
    assert _luminance(dt["--bg"]) < 0.05, f"{name}: the dark ground {dt['--bg']} is not dark"
    assert _luminance(lt["--ink"]) < _luminance(dt["--ink"]), (
        f"{name}: light ink must be darker than dark-theme ink"
    )


def test_text_tokens_clear_aa_against_their_own_ground(page):
    name, path = page
    light, dark = _root_blocks(_stylesheet(path))
    for theme, block in (("light", light), ("dark", dark)):
        tokens = _tokens(block)
        ground = tokens["--bg"]
        # The setup page's text sits on its panel, which is the stricter ground
        # in light (pure white) and the lighter one in dark.
        grounds = [ground] + ([tokens["--panel"]] if "--panel" in tokens else [])
        for token in TEXT_TOKENS:
            assert token in tokens, f"{name}/{theme}: no {token}"
            for g in grounds:
                ratio = _contrast(tokens[token], g)
                assert ratio >= AA_NORMAL_TEXT, (
                    f"{name}/{theme}: {token} {tokens[token]} on {g} is {ratio:.2f}:1, "
                    f"below WCAG AA {AA_NORMAL_TEXT}:1 for normal text"
                )


def test_no_colour_is_written_outside_the_two_palettes(page):
    """The scan that keeps the panel two-theme: strip the palettes, find nothing."""
    name, path = page
    css = _strip_comments(_stylesheet(path))
    dark = re.search(
        r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{\s*:root\s*\{[^}]*\}\s*\}",
        css,
        flags=re.S,
    )
    assert dark
    rest = css[: dark.start()] + css[dark.end() :]
    rest = re.sub(r":root\s*\{[^}]*\}", "", rest, count=1, flags=re.S)
    literals = COLOUR_LITERAL.findall(rest)
    assert not literals, (
        f"{name}: colour literal(s) outside the palettes — each is wrong in one theme: {literals}"
    )
    # And the rules DO use the tokens (anti-vacuity: an empty stylesheet passes the
    # scan above).
    assert rest.count("var(--") >= 10, f"{name}: the rules do not reach colours through tokens"
