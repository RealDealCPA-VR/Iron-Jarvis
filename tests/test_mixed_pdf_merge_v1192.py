"""The mixed-PDF transcript is MERGED into the extraction, never substituted.

THE DEFECT (found in the 2026-08-20 review, shipped in v1.176.0):
``ocr_if_unreadable`` ended with ``return (text or extracted_text), note``. For
a MIXED document — native text with a scanned page stapled in, the exact shape
v1.176.0's per-page classifier exists to FIND — ``ocr_pdf`` transcribes only the
scanned pages, and that partial transcript then replaced the WHOLE extraction.
A 20-page return with one scanned K-1 came back as that K-1 alone: 19 pages of
text the app could read the day before vanished from ``read_document``,
``convert_document``, batch extraction and — the expensive one —
``redact_scan``, whose PII candidate list then omitted the SSN on page 1 without
saying a single page had been excluded.

Strictly worse than the blindness it replaced, and a direct violation of the
module's own asymmetry rule: a classifier may make the app read MORE of a
client's document, never less.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.documents import ocr as _ocr
from iron_jarvis.documents.ocr import (
    OCR_MARK,
    merge_transcript,
    ocr_if_unreadable,
    transcript_pages,
)
from iron_jarvis.documents.readers import extract_text

from .test_mixed_pdf_routing_v1176 import _mixed_pdf
from .test_ocr_reach_v1174 import TRANSCRIPT, _cfg, _scanned_pdf, _VisionRouter


@pytest.fixture(autouse=True)
def _clean_cache_roots():
    _ocr._CACHE_ROOTS.clear()
    yield
    _ocr._CACHE_ROOTS.clear()


def _mixed_pdf_with_first_page_ssn(path: Path) -> None:
    """``_mixed_pdf`` with a real SSN in page 1's TEXT LAYER — the shape the PII
    scan has to keep seeing after a stapled-in K-1 is transcribed."""
    import pypdf
    from fpdf import FPDF

    _mixed_pdf(path, native_pages=2, scan_at_end=1)
    cover = path.parent / ".cover.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 7, "Form 1120-S page 1: Taxpayer SSN 987-65-4321")
    pdf.output(str(cover))

    writer = pypdf.PdfWriter()
    for page in pypdf.PdfReader(str(cover)).pages:
        writer.add_page(page)
    for page in pypdf.PdfReader(str(path)).pages:
        writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    cover.unlink()


# ------------------------------------------------------- the reproduction ----


async def test_mixed_document_keeps_its_native_pages_AND_the_transcript(tmp_path):
    """THE regression. Route a mixed file through the shared entry point and
    demand BOTH halves of the document in the answer."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    native = extract_text(pdf)
    assert "SCHEDULE K-1" not in native, "the scan is invisible to extraction"
    assert "Page 1 line 1" in native and "Page 2 line 1" in native

    router = _VisionRouter()
    text, note = await ocr_if_unreadable(
        pdf, native, lambda: router, config=_cfg(tmp_path)
    )

    # The scan was recovered...
    assert TRANSCRIPT in text
    assert "[page 3]" in text
    # ...and NOT at the price of the two native-text pages. Before the fix the
    # transcript replaced the extraction and both of these were gone.
    assert "Page 1 line 1" in text
    assert "Page 2 line 1" in text
    assert "Page 1 line 25" in text and "Page 2 line 25" in text
    # The note keeps its parsed contract and says what it merged.
    assert OCR_MARK in note and "1 of 1 page(s) transcribed" in note
    assert "2 page(s) that were not scanned kept their native text" in note


async def test_a_pii_scan_over_the_merged_text_still_sees_page_one(tmp_path):
    """The highest-consequence half: redact_scan runs its patterns over exactly
    the text this function returns. With the transcript substituted, an SSN on a
    native page was simply not in the string being scanned."""
    from iron_jarvis.documents.redact import scan_text

    pdf = tmp_path / "return.pdf"
    # The SSN goes in the FILE's page-1 text layer, the way a client's return
    # carries it — the merge rebuilds each native page from the document itself.
    _mixed_pdf_with_first_page_ssn(pdf)
    native = extract_text(pdf)
    assert "987-65-4321" in native
    router = _VisionRouter()
    text, _note = await ocr_if_unreadable(
        pdf, native, lambda: router, config=_cfg(tmp_path)
    )
    found = {c["value"] for c in scan_text(text)}
    assert "987-65-4321" in found, "the native page's SSN must survive the merge"
    assert "123-45-6789" in found, "and the transcribed page's SSN too"


async def test_a_wholly_scanned_pdf_is_unchanged_by_the_merge(tmp_path):
    """The common case must stay byte-identical: nothing native to keep, so the
    transcript IS the document and the note gains no merge clause."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    text, note = await ocr_if_unreadable(
        pdf, extract_text(pdf), lambda: router, config=_cfg(tmp_path)
    )
    assert text.strip() == f"[page 1]\n{TRANSCRIPT}"
    assert "merged into the document's own text" not in note


async def test_a_merge_fault_never_costs_the_transcript(tmp_path, monkeypatch):
    """Fail-soft: if the merge raises, the caller still gets the OCR text it
    paid a vision call for — exactly the old behaviour, never an exception."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)

    def _boom(*_a, **_k):
        raise RuntimeError("pypdf exploded")

    monkeypatch.setattr(_ocr, "merge_transcript", _boom)
    router = _VisionRouter()
    text, note = await ocr_if_unreadable(
        pdf, extract_text(pdf), lambda: router, config=_cfg(tmp_path)
    )
    assert TRANSCRIPT in text and OCR_MARK in note


async def test_the_merge_survives_a_cache_hit(tmp_path):
    """A second read serves the transcript from the contract-5 cache, which
    carries labels and no plan — the merge must still put it back in place."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    cfg = _cfg(tmp_path)
    native = extract_text(pdf)
    router = _VisionRouter()
    await ocr_if_unreadable(pdf, native, lambda: router, config=cfg)
    assert router.calls == 1

    text, note = await ocr_if_unreadable(pdf, native, lambda: router, config=cfg)
    assert router.calls == 1, "the second read is free"
    assert TRANSCRIPT in text and "Page 1 line 1" in text
    assert "cached — already transcribed earlier" in note
    assert "kept their native text" in note


# ------------------------------------------------------------ the machinery ---


def test_transcript_pages_parses_the_page_labels():
    parsed = transcript_pages("[page 3]\nK-1 BODY\n\n[page 7]\n8879 BODY")
    assert parsed == {3: "K-1 BODY", 7: "8879 BODY"}
    # An UNLABELED transcript (an image file) is not guessed at.
    assert transcript_pages("just some transcribed words") == {}
    assert transcript_pages("") == {}


def test_merge_refuses_when_it_cannot_place_the_transcript(tmp_path):
    """Every "I cannot merge this" answer is ("", 0) — the caller then returns
    the transcript unchanged rather than losing it."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    # unlabeled transcript
    assert merge_transcript(pdf, "native", "no labels here") == ("", 0)
    # a label naming a page this 3-page file does not have
    assert merge_transcript(pdf, "native", "[page 40]\nBODY") == ("", 0)
    # not a readable PDF at all
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"not a pdf")
    assert merge_transcript(junk, "native", "[page 1]\nBODY") == ("", 0)


def test_merge_keeps_a_transcribed_page_that_also_has_a_text_layer(tmp_path):
    """The asymmetry rule inside the merge: substituting is only safe because
    a routed page has no text of its own. When it DOES, both are kept — read
    more, never less."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    merged, kept = merge_transcript(pdf, extract_text(pdf), "[page 1]\nOCR BODY")
    assert kept == 1, "page 2 is the only page kept purely for its native text"
    assert "OCR BODY" in merged
    assert "Page 1 line 1" in merged, "page 1's own text layer is not discarded"
    assert "Page 2 line 1" in merged


def test_merge_carries_the_readers_file_note_to_the_top(tmp_path):
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    native = "[NOTE: this file is named x.xlsx but its contents are a PDF]\n" + (
        extract_text(pdf)
    )
    merged, kept = merge_transcript(pdf, native, "[page 3]\nK-1 BODY")
    assert merged.startswith("[NOTE: this file is named x.xlsx")
    assert kept == 2 and "K-1 BODY" in merged


def test_pages_come_back_in_document_order(tmp_path):
    """A merged mixed document reads front to back — a transcript pasted on the
    end would put page 3 in front of pages 1-2 for every reader downstream."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    merged, _kept = merge_transcript(pdf, extract_text(pdf), "[page 3]\nK-1 BODY")
    assert merged.index("Page 1 line 1") < merged.index("Page 2 line 1")
    assert merged.index("Page 2 line 1") < merged.index("K-1 BODY")


def test_merge_is_a_pure_function_of_the_file(tmp_path):
    """Sanity: nothing here writes to the document."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    before = Path(pdf).read_bytes()
    merge_transcript(pdf, extract_text(pdf), "[page 3]\nK-1 BODY")
    assert Path(pdf).read_bytes() == before
