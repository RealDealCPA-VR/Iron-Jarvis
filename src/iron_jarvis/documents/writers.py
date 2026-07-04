"""Document writers.

``write_document(path, content, *, kind=None)`` creates a real file on disk,
choosing the format from the path suffix (or ``kind``, which overrides it).

String content is markdown-aware: it is parsed by
:mod:`iron_jarvis.documents.markdown` into blocks (headings, bullets, numbered
lists, code fences, pipe tables, ``---`` rules, ``**bold**``/``*italic*`` runs)
which the rich writers render natively. Plain text with no markers simply
becomes paragraphs, so flat strings keep working everywhere.

* ``.docx`` -> python-docx: real Heading/List styles, real tables, monospace
  code, bold/italic runs. Non-string content: one paragraph per line.
* ``.xlsx`` -> openpyxl: a ``list[list]`` writes rows; a str writes one cell per
  line in column A; ``{"sheets": {name: rows}}`` writes one worksheet per key
  with a bolded/frozen header row and sized columns. ``=...`` strings stay
  formulas.
* ``.pptx`` -> python-pptx: each ``# `` heading or ``---`` starts a new slide
  (title slide + one slide per section); without sections, the legacy
  title-plus-bullet-slide deck is produced.
* ``.pdf``  -> fpdf2: sized bold headings, 11pt paragraphs, indented bullets,
  bordered table grids, Courier code on grey fill (Latin-1 sanitised so fpdf2
  never crashes on non-Latin-1 characters).
* ``.html/.htm`` -> standalone HTML with inline CSS from the same blocks
  (content that is already a full HTML page is passed through verbatim).
* ``.csv``  -> stdlib csv from a ``list[list]`` or from text lines.
* ``.txt/.md`` and anything else -> UTF-8 text.

Parent directories are created as needed; the written :class:`Path` is returned.
"""

from __future__ import annotations

import csv
import html as _html_mod
import re
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
    else:
        _write_text(p, content)
    return p


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


# --- docx ----------------------------------------------------------------------


def _write_docx(p: Path, content: Any) -> None:
    import docx

    doc = docx.Document()
    if isinstance(content, str):
        _docx_render(doc, parse_markdown(content))
    else:
        for line in _as_lines(content):
            doc.add_paragraph(line)
    doc.save(str(p))


def _docx_runs(paragraph: Any, runs: list[Run]) -> None:
    for text, bold, italic in runs:
        run = paragraph.add_run(text)
        run.bold = bold
        run.italic = italic


def _docx_styled_paragraph(doc: Any, style: str, fallback: str) -> Any:
    try:
        return doc.add_paragraph(style=style)
    except KeyError:  # style missing from the template -> nearest base style
        return doc.add_paragraph(style=fallback)


def _docx_render(doc: Any, blocks: list[Block]) -> None:
    for b in blocks:
        if b.kind == "heading":
            doc.add_heading(b.text, level=b.level)
        elif b.kind == "bullet":
            style = "List Bullet" if b.level == 0 else "List Bullet 2"
            _docx_runs(_docx_styled_paragraph(doc, style, "List Bullet"), b.runs)
        elif b.kind == "numbered":
            style = "List Number" if b.level == 0 else "List Number 2"
            _docx_runs(_docx_styled_paragraph(doc, style, "List Number"), b.runs)
        elif b.kind == "code":
            for line in b.text.split("\n"):
                run = doc.add_paragraph().add_run(line)
                run.font.name = "Consolas"
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
                    if ri == 0:  # header row
                        for para in cell.paragraphs:
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
    wb.save(str(p))


def _xlsx_fill(ws: Any, rows: Any) -> None:
    """Fill one worksheet; bold+freeze a header row and size the columns."""
    if isinstance(rows, (list, tuple)):
        norm: list[list[Any]] = [
            [("" if c is None else c) for c in row]
            if isinstance(row, (list, tuple))
            else [row]
            for row in rows
        ]
    else:
        norm = [[line] for line in _as_lines(rows)]
    for row in norm:
        ws.append(row)

    header = (
        len(norm) >= 2
        and bool(norm[0])
        and all(isinstance(c, str) and not c.startswith("=") for c in norm[0])
    )
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


def _write_pptx(p: Path, content: Any) -> None:
    import pptx

    sections = _pptx_sections(parse_markdown(content)) if isinstance(content, str) else None

    prs = pptx.Presentation()

    if sections is None:
        # Legacy flat deck: title slide + one bullet slide with every line.
        lines = [ln for ln in _as_lines(content)]
        title = lines[0] if lines and lines[0].strip() else "Document"

        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = title
        if len(title_slide.placeholders) > 1:
            title_slide.placeholders[1].text = "Generated by Iron Jarvis"

        bullet_slide = prs.slides.add_slide(prs.slide_layouts[1])
        bullet_slide.shapes.title.text = title
        body = bullet_slide.placeholders[1].text_frame
        first, *rest = lines or [""]
        body.text = first
        for line in rest:
            body.add_paragraph().text = line
    else:
        # Sectioned deck: title slide + one slide per '# '/'---' section.
        title_slide = prs.slides.add_slide(prs.slide_layouts[0])
        title_slide.shapes.title.text = sections[0][0] or "Document"
        if len(title_slide.placeholders) > 1:
            title_slide.placeholders[1].text = "Generated by Iron Jarvis"

        for title, items in sections:
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            slide.shapes.title.text = title or "Section"
            body = slide.placeholders[1].text_frame
            for idx, (text, level) in enumerate(items or [("", 0)]):
                if idx == 0:
                    body.text = text
                    para = body.paragraphs[0]
                else:
                    para = body.add_paragraph()
                    para.text = text
                para.level = min(level, 4)

    prs.save(str(p))


def _pptx_sections(
    blocks: list[Block],
) -> list[tuple[str, list[tuple[str, int]]]] | None:
    """Split blocks into ``(title, [(line, indent)])`` sections.

    A level-1 heading or a ``---`` rule starts a new section. Returns ``None``
    when the content has neither, so the caller keeps today's flat deck.
    """
    if not any(
        (b.kind == "heading" and b.level == 1) or b.kind == "hr" for b in blocks
    ):
        return None

    sections: list[tuple[str, list[tuple[str, int]]]] = []
    for b in blocks:
        if b.kind == "heading" and b.level == 1:
            # An hr immediately followed by a heading is ONE section, titled.
            if sections and sections[-1][0] == "" and not sections[-1][1]:
                sections[-1] = (b.text, sections[-1][1])
            else:
                sections.append((b.text, []))
            continue
        if b.kind == "hr":
            sections.append(("", []))
            continue
        if not sections:  # leading content before any marker
            sections.append(("", []))
        lines = sections[-1][1]
        if b.kind in ("bullet", "numbered"):
            lines.append((b.text, b.level))
        elif b.kind == "table":
            for row in b.rows:
                lines.append((" | ".join(row), 0))
        elif b.kind == "code":
            for code_line in b.text.split("\n"):
                lines.append((code_line, 1))
        else:  # paragraph / sub-heading
            lines.append((b.text, 0))
    # A trailing '---' (footer rule) must not yield a blank slide.
    while sections and sections[-1][0] == "" and not sections[-1][1]:
        sections.pop()
    return sections or None


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


def _write_pdf(p: Path, content: Any) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    sans, mono, clean = _pdf_fonts(pdf)
    pdf.add_page()
    if isinstance(content, str):
        _pdf_render(pdf, parse_markdown(content), sans, mono, clean)
    else:
        text = _as_text(content)
        safe = clean(text)
        if not safe.strip():
            safe = " "
        pdf.set_font(sans, size=12)
        pdf.multi_cell(0, 8, safe)
    pdf.output(str(p))


def _pdf_render(pdf: Any, blocks: list[Block], sans: str, mono: str, clean: Any) -> None:
    if not blocks:  # keep the empty-content page valid, as before
        pdf.set_font(sans, size=12)
        pdf.multi_cell(0, 8, " ")
        return
    number = 0  # running counter for consecutive numbered items
    for b in blocks:
        if b.kind != "numbered":
            number = 0
        if b.kind == "heading":
            size = _PDF_HEADING_SIZES.get(b.level, 12)
            pdf.set_font(sans, "B", size)
            pdf.multi_cell(0, size * 0.5 + 2, clean(b.text) or " ")
            pdf.ln(2)
        elif b.kind == "bullet":
            pdf.set_font(sans, size=11)
            pdf.set_x(pdf.l_margin + 5 * (b.level + 1))
            pdf.multi_cell(0, 6, "- " + clean(b.text))
        elif b.kind == "numbered":
            number += 1
            pdf.set_font(sans, size=11)
            pdf.set_x(pdf.l_margin + 5 * (b.level + 1))
            pdf.multi_cell(0, 6, f"{number}. " + clean(b.text))
        elif b.kind == "code":
            pdf.set_font(mono, size=10)
            pdf.set_fill_color(235, 235, 235)
            for line in b.text.split("\n"):
                pdf.multi_cell(0, 5, clean(line) or " ", fill=True)
            pdf.ln(2)
        elif b.kind == "table":
            _pdf_table(pdf, b.rows, sans, clean)
        elif b.kind == "hr":
            y = pdf.get_y() + 2
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.set_y(y + 3)
        else:  # paragraph
            pdf.set_font(sans, size=11)
            pdf.multi_cell(0, 6, clean(b.text) or " ")
            pdf.ln(1)


def _pdf_table(pdf: Any, rows: list[list[str]], sans: str, clean: Any) -> None:
    cols = max(len(r) for r in rows)
    width = (pdf.w - pdf.l_margin - pdf.r_margin) / max(cols, 1)
    for ri, row in enumerate(rows):
        pdf.set_font(sans, "B" if ri == 0 else "", 10)
        for ci in range(cols):
            cell = row[ci] if ci < len(row) else ""
            pdf.cell(width, 7, clean(cell), border=1)
        pdf.ln(7)
    pdf.ln(2)


# --- csv / text ------------------------------------------------------------------


def _write_csv(p: Path, content: Any) -> None:
    with open(p, "w", newline="", encoding="utf-8") as f:
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
    p.write_text(_as_text(content), encoding="utf-8")


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
            p.write_text(content, encoding="utf-8")  # already a full page
            return
        body = _html_render(parse_markdown(content))
    elif isinstance(content, (list, tuple)) and any(
        isinstance(r, (list, tuple)) for r in content
    ):
        rows = [
            [str(c) for c in r] if isinstance(r, (list, tuple)) else [str(r)]
            for r in content
        ]
        body = _html_table(rows)
    else:
        body = _html_render(parse_markdown(_as_text(content)))
    p.write_text(_HTML_HEAD + body + _HTML_FOOT, encoding="utf-8")


def _runs_html(runs: list[Run]) -> str:
    parts: list[str] = []
    for text, bold, italic in runs:
        chunk = _html_mod.escape(text)
        if bold:
            chunk = f"<strong>{chunk}</strong>"
        if italic:
            chunk = f"<em>{chunk}</em>"
        parts.append(chunk)
    return "".join(parts)


def _html_table(rows: list[list[str]]) -> str:
    parts = ["<table>"]
    for ri, row in enumerate(rows):
        tag = "th" if ri == 0 else "td"
        cells = "".join(f"<{tag}>{_html_mod.escape(c)}</{tag}>" for c in row)
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def _html_list(tag: str, items: list[Block]) -> str:
    parts = [f"<{tag}>"]
    depth = 0
    for it in items:
        while depth < it.level:
            parts.append(f"<{tag}>")
            depth += 1
        while depth > it.level:
            parts.append(f"</{tag}>")
            depth -= 1
        parts.append(f"<li>{_runs_html(it.runs)}</li>")
    parts.extend(f"</{tag}>" for _ in range(depth))
    parts.append(f"</{tag}>")
    return "\n".join(parts)


def _html_render(blocks: list[Block]) -> str:
    out: list[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind in ("bullet", "numbered"):
            tag = "ul" if b.kind == "bullet" else "ol"
            items: list[Block] = []
            while i < len(blocks) and blocks[i].kind == b.kind:
                items.append(blocks[i])
                i += 1
            out.append(_html_list(tag, items))
            continue
        if b.kind == "heading":
            out.append(f"<h{b.level}>{_runs_html(b.runs)}</h{b.level}>")
        elif b.kind == "code":
            out.append(f"<pre><code>{_html_mod.escape(b.text)}</code></pre>")
        elif b.kind == "table":
            out.append(_html_table(b.rows))
        elif b.kind == "hr":
            out.append("<hr>")
        else:  # paragraph
            out.append(f"<p>{_runs_html(b.runs)}</p>")
        i += 1
    return "\n".join(out)
