"""file_search / grep: UTF-16 is text, and an oversize skip is a HOLE (T7).

v1.232.0 (audit Wave 6, finding T7) - two silent holes in both search tools,
the v1.195.0 lesson applied one layer down ("search must never answer a
question it did not actually ask"):

1. **UTF-16 was invisible.** Every other byte of a UTF-16 file IS a NUL, so the
   cheap binary sniff both tools share classified it as a blob and skipped it
   without a word. On this machine that is not exotic: PowerShell 5.1's ``>``
   redirect writes UTF-16LE, so the user's own logs were unsearchable. The BOM
   is checked BEFORE the NUL sniff now and the file is decoded.
2. **The size caps were silent.** A file over the cap was dropped like a
   binary, so a 3 MB log the search "did not find" read exactly like one with
   no match. Oversize files are COUNTED and reported the way undecodable ones
   already were - a separate count, because "we could not read it" and "we
   chose not to open it" are different apologies.

Converted from ``tests/_audit_20260904/test_q5_search_caps.py``, deleted with
this change.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.filesearch.service import (
    MAX_FILE_BYTES,
    FileSearchService,
    SearchNotes,
)
from iron_jarvis.filesearch.tools import FileSearchTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import _MAX_GREP_FILE_BYTES, GrepTool


def _ctx(tmp_path: Path) -> ToolContext:
    engine = make_engine(str(tmp_path / "caps.db"))
    init_db(engine)
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


def _search(root: Path, query: str = "needle"):
    notes = SearchNotes()
    hits = FileSearchService([root]).search(
        query, mode="content", limit=50, roots=None, notes=notes
    )
    return hits, notes


def _grep(root: Path, pattern: str = "needle", path: str | None = None):
    args = {"pattern": pattern}
    if path:
        args["path"] = path
    return asyncio.run(GrepTool().execute(args, _ctx(root)))


# --------------------------------------------------------------------------- #
# UTF-16 is text
# --------------------------------------------------------------------------- #
def test_file_search_finds_a_utf16_file(tmp_path: Path):
    (tmp_path / "ps.log").write_text("the needle is here\n", encoding="utf-16")
    hits, notes = _search(tmp_path)
    assert [Path(h["path"]).name for h in hits] == ["ps.log"]
    # Found, not "reported as skipped" - a hit is the honest answer here.
    assert notes.unreadable == 0


def test_grep_finds_a_utf16_file(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ps.log").write_text("the needle is here\n", encoding="utf-16")
    res = _grep(tmp_path, path="ws")
    assert res.data["matches"] == 1
    assert "needle" in res.output
    assert res.data["skipped_unreadable"] == 0


def test_a_real_binary_is_still_skipped_silently(tmp_path: Path):
    """The BOM check must not turn the NUL sniff off: a .png has no BOM and
    counting every binary would drown the notes this feature exists to carry."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00needle\x00")
    (ws / "ok.txt").write_text("needle\n", encoding="utf-8")
    hits, notes = _search(ws)
    assert [Path(h["path"]).name for h in hits] == ["ok.txt"]
    assert notes.note() == ""
    res = _grep(tmp_path, path="ws")
    assert res.data["matches"] == 1
    assert "skipped" not in res.output


# --------------------------------------------------------------------------- #
# an oversize skip is reported
# --------------------------------------------------------------------------- #
def test_file_search_counts_and_reports_an_oversize_file(tmp_path: Path):
    (tmp_path / "big.log").write_text("x" * MAX_FILE_BYTES + "\nneedle\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("needle\n", encoding="utf-8")
    hits, notes = _search(tmp_path)
    assert [Path(h["path"]).name for h in hits] == ["ok.txt"]
    assert notes.oversize == 1
    assert "1 file(s) skipped as oversize" in notes.note()
    assert "did NOT cover them" in notes.note()


def test_the_file_search_tool_carries_the_oversize_count(tmp_path: Path):
    # The searched root is a subfolder: _ctx boots the state DB in tmp_path
    # itself, and that file is over the 1 MB cap too (truthfully counted).
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.log").write_text("x" * MAX_FILE_BYTES + "\nneedle\n", encoding="utf-8")
    res = asyncio.run(
        FileSearchTool(FileSearchService([ws])).execute(
            {"query": "needle", "mode": "content"}, _ctx(tmp_path)
        )
    )
    assert res.data["skipped_oversize"] == 1
    assert "skipped as oversize" in res.output


def test_grep_counts_and_reports_an_oversize_file(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.log").write_text(
        "x" * _MAX_GREP_FILE_BYTES + "\nneedle\n", encoding="utf-8"
    )
    (ws / "ok.txt").write_text("needle\n", encoding="utf-8")
    res = _grep(tmp_path, path="ws")
    assert res.data["matches"] == 1
    assert res.data["skipped_oversize"] == 1
    assert "1 file(s) skipped as oversize" in res.output
    assert "did NOT cover them" in res.output


def test_a_clean_tree_still_says_nothing(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.txt").write_text("needle\n", encoding="utf-8")
    hits, notes = _search(ws)
    assert len(hits) == 1 and notes.note() == ""
    res = _grep(tmp_path, path="ws")
    assert res.data["skipped_oversize"] == 0
    assert "skipped" not in res.output
