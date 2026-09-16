"""v1.268.0 — the side panel wears the app's look, sleek.

THE REPORT: "the layout of that chat extension is very blocky … Improve the
look of the extension so it has more of a feel like the overall app, but sleek."

What "blocky" was, in the stylesheet: a 1px border on the header and on the
footer, square-ish controls, a full-width text box over a full-width row of
buttons, every message a flat paragraph. What "the overall app" is, in the
dashboard's stylesheet: the arc-reactor accent (`--accent-rgb: 34 211 238` on
the default Mark 2 theme), soft translucent lines over deep ink surfaces, a
radial accent bloom on the ground, rounded surfaces.

Pinned here as STRUCTURE and as a CROSS-FILE fact — nothing here renders a
pixel, and the theme test (v1.265.0) still owns contrast in both themes:

* the panel's dark accent IS the dashboard's accent, and its light accent is
  one of the dashboard's light-theme accents;
* the ground carries the accent bloom;
* no hard dividers: neither the header nor the footer draws a border;
* messages are bubbles — yours on the right with the accent wash, Jarvis's on
  the left on a surface — not flat paragraphs;
* one rounded composer holds the text box (borderless, transparent) and the
  send button;
* controls are pills, and the primary button is filled with the accent;
* the idle word budget (v1.267.0) is untouched by the redesign — the look added
  no words.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL_HTML = ROOT / "extensions" / "chrome" / "src" / "sidepanel" / "sidepanel.html"
GLOBALS_CSS = ROOT / "dashboard" / "app" / "globals.css"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _style() -> str:
    m = re.search(r"<style>(.*?)</style>", _src(PANEL_HTML), flags=re.S)
    assert m
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _rule(css: str, selector: str) -> str:
    """The declaration block of ONE selector, exactly (not a prefix of a longer one)."""
    # After a closing brace (or the start), so `html,\n body {` never stands in for `body {`.
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"no rule for {selector!r}"
    return m.group(1)


def _palettes() -> tuple[dict[str, str], dict[str, str]]:
    css = _style()
    dark_m = re.search(
        r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{\s*:root\s*\{([^}]*)\}", css, flags=re.S
    )
    assert dark_m
    light_m = re.search(r":root\s*\{([^}]*)\}", css[: dark_m.start()], flags=re.S)
    assert light_m
    tokens = lambda block: dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", block))  # noqa: E731
    return tokens(light_m.group(1)), tokens(dark_m.group(1))


def _rgb_triplets(css: str, name: str) -> set[str]:
    """Every `--name: R G B;` in the dashboard's stylesheet, as lowercase hex."""
    out = set()
    for m in re.finditer(rf"{re.escape(name)}:\s*(\d+)\s+(\d+)\s+(\d+)\s*;", css):
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        out.add(f"#{r:02x}{g:02x}{b:02x}")
    return out


def test_the_accent_is_the_apps_accent():
    light, dark = _palettes()
    globals_css = _src(GLOBALS_CSS)
    app_accents = _rgb_triplets(globals_css, "--accent-rgb")
    app_deep = _rgb_triplets(globals_css, "--accent-deep-rgb")
    # The dashboard's DEFAULT theme is the first --accent-rgb declared (Mark 2).
    first = re.search(r"--accent-rgb:\s*(\d+)\s+(\d+)\s+(\d+)\s*;", globals_css)
    assert first
    default_accent = "#{:02x}{:02x}{:02x}".format(*(int(first.group(i)) for i in (1, 2, 3)))
    assert dark["--cyan"].strip().lower() == default_accent, (
        f"the panel's dark accent {dark['--cyan']} is not the dashboard's {default_accent}"
    )
    assert light["--cyan"].strip().lower() in app_accents | app_deep, (
        f"the panel's light accent {light['--cyan']} is none of the dashboard's accents"
    )


def test_the_ground_carries_the_accent_bloom():
    body = _rule(_style(), "body")
    assert "radial-gradient(" in body and "var(--cyan-glow)" in body and "var(--bg)" in body


def test_no_hard_dividers():
    css = _style()
    assert "border-bottom" not in _rule(css, "header"), "the header still draws a divider"
    assert "border-top" not in _rule(css, "footer"), "the footer still draws a divider"


def test_messages_are_bubbles_yours_right_jarvis_left():
    css = _style()
    turn = _rule(css, ".turn")
    assert "border-radius" in turn and "max-width" in turn, "a message is still a flat paragraph"
    you = _rule(css, '.turn[data-who="you"]')
    assert "margin-left: auto" in you and "var(--cyan-wash)" in you
    jarvis = _rule(css, '.turn[data-who="jarvis"]')
    assert "margin-right: auto" in jarvis and "var(--surface)" in jarvis
    assert "flex-direction: column" in _rule(css, "#transcript")


def test_one_rounded_composer_holds_the_box_and_the_send():
    html = _src(PANEL_HTML)
    composer = re.search(r'<div class="composer">(.*?)</div>\s*<!--', html, flags=re.S)
    assert composer, "no composer card"
    assert 'id="ask"' in composer.group(1) and 'id="send"' in composer.group(1)
    css = _style()
    card = _rule(css, ".composer")
    radius = re.search(r"border-radius:\s*(\d+)px", card)
    assert radius and int(radius.group(1)) >= 14, "the composer is not a rounded card"
    box = _rule(css, "textarea")
    assert "border: 0" in box and "background: transparent" in box, "the text box still draws its own box"


def test_controls_are_pills_and_the_primary_is_filled_with_the_accent():
    css = _style()
    assert "border-radius: 999px" in _rule(css, "button")
    primary = _rule(css, "button.primary")
    assert "background: var(--cyan)" in primary and "var(--on-cyan)" in primary
    assert "border-radius: 999px" in _rule(css, "#model") and "appearance: none" in _rule(css, "#model")


def test_the_redesign_added_no_words():
    from tests.test_browser_sidepanel_minimal_v1267 import IDLE_WORD_BUDGET, _visible_words

    assert len(_visible_words(_src(PANEL_HTML))) <= IDLE_WORD_BUDGET
