"""Integration tests for the memory/RAG additions — fully offline.

Covers the pieces wired into the daemon in the coordinating session (the
connectors themselves are unit-tested in test_ltm_cloud.py / test_ltm_http_rag.py):

* ``POST /ltm/ingest-document`` — a PDF is converted to Markdown and stored
  durably in long-term memory, then found by ``GET /ltm/search``.
* ``POST /ltm/sources`` — the new source kinds (``http_rag`` + cloud drives)
  persist and register live, and validation rejects bad payloads.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _tiny_pdf_b64() -> str:
    """A small real PDF carrying a distinctive token, base64-encoded."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)
    pdf.cell(0, 10, "Iron Jarvis Ingestion Report")
    pdf.ln(12)
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 8, "The arc reactor telemetry token is zxqmarker42 unique.")
    data = pdf.output()  # fpdf2 returns a bytearray
    return base64.b64encode(bytes(data)).decode("ascii")


# --- PDF -> durable memory ------------------------------------------------


def test_ingest_pdf_becomes_searchable_memory(client):
    resp = client.post(
        "/ltm/ingest-document",
        json={"filename": "report.pdf", "content_b64": _tiny_pdf_b64()},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "report"  # filename stem
    assert body["chars"] > 0
    assert body["source"]  # landed in the brain source

    # The distinctive token from the PDF is now retrievable from long-term memory.
    found = client.get("/ltm/search", params={"q": "zxqmarker42"})
    assert found.status_code == 200, found.text
    hits = found.json()["results"]
    assert any("zxqmarker42" in (h.get("snippet", "") + h.get("title", "")) for h in hits)


def test_ingest_rejects_empty_document(client):
    # A zero-byte "pdf" has no extractable text -> 422 (not a 500).
    resp = client.post(
        "/ltm/ingest-document",
        json={"filename": "empty.txt", "content_b64": base64.b64encode(b"").decode()},
    )
    assert resp.status_code == 422


def test_ingest_rejects_bad_base64(client):
    resp = client.post(
        "/ltm/ingest-document",
        json={"filename": "x.pdf", "content_b64": "!!!not base64!!!"},
    )
    # Either invalid-base64 (400) or empty-after-decode (422) — never a crash.
    assert resp.status_code in (400, 422)


# --- New LTM source kinds -------------------------------------------------


def test_add_http_rag_source_persists_and_registers(client):
    resp = client.post(
        "/ltm/sources",
        json={
            "name": "my-offsite-rag",
            "kind": "http_rag",
            "endpoint_url": "https://rag.example.com/query",
            "config": {"query_field": "q", "results_path": "documents"},
            "token": "secret-bearer-123",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "my-offsite-rag", "kind": "http_rag"}

    listing = client.get("/ltm/sources").json()
    assert "my-offsite-rag" in listing["active"]
    rec = next(s for s in listing["sources"] if s["name"] == "my-offsite-rag")
    assert rec["kind"] == "http_rag"
    assert rec["endpoint_url"] == "https://rag.example.com/query"
    # The bearer is stored in the vault, only its name persisted on the record.
    assert rec["token_secret"] and "secret-bearer-123" not in str(rec)


def test_http_rag_requires_endpoint_url(client):
    resp = client.post(
        "/ltm/sources", json={"name": "bad-rag", "kind": "http_rag"}
    )
    assert resp.status_code == 400
    assert "endpoint_url" in resp.text


def test_add_cloud_drive_source_registers(client):
    # A cloud drive can be added even before its OAuth is connected — the token
    # is resolved lazily at search time (and search degrades to [] without one).
    resp = client.post(
        "/ltm/sources",
        json={"name": "work-drive", "kind": "google_drive", "path": "Notes"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "work-drive", "kind": "google_drive"}
    assert "work-drive" in client.get("/ltm/sources").json()["active"]


def test_unknown_source_kind_rejected(client):
    resp = client.post("/ltm/sources", json={"name": "n", "kind": "bogus"})
    assert resp.status_code == 400
