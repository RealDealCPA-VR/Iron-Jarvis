"""Idea 30-a: every figure a chat turn READ is recorded with its place.

The record (``sourcemark``) is written by code at the moment of the read,
never from a model's words. These pins drive the real readers through the
real tool registry with a chat turn's record in the tool context.

* a PDF with a figure on page 7: the mark says page 7 and its line, not the
  first page of the chunk it was retrieved in;
* an OCR transcript's own page labels;
* a sheet cell (``excel_read``, ``read_document`` of a workbook whose heading
  cell holds a line break, ``excel_query``'s matching rows);
* a Word paragraph and a Word table cell; a CSV line and column;
* what is NOT a figure (under three digits, a bare year, a date, an id);
* the line kept with a mark is secret-masked;
* marks only for a chat turn (an agent run writes none), at most 3,000 a
  read, written again for a remembered (cached) read, swept with the chat
  unless kept.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.numbers import figures
from iron_jarvis.documents import sources as S
from iron_jarvis.documents import source_store as store
from iron_jarvis.documents.attachment_rag import extract_for_rag, rag_block
from iron_jarvis.documents.sources import SourceTurn, marks_for_output, source_map_for
from iron_jarvis.tools.base import ToolContext


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def make_pdf(path: Path, *, pages: int = 9, figure_page: int = 7) -> Path:
    """A text PDF: filler on every page, and on ``figure_page`` the line
    "Total wages 48,210.00" as the THIRD line of the page."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for n in range(1, pages + 1):
        pdf.add_page()
        pdf.cell(0, 8, f"Statement page {n}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 8, "Account summary for the period", new_x="LMARGIN", new_y="NEXT")
        if n == figure_page:
            pdf.cell(0, 8, "Total wages 48,210.00 for the year", new_x="LMARGIN", new_y="NEXT")
        # Enough words that a 1,600-character chunk starting on an earlier
        # page runs over into this one.
        for k in range(14):
            pdf.cell(0, 6, f"filler line {k} of page {n} with nothing worth a figure here",
                     new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return path


def make_xlsx(path: Path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Item"
    ws["B1"] = "Q2"
    ws["C1"] = "Q3\nfinal"  # a heading with a line break
    ws["A2"] = "Rent"
    ws["B2"] = 1200
    ws["C2"] = 1250
    ws["A7"] = "Travel"
    ws["C7"] = 48210.5
    wb.save(str(path))
    return path


def make_docx(path: Path) -> Path:
    import docx

    doc = docx.Document()
    doc.add_paragraph("Engagement letter")
    doc.add_paragraph("The agreed fee is $7,450 for the year.")
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "Item"
    table.cell(0, 1).text = "Amount"
    table.cell(1, 0).text = "Retainer"
    table.cell(1, 1).text = "2,600.00"
    doc.add_paragraph("Closing words.")
    doc.save(str(path))
    return path


def _ctx(tmp_path: Path, platform, turn: "SourceTurn | None") -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
        thread_id=turn.thread_id if turn else "", source_turn=turn,
    )


@pytest.fixture()
def platform(tmp_path):
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "home")))
    yield client.app.state.platform
    client.close()


def _invoke(platform, name: str, args: dict, ctx: ToolContext):
    async def go():
        return await platform.registry.invoke(
            name, args, ctx, platform.permissions,
            {name: "allow"}, session_allow={name},
        )

    return asyncio.run(go())


def _rows(platform) -> "list[dict]":
    with session_scope(platform.engine) as db:
        return [store.as_dict(r) for r in db.exec(select(store.SourceMark))]


def _by_key(marks, key):
    return [m for m in marks if m["key"] == key]


# --------------------------------------------------------------------------- #
# the figure rule (core/numbers)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,keys", [
    ("Total 48,210.00 due", ["48210"]),
    ("Fee $45 and 45 apples", ["45"]),          # currency makes a small one count
    ("Rate 7% and 7 days", ["7"]),
    ("Paid 12.5 hours", ["12.5"]),
    ("In 2024 we filed; FY 2,024 units", ["2024"]),  # a bare year is not; with a comma it is
    ("Due 2026-11-20 or 555-1234", []),          # a date, a phone number
    ("Form K1 and W2, the 2nd copy", []),        # glued to letters
    ("About 25k and 2 million", ["25~k", "2~m"]),
    ("Only 99 rows", []),
    ("(1,234) owed", ["1234"]),
])
def test_what_counts_as_a_figure(text, keys):
    assert [f.key for f in figures(text)] == keys


# --------------------------------------------------------------------------- #
# PDF: the page the figure is ON
# --------------------------------------------------------------------------- #
def test_read_document_marks_the_page_and_line_of_a_figure(tmp_path, platform):
    pdf = make_pdf(tmp_path / "statement.pdf")
    turn = SourceTurn(thread_id="thr_pdf", turn_id="turn_pdf", engine=platform.engine)
    res = _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, reads = turn.snapshot()
    assert reads == 1
    hit = _by_key(marks, "48210")
    assert len(hit) == 1, marks
    assert hit[0]["locator"] == {"page": 7, "line": 3}
    assert hit[0]["path"] == str(pdf)
    row = [r for r in _rows(platform) if r["id"] == hit[0]["id"]][0]
    assert row["thread_id"] == "thr_pdf" and row["turn_id"] == "turn_pdf"
    assert row["shown"] == "48,210.00"
    assert "Total wages 48,210.00" in row["context"]
    assert row["file_sha256"] and len(row["file_sha256"]) == 64
    # The answer the model was given is exactly what it always was.
    assert "[page" not in res.output


def test_extract_pdf_marks_too_and_a_page_slice_keeps_real_page_numbers(tmp_path, platform):
    pdf = make_pdf(tmp_path / "statement.pdf")
    turn = SourceTurn(engine=platform.engine)
    res = _invoke(platform, "extract_pdf", {"path": str(pdf), "page_range": "6-8"},
                  _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, _ = turn.snapshot()
    assert [m["locator"] for m in _by_key(marks, "48210")] == [{"page": 7, "line": 3}]


def test_a_retrieved_excerpt_names_the_page_the_figure_is_on_not_the_chunk_start(tmp_path):
    """The attachment chunk starting on page 6 is labelled p.6 — the figure
    on page 7 inside it is marked page 7."""
    pdf = make_pdf(tmp_path / "statement.pdf")
    text = extract_for_rag(pdf)
    shown: list = []
    block = rag_block("statement.pdf", text, "total wages for the year", None,
                      k=6, char_budget=6000, shown_out=shown)
    assert "48,210.00" in block
    indexed, spans = shown
    smap = source_map_for(pdf, text, indexed, {})
    marks = marks_for_output(indexed, smap, spans=spans)
    hit = [m for m in marks if m.key == "48210"]
    assert hit and hit[0].locator["page"] == 7
    # The chunk that carried it started on an earlier page.
    import re

    excerpts = re.split(r"\n(?=\[p\.\d+\] )", block)
    carrier = [x for x in excerpts if "48,210" in x][0]
    assert re.match(r"\[p\.\d+\]", carrier) and not carrier.startswith("[p.7]"), carrier[:40]


def test_marks_from_pages_places_each_kind():
    pdf = S.marks_from_pages([({"page": 7}, "Statement\nTotal 48,210.00")], "pdf")
    assert [(m.key, m.locator, m.shown) for m in pdf] == [("48210", {"page": 7, "line": 2}, "48,210.00")]
    sheet = S.marks_from_pages([({"sheet": "S", "row": 9}, "Travel\t\t48210.5")], "sheet")
    assert [m.locator for m in sheet] == [{"sheet": "S", "cell": "C9"}]
    word = S.marks_from_pages([({"table": 2, "row": 3}, "Retainer\t2,600.00")], "docx")
    assert [m.locator for m in word] == [{"table": 2, "row": 3, "col": 2}]


def test_ocr_pages_are_read_from_the_transcripts_own_labels():
    text = "[scanned — text recovered via OCR]\n\n[page 1]\nCover sheet\n[page 2]\nDue now\nBalance 9,999.50"
    smap = source_map_for("scan.pdf", "", text, {}, note="text recovered via OCR")
    assert smap and smap["labels"] is True
    marks = marks_for_output(text, smap)
    assert [(m.key, m.locator) for m in marks] == [("9999.5", {"page": 2, "line": 2})]
    # A transcript merged into the native pages cannot say where a native
    # page's text sits: no marks rather than a wrong page.
    assert source_map_for("scan.pdf", "", text, {}, note="… merged into the document's own text") is None


# --------------------------------------------------------------------------- #
# sheets, Word, CSV
# --------------------------------------------------------------------------- #
def test_excel_read_marks_the_cell(tmp_path, platform):
    book = make_xlsx(tmp_path / "book.xlsx")
    turn = SourceTurn(engine=platform.engine)
    res = _invoke(platform, "excel_read", {"path": str(book), "sheet": "Summary"},
                  _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, _ = turn.snapshot()
    assert [m["locator"] for m in _by_key(marks, "48210.5")] == [{"sheet": "Summary", "cell": "C7"}]
    assert [m["locator"] for m in _by_key(marks, "1250")] == [{"sheet": "Summary", "cell": "C2"}]
    row = [r for r in _rows(platform) if r["key"] == "48210.5"][0]
    assert row["kind"] == "sheet"
    assert "Summary!C7" in row["context"] and "Travel" in row["context"]
    # The model's answer did not change: no map in it.
    assert "source_map" not in res.output


def test_read_document_of_a_workbook_marks_cells_past_a_heading_with_a_line_break(tmp_path, platform):
    book = make_xlsx(tmp_path / "book.xlsx")
    turn = SourceTurn(engine=platform.engine)
    res = _invoke(platform, "read_document", {"path": str(book)}, _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, _ = turn.snapshot()
    assert [m["locator"] for m in _by_key(marks, "48210.5")] == [{"sheet": "Summary", "cell": "C7"}]
    assert [m["locator"] for m in _by_key(marks, "1200")] == [{"sheet": "Summary", "cell": "B2"}]


def test_excel_query_marks_the_matching_rows_cells(tmp_path, platform):
    book = make_xlsx(tmp_path / "book.xlsx")
    turn = SourceTurn(engine=platform.engine)
    res = _invoke(platform, "excel_query", {
        "path": str(book), "sheet": "Summary", "op": "filter",
        "where": [{"column": "Item", "op": "eq", "value": "Travel"}],
    }, _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, _ = turn.snapshot()
    assert [m["locator"] for m in _by_key(marks, "48210.5")] == [{"sheet": "Summary", "cell": "C7"}]
    # A smallest value IS a cell; a sum is worked out (30-b) and gets none.
    turn2 = SourceTurn(engine=platform.engine)
    _invoke(platform, "excel_query", {"path": str(book), "sheet": "Summary", "op": "max",
                                      "column": "C"}, _ctx(tmp_path, platform, turn2))
    assert [m["locator"] for m in _by_key(turn2.snapshot()[0], "48210.5")] == [
        {"sheet": "Summary", "cell": "C7"}]
    turn3 = SourceTurn(engine=platform.engine)
    _invoke(platform, "excel_query", {"path": str(book), "sheet": "Summary", "op": "sum",
                                      "column": "C"}, _ctx(tmp_path, platform, turn3))
    assert turn3.snapshot() == ([], 0)


def test_word_paragraph_and_table_cell(tmp_path, platform):
    doc = make_docx(tmp_path / "letter.docx")
    turn = SourceTurn(engine=platform.engine)
    res = _invoke(platform, "read_document", {"path": str(doc)}, _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, _ = turn.snapshot()
    assert [m["locator"] for m in _by_key(marks, "7450")] == [{"paragraph": 2}]
    assert [m["locator"] for m in _by_key(marks, "2600")] == [{"table": 1, "row": 2, "col": 2}]


def test_csv_line_and_column_and_a_text_file_line(tmp_path, platform):
    csv_file = tmp_path / "ledger.csv"
    csv_file.write_text("date,payee,amount\n2026-01-03,Acme,1500.25\n2026-01-09,Bolt,312\n",
                        encoding="utf-8")
    note = tmp_path / "notes.txt"
    note.write_text("first\nsecond line has 6,400 in it\n", encoding="utf-8")
    turn = SourceTurn(engine=platform.engine)
    ctx = _ctx(tmp_path, platform, turn)
    assert _invoke(platform, "read_document", {"path": str(csv_file)}, ctx).ok
    assert _invoke(platform, "read_document", {"path": str(note)}, ctx).ok
    marks, reads = turn.snapshot()
    assert reads == 2
    # A CSV figure's place is its record's row and column (the preview's).
    assert [m["locator"] for m in _by_key(marks, "1500.25")] == [{"row": 2, "col": 3}]
    assert [m["locator"] for m in _by_key(marks, "312")] == [{"row": 3, "col": 3}]
    assert [m["locator"] for m in _by_key(marks, "6400")] == [{"line": 2}]
    # Dates are not figures.
    assert not [m for m in marks if m["key"] in ("2026", "01", "03")]


def _ledger_with_a_line_break(path: Path) -> Path:
    import csv

    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Payee", "Memo", "Amount"])
        w.writerow(["Acme", "rent\nfor March", "1200.00"])  # a quoted cell with a line break
        w.writerow(["Bolt", "tools", "48210.50"])
        w.writerow(["", "", ""])
        w.writerow(["Cato", "fees", "7315.25"])
    return path


def _preview_places(path: Path) -> dict:
    """Each figure's row and column as the preview shows the file (csv.reader)."""
    import csv

    rows = list(csv.reader(path.open(newline="", encoding="utf-8")))
    return {v: {"row": r, "col": c} for r, row in enumerate(rows, 1) for c, v in enumerate(row, 1)}


def test_a_csv_cell_with_a_line_break_keeps_every_later_figure_in_its_row(tmp_path, platform):
    """Review B3: a quoted cell holding a line break made every later figure
    one line off (``line 4`` for the preview's row 3)."""
    ledger = _ledger_with_a_line_break(tmp_path / "ledger.csv")
    turn = SourceTurn(engine=platform.engine)
    assert _invoke(platform, "read_document", {"path": str(ledger)}, _ctx(tmp_path, platform, turn)).ok
    marks, _ = turn.snapshot()
    want = _preview_places(ledger)
    for value, key in (("1200.00", "1200"), ("48210.50", "48210.5"), ("7315.25", "7315.25")):
        assert [m["locator"] for m in _by_key(marks, key)] == [want[value]], (value, marks)
    assert want["48210.50"] == {"row": 3, "col": 3}


def test_an_attached_csv_with_a_line_break_is_placed_by_record_too(tmp_path):
    ledger = _ledger_with_a_line_break(tmp_path / "ledger.csv")
    layout: dict = {}
    text = extract_for_rag(ledger, layout=layout)
    smap = source_map_for(ledger, text, text, layout)
    marks = marks_for_output(text, smap)
    assert [(m.key, m.locator) for m in marks] == [
        ("1200", {"row": 2, "col": 3}), ("48210.5", {"row": 3, "col": 3}), ("7315.25", {"row": 5, "col": 3}),
    ]


def test_a_word_table_cell_after_a_cell_with_two_paragraphs_keeps_its_column():
    """Review B4: a cell holding two paragraphs carried no tab into the
    next line, so the figure after it was put one column left."""
    word = S.marks_from_pages([({"table": 1, "row": 3}, "x\tnote\nmore\t1,200")], "docx")
    assert [m.locator for m in word] == [{"table": 1, "row": 3, "col": 3}]


def test_a_ranged_excel_read_marks_the_cell_where_the_block_starts(tmp_path, platform):
    """Review A6(c): ``excel_read`` of ``B3:D5`` puts its first value at B3,
    so the figure in the block's last cell is D5 (not C3)."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws["B3"] = "Item"
    ws["C3"] = "Note"
    ws["D3"] = "Amount"
    ws["B5"] = "Travel"
    ws["D5"] = 48210.5
    book = tmp_path / "ranged.xlsx"
    wb.save(str(book))
    turn = SourceTurn(engine=platform.engine)
    res = _invoke(platform, "excel_read", {"path": str(book), "sheet": "Ledger", "range": "B3:D5"},
                  _ctx(tmp_path, platform, turn))
    assert res.ok, res.error
    marks, _ = turn.snapshot()
    assert [m["locator"] for m in _by_key(marks, "48210.5")] == [{"sheet": "Ledger", "cell": "D5"}]
    row = [r for r in _rows(platform) if r["key"] == "48210.5"][0]
    assert "Ledger!D5" in row["context"] and "Travel" in row["context"]


# --------------------------------------------------------------------------- #
# what is kept, and how much
# --------------------------------------------------------------------------- #
def test_the_kept_line_is_secret_masked(tmp_path, platform):
    note = tmp_path / "keys.txt"
    note.write_text("api_key=sk-livekeyvalue123456789 total 1,234.00\n", encoding="utf-8")
    turn = SourceTurn(engine=platform.engine)
    assert _invoke(platform, "read_document", {"path": str(note)}, _ctx(tmp_path, platform, turn)).ok
    rows = _rows(platform)
    assert [r["key"] for r in rows] == ["1234"]
    assert "livekeyvalue" not in rows[0]["context"]
    assert "123456789" not in json.dumps(rows)


def test_an_agent_run_writes_no_marks(tmp_path, platform):
    pdf = make_pdf(tmp_path / "statement.pdf")
    res = _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, None))
    assert res.ok
    assert _rows(platform) == []


def test_at_most_3000_marks_a_read(tmp_path):
    text = "\n".join(f"row {i} amount {1000 + i}.50" for i in range(4000))
    marks = marks_for_output(text, {"path": "x.txt", "kind": "text", "start": 0, "end": len(text)})
    assert len(marks) == S.MAX_MARKS_PER_READ == 3000


def test_a_remembered_read_records_marks_for_the_new_turn(tmp_path, platform):
    pdf = make_pdf(tmp_path / "statement.pdf")
    first = SourceTurn(turn_id="t1", engine=platform.engine)
    _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, first))
    second = SourceTurn(turn_id="t2", engine=platform.engine)
    res = _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, second))
    assert (res.data or {}).get("cached") is True
    marks, reads = second.snapshot()
    assert reads == 1 and [m["locator"] for m in _by_key(marks, "48210")] == [{"page": 7, "line": 3}]
    assert {r["turn_id"] for r in _rows(platform)} == {"t1", "t2"}


def test_marks_go_with_their_chat_unless_kept(tmp_path, platform):
    from iron_jarvis.core.models import ChatThreadRecord
    from iron_jarvis.daemon.routes.chat import delete_thread

    with session_scope(platform.engine) as db:
        db.add(ChatThreadRecord(id="thr_gone", title="x", messages_json="[]"))
        db.commit()
    pdf = make_pdf(tmp_path / "statement.pdf")
    book = make_xlsx(tmp_path / "book.xlsx")
    turn = SourceTurn(thread_id="thr_gone", engine=platform.engine)
    _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, turn))
    _invoke(platform, "read_document", {"path": str(book)}, _ctx(tmp_path, platform, turn))
    assert len(turn.snapshot()[0]) >= 4
    other = SourceTurn(thread_id="thr_other", engine=platform.engine)
    _invoke(platform, "extract_pdf", {"path": str(pdf)}, _ctx(tmp_path, platform, other))
    kept_id = turn.snapshot()[0][0]["id"]
    with platform.engine.begin() as conn:
        conn.execute(store.SourceMark.__table__.update()
                     .where(store.SourceMark.id == kept_id).values(kept=True))
    assert delete_thread(platform.engine, "thr_gone") is True
    left = _rows(platform)
    assert {r["thread_id"] for r in left} == {"thr_other", "thr_gone"}
    assert [r["id"] for r in left if r["thread_id"] == "thr_gone"] == [kept_id]


def test_a_new_chats_first_turn_gets_its_chat_when_linked(tmp_path, platform):
    pdf = make_pdf(tmp_path / "statement.pdf")
    turn = SourceTurn(turn_id="turn_new", engine=platform.engine)
    _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, turn))
    assert {r["thread_id"] for r in _rows(platform)} == {""}
    assert S.link_live_turn("turn_new", "thr_new") is True
    assert store.home_turn(platform.engine, "turn_new", "thr_new") > 0
    _invoke(platform, "extract_pdf", {"path": str(pdf)}, _ctx(tmp_path, platform, turn))
    assert {r["thread_id"] for r in _rows(platform)} == {"thr_new"}


def test_rows_that_never_got_a_chat_are_swept_after_two_days(tmp_path, platform):
    from datetime import timedelta

    from iron_jarvis.core.ids import utcnow

    pdf = make_pdf(tmp_path / "statement.pdf")
    turn = SourceTurn(engine=platform.engine)
    _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, turn))
    with platform.engine.begin() as conn:
        conn.execute(store.SourceMark.__table__.update()
                     .values(created_at=utcnow() - timedelta(days=3)))
    fresh = SourceTurn(thread_id="thr_fresh", engine=platform.engine)
    _invoke(platform, "extract_pdf", {"path": str(pdf)}, _ctx(tmp_path, platform, fresh))
    assert {r["thread_id"] for r in _rows(platform)} == {"thr_fresh"}
