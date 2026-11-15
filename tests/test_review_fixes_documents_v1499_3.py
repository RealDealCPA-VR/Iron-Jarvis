"""Review fixes for documents and memory (v1.499.3).

Each test here failed on v1.499.2:

1. A filled PDF form field (AcroForm ``/V`` plus the widget's drawn
   appearance) was never scanned and never redacted, yet the in-place path
   said the removed text was "verified gone".
2. Redacting a PDF in which nothing was found REBUILT it (a legal-size page
   came back A4) while the tool said "identical copy".
3. The PDF arrange/split tools kept passwords in the tool ledger.
4. CRLF text was written with doubled carriage returns.
5. Comma- or tab-separated text written to .csv/.xlsx became ONE column.
6. Reading a .doc left a converted copy of the client file in %TEMP%.
7. Two uploads with the same file name overwrote each other.
8. "Save a copy" joined an unchecked name to the chosen folder.
9. ``memory_read`` returned stored memory without the untrusted fence.
10. ``read_document`` ran the read gate (which can touch a network share) on
    the event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pikepdf = pytest.importorskip("pikepdf")

from pikepdf import Array, Dictionary, Name, Pdf, String  # noqa: E402

SSN = "123-45-6789"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _filled_form_pdf(path: Path, *, value: str = SSN, xfa: bool = False) -> Path:
    """A one-page form: the page text has a phone number and the label "SSN:",
    and a filled text field carries the SSN in /V AND in its drawn
    appearance (exactly how a filled W-9 looks on disk)."""
    pdf = Pdf.new()
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
        Encoding=Name.WinAnsiEncoding,
    ))
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"BT /F1 12 Tf 72 720 Td (Request for Taxpayer ID. Phone 555-123-4567) Tj ET\n"
        b"BT /F1 12 Tf 72 690 Td (SSN:) Tj ET"
    )
    ap = pdf.make_stream(
        b"/Tx BMC BT /F1 12 Tf 2 4 Td (" + value.encode() + b") Tj ET EMC"
    )
    ap.Type = Name.XObject
    ap.Subtype = Name.Form
    ap.BBox = Array([0, 0, 150, 20])
    ap.Resources = Dictionary(Font=Dictionary(F1=font))
    widget = pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("ssn"),
        V=String(value), Rect=Array([110, 685, 260, 705]), AP=Dictionary(N=ap),
        P=page.obj, F=4, DA=String("/F1 12 Tf 0 g"),
    ))
    page.Annots = pdf.make_indirect(Array([widget]))
    acro = Dictionary(
        Fields=Array([widget]), DA=String("/F1 12 Tf 0 g"),
        DR=Dictionary(Font=Dictionary(F1=font)),
    )
    if xfa:
        acro.XFA = pdf.make_stream(
            b"<xfa:datasets><xfa:data><form><ssn>" + value.encode()
            + b"</ssn></form></xfa:data></xfa:datasets>"
        )
    pdf.Root.AcroForm = pdf.make_indirect(acro)
    pdf.save(path)
    return path


def _legal_memo_pdf(path: Path) -> Path:
    pdf = Pdf.new()
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
    ))
    page = pdf.add_blank_page(page_size=(612, 1008))
    page.Resources = Dictionary(Font=Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"BT /F1 12 Tf 72 900 Td (Quarterly memo, nothing sensitive here.) Tj ET"
    )
    pdf.save(path)
    return path


def _every_field_value(path: Path) -> list[str]:
    from iron_jarvis.documents.pdf_forms import list_fields

    return [str(f.get("value")) for f in list_fields(path)]


def _appearance_bytes(path: Path) -> bytes:
    out = b""
    with Pdf.open(path) as pdf:
        for page in pdf.pages:
            for annot in page.get("/Annots") or []:
                ap = annot.get("/AP")
                if ap is None:
                    continue
                n = ap.get("/N")
                if isinstance(n, pikepdf.Stream):
                    out += n.read_bytes()
    return out


# --------------------------------------------------------------------------- #
# (1) Filled form fields are scanned, redacted and verified
# --------------------------------------------------------------------------- #
def test_scan_finds_a_value_typed_into_a_form_field(tmp_path):
    from iron_jarvis.documents.redact import scan_document

    src = _filled_form_pdf(tmp_path / "w9.pdf")
    values = {f["value"] for f in scan_document(src)}
    assert SSN in values
    assert "555-123-4567" in values


def test_scan_with_supplied_text_still_reads_the_form_fields(tmp_path):
    """The chat tool hands its own text in (an OCR-aware read); the fields
    must be scanned anyway."""
    from iron_jarvis.documents.redact import scan_document

    src = _filled_form_pdf(tmp_path / "w9.pdf")
    values = {f["value"] for f in scan_document(src, text="Phone 555-123-4567")}
    assert SSN in values


def test_redaction_removes_a_filled_field_value_everywhere(tmp_path):
    from iron_jarvis.documents.redact import redact_file

    src = _filled_form_pdf(tmp_path / "w9.pdf")
    dst = tmp_path / "w9.redacted.pdf"
    counts, note = redact_file(src, dst)
    assert dst.exists()
    assert sum(counts.values()) >= 2, counts
    assert SSN.encode() not in dst.read_bytes()
    assert all(SSN not in v for v in _every_field_value(dst))
    assert SSN.encode() not in _appearance_bytes(dst)
    # The page itself was edited, not rebuilt.
    with Pdf.open(dst) as out:
        assert [float(x) for x in out.pages[0].MediaBox] == [0, 0, 612, 792]
    assert "in place" in note.lower()


def test_confirmed_terms_reach_the_form_field(tmp_path):
    from iron_jarvis.documents.redact import redact_file

    src = _filled_form_pdf(tmp_path / "w9.pdf")
    dst = tmp_path / "w9.redacted.pdf"
    counts, _note = redact_file(src, dst, only_terms=[SSN])
    assert sum(counts.values()) == 1
    assert SSN.encode() not in dst.read_bytes()
    assert all(SSN not in v for v in _every_field_value(dst))


def test_verification_fails_when_a_field_value_survives(tmp_path, monkeypatch):
    """If the field step misses, the in-place file is never handed back."""
    from iron_jarvis.documents import pdf_redact

    src = _filled_form_pdf(tmp_path / "w9.pdf")
    dst = tmp_path / "out.pdf"
    monkeypatch.setattr(pdf_redact, "_redact_forms_and_notes", lambda pdf, replace: 0)
    with pytest.raises(pdf_redact.RedactionUnverified):
        pdf_redact.redact_pdf(src, dst, values=[SSN])
    assert not dst.exists()


def test_verification_fails_when_only_the_appearance_survives(tmp_path, monkeypatch):
    """/V cleared but the drawn appearance still shows the SSN: not verified."""
    from iron_jarvis.documents import pdf_redact

    src = _filled_form_pdf(tmp_path / "w9.pdf")
    dst = tmp_path / "out.pdf"

    def _only_values(pdf, replace):
        acro = pdf.Root.AcroForm
        for field in acro.Fields:
            field.V = String("")
        return 1

    monkeypatch.setattr(pdf_redact, "_redact_forms_and_notes", _only_values)
    with pytest.raises(pdf_redact.RedactionUnverified):
        pdf_redact.redact_pdf(src, dst, values=[SSN])
    assert not dst.exists()


def test_an_xfa_form_holding_a_value_is_not_redacted_in_place(tmp_path):
    from iron_jarvis.documents import pdf_redact

    src = _filled_form_pdf(tmp_path / "xfa.pdf", xfa=True)
    with pytest.raises(pdf_redact.UnsupportedPdf):
        pdf_redact.redact_pdf(src, tmp_path / "out.pdf", values=[SSN])


def test_redact_file_never_says_verified_over_a_surviving_field(tmp_path):
    """The whole file path: an XFA copy of the value cannot be cleared in
    place, so the result is the rebuild, and the rebuild carries no value."""
    from iron_jarvis.documents.redact import redact_file

    src = _filled_form_pdf(tmp_path / "xfa.pdf", xfa=True)
    dst = tmp_path / "xfa.redacted.pdf"
    _counts, note = redact_file(src, dst)
    assert "verified gone" not in note
    assert SSN.encode() not in dst.read_bytes()


def test_a_form_that_cannot_be_read_is_refused_by_the_scan(tmp_path, monkeypatch):
    from iron_jarvis.documents import pdf_redact
    from iron_jarvis.documents.redact import scan_document

    src = _filled_form_pdf(tmp_path / "w9.pdf")

    def _boom(path):
        raise pdf_redact.UnsupportedPdf("broken field tree")

    monkeypatch.setattr(pdf_redact, "pdf_form_text", _boom)
    with pytest.raises(ValueError, match="form"):
        scan_document(src)


# --------------------------------------------------------------------------- #
# (2) Nothing found: a byte-for-byte copy, said truthfully
# --------------------------------------------------------------------------- #
def test_a_pdf_with_nothing_found_is_copied_byte_for_byte(tmp_path):
    from iron_jarvis.documents.redact import redact_file

    src = _legal_memo_pdf(tmp_path / "memo.pdf")
    dst = tmp_path / "memo.redacted.pdf"
    counts, note = redact_file(src, dst)
    assert counts == {}
    assert dst.read_bytes() == src.read_bytes()
    assert "REBUILT" not in note
    assert "identical" in note.lower()


# --------------------------------------------------------------------------- #
# (3) A PDF password never reaches the tool ledger
# --------------------------------------------------------------------------- #
def test_pdf_arrange_blanks_every_password_for_the_ledger():
    from iron_jarvis.documents.pdf_tools import PdfArrangeTool

    args = {
        "inputs": [
            {"path": "a.pdf", "password": "pw-sample-one"},
            {"path": "b.pdf", "pages": "1-2"},
        ],
        "output": "out.pdf",
        "encrypt_password": "pw-sample-two",
    }
    safe = PdfArrangeTool().redact_args(args)
    dumped = repr(safe)
    assert "pw-sample-one" not in dumped
    assert "pw-sample-two" not in dumped
    assert safe["inputs"][1] == {"path": "b.pdf", "pages": "1-2"}
    assert safe["output"] == "out.pdf"
    # The call itself still gets the real values.
    assert args["inputs"][0]["password"] == "pw-sample-one"
    assert args["encrypt_password"] == "pw-sample-two"


def test_pdf_split_blanks_the_password_for_the_ledger():
    from iron_jarvis.documents.pdf_tools import PdfSplitTool

    args = {"path": "a.pdf", "every": 2, "password": "pw-sample-three"}
    safe = PdfSplitTool().redact_args(args)
    assert "pw-sample-three" not in repr(safe)
    assert safe["path"] == "a.pdf" and safe["every"] == 2
    assert args["password"] == "pw-sample-three"


# --------------------------------------------------------------------------- #
# (4) CRLF text round-trips cleanly
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["notes.txt", "notes.md"])
def test_crlf_text_is_written_without_doubled_carriage_returns(tmp_path, name):
    from iron_jarvis.documents.writers import write_document

    out = write_document(tmp_path / name, "Client list\r\nAlice\r\nBob\r\n")
    raw = out.read_bytes()
    assert b"\r\r" not in raw
    assert raw.replace(b"\r\n", b"\n") == b"Client list\nAlice\nBob\n"


def test_a_lone_cr_line_ending_becomes_a_line_break(tmp_path):
    from iron_jarvis.documents.writers import write_document

    raw = write_document(tmp_path / "old.txt", "a\rb").read_bytes()
    assert raw.replace(b"\r\n", b"\n") == b"a\nb"


def test_crlf_lines_written_to_csv_carry_no_trailing_cr(tmp_path):
    import csv

    from iron_jarvis.documents.writers import write_document

    out = write_document(tmp_path / "list.csv", "Client list\r\nAlice\r\nBob\r\n")
    with open(out, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows == [["Client list"], ["Alice"], ["Bob"]]


# --------------------------------------------------------------------------- #
# (5) Delimited text is written as columns
# --------------------------------------------------------------------------- #
def _csv_rows(path: Path) -> list[list[str]]:
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def _xlsx_rows(path: Path) -> list[list]:
    from openpyxl import load_workbook

    ws = load_workbook(path).active
    return [[c.value for c in r] for r in ws.iter_rows()]


@pytest.mark.parametrize("text", ["a,b\n1,2\n", "a,b\r\n1,2\r\n", "a\tb\n1\t2\n"])
def test_delimited_text_becomes_columns_in_a_csv(tmp_path, text):
    from iron_jarvis.documents.writers import write_document

    out = write_document(tmp_path / "t.csv", text)
    assert _csv_rows(out) == [["a", "b"], ["1", "2"]]


@pytest.mark.parametrize("text", ["a,b\n1,2\n", "a,b\r\n1,2\r\n", "a\tb\n1\t2\n"])
def test_delimited_text_becomes_columns_in_a_workbook(tmp_path, text):
    from iron_jarvis.documents.writers import write_document

    out = write_document(tmp_path / "t.xlsx", text)
    assert _xlsx_rows(out) == [["a", "b"], [1, 2]]


def test_a_quoted_cell_with_a_comma_stays_one_cell(tmp_path):
    from iron_jarvis.documents.writers import write_document

    out = write_document(
        tmp_path / "t.csv", 'name,city\n"Roe, Jane",Austin\n"Doe, John",Boston\n'
    )
    assert _csv_rows(out) == [
        ["name", "city"], ["Roe, Jane", "Austin"], ["Doe, John", "Boston"],
    ]


def test_prose_with_commas_is_not_split_into_columns(tmp_path):
    from iron_jarvis.documents.writers import write_document

    # One comma per line: the same "column count" on every line, so only the
    # sentence guard keeps these whole.
    prose = (
        "Thank you for meeting with us today, it was a pleasure.\n"
        "Please send the signed letter by Friday, and call me after.\n"
    )
    out = write_document(tmp_path / "t.csv", prose)
    assert [len(r) for r in _csv_rows(out)] == [1, 1]
    no_stop = (
        "Thank you for meeting with us today, it was a real pleasure\n"
        "Please send the signed letter by Friday, and call me after that\n"
    )
    out = write_document(tmp_path / "u.csv", no_stop)
    assert [len(r) for r in _csv_rows(out)] == [1, 1]


# --------------------------------------------------------------------------- #
# (6) Reading a .doc leaves no converted copy behind
# --------------------------------------------------------------------------- #
def _fake_word(calls: list[Path]):
    """Stands in for Word: writes a real .docx where Word would."""

    def convert(src, dst, **_kw):
        import docx

        calls.append(Path(dst))
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        d = docx.Document()
        d.add_paragraph("Engagement letter for a sample client")
        d.save(str(dst))

    return convert


def test_reading_a_doc_leaves_no_converted_copy(tmp_path, monkeypatch):
    from iron_jarvis.documents import legacy

    base = tmp_path / "legacy"
    monkeypatch.setattr(legacy, "_cache_dir", lambda: base)
    monkeypatch.setattr(legacy, "_TEXT_MEMO", type(legacy._TEXT_MEMO)())
    calls: list[Path] = []
    monkeypatch.setattr(legacy, "convert_doc_to_docx", _fake_word(calls))
    doc = tmp_path / "letter.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0sample bytes")

    assert "Engagement letter" in legacy.doc_text(doc)
    assert calls, "the conversion never ran"
    assert not calls[0].exists(), "the converted copy is still on disk"
    assert list(base.rglob("*")) == [], list(base.rglob("*"))

    # The same bytes again: answered from memory, Word is not started twice,
    # and still nothing is written.
    assert "Engagement letter" in legacy.doc_text(doc)
    assert len(calls) == 1
    assert list(base.rglob("*")) == []


def test_a_failed_conversion_also_leaves_nothing(tmp_path, monkeypatch):
    from iron_jarvis.documents import legacy

    base = tmp_path / "legacy"
    monkeypatch.setattr(legacy, "_cache_dir", lambda: base)
    monkeypatch.setattr(legacy, "_TEXT_MEMO", type(legacy._TEXT_MEMO)())

    def half_then_fail(src, dst, **_kw):
        Path(dst).write_bytes(b"PK partial client content")
        raise legacy.LegacyReadError("Word stopped")

    monkeypatch.setattr(legacy, "convert_doc_to_docx", half_then_fail)
    doc = tmp_path / "letter.doc"
    doc.write_bytes(b"\xd0\xcf\x11\xe0sample bytes")
    with pytest.raises(legacy.LegacyReadError):
        legacy.doc_text(doc)
    assert list(base.rglob("*")) == []


def test_copies_left_by_earlier_versions_are_cleared(tmp_path):
    """v1.499.2 and earlier kept <hash>.docx in the folder forever; a crashed
    read can leave a work folder. Both go; a fresh folder that is not ours
    (another read still running) stays."""
    from iron_jarvis.documents import legacy

    base = tmp_path / "legacy"
    base.mkdir()
    old_copy = base / ("ab" * 16 + ".docx")
    old_copy.write_bytes(b"PK client")
    stale = base / "read-stale"
    stale.mkdir()
    (stale / "converted.docx").write_bytes(b"PK client")
    fresh = base / "read-fresh"
    fresh.mkdir()
    now = 10_000_000.0
    import os

    os.utime(stale, (now - 4000, now - 4000))
    os.utime(fresh, (now - 5, now - 5))

    legacy._sweep(base, now=now)
    assert not old_copy.exists()
    assert not stale.exists()
    assert fresh.exists()


# --------------------------------------------------------------------------- #
# (7) and (8) The upload and "Save a copy" routes
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    monkeypatch.delenv("IRONJARVIS_FS_ALLOWLIST", raising=False)
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    return TestClient(create_app(str(tmp_path / "home")))


def _upload(client, name: str, data: bytes):
    import base64

    r = client.post("/documents/upload", json={
        "filename": name, "content_b64": base64.b64encode(data).decode(),
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_two_uploads_with_one_name_keep_both_files(client):
    first = _upload(client, "w2.pdf", b"first client file")
    second = _upload(client, "w2.pdf", b"second client file")
    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == b"first client file"
    assert Path(second["path"]).read_bytes() == b"second client file"
    # The original name stays readable, and both sit directly in uploads.
    assert second["name"].startswith("w2") and second["name"].endswith(".pdf")
    assert Path(first["path"]).parent == Path(second["path"]).parent
    assert Path(second["path"]).name == second["name"]


def test_the_same_file_uploaded_again_is_not_duplicated(client):
    first = _upload(client, "notes.txt", b"same bytes")
    again = _upload(client, "notes.txt", b"same bytes")
    assert again["path"] == first["path"]


@pytest.mark.parametrize(
    "name", [r"..\..\escaped.txt", "../../escaped.txt", "sub/escaped.txt",
             "C:escaped.txt", "..", "<absolute>"],
)
def test_save_a_copy_refuses_a_name_that_is_a_path(client, tmp_path, name):
    if name == "<absolute>":  # an absolute name inside this test's own folder
        name = str(tmp_path / "escaped.txt")
    src = tmp_path / "docs" / "organizer.txt"
    src.parent.mkdir()
    src.write_text("sample", encoding="utf-8")
    dest = tmp_path / "picked" / "inner"
    dest.mkdir(parents=True)
    r = client.post("/documents/save-copy", json={
        "source": str(src), "dest_dir": str(dest), "name": name,
    })
    assert r.status_code == 400, r.text
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "picked" / "escaped.txt").exists()
    assert list(dest.iterdir()) == []


def test_save_a_copy_with_a_plain_name_still_works(client, tmp_path):
    src = tmp_path / "docs" / "organizer.txt"
    src.parent.mkdir()
    src.write_text("sample", encoding="utf-8")
    dest = tmp_path / "picked"
    dest.mkdir()
    r = client.post("/documents/save-copy", json={
        "source": str(src), "dest_dir": str(dest), "name": "organizer copy.txt",
    })
    assert r.status_code == 200, r.text
    assert (dest / "organizer copy.txt").read_text(encoding="utf-8") == "sample"


# --------------------------------------------------------------------------- #
# (9) memory_read is fenced like memory_search
# --------------------------------------------------------------------------- #
def test_memory_read_is_marked_as_untrusted_content():
    from iron_jarvis.memory.tools import MemoryReadTool, MemorySearchTool

    assert MemoryReadTool.returns_untrusted_content is True
    assert MemorySearchTool.returns_untrusted_content is True


def test_the_chat_lanes_fence_what_memory_read_returns():
    """The ONE fence both chat lanes call wraps memory_read's output."""
    from iron_jarvis.computeruse.safety import wrap_untrusted
    from iron_jarvis.daemon.chat_turn import _fence_tool_output
    from iron_jarvis.memory.tools import MemoryReadTool

    tool = MemoryReadTool(layers=None)
    stored = "The client prefers email on Fridays."
    out = asyncio.run(_fence_tool_output(None, {}, {}, "memory_read", tool, stored))
    assert out == wrap_untrusted(stored)
    assert out != stored


# --------------------------------------------------------------------------- #
# (10) read_document runs its read gate off the event loop
# --------------------------------------------------------------------------- #
def test_read_document_runs_the_read_gate_off_the_event_loop(tmp_path, monkeypatch):
    from iron_jarvis.documents import document_tools
    from iron_jarvis.documents import tools as doc_tools
    from iron_jarvis.tools.base import ToolContext

    seen: list[bool] = []

    def gate(path):
        try:
            asyncio.get_running_loop()
            seen.append(True)  # called ON the loop thread
        except RuntimeError:
            seen.append(False)
        return True, ""

    monkeypatch.setattr(doc_tools, "fs_read_ok", gate)
    f = tmp_path / "note.txt"
    f.write_text("hello", encoding="utf-8")
    tool = next(t for t in document_tools() if t.name == "read_document")
    ctx = ToolContext(workspace=tmp_path, session_id="t", agent_run_id="t",
                      config=None, event_bus=None, engine=None)
    res = asyncio.run(tool.execute({"path": str(f)}, ctx))
    assert res.ok, res.error
    assert "hello" in res.output
    assert seen == [False], "the read gate ran on the event loop"
