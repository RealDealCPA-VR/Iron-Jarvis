"""Created-paths contract (v1.166.0) — P2 territory.

The P2 contract: a tool that writes a file to disk sets
``ToolResult.created_paths`` to the ABSOLUTE path(s) it wrote — on SUCCESS
only, never on failure, never relative (chat drops relative entries by design,
so a relative report is a silent one).

THE REGISTRY SEAM (reworked v1.166.0, coordinator): ``UndoJournal.action_id``
is the table's PRIMARY KEY (== the ToolInvocation id), so one invocation owns
AT MOST ONE journal row. ``registry._record`` therefore (a) SKIPS post-hoc
created_paths journaling whenever a ``capture_undo`` descriptor already holds
the slot — the capture saw the pre-image and is strictly better information —
and (b) collapses MULTIPLE created paths into one ``files_delete`` envelope
row (``pre_inline {"paths": [...]}``). Before the rework, a reversible tool
with created_paths (or any 2+-path call) raised IntegrityError at commit,
rolling back the ToolInvocation + event while the file stayed on disk (the
invisible-write class, v1.160.0). The registry-path tests below pin both
rules end-to-end.

CREATED means CREATED: tools without a capture report created_paths only for
files this call brought into existence. An overwrite (in-place image_resize,
convert onto an existing target, pixio re-delivery) reports None — its journal
row would say "created → unlink on undo" and undo would DELETE a file the user
had before the call.

Deliberately NOT covered: ``write_document`` / ``excel_edit`` /
``excel_apply_spec`` / ``redact_pii`` — those four are already merged into the
``documents`` payload via ``chat_turn._DOC_WRITING_TOOLS``, and adding
created_paths there would double-report every file.

Tests are mutation-minded: they assert the exact path VALUE (and that the file
really exists), not just that the field is non-empty — and the registry-path
tests count journal rows exactly.
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

import sys
import types
from pathlib import Path

import pytest
from sqlmodel import select

import iron_jarvis.documents as _docs_pkg
import iron_jarvis.documents.batch as _batch_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.documents.pdf_tools import PdfArrangeTool, PdfSplitTool
from iron_jarvis.documents.tools import BatchDocumentsTool, ConvertDocumentTool
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import WriteFileTool
from iron_jarvis.tools.images import ImageConvertTool, ImageResizeTool
from iron_jarvis.tools.pixio import (
    _BASE_URL,
    PixioGenerateTool,
    PixioStatusTool,
)


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws,
        session_id="t",
        agent_run_id="t",
        config=None,
        event_bus=None,
        engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "ws"
    d.mkdir()
    return d


def _assert_created(res, expected: list[str]) -> None:
    """The contract in one place: exact absolute values, every one absolute."""
    assert res.ok is True
    assert res.created_paths == expected
    for p in res.created_paths:
        assert Path(p).is_absolute()
        assert Path(p).is_file()


# --- the registry seam (defects 1+2): invoke() must never IntegrityError ------
#
# These run the FULL path the chat/agent lanes use: permission -> capture_undo
# -> execute -> _record journaling -> event. This is exactly where the original
# P2 change detonated (UNIQUE constraint failed: undojournal.action_id), which
# the 18 direct-execute tests could never see.


def _platform_ctx(tmp_path, ws: Path):
    platform = build_platform(str(tmp_path))
    ws.mkdir(parents=True, exist_ok=True)
    ctx = ToolContext(
        workspace=ws,
        session_id="session_cp1166",
        agent_run_id="run_cp1166",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )
    return platform, ctx


def _journal_rows(platform) -> list[UndoJournal]:
    with session_scope(platform.engine) as db:
        return list(db.exec(select(UndoJournal)).all())


def _invocation_rows(platform, tool: str) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        return list(
            db.exec(select(ToolInvocation).where(ToolInvocation.tool == tool)).all()
        )


async def test_registry_write_file_new_file_ok_and_single_journal_row(tmp_path):
    """write_file through registry.invoke: the capture's file_delete row is the
    ONLY journal row, the invocation is recorded ok, and the result is a
    success — before the fix this raised IntegrityError, rolled everything
    back, and reported a landed write as a failure."""
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    res = await platform.registry.invoke(
        "write_file", {"path": "fresh.txt", "content": "hi\n"}, ctx, platform.permissions
    )
    assert res.ok is True
    assert (tmp_path / "ws" / "fresh.txt").read_text(encoding="utf-8") == "hi\n"
    rows = _journal_rows(platform)
    assert len(rows) == 1
    assert rows[0].kind == "file_delete"  # the CAPTURE's inverse, not a dupe
    assert rows[0].tool == "write_file"
    invs = _invocation_rows(platform, "write_file")
    assert len(invs) == 1 and invs[0].ok is True
    assert rows[0].action_id == invs[0].id  # ledger row survived the commit


async def test_registry_write_file_overwrite_journals_restore_not_delete(tmp_path):
    """Overwriting an existing file journals ONE file_restore row — a second
    (created_paths) file_delete row under the same PK would both collide and,
    semantically, make undo DELETE the file instead of restoring it."""
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    (tmp_path / "ws" / "note.txt").write_text("ORIGINAL\n", encoding="utf-8")
    res = await platform.registry.invoke(
        "write_file", {"path": "note.txt", "content": "NEW\n"}, ctx, platform.permissions
    )
    assert res.ok is True
    rows = _journal_rows(platform)
    assert len(rows) == 1
    assert rows[0].kind == "file_restore"


async def test_registry_pdf_split_two_parts_ok_and_single_journal_row(
    tmp_path, fake_engine
):
    """pdf_split (capture_undo + TWO outputs) through the registry: exactly the
    capture's pdf_split_delete row, no per-part file_delete dupes, ok=True."""
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    (tmp_path / "ws" / "src.pdf").write_bytes(b"%PDF-src")
    res = await platform.registry.invoke(
        "pdf_split",
        {"path": "src.pdf", "per_page": True, "out_dir": "parts"},
        ctx,
        platform.permissions,
    )
    assert res.ok is True, res.error
    assert (tmp_path / "ws" / "parts" / "src-part01.pdf").is_file()
    assert (tmp_path / "ws" / "parts" / "src-part02.pdf").is_file()
    rows = _journal_rows(platform)
    assert len(rows) == 1
    assert rows[0].kind == "pdf_split_delete"
    invs = _invocation_rows(platform, "pdf_split")
    assert len(invs) == 1 and invs[0].ok is True


async def test_registry_pdf_arrange_ok_and_single_journal_row(tmp_path, fake_engine):
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    (tmp_path / "ws" / "in.pdf").write_bytes(b"%PDF-src")
    res = await platform.registry.invoke(
        "pdf_arrange",
        {"inputs": [{"path": "in.pdf"}], "output": "out/merged.pdf"},
        ctx,
        platform.permissions,
    )
    assert res.ok is True, res.error
    assert (tmp_path / "ws" / "out" / "merged.pdf").is_file()
    rows = _journal_rows(platform)
    assert len(rows) == 1
    assert rows[0].kind == "file_delete"  # the capture's inverse for a NEW output


async def test_registry_batch_documents_two_deliverables_one_envelope_row(
    tmp_path, monkeypatch
):
    """batch_documents' default output="both" produces TWO deliverables — before
    the seam rework those were two UndoJournal rows under ONE primary key
    (IntegrityError even with no capture_undo). Now: clean success, ONE
    ``files_delete`` envelope row carrying BOTH paths, and created_paths
    reports both deliverables absolute."""
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    folder = tmp_path / "ws" / "docs"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("doc", encoding="utf-8")

    async def fake_run_batch(
        src, out_dir, router, *, instructions, output, max_files, config
    ):
        out_dir.mkdir(parents=True, exist_ok=True)
        outs = []
        for name in ("summary.docx", "summary.xlsx"):
            p = out_dir / name
            p.write_bytes(b"fake")
            outs.append(str(p))
        return {
            "processed": 1,
            "cached": 0,
            "failed": [],
            "skipped": [],
            "deliverables": outs,
            "synthesis_errors": [],
            "qa": {},
        }

    monkeypatch.setattr(_batch_mod, "run_batch", fake_run_batch)
    res = await platform.registry.invoke(
        "batch_documents",
        {"folder": "docs"},
        ctx,
        platform.permissions,
        session_allow={"batch_documents"},  # not in default policy -> ask tier
    )
    assert res.ok is True, res.error
    # The fake writes into out_dir chosen by the tool; assert against what the
    # tool actually reported, then pin absoluteness + existence + count.
    assert res.created_paths is not None and len(res.created_paths) == 2
    for p in res.created_paths:
        assert Path(p).is_absolute()
        assert Path(p).is_file()
        assert Path(p).name in ("summary.docx", "summary.xlsx")
    rows = _journal_rows(platform)
    assert len(rows) == 1
    assert rows[0].kind == "files_delete"  # ONE envelope row, not two PK dupes
    import json as _json

    env = _json.loads(rows[0].pre_inline or "{}")
    assert sorted(Path(p).name for p in env.get("paths", [])) == [
        "summary.docx",
        "summary.xlsx",
    ]
    invs = _invocation_rows(platform, "batch_documents")
    assert len(invs) == 1 and invs[0].ok is True
    assert rows[0].action_id == invs[0].id


async def test_registry_convert_document_created_path_journals_one_row(tmp_path):
    """The SURVIVING contract path end-to-end: a single-path, no-capture tool
    ships created_paths through the registry — one file_delete row from the
    created_paths loop, result ok, path absolute on the returned result."""
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    (tmp_path / "ws" / "notes.txt").write_text("plain text", encoding="utf-8")
    res = await platform.registry.invoke(
        "convert_document",
        {"source": "notes.txt", "target": "out/notes.md"},
        ctx,
        platform.permissions,
        session_allow={"convert_document"},  # not in default policy -> ask tier
    )
    assert res.ok is True, res.error
    expected = str((tmp_path / "ws" / "out" / "notes.md").resolve())
    assert res.created_paths == [expected]
    rows = _journal_rows(platform)
    assert len(rows) == 1
    assert rows[0].kind == "file_delete"
    assert rows[0].tool == "convert_document"


# --- write_file (builtins) ----------------------------------------------------


async def test_write_file_reports_absolute_created_path(ws):
    """write_file carries created_paths again (seam reworked): the registry
    skips post-hoc journaling under its capture, so the path only feeds chat's
    documents/ArtifactsRail merge."""
    res = await WriteFileTool().execute(
        {"path": "sub/out.txt", "content": "hello"}, _ctx(ws)
    )
    _assert_created(res, [str((ws / "sub" / "out.txt").resolve())])
    assert (ws / "sub" / "out.txt").read_text(encoding="utf-8") == "hello"
    # Existing fields are untouched — only ADDED (packaged-app API stability).
    assert res.data == {"path": "sub/out.txt", "bytes": 5}


async def test_write_file_escape_writes_nothing(ws):
    # write_file has no ok=False return: an escaping path raises out of
    # safe_path BEFORE any write, so no success result (and no created_paths)
    # can ever exist for it.
    with pytest.raises(PermissionError):
        await WriteFileTool().execute(
            {"path": "../outside.txt", "content": "x"}, _ctx(ws)
        )
    assert not (ws.parent / "outside.txt").exists()


# --- convert_document ---------------------------------------------------------


async def test_convert_document_reports_absolute_created_path(ws):
    src = ws / "notes.txt"
    src.write_text("plain text notes", encoding="utf-8")
    res = await ConvertDocumentTool().execute(
        {"source": "notes.txt", "target": "out/notes.md"}, _ctx(ws)
    )
    expected = str((ws / "out" / "notes.md").resolve())
    _assert_created(res, [expected])
    assert res.data["abs_path"] == expected  # the two reports agree


async def test_convert_document_failure_has_no_created_paths(ws):
    src = ws / "notes.txt"
    src.write_text("x", encoding="utf-8")
    res = await ConvertDocumentTool().execute(
        {"source": "notes.txt", "target": "out.xyz"}, _ctx(ws)
    )
    assert res.ok is False
    assert res.created_paths is None


# --- batch_documents ----------------------------------------------------------


async def test_batch_documents_reports_absolute_deliverables(ws, monkeypatch):
    """Deliverables reach created_paths as ABSOLUTE paths, captured before the
    display-side workspace-relativization rewrites result["deliverables"]."""
    folder = ws / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("doc", encoding="utf-8")
    written: dict[str, Path] = {}

    async def fake_run_batch(
        src, out_dir, router, *, instructions, output, max_files, config
    ):
        out_dir.mkdir(parents=True, exist_ok=True)
        deliverable = out_dir / "summary.docx"
        deliverable.write_bytes(b"fake-docx")
        written["deliverable"] = deliverable
        return {
            "processed": 1,
            "cached": 0,
            "failed": [],
            "skipped": [],
            "deliverables": [str(deliverable)],
            "synthesis_errors": [],
            "qa": {},
        }

    monkeypatch.setattr(_batch_mod, "run_batch", fake_run_batch)
    res = await BatchDocumentsTool(lambda: object()).execute(
        {"folder": "docs"}, _ctx(ws)
    )
    _assert_created(res, [str(written["deliverable"].resolve())])
    # data["deliverables"] stays workspace-relative (existing behavior).
    assert res.data["deliverables"] == [
        str(written["deliverable"].resolve().relative_to(ws.resolve())).replace(
            "\\", "/"
        )
    ]


async def test_batch_documents_failure_has_no_created_paths(ws):
    (ws / "not-a-folder.txt").write_text("x", encoding="utf-8")
    res = await BatchDocumentsTool(lambda: object()).execute(
        {"folder": "not-a-folder.txt"}, _ctx(ws)
    )
    assert res.ok is False
    assert res.created_paths is None


# --- pdf_arrange / pdf_split (fake engine, same seam as test_pdf_tools) -------


@pytest.fixture
def fake_engine(monkeypatch):
    """Fake ``iron_jarvis.documents.pdf_pages`` that writes real files —
    the tools lazy-import it, so patch both the sys.modules entry and the
    package attribute (the latter wins once the real module was imported)."""
    mod = types.ModuleType("iron_jarvis.documents.pdf_pages")

    class ArrangeInput:
        def __init__(self, path, pages_spec="all", password=None):
            self.path = path
            self.pages_spec = pages_spec
            self.password = password

    def arrange(inputs, out_path, *, crop=None, encrypt_password=None, metadata=None):
        Path(out_path).write_bytes(b"%PDF-fake-arranged")
        return {
            "path": str(out_path),
            "pages": 7,
            "inputs": [{"path": i.path, "pages": 3} for i in inputs],
        }

    def split(path, out_dir, *, mode, password=None):
        outs = []
        for i in (1, 2):
            p = Path(out_dir) / f"{Path(path).stem}-part0{i}.pdf"
            p.write_bytes(b"%PDF-fake-part")
            outs.append({"path": str(p), "pages": i})
        return {"outputs": outs}

    mod.ArrangeInput = ArrangeInput
    mod.arrange = arrange
    mod.split = split
    monkeypatch.setitem(sys.modules, "iron_jarvis.documents.pdf_pages", mod)
    monkeypatch.setattr(_docs_pkg, "pdf_pages", mod, raising=False)
    return mod


async def test_pdf_arrange_reports_absolute_created_path(ws, fake_engine):
    """pdf_arrange carries created_paths again (seam reworked): its capture
    owns the journal slot, so the path only feeds the chat documents merge."""
    (ws / "in.pdf").write_bytes(b"%PDF-src")
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": "in.pdf"}], "output": "out/merged.pdf"}, _ctx(ws)
    )
    _assert_created(res, [str((ws / "out" / "merged.pdf").resolve())])


async def test_pdf_arrange_failure_has_no_created_paths(ws, fake_engine):
    (ws / "in.pdf").write_bytes(b"%PDF-src")
    res = await PdfArrangeTool().execute(
        {"inputs": [{"path": "in.pdf"}], "output": "merged.txt"}, _ctx(ws)
    )
    assert res.ok is False
    assert res.created_paths is None


async def test_pdf_split_reports_every_part_absolute(ws, fake_engine):
    """pdf_split carries EVERY engine-verified part (seam reworked)."""
    (ws / "src.pdf").write_bytes(b"%PDF-src")
    res = await PdfSplitTool().execute(
        {"path": "src.pdf", "per_page": True, "out_dir": "parts"}, _ctx(ws)
    )
    _assert_created(
        res,
        [
            str((ws / "parts" / "src-part01.pdf").resolve()),
            str((ws / "parts" / "src-part02.pdf").resolve()),
        ],
    )


async def test_pdf_split_failure_has_no_created_paths(ws, fake_engine):
    (ws / "src.pdf").write_bytes(b"%PDF-src")
    res = await PdfSplitTool().execute({"path": "src.pdf"}, _ctx(ws))  # no mode
    assert res.ok is False
    assert res.created_paths is None


# --- image_convert / image_resize (real Pillow) -------------------------------


def _png(path: Path, size=(32, 16)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (255, 0, 0, 255)).save(path, format="PNG")
    return path


async def test_image_convert_reports_absolute_created_path(ws):
    _png(ws / "pic.png")
    res = await ImageConvertTool().execute(
        {"source": "pic.png", "target": "out/pic.jpg"}, _ctx(ws)
    )
    _assert_created(res, [str((ws / "out" / "pic.jpg").resolve())])


async def test_image_convert_failure_has_no_created_paths(ws):
    _png(ws / "pic.png")
    res = await ImageConvertTool().execute(
        {"source": "pic.png", "target": "pic.xyz"}, _ctx(ws)
    )
    assert res.ok is False
    assert res.created_paths is None


async def test_image_resize_reports_absolute_created_path(ws):
    _png(ws / "big.png", size=(64, 64))
    res = await ImageResizeTool().execute(
        {"source": "big.png", "target": "small.png", "max_width": 16}, _ctx(ws)
    )
    _assert_created(res, [str((ws / "small.png").resolve())])


async def test_image_resize_overwrite_source_reports_no_created_paths(ws):
    # Default target = overwrite the SOURCE in place. That is a modification,
    # not a creation — reporting it under created_paths would journal a
    # "created → unlink on undo" row pointed at the user's original image.
    _png(ws / "big.png", size=(64, 64))
    res = await ImageResizeTool().execute(
        {"source": "big.png", "max_width": 16}, _ctx(ws)
    )
    assert res.ok is True
    assert res.created_paths is None
    assert (ws / "big.png").is_file()  # the resize itself still happened


async def test_image_resize_failure_has_no_created_paths(ws):
    _png(ws / "big.png")
    res = await ImageResizeTool().execute({"source": "big.png"}, _ctx(ws))
    assert res.ok is False  # neither max_width nor max_height
    assert res.created_paths is None


# --- pixio media download (shared _deliver) -----------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _ScriptedHttp:
    def __init__(self, script):
        self._script = dict(script)

    def __call__(self, method, url, headers, json_body):
        path = url[len(_BASE_URL):] if url.startswith(_BASE_URL) else url
        return self._script[(method, path)]


_KEY = "pxio_live_test"


def _pixio_http(gen_id: str) -> _ScriptedHttp:
    return _ScriptedHttp(
        {
            ("GET", f"/api/v1/generations/{gen_id}"): _FakeResponse(
                200,
                {
                    "status": "succeeded",
                    "outputUrl": f"https://cdn.pixio.test/out/{gen_id}.png",
                },
            ),
            ("GET", f"https://cdn.pixio.test/out/{gen_id}.png"): _FakeResponse(
                200, content=b"\x89PNG fake"
            ),
        }
    )


async def test_pixio_status_delivery_reports_absolute_created_path(ws):
    tool = PixioStatusTool(key_resolver=lambda: _KEY, http=_pixio_http("gen_2"))
    res = await tool.execute({"generation_id": "gen_2"}, _ctx(ws))
    _assert_created(res, [str((ws / "pixio" / "gen_2.png").resolve())])
    assert res.created_paths == [res.data["abs_path"]]  # the two reports agree


async def test_pixio_delivery_absolute_even_with_relative_workspace(
    tmp_path, monkeypatch
):
    """Defect pin (review, v1.166.0): dest was ``ctx.workspace / rel`` UNresolved
    — absolute only because every current caller passes an absolute workspace.
    A relative workspace made chat silently drop the path (relative entries are
    discarded by design). Both reports must be absolute and byte-identical."""
    monkeypatch.chdir(tmp_path)
    rel_ws = Path("wsrel")  # deliberately RELATIVE
    rel_ws.mkdir()
    tool = PixioStatusTool(key_resolver=lambda: _KEY, http=_pixio_http("gen_4"))
    res = await tool.execute({"generation_id": "gen_4"}, _ctx(rel_ws))
    expected = str((tmp_path / "wsrel" / "pixio" / "gen_4.png").resolve())
    _assert_created(res, [expected])
    assert res.data["abs_path"] == expected  # byte-identical, both resolved


async def test_pixio_failed_download_has_no_created_paths(ws):
    http = _ScriptedHttp(
        {
            ("GET", "/api/v1/generations/gen_3"): _FakeResponse(
                200,
                {"status": "succeeded", "outputUrl": "https://cdn.pixio.test/out/gen_3.png"},
            ),
            ("GET", "https://cdn.pixio.test/out/gen_3.png"): _FakeResponse(500),
        }
    )
    tool = PixioStatusTool(key_resolver=lambda: _KEY, http=http)
    res = await tool.execute({"generation_id": "gen_3"}, _ctx(ws))
    assert res.ok is False
    assert res.created_paths is None


async def test_pixio_generate_pending_has_no_created_paths(ws):
    # wait=false writes nothing — created_paths must stay absent even though
    # the call SUCCEEDS (success-only is about the write, not the HTTP call).
    http = _ScriptedHttp(
        {("POST", "/api/v1/generate"): _FakeResponse(200, {"id": "gen_9"})}
    )
    tool = PixioGenerateTool(key_resolver=lambda: _KEY, http=http, poll_seconds=0)
    res = await tool.execute(
        {"model_id": "pixio/flux", "params": {"prompt": "x"}, "wait": False}, _ctx(ws)
    )
    assert res.ok is True
    assert res.created_paths is None


# --- overwrite honesty (v1.166.0 coordinator): CREATED means created ----------


async def test_image_convert_overwrite_target_reports_no_created_paths(ws):
    """Converting onto an EXISTING target is an overwrite, not a creation —
    a "created" journal row there would let undo unlink a pre-existing file."""
    _png(ws / "pic.png")
    _png(ws / "out" / "pic.jpg")  # target already exists
    res = await ImageConvertTool().execute(
        {"source": "pic.png", "target": "out/pic.jpg"}, _ctx(ws)
    )
    assert res.ok is True
    assert res.created_paths is None


async def test_convert_document_overwrite_target_reports_no_created_paths(ws):
    (ws / "notes.txt").write_text("plain text", encoding="utf-8")
    out = ws / "out" / "notes.md"
    out.parent.mkdir(parents=True)
    out.write_text("OLD", encoding="utf-8")
    res = await ConvertDocumentTool().execute(
        {"source": "notes.txt", "target": "out/notes.md"}, _ctx(ws)
    )
    assert res.ok is True
    assert res.created_paths is None
    assert res.data["abs_path"] == str(out.resolve())  # location still reported


# --- the files_delete envelope reverts: unlink every created path -------------


async def test_files_delete_envelope_revert_unlinks_all(ws):
    import json as _json

    from iron_jarvis.tools.undo import revert_workspace_file

    a = ws / "made-a.txt"
    b = ws / "sub" / "made-b.txt"
    b.parent.mkdir(parents=True)
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    class _Cfg:
        home = str(ws)

    ctx = ToolContext(
        workspace=ws,
        session_id="t",
        agent_run_id="t",
        config=_Cfg(),
        event_bus=None,
        engine=None,
    )
    undo = {
        "kind": "files_delete",
        "reversible": True,
        "pre_ref": None,
        "pre_inline": _json.dumps(
            {"paths": ["made-a.txt", "sub/made-b.txt"], "mode": "raw"}
        ),
        "pre_sha256": None,
        "post_sha256": None,
    }
    res = await revert_workspace_file(undo, ctx)
    assert res.ok is True, res.error
    assert "2" in res.output  # says how many it removed
    assert not a.exists() and not b.exists()


async def test_files_delete_envelope_refuses_escaping_path(ws):
    import json as _json

    from iron_jarvis.tools.undo import revert_workspace_file

    outside = ws.parent / "outside-target.txt"
    outside.write_text("precious", encoding="utf-8")

    class _Cfg:
        home = str(ws)

    ctx = ToolContext(
        workspace=ws,
        session_id="t",
        agent_run_id="t",
        config=_Cfg(),
        event_bus=None,
        engine=None,
    )
    undo = {
        "kind": "files_delete",
        "reversible": True,
        "pre_ref": None,
        "pre_inline": _json.dumps({"paths": ["../outside-target.txt"], "mode": "raw"}),
        "pre_sha256": None,
        "post_sha256": None,
    }
    res = await revert_workspace_file(undo, ctx)
    assert res.ok is False
    assert outside.read_text(encoding="utf-8") == "precious"  # untouched


async def test_failed_capture_on_reversible_tool_journals_nothing(
    tmp_path, monkeypatch
):
    """v1.167.0: for a REVERSIBLE tool, `undo is None` at _record means the
    capture FAILED (disk full, blob-store error). That must degrade to NO undo
    row — before the gate, the post-hoc branch journaled the overwrite as
    "file_delete" ("created → unlink on undo") and undoing the overwrite
    DELETED the user's only remaining copy instead of refusing."""
    platform, ctx = _platform_ctx(tmp_path, tmp_path / "ws")
    target = tmp_path / "ws" / "note.txt"
    target.write_text("ORIGINAL CLIENT DATA", encoding="utf-8")

    async def broken_capture(self, args, c):
        raise OSError("undo blob store unavailable")

    monkeypatch.setattr(WriteFileTool, "capture_undo", broken_capture)
    res = await platform.registry.invoke(
        "write_file", {"path": "note.txt", "content": "NEW"}, ctx, platform.permissions
    )
    assert res.ok is True  # the write itself still lands (capture never blocks)
    assert target.read_text(encoding="utf-8") == "NEW"
    rows = _journal_rows(platform)
    assert rows == [], (
        "a failed capture must journal NOTHING for a reversible tool — "
        f"got {[(r.kind, r.tool) for r in rows]} (the fabricated-inverse bug)"
    )
    invs = _invocation_rows(platform, "write_file")
    assert len(invs) == 1 and invs[0].ok is True  # the ledger row survived
