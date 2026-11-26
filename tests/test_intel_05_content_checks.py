"""Intelligence plan, release 5: what a made file SAYS is checked by rule.

Real files (an .xlsx written by openpyxl, a .docx by python-docx, a PDF by
pypdf) go through ``checks/content``: a spreadsheet with an error cell fails
and the finding names the cell; totals that add up are counted and a wrong
one is named with both figures; a formula total with no saved result is
never read as zero; an empty document fails; a PDF's page count is compared
with what the reply states for that file. A check that breaks, a file that
is too large, and a reply that runs out of time read "not checked", never
"passed". Nothing here calls a model.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from openpyxl import Workbook

from iron_jarvis.checks import content
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

    def with_saved(path):
        book = real(path)
        book.values["Summary"][(1, 2)] = "#DIV/0!"
        return book

    monkeypatch.setattr(content, "_load", with_saved)
    (errors,) = _by(content.check_files([str(p)]), SHEET_ERRORS)
    assert (errors.result, errors.detail) == (FAILED, "Summary!B1 holds #DIV/0!")


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #
def test_totals_that_add_up_are_counted(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [
        ["Item", "Q1", "Q2", "Q3"],
        ["Rent", 1200, 1200, 1250],
        ["Food", 300, 310.25, 290],
        ["Total", 1500, 1510.25, 1540],
    ])
    got = content.check_files([str(p)])
    assert _by(got, SHEET_TOTALS) == [Check(SHEET_TOTALS, PASSED, "3 totals add up", file="t.xlsx")]
    assert line_for(_rows(got))[0] == "Checked by Iron Jarvis: the spreadsheet opens, no error cells, 3 totals add up."


def test_a_wrong_total_is_named_with_what_it_says_and_what_the_cells_add_up_to(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1700]])
    got = _by(content.check_files([str(p)]), SHEET_TOTALS)
    assert got == [Check(
        SHEET_TOTALS, FAILED, "Summary!B5 says 1,700 and the cells above it add up to 1,650.50", file="t.xlsx")]


def test_one_right_and_one_wrong_total_are_both_said(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [["Item", "A", "B"], ["x", 1, 10], ["y", 2, 20], ["Total", 3, 31]])
    got = {c.result: c.detail for c in _by(content.check_files([str(p)]), SHEET_TOTALS)}
    assert got == {PASSED: "1 total adds up", FAILED: "Summary!C4 says 31 and the cells above it add up to 30"}


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


def test_a_formula_total_whose_saved_result_differs_from_its_cells_is_wrong(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", "=SUM(B2:B4)"]])
    real = content._load

    def stale(path):
        book = real(path)
        book.values["Summary"][(5, 2)] = 1500
        return book

    monkeypatch.setattr(content, "_load", stale)
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [Check(
        SHEET_TOTALS, FAILED, "Summary!B5 says 1,500 and the cells it totals add up to 1,650.50", file="t.xlsx")]


def test_a_total_that_cannot_be_worked_out_by_rule_is_not_checked_never_passed(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [
        ["Item", "Amount"],
        ["Rent", 1200],
        ["Food", "=VLOOKUP(A3,Other!A:B,2,0)"],     # no saved result, not a sum
        ["Total", "=SUM(B2:B3)"],
        ["Grand total", "=AVERAGE(B2:B3)*2"],
    ])
    got = _by(content.check_files([str(p)]), SHEET_TOTALS)
    assert [c.result for c in got] == [NOT_CHECKED]
    assert got[0].detail == (
        "2 totals could not be worked out by rule: Summary!B4 adds cells this check cannot work out, and 1 more")


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


def test_a_rounded_total_is_right_and_a_rate_column_is_not_a_total(tmp_path):
    p = _xlsx(tmp_path / "t.xlsx", [
        ["Item", "Amount", "Rate"],
        ["a", 33.4, 0.2],
        ["b", 66.9, 0.4],
        ["Total", 100, 0.3],
    ])
    assert _by(content.check_files([str(p)]), SHEET_TOTALS) == [
        Check(SHEET_TOTALS, PASSED, "1 total adds up", file="t.xlsx")]


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


@pytest.mark.parametrize("reply,want", [
    ("Saved report.pdf (3 pages).", 3),
    ("`report.pdf`, 12 pages, is in the folder.", 12),
    ("report.pdf has two pages.", 2),
    ("The 4-page report.pdf is ready.", 4),
    ("A one page PDF report.pdf.", 1),
    # A count that is about something else states nothing for this file.
    ("I read your 12-page contract and saved report.pdf.", None),
    ("report.pdf was built from the 40 pages you sent.", None),
    ("See page 3 of report.pdf.", None),
    ("report.pdf (3 pages). Later: report.pdf (4 pages).", None),   # two counts: none is taken
    ("other.pdf (9 pages)", None),
    ("", None),
])
def test_only_a_count_tied_to_the_files_own_name_is_read(reply, want):
    assert content.stated_pages(reply, "report.pdf") == want


# --------------------------------------------------------------------------- #
# Never raises, bounded, off the loop, only readable files
# --------------------------------------------------------------------------- #
def test_a_check_that_raises_is_not_checked_never_passed(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "t.xlsx", [*BUDGET, ["Total", 1650.5]])

    def broken(_path):
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


def test_a_file_past_the_size_cap_is_not_opened(tmp_path, monkeypatch):
    p = _xlsx(tmp_path / "big.xlsx", BUDGET)
    opened: list[str] = []
    monkeypatch.setattr(content, "MAX_BYTES", 100)
    monkeypatch.setattr(content, "_load", lambda path: opened.append(str(path)))
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
    monkeypatch.setattr(content, "_load", lambda path: opened.append(str(path)))
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


def test_the_deadline_is_read_from_the_clock_and_what_is_left_is_not_checked(tmp_path):
    files = [str(_xlsx(tmp_path / f"f{i}.xlsx", BUDGET)) for i in range(3)]
    ticks = iter([0.0, 1.0, 9.0, 9.0])          # the start, then one look before each file
    got = content.check_files(files, clock=lambda: next(ticks), deadline_s=4.0)
    by_file = {name: [c for c in got if c.file == name] for name in ("f0.xlsx", "f1.xlsx", "f2.xlsx")}
    assert [c.result for c in by_file["f0.xlsx"]] == [PASSED, PASSED]
    assert by_file["f1.xlsx"] == [Check(FILE_OPENS, NOT_CHECKED, content.OUT_OF_TIME, file="f1.xlsx")]
    assert by_file["f2.xlsx"] == [Check(FILE_OPENS, NOT_CHECKED, content.OUT_OF_TIME, file="f2.xlsx")]


def test_only_the_first_files_of_a_reply_are_checked_and_the_rest_are_said(tmp_path):
    files = [str(_xlsx(tmp_path / f"f{i}.xlsx", BUDGET)) for i in range(4)]
    got = content.check_files(files + [files[0]], max_files=2)      # a file named twice is one file
    assert {c.file for c in got if c.result == PASSED} == {"f0.xlsx", "f1.xlsx"}
    assert got[-1] == Check(FILE_OPENS, NOT_CHECKED, "2 more files were not checked: only the first 2 of a reply are")
    assert line_for(_rows(got))[0].endswith(
        "Not checked: the other files (2 more files were not checked: only the first 2 of a reply are).")


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
    monkeypatch.setattr(content, "_load", lambda path: opened.append(str(path)))
    assert content.check_files([str(p)]) == [
        Check(FILE_OPENS, NOT_CHECKED, "it is outside the folders Iron Jarvis may read", file="t.xlsx")]
    assert opened == []


def test_a_file_that_is_gone_says_nothing_and_an_empty_or_broken_one_fails(tmp_path):
    assert content.check_files([str(tmp_path / "scratch.py")]) == []
    empty = tmp_path / "empty.xlsx"
    empty.write_bytes(b"")
    assert content.check_files([str(empty)]) == [Check(FILE_OPENS, FAILED, "it is empty", file="empty.xlsx")]
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"this is not a workbook")
    (only,) = content.check_files([str(broken)])
    assert (only.check, only.result) == (FILE_OPENS, FAILED)
    assert only.detail.startswith("it does not open (")
