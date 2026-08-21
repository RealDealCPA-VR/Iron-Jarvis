"""v1.195.0 — the document/LTM undo hooks must not block the event loop.

THE MEASURED SHAPE OF THIS BUG: ``tools/builtins.py`` moved ``write_file`` /
``edit_file``'s ``capture_undo`` onto a worker thread in v1.153.1 ("this hook is
awaited by ``registry.invoke`` BEFORE ``execute`` runs, so its stat + full read
of the prior file blocks every other request in the daemon"). The DOCUMENT tools
— the ones that touch client workbooks and scanned returns — kept doing the same
work inline: ``read_bytes()`` → ``sha256`` → ``save_preimage`` (everything over
``INLINE_MAX_BYTES`` = 8 KB spills to ``<home>/undo/``, i.e. every real
document), all on the daemon's single loop, and then ``execute`` read the same
file a second time. Measured at ~1.4 ms per MB blocked: 59 ms for a 40 MB
scanned return, unbounded on a network share or an unhydrated OneDrive path.
``revert_workspace_file`` — the SHARED inverse, also used by write_file and
edit_file — was worse: re-hash + pre-image load + full write-back, on the loop.

Every offload test asserts BOTH halves, so deleting a hop cannot leave it green:
  * STRUCTURAL — the blocking call ran on a worker thread, not the loop's
    thread (the main thread under pytest-asyncio).
  * BEHAVIOURAL — the loop actually serviced other work while it ran.

The round-trip tests are the other half of the brief: a thread hop must not
change undo SEMANTICS. Same descriptors, same restored bytes, and — the one that
matters most — ``RevertConflict`` is still RAISED (``asyncio.to_thread``
re-raises it in the awaiting coroutine), because ``daemon/routes/undo.py:272``
maps that exception to the 409 the dashboard shows.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.documents.excel_tools import ExcelApplySpecTool, ExcelEditTool
from iron_jarvis.documents.pdf_tools import PdfArrangeTool, PdfSplitTool
from iron_jarvis.documents.tools import RedactPiiTool, WriteDocumentTool
from iron_jarvis.ltm.tools import LTMAppendTool
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.undo import (
    INLINE_MAX_BYTES,
    RevertConflict,
    revert_workspace_file,
    sha256_bytes,
)

#: How long the probed call blocks, and how often the watcher ticks. The block
#: must dwarf the tick so an offloaded run yields many ticks and an inline one
#: yields none. Copied from tests/test_event_loop_offload_v1175.py.
_BLOCK_S = 0.30
_TICK_S = 0.01
_MIN_TICKS = 5

#: Big enough that ``make_file_descriptor`` SPILLS the pre-image to a blob file
#: instead of riding inline — the disk write the finding is about.
_BIG = b"%PDF-" + b"x" * (INLINE_MAX_BYTES * 8)


class _Cfg:
    """make_file_descriptor stores pre-images under config.home."""

    def __init__(self, home: Path) -> None:
        self.home = str(home)


def _ctx(ws: Path, home: Path) -> ToolContext:
    return ToolContext(
        workspace=ws,
        session_id="t",
        agent_run_id="t",
        config=_Cfg(home),
        event_bus=None,
        engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    return d


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "home"
    d.mkdir()
    return d


async def _ticks_during(coro):
    """Await ``coro`` while counting how many times the event loop got control.

    Returns ``(result, ticks)``. An inline blocking call starves the ticker and
    returns ~0; a properly offloaded one lets it run throughout.
    """
    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(_TICK_S)
            ticks += 1

    task = asyncio.ensure_future(_ticker())
    try:
        result = await coro
    finally:
        stop = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    return result, ticks


def _probe(monkeypatch, target, attr):
    """Make ``target.attr`` block like a slow disk and record WHERE it ran.

    Returns the dict the wrapper writes into (``{"thread": ...}``). Only the
    FIRST call is recorded so a hook that touches the probe twice still reports
    the thread the work started on.
    """
    if isinstance(target, str):  # a module path, resolved like monkeypatch's own
        target = importlib.import_module(target)
    seen: dict[str, str] = {}
    real = getattr(target, attr)

    def _slow(*args, **kwargs):
        seen.setdefault("thread", threading.current_thread().name)
        time.sleep(_BLOCK_S)
        return real(*args, **kwargs)

    monkeypatch.setattr(target, attr, _slow)
    return seen


def _assert_offloaded(seen: dict[str, str], ticks: int, what: str) -> None:
    assert seen.get("thread") is not None, f"{what}: the probed call never ran"
    # STRUCTURAL: not the loop's own thread.
    assert seen["thread"] != threading.main_thread().name, (
        f"{what} ran ON THE EVENT LOOP (thread {seen['thread']!r})"
    )
    # BEHAVIOURAL: the loop kept serving other work throughout.
    assert ticks >= _MIN_TICKS, f"{what}: event loop was starved (only {ticks} ticks)"


# --- capture_undo: read + hash + spill, off the loop ---------------------------


async def test_excel_edit_capture_undo_offloads(ws, home, monkeypatch):
    """``excel_edit`` on a real client workbook: the pre-image read is the whole
    file, and it is awaited BEFORE execute reads the same file again."""
    (ws / "book.xlsx").write_bytes(_BIG)
    seen = _probe(monkeypatch, Path, "read_bytes")

    undo, ticks = await _ticks_during(
        ExcelEditTool().capture_undo({"path": "book.xlsx"}, _ctx(ws, home))
    )

    _assert_offloaded(seen, ticks, "ExcelEditTool.capture_undo")
    assert undo is not None and undo["kind"] == "file_restore"
    assert undo["pre_ref"], "a >8 KB pre-image must spill to a blob file"


async def test_excel_apply_spec_capture_undo_offloads(ws, home, monkeypatch):
    """The second Excel writer carries the identical hook — and the identical bug."""
    (ws / "book.xlsx").write_bytes(_BIG)
    seen = _probe(monkeypatch, Path, "read_bytes")

    undo, ticks = await _ticks_during(
        ExcelApplySpecTool().capture_undo({"path": "book.xlsx"}, _ctx(ws, home))
    )

    _assert_offloaded(seen, ticks, "ExcelApplySpecTool.capture_undo")
    assert undo is not None and undo["kind"] == "file_restore"


async def test_pdf_arrange_capture_undo_offloads(ws, home, monkeypatch):
    """``pdf_merge``/``pdf_arrange`` overwriting an existing output — a scanned
    return is tens of MB, and this hook reads all of it."""
    (ws / "out.pdf").write_bytes(_BIG)
    seen = _probe(monkeypatch, Path, "read_bytes")

    undo, ticks = await _ticks_during(
        PdfArrangeTool().capture_undo(
            {"inputs": [{"path": "a.pdf"}], "output": "out.pdf"}, _ctx(ws, home)
        )
    )

    _assert_offloaded(seen, ticks, "PdfArrangeTool.capture_undo")
    assert undo is not None and undo["kind"] == "file_restore"


async def test_write_document_capture_undo_offloads(ws, home, monkeypatch):
    """``write_document`` already offloaded the READ; the sha256 of the same
    payload and the pre-image spill stayed on the loop. One hop now covers all
    three — probed at ``make_file_descriptor``, the half that was still inline."""
    (ws / "report.docx").write_bytes(_BIG)
    seen = _probe(monkeypatch, "iron_jarvis.documents.tools", "make_file_descriptor")

    undo, ticks = await _ticks_during(
        WriteDocumentTool().capture_undo(
            {"path": "report.docx", "content": "x"}, _ctx(ws, home)
        )
    )

    _assert_offloaded(seen, ticks, "WriteDocumentTool.capture_undo (hash + spill)")
    assert undo is not None and undo["kind"] == "file_restore"


async def test_redact_pii_capture_undo_offloads(ws, home, monkeypatch):
    """``redact_pii``'s hook also RESOLVES paths (three ``resolve()`` calls) —
    all of it now runs in the worker thread."""
    (ws / "k1.pdf").write_bytes(b"%PDF-source")
    (ws / "k1.redacted.pdf").write_bytes(_BIG)  # a prior run's output to restore
    seen = _probe(monkeypatch, Path, "read_bytes")

    undo, ticks = await _ticks_during(
        RedactPiiTool().capture_undo({"path": "k1.pdf"}, _ctx(ws, home))
    )

    _assert_offloaded(seen, ticks, "RedactPiiTool.capture_undo")
    assert undo is not None and undo["kind"] == "file_restore"


async def test_pdf_split_capture_undo_offloads(ws, home, monkeypatch):
    """The split snapshot is a DIRECTORY LISTING, not a read — same rule: an
    ``iterdir`` on a network share is bounded only by the OS."""
    (ws / "book.pdf").write_bytes(b"%PDF-source")
    (ws / "parts").mkdir()
    (ws / "parts" / "book-part01.pdf").write_bytes(b"%PDF-old")
    seen = _probe(monkeypatch, Path, "iterdir")

    tool = PdfSplitTool()
    args = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo, ticks = await _ticks_during(tool.capture_undo(args, _ctx(ws, home)))

    _assert_offloaded(seen, ticks, "PdfSplitTool.capture_undo")
    assert undo is not None and undo["kind"] == "pdf_split_delete"
    assert json.loads(undo["pre_inline"])["existing"] == ["book-part01.pdf"]
    # The pending handoff execute() reads back by id(args) must survive the hop.
    assert tool._pending[id(args)] is undo


async def test_ltm_append_capture_undo_offloads(ws, home, monkeypatch, tmp_path):
    """An Obsidian vault lives on a synced folder — the note read goes off the
    loop like every other pre-image."""
    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "retainer.md").write_text("# Retainer\n\n" + "x" * 40000, encoding="utf-8")
    manager = SimpleNamespace(
        default_source=lambda: "brain",
        get=lambda src: SimpleNamespace(dir=str(notes)),
    )
    seen = _probe(monkeypatch, Path, "read_text")

    undo, ticks = await _ticks_during(
        LTMAppendTool(manager).capture_undo(
            {"title": "Retainer", "content": "more"}, _ctx(ws, home)
        )
    )

    _assert_offloaded(seen, ticks, "LTMAppendTool.capture_undo")
    assert undo is not None and undo["kind"] == "memory_restore"
    assert undo["pre_ref"], "a >8 KB note must spill to a blob file"


# --- revert: re-hash + pre-image load + write-back, off the loop ---------------


async def test_shared_revert_workspace_file_offloads(ws, home, monkeypatch):
    """``revert_workspace_file`` is the inverse for write_file, edit_file,
    write_document, excel_edit, pdf_merge and redact_pii — one blocked loop
    reachable from six tools, and from Time Travel."""
    tool = ExcelEditTool()
    ctx = _ctx(ws, home)
    (ws / "book.xlsx").write_bytes(_BIG)
    undo = await tool.capture_undo({"path": "book.xlsx"}, ctx)
    (ws / "book.xlsx").write_bytes(b"%PDF-edited-by-execute")
    undo["post_sha256"] = sha256_bytes(b"%PDF-edited-by-execute")

    seen = _probe(monkeypatch, "iron_jarvis.tools.undo", "sha256_target")
    result, ticks = await _ticks_during(tool.revert(undo, ctx))

    _assert_offloaded(seen, ticks, "revert_workspace_file")
    assert result.ok, result.error
    assert (ws / "book.xlsx").read_bytes() == _BIG


async def test_ltm_revert_offloads(ws, home, monkeypatch, tmp_path):
    notes = tmp_path / "vault"
    notes.mkdir()
    body = "# Retainer\n\n" + "x" * 40000
    (notes / "retainer.md").write_text(body, encoding="utf-8")
    manager = SimpleNamespace(
        default_source=lambda: "brain",
        get=lambda src: SimpleNamespace(dir=str(notes)),
    )
    tool = LTMAppendTool(manager)
    ctx = _ctx(ws, home)
    undo = await tool.capture_undo({"title": "Retainer", "content": "more"}, ctx)
    # What MarkdownDirConnector.append leaves behind (capture predicted its hash).
    (notes / "retainer.md").write_text(
        f"{body.rstrip()}\n\nmore\n", encoding="utf-8"
    )

    seen = _probe(monkeypatch, "iron_jarvis.ltm.tools", "sha256_target")
    result, ticks = await _ticks_during(tool.revert(undo, ctx))

    _assert_offloaded(seen, ticks, "LTMAppendTool.revert")
    assert result.ok, result.error
    assert (notes / "retainer.md").read_text(encoding="utf-8") == body


async def test_pdf_split_revert_offloads(ws, home, monkeypatch):
    """The heaviest undo in the tree: two directory listings plus a full re-read
    of every split part before anything is deleted."""
    (ws / "book.pdf").write_bytes(b"%PDF-source")
    (ws / "parts").mkdir()
    tool = PdfSplitTool()
    ctx = _ctx(ws, home)
    args = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo = await tool.capture_undo(args, ctx)
    outs = []
    for i in (1, 2):
        p = ws / "parts" / f"book-part0{i}.pdf"
        p.write_bytes(_BIG)
        outs.append({"path": str(p)})
    tool._record_outputs(undo, outs)  # what execute() does after a good run

    seen = _probe(monkeypatch, "iron_jarvis.documents.pdf_tools", "sha256_bytes")
    result, ticks = await _ticks_during(tool.revert(undo, ctx))

    _assert_offloaded(seen, ticks, "PdfSplitTool.revert")
    assert result.ok, result.error
    assert not list((ws / "parts").iterdir())


# --- semantics unchanged: same bytes back, and a conflict still RAISES ---------


async def test_capture_and_revert_round_trip_a_large_file_exactly(ws, home):
    """End-to-end through the hops: the spilled pre-image restores the workbook
    byte-for-byte, and the consumed blob is cleaned up."""
    tool = ExcelEditTool()
    ctx = _ctx(ws, home)
    (ws / "book.xlsx").write_bytes(_BIG)

    undo = await tool.capture_undo({"path": "book.xlsx"}, ctx)
    assert undo["pre_ref"] and (home / "undo" / undo["pre_ref"]).is_file()
    assert undo["pre_sha256"] == sha256_bytes(_BIG)

    (ws / "book.xlsx").write_bytes(b"edited")
    undo["post_sha256"] = sha256_bytes(b"edited")
    result = await tool.revert(undo, ctx)

    assert result.ok and "restored prior content" in result.output
    assert (ws / "book.xlsx").read_bytes() == _BIG
    assert not (home / "undo" / undo["pre_ref"]).exists()


async def test_shared_revert_file_delete_branch_still_removes_the_created_file(ws, home):
    """The other branch of the shared inverse (write_file / write_document
    creating a NEW file): the created file is unlinked, not restored."""
    ctx = _ctx(ws, home)
    (ws / "new.txt").write_text("created by execute", encoding="utf-8")
    undo = {
        "kind": "file_delete",
        "reversible": True,
        "pre_ref": None,
        "pre_inline": json.dumps({"path": "new.txt", "mode": "text", "data": None}),
        "pre_sha256": None,
        "post_sha256": sha256_bytes(b"created by execute"),
    }

    result = await revert_workspace_file(undo, ctx)

    assert result.ok and "removed created file" in result.output
    assert not (ws / "new.txt").exists()


async def test_revert_still_raises_conflict_through_the_thread_hop(ws, home):
    """The control flow the /undo route depends on: a target that changed since
    the action RAISES ``RevertConflict`` (→ 409), and NOTHING is written."""
    tool = ExcelEditTool()
    ctx = _ctx(ws, home)
    (ws / "book.xlsx").write_bytes(_BIG)
    undo = await tool.capture_undo({"path": "book.xlsx"}, ctx)
    (ws / "book.xlsx").write_bytes(b"what execute wrote")
    undo["post_sha256"] = sha256_bytes(b"what execute wrote")
    (ws / "book.xlsx").write_bytes(b"the user edited it since")

    with pytest.raises(RevertConflict) as exc:
        await tool.revert(undo, ctx)

    assert "refusing to undo" in str(exc.value)
    assert (ws / "book.xlsx").read_bytes() == b"the user edited it since"


async def test_ltm_revert_still_raises_conflict_through_the_thread_hop(
    ws, home, tmp_path
):
    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "retainer.md").write_text("# Retainer\n\nold\n", encoding="utf-8")
    manager = SimpleNamespace(
        default_source=lambda: "brain",
        get=lambda src: SimpleNamespace(dir=str(notes)),
    )
    tool = LTMAppendTool(manager)
    ctx = _ctx(ws, home)
    undo = await tool.capture_undo({"title": "Retainer", "content": "more"}, ctx)
    (notes / "retainer.md").write_text("# Retainer\n\nI edited this myself.\n",
                                       encoding="utf-8")

    with pytest.raises(RevertConflict):
        await tool.revert(undo, ctx)

    assert "I edited this myself" in (notes / "retainer.md").read_text(encoding="utf-8")


async def test_pdf_split_revert_still_raises_conflict_through_the_thread_hop(ws, home):
    """All-or-nothing: an edited part refuses the whole undo and its sibling
    survives — unchanged by the hop."""
    (ws / "book.pdf").write_bytes(b"%PDF-source")
    (ws / "parts").mkdir()
    tool = PdfSplitTool()
    ctx = _ctx(ws, home)
    args = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo = await tool.capture_undo(args, ctx)
    outs = []
    for i in (1, 2):
        p = ws / "parts" / f"book-part0{i}.pdf"
        p.write_bytes(b"%PDF-part" + str(i).encode())
        outs.append({"path": str(p)})
    tool._record_outputs(undo, outs)
    (ws / "parts" / "book-part01.pdf").write_bytes(b"%PDF-user-edited-since")

    with pytest.raises(RevertConflict):
        await tool.revert(undo, ctx)

    assert (ws / "parts" / "book-part01.pdf").read_bytes() == b"%PDF-user-edited-since"
    assert (ws / "parts" / "book-part02.pdf").is_file()
