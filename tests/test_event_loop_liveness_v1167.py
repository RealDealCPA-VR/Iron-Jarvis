"""The document tools stay off the event loop (v1.167.0).

Two confirmed freezes from the deep review: WriteDocumentTool rendered
docx/xlsx/pdf SYNCHRONOUSLY inside its async execute (a 40k-row workbook froze
the whole daemon a measured ~2s — the "Daemon offline" class the v1.153.1 hard
rule exists to prevent), and ListFolderTool ran an UNBOUNDED sync
iterdir/stat loop (an unhydrated OneDrive folder or dead network share hung
every request in the app).

The liveness pins use a heartbeat, not a wall-clock threshold: a ticker task
on the same loop must keep ticking WHILE the tool works. Offloaded work lets
it tick freely; the pre-fix inline call starved it to ~zero. The assertion is
a generous floor (>= 3 ticks during a 0.4s block), so a slow CI runner cannot
flake it — only re-blocking the loop can fail it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from iron_jarvis.documents import tools as doc_tools
from iron_jarvis.documents.tools import ListFolderTool, WriteDocumentTool
from iron_jarvis.tools.base import ToolContext


def _ctx(ws: Path) -> ToolContext:
    ws.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="t",
        agent_run_id="t",
        config=None,
        event_bus=None,
        engine=None,
    )


async def _run_with_heartbeat(coro):
    """Run *coro* while a same-loop ticker counts how often it got scheduled."""
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        done.set()
        await t
    return result, ticks


def test_write_document_render_does_not_starve_the_loop(tmp_path, monkeypatch):
    ws = tmp_path / "ws"

    def slow_render(target, content, **kwargs):
        time.sleep(0.4)  # a big workbook's render, compressed
        Path(target).write_text(str(content), encoding="utf-8")
        return Path(target)

    monkeypatch.setattr(doc_tools, "write_document", slow_render)

    async def body():
        res, ticks = await _run_with_heartbeat(
            WriteDocumentTool().execute(
                {"path": "big.txt", "content": "rows"}, _ctx(ws)
            )
        )
        assert res.ok, res.error
        assert ticks >= 3, (
            f"the loop starved during the render ({ticks} ticks) — "
            "write_document is back on the event loop"
        )

    asyncio.run(body())


def test_list_folder_scan_does_not_starve_the_loop(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    folder = tmp_path / "big-folder"
    folder.mkdir()
    for i in range(60):
        (folder / f"f{i:03}.txt").write_text("x", encoding="utf-8")

    real_stat = Path.stat

    def slow_stat(self, *a, **k):
        if self.parent == folder:
            time.sleep(0.01)  # a cold OneDrive/network stat, compressed
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", slow_stat)

    async def body():
        res, ticks = await _run_with_heartbeat(
            ListFolderTool().execute({"path": str(folder)}, _ctx(ws))
        )
        assert res.ok, res.error
        assert ticks >= 3, (
            f"the loop starved during the folder scan ({ticks} ticks) — "
            "list_folder is back on the event loop"
        )

    asyncio.run(body())


def test_list_folder_scan_cap_is_reported_never_silent(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    folder = tmp_path / "huge"
    folder.mkdir()
    for i in range(30):
        (folder / f"f{i:03}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ListFolderTool, "_SCAN_CAP", 10)

    async def body():
        res = await ListFolderTool().execute({"path": str(folder)}, _ctx(ws))
        assert res.ok, res.error
        assert res.data["scan_capped"] is True
        assert res.data["total"] == 10
        assert "SCAN CAPPED" in res.output  # truncation is always REPORTED
        assert "10+" in res.output  # the count admits it is a floor

    asyncio.run(body())


def test_list_folder_uncapped_reports_exact_total(tmp_path):
    ws = tmp_path / "ws"
    folder = tmp_path / "small"
    folder.mkdir()
    for i in range(5):
        (folder / f"f{i}.txt").write_text("x", encoding="utf-8")

    async def body():
        res = await ListFolderTool().execute({"path": str(folder)}, _ctx(ws))
        assert res.ok, res.error
        assert res.data == {"path": str(folder), "total": 5, "scan_capped": False}
        assert "5 entries" in res.output and "+" not in res.output.split("\n")[0]

    asyncio.run(body())
