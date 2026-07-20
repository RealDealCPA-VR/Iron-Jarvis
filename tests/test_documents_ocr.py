"""Scanned-PDF OCR fallback (v1.69.0).

An image-only PDF (a scanned death certificate, a signed form) has no text
layer, so extract_text honestly returns nothing — which read as "extract did
nothing" on the Documents page. The fallback pulls each page's embedded scan
image and transcribes it via the router's vision path. These tests pin the
detection heuristic, the image harvest, the honest notes (mock guard, no
vision), and the route + tool wiring — all offline via a fake router.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.ocr import (
    looks_scanned_pdf,
    ocr_pdf,
    pdf_page_scan_images,
)
from iron_jarvis.documents.tools import document_tools
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext

TRANSCRIPT = "CERTIFICATE OF DEATH\nName: M. Dewerff\nDate filed: 2026-01-15"


def _scanned_pdf(path: Path) -> None:
    """A PDF whose single page is ONE embedded photo — no text layer."""
    from PIL import Image
    from fpdf import FPDF

    png = path.parent / "scan.png"
    Image.new("RGB", (600, 800), (240, 240, 235)).save(png)
    pdf = FPDF()
    pdf.add_page()
    pdf.image(str(png), x=5, y=5, w=200)
    pdf.output(str(path))


class _VisionRouter:
    def __init__(self, provider="anthropic", text=TRANSCRIPT):
        self.provider = provider
        self.text = text
        self.calls = 0

    async def complete(self, *, system, messages, tools, task_class=None, **kw):
        self.calls += 1
        assert messages and messages[0].images, "OCR must send the page image"
        return RouteResult(LLMResponse(text=self.text), self.provider, "vision-x")


# --------------------------------------------------------------- detection ---


def test_looks_scanned_only_for_empty_pdfs(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    assert looks_scanned_pdf(pdf, "")
    assert looks_scanned_pdf(pdf, "  \n ")
    assert not looks_scanned_pdf(pdf, "A real paragraph of extracted text " * 3)
    assert not looks_scanned_pdf(tmp_path / "a.docx", "")


def test_scan_images_harvested(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    blobs, total = pdf_page_scan_images(pdf)
    assert total == 1 and len(blobs) == 1
    from PIL import Image

    with Image.open(io.BytesIO(blobs[0])) as im:
        assert im.size[0] > 100  # a real decodable page image


# ------------------------------------------------------------------- ocr ----


async def test_ocr_transcribes_pages_with_note(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    text, note = await ocr_pdf(pdf, router)
    assert "CERTIFICATE OF DEATH" in text and "[page 1]" in text
    assert "recovered via OCR" in note and "1 of 1" in note
    assert router.calls == 1


async def test_ocr_never_fabricates_from_the_mock(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)
    text, note = await ocr_pdf(pdf, _VisionRouter(provider="mock"))
    assert text == ""
    assert "mock" in note and "fabricated" in note


async def test_ocr_router_failure_is_an_honest_note(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf)

    class _Down:
        async def complete(self, **kw):
            raise RuntimeError("no vision provider")

    text, note = await ocr_pdf(pdf, _Down())
    assert text == ""
    assert "vision" in note.lower()


# ------------------------------------------------------- route + tool wiring --


def test_documents_read_route_recovers_scanned_text(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    pdf = tmp_path / "cert.pdf"
    _scanned_pdf(pdf)
    platform = client.app.state.platform

    async def fake_complete(*, system, messages, tools, task_class=None, **kw):
        return RouteResult(LLMResponse(text=TRANSCRIPT), "anthropic", "vision-x")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.get(f"/documents/read?path={pdf}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "CERTIFICATE OF DEATH" in body["text"]
    assert "recovered via OCR" in body["note"]


def test_documents_read_route_plain_pdf_has_no_note(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    from iron_jarvis.documents import write_document

    pdf = tmp_path / "plain.pdf"
    write_document(pdf, "An ordinary digital PDF with a real text layer inside it.")
    r = client.get(f"/documents/read?path={pdf}")
    assert r.status_code == 200
    body = r.json()
    assert "ordinary digital PDF" in body["text"]
    assert body["note"] == ""


async def test_read_document_tool_uses_ocr_when_wired(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    pdf = ws / "cert.pdf"
    _scanned_pdf(pdf)
    router = _VisionRouter()
    tool = next(
        t for t in document_tools(router_resolver=lambda: router)
        if t.name == "read_document"
    )
    ctx = ToolContext(
        workspace=ws, session_id="t", agent_run_id="t",
        config=None, event_bus=None, engine=None,
    )
    res = await tool.execute({"path": "cert.pdf"}, ctx)
    assert res.ok, res.error
    assert "CERTIFICATE OF DEATH" in res.output
    assert "recovered via OCR" in res.output  # the method is always disclosed


async def test_read_document_tool_without_router_stays_plain(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    pdf = ws / "cert.pdf"
    _scanned_pdf(pdf)
    tool = next(t for t in document_tools() if t.name == "read_document")
    ctx = ToolContext(
        workspace=ws, session_id="t", agent_run_id="t",
        config=None, event_bus=None, engine=None,
    )
    res = await tool.execute({"path": "cert.pdf"}, ctx)
    assert res.ok  # no crash, no fallback — the old behavior exactly
