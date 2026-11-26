"""Intelligence plan, release 5: what a made file SAYS is checked by rule.

Real files (an .xlsx written by openpyxl and by the app's own writer, a
.docx by python-docx, a PDF by pypdf) go through ``checks/content``: a
spreadsheet with an error cell fails and the finding names the cell; a
formula with no saved result is worked out by the app's own evaluator, and
"no error cells" is said only when every one was; totals that add up are
counted and a wrong one is named with both figures, in ONE unambiguous shape
only (the table of shapes below holds every layout, right and wrong, with the
answer the rule must give); an empty document fails; a PDF's page count is
compared with what the reply states for that file, in four plain forms and
never when the reply changed pages. A check that breaks, a file that is too
large, and a reply that runs out of time read "not checked", never "passed".
Nothing here calls a model.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from iron_jarvis.checks import content
from iron_jarvis.checks import record as R
from iron_jarvis.checks.record import (
    DOC_TEXT,
    FAILED,
    FILE_OPENS,
    NOT_CHECKED,
    PASSED,
    PDF_PAGES,
    SHEET_ERRORS,
    SHEET_TOTALS,
    Check,
    line_for,
)
from iron_jarvis.documents.writers import write_document


def _xlsx(path: Path, rows, title: str = "Summary", more=None) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    for name, extra in (more or {}).items():
        sheet = wb.create_sheet(name)
        for row in extra:
            sheet.append(row)
    wb.save(path)
    return path


def _by(checks: list[Check], code: str) -> list[Check]:
    return [c for c in checks if c.check == code]


def _rows(checks: list[Check]) -> list[dict]:
    return [{"check": c.check, "file": c.file, "result": c.result, "detail": c.detail, "by": c.by, "step": c.step}
            for c in checks]


BUDGET = [["Item", "Amount"], ["Rent", 1200], ["Food", 300], ["Travel", 150.5]]


# --------------------------------------------------------------------------- #
# Error cells
# --------------------------------------------------------------------------- #
def test_a_spreadsheet_with_an_error_cell_fails_and_the_finding_names_the_cell(tmp_path):
    p = _xlsx(tmp_path / "summary.xlsx", [*BUDGET, ["Share", "#DIV/0!"]])
    got = content.check_files([str(p)])
    (errors,) = _by(got, SHEET_ERRORS)
    assert (errors.result, errors.detail, errors.file, errors.by) == (
        FAILED, "Summary!B5 holds #DIV/0!", "summary.xlsx", "rule")
    assert _by(got, FILE_OPENS)[0].result == PASSED
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the spreadsheet opens. Problem in the spreadsheet: Summary!B5 holds #DIV/0!."
    assert tone == "warn"


def test_a_clean_spreadsheet_passes_and_many_error_cells_are_counted_not_listed(tmp_path):
    clean = _xlsx(tmp_path / "clean.xlsx", BUDGET)
    assert _by(content.check_files([str(clean)]), SHEET_ERRORS)[0] == Check(
        SHEET_ERRORS, PASSED, "no error cells", file="clean.xlsx")
    bad = _xlsx(tmp_path / "bad.xlsx", [["a", "#REF!"], ["b", "#N/A"], ["c", "#VALUE!"], ["d", "#NAME?"], ["e", "#NUM!"]])
    (errors,) = _by(content.check_files([str(bad)]), SHEET_ERRORS)
    assert errors.result == FAILED
    assert errors.detail == (
        "Summary!B1 holds #REF!; Summary!B2 holds #N/A; Summary!B3 holds #VALUE!; and 2 more cells hold an error")


def test_a_formula_whose_saved_result_is_an_error_is_an_error_cell(tmp_path, monkeypatch):
    """A workbook saved by a spreadsheet program keeps each formula's last
    result. openpyxl writes none, so the saved results are handed in the way
    its second, values-only read returns them."""
    p = _xlsx(tmp_path / "saved.xlsx", [["Ratio", "=B2/B3"], ["a", 4], ["b", 0]])
    real = content._load

    def with_saved(path, late=content._never_late):
        book = real(path, late)
        book.values["Summary"][(1, 2)] = "#DIV/0!"
        return book

    monkeypatch.setattr(content, "_load", with_saved)
    (errors,) = _by(content.check_files([str(p)]), SHEET_ERRORS)
    assert (errors.result, errors.detail) == (FAILED, "Summary!B1 holds #DIV/0!")


# --------------------------------------------------------------------------- #
# A formula with NO saved result is worked out: "no error cells" is said only
# when every filled cell holds a value or a formula worked out without an error
# --------------------------------------------------------------------------- #
#: The reviewer's rows: the app's own writer warns that D3 is #DIV/0!, saves a
#: result for D2 and none for D3.
DIVIDES = [["Name", "Amount", "Parts", "Each"], ["A", 100, 4, "=B2/C2"], ["B", 50, 0, "=B3/C3"]]


def test_a_formula_the_apps_own_writer_could_not_save_a_result_for_is_an_error_cell(tmp_path):
    p = tmp_path / "summary.xlsx"
    warnings: list[str] = []
    write_document(p, DIVIDES, warnings=warnings)
    assert warnings and "D3" in warnings[0] and "#DIV/0!" in warnings[0], warnings
    book = content._load(p)
    assert book.values["Sheet"].get((2, 4)) == 25 and book.values["Sheet"].get((3, 4)) is None, (
        "the file should hold a saved result for D2 and none for D3")
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, FAILED, "Sheet!D3 works out to #DIV/0!", file="summary.xlsx")]
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the spreadsheet opens. Problem in the spreadsheet: Sheet!D3 works out to #DIV/0!."
    assert tone == "warn" and "no error cells" not in line


@pytest.mark.parametrize("formula,error", [
    ("=1/0", "#DIV/0!"),
    ('=VLOOKUP("nobody",A1:B2,2,FALSE)', "#N/A"),
    ('="a"+1', "#VALUE!"),
])
def test_a_formula_with_no_saved_result_that_works_out_to_an_error_fails(tmp_path, formula, error):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["j", 2], ["f", formula]])
    assert content._load(p).values["Summary"].get((3, 2)) is None, "the file really holds no saved result"
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, FAILED, f"Summary!B3 works out to {error}", file="t.xlsx")]


def test_a_formula_that_cannot_be_worked_out_is_not_checked_and_the_line_never_says_no_error_cells(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["a", "=NOSUCHFUNCTION(B1)"], ["b", "=Missing!A1"], ["c", "=B1*2"]])
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "2 formulas have no saved result and could not be worked out here", file="t.xlsx")]
    line, tone = line_for(_rows(got))
    assert line == ("Checked by Iron Jarvis: the spreadsheet opens. Not checked: the spreadsheet "
                    "(2 formulas have no saved result and could not be worked out here).")
    assert tone == "quiet" and "no error cells" not in line
    one = _xlsx(tmp_path / "one.xlsx", [["k", 1], ["a", "=NOSUCHFUNCTION(B1)"]])
    assert _by(content.check_files([str(one)]), SHEET_ERRORS)[0].detail == (
        "1 formula has no saved result and could not be worked out here")


def test_formulas_that_all_work_out_without_an_error_read_no_error_cells(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "Qty", "Each", "Amount"], ["a", 2, 5, "=B2*C2"], ["b", 3, 7, "=B3*C3"],
                                    ["Share", None, None, "=IFERROR(D2/0,0)"]])
    book = content._load(p)
    assert not [v for v in book.values["Summary"].values() if isinstance(v, (int, float)) and v in (10, 21)], (
        "openpyxl saved no result: the pass below is the evaluator's")
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "no error cells", file="t.xlsx")]


def test_an_error_and_a_formula_that_could_not_be_worked_out_are_both_said(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["a", "=B1/0"], ["b", "=NOSUCHFUNCTION(B1)"]])
    got = {c.result: c.detail for c in _by(content.check_files([str(p)]), SHEET_ERRORS)}
    assert got == {FAILED: "Summary!B2 works out to #DIV/0!",
                   NOT_CHECKED: "1 formula has no saved result and could not be worked out here"}


def test_the_working_out_is_capped_and_what_is_past_the_cap_is_counted_not_passed(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], *[["f", "=B1+1"] for _ in range(5)]])
    monkeypatch.setattr(content, "MAX_WORKED", 2)
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "3 formulas have no saved result and could not be worked out here", file="t.xlsx")]
    # ... and a formula that reads more cells than one may is not worked out.
    monkeypatch.setattr(content, "MAX_WORKED", 20_000)
    monkeypatch.setattr(content, "MAX_FORMULA_READS", 3)
    wide = _xlsx(tmp_path / "wide.xlsx", [[1], [2], [3], [4], [5], ["=SUM(A1:A5)"]])
    assert _by(content.check_files([str(wide)]), SHEET_ERRORS)[0].detail == (
        "1 formula has no saved result and could not be worked out here")


def test_a_workbook_read_only_in_part_has_no_formula_worked_out(tmp_path, monkeypatch):
    """Half a column would come to the wrong number: nothing is worked out
    over a workbook that was cut, and nothing is called an error by it."""
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["f", "=1/0"], ["x", 2], ["y", 3]])
    asked: list[int] = []
    real = content._work_out
    monkeypatch.setattr(content, "_work_out", lambda *a, **kw: (asked.append(1), real(*a, **kw))[1])
    monkeypatch.setattr(content, "MAX_CELLS", 3)
    got = _by(content.check_files([str(p)]), SHEET_ERRORS)
    assert [c.result for c in got] == [NOT_CHECKED] and asked == []


# --------------------------------------------------------------------------- #
# Totals: the table of shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Shape:
    name: str
    rows: list
    #: How many totals "add up".
    good: int = 0
    #: The cells called wrong, as "B5".
    wrong: tuple = ()
    #: How many totals could not be worked out by rule.
    unknown: int = 0
    #: True for a sheet whose totals are all RIGHT (or that has none): the
    #: rule must never call one wrong.
    correct: bool = True
    note: str = field(default="", compare=False)


HDR = ["Item", "Amount"]

SHAPES: list[Shape] = [
    # ---- the nine everyday shapes that read "Problem" before the fix (all CORRECT sheets) ----
    Shape("a blank cell inside the data",
          [HDR, ["Rent", 100], ["Parking", None], ["Food", 300], ["Total", 400]], good=1),
    Shape("groups separated by a blank row",
          [HDR, ["Rent", 100], ["Food", 300], [None, None], ["Fuel", 50], ["Tolls", 20], ["Total", 470]], good=1),
    Shape("a summary list with a line called Total revenue",
          [["Metric", "Value"], ["Orders", 120], ["Customers", 45], ["Total revenue", 45000]]),
    Shape("a payee called Total Wine & More",
          [["Payee", "Amount"], ["Acme", 100], ["Total Wine & More", 250], ["Zed", 50]]),
    Shape("year columns typed as numbers",
          [["Item", 2023, 2024], ["Rent", 100, 110], ["Food", 300, 310], ["Total", 400, 420]], unknown=2,
          note="the year above the list cannot be told from an amount: not judged right, never wrong"),
    Shape("a running balance column with a total row",
          [["Month", "Amount", "Balance"], ["Jan", 100, 100], ["Feb", 50, 150], ["Total", 150, 150]], good=1),
    Shape("an invoice number column with a count in the total row",
          [["Client", "Invoice no", "Amount"], ["Acme", 1001, 100], ["Zed", 1002, 200], ["Total", 2, 300]], good=1),
    Shape("an hourly column with a blended rate in the total row",
          [["Name", "Hours", "Hourly", "Amount"], ["A", 10, 50, 500], ["B", 20, 80, 1600],
           ["Total", 30, 70, 2100]], good=2),
    Shape("one number typed as text among numbers",
          [HDR, ["a", 100], ["b", "200"], ["c", 300], ["Total", 600]], unknown=1,
          note="what a person reads adds up, a spreadsheet's own SUM would leave the text out: not judged"),
    # ---- right, in the shapes that were already right ----
    Shape("a plain list", [HDR, ["Rent", 1200], ["Food", 300], ["Travel", 150.5], ["Total", 1650.5]], good=1),
    Shape("three totals in one row",
          [["Item", "Q1", "Q2", "Q3"], ["Rent", 1200, 1200, 1250], ["Food", 300, 310.25, 290],
           ["Total", 1500, 1510.25, 1540]], good=3),
    Shape("a total written rounded", [HDR, ["a", 33.4], ["b", 66.9], ["Total", 100]], good=1),
    Shape("negative amounts", [HDR, ["Sales", 1000], ["Refunds", -150], ["Total", 850]], good=1),
    Shape("the label in capitals with a colon", [HDR, ["a", 1], ["b", 2], ["TOTAL:", 3]], good=1),
    Shape("a grand total label", [HDR, ["a", 1], ["b", 2], ["Grand Total", 3]], good=1),
    Shape("the plain word in German", [["Posten", "Betrag"], ["a", 1], ["b", 2], ["Summe", 3]], good=1),
    Shape("the plain word in Italian", [["Voce", "Importo"], ["a", 1], ["b", 2], ["Totale", 3]], good=1),
    Shape("one blank row before the total", [HDR, ["Rent", 100], ["Food", 300], [None, None], ["Total", 400]], good=1),
    Shape("subtotals and a grand total",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", 1050], ["Fees", 20], ["Interest", 5],
           ["Subtotal", 25], ["Grand total", 1075]], good=3),
    Shape("the total row above the data", [["Total", 400], ["Rent", 100], ["Food", 300]], unknown=1),
    Shape("two tables side by side",
          [["Item", "Amount", None, "Item", "Amount"], ["a", 1, None, "x", 10], ["b", 2, None, "y", 20],
           ["Total", 3, None, "Total", 30]], good=2),
    Shape("two tables one above the other",
          [HDR, ["a", 1], ["b", 2], ["Total", 3], [None, None], HDR, ["c", 5], ["d", 6], ["Total", 11]], good=2),
    Shape("a totals column of sums", [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, 3], ["b", 10, 20, 30]], good=1),
    Shape("a totals column of quantity times price",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 7.5], ["ink", 2, 10, 20]], good=1),
    Shape("a percent column in the total row",
          [["Item", "Amount", "Share %"], ["a", 25, 25], ["b", 75, 75], ["Total", 100, 50]], good=1),
    Shape("a rate column in the total row",
          [["Item", "Amount", "Rate"], ["a", 33.4, 0.2], ["b", 66.9, 0.4], ["Total", 100, 0.3]], good=1),
    Shape("a unit price column in the total row",
          [["Item", "Qty", "Unit price"], ["a", 2, 5], ["b", 3, 7], ["Total", 5, 6]], good=1),
    Shape("an average column in the total row",
          [["Team", "Sales", "Avg deal"], ["a", 100, 10], ["b", 300, 30], ["Total", 400, 20]], good=1),
    Shape("a column headed Year 1 is a column of amounts",
          [["Item", "Year 1", "Year 2"], ["a", 1, 10], ["b", 2, 20], ["Total", 3, 30]], good=2),
    Shape("a column of tax years is not added",
          [["Client", "Tax year", "Amount"], ["a", 2023, 5], ["b", 2024, 6], ["Total", 2, 11]], good=1),
    Shape("a title above the heading",
          [["Budget 2024", None], [None, None], HDR, ["Rent", 100], ["Food", 300], ["Total", 400]], good=1),
    Shape("rows labelled by a date",
          [["Date", "Amount"], [datetime(2024, 1, 1), 100], [datetime(2024, 1, 2), 300], ["Total", 400]], good=1),
    Shape("amounts written as currency text",
          [HDR, ["a", "$100"], ["b", "$300"], ["Total", 400]], unknown=1),
    Shape("a profit and loss layout",
          [["", "2024"], ["Revenue", 1000], ["Total revenue", 1000], ["Cost of sales", -400], ["Gross profit", 600]]),
    Shape("an invoice with a subtotal, tax and a total",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 7.5], ["ink", 2, 10, 20], ["Subtotal", None, None, 27.5],
           ["Tax", None, None, 2.2], ["Total", None, None, 29.7]], good=2),
    Shape("a total that is a plain SUM of exactly the list",
          [HDR, ["a", 100], ["b", 300], ["Total", "=SUM(B2:B3)"]], good=1),
    Shape("a SUM total after a blank row",
          [HDR, ["a", 100], ["b", 300], [None, None], ["Total", "=SUM(B2:B3)"]], good=1),
    Shape("SUM subtotals and a grand total that adds them",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", "=SUM(B2:B3)"], ["Fees", 20],
           ["Interest", 5], ["Subtotal", "=SUM(B5:B6)"], ["Grand total", "=B4+B7"]], good=2, unknown=1),
    Shape("a list of formulas under a SUM",
          [["Item", "Qty", "Each", "Amount"], ["a", 2, 5, "=B2*C2"], ["b", 3, 7, "=B3*C3"],
           ["Total", 5, None, "=SUM(D2:D3)"]], good=2),
    Shape("a totals column of SUM formulas",
          [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, "=SUM(B2:C2)"], ["b", 10, 20, "=SUM(B3:C3)"]], good=1),
    Shape("a SUBTOTAL formula", [HDR, ["a", 100], ["b", 300], ["Total", "=SUBTOTAL(9,B2:B3)"]], unknown=1),
    Shape("an AVERAGE in a row called total", [HDR, ["a", 100], ["b", 300], ["Total", "=AVERAGE(B2:B3)"]], unknown=1),
    # ---- a formula total that does NOT add the list above it: never "adds up", never "wrong" ----
    Shape("a SUM that leaves the last row out",
          [HDR, ["a", 100], ["b", 300], ["c", 50], ["Total", "=SUM(B2:B3)"]], unknown=1, correct=False),
    Shape("a SUM that starts one row late",
          [HDR, ["a", 100], ["b", 300], ["c", 50], ["Total", "=SUM(B3:B4)"]], unknown=1, correct=False),
    Shape("a total that is one cell", [HDR, ["a", 100], ["b", 300], ["Total", "=B2"]], unknown=1, correct=False),
    Shape("a SUM of another column",
          [["Item", "Amount", "Other"], ["a", 100, 1], ["b", 300, 2], ["Total", "=SUM(C2:C3)", 3]], good=1, unknown=1,
          correct=False),
    Shape("a grand total whose SUM covers only the last group",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", "=SUM(B2:B3)"], ["Fees", 20],
           ["Interest", 5], ["Grand total", "=SUM(B5:B6)"]], good=1, unknown=1, correct=False),
    Shape("a SUM total far below its list",
          [HDR, ["a", 100], ["b", 300], [None, None], [None, None], [None, None], ["Total", "=SUM(B2:B3)"]], good=1),
    Shape("a SUM that takes a year heading in",
          [["Item", 2023], ["a", 100], ["b", 300], ["Total", "=SUM(B1:B3)"]], unknown=1, correct=False),
    Shape("a SUM under a year typed as a number",
          [["Item", 2023], ["a", 100], ["b", 300], ["Total", "=SUM(B2:B3)"]], unknown=1),
    Shape("a totals column whose SUM leaves a month out",
          [["Name", "Jan", "Feb", "Mar", "Total"], ["a", 1, 2, 3, "=SUM(B2:C2)"]], unknown=1, correct=False),
    Shape("a totals column of products",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, "=B2*C2"], ["ink", 2, 10, "=B3*C3"]], unknown=1),
    # ---- WRONG, in the one clean shape: said, with both figures ----
    Shape("a wrong total under a clean list",
          [HDR, ["Rent", 1200], ["Food", 300], ["Travel", 150.5], ["Total", 1700]], wrong=("B5",), correct=False),
    Shape("one right and one wrong total",
          [["Item", "A", "B"], ["x", 1, 10], ["y", 2, 20], ["Total", 3, 31]], good=1, wrong=("C4",), correct=False),
    Shape("three wrong totals",
          [["Item", "A", "B", "C"], ["x", 1, 10, 100], ["y", 2, 20, 200], ["Total", 4, 31, 301]],
          wrong=("B4", "C4", "D4"), correct=False),
    Shape("a wrong subtotal", [HDR, ["a", 1], ["b", 2], ["Subtotal", 99]], wrong=("B4",), correct=False),
    Shape("a wrong total in the lower of two tables",
          [HDR, ["a", 1], ["b", 2], ["Total", 3], [None, None], HDR, ["c", 5], ["d", 6], ["Total", 12]],
          good=1, wrong=("B9",), correct=False),
    # ---- a mismatch in any looser shape: "could not be worked out", never "wrong" ----
    Shape("a mismatch after a blank row",
          [HDR, ["Rent", 100], ["Food", 300], [None, None], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch with no heading", [["Rent", 100], ["Food", 300], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch under one row", [HDR, ["Rent", 100], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch where the rows are numbered, not named",
          [["No", "Amount"], [1, 100], [2, 300], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch with a blank cell inside",
          [HDR, ["Rent", 100], ["Parking", None], ["Food", 300], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch under a list with a blank cell higher up",
          [HDR, ["a", 100], ["b", None], ["c", 300], ["d", 200], ["Total", 999]], unknown=1, correct=False),
    Shape("a total that leaves out a number typed as text",
          [HDR, ["a", 100], ["b", "200"], ["c", 300], ["Total", 400]], unknown=1, correct=False,
          note="a spreadsheet's SUM says 400, a person reading the list says 600: neither right nor wrong"),
    Shape("a mismatch under a dash in the middle of the list",
          [HDR, ["a", 100], ["b", "-"], ["c", 300], ["d", 200], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch with a number typed as text",
          [HDR, ["a", 100], ["b", "200"], ["c", 300], ["Total", 999]], unknown=1, correct=False),
    Shape("a mismatch under a year typed as text",
          [["Item", "2023"], ["a", 1], ["b", 2], ["Total", 99]], unknown=1, correct=False),
    Shape("a mismatch over subtotals",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", 1050], ["Fees", 20], ["Interest", 5],
           ["Subtotal", 25], ["Grand total", 999]], good=2, unknown=1, correct=False),
    Shape("a typed total over a list of formulas",
          [["Item", "Qty", "Each", "Amount"], ["a", 2, 5, "=B2*C2"], ["b", 3, 7, "=B3*C3"],
           ["Total", 5, None, 99]], good=1, unknown=1, correct=False),
    Shape("a mismatch over an error value", [HDR, ["a", 100], ["b", "#N/A"], ["Total", 100]], unknown=1,
          correct=False),
    Shape("a totals column that is neither a sum nor a product",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 99]], unknown=1, correct=False),
    # ---- a figure beside words that are not, WHOLE, a total word: not judged at all ----
    Shape("a wrong figure beside Total due", [HDR, ["a", 1], ["b", 2], ["Total due", 99]], correct=False),
    Shape("a figure beside Totals by region", [["Totals by region", 99], ["North", 1], ["South", 2]]),
    Shape("a rate total that is not the sum of the rates",
          [["Item", "Rate"], ["a", 0.2], ["b", 0.4], ["Total", 0.3]]),
]

def _judged(path: Path) -> "tuple[tuple[int, tuple, int], list[Check]]":
    """``((good, wrong cells, unknown), the totals rows)`` as the checks say it."""
    got = _by(content.check_files([str(path)]), SHEET_TOTALS)
    good, wrong, unknown = 0, (), 0
    for c in got:
        if c.result == PASSED:
            good = int(re.fullmatch(r"(\d+) totals? adds? up", c.detail).group(1))
        elif c.result == FAILED:
            wrong = tuple(re.findall(r"!([A-Z]+\d+) says ", c.detail))
        else:
            unknown = int(re.match(r"(\d+) totals? could not be worked out by rule: ", c.detail).group(1))
    return (good, wrong, unknown), got


def _write(how: str, path: Path, rows) -> Path:
    if how == "openpyxl":
        return _xlsx(path, rows)
    write_document(path, rows)       # the app's own writer (a None cell is an empty one)
    return path


def test_the_table_holds_every_everyday_shape():
    assert len(SHAPES) >= 40 and len({s.name for s in SHAPES}) == len(SHAPES)
    assert sum(1 for s in SHAPES if s.correct) >= 30 and sum(1 for s in SHAPES if s.wrong) >= 5
    # A correct sheet is never expected to read "wrong".
    assert not [s.name for s in SHAPES if s.correct and s.wrong]


@pytest.mark.parametrize("how", ["openpyxl", "the app's own writer"])
@pytest.mark.parametrize("shape", SHAPES, ids=[s.name for s in SHAPES])
def test_every_shape_of_total_gets_the_answer_the_table_says(tmp_path, shape, how):
    p = _write(how, tmp_path / "sheet.xlsx", shape.rows)
    got, rows = _judged(p)
    assert got == (shape.good, tuple(shape.wrong), shape.unknown), (shape.name, how, [c.detail for c in rows])
    if shape.correct:
        assert not [c for c in rows if c.result == FAILED], f"a correct sheet reads 'Problem': {shape.name}"
        line, tone = line_for(_rows(content.check_files([str(p)])))
        assert tone == "quiet" and "Problem" not in line, (shape.name, line)
    if not shape.good and not shape.wrong and not shape.unknown:
        assert rows == [], "a figure that is not beside a total word was judged"


def test_a_wrong_total_is_named_with_what_it_says_and_what_the_cells_add_up_to(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1700]])
    got = _by(content.check_files([str(p)]), SHEET_TOTALS)
    assert got == [Check(
        SHEET_TOTALS, FAILED, "Summary!B5 says 1,700 and the cells above it add up to 1,650.50", file="t.xlsx")]
    line, tone = line_for(_rows(content.check_files([str(p)])))
    assert tone == "warn" and line.endswith(
        "Problem in the spreadsheet: Summary!B5 says 1,700 and the cells above it add up to 1,650.50.")


def test_one_right_and_one_wrong_total_are_both_said(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "A", "B"], ["x", 1, 10], ["y", 2, 20], ["Total", 3, 31]])
    got = {c.result: c.detail for c in _by(content.check_files([str(p)]), SHEET_TOTALS)}
    assert got == {PASSED: "1 total adds up", FAILED: "Summary!C4 says 31 and the cells above it add up to 30"}


def test_a_mismatch_in_a_loose_shape_says_why_in_plain_words(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["Rent", 100], ["Food", 300], ["Total", 999]])
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [Check(
        SHEET_TOTALS, NOT_CHECKED,
        "1 total could not be worked out by rule: Summary!B3 could not be added up: the rows above it could not be "
        "read as one list", file="t.xlsx")]


def test_a_percent_format_is_not_a_total_to_add(tmp_path):
    p = tmp_path / "t.xlsx"
    wb = Workbook()
    for row in (["Item", "Growth"], ["a", 0.2], ["b", 0.4], ["Total", 0.3]):
        wb.active.append(row)
    wb.active["B4"].number_format = "0.0%"
    wb.save(p)
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == []


def test_a_formula_total_with_no_saved_result_is_added_up_never_read_as_zero(tmp_path):
    """openpyxl writes ``=SUM(B2:B4)`` with no saved result. Read as zero it
    would be "says 0, cells add up to 1,650.50": a false alarm on every sheet
    the app itself writes."""
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", "=SUM(B2:B4)"]])
    book = content._load(p)
    assert book.values["Summary"].get((5, 2)) is None, "the file really holds no saved result"
    assert book.number("Summary", 5, 2) == pytest.approx(1650.5)
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="t.xlsx")]


def test_a_formula_total_adds_up_only_when_it_sums_exactly_the_list_above_it(tmp_path):
    """S1: ``=SUM(B2:B3)`` over data in B2:B4 used to read "1 total adds
    up" (it adds what it names). It is "not checked", with both ranges."""
    short = _xlsx(tmp_path / "short.xlsx", [*BUDGET, ["Total", "=SUM(B2:B3)"]])
    assert _by(content.check_files([str(short)]), SHEET_TOTALS) == [Check(
        SHEET_TOTALS, NOT_CHECKED,
        "1 total could not be worked out by rule: Summary!B5 adds B2 to B3, and the list above it runs from B2 to B4",
        file="short.xlsx")]
    one_cell = _xlsx(tmp_path / "cell.xlsx", [*BUDGET, ["Total", "=B2"]])
    assert _by(content.check_files([str(one_cell)]), SHEET_TOTALS) == [Check(
        SHEET_TOTALS, NOT_CHECKED,
        "1 total could not be worked out by rule: Summary!B5 is worked out by a formula this check cannot follow",
        file="cell.xlsx")]
    plus = _xlsx(tmp_path / "plus.xlsx", [*BUDGET, ["Total", "=B2+B3+B4"]])
    assert [c.result for c in _by(content.check_files([str(plus)]), SHEET_TOTALS)] == [NOT_CHECKED]


def test_a_formula_total_whose_saved_result_differs_from_its_cells_is_not_checked_never_wrong(tmp_path, monkeypatch):
    """A file another program wrote may hold any saved result for a formula
    (one common writer saves 0 for every one); a spreadsheet program works it
    out again on opening. Said, with both figures; never "Problem"."""
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", "=SUM(B2:B4)"]])
    real = content._load

    def stale(path, late=content._never_late):
        book = real(path, late)
        book.values["Summary"][(5, 2)] = 1500
        return book

    monkeypatch.setattr(content, "_load", stale)
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [Check(
        SHEET_TOTALS, NOT_CHECKED,
        "1 total could not be worked out by rule: Summary!B5 has a saved result of 1,500 and its cells add up to "
        "1,650.50: a spreadsheet program works it out again when it opens the file", file="t.xlsx")]


def test_a_total_that_cannot_be_worked_out_by_rule_is_not_checked_never_passed(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [
        ["Item", "Amount"],
        ["Rent", 1200],
        ["Food", "=VLOOKUP(A3,Other!A:B,2,0)"],     # no saved result, and no such sheet
        ["Total", "=SUM(B2:B3)"],
        ["Grand total", "=AVERAGE(B2:B3)*2"],
    ])
    checks = content.check_files([str(p)])
    got = _by(checks, SHEET_TOTALS)
    assert [c.result for c in got] == [NOT_CHECKED]
    assert got[0].detail == (
        "2 totals could not be worked out by rule: Summary!B4 adds cells this check cannot work out, and 1 more")
    assert _by(checks, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "3 formulas have no saved result and could not be worked out here", file="t.xlsx")]


def test_a_grand_total_over_subtotals_is_accepted_and_a_wrong_one_is_not_called_wrong_by_guess(tmp_path):
    rows = [
        ["Line", "Amount"],
        ["Sales", 1000], ["Other", 50], ["Subtotal", 1050],
        ["Fees", 20], ["Interest", 5], ["Subtotal", 25],
    ]
    good = _xlsx(tmp_path / "good.xlsx", [*rows, ["Grand total", 1075]])
    assert _by(content.check_files([str(good)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "3 totals add up", file="good.xlsx")]
    odd = _xlsx(tmp_path / "odd.xlsx", [*rows, ["Grand total", 999]])
    got = {c.result: c.detail for c in _by(content.check_files([str(odd)]), SHEET_TOTALS)}
    assert got[PASSED] == "2 totals add up"
    assert got[NOT_CHECKED].startswith("1 total could not be worked out by rule: Summary!B8 sits under other totals")
    assert FAILED not in got


def test_a_total_column_of_sums_or_of_quantity_times_price_is_one_total(tmp_path):
    sums = _xlsx(tmp_path / "sums.xlsx", [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, 3], ["b", 10, 20, 30]])
    assert _by(content.check_files([str(sums)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="sums.xlsx")]
    invoice = _xlsx(tmp_path / "inv.xlsx", [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 7.5], ["ink", 2, 10, 20]])
    assert _by(content.check_files([str(invoice)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="inv.xlsx")]
    other = _xlsx(tmp_path / "other.xlsx", [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 99]])
    (only,) = _by(content.check_files([str(other)]), SHEET_TOTALS)
    assert only.result == NOT_CHECKED and "'Total' column of Summary" in only.detail


def test_a_sheet_with_no_labelled_total_says_nothing_about_totals(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", BUDGET)
    got = content.check_files([str(p)])
    assert _by(got, SHEET_TOTALS) == []
    assert line_for(_rows(got))[0] == "Checked by Iron Jarvis: the spreadsheet opens, no error cells."


@pytest.mark.parametrize("cell,is_label", [
    ("Total", True), ("TOTAL:", True), ("  totals. ", True), ("Grand Total", True), ("Sub-total", True),
    ("Sub total:", True), ("Subtotal", True), ("Sum", True), ("Summe", True), ("Totale", True), ("Somme", True),
    ("Total revenue", False), ("Total Wine & More", False), ("Totals by region", False), ("Total due", False),
    ("Net total", False), ("Total (USD)", False), ("Subtotal: fees", False), ("=SUM(A1:A2)", False), (12, False),
])
def test_a_total_label_is_the_whole_cell(cell, is_label):
    assert content._is_label(cell) is is_label


@pytest.mark.parametrize("heading,added", [
    ("Amount", True), ("Q1", True), ("Year 1", True), ("Units", True), ("No. of items", True), ("Hours", True),
    ("Rate", False), ("Unit price", False), ("Share %", False), ("Avg deal", False), ("Hourly", False),
    ("Margin", False), ("Balance", False), ("Running total", False), ("Cumulative", False), ("Per head", False),
    ("Invoice no", False), ("Invoice No.", False), ("Order #", False), ("Client ID", False), ("Ref", False),
    ("Cost code", False), ("Tax year", False), ("Due date", False), ("Ratio", False),
])
def test_a_heading_that_says_the_column_is_not_added(heading, added):
    assert (content._NOT_ADDED_RX.search(heading) is None) is added


@pytest.mark.parametrize("formula,want", [
    ("=SUM(B2:B5)", [("", 2, 2, 5, 2)]),
    ("=sum($B$2:$B$5, D2)", [("", 2, 2, 5, 2), ("", 2, 4, 2, 4)]),
    ("=B2+B3+C4", [("", 2, 2, 2, 2), ("", 3, 2, 3, 2), ("", 4, 3, 4, 3)]),
    ("=SUM('My Sheet'!A1:A3)", [("My Sheet", 1, 1, 3, 1)]),
    ("=SUM(B:B)", None),
    ("=B2*B3", None),
    ("=B2-B3", None),
    ("=AVERAGE(B2:B5)", None),
    ("=SUM(B2:B5)*2", None),
    ("=B2:B5+C1", None),
    ("1500", None),
])
def test_only_a_plain_sum_of_cells_is_followed(formula, want):
    assert content.parse_sum(formula) == want


# --------------------------------------------------------------------------- #
# Documents and PDFs
# --------------------------------------------------------------------------- #
def test_an_empty_word_document_fails_and_one_with_text_passes(tmp_path):
    import docx

    empty = tmp_path / "empty.docx"
    docx.Document().save(str(empty))
    got = content.check_files([str(empty)])
    assert _by(got, DOC_TEXT) == [Check(DOC_TEXT, FAILED, "it has no text", file="empty.docx")]
    assert line_for(_rows(got))[0] == (
        "Checked by Iron Jarvis: the document opens. Problem in the document: it has no text.")
    full = tmp_path / "letter.docx"
    d = docx.Document()
    d.add_paragraph("Dear Sam, the figures are attached.")
    d.save(str(full))
    assert _by(content.check_files([str(full)]), DOC_TEXT) == [Check(DOC_TEXT, PASSED, "has text", file="letter.docx")]


def test_a_text_file_of_blank_space_has_no_text(tmp_path):
    blank = tmp_path / "notes.md"
    blank.write_text("   \n\n\t\n", encoding="utf-8")
    assert content.check_files([str(blank)]) == [
        Check(FILE_OPENS, PASSED, "opens", file="notes.md"),
        Check(DOC_TEXT, FAILED, "it has no text", file="notes.md"),
    ]
    page = tmp_path / "page.html"
    page.write_text("<html><body><p> </p></body></html>", encoding="utf-8")
    assert _by(content.check_files([str(page)]), DOC_TEXT)[0].result == FAILED
    words = tmp_path / "words.txt"
    words.write_text("hello", encoding="utf-8")
    assert _by(content.check_files([str(words)]), DOC_TEXT) == [Check(DOC_TEXT, PASSED, "has text", file="words.txt")]


def _pdf(path: Path, pages: int) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_a_pdf_page_count_the_reply_states_wrongly_fails(tmp_path):
    p = _pdf(tmp_path / "report.pdf", 3)
    wrong = content.check_files([str(p)], "I saved report.pdf (5 pages) in your folder.")
    assert _by(wrong, PDF_PAGES) == [Check(PDF_PAGES, FAILED, "it has 3 pages and the reply says 5", file="report.pdf")]
    right = content.check_files([str(p)], "Here is the three-page report.pdf you asked for.")
    assert _by(right, PDF_PAGES) == [Check(PDF_PAGES, PASSED, "has the 3 pages the reply says", file="report.pdf")]
    silent = content.check_files([str(p)], "I saved report.pdf.")
    assert _by(silent, PDF_PAGES) == [Check(PDF_PAGES, PASSED, "has 3 pages", file="report.pdf")]


@pytest.mark.parametrize("reply", [
    "I removed page 3 from the 5-page contract.pdf.",
    "I saved contract.pdf, 2 pages shorter than before.",
    "I deleted the cover, so contract.pdf (5 pages) is ready.",
    "I split the 9-page contract.pdf and kept the first part.",
    "I merged both files into contract.pdf (5 pages).",
    "contract.pdf has 5 pages; I added a signature page at the end.",
    "I extracted the schedule: contract.pdf is 5 pages.",
    "The 5-page contract.pdf was trimmed.",
    "Combined into the 2-page contract.pdf.",
    "I inserted the exhibit, so contract.pdf (3 pages) is complete.",
    "The old contract.pdf (5 pages) was too long; this one is shorter.",
    "I dropped the appendix from contract.pdf (5 pages).",
    "I cut the cover from the 5-page contract.pdf.",
    "I appended the schedule: contract.pdf (3 pages).",
    "contract.pdf (5 pages) before; it now has fewer.",
    "contract.pdf is 5 pages, one longer than the draft.",
])
def test_a_reply_that_changed_pages_states_no_count_and_never_reads_problem(tmp_path, reply):
    """S2: a real 4-page PDF and a truthful reply read "Problem in the PDF"
    because the count before the change was taken for the count now."""
    p = _pdf(tmp_path / "contract.pdf", 4)
    assert content.stated_pages(reply, "contract.pdf") is None
    assert _by(content.check_files([str(p)], reply), PDF_PAGES) == [
        Check(PDF_PAGES, PASSED, "has 4 pages", file="contract.pdf")]


@pytest.mark.parametrize("reply,want", [
    # The four forms a count is read in.
    ("Saved report.pdf (3 pages).", 3),
    ("report.pdf has two pages.", 2),
    ("`report.pdf` is 12 pages.", 12),
    ("The 4-page report.pdf is ready.", 4),
    ("A one page report.pdf.", 1),
    ("report.pdf has only 3 pages.", 3),
    ("I rotated the scans: report.pdf (3 pages).", 3),           # turning a page keeps the count
    # Any other wording states nothing for this file.
    ("`report.pdf`, 12 pages, is in the folder.", None),
    ("report.pdf: 12 pages.", None),
    ("report.pdf with 12 pages.", None),
    ("report.pdf runs to 12 pages.", None),
    ("A one page PDF report.pdf.", None),
    ("I read your 12-page contract and saved report.pdf.", None),
    ("report.pdf was built from the 40 pages you sent.", None),
    ("See page 3 of report.pdf.", None),
    ("report.pdf (3 pages). Later: report.pdf (4 pages).", None),   # two counts: none is taken
    ("other.pdf (9 pages)", None),
    ("", None),
])
def test_only_a_count_tied_to_the_files_own_name_in_a_plain_form_is_read(reply, want):
    assert content.stated_pages(reply, "report.pdf") == want


# --------------------------------------------------------------------------- #
# Never raises, bounded, off the loop, only readable files
# --------------------------------------------------------------------------- #
def test_a_check_that_raises_is_not_checked_never_passed(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1650.5]])

    def broken(_path, _late=None):
        raise RuntimeError("the reader broke")

    monkeypatch.setattr(content, "_load", broken)
    got = content.check_files([str(p)])
    assert got == [
        Check(FILE_OPENS, PASSED, "opens", file="t.xlsx"),
        Check(SHEET_ERRORS, NOT_CHECKED, content.BROKE, file="t.xlsx"),
    ]
    assert line_for(_rows(got))[0] == (
        "Checked by Iron Jarvis: the spreadsheet opens. Not checked: the spreadsheet (it could not be checked here).")

    def one_broken(_path, _reply="", **_kw):
        raise ValueError("no")

    assert content.check_files([str(p)], one=one_broken) == [Check(FILE_OPENS, NOT_CHECKED, content.BROKE, file="t.xlsx")]


def test_an_evaluator_that_breaks_is_not_checked_never_no_error_cells(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["f", "=B1*2"]])

    def no_grid(_book, _late):
        raise RuntimeError("the evaluator broke")

    monkeypatch.setattr(content, "_grid", no_grid)
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "1 formula has no saved result and could not be worked out here", file="t.xlsx")]


def test_a_file_past_the_size_cap_is_not_opened(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "big.xlsx", BUDGET)
    opened: list[str] = []
    monkeypatch.setattr(content, "MAX_BYTES", 100)
    monkeypatch.setattr(content, "_load", lambda path, late=None: opened.append(str(path)))
    (only,) = content.check_files([str(p)])
    assert (only.check, only.result) == (FILE_OPENS, NOT_CHECKED)
    assert only.detail.startswith("it is too large to check here (")
    assert opened == []


def test_a_package_that_unpacks_too_large_is_opened_by_no_reader(tmp_path, monkeypatch):
    """The cap comes BEFORE the "does it open" look, which loads a workbook
    too: a small file that unpacks huge is opened by nothing."""
    import docx

    from iron_jarvis.deliverables import check as deliverables_check

    book = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1650.5]])
    letter = tmp_path / "letter.docx"
    d = docx.Document()
    d.add_paragraph("hello")
    d.save(str(letter))
    opened: list[str] = []
    real = deliverables_check.check_file
    monkeypatch.setattr(deliverables_check, "check_file", lambda p: (opened.append(str(p)), real(p))[1])
    monkeypatch.setattr(content, "_load", lambda path, late=None: opened.append(str(path)))
    monkeypatch.setattr(content, "MAX_UNPACKED_BYTES", 10)
    assert content.check_files([str(book), str(letter)]) == [
        Check(FILE_OPENS, NOT_CHECKED, "it unpacks too large to check here", file="t.xlsx"),
        Check(FILE_OPENS, NOT_CHECKED, "it unpacks too large to check here", file="letter.docx"),
    ]
    assert opened == []


def _declares(path: Path, out: Path, ref: bytes) -> Path:
    """A copy of a workbook whose first sheet DECLARES the size *ref*."""
    import zipfile

    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                start = data.index(b'<dimension ref="') + len(b'<dimension ref="')
                data = data[:start] + ref + data[data.index(b'"', start):]
            zout.writestr(item, data)
    return out


def test_a_sheet_is_read_by_its_real_cells_never_by_the_size_it_declares(tmp_path, monkeypatch):
    """A sheet may declare any size. Read as declared, each of its rows is
    padded out to 16,384 columns: a long sheet would be stepped through for
    minutes. Its real cells are read, and the blanks between two far-apart
    cells of one row are counted against their own cap."""
    small = _xlsx(tmp_path / "small.xlsx", [*BUDGET, ["Total", 1650.5]])
    huge = _declares(small, tmp_path / "huge.xlsx", b"A1:XFD1048576")
    monkeypatch.setattr(content, "MAX_VISITED", 100)       # 5 rows of 2 cells are 10
    assert [(c.check, c.result, c.detail) for c in content.check_files([str(huge)])] == [
        (FILE_OPENS, PASSED, "opens"), (SHEET_ERRORS, PASSED, "no error cells"),
        (SHEET_TOTALS, PASSED, "1 total adds up"),
    ]
    wide = Workbook()
    wide.active.append(["Total", 5])
    wide.active["XFD1"] = 5
    wide.save(tmp_path / "wide.xlsx")
    got = content.check_files([str(tmp_path / "wide.xlsx")])
    assert [(c.check, c.result) for c in got] == [
        (FILE_OPENS, PASSED), (SHEET_ERRORS, NOT_CHECKED), (SHEET_TOTALS, NOT_CHECKED)]


def test_a_workbook_with_too_many_cells_is_not_passed(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1650.5]])
    monkeypatch.setattr(content, "MAX_CELLS", 3)
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS)[0].result == NOT_CHECKED
    assert _by(got, SHEET_TOTALS) == [
        Check(SHEET_TOTALS, NOT_CHECKED, "it has too many cells to add its totals up here", file="t.xlsx")]


class _Clock:
    """A clock a test moves by hand: it reads ``at`` and counts its looks."""

    def __init__(self, late_after: "int | None" = None, late_at: float = 99.0) -> None:
        self.at, self.looks, self.late_after, self.late_at = 0.0, 0, late_after, late_at

    def __call__(self) -> float:
        self.looks += 1
        if self.late_after is not None and self.looks > self.late_after:
            return self.late_at
        return self.at


def test_the_deadline_is_read_from_the_clock_and_what_is_left_is_not_checked(tmp_path, monkeypatch):
    files = [str(_xlsx(tmp_path / f"f{i}.xlsx", BUDGET)) for i in range(3)]
    clock = _Clock()
    real = content.check_one

    def one(path, reply="", **kw):
        out = real(path, reply, **kw)
        clock.at = 9.0             # the first file took the whole deadline
        return out

    got = content.check_files(files, clock=clock, deadline_s=4.0, one=one)
    by_file = {name: [c for c in got if c.file == name] for name in ("f0.xlsx", "f1.xlsx", "f2.xlsx")}
    assert [c.result for c in by_file["f0.xlsx"]] == [PASSED, PASSED]
    assert by_file["f1.xlsx"] == [Check(FILE_OPENS, NOT_CHECKED, content.OUT_OF_TIME, file="f1.xlsx")]
    assert by_file["f2.xlsx"] == [Check(FILE_OPENS, NOT_CHECKED, content.OUT_OF_TIME, file="f2.xlsx")]


def test_the_deadline_is_looked_at_inside_the_cell_walk_not_only_between_files(tmp_path):
    """N1: one long workbook used to be walked to its end whatever the
    clock said (6.4 s for 400,000 cells against a 4 s deadline). The walk now
    stops on the row where the deadline runs out."""
    p = _xlsx(tmp_path / "long.xlsx", [["Item", "Amount"], *[[f"r{i}", i] for i in range(600)], ["Total", 1]])
    clock = _Clock(late_after=40)          # the start, the look before the file, then one a row
    got = content.check_files([str(p)], clock=clock, deadline_s=4.0)
    assert got == [
        Check(FILE_OPENS, PASSED, "opens", file="long.xlsx"),
        Check(SHEET_ERRORS, NOT_CHECKED, content.OUT_OF_TIME, file="long.xlsx"),
    ]
    assert clock.looks < 100, f"the walk went on for {clock.looks} looks after the deadline ran out"


def test_the_deadline_is_looked_at_before_each_formula_and_each_total(tmp_path, monkeypatch):
    rows = [["Item", "Amount"], ["a", 1], ["b", "=B2*2"], ["c", "=B2*3"], ["d", "=B2*4"], ["Total", 10]]
    p = _xlsx(tmp_path / "t.xlsx", rows)
    # Late from the first formula on: none is worked out, and none is passed.
    state = {"late": False}
    real_load = content._load

    def load_then_late(path, late=content._never_late):
        book = real_load(path, late)
        state["late"] = True
        return book

    monkeypatch.setattr(content, "_load", load_then_late)
    clock = lambda: 99.0 if state["late"] else 0.0   # noqa: E731
    got = content.check_files([str(p)], clock=clock, deadline_s=4.0)
    assert _by(got, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "3 formulas have no saved result and could not be worked out here", file="t.xlsx")]
    assert _by(got, SHEET_TOTALS) == [Check(SHEET_TOTALS, NOT_CHECKED, content.OUT_OF_TIME, file="t.xlsx")]


def test_the_deadline_is_looked_at_before_each_figure_of_a_wide_total_row(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "Q1", "Q2", "Q3"], ["a", 1, 2, 3], ["b", 1, 2, 3], ["Total", 2, 4, 6]])
    state = {"late": False}
    real = content._row_total

    def first_then_late(*a, **kw):
        out = real(*a, **kw)
        state["late"] = True           # the first figure took the rest of the deadline
        return out

    monkeypatch.setattr(content, "_row_total", first_then_late)
    got = content.check_files([str(p)], clock=lambda: 99.0 if state["late"] else 0.0, deadline_s=4.0)
    assert _by(got, SHEET_TOTALS) == [Check(SHEET_TOTALS, NOT_CHECKED, content.OUT_OF_TIME, file="t.xlsx")]
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, PASSED, "no error cells", file="t.xlsx")], (
        "what was checked before the deadline ran out was thrown away")


def test_only_the_first_files_of_a_reply_are_checked_and_the_rest_are_said(tmp_path):
    files = [str(_xlsx(tmp_path / f"f{i}.xlsx", BUDGET)) for i in range(4)]
    got = content.check_files(files + [files[0]], max_files=2)      # a file named twice is one file
    assert {c.file for c in got if c.result == PASSED} == {"f0.xlsx", "f1.xlsx"}
    assert got[-1] == Check(FILE_OPENS, NOT_CHECKED, "2 more files were not checked: only the first 2 of a reply are")
    assert line_for(_rows(got))[0].endswith(
        "Not checked: the other files (2 more files were not checked: only the first 2 of a reply are).")


def test_two_files_of_the_same_name_in_two_folders_are_told_apart_by_the_folders_name(tmp_path):
    """N2: both were "summary.xlsx", so the line said one file's checks
    twice under one name. The folder's own name goes beside it; never a path."""
    (tmp_path / "2023").mkdir()
    (tmp_path / "2024").mkdir()
    old = _xlsx(tmp_path / "2023" / "summary.xlsx", [*BUDGET, ["Total", 1650.5]])
    new = _xlsx(tmp_path / "2024" / "summary.xlsx", [*BUDGET, ["Total", 1700]])
    alone = _xlsx(tmp_path / "other.xlsx", BUDGET)
    got = content.check_files([str(old), str(new), str(alone)])
    assert list(dict.fromkeys(c.file for c in got)) == ["summary.xlsx (in 2023)", "summary.xlsx (in 2024)", "other.xlsx"]
    assert not [c.file for c in got if "/" in c.file or "\\" in c.file], "a path reached the record"
    line, tone = line_for(_rows(got))
    assert line == (
        "Checked by Iron Jarvis: summary.xlsx (in 2023) opens, no error cells, 1 total adds up; "
        "summary.xlsx (in 2024) opens, no error cells; other.xlsx opens, no error cells. "
        "Problem in summary.xlsx (in 2024): Summary!B5 says 1,700 and the cells above it add up to 1,650.50."
    ), line
    # The name as the record stores it survives the record's own cleaning, and reads as a spreadsheet.
    assert R._clean(got[0]).file == "summary.xlsx (in 2023)" and R.kind_words("summary.xlsx (in 2023)") == "the spreadsheet"
    # Same name AND same folder name, further up: still two names.
    (tmp_path / "a" / "x").mkdir(parents=True)
    (tmp_path / "b" / "x").mkdir(parents=True)
    twins = [str(_xlsx(tmp_path / side / "x" / "s.xlsx", BUDGET)) for side in ("a", "b")]
    assert list(content.names_for(twins).values()) == ["s.xlsx (in x)", "s.xlsx (in another folder, 2)"]
    # One file, or files with different names: the bare name, as before.
    assert list(content.names_for([str(alone)]).values()) == ["other.xlsx"]


def test_a_page_count_is_looked_for_by_the_files_own_name_even_when_two_share_it(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    files = [str(_pdf(tmp_path / side / "report.pdf", 3)) for side in ("a", "b")]
    got = _by(content.check_files(files, "I saved report.pdf (3 pages) in both folders."), PDF_PAGES)
    assert got == [Check(PDF_PAGES, PASSED, "has the 3 pages the reply says", file="report.pdf (in a)"),
                   Check(PDF_PAGES, PASSED, "has the 3 pages the reply says", file="report.pdf (in b)")]


async def test_the_checks_run_off_the_event_loop_and_a_stuck_one_is_waited_for_only_so_long(tmp_path):
    p = str(_xlsx(tmp_path / "t.xlsx", BUDGET))
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def watched(paths, reply, **kw):
        seen.append(threading.get_ident())
        return content.check_files(paths, reply, **kw)

    got = await content.check_made_files([p], worker=watched)
    assert seen and seen[0] != loop_thread, "the files were opened on the event loop"
    assert [c.result for c in got] == [PASSED, PASSED]

    release = threading.Event()

    def stuck(paths, reply, **kw):
        release.wait(30)
        return []

    try:
        late = await content.check_made_files([p], worker=stuck, hard_wait_s=0.05)
    finally:
        release.set()
    assert late == [Check(FILE_OPENS, NOT_CHECKED, content.OUT_OF_TIME, file="t.xlsx")]
    assert await content.check_made_files([]) == []


def test_a_network_spelling_is_refused_by_its_text_and_an_unreadable_folder_is_not_opened(tmp_path, monkeypatch):
    from iron_jarvis.core import fs_policy

    asked: list[str] = []
    monkeypatch.setattr(fs_policy, "fs_read_ok", lambda path: (asked.append(str(path)), (True, ""))[1])
    for spelled in ("\\\\host\\share\\a.xlsx", "//host/share/a.xlsx"):
        assert content.readable(spelled) is False
    assert asked == [], "a network path reached the file policy (it must be refused by its text first)"

    p = _xlsx(tmp_path / "t.xlsx", BUDGET)
    assert content.readable(str(p)) is True and asked == [str(p)]
    monkeypatch.setattr(fs_policy, "fs_read_ok", lambda path: (False, "outside your folders"))
    opened: list[str] = []
    monkeypatch.setattr(content, "_load", lambda path, late=None: opened.append(str(path)))
    assert content.check_files([str(p)]) == [
        Check(FILE_OPENS, NOT_CHECKED, "it is outside the folders Iron Jarvis may read", file="t.xlsx")]
    assert opened == []


def test_a_file_that_is_gone_says_nothing_and_an_empty_or_broken_one_fails_in_plain_words(tmp_path):
    assert content.check_files([str(tmp_path / "scratch.py")]) == []
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")
    assert content.check_files([str(empty)]) == [Check(FILE_OPENS, FAILED, "it is empty", file="empty.xlsx")]
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"this is not a workbook")
    assert content.check_files([str(broken)]) == [
        Check(FILE_OPENS, FAILED, "it does not open: it is not a real Office file", file="broken.xlsx")]
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(b"%PDF-1.7\n" + b"x" * 400)
    assert content.check_files([str(cut)]) == [
        Check(FILE_OPENS, FAILED, "it does not open: the PDF was cut off", file="cut.pdf")]


@pytest.mark.parametrize("why,said", [
    ("it does not open (BadZipFile: File is not a zip file)", "it does not open"),
    ("it does not open (KeyError: \"There is no item named 'xl/sharedStrings.xml' in the archive\")", "it does not open"),
    ("not a valid Office file (it is not a package)", "it does not open: it is not a real Office file"),
    ("its package has no xl/workbook.xml", "it does not open: a part of the file is missing"),
    ("the workbook has no sheets", "it does not open: the workbook has no sheets"),
    ("it does not start like a PDF file", "it does not open: it is not the kind of file its name says"),
    ("the PDF has no end marker (it was cut off)", "it does not open: the PDF was cut off"),
    ("it is not UTF-8 text", "its text could not be read"),
    ("it cannot be read (PermissionError)", "it could not be read"),
    ("something nobody planned for (ZeroDivisionError: x)", "it does not open"),
])
def test_why_a_file_does_not_open_is_said_in_plain_words_never_an_engines(why, said):
    """N8: the older check's own reason named an exception ("BadZipFile:
    ...") or a part of the package, and it reached the line."""
    assert R.open_words(why) == said
    for word in ("Error", "BadZip", "xl/", "package", "Exception", "UTF"):
        assert word not in R.open_words(why)
