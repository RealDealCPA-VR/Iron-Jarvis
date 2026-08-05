"""pdf_arrange / pdf_split tools (v1.138.0) — contract, wiring, and undo.

The tool layer is exercised two ways:

* CONTRACT tests run against a FAKE ``pdf_pages`` engine injected at the
  module boundary (the tools lazy-import it), so the tool's own rules —
  workspace-confined outputs, read-gated inputs, undo, honest engine-computed
  counts — are pinned independently of the engine's landing.
* INTEGRATION tests (``importorskip``) run the same tools against the REAL
  ``iron_jarvis.documents.pdf_pages`` with real pypdf files; they go green the
  moment the engine module lands.

Wiring tests pin the config permission tiers, AUTO_SAFE membership, the
autoselect sentence rule, and the chat_turn nudge line (source-pin).
"""

from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import pytest

import iron_jarvis.documents as _docs_pkg
from iron_jarvis.documents.pdf_tools import (
    PdfArrangeTool,
    PdfSplitTool,
    pdf_page_tools,
)
from iron_jarvis.tools.base import Reversibility, ToolContext


# --- harness ------------------------------------------------------------------


class _Cfg:
    """make_file_descriptor stores pre-images under config.home."""

    def __init__(self, home: Path) -> None:
        self.home = str(home)


def _ctx(ws: Path, home: Path | None = None) -> ToolContext:
    return ToolContext(
        workspace=ws,
        session_id="t",
        agent_run_id="t",
        config=_Cfg(home) if home is not None else None,
        event_bus=None,
        engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    return d


@pytest.fixture
def fake_engine(monkeypatch):
    """Install a fake ``iron_jarvis.documents.pdf_pages`` that writes real
    files and reports FIXED page counts — so any count surfacing in a tool
    result is provably the engine's, not the tool's arithmetic."""
    mod = types.ModuleType("iron_jarvis.documents.pdf_pages")

    class ArrangeInput:  # the spec-pinned constructor shape
        def __init__(self, path, pages_spec="all", password=None):
            self.path = path
            self.pages_spec = pages_spec
            self.password = password

    calls: dict = {}

    def arrange(inputs, out_path, *, crop=None, encrypt_password=None, metadata=None):
        calls["arrange"] = {
            "inputs": inputs,
            "out_path": str(out_path),
            "crop": crop,
            "encrypt_password": encrypt_password,
            "metadata": metadata,
        }
        Path(out_path).write_bytes(b"%PDF-fake-arranged")
        return {
            "path": str(out_path),
            "pages": 7,
            "inputs": [{"path": i.path, "pages": 3} for i in inputs],
        }

    def split(path, out_dir, *, mode, password=None):
        calls["split"] = {
            "path": str(path),
            "out_dir": str(out_dir),
            "mode": mode,
            "password": password,
        }
        outs = []
        for i in (1, 2):
            p = Path(out_dir) / f"{Path(path).stem}-part0{i}.pdf"
            n = 2
            while p.exists():  # the real engine never clobbers — mirror it
                p = Path(out_dir) / f"{Path(path).stem}-part0{i}-{n}.pdf"
                n += 1
            p.write_bytes(b"%PDF-fake-part")
            outs.append({"path": str(p), "pages": i})
        return {"outputs": outs}

    mod.ArrangeInput = ArrangeInput
    mod.arrange = arrange
    mod.split = split
    mod.calls = calls
    monkeypatch.setitem(sys.modules, "iron_jarvis.documents.pdf_pages", mod)
    # `from . import pdf_pages` resolves the package ATTRIBUTE first once the
    # real module has ever been imported — patch both lookups.
    monkeypatch.setattr(_docs_pkg, "pdf_pages", mod, raising=False)
    return mod


def _src_pdf(where: Path, name: str = "src.pdf") -> Path:
    p = where / name
    p.write_bytes(b"%PDF-1.4 fake source")
    return p


# --- pdf_arrange: tool contract ----------------------------------------------


async def test_arrange_writes_workspace_output_with_engine_counts(ws, fake_engine):
    src = _src_pdf(ws)
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": "src.pdf", "pages": "2-5@90"}], "output": "out/new.pdf"},
        _ctx(ws),
    )
    assert res.ok, res.error
    assert (ws / "out" / "new.pdf").is_file()
    # Result data shape + engine-computed honesty: the counts are the fake
    # engine's fixed report values, verbatim.
    assert res.data == {
        "path": "out/new.pdf",
        "pages": 7,
        "inputs": [{"path": str(src), "pages": 3}],
    }
    assert "7 page(s)" in res.output
    # The page spec and defaults reached the engine via the pinned API.
    sent = fake_engine.calls["arrange"]["inputs"]
    assert sent[0].pages_spec == "2-5@90" and sent[0].password is None


async def test_arrange_output_escape_blocked(ws, fake_engine):
    _src_pdf(ws)
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": "src.pdf"}], "output": "../evil.pdf"}, _ctx(ws)
    )
    assert not res.ok
    assert "escapes the session workspace" in (res.error or "")
    assert not (ws.parent / "evil.pdf").exists()


async def test_arrange_absolute_input_is_read_gated(ws, tmp_path, monkeypatch):
    outside = _src_pdf(tmp_path, "outside.pdf")
    from iron_jarvis.documents import pdf_tools as mod

    monkeypatch.setattr(mod, "fs_read_ok", lambda p: (False, "protected root"))
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": str(outside)}], "output": "o.pdf"}, _ctx(ws)
    )
    assert not res.ok
    assert "read denied" in (res.error or "")


async def test_arrange_absolute_input_allowed_reads_fine(ws, tmp_path, fake_engine):
    outside = _src_pdf(tmp_path, "anywhere.pdf")
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": str(outside)}], "output": "copy.pdf"}, _ctx(ws)
    )
    assert res.ok, res.error
    assert (ws / "copy.pdf").is_file()


async def test_arrange_never_overwrites_an_input(ws, fake_engine):
    _src_pdf(ws, "same.pdf")
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": "same.pdf"}], "output": "same.pdf"}, _ctx(ws)
    )
    assert not res.ok
    assert "never overwritten" in (res.error or "")
    assert (ws / "same.pdf").read_bytes() == b"%PDF-1.4 fake source"


async def test_arrange_honest_input_errors(ws, fake_engine):
    _src_pdf(ws)
    tool = PdfArrangeTool()
    res = await tool.execute({"inputs": [], "output": "o.pdf"}, _ctx(ws))
    assert not res.ok and "non-empty" in res.error
    res = await tool.execute(
        {"inputs": [{"path": "missing.pdf"}], "output": "o.pdf"}, _ctx(ws)
    )
    assert not res.ok and "not a file" in res.error
    res = await tool.execute(
        {"inputs": [{"path": "src.pdf"}], "output": "o.txt"}, _ctx(ws)
    )
    assert not res.ok and ".pdf" in res.error


async def test_arrange_engine_error_passes_through(ws, fake_engine):
    _src_pdf(ws)

    def boom(*a, **k):
        raise ValueError("page 12 is out of range — the file has 9 pages")

    fake_engine.arrange = boom
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": "src.pdf", "pages": "12"}], "output": "o.pdf"}, _ctx(ws)
    )
    assert not res.ok
    assert "page 12 is out of range" in res.error


# --- pdf_arrange: undo --------------------------------------------------------


async def test_arrange_undo_removes_created_output(ws, tmp_path, fake_engine):
    _src_pdf(ws)
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfArrangeTool()
    args = {"inputs": [{"path": "src.pdf"}], "output": "merged.pdf"}
    undo = await tool.capture_undo(args, ctx)
    assert undo is not None and undo["kind"] == "file_delete"
    res = await tool.execute(args, ctx)
    assert res.ok and (ws / "merged.pdf").is_file()
    rev = await tool.revert(undo, ctx)
    assert rev.ok, rev.error
    assert not (ws / "merged.pdf").exists()


async def test_arrange_undo_restores_prior_bytes(ws, tmp_path, fake_engine):
    _src_pdf(ws)
    (ws / "merged.pdf").write_bytes(b"prior version")
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfArrangeTool()
    args = {"inputs": [{"path": "src.pdf"}], "output": "merged.pdf"}
    undo = await tool.capture_undo(args, ctx)
    assert undo is not None and undo["kind"] == "file_restore"
    res = await tool.execute(args, ctx)
    assert res.ok and (ws / "merged.pdf").read_bytes() != b"prior version"
    rev = await tool.revert(undo, ctx)
    assert rev.ok, rev.error
    assert (ws / "merged.pdf").read_bytes() == b"prior version"


# --- pdf_split: tool contract + undo -----------------------------------------


async def test_split_outputs_and_engine_counts(ws, fake_engine):
    _src_pdf(ws, "book.pdf")
    res = await PdfSplitTool().execute(
        {"path": "book.pdf", "every": 2, "out_dir": "parts"}, _ctx(ws)
    )
    assert res.ok, res.error
    # data {outputs:[{path, pages}]} — counts verbatim from the engine report.
    assert res.data == {
        "outputs": [
            {"path": "parts/book-part01.pdf", "pages": 1},
            {"path": "parts/book-part02.pdf", "pages": 2},
        ]
    }
    assert fake_engine.calls["split"]["mode"] == {"every": 2}
    assert (ws / "parts" / "book-part01.pdf").is_file()


async def test_split_requires_exactly_one_mode(ws, fake_engine):
    _src_pdf(ws, "book.pdf")
    tool = PdfSplitTool()
    res = await tool.execute({"path": "book.pdf"}, _ctx(ws))
    assert not res.ok and "exactly one split mode" in res.error
    res = await tool.execute(
        {"path": "book.pdf", "every": 2, "per_page": True}, _ctx(ws)
    )
    assert not res.ok and "exactly one split mode" in res.error


async def test_split_out_dir_escape_blocked(ws, fake_engine):
    _src_pdf(ws, "book.pdf")
    res = await PdfSplitTool().execute(
        {"path": "book.pdf", "per_page": True, "out_dir": "../loose"}, _ctx(ws)
    )
    assert not res.ok
    assert "escapes the session workspace" in (res.error or "")


async def test_split_input_read_gated(ws, tmp_path, monkeypatch):
    outside = _src_pdf(tmp_path, "outside.pdf")
    from iron_jarvis.documents import pdf_tools as mod

    monkeypatch.setattr(mod, "fs_read_ok", lambda p: (False, "protected root"))
    res = await PdfSplitTool().execute(
        {"path": str(outside), "per_page": True}, _ctx(ws)
    )
    assert not res.ok and "read denied" in res.error


async def test_split_undo_removes_only_new_outputs(ws, tmp_path, fake_engine):
    _src_pdf(ws, "book.pdf")
    parts = ws / "parts"
    parts.mkdir()
    # A PRE-EXISTING part-file (from an earlier run) must survive the undo.
    keeper = parts / "book-part09.pdf"
    keeper.write_bytes(b"older run")
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfSplitTool()
    args = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo = await tool.capture_undo(args, ctx)
    assert undo is not None and undo["kind"] == "pdf_split_delete"
    res = await tool.execute(args, ctx)
    assert res.ok and (parts / "book-part01.pdf").is_file()
    rev = await tool.revert(undo, ctx)
    assert rev.ok, rev.error
    assert not (parts / "book-part01.pdf").exists()
    assert not (parts / "book-part02.pdf").exists()
    assert keeper.read_bytes() == b"older run"  # untouched
    assert parts.is_dir()  # pre-existing dir is never rmdir'd


async def test_split_undo_out_of_order_is_per_run(ws, tmp_path, fake_engine):
    """Two splits into the same dir; undoing the FIRST while the second's
    files still exist must delete ONLY the first run's outputs (the journal
    still lists the second as undoable — its files must survive)."""
    _src_pdf(ws, "book.pdf")
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfSplitTool()
    args1 = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo1 = await tool.capture_undo(args1, ctx)
    res1 = await tool.execute(args1, ctx)
    assert res1.ok, res1.error
    args2 = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo2 = await tool.capture_undo(args2, ctx)
    res2 = await tool.execute(args2, ctx)
    assert res2.ok, res2.error
    run2_names = {Path(o["path"]).name for o in res2.data["outputs"]}
    assert run2_names == {"book-part01-2.pdf", "book-part02-2.pdf"}
    # OUT OF ORDER: undo run 1 first.
    rev = await tool.revert(undo1, ctx)
    assert rev.ok, rev.error
    left = {p.name for p in (ws / "parts").iterdir()}
    assert left == run2_names  # run 2's outputs survived
    # Then run 2's own undo still works. Run 2 captured with the dir already
    # present (run 1 created it), so the empty dir correctly survives.
    rev = await tool.revert(undo2, ctx)
    assert rev.ok, rev.error
    assert (ws / "parts").is_dir()
    assert not any((ws / "parts").iterdir())


async def test_split_undo_refuses_when_output_edited(ws, tmp_path, fake_engine):
    """A user-edited split output is a NEWER change — the undo must refuse
    (RevertConflict, mapped to 409 by the /undo route) and delete NOTHING."""
    from iron_jarvis.tools.undo import RevertConflict

    _src_pdf(ws, "book.pdf")
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfSplitTool()
    args = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok, res.error
    edited = ws / "parts" / "book-part01.pdf"
    edited.write_bytes(b"%PDF-user-edited-since")
    with pytest.raises(RevertConflict):
        await tool.revert(undo, ctx)
    # All-or-nothing: the sibling was not partially deleted either.
    assert edited.read_bytes() == b"%PDF-user-edited-since"
    assert (ws / "parts" / "book-part02.pdf").is_file()


async def test_split_undo_legacy_descriptor_falls_back_to_snapshot_diff(
    ws, tmp_path, fake_engine
):
    """A descriptor without the recorded ``outputs`` list (captured but never
    enriched) still reverts via the pre-run snapshot diff."""
    import json

    _src_pdf(ws, "book.pdf")
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfSplitTool()
    args = {"path": "book.pdf", "every": 2, "out_dir": "parts"}
    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok, res.error
    meta = json.loads(undo["pre_inline"])
    assert meta["outputs"] == ["book-part01.pdf", "book-part02.pdf"]
    meta.pop("outputs")
    meta.pop("hashes")
    undo["pre_inline"] = json.dumps(meta)
    rev = await tool.revert(undo, ctx)
    assert rev.ok, rev.error
    assert not (ws / "parts").exists()


# --- wiring: factory, config, autoselect, chat nudge -------------------------


def test_factory_and_registry_wiring():
    tools = pdf_page_tools()
    assert [t.name for t in tools] == ["pdf_arrange", "pdf_split"]
    for t in tools:
        assert t.reversibility is Reversibility.REVERSIBLE
        assert t.perm_key() == t.name  # config tier keys match tool names
        assert t.input_schema["type"] == "object"
    arrange, split = tools
    assert set(arrange.input_schema["required"]) == {"inputs", "output"}
    assert split.input_schema["required"] == ["path"]
    # The document_tools() factory ships both (platform.py registers via it).
    from iron_jarvis.documents.tools import document_tools

    names = {t.name for t in document_tools()}
    assert {"pdf_arrange", "pdf_split"} <= names


def test_config_permission_tiers_present():
    from iron_jarvis.core import config as cfg

    perms = cfg.default_permissions()
    assert perms["pdf_arrange"] == "allow"
    assert perms["pdf_split"] == "allow"
    # Mirror extract_pdf's placement in the documents section — EXACTLY once
    # each (a repeated dict-key literal is dead code the second entry silently
    # wins over; ruff F601 rejects it, so the legacy duplicate was removed).
    src = inspect.getsource(cfg.default_permissions)
    assert src.count('"pdf_arrange": "allow"') == 1
    assert src.count('"pdf_split": "allow"') == 1
    assert src.count('"extract_pdf": "allow"') == 1  # the mirrored anchor


def test_auto_safe_membership():
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

    assert "pdf_arrange" in AUTO_SAFE_TOOLS
    assert "pdf_split" in AUTO_SAFE_TOOLS


def test_autoselect_rule_fires_on_pdf_page_verbs():
    from iron_jarvis.tools.autoselect import select_auto_tools

    picked = select_auto_tools("merge these pdfs into one file please")
    assert "pdf_arrange" in picked
    picked = select_auto_tools("split this pdf into three parts")
    assert "pdf_split" in picked and "pdf_arrange" in picked
    picked = select_auto_tools("the pdf needs splitting by chapter")
    assert "pdf_split" in picked
    picked = select_auto_tools("rotate the pages in my pdf")
    assert "pdf_arrange" in picked
    # Page/scan phrasings without the literal word "pdf" — the natural way
    # users describe scanned-PDF work — must fire too.
    picked = select_auto_tools("split the scan into separate pages")
    assert "pdf_split" in picked
    picked = select_auto_tools("rotate the last page of the return")
    assert "pdf_arrange" in picked


def test_autoselect_creation_still_picks_write_document():
    from iron_jarvis.tools.autoselect import select_auto_tools

    picked = select_auto_tools("create a pdf report of q3 sales")
    assert picked[0] == "write_document"  # the creator outranks the page tools


def test_autoselect_rule_does_not_fire_without_pdf():
    from iron_jarvis.tools.autoselect import select_auto_tools

    picked = select_auto_tools("merge these cells in the spreadsheet")
    assert "pdf_arrange" not in picked and "pdf_split" not in picked
    picked = select_auto_tools("combine the two branches and rotate the logs")
    assert "pdf_arrange" not in picked and "pdf_split" not in picked


def test_tools_page_lists_pdf_tools(tmp_path):
    """The dashboard Tools page renders GET /tools (registry-driven) — both
    tools must appear there with real descriptions, as the user asked for
    'one of the options' in the tools module."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    specs = {s["name"]: s for s in client.get("/tools").json()["tools"]}
    assert "pdf_arrange" in specs and "pdf_split" in specs
    assert "NEVER modified" in specs["pdf_arrange"]["description"]
    assert specs["pdf_split"]["description"]
    assert specs["pdf_arrange"]["input_schema"]["required"] == ["inputs", "output"]


def test_chat_turn_nudge_line_source_pinned():
    from iron_jarvis.daemon import chat_turn

    src = inspect.getsource(chat_turn)
    # The nudge ships, says what matters, and is gated on the pdf tools.
    assert "pdf_arrange/pdf_split" in src
    assert "never modify the original" in src
    assert 'any(t in ("pdf_arrange", "pdf_split") for t in armed)' in src


# --- integration: the REAL engine (green once pdf_pages lands) ---------------


def _real_pdf(path: Path, pages: int) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    with open(path, "wb") as f:
        w.write(f)
    return path


async def test_integration_arrange_merges_real_pdfs(ws, tmp_path):
    pytest.importorskip("iron_jarvis.documents.pdf_pages")
    _real_pdf(ws / "a.pdf", 2)
    _real_pdf(ws / "b.pdf", 1)
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfArrangeTool()
    args = {
        "inputs": [{"path": "a.pdf"}, {"path": "b.pdf"}],
        "output": "merged.pdf",
    }
    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok, res.error
    assert res.data["pages"] == 3  # engine re-opened the real output
    assert [e["pages"] for e in res.data["inputs"]] == [2, 1]
    from pypdf import PdfReader

    assert len(PdfReader(ws / "merged.pdf").pages) == 3
    rev = await tool.revert(undo, ctx)
    assert rev.ok and not (ws / "merged.pdf").exists()


async def test_integration_split_real_pdf_per_page(ws, tmp_path):
    pytest.importorskip("iron_jarvis.documents.pdf_pages")
    _real_pdf(ws / "tri.pdf", 3)
    home = tmp_path / "home"
    home.mkdir()
    ctx = _ctx(ws, home)
    tool = PdfSplitTool()
    args = {"path": "tri.pdf", "per_page": True, "out_dir": "parts"}
    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok, res.error
    outs = res.data["outputs"]
    assert len(outs) == 3
    assert all(o["pages"] == 1 for o in outs)
    from pypdf import PdfReader

    for o in outs:
        assert len(PdfReader(ws / o["path"]).pages) == 1
    rev = await tool.revert(undo, ctx)
    assert rev.ok
    assert not list((ws / "parts").glob("*.pdf"))
