"""Redaction receipt (v1.168.0) — server-side pins for a client-side feature.

The dashboard's DocPreview now offers "Compare to original" on any file named
``<stem>.redacted<suffix>``: it infers the source path from that convention,
reads BOTH files through ``GET /documents/read``, diffs them, and counts the
removed PII from the placeholder tokens the engine wrote into the redacted
copy ([SSN]-style tags, █ blocks). Zero new backend — which means the client
now depends on three existing behaviours that nothing else pinned by VALUE:

1. the output naming convention (tool default AND route default — separate
   f-strings that must not drift apart, or the panel infers a wrong source);
2. the label vocabulary ``_LABELS`` (the client's marker regex is a literal
   copy of these strings);
3. the ``/documents/read`` 20,000-char clip (the client's "compared over the
   first 20,000 characters" honesty line keys on payloads AT that length).

A mutation to any of the three would leave every existing test green and the
new UI silently wrong. These tests make that impossible.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.redact import _LABELS, mask_text
from iron_jarvis.documents.tools import RedactPiiTool

SAMPLE = (
    "Taxpayer: Robert J. Alvarez\n"
    "SSN: 412-88-7391\n"
    "Email: r.alvarez@northwindcpa.com\n"
    "Plain line\n"
)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def doc(tmp_path):
    p = tmp_path / "client_docs" / "organizer.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(SAMPLE, encoding="utf-8")
    return p


# --- 1. the naming convention, everywhere it is spelled ----------------------


def test_scan_default_output_is_stem_dot_redacted_beside_the_source(client, doc):
    """EXACT value, not endswith: the panel reverses this string to find the
    original, so `<stem>.redacted<suffix>` in the source's own folder is the
    whole contract."""
    r = client.post("/documents/redact/scan", json={"path": str(doc)})
    assert r.status_code == 200
    assert r.json()["default_output_path"] == str(
        doc.parent / "organizer.redacted.txt"
    )


def test_apply_default_writes_exactly_the_convention_path(client, doc):
    r = client.post(
        "/documents/redact/apply",
        json={"path": str(doc), "terms": ["412-88-7391"]},
    )
    assert r.status_code == 200
    expected = doc.parent / "organizer.redacted.txt"
    assert r.json()["path"] == str(expected)
    assert expected.is_file()


def test_tool_default_output_matches_the_route_convention(tmp_path):
    """The TOOL (the chat/agent flow that actually produces most previewed
    files) has its own f-string for the same convention — line-for-line drift
    between the two would break source inference for exactly the files chat
    shows. `_output_target` only touches ctx.workspace, so a stub ctx keeps
    this a unit test."""
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    ctx = SimpleNamespace(workspace=ws)
    tool = RedactPiiTool()

    inside = ws / "docs" / "organizer.txt"
    inside.write_text(SAMPLE, encoding="utf-8")
    assert tool._output_target(inside, {}, ctx) == (
        ws / "docs" / "organizer.redacted.txt"
    )

    # Source outside the workspace: the copy lands in the workspace ROOT but
    # KEEPS the convention name — the panel's inference then misses (the
    # original is not beside it), which the UI reports honestly on click.
    outside = tmp_path / "elsewhere" / "K-1.v2.pdf"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"%PDF-1.4")
    assert tool._output_target(outside, {}, ctx) == (ws / "K-1.v2.redacted.pdf")


def test_multi_dot_stems_keep_their_inner_dots(client, tmp_path):
    """`K-1.v2.pdf` → `K-1.v2.redacted.pdf` (Path.stem strips ONE suffix) —
    the client's reverse regex relies on `.redacted` sitting between the full
    stem and a single extension."""
    p = tmp_path / "K-1.v2.txt"
    p.write_text(SAMPLE, encoding="utf-8")
    client_ = TestClient(create_app(str(tmp_path / "home2")))
    r = client_.post("/documents/redact/scan", json={"path": str(p)})
    assert r.json()["default_output_path"] == str(tmp_path / "K-1.v2.redacted.txt")


# --- 2. the marker vocabulary the client's regex mirrors ---------------------


def test_label_vocabulary_is_exactly_what_the_client_counts():
    """REDACTION_LABELS in DocPreview.tsx is a literal copy of these values.
    Renaming/adding a label must land here first, then in the client."""
    assert set(_LABELS.values()) == {
        "SSN", "ITIN", "EIN", "EMAIL", "PHONE", "CARD", "ACCOUNT", "DOB",
        "ADDRESS", "IP", "REDACTED",
    }


def test_label_style_writes_the_bracketed_tags_the_client_counts(client, doc, tmp_path):
    dest = tmp_path / "labelled.txt"
    r = client.post(
        "/documents/redact/apply",
        json={
            "path": str(doc),
            "terms": ["412-88-7391", "r.alvarez@northwindcpa.com"],
            "style": "label",
            "output_path": str(dest),
        },
    )
    assert r.status_code == 200
    out = dest.read_text(encoding="utf-8")
    # Exact tokens on their exact lines — what redactionMarkers() counts.
    assert "SSN: [SSN]" in out
    assert "Email: [EMAIL]" in out
    assert r.json()["counts"] == {"ssn": 1, "email": 1}
    assert r.json()["total"] == 2


def test_black_style_writes_same_length_block_runs():
    masked, counts = mask_text(SAMPLE, style="black", only_terms=["412-88-7391"])
    assert "SSN: " + "█" * len("412-88-7391") in masked
    assert "412-88-7391" not in masked
    assert counts == {"ssn": 1}


def test_remove_style_leaves_no_marker_at_all():
    """The client's no-marker wording ("no redaction markers … the diff below
    shows what differs") exists because of this style — prove it emits neither
    tags nor blocks."""
    masked, counts = mask_text(SAMPLE, style="remove", only_terms=["412-88-7391"])
    assert counts == {"ssn": 1}
    assert "412-88-7391" not in masked
    assert "█" not in masked
    assert "[SSN]" not in masked
    assert "SSN: \n" in masked  # the value is deleted outright, line kept


# --- 3. the /documents/read window the client's honesty line keys on ---------


def test_documents_read_returns_the_redacted_markers(client, doc, tmp_path):
    """The comparison's counting endpoint: reading the redacted file back must
    surface the tags — that IS the re-read the header line claims."""
    dest = tmp_path / "labelled2.txt"
    client.post(
        "/documents/redact/apply",
        json={
            "path": str(doc),
            "terms": ["412-88-7391"],
            "style": "label",
            "output_path": str(dest),
        },
    )
    r = client.get("/documents/read", params={"path": str(dest)})
    assert r.status_code == 200
    assert "[SSN]" in r.json()["text"]
    assert "412-88-7391" not in r.json()["text"]


def test_documents_read_clips_at_exactly_20000_chars(client, tmp_path):
    """READ_CAP in DocPreview.tsx is 20_000 because of this clip: a payload AT
    the cap triggers the "compared over the first 20,000 characters" line.
    Move the cap and the client's disclaimer threshold must move with it."""
    big = tmp_path / "big.txt"
    big.write_text("x" * 25_000, encoding="utf-8")
    r = client.get("/documents/read", params={"path": str(big)})
    assert r.status_code == 200
    assert len(r.json()["text"]) == 20_000

    small = tmp_path / "small.txt"
    small.write_text("x" * 19_999, encoding="utf-8")
    r2 = client.get("/documents/read", params={"path": str(small)})
    assert len(r2.json()["text"]) == 19_999  # below the cap: untouched
