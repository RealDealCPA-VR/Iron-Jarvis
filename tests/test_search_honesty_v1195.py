"""Search must never answer a question it did not actually ask (v1.195.0).

Two HIGH findings from the 2026-08-21 review, one failure shape:

1. ``file_search`` in content mode swallowed ``re.error`` and returned
   ``ok=True / count=0 / output=""``. A model asking for the everyday literals
   ``read_file(``, ``C:\\Users`` or ``a[b`` was told the text is nowhere on the
   user's disk. The sibling ``grep`` already returned ``bad regex: ...``.
2. ``file_search`` and ``grep`` both hard-coded ``decode("utf-8")`` and treated
   ``UnicodeDecodeError`` as "skip this file". A cp1252 CSV exported by
   Excel/QuickBooks/Lacerte — curly apostrophes, ``€``, an accented client name —
   was invisible with no signal at all. Both now decode through the project's ONE
   decoder (``documents/readers._decode_bytes``), and a file that still cannot be
   turned into text is COUNTED and reported next to grep's truncation note.

Both are the v1.153.1 rule ("a silently short listing reads as complete, and the
model then says a file does not exist") applied to the two search tools.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.filesearch.service import (
    BadSearchPattern,
    FileSearchService,
    SearchNotes,
)
from iron_jarvis.filesearch.tools import FileSearchTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import GrepTool

#: The literal bytes a real Windows office export produces: cp1252 curly
#: apostrophe (0x92 -> U+2019), euro sign (0x80 -> U+20AC) and an accented vowel
#: (0xE9 -> U+00E9). ``bytes``, not an encode() call, because these are exactly
#: the bytes the review recovered off disk and 0x92 is not reachable by encoding
#: the ASCII apostrophe.
CP1252_ROW = b"Smith,O\x92Brien,Retainer fee,\x80500,caf\xe9\n"


def _ctx(tmp_path: Path) -> ToolContext:
    engine = make_engine(str(tmp_path / "honesty.db"))
    init_db(engine)
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


@pytest.fixture
def office_root(tmp_path: Path) -> Path:
    """Two CSVs that both mention "Retainer" — one UTF-8, one cp1252."""
    root = tmp_path / "clients"
    root.mkdir()
    (root / "ok.csv").write_text("Jones,Retainer fee,300\n", encoding="utf-8")
    (root / "excel_export.csv").write_bytes(CP1252_ROW)
    # The premise of the whole finding: these bytes are NOT valid UTF-8.
    with pytest.raises(UnicodeDecodeError):
        CP1252_ROW.decode("utf-8")
    return root


# --------------------------------------------------------------------------- #
# Finding 1 — an invalid regex is an ERROR, not "no matches".
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("query", ["read_file(", "C:\\Users", "a[b", "(unclosed"])
def test_service_raises_instead_of_returning_empty(office_root: Path, query: str):
    svc = FileSearchService([office_root])
    with pytest.raises(BadSearchPattern) as caught:
        svc.search_content(query)
    # The escaped literal is OFFERED so the caller can retry, never applied
    # silently — a fallback nobody asked for is the same class of wrong answer.
    assert caught.value.literal
    assert caught.value.pattern == query


@pytest.mark.parametrize("query", ["read_file(", "C:\\Users", "a[b"])
def test_tool_reports_a_bad_regex_in_greps_shape(office_root: Path, tmp_path, query):
    """The exact repro from the review: ok=True/count=0/output='' was the bug."""
    tool = FileSearchTool(FileSearchService([office_root]))
    res = asyncio.run(tool.execute({"query": query, "mode": "content"}, _ctx(tmp_path)))
    assert res.ok is False, "an uncompilable pattern must not report ok=True"
    assert "bad regex" in (res.error or "")
    # And it must say the search did not happen, not merely that it found nothing.
    assert "Nothing was searched" in (res.error or "")


def test_the_http_route_answers_400_not_500(tmp_path: Path):
    """The two callers this unit may not edit (``GET /filesearch`` and the
    ``file-search`` CLI) now see the raise instead of a silent empty list.

    The ``ValueError`` base is what makes that safe: ``daemon/app.py`` registers a
    ``ValueError`` handler returning **400**, and Starlette resolves handlers by
    walking ``type(exc).__mro__``. Re-basing this on ``Exception`` would turn a
    typo in the dashboard's search box into a 500 — the shape this project's
    history says the UI reads as "daemon offline". Asserted end to end so the
    coupling cannot rot silently in either file.
    """
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    assert issubclass(BadSearchPattern, ValueError)
    client = TestClient(create_app(str(tmp_path)))
    r = client.get("/filesearch", params={"q": "C:\\Users", "mode": "content"})
    assert r.status_code == 400, "a bad pattern must not be a 500 (nor a silent 200)"
    assert "bad regex" in r.text


def test_a_valid_regex_still_searches(office_root: Path, tmp_path: Path):
    """The guard must not have broken the feature it guards."""
    tool = FileSearchTool(FileSearchService([office_root]))
    res = asyncio.run(
        tool.execute({"query": r"Retainer", "mode": "content"}, _ctx(tmp_path))
    )
    assert res.ok and res.data["count"] >= 1


def test_name_mode_is_untouched_by_the_regex_guard(office_root: Path, tmp_path: Path):
    """`name` mode is glob/substring, so '(' is a legal query there and must
    keep working — the guard belongs to content mode alone."""
    tool = FileSearchTool(FileSearchService([office_root]))
    res = asyncio.run(tool.execute({"query": "*.csv", "mode": "name"}, _ctx(tmp_path)))
    assert res.ok and res.data["count"] == 2


# --------------------------------------------------------------------------- #
# Finding 2 — a cp1252 office export is SEARCHABLE, by both tools.
# --------------------------------------------------------------------------- #


def test_file_search_finds_the_cp1252_export(office_root: Path, tmp_path: Path):
    tool = FileSearchTool(FileSearchService([office_root]))
    res = asyncio.run(
        tool.execute({"query": "Retainer", "mode": "content"}, _ctx(tmp_path))
    )
    assert res.ok
    hit_files = {Path(r["path"]).name for r in res.data["results"]}
    assert hit_files == {"ok.csv", "excel_export.csv"}, (
        "the cp1252 export was dropped on the floor — the exact defect"
    )


def test_file_search_finds_the_client_name_inside_the_cp1252_bytes(
    office_root: Path, tmp_path: Path
):
    """Searching O'Brien used to return zero hits and no indication why."""
    tool = FileSearchTool(FileSearchService([office_root]))
    res = asyncio.run(
        tool.execute({"query": "Brien", "mode": "content"}, _ctx(tmp_path))
    )
    assert res.ok and res.data["count"] == 1
    assert "excel_export.csv" in res.output
    # Decoded, not mangled: cp1252 0x92 is U+2019, not U+FFFD.
    assert "\u2019" in res.data["results"][0]["text"]


def test_grep_finds_the_cp1252_export(office_root: Path):
    res = asyncio.run(GrepTool().execute({"pattern": "Brien"}, _ctx(office_root)))
    assert res.ok
    assert res.data["matches"] == 1, "grep skipped the cp1252 file silently"
    assert "excel_export.csv" in res.output


def test_grep_still_ignores_binaries(tmp_path: Path):
    """The old code kept binaries out BY ACCIDENT, via UnicodeDecodeError. The
    replacement decoder is total (latin-1 maps all 256 bytes), so the NUL sniff
    is now the only thing standing between grep and every .exe in the tree."""
    (tmp_path / "blob.bin").write_bytes(b"\x00needle\x00\x01\x02")
    (tmp_path / "real.txt").write_text("needle\n", encoding="utf-8")
    res = asyncio.run(GrepTool().execute({"pattern": "needle"}, _ctx(tmp_path)))
    assert res.ok and res.data["matches"] == 1
    assert "real.txt" in res.output and "blob.bin" not in res.output


# --------------------------------------------------------------------------- #
# Finding 2, second half — a file we truly cannot read is COUNTED and SAID.
# --------------------------------------------------------------------------- #


def test_file_search_reports_files_it_could_not_decode(tmp_path: Path):
    """A .docx that is not a zip: the extractor genuinely cannot read it.

    Silence here is the dangerous option — the count comes back lower and reads
    as a complete answer.
    """
    root = tmp_path / "docs"
    root.mkdir()
    (root / "good.txt").write_text("Retainer agreement\n", encoding="utf-8")
    # Named .docx, but the bytes are not an OOXML package -> extraction raises.
    (root / "broken.docx").write_bytes(b"this is not really a docx package")

    tool = FileSearchTool(FileSearchService([root]))
    res = asyncio.run(
        tool.execute({"query": "Retainer", "mode": "content"}, _ctx(tmp_path))
    )
    assert res.ok
    assert res.data["count"] == 1
    assert res.data["skipped_unreadable"] == 1
    assert "1 file(s) skipped" in res.output
    assert "did NOT cover" in res.output


def test_a_clean_search_carries_no_skip_note(office_root: Path, tmp_path: Path):
    """The note must mean something: it cannot fire on an ordinary search."""
    tool = FileSearchTool(FileSearchService([office_root]))
    res = asyncio.run(
        tool.execute({"query": "Retainer", "mode": "content"}, _ctx(tmp_path))
    )
    assert res.data["skipped_unreadable"] == 0
    assert "skipped" not in res.output


def test_binary_and_oversized_files_are_not_counted_as_skips(tmp_path: Path):
    """Counting deliberate exclusions would put a scary note on every search of a
    real folder and drown the signal the note exists to carry."""
    root = tmp_path / "mixed"
    root.mkdir()
    (root / "note.txt").write_text("Retainer\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02Retainer\x00")
    (root / "big.log").write_text("Retainer " * 200_000, encoding="utf-8")  # > 1 MiB

    notes = SearchNotes()
    svc = FileSearchService([root])
    hits = svc.search_content("Retainer", notes=notes)
    assert len(hits) == 1
    assert notes.unreadable == 0
    assert notes.note() == ""


def test_grep_reports_files_it_could_not_read(tmp_path: Path, monkeypatch):
    """grep's skip note rides alongside the truncation note it already had."""
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("needle\n", encoding="utf-8")

    import iron_jarvis.tools.builtins as B

    ctx = _ctx(tmp_path)  # built BEFORE the patch so the DB boot is untouched
    real_read_bytes = Path.read_bytes

    def flaky(self: Path):
        # One specific file is unreadable — a denied ACL / locked handle, which
        # `except OSError: continue` used to hide completely.
        if self.name == "b.txt":
            raise PermissionError("access is denied")
        return real_read_bytes(self)

    monkeypatch.setattr(B.Path, "read_bytes", flaky)
    res = asyncio.run(B.GrepTool().execute({"pattern": "needle"}, ctx))
    assert res.ok
    assert res.data["matches"] == 1
    assert res.data["skipped_unreadable"] == 1
    assert "1 file(s) skipped" in res.output


def test_grep_stays_quiet_when_nothing_was_skipped(tmp_path: Path):
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    res = asyncio.run(GrepTool().execute({"pattern": "needle"}, _ctx(tmp_path)))
    assert res.data["skipped_unreadable"] == 0
    assert "skipped" not in res.output


# --------------------------------------------------------------------------- #
# The decoder is the project's, not a second copy of the same logic.
# --------------------------------------------------------------------------- #


def test_both_paths_use_the_shared_decoder():
    """Two independent decode ladders drift; ``documents/readers._decode_bytes``
    is the one written for exactly these bytes and already under test.

    Asserted by IDENTITY, not by grepping the source: a second ladder that
    happened to mention the right name would pass a source check.
    """
    import iron_jarvis.filesearch.service as S
    import iron_jarvis.tools.builtins as B
    from iron_jarvis.documents.readers import _decode_bytes

    for mod in (S, B):
        mod._DECODER = None  # force the lazy bind to run again
        assert mod._decode_text(b"caf\xe9") == "café"
        assert mod._DECODER is _decode_bytes


def test_the_decoder_import_is_lazy():
    """It is called once per file across a bounded walk — paying the
    ``iron_jarvis.documents`` package import per call would dominate a search."""
    import inspect

    import iron_jarvis.filesearch.service as S
    import iron_jarvis.tools.builtins as B

    for mod in (S, B):
        # The import lives INSIDE the helper (lazy) and is cached in a module
        # global, mirroring `documents/redact.py`'s lazy private import.
        assert "from ..documents.readers import _decode_bytes" in inspect.getsource(
            mod._decode_text
        )
        assert "_DECODER" in inspect.getsource(mod._decode_text)
