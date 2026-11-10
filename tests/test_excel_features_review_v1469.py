"""The review of Excel's features in a SAVED workbook (v1.469.0,
documents/excel_features).

Each step refused here was WRITTEN by the first cut, and the file it left was
opened in the real Excel on the reviewer's PC (a hidden Excel of the
script's own, read-only, by full path, 2026-11-10):

* a name that differs from an existing one only by case ("amounts" beside
  "Amounts"): Excel kept ONE of the two and dropped the other without a word;
* a name equal to a table's name, a name or a table called "c", "R1C1" or
  "R2C2", a table whose heading cell holds a formula, a table laid over the
  sheet's own filter: Excel REFUSED TO OPEN the workbook.

Also red on the first cut: autofit walked every column of the sheet for every
row (minutes on a sheet with one cell far to the right), a note on a merged
cell raised a raw error, and a number limit with min above max was written.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis.documents import excel_features as F
from iron_jarvis.documents.excel_tools import ExcelEditTool
from iron_jarvis.tools.base import ToolContext


@pytest.fixture()
def book(tmp_path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Client", "Amount", "Status"])
    for i in range(1, 6):
        ws.append([f"c{i}", i * 10, "Open"])
    ws["B7"] = "=SUM(B2:B6)"
    lists = wb.create_sheet("Lists")
    lists.append(["Open"])
    lists.append(["Closed"])
    path = tmp_path / "b.xlsx"
    wb.save(path)
    return path


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


def _edit(book: Path, features, **more):
    return asyncio.run(ExcelEditTool().execute({"path": book.name, "features": features, **more}, _ctx(book.parent)))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refused(book: Path, features, words: str) -> None:
    before = _sha(book)
    res = _edit(book, features)
    assert not res.ok, "this step leaves a workbook Excel will not open"
    assert words in res.error and res.error.endswith("Nothing was changed."), res.error
    assert _sha(book) == before


# ---------------------------------------------------------------------- names --


def test_a_name_that_differs_only_by_case_replaces_the_old_one(book):
    assert _edit(book, [{"op": "name", "name": "Amounts", "refers_to": "Data!B2:B6"}]).ok
    res = _edit(book, [{"op": "name", "name": "amounts", "refers_to": "Lists!A1:A2"}])
    assert res.ok, res.error
    names = load_workbook(book).defined_names
    assert [k.lower() for k in names.keys()] == ["amounts"], "Excel reads names without regard to case: two would lose one"
    assert names[next(iter(names.keys()))].attr_text == "'Lists'!$A$1:$A$2"
    assert "now means" in res.output


@pytest.mark.parametrize("name", ["c", "R", "rc", "R1C1", "r12", "C5", "R3C", "TRUE", "false"])
def test_a_name_excel_reads_as_a_cell_is_refused(book, name):
    _refused(book, [{"op": "name", "name": name, "refers_to": "Lists!A1:A2"}], "is not a cell address")


def test_a_name_may_not_be_a_tables_name(book):
    assert _edit(book, [{"op": "table", "range": "Data!A1:C6", "name": "Sales"}]).ok
    _refused(book, [{"op": "name", "name": "sales", "refers_to": "Lists!A1:A2"}], "already has a table called Sales")


def test_a_table_and_a_name_made_in_one_call_may_not_share_a_name(book):
    _refused(book, [{"op": "table", "range": "Data!A1:C6", "name": "Picks"}, {"op": "name", "name": "Picks", "refers_to": "Data!B2:B6"}],
             "already has a table called Picks")
    _refused(book, [{"op": "name", "name": "Picks", "refers_to": "Data!B2:B6"}, {"op": "table", "range": "Data!A1:C6", "name": "picks"}],
             "already has a name called picks")


@pytest.mark.parametrize("name", ["Margin", "Tax_Rate", "_x", "Sales.2026", "Rate", "Cost", "Rows"])
def test_ordinary_names_are_still_written(book, name):
    res = _edit(book, [{"op": "name", "name": name, "refers_to": "Data!B2:B6"}])
    assert res.ok, res.error
    assert name in load_workbook(book).defined_names


# --------------------------------------------------------------------- tables --


@pytest.mark.parametrize("name", ["R2C2", "c", "r"])
def test_a_table_name_excel_reads_as_a_cell_is_refused(book, name):
    _refused(book, [{"op": "table", "range": "Data!A1:C6", "name": name}], "a table's name")


def test_a_table_is_not_laid_over_a_formula_heading(book):
    wb = load_workbook(book)
    wb["Data"]["C1"] = '="Sta"&"tus"'
    wb.save(book)
    _refused(book, [{"op": "table", "range": "Data!A1:C6"}], "C1 holds a formula")


def test_a_table_is_not_laid_over_the_sheets_own_filter(book):
    wb = load_workbook(book)
    wb["Data"].auto_filter.ref = "A1:C6"
    wb.save(book)
    _refused(book, [{"op": "table", "range": "Data!A1:C6"}], "already has a filter on A1:C6")
    _refused(book, [{"op": "table", "range": "Data!B1:C4"}], "already has a filter on A1:C6")


def test_a_table_is_not_laid_over_merged_cells(book):
    wb = load_workbook(book)
    wb["Data"].merge_cells("A8:B8")
    wb["Data"].merge_cells("C3:C4")
    wb.save(book)
    _refused(book, [{"op": "table", "range": "Data!A1:C6"}], "merged cells (C3:C4)")


def test_a_table_beside_those_is_still_written(book):
    wb = load_workbook(book)
    wb["Data"].merge_cells("E1:F2")
    wb["Lists"].auto_filter.ref = "A1:A2"
    wb.save(book)
    res = _edit(book, [{"op": "table", "range": "Data!A1:C6", "name": "Sales"}])
    assert res.ok, res.error
    assert "Sales" in load_workbook(book)["Data"].tables


# ---------------------------------------------------------------- other steps --


def test_a_step_that_fails_in_any_way_changes_nothing_and_says_so(book):
    wb = load_workbook(book)
    wb["Data"].merge_cells("E1:F2")
    wb.save(book)
    _refused(book, [{"op": "freeze", "sheet": "Data", "cell": "B2"}, {"op": "note", "cell": "Data!F2", "text": "hi"}], "step 2 (note)")


@pytest.mark.parametrize(
    ("step", "words"),
    [
        ({"op": "validation", "range": "Data!B2:B6", "type": "whole", "min": 10, "max": 5}, "'min' is above 'max'"),
        ({"op": "validation", "range": "Data!B2:B6", "type": "date", "min": "2026-12-31", "max": "2026-01-01"}, "'min' is above 'max'"),
        ({"op": "validation", "range": "Data!B2:B6", "type": "whole", "min": 1.5}, "whole number"),
    ],
)
def test_a_limit_nothing_could_meet_is_refused(book, step, words):
    _refused(book, [step], words)


def test_autofit_looks_only_at_the_columns_it_sizes(book, monkeypatch):
    """One cell far to the right made the first cut walk 16,384 columns for
    every row (minutes). Counted, not timed."""
    wb = load_workbook(book)
    wb["Data"]["XFD3000"] = 1
    wb.save(book)
    seen = {"cells": 0}
    real = F._autofit_cells

    def counting(ws, first, last, rows):
        for cell in real(ws, first, last, rows):
            seen["cells"] += 1
            yield cell

    monkeypatch.setattr(F, "_autofit_cells", counting)
    res = _edit(book, [{"op": "autofit", "sheet": "Data", "columns": "A:C"}])
    assert res.ok, res.error
    assert seen["cells"] == 19, "the 19 cells columns A to C hold, and not the one in XFD"
    ws = load_workbook(book)["Data"]
    assert ws.column_dimensions["A"].width == 8 and ws.max_column == 16384 and ws.max_row == 3000
    # With no columns named: the first 200, never all 16,384.
    seen["cells"] = 0
    assert _edit(book, [{"op": "autofit", "sheet": "Data"}]).ok
    assert seen["cells"] <= 200 * 3000
