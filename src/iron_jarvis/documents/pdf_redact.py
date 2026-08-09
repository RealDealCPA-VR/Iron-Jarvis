"""TRUE PDF redaction: the page survives, the PII does not (v1.154.0).

What this replaces, measured on the document that prompted it (a 29-page
Lacerte return): the old path extracted the text, redacted the string, and
REBUILT a PDF. That turned 29 pages into 37, changed the page size to A4,
substituted every font, and dropped all 30 form rules. The PII was genuinely
gone — and the result was not a tax return any more.

The old choice was deliberate and the reasoning was right: pypdf cannot edit
page content, and painting a black box over live text is a FAKE redaction
because the text underneath stays extractable. Given only those two options,
"truly gone, layout approximate" beats "looks right, still leaks". This module
adds the third option rather than picking between them.

HOW IT WORKS — two independent tools, each for what it is good at:

* **pikepdf** (MPL-2.0, so it can ship here) rewrites the CONTENT STREAM. The
  glyphs are removed from the text-showing operators, so the characters are
  actually deleted from the file. Every other page object — form rules, page
  size, fonts, images — is left exactly as it was, because the page is edited
  rather than regenerated.
* **pdfplumber** supplies GEOMETRY. It reports where each word is actually
  rendered, which is used to paint black boxes. Taking the geometry from a
  renderer avoids re-implementing PDF text-matrix arithmetic (``Tm``/``Td``/
  ``TJ`` offsets, per-glyph advances, font widths) — the part of this problem
  that is easy to get subtly, silently wrong.

WHY IT VERIFIES ITS OWN OUTPUT
------------------------------
Matching text inside content streams is heuristic: a value can be split across
several operators, encodings vary, and a font can map bytes to anything. So the
transform is not trusted. :func:`redact_pdf` re-extracts the text from the file
it just wrote and confirms every target value is gone. If ANY survives it
raises :class:`RedactionUnverified` and the caller falls back — because a PDF
that looks redacted while still carrying the SSN is the single worst artifact
this feature could produce, far worse than an ugly rebuild.

The font gate is the other half of that caution: fonts whose bytes cannot be
decoded to characters (Type0/CID, or a symbolic font with no usable encoding)
are refused up front rather than matched against and quietly missed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

#: Simple-font encodings whose operand bytes decode to text we can match.
_DECODABLE_ENCODINGS = {
    "/WinAnsiEncoding",
    "/MacRomanEncoding",
    "/StandardEncoding",
    "/PDFDocEncoding",
}

#: Text-showing operators. ``'`` and ``"`` also move to the next line first.
_TEXT_OPS = {"Tj", "TJ", "'", '"'}

#: Padding around a word box, in points — a glyph's ink can sit a hair outside
#: its reported box, and a redaction bar that leaves a sliver of a digit
#: showing is not a redaction.
_BOX_PAD = 0.6


class RedactionUnverified(RuntimeError):
    """The output could not be PROVEN free of the target values.

    Raised instead of returning a file that merely looks redacted. The caller
    is expected to fall back to a method whose guarantee it can state.
    """


class UnsupportedPdf(RuntimeError):
    """This PDF's fonts cannot be matched reliably (Type0/CID or unmapped).

    Distinct from :class:`RedactionUnverified`: nothing was attempted, so there
    is no half-redacted artifact — the caller simply uses another route.
    """


def _font_is_decodable(font: Any) -> bool:
    """True when a font's string operands decode to matchable characters."""
    try:
        subtype = str(font.get("/Subtype", ""))
        if subtype == "/Type0":
            return False  # CID: bytes are glyph ids, not characters
        enc = font.get("/Encoding")
        if enc is None:
            # No /Encoding on a simple font means the font's BUILT-IN encoding.
            # For the standard 14 and ordinary TrueType text fonts that is
            # ASCII-compatible in practice; a symbolic font is not, and we
            # cannot tell from here, so this is the one benefit of the doubt —
            # and the output verification below is what makes it safe to give.
            return True
        if hasattr(enc, "get"):  # a dictionary, possibly with /Differences
            base = str(enc.get("/BaseEncoding", "/StandardEncoding"))
            return base in _DECODABLE_ENCODINGS
        return str(enc) in _DECODABLE_ENCODINGS
    except Exception:  # noqa: BLE001 — an unreadable font is an unsupported one
        return False


def _page_fonts_decodable(page: Any) -> bool:
    try:
        res = page.get("/Resources") or {}
        fonts = res.get("/Font") or {}
        return all(_font_is_decodable(f) for f in fonts.values())
    except Exception:  # noqa: BLE001
        return False


def _word_boxes(path: Path, targets: list[str]) -> dict[int, list[tuple[float, float, float, float]]]:
    """Per-page black-box rectangles for every occurrence of *targets*.

    Coordinates are converted from pdfplumber's top-left origin to PDF user
    space (bottom-left). Matching is per WORD and case-insensitive: a target
    like "NICHOLAS GIORDANO" is drawn as two boxes, which is what the renderer
    actually laid out.
    """
    import pdfplumber

    wanted: set[str] = set()
    for t in targets:
        for part in str(t).split():
            cleaned = part.strip().strip(".,;:()[]").lower()
            if len(cleaned) >= 2:  # 1-char tokens would black out the page
                wanted.add(cleaned)
    boxes: dict[int, list[tuple[float, float, float, float]]] = {}
    if not wanted:
        return boxes
    with pdfplumber.open(str(path)) as doc:
        for i, page in enumerate(doc.pages):
            height = float(page.height)
            found: list[tuple[float, float, float, float]] = []
            try:
                words = page.extract_words()
            except Exception:  # noqa: BLE001 — a bad page yields no boxes
                words = []
            for w in words:
                if w["text"].strip().strip(".,;:()[]").lower() in wanted:
                    x0, x1 = float(w["x0"]), float(w["x1"])
                    top, bottom = float(w["top"]), float(w["bottom"])
                    found.append((
                        x0 - _BOX_PAD,
                        height - bottom - _BOX_PAD,
                        (x1 - x0) + 2 * _BOX_PAD,
                        (bottom - top) + 2 * _BOX_PAD,
                    ))
            if found:
                boxes[i] = found
    return boxes


def _rewrite_operands(ops: list[Any], replace: Callable[[str], "tuple[str, int]"]):
    """Apply *replace* to every text operand. Returns (new_ops, hits)."""
    from pikepdf import ContentStreamInstruction, Operator, String

    out: list[Any] = []
    hits = 0
    for ins in ops:
        op = str(ins.operator)
        if op not in _TEXT_OPS:
            out.append(ins)
            continue
        try:
            if op in ("Tj", "'", '"'):
                # The string is the LAST operand ('"' takes two numbers first).
                operands = list(ins.operands)
                original = str(operands[-1])
                masked, n = replace(original)
                if n:
                    hits += n
                    operands[-1] = String(masked)
                    ins = ContentStreamInstruction(operands, Operator(op))
            else:  # TJ — an array of strings interleaved with kern numbers
                array = list(ins.operands[0])
                changed = False
                for j, item in enumerate(array):
                    if isinstance(item, (int, float)):
                        continue
                    masked, n = replace(str(item))
                    if n:
                        hits += n
                        array[j] = String(masked)
                        changed = True
                if changed:
                    ins = ContentStreamInstruction([array], Operator("TJ"))
        except Exception:  # noqa: BLE001 — never corrupt a stream we can't parse
            pass
        out.append(ins)
    return out, hits


def _black_box_ops(boxes: list[tuple[float, float, float, float]]) -> bytes:
    """Content-stream fragment painting opaque rectangles, appended last."""
    parts = ["\nq 0 0 0 rg\n"]
    for x, y, w, h in boxes:
        parts.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re f\n")
    parts.append("Q\n")
    return "".join(parts).encode("ascii")


def redact_pdf(
    src: Path,
    dst: Path,
    *,
    values: list[str],
    replacement: Callable[[str], str] | None = None,
    draw_boxes: bool = True,
) -> dict[str, int]:
    """Remove *values* from *src* IN PLACE, writing *dst*. Layout survives.

    *replacement* maps a matched value to the text left behind (the caller's
    style: same-width blanks, a ``[SSN]`` label, or ""). Default is spaces,
    which keeps the surrounding line's spacing closest to the original.

    Raises :class:`UnsupportedPdf` when the fonts cannot be matched, and
    :class:`RedactionUnverified` when the written file still contains a target.
    Both leave *dst* absent; the caller chooses what to do instead.
    """
    from pikepdf import Pdf, parse_content_stream, unparse_content_stream

    targets = [str(v) for v in values if str(v).strip()]
    if not targets:
        raise ValueError("no values to redact")
    # Longest first: redacting "GIORDANO" before "NICHOLAS GIORDANO" would leave
    # the first name sitting on the page.
    targets.sort(key=len, reverse=True)
    blank = replacement or (lambda v: " " * len(v))

    counts: dict[str, int] = {}

    def _replace(text: str) -> tuple[str, int]:
        hits = 0
        for value in targets:
            if value and value in text:
                n = text.count(value)
                text = text.replace(value, blank(value))
                counts[value] = counts.get(value, 0) + n
                hits += n
        return text, hits

    boxes = _word_boxes(src, targets) if draw_boxes else {}

    with Pdf.open(str(src)) as pdf:
        for index, page in enumerate(pdf.pages):
            if not _page_fonts_decodable(page):
                raise UnsupportedPdf(
                    "this PDF uses fonts whose text cannot be matched reliably "
                    "(embedded CID/Type0 or a symbolic encoding)"
                )
            try:
                ops = list(parse_content_stream(page))
            except Exception as exc:  # noqa: BLE001
                raise UnsupportedPdf(f"unreadable page content: {exc}") from exc
            new_ops, _hits = _rewrite_operands(ops, _replace)
            body = unparse_content_stream(new_ops)
            if draw_boxes and boxes.get(index):
                body = body + _black_box_ops(boxes[index])
            page.Contents = pdf.make_stream(body)
        dst.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(str(dst))

    _verify(dst, targets)
    return counts


def _verify(written: Path, targets: list[str]) -> None:
    """Re-read the written file and prove the targets are gone.

    The transform is heuristic; this is not. A PDF that looks redacted while
    still carrying the SSN is the worst thing this module could produce, so the
    guarantee is made against the actual bytes on disk rather than against the
    intent of the code above.
    """
    from .readers import extract_text

    try:
        text = extract_text(written)
    except Exception as exc:  # noqa: BLE001 — unverifiable is not verified
        written.unlink(missing_ok=True)
        raise RedactionUnverified(f"could not re-read the output: {exc}") from exc

    hay = " ".join(text.split()).lower()
    survivors = [v for v in targets if " ".join(v.split()).lower() in hay]
    if survivors:
        written.unlink(missing_ok=True)
        shown = ", ".join(repr(s) for s in survivors[:3])
        raise RedactionUnverified(
            f"{len(survivors)} value(s) still extractable after redaction: {shown}"
        )
