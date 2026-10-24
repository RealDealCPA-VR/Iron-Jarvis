"""The desktop splash is the user's lighthouse (2026-10-24).

``desktop/loading.html`` is what the desktop app shows in its small frameless
window while it starts (``createLoadingWindow`` in ``desktop/main.js``). The
user replaced the red orb with a design of their own: a glowing blue
lighthouse on dark rocks, beams fanning left and right, a thin arc behind it,
water below, all on a glass card, over the same two lines of words.

It is drawn as inline SVG and CSS in that one file, so it is sharp at any
display scale and nothing new has to be packaged. These pins hold the pieces
the design is made of, the words and timing that did not change, and the
rules that keep the page safe and calm:

* the words and the dots' timing are the ones the app always had;
* the page loads nothing (the CSP stays ``default-src 'none'``) and every
  ``url(#…)`` points at something defined in the same page;
* every animation stops under "reduce motion", and the heading then keeps
  one dot, as the mockup shows;
* the window that shows it is unchanged except its colour, which matches
  the page's night blue so no other colour flashes before it paints;
* the file is still the one ``build.files`` ships.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
SVG_NS = "{http://www.w3.org/2000/svg}"


def _page() -> str:
    # Normalised at the reader: CI's Windows runners check files out CRLF.
    return (DESKTOP / "loading.html").read_text(encoding="utf-8").replace("\r\n", "\n")


def _style() -> str:
    m = re.search(r"<style>(.*?)</style>", _page(), re.S)
    assert m, "loading.html has no <style> block"
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _svgs() -> dict[str, ET.Element]:
    """Every inline SVG, parsed as XML, keyed by its class."""
    out: dict[str, ET.Element] = {}
    for m in re.finditer(r"<svg\b.*?</svg>", _page(), re.S):
        text = m.group(0)
        if "xmlns=" not in text.split(">", 1)[0]:
            text = text.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
        el = ET.fromstring(text)  # raises if the markup is not well formed
        out[el.get("class", "")] = el
    return out


def _rules_outside_media(css: str) -> str:
    """The style sheet with every @media block removed."""
    out, i = [], 0
    while True:
        j = css.find("@media", i)
        if j < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:j])
        depth, k = 0, css.index("{", j)
        while True:
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1


def _reduce_block(css: str) -> str:
    m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{", css)
    assert m, "no prefers-reduced-motion block"
    depth, k = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(css[k], 0)
        k += 1
    return css[m.end() : k - 1]


# --- the words and the timing the app always had ---------------------------


def test_the_words_are_the_ones_the_app_always_had():
    page = _page()
    assert "<title>Starting Iron Jarvis…</title>" in page
    assert '<h1>Starting Iron Jarvis<span class="dots"></span></h1>' in page
    assert "<p>Getting Iron Jarvis ready — this can take a few seconds.</p>" in page
    assert "daemon" not in page.lower()


def test_the_dots_count_at_the_old_pace():
    css = _style()
    assert re.search(r"\.dots::after\s*\{[^}]*animation:\s*dots 1\.4s steps\(4, end\) infinite", css)
    frames = re.search(r"@keyframes dots\s*\{(.*?)\n\s*\}\n", css, re.S).group(1)
    assert re.findall(r'content:\s*"([^"]*)"', frames) == ["", ".", "..", "...", ""]


def test_the_dots_hang_after_the_words_so_the_heading_never_moves():
    css = _style()
    rule = re.search(r"\n\s*\.dots\s*\{([^}]*)\}", css).group(1)
    assert "position: absolute" in rule and "left: 100%" in rule


# --- the design's pieces ----------------------------------------------------


def test_the_picture_is_inline_svg_in_five_layers():
    svgs = _svgs()
    assert list(svgs) == ["sky", "light", "arc", "land", "glint"], list(svgs)
    # The art is drawn in the mockup's own coordinates: its card is x 30..636.
    for name in ("sky", "light", "land", "glint"):
        assert svgs[name].get("viewBox") == "30 38 606 290", name
    # The arc's box is the square round its circle (centre 334,191, r 112),
    # so turning the box turns the arc about its own centre.
    assert svgs["arc"].get("viewBox") == "214 71 240 240"
    assert '<div class="art" aria-hidden="true">' in _page()


def test_the_lighthouse_has_its_parts():
    land = _svgs()["land"]
    ids = {el.get("id") for el in land.iter() if el.get("id")}
    # tower outline, its blue body, the band turning up it, the white stripe,
    # the gallery, the lantern frame and glass, the dome, rocks and water
    for want in ("towerShape", "towerBody", "band", "stripe", "gallery", "frame",
                 "glassSide", "dome", "rock", "rockRim", "waterLine", "waterGlow"):
        assert want in ids, want
    rocks = [p for g in land.iter(f"{SVG_NS}g") if g.get("fill") == "url(#rock)"
             for p in g.iter(f"{SVG_NS}path")]
    assert len(rocks) == 8, "two rocks behind the tower and six in front"
    finial = [r for r in land.iter(f"{SVG_NS}rect") if r.get("y") == "107"]
    assert finial, "the rod on top of the dome"


def test_the_light_fans_both_ways_and_the_arc_has_three_pieces():
    svgs = _svgs()
    light_ids = {el.get("id") for el in svgs["light"].iter() if el.get("id")}
    assert {"beamLeft", "beamRight", "halo"} <= light_ids
    beams = [p for p in svgs["light"].iter(f"{SVG_NS}path")]
    assert {p.get("fill") for p in beams} == {"url(#beamLeft)", "url(#beamRight)"}
    arcs = [p for p in svgs["arc"].iter(f"{SVG_NS}path")]
    strokes = [p.get("stroke") for p in arcs]
    assert strokes.count("url(#arcLeft)") == 1
    assert strokes.count("url(#arcHead)") == 2  # the head and its glow
    assert strokes.count("url(#arcRight)") == 1
    for p in arcs:
        assert " A112,112 " in p.get("d"), "every piece is on the one circle"


def test_the_card_is_glass_on_night_blue():
    css = _style()
    assert "--night: #041027;" in css
    card = re.search(r"\n\s*\.card\s*\{([^}]*)\}", css).group(1)
    assert "border-radius" in card and "border: 1px solid" in card
    assert "overflow: hidden" in card


# --- safe and self-contained ------------------------------------------------


def test_the_page_loads_nothing():
    page = _page()
    assert ("content=\"default-src 'none'; style-src 'unsafe-inline';\"") in page
    assert "<script" not in page and "<img" not in page and "@import" not in page
    assert not re.search(r"\b(src|href|xlink:href)\s*=", page)
    for target in re.findall(r"url\(([^)]*)\)", page):
        assert target.startswith("#"), f"url({target}) would load something"


def test_every_reference_points_inside_the_page():
    page = _page()
    defined = re.findall(r'\bid="([^"]+)"', page)
    assert len(defined) == len(set(defined)), "an id is used twice"
    for ref in re.findall(r"url\(#([^)]+)\)", page):
        assert ref in defined, f"url(#{ref}) points at nothing"


# --- calm -------------------------------------------------------------------


def test_every_animation_stops_under_reduce_motion():
    css = _style()
    animated = set()
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _rules_outside_media(css)):
        if re.search(r"animation:\s*(?!none)", body):
            animated.add(sel.strip())
    assert animated == {".art svg.arc", ".art svg.light", ".art svg.glint", ".dots::after"}
    reduce = _reduce_block(css)
    stopped = re.search(r"([^{}]+)\{\s*animation:\s*none;\s*\}", reduce).group(1)
    assert {s.strip() for s in stopped.split(",")} == animated
    # With nothing counting, the heading keeps one dot, as in the mockup.
    assert re.search(r'\.dots::after\s*\{\s*content:\s*"\.";\s*\}', reduce)


def test_motion_is_slow_and_on_whole_layers():
    css = _style()
    assert re.search(r"\.art svg\.arc\s*\{[^}]*animation:\s*turn 8s linear infinite", css)
    assert re.search(r"\.art svg\.arc\s*\{[^}]*transform-origin:\s*50% 50%", css)
    # The arc's box sits exactly on its square: (214-30)/606, (71-38)/290, 240/606, 240/290.
    arc = re.search(r"\.art svg\.arc\s*\{([^}]*)\}", css).group(1)
    nums = dict(re.findall(r"(left|top|width|height):\s*([\d.]+)%", arc))
    assert abs(float(nums["left"]) - 100 * 184 / 606) < 0.01
    assert abs(float(nums["top"]) - 100 * 33 / 290) < 0.01
    assert abs(float(nums["width"]) - 100 * 240 / 606) < 0.01
    assert abs(float(nums["height"]) - 100 * 240 / 290) < 0.01


# --- the window and the package --------------------------------------------


def _loading_window() -> str:
    src = (DESKTOP / "main.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"function createLoadingWindow\(\) \{(.*?)\n\}\n", src, re.S)
    assert m, "createLoadingWindow not found in main.js"
    return m.group(1)


def test_the_window_is_the_same_window_in_the_pages_colour():
    body = _loading_window()
    assert "width: 520," in body and "height: 380," in body
    assert "frame: false," in body and "resizable: false," in body
    assert 'title: "Starting Iron Jarvis…",' in body
    assert 'loadFile(path.join(__dirname, "loading.html"))' in body
    # No flash of another colour before the page paints.
    assert 'backgroundColor: "#041027",' in body
    assert "--night: #041027;" in _style()


def test_the_package_ships_the_page_and_needs_nothing_new():
    pkg = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    assert "loading.html" in pkg["build"]["files"]
