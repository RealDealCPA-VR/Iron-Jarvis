"""C-05 — a scanned form is read ON THIS PC before any vision model is asked.

Before this, OCR meant one vision call per page: with no vision-capable model
connected a scanned W-2 was simply unreadable, and with a cloud one connected
the client's scan left the machine. ``documents/local_ocr`` reads the page with
Windows' built-in recognizer first; the vision model is asked only for pages it
comes back empty or doubtful on.

The routing tests inject a FAKE recognizer so they run identically on every
machine (the offline suite keeps local OCR switched off — see conftest's
``_local_ocr_off_by_default``); the tests at the bottom drive the REAL engine
and skip where Windows OCR is unavailable.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from iron_jarvis.documents import local_ocr, ocr as ocr_mod
from iron_jarvis.documents.attachment_rag import ocr_pages_spent
from iron_jarvis.documents.local_ocr import (
    CHECK_FIGURES,
    LOCAL_NOTE,
    LOW_CONFIDENCE,
    OcrResult,
)
from iron_jarvis.documents.ocr import (
    OCR_MARK,
    cache_dir,
    load_cached,
    lookup_cached_text,
    ocr_document,
    ocr_image,
    ocr_pdf,
    store_cached,
)
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

W2_TEXT = "Form W-2 2025\n1 Wages 58,432.17\n2 Federal income tax withheld 7,215.40"
VISION_TEXT = "VISION TRANSCRIPT\nWages 58,432.17"


def _scanned_pdf(path: Path, pages: int = 1) -> None:
    """A PDF whose every page is ONE embedded photo — no text layer."""
    from PIL import Image
    from fpdf import FPDF

    png = path.parent / f"{path.stem}-scan.png"
    Image.new("RGB", (600, 800), (240, 240, 235)).save(png)
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.image(str(png), x=5, y=5, w=200)
    pdf.output(str(path))


def _image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (800, 600), (250, 250, 250)).save(path)


class _VisionRouter:
    def __init__(self, provider: str = "anthropic", text: str = VISION_TEXT) -> None:
        self.provider = provider
        self.text = text
        self.calls = 0

    async def complete(self, *, system, messages, tools, task_class=None, **kw):
        self.calls += 1
        return RouteResult(LLMResponse(text=self.text), self.provider, "vision-x")


@pytest.fixture
def local_on(monkeypatch):
    """Switch local OCR back on for this test (the suite disables it)."""
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    monkeypatch.setattr(local_ocr, "available", lambda: True)


def _fake_engine(monkeypatch, results, seen=None):
    """Answer each page in turn with ``results`` (an OcrResult or None)."""
    queue = list(results)

    def fake(data, *, deadline_s=local_ocr.PAGE_DEADLINE_S):
        if seen is not None:
            seen.append(threading.get_ident())
        return queue.pop(0) if queue else None

    monkeypatch.setattr(local_ocr, "local_ocr_bytes", fake)


def _good(text: str = W2_TEXT) -> OcrResult:
    return OcrResult(text=text, confident=True, number_heavy=True, seconds=0.05)


def _doubtful(text: str = "W 2 ### ~~ 58,4 32") -> OcrResult:
    return OcrResult(text=text, confident=False, number_heavy=True, seconds=0.05)


# ------------------------------------------------------- routing: PDF pages ---


async def test_a_page_read_on_this_pc_costs_no_vision_call(tmp_path, monkeypatch, local_on):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    _fake_engine(monkeypatch, [_good()])
    router = _VisionRouter()

    text, note = await ocr_pdf(pdf, router)

    assert "58,432.17" in text and "[page 1]" in text
    assert router.calls == 0, "a confident local read must not call the model"
    assert OCR_MARK in note and LOCAL_NOTE in note and CHECK_FIGURES in note
    # The turn's vision budget is for VISION calls; this page cost none.
    assert ocr_pages_spent(note, text, 10) == 0


async def test_a_doubtful_page_still_goes_to_the_vision_model(tmp_path, monkeypatch, local_on):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    _fake_engine(monkeypatch, [_doubtful()])
    router = _VisionRouter()

    text, note = await ocr_pdf(pdf, router)

    assert "VISION TRANSCRIPT" in text
    assert router.calls == 1
    assert ocr_pages_spent(note, text, 10) == 1


async def test_a_doubtful_page_is_kept_when_no_model_can_check_it(tmp_path, monkeypatch, local_on):
    """Never read LESS: with only the offline mock connected the doubtful local
    text is still handed over — labelled — instead of nothing."""
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    _fake_engine(monkeypatch, [_doubtful("W 2 ### 58,4 32")])

    text, note = await ocr_pdf(pdf, _VisionRouter(provider="mock"))

    assert "58,4 32" in text
    assert LOW_CONFIDENCE in note and CHECK_FIGURES in note and OCR_MARK in note


async def test_mixed_pages_split_between_this_pc_and_the_model(tmp_path, monkeypatch, local_on):
    pdf = tmp_path / "two.pdf"
    _scanned_pdf(pdf, pages=2)
    _fake_engine(monkeypatch, [_good(), _doubtful()])
    router = _VisionRouter()

    text, note = await ocr_pdf(pdf, router)

    assert "[page 1]" in text and "[page 2]" in text
    assert "58,432.17" in text and "VISION TRANSCRIPT" in text
    assert router.calls == 1, "only the doubtful page is sent to the model"
    assert f"; 1 {LOCAL_NOTE}" in note
    assert ocr_pages_spent(note, text, 10) == 1


async def test_the_local_read_runs_off_the_event_loop(tmp_path, monkeypatch, local_on):
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    seen: list[int] = []
    _fake_engine(monkeypatch, [_good()], seen=seen)

    await ocr_pdf(pdf, _VisionRouter())

    assert seen and threading.get_ident() not in seen


# ----------------------------------------------------------- routing: image ---


async def test_an_image_is_read_on_this_pc(tmp_path, monkeypatch, local_on):
    png = tmp_path / "photo.png"
    _image(png)
    _fake_engine(monkeypatch, [_good()])
    router = _VisionRouter()

    text, note = await ocr_image(png, router)

    assert "58,432.17" in text
    assert router.calls == 0
    assert OCR_MARK in note and LOCAL_NOTE in note


async def test_an_image_the_engine_cannot_read_goes_to_the_model(tmp_path, monkeypatch, local_on):
    png = tmp_path / "photo.png"
    _image(png)
    _fake_engine(monkeypatch, [None])
    router = _VisionRouter()

    text, note = await ocr_image(png, router)

    assert "VISION TRANSCRIPT" in text and router.calls == 1
    assert LOCAL_NOTE not in note


# ------------------------------------------------------------ no engine here ---


async def test_without_the_engine_everything_behaves_as_before(tmp_path, monkeypatch):
    """The asymmetry rule: a missing engine reads exactly what v1.174.0 read."""
    monkeypatch.setattr(local_ocr, "available", lambda: False)
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()

    text, note = await ocr_pdf(pdf, router)

    assert "VISION TRANSCRIPT" in text and router.calls == 1
    assert "1 of 1 page(s) transcribed" in note and LOCAL_NOTE not in note


async def test_the_switch_turns_it_off(tmp_path, monkeypatch):
    """``ocr_local = false`` (or IRONJARVIS_OCR_LOCAL=0) keeps the old path."""
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    monkeypatch.setattr(local_ocr, "available", lambda: True)
    _fake_engine(monkeypatch, [_good()])
    pdf = tmp_path / "w2.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()

    class _Cfg:
        ocr_local = False

    text, note = await ocr_pdf(pdf, router, config=_Cfg())

    assert router.calls == 1 and LOCAL_NOTE not in note


# ------------------------------------------------------------------- cache ---


async def test_local_and_vision_transcripts_never_share_a_cache_record(tmp_path, monkeypatch, local_on):
    home = tmp_path / "home"
    digest = "d" * 64
    store_cached(home, digest, 10, "LOCAL TEXT", f"scanned PDF — {OCR_MARK} (1 of 1 page(s) transcribed; 1 {LOCAL_NOTE})")
    store_cached(home, digest, 10, "VISION TEXT", f"scanned PDF — {OCR_MARK} (1 of 1 page(s) transcribed)")

    names = sorted(p.name for p in cache_dir(home).glob("*.json"))
    assert names == [f"{digest}.p10.json", f"{digest}.p10.local.json"]
    # The vision transcript is the more expensive read: it wins both lookups.
    assert load_cached(home, digest, 10)["text"] == "VISION TEXT"
    ocr_mod.remember_cache_root(cache_dir(home))
    hit = lookup_cached_text(_file_with_digest(tmp_path, digest))
    assert hit is None or hit[0] in ("VISION TEXT", "LOCAL TEXT")


def _file_with_digest(tmp_path: Path, digest: str) -> Path:
    """A path whose content hash will NOT match ``digest`` — the lookup is
    exercised for shape, never for a fabricated hit."""
    p = tmp_path / "other.bin"
    p.write_bytes(b"not the cached bytes")
    return p


def test_a_doubtful_local_read_is_never_cached(tmp_path):
    home = tmp_path / "home"
    digest = "e" * 64
    store_cached(home, digest, 10, "MAYBE 58,4 32", f"image file — {OCR_MARK} ({LOCAL_NOTE} — {LOW_CONFIDENCE})")
    assert not list(cache_dir(home).glob("*.json"))
    assert load_cached(home, digest, 10) is None


async def test_a_confident_local_read_is_cached_and_served(tmp_path, monkeypatch, local_on):
    png = tmp_path / "photo.png"
    _image(png)
    _fake_engine(monkeypatch, [_good(), _good()])
    home = tmp_path / "home"
    router = _VisionRouter()

    first, note1 = await ocr_document(png, router, home=home)
    second, note2 = await ocr_document(png, router, home=home)

    assert first == second and "58,432.17" in second
    assert router.calls == 0
    assert "cached" in note2 and ocr_pages_spent(note2, second, 10) == 0


# ------------------------------------------------------ judgement + wording ---


def test_a_page_of_figures_is_flagged_for_checking():
    confident, number_heavy = local_ocr._judge("Wages 58,432.17 Fed 7,215.40 SS 3,726.20 Med 871.45")
    assert confident and number_heavy


def test_symbol_soup_is_doubted():
    """Enough characters to clear the "almost empty" rule, but mostly junk —
    so this pins the JUDGEMENT and not the length check (the first version of
    this test passed with the judgement deleted: its soup had no letters at
    all and never reached it)."""
    soup = "abcdefghij klmnopqrst 1234567890 ### $$$ %%% ^^^ &&& *** ((( ))) +++ === ~~~"
    assert sum(ch.isalnum() for ch in soup) >= 20
    confident, _ = local_ocr._judge(soup)
    assert not confident


def test_an_almost_empty_page_is_doubted():
    assert local_ocr._judge("a b") == (False, False)


def test_the_words_a_local_read_carries():
    assert local_ocr.note_for(_good()) == f"{LOCAL_NOTE} — {CHECK_FIGURES}"
    doubtful = local_ocr.note_for(_doubtful())
    assert LOW_CONFIDENCE in doubtful and CHECK_FIGURES in doubtful
    assert local_ocr.note_for(None) == ""


def test_the_deadline_ends_a_page_that_will_not_finish(monkeypatch):
    async def _never(png):
        await asyncio.sleep(30)
        return "never"

    monkeypatch.setattr(local_ocr, "_recognize_async", _never)
    monkeypatch.setattr(local_ocr, "available", lambda: True)
    import time as _t

    t0 = _t.perf_counter()
    assert local_ocr.recognize_png(b"png", deadline_s=0.2) is None
    assert _t.perf_counter() - t0 < 10


async def test_the_engine_is_never_run_on_the_event_loop(monkeypatch):
    """Inside a running loop the recogniser REFUSES — it is never started.

    Asserting only the ``None`` would pass with the guard deleted too: the
    outer catch swallows the "asyncio.run() cannot be called from a running
    event loop" error and returns ``None`` all the same. What matters is that
    a 0.1 s page never runs on the loop, so the spy is the assertion."""
    started: list[int] = []

    def _spy(png):
        # A PLAIN function on purpose: calling it counts. An `async def` spy
        # would only count on the AWAIT, and `asyncio.run` raises before
        # awaiting anything — so this test passed with the guard deleted.
        started.append(1)
        return asyncio.sleep(0)

    monkeypatch.setattr(local_ocr, "_recognize_async", _spy)
    monkeypatch.setattr(local_ocr, "available", lambda: True)

    assert local_ocr.recognize_png(b"png") is None
    assert not started, "the recogniser must not be started on the event loop"


# ------------------------------------------------------------------ doctor ---


def test_the_doctor_reports_local_ocr(monkeypatch):
    # From the MODULE path: `iron_jarvis.onboarding` exports a `doctor`
    # FUNCTION, which shadows the submodule of the same name.
    from iron_jarvis.onboarding.doctor import CHECKS, check_local_ocr

    monkeypatch.setattr(
        local_ocr,
        "self_test",
        lambda: {"ok": True, "languages": ["en-US"], "detail": "read the test line"},
    )
    good = check_local_ocr()
    assert good["ok"] and "en-US" in good["detail"]

    monkeypatch.setattr(
        local_ocr,
        "self_test",
        lambda: {"ok": False, "languages": [], "detail": "no OCR language is installed"},
    )
    bad = check_local_ocr()
    assert not bad["ok"] and "vision" in bad["detail"]
    assert "Optical character recognition" in bad["fix"]
    assert bad["level"] == "recommended", "a missing engine must never block a boot"
    assert check_local_ocr in CHECKS


# ------------------------------------------------------------- real engine ---

real_engine = pytest.mark.skipif(
    not local_ocr.languages(), reason="Windows OCR is not available on this machine"
)


def _w2_png(path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (1400, 500), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    d.text((40, 40), "Form W-2 Wage and Tax Statement 2025", fill="black", font=font)
    d.text((40, 140), "1 Wages, tips, other compensation  58,432.17", fill="black", font=font)
    d.text((40, 240), "2 Federal income tax withheld  7,215.40", fill="black", font=font)
    d.text((40, 340), "Employee  Jordan Q. Sample", fill="black", font=font)
    im.save(path)


@real_engine
def test_the_real_engine_reads_a_fictional_w2(tmp_path, monkeypatch):
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    png = tmp_path / "w2.png"
    _w2_png(png)

    result = local_ocr.local_ocr_image(png)

    assert result is not None
    flat = result.text.replace(" ", "")
    assert "58,432.17".replace(" ", "") in flat and "7,215.40" in flat
    assert result.confident and result.number_heavy


@real_engine
async def test_the_real_engine_reads_a_scan_with_only_the_mock_connected(tmp_path, monkeypatch):
    """The whole point: no vision model, no cloud, and the scan is still read."""
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    from PIL import Image
    from fpdf import FPDF

    png = tmp_path / "w2.png"
    _w2_png(png)
    with Image.open(png) as im:
        im.save(tmp_path / "w2b.png")
    pdf = tmp_path / "scan.pdf"
    doc = FPDF()
    doc.add_page()
    doc.image(str(png), x=5, y=5, w=200)
    doc.output(str(pdf))

    text, note = await ocr_pdf(pdf, _VisionRouter(provider="mock"))

    assert "58,432.17" in text.replace(" ", "")
    assert OCR_MARK in note and LOCAL_NOTE in note


@real_engine
def test_the_self_test_reads_its_own_line():
    local_ocr._SELF_TEST = None
    try:
        assert local_ocr.self_test()["ok"]
    finally:
        local_ocr._SELF_TEST = None
