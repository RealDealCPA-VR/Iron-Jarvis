"""Chat sees scans too (v1.174.0, pair P5).

The wave's evidence folder is 22 PDFs of which ELEVEN are image-only scans.
Attached to chat, every one of them used to arrive as nothing: ``extract_text``
reads a PDF's text layer and a photographed page has none, so the retrieval
block cheerfully announced "0 indexed sections" and the model answered about a
document it had never seen a word of.

These tests pin the fix and the four ways it stays honest:

1. DETECTION SURVIVES THE PAGE MARKERS. ``extract_for_rag`` inserts ``[page N]``
   per page, so a 20-page scan arrives as ~180 chars of pure scaffolding and
   clears ``looks_scanned_pdf``'s 80-char "effectively empty" threshold — the
   LONGER the scan, the more certainly it was declared a normal text PDF.
   ``is_scan_candidate`` strips the markers first.
2. OCR REACHES BOTH LANES, from ONE implementation (``_prepare_attachments``).
   The streaming lane is the one the dashboard runs; a single-lane fix is a fix
   the user never sees.
3. NOTHING IS EVER INVENTED. OCR off, no router, budget spent, provider down,
   or the offline mock ⇒ empty text plus a note that says exactly which.
4. AN UNSEEN IMAGE SAYS SO. Images ride to vision, but the router's vision
   preference is soft: with no vision-capable provider anywhere the adapter
   drops them silently. That now reads like the >8 MB case — "NOT analyzed".

Offline throughout: the router is faked, the scans are generated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn as _ct
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _prepare_attachments,
    _vision_unavailable_reason,
)
from iron_jarvis.documents.attachment_rag import (
    OCR_BUDGET_NOTE,
    OCR_DISABLED_NOTE,
    OCR_NO_ROUTER_NOTE,
    Extraction,
    extract_for_rag,
    extract_for_rag_async,
    honest_scan_note,
    is_scan_candidate,
    ocr_pages_spent,
    rag_block,
    strip_page_marks,
)
from iron_jarvis.documents.ocr import looks_scanned_pdf
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

TRANSCRIPT = "FORM W-2 Wage and Tax Statement\nEmployer: Dewerff LLC\nWages: 84,120.55"


# --------------------------------------------------------------- fixtures ---


def _scanned_pdf(path: Path, pages: int = 1, *, layer: str = "") -> None:
    """A PDF whose every page is ONE embedded photo — no text layer at all.

    The page tint is derived from the FILE NAME so two scans in one test never
    share a sha256: the OCR cache (contract 5) is keyed on the file's BYTES, and
    identical fixtures would make a cache hit look like a budget that held.

    ``layer`` adds a SHORT real text layer — the hybrid page (a stamped header,
    a digital footer) that is still a scan by every measure but no longer a file
    of which "NOTHING was read" is true.
    """
    from PIL import Image
    from fpdf import FPDF

    tint = sum(path.stem.encode()) % 60
    png = path.parent / f"{path.stem}-scan.png"
    Image.new("RGB", (500, 650), (238 - tint, 238, 232)).save(png)
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.image(str(png), x=5, y=5, w=180)
        if layer:
            pdf.set_font("helvetica", size=9)
            pdf.text(8, 8, layer)
    pdf.output(str(path))


class _VisionRouter:
    """Stands in for the platform router's vision path; counts the calls
    because each one is a real vision request in production."""

    def __init__(self, provider: str = "anthropic", text: str = TRANSCRIPT) -> None:
        self.provider = provider
        self.text = text
        self.calls = 0

    async def complete(self, *, system, messages, tools, task_class=None, **kw):
        self.calls += 1
        assert messages and messages[0].images, "OCR must send the page image"
        return RouteResult(LLMResponse(text=self.text), self.provider, "vision-x")


class _BlankRouter:
    """A vision route that ANSWERS every page with whitespace — a blank fax
    cover, a blank reverse side, a local VL model that says nothing. Every call
    is still a real (billed, slow) vision request; ``ocr.py`` drops the page."""

    def __init__(self, real_pages: int = 0, text: str = TRANSCRIPT) -> None:
        self.real_pages = real_pages
        self.text = text
        self.calls = 0

    async def complete(self, *, system, messages, tools, task_class=None, **kw):
        self.calls += 1
        body = self.text if self.calls <= self.real_pages else "   \n  "
        return RouteResult(LLMResponse(text=body), "anthropic", "vision-x")


def _body(attachments, question="what does this say?"):
    return SimpleNamespace(
        attachments=list(attachments),
        messages=[SimpleNamespace(role="user", content=question)],
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


# ------------------------------------------------- 1. detection on markers ---


def test_page_markers_defeat_the_raw_scan_heuristic(tmp_path):
    """The regression that made LONG scans invisible: pure ``[page N]``
    scaffolding is >80 chars, so the raw heuristic calls a 20-page scan a
    normal text PDF."""
    pdf = tmp_path / "long.pdf"
    _scanned_pdf(pdf)
    scaffolding = "".join(f"[page {i}]\n\n" for i in range(1, 21))
    assert len(scaffolding.strip()) > 80  # the whole trap, in one number

    assert looks_scanned_pdf(pdf, scaffolding) is False   # what we must not use
    assert is_scan_candidate(pdf, scaffolding) is True    # what we do use


def test_strip_page_marks_keeps_the_real_words():
    assert strip_page_marks("[page 1]\nWages: 12\n[page 2]\nDone") == (
        "\nWages: 12\n\nDone"
    )
    assert strip_page_marks("") == ""


def test_a_real_text_pdf_is_never_a_scan_candidate(tmp_path):
    from iron_jarvis.documents import write_document

    pdf = tmp_path / "digital.pdf"
    write_document(pdf, "An ordinary digital PDF with a real text layer inside.")
    assert is_scan_candidate(pdf, extract_for_rag(pdf)) is False


def test_scan_candidate_never_raises_on_a_broken_path(tmp_path):
    assert is_scan_candidate(tmp_path / "missing.pdf", "") is False


# ------------------------------------------------------ 2. the async extract --


async def test_scanned_attachment_is_transcribed_and_disclosed(tmp_path):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    got = await extract_for_rag_async(pdf, router=router)
    assert isinstance(got, Extraction)
    assert "FORM W-2" in got.text and "[page 1]" in got.text
    assert got.ocr_used is True
    assert "recovered via OCR" in got.note
    assert got.ocr_pages == 1  # the budget is charged what was actually spent
    assert router.calls == 1


async def test_a_text_pdf_never_costs_a_vision_call(tmp_path):
    from iron_jarvis.documents import write_document

    pdf = tmp_path / "plain.pdf"
    write_document(pdf, "An ordinary digital PDF with a real text layer inside.")
    router = _VisionRouter()
    got = await extract_for_rag_async(pdf, router=router)
    assert "ordinary digital PDF" in got.text
    assert got.note == "" and got.ocr_used is False and got.ocr_pages == 0
    assert router.calls == 0


async def test_ocr_disabled_reads_nothing_and_says_so(tmp_path):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    got = await extract_for_rag_async(pdf, router=router, ocr_enabled=False)
    assert got.text == ""  # NOT the page scaffolding dressed up as content
    assert got.note == OCR_DISABLED_NOTE
    assert router.calls == 0


async def test_no_router_reads_nothing_and_says_so(tmp_path):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    got = await extract_for_rag_async(pdf, router=None)
    assert got.text == "" and got.note == OCR_NO_ROUTER_NOTE


async def test_zero_page_budget_reads_nothing_and_says_so(tmp_path):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    got = await extract_for_rag_async(pdf, router=router, max_ocr_pages=0)
    assert got.text == "" and got.note == OCR_BUDGET_NOTE
    assert router.calls == 0


async def test_the_mock_never_fabricates_a_scanned_return(tmp_path):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter(provider="mock", text="Wages: $1,000,000")
    got = await extract_for_rag_async(pdf, router=router)
    assert got.text == "" and got.ocr_used is False
    assert "mock" in got.note and "fabricated" in got.note
    assert "1,000,000" not in got.note  # the invented number reaches nobody
    assert got.ocr_pages == 1  # an attempt still cost a call


async def test_a_failing_provider_is_a_note_not_an_exception(tmp_path):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)

    class _Down:
        async def complete(self, **kw):
            raise RuntimeError("endpoint down")

    got = await extract_for_rag_async(pdf, router=_Down())
    assert got.text == "" and got.ocr_used is False
    assert "vision" in got.note.lower()


async def test_an_exploding_ocr_call_still_returns_an_extraction(tmp_path, monkeypatch):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)

    async def boom(*a, **kw):
        raise ValueError("pypdf blew up")

    monkeypatch.setattr("iron_jarvis.documents.ocr.ocr_pdf", boom)
    got = await extract_for_rag_async(pdf, router=_VisionRouter())
    assert got.text == "" and "OCR failed" in got.note and "ValueError" in got.note
    assert got.ocr_pages == 1


async def test_page_cap_bounds_the_vision_spend(tmp_path):
    pdf = tmp_path / "big-scan.pdf"
    _scanned_pdf(pdf, pages=4)
    router = _VisionRouter()
    got = await extract_for_rag_async(pdf, router=router, max_ocr_pages=2)
    assert router.calls == 2, "the page cap is the only thing bounding the cost"
    assert got.ocr_pages == 2
    assert "2 of 4" in got.note and "first 2 pages" in got.note


# ------------------------------------------- 2b. FROZEN CONTRACT 5: the cache --
#
# The defect these pin: chat called ``ocr_pdf`` directly, so it neither READ nor
# WROTE <home>/ocr. Measured — the same 3-page scan attached on two consecutive
# turns cost SIX vision calls and the cache directory was never created.


def test_the_extractor_goes_through_the_cached_entry_point():
    """The source-level pin. ``ocr_document`` IS contract 5 (hash the bytes,
    look up, transcribe, store); ``ocr_pdf`` is the uncached inner call, and
    swapping back to it is invisible in every behavioural test that uses a fresh
    home per test."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "iron_jarvis" / "documents" / "attachment_rag.py"
    ).read_text(encoding="utf-8")
    assert "ocr_document(" in src, "chat's OCR must go through the cache"
    assert "ocr_pdf(" not in src, "the uncached inner call is back"


async def test_a_second_turn_on_the_same_scan_costs_nothing(client, tmp_path):
    platform = client.app.state.platform
    pdf = tmp_path / "k1.pdf"
    _scanned_pdf(pdf, pages=3)
    router = _VisionRouter()
    platform.router.complete = router.complete
    d = SimpleNamespace(platform=platform)

    _i, first = await _prepare_attachments(
        d, _body([str(pdf)]), inline_budget=6000, rag_budget=2400, rag_k=6)
    assert router.calls == 3
    assert (Path(platform.config.home) / "ocr").is_dir(), "nothing was cached"

    _i, second = await _prepare_attachments(
        d, _body([str(pdf)], "and the employer?"),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert router.calls == 3, "the follow-up re-paid the whole transcription"
    assert "FORM W-2" in first and "FORM W-2" in second
    assert "cached — already transcribed earlier" in second


async def test_chat_reads_what_the_agent_lane_transcribed(client, tmp_path):
    """Contract 5 is ACROSS tools, not per-lane: a scan ``read_document`` or the
    Documents page already paid for must not be paid for again by chat. The page
    cap is half the key, so this also pins that chat asks for the same cap
    ``ocr_settings`` gives every other path."""
    from iron_jarvis.documents.ocr import ocr_document

    platform = client.app.state.platform
    pdf = tmp_path / "engagement.pdf"
    _scanned_pdf(pdf)
    agent = _VisionRouter()
    text, _note = await ocr_document(pdf, agent, config=platform.config)
    assert agent.calls == 1 and "FORM W-2" in text

    chat = _VisionRouter(text="DIFFERENT — this must never be asked for")
    platform.router.complete = chat.complete
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(pdf)]),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert chat.calls == 0, "chat re-paid a transcription the agent lane owned"
    assert "FORM W-2" in block and "DIFFERENT" not in block


async def test_a_cache_hit_charges_the_turn_budget_nothing(client, tmp_path):
    from iron_jarvis.documents.ocr import ocr_document

    platform = client.app.state.platform
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf, pages=2)
    await ocr_document(pdf, _VisionRouter(), config=platform.config)

    got = await extract_for_rag_async(
        pdf, router=_VisionRouter(), config=platform.config, max_ocr_pages=10)
    assert got.ocr_used is True and "FORM W-2" in got.text
    assert got.ocr_pages == 0, "a cache hit makes no vision calls at all"


# --------------------------------- 2c. the budget charges ATTEMPTED pages -----
#
# ``ocr.py`` makes one vision call per BLOB and drops pages whose response is
# empty. Charging transcribed pages therefore made the worst case — the one the
# budget exists to bound — free. Measured: 40 live calls charged 4.


async def test_blank_responses_are_charged_what_they_cost(tmp_path):
    pdf = tmp_path / "blank.pdf"
    _scanned_pdf(pdf, pages=6)
    router = _BlankRouter()
    got = await extract_for_rag_async(pdf, router=router, max_ocr_pages=6)
    assert router.calls == 6, "every page was a real vision request"
    assert got.text == "" and got.ocr_used is False
    assert got.ocr_pages == 6, "attempted pages, not transcribed ones"


async def test_a_partial_transcript_is_charged_the_whole_attempt(tmp_path):
    pdf = tmp_path / "partial.pdf"
    _scanned_pdf(pdf, pages=6)
    router = _BlankRouter(real_pages=2)
    got = await extract_for_rag_async(pdf, router=router, max_ocr_pages=6)
    assert router.calls == 6
    assert "2 of 6" in got.note and got.ocr_used is True
    assert got.ocr_pages == 6, "4 pages came back empty and were spent anyway"


def test_ocr_pages_spent_reads_the_note_ocr_py_actually_writes():
    """This function is COUPLED to ocr.py's note wording — the only place the
    attempted count survives the call. Pin the exact strings."""
    from iron_jarvis.documents.ocr import OCR_MARK

    done = f"scanned PDF — {OCR_MARK} (3 of 10 page(s) transcribed)"
    assert ocr_pages_spent(done, "[page 1]\nx", 10) == 10
    capped = f"scanned PDF — {OCR_MARK} (2 of 9 page(s) transcribed; only the " \
             "first 2 pages are attempted)"
    assert ocr_pages_spent(capped, "[page 1]\nx\n\n[page 2]\ny", 2) == 2
    empty = ("scanned PDF — the current model returned no transcription; it may "
             "not support vision (connect a vision-capable model and retry)")
    assert ocr_pages_spent(empty, "", 10) == 10
    assert ocr_pages_spent(f"{done} [cached — already transcribed earlier]",
                           "[page 1]\nx", 10) == 0
    # a one-call fatal (the mock guard) is still charged, never zero
    assert ocr_pages_spent("scanned document — only the offline mock", "", 10) == 1


async def test_the_turn_budget_binds_when_every_page_comes_back_empty(
    client, tmp_path, monkeypatch
):
    """The reviewer's probe, shrunk: three 6-page scans against a 12-page turn
    budget. Charging transcribed pages spent 18 vision calls and charged 3."""
    platform = client.app.state.platform
    monkeypatch.setattr(_ct, "_TURN_OCR_PAGES", 12)
    platform.config.ocr_max_pages = 6
    router = _BlankRouter()
    platform.router.complete = router.complete
    paths = []
    for name in ("one", "two", "three"):
        p = tmp_path / f"{name}.pdf"
        _scanned_pdf(p, pages=6)
        paths.append(str(p))
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body(paths),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert router.calls == 12, "the turn budget did not bound the vision spend"
    assert OCR_BUDGET_NOTE in block
    assert block.index("## Attached file: three.pdf") < block.index(OCR_BUDGET_NOTE)


# ------------------------------- 2d. the note matches what actually shipped ---


async def test_a_hybrid_page_never_claims_nothing_was_read(tmp_path):
    """A scan with a small real text layer keeps 1..79 chars AND used to get a
    note ending "so NOTHING in this file was read" — the model told two
    different things about one attachment, in the same block."""
    pdf = tmp_path / "hybrid.pdf"
    _scanned_pdf(pdf, layer="2023 Form 1099-INT")
    raw = extract_for_rag(pdf)
    assert is_scan_candidate(pdf, raw) is True
    assert "1099-INT" in strip_page_marks(raw)

    got = await extract_for_rag_async(pdf, router=None)
    assert "1099-INT" in got.text, "the real text layer still ships"
    assert "NOTHING in this file was read" not in got.note
    assert "only the small text layer below was read" in got.note
    assert "no model is wired to transcribe it" in got.note


async def test_a_pure_scan_still_says_nothing_was_read(tmp_path):
    """The other direction — the absolute wording is kept for the case it is
    true of, or the softened note becomes the new lie."""
    pdf = tmp_path / "pure.pdf"
    _scanned_pdf(pdf)
    got = await extract_for_rag_async(pdf, router=None)
    assert got.text == ""
    assert got.note == OCR_NO_ROUTER_NOTE
    assert "NOTHING in this file was read" in got.note


def test_honest_scan_note_rewrites_only_what_is_untrue():
    assert honest_scan_note(OCR_BUDGET_NOTE) == OCR_BUDGET_NOTE
    partial = honest_scan_note(OCR_BUDGET_NOTE, text="[page 1]\nstamped header")
    assert "NOTHING" not in partial
    assert partial.endswith("attach it on its own to have it transcribed")
    # page scaffolding alone is not a text layer
    assert honest_scan_note(OCR_BUDGET_NOTE, text="[page 1]\n") == OCR_BUDGET_NOTE
    # and a raster image is not a PDF
    img = honest_scan_note(OCR_DISABLED_NOTE, image=True)
    assert "PDF" not in img and "image file" in img


# ------------------------------------------------------- rag_block disclosure --


def test_rag_block_note_rides_under_the_header_only_when_given():
    text = "engagement paragraph. " * 400
    plain = rag_block("big.pdf", text, "engagement?", None)
    assert plain == rag_block("big.pdf", text, "engagement?", None, note="")
    assert "\n[scanned" not in plain

    noted = rag_block("big.pdf", text, "engagement?", None,
                      note="scanned PDF — text recovered via OCR (3 of 9 page(s))")
    lines = noted.splitlines()
    header = next(i for i, ln in enumerate(lines) if ln.startswith("## Attached"))
    assert lines[header + 1] == (
        "[scanned PDF — text recovered via OCR (3 of 9 page(s))]"
    ), "the disclosure rides directly under the header, before any excerpt"


# ---------------------------------------------- 3. the shared lane preparer ---


def test_both_chat_lanes_call_the_shared_preparer():
    """The lock-step pin. Both lanes ran hand-copied attachment loops; the
    streaming one is what the dashboard uses. A mutation deleting either call
    site must fail HERE."""
    rx = re.compile(r"_prepare_attachments\(\s*\n?\s*d, body,")
    turn = (_SRC / "chat_turn.py").read_text(encoding="utf-8")
    stream = (_SRC / "routes" / "chat.py").read_text(encoding="utf-8")
    assert rx.search(turn), "run_chat_turn stopped calling _prepare_attachments"
    assert rx.search(stream), "POST /chat/stream stopped calling it"
    # And neither lane kept a private copy of the old loop.
    for src in (turn, stream):
        assert "extract_for_rag(p)" not in src


async def test_preparer_ocrs_and_discloses(client, tmp_path):
    platform = client.app.state.platform
    pdf = tmp_path / "k1.pdf"
    _scanned_pdf(pdf)
    platform.router.complete = _VisionRouter().complete
    images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(pdf)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert images == []
    assert "## Attached file: k1.pdf" in block
    assert "FORM W-2" in block                 # the scan actually reached the model
    assert "recovered via OCR" in block        # and the method is disclosed


async def test_preparer_is_honest_when_ocr_cannot_run(client, tmp_path):
    platform = client.app.state.platform
    platform.config.ocr_enabled = False
    pdf = tmp_path / "k1.pdf"
    _scanned_pdf(pdf)
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(pdf)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert OCR_DISABLED_NOTE in block
    assert "0 indexed section" not in block  # the old silent lie


async def test_turn_page_budget_is_shared_across_attachments(
    client, tmp_path, monkeypatch
):
    """Per-DOCUMENT caps do not bound a turn: four scans multiply them. The
    third attachment here must be refused honestly, not silently blanked."""
    platform = client.app.state.platform
    monkeypatch.setattr(_ct, "_TURN_OCR_PAGES", 2)
    router = _VisionRouter()
    platform.router.complete = router.complete
    paths = []
    for name in ("a", "b", "c"):
        p = tmp_path / f"{name}.pdf"
        _scanned_pdf(p)
        paths.append(str(p))
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body(paths),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert router.calls == 2, "the turn budget, not the per-doc cap, must bind"
    assert block.count("recovered via OCR") == 2
    assert OCR_BUDGET_NOTE in block
    assert block.index("## Attached file: c.pdf") < block.index(OCR_BUDGET_NOTE)


async def test_preparer_keeps_the_pre_v1174_behaviors(client, tmp_path):
    """Text inline, images to vision, the 4-attachment cap, the >8 MB note and
    the unreadable-file note all survive the move into the shared helper."""
    platform = client.app.state.platform
    small = tmp_path / "note.txt"
    small.write_text("deadline is March 16", encoding="utf-8")
    png = tmp_path / "shot.png"
    from PIL import Image

    Image.new("RGB", (12, 12), (10, 20, 30)).save(png)
    huge = tmp_path / "huge.png"
    huge.write_bytes(b"\x89PNG" + b"\0" * (9 * 1024 * 1024))
    extra = tmp_path / "extra.txt"
    extra.write_text("never reached — the cap is four", encoding="utf-8")
    fifth = tmp_path / "fifth.txt"
    fifth.write_text("FIFTH-ATTACHMENT-MARKER", encoding="utf-8")

    images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform),
        _body([str(small), str(png), str(huge), str(extra), str(fifth)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert "deadline is March 16" in block
    assert images and images[0]["media_type"] == "image/png"
    assert "9 MB exceeds the 8 MB inline-image limit" in block
    assert "FIFTH-ATTACHMENT-MARKER" not in block  # the 4-attachment cap holds


async def test_missing_and_denied_attachments_are_skipped(client, tmp_path):
    platform = client.app.state.platform
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(tmp_path / "gone.txt")]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert block == ""


async def test_big_scan_goes_through_retrieval_with_the_ocr_note(client, tmp_path):
    platform = client.app.state.platform
    pdf = tmp_path / "long.pdf"
    _scanned_pdf(pdf)
    long_text = "[page 1]\n" + ("wages and withholding narrative. " * 400)
    platform.router.complete = _VisionRouter(text=long_text).complete
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(pdf)], "withholding?"),
        inline_budget=500, rag_budget=800, rag_k=3,
    )
    assert "indexed section(s)" in block          # retrieval, not a head clip
    assert "recovered via OCR" in block           # still disclosed on this path
    assert "across 0 indexed" not in block        # the old silent lie


async def test_ocr_max_pages_zero_means_the_default_not_a_refusal(client, tmp_path):
    """``ocr_settings`` treats 0 as "use the default" and clamps 1..ceiling.
    Chat re-derived the value raw, so a config of 0 refused the FIRST attachment
    of the turn with "the budget was already spent on earlier attachments" —
    when there were none."""
    platform = client.app.state.platform
    platform.config.ocr_max_pages = 0
    pdf = tmp_path / "k1.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    platform.router.complete = router.complete
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(pdf)]),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert router.calls == 1
    assert OCR_BUDGET_NOTE not in block
    assert "FORM W-2" in block


async def test_a_negative_page_config_is_clamped_too(client, tmp_path):
    platform = client.app.state.platform
    platform.config.ocr_max_pages = -5
    pdf = tmp_path / "k1.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    platform.router.complete = router.complete
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(pdf)]),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert router.calls == 1 and "FORM W-2" in block


# ------------------- 3b. an image the inline map does not carry ---------------


def _bmp(path: Path) -> None:
    """A raster image whose bytes are unique to its PATH — the OCR cache is
    content-addressed and lives in a process-global root list, so two fixtures
    sharing a sha256 would make one test's transcription serve another's."""
    from PIL import Image

    tint = sum(str(path).encode()) % 200
    Image.new("RGB", (60, 40), (200, tint, 30)).save(path)


async def test_a_bmp_is_transcribed_not_described(client, tmp_path):
    """``.bmp`` is in ``readers._IMAGE_SUFFIXES`` but NOT in the inline vision
    map, so it took the document path and arrived as the literal string
    "[image BMP 60x40, mode RGB]" with no note — an invitation to invent."""
    platform = client.app.state.platform
    bmp = tmp_path / "receipt.bmp"
    _bmp(bmp)
    router = _VisionRouter()
    platform.router.complete = router.complete
    images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(bmp)]),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert images == [], "bmp is not an inline vision media type"
    assert "[image BMP" not in block, "the size sentinel reached the model"
    assert "FORM W-2" in block and "recovered via OCR" in block


async def test_an_unreadable_bmp_says_so_without_calling_it_a_pdf(tmp_path):
    bmp = tmp_path / "receipt.bmp"
    _bmp(bmp)
    got = await extract_for_rag_async(bmp, router=None)
    assert got.text == "", "the size sentinel is not content"
    assert "image file" in got.note and "PDF" not in got.note
    assert "no model is wired to transcribe it" in got.note


async def test_a_bmp_uses_the_same_cache(client, tmp_path):
    platform = client.app.state.platform
    bmp = tmp_path / "receipt.bmp"
    _bmp(bmp)
    router = _VisionRouter()
    platform.router.complete = router.complete
    d = SimpleNamespace(platform=platform)
    for _ in range(2):
        await _prepare_attachments(
            d, _body([str(bmp)]), inline_budget=6000, rag_budget=2400, rag_k=6)
    assert router.calls == 1


# ------------------------------------------------------- 4. unseen images ----


class _Blind:
    provider = "codex-cli"
    model = "gpt"

    def capabilities(self):
        return {"tool_use": True, "vision": False}


class _Seeing:
    provider = "anthropic"
    model = "sonnet"

    def capabilities(self):
        return {"tool_use": True, "vision": True}


def _fleet(platform, monkeypatch, adapters: dict):
    monkeypatch.setattr(platform.router, "_snapshot", lambda: set(adapters))
    monkeypatch.setattr(
        platform.providers, "get", lambda n, m=None: adapters[n]
    )


def test_no_vision_anywhere_is_reported(client, monkeypatch):
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind()})
    reason = _vision_unavailable_reason(SimpleNamespace(platform=platform), "", "")
    assert "NOT seen" in reason and "codex-cli" in reason


def test_one_vision_capable_provider_silences_the_note(client, monkeypatch):
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind(), "anthropic": _Seeing()})
    assert _vision_unavailable_reason(
        SimpleNamespace(platform=platform), "", ""
    ) == "", "the router reroutes to it — a warning here would be the lie"


def test_offline_no_real_provider_says_nothing(client, monkeypatch):
    """Empty availability = the mock path, already disclosed as reason='mock'
    by the TurnReceipt. Warning here would fire on every offline turn."""
    platform = client.app.state.platform
    monkeypatch.setattr(platform.router, "_snapshot", lambda: set())
    assert _vision_unavailable_reason(
        SimpleNamespace(platform=platform), "", ""
    ) == ""


def test_a_broken_probe_never_breaks_the_turn():
    boom = SimpleNamespace(platform=SimpleNamespace())
    assert _vision_unavailable_reason(boom, "", "") == ""


async def test_unseen_image_gets_the_not_analyzed_note(client, tmp_path, monkeypatch):
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind()})
    png = tmp_path / "receipt.png"
    from PIL import Image

    Image.new("RGB", (10, 10), (0, 0, 0)).save(png)
    images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(png)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert images, "the image still rides along — the router may yet reroute"
    assert "## Attached image: receipt.png" in block
    assert "NOT analyzed" in block and "codex-cli" in block


def test_a_vision_provider_with_an_open_circuit_does_not_silence_the_note(
    client, monkeypatch
):
    """``_first_capable`` skips a provider whose circuit is OPEN, so counting it
    as "vision is available" left routing on a blind adapter with no note — the
    exact gap this probe exists to close."""
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind(), "anthropic": _Seeing()})
    monkeypatch.setattr(
        platform.router.health, "allow", lambda p: p != "anthropic"
    )
    reason = _vision_unavailable_reason(SimpleNamespace(platform=platform), "", "")
    assert "NOT seen" in reason and "codex-cli" in reason
    assert "anthropic" not in reason, "an unroutable provider is not 'checked'"


def test_an_open_circuit_everywhere_stays_silent(client, monkeypatch):
    """No routable provider at all is not a vision verdict — it is the offline
    case, and a false "not analyzed" is its own lie."""
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind()})
    monkeypatch.setattr(platform.router.health, "allow", lambda p: False)
    assert _vision_unavailable_reason(
        SimpleNamespace(platform=platform), "", "") == ""


def test_a_breaker_that_raises_is_not_a_verdict(client, monkeypatch):
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind(), "anthropic": _Seeing()})

    def boom(_p):
        raise RuntimeError("breaker state unreadable")

    monkeypatch.setattr(platform.router.health, "allow", boom)
    assert _vision_unavailable_reason(
        SimpleNamespace(platform=platform), "", "") == ""


async def test_the_unseen_image_note_sits_next_to_its_own_attachment(
    client, tmp_path, monkeypatch
):
    """It used to be appended after EVERY file block, so on a mixed set the
    "## Attached image: X" note was separated from that turn's listing."""
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"codex-cli": _Blind()})
    png = tmp_path / "receipt.png"
    from PIL import Image

    Image.new("RGB", (10, 10), (0, 0, 0)).save(png)
    memo = tmp_path / "memo.txt"
    memo.write_text("LATER-FILE-MARKER", encoding="utf-8")
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(png), str(memo)]),
        inline_budget=6000, rag_budget=2400, rag_k=6)
    assert block.index("## Attached image: receipt.png") < block.index(
        "LATER-FILE-MARKER"
    ), "the image note drifted past the attachments that followed it"


async def test_seen_image_gets_no_note(client, tmp_path, monkeypatch):
    platform = client.app.state.platform
    _fleet(platform, monkeypatch, {"anthropic": _Seeing()})
    png = tmp_path / "receipt.png"
    from PIL import Image

    Image.new("RGB", (10, 10), (0, 0, 0)).save(png)
    _images, block = await _prepare_attachments(
        SimpleNamespace(platform=platform), _body([str(png)]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
    )
    assert "NOT analyzed" not in block


# ------------------------------------------------------ 5. end to end, both ---


def _ocr_router(platform, monkeypatch, seen: dict):
    """One fake serving BOTH the OCR calls and the turn's own completion; the
    LAST completion is the chat turn, so its system prompt is what we assert."""
    vision = _VisionRouter()

    async def fake_complete(*, system, messages, tools, provider=None, model=None,
                            task_class=None, **kw):
        if task_class == "ocr":
            return await vision.complete(
                system=system, messages=messages, tools=tools, task_class=task_class
            )
        seen["system"] = system
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    return vision


def test_post_chat_reads_a_scanned_attachment(client, tmp_path, monkeypatch):
    platform = client.app.state.platform
    seen: dict = {}
    vision = _ocr_router(platform, monkeypatch, seen)
    pdf = tmp_path / "1099.pdf"
    _scanned_pdf(pdf)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "whose W-2 is this?"}],
        "attachments": [str(pdf)],
    })
    assert r.status_code == 200, r.text
    assert vision.calls == 1
    assert "FORM W-2" in seen["system"]
    assert "recovered via OCR" in seen["system"]


def test_chat_stream_reads_a_scanned_attachment(client, tmp_path, monkeypatch):
    platform = client.app.state.platform
    seen: dict = {}
    vision = _ocr_router(platform, monkeypatch, seen)

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        seen["system"] = system
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"),
               "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream
    pdf = tmp_path / "1099.pdf"
    _scanned_pdf(pdf)
    with client.stream("POST", "/chat/stream", json={
        "messages": [{"role": "user", "content": "whose W-2 is this?"}],
        "attachments": [str(pdf)],
    }) as r:
        assert r.status_code == 200
        done = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "escalate" in payload:
                    done = payload
    assert done is not None, "no done frame arrived"
    assert vision.calls == 1, "the STREAM lane is the one the dashboard runs"
    assert "FORM W-2" in seen["system"]
    assert "recovered via OCR" in seen["system"]


def test_post_chat_never_ships_mock_ocr_text(client, tmp_path, monkeypatch):
    """The whole turn on the offline mock: the scan must reach the model as an
    explanation, never as invented wages."""
    platform = client.app.state.platform
    seen: dict = {}

    async def fake_complete(*, system, messages, tools, provider=None, model=None,
                            task_class=None, **kw):
        seen["system"] = system
        return RouteResult(LLMResponse(text="Wages: $1,000,000"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "wages?"}],
        "attachments": [str(pdf)],
    })
    assert r.status_code == 200, r.text
    assert "1,000,000" not in seen["system"]
    assert "fabricated" in seen["system"]
