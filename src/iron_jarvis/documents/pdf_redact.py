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
        # FORM FIELDS AND NOTES (v1.499.3). A filled field keeps its value in
        # /V and draws it from its own appearance stream, outside the page
        # content above — a filled SSN field survived "verified" redaction.
        _redact_forms_and_notes(pdf, _replace)
        _scrub_metadata(pdf, _replace, targets)
        dst.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(str(dst))

    _verify(dst, targets)
    return counts


# ------------------------------------------------- form fields and notes ---
#
# A filled form keeps what was typed OUTSIDE the page content: the field's
# value (/V, and its default /DV) and the field's drawn appearance (/AP), and
# a comment keeps its words in /Contents and its own appearance. pdfplumber's
# page text sees none of it, so before v1.499.3 a filled SSN field was never
# scanned, never redacted, and the note still said "verified gone".

#: Annotation / field keys whose STRING value is text the user can see.
_TEXT_KEYS = ("/V", "/DV", "/Contents", "/RC")

#: How deep a field tree or an appearance's nested forms are followed.
_MAX_DEPTH = 12


def _string_values(obj: Any) -> list[str]:
    """The text in a field value: a string, or an array of strings."""
    from pikepdf import Array, String

    if isinstance(obj, String):
        return [str(obj)]
    if isinstance(obj, Array):
        return [str(x) for x in obj if isinstance(x, String)]
    return []


def _operand_strings(obj: Any, out: list[str], depth: int = 0) -> None:
    """Every string inside a content-stream operand (arrays and dictionaries
    included, so ``/ActualText`` in a marked-content tag is seen too)."""
    from pikepdf import Array, Dictionary, String

    if depth > _MAX_DEPTH:
        return
    if isinstance(obj, String):
        out.append(str(obj))
    elif isinstance(obj, Array):
        # A TJ array is one run of text split by kerning: join its pieces.
        pieces = [str(x) for x in obj if isinstance(x, String)]
        if pieces:
            out.append("".join(pieces))
        for x in obj:
            if isinstance(x, (Array, Dictionary)):
                _operand_strings(x, out, depth + 1)
    elif isinstance(obj, Dictionary):
        for key in list(obj.keys()):
            _operand_strings(obj.get(key), out, depth + 1)


def _stream_text(stream: Any, out: list[str], depth: int = 0) -> None:
    """The strings drawn by a form stream (an appearance), nested forms too."""
    from pikepdf import Stream, parse_content_stream

    if depth > _MAX_DEPTH or not isinstance(stream, Stream):
        return
    for ins in parse_content_stream(stream):
        for operand in ins.operands:
            _operand_strings(operand, out)
    xobjects = (stream.get("/Resources") or {}).get("/XObject") or {}
    for key in list(xobjects.keys()):
        child = xobjects.get(key)
        if isinstance(child, Stream) and str(child.get("/Subtype", "")) == "/Form":
            _stream_text(child, out, depth + 1)


def _appearance_streams(annot: Any) -> list[Any]:
    """Every appearance stream of an annotation (normal, rollover, down; each
    may be one stream or one per on/off state)."""
    from pikepdf import Dictionary, Stream

    ap = annot.get("/AP")
    if not isinstance(ap, Dictionary):
        return []
    found: list[Any] = []
    for key in ("/N", "/R", "/D"):
        entry = ap.get(key)
        if isinstance(entry, Stream):
            found.append(entry)
        elif isinstance(entry, Dictionary):
            for state in list(entry.keys()):
                s = entry.get(state)
                if isinstance(s, Stream):
                    found.append(s)
    return found


def _iter_fields(pdf: Any):
    """Every node of the AcroForm field tree, parents and widgets alike."""
    from pikepdf import Dictionary

    acro = pdf.Root.get("/AcroForm")
    if not isinstance(acro, Dictionary):
        return
    seen: set[tuple[int, int]] = set()
    stack = [(f, 0) for f in (acro.get("/Fields") or [])]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, Dictionary) or depth > _MAX_DEPTH:
            continue
        key = node.objgen
        if key != (0, 0):
            if key in seen:
                continue
            seen.add(key)
        yield node
        for kid in node.get("/Kids") or []:
            stack.append((kid, depth + 1))


def _iter_annots(pdf: Any):
    from pikepdf import Dictionary

    for page in pdf.pages:
        for annot in page.get("/Annots") or []:
            if isinstance(annot, Dictionary):
                yield annot


def _xfa_text(pdf: Any) -> str:
    """The XML of an XFA form (where such a form keeps its typed values)."""
    from pikepdf import Array, Dictionary, Stream

    acro = pdf.Root.get("/AcroForm")
    if not isinstance(acro, Dictionary):
        return ""
    xfa = acro.get("/XFA")
    parts: list[bytes] = []
    if isinstance(xfa, Stream):
        parts.append(xfa.read_bytes())
    elif isinstance(xfa, Array):
        for item in xfa:
            if isinstance(item, Stream):
                parts.append(item.read_bytes())
    import re

    text = b"\n".join(parts).decode("utf-8", errors="replace")
    # Values sit between tags; one per line keeps the per-line detection honest.
    return "\n".join(t.strip() for t in re.split(r"<[^>]*>", text) if t.strip())


def _form_and_note_text(pdf: Any) -> list[str]:
    lines: list[str] = []
    for field in _iter_fields(pdf):
        for key in ("/V", "/DV"):
            lines.extend(_string_values(field.get(key)))
    for annot in _iter_annots(pdf):
        for key in _TEXT_KEYS:
            lines.extend(_string_values(annot.get(key)))
        # A widget draws its field's value, already read above; any other
        # annotation (a typed note, a stamp) is read from what it draws.
        if str(annot.get("/Subtype", "")) != "/Widget":
            for stream in _appearance_streams(annot):
                _stream_text(stream, lines)
    xfa = _xfa_text(pdf)
    if xfa:
        lines.extend(xfa.splitlines())
    return lines


def pdf_form_text(path: Path) -> str:
    """Text a PDF carries OUTSIDE its page content: filled form field values,
    note (comment) text, what notes draw, and an XFA form's data — one entry
    per line, duplicates dropped. "" for a PDF that has none.

    Raises :class:`UnsupportedPdf` when the file's fields cannot be read, so a
    caller can refuse in words instead of calling the file clean.
    """
    from pikepdf import Pdf

    try:
        with Pdf.open(str(path)) as pdf:
            lines = _form_and_note_text(pdf)
    except Exception as exc:  # noqa: BLE001 — unread is not clean
        raise UnsupportedPdf(f"the form fields and notes could not be read: {exc}") from exc
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        for piece in str(line).splitlines():
            piece = piece.strip()
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    return "\n".join(out)


def _redact_forms_and_notes(pdf: Any, replace: Callable[[str], "tuple[str, int]"]) -> int:
    """Remove the target values from field values, notes and their drawings.

    A field whose value held a target gets the masked value and LOSES its
    drawn appearance (viewers redraw it from the masked value, because
    NeedAppearances is set) — rewriting a widget's own drawing in place is
    the same heuristic as the page, and the drawing is only a copy of /V.
    Other notes keep their drawing with the text operands rewritten.
    An XFA form holding a target is refused: its XML is a second copy of
    every value that this module does not rewrite.
    """
    from pikepdf import Array, Dictionary, String, parse_content_stream, unparse_content_stream

    hits = 0
    touched: set[tuple[int, int]] = set()

    def _mask_key(node: Any, key: str) -> int:
        value = node.get(key)
        if isinstance(value, String):
            masked, n = replace(str(value))
            if n:
                node[key] = String(masked)
            return n
        if isinstance(value, Array):
            n_total = 0
            items = list(value)
            for i, item in enumerate(items):
                if isinstance(item, String):
                    masked, n = replace(str(item))
                    if n:
                        items[i] = String(masked)
                        n_total += n
            if n_total:
                node[key] = Array(items)
            return n_total
        return 0

    def _drop_drawings(node: Any, depth: int = 0) -> None:
        if depth > _MAX_DEPTH or not isinstance(node, Dictionary):
            return
        if "/AP" in node:
            del node["/AP"]
        for kid in node.get("/Kids") or []:
            _drop_drawings(kid, depth + 1)

    xfa = _xfa_text(pdf)
    if xfa:
        _masked, n = replace(xfa)
        if n:
            raise UnsupportedPdf(
                "this is an XFA form, and its form data holds a value to remove; "
                "that copy cannot be edited in place"
            )

    for field in _iter_fields(pdf):
        n = _mask_key(field, "/V") + _mask_key(field, "/DV")
        if n:
            hits += n
            _drop_drawings(field)
            touched.add(field.objgen)

    for annot in _iter_annots(pdf):
        n = sum(_mask_key(annot, key) for key in _TEXT_KEYS)
        parent = annot.get("/Parent")
        inherited = isinstance(parent, Dictionary) and parent.objgen in touched
        if (n or inherited) and str(annot.get("/Subtype", "")) == "/Widget":
            _drop_drawings(annot)
        else:
            for stream in _appearance_streams(annot):
                try:
                    ops = list(parse_content_stream(stream))
                except Exception:  # noqa: BLE001 — the verification decides
                    continue
                new_ops, m = _rewrite_operands(ops, replace)
                if m:
                    stream.write(unparse_content_stream(new_ops))
                    n += m
        hits += n

    if hits:
        acro = pdf.Root.get("/AcroForm")
        if isinstance(acro, Dictionary):
            acro.NeedAppearances = True  # viewers redraw from the masked value
    return hits


def _scrub_metadata(pdf: Any, replace: Callable[[str], "tuple[str, int]"], targets: list[str]) -> None:
    """The document's title/author/subject fields and its XMP packet: a client
    name or SSN typed there is a copy outside every page."""
    from pikepdf import String

    try:
        info = pdf.trailer.get("/Info")
        if info is not None:
            for key in list(info.keys()):
                value = info.get(key)
                if isinstance(value, String):
                    masked, n = replace(str(value))
                    if n:
                        info[key] = String(masked)
    except Exception:  # noqa: BLE001 — the verification decides
        pass
    try:
        meta = pdf.Root.get("/Metadata")
        if meta is not None:
            xml = meta.read_bytes().decode("utf-8", errors="replace").lower()
            if any(" ".join(t.split()).lower() in xml for t in targets):
                del pdf.Root["/Metadata"]
    except Exception:  # noqa: BLE001
        pass


#: Streams that carry no text the user reads (fonts, pictures, attachments,
#: the file's own index) — parsing them as drawing commands is meaningless.
_SKIP_STREAM_TYPES = {"/XRef", "/ObjStm", "/EmbeddedFile", "/FontDescriptor"}
_SKIP_STREAM_SUBTYPES = {"/Image", "/Type1C", "/CIDFontType0C", "/OpenType"}


def _every_string_in(pdf: Any) -> list[str]:
    """Every string anywhere in the file: dictionary values, the strings drawn
    by any page or form stream, field values, notes, metadata.

    This is what makes the verification about the FILE rather than about the
    page text pdfplumber renders — a value left in a field, a note, a hidden
    ``/ActualText`` or the document title is found here.
    """
    from pikepdf import Array, Dictionary, Stream, String, parse_content_stream

    out: list[str] = []

    def walk(obj: Any, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        if isinstance(obj, String):
            out.append(str(obj))
        elif isinstance(obj, Array):
            for x in obj:
                if not getattr(x, "is_indirect", False):
                    walk(x, depth + 1)
        elif isinstance(obj, Dictionary):
            for key in list(obj.keys()):
                x = obj.get(key)
                if not getattr(x, "is_indirect", False):
                    walk(x, depth + 1)

    for obj in pdf.objects:
        if isinstance(obj, Stream):
            walk(obj.stream_dict, 0)
            kind = str(obj.get("/Type", ""))
            sub = str(obj.get("/Subtype", ""))
            if kind in _SKIP_STREAM_TYPES or sub in _SKIP_STREAM_SUBTYPES:
                continue
            if any(k in obj for k in ("/Length1", "/Length2", "/Length3")):
                continue  # an embedded font program
            if "/N" in obj and sub != "/Form":
                continue  # a colour profile
            if sub == "/XML" or kind == "/Metadata":
                out.append(obj.read_bytes().decode("utf-8", errors="replace"))
                continue
            try:
                instructions = parse_content_stream(obj)
            except Exception:  # noqa: BLE001 — not drawing commands
                continue
            for ins in instructions:
                for operand in ins.operands:
                    _operand_strings(operand, out)
        else:
            walk(obj, 0)
    try:
        walk(pdf.trailer.get("/Info"), 0)
    except Exception:  # noqa: BLE001
        pass
    xfa = _xfa_text(pdf)
    if xfa:
        out.append(xfa)
    return out


def _verify(written: Path, targets: list[str]) -> None:
    """Re-read the written file and prove the targets are gone.

    The transform is heuristic; this is not. A PDF that looks redacted while
    still carrying the SSN is the worst thing this module could produce, so the
    guarantee is made against the actual bytes on disk rather than against the
    intent of the code above. Since v1.499.3 that means the page text AND every
    string in the file — field values, what fields and notes draw, metadata —
    not only what the page renders.
    """
    from pikepdf import Pdf

    from .readers import extract_text

    try:
        text = extract_text(written)
        with Pdf.open(str(written)) as pdf:
            strings = _every_string_in(pdf)
    except Exception as exc:  # noqa: BLE001 — unverifiable is not verified
        written.unlink(missing_ok=True)
        raise RedactionUnverified(f"could not re-read the output: {exc}") from exc

    hay = " ".join(text.split()).lower()
    hidden = [" ".join(s.split()).lower() for s in strings]
    survivors = []
    for v in targets:
        needle = " ".join(v.split()).lower()
        if needle in hay or any(needle in s for s in hidden):
            survivors.append(v)
    if survivors:
        written.unlink(missing_ok=True)
        shown = ", ".join(repr(s) for s in survivors[:3])
        raise RedactionUnverified(
            f"{len(survivors)} value(s) still extractable after redaction: {shown}"
        )
