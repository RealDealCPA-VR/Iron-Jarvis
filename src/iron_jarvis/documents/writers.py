"""Document writers.

``write_document(path, content, *, kind=None)`` creates a real file on disk,
choosing the format from the path suffix (or ``kind``, which overrides it).

String content is markdown-aware: it is parsed by
:mod:`iron_jarvis.documents.markdown` into blocks (headings, bullets, numbered
lists, code fences, pipe tables, ``---`` rules, and inline
``**bold**``/``*italic*``/`` `code` ``/``[link](url)``/``![img](url)`` runs)
which the rich writers render natively. Plain text with no markers simply
becomes paragraphs, so flat strings keep working everywhere.

* ``.docx`` -> python-docx: real Heading/List styles, real tables, shaded
  monospace code blocks, bold/italic/code/hyperlink runs. Non-string content:
  one paragraph per line.
* ``.xlsx`` -> openpyxl: a ``list[list]`` writes rows; a str writes one cell per
  line in column A; ``{"sheets": {name: rows}}`` writes one worksheet per key
  with a bolded/frozen header row, sized columns, and numeric/date COERCION of
  data cells. ``=...`` strings stay formulas; leading-zero ids stay text.
* ``.pptx`` -> python-pptx: each ``# `` heading or ``---`` starts a new slide;
  long sections spill onto ``Title (cont.)`` slides; tables render as real
  pptx tables; a ``Notes:`` line feeds the slide's speaker notes.
* ``.pdf``  -> fpdf2: sized bold headings, wrapped paragraphs, indented
  bullets, native wrapping tables with per-column alignment, Courier code on
  grey fill. Long tokens are pre-broken and any layout failure degrades to a
  reduced/plain render rather than producing no file.
* ``.html/.htm`` -> standalone HTML with inline CSS (links/images, validly
  nested lists, aligned tables). A full HTML page is passed through verbatim.
* ``.json`` -> ``json.dumps`` for dict/list content (str passthrough).
* ``.yaml/.yml`` -> ``yaml.safe_dump`` if PyYAML is importable, else JSON.
* ``.csv``  -> stdlib csv from a ``list[list]`` or from text lines.
* ``.txt/.md`` and anything else -> UTF-8 text.

Every writer saves to a sibling temp file then ``os.replace()`` onto the target
(atomic): a mid-save failure never clobbers an existing good file or leaves a
0-byte document. Parent directories are created as needed; the Path is returned.
"""

from __future__ import annotations

import csv
import html as _html_mod
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .markdown import Block, Run, parse_markdown

#: Suffixes with a dedicated writer (everything else falls back to UTF-8 text).
SUPPORTED_WRITE: set[str] = {
    ".docx",
    ".xlsx",
    ".pptx",
    ".pdf",
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".html",
    ".htm",
    ".log",
    ".yaml",
    ".yml",
}


def write_document(
    path: str | Path, content: Any, *, kind: str | None = None
) -> Path:
    """Write ``content`` to ``path`` as a real document. Returns the Path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    suffix = ("." + kind.lstrip(".")).lower() if kind else p.suffix.lower()

    if suffix == ".docx":
        _write_docx(p, content)
    elif suffix == ".xlsx":
        _write_xlsx(p, content)
    elif suffix == ".pptx":
        _write_pptx(p, content)
    elif suffix == ".pdf":
        _write_pdf(p, content)
    elif suffix == ".csv":
        _write_csv(p, content)
    elif suffix in (".html", ".htm"):
        _write_html(p, content)
    elif suffix == ".json":
        _write_json(p, content)
    elif suffix in (".yaml", ".yml"):
        _write_yaml(p, content)
    else:
        _write_text(p, content)
    return p


# --- atomic write --------------------------------------------------------------


@contextmanager
def _atomic(p: Path):
    """Yield a sibling temp path; ``os.replace`` it onto ``p`` only on success.

    If the body raises (a half-written temp), we delete the temp and re-raise,
    so the pre-existing good file at ``p`` is never touched and no 0-byte doc is
    ever left behind. The temp lives in the SAME directory so ``os.replace`` is
    an atomic same-filesystem rename on every platform (incl. Windows).
    """
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    try:
        yield tmp
        os.replace(tmp, p)
    except BaseException:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise


def _atomic_text(p: Path, data: str) -> None:
    with _atomic(p) as tmp:
        Path(tmp).write_text(data, encoding="utf-8")


# --- helpers ------------------------------------------------------------------


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "\n".join(
            "\t".join(str(c) for c in row)
            if isinstance(row, (list, tuple))
            else str(row)
            for row in content
        )
    return str(content)


def _as_lines(content: Any) -> list[str]:
    return _as_text(content).split("\n")


def _run_parts(r: Any) -> tuple[str, bool, bool, bool, str | None, bool]:
    """(text, bold, italic, code, href, image) for a Run OR a bare 3-tuple."""
    return (
        r[0],
        r[1],
        r[2],
        getattr(r, "code", False),
        getattr(r, "href", None),
        getattr(r, "image", False),
    )


# --- json / yaml ---------------------------------------------------------------


def _write_json(p: Path, content: Any) -> None:
    # A dict/list must serialise as real JSON — the old text path wrote a Python
    # repr (single quotes, True/None) that no JSON parser accepts. Already-
    # serialised strings pass through untouched.
    if isinstance(content, str):
        data = content
    else:
        data = json.dumps(content, indent=2, ensure_ascii=False, default=str)
    _atomic_text(p, data)


def _write_yaml(p: Path, content: Any) -> None:
    if isinstance(content, str):
        _atomic_text(p, content)
        return
    try:
        import yaml  # optional dependency

        data = yaml.safe_dump(content, sort_keys=False, allow_unicode=True)
    except Exception:  # PyYAML missing or un-dumpable -> valid JSON is a fine YAML
        data = json.dumps(content, indent=2, ensure_ascii=False, default=str)
    _atomic_text(p, data)


# --- docx ----------------------------------------------------------------------


def _write_docx(p: Path, content: Any) -> None:
    import docx

    doc = docx.Document()
    if isinstance(content, str):
        _docx_render(doc, parse_markdown(content))
    else:
        for line in _as_lines(content):
            doc.add_paragraph(line)
    with _atomic(p) as tmp:
        doc.save(str(tmp))


def _docx_hyperlink(paragraph: Any, url: str, text: str, *, code: bool = False) -> None:
    """Append a real clickable ``w:hyperlink`` run (blue + underlined)."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rpr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(underline)
    if code:
        rfonts = OxmlElement("w:rFonts")
        rfonts.set(qn("w:ascii"), "Consolas")
        rfonts.set(qn("w:hAnsi"), "Consolas")
        rpr.append(rfonts)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def _docx_runs(paragraph: Any, runs: list[Run]) -> None:
    for r in runs:
        text, bold, italic, code, href, image = _run_parts(r)
        if href:
            # Links and images both become clickable text — the alt/label never
            # leaks the raw ``[..](..)`` markup into the document.
            label = text if not image else (text or href)
            _docx_hyperlink(paragraph, href, label, code=code)
            continue
        run = paragraph.add_run(text)
        run.bold = bold
        run.italic = italic
        if code:
            run.font.name = "Consolas"


def _docx_styled_paragraph(doc: Any, style: str, fallback: str) -> Any:
    try:
        return doc.add_paragraph(style=style)
    except KeyError:  # style missing from the template -> nearest base style
        return doc.add_paragraph(style=fallback)


def _docx_code_block(doc: Any, text: str) -> None:
    """One shaded monospace paragraph for a whole code fence (not one per line)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    para = _docx_styled_paragraph(doc, "No Spacing", "Normal")
    p_pr = para._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")  # light-grey block fill behind the code
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F2F2F2")
    p_pr.append(shd)
    lines = text.split("\n") or [""]
    first = True
    for line in lines:
        run = para.add_run()
        if not first:
            run.add_break()  # keep the block one paragraph, wrap on soft breaks
        run.add_text(line)
        run.font.name = "Consolas"
        run.font.size = Pt(9)
        first = False


def _docx_align(align: str) -> Any:
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    return {
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "left": WD_ALIGN_PARAGRAPH.LEFT,
    }.get(align)


def _docx_render(doc: Any, blocks: list[Block]) -> None:
    for b in blocks:
        if b.kind == "heading":
            heading = doc.add_heading(level=b.level)
            _docx_runs(heading, b.runs)  # honor inline runs inside headings
        elif b.kind == "bullet":
            style = "List Bullet" if b.level == 0 else "List Bullet 2"
            _docx_runs(_docx_styled_paragraph(doc, style, "List Bullet"), b.runs)
        elif b.kind == "numbered":
            style = "List Number" if b.level == 0 else "List Number 2"
            _docx_runs(_docx_styled_paragraph(doc, style, "List Number"), b.runs)
        elif b.kind == "code":
            _docx_code_block(doc, b.text)
        elif b.kind == "table":
            cols = max(len(r) for r in b.rows)
            table = doc.add_table(rows=len(b.rows), cols=cols)
            try:
                table.style = "Table Grid"
            except KeyError:
                pass  # borderless is better than no table at all
            for ri, row in enumerate(b.rows):
                for ci in range(cols):
                    cell = table.cell(ri, ci)
                    cell.text = row[ci] if ci < len(row) else ""
                    align = _docx_align(b.aligns[ci]) if ci < len(b.aligns) else None
                    for para in cell.paragraphs:
                        if align is not None:
                            para.alignment = align
                        if ri == 0:  # header row bold
                            for run in para.runs:
                                run.bold = True
        elif b.kind == "hr":
            doc.add_paragraph("─" * 30)
        else:  # paragraph
            _docx_runs(doc.add_paragraph(), b.runs)


# --- xlsx ----------------------------------------------------------------------


def _write_xlsx(p: Path, content: Any) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    if isinstance(content, dict) and isinstance(content.get("sheets"), dict):
        wb.remove(wb.active)
        for name, rows in content["sheets"].items():
            title = re.sub(r"[\[\]:*?/\\]", "_", str(name))[:31] or "Sheet"
            base, n = title, 1
            while title in wb.sheetnames:  # duplicate names must not crash
                n += 1
                title = f"{base[:28]}~{n}"
            _xlsx_fill(wb.create_sheet(title=title), rows)
    else:
        ws = wb.active
        if isinstance(content, (list, tuple)):
            for row in content:
                if isinstance(row, (list, tuple)):
                    ws.append([("" if c is None else c) for c in row])
                else:
                    ws.append([row])
        else:
            for line in _as_lines(content):
                ws.append([line])
    with _atomic(p) as tmp:
        wb.save(str(tmp))


_XLSX_INT_RX = re.compile(r"-?\d+")
_XLSX_FLOAT_RX = re.compile(r"-?\d+\.\d+")
_XLSX_DATE_RX = re.compile(r"\d{4}-\d{2}-\d{2}")
_XLSX_DATETIME_RX = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")


def _xlsx_coerce(v: Any) -> tuple[Any, str | None]:
    """Coerce a data cell to number/date; return (value, number_format|None).

    Only clean, unambiguous strings convert: pure ints/floats and ISO dates.
    Formulas (``=``), mixed text, and leading-zero strings (ids, zips, phone
    numbers) stay text so we never silently mangle "007" into 7.
    """
    if not isinstance(v, str):
        return v, None
    s = v.strip()
    if not s or s.startswith("="):
        return v, None
    # A leading zero on a multi-digit integer means "identifier", keep as text.
    if len(s) > 1 and s[0] == "0" and s[1] != ".":
        return v, None
    if _XLSX_INT_RX.fullmatch(s):
        try:
            return int(s), None
        except ValueError:
            return v, None
    if _XLSX_FLOAT_RX.fullmatch(s):
        try:
            return float(s), None
        except ValueError:
            return v, None
    if _XLSX_DATETIME_RX.fullmatch(s):
        try:
            return datetime.fromisoformat(s.replace(" ", "T")), "yyyy-mm-dd hh:mm:ss"
        except ValueError:
            return v, None
    if _XLSX_DATE_RX.fullmatch(s):
        try:
            return datetime.fromisoformat(s), "yyyy-mm-dd"
        except ValueError:
            return v, None
    return v, None


def _xlsx_fill(ws: Any, rows: Any) -> None:
    """Fill one worksheet; bold+freeze a header row, size columns, coerce data."""
    if isinstance(rows, (list, tuple)):
        norm: list[list[Any]] = [
            [("" if c is None else c) for c in row]
            if isinstance(row, (list, tuple))
            else [row]
            for row in rows
        ]
    else:
        norm = [[line] for line in _as_lines(rows)]

    header = (
        len(norm) >= 2
        and bool(norm[0])
        and all(isinstance(c, str) and not c.startswith("=") for c in norm[0])
    )

    for ri, row in enumerate(norm, start=1):
        for ci, val in enumerate(row, start=1):
            cell = ws.cell(row=ri, column=ci)
            if header and ri == 1:
                cell.value = val  # header labels stay verbatim text
                continue
            coerced, fmt = _xlsx_coerce(val)
            cell.value = coerced
            if fmt:
                cell.number_format = fmt

    if header:
        from openpyxl.styles import Font

        for cell in ws[1]:
            cell.font = Font(bold=True)
        ws.freeze_panes = "A2"

    from openpyxl.utils import get_column_letter

    widths: dict[int, int] = {}
    for row in norm:
        for i, c in enumerate(row, start=1):
            widths[i] = max(widths.get(i, 0), len(str(c)))
    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i)].width = min(max(w + 2, 8), 60)


# --- pptx ----------------------------------------------------------------------

#: Bullets past this many spill onto a "Title (cont.)" continuation slide so a
#: dense section never runs silently off the bottom of the slide.
_PPTX_MAX_ITEMS = 9


def _write_pptx(p: Path, content: Any) -> None:
    import pptx

    sections = (
        _pptx_sections(parse_markdown(content)) if isinstance(content, str) else None
    )

    prs = pptx.Presentation()

    if sections is None:
        # Legacy flat deck: title slide + one bullet slide with every line.
        lines = list(_as_lines(content))
        title = lines[0] if lines and lines[0].strip() else "Document"

        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = title
        if len(title_slide.placeholders) > 1:
            title_slide.placeholders[1].text = "Generated by Iron Jarvis"

        bullet_slide = prs.slides.add_slide(prs.slide_layouts[1])
        bullet_slide.shapes.title.text = title
        body = bullet_slide.placeholders[1].text_frame
        _pptx_prep_body(body)
        first, *rest = lines or [""]
        body.text = first
        for line in rest:
            body.add_paragraph().text = line
    else:
        # Sectioned deck: title slide + one (or more) slide per '# '/'---'.
        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = sections[0][0] or "Document"
        if len(title_slide.placeholders) > 1:
            title_slide.placeholders[1].text = "Generated by Iron Jarvis"

        for title, items, tables, notes in sections:
            _pptx_content_slides(prs, title or "Section", items, tables, notes)

    with _atomic(p) as tmp:
        prs.save(str(tmp))


def _pptx_prep_body(tf: Any) -> None:
    """Make a body text frame wrap + shrink-to-fit instead of overflowing."""
    from pptx.enum.text import MSO_AUTO_SIZE

    tf.word_wrap = True
    try:
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    except Exception:  # pragma: no cover - some templates reject it
        pass


def _pptx_fill_para(para: Any, runs: list[Run]) -> None:
    """Render styled runs onto one bullet paragraph (bold/italic/code/link)."""
    for r in runs:
        text, bold, italic, code, href, _img = _run_parts(r)
        run = para.add_run()
        run.text = text
        if bold:
            run.font.bold = True
        if italic:
            run.font.italic = True
        if code:
            run.font.name = "Consolas"
        if href:
            try:
                run.hyperlink.address = href
            except Exception:  # pragma: no cover - defensive
                pass


def _pptx_content_slides(
    prs: Any,
    title: str,
    items: list[tuple[list[Run], int]],
    tables: list[Block],
    notes: str,
) -> None:
    """Emit bullet slide(s) (+ continuation slides) then any table slides."""
    chunks = [
        items[i : i + _PPTX_MAX_ITEMS]
        for i in range(0, max(len(items), 1), _PPTX_MAX_ITEMS)
    ]
    first_slide = None
    for ci, chunk in enumerate(chunks):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title if ci == 0 else f"{title} (cont.)"
        first_slide = first_slide or slide
        body = slide.placeholders[1].text_frame
        _pptx_prep_body(body)
        for idx, (runs, level) in enumerate(chunk or [([Run("", False, False)], 0)]):
            para = body.paragraphs[0] if idx == 0 else body.add_paragraph()
            para.level = min(level, 4)
            _pptx_fill_para(para, runs)

    for tb in tables:
        _pptx_table_slide(prs, f"{title} (cont.)", tb)

    if notes and first_slide is not None:
        try:
            first_slide.notes_slide.notes_text_frame.text = notes
        except Exception:  # pragma: no cover - defensive
            pass


def _pptx_table_slide(prs: Any, title: str, b: Block) -> None:
    """Render a table Block as a REAL pptx table on its own slide."""
    from pptx.util import Emu, Inches

    try:
        layout = prs.slide_layouts[5]  # "Title Only" in the default template
    except IndexError:  # pragma: no cover
        layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    try:
        slide.shapes.title.text = title
    except AttributeError:  # pragma: no cover
        pass

    rows = b.rows
    n_rows = len(rows)
    n_cols = max(len(r) for r in rows)
    left, top = Inches(0.5), Inches(1.5)
    width = prs.slide_width - Emu(2 * Inches(0.5))
    height = Inches(0.4) * n_rows
    tbl = slide.shapes.add_table(n_rows, n_cols, left, top, width, height).table
    for ri, row in enumerate(rows):
        for ci in range(n_cols):
            tbl.cell(ri, ci).text = row[ci] if ci < len(row) else ""


def _pptx_sections(
    blocks: list[Block],
) -> list[tuple[str, list[tuple[list[Run], int]], list[Block], str]] | None:
    """Split blocks into ``(title, items, tables, notes)`` sections.

    A level-1 heading or a ``---`` rule starts a new section. ``items`` are
    ``(runs, indent)`` bullet lines; tables are kept as Blocks for real rendering;
    a ``Notes:`` paragraph feeds the slide's speaker notes. Returns ``None`` when
    the content has neither heading nor rule, so the caller keeps the flat deck.
    """
    if not any(
        (b.kind == "heading" and b.level == 1) or b.kind == "hr" for b in blocks
    ):
        return None

    sections: list[tuple[str, list[tuple[list[Run], int]], list[Block], list[str]]] = []

    def cur() -> tuple[str, list, list, list]:
        if not sections:
            sections.append(("", [], [], []))
        return sections[-1]

    for b in blocks:
        if b.kind == "heading" and b.level == 1:
            # An hr immediately followed by a heading is ONE section, titled.
            if sections and sections[-1][0] == "" and not any(sections[-1][1:]):
                sections[-1] = (b.text, sections[-1][1], sections[-1][2], sections[-1][3])
            else:
                sections.append((b.text, [], [], []))
            continue
        if b.kind == "hr":
            sections.append(("", [], [], []))
            continue

        _title, items, tables, notes = cur()
        if b.kind in ("bullet", "numbered"):
            items.append((b.runs, b.level))
        elif b.kind == "table":
            tables.append(b)
        elif b.kind == "code":
            for code_line in b.text.split("\n"):
                items.append(([Run(code_line, code=True)], 1))
        elif b.kind == "paragraph" and b.text.startswith("Notes:"):
            notes.append(b.text[len("Notes:") :].strip())
        else:  # paragraph / sub-heading
            items.append((b.runs, 0))

    # A trailing '---' (footer rule) must not yield a blank slide.
    while sections and sections[-1][0] == "" and not any(sections[-1][1:]):
        sections.pop()
    if not sections:
        return None
    return [(t, it, tb, "\n".join(nt)) for (t, it, tb, nt) in sections]


# --- pdf -----------------------------------------------------------------------


_PDF_HEADING_SIZES = {1: 20, 2: 16, 3: 14, 4: 12}


def _latin1(text: str) -> str:
    # Core fonts are Latin-1 only; replace anything outside it so fpdf2 cannot crash.
    return text.encode("latin-1", "replace").decode("latin-1")


#: Bundled DejaVu TTFs (Bitstream Vera license) for FULL-UNICODE PDF output —
#: accents, Cyrillic, Greek, symbols. When present, PDFs keep real unicode;
#: when missing (stripped install), we fall back to the core Latin-1 fonts with
#: the historical 'replace' sanitiser so nothing ever crashes.
_FONT_DIR = Path(__file__).resolve().parent / "fonts"


def _pdf_fonts(pdf: Any) -> tuple[str, str, Any]:
    """Register unicode fonts on ``pdf``; return (sans, mono, sanitize)."""
    try:
        sans = _FONT_DIR / "DejaVuSans.ttf"
        bold = _FONT_DIR / "DejaVuSans-Bold.ttf"
        mono = _FONT_DIR / "DejaVuSansMono.ttf"
        if sans.is_file() and bold.is_file() and mono.is_file():
            pdf.add_font("DJSans", "", str(sans))
            pdf.add_font("DJSans", "B", str(bold))
            pdf.add_font("DJMono", "", str(mono))
            return "DJSans", "DJMono", lambda s: s  # true unicode — no mangling
    except Exception:  # noqa: BLE001 — font trouble must never break a write
        pass
    return "Helvetica", "Courier", _latin1


def _pdf_usable(pdf: Any) -> float:
    return pdf.w - pdf.l_margin - pdf.r_margin


def _prebreak(pdf: Any, text: str, max_w: float) -> str:
    """Hard-break any single token wider than ``max_w`` so wrapping can't fail.

    fpdf2's ``multi_cell`` raises when one unbreakable word is wider than the
    cell; we slice such words across newlines (which multi_cell honours) up
    front. Requires the current font to already be set (widths depend on it).
    """
    if max_w <= 0:
        return text
    out: list[str] = []
    for word in text.split(" "):
        if not word or pdf.get_string_width(word) <= max_w:
            out.append(word)
            continue
        chunks: list[str] = []
        cur = ""
        for ch in word:
            if cur and pdf.get_string_width(cur + ch) > max_w:
                chunks.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            chunks.append(cur)
        out.append("\n".join(chunks))
    return " ".join(out)


def _write_pdf(p: Path, content: Any) -> None:
    """Compose a PDF in memory, then write atomically.

    Building in memory (``pdf.output()`` -> bytes) means a layout failure never
    leaves a truncated file. If a render still trips an ``FPDFException`` we
    retry smaller, then as hard-chunked plain text, so SOME valid file always
    lands rather than none.
    """
    from fpdf.errors import FPDFException

    data: bytes | None = None
    for scale, plain in ((1.0, False), (0.7, False), (0.5, True)):
        try:
            pdf = _compose_pdf(content, scale, plain)
            data = bytes(pdf.output())
            break
        except FPDFException:
            continue
    if data is None:  # last resort: a minimal but valid one-page document
        pdf = _compose_pdf(" ", 0.5, True)
        data = bytes(pdf.output())
    with _atomic(p) as tmp:
        Path(tmp).write_bytes(data)


def _compose_pdf(content: Any, scale: float, plain: bool) -> Any:
    from fpdf import FPDF

    pdf = FPDF()
    sans, mono, clean = _pdf_fonts(pdf)
    pdf.add_page()
    if plain or not isinstance(content, str):
        pdf.set_font(sans, size=max(6, int(12 * scale)))
        text = clean(_as_text(content))
        if not text.strip():
            text = " "
        pdf.multi_cell(0, 8 * scale, _prebreak(pdf, text, _pdf_usable(pdf) - 1))
        return pdf
    _pdf_render(pdf, parse_markdown(content), sans, mono, clean, scale)
    return pdf


def _pdf_render(
    pdf: Any, blocks: list[Block], sans: str, mono: str, clean: Any, scale: float = 1.0
) -> None:
    if not blocks:  # keep the empty-content page valid, as before
        pdf.set_font(sans, size=12)
        pdf.multi_cell(0, 8, " ")
        return
    number = 0  # running counter for consecutive numbered items
    for b in blocks:
        if b.kind != "numbered":
            number = 0
        usable = _pdf_usable(pdf)
        if b.kind == "heading":
            size = max(7, int(_PDF_HEADING_SIZES.get(b.level, 12) * scale))
            pdf.set_font(sans, "B", size)
            pdf.multi_cell(0, size * 0.5 + 2, _prebreak(pdf, clean(b.text) or " ", usable - 1))
            pdf.ln(2)
        elif b.kind == "bullet":
            pdf.set_font(sans, size=max(7, int(11 * scale)))
            indent = 5 * (b.level + 1)
            pdf.set_x(pdf.l_margin + indent)
            pdf.multi_cell(0, 6, _prebreak(pdf, "- " + clean(b.text), usable - indent - 1))
        elif b.kind == "numbered":
            number += 1
            pdf.set_font(sans, size=max(7, int(11 * scale)))
            indent = 5 * (b.level + 1)
            pdf.set_x(pdf.l_margin + indent)
            pdf.multi_cell(
                0, 6, _prebreak(pdf, f"{number}. " + clean(b.text), usable - indent - 1)
            )
        elif b.kind == "code":
            pdf.set_font(mono, size=max(6, int(10 * scale)))
            pdf.set_fill_color(235, 235, 235)
            for line in b.text.split("\n"):
                pdf.multi_cell(0, 5, _prebreak(pdf, clean(line) or " ", usable - 1), fill=True)
            pdf.ln(2)
        elif b.kind == "table":
            _pdf_table(pdf, b.rows, b.aligns, sans, clean, scale)
        elif b.kind == "hr":
            y = pdf.get_y() + 2
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.set_y(y + 3)
        else:  # paragraph
            pdf.set_font(sans, size=max(7, int(11 * scale)))
            pdf.multi_cell(0, 6, _prebreak(pdf, clean(b.text) or " ", usable - 1))
            pdf.ln(1)


_PDF_ALIGN = {"left": "LEFT", "right": "RIGHT", "center": "CENTER"}


def _pdf_table(
    pdf: Any, rows: list[list[str]], aligns: list[str], sans: str, clean: Any, scale: float
) -> None:
    cols = max(len(r) for r in rows)
    pdf.set_font(sans, size=max(7, int(10 * scale)))
    if not hasattr(pdf, "table"):  # older fpdf2 -> hand-wrapped fallback
        _pdf_table_fallback(pdf, rows, cols, sans, clean, scale)
        pdf.ln(2)
        return
    text_align = tuple(
        _PDF_ALIGN.get(aligns[i] if i < len(aligns) else "", "LEFT") for i in range(cols)
    )
    try:
        # fpdf2's native table WRAPS long cell text, sizes columns, and repeats
        # the heading row after a page break — none of which the old fixed-width
        # ``cell`` grid did (it clipped and mis-aligned on overflow).
        with pdf.table(text_align=text_align, first_row_as_headings=True) as table:
            for row in rows:
                trow = table.row()
                for ci in range(cols):
                    trow.cell(clean(row[ci]) if ci < len(row) else "")
    except Exception:  # noqa: BLE001 — any table quirk degrades to the fallback
        _pdf_table_fallback(pdf, rows, cols, sans, clean, scale)
    pdf.ln(2)


def _pdf_table_fallback(
    pdf: Any, rows: list[list[str]], cols: int, sans: str, clean: Any, scale: float
) -> None:
    """Wrapping grid for fpdf2 builds without ``pdf.table`` (multi_cell per cell)."""
    width = _pdf_usable(pdf) / max(cols, 1)
    for ri, row in enumerate(rows):
        pdf.set_font(sans, "B" if ri == 0 else "", max(7, int(10 * scale)))
        x0, y0 = pdf.get_x(), pdf.get_y()
        max_y = y0
        for ci in range(cols):
            cell = clean(row[ci]) if ci < len(row) else ""
            pdf.set_xy(x0 + ci * width, y0)
            pdf.multi_cell(width, 6, _prebreak(pdf, cell, width - 1), border=1)
            max_y = max(max_y, pdf.get_y())
        pdf.set_xy(x0, max_y)


# --- csv / text ------------------------------------------------------------------


def _write_csv(p: Path, content: Any) -> None:
    with _atomic(p) as tmp:
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if isinstance(content, (list, tuple)):
                for row in content:
                    if isinstance(row, (list, tuple)):
                        writer.writerow(list(row))
                    else:
                        writer.writerow([row])
            else:
                for line in _as_lines(content):
                    writer.writerow([line])


def _write_text(p: Path, content: Any) -> None:
    _atomic_text(p, _as_text(content))


# --- html ----------------------------------------------------------------------


_HTML_HEAD = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body { font-family: -apple-system, "Segoe UI", Arial, sans-serif; color: #1a1a1a;
       background: #ffffff; max-width: 800px; margin: 2rem auto; padding: 0 1rem;
       line-height: 1.5; }
h1, h2, h3, h4 { line-height: 1.25; }
pre { background: #f4f4f4; padding: 0.75rem; overflow-x: auto; }
code, pre { font-family: Consolas, "Courier New", monospace; font-size: 0.9em; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #999; padding: 0.35em 0.6em; text-align: left; }
th { background: #f0f0f0; }
img { max-width: 100%; }
hr { border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }
</style>
</head>
<body>
"""

_HTML_FOOT = "\n</body>\n</html>\n"


def _write_html(p: Path, content: Any) -> None:
    if isinstance(content, str):
        sniff = content.lstrip().lower()
        if sniff.startswith("<!doctype") or sniff.startswith("<html"):
            _atomic_text(p, content)  # already a full page
            return
        body = _html_render(parse_markdown(content))
    elif isinstance(content, (list, tuple)) and any(
        isinstance(r, (list, tuple)) for r in content
    ):
        rows = [
            [str(c) for c in r] if isinstance(r, (list, tuple)) else [str(r)]
            for r in content
        ]
        body = _html_table(rows, [])
    else:
        body = _html_render(parse_markdown(_as_text(content)))
    _atomic_text(p, _HTML_HEAD + body + _HTML_FOOT)


def _runs_html(runs: list[Run]) -> str:
    parts: list[str] = []
    for r in runs:
        text, bold, italic, code, href, image = _run_parts(r)
        if image and href:
            src = _html_mod.escape(href, quote=True)
            alt = _html_mod.escape(text)
            parts.append(f'<img src="{src}" alt="{alt}">')
            continue
        chunk = _html_mod.escape(text)
        if code:
            chunk = f"<code>{chunk}</code>"
        if bold:
            chunk = f"<strong>{chunk}</strong>"
        if italic:
            chunk = f"<em>{chunk}</em>"
        if href:
            chunk = f'<a href="{_html_mod.escape(href, quote=True)}">{chunk}</a>'
        parts.append(chunk)
    return "".join(parts)


def _html_align_attr(align: str) -> str:
    return f' style="text-align:{align}"' if align in ("center", "right", "left") else ""


def _html_table(rows: list[list[str]], aligns: list[str]) -> str:
    parts = ["<table>"]
    for ri, row in enumerate(rows):
        tag = "th" if ri == 0 else "td"
        cells = "".join(
            f"<{tag}{_html_align_attr(aligns[ci] if ci < len(aligns) else '')}>"
            f"{_html_mod.escape(c)}</{tag}>"
            for ci, c in enumerate(row)
        )
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def _html_lists(items: list[Block]) -> str:
    """Render a run of list Blocks with VALID nesting.

    A deeper item's sublist is wrapped in its own ``<li>`` (``<li><ul>…</ul></li>``)
    rather than the old invalid ``<ul><ul>``; ``ul`` and ``ol`` open/close
    independently by tracking each item's kind at each level.
    """
    pos = 0

    def render(level: int) -> str:
        nonlocal pos
        out: list[str] = []
        while pos < len(items) and items[pos].level >= level:
            kind = items[pos].kind
            tag = "ul" if kind == "bullet" else "ol"
            out.append(f"<{tag}>")
            while (
                pos < len(items)
                and items[pos].level >= level
                and items[pos].kind == kind
            ):
                if items[pos].level > level:
                    # A deeper run is a child list — wrap it in its OWN <li>
                    # (valid ``<li><ul>…</ul></li>`` instead of the old
                    # invalid ``<ul><ul>``), independent of ul/ol kind.
                    out.append(f"<li>{render(items[pos].level)}</li>")
                    continue
                out.append(f"<li>{_runs_html(items[pos].runs)}</li>")
                pos += 1
            out.append(f"</{tag}>")
        return "".join(out)

    return render(items[0].level)


def _html_render(blocks: list[Block]) -> str:
    out: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind in ("bullet", "numbered"):
            items: list[Block] = []
            while i < len(blocks) and blocks[i].kind in ("bullet", "numbered"):
                items.append(blocks[i])
                i += 1
            out.append(_html_lists(items))
            continue
        if b.kind == "heading":
            out.append(f"<h{b.level}>{_runs_html(b.runs)}</h{b.level}>")
        elif b.kind == "code":
            out.append(f"<pre><code>{_html_mod.escape(b.text)}</code></pre>")
        elif b.kind == "table":
            out.append(_html_table(b.rows, b.aligns))
        elif b.kind == "hr":
            out.append("<hr>")
        else:  # paragraph
            out.append(f"<p>{_runs_html(b.runs)}</p>")
        i += 1
    return "\n".join(out)
