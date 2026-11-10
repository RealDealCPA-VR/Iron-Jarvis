"""A link to another workbook, against the REAL Excel (v1.469.1).

Skipped unless ``IJ_LIVE_EXCEL=1`` on Windows with Excel. Three workbooks are
open in this test's OWN hidden Excel: the one being changed and a partner's,
both in a folder Jarvis may read, and a third in a folder it may not. A link
into the third is refused before Excel is asked to change anything (read back
through this test's own handle); a link into the partner's is written and
shows the partner's figure. With no folder limit a link is written as before.

The check that judges a link by file name asks Excel where its workbooks are
open from, so those cases only run when this test's Excel is the only one.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Excel on Windows only"),
    pytest.mark.skipif(os.environ.get("IJ_LIVE_EXCEL") != "1", reason="set IJ_LIVE_EXCEL=1 to drive the real Excel"),
    pytest.mark.timeout(240),
]

ENV = "IRONJARVIS_FS_ALLOWLIST"


@pytest.fixture(scope="module")
def excel():
    import pythoncom
    import win32com.client as wc
    import win32process

    pythoncom.CoInitialize()
    app = wc.DispatchEx("Excel.Application")
    pid = win32process.GetWindowThreadProcessId(app.Hwnd)[1]
    app.Visible = False
    app.DisplayAlerts = False
    app.IgnoreRemoteRequests = True
    try:
        yield app
    finally:
        try:
            for wb in list(app.Workbooks):
                wb.Close(False)
            app.IgnoreRemoteRequests = False
            app.Quit()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
        import psutil

        if psutil.pid_exists(pid):
            try:
                p = psutil.Process(pid)
                if p.name().lower() == "excel.exe":
                    p.kill()
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture()
def the_child_may_run(monkeypatch):
    """The suite keeps the Excel child off (conftest); this is what it is for."""
    from iron_jarvis.core import fs_policy
    from iron_jarvis.documents import excel_live as L

    monkeypatch.delenv(L.OFF_ENV, raising=False)
    monkeypatch.delenv(ENV, raising=False)
    fs_policy.use_config(None)
    yield
    fs_policy.use_config(None)


def _new(excel, path: Path, figure: float):
    wb = excel.Workbooks.Add()
    ws = wb.Worksheets(1)
    ws.Name = "Summary"
    ws.Range("A4:B4").Formula = (("Net income", figure),)
    wb.SaveAs(str(path))
    return wb


@pytest.fixture()
def books(excel, tmp_path):
    allowed, other = tmp_path / "Smith", tmp_path / "Jones"
    allowed.mkdir()
    other.mkdir()
    mine = _new(excel, allowed / "Smith.xlsx", 10)
    partner = _new(excel, allowed / "Partner.xlsx", 77)
    theirs = _new(excel, other / "Jones.xlsx", 4242)
    try:
        yield SimpleNamespace(mine=mine, path=str(allowed / "Smith.xlsx"), allowed=allowed, other=other)
    finally:
        for wb in (mine, partner, theirs):
            try:
                wb.Close(False)
            except Exception:  # noqa: BLE001
                pass


def _edit(path: str, formula: str, home: Path):
    from iron_jarvis.documents.excel_live import ExcelLiveEditTool
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=home / "ws", session_id="chat", agent_run_id="r", config=SimpleNamespace(home=home),
                      event_bus=None, engine=None)
    args = {"workbook": path, "ops": [{"op": "set", "sheet": "Summary", "cell": "D4", "formula": formula}]}
    return asyncio.run(ExcelLiveEditTool().execute(args, ctx))


def _only_my_excel() -> bool:
    import psutil

    return sum(1 for p in psutil.process_iter(["name"]) if str(p.info.get("name") or "").lower() == "excel.exe") == 1


def _cell(books):
    return books.mine.Worksheets("Summary").Range("D4")


def test_a_link_that_names_a_folder_jarvis_may_not_read_never_reaches_excel(books, the_child_may_run, monkeypatch, tmp_path):
    from iron_jarvis.documents import excel_live as L

    monkeypatch.setenv(ENV, str(books.allowed))
    res = _edit(books.path, f"='{books.other}\\[Jones.xlsx]Summary'!B4", tmp_path / "home")
    assert not res.ok and res.error.startswith(L.UNCHANGED_LEAD), res.error
    assert "it links to Jones.xlsx, a workbook in a folder Jarvis may not read" in res.error
    assert _cell(books).Formula == "" and books.mine.Saved is True, "nothing was written"


def test_a_link_by_name_to_a_workbook_open_from_such_a_folder_is_refused(books, the_child_may_run, monkeypatch, tmp_path):
    if not _only_my_excel():
        pytest.skip("another Excel is running on this PC")
    monkeypatch.setenv(ENV, str(books.allowed))
    res = _edit(books.path, "=[Jones.xlsx]Summary!B4", tmp_path / "home")
    assert not res.ok and "it links to Jones.xlsx, a workbook in a folder Jarvis may not read" in res.error, res.error
    assert _cell(books).Formula == "" and books.mine.Saved is True


def test_a_link_to_a_workbook_jarvis_may_read_is_written_and_shows_its_figure(books, the_child_may_run, monkeypatch, tmp_path):
    if not _only_my_excel():
        pytest.skip("another Excel is running on this PC")
    monkeypatch.setenv(ENV, str(books.allowed))
    res = _edit(books.path, "=[Partner.xlsx]Summary!B4", tmp_path / "home")
    assert res.ok, res.error
    assert _cell(books).Value == 77
    assert "Partner.xlsx" in _cell(books).Formula


def test_with_no_folder_limit_a_link_is_written_as_before(books, the_child_may_run, tmp_path):
    if not _only_my_excel():
        pytest.skip("another Excel is running on this PC")
    res = _edit(books.path, "=[Jones.xlsx]Summary!B4", tmp_path / "home")
    assert res.ok, res.error
    assert _cell(books).Value == 4242
