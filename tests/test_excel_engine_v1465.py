"""v1.465.0: the spreadsheet engine works a whole workbook out, as Excel does.

THE GROUND TRUTH IS EXCEL ITSELF. ``tests/fixtures/excel_truth_v1465`` holds
two workbooks and ``excel_truth.json``: what real Excel (opened hidden and
read-only on the owner's PC) answered for every formula cell in them, 318 in
all. The first comparison found three real faults, each fixed and pinned here:

* ROUND read a number at 17 digits where Excel keeps 15 (750*7.25% rounded to
  54.37; Excel says 54.38);
* ``=-2^2`` was -4 and ``=2^3^2`` was 512 (Excel: 4 and 64);
* XLOOKUP, IFNA, MAXIFS, MINIFS, CONCAT and DAYS written into a file plainly
  opened in Excel as #NAME? (they need the file format's prefix).

CI has no Excel, so the answers are replayed from the fixture.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.workbook.defined_name import DefinedName

from iron_jarvis.documents import excel_formula as F
from iron_jarvis.documents.excel_formula import (
    ExcelError,
    FormulaError,
    Grid,
    calculate,
    evaluate_formula,
    excel_round,
    for_file,
    for_reading,
)

TRUTH_DIR = Path(__file__).parent / "fixtures" / "excel_truth_v1465"
TRUTH = json.loads((TRUTH_DIR / "excel_truth.json").read_text(encoding="utf-8-sig"))
CASES = [(book, ref) for book, cells in TRUTH.items() for ref in cells]


@pytest.fixture(scope="module")
def grids():
    made = {book: Grid(TRUTH_DIR / book) for book in TRUTH}
    yield made
    for g in made.values():
        g.close()


def test_the_fixture_is_the_whole_run():
    assert len(CASES) == 318
    assert {b for b, _ in CASES} == {"cov.xlsx", "books.xlsx"}
    # every function the engine knows (but TODAY, which moves) was put to Excel
    asked = " ".join(c["formula"].upper() for cells in TRUTH.values() for c in cells.values())
    missing = [f for f in F.SUPPORTED if f not in ("TODAY", "TRUE", "FALSE", "NA")
               and not re.search(rf"\b{re.escape(f)}\(", asked)]
    assert missing == [], missing


@pytest.mark.parametrize("book,ref", CASES)
def test_the_engine_answers_what_excel_answered(grids, book, ref):
    want = TRUTH[book][ref]
    sheet, addr = ref.rsplit("!", 1)
    m = re.match(r"([A-Z]+)([0-9]+)", addr)
    grid = grids[book]
    grid._reads = 0
    try:
        got = grid.get(sheet, F._col_num(m.group(1)), int(m.group(2)))
    except ExcelError as exc:
        assert want["kind"] == "error" and exc.code == want["value"], (want, exc.code)
        return
    if want["kind"] == "number":
        assert not isinstance(got, (bool, str)), (want, got)
        assert math.isclose(float(got or 0), float(want["value"]), rel_tol=1e-9, abs_tol=1e-9), (want, got)
    elif want["kind"] == "blank":
        assert got is None
    else:
        assert got == want["value"], (want, got)


# ----------------------------------------------------- Excel's own arithmetic --


@pytest.mark.parametrize("expr,want", [
    ("=ROUND(2.5,0)", 3), ("=ROUND(-2.5,0)", -3), ("=ROUND(3.5,0)", 4),  # half AWAY from zero
    ("=ROUND(2.675,2)", 2.68), ("=ROUND(1.005,2)", 1.01),
    ("=ROUND(750*0.0725,2)", 54.38),  # 54.37499999999999 in binary; 54.375 to Excel
    ("=-2^2", 4), ("=2^3^2", 64), ("=2*-3^2", 18), ("=2^-1", 0.5),
    ("=IF(0.1+0.2=0.3,1,0)", 1),  # compared at 15 significant digits
    ("=10%*200", 20), ("=MOD(-7,3)", 2), ("=INT(-2.5)", -3),
])
def test_arithmetic_is_excels_not_pythons(expr, want):
    assert calculate(expr) == pytest.approx(want, abs=1e-12)


def test_pythons_own_round_would_have_been_wrong():
    """The control: the three cases above differ from Python's arithmetic."""
    assert round(2.5) == 2 and excel_round(2.5) == 3
    assert round(750 * 0.0725, 2) == 54.37 and excel_round(750 * 0.0725, 2) == 54.38
    assert -2 ** 2 == -4 and 2 ** 3 ** 2 == 512


# ------------------------------------------------ recalculation in the Grid --


def _book(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Item", "Qty", "Price", "Line"])
    for i, (a, q, p) in enumerate([("A", 2, 10.5), ("B", 3, 5), ("C", 1, 7)], start=2):
        ws.append([a, q, p, f"=B{i}*C{i}"])
    ws["A6"], ws["D6"] = "Total", "=SUM(D2:D4)"
    ws["F1"], ws["F2"] = "=F2", "=F1"  # a circle
    ws["G1"] = 0.1
    wb.defined_names["Rate"] = DefinedName("Rate", attr_text="Data!$G$1")
    wb.defined_names["Lines"] = DefinedName("Lines", attr_text="Data!$D$2:$D$4")
    s2 = wb.create_sheet("Sum mary")
    s2["A1"] = "='Data'!D6*2"
    s2["A2"] = "=SUM(Data!D:D)"
    wb.save(path)
    return path


def test_a_formula_with_no_saved_result_is_worked_out_through_other_formulas(tmp_path):
    grid = Grid(_book(tmp_path / "b.xlsx"))
    try:
        assert grid.saved("Data", 4, 6) is None, "the library saved no result"
        assert grid.get("Data", 4, 6) == 43  # 21 + 15 + 7, each itself a formula
        assert grid.get("Sum mary", 1, 1) == 86  # across sheets, a quoted name
        assert grid.get("Sum mary", 1, 2) == 86  # a whole column: D2:D4 and the total
        assert grid.computed >= 5
        # worked out once: the memo answers the second read
        before = grid.computed
        assert grid.get("Data", 4, 6) == 43 and grid.computed == before
    finally:
        grid.close()


def test_a_circular_reference_is_refused_in_words_never_looped(tmp_path):
    grid = Grid(_book(tmp_path / "b.xlsx"))
    try:
        with pytest.raises(FormulaError, match="circular reference"):
            grid.get("Data", 6, 1)
        with pytest.raises(FormulaError, match="circular reference"):
            evaluate_formula("=F1+1", grid, "Data")
    finally:
        grid.close()


def test_names_and_sheet_names_resolve(tmp_path):
    grid = Grid(_book(tmp_path / "b.xlsx"))
    try:
        assert evaluate_formula("=ROUND(D6*Rate,2)", grid, "Data") == 4.3
        assert evaluate_formula("=SUM(Lines)", grid, "Data") == 43
        assert evaluate_formula("=data!B2", grid, "Sum mary") == 2  # names ignore case
        with pytest.raises(FormulaError, match="not a cell, a function or a name"):
            evaluate_formula("=Nonsense+1", grid, "Data")
        with pytest.raises(FormulaError, match="no sheet named"):
            evaluate_formula("=Missing!A1", grid, "Data")
    finally:
        grid.close()


def test_only_the_branch_taken_is_worked_out():
    assert calculate("=IF(TRUE,1,1/0)") == 1
    assert calculate("=IF(1>2,1/0,5)") == 5
    assert calculate('=IFERROR(1/0,"none")') == "none"
    assert calculate("=IFERROR(4/2,0)") == 2
    with pytest.raises(ExcelError) as err:
        calculate("=IFNA(1/0,0)")  # IFNA catches #N/A only
    assert err.value.code == "#DIV/0!"


def test_a_saved_error_value_is_an_error_not_a_number(tmp_path):
    wb = Workbook()
    wb.active["A1"] = "#REF!"
    wb.save(tmp_path / "e.xlsx")
    grid = Grid(tmp_path / "e.xlsx")
    try:
        with pytest.raises(ExcelError) as err:
            evaluate_formula("=A1+1", grid, grid.active)
        assert err.value.code == "#REF!"
    finally:
        grid.close()


@pytest.mark.parametrize("expr,needle", [
    ('=INDIRECT("A1")', "INDIRECT is not supported"),
    ("=1+", "unexpected"),
    ("=SUM(1,2", "expected ')'"),
    ("=A1", "there is no workbook here"),
    ("", "empty formula"),
    ("=1 @ 2", "cannot read the formula"),
])
def test_what_it_cannot_do_is_said_never_guessed(expr, needle):
    with pytest.raises(FormulaError, match=re.escape(needle)):
        calculate(expr)


def test_deep_nesting_and_huge_input_end_in_words():
    with pytest.raises(FormulaError):
        calculate("=" + "(" * 3000 + "1" + ")" * 3000)
    with pytest.raises(FormulaError, match="too long"):
        calculate("=1" + "+1" * 5000)


# ------------------------------------------- how a formula is put in a file --


@pytest.mark.parametrize("typed,stored", [
    ("=XLOOKUP(A1,B:B,C:C)", "=_xlfn.XLOOKUP(A1,B:B,C:C)"),
    ('=IFNA(VLOOKUP("IFNA(x)",A:B,2,0),"XLOOKUP(")', '=_xlfn.IFNA(VLOOKUP("IFNA(x)",A:B,2,0),"XLOOKUP(")'),
    ("=_xlfn.XLOOKUP(1,A:A,B:B)", "=_xlfn.XLOOKUP(1,A:A,B:B)"),  # already stored: untouched
    ("=SUM(A1:A2)", "=SUM(A1:A2)"),
    ("='IFNA sheet'!A1+IFNA(A1,0)", "='IFNA sheet'!A1+_xlfn.IFNA(A1,0)"),
    ("=FILTER(A:A,B:B>1)", "=_xlfn._xlws.FILTER(A:A,B:B>1)"),
    ("=MYIFNA(1)", "=MYIFNA(1)"),
    ("plain text IFNA(", "plain text IFNA("),
    (42, 42),
])
def test_newer_functions_get_the_prefix_excel_stores_them_with(typed, stored):
    assert for_file(typed) == stored
    if isinstance(typed, str) and typed.startswith("=") and "_xlfn" not in typed:
        assert for_reading(stored) == typed  # and a person reads it back as typed


def test_the_engine_reads_a_stored_formula_and_a_typed_one_alike():
    assert calculate('=_xlfn.IFNA(1,"x")') == calculate('=IFNA(1,"x")') == 1


def test_every_prefixed_name_excel_called_name_error_is_covered():
    """The six functions the first Excel run answered #NAME? for."""
    for name in ("XLOOKUP", "IFNA", "MAXIFS", "MINIFS", "CONCAT", "DAYS"):
        assert for_file(f"={name}(A1)") == f"=_xlfn.{name}(A1)"
        # ...and they are in the fixture, answered with a value now
        hits = [c for c in TRUTH["cov.xlsx"].values() if f"{name}(" in c["formula"]]
        assert hits and all(c["kind"] != "error" for c in hits), name


def test_a_written_workbook_stores_the_prefix(tmp_path):
    from iron_jarvis.documents.writers import write_document

    out = tmp_path / "x.xlsx"
    write_document(out, [["Key", "Val"], ["a", 1], ["b", 2], ["=XLOOKUP(\"b\",A2:A3,B2:B3)", "=IFNA(B2,0)"]])
    ws = load_workbook(out).active
    assert ws["A4"].value == '=_xlfn.XLOOKUP("b",A2:A3,B2:B3)'
    assert ws["B4"].value == "=_xlfn.IFNA(B2,0)"
    assert load_workbook(out, data_only=True).active["A4"].value == 2  # and its result is in the file
