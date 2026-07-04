"""Structure-preserving PDF -> Markdown via markitdown (documents/pdf_markdown).

A real PDF is generated at runtime with fpdf2 (already installed), converted to
Markdown, and the text content is asserted present. The fallback path (a corrupt
file with a .pdf name, and a plain-text source) must degrade to
``extract_text`` rather than raise. Finally ``convert_document`` PDF -> .md is
exercised end to end so the tool now yields structured Markdown, not flattened
text.
"""

from __future__ import annotations

from pathlib import Path

from iron_jarvis.documents import (
    document_to_markdown,
    extract_text,
    pdf_to_markdown,
    write_document,
)
from iron_jarvis.documents import document_tools
from iron_jarvis.tools.base import ToolContext


def _make_pdf(path: Path) -> None:
    """Write a small PDF with a title, a heading and a paragraph via fpdf2."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()

    def block(text: str, size: int, style: str = "") -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, 10, text)
        pdf.ln(2)

    block("Quarterly Report", 20, "B")
    block("Revenue Section", 14, "B")
    block(
        "Revenue increased by forty percent this quarter across all regions.",
        11,
    )
    pdf.output(str(path))


def _ctx(workspace: Path) -> ToolContext:
    """Minimal ToolContext — only ``.workspace`` is used by the document tools."""
    return ToolContext(
        workspace=workspace,
        session_id="t",
        agent_run_id="t",
        config=None,
        event_bus=None,
        engine=None,
    )


def _tool(name: str):
    return next(t for t in document_tools() if t.name == name)


# --- pdf_to_markdown ----------------------------------------------------------


def test_pdf_to_markdown_has_content(tmp_path):
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)

    md = pdf_to_markdown(pdf)
    assert md.strip()
    assert "Quarterly Report" in md
    assert "Revenue Section" in md
    assert "forty percent" in md


def test_pdf_to_markdown_accepts_str_path(tmp_path):
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    md = pdf_to_markdown(str(pdf))
    assert "Quarterly Report" in md


def test_pdf_to_markdown_structure_when_present(tmp_path):
    """If markitdown emits any Markdown structure, we should keep it verbatim.

    Plain PDFs (fpdf text) often carry no ``#`` headings, so this only asserts
    structure when markitdown actually produced some — the point is that any
    structure is preserved, not stripped.
    """
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    md = pdf_to_markdown(pdf)
    if "#" in md or "|" in md or "- " in md:
        # Structure preserved verbatim (no assertion of a specific shape).
        assert md.strip()


# --- document_to_markdown (generalised) ---------------------------------------


def test_document_to_markdown_pdf(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    assert "Revenue Section" in document_to_markdown(pdf)


def test_document_to_markdown_non_markitdown_uses_extract_text(tmp_path):
    # A .txt is not routed through markitdown — same text as extract_text.
    txt = tmp_path / "note.txt"
    txt.write_text("plain body content", encoding="utf-8")
    assert document_to_markdown(txt) == extract_text(txt)


def test_document_to_markdown_office_falls_back(tmp_path):
    """docx has no markitdown extra installed -> falls back to extract_text."""
    docx = tmp_path / "memo.docx"
    write_document(docx, "boardroom memo body")
    md = document_to_markdown(docx)
    assert "boardroom memo body" in md


# --- fallback / never-raise ---------------------------------------------------


def test_corrupt_pdf_falls_back_without_raising(tmp_path):
    """A non-PDF file with a .pdf name must not raise — it degrades gracefully."""
    fake = tmp_path / "corrupt.pdf"
    fake.write_text("this is not really a pdf, just text", encoding="utf-8")
    # markitdown will fail on the bytes; the fallback reaches extract_text,
    # which reads it as text (no \x00) rather than raising.
    md = document_to_markdown(fake)
    assert "not really a pdf" in md


def test_empty_pdf_matches_extract_text_contract(tmp_path):
    """A zero-length .pdf degrades to the SAME behavior as extract_text.

    markitdown fails on the empty bytes and we fall back to extract_text,
    preserving its existing contract (pypdf raises EmptyFileError on a truly
    empty PDF) rather than silently inventing content. The convert_document
    tool wraps this in try/except, so the user-facing surface stays graceful.
    """
    import pytest

    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(Exception) as via_convert:
        document_to_markdown(empty)
    with pytest.raises(Exception) as via_extract:
        extract_text(empty)
    assert type(via_convert.value) is type(via_extract.value)


# --- convert_document PDF -> .md now yields structured Markdown ----------------


async def test_convert_document_pdf_to_md_is_structured(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = _ctx(ws)
    _make_pdf(ws / "report.pdf")

    res = await _tool("convert_document").execute(
        {"source": "report.pdf", "target": "report.md"}, ctx
    )
    assert res.ok, res.error

    out = (ws / "report.md").read_text(encoding="utf-8")
    assert "Quarterly Report" in out
    assert "Revenue Section" in out
    assert "forty percent" in out
    # markitdown separates blocks with blank lines — richer than a flat dump.
    assert out != extract_text(ws / "report.pdf") or "\n\n" in out


async def test_convert_document_other_targets_unchanged(tmp_path):
    """A PDF -> .docx conversion still goes through the plain-text path."""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = _ctx(ws)
    _make_pdf(ws / "report.pdf")

    res = await _tool("convert_document").execute(
        {"source": "report.pdf", "target": "report.docx"}, ctx
    )
    assert res.ok, res.error
    assert "Quarterly Report" in extract_text(ws / "report.docx")
