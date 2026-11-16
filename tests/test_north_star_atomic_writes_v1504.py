"""v1.504.0 (north-star 4.4): writers save BESIDE the file, then swap.

Before this, ``write_file``, ``edit_file``, ``multi_edit``, ``notebook_edit``,
``excel_edit``, ``excel_apply_spec`` and ``excel_reconcile``'s result opened the
target for writing first: a failure half-way (a full disk, a crash) left the
user's file truncated. Every one now goes through ``tools.base.atomic_target``
(one helper, the pattern ``documents/writers.py`` already used): the old file
is untouched until the new one is whole, and nothing is left beside it.

The fault is injected at the lowest level both the old and the new code reach
(``Path.write_text`` / ``Path.write_bytes`` / ``openpyxl.Workbook.save``): it
writes HALF of what it was given and then fails, which is what a full disk does.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext, atomic_target, atomic_write_bytes


def _ctx(p, ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s-1504-atomic", agent_run_id="r-1504",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


@pytest.fixture()
def env(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    return p, ws


def _beside(ws: Path) -> list[str]:
    """Every name in the folder (a stray temp would show here)."""
    return sorted(x.name for x in ws.iterdir())


def _half_then_full_disk(monkeypatch, ws: Path) -> None:
    """``Path.write_text`` / ``write_bytes`` under *ws* write half, then fail."""
    real_text, real_bytes = Path.write_text, Path.write_bytes

    def _inside(path: Path) -> bool:
        try:
            return Path(path).resolve().is_relative_to(ws.resolve())
        except OSError:
            return False

    def fake_text(self, data, encoding=None, errors=None, newline=None):
        if not _inside(self):
            return real_text(self, data, encoding=encoding, errors=errors, newline=newline)
        with open(self, "w", encoding=encoding, errors=errors, newline=newline) as fh:
            fh.write(data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    def fake_bytes(self, data):
        if not _inside(self):
            return real_bytes(self, data)
        with open(self, "wb") as fh:
            fh.write(bytes(data)[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_text", fake_text)
    monkeypatch.setattr(Path, "write_bytes", fake_bytes)


async def _run(tool, args, ctx):
    """The tool's own answer; a raised error counts as a failed call."""
    try:
        res = await tool.execute(args, ctx)
    except OSError as exc:
        return False, str(exc)
    return res.ok, res.error or ""


# --------------------------------------------------------------------------- #
# the helper itself
# --------------------------------------------------------------------------- #


def test_the_helper_swaps_only_a_whole_file_and_leaves_nothing_beside(tmp_path):
    target = tmp_path / "a.bin"
    target.write_bytes(b"old")
    with pytest.raises(RuntimeError):
        with atomic_target(target) as tmp:
            tmp.write_bytes(b"half")
            assert ".tmp-" in tmp.name and tmp.parent == target.parent
            raise RuntimeError("stopped half-way")
    assert target.read_bytes() == b"old"
    assert _beside(tmp_path) == ["a.bin"]
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    assert _beside(tmp_path) == ["a.bin"]


def test_a_refused_swap_keeps_the_file_and_the_honest_open_elsewhere_words(env, monkeypatch):
    """Windows refuses the swap while Excel holds the file: today's words
    ("it is open in another program") and no stray temp."""
    from iron_jarvis.tools.builtins import WriteFileTool

    p, ws = env
    (ws / "a.txt").write_text("original", encoding="utf-8")
    real_replace = os.replace

    def held(src, dst, *a, **k):
        if Path(dst).name == "a.txt":
            raise PermissionError(errno.EACCES, "Access is denied", str(src), None, str(dst))
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", held)
    import asyncio

    res = asyncio.run(WriteFileTool().execute({"path": "a.txt", "content": "new"}, _ctx(p, ws)))
    assert res.ok is False
    assert "open in another program" in (res.error or "")
    assert (ws / "a.txt").read_text(encoding="utf-8") == "original"
    assert _beside(ws) == ["a.txt"]


# --------------------------------------------------------------------------- #
# every site
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_file_failing_half_way_keeps_the_old_file(env, monkeypatch):
    from iron_jarvis.tools.builtins import WriteFileTool

    p, ws = env
    (ws / "notes.txt").write_text("the client's original notes\n", encoding="utf-8")
    _half_then_full_disk(monkeypatch, ws)
    ok, _err = await _run(WriteFileTool(), {"path": "notes.txt", "content": "x" * 400}, _ctx(p, ws))
    assert ok is False
    monkeypatch.undo()
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "the client's original notes\n"
    assert _beside(ws) == ["notes.txt"]


@pytest.mark.asyncio
async def test_write_file_still_reports_its_path_and_created_paths(env):
    from iron_jarvis.tools.builtins import WriteFileTool

    p, ws = env
    res = await WriteFileTool().execute({"path": "sub/new.txt", "content": "a\nb\n"}, _ctx(p, ws))
    assert res.ok and res.output == "wrote 4 bytes to sub/new.txt"
    assert res.created_paths == [str((ws / "sub" / "new.txt").resolve())]
    # The same bytes ``Path.write_text`` would have written (newlines included).
    probe = ws / "probe.txt"
    probe.write_text("a\nb\n", encoding="utf-8")
    assert (ws / "sub" / "new.txt").read_bytes() == probe.read_bytes()
    assert _beside(ws / "sub") == ["new.txt"]


@pytest.mark.asyncio
async def test_edit_file_failing_half_way_keeps_the_old_file(env, monkeypatch):
    from iron_jarvis.tools.builtins import EditFileTool

    p, ws = env
    (ws / "a.py").write_bytes(b"alpha = 1\nbeta = 2\n" * 20)
    _half_then_full_disk(monkeypatch, ws)
    ok, _ = await _run(EditFileTool(), {"path": "a.py", "old": "alpha = 1\nbeta = 2\n" * 20, "new": "gamma"}, _ctx(p, ws))
    assert ok is False
    monkeypatch.undo()
    assert (ws / "a.py").read_bytes() == b"alpha = 1\nbeta = 2\n" * 20
    assert _beside(ws) == ["a.py"]


@pytest.mark.asyncio
async def test_multi_edit_failing_half_way_keeps_the_old_file(env, monkeypatch):
    from iron_jarvis.harness.tools import MultiEditTool

    p, ws = env
    body = b"one\ntwo\nthree\n" * 30
    (ws / "m.txt").write_bytes(body)
    _half_then_full_disk(monkeypatch, ws)
    ok, _ = await _run(
        MultiEditTool(), {"path": "m.txt", "edits": [{"old": "one", "new": "1", "replace_all": True}]}, _ctx(p, ws)
    )
    assert ok is False
    monkeypatch.undo()
    assert (ws / "m.txt").read_bytes() == body
    assert _beside(ws) == ["m.txt"]


@pytest.mark.asyncio
async def test_notebook_edit_failing_half_way_keeps_the_old_file(env, monkeypatch):
    from iron_jarvis.harness.tools import NotebookEditTool

    p, ws = env
    nb = {
        "cells": [{"cell_type": "code", "id": "c1", "metadata": {}, "outputs": [],
                   "execution_count": None, "source": ["x = 1\n" * 40]}],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }
    raw = json.dumps(nb, indent=1).encode("utf-8")
    (ws / "n.ipynb").write_bytes(raw)
    _half_then_full_disk(monkeypatch, ws)
    ok, _ = await _run(NotebookEditTool(), {"path": "n.ipynb", "cell_index": 0, "new_source": "y = 2"}, _ctx(p, ws))
    assert ok is False
    monkeypatch.undo()
    assert (ws / "n.ipynb").read_bytes() == raw
    assert _beside(ws) == ["n.ipynb"]


def _workbook(path: Path, rows) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    for r in rows:
        wb.active.append(list(r))
    wb.save(str(path))
    return path.read_bytes()


def _save_half_then_fail(monkeypatch, names: set[str]) -> None:
    """``openpyxl.Workbook.save`` writes half a file and fails for a save
    whose file name starts with one of *names* or is a temp of one."""
    from openpyxl import Workbook

    real = Workbook.save

    def fake(self, filename):
        name = Path(str(filename)).name
        if any(name == n or name.startswith(f".{n}.tmp-") for n in names):
            with open(filename, "wb") as fh:
                fh.write(b"PK\x03\x04 half a workbook")
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, filename)

    monkeypatch.setattr(Workbook, "save", fake)


@pytest.mark.asyncio
async def test_excel_edit_failing_half_way_keeps_the_workbook(env, monkeypatch):
    from iron_jarvis.documents.excel_tools import ExcelEditTool

    p, ws = env
    before = _workbook(ws / "book.xlsx", [("a", 1), ("b", 2)])
    _save_half_then_fail(monkeypatch, {"book.xlsx"})
    res = await ExcelEditTool().execute({"path": "book.xlsx", "edits": [{"cell": "B1", "value": 9}]}, _ctx(p, ws))
    assert res.ok is False
    assert (ws / "book.xlsx").read_bytes() == before
    assert _beside(ws) == ["book.xlsx"]


@pytest.mark.asyncio
async def test_excel_apply_spec_failing_half_way_keeps_the_workbook(env, monkeypatch):
    from iron_jarvis.documents.excel_tools import ExcelApplySpecTool

    p, ws = env
    before = _workbook(ws / "spec.xlsx", [("a", 1), ("b", 2)])
    _save_half_then_fail(monkeypatch, {"spec.xlsx"})
    ok, _ = await _run(
        ExcelApplySpecTool(),
        {"path": "spec.xlsx", "sheet": "Sheet", "spec": {"cells": {"C1": {"bold": True}}}},
        _ctx(p, ws),
    )
    assert ok is False
    assert (ws / "spec.xlsx").read_bytes() == before
    assert _beside(ws) == ["spec.xlsx"]


@pytest.mark.asyncio
async def test_excel_reconcile_result_failing_half_way_leaves_no_half_file(env, monkeypatch):
    from iron_jarvis.documents.excel_reconcile import ExcelReconcileTool

    p, ws = env
    _workbook(ws / "bank.xlsx", [("Amount",), (10,), (20,)])
    _workbook(ws / "books.xlsx", [("Amount",), (10,), (20,)])
    _save_half_then_fail(monkeypatch, {"out.xlsx"})
    ok, _ = await _run(
        ExcelReconcileTool(),
        {"a": {"path": "bank.xlsx", "amount": "Amount"}, "b": {"path": "books.xlsx", "amount": "Amount"},
         "save_as": "out.xlsx"},
        _ctx(p, ws),
    )
    assert ok is False
    assert _beside(ws) == ["bank.xlsx", "books.xlsx"]
