"""v1.176.0 — per-PAGE scan routing: the mixed document stops being invisible.

THE MEASURED FAILURE, reproduced here as ``_mixed_pdf``. v1.174.0 taught every
document path to OCR a scan, but it asks ONE question of the WHOLE file:
``looks_scanned_pdf`` returns False the moment the document's text layer clears
80 characters, and it only inspects page ONE for an embedded image. So the
shape that actually lands on this desk — a native-text return with a SCANNED
K-1 (or 8879, or signed engagement letter) stapled in behind it — defeats both
signals at once. Measured before a line of this was written:

    extracted text chars : 3178
    looks_scanned_pdf    : False
    needs_ocr            : False        <- the scanned page is INVISIBLE
    pdf-inspector        : mixed, pages_needing_ocr=[2], in 1.5 ms

No error and no note: the model answers about a return whose K-1 it never saw.
That is v1.174.0's own bug one level down.

THE SAFETY ARGUMENT these tests exist to hold. The classifier is an OPTIONAL
ACCELERANT. It may only ever ADD pages to the plan; it can never subtract. A
classifier that is absent, broken, or simply wrong must be able to make the app
read MORE of a client's document than before — never less. Every test below
that touches the fallback is really testing that asymmetry.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.documents import ocr as _ocr
from iron_jarvis.documents import pdf_classify
from iron_jarvis.documents.ocr import (
    OCR_MARK,
    file_digest,
    load_cached,
    needs_ocr,
    ocr_document,
    ocr_page_plan,
    pdf_page_scan_images,
)
from iron_jarvis.documents.readers import extract_text

from .test_ocr_reach_v1174 import (  # reuse the v1.174.0 rig verbatim
    TRANSCRIPT,
    _cfg,
    _scanned_pdf,
    _text_pdf,
    _VisionRouter,
)


@pytest.fixture(autouse=True)
def _clean_cache_roots():
    _ocr._CACHE_ROOTS.clear()
    yield
    _ocr._CACHE_ROOTS.clear()


def _mixed_pdf(path: Path, *, native_pages: int = 2, scan_at_end: int = 1) -> None:
    """A native-text return with scanned page(s) stapled on the end — the exact
    shape the whole-document heuristic cannot see."""
    import pypdf
    from fpdf import FPDF
    from PIL import Image, ImageDraw

    native = path.parent / f".{path.stem}-native.pdf"
    pdf = FPDF()
    for n in range(native_pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        for i in range(25):
            pdf.cell(
                0, 7, f"Page {n + 1} line {i + 1}: ordinary business income detail"
            )
            pdf.ln()
    pdf.output(str(native))

    scan = path.parent / f".{path.stem}-scan.pdf"
    img = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(img)
    draw.text((80, 100), "SCHEDULE K-1 (Form 1120-S)", fill="black")
    img.save(str(scan), "PDF", resolution=150.0)

    writer = pypdf.PdfWriter()
    for page in pypdf.PdfReader(str(native)).pages:
        writer.add_page(page)
    for _ in range(scan_at_end):
        for page in pypdf.PdfReader(str(scan)).pages:
            writer.add_page(page)
    with open(path, "wb") as fh:
        writer.write(fh)
    native.unlink()
    scan.unlink()


# ------------------------------------------------------- the reproduction ----


def test_the_old_heuristic_is_blind_to_a_mixed_document(tmp_path):
    """Pin the defect itself. If this ever fails, the whole-document heuristic
    grew the ability to see mixed files and this feature's premise changed."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf)
    text = extract_text(pdf)
    assert len(text) > 1000, "the native pages really do carry a text layer"
    assert "SCHEDULE K-1" not in text, "the scan really is absent from the text"
    assert _ocr.looks_scanned_pdf(pdf, text) is False


def test_routing_finds_the_stapled_in_scan(tmp_path):
    """The fix: the scanned page is located, by page, in milliseconds."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf)
    plan = ocr_page_plan(pdf, extract_text(pdf))
    assert plan == (2,), f"expected the 3rd page (0-indexed 2), got {plan}"
    # ...and the shared classifier now says yes where it used to say no.
    assert needs_ocr(pdf, extract_text(pdf)) is True


async def test_mixed_document_transcribes_only_the_scanned_page(tmp_path):
    """End to end: one vision call, spent on the page that needed it."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf)
    router = _VisionRouter()
    text, note = await ocr_document(pdf, router, config=_cfg(tmp_path))

    assert router.calls == 1, "a native-text page must not cost a vision call"
    assert TRANSCRIPT in text
    # Labelled with the REAL page number — "page 1" would send the reader to
    # the wrong page of a 3-page return.
    assert "[page 3]" in text
    assert "1 of 1 page(s) transcribed" in note
    assert "3-page document: 3" in note


async def test_the_cap_is_spent_on_scans_not_on_the_pages_in_front(tmp_path):
    """THE SECOND HALF OF THE WIN. Pre-v1.176.0 the cap took the first N pages,
    so a cap of 2 on a return whose scans start at page 3 transcribed two
    native-text pages and reached NO scan at all. The cap now bounds the
    selection, so it buys scans wherever they sit."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=4, scan_at_end=2)
    router = _VisionRouter()
    text, note = await ocr_document(
        pdf, router, config=_cfg(tmp_path, ocr_max_pages=2)
    )
    assert router.calls == 2
    assert "[page 5]" in text and "[page 6]" in text
    assert "2 of 2 page(s) transcribed" in note


# --------------------------------------------- the asymmetry (never read less) --


def test_a_dead_classifier_never_reduces_what_is_read(tmp_path, monkeypatch):
    """A wholly-scanned PDF must still OCR when the classifier is gone. This is
    the ONE property that makes taking the dependency safe."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)

    monkeypatch.setattr(pdf_classify, "classify", lambda *_a, **_k: None)
    assert pdf_classify.scan_pages(pdf) is None
    assert ocr_page_plan(pdf, "") is None
    assert needs_ocr(pdf, "") is True, "the v1.174.0 heuristic still stands alone"


async def test_a_dead_classifier_still_transcribes_the_whole_scan(
    tmp_path, monkeypatch
):
    """...and the harvest falls back to the first N pages, as before."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    monkeypatch.setattr(pdf_classify, "classify", lambda *_a, **_k: None)
    router = _VisionRouter()
    text, note = await ocr_document(pdf, router, config=_cfg(tmp_path))
    assert router.calls == 1 and TRANSCRIPT in text
    assert OCR_MARK in note


def test_a_wrong_empty_plan_cannot_veto_the_old_heuristic(tmp_path, monkeypatch):
    """A classifier that clears a file the heuristic calls scanned must NOT be
    able to skip the OCR. Empty plan + heuristic yes => still yes."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    monkeypatch.setattr(pdf_classify, "scan_pages", lambda *_a, **_k: ())
    assert ocr_page_plan(pdf, "") == ()
    assert needs_ocr(pdf, "") is True


async def test_an_empty_plan_falls_back_to_harvesting_the_document(
    tmp_path, monkeypatch
):
    """The same asymmetry inside ocr_pdf: an empty selection must not become
    "harvest nothing", it must become "harvest as we did before"."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    monkeypatch.setattr(pdf_classify, "scan_pages", lambda *_a, **_k: ())
    router = _VisionRouter()
    text, _note = await ocr_document(pdf, router, config=_cfg(tmp_path))
    assert router.calls == 1 and TRANSCRIPT in text


# ------------------------------------------------------------- the seam ------


def test_classifier_is_fail_soft_on_everything(tmp_path):
    """None for anything it cannot answer — never an exception."""
    assert pdf_classify.scan_pages(tmp_path / "missing.pdf") is None
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"this is definitely not a pdf")
    assert pdf_classify.scan_pages(junk) is None
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert pdf_classify.scan_pages(empty) is None


def test_a_clean_text_pdf_is_positively_cleared(tmp_path):
    """`()` and `None` are DIFFERENT answers: cleared vs unknown."""
    plain = tmp_path / "plain.pdf"
    _text_pdf(plain)
    assert pdf_classify.scan_pages(plain) == ()
    assert needs_ocr(plain, extract_text(plain)) is False


def test_out_of_range_pages_are_dropped(tmp_path, monkeypatch):
    """A classifier claiming page 900 of a 3-page file is wrong about
    something; the entry is dropped rather than becoming a bad read."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf)

    class _Result:
        pdf_type = "mixed"
        confidence = 0.9
        page_count = 3
        pages_needing_ocr = [2, 900, -1, "x"]

    import pdf_inspector

    monkeypatch.setattr(pdf_inspector, "classify_pdf", lambda *_a: _Result())
    assert pdf_classify.scan_pages(pdf) == (2,)


def test_already_transcribed_text_never_buys_a_second_pass(tmp_path):
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf)
    assert ocr_page_plan(pdf, f"something — {OCR_MARK} (1 of 1)") == ()
    assert needs_ocr(pdf, f"something — {OCR_MARK} (1 of 1)") is False


def test_harvest_selects_pages_and_reports_their_numbers(tmp_path):
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf, native_pages=2, scan_at_end=1)
    blobs, total, numbers = pdf_page_scan_images(pdf, pages=(2,))
    assert total == 3
    assert len(blobs) == 1 and numbers == [3]


# --------------------------------------------------------------- the cache ---


def test_cache_version_bump_rejects_pre_routing_records(tmp_path):
    """A v1 record for a mixed document covered the FIRST N pages — mostly
    native text, missing the scan. Serving it now would freeze this exact
    blindness into the cache permanently."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "ocr").mkdir()
    digest = "0" * 64
    stale = home / "ocr" / f"{digest}.p10.json"
    stale.write_text(
        json.dumps(
            {
                "version": 1,  # pre-routing
                "sha256": digest,
                "max_pages": 10,
                "text": "a transcript that missed the stapled-in K-1",
                "note": "scanned PDF",
            }
        ),
        encoding="utf-8",
    )
    assert load_cached(home, digest, 10) is None


async def test_a_routed_transcript_round_trips_through_the_cache(tmp_path):
    """The second read of the same mixed return costs zero vision calls."""
    pdf = tmp_path / "return.pdf"
    _mixed_pdf(pdf)
    config = _cfg(tmp_path)
    first = _VisionRouter()
    text1, _ = await ocr_document(pdf, first, config=config)
    second = _VisionRouter()
    text2, note2 = await ocr_document(pdf, second, config=config)

    assert first.calls == 1
    assert second.calls == 0, "contract 5: the same bytes are never paid for twice"
    assert text1 == text2
    assert "cached" in note2
    assert load_cached(config.home, file_digest(pdf), 10) is not None


def test_note_keeps_the_parsed_contract(tmp_path):
    """`attachment_rag.ocr_pages_spent` parses "(N of M page(s) transcribed" to
    charge the turn budget. The routing note must not break that regex — and M
    must be the CANDIDATE count, or a 20-page return with 2 scans gets billed
    for 20 vision calls."""
    from iron_jarvis.documents.attachment_rag import ocr_pages_spent

    note = (
        f"scanned PDF — {OCR_MARK} (2 of 2 page(s) transcribed; the scanned "
        "page(s) of a 20-page document: 12, 13)"
    )
    assert ocr_pages_spent(note, "[page 12]\nx\n[page 13]\ny", 10) == 2
