"""A picture of one PDF page with the line a figure was read from boxed
(v1.547.0, idea 30-a).

``GET /documents/page-image`` draws it for the preview beside the chat:

* the page is rendered with ``pypdfium2`` (it ships in the frozen build; one
  render at a time, pdfium is not safe on two threads at once);
* the recorded line is found among ``pdfplumber``'s word boxes (the same
  geometry PDF redaction reads): the line holding the figure whose words
  best match the recorded line. The line is boxed and the figure lit;
* when the line cannot be found (the file changed, the text layer reads
  differently), the page is drawn with the recorded line printed ABOVE it
  and words saying so. It never boxes a guess.

BLOCKING: the route runs it on the threadpool.
"""

from __future__ import annotations

import io
import re
import threading
from pathlib import Path

from ..core.numbers import figures_in

#: The widest picture drawn (pixels); a page is never drawn larger than 2x.
MAX_WIDTH_PX = 1400
MAX_SCALE = 2.0
#: Two words this far apart vertically (points) are on one line.
LINE_TOLERANCE = 3.0
_PDFIUM_LOCK = threading.Lock()
_TOKEN_RX = re.compile(r"[a-z0-9]+")

#: Said above the page when the line was not found (twinned in no other
#: place: it is drawn into the picture).
NOT_FOUND_WORDS = "This line was not found on page {page}. The line as Iron Jarvis read it:"


class NoSuchPage(ValueError):
    """The PDF has no such page."""


def _tokens(text: str) -> "list[str]":
    return [t for t in _TOKEN_RX.findall((text or "").lower()) if t]


def _lines(words: "list[dict]") -> "list[list[dict]]":
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(float(w["top"]), 1), float(w["x0"]))):
        if rows and abs(float(rows[-1][0]["top"]) - float(w["top"])) <= LINE_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def find_line(path: "str | Path", page: int, find: str, fig: str = "") -> "tuple[tuple, tuple | None] | None":
    """``(line box, figure box | None)`` in PDF points (top-left origin), or
    None when the recorded line is not on that page. Raises
    :class:`NoSuchPage`."""
    import pdfplumber

    want = [t for t in _tokens(find) if t != "*" ]
    fig_keys = figures_in(fig) if fig else set()
    with pdfplumber.open(str(path)) as pdf:
        if page < 1 or page > len(pdf.pages):
            raise NoSuchPage(page)
        words = pdf.pages[page - 1].extract_words() or []
    best: "tuple[float, list[dict], dict | None] | None" = None
    for line in _lines(words):
        text = " ".join(str(w.get("text") or "") for w in line)
        holder = None
        if fig_keys:
            holder = next((w for w in line if figures_in(str(w.get("text") or "")) & fig_keys), None)
            if holder is None:
                continue
        have = set(_tokens(text))
        score = (sum(1 for t in want if t in have) / len(want)) if want else (1.0 if holder else 0.0)
        if best is None or score > best[0]:
            best = (score, line, holder)
    if best is None or best[0] < 0.5:
        return None
    _score, line, holder = best
    box = (min(float(w["x0"]) for w in line), min(float(w["top"]) for w in line),
           max(float(w["x1"]) for w in line), max(float(w["bottom"]) for w in line))
    fig_box = None
    if holder is not None:
        fig_box = (float(holder["x0"]), float(holder["top"]), float(holder["x1"]), float(holder["bottom"]))
    return box, fig_box


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an older Pillow: the small built-in font
        return ImageFont.load_default()


def _wrap(text: str, width_chars: int) -> "list[str]":
    words = (text or "").split()
    out: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width_chars:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out[:6]


def render_page(path: "str | Path", page: int, *, find: str = "", fig: str = "") -> "tuple[bytes, bool]":
    """``(png bytes, line found)`` for page ``page`` (1-based) of ``path``."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw

    hit = find_line(path, page, find, fig) if (find or fig) else None
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(path))
        try:
            if page < 1 or page > len(doc):
                raise NoSuchPage(page)
            pg = doc[page - 1]
            width_pt, _height_pt = pg.get_size()
            scale = min(MAX_SCALE, MAX_WIDTH_PX / max(1.0, float(width_pt)))
            img = pg.render(scale=scale).to_pil().convert("RGB")
            pg.close()
        finally:
            doc.close()
    found = hit is not None
    if found:
        line_box, fig_box = hit
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        pad = 4

        def px(b: tuple) -> tuple:
            return (b[0] * scale - pad, b[1] * scale - pad, b[2] * scale + pad, b[3] * scale + pad)

        draw.rectangle(px(line_box), fill=(255, 196, 0, 46), outline=(234, 88, 12, 255), width=3)
        if fig_box is not None:
            draw.rectangle(px(fig_box), fill=(255, 196, 0, 110))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    elif find:
        font = _font(18)
        lines = [NOT_FOUND_WORDS.format(page=page)] + [f"  {ln}" for ln in _wrap(find, 90)]
        band = 18 + 26 * len(lines)
        out = Image.new("RGB", (img.width, img.height + band), (255, 247, 230))
        out.paste(img, (0, band))
        draw = ImageDraw.Draw(out)
        for i, ln in enumerate(lines):
            draw.text((16, 10 + 26 * i), ln, fill=(120, 53, 15), font=font)
        img = out
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), found


__all__ = ["MAX_WIDTH_PX", "NOT_FOUND_WORDS", "NoSuchPage", "find_line", "render_page"]
