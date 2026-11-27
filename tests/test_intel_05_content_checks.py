"""Intelligence plan, release 5: what a made file SAYS is checked by rule.

Real files (an .xlsx written by openpyxl and by the app's own writer, a
.docx by python-docx, a PDF by pypdf) go through ``checks/content``.

WHICH WORD A FINDING GETS is fixed by what kind of knowledge it is, and
these tests hold it:

* "Problem" (``failed``) only for a fact: the reader the app's own tools
  use could not open the file ("Iron Jarvis could not open “x.docx”"), or a
  cell's TYPE is an error.
* "Worth a look" (``look``) for what Iron Jarvis worked out itself: a typed
  total that differs from the ONE shape of list above it, a formula with no
  saved result (or one the workbook does not stand by) that works out to an
  error or names a function Excel may not know, a document that is empty or
  in which no text was found. Never ``failed``: over the whole table of
  sheet shapes no check of that kind ever reads "Problem".
* A phrase is said only where it is true by construction for EVERY file. A
  total that is not the one shape (a subtotal anywhere, a gap, a formula in
  the list, a second total in the column) gets NO row at all.

A check that breaks, a file that is too large, and a reply that runs out of
time read "not checked", never "passed". Nothing here calls a model.
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
    LOOK,
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
    return [{"check": c.check, "file": c.file, "folder": c.folder, "result": c.result, "detail": c.detail,
             "by": c.by, "step": c.step} for c in checks]


BUDGET = [["Item", "Amount"], ["Rent", 1200], ["Food", 300], ["Travel", 150.5]]


# --------------------------------------------------------------------------- #
# Error cells
# --------------------------------------------------------------------------- #
def test_a_spreadsheet_with_an_error_cell_fails_and_the_finding_names_the_cell(tmp_path):
    p = _xlsx(tmp_path / "summary.xlsx", [*BUDGET, ["Share", "#DIV/0!"]])
    got = content.check_files([str(p)])
    (errors,) = _by(got, SHEET_ERRORS)
    assert (errors.result, errors.detail, errors.file, errors.by) == (
        FAILED, "B5 holds #DIV/0!", "summary.xlsx", "rule")
    assert _by(got, FILE_OPENS)[0].result == PASSED
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the spreadsheet opens. Problem in the spreadsheet: B5 holds #DIV/0!."
    assert tone == "warn"


def test_a_clean_spreadsheet_passes_and_many_error_cells_are_counted_not_listed(tmp_path):
    clean = _xlsx(tmp_path / "clean.xlsx", BUDGET)
    assert _by(content.check_files([str(clean)]), SHEET_ERRORS)[0] == Check(
        SHEET_ERRORS, PASSED, "no error cells", file="clean.xlsx")
    bad = _xlsx(tmp_path / "bad.xlsx", [["a", "#REF!"], ["b", "#N/A"], ["c", "#VALUE!"], ["d", "#NAME?"], ["e", "#NUM!"]])
    (errors,) = _by(content.check_files([str(bad)]), SHEET_ERRORS)
    assert errors.result == FAILED
    assert errors.detail == "B1 holds #REF!; B2 holds #N/A; B3 holds #VALUE!; and 2 more cells hold an error"


def test_a_cell_is_named_in_plain_words_with_its_sheet_only_when_there_are_several(tmp_path):
    """Never "Summary!B5": "B5" in a workbook of one sheet, B5 on “Summary”
    where there are more (a sheet's name is somebody's words: quoted). A
    worked-out error is never named: the evaluator's can differ from Excel's."""
    one = _xlsx(tmp_path / "one.xlsx", [*BUDGET, ["Total", 1700], ["Share", "#DIV/0!"]])
    said = " | ".join(c.detail for c in content.check_files([str(one)]))
    assert "B6 holds #DIV/0!" in said and "B5 says 1,700" in said and "!" not in said.replace("#DIV/0!", "")
    assert " on " not in said
    two = _xlsx(tmp_path / "two.xlsx", [*BUDGET, ["Total", 1700], ["Share", "#DIV/0!"]], more={
        "Q2 plan": [["Item", "Amount"], ["a", 1], ["b", 2], ["Total", 9], ["x", "=1/0"]]})
    got = content.check_files([str(two)])
    assert [c.detail for c in _by(got, SHEET_ERRORS)] == [
        "B6 on “Summary” holds #DIV/0!", "B5 on “Q2 plan” works out to an error"]
    assert [c.detail for c in _by(got, SHEET_TOTALS)] == [
        "B5 on “Summary” says 1,700 and the cells above it add up to 1,650.50; "
        "B4 on “Q2 plan” says 9 and the cells above it add up to 3"]
    # A sheet whose NAME reads like a verdict stays a name.
    odd = _xlsx(tmp_path / "odd.xlsx", [*BUDGET, ["Total", 1700]], title="Summary. All good",
                more={"x": [["a", 1]]})
    (said,) = [c.detail for c in _by(content.check_files([str(odd)]), SHEET_TOTALS)]
    assert said.startswith("B5 on “Summary. All good” says 1,700")
    assert "Summary!" not in " ".join(c.detail for c in got)


_FULL_CALC = b' fullCalcOnLoad="1"'


def _repack(src: Path, out: Path, change) -> Path:
    """*src* (a package) written again as *out*, each part's bytes put
    through ``change(name, data)``."""
    import zipfile

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, change(item.filename, zin.read(item.filename)))
    return out


def _stands_by(src: Path, out: Path) -> Path:
    """*src* as a spreadsheet program saves it: the workbook no longer asks
    for a full recalculation on load, so it STANDS BY its saved results.
    (openpyxl, and so the app's own writer, always asks for one.)"""
    def change(name: str, data: bytes) -> bytes:
        if name == "xl/workbook.xml":
            assert _FULL_CALC in data, "the writer no longer asks for a recalculation: read the test again"
            return data.replace(_FULL_CALC, b"")
        return data

    return _repack(src, out, change)


def _saved_error_file(tmp_path: Path, stands_by: bool = True) -> Path:
    """A REAL file whose formula B1 holds a saved error result and whose B2
    holds a saved number (what a spreadsheet program writes; no stub).
    *stands_by* False: the same file from a writer that asks for a full
    recalculation on load (its saved results are placeholders)."""
    plain = tmp_path / "plain.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"], ws["B1"], ws["B2"] = "x", "=1/0", "=2+2"
    wb.save(plain)

    def change(name: str, data: bytes) -> bytes:
        if name != "xl/worksheets/sheet1.xml":
            return data
        text = data.decode("utf-8")
        for empty in ("<v></v>", "<v />", "<v/>"):
            text = text.replace(f'<c r="B1"><f>1/0</f>{empty}</c>', '<c r="B1" t="e"><f>1/0</f><v>#DIV/0!</v></c>')
            text = text.replace(f'<c r="B2"><f>2+2</f>{empty}</c>', '<c r="B2"><f>2+2</f><v>4</v></c>')
        assert "#DIV/0!" in text, text[:400]
        return text.encode("utf-8")

    asks = _repack(plain, tmp_path / "asks.xlsx", change)
    return _stands_by(asks, tmp_path / "saved.xlsx") if stands_by else asks


def test_a_formula_whose_saved_result_is_an_error_is_a_problem_the_file_holds(tmp_path):
    """What the file itself holds: a saved error value is a "Problem". The
    file here is real (its saved results are in it, and it stands by them),
    read by the ONE load."""
    p = _saved_error_file(tmp_path)
    book = content._load(p)
    assert book.values["S"] == {(1, 2): "#DIV/0!", (2, 2): 4}, "the one load did not read the saved results"
    assert book.errors["S"] == {(1, 2): "#DIV/0!"}
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, FAILED, "B1 holds #DIV/0!", file="saved.xlsx")]
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the spreadsheet opens. Problem in the spreadsheet: B1 holds #DIV/0!."
    assert tone == "warn"


def test_a_workbook_that_asks_to_be_worked_out_again_has_no_saved_result_to_trust(tmp_path):
    """Decision 5. A writer that cannot work formulas out says so in the
    file (a full recalculation on load) and saves a placeholder as every
    result. The SAME cells then hold no "saved result": the saved error is
    not a problem the file holds, and a saved 4 is not "no error cells".
    Each formula is worked out here."""
    p = _saved_error_file(tmp_path, stands_by=False)
    assert content._recalculates(p) and not content._recalculates(_saved_error_file(tmp_path))
    book = content._load(p)
    assert book.values["S"] == {} and book.errors["S"] == {}, "a placeholder was read as a saved result"
    assert content._formula_counts(book) == (2, 2)
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, LOOK, "B1 works out to an error", file="asks.xlsx")]
    assert not [c for c in got if c.result == FAILED]
    # In doubt (the setting cannot be read), the saved results are not trusted.
    assert content._recalculates(tmp_path / "not-there.xlsx") is True
    # The slower read gives the same answer.
    from openpyxl import load_workbook

    wb = load_workbook(str(p), read_only=True)
    try:
        slow = content._read_twice(wb, p, content._never_late, False)
    finally:
        wb.close()
    assert slow.values["S"] == {} and slow.errors["S"] == {}


def test_a_writer_that_saves_zero_for_every_formula_never_reads_no_error_cells(tmp_path):
    """The reviewer's file, from XlsxWriter (it ships with the app): every
    formula is saved as 0, D3 divides by zero and D4 names no function. The
    line read "no error cells, 1 total adds up"."""
    import zipfile

    import xlsxwriter

    p = tmp_path / "report.xlsx"
    wb = xlsxwriter.Workbook(str(p))
    ws = wb.add_worksheet()
    for r, row in enumerate([["Item", "Sold", "Days", "Per day"], ["A", 100, 4, "=B2/C2"], ["B", 50, 0, "=B3/C3"],
                             ["C", 10, 2, "=SUMM(B4:C4)"], ["Total", "=SUM(B2:B4)", None, None]]):
        for c, v in enumerate(row):
            if v is not None:
                ws.write(r, c, v)
    wb.close()
    with zipfile.ZipFile(p) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "<f>B3/C3</f><v>0</v>" in sheet, "the writer no longer saves a placeholder 0: read the test again"
    got = content.check_files([str(p)])
    said = {c.result: c.detail for c in _by(got, SHEET_ERRORS)}
    assert said == {LOOK: "D3 works out to an error",
                    NOT_CHECKED: "1 formula could not be worked out here"}
    line = line_for(_rows(got))[0]
    assert "no error cells" not in line and "work out," not in line and "works out," not in line


# --------------------------------------------------------------------------- #
# A formula with NO saved result is worked out: "no error cells" is said only
# when every filled cell holds a value or a formula worked out without an error
# --------------------------------------------------------------------------- #
#: The reviewer's rows: the app's own writer warns that D3 is #DIV/0!, saves a
#: result for D2 and none for D3.
DIVIDES = [["Name", "Amount", "Parts", "Each"], ["A", 100, 4, "=B2/C2"], ["B", 50, 0, "=B3/C3"]]


def test_a_formula_the_apps_own_writer_could_not_save_a_result_for_is_worth_a_look_never_a_problem(tmp_path):
    p = tmp_path / "summary.xlsx"
    warnings: list[str] = []
    write_document(p, DIVIDES, warnings=warnings)
    assert warnings and "D3" in warnings[0] and "#DIV/0!" in warnings[0], warnings
    # The writer saves the result it worked out for D2 and none for D3, and
    # asks for a recalculation on load: neither is a saved result to trust.
    kept = content._load(_stands_by(p, tmp_path / "kept.xlsx"))
    assert kept.values["Sheet"].get((2, 4)) == 25 and kept.values["Sheet"].get((3, 4)) is None, (
        "the file should hold a result for D2 and none for D3")
    assert content._load(p).values["Sheet"] == {}
    got = content.check_files([str(p)])
    # Iron Jarvis worked it out itself: the file holds no error value.
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, LOOK, "D3 works out to an error", file="summary.xlsx")]
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the spreadsheet opens. Worth a look: D3 works out to an error."
    assert tone == "warn" and "no error cells" not in line and "Problem" not in line


@pytest.mark.parametrize("formula,error", [
    ("=1/0", "#DIV/0!"),
    ('=VLOOKUP("nobody",A1:B2,2,FALSE)', "#N/A"),
    ("=SQRT(0-B1)", "#NUM!"),
    ("=INDEX(B1:B2,3)", "#REF!"),
])
def test_a_formula_with_no_saved_result_that_works_out_to_an_error_is_worth_a_look(tmp_path, formula, error):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["j", 2], ["f", formula]])
    assert content._load(p).values["Summary"].get((3, 2)) is None, "the file really holds no saved result"
    # WHICH error is not said: the evaluator's can differ from Excel's
    # (=0^-1 is #DIV/0! there; a 4-argument VLOOKUP another one).
    (got,) = _by(content.check_files([str(p)]), SHEET_ERRORS)
    assert got == Check(SHEET_ERRORS, LOOK, "B3 works out to an error", file="t.xlsx")
    assert error not in got.detail and "#" not in got.detail


#: Formulas that are FINE in Excel and that the app's evaluator turns into
#: #VALUE! (it is stricter about text in a cell it reads: a dash, a date or a
#: number typed as text). The reviewer's cases. None may read "Problem" or
#: "Worth a look": they are "not checked".
STRICTER_THAN_EXCEL = [
    ("a direct reference to text in SUM", [["a", 100], ["b", "n/a"], ["t", "=SUM(B1,B2)"]]),
    ("a direct reference to text in AVERAGE", [["a", 100], ["b", "n/a"], ["t", "=AVERAGE(B1,B2)"]]),
    ("a dash in MAX", [["a", 100], ["b", "-"], ["t", "=MAX(B1,B2)"]]),
    ("TBD in MIN", [["a", 100], ["b", "TBD"], ["t", "=MIN(B1,B2)"]]),
    ("two dates typed as text", [["2024-01-05", "2024-02-05"], ["days", "=B1-A1"]]),
    ("a date typed the US way", [["1/5/2024", 0], ["due", "=A1+30"]]),
    ("a time typed as text", [["9:30", 0], ["t", "=A1+1"]]),
    ("a negative in brackets", [["a", "(45.10)"], ["t", "=B1+1"]]),
    ("YEAR of a text date", [["2024-01-05", 0], ["y", "=YEAR(A1)"]]),
    ("EDATE of a text date", [["2024-01-05", 0], ["d", "=EDATE(A1,1)"]]),
    ("PRODUCT over text", [["a", "x"], ["b", 2], ["t", "=PRODUCT(B1,B2)"]]),
    ("COUNT of text", [["a", "x"], ["b", 2], ["t", "=COUNT(B1,B2)"]]),
    # ... and the ones that ARE #VALUE! in Excel: still only "not checked".
    ("text plus one", [["a", "abc"], ["t", "=A1+1"]]),
    ("a dash plus one", [["a", "-"], ["t", "=B1+1"]]),
    ("a cell of blank space plus one", [["a", " "], ["t", "=B1+1"]]),
]


@pytest.mark.parametrize("how", ["openpyxl", "the app's own writer"])
@pytest.mark.parametrize("rows", [r for _n, r in STRICTER_THAN_EXCEL], ids=[n for n, _r in STRICTER_THAN_EXCEL])
def test_a_formula_the_evaluator_calls_a_value_error_is_only_not_checked(tmp_path, rows, how):
    p = _write(how, tmp_path / "t.xlsx", rows)
    got = _by(content.check_files([str(p)]), SHEET_ERRORS)
    assert [(c.result, c.detail) for c in got] == [
        (NOT_CHECKED, "1 formula could not be worked out here")], got
    line, tone = line_for(_rows(content.check_files([str(p)])))
    assert tone == "quiet" and "no error cells" not in line and "Worth a look" not in line and "Problem" not in line


def test_a_cell_of_blank_space_is_text_to_a_formula_never_an_empty_cell(tmp_path):
    """Arithmetic over a cell that holds only spaces is #VALUE! in Excel.
    Read as an empty cell it worked out to a clean 1: "no error cells"."""
    p = _xlsx(tmp_path / "t.xlsx", [["a", " "], ["t", "=B1+1"], ["u", "=SUM(B1:B1)+1"]])
    book = content._load(p)
    assert ("Summary", 1, 2) in book.spaces and (1, 2) not in book.formulas["Summary"]
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "1 formula could not be worked out here", file="t.xlsx")]


def test_a_date_that_cannot_exist_is_not_worked_out_to_a_clean_number(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["a", 5], ["t", "=DATE(-1,1,1)"]])
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "1 formula could not be worked out here", file="t.xlsx")]
    fine = _xlsx(tmp_path / "fine.xlsx", [["a", 5], ["t", "=DATE(2024,1,31)+30"], ["u", "=DATE(99,1,1)"]])
    assert _by(content.check_files([str(fine)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "its 2 formulas work out", file="fine.xlsx")]


@pytest.mark.parametrize("formula", [
    '=CONCAT(A1,"x")', "=IFNA(B1,0)", "=DAYS(B1,B1)", '=XLOOKUP("a",A1:A1,B1:B1)', '=MAXIFS(B1:B1,A1:A1,"a")',
])
def test_a_newer_function_measured_to_open_as_name_is_worth_a_look_never_a_good_phrase(tmp_path, formula):
    """Excel stores CONCAT, IFNA, DAYS, XLOOKUP and MAXIFS with a prefix.
    Written plainly into a file (a script did it) the cell opened as #NAME?
    when it was MEASURED (``excel_formula._PREFIXED``'s own comment names
    these five). The app's evaluator works them out fine, so the line said
    "no error cells"."""
    from iron_jarvis.documents import excel_formula

    assert excel_formula.for_file(formula) != formula, "this function no longer needs a prefix: pick another"
    p = _xlsx(tmp_path / "t.xlsx", [["a", 5], ["t", formula]])
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [
        Check(SHEET_ERRORS, LOOK, "B2 may show #NAME? when opened in Excel", file="t.xlsx")]
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the spreadsheet opens. Worth a look: B2 may show #NAME? when opened in Excel."
    assert tone == "warn"


def test_the_measured_names_are_the_ones_the_writers_table_says_were_measured():
    """The finding is said ONLY for the names the module that owns the table
    says it measured. Its comment is read here: a name added to the claim
    without a measurement goes red."""
    import inspect
    import re as _re

    from iron_jarvis.documents import excel_formula

    source = inspect.getsource(excel_formula).replace("\r\n", "\n")
    note = source[source.index("#: Functions newer than the file format"):source.index("_PREFIXED: dict")]
    measured = set(_re.findall(r"=([A-Z][A-Z.]*)", note))
    assert measured == content._NAME_MEASURED == {"XLOOKUP", "IFNA", "MAXIFS", "CONCAT", "DAYS"}
    assert content._NAME_MEASURED <= set(excel_formula._PREFIXED)


@pytest.mark.parametrize("formula", [
    '=IF(B1>0,TEXTJOIN(",",TRUE,A1:A1),"")', "=NETWORKDAYS.INTL(B1,B1)", "=WORKDAY.INTL(B1,1)", "=IFS(B1>0,1)",
    "=STDEV.S(B1:B1)",
    '=MINIFS(B1:B1,A1:A1,"a")',         # one the app's evaluator CAN work out: still not called clean
])
def test_a_newer_function_nobody_measured_is_not_worked_out_and_is_no_finding(tmp_path, formula):
    """NETWORKDAYS.INTL and WORKDAY.INTL are on the writer's table, and the
    spreadsheet library lists both as plain names. Nothing shows they open as
    #NAME? without a prefix, so nothing is claimed: not a finding, and not a
    clean result either."""
    from iron_jarvis.documents import excel_formula

    assert excel_formula.for_file(formula) != formula
    p = _xlsx(tmp_path / "t.xlsx", [["a", 5], ["t", formula]])
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "1 formula could not be worked out here", file="t.xlsx")]
    line, tone = line_for(_rows(got))
    assert tone == "quiet" and "#NAME?" not in line and "work out" not in line and "no error cells" not in line


def test_a_function_with_its_prefix_and_the_apps_own_writer_are_not_flagged(tmp_path):
    prefixed = _xlsx(tmp_path / "p.xlsx", [["a", 5], ["t", "=_xlfn.IFNA(B1,0)"], ["u", '=LEFT("CONCAT(x)",3)']])
    assert _by(content.check_files([str(prefixed)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "its 2 formulas work out", file="p.xlsx")]
    own = tmp_path / "own.xlsx"
    write_document(own, [["a", 5], ["t", "=IFNA(B1,0)"]])     # the writer adds the prefix itself
    assert _by(content.check_files([str(own)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "its formula works out", file="own.xlsx")]


def test_a_formula_that_cannot_be_worked_out_is_not_checked_and_the_line_never_says_no_error_cells(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["a", "=NOSUCHFUNCTION(B1)"], ["b", "=Missing!A1"], ["c", "=B1*2"]])
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "2 formulas could not be worked out here", file="t.xlsx")]
    line, tone = line_for(_rows(got))
    assert line == ("Checked by Iron Jarvis: the spreadsheet opens. Not checked: the spreadsheet "
                    "(2 formulas could not be worked out here).")
    assert tone == "quiet" and "no error cells" not in line
    one = _xlsx(tmp_path / "one.xlsx", [["k", 1], ["a", "=NOSUCHFUNCTION(B1)"]])
    assert _by(content.check_files([str(one)]), SHEET_ERRORS)[0].detail == (
        "1 formula could not be worked out here")


def test_the_good_phrase_is_one_of_two_true_ones_never_a_loose_one(tmp_path):
    """ "no error cells" is said when every formula has a SAVED result the
    workbook stands by and none is an error. When Iron Jarvis worked
    formulas out ITSELF it says so, with the count: "its 3 formulas work
    out". Never "no error cells" over a cell whose result nobody saved, or
    over a result the workbook asks to have worked out again."""
    rows = [["Item", "Qty", "Each", "Amount"], ["a", 2, 5, "=B2*C2"], ["b", 3, 7, "=B3*C3"],
            ["Share", None, None, "=IFERROR(D2/0,0)"]]
    p = _xlsx(tmp_path / "t.xlsx", rows)
    book = content._load(p)
    assert content._formula_counts(book) == (3, 3), "openpyxl saved no result: the phrase below is the evaluator's"
    got = content.check_files([str(p)])
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, PASSED, "its 3 formulas work out", file="t.xlsx")]
    assert line_for(_rows(got))[0] == "Checked by Iron Jarvis: the spreadsheet opens, its 3 formulas work out."
    one = _xlsx(tmp_path / "one.xlsx", [["a", 2], ["b", "=B1*2"]])
    assert _by(content.check_files([str(one)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "its formula works out", file="one.xlsx")]
    # The app's own writer saves the results IT worked out and asks for a recalculation on load: they are
    # not saved results to trust, so the phrase is still the evaluator's.
    own = tmp_path / "own.xlsx"
    write_document(own, rows)
    assert content._formula_counts(content._load(own)) == (3, 3)
    assert _by(content.check_files([str(own)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "its 3 formulas work out", file="own.xlsx")]
    # The same cells in a file that STANDS BY its saved results (what a spreadsheet program saves): the other
    # true phrase.
    kept = _stands_by(own, tmp_path / "kept.xlsx")
    assert content._formula_counts(content._load(kept)) == (3, 0)
    assert _by(content.check_files([str(kept)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "no error cells", file="kept.xlsx")]
    # No formula at all: every cell is a saved value.
    plain = _xlsx(tmp_path / "plain.xlsx", BUDGET)
    assert _by(content.check_files([str(plain)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "no error cells", file="plain.xlsx")]


def test_formulas_worked_out_here_are_counted_with_the_ones_whose_result_is_saved(tmp_path):
    """One formula with a saved result and one with none: "its 2 formulas
    work out" (EVERY formula of the workbook has a result that is not an
    error), never "its formula works out" and never "no error cells"."""
    plain = tmp_path / "plain.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"], ws["B1"], ws["B2"] = "x", "=2+2", "=B1*3"
    wb.save(plain)

    def change(name: str, data: bytes) -> bytes:
        if name != "xl/worksheets/sheet1.xml":
            return data
        text = data.decode("utf-8")
        for empty in ("<v></v>", "<v />", "<v/>"):
            text = text.replace(f'<c r="B1"><f>2+2</f>{empty}</c>', '<c r="B1"><f>2+2</f><v>4</v></c>')
        assert "<v>4</v>" in text, text[:400]
        return text.encode("utf-8")

    out = _stands_by(_repack(plain, tmp_path / "asks.xlsx", change), tmp_path / "mixed.xlsx")
    assert content._formula_counts(content._load(out)) == (2, 1)
    assert _by(content.check_files([str(out)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "its 2 formulas work out", file="mixed.xlsx")]


def _worked_out(tmp_path, rows):
    """``(what each formula came to, the cells that work out to an error,
    how many could not be worked out)`` for a sheet whose formulas hold no
    saved result."""
    book = content._load(_xlsx(tmp_path / "e.xlsx", rows))
    found, unworked = content._work_out(book)
    return book.worked.get("Summary", {}), sorted((r, c) for _s, r, c, _w in found), unworked


def test_text_is_a_number_only_when_it_is_spelled_as_one(tmp_path):
    """Python's ``float()`` reads "nan", "inf", "Infinity" and "1_000".
    Excel reads none of them: arithmetic over such a cell is #VALUE! there,
    and the line said the formulas were clean."""
    from iron_jarvis.documents import excel_formula as XF

    for text in ("nan", "NaN", "inf", "-inf", "Infinity", "1_000", "0x10", "1e", "--5", "five", ""):
        with pytest.raises(XF.ExcelError) as err:
            XF._as_num(text)
        assert err.value.code == "#VALUE!", text
    assert [XF._as_num(t) for t in ("12", " 12 ", "1,000", "$5", "50%", "1e3", "-0.5", ".5", "+7", "3.")] == [
        12.0, 12.0, 1000.0, 5.0, 0.5, 1000.0, -0.5, 0.5, 7.0, 3.0]
    # A number cell that holds NaN or infinity is no number at all: it cannot be worked out.
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(XF.FormulaError) as err:
            XF._as_num(bad)
        assert not isinstance(err.value, XF.ExcelError)
    rows = [["Item", "Amount", "Double"], ["A", "nan", "=B2*2"], ["B", "inf", "=B3+1"], ["C", "Infinity", "=B4-1"],
            ["D", "1_000", "=B5+1"]]
    worked, errors, unworked = _worked_out(tmp_path, rows)
    assert (worked, errors, unworked) == ({}, [], 4)
    got = _by(content.check_files([str(_xlsx(tmp_path / "t.xlsx", rows))]), SHEET_ERRORS)
    assert got == [Check(SHEET_ERRORS, NOT_CHECKED, "4 formulas could not be worked out here",
                         file="t.xlsx")]


def test_the_evaluator_gives_excels_own_errors_where_it_gave_a_clean_number(tmp_path):
    """Each of these read as a formula that worked out. Excel shows an error
    for every one (#NUM!, #DIV/0!, #N/A): the reviewer's list."""
    from iron_jarvis.documents import excel_formula as XF

    rows = [["what", "value"], ["zero to the zero", "=0^0"], ["the same, by name", "=POWER(0,0)"],
            ["zero to a negative", "=0^-1"], ["a positive number, a negative step", "=CEILING(5,-2)"],
            ["the same, down", "=FLOOR(5,-2)"], ["down by zero", "=FLOOR(5,0)"], ["the year of a negative", "=YEAR(-5)"],
            ["the day of a negative", "=DAY(-1)"], ["a block for MATCH", '=MATCH("b",A1:B2,0)'],
            ["too large to hold", "=1E308*10"]]
    worked, errors, unworked = _worked_out(tmp_path, rows)
    assert errors == [(r, 2) for r in range(2, 12)] and worked == {} and unworked == 0
    with pytest.raises(XF.ExcelError) as err:
        XF._power(0.0, -1.0)
    assert err.value.code == "#DIV/0!"
    with pytest.raises(XF.ExcelError) as err:
        XF._power(0.0, 0.0)
    assert err.value.code == "#NUM!"
    # ... and the neighbours that ARE numbers in Excel still are.
    fine = [["what", "value"], ["up", "=CEILING(5,2)"], ["a negative up", "=CEILING(-5,2)"], ["a negative down", "=FLOOR(-5,-2)"],
            ["up by zero", "=CEILING(5,0)"], ["zero down by zero", "=FLOOR(0,0)"], ["a year", "=YEAR(45000)"],
            ["along a row", '=MATCH("value",A1:B1,0)'], ["a power", "=2^10"], ["zero to a power", "=0^3"]]
    worked, errors, unworked = _worked_out(tmp_path, fine)
    assert (errors, unworked) == ([], 0)
    assert [worked[(r, 2)] for r in range(2, 11)] == [6.0, -4.0, -4.0, 0.0, 0.0, 2023.0, 2.0, 1024.0, 0.0]


def test_match_with_minus_one_is_worked_out_only_over_a_list_sorted_large_to_small(tmp_path):
    """Over any other order Excel's answer depends on how it searches (it is
    #N/A for 1, 2, 3 where the app said 3). Not guessed: not worked out."""
    up = [[1, "=MATCH(2,A1:A3,-1)"], [2], [3]]
    assert _worked_out(tmp_path, up) == ({}, [], 1)
    down = [[3, "=MATCH(2,A1:A3,-1)"], [2], [1]]
    assert _worked_out(tmp_path, down) == ({(1, 2): 2.0}, [], 0)
    # MATCH looks along ONE row or column. Over a block it is #N/A in Excel, where the app said 2.
    block = [["a", "b", '=MATCH("b",A1:B2,0)'], ["c", "d"]]
    assert _worked_out(tmp_path, block) == ({}, [(1, 3)], 0)
    row = [["a", "b", '=MATCH("b",A1:B1,0)'], ["c", "d"]]
    assert _worked_out(tmp_path, row) == ({(1, 3): 2.0}, [], 0)


def test_count_and_counta_do_not_fail_over_a_cell_that_holds_an_error(tmp_path):
    """=COUNT over a range with one #DIV/0! in it is 1 in Excel, and =COUNTA
    counts the error cell. Both read as errors of their own. =SUM over the
    same range still is one. PRODUCT of no numbers at all is 0."""
    rows = [["x", 1, 0, "=B1/C1"], ["y", 4, 2, "=B2/C2"], ["how many", None, None, "=COUNT(D1:D2)"],
            ["filled", None, None, "=COUNTA(D1:D2)"], ["sum", None, None, "=SUM(D1:D2)"],
            ["nothing", None, None, "=PRODUCT(B6:C6)"], ["something", 3, 4, "=PRODUCT(B7:C7)"],
            ["a literal", None, None, "=COUNT(1,2,3)"]]
    worked, errors, unworked = _worked_out(tmp_path, rows)
    assert errors == [(1, 4), (5, 4)] and unworked == 0
    assert worked == {(2, 4): 2.0, (3, 4): 1.0, (4, 4): 2.0, (6, 4): 0.0, (7, 4): 12.0, (8, 4): 3.0}


def test_a_result_too_large_to_hold_is_an_error_never_a_clean_number(tmp_path):
    """=1E308*10 is #NUM! in Excel. Python makes it infinity, and infinity
    read as a number that worked out."""
    p = _xlsx(tmp_path / "t.xlsx", [["a", 5], ["t", "=1E308*10"], ["u", "=B1*2"]])
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, LOOK, "B2 works out to an error", file="t.xlsx")]


def test_every_check_of_a_made_file_carries_the_key_of_its_own_path(tmp_path):
    """A run's card matches a check to the file it lists by this key (never
    by a name two files may share): every row of every kind has it."""
    import docx

    made = [_xlsx(tmp_path / "a.xlsx", [*BUDGET, ["Total", 1700]]), tmp_path / "b.docx", tmp_path / "c.md",
            tmp_path / "d.json", tmp_path / "e.txt", tmp_path / "f.xlsx", tmp_path / "g.pdf"]
    docx.Document().save(str(made[1]))
    made[2].write_text("words\n", encoding="utf-8")
    made[3].write_text("{not json", encoding="utf-8")
    made[4].write_bytes(b"")
    made[5].write_bytes(b"not a workbook")
    _pdf(made[6], 2)
    for path in made:
        got = content.check_files([str(path)])
        assert got and {c.path_key for c in got} == {R.path_key(path)}, path.name
    late = content.check_files([str(made[0])], clock=iter([0.0, 99.0, 99.0]).__next__, deadline_s=1.0)
    assert [(c.detail, c.path_key) for c in late] == [(content.OUT_OF_TIME, R.path_key(made[0]))]


def test_a_number_cell_that_holds_no_number_makes_no_claim_about_error_cells(tmp_path):
    """A NaN in a number cell (a script wrote ``float("nan")``) is saved as a
    number cell with nothing in it. Nothing is said about error cells then:
    not "no error cells", only what could not be read."""
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "Amount"], ["A", float("nan")], ["B", float("inf")], ["C", 5]])
    got = _by(content.check_files([str(p)]), SHEET_ERRORS)
    assert got == [Check(SHEET_ERRORS, NOT_CHECKED, "B2 holds a number this check cannot read", file="t.xlsx")]
    book = content._Book()
    book.sheets = ["S"]
    book.formulas["S"] = {(1, 1): float("nan")}
    assert content._not_a_number(book) == "A1"


def test_what_the_file_holds_what_was_worked_out_and_what_could_not_be_are_each_said_in_their_own_word(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["a", "=B1/0"], ["b", "=NOSUCHFUNCTION(B1)"], ["c", "#REF!"]])
    got = {c.result: c.detail for c in _by(content.check_files([str(p)]), SHEET_ERRORS)}
    assert got == {FAILED: "B4 holds #REF!", LOOK: "B2 works out to an error",
                   NOT_CHECKED: "1 formula could not be worked out here"}
    line, tone = line_for(_rows(content.check_files([str(p)])))
    assert line == (
        "Checked by Iron Jarvis: the spreadsheet opens. Problem in the spreadsheet: B4 holds #REF!. "
        "Worth a look: B2 works out to an error. "
        "Not checked: the spreadsheet (1 formula could not be worked out here)."
    )
    assert tone == "warn"


def test_the_working_out_is_capped_and_what_is_past_the_cap_is_counted_not_passed(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], *[["f", "=B1+1"] for _ in range(5)]])
    monkeypatch.setattr(content, "MAX_WORKED", 2)
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "3 formulas could not be worked out here", file="t.xlsx")]
    # ... and a formula that reads more cells than one may is not worked out.
    monkeypatch.setattr(content, "MAX_WORKED", 20_000)
    monkeypatch.setattr(content, "MAX_FORMULA_READS", 3)
    wide = _xlsx(tmp_path / "wide.xlsx", [[1], [2], [3], [4], [5], ["=SUM(A1:A5)"]])
    assert _by(content.check_files([str(wide)]), SHEET_ERRORS)[0].detail == (
        "1 formula could not be worked out here")


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
    #: The cells said to be worth a look, as "B5" (Iron Jarvis added the list
    #: up itself and got another figure; never "wrong", never a problem).
    look: tuple = ()
    #: (A total that is not judged gets NO row: there is nothing to count.)
    #: True for a sheet whose totals are all RIGHT (or that has none): the
    #: rule must never say one is worth a look.
    correct: bool = True
    #: A CORRECT sheet whose total nothing on the sheet explains (hours and
    #: minutes, orders and units, customers counted once): Iron Jarvis cannot
    #: know it is right, and it stays worth a look. Listed, never hidden.
    unknowable: bool = False
    note: str = field(default="", compare=False)


HDR = ["Item", "Amount"]

SHAPES: list[Shape] = [
    # A total that is NOT the one shape gets no row at all: no good, no look. (It read "not checked" before.)
    # ---- the nine everyday shapes that read "Problem" in the first build (all CORRECT sheets) ----
    Shape("a blank cell inside the data",
          [HDR, ["Rent", 100], ["Parking", None], ["Food", 300], ["Total", 400]]),
    Shape("groups separated by a blank row",
          [HDR, ["Rent", 100], ["Food", 300], [None, None], ["Fuel", 50], ["Tolls", 20], ["Total", 470]]),
    Shape("a summary list with a line called Total revenue",
          [["Metric", "Value"], ["Orders", 120], ["Customers", 45], ["Total revenue", 45000]]),
    Shape("a payee called Total Wine & More",
          [["Payee", "Amount"], ["Acme", 100], ["Total Wine & More", 250], ["Zed", 50]]),
    Shape("year columns typed as numbers",
          [["Item", 2023, 2024], ["Rent", 100, 110], ["Food", 300, 310], ["Total", 400, 420]],
          note="the year above the list cannot be told from an amount: not judged"),
    Shape("a running balance column with a total row",
          [["Month", "Amount", "Balance"], ["Jan", 100, 100], ["Feb", 50, 150], ["Total", 150, 150]], good=1),
    Shape("an invoice number column with a count in the total row",
          [["Client", "Invoice no", "Amount"], ["Acme", 1001, 100], ["Zed", 1002, 200], ["Total", 2, 300]], good=1),
    Shape("an hourly column with a blended rate in the total row",
          [["Name", "Hours", "Hourly", "Amount"], ["A", 10, 50, 500], ["B", 20, 80, 1600],
           ["Total", 30, 70, 2100]], good=2),
    Shape("one number typed as text among numbers",
          [HDR, ["a", 100], ["b", "200"], ["c", 300], ["Total", 600]],
          note="what a person reads adds up, a spreadsheet's own SUM would leave the text out: not judged"),
    # ---- right, in plain shapes ----
    Shape("a plain list", [HDR, ["Rent", 1200], ["Food", 300], ["Travel", 150.5], ["Total", 1650.5]], good=1),
    Shape("three totals in one row",
          [["Item", "Q1", "Q2", "Q3"], ["Rent", 1200, 1200, 1250], ["Food", 300, 310.25, 290],
           ["Total", 1500, 1510.25, 1540]], good=3),
    Shape("negative amounts", [HDR, ["Sales", 1000], ["Refunds", -150], ["Total", 850]], good=1),
    Shape("the label in capitals with a colon", [HDR, ["a", 1], ["b", 2], ["TOTAL:", 3]], good=1),
    Shape("a grand total label", [HDR, ["a", 1], ["b", 2], ["Grand Total", 3]], good=1),
    Shape("the plain word in German", [["Posten", "Betrag"], ["a", 1], ["b", 2], ["Summe", 3]], good=1),
    Shape("the plain word in Italian", [["Voce", "Importo"], ["a", 1], ["b", 2], ["Totale", 3]], good=1),
    Shape("one blank row before the total", [HDR, ["Rent", 100], ["Food", 300], [None, None], ["Total", 400]]),
    Shape("subtotals and a grand total",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", 1050], ["Fees", 20], ["Interest", 5],
           ["Subtotal", 25], ["Grand total", 1075]],
          note="three totals in one column: which cells each adds is a guess"),
    Shape("the total row above the data", [["Total", 400], ["Rent", 100], ["Food", 300]]),
    Shape("two tables side by side",
          [["Item", "Amount", None, "Item", "Amount"], ["a", 1, None, "x", 10], ["b", 2, None, "y", 20],
           ["Total", 3, None, "Total", 30]], good=2),
    Shape("two tables one above the other",
          [HDR, ["a", 1], ["b", 2], ["Total", 3], [None, None], HDR, ["c", 5], ["d", 6], ["Total", 11]],
          note="two totals in one column: a sectioned sheet is not judged"),
    Shape("a totals column of sums", [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, 3], ["b", 10, 20, 30]],
          note="a column HEADED by a total word is not judged (the check of it is gone)"),
    Shape("a totals column of quantity times price",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 7.5], ["ink", 2, 10, 20]]),
    Shape("a percent column in the total row",
          [["Item", "Amount", "Share %"], ["a", 25, 25], ["b", 75, 75], ["Total", 100, 50]], good=1),
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
          [["Date", "Amount"], [datetime(2024, 1, 1), 100], [datetime(2024, 1, 2), 300], ["Total", 400]],
          note="a row with no label in WORDS (a date, a number, nothing) is not a list this check judges"),
    Shape("rows labelled by a number", [["Year", "Sales"], [2021, 100], [2022, 140], ["Total", 240]]),
    Shape("amounts written as currency text",
          [HDR, ["a", "$100"], ["b", "$300"], ["Total", 400]]),
    Shape("a profit and loss layout",
          [["", "2024"], ["Revenue", 1000], ["Total revenue", 1000], ["Cost of sales", -400], ["Gross profit", 600]]),
    Shape("an invoice with a subtotal, tax and a total",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 7.5], ["ink", 2, 10, 20], ["Subtotal", None, None, 27.5],
           ["Tax", None, None, 2.2], ["Total", None, None, 29.7]],
          note="a subtotal and a total in one column"),
    Shape("a total that is a plain SUM of exactly the list",
          [HDR, ["a", 100], ["b", 300], ["Total", "=SUM(B2:B3)"]], good=1),
    Shape("a SUM total after a blank row",
          [HDR, ["a", 100], ["b", 300], [None, None], ["Total", "=SUM(B2:B3)"]]),
    Shape("a SUM total far below its list",
          [HDR, ["a", 100], ["b", 300], [None, None], [None, None], [None, None], ["Total", "=SUM(B2:B3)"]]),
    Shape("SUM subtotals and a grand total that adds them",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", "=SUM(B2:B3)"], ["Fees", 20],
           ["Interest", 5], ["Subtotal", "=SUM(B5:B6)"], ["Grand total", "=B4+B7"]],
          note="three totals in one column"),
    Shape("a list of formulas under a SUM",
          [["Item", "Qty", "Each", "Amount"], ["a", 2, 5, "=B2*C2"], ["b", 3, 7, "=B3*C3"],
           ["Total", 5, None, "=SUM(D2:D3)"]], good=1,
          note="the Qty column is typed (2 + 3 = 5); a list of formulas is not judged"),
    Shape("a totals column of SUM formulas",
          [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, "=SUM(B2:C2)"], ["b", 10, 20, "=SUM(B3:C3)"]],
          note="a column headed by a total word is not judged"),
    Shape("a SUBTOTAL formula", [HDR, ["a", 100], ["b", 300], ["Total", "=SUBTOTAL(9,B2:B3)"]]),
    Shape("an AVERAGE in a row called total", [HDR, ["a", 100], ["b", 300], ["Total", "=AVERAGE(B2:B3)"]]),
    # ---- the second review's everyday sheets: every one CORRECT, none may be worth a look ----
    Shape("quantity times a unit price", [["Item", "Value"], ["Quantity", 5], ["Unit price", 20], ["Total", 100]]),
    Shape("hours times a rate", [["Item", "Value"], ["Hours", 10], ["Rate", 50], ["Total", 500]]),
    Shape("nights times a price per night",
          [["Trip", "Cost"], ["Nights", 3], ["Price per night", 100], ["Total", 300]]),
    Shape("a monthly payment times its months",
          [["Loan", "Figure"], ["Monthly payment", 500], ["Months", 12], ["Total", 6000]]),
    Shape("a count, an average and their total",
          [["Statistic", "Value"], ["Count", 12], ["Average", 250], ["Total", 3000]]),
    Shape("a count, the largest, the smallest and a total",
          [["Statistic", "Value"], ["Invoices", 12], ["Largest", 900], ["Smallest", 40], ["Total", 3000]]),
    Shape("a price less a discount", [HDR, ["Price", 100], ["Discount", 20], ["Total", 80]]),
    Shape("income less expenses", [["Budget", "Amount"], ["Income", 5000], ["Expenses", 4200], ["Total", 800]]),
    Shape("a job less the deposit paid",
          [HDR, ["Kitchen refit", 9000], ["Extras", 500], ["Less deposit paid", 2000], ["Total", 7500]]),
    Shape("two heading rows, the second of years",
          [["Category", "Sales", None], ["Item", 2023, 2024], ["Rent", 100, 110], ["Food", 200, 210],
           ["Total", 300, 320]]),
    Shape("two heading rows, the second of quarters",
          [["Region", "Quarter", None], ["Name", 1, 2], ["North", 100, 110], ["South", 200, 210],
           ["Total", 300, 320]]),
    Shape("a growth column with an average in the total row",
          [["Region", "Sales", "Growth"], ["North", 100, 5], ["South", 200, 10], ["Total", 300, 7.5]], good=1),
    Shape("a cost each column with a blended figure",
          [["Item", "Qty", "Cost each", "Amount"], ["Widget", 2, 10, 20], ["Gadget", 3, 15, 45],
           ["Total", 5, 12.5, 65]], good=2),
    Shape("scores with their average in a row called total",
          [["Student", "Score"], ["Ann", 80], ["Bob", 90], ["Total", 85]]),
    Shape("a subtotal, tax and a total",
          [HDR, ["A", 100], ["B", 200], ["Subtotal", 300], ["Tax", 24], ["Total", 324]],
          note="a subtotal and a total in one column"),
    Shape("a subtotal, a discount typed positive and a total",
          [HDR, ["A", 100], ["B", 200], ["Subtotal", 300], ["Discount", 30], ["Total", 270]],
          note="a subtotal and a total in one column"),
    Shape("rows of fractions of a cent under a total of the rounded rows",
          [HDR, ["A", 33.335], ["B", 33.335], ["C", 33.335], ["Total", 100.02]]),
    Shape("thousands rounded in the total",
          [["Item", "Amount (000s)"], ["A", 1.4], ["B", 1.4], ["Total", 3]]),
    Shape("shares typed as whole numbers", [["Region", "Share of sales"], ["North", 40], ["South", 60], ["Total", 100]]),
    Shape("two sections and a grand total that is their difference",
          [["Line", "Amount"], ["Sales", 1000], ["Services", 500], ["Subtotal", 1500], ["Rent", 400], ["Wages", 600],
           ["Subtotal", 1000], ["Grand total", 500]],
          note="three totals in one column"),
    Shape("revenue less cost of sales", [["Line", "Amount"], ["Revenue", 1000], ["Cost of sales", 400], ["Total", 600]]),
    Shape("a total in the first row", [["Total", 600], ["Rent", 100], ["Food", 200], ["Fun", 300]]),
    Shape("a totals column with no total row",
          [["Item", "Qty", "Price", "Total"], ["W", 2, 10, 20], ["G", 3, 15, 45]]),
    Shape("a totals column and a total row",
          [["Item", "Jan", "Feb", "Total"], ["A", 1, 2, 3], ["B", 4, 5, 9], ["Total", 5, 7, 12]], good=3,
          note="the total ROW's three figures each equal the two typed cells above them; the column headed Total is not judged as a column"),
    Shape("a running column with the last value in the total row",
          [["Date", "Amount", "Running"], ["Mon", 10, 10], ["Tue", 20, 30], ["Total", 30, 30]], good=1),
    Shape("a rank column in the total row",
          [["Team", "Members", "Rank"], ["Red", 4, 1], ["Blue", 6, 2], ["Total", 10, 2]], good=1),
    Shape("an opening and a closing figure and their difference",
          [["Cash", "Amount"], ["Opening", 1000], ["Closing", 1500], ["Total", 500]]),
    Shape("a long table whose metric column holds a total",
          [["Region", "Metric", "Value"], ["North", "Orders", 12], ["North", "Customers", 9],
           ["North", "Total", 3000]]),
    Shape("weights and a weighted return",
          [["Asset", "Weight", "Return"], ["Stocks", 0.6, 8], ["Bonds", 0.4, 3], ["Total", 1, 6]], good=1),
    Shape("ages with a head count in the total row", [["Name", "Age"], ["Ann", 30], ["Bob", 40], ["Total", 2]]),
    Shape("target, actual and variance",
          [["Month", "Target", "Actual", "Variance"], ["Jan", 100, 90, -10], ["Feb", 100, 120, 20],
           ["Total", 200, 210, 10]], good=2),
    Shape("a year-to-date column", [["Month", "Sales", "YTD"], ["Jan", 100, 100], ["Feb", 150, 250],
                                    ["Total", 250, 250]], good=1),
    Shape("distance and a consumption figure",
          [["Trip", "Km", "L/100km"], ["A", 100, 6], ["B", 300, 8], ["Total", 400, 7.5]], good=1),
    Shape("a total rounded up at an exact half", [HDR, ["a", 12.5], ["b", 8], ["Total", 21]],
          note="20.5 is not 21: equal only after rounding, so nothing is said"),
    Shape("a total rounded to even at an exact half", [HDR, ["a", 12.5], ["b", 8], ["Total", 20]],
          note="20.5 is not 20: equal only after rounding"),
    Shape("a half at one decimal place, rounded up", [HDR, ["a", 0.25], ["b", 0.1], ["Total", 0.4]],
          note="0.35 is not 0.4: equal only after rounding"),
    Shape("a half at one decimal place, rounded down", [HDR, ["a", 0.25], ["b", 0.1], ["Total", 0.3]],
          note="0.35 is not 0.3: equal only after rounding"),
    Shape("a total written without its cents", [HDR, ["a", 600.20], ["b", 634.29], ["Total", 1234]]),
    Shape("a total written rounded", [HDR, ["a", 33.4], ["b", 66.9], ["Total", 100]]),
    Shape("a rate column beside a rounded total",
          [["Item", "Amount", "Rate"], ["a", 33.4, 0.2], ["b", 66.9, 0.4], ["Total", 100, 0.3]]),
    Shape("a units row under the heading", [HDR, [None, "USD"], ["Rent", 100], ["Food", 200], ["Total", 300]], good=1),
    Shape("a head count above the list",
          [["Name", "Hours"], ["Staff", 3], ["Ann", 10], ["Bob", 20], ["Cy", 30], ["Total", 60]]),
    Shape("a tax column of fractions",
          [["Item", "Amount", "Tax"], ["a", 100, 0.2], ["b", 200, 0.1], ["Total", 300, 0.13]], good=1),
    Shape("a list with a not-available amount", [HDR, ["a", 100], ["b", "N/A"], ["Total", 100]]),
    Shape("a title above the list", [["Budget", None], HDR, ["Rent", 100], ["Food", 200], ["Total", 300]], good=1),
    # ---- a formula total that does NOT add the list above it: no row ----
    Shape("a SUM that leaves the last row out",
          [HDR, ["a", 100], ["b", 300], ["c", 50], ["Total", "=SUM(B2:B3)"]], correct=False),
    Shape("a SUM that starts one row late",
          [HDR, ["a", 100], ["b", 300], ["c", 50], ["Total", "=SUM(B3:B4)"]], correct=False),
    Shape("a total that is one cell", [HDR, ["a", 100], ["b", 300], ["Total", "=B2"]], correct=False),
    Shape("a SUM of another column",
          [["Item", "Amount", "Other"], ["a", 100, 1], ["b", 300, 2], ["Total", "=SUM(C2:C3)", 3]], good=1,
          correct=False),
    Shape("a grand total whose SUM covers only the last group",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", "=SUM(B2:B3)"], ["Fees", 20],
           ["Interest", 5], ["Grand total", "=SUM(B5:B6)"]], correct=False,
          note="a subtotal and a grand total in one column"),
    Shape("a SUM that takes a year heading in",
          [["Item", 2023], ["a", 100], ["b", 300], ["Total", "=SUM(B1:B3)"]], correct=False),
    Shape("a SUM under a year typed as a number",
          [["Item", 2023], ["a", 100], ["b", 300], ["Total", "=SUM(B2:B3)"]]),
    Shape("a totals column whose SUM leaves a month out",
          [["Name", "Jan", "Feb", "Mar", "Total"], ["a", 1, 2, 3, "=SUM(B2:C2)"]], correct=False),
    Shape("a totals column of products",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, "=B2*C2"], ["ink", 2, 10, "=B3*C3"]]),
    # ---- a typed total that differs from a clean list, and nothing else explains it: WORTH A LOOK ----
    Shape("a total that differs under a clean list",
          [HDR, ["Rent", 1200], ["Food", 300], ["Travel", 150.5], ["Total", 1700]], look=("B5",), correct=False),
    Shape("650 against 600", [HDR, ["Rent", 100], ["Food", 200], ["Fun", 300], ["Total", 650]],
          correct=False,
          note="300 = 100 + 200: a row that equals the rows above it may be a subtotal, so the list is not judged"),
    Shape("one total that adds up and one that differs",
          [["Item", "A", "B"], ["x", 1, 10], ["y", 2, 20], ["Total", 3, 31]], good=1, look=("C4",), correct=False),
    Shape("three totals that differ",
          [["Item", "A", "B", "C"], ["x", 1, 10, 100], ["y", 2, 20, 200], ["Total", 4, 31, 301]],
          look=("B4", "C4", "D4"), correct=False),
    Shape("a subtotal that differs", [HDR, ["a", 1], ["b", 2], ["Subtotal", 99]], look=("B4",), correct=False),
    Shape("a total that differs in the lower of two tables",
          [HDR, ["a", 1], ["b", 2], ["Total", 3], [None, None], HDR, ["c", 5], ["d", 6], ["Total", 12]], correct=False,
          note="two totals in one column"),
    Shape("a total that differs by more than rounding",
          [HDR, ["a", 600.20], ["b", 634.29], ["Total", 1236]], look=("B4",), correct=False),
    # ---- a difference in any looser shape: no row, never "worth a look" ----
    Shape("a mismatch after a blank row",
          [HDR, ["Rent", 100], ["Food", 300], [None, None], ["Total", 999]], correct=False),
    Shape("a mismatch with no heading", [["Rent", 100], ["Food", 300], ["Total", 999]], correct=False),
    Shape("a mismatch under one row", [HDR, ["Rent", 100], ["Total", 999]], correct=False),
    Shape("a mismatch where the rows are numbered, not named",
          [["No", "Amount"], [1, 100], [2, 300], ["Total", 999]], correct=False),
    Shape("a mismatch with a blank cell inside",
          [HDR, ["Rent", 100], ["Parking", None], ["Food", 300], ["Total", 999]], correct=False),
    Shape("a mismatch under a list with a blank cell higher up",
          [HDR, ["a", 100], ["b", None], ["c", 300], ["d", 200], ["Total", 999]], correct=False),
    Shape("a total that leaves out a number typed as text",
          [HDR, ["a", 100], ["b", "200"], ["c", 300], ["Total", 400]], correct=False,
          note="a spreadsheet's SUM says 400, a person reading the list says 600: not judged"),
    Shape("a mismatch under a dash in the middle of the list",
          [HDR, ["a", 100], ["b", "-"], ["c", 300], ["d", 200], ["Total", 999]], correct=False),
    Shape("a mismatch with a number typed as text",
          [HDR, ["a", 100], ["b", "200"], ["c", 300], ["Total", 999]], correct=False),
    Shape("a mismatch under a year typed as text",
          [["Item", "2023"], ["a", 1], ["b", 2], ["Total", 99]], correct=False),
    Shape("a mismatch over subtotals",
          [["Line", "Amount"], ["Sales", 1000], ["Other", 50], ["Subtotal", 1050], ["Fees", 20], ["Interest", 5],
           ["Subtotal", 25], ["Grand total", 999]], correct=False,
          note="three totals in one column"),
    Shape("a typed total over a list of formulas",
          [["Item", "Qty", "Each", "Amount"], ["a", 2, 5, "=B2*C2"], ["b", 3, 7, "=B3*C3"],
           ["Total", 5, None, 99]], good=1, correct=False),
    Shape("a mismatch over an error value", [HDR, ["a", 100], ["b", "#N/A"], ["Total", 100]],
          correct=False),
    Shape("a totals column that is neither a sum nor a product",
          [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 99]], correct=False),
    Shape("a mismatch in a list with a row called Discount",
          [HDR, ["Price", 100], ["Discount", 20], ["Total", 999]], correct=False,
          note="a row that is taken away: the total is not the plain sum, so a difference is not judged"),
    Shape("a mismatch in a list with a rate row",
          [HDR, ["Net pay", 2000], ["Tax rate", 20], ["Total", 5000]], correct=False,
          note="a row that says it is a rate: the total is not the plain sum of the rows"),
    Shape("a mismatch in a list with a count and a largest row",
          [["Statistic", "Value"], ["Invoices", 12], ["Largest", 900], ["Smallest", 40], ["Total", 99999]], correct=False),
    Shape("a mismatch under a second heading row of years",
          [["Category", "Sales", "Sales"], ["Item", 2023, 2024], ["Rent", 100, 110], ["Food", 200, 210],
           ["Total", 999, 998]], correct=False),
    Shape("a mismatch above every row, under a second heading row of years",
          [["Category", "Sales", "Sales"], ["Item", 2023, 2024], ["Rent", 100, 110], ["Food", 200, 210],
           ["Total", 9999, 9998]], correct=False,
          note="no other reading gives 9999: only the heading row of years keeps it from being judged"),
    Shape("a mismatch in a long table's total row",
          [["Region", "Metric", "Value"], ["North", "Orders", 12], ["North", "Customers", 9],
           ["North", "Total", 999]], correct=False),
    # ---- a figure beside words that are not, WHOLE, a total word: not judged at all ----
    Shape("a figure beside Total due that differs", [HDR, ["a", 1], ["b", 2], ["Total due", 99]], correct=False),
    Shape("a figure beside Totals by region", [["Totals by region", 99], ["North", 1], ["South", 2]]),
    Shape("a rate total that is not the sum of the rates",
          [["Item", "Rate"], ["a", 0.2], ["b", 0.4], ["Total", 0.3]]),
    # ---- the third review's sheets (scratch probes r5rev3_correct / _batch2 / _more) ----
    # A subtotal of ANY kind in the list (by a word, or a row that equals the rows above it): no row.
    *[Shape(f"an invoice whose subtotal is called {label}",
            [HDR, ["Widget", 20], ["Gadget", 35], [label, 55], ["VAT", 11], ["Total", 66]],
            note="55 = 20 + 35: a subtotal, whatever it is called. The list is not judged")
      for label in ("Net", "Net amount", "Net total", "Total before tax", "Subtotal (ex VAT)", "Items total", "Goods")],
    Shape("an invoice with a net amount, tax and shipping",
          [["Item", "Qty", "Unit price", "Amount"], ["Widget", 2, 10, 20], ["Gadget", 1, 35, 35], ["Bracket", 3, 4, 12],
           ["Net amount", None, None, 67], ["Sales tax", None, None, 5.36], ["Shipping", None, None, 9],
           ["Total", None, None, 81.36]],
          note="a row called Net amount: a subtotal by its word"),
    Shape("an unlabelled subtotal and a total that fits neither reading",
          [HDR, ["Widget", 20], ["Gadget", 35], ["Net", 55], ["VAT", 11], ["Total", 70]], correct=False,
          note="it reads two ways (with and without the subtotal): not judged, never a look"),
    Shape("a last row that happens to equal the rows above it",
          [HDR, ["A", 100], ["B", 200], ["C", 300], ["Total", 650]], correct=False,
          note="300 = 100 + 200: it may be a subtotal, so nothing is said (it read 'worth a look')"),
    Shape("a last row that equals the rows above it, under a total that repeats it",
          [HDR, ["A", 100], ["B", 200], ["C", 300], ["Total", 300]], correct=False,
          note="never 'adds up': the list adds up to 600. 300 = 100 + 200 may be a subtotal: not judged"),
    Shape("the best two of three scores", [["Quiz", "Score"], ["Quiz 1", 8], ["Quiz 2", 6], ["Quiz 3", 9], ["Total", 17]]),
    Shape("a variance column in points",
          [["Region", "Plan", "Actual", "Variance"], ["North", 100, 105, 5], ["South", 100, 97, -3],
           ["West", 100, 101, 1], ["Total", 300, 303, 1]], good=2),
    Shape("revenue rows, then materials taken away",
          [HDR, ["Sales", 900], ["Other income", 100], ["Revenue", 1000], ["Materials", 400], ["Total", 600]]),
    Shape("hours and minutes under one total", [["Part", "Time"], ["Hours", 2], ["Minutes", 30], ["Total", 150]],
          look=("B4",), unknowable=True, note="2 h 30 min is 150 minutes: nothing on the sheet says so"),
    Shape("a salary less thirteen bills",
          [HDR, ["Salary", 3000]] + [[f"Bill {i}", 100 + i] for i in range(1, 13)]
          + [["Total", 3000 - sum(100 + i for i in range(1, 13))]]),
    Shape("a change in staff by month", [["Month", "Change in staff"], ["Jan", 5], ["Feb", -2], ["Mar", 4], ["Total", 7]]),
    Shape("temperatures with their mean in the total row",
          [["City", "Low", "High"], ["Oslo", -5, 2], ["Rome", 6, 14], ["Total", 0.5, 8]],
          note="a figure between the smallest and the largest row, of mixed signs"),
    Shape("goals for, against and the difference",
          [["Team", "For", "Against", "Diff"], ["Reds", 10, 4, 6], ["Blues", 3, 9, -6], ["Total", 13, 13, 0]], good=3),
    Shape("a survey's counts and fractions",
          [["Answer", "Count", "Fraction"], ["Yes", 60, 0.6], ["No", 40, 0.4], ["Total", 100, 1]], good=2),
    Shape("shares and a weighted cost",
          [["Lot", "Shares", "Cost basis"], ["Lot 1", 10, 50], ["Lot 2", 30, 70], ["Total", 40, 65]], good=1),
    Shape("gross less a fee", [HDR, ["Gross", 1000], ["Fee", 30], ["Total", 970]]),
    Shape("ticket sales and a sponsor less the venue",
          [HDR, ["Ticket sales", 1000], ["Sponsor", 500], ["Venue", 400], ["Total", 1100]]),
    Shape("a mileage total a cent off its rounded rows",
          [["Trip", "Miles", "Rate", "Amount"], ["Client A", 11.1, 0.67, 7.44], ["Client B", 11.1, 0.67, 7.44],
           ["Client C", 11.1, 0.67, 7.44], ["Total", 33.3, None, 22.31]], good=1, look=("D5",), unknowable=True,
          note="33.3 x 0.67 = 22.31; the rows add up to 22.32"),
    Shape("orders, units and a revenue called total",
          [["Metric", "Value"], ["Orders", 55], ["Units", 210], ["Total", 9450]], look=("B4",), unknowable=True),
    Shape("customers counted once across two months",
          [["Month", "Unique customers"], ["Jan", 100], ["Feb", 120], ["Total", 180]], look=("B4",), unknowable=True),
    Shape("a budget in sections under section titles",
          [["Category", "Budget"], ["Housing", None], ["Rent", 1200], ["Utilities", 150], ["Subtotal", 1350],
           ["Food", None], ["Groceries", 400], ["Dining", 120], ["Subtotal", 520], ["Grand total", 1870]],
          note="a title row has no amount: a blank in the list, so nothing is judged"),
    Shape("a bank register with a column for each way",
          [["Description", "Withdrawal", "Deposit", "Balance"], ["Opening balance", None, None, 1000],
           ["Rent", 500, None, 500], ["Salary", None, 2000, 2500], ["Groceries", 120, None, 2380],
           ["Total", 620, 2000, 2380]]),
    Shape("years as numbers in the heading row",
          [["Category", 2023, 2024, "Change"], ["Hardware", 100, 150, 50], ["Software", 200, 180, -20],
           ["Total", 300, 330, 30]]),
    # A total and a subtotal in one column: nothing is said of either (each read "adds up" or "worth a look").
    Shape("a total that forgot the tax row",
          [HDR, ["Widget", 100], ["Gadget", 200], ["Subtotal", 300], ["Tax", 24], ["Total", 300]], correct=False,
          note="a subtotal and a total in one column: not judged, so never a look"),
    Shape("a total that counts its subtotal twice",
          [HDR, ["Widget", 100], ["Gadget", 200], ["Subtotal", 300], ["Tax", 24], ["Total", 624]], correct=False,
          note="a subtotal and a total in one column"),
    Shape("a wrong total between a small row and the subtotal",
          [HDR, ["Widget", 100], ["Gadget", 200], ["Subtotal", 300], ["Tax", 24], ["Total", 250]], correct=False,
          note="a subtotal and a total in one column"),
    Shape("a total after a subtotal that is itself off",
          [HDR, ["Widget", 100], ["Gadget", 200], ["Subtotal", 310], ["Tax", 24], ["Total", 334]], correct=False,
          note="a subtotal and a total in one column"),
    Shape("a total with its tax row added",
          [HDR, ["Widget", 100], ["Gadget", 200], ["Subtotal", 300], ["Tax", 24], ["Total", 324]],
          note="a subtotal and a total in one column"),
    Shape("a grand total that left the shipping out",
          [HDR, ["Rent", 1200], ["Power", 150], ["Subtotal", 1350], ["Food", 400], ["Dining", 120], ["Subtotal", 520],
           ["Shipping", 50], ["Grand total", 1870]], correct=False,
          note="three totals in one column"),
    Shape("a grand total that is only the row after the subtotal",
          [HDR, ["Widget", 100], ["Gadget", 200], ["Subtotal", 300], ["Shipping", 50], ["Grand total", 50]], correct=False,
          note="a subtotal and a grand total in one column"),
    Shape("a total over the rows after a word in the list",
          [HDR, ["A", 100], ["B", "TBD"], ["C", 50], ["D", 25], ["Total", 75]], correct=False,
          note="75 is C + D: never 'adds up' over part of a list"),
    Shape("a total with a word in the list above it",
          [HDR, ["A", 100], ["B", "TBD"], ["C", 50], ["D", 25], ["Total", 175]]),
    Shape("a total over the rows after a blank row",
          [HDR, ["A", 100], ["B", 200], [None, None], ["C", 50], ["D", 25], ["Total", 75]], correct=False),
    Shape("a total between its smallest and its largest row",
          [HDR, ["A", 100], ["B", 200], ["C", 300], ["Total", 250]], correct=False,
          note="admitted: an average reads the same (and 300 = 100 + 200 may be a subtotal)"),
    Shape("a wrong total between the smallest and the largest of rows that are no subtotal",
          [HDR, ["A", 100], ["B", 250], ["C", 300], ["Total", 260]], correct=False,
          note="admitted: 260 lies between 100 and 300, and an average or a last value reads the same. Not judged"),
    Shape("a word in the first row of the list, and a total of the numbers under it",
          [HDR, ["A", "TBD"], ["B", 50], ["C", 25], ["Total", 75]], good=1,
          note="admitted: the word reads as a second heading row (a units row reads the same). The total equals "
               "every number above it, which is all 'adds up' says"),
    Shape("a word in the first row of the list, and a total that counts a guess for it",
          [HDR, ["A", "TBD"], ["B", 50], ["C", 25], ["Total", 175]], look=("B5",), correct=False,
          note="175 may count 100 for the row that says TBD: the numbers above it add up to 75, and both are said"),
    Shape("a number typed as text inside a SUM total's list",
          [HDR, ["A", 100], ["B", "200"], ["C", 300], ["Total", "=SUM(B2:B4)"]]),
    Shape("a totals column that adds a quantity to a price",
          [["Item", "Qty", "Price", "Total"], ["A", 3, 5, 8], ["B", 2, 10, 12]], correct=False,
          note="should be 15 and 20: a column beside a price is never said to add up"),
    Shape("a wrong total under thirteen rows",
          [HDR] + [[f"Row {i}", 10] for i in range(1, 14)] + [["Total", 150]], look=("B15",), correct=False),
    Shape("a wrong total over rows that are formulas",
          [HDR, ["A", "=50*2"], ["B", 200], ["Total", 350]], correct=False,
          note="the rows are worked out, not typed: a difference is not judged"),
    Shape("a SUM total over a row the evaluator refuses",
          [["Item", "Jan", "Feb", "Both"], ["A", 50, "n/a", "=B2+C2"], ["B", 50, 0, "=B3+C3"],
           ["Total", None, None, "=SUM(D2:D3)"]],
          note="D2 is #VALUE! in Excel: no fallback figure is ever read for it"),
    Shape("a typed total over a row the evaluator refuses",
          [["Item", "Jan", "Feb", "Both"], ["A", 50, "abc", "=B2+C2"], ["B", 50, 0, "=B3+C3"],
           ["Total", None, None, 100]], correct=False),
    # ---- the fourth review's sheets (scratch probe r5rev4_p3_sheets) ----
    Shape("two Subtotals with rows between them, the second a copy of the first",
          [HDR, ["Rent", 100], ["Power", 200], ["Subtotal", 300], ["Food", 50], ["Fun", 70], ["Subtotal", 300],
           ["Total", 420]], correct=False,
          note="it read '3 totals add up'. Three totals in one column: no row"),
    Shape("two Subtotals with rows between them and no total",
          [HDR, ["Rent", 100], ["Power", 200], ["Subtotal", 300], ["Food", 50], ["Fun", 70], ["Subtotal", 300]],
          correct=False, note="it read '2 totals add up'"),
    Shape("a Total column with a blank row and wrong rows below it",
          [["Product", "Jan", "Feb", "Total"], ["Apples", 3, 4, 7], ["Pears", 1, 2, 3], [None, None, None, None],
           ["Plums", 6, 6, 99], ["Figs", 2, 2, 50]], correct=False,
          note="it read '1 total adds up' over the two rows it reached. A column of line totals is not judged"),
    Shape("a Total column with one wrong row", [["Product", "Jan", "Feb", "Total"], ["Apples", 3, 4, 7],
                                                 ["Pears", 1, 2, 30], ["Plums", 6, 6, 12]], correct=False),
    Shape("a budget in sections, tightly packed",
          [["Category", "Budget"], ["Rent", 1200], ["Utilities", 150], ["Subtotal", 1350], ["Groceries", 400],
           ["Eating out", 120], ["Subtotal", 520], ["Grand total", 1870]],
          note="every figure is right, and nothing is said: a sectioned sheet is not judged"),
    Shape("a grand total that misses a section",
          [["Category", "Budget"], ["Rent", 1200], ["Utilities", 150], ["Subtotal", 1350], ["Groceries", 400],
           ["Eating out", 120], ["Subtotal", 520], ["Grand total", 1350]], correct=False),
    Shape("a total that counts a net amount and its rows",
          [HDR, ["a", 100], ["b", 200], ["Net amount", 300], ["VAT", 60], ["Total", 660]], correct=False,
          note="every cell above it added: 660. With a subtotal in the list 'adds up' is never said"),
    Shape("a total that left the last row out, over a row that equals the rows above it",
          [HDR, ["a", 100], ["b", 200], ["c", 300], ["d", 50], ["Total", 600]], correct=False,
          note="300 = 100 + 200: not judged"),
    Shape("a subtotal alone in its column", [HDR, ["a", 100], ["b", 250], ["Subtotal", 350]], good=1),
    Shape("a total that is its two rows to the last cent", [HDR, ["a", 10.10], ["b", 20.20], ["Total", 30.30]], good=1),
    Shape("a total a cent off its two rows", [HDR, ["a", 10.10], ["b", 20.20], ["Total", 30.31]], look=("B4",),
          correct=False),
    Shape("a sign forgotten on a refund", [HDR, ["Income", 500], ["Refund given", -200], ["Total", 700]], correct=False,
          note="700 is 500 less -200: a plain other reading (rows taken away) explains it, so it is not judged"),
]


def _judged(path: Path) -> "tuple[tuple[int, tuple], list[Check]]":
    """``((good, the cells worth a look), the totals rows)`` as the checks say it."""
    got = _by(content.check_files([str(path)]), SHEET_TOTALS)
    good, look = 0, ()
    for c in got:
        if c.result == PASSED:
            good = int(re.fullmatch(r"(\d+) totals? adds? up", c.detail).group(1))
        elif c.result == LOOK:
            look = tuple(re.findall(r"(?:^|; )([A-Z]+\d+) says ", c.detail))
    return (good, look), got


def _write(how: str, path: Path, rows) -> Path:
    if how == "openpyxl":
        return _xlsx(path, rows)
    write_document(path, rows)       # the app's own writer (a None cell is an empty one)
    return path


def test_the_table_holds_every_everyday_shape():
    assert len(SHAPES) >= 100 and len({s.name for s in SHAPES}) == len(SHAPES)
    assert sum(1 for s in SHAPES if s.correct) >= 70 and sum(1 for s in SHAPES if s.look) >= 6
    # Most everyday sheets get NO row about their totals now: that is the rule, and the table says so.
    silent = sum(1 for s in SHAPES if not s.good and not s.look)
    assert silent > len(SHAPES) // 2, silent
    # A correct sheet is never expected to be worth a look, but for the few
    # nothing on the sheet can explain (each is named so, and stays listed).
    assert not [s.name for s in SHAPES if s.correct and s.look and not s.unknowable]
    assert all(s.correct and s.look for s in SHAPES if s.unknowable)
    assert 1 <= sum(1 for s in SHAPES if s.unknowable) <= 6


@pytest.mark.parametrize("how", ["openpyxl", "the app's own writer"])
def test_over_the_whole_table_no_total_is_ever_a_problem(tmp_path, how):
    """The blanket: a total is Iron Jarvis's own adding up. Whatever the
    sheet, right or wrong, the totals check NEVER returns ``failed``."""
    seen = 0
    for i, shape in enumerate(SHAPES):
        p = _write(how, tmp_path / f"sheet{i}.xlsx", shape.rows)
        got = content.check_files([str(p)])
        assert not [c for c in got if c.check == SHEET_TOTALS and c.result == FAILED], shape.name
        # The only problem a sheet of this table can have is an error VALUE a cell holds.
        problems = [c for c in got if c.result == FAILED]
        assert all(c.check == SHEET_ERRORS and " holds #" in c.detail for c in problems), shape.name
        assert ("Problem" in line_for(_rows(got))[0]) == bool(problems), shape.name
        seen += 1
    assert seen == len(SHAPES)


@pytest.mark.parametrize("how", ["openpyxl", "the app's own writer"])
@pytest.mark.parametrize("shape", SHAPES, ids=[s.name for s in SHAPES])
def test_every_shape_of_total_gets_the_answer_the_table_says(tmp_path, shape, how):
    p = _write(how, tmp_path / "sheet.xlsx", shape.rows)
    got, rows = _judged(p)
    assert got == (shape.good, tuple(shape.look)), (shape.name, how, [c.detail for c in rows])
    assert not [c for c in rows if c.result in (FAILED, NOT_CHECKED)], f"a total reads 'Problem' or 'Not checked': {shape.name}"
    if shape.correct and not shape.unknowable:
        assert not [c for c in rows if c.result == LOOK], f"a correct sheet is 'worth a look': {shape.name}"
        line, tone = line_for(_rows(content.check_files([str(p)])))
        assert tone == "quiet" and "Problem" not in line and "Worth a look" not in line, (shape.name, line)
    if not shape.good and not shape.look:
        assert rows == [], "a total that is not the one shape got a row"


def test_a_total_that_differs_is_worth_a_look_with_both_figures_and_no_verdict(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1700]])
    got = _by(content.check_files([str(p)]), SHEET_TOTALS)
    assert got == [Check(SHEET_TOTALS, LOOK, "B5 says 1,700 and the cells above it add up to 1,650.50", file="t.xlsx")]
    line, tone = line_for(_rows(content.check_files([str(p)])))
    assert line == ("Checked by Iron Jarvis: the spreadsheet opens, no error cells. "
                    "Worth a look: B5 says 1,700 and the cells above it add up to 1,650.50.")
    assert tone == "warn" and "Problem" not in line and "wrong" not in line


def test_one_total_that_adds_up_and_one_that_differs_are_both_said(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "A", "B"], ["x", 1, 10], ["y", 2, 20], ["Total", 3, 31]])
    got = {c.result: c.detail for c in _by(content.check_files([str(p)]), SHEET_TOTALS)}
    assert got == {PASSED: "1 total adds up", LOOK: "C4 says 31 and the cells above it add up to 30"}
    many = _xlsx(tmp_path / "m.xlsx", [["Item", "A", "B", "C", "D"], ["x", 1, 1, 1, 1], ["y", 2, 2, 2, 2],
                                       ["Total", 9, 9, 9, 9]])
    (look,) = [c for c in _by(content.check_files([str(many)]), SHEET_TOTALS) if c.result == LOOK]
    assert look.detail.endswith("; and 1 more totals") and look.detail.count(" says 9 ") == 3


@pytest.mark.parametrize("rows,total,result", [
    # "Adds up" means exactly its arithmetic: EQUAL to the sum of every cell of the list.
    ([33.33, 33.33, 33.34], 100, PASSED), ([0.1, 0.2], 0.3, PASSED), ([0.1, 0.2, 0.35], 0.65, PASSED),
    ([1e15, 1], 1e15 + 1, PASSED), ([-12.5, -8], -20.5, PASSED), ([1000000.1, -1000000, 0.2], 0.3, PASSED),
    ([0.1] * 10, 1, PASSED), ([19.99] * 7, 139.93, PASSED),
    # A total that matches only after ROUNDING is a guess about what its author meant: nothing is said.
    ([12.5, 8], 21, None), ([12.5, 8], 20, None), ([0.25, 0.1], 0.4, None), ([0.25, 0.1], 0.3, None),
    ([-12.5, -8], -21, None), ([-12.5, -8], -20, None),
    ([600.20, 634.29], 1234, None), ([33.4, 66.9], 100, None), ([1.4, 1.4], 3, None),
    ([33.335, 33.335, 33.335], 100.02, None), ([33.333, 33.333, 33.333], 100, None),
    # A figure between the smallest and the largest row is a plain other reading: not judged.
    ([1000000.1, -1000000, 0.2], 0.31, None), ([19.99] * 7, 19.99, None),
    # ... and one that is simply different is worth a look, rounded or not.
    ([600.20, 634.29], 1236, LOOK), ([12.5, 8], 22, LOOK), ([0.25, 0.1], 0.5, LOOK), ([100, 200, 310], 650, LOOK),
    ([1e15, 1], 1e15 + 3, LOOK), ([10.10, 20.20], 30.31, LOOK),
])
def test_adds_up_means_exactly_its_arithmetic_and_a_total_that_only_rounds_to_it_is_not_judged(
        tmp_path, rows, total, result):
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "Amount"], *[[f"row {i}", v] for i, v in enumerate(rows)],
                                    ["Total", total]])
    got = _by(content.check_files([str(p)]), SHEET_TOTALS)
    assert [c.result for c in got] == ([result] if result else []), [c.detail for c in got]
    assert NOT_CHECKED not in [c.result for c in got]


def test_the_total_is_quoted_as_its_cell_holds_it_and_two_figures_never_print_the_same(tmp_path):
    """The line quotes the cell: 0.0425 stays 0.0425 (it read "0.04"), and
    2.0149 stays 2.0149 (it read "2.015"). The sum is printed with as many
    places as it takes to differ from it."""
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "Amount"], ["a", 1.0025], ["b", 1.0026], ["Total", 2.0149]])
    (look,) = _by(content.check_files([str(p)]), SHEET_TOTALS)
    assert (look.result, look.detail) == (LOOK, "B4 says 2.0149 and the cells above it add up to 2.0051")
    assert content._figures(2.0149, 2.0051) == ("2.0149", "2.0051")
    assert content._figures(0.0425, 0.07) == ("0.0425", "0.0700")
    assert content._figures(1700, 1650.5) == ("1,700", "1,650.50")
    assert content._figures(22.31, 22.32) == ("22.31", "22.32")
    assert content._figures(1234.5, 1234.75) == ("1,234.5", "1,234.75")
    assert content._figures(300, 324) == ("300", "324")
    assert content._figures(0.123456, 0.1234569) == ("0.123456", "0.123457")
    assert content._figures(3.0, 3.0) == ("3", "3")
    # Two that differ far to the right never print as the same NUMBER ("1" and "1.00" are one number).
    assert content._figures(1.0, 1.0000001) == ("1", "1.0000001")


def test_a_total_in_any_other_shape_gets_no_row_at_all(tmp_path):
    """Decision 3. Not "adds up", not "worth a look", and not "not checked"
    either: where the shape needs a guess about what the author meant,
    nothing is said. Each of these said something before."""
    loose = {
        "no heading": [["Rent", 100], ["Food", 300], ["Total", 999]],
        "a subtotal in the list, total right": [["Item", "Amount"], ["Widget", 100], ["Gadget", 200], ["Subtotal", 300],
                                                ["Tax", 24], ["Total", 324]],
        "a subtotal in the list, total forgot the tax": [["Item", "Amount"], ["Widget", 100], ["Gadget", 200],
                                                         ["Subtotal", 300], ["Tax", 24], ["Total", 300]],
        "a row that is the sum of the rows above it": [["Item", "Amount"], ["A", 100], ["B", 200], ["C", 300],
                                                       ["Total", 650]],
        "a formula in the list": [["Item", "Amount"], ["A", "=50*2"], ["B", 200], ["Total", 300]],
        "one row": [["Item", "Amount"], ["Rent", 100], ["Total", 100]],
        "a blank in the list": [["Item", "Amount"], ["Rent", 100], ["Parking", None], ["Food", 300], ["Total", 400]],
        "a column of line totals": [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, 3], ["b", 10, 20, 30]],
        # A column that is not added, by its heading: nothing is said even when the figure IS its sum ...
        "a rate column whose total is the sum of the rates": [["Item", "Rate"], ["a", 0.2], ["b", 0.4], ["Total", 0.6]],
        # ... or is far from it.
        "an invoice number column": [["Client", "Invoice no"], ["Acme", 1001], ["Zed", 1002], ["Total", 5000]],
        # A row whose WORD says it is a subtotal, though it is not the sum of the rows above it.
        "a row called Net amount": [["Item", "Amount"], ["a", 100], ["b", 200], ["Net amount", 310], ["VAT", 60],
                                    ["Total", 670]],
        "a number above the heading": [["Item", 2024], ["Item", "Amount"], ["a", 1], ["b", 5], ["Total", 6]],
    }
    for name, rows in loose.items():
        p = _xlsx(tmp_path / "t.xlsx", rows)
        assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [], name


def test_a_workbook_with_too_many_totals_says_nothing_about_them(tmp_path, monkeypatch):
    """Past the cap nothing is judged at all (never "adds up" over the ones
    that were reached)."""
    side = [["Item", "Amount", None, "Item", "Amount"], ["a", 1, None, "x", 10], ["b", 5, None, "y", 20],
            ["Total", 6, None, "Total", 30]]
    p = _xlsx(tmp_path / "t.xlsx", side)
    assert [c.detail for c in _by(content.check_files([str(p)]), SHEET_TOTALS)] == ["2 totals add up"]
    monkeypatch.setattr(content, "MAX_TOTALS", 1)
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == []


def test_three_functions_refuse_arguments_outside_their_range_never_a_clean_number(tmp_path):
    """Decision 12. Each read "its formula works out" where Excel shows an
    error. EOMONTH to a month before the calendar is the error Excel
    documents; the other two (and EDATE) cannot be worked out here."""
    worked, errors, unworked = _worked_out(tmp_path, [["a", "=EOMONTH(1,-5)"]])
    assert (worked, errors, unworked) == ({}, [(1, 2)], 0)
    for formula in ("=EDATE(1,-5)", "=SYD(100,10,5,6)", "=SYD(100,10,5,0)", "=SYD(100,10,0,1)", "=PMT(0.05,5,100,0,3)",
                    "=FV(0.05,5,-100,0,2)", "=PV(0.05,5,-100,0,-1)"):
        assert _worked_out(tmp_path, [["a", formula]]) == ({}, [], 1), formula
    # The neighbours that ARE numbers in Excel still are.
    fine = [["end of month", "=EOMONTH(DATE(2024,1,15),1)"], ["a month on", "=EDATE(DATE(2024,1,31),1)"],
            ["the second year", "=SYD(100,10,5,2)"], ["the last year", "=SYD(100,10,5,5)"],
            ["due at the start", "=PMT(0.05,5,100,0,1)"], ["due at the end", "=PMT(0.05,5,100,0,0)"],
            ["no last argument", "=PMT(0.05,5,100)"]]
    worked, errors, unworked = _worked_out(tmp_path, fine)
    assert (errors, unworked) == ([], 0) and len(worked) == 7
    assert worked[(1, 2)] == 45351.0 and worked[(2, 2)] == 45351.0          # 29 February 2024
    assert worked[(3, 2)] == pytest.approx(24.0) and worked[(4, 2)] == pytest.approx(6.0)
    assert worked[(5, 2)] == pytest.approx(worked[(6, 2)] / 1.05) and worked[(6, 2)] == worked[(7, 2)]
    p = _xlsx(tmp_path / "t.xlsx", [["a", 5], ["t", "=SYD(100,10,5,6)"]])
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "1 formula could not be worked out here", file="t.xlsx")]


def test_the_one_shape_holds_no_subtotal_of_any_kind():
    """A word that reads like a total in ANY cell of a row, and a row that
    equals the sum of two or more rows directly above it."""
    def held(rows):
        cells = {(r, c): v for r, row in enumerate(rows, start=1) for c, v in enumerate(row, start=1) if v is not None}
        by_row: dict = {}
        for (r, c) in cells:
            by_row.setdefault(r, []).append(c)
        return content._has_subtotal(cells, by_row, [(r, float(row[1])) for r, row in enumerate(rows, start=1)])

    assert not held([["Rent", 1200], ["Food", 300], ["Travel", 150.5]])
    for label in ("Subtotal", "Sub-total", "Net", "Net amount", "Total before tax", "Items total", "Gross pay",
                  "Zwischensumme Gesamt", "Sum of the above"):
        assert held([["Rent", 100], [label, 100], ["Tax", 5]]), label
    assert held([["x", 5, "net of fees"], ["y", 7], ["z", 9]]), "the word may sit in any cell of the row"
    assert not held([["Internet", 100], ["Sumo class", 20], ["Grossery", 7]]), "a word, not a part of one"
    # A row that equals two or more rows directly above it: anywhere in the list, of any length.
    assert held([["a", 100], ["b", 200], ["c", 300]]) and held([["a", 100], ["b", 200], ["c", 300], ["d", 50]])
    assert held([["x", 7], ["a", 100], ["b", 200], ["c", 300], ["d", 50]]), "the run need not start the list"
    assert held([["a", 1], ["b", 2], ["c", 3], ["d", 4], ["e", 10]]) and held([["a", 33.33], ["b", 33.33], ["c", 66.66]])
    assert not held([["a", 100], ["b", 100]]), "one row above is a row that repeats, not a sum"
    assert not held([["a", 100], ["b", 250], ["c", 300]]) and not held([["a", 100], ["b", 200], ["c", 301]])
    assert held([["a", 0.1], ["b", 0.2], ["c", 0.3]]), "what a computer's arithmetic loses is not a difference"
    # A long list is looked through in one pass (never one row against every row above it, one by one).
    long = [[f"r{i}", 1000.5] for i in range(20_000)]
    assert not held(long) and held(long + [["the last three", 3001.5]])


@pytest.mark.parametrize("vals,total", [
    ([5, 20], 100), ([10, 50], 500), ([500, 12], 6000),             # the product of the rows
    ([100, 20], 80), ([5000, 4200], 800), ([9000, 500, 2000], 7500), ([1000, 1500], 500),   # rows taken away
    ([2023, 100, 200], 300), ([3, 10, 20, 30], 60),                 # the sum without the top row
    ([80, 90], 85), ([100, 250], 250), ([5, 10], 7.5), ([1, 2], 2),  # between the smallest and the largest
    ([30, 40], 2), ([7, 9, 11], 3),                                  # how many rows there are
])
def test_a_plain_other_reading_of_the_rows_explains_a_total(vals, total):
    assert content._explained(float(total), [float(v) for v in vals]) is True


@pytest.mark.parametrize("vals,total", [
    ([100, 200, 300], 650), ([100, 200, 300], 5000), ([1200, 300, 150.5], 1700), ([1, 2], 9), ([5, 6], 12),
    ([10, -4, 3], 100),
])
def test_a_total_nothing_else_explains_is_not_explained(vals, total):
    assert content._explained(float(total), [float(v) for v in vals]) is False


def test_rows_are_taken_away_only_over_a_short_list(tmp_path):
    """Every choice of signs is tried over at most twelve rows (4,096
    choices), never over a long list."""
    short = [float(i) for i in range(1, 13)]                 # twelve rows, the fourth taken away
    assert content._explained(sum(short) - 8.0, short) is True
    longer = [float(i) for i in range(1, 14)]               # thirteen: not tried
    assert content._explained(sum(longer) - 8.0, longer) is False


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
    # A plain SUM of exactly the typed list above it adds up by what it IS, whatever anybody saved for it.
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="t.xlsx")]


def test_a_formula_total_adds_up_only_when_it_sums_exactly_the_list_above_it(tmp_path):
    """S1: ``=SUM(B2:B3)`` over data in B2:B4 used to read "1 total adds
    up" (it adds what it names). Any formula that is not a plain SUM of
    exactly the list above it gets no row."""
    for name, formula in {"short": "=SUM(B2:B3)", "late": "=SUM(B3:B4)", "long": "=SUM(B1:B4)", "cell": "=B2",
                          "plus": "=B2+B3+B4", "other column": "=SUM(A2:A4)", "two ranges": "=SUM(B2:B3,B4)",
                          "another sheet": "=SUM(Other!B2:B4)", "twice": "=SUM(B2:B4)*2",
                          "an average": "=AVERAGE(B2:B4)", "a subtotal function": "=SUBTOTAL(9,B2:B4)"}.items():
        p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", formula]], more={"Other": [["a", 1]]})
        assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [], name
    exact = _xlsx(tmp_path / "exact.xlsx", [*BUDGET, ["Total", "=SUM(B2:B4)"]], more={"Other": [["a", 1]]})
    assert [c.result for c in _by(content.check_files([str(exact)]), SHEET_TOTALS)] == [PASSED]
    named = _xlsx(tmp_path / "named.xlsx", [*BUDGET, ["Total", "=SUM(Summary!$B$2:$B$4)"]], more={"Other": [["a", 1]]})
    assert [c.result for c in _by(content.check_files([str(named)]), SHEET_TOTALS)] == [PASSED]


def test_a_formula_total_whose_saved_result_differs_from_its_cells_gets_no_row(tmp_path):
    """A file that stands by a saved result for its SUM which is NOT what
    its cells add up to: the formula adds up by what it is, the file says
    another figure. Nothing is said: never "adds up", never a problem."""
    plain = _xlsx(tmp_path / "plain.xlsx", [*BUDGET, ["Total", "=SUM(B2:B4)"]])

    def saved(figure: str):
        def change(name: str, data: bytes) -> bytes:
            if name != "xl/worksheets/sheet1.xml":
                return data
            text = data.decode("utf-8")
            for empty in ("<v></v>", "<v />", "<v/>"):
                text = text.replace(f"<f>SUM(B2:B4)</f>{empty}", f"<f>SUM(B2:B4)</f><v>{figure}</v>")
            assert f"<v>{figure}</v>" in text, text[-400:]
            return text.encode("utf-8")
        return change

    stale = _stands_by(_repack(plain, tmp_path / "a.xlsx", saved("1500")), tmp_path / "stale.xlsx")
    assert content._load(stale).values["Summary"] == {(5, 2): 1500}
    got = content.check_files([str(stale)])
    assert _by(got, SHEET_TOTALS) == [] and not [c for c in got if c.result in (FAILED, LOOK)]
    right = _stands_by(_repack(plain, tmp_path / "b.xlsx", saved("1650.5")), tmp_path / "right.xlsx")
    assert _by(content.check_files([str(right)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="right.xlsx")]
    # The same stale figure in a file that asks to be worked out again is a placeholder: it is not read at all.
    asks = _repack(plain, tmp_path / "asks.xlsx", saved("1500"))
    assert _by(content.check_files([str(asks)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="asks.xlsx")]


def test_a_total_over_a_formula_nobody_could_work_out_gets_no_row(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [
        ["Item", "Amount"],
        ["Rent", 1200],
        ["Food", "=VLOOKUP(A3,Other!A:B,2,0)"],     # no saved result, and no such sheet
        ["Total", "=SUM(B2:B3)"],
        ["Grand total", "=AVERAGE(B2:B3)*2"],
    ])
    checks = content.check_files([str(p)])
    assert _by(checks, SHEET_TOTALS) == []
    assert _by(checks, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "3 formulas could not be worked out here", file="t.xlsx")]


def test_a_sheet_with_a_subtotal_or_two_totals_in_a_column_says_nothing_about_totals(tmp_path):
    """The subtotal acceptance is GONE. A grand total over subtotals read
    "3 totals add up"; a second Subtotal that was a copy of the first read
    the same. Which cells each of several totals adds is a guess."""
    rows = [
        ["Line", "Amount"],
        ["Sales", 1000], ["Other", 50], ["Subtotal", 1050],
        ["Fees", 20], ["Interest", 5], ["Subtotal", 25],
    ]
    sheets = {
        "right": [*rows, ["Grand total", 1075]],
        "a grand total off": [*rows, ["Grand total", 999]],
        "the second subtotal is a copy of the first": [*rows[:6], ["Subtotal", 1050], ["Grand total", 1075]],
        "two subtotals and no grand total": rows,
        "one subtotal, then a total": [["Item", "Amount"], ["Widget", 100], ["Gadget", 250], ["Subtotal", 350],
                                       ["Total", 350]],
        "two tables one above the other": [["Item", "Amount"], ["a", 1], ["b", 5], ["Total", 6], [None, None],
                                           ["Item", "Amount"], ["c", 5], ["d", 7], ["Total", 12]],
    }
    for name, grid in sheets.items():
        got = content.check_files([str(_xlsx(tmp_path / "t.xlsx", grid))])
        assert _by(got, SHEET_TOTALS) == [], name
        assert line_for(_rows(got))[0] == "Checked by Iron Jarvis: the spreadsheet opens, no error cells.", name
    # One total alone in its column is judged, each column on its own (two tables side by side).
    side = [["Item", "Amount", None, "Item", "Amount"], ["a", 1, None, "x", 10], ["b", 5, None, "y", 20],
            ["Total", 6, None, "Total", 30]]
    assert _by(content.check_files([str(_xlsx(tmp_path / "s.xlsx", side))]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "2 totals add up", file="s.xlsx")]


def test_a_column_headed_by_a_total_word_is_not_judged(tmp_path):
    """The check of a totals COLUMN is GONE: it walked down to the first
    blank and said "adds up" over the rows it never reached."""
    for name, rows in {
        "sums.xlsx": [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, 3], ["b", 10, 20, 30]],
        "wrong_below_a_blank.xlsx": [["Product", "Jan", "Feb", "Total"], ["Apples", 3, 4, 7], ["Pears", 1, 2, 3],
                                     [None, None, None, None], ["Plums", 6, 6, 99], ["Figs", 2, 2, 50]],
        "product.xlsx": [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 7.5], ["ink", 2, 10, 20]],
        "sum_of_qty_and_price.xlsx": [["Item", "Qty", "Price", "Total"], ["A", 3, 5, 8], ["B", 2, 10, 12]],
        "neither.xlsx": [["Item", "Qty", "Each", "Total"], ["pen", 3, 2.5, 99]],
        "sum_formulas.xlsx": [["Name", "Jan", "Feb", "Total"], ["a", 1, 2, "=SUM(B2:C2)"], ["b", 10, 20, "=SUM(B3:C3)"]],
    }.items():
        assert _by(content.check_files([str(_xlsx(tmp_path / name, rows))]), SHEET_TOTALS) == [], name
    assert not hasattr(content, "_column_total")


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
    # The third review: a growth, a return, a yield, a change, a variance, a score, a rank, an average.
    ("Growth", False), ("Return", False), ("Yield", False), ("Change", False), ("Change in staff", False),
    ("Variance", False), ("Score", False), ("Rank", False), ("Average", False), ("Mean", False), ("Median", False),
    ("Exchange", True), ("Scoreboard points", True), ("Returning customers", True),
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
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:v="urn:schemas-microsoft-com:vml"'


def _docx(path: Path, body_xml: str = "", header_text: str = "") -> Path:
    """A real Word file whose body is given as XML (a text box and a content
    control are not things the library's own API builds)."""
    import docx
    from docx.oxml import parse_xml

    d = docx.Document()
    if body_xml:
        d.element.body.insert(0, parse_xml(body_xml))
    if header_text:
        d.sections[0].header.paragraphs[0].text = header_text
    d.save(str(path))
    return path


def test_a_word_document_with_no_text_found_is_worth_a_look_never_a_problem(tmp_path):
    import docx

    empty = tmp_path / "empty.docx"
    docx.Document().save(str(empty))
    got = content.check_files([str(empty)])
    assert got == [Check(FILE_OPENS, PASSED, "opens", file="empty.docx"),
                   Check(DOC_TEXT, LOOK, "Iron Jarvis found no text in it", file="empty.docx")]
    line, tone = line_for(_rows(got))
    assert line == "Checked by Iron Jarvis: the document opens. Worth a look: Iron Jarvis found no text in it."
    assert tone == "warn" and "Problem" not in line
    full = tmp_path / "letter.docx"
    d = docx.Document()
    d.add_paragraph("Dear Sam, the figures are attached.")
    d.save(str(full))
    assert _by(content.check_files([str(full)]), DOC_TEXT) == [Check(DOC_TEXT, PASSED, "has text", file="letter.docx")]


def test_found_no_text_is_said_only_when_the_reader_found_none_and_no_part_of_the_file_holds_any(tmp_path):
    """Decision 9. "has text": the app's own reader read words (the body's
    paragraphs and tables, the headers, the footers, the notes). A letter
    whose words sit where that reader does not read (a text box, a content
    control, a part nothing points to, another prefix) is NOT "no text
    found": it is "not checked". "Found no text" needs both: the reader
    read none, and no part of the package holds a run of text."""
    import zipfile

    import docx

    letterhead = _docx(tmp_path / "letterhead.docx", header_text="ACME LTD")
    table = tmp_path / "table.docx"
    d = docx.Document()
    d.add_table(rows=1, cols=1).cell(0, 0).text = "Rent"
    d.save(str(table))
    for path in (letterhead, table):
        assert _by(content.check_files([str(path)]), DOC_TEXT) == [
            Check(DOC_TEXT, PASSED, "has text", file=path.name)], path.name
    control = _docx(tmp_path / "control.docx", (
        f"<w:sdt {_W}><w:sdtContent><w:p><w:r><w:t>Dear Ms Lee, thank you.</w:t></w:r></w:p></w:sdtContent></w:sdt>"))
    box = _docx(tmp_path / "box.docx", (
        f"<w:p {_W}><w:r><w:pict><v:textbox><w:txbxContent><w:p><w:r><w:t>Words in a box</w:t></w:r></w:p>"
        "</w:txbxContent></v:textbox></w:pict></w:r></w:p>"))
    # Words in a part the document does not point to (the reviewer's file), and under another prefix.
    stray = tmp_path / "stray.docx"
    docx.Document().save(str(stray))
    with zipfile.ZipFile(stray, "a") as z:
        z.writestr("word/footnotes.xml", '<x:footnotes xmlns:x="urn:x"><x:t>See the 2023 audit.</x:t></x:footnotes>')
    for path in (control, box, stray):
        got = content.check_files([str(path)])
        assert got == [Check(FILE_OPENS, PASSED, "opens", file=path.name),
                       Check(DOC_TEXT, NOT_CHECKED, content.TEXT_NOT_READ, file=path.name)], path.name
        line, tone = line_for(_rows(got))
        assert tone == "quiet" and "found no text" not in line and "Worth a look" not in line, line
    # Blank runs, a tab and a line break are not text: no part holds any, so "found no text" is true.
    blank = _docx(tmp_path / "blank.docx", (
        f"<w:sdt {_W}><w:sdtContent><w:p><w:r><w:t xml:space='preserve'>   </w:t><w:tab/></w:r></w:p>"
        "</w:sdtContent></w:sdt>"))
    assert _by(content.check_files([str(blank)]), DOC_TEXT) == [
        Check(DOC_TEXT, LOOK, content.NO_TEXT, file="blank.docx")]
    # A title in the document's properties is not text of the document.
    titled = tmp_path / "titled.docx"
    d = docx.Document()
    d.core_properties.title = "Quarterly report"
    d.save(str(titled))
    assert _by(content.check_files([str(titled)]), DOC_TEXT) == [Check(DOC_TEXT, LOOK, content.NO_TEXT, file="titled.docx")]
    # A package that cannot be looked through, or a deadline that ran out: never "found no text".
    assert content._package_holds(tmp_path / "gone.docx", content._WORD_PART_RX, content._TEXT_RUN_RX) is None
    assert content._package_holds(blank, content._WORD_PART_RX, content._TEXT_RUN_RX, lambda: True) is None
    assert content._package_holds(blank, content._WORD_PART_RX, content._TEXT_RUN_RX) is False
    assert content._package_holds(box, content._WORD_PART_RX, content._TEXT_RUN_RX) is True
    late = content.check_one(str(blank), may_read=lambda _p: True, late=lambda: True)
    assert [(c.check, c.result, c.detail) for c in late] == [
        (FILE_OPENS, PASSED, "opens"), (DOC_TEXT, NOT_CHECKED, content.TEXT_NOT_READ)]


def test_a_word_file_is_opened_by_the_apps_own_reader_never_judged_by_the_names_of_its_parts(tmp_path):
    """Decision 2. Word Online saves the main part as ``word/document2.xml``.
    That is a valid file (the package says where its main part is): it read
    "Problem: a part of the file is missing"."""
    import docx

    plain = tmp_path / "plain.docx"
    d = docx.Document()
    d.add_paragraph("The meeting is on Monday.")
    d.save(str(plain))

    def change(name: str, data: bytes) -> bytes:
        if name in ("[Content_Types].xml", "_rels/.rels"):
            return data.replace(b"word/document.xml", b"word/document2.xml")
        return data

    import zipfile

    online = tmp_path / "letter.docx"
    with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(online, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name = {"word/document.xml": "word/document2.xml",
                    "word/_rels/document.xml.rels": "word/_rels/document2.xml.rels"}.get(item.filename, item.filename)
            zout.writestr(name, change(item.filename, zin.read(item.filename)))
    with zipfile.ZipFile(online) as z:
        assert "word/document.xml" not in z.namelist()
    assert content.check_files([str(online)]) == [
        Check(FILE_OPENS, PASSED, "opens", file="letter.docx"), Check(DOC_TEXT, PASSED, "has text", file="letter.docx")]


def test_a_workbook_is_opened_by_the_apps_own_reader_never_judged_by_the_names_of_its_parts(tmp_path):
    plain = _xlsx(tmp_path / "plain.xlsx", [*BUDGET, ["Total", 1650.5]])
    import zipfile

    other = tmp_path / "book.xlsx"
    with zipfile.ZipFile(plain) as zin, zipfile.ZipFile(other, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in ("[Content_Types].xml", "_rels/.rels"):
                data = data.replace(b"xl/workbook.xml", b"xl/workbook2.xml")
            name = {"xl/workbook.xml": "xl/workbook2.xml",
                    "xl/_rels/workbook.xml.rels": "xl/_rels/workbook2.xml.rels"}.get(item.filename, item.filename)
            zout.writestr(name, data)
    with zipfile.ZipFile(other) as z:
        assert "xl/workbook.xml" not in z.namelist()
    got = content.check_files([str(other)])
    assert [(c.check, c.result, c.detail) for c in got] == [
        (FILE_OPENS, PASSED, "opens"), (SHEET_ERRORS, PASSED, "no error cells"), (SHEET_TOTALS, PASSED, "1 total adds up")]
    # ... and whether it asks to be worked out again is read from its workbook part, whatever it is called.
    assert content._recalculates(other) is True


def test_a_workbook_whose_cells_the_reader_does_not_see_is_not_called_empty(tmp_path):
    """Decision 9. A Strict Open XML workbook (Excel: Save As) opens, and the
    reader finds no cell in it: it read "it has no filled cells"."""
    plain = _xlsx(tmp_path / "plain.xlsx", [*BUDGET, ["Total", 1650.5]])

    def strict(_name: str, data: bytes) -> bytes:
        return data.replace(b"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                            b"http://purl.oclc.org/ooxml/spreadsheetml/main")

    odd = _repack(plain, tmp_path / "strict.xlsx", strict)
    assert not any(content._load(odd).formulas.values()), "the reader reads Strict Open XML now: read the test again"
    got = content.check_files([str(odd)])
    assert got == [Check(FILE_OPENS, PASSED, "opens", file="strict.xlsx"),
                   Check(DOC_TEXT, NOT_CHECKED, content.CELLS_NOT_READ, file="strict.xlsx")]
    assert "no filled cells" not in line_for(_rows(got))[0]
    # A workbook that really holds no cell is still worth a look.
    bare = tmp_path / "bare.xlsx"
    Workbook().save(bare)
    assert content._package_holds(bare, content._SHEET_PART_RX, content._CELL_RX) is False
    assert content._package_holds(odd, content._SHEET_PART_RX, content._CELL_RX) is True
    assert _by(content.check_files([str(bare)]), DOC_TEXT) == [Check(DOC_TEXT, LOOK, content.NO_CELLS, file="bare.xlsx")]


def test_an_error_cell_is_a_cell_whose_type_is_an_error_never_text_that_reads_like_one(tmp_path):
    """Decision 8. " #REF! " typed with spaces round it, and "#N/A" kept as
    text (an inline string), are words. Only a cell whose TYPE is an error
    is one."""
    p = _xlsx(tmp_path / "t.xlsx", [["Note"], ["If you see #DIV/0! call me"], [" #REF! "], ["#N/A"], [5]])
    assert content._load(p).errors["Summary"] == {(4, 1): "#N/A"}, "openpyxl saves a bare error word as an error cell"
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, FAILED, "A4 holds #N/A", file="t.xlsx")]

    def as_text(name: str, data: bytes) -> bytes:
        if name != "xl/worksheets/sheet1.xml":
            return data
        out = re.sub(rb'<c r="A4"[^>]*>.*?</c>', b'<c r="A4" t="inlineStr"><is><t>#N/A</t></is></c>', data, flags=re.S)
        assert out != data
        return out

    text = _repack(p, tmp_path / "text.xlsx", as_text)
    book = content._load(text)
    assert book.formulas["Summary"][(4, 1)] == "#N/A" and book.errors["Summary"] == {}
    assert _by(content.check_files([str(text)]), SHEET_ERRORS) == [
        Check(SHEET_ERRORS, PASSED, "no error cells", file="text.xlsx")]
    # The slower read tells the two apart the same way.
    from openpyxl import load_workbook

    for path, want in ((p, {(4, 1): "#N/A"}), (text, {})):
        wb = load_workbook(str(path), read_only=True)
        try:
            assert content._read_twice(wb, path, content._never_late).errors["Summary"] == want
        finally:
            wb.close()


def test_a_text_document_of_blank_space_is_worth_a_look(tmp_path):
    blank = tmp_path / "notes.md"
    blank.write_text("   \n\n\t\n", encoding="utf-8")
    assert content.check_files([str(blank)]) == [
        Check(FILE_OPENS, PASSED, "opens", file="notes.md"),
        Check(DOC_TEXT, LOOK, "Iron Jarvis found no text in it", file="notes.md"),
    ]
    words = tmp_path / "words.txt"
    words.write_text("hello", encoding="utf-8")
    assert _by(content.check_files([str(words)]), DOC_TEXT) == [Check(DOC_TEXT, PASSED, "has text", file="words.txt")]
    table = tmp_path / "empty.csv"
    table.write_text("\n", encoding="utf-8")
    assert _by(content.check_files([str(table)]), DOC_TEXT) == [Check(DOC_TEXT, LOOK, content.NO_TEXT, file="empty.csv")]
    # A table of text that holds only its separators has no text (it read "has text").
    for name, data in {"commas.csv": ",,,\r\n,,,\r\n", "quotes.csv": '"","",""\n;;\n', "tabs.tsv": "\t\t\n\t\t\n",
                       "pipes.csv": "||\n||\n"}.items():
        path = tmp_path / name
        path.write_text(data, encoding="utf-8")
        assert _by(content.check_files([str(path)]), DOC_TEXT) == [Check(DOC_TEXT, LOOK, content.NO_TEXT, file=name)], name
    one = tmp_path / "one.csv"
    one.write_text(",,x,\n", encoding="utf-8")
    assert _by(content.check_files([str(one)]), DOC_TEXT) == [Check(DOC_TEXT, PASSED, "has text", file="one.csv")]
    # Only a TABLE's separators are nothing: a note that holds commas has text.
    note = tmp_path / "note.txt"
    note.write_text(",,,\n", encoding="utf-8")
    assert _by(content.check_files([str(note)]), DOC_TEXT) == [Check(DOC_TEXT, PASSED, "has text", file="note.txt")]


def test_an_empty_document_is_worth_a_look_and_an_empty_file_of_another_kind_says_nothing(tmp_path):
    """ "It is empty" was a Problem. An empty ``notes.txt`` or
    ``requirements.txt`` may be exactly what was asked for: worth a look,
    never a problem. An empty or blank file that is NOT a document
    (``__init__.py`` with one line break, a settings file, a log, a style
    sheet) is what it was meant to be: no row at all, so no line."""
    quiet = {"__init__.py": b"", "pkg__init__.py": b"\n", ".gitkeep": b"\n", "placeholder.json": b"", "styles.css": b"\n",
             "settings.ini": b"\r\n", "notes.log": b"\n\n", "blank.htm": b"  \n ", "notes": b"", "query.sql": b" "}
    for name, data in quiet.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert content.check_files([str(path)]) == [], name
    assert line_for(_rows(content.check_files([str(tmp_path / n) for n in list(quiet)[:6]]))) == ("", "quiet")
    for name in ("empty.xlsx", "empty.docx", "empty.pdf", "empty.txt", "empty.md", "empty.csv", "requirements.txt"):
        path = tmp_path / name
        path.write_bytes(b"")
        got = content.check_files([str(path)])
        assert got == [Check(DOC_TEXT, LOOK, "it is empty", file=name)], name
        line, tone = line_for(_rows(got))
        assert line == f"Worth a look: “{name}” is empty." and tone == "warn"
    # A workbook with no filled cell is an empty document too.
    bare = tmp_path / "bare.xlsx"
    Workbook().save(bare)
    assert content.check_files([str(bare)]) == [
        Check(FILE_OPENS, PASSED, "opens", file="bare.xlsx"), Check(DOC_TEXT, LOOK, "it has no filled cells", file="bare.xlsx")]
    # Code and a web page that hold something are read ("opens" is true of them), and never asked for words.
    for name, text in {"tool.py": "x = 1\n", "page.html": "<html><body><img src='a.png'></body></html>",
                       "data.jsonl": '{"a": 1}\n{"a": 2}\n'}.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        assert content.check_files([str(path)]) == [Check(FILE_OPENS, PASSED, "opens", file=name)], name


OPENED_BY_NOTHING = {
    "books.xls": b"Item\tAmount\r\nRent\t1200\r\n", "letter.doc": b"this is not a Word file at all",
    "photo.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "photo.jpg": b"\xff\xd8garbage", "deck.pptx": b"PK\x03\x04junk",
    "old.ods": b"x", "old.odt": b"x", "letter.rtf": b"{\\rtf1 hello}", "archive.zip": b"PK", "noext": b"words",
    "movie.mp4": b"\x00\x00", "data.parquet": b"PAR1",
}


@pytest.mark.parametrize("name", sorted(OPENED_BY_NOTHING))
def test_a_kind_nothing_here_opens_gets_no_row_and_is_never_said_to_open(tmp_path, name):
    """ "the spreadsheet opens" was said of a tab-separated text file named
    .xls, "the picture opens" of eight bytes, "the slides opens" of a broken
    deck: nothing had opened them. A kind with no reader here gets no row at
    all: not "opens", not "not checked"."""
    path = tmp_path / name
    path.write_bytes(OPENED_BY_NOTHING[name])
    assert content.opened_kind(path) is False
    assert content.check_one(str(path), may_read=lambda _p: True) == []
    assert content.check_files([str(path)]) == []
    # ... not when the deadline has run out, not past the cap of files, and not outside the folders it may read.
    assert content.check_files([str(path)], clock=iter([0.0, 99.0, 99.0, 99.0]).__next__, deadline_s=1.0) == []
    assert content.check_one(str(path), may_read=lambda _p: False) == []
    many = [str(path)] * 1 + [str(tmp_path / f"n{i}{path.suffix or '.bin'}") for i in range(8)]
    assert content.check_files(many) == []


def test_opens_is_said_only_of_what_was_really_opened(tmp_path):
    assert content.OPENED_EXTS >= {".xlsx", ".xlsm", ".docx", ".pdf", ".txt", ".md", ".csv", ".json", ".py"}
    assert not content.OPENED_EXTS & {".xls", ".doc", ".pptx", ".png", ".jpg", ".ods", ".odt", ".rtf", ".zip", ""}
    assert {R.kind_words(f"a{ext}") for ext in (".pptx", ".png", ".xls", ".doc", "")} == {"the file"}
    # Every kind's own name is singular: "<kind> opens" reads right.
    assert {R.kind_words(f"a{ext}") for ext in content.OPENED_EXTS} == {
        "the spreadsheet", "the document", "the table", "the PDF", "the file"}


def _zip(path: Path, parts: dict) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return path


def test_a_file_reads_problem_exactly_when_the_apps_own_reader_could_not_open_it(tmp_path):
    """Decision 2. A PROBLEM is the reader's own failure, said as that fact
    and nothing more: "Iron Jarvis could not open “x.xlsx”". No reason is
    given (a reason is a guess about the file), and no part of a package is
    looked for by its name."""
    broken = {
        "notzip.xlsx": b"this is not a workbook", "notzip.docx": b"this is not a document",
        "notpdf.pdf": b"just some text\n%%EOF\n", "cut.pdf": b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\n",
        "odd.pdf": b"%PDF-1.7\nthis is not a pdf body\n%%EOF\n",
    }
    made = []
    for name, data in broken.items():
        path = tmp_path / name
        path.write_bytes(data)
        made.append(path)
    made += [_zip(tmp_path / "nobook.xlsx", {"docProps/app.xml": "<x/>"}),
             _zip(tmp_path / "nodoc.docx", {"docProps/app.xml": "<x/>"}),
             _zip(tmp_path / "oddbook.xlsx", {"xl/workbook.xml": "<not-xml", "[Content_Types].xml": "<Types/>"}),
             _zip(tmp_path / "odddoc.docx", {"word/document.xml": "<not-xml", "[Content_Types].xml": "<Types/>"})]
    for path in made:
        got = content.check_files([str(path)])
        assert got == [Check(FILE_OPENS, FAILED, f"Iron Jarvis could not open “{path.name}”", file=path.name)], path.name
        line, tone = line_for(_rows(got))
        kind = R.kind_words(path.name)
        assert line == f"Checked by Iron Jarvis: a problem in {kind}: Iron Jarvis could not open “{path.name}”."
        assert tone == "warn" and "opens" not in line and "does not open" not in line
    assert content.could_not_open("C:/clients/acme/letter.docx") == "Iron Jarvis could not open “letter.docx”"
    assert content.could_not_open("") == "Iron Jarvis could not open it"


def test_a_workbook_the_reader_cannot_open_says_only_that(tmp_path):
    """A workbook with a chart sheet and no chart is a file Excel may open
    and the app's reader does not: the app's own tools cannot read it back
    either. The line says that fact, and never why."""
    wb = Workbook()
    wb.active.title = "Data"
    wb.active.append(["a", 1])
    wb.create_chartsheet("Chart")
    p = tmp_path / "chart.xlsx"
    wb.save(p)
    got = content.check_files([str(p)])
    assert got == [Check(FILE_OPENS, FAILED, "Iron Jarvis could not open “chart.xlsx”", file="chart.xlsx")] or (
        got[0].detail == "opens"), got


def test_the_disks_refusal_is_not_the_readers_failure(tmp_path, monkeypatch):
    """A file the disk will not hand over (another program holds it, a
    permission) was not opened, and that is not a fact about the file: not
    checked, never a problem."""
    import docx
    import openpyxl
    import pypdf

    def refused(*_a, **_kw):
        raise PermissionError(13, "the file is in use")

    assert content._disk_refused(PermissionError(13, "in use")) and content._disk_refused(FileNotFoundError(2, "gone"))
    # A reader that says "this is no workbook" with an error of the same class, and no number, is not the disk.
    assert not content._disk_refused(OSError("File contains no valid workbook part"))
    assert not content._disk_refused(ValueError("x"))

    book = _xlsx(tmp_path / "t.xlsx", BUDGET)
    monkeypatch.setattr(openpyxl, "load_workbook", refused)
    assert content.check_files([str(book)]) == [Check(FILE_OPENS, NOT_CHECKED, content.NOT_READ, file="t.xlsx")]
    letter = tmp_path / "t.docx"
    docx.Document().save(str(letter))
    monkeypatch.setattr(docx, "Document", refused)
    assert content.check_files([str(letter)]) == [Check(FILE_OPENS, NOT_CHECKED, content.NOT_READ, file="t.docx")]
    pdf = _pdf(tmp_path / "t.pdf", 1)
    monkeypatch.setattr(pypdf, "PdfReader", refused)
    assert content.check_files([str(pdf)]) == [Check(FILE_OPENS, NOT_CHECKED, content.NOT_READ, file="t.pdf")]


def test_a_word_file_that_opens_and_whose_text_cannot_be_read_is_not_called_broken(tmp_path, monkeypatch):
    import docx

    from iron_jarvis.documents import readers

    letter = tmp_path / "t.docx"
    d = docx.Document()
    d.add_paragraph("words")
    d.save(str(letter))

    def trips(_p, **_kw):
        raise RuntimeError("a table it cannot walk")

    monkeypatch.setattr(readers, "_read_docx", trips)
    assert content.check_files([str(letter)]) == [
        Check(FILE_OPENS, PASSED, "opens", file="t.docx"), Check(DOC_TEXT, NOT_CHECKED, content.TEXT_NOT_READ, file="t.docx")]


def test_a_file_protected_with_a_password_is_not_checked_never_a_problem(tmp_path):
    """Decision 2. A .docx or .xlsx protected with a password is not a
    package: it is the older Office container. It read "Problem: it is not a
    real Office file". Said for certain only when the container names its
    encrypted part; any other file of that container MAY be one."""
    head = bytes.fromhex("D0CF11E0A1B11AE1")
    for name in ("locked.docx", "locked.xlsx", "locked.xlsm"):
        path = tmp_path / name
        path.write_bytes(head + b"\x00" * 512 + "EncryptedPackage".encode("utf-16-le") + b"\x00" * 512)
        assert content.check_files([str(path)]) == [
            Check(FILE_OPENS, NOT_CHECKED, "it is protected with a password", file=name)], name
        other = tmp_path / f"older-{name}"
        other.write_bytes(head + b"\x00" * 4096)
        got = content.check_files([str(other)])
        assert got == [Check(FILE_OPENS, NOT_CHECKED, content.MAY_BE_PROTECTED, file=other.name)], name
        assert line_for(_rows(got))[1] == "quiet"
    assert content.MAY_BE_PROTECTED == "it may be protected with a password, or be an older kind of Office file"
    assert content._older_container(_xlsx(tmp_path / "plain.xlsx", BUDGET)) == ""


def test_text_in_an_older_code_page_is_read_and_a_file_that_is_not_text_is_not_checked(tmp_path):
    """A .txt, .csv or .sql file saved by an older Windows program is not
    UTF-8. It read "Problem: its text could not be read". The ONE decoder
    the document readers use reads it."""
    for name, text, enc in (("cafe.txt", "Café menu: crème brûlée £4.50\r\n", "cp1252"),
                            ("prices.csv", "Item,Price\r\nCrème,£4.50\r\n", "cp1252"),
                            ("script.sql", "SELECT 'Zoë' AS name;\n", "latin-1"),
                            ("bom.txt", "Hello there.\r\n", "utf-16")):
        path = tmp_path / name
        path.write_bytes(text.encode(enc))
        got = content.check_files([str(path)])
        assert got[0] == Check(FILE_OPENS, PASSED, "opens", file=name), name
        assert not [c for c in got if c.result in (FAILED, LOOK, NOT_CHECKED)], name
    assert content._decoded("Café".encode("cp1252")) == "Café"
    binary = tmp_path / "blob.txt"
    binary.write_bytes(b"\x00\x01\x02\x03 words \x00\x00")
    assert content.check_files([str(binary)]) == [
        Check(FILE_OPENS, NOT_CHECKED, "its text could not be read here", file="blob.txt")]


def test_a_large_valid_file_is_read_whole_before_it_is_judged(tmp_path, monkeypatch):
    """A valid 6.6 MB JSON read "Problem: it does not open" and a valid 5 MB
    CSV "its text could not be read": the first 4 MB were judged (a cut
    piece: half a value, half a character). The WHOLE file is read."""
    import json

    data = json.dumps([{"id": i, "name": f"customer {i}", "amount": i * 1.5} for i in range(110_000)]).encode("utf-8")
    assert 4 * 1024 * 1024 < len(data) < content.MAX_BYTES
    big = tmp_path / "big.json"
    big.write_bytes(data)
    assert content.check_files([str(big)]) == [Check(FILE_OPENS, PASSED, "opens", file="big.json")]
    line = "Zoë Müller;Crème brûlée;4,50 €\r\n".encode("utf-8")
    blob = line * (5 * 1024 * 1024 // len(line))
    pad = next(n for n in range(0, 4) if _cut_mid_character((b"x" * n + blob)[:4 * 1024 * 1024]))
    table = tmp_path / "big.csv"
    table.write_bytes(b"x" * pad + blob)
    assert content.check_files([str(table)]) == [
        Check(FILE_OPENS, PASSED, "opens", file="big.csv"), Check(DOC_TEXT, PASSED, "has text", file="big.csv")]
    # Over the cap: not opened at all, and it says so.
    monkeypatch.setattr(content, "MAX_BYTES", 1024)
    (only,) = content.check_files([str(big)])
    assert only.result == NOT_CHECKED and only.detail.startswith("it is too large to check here")


def _cut_mid_character(piece: bytes) -> bool:
    try:
        piece.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


@pytest.mark.parametrize("name,text", [
    ("tsconfig.json", '{\n  // comments are allowed in tsconfig\n  "compilerOptions": {"strict": true},\n}\n'),
    ("lines.json", '{"a": 1}\n{"a": 2}\n'),
    ("half.json", '{"a": [1, 2'),
])
def test_a_json_file_iron_jarvis_cannot_parse_is_not_checked_never_a_problem(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    got = content.check_files([str(path)])
    assert got == [Check(FILE_OPENS, NOT_CHECKED, "it could not be read as JSON here", file=name)]
    assert line_for(_rows(got)) == ("Not checked: the file (it could not be read as JSON here).", "quiet")
    good = tmp_path / "good.json"
    good.write_text('{"a": [1, 2]}', encoding="utf-8")
    assert content.check_files([str(good)]) == [Check(FILE_OPENS, PASSED, "opens", file="good.json")]


def _pdf(path: Path, pages: int, text: str = "") -> Path:
    """A real PDF of *pages* pages: blank ones, the first with *text* (made
    by the app's own writer) when given."""
    import io

    from pypdf import PdfReader, PdfWriter

    w = PdfWriter()
    if text:
        first = path.with_name(f"first-{path.name}")
        write_document(first, text)
        w.add_page(PdfReader(io.BytesIO(first.read_bytes())).pages[0])
        first.unlink()
    for _ in range(pages - (1 if text else 0)):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def test_a_pdfs_pages_are_said_as_a_fact_and_nothing_the_reply_says_is_compared(tmp_path):
    """The page count read out of the reply is GONE: 17 of 26 truthful
    sentences read "Worth a look". The PDF check says what Iron Jarvis did:
    it opened the file and counted its pages."""
    p = _pdf(tmp_path / "report.pdf", 12, "Quarterly report")
    for reply in ("", "report.pdf has 40 pages.", "Saved report.pdf (3 pages).", "Here is the 40-page report.pdf.",
                  "The cover letter for report.pdf is 1 page.", "report.pdf is 5 pages, double sided."):
        got = content.check_files([str(p)], reply)
        assert got == [Check(FILE_OPENS, PASSED, "opens", file="report.pdf"),
                       Check(PDF_PAGES, PASSED, "12 pages", file="report.pdf"),
                       Check(DOC_TEXT, PASSED, "has text", file="report.pdf")], reply
        assert line_for(_rows(got)) == ("Checked by Iron Jarvis: the PDF opens, 12 pages, has text.", "quiet")
    assert not hasattr(content, "stated_pages")
    one = _pdf(tmp_path / "one.pdf", 1, "x")
    assert _by(content.check_files([str(one)]), PDF_PAGES) == [Check(PDF_PAGES, PASSED, "1 page", file="one.pdf")]


def test_a_pdf_in_which_no_text_was_found_is_worth_a_look_and_one_read_in_part_says_nothing(tmp_path, monkeypatch):
    blank = _pdf(tmp_path / "scan.pdf", 3)
    got = content.check_files([str(blank)])
    assert got == [Check(FILE_OPENS, PASSED, "opens", file="scan.pdf"), Check(PDF_PAGES, PASSED, "3 pages", file="scan.pdf"),
                   Check(DOC_TEXT, LOOK, "Iron Jarvis found no text in it", file="scan.pdf")]
    assert line_for(_rows(got)) == (
        "Checked by Iron Jarvis: the PDF opens, 3 pages. Worth a look: Iron Jarvis found no text in it.", "warn")
    # Only its first pages are read for text: with pages left unread, nothing is said about its text.
    monkeypatch.setattr(content, "MAX_PDF_TEXT_PAGES", 2)
    assert _by(content.check_files([str(blank)]), DOC_TEXT) == []
    # ... and the same when the reply's time runs out among the pages.
    monkeypatch.setattr(content, "MAX_PDF_TEXT_PAGES", 25)
    ticks = iter([0.0, 0.0, 0.0, 99.0, 99.0, 99.0, 99.0])
    late = content.check_files([str(blank)], clock=lambda: next(ticks, 99.0), deadline_s=1.0)
    assert [c.check for c in late] == [FILE_OPENS, PDF_PAGES]


def test_a_locked_pdf_is_not_checked_and_is_not_said_to_open(tmp_path):
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("a-password-for-this-test")
    p = tmp_path / "locked.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    assert content.check_files([str(p)]) == [
        Check(FILE_OPENS, NOT_CHECKED, "it is protected with a password", file="locked.pdf")]
    # A PDF that only limits what may be done with it opens with no password (the app's own reader tries
    # the empty one): it is opened, and its pages are counted.
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("", owner_password="an-owner-password-for-this-test")
    free = tmp_path / "limited.pdf"
    with open(free, "wb") as fh:
        w.write(fh)
    assert [(c.check, c.result, c.detail) for c in content.check_files([str(free)])][:2] == [
        (FILE_OPENS, PASSED, "opens"), (PDF_PAGES, PASSED, "1 page")]


# --------------------------------------------------------------------------- #
# Never raises, bounded, off the loop, only readable files
# --------------------------------------------------------------------------- #
def test_a_check_that_raises_is_not_checked_never_passed(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1650.5]])

    def broken(_path, _late=None):
        raise RuntimeError("the reader broke")

    monkeypatch.setattr(content, "_load", broken)
    got = content.check_files([str(p)])
    # A check that breaks says so, and nothing else: not a pass, and not "opens" either.
    assert got == [Check(FILE_OPENS, NOT_CHECKED, content.BROKE, file="t.xlsx")]
    assert line_for(_rows(got))[0] == "Not checked: the spreadsheet (it could not be checked here)."

    def one_broken(_path, _reply="", **_kw):
        raise ValueError("no")

    assert content.check_files([str(p)], one=one_broken) == [Check(FILE_OPENS, NOT_CHECKED, content.BROKE, file="t.xlsx")]


def test_an_evaluator_that_breaks_is_not_checked_never_no_error_cells(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [["k", 1], ["f", "=B1*2"]])

    def no_grid(_book, _late):
        raise RuntimeError("the evaluator broke")

    monkeypatch.setattr(content, "_grid", no_grid)
    assert _by(content.check_files([str(p)]), SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "1 formula could not be worked out here", file="t.xlsx")]


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
    """A sheet may declare any size ("A1:XFD1048576"), and two cells of one
    row may sit sixteen thousand columns apart. The cells that are really in
    the file are read, and nothing is stepped over for the gaps."""
    small = _xlsx(tmp_path / "small.xlsx", [*BUDGET, ["Total", 1650.5]])
    huge = _declares(small, tmp_path / "huge.xlsx", b"A1:XFD1048576")
    assert [(c.check, c.result, c.detail) for c in content.check_files([str(huge)])] == [
        (FILE_OPENS, PASSED, "opens"), (SHEET_ERRORS, PASSED, "no error cells"),
        (SHEET_TOTALS, PASSED, "1 total adds up"),
    ]
    wide = Workbook()
    wide.active.append(["Total", 5])
    wide.active["XFD1"] = 5
    wide.save(tmp_path / "wide.xlsx")
    book = content._load(tmp_path / "wide.xlsx")
    assert book.formulas["Sheet"] == {(1, 1): "Total", (1, 2): 5, (1, 16384): 5} and not book.cut


def test_a_workbook_is_loaded_once_and_that_one_load_answers_whether_it_opens(tmp_path, monkeypatch):
    """The workbook was opened three times (the "does it open" look, the
    cells as written, the saved results). ONE load does all three."""
    import openpyxl

    from iron_jarvis.deliverables import check as deliverables_check

    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", "=SUM(B2:B4)"], ["Share", "=B2/B5"]])
    loads: list[dict] = []
    real = openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **kw: (loads.append(kw), real(*a, **kw))[1])
    other: list[str] = []
    real_check = deliverables_check.check_file
    monkeypatch.setattr(deliverables_check, "check_file", lambda q: (other.append(str(q)), real_check(q))[1])
    got = content.check_files([str(p)])
    assert [(c.check, c.result, c.detail) for c in got] == [
        (FILE_OPENS, PASSED, "opens"), (SHEET_ERRORS, PASSED, "its 2 formulas work out"),
        (SHEET_TOTALS, PASSED, "1 total adds up")]
    assert len(loads) == 1 and loads[0].get("read_only") is True, f"the workbook was loaded {len(loads)} times"
    assert other == [], "the older open check loaded the workbook again"


def test_the_slower_read_gives_the_same_book_when_the_one_pass_read_is_not_there(tmp_path, monkeypatch):
    """The one-pass read leans on the library's own cell parser. If a later
    version moves it, the same book is read the slower way: never a wrong
    answer, never a crash."""
    p = _saved_error_file(tmp_path)
    q = _xlsx(tmp_path / "q.xlsx", [["Item", "Amount", "Rate"], ["a", 1, 0.5], ["b", " ", "=B2*2"], ["Total", 1, None]])
    for cell in ("C2",):
        wb = __import__("openpyxl").load_workbook(q)
        wb.active[cell].number_format = "0%"
        wb.save(q)
    once = [content._load(p), content._load(q)]

    def gone(_wb, _late, _trusted=True):
        raise ImportError("the library moved its parser")

    monkeypatch.setattr(content, "_read_once", gone)
    twice = [content._load(p), content._load(q)]
    for a, b in zip(once, twice):
        assert (a.formulas, a.values, a.errors, a.percent, a.spaces, a.sheets, a.cut) == (
            b.formulas, b.values, b.errors, b.percent, b.spaces, b.sheets, b.cut)
    assert once[0].errors == {"S": {(1, 2): "#DIV/0!"}}
    assert once[1].percent == {("Summary", 2, 3)} and once[1].spaces == {("Summary", 3, 2)}
    assert [(c.check, c.result) for c in content.check_files([str(p)])] == [
        (FILE_OPENS, PASSED), (SHEET_ERRORS, FAILED)]


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
    # Late from the moment the evaluator is ready: no formula is worked out, and none is passed.
    state = {"late": False}
    real_grid = content._grid

    def grid_then_late(book, late):
        grid = real_grid(book, late)
        state["late"] = True
        return grid

    monkeypatch.setattr(content, "_grid", grid_then_late)
    clock = lambda: 99.0 if state["late"] else 0.0   # noqa: E731
    got = content.check_files([str(p)], clock=clock, deadline_s=4.0)
    assert _by(got, SHEET_ERRORS) == [Check(
        SHEET_ERRORS, NOT_CHECKED, "3 formulas could not be worked out here", file="t.xlsx")]
    assert _by(got, SHEET_TOTALS) == [Check(SHEET_TOTALS, NOT_CHECKED, content.OUT_OF_TIME, file="t.xlsx")]


def test_a_deadline_that_runs_out_while_the_formulas_are_looked_at_never_reads_no_error_cells(tmp_path, monkeypatch):
    """Every formula is looked at for a function Excel may not know, saved
    result or not. Out of time there, the check says so: it is not "no error
    cells" (nothing was looked at), and what the file HOLDS is still said."""
    p = _saved_error_file(tmp_path)                     # two formulas, both with a saved result
    state = {"late": False}
    real_load = content._load

    def load_then_late(path, late=content._never_late):
        book = real_load(path, late)
        state["late"] = True
        return book

    monkeypatch.setattr(content, "_load", load_then_late)
    got = content.check_files([str(p)], clock=lambda: 99.0 if state["late"] else 0.0, deadline_s=4.0)
    assert [(c.result, c.detail) for c in _by(got, SHEET_ERRORS)] == [
        (FAILED, "B1 holds #DIV/0!"), (NOT_CHECKED, content.OUT_OF_TIME)]
    clean = _xlsx(tmp_path / "clean.xlsx", [["a", 5], ["t", "=B1*2"]])
    state["late"] = False
    got = content.check_files([str(clean)], clock=lambda: 99.0 if state["late"] else 0.0, deadline_s=4.0)
    assert _by(got, SHEET_ERRORS) == [Check(SHEET_ERRORS, NOT_CHECKED, content.OUT_OF_TIME, file="clean.xlsx")]


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
    assert got[-1] == Check(FILE_OPENS, NOT_CHECKED, "2 more files were not checked: only the first 2 are")
    assert line_for(_rows(got))[0].endswith(
        "Not checked: the other files (2 more files were not checked: only the first 2 are).")


def test_two_files_of_the_same_name_in_two_folders_are_told_apart_by_the_folders_name(tmp_path):
    """N2: both were "summary.xlsx", so the line said one file's checks
    twice under one name. The folder's own name is kept in its OWN field and
    shown beside the name; never a path."""
    (tmp_path / "2023").mkdir()
    (tmp_path / "2024").mkdir()
    old = _xlsx(tmp_path / "2023" / "summary.xlsx", [*BUDGET, ["Total", 1650.5]])
    new = _xlsx(tmp_path / "2024" / "summary.xlsx", [*BUDGET, ["Total", 1700]])
    alone = _xlsx(tmp_path / "other.xlsx", BUDGET)
    got = content.check_files([str(old), str(new), str(alone)])
    assert list(dict.fromkeys((c.file, c.folder) for c in got)) == [
        ("summary.xlsx", "2023"), ("summary.xlsx", "2024"), ("other.xlsx", "")]
    assert not [c for c in got if "/" in c.file + c.folder or "\\" in c.file + c.folder], "a path reached the record"
    line, tone = line_for(_rows(got))
    assert line == (
        "Checked by Iron Jarvis: “summary.xlsx” (in “2023”) opens, no error cells, 1 total adds up; "
        "“summary.xlsx” (in “2024”) opens, no error cells; “other.xlsx” opens, no error cells. "
        "Worth a look: “summary.xlsx” (in “2024”): B5 says 1,700 and the cells above it add up to 1,650.50."
    ), line
    # The two fields survive the record's own cleaning, and the name alone says what kind of file it is.
    kept = R._clean(got[0])
    assert (kept.file, kept.folder) == ("summary.xlsx", "2023") and R.kind_words(kept.file) == "the spreadsheet"
    # Same name AND same folder name, further up: still two names.
    (tmp_path / "a" / "x").mkdir(parents=True)
    (tmp_path / "b" / "x").mkdir(parents=True)
    twins = [str(_xlsx(tmp_path / side / "x" / "s.xlsx", BUDGET)) for side in ("a", "b")]
    assert list(content.names_for(twins).values()) == [("s.xlsx", "x"), ("s.xlsx", "another folder, 2")]
    # One file, or files with different names: the bare name, no folder.
    assert list(content.names_for([str(alone)]).values()) == [("other.xlsx", "")]


def test_a_file_whose_own_name_holds_in_brackets_keeps_its_name_and_its_kind(tmp_path):
    """A file really called "Budget (in USD).xlsx" was read as a file told
    apart by a folder called USD: it lost its kind ("the file") and was
    listed on a card as not covered."""
    from iron_jarvis.checks import turn as T

    p = _xlsx(tmp_path / "Budget (in USD).xlsx", [["Item", "Amount"], ["a", 1], ["b", 2], ["Total", 3]])
    got = content.check_files([str(p)])
    assert {(c.file, c.folder) for c in got} == {("Budget (in USD).xlsx", "")}
    assert line_for(_rows(got))[0] == "Checked by Iron Jarvis: the spreadsheet opens, no error cells, 1 total adds up."
    assert T.not_covered(_rows(got), [str(p)]) is None


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


def test_a_file_that_is_gone_says_nothing_and_an_empty_one_is_worth_a_look(tmp_path):
    assert content.check_files([str(tmp_path / "scratch.py")]) == []
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")
    assert content.check_files([str(empty)]) == [Check(DOC_TEXT, LOOK, "it is empty", file="empty.xlsx")]


def test_nothing_says_a_file_does_not_open_or_why():
    """The words "does not open" and every reason for it (it is not a real
    Office file, a part is missing, it was cut off) are GONE from the
    package: each was a guess about the file. What is said is the reader's
    own failure (``content.could_not_open``)."""
    import inspect

    from iron_jarvis.checks import existing, tool, turn

    assert not hasattr(R, "open_words")
    for module in (content, R, existing, tool, turn):
        text = inspect.getsource(module)
        code = "\n".join(line for line in text.splitlines() if "return " in line or " = " in line)
        for words in ("it does not open", "not a real Office file", "a part of the file is missing",
                      "the PDF was cut off", "not the kind of file its name says"):
            assert words not in code, (module.__name__, words)
