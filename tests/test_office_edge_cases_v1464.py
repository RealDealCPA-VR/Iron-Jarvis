"""v1.464.0: edge cases in the office-file tools, each found by running the
real tools against awkward but ordinary files.

1. A long tool result in a plain chat was cut at 12,000 characters with no
   word that it was cut (and the tool's own "this was shortened" note, which
   sits last, was cut off with it). A 1,000-row ledger reached the model as
   its first 275 rows.
2. A long number sent as text (an 18-digit id, a 16-digit card number) was
   written to a spreadsheet as a number and came back rounded.
3. A file name with a colon ("Re: Q1 budget.txt") answered "wrote 17 bytes"
   and left an empty file called "Re".
4. A UTF-16 text or CSV file (Excel's "Unicode Text", PowerShell's ">") was
   read as letters with a NUL after each one.
5. Text that only starts with "=" was stored as a formula.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.documents import writers as W
from iron_jarvis.documents.readers import _decode_bytes
from iron_jarvis.tools.base import ToolContext, safe_path
from iron_jarvis.tools.builtins import ReadFileTool, WriteFileTool


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r",
                       config=None, event_bus=None, engine=None)


# ------------------------------------------------- 1. a cut is always said --


def test_a_plain_chat_result_that_fits_is_unchanged_byte_for_byte():
    for text in ("", "short", "x" * CT.TOOL_MESSAGE_CHARS):
        assert CT.tool_message_text(text, None) == text


def test_a_plain_chat_result_that_is_cut_says_so_and_keeps_its_end():
    rows = "\n".join(f"2026-01-{i % 28 + 1:02d},Vendor {i:04d},Office supplies,{100 + i}" for i in range(1000))
    full = rows + "\nTOTAL,,,599500\n[truncated to 16000 of 43138 chars]"
    seen = CT.tool_message_text(full, None)
    assert len(seen) <= CT.TOOL_MESSAGE_CHARS
    assert "characters cut here" in seen
    assert f"of {len(full):,} characters" in seen
    # The end survives: the total row and the tool's own note.
    assert "TOTAL,,,599500" in seen and seen.endswith("[truncated to 16000 of 43138 chars]")
    # ...and so does the start.
    assert seen.startswith("2026-01-01,Vendor 0000")


def test_a_coding_sessions_own_cap_is_untouched():
    text = "y" * 50_000
    assert CT.tool_message_text(text, 30_000) != CT.tool_message_text(text, None)
    assert len(CT.tool_message_text(text, 30_000)) <= 30_000


# ---------------------------------------- 2. long numbers keep every digit --


@pytest.mark.parametrize("text", [
    "123456789012345678",      # an 18-digit id
    "4111111111111111",        # a 16-digit card number
    "5500005555555559",
    "-0042",                   # a code, not minus forty-two
    "1234567890123.4567",      # more digits than a spreadsheet number holds
    "00123",                   # the older rule still holds
])
def test_a_number_a_spreadsheet_cannot_hold_exactly_stays_text(text):
    assert W._xlsx_coerce(text) == (text, None)


@pytest.mark.parametrize("text,number", [
    ("1245.50", 1245.5), ("90210", 90210), ("-7", -7), ("0.10", 0.1),
    ("123456789012345", 123456789012345),   # 15 digits is exact
])
def test_ordinary_numbers_are_still_real_numbers(text, number):
    value, _ = W._xlsx_coerce(text)
    assert value == number and not isinstance(value, str)


def test_a_workbook_round_trip_keeps_the_long_numbers(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    out = tmp_path / "accounts.xlsx"
    W.write_document(out, {"Accounts": [
        ["Account", "Card", "Balance", "Note"],
        ["123456789012345678", "4111111111111111", "1245.50", "=== see memo ==="],
        ["000987", "5500005555555559", "=SUM(C2:C2)", "=> see the next page"],
    ]})
    ws = openpyxl.load_workbook(out)["Accounts"]
    assert ws["A2"].value == "123456789012345678" and ws["A2"].data_type == "s"
    assert ws["B2"].value == "4111111111111111"
    assert ws["A3"].value == "000987"
    assert ws["C2"].value == 1245.5 and ws["C2"].data_type == "n"
    # 5. text that only starts with "=" is text; a real formula is a formula.
    assert ws["D2"].value == "=== see memo ===" and ws["D2"].data_type == "s"
    assert ws["D3"].data_type == "s"
    assert ws["C3"].value == "=SUM(C2:C2)" and ws["C3"].data_type == "f"


@pytest.mark.parametrize("formula", ["=SUM(B2:B5)", "=A1+B1", "=-A1", "=(A1)*2", '="x"&A1', "=$A$1", "= SUM(A1)"])
def test_real_formulas_are_still_formulas(formula):
    assert W._XLSX_FORMULA_RX.match(formula)


@pytest.mark.parametrize("text", ["=== see memo ===", "==", "=", "= = =", "=>", "=/"])
def test_text_that_only_starts_with_an_equals_sign_is_not_a_formula(text):
    assert not W._XLSX_FORMULA_RX.match(text)


# --------------------------------------------- 3. a colon in a file name ----

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="NTFS streams")


@windows_only
@pytest.mark.parametrize("name", ["Re: Q1 budget.txt", "re:port.xlsx", "notes/a:b.txt", "x.txt:hidden"])
def test_a_colon_in_a_name_is_refused_in_words_before_anything_is_written(tmp_path, name):
    with pytest.raises(PermissionError, match="has a colon"):
        safe_path(tmp_path, name)
    # The tool refuses the same way it refuses a path outside the folder: the
    # registry turns this into a failed call carrying these words.
    with pytest.raises(PermissionError, match="has a colon"):
        asyncio.run(WriteFileTool().execute({"path": name, "content": "seventeen bytes!!"}, _ctx(tmp_path)))
    assert list(tmp_path.iterdir()) == [], "nothing may be left behind (it used to leave an empty file)"


@windows_only
def test_a_drive_letter_is_not_a_colon_in_a_name(tmp_path):
    inside = tmp_path / "ok.txt"
    assert safe_path(tmp_path, str(inside)) == inside.resolve()
    assert safe_path(tmp_path, "plain name.txt") == (tmp_path / "plain name.txt").resolve()


# ------------------------------------------------------- 4. UTF-16 files ----


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
def test_a_utf16_file_with_its_marker_is_read_as_text(tmp_path, encoding):
    text = "Name\tAmount\nJosé Ñuñez\t1,245.50\n"
    marker = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}.get(encoding, b"")
    data = marker + text.encode(encoding)
    assert _decode_bytes(data).replace("﻿", "") == text
    f = tmp_path / "export.txt"
    f.write_bytes(data)
    res = asyncio.run(ReadFileTool().execute({"path": "export.txt"}, _ctx(tmp_path)))
    assert res.ok is True
    assert "José Ñuñez" in res.output and "\x00" not in res.output


@pytest.mark.parametrize("data,expect", [
    ("café".encode("utf-8"), "café"),
    (b"\xef\xbb\xbfName,Amount\n", "Name,Amount\n"),
    ("café".encode("cp1252"), "café"),
    (b"", ""),
])
def test_the_older_encodings_read_as_before(data, expect):
    assert _decode_bytes(data) == expect


# ------------------------------ 6. a file open in another program says so ---


@windows_only
def test_a_file_open_in_another_program_is_named_not_the_folder(tmp_path):
    """A workbook open in Excel: the write is refused by Windows. The old
    words blamed the folder and told the user to pick another one."""
    import msvcrt  # noqa: F401 - Windows only, like the lock below
    import os

    from iron_jarvis.tools.base import unwritable_workspace_error

    target = tmp_path / "books.txt"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o444)  # read-only: Windows refuses the write with EACCES
    try:
        res = asyncio.run(WriteFileTool().execute({"path": "books.txt", "content": "new"}, _ctx(tmp_path)))
    finally:
        os.chmod(target, 0o666)
    assert res.ok is False
    assert res.error == (
        "cannot write books.txt: it is open in another program (such as Excel or Word) "
        "or is marked read-only. Close it there, then try again"
    )
    assert target.read_text(encoding="utf-8") == "old"
    # A refusal with no file behind it keeps the older sentence about the folder.
    plain = PermissionError(13, "Permission denied")
    assert "not writable" in unwritable_workspace_error(plain, tmp_path)
    # The path-check's own refusal keeps its own words.
    assert unwritable_workspace_error(PermissionError("path 'x' escapes"), tmp_path) == "PermissionError: path 'x' escapes"


# ------------------- 7. formula results a program never saved are said ------


def _workbook(path, rows, formulas_cached=False):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inv"
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_a_total_over_formulas_with_no_saved_result_says_it_is_incomplete(tmp_path):
    from iron_jarvis.documents.excel_tools import ExcelQueryTool, uncached_formulas

    book = tmp_path / "inv.xlsx"
    _workbook(book, [["Item", "Qty", "Price", "Line"], ["A", 2, 10, "=B2*C2"], ["B", 3, 5, "=B3*C3"], ["C", 1, 7, 7]])
    assert uncached_formulas(book, None) == 2
    res = asyncio.run(ExcelQueryTool().execute({"path": "inv.xlsx", "op": "sum", "column": "Line"}, _ctx(tmp_path)))
    # v1.465.0 (moved on purpose): the two formulas are WORKED OUT, so the
    # figure is the true 42 (v1.464.0 could only say 7 and warn).
    assert res.ok is True and "SUM of Line = 42.00" in res.output
    assert "2 formula cell(s) on this sheet had no saved result; Iron Jarvis worked them out" in res.output
    assert "incomplete" not in res.output

    # A formula the engine cannot work out is still LEFT OUT, and said.
    odd = tmp_path / "odd.xlsx"
    _workbook(odd, [["Item", "Line"], ["A", 20], ["B", '=INDIRECT("B2")'], ["C", "=B2*2"]])
    res = asyncio.run(ExcelQueryTool().execute({"path": "odd.xlsx", "op": "sum", "column": "Line"}, _ctx(tmp_path)))
    assert res.ok is True and "SUM of Line = 60.00" in res.output
    assert "1 formula cell(s) on this sheet had no saved result; Iron Jarvis worked them out" in res.output
    assert "WARNING: 1 cell(s) on this sheet hold a formula" in res.output
    assert "this answer is incomplete" in res.output


def test_a_sheet_of_plain_numbers_gets_no_warning(tmp_path):
    from iron_jarvis.documents.excel_tools import ExcelQueryTool, uncached_formulas

    book = tmp_path / "plain.xlsx"
    _workbook(book, [["Item", "Line"], ["A", 20], ["B", 15]])
    assert uncached_formulas(book, None) == 0
    assert uncached_formulas(tmp_path / "missing.xlsx", None) == 0
    res = asyncio.run(ExcelQueryTool().execute({"path": "plain.xlsx", "op": "sum", "column": "Line"}, _ctx(tmp_path)))
    assert res.output == "Inv: SUM of Line = 35.00 (2 row(s) considered)"


def test_an_edit_of_a_workbook_with_formulas_says_their_results_are_now_blank(tmp_path):
    from iron_jarvis.documents.excel_tools import ExcelEditTool

    book = tmp_path / "pl.xlsx"
    _workbook(book, [["Item", "Amount"], ["Rent", 500], ["Total", "=SUM(B2:B2)"]])
    res = asyncio.run(ExcelEditTool().execute(
        {"path": "pl.xlsx", "edits": [{"cell": "A1", "value": "Line item"}]}, _ctx(tmp_path)))
    assert res.ok is True, res.error
    # v1.465.0 (moved on purpose): the result is worked out and kept IN the
    # file, so it is not blank for the next reader.
    assert "Worked out 1 formula result(s) and saved them in the file" in res.output
    import openpyxl

    assert openpyxl.load_workbook(book, data_only=True)["Inv"]["B3"].value == 500
    assert openpyxl.load_workbook(book)["Inv"]["B3"].value == "=SUM(B2:B2)"

    plain = tmp_path / "plain.xlsx"
    _workbook(plain, [["Item", "Amount"], ["Rent", 500]])
    res2 = asyncio.run(ExcelEditTool().execute(
        {"path": "plain.xlsx", "edits": [{"cell": "A1", "value": "Line item"}]}, _ctx(tmp_path)))
    assert res2.ok is True and "formula" not in res2.output
