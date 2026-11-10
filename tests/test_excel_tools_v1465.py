"""v1.465.0: the spreadsheet tools use worked-out values, keep them in the
file, and map a workbook well enough to review it.

* a formula nobody saved a result for is worked out by read, query and edit;
* an edit or a new workbook has every result written INTO the file beside its
  formula, so it reads correctly in any program (verified before the file is
  replaced; on any doubt the file is left exactly as it was);
* ``excel_edit`` answers each formula it wrote with what it comes to, and
  formats cells;
* ``excel_profile`` is a map: header row, column kinds, formulas filled down,
  totals, hidden sheets, and the mistakes a reviewer would stop at;
* ``excel_query`` lists every group or says what it left out, filters dates,
  and never rounds a difference away.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import shutil
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis.documents import excel_recalc as R
from iron_jarvis.documents.excel_map import formula_pattern, render, workbook_map
from iron_jarvis.documents.excel_tools import (
    ExcelEditTool,
    ExcelProfileTool,
    ExcelQueryTool,
    ExcelReadTool,
)
from iron_jarvis.tools.base import ToolContext

BOOKS = Path(__file__).parent / "fixtures" / "excel_truth_v1465" / "books.xlsx"


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r",
                       config=None, event_bus=None, engine=None)


def _run(tool, args, ws):
    return asyncio.run(tool.execute(args, _ctx(ws)))


def _inventory(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Inv"
    ws.append(["Item", "Qty", "Price", "Line"])
    for i, (a, q, p) in enumerate([("A", 2, 10), ("B", 3, 5), ("C", 1, 7)], start=2):
        ws.append([a, q, p, f"=B{i}*C{i}"])
    ws["A5"], ws["D5"] = "Total", "=SUM(D2:D4)"
    wb.save(path)
    return path


# ---------------------------------------------------- results kept in the file --


def test_results_are_written_beside_their_formulas_and_read_back(tmp_path):
    book = _inventory(tmp_path / "inv.xlsx")
    assert load_workbook(book, data_only=True)["Inv"]["D5"].value is None  # as the library left it
    res = R.store_results(book)
    assert (res.found, res.computed, res.stored) == (4, 4, 4) and not res.failed
    values, formulas = load_workbook(book, data_only=True)["Inv"], load_workbook(book)["Inv"]
    assert [values[f"D{i}"].value for i in (2, 3, 4, 5)] == [20, 15, 7, 42]
    assert formulas["D5"].value == "=SUM(D2:D4)", "the formula is still the formula"
    assert R.compute_missing(book).found == 0, "nothing is left unsaved"
    assert [p.name for p in tmp_path.iterdir()] == ["inv.xlsx"], "no working copy is left behind"


def test_text_dates_and_yes_no_results_are_stored_as_what_they_are(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws["A1"], ws["A2"] = "Acme & <Sons>", 5
    ws["B1"] = '=A1&" ok"'
    ws["B2"] = "=A2>3"
    ws["B3"] = "=EOMONTH(DATE(2026,1,15),0)"
    ws["B3"].number_format = "yyyy-mm-dd"
    ws["B4"] = '=" padded "'
    wb.save(tmp_path / "t.xlsx")
    res = R.store_results(tmp_path / "t.xlsx")
    assert res.stored == 4
    v = load_workbook(tmp_path / "t.xlsx", data_only=True).active
    assert v["B1"].value == "Acme & <Sons> ok" and v["B2"].value is True
    assert v["B3"].value == dt.datetime(2026, 1, 31) and v["B4"].value == " padded "
    assert res.values[(v.title, 3, 2)] == "2026-01-31", "a reader is handed the date, not a day number"


def test_what_cannot_be_worked_out_stays_blank_and_is_named(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"] = 4
    ws["A2"] = "=A1*2"
    ws["A3"] = '=INDIRECT("A1")'
    ws["A4"] = "=1/0"
    ws["A5"], ws["A6"] = "=A6", "=A5"
    wb.save(tmp_path / "t.xlsx")
    res = R.store_results(tmp_path / "t.xlsx")
    assert res.stored == 1 and set(res.failed) == {"S!A3", "S!A4", "S!A5", "S!A6"}
    assert "INDIRECT is not supported" in res.failed["S!A3"]
    assert "#DIV/0!" in res.failed["S!A4"] and "circular" in res.failed["S!A5"]
    v = load_workbook(tmp_path / "t.xlsx", data_only=True)["S"]
    assert v["A2"].value == 8 and v["A3"].value is None and v["A4"].value is None
    note = R.summary_note(res, stored=True)
    assert "Worked out 1 formula result(s) and saved them in the file" in note
    assert "WARNING: 4 formula cell(s) could not be worked out here" in note and "S!A3" in note


def test_a_file_excel_would_trust_is_never_given_a_stored_number(tmp_path):
    """Results are only stored where the workbook says "recalculate everything
    when opened". Without that flag Excel would show OUR number as its own."""
    book = _inventory(tmp_path / "inv.xlsx")
    plain = tmp_path / "plain.xlsx"
    with zipfile.ZipFile(book) as zin, zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/workbook.xml":
                data = data.replace(b' fullCalcOnLoad="1"', b"")
            zout.writestr(item, data)
    before = plain.read_bytes()
    res = R.store_results(plain)
    assert res.computed == 4 and res.stored == 0
    assert plain.read_bytes() == before, "the file was not touched"


def test_a_copy_that_does_not_read_back_is_thrown_away(tmp_path, monkeypatch):
    book = _inventory(tmp_path / "inv.xlsx")
    before = book.read_bytes()
    monkeypatch.setattr(R, "_stored_reads_back", lambda tmp, res: False)
    res = R.store_results(book)
    assert res.stored == 0 and book.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["inv.xlsx"]
    assert "could not be saved in the file" in R.summary_note(res, stored=True)


def test_the_read_back_check_catches_a_wrong_stored_value(tmp_path):
    book = _inventory(tmp_path / "inv.xlsx")
    res = R.compute_missing(book)
    good = tmp_path / "good.xlsx"
    shutil.copy(book, good)
    R.store_results(good)
    assert R._stored_reads_back(good, res) is True
    res.raw[("Inv", 5, 4)] = 999.0  # what we meant to store is not what is there
    assert R._stored_reads_back(good, res) is False


def test_a_huge_workbook_is_left_alone_in_words(tmp_path, monkeypatch):
    book = _inventory(tmp_path / "inv.xlsx")
    monkeypatch.setattr(R, "MAX_FILE_BYTES", 10)
    res = R.compute_missing(book)
    assert res.computed == 0 and "too large" in res.why_not


def test_a_bound_reached_is_counted_not_hidden(tmp_path):
    book = _inventory(tmp_path / "inv.xlsx")
    res = R.compute_missing(book, max_formulas=2)
    assert res.found == 4 and res.computed == 2 and res.skipped == 2
    assert "WARNING: 2 formula cell(s) could not be worked out here" in R.summary_note(res, stored=False)


def test_tidy_shows_a_number_as_excel_does():
    assert R.tidy(0.1 + 0.2) == 0.3 and R.tidy(42.0) == 42 and isinstance(R.tidy(42.0), int)
    assert R.tidy(True) is True and R.tidy("x") == "x"


# ------------------------------------------------------------------ excel_edit --


def test_an_edit_answers_each_formula_it_wrote_and_keeps_results_in_the_file(tmp_path):
    _inventory(tmp_path / "inv.xlsx")
    res = _run(ExcelEditTool(), {"path": "inv.xlsx", "edits": [
        {"cell": "B2", "value": 4},
        {"cell": "E1", "value": "Tax"},
        {"cell": "E5", "formula": "=ROUND(D5*7.25%,2)"},
        {"cell": "F5", "formula": 'XLOOKUP("B",A2:A4,D2:D4)'},  # no "=", a newer function
        {"cell": "G5", "formula": '=INDIRECT("A1")'},
    ]}, tmp_path)
    assert res.ok, res.error
    assert "Inv!E5 =ROUND(D5*7.25%,2) = 4.50" in res.output  # 62 * 7.25% = 4.495 -> 4.50
    assert 'Inv!F5 =XLOOKUP("B",A2:A4,D2:D4) = 15.00' in res.output
    assert "Inv!G5 =INDIRECT(\"A1\") = NOT WORKED OUT" in res.output
    assert "WARNING: 1 formula cell(s) could not be worked out here" in res.output
    by_cell = {r["cell"]: r for r in res.data["results"]}
    assert by_cell["E5"]["value"] == 4.5 and "error" in by_cell["G5"]
    v, f = load_workbook(tmp_path / "inv.xlsx", data_only=True)["Inv"], load_workbook(tmp_path / "inv.xlsx")["Inv"]
    assert v["D2"].value == 40 and v["D5"].value == 62, "the changed input flowed through every formula"
    assert f["F5"].value == '=_xlfn.XLOOKUP("B",A2:A4,D2:D4)', "stored the way Excel needs it"
    assert res.data["recalc"]["saved_in_file"] == res.data["recalc"]["worked_out"] == 6


def test_an_edit_formats_cells_and_ranges(tmp_path):
    _inventory(tmp_path / "inv.xlsx")
    res = _run(ExcelEditTool(), {"path": "inv.xlsx", "edits": [
        {"cell": "A1:D1", "bold": True, "fill": "#FFF2CC", "align": "center"},
        {"cell": "D5", "number_format": "#,##0.00", "bold": True, "font_color": "C00000"},
        {"cell": "A5", "italic": True, "wrap": True, "fill": "not-a-colour"},
    ]}, tmp_path)
    assert res.ok, res.error
    ws = load_workbook(tmp_path / "inv.xlsx")["Inv"]
    assert all(ws[f"{c}1"].font.bold for c in "ABCD") and ws["C1"].fill.fgColor.rgb == "FFFFF2CC"
    assert ws["B1"].alignment.horizontal == "center" and ws["A1"].value == "Item"
    assert ws["D5"].number_format == "#,##0.00" and ws["D5"].font.bold and ws["D5"].font.color.rgb == "FFC00000"
    assert ws["D5"].value == "=SUM(D2:D4)", "formatting a cell leaves what it holds"
    assert ws["A5"].font.italic and ws["A5"].alignment.wrap_text
    assert ws["A5"].fill.fgColor.rgb in ("00000000", None), "an unknown colour is ignored"


# ------------------------------------------------------------------ excel_read --


def test_a_read_works_out_what_nobody_saved_and_says_so(tmp_path):
    _inventory(tmp_path / "inv.xlsx")
    res = _run(ExcelReadTool(), {"path": "inv.xlsx", "sheet": "Inv", "range": "A4:D5"}, tmp_path)
    assert res.ok and res.data["rows"] == [["C", 1, 7, 7], ["Total", None, None, 42]]
    assert res.data["worked_out"] == 2
    asked = _run(ExcelReadTool(), {"path": "inv.xlsx", "sheet": "Inv", "range": "D5:D5",
                                   "include_formulas": True}, tmp_path)
    assert asked.data["rows"] == [["=SUM(D2:D4)"]] and "worked_out" not in asked.data


def test_a_read_names_the_cells_it_could_not_work_out(tmp_path):
    wb = Workbook()
    wb.active.title = "S"
    wb.active["A1"] = '=INDIRECT("B1")'
    wb.active["A2"] = "=2+2"
    wb.save(tmp_path / "t.xlsx")
    res = _run(ExcelReadTool(), {"path": "t.xlsx", "sheet": "S"}, tmp_path)
    assert res.data["rows"] == [[None], [4]]
    assert list(res.data["not_worked_out"]) == ["S!A1"] and res.data["not_worked_out_count"] == 1


# ----------------------------------------------------------------- excel_query --


def _ledger(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "L"
    ws.append(["Date", "Vendor", "Amount", "Rate"])
    for i in range(30):
        ws.append([dt.datetime(2026, 1 + i % 3, 1 + i % 28), f"Vendor {i:02d}", 100 + i, 0.004 + i * 0.0025])
    ws.append([dt.datetime(2026, 2, 1), "Refund Co", -5000, 0.01])
    wb.save(path)
    return path


def test_every_group_is_counted_and_the_cut_is_said(tmp_path):
    _ledger(tmp_path / "l.xlsx")
    res = _run(ExcelQueryTool(), {"path": "l.xlsx", "op": "group", "group_by": "Vendor",
                                  "column": "Amount"}, tmp_path)
    assert res.ok, res.error
    lines = res.output.splitlines()
    assert lines[1] == "- Refund Co: -5,000.00", "the largest group leads, whichever way it points"
    assert "(+11 more group(s) not listed, together" in res.output
    assert "Total of all 31 group(s): -1,565.00" in res.output  # sum(100..129) - 5000
    assert res.data["groups_total"] == 31 and len(res.data["groups"]) == 20
    full = _run(ExcelQueryTool(), {"path": "l.xlsx", "op": "group", "group_by": "Vendor",
                                   "column": "Amount", "limit": 200}, tmp_path)
    assert "more group(s)" not in full.output and len(full.data["groups"]) == 31


def test_a_date_filter_matches_dates(tmp_path):
    _ledger(tmp_path / "l.xlsx")
    q = ExcelQueryTool()
    one = _run(q, {"path": "l.xlsx", "op": "count", "column": "Vendor",
                   "where": [{"column": "Date", "op": "eq", "value": "2026-02-01"}]}, tmp_path)
    assert one.data["value"] == 2, one.output  # 1 Feb: one vendor row and the refund
    us = _run(q, {"path": "l.xlsx", "op": "count", "column": "Vendor",
                  "where": [{"column": "Date", "op": "ge", "value": "3/1/2026"}]}, tmp_path)
    later = _run(q, {"path": "l.xlsx", "op": "count", "column": "Vendor",
                     "where": [{"column": "Date", "op": "ge", "value": "2026-03-01"}]}, tmp_path)
    assert us.data["value"] == later.data["value"] == 10, "both spellings of 1 March"
    none = _run(q, {"path": "l.xlsx", "op": "count", "column": "Vendor",
                    "where": [{"column": "Date", "op": "eq", "value": "not a date"}]}, tmp_path)
    assert "(0 row(s) considered)" in none.output


def test_a_small_figure_is_not_rounded_to_nothing(tmp_path):
    _ledger(tmp_path / "l.xlsx")
    res = _run(ExcelQueryTool(), {"path": "l.xlsx", "op": "min", "column": "Rate"}, tmp_path)
    assert "MIN of Rate = 0.004 " in res.output, res.output
    avg = _run(ExcelQueryTool(), {"path": "l.xlsx", "op": "avg", "column": "Rate"}, tmp_path)
    assert "AVG of Rate = 0.039274 " in avg.output, "an average keeps its digits (it read 0.04)"


# --------------------------------------------------------------- the workbook map --


def test_the_map_finds_every_mistake_planted_in_the_books():
    data = workbook_map(BOOKS)
    by = {s["sheet"]: s for s in data["sheets"]}
    sales, pl = by["Sales"], by["P&L"]
    checks = "\n".join(sales["checks"] + pl["checks"] + by["Calc"]["checks"])
    assert "F17 is a typed number (1,200.00) in a column of formulas (=D2*E2 filled down)" in checks
    assert "G23 =ROUND(F22*TaxRate,2) does not follow its column's pattern" in checks
    assert "F43 =SUM(F2:F39) stops short: it leaves out F40:F41" in checks
    assert "1 number(s) in column E are stored as text (first: E30)" in checks
    assert "B7 (Total expenses and revenue) is typed as 99,999.00, but the 5 numbers above it add up to" in checks
    assert "A2 =1/0 comes to the error #DIV/0!" in checks
    # ...and a column that is right draws no note
    assert not any("column H" in c or c.startswith("H") for c in sales["checks"])


def test_the_map_says_what_each_sheet_is_and_how_it_is_laid_out():
    data = workbook_map(BOOKS)
    by = {s["sheet"]: s for s in data["sheets"]}
    assert by["Inputs"]["looks_like"].startswith("an inputs sheet")
    assert by["P&L"]["looks_like"] == "an income statement" and by["P&L"]["layout"].startswith("a statement")
    sales = by["Sales"]
    assert sales["header_row"] == 1 and sales["layout"].startswith("a table: header on row 1")
    kinds = {c["column"]: (c["header"], c["kind"]) for c in sales["columns"]}
    assert kinds["A"] == ("Date", "date") and kinds["B"] == ("Client", "text")
    assert kinds["D"] == ("Qty", "number") and kinds["H"] == ("Gross", "formula")
    assert next(c for c in sales["columns"] if c["column"] == "F")["format"] == "#,##0.00"
    assert {"range": "H2:H41", "formula": "=F2+G2", "cells": 40} in sales["formula_groups"]
    totals = {t["cell"]: t for t in sales["totals"]}
    assert totals["H43"]["label"] == "Total" and totals["H43"]["value"] == pytest.approx(23997.05)
    assert sales["frozen_at"] == "A2" and by["P&L"]["uses_sheets"] == ["Inputs", "Sales"]
    assert data["hidden_sheets"] == ["Calc"] and data["names"] == ["TaxRate = Inputs!$B$2"]
    assert data["formulas"] == 132 and data["formulas_unsaved"] == 132


def test_the_totals_in_the_map_are_excels_own_numbers():
    import json

    truth = json.loads((BOOKS.parent / "excel_truth.json").read_text(encoding="utf-8-sig"))["books.xlsx"]
    sales = next(s for s in workbook_map(BOOKS)["sheets"] if s["sheet"] == "Sales")
    for t in sales["totals"]:
        assert t["value"] == pytest.approx(float(truth[f"Sales!{t['cell']}"]["value"]), abs=1e-9)


def test_the_profile_tool_gives_the_map_and_keeps_its_headline(tmp_path):
    shutil.copy(BOOKS, tmp_path / "books.xlsx")
    res = _run(ExcelProfileTool(), {"path": "books.xlsx"}, tmp_path)
    assert res.ok, res.error
    lines = res.output.splitlines()
    assert lines[0] == "books.xlsx — 4 sheet(s)"
    assert "- Sales: 43x8 | headers: Date, Client, Region, Qty, Unit price, Net, Tax, Gross" in lines
    assert "- Calc [HIDDEN]: 2x1 | headers: scratch" in lines
    assert res.output.count("WORTH A LOOK:") == 7 and "hidden sheet(s): Calc" in lines[-1]
    assert len(res.output) < 4000, "a map, not a dump"


def test_a_workbook_the_map_cannot_take_gets_the_quick_view(tmp_path, monkeypatch):
    import iron_jarvis.documents.excel_map as M

    _inventory(tmp_path / "inv.xlsx")
    monkeypatch.setattr(M, "workbook_map", lambda path: (_ for _ in ()).throw(ValueError("too large")))
    res = _run(ExcelProfileTool(), {"path": "inv.xlsx"}, tmp_path)
    assert res.ok and res.data["quick"] is True
    assert "(quick view: sizes and headers only)" in res.output and "- Inv: 5x4" in res.output


def test_a_clean_table_draws_no_notes(tmp_path):
    book = _inventory(tmp_path / "inv.xlsx")
    sheet = workbook_map(book)["sheets"][0]
    assert sheet["checks"] == [] and "WORTH A LOOK" not in render("inv.xlsx", workbook_map(book))
    assert sheet["totals"][0] == {"cell": "D5", "label": "Total", "formula": "=SUM(D2:D4)", "value": 42}


@pytest.mark.parametrize("formula,row,col,want", [
    ("=B2*C2", 2, 4, "R[0]C[-2]*R[0]C[-1]"),
    ("=B9*C9", 9, 4, "R[0]C[-2]*R[0]C[-1]"),       # the same formula, filled down
    ("=B9*C8", 9, 4, "R[0]C[-2]*R[-1]C[-1]"),      # one row off: a different pattern
    ("=F2*$B$1", 2, 7, "R[0]C[-1]*R1C2"),          # a fixed cell stays fixed
    ("=sum(D2:D4)", 5, 4, "SUM(R[-3]C[0]:R[-1]C[0])"),
])
def test_a_formula_filled_down_is_one_pattern(formula, row, col, want):
    assert formula_pattern(formula, row, col) == want


# ----------------------------------------------------- large workbooks stay fast --


def test_a_workbook_with_no_formula_is_never_loaded_to_look_for_one(tmp_path, monkeypatch):
    """Measured before this guard: a 100,000-row sheet of plain numbers cost a
    16-second load after every write, to learn there was nothing to work out."""
    import iron_jarvis.documents.excel_formula as EF

    wb = Workbook()
    wb.active.append(["Item", "Amount"])
    wb.active.append(["Rent", 500])
    wb.save(tmp_path / "plain.xlsx")
    assert R.has_formulas(tmp_path / "plain.xlsx") is False
    assert R.has_formulas(_inventory(tmp_path / "inv.xlsx")) is True
    assert R.has_formulas(tmp_path / "missing.xlsx") is True, "cannot tell is never 'none'"

    def boom(*a, **kw):
        raise AssertionError("the workbook was loaded")

    monkeypatch.setattr(EF.Grid, "__init__", boom)
    res = R.store_results(tmp_path / "plain.xlsx")
    assert (res.found, res.stored, res.why_not) == (0, 0, "")
    from iron_jarvis.documents.excel_tools import uncached_formulas

    assert uncached_formulas(tmp_path / "plain.xlsx", None) == 0


def test_a_workbook_too_large_to_work_out_says_so_everywhere(tmp_path, monkeypatch):
    _inventory(tmp_path / "inv.xlsx")
    monkeypatch.setattr(R, "MAX_CELLS", 5)  # the sheet states 5 x 4 = 20 cells
    assert R.cell_estimate(tmp_path / "inv.xlsx") >= 20

    # a workbook written as a stream states no size: its XML's size is read instead
    stream = Workbook(write_only=True)
    sheet = stream.create_sheet("S")
    for i in range(400):
        sheet.append([i, i * 2, f"=A{i + 1}+B{i + 1}"])
    stream.save(tmp_path / "stream.xlsx")
    assert R.cell_estimate(tmp_path / "stream.xlsx") > 600, "1,200 cells, and no stated size"
    assert "too large" in R.too_large(tmp_path / "inv.xlsx")
    res = R.compute_missing(tmp_path / "inv.xlsx")
    assert res.computed == 0 and "too large" in res.why_not
    assert "its formulas were not worked out (the workbook is too large" in R.summary_note(res, stored=True)
    # a question over it leaves the unsaved formulas out, and says how many
    q = _run(ExcelQueryTool(), {"path": "inv.xlsx", "op": "sum", "column": "Line"}, tmp_path)
    assert not q.ok and "no numeric values" in q.error, "every Line cell is an unsaved formula"
    assert "WARNING: 4 cell(s) on this sheet hold a formula" in q.error
    # an edit says its results were not worked out
    e = _run(ExcelEditTool(), {"path": "inv.xlsx", "edits": [{"cell": "E1", "formula": "=1+1"}]}, tmp_path)
    assert e.ok and "NOT WORKED OUT" in e.output and "its formulas were not worked out" in e.output
    # the profile falls back to the quick view
    p = _run(ExcelProfileTool(), {"path": "inv.xlsx"}, tmp_path)
    assert p.ok and p.data["quick"] is True
