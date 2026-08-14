"""OCR on EVERY document path (v1.174.0, pair P4).

THE EVIDENCE. A real job — "rename all files in this folder to a name that is
more appropriate given the content", 26 tax documents — spent 12 steps and 18
tool calls and renamed NOTHING. Eleven of the folder's 22 PDFs are image-only
scans: ``extract_pdf`` returned silence, so the agent retried each file with
``read_document``, and the budget went on compensating for a missing
capability. OCR existed — it reached ``read_document`` and the
``/documents/read`` route and nothing else, so whether a scan was readable
depended on which of two interchangeable tools the model happened to name.

These tests pin, offline, through a fake vision router:

* the reach — ``extract_pdf``, ``convert_document``, ``batch_documents``,
  ``redact_scan`` and (via the cache) every plain ``extract_text`` caller;
* the CACHE (frozen contract 5) — keyed by (content sha256, page cap), so the
  same scan is never transcribed twice; failures are NEVER cached;
* the vision ROLE — ``model_roles["vision"]`` reaches OCR, not just view_image;
* IMAGES — a ``.png`` scan is transcribed instead of served as
  ``"[image PNG 800x600, mode RGB]"``, which batch fed to a model as content;
* SAFETY — a redaction path may never call a scan clean: ``redact_scan``
  FAILS and ``redact_pii`` REFUSES rather than writing an "identical copy" of
  a tax return full of SSNs;
* the honest floors that already existed and must survive: the mock never
  fabricates, and nothing blocking runs on the event loop.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.documents import batch as _batch
from iron_jarvis.documents import ocr as _ocr
from iron_jarvis.documents.ocr import (
    MAX_OCR_PAGES_CEILING,
    OCR_MARK,
    file_digest,
    load_cached,
    lookup_cached_text,
    needs_ocr,
    ocr_document,
    ocr_settings,
    store_cached,
)
from iron_jarvis.documents.readers import SCANNED_PDF_SENTINEL, extract_text
from iron_jarvis.documents.tools import document_tools
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext

#: The transcript the fake vision model "reads" off every scan. It carries an
#: SSN on purpose — the redaction tests key on a real, matchable value.
TRANSCRIPT = (
    "FORM W-2 Wage and Tax Statement\n"
    "Employee: M. Dewerff\n"
    "SSN: 123-45-6789\n"
    "Wages: 84,210.00"
)


@pytest.fixture(autouse=True)
def _clean_cache_roots():
    """The synchronous cache lookup memoizes roots per PROCESS; a leaked root
    from another test would make a miss look like a hit (and vice versa)."""
    _ocr._CACHE_ROOTS.clear()
    yield
    _ocr._CACHE_ROOTS.clear()


# ------------------------------------------------------------------ fixtures --


def _scanned_pdf(path: Path) -> None:
    """A PDF whose single page is ONE embedded photo — no text layer."""
    from fpdf import FPDF
    from PIL import Image

    png = path.parent / f".{path.stem}-src.png"
    Image.new("RGB", (600, 800), (240, 240, 235)).save(png)
    pdf = FPDF()
    pdf.add_page()
    pdf.image(str(png), x=5, y=5, w=200)
    pdf.output(str(path))
    png.unlink()


def _text_pdf(path: Path) -> None:
    from iron_jarvis.documents import write_document

    write_document(
        path, "An ordinary digital PDF with a real text layer inside of it."
    )


def _image(path: Path) -> None:
    from PIL import Image

    Image.new("RGB", (900, 1200), (250, 250, 245)).save(path)


class _VisionRouter:
    """One scripted vision reply per call; records what it was asked."""

    def __init__(self, provider="anthropic", text=TRANSCRIPT, replies=None):
        self.provider = provider
        self.text = text
        self.replies = list(replies) if replies is not None else None
        self.calls = 0
        self.kwargs: list[dict] = []
        self.prompts: list[str] = []

    async def complete(self, *, system, messages, tools, task_class=None, **kw):
        self.calls += 1
        self.kwargs.append(dict(kw))
        self.prompts.append(str(messages[0].content) if messages else "")
        if self.replies is not None:
            item = self.replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return RouteResult(LLMResponse(text=item), self.provider, "test-model")
        assert messages and messages[0].images, "OCR must send the page image"
        return RouteResult(LLMResponse(text=self.text), self.provider, "vision-x")


def _cfg(tmp_path: Path, **over):
    """A Config-shaped stand-in: only the attributes OCR reads."""
    base = {
        "home": tmp_path / "home",
        "ocr_enabled": True,
        "ocr_max_pages": 10,
        "model_roles": {},
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(ws: Path, config=None) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="t", agent_run_id="t",
        config=config, event_bus=None, engine=None,
    )


def _tool(name: str, resolver=None):
    return next(t for t in document_tools(resolver) if t.name == name)


# ------------------------------------------------------------------ settings --


def test_ocr_settings_defaults_and_clamps():
    assert ocr_settings(None) == (True, 10)  # no config at all
    assert ocr_settings(SimpleNamespace()) == (True, 10)
    assert ocr_settings(SimpleNamespace(ocr_enabled=False))[0] is False
    # The cap is a SPEND (one vision call per page): neither 0 nor 400 may
    # come out of a config file.
    assert ocr_settings(SimpleNamespace(ocr_max_pages=0))[1] == 10  # 0 -> default
    assert ocr_settings(SimpleNamespace(ocr_max_pages=1))[1] == 1
    assert ocr_settings(SimpleNamespace(ocr_max_pages=400))[1] == MAX_OCR_PAGES_CEILING
    assert ocr_settings(SimpleNamespace(ocr_max_pages="nonsense"))[1] == 10


def test_ocr_settings_page_cap_reaches_the_transcription(tmp_path):
    """A configured cap is not decoration — it bounds the vision calls."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()

    seen: list[int] = []
    real = _ocr.pdf_page_scan_images

    def spy(path, *, max_pages):
        seen.append(max_pages)
        return real(path, max_pages=max_pages)

    _ocr.pdf_page_scan_images = spy
    try:
        import asyncio

        asyncio.run(
            ocr_document(pdf, router, config=_cfg(tmp_path, ocr_max_pages=3))
        )
    finally:
        _ocr.pdf_page_scan_images = real
    assert seen == [3]


async def test_ocr_disabled_spends_nothing_and_says_so(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    text, note = await ocr_document(
        pdf, router, config=_cfg(tmp_path, ocr_enabled=False)
    )
    assert text == ""
    assert router.calls == 0  # a disabled feature costs zero provider calls
    assert "ocr_enabled" in note and "NOT recovered" in note


# ----------------------------------------------------------------- detection --


def test_needs_ocr_classifies_every_case(tmp_path):
    scan = tmp_path / "scan.pdf"
    _scanned_pdf(scan)
    plain = tmp_path / "plain.pdf"
    _text_pdf(plain)
    png = tmp_path / "photo.png"
    _image(png)
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")

    assert needs_ocr(scan, SCANNED_PDF_SENTINEL) is True
    assert needs_ocr(png, "[image PNG 900x1200, mode RGB]") is True
    assert needs_ocr(plain, extract_text(plain)) is False
    assert needs_ocr(tmp_path / "notes.txt", "hello") is False
    # An existing transcript must NEVER trigger a second (paid) pass.
    assert needs_ocr(scan, f"[scanned PDF — {OCR_MARK} (1 of 1)]\n{TRANSCRIPT}") is False


# --------------------------------------------------- cache (frozen contract 5) --


def test_cache_round_trip_is_keyed_on_bytes_and_page_cap(tmp_path):
    home = tmp_path / "home"
    f = tmp_path / "a.pdf"
    f.write_bytes(b"some scanned bytes")
    digest = file_digest(f)
    store_cached(home, digest, 10, TRANSCRIPT, "scanned PDF — " + OCR_MARK)

    hit = load_cached(home, digest, 10)
    assert hit is not None and hit["text"] == TRANSCRIPT
    # A DIFFERENT page cap is a different transcript (fewer pages read).
    assert load_cached(home, digest, 3) is None
    # Different bytes = different key, even at the same path.
    f.write_bytes(b"different scanned bytes")
    assert load_cached(home, file_digest(f), 10) is None


def test_cache_never_stores_a_failure(tmp_path):
    home = tmp_path / "home"
    store_cached(home, "deadbeef", 10, "", "no vision model connected")
    # BOTH layers hold: no record is written, and none would be honored.
    assert not (home / "ocr" / "deadbeef.p10.json").exists()
    assert load_cached(home, "deadbeef", 10) is None
    # ...and neither does a whitespace-only "transcription".
    store_cached(home, "deadbeef", 10, "   \n ", "note")
    assert not (home / "ocr" / "deadbeef.p10.json").exists()
    assert load_cached(home, "deadbeef", 10) is None


def test_a_stale_schema_version_invalidates_the_record(tmp_path):
    """The version field is the invalidation mechanism: bumping it must retire
    every old record, not silently serve one written under other rules."""
    home = tmp_path / "home"
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"scanned bytes")
    digest = file_digest(f)
    store_cached(home, digest, 10, TRANSCRIPT, "note")
    assert load_cached(home, digest, 10) is not None  # honored while current
    assert lookup_cached_text(f) is not None

    record = home / "ocr" / f"{digest}.p10.json"
    stale = json.loads(record.read_text(encoding="utf-8"))
    stale["version"] = 0
    record.write_text(json.dumps(stale), encoding="utf-8")
    assert load_cached(home, digest, 10) is None
    assert lookup_cached_text(f) is None


def test_corrupt_cache_record_is_a_miss_not_a_crash(tmp_path):
    home = tmp_path / "home"
    digest = "0" * 64
    store_cached(home, digest, 10, TRANSCRIPT, "note")
    record = next((home / "ocr").glob(f"{digest}.p10.json"))
    record.write_text("{not json", encoding="utf-8")
    assert load_cached(home, digest, 10) is None


async def test_second_read_of_the_same_scan_costs_no_vision_call(tmp_path):
    """The acceptance folder holds ELEVEN scans and one page took >180s live —
    the cache is what makes the job finishable, and a re-run free."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    cfg = _cfg(tmp_path)

    text1, note1 = await ocr_document(pdf, router, config=cfg)
    assert TRANSCRIPT in text1 and router.calls == 1
    assert "cached" not in note1

    text2, note2 = await ocr_document(pdf, router, config=cfg)
    assert text2 == text1
    assert router.calls == 1, "a cached scan must not call the vision model again"
    assert "cached" in note2 and OCR_MARK in note2


async def test_a_failed_transcription_is_retried_not_frozen(tmp_path):
    """A transient outage must not freeze into a permanent 'this scan is empty'."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    cfg = _cfg(tmp_path)
    down = _VisionRouter(replies=[RuntimeError("vision endpoint down")])
    text, note = await ocr_document(pdf, down, config=cfg)
    assert text == "" and "vision" in note.lower()

    good = _VisionRouter()
    text, _note = await ocr_document(pdf, good, config=cfg)
    assert TRANSCRIPT in text and good.calls == 1


async def test_renaming_the_file_keeps_the_cache_hit(tmp_path):
    """Content-addressed on purpose: the acceptance job RENAMES what it reads,
    and a path-keyed cache would miss every file on the second pass."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    cfg = _cfg(tmp_path)
    await ocr_document(pdf, router, config=cfg)
    renamed = tmp_path / "2024 W-2 Dewerff.pdf"
    pdf.rename(renamed)
    text, note = await ocr_document(renamed, router, config=cfg)
    assert TRANSCRIPT in text and router.calls == 1 and "cached" in note


# ------------------------------------------------------------- vision role ----


async def test_ocr_honors_the_pinned_vision_model(tmp_path):
    """It passed task_class='ocr' and ignored model_roles['vision'], so
    view_image used the user's pin and OCR did not — the two vision consumers
    disagreeing about which model does vision."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    router.manager = SimpleNamespace(available=lambda name: True)
    cfg = _cfg(tmp_path, model_roles={"vision": "anthropic:qwen-vl"})
    await ocr_document(pdf, router, config=cfg)
    assert router.kwargs == [{"provider": "anthropic", "model": "qwen-vl"}]


async def test_unmapped_vision_role_passes_no_extra_kwargs(tmp_path):
    """Dormant path stays byte-identical — a narrow fake router still works."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    await ocr_document(pdf, router, config=_cfg(tmp_path))
    assert router.kwargs == [{}]


# ----------------------------------------------------- event-loop discipline --


async def test_pdf_parsing_never_runs_on_the_event_loop(tmp_path):
    """pypdf parsing + Pillow re-encoding of a 10-page scan is exactly the
    CPU-bound work that reads to the user as 'Daemon offline' (v1.153.1)."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    seen: list[str] = []
    real = _ocr.pdf_page_scan_images

    def spy(path, *, max_pages):
        seen.append(threading.current_thread().name)
        return real(path, max_pages=max_pages)

    _ocr.pdf_page_scan_images = spy
    try:
        await ocr_document(pdf, _VisionRouter(), config=_cfg(tmp_path))
    finally:
        _ocr.pdf_page_scan_images = real
    assert seen and all(name != "MainThread" for name in seen)


# --------------------------------------------------------- the mock floor -----


async def test_the_mock_still_never_fabricates_a_document(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    cfg = _cfg(tmp_path)
    text, note = await ocr_document(pdf, _VisionRouter(provider="mock"), config=cfg)
    assert text == ""
    assert "mock" in note and "fabricated" in note
    # ...and the refusal is not cached as an answer: a later real model must be
    # asked, not handed a frozen empty transcript.
    assert load_cached(cfg.home, file_digest(pdf), 10) is None
    good = _VisionRouter()
    text, _ = await ocr_document(pdf, good, config=cfg)
    assert TRANSCRIPT in text and good.calls == 1


# ------------------------------------------------------------ extract_pdf ----


async def test_extract_pdf_transcribes_a_scan(tmp_path):
    """THE evidence test: this is the tool the failed job reached for first."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "w2.pdf")
    router = _VisionRouter()
    res = await _tool("extract_pdf", lambda: router).execute(
        {"path": "w2.pdf"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert "FORM W-2" in res.output
    assert OCR_MARK in res.output  # the method is always disclosed
    assert res.data["chars"] > 0


async def test_extract_pdf_without_a_router_is_unchanged(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "w2.pdf")
    res = await _tool("extract_pdf").execute({"path": "w2.pdf"}, _ctx(ws))
    assert res.ok
    assert SCANNED_PDF_SENTINEL in res.output
    assert OCR_MARK not in res.output


async def test_extract_pdf_leaves_a_real_text_layer_alone(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _text_pdf(ws / "plain.pdf")
    router = _VisionRouter()
    res = await _tool("extract_pdf", lambda: router).execute(
        {"path": "plain.pdf"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok and "ordinary digital PDF" in res.output
    assert router.calls == 0  # no vision spend on a readable PDF
    assert "note" not in (res.data or {})


# ----------------------------------------------------------- read_document ----


async def test_read_document_transcribes_an_IMAGE(tmp_path):
    """A .png scan yielded '[image PNG 900x1200, mode RGB]' from every document
    path — and batch fed that STRING to the extraction model as the document's
    content, which is worse than empty: it is an invitation to invent."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _image(ws / "receipt.png")
    router = _VisionRouter()
    res = await _tool("read_document", lambda: router).execute(
        {"path": "receipt.png"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert "FORM W-2" in res.output and OCR_MARK in res.output


async def test_a_transcribed_image_is_not_transcribed_a_second_time(tmp_path):
    """An image ALWAYS lacks a text layer, so "does this need OCR?" cannot be
    answered by looking for text — it is answered by looking for the transcript
    marker. Without that, every read of a cached image scan paid again."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _image(ws / "receipt.png")
    cfg = _cfg(tmp_path)
    first = _VisionRouter()
    await _tool("read_document", lambda: first).execute(
        {"path": "receipt.png"}, _ctx(ws, cfg)
    )
    assert first.calls == 1
    second = _VisionRouter()
    res = await _tool("read_document", lambda: second).execute(
        {"path": "receipt.png"}, _ctx(ws, cfg)
    )
    assert res.ok and "FORM W-2" in res.output
    assert second.calls == 0


async def test_an_already_transcribed_scan_is_never_re_asked_or_maligned(tmp_path):
    """The transcript reaches a caller whose config has no home (so the async
    cache cannot help), and the only model connected is the mock. Recognising
    the transcript for what it is keeps BOTH promises: no second vision call,
    and no "OCR failed" note stamped on text we are, in fact, holding."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _image(ws / "receipt.png")
    await ocr_document(ws / "receipt.png", _VisionRouter(), config=_cfg(tmp_path))

    mock = _VisionRouter(provider="mock")
    res = await _tool("read_document", lambda: mock).execute(
        {"path": "receipt.png"}, _ctx(ws, None)
    )
    assert res.ok and "FORM W-2" in res.output
    assert mock.calls == 0
    assert "fabricated" not in res.output and "failed" not in res.output


# -------------------------------------------------------- convert_document ----


async def test_convert_document_converts_a_scan_to_real_text(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "w2.pdf")
    router = _VisionRouter()
    res = await _tool("convert_document", lambda: router).execute(
        {"source": "w2.pdf", "target": "w2.md"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    body = (ws / "w2.md").read_text(encoding="utf-8")
    assert "FORM W-2" in body and SCANNED_PDF_SENTINEL not in body
    assert OCR_MARK in res.output


# --------------------------------------------------------- the readers cache --


def test_a_cached_scan_reaches_plain_extract_text(tmp_path):
    """The reach for callers that never learned about OCR — read_file's office
    redirect, the CLI, project ingest. Only ever REPLACES a useless sentinel,
    and the text carries its own disclosure."""
    import asyncio

    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    assert extract_text(pdf) == SCANNED_PDF_SENTINEL  # before
    asyncio.run(ocr_document(pdf, _VisionRouter(), config=_cfg(tmp_path)))

    served = extract_text(pdf)
    assert "FORM W-2" in served
    assert OCR_MARK in served  # never passes as a text layer
    # A redactor must still see the REAL text layer.
    assert extract_text(pdf, use_ocr_cache=False) == SCANNED_PDF_SENTINEL
    # A page slice asks about specific pages; the transcript cannot answer it.
    assert "FORM W-2" not in extract_text(pdf, page_range="1")


# ------------------------------------------------ when the extension lies -----


def test_a_pdf_named_xlsx_is_read_as_a_pdf_and_says_so(tmp_path):
    """Real, and in the acceptance folder: 'ORGANIZED NUMBERS FOR HOUSES
    2025.xlsx' is a 71 KB PDF. openpyxl said 'BadZipFile: File is not a zip
    file' — a cryptic no for a file we read perfectly, on the very job (rename
    these by their contents) that exists because filenames lie."""
    real = tmp_path / "real.pdf"
    _text_pdf(real)
    liar = tmp_path / "ORGANIZED NUMBERS 2025.xlsx"
    liar.write_bytes(real.read_bytes())

    text = extract_text(liar)
    assert "ordinary digital PDF" in text
    assert "contents are a PDF" in text  # the mismatch IS the finding
    assert "ORGANIZED NUMBERS 2025.xlsx" in text


async def test_a_scan_named_xlsx_still_reaches_ocr(tmp_path):
    """The reader's own [NOTE:] line must not count as the file's text layer —
    it is ~110 characters, which is more than the 'is this scanned?' threshold,
    so counting it made a mislabeled scan look readable."""
    ws = tmp_path / "ws"
    ws.mkdir()
    scan = tmp_path / "scan.pdf"
    _scanned_pdf(scan)
    liar = ws / "NUMBERS 2025.xlsx"
    liar.write_bytes(scan.read_bytes())

    assert needs_ocr(liar, extract_text(liar)) is True
    router = _VisionRouter()
    res = await _tool("read_document", lambda: router).execute(
        {"path": "NUMBERS 2025.xlsx"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert "FORM W-2" in res.output and router.calls == 1


def test_a_real_xlsx_is_untouched_by_the_sniff(tmp_path):
    from openpyxl import Workbook

    book = tmp_path / "book.xlsx"
    wb = Workbook()
    wb.active.append(["Client", "Amount"])
    wb.active.append(["Acme", 1200])
    wb.save(str(book))
    text = extract_text(book)
    assert "Acme" in text and "NOTE:" not in text


def test_lookup_is_free_when_nothing_was_ever_transcribed(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    assert lookup_cached_text(pdf) is None


# ------------------------------------------------------------------ safety ----


async def test_redact_scan_FAILS_on_an_unreadable_scan(tmp_path):
    """It reported 'no PII candidates found — nothing to confirm' for a scanned
    tax return: a certification of cleanliness for a document it never read."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "return.pdf")
    router = _VisionRouter(provider="mock")  # connected, but may not transcribe
    res = await _tool("redact_scan", lambda: router).execute(
        {"path": "return.pdf"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok is False
    assert "NO TEXT LAYER" in res.error
    assert "nothing to confirm" not in (res.output or "")


async def test_redact_scan_finds_pii_in_the_transcription(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "return.pdf")
    res = await _tool("redact_scan", lambda: _VisionRouter()).execute(
        {"path": "return.pdf"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    values = [f["value"] for f in res.data["findings"]]
    assert "123-45-6789" in values
    # The provenance changes what redaction can do — say so before the list.
    assert OCR_MARK in res.output and "cannot be removed" in res.output


async def test_redact_pii_REFUSES_a_scan_and_writes_nothing(tmp_path):
    """The worst bug in this repo's problem space: an 'identical copy' of a
    scanned return, reported as 'no PII found'."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "return.pdf")
    res = await _tool("redact_pii", lambda: _VisionRouter()).execute(
        {"path": "return.pdf"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok is False
    assert "NO TEXT LAYER" in res.error
    assert not (ws / "return.redacted.pdf").exists(), "nothing may be written"


async def test_redact_pii_refusal_names_what_a_prior_ocr_found(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf(ws / "return.pdf")
    cfg = _cfg(tmp_path)
    # A transcription already exists (redact_scan ran earlier)...
    await ocr_document(ws / "return.pdf", _VisionRouter(), config=cfg)
    router = _VisionRouter()
    res = await _tool("redact_pii", lambda: router).execute(
        {"path": "return.pdf"}, _ctx(ws, cfg)
    )
    assert res.ok is False
    assert "previous OCR" in res.error and "SSN" in res.error
    assert "123-45-6789" not in res.error  # counts and categories, never values
    assert router.calls == 0, "a refusal must not spend vision calls"


async def test_redact_pii_still_works_on_a_real_text_layer(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    from iron_jarvis.documents import write_document

    write_document(ws / "letter.txt", "Client SSN: 123-45-6789 on file.")
    res = await _tool("redact_pii", lambda: _VisionRouter()).execute(
        {"path": "letter.txt"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert res.data["total"] == 1
    assert "123-45-6789" not in (ws / "letter.redacted.txt").read_text(encoding="utf-8")


def test_redact_file_itself_refuses_a_scan(tmp_path):
    """The guard sits in redact_file so the /documents/redact ROUTE is covered
    too, not only the tool."""
    from iron_jarvis.documents.redact import redact_file

    src = tmp_path / "scan.pdf"
    _scanned_pdf(src)
    dst = tmp_path / "scan.redacted.pdf"
    with pytest.raises(ValueError, match="NO TEXT LAYER"):
        redact_file(src, dst)
    assert not dst.exists()


async def test_a_transcribed_scan_is_STILL_refused_by_redact_file(tmp_path):
    """A cached transcription proves the words are there; it does NOT make the
    pixels editable. Rebuilding the page from OCR text would destroy the
    document and leave every value in whatever the user shares next."""
    from iron_jarvis.documents.redact import redact_file

    src = tmp_path / "scan.pdf"
    _scanned_pdf(src)
    await ocr_document(src, _VisionRouter(), config=_cfg(tmp_path))
    assert "FORM W-2" in extract_text(src)  # the transcript is available...
    dst = tmp_path / "scan.redacted.pdf"
    with pytest.raises(ValueError, match="NO TEXT LAYER"):  # ...and still refused
        redact_file(src, dst)
    assert not dst.exists()


# ------------------------------------------------------------ batch_documents --


def _ext_reply(summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "facts": ["wages 84,210.00"],
            "entities": {"people": [], "orgs": [], "dates": [], "amounts": []},
            "figures": [],
        }
    )


async def test_batch_extracts_a_folder_of_scans(tmp_path):
    """A folder of scans used to report EVERY file FAILED — while the pipeline
    held a vision-capable router and never asked it to look."""
    src = tmp_path / "docs"
    src.mkdir()
    _scanned_pdf(src / "w2.pdf")
    router = _VisionRouter(
        replies=[TRANSCRIPT, _ext_reply("A W-2 for M. Dewerff."), "# Report\n\n- one"]
    )
    result = await _batch.run_batch(
        src,
        tmp_path / "out",
        router,
        instructions="summarize",
        output="docx",
        config=_cfg(tmp_path),
    )
    assert result["failed"] == []
    assert result["processed"] == 1
    assert result["deliverables"]
    # The extraction prompt DISCLOSES that the text is a transcription: a scan
    # can misread a digit, and a model told it is reading ground truth will
    # state a wrong EIN with full confidence.
    extraction_prompt = router.prompts[1]
    assert "FORM W-2" in extraction_prompt
    assert "OCR transcription" in extraction_prompt
    assert "recognition errors" in extraction_prompt
    # ...and so does the synthesis digest built from that extraction.
    assert "OCR transcription of a scanned document" in router.prompts[2]


async def test_batch_scan_failure_names_the_missing_capability(tmp_path):
    """'document extracted to no text' named the symptom and hid the cause."""
    src = tmp_path / "docs"
    src.mkdir()
    _scanned_pdf(src / "w2.pdf")
    router = _VisionRouter(provider="mock")
    result = await _batch.run_batch(
        src, tmp_path / "out", router, output="docx", config=_cfg(tmp_path)
    )
    assert result["processed"] == 0
    assert len(result["failed"]) == 1
    error = result["failed"][0]["error"]
    assert "no text layer" in error and "mock" in error
    assert "extracted to no text" not in error


def test_sweep_admits_images_only_with_ocr(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    _image(src / "receipt.png")
    (src / "notes.txt").write_text("hello", encoding="utf-8")

    files, skipped = _batch.sweep(src, max_files=25)
    assert [p.name for p in files] == ["notes.txt"]
    assert any("receipt.png" in s["file"] for s in skipped)

    files, _skipped = _batch.sweep(src, max_files=25, include_images=True)
    assert sorted(p.name for p in files) == ["notes.txt", "receipt.png"]


async def test_batch_reads_an_image_document(tmp_path):
    src = tmp_path / "docs"
    src.mkdir()
    _image(src / "receipt.png")
    router = _VisionRouter(
        replies=[TRANSCRIPT, _ext_reply("A wage statement photo."), "# Report\n\n- one"]
    )
    result = await _batch.run_batch(
        src, tmp_path / "out", router, output="docx", config=_cfg(tmp_path)
    )
    assert result["failed"] == [] and result["processed"] == 1
    record = json.loads(
        next((tmp_path / "out" / "extractions").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    # Provenance is persisted, so a resumed run stays honest about the source.
    assert OCR_MARK in record["extraction"]["ocr_note"]


# ============================================================================
# REVIEW FIXES (v1.174.0, P4). Six defects the reviewer proved on the built
# code. Each test below is that proof, kept.
# ============================================================================


def _scanned_pdf_pages(path: Path, pages: int) -> None:
    """A multi-page scan — one embedded photo per page, no text layer."""
    from fpdf import FPDF
    from PIL import Image

    png = path.parent / f".{path.stem}-src.png"
    Image.new("RGB", (600, 800), (240, 240, 235)).save(png)
    pdf = FPDF()
    for _ in range(pages):
        pdf.add_page()
        pdf.image(str(png), x=5, y=5, w=200)
    pdf.output(str(path))
    png.unlink()


# ------------------------------- 1. the SCAN leg may not certify a scan clean --
#
# ``POST /documents/redact/scan`` calls scan_document with NO text=. On a
# scanned return the patterns ran over the "no extractable text" sentinel, came
# back with [], and the Documents page rendered a green shield: "No personal
# data found." for a tax return. Guarding only the APPLY leg is no guard at all
# — nobody redacts a file they have just been told is clean.


def test_scan_document_REFUSES_a_scan_instead_of_reporting_it_clean(tmp_path):
    from iron_jarvis.documents.redact import scan_document

    scan = tmp_path / "return.pdf"
    _scanned_pdf(scan)
    with pytest.raises(ValueError, match="NO TEXT LAYER"):
        scan_document(scan)  # the route's exact call shape


def test_scan_document_REFUSES_an_image_instead_of_reporting_it_clean(tmp_path):
    """A .png W-2 extracts to "[image PNG 900x1200, mode RGB]" — zero PII
    candidates by construction, and a clean bill of health for a photograph."""
    from iron_jarvis.documents.redact import scan_document

    png = tmp_path / "w2.png"
    _image(png)
    with pytest.raises(ValueError, match="NO TEXT LAYER"):
        scan_document(png)


def test_scan_document_still_reads_a_real_text_layer(tmp_path):
    from iron_jarvis.documents.redact import scan_document

    doc = tmp_path / "letter.txt"
    doc.write_text("Client SSN: 123-45-6789 on file.", encoding="utf-8")
    assert [f["value"] for f in scan_document(doc)] == ["123-45-6789"]


def test_scan_document_accepts_supplied_ocr_text_unchanged(tmp_path):
    """redact_scan passes text= explicitly — the refusal must not touch it."""
    from iron_jarvis.documents.redact import scan_document

    scan = tmp_path / "return.pdf"
    _scanned_pdf(scan)
    findings = scan_document(scan, text=TRANSCRIPT)
    assert [f["value"] for f in findings] == ["123-45-6789"]


async def test_scan_document_scans_a_transcription_it_already_paid_for(tmp_path):
    """A file some earlier OCR transcribed is SCANNED, not refused: extract_text
    serves that transcription, so the candidates are the real ones."""
    from iron_jarvis.documents.redact import scan_document

    scan = tmp_path / "return.pdf"
    _scanned_pdf(scan)
    await ocr_document(scan, _VisionRouter(), config=_cfg(tmp_path))
    assert "123-45-6789" in [f["value"] for f in scan_document(scan)]


# ------------------ 2. a refusal that contradicted itself and flipped on retry --


async def test_redact_scan_finds_pii_in_an_IMAGE_on_the_FIRST_call(tmp_path):
    """MEASURED: a .png W-2 with a working vision router spent one vision call,
    then failed with "NO TEXT LAYER ... NOTHING was scanned for PII" — while the
    SAME error said "image file — text recovered via OCR". The identical call
    succeeded on the second run (by then the readers' cache prepended a banner
    carrying the marker). Same file, same config, opposite answers."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _image(ws / "w2.png")
    router = _VisionRouter()
    res = await _tool("redact_scan", lambda: router).execute(
        {"path": "w2.png"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert router.calls == 1
    assert "123-45-6789" in [f["value"] for f in res.data["findings"]]
    assert OCR_MARK in res.output


async def test_redact_scan_answers_the_same_way_twice(tmp_path):
    """The second call must not merely agree — it must agree for free, and it
    must still disclose that the candidates came out of a transcription."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _image(ws / "w2.png")
    cfg = _cfg(tmp_path)
    first = await _tool("redact_scan", lambda: _VisionRouter()).execute(
        {"path": "w2.png"}, _ctx(ws, cfg)
    )
    again = _VisionRouter()
    second = await _tool("redact_scan", lambda: again).execute(
        {"path": "w2.png"}, _ctx(ws, cfg)
    )
    assert first.ok and second.ok
    assert again.calls == 0
    assert [f["value"] for f in first.data["findings"]] == [
        f["value"] for f in second.data["findings"]
    ]
    assert OCR_MARK in second.output and "cached" in second.output


async def test_redact_scan_still_FAILS_when_ocr_really_cannot_run(tmp_path):
    """The floor under the fix: no transcription, no candidates, no "clean"."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _image(ws / "w2.png")
    res = await _tool("redact_scan", lambda: _VisionRouter(provider="mock")).execute(
        {"path": "w2.png"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok is False
    assert "NO TEXT LAYER" in res.error
    assert OCR_MARK not in res.error  # never both halves in one sentence


# ------------------------------- 3. a page slice must not silently buy the cap --


async def test_extract_pdf_page_slice_does_not_secretly_transcribe(tmp_path):
    """MEASURED on a 4-page scan: page_range="3" made FOUR vision calls and
    returned "[page 1]...[page 4]" — 4x the requested spend, content nobody
    asked for, and page labels from the document while the caller asked for a
    slice. Nothing said the slice had been dropped."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf_pages(ws / "big.pdf", 4)
    router = _VisionRouter()
    res = await _tool("extract_pdf", lambda: router).execute(
        {"path": "big.pdf", "page_range": "3"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert router.calls == 0, "a page slice bought a whole-document transcription"
    assert "FORM W-2" not in res.output
    assert "page_range" in res.output and "SKIPPED" in res.output
    assert "page_range" in res.data["note"]


async def test_read_document_page_slice_does_not_secretly_transcribe(tmp_path):
    """The two lanes must not drift: readers.py already refused to serve a
    cached transcript for a slice, and THIS is the lane that spends money."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf_pages(ws / "big.pdf", 4)
    router = _VisionRouter()
    res = await _tool("read_document", lambda: router).execute(
        {"path": "big.pdf", "page_range": "2-3"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok and router.calls == 0
    assert "SKIPPED" in res.output and "FORM W-2" not in res.output


async def test_no_page_slice_still_transcribes_the_whole_scan(tmp_path):
    """The guard must not become a way to lose OCR on the normal call."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _scanned_pdf_pages(ws / "big.pdf", 2)
    router = _VisionRouter()
    res = await _tool("extract_pdf", lambda: router).execute(
        {"path": "big.pdf"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok and router.calls == 2
    assert "FORM W-2" in res.output and OCR_MARK in res.output


async def test_a_page_slice_of_a_REAL_text_pdf_is_untouched(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _text_pdf(ws / "plain.pdf")
    router = _VisionRouter()
    res = await _tool("extract_pdf", lambda: router).execute(
        {"path": "plain.pdf", "page_range": "1"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok and "ordinary digital PDF" in res.output
    assert router.calls == 0 and "note" not in (res.data or {})


# ------------------------------- 4. a cached read says CACHED, and only once ---


def test_a_cached_read_drops_the_sentinel_and_says_cached(tmp_path):
    """MEASURED: a second read printed "[no extractable text — likely a
    scanned/image-only PDF; OCR not available]" immediately followed by the OCR
    transcript. The model is handed both halves and relays the wrong one."""
    import asyncio

    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    asyncio.run(ocr_document(pdf, _VisionRouter(), config=_cfg(tmp_path)))

    served = extract_text(pdf)
    assert "FORM W-2" in served and OCR_MARK in served
    assert SCANNED_PDF_SENTINEL not in served, "'OCR not available' + the OCR text"
    assert "OCR not available" not in served
    # Honesty rule 5: a skipped-because-cached read must SAY so — in the same
    # words the async lane uses, so the two lanes cannot tell different stories.
    assert _ocr.CACHED_NOTE_SUFFIX.strip() in served


def test_an_image_keeps_its_size_note_on_a_cached_read(tmp_path):
    """Dimensions are real information the transcription does not carry, and
    callers branch on them — only the PDF sentinel is a contradiction."""
    import asyncio

    png = tmp_path / "receipt.png"
    _image(png)
    asyncio.run(ocr_document(png, _VisionRouter(), config=_cfg(tmp_path)))
    served = extract_text(png)
    assert "[image PNG 900x1200" in served and "FORM W-2" in served
    assert _ocr.CACHED_NOTE_SUFFIX.strip() in served


# ------------------------- 5. extract_pdf must judge a PDF by its BYTES --------


async def test_extract_pdf_reads_the_pdf_named_xlsx(tmp_path):
    """The acceptance folder's "ORGANIZED NUMBERS FOR HOUSES 2025.xlsx" is a
    71 KB PDF — the file this content sniff was added for. extract_pdf (the
    tool the failed job reached for FIRST) refused it on its extension while
    read_document read it perfectly."""
    ws = tmp_path / "ws"
    ws.mkdir()
    real = tmp_path / "real.pdf"
    _text_pdf(real)
    (ws / "ORGANIZED NUMBERS 2025.xlsx").write_bytes(real.read_bytes())

    res = await _tool("extract_pdf", lambda: _VisionRouter()).execute(
        {"path": "ORGANIZED NUMBERS 2025.xlsx"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert "ordinary digital PDF" in res.output
    assert "contents are a PDF" in res.output  # the mismatch IS the finding


async def test_extract_pdf_still_refuses_a_file_that_is_not_a_pdf(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("hello", encoding="utf-8")
    res = await _tool("extract_pdf").execute({"path": "notes.txt"}, _ctx(ws))
    assert res.ok is False and "not a PDF file" in res.error


async def test_a_scan_named_xlsx_reaches_ocr_through_extract_pdf(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    scan = tmp_path / "scan.pdf"
    _scanned_pdf(scan)
    (ws / "NUMBERS 2025.xlsx").write_bytes(scan.read_bytes())
    router = _VisionRouter()
    res = await _tool("extract_pdf", lambda: router).execute(
        {"path": "NUMBERS 2025.xlsx"}, _ctx(ws, _cfg(tmp_path))
    )
    assert res.ok, res.error
    assert "FORM W-2" in res.output and router.calls == 1


# ------------------------------- 6. the cache-root memo is shared, so lock it --


def test_remember_cache_root_survives_concurrent_writers(tmp_path):
    """_CACHE_ROOTS is written from asyncio.to_thread workers AND from FastAPI's
    sync-endpoint threadpool. The old check-then-remove let the loser raise
    ValueError("list.remove(x): x not in list") out of store_cached — surfacing
    as "OCR fallback failed" on a transcription that had SUCCEEDED."""
    import sys

    roots = [tmp_path / f"h{i}" / "ocr" for i in range(6)]
    errors: list[BaseException] = []

    def hammer(root):
        try:
            for _ in range(2000):
                _ocr.remember_cache_root(root)
        except BaseException as exc:  # noqa: BLE001 — the race IS the finding
            errors.append(exc)

    threads = [
        threading.Thread(target=hammer, args=(roots[i % len(roots)],))
        for i in range(16)
    ]
    # Force the interpreter to switch threads constantly: the unlocked window
    # between "is this root already in the list?" and ``remove`` is a few
    # bytecodes wide, and the default 5 ms switch interval hides it.
    prior = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(prior)
    assert errors == [], f"concurrent writers raised: {errors}"
    snapshot = _ocr.cache_roots()
    assert len(snapshot) <= _ocr._MAX_CACHE_ROOTS
    assert len(set(snapshot)) == len(snapshot), "a root was remembered twice"


def test_a_cache_MISS_does_not_arm_the_synchronous_lookup(tmp_path):
    """load_cached armed the memo on a MISS, so any ATTEMPTED OCR made every
    later synchronous extract_text of an image or scan pay a full-file sha256
    plus a directory glob to discover the same nothing."""
    home = tmp_path / "home"
    assert load_cached(home, "0" * 64, 10) is None
    assert _ocr.cache_roots() == []

    store_cached(home, "0" * 64, 10, "recovered text", "note")
    assert _ocr.cache_roots() == [_ocr.cache_dir(home)]

    _ocr._CACHE_ROOTS.clear()
    assert load_cached(home, "0" * 64, 10) is not None  # a HIT arms it
    assert _ocr.cache_roots() == [_ocr.cache_dir(home)]


async def test_a_cache_WRITE_failure_never_fails_a_good_transcription(tmp_path):
    """store_cached's caller only caught OSError, so anything else escaped and
    became "OCR fallback failed (ValueError: ...)" — on text we were holding."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)

    def boom(*a, **kw):
        raise ValueError("list.remove(x): x not in list")

    real = _ocr.store_cached
    _ocr.store_cached = boom
    try:
        text, note = await ocr_document(pdf, _VisionRouter(), config=_cfg(tmp_path))
    finally:
        _ocr.store_cached = real
    assert "FORM W-2" in text
    assert OCR_MARK in note and "failed" not in note
