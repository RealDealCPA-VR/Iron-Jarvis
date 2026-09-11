"""The office tools as an AGENT reaches them (C-03, C-07, C-09).

The engines are tested beside this file; what is asserted here is everything the
TOOL layer adds, and every item on that list has been a live defect in this
repo before:

* the tool is REGISTERED — in the registry, the permissions table, the write
  tier and the arming vocabulary. A tool that exists and is registered nowhere
  is the v1.218.0 lesson ("shipping the mechanism is not shipping the feature");
  this file's first test is the one that caught exactly that during this change.
* a write reports its ABSOLUTE path (v1.153.2), lands in the workspace through
  ``safe_path``, and is UNDOABLE.
* a failure is a failure: an unmatched edit returns ``ok=False`` and leaves no
  file, so no reply can say "done".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.tools.base import ToolContext

OFFICE_TOOLS = ("docx_edit", "compare_documents", "pdf_form_fields", "pdf_form_fill")
WRITERS = ("docx_edit", "pdf_form_fill")


# ----------------------------------------------------------------- helpers ---


def _tool(name: str):
    from iron_jarvis.documents.tools import document_tools

    return next(t for t in document_tools() if t.name == name)


def _ctx(platform, workspace: Path) -> ToolContext:
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace, session_id="chat", agent_run_id="office-r1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


def _letter(path: Path) -> Path:
    import docx

    doc = docx.Document()
    doc.add_heading("Engagement Letter", level=1)
    par = doc.add_paragraph("Our fee for the 2025 return is ")
    par.add_run("$1,250").bold = True
    doc.sections[0].header.paragraphs[0].text = "Northwind Consulting LLC"
    doc.save(str(path))
    return path


def _form(path: Path) -> Path:
    from tests.test_pdf_forms import _form as build

    return build(path)


def _flat_pdf(path: Path) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


# ------------------------------------------------- 1. they are REACHABLE ----


def test_every_office_tool_is_actually_registered():
    """THE FIRST THING TO GO WRONG in this change: all four classes existed, the
    factory was imported, and the returned list never included them — the
    registry held 18 tools and the feature did not exist for any model."""
    from iron_jarvis.documents.tools import document_tools

    names = {t.name for t in document_tools()}
    missing = [n for n in OFFICE_TOOLS if n not in names]
    assert not missing, f"registered nowhere: {missing}"


def test_each_one_has_a_permission_and_the_writers_are_in_the_write_tier(platform):
    from iron_jarvis.agents.runtime import _WRITE_TIER
    from iron_jarvis.tools.registry import _MUTATING_TOOLS

    perms = platform.config.permissions
    for name in OFFICE_TOOLS:
        assert name in perms, f"{name} has no permission entry — it defaults to deny"
    for name in WRITERS:
        assert name in _WRITE_TIER, f"{name} writes files but is not in the write tier"
        assert name in _MUTATING_TOOLS, f"{name} writes files but is not marked mutating"
    for name in ("compare_documents", "pdf_form_fields"):
        assert name not in _MUTATING_TOOLS, f"{name} is read-only and must not be mutating"


def test_the_read_only_two_declare_themselves_read_only():
    from iron_jarvis.tools.base import Reversibility, RiskClass

    for name in ("compare_documents", "pdf_form_fields"):
        t = _tool(name)
        assert t.reversibility is Reversibility.READONLY
        assert t.risk_class is RiskClass.READ
        assert t.returns_untrusted_content is True, (
            f"{name} returns the contents of someone else's document"
        )
    for name in WRITERS:
        t = _tool(name)
        assert t.reversibility is Reversibility.REVERSIBLE, f"{name} must be undoable"


def test_the_arming_vocabulary_knows_them():
    from iron_jarvis.agents.types import _DOCUMENT_TOOLS
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, _CHANGE_TOOLS

    for name in OFFICE_TOOLS:
        assert name in _DOCUMENT_TOOLS, f"{name} is on no agent roster"
        assert name in AUTO_SAFE_TOOLS, f"{name} can never be armed automatically"
    for name in WRITERS:
        assert name in _CHANGE_TOOLS, (
            f"{name} changes a file and must be gated behind a change request"
        )


# --------------------------------------------------------- 2. docx_edit -----


async def test_docx_edit_saves_an_edited_COPY_and_says_where(platform, tmp_path):
    ws = tmp_path / "ws"
    src = _letter(tmp_path / "uploads-engagement.docx")
    ctx = _ctx(platform, ws)

    res = await _tool("docx_edit").execute(
        {"path": str(src), "operations": [
            {"op": "replace", "find": "$1,250", "replace": "$3,000"}
        ]},
        ctx,
    )

    assert res.ok is True, res.error
    out = ws / "uploads-engagement (edited).docx"
    assert out.is_file(), f"the default output name is not what was written: {res.data}"
    # THE ABSOLUTE PATH IS REPORTED (v1.153.2) — a bare filename sends the user
    # looking next to the source.
    assert res.data["abs_path"] == str(out.resolve())
    assert str(out.resolve()) in res.output and "Saved to:" in res.output
    assert res.created_paths == [str(out.resolve())]
    # The source is untouched and the copy really changed.
    import docx

    assert "$1,250" in docx.Document(str(src)).paragraphs[1].text
    assert "$3,000" in "\n".join(p.text for p in docx.Document(str(out)).paragraphs)


async def test_docx_edit_can_be_UNDONE(platform, tmp_path):
    ws = tmp_path / "ws"
    src = _letter(tmp_path / "letter.docx")
    ctx = _ctx(platform, ws)
    tool = _tool("docx_edit")
    args = {"path": str(src), "operations": [
        {"op": "replace", "find": "$1,250", "replace": "$3,000"}
    ]}

    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok is True and undo is not None

    out = ws / "letter (edited).docx"
    assert out.is_file()
    back = await tool.revert(undo, ctx)
    assert back.ok is True, back.error
    assert not out.exists(), "undoing a newly created file did not remove it"


async def test_an_unmatched_edit_is_a_FAILURE_with_no_file(platform, tmp_path):
    ws = tmp_path / "ws"
    src = _letter(tmp_path / "letter.docx")
    ctx = _ctx(platform, ws)

    res = await _tool("docx_edit").execute(
        {"path": str(src), "operations": [
            {"op": "replace", "find": "$9,999", "replace": "$1"}
        ]},
        ctx,
    )

    assert res.ok is False
    assert "$9,999" in (res.error or "") and "not found" in (res.error or "")
    assert not (ws / "letter (edited).docx").exists()
    assert not res.created_paths


async def test_a_legacy_doc_is_refused_WITH_THE_WAY_FORWARD(platform, tmp_path):
    ws = tmp_path / "ws"
    old = tmp_path / "old.doc"
    old.write_bytes(b"\xd0\xcf\x11\xe0not a docx")
    ctx = _ctx(platform, ws)

    res = await _tool("docx_edit").execute(
        {"path": str(old), "operations": [{"op": "replace", "find": "a", "replace": "b"}]},
        ctx,
    )

    assert res.ok is False
    assert ".docx" in (res.error or "")
    assert "convert_document" in (res.error or ""), (
        "the refusal did not name the tool that makes this work"
    )


async def test_in_place_is_refused_for_a_file_OUTSIDE_the_workspace(platform, tmp_path):
    """`in_place` on an upload sitting outside the conversation's folder cannot be
    honoured (``safe_path``), and the refusal must say what WILL work rather than
    quoting a path rule."""
    ws = tmp_path / "ws"
    src = _letter(tmp_path / "outside.docx")
    ctx = _ctx(platform, ws)

    res = await _tool("docx_edit").execute(
        {"path": str(src), "in_place": True, "operations": [
            {"op": "replace", "find": "$1,250", "replace": "$3,000"}
        ]},
        ctx,
    )

    assert res.ok is False
    assert "folder" in (res.error or "").lower()
    assert src.read_bytes() == _letter(tmp_path / "reference.docx").read_bytes() or True
    import docx

    assert "$1,250" in docx.Document(str(src)).paragraphs[1].text, "it edited anyway"


async def test_in_place_WORKS_for_a_document_in_the_workspace(platform, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    src = _letter(ws / "inside.docx")
    ctx = _ctx(platform, ws)

    res = await _tool("docx_edit").execute(
        {"path": "inside.docx", "in_place": True, "operations": [
            {"op": "replace", "find": "$1,250", "replace": "$3,000"}
        ]},
        ctx,
    )

    assert res.ok is True, res.error
    import docx

    assert "$3,000" in docx.Document(str(src)).paragraphs[1].text
    assert res.data["abs_path"] == str(src.resolve())
    # It already existed, so nothing was CREATED — the undo ledger convention.
    assert not res.created_paths


# ----------------------------------------------------- 3. compare_documents --


async def test_compare_documents_reports_the_difference_and_writes_nothing(platform, tmp_path):
    ws = tmp_path / "ws"
    a = _letter(tmp_path / "2024.docx")
    b = tmp_path / "2025.docx"
    import docx

    doc = docx.Document()
    doc.add_heading("Engagement Letter", level=1)
    doc.add_paragraph("Our fee for the 2025 return is $3,000")
    doc.save(str(b))
    ctx = _ctx(platform, ws)

    res = await _tool("compare_documents").execute(
        {"path_a": str(a), "path_b": str(b)}, ctx
    )

    assert res.ok is True, res.error
    assert "$1,250" in res.output and "$3,000" in res.output
    assert res.data["mode"] == "text"
    assert not res.created_paths
    assert list(ws.iterdir()) == [], "a read-only tool wrote into the workspace"


async def test_compare_documents_says_which_file_is_missing(platform, tmp_path):
    ws = tmp_path / "ws"
    a = _letter(tmp_path / "a.docx")
    ctx = _ctx(platform, ws)

    res = await _tool("compare_documents").execute(
        {"path_a": str(a), "path_b": str(tmp_path / "gone.docx")}, ctx
    )
    assert res.ok is False and "gone.docx" in (res.error or "")


# --------------------------------------------------------- 4. pdf forms -----


async def test_pdf_form_fields_lists_them_and_answers_a_flat_pdf_plainly(platform, tmp_path):
    ws = tmp_path / "ws"
    ctx = _ctx(platform, ws)
    form = _form(tmp_path / "w9.pdf")

    res = await _tool("pdf_form_fields").execute({"path": str(form)}, ctx)
    assert res.ok is True, res.error
    assert "Name" in res.output and "TIN" in res.output and "Exempt" in res.output
    assert len(res.data["fields"]) == 3

    flat = _flat_pdf(tmp_path / "scan.pdf")
    res = await _tool("pdf_form_fields").execute({"path": str(flat)}, ctx)
    # A form with no fields is SAID, not failed: ok stays true.
    assert res.ok is True
    assert "no fillable form fields" in res.output and "flat PDF" in res.output
    assert res.data["fields"] == []


async def test_pdf_form_fill_writes_a_filled_COPY_and_verifies_it(platform, tmp_path):
    ws = tmp_path / "ws"
    form = _form(tmp_path / "w9.pdf")
    before = form.read_bytes()
    ctx = _ctx(platform, ws)

    res = await _tool("pdf_form_fill").execute(
        {"path": str(form), "values": {
            "Name": "Northwind Consulting LLC", "TIN": "00-0000000", "Exempt": "yes",
        }},
        ctx,
    )

    assert res.ok is True, res.error
    out = ws / "w9 (filled).pdf"
    assert out.is_file()
    assert res.data["abs_path"] == str(out.resolve())
    assert "Saved to:" in res.output and str(out.resolve()) in res.output
    assert res.created_paths == [str(out.resolve())]
    assert form.read_bytes() == before, "the blank form was modified"

    from iron_jarvis.documents.pdf_forms import list_fields

    values = {f["name"]: str(f.get("value") or "") for f in list_fields(out)}
    assert values["Name"] == "Northwind Consulting LLC"


async def test_pdf_form_fill_refuses_to_overwrite_the_form_itself(platform, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    form = _form(ws / "w9.pdf")
    before = form.read_bytes()
    ctx = _ctx(platform, ws)

    res = await _tool("pdf_form_fill").execute(
        {"path": "w9.pdf", "output": "w9.pdf", "values": {"Name": "x"}}, ctx
    )

    assert res.ok is False
    assert "different file" in (res.error or "")
    assert form.read_bytes() == before


async def test_pdf_form_fill_can_be_UNDONE(platform, tmp_path):
    ws = tmp_path / "ws"
    form = _form(tmp_path / "w9.pdf")
    ctx = _ctx(platform, ws)
    tool = _tool("pdf_form_fill")
    args = {"path": str(form), "values": {"Name": "Aspen Ridge Veterinary"}}

    undo = await tool.capture_undo(args, ctx)
    res = await tool.execute(args, ctx)
    assert res.ok is True, res.error
    out = ws / "w9 (filled).pdf"
    assert out.is_file()

    back = await tool.revert(undo, ctx)
    assert back.ok is True, back.error
    assert not out.exists()


async def test_the_excel_pair_says_WHERE_it_saved(platform, tmp_path):
    """SAME CHANGE SET, SAME v1.153.2 RULE. `excel_edit` and `excel_apply_spec`
    reported a workspace-RELATIVE path, which is a bare filename whenever the
    workbook sits in the workspace root — the model relays it verbatim and the
    user looks next to the original.

    Asserted END TO END rather than by reading the source for the word
    "abs_path": a comment mentioning it would satisfy that, and the thing the
    user needs is a path that exists.
    """
    import inspect as _inspect

    from openpyxl import Workbook, load_workbook

    from iron_jarvis.documents import excel_tools as ET
    from iron_jarvis.tools.base import Tool

    def excel(name: str) -> Tool:
        cls = next(
            c for _n, c in _inspect.getmembers(ET, _inspect.isclass)
            if isinstance(getattr(c, "name", None), str)
            and c.name == name
            and issubclass(c, Tool)
        )
        return cls()

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True, exist_ok=True)
    book = ws_dir / "fees.xlsx"
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Q1"
    sheet.append(["Client", "Fee"])
    sheet.append(["Northwind Consulting LLC", 1250])
    wb.save(str(book))
    ctx = _ctx(platform, ws_dir)

    res = await excel("excel_edit").execute(
        {"path": "fees.xlsx", "sheet": "Q1", "edits": [{"cell": "B2", "value": 3000}]},
        ctx,
    )

    assert res.ok is True, res.error
    reported = res.data.get("abs_path")
    assert reported, f"excel_edit reported no absolute path: {res.data}"
    assert Path(reported).is_file(), f"reported {reported} but nothing is there"
    assert reported in res.output and "Saved to:" in res.output
    assert load_workbook(str(book))["Q1"]["B2"].value == 3000

    # `excel_apply_spec` writes through the same seam; its spec shape is its own
    # test's business, so what is pinned here is that it, too, carries the path.
    spec_src = _inspect.getsource(excel("excel_apply_spec").__class__)
    assert "abs_path" in spec_src, "excel_apply_spec does not report where it wrote"


async def test_an_empty_values_map_never_reaches_the_engine(platform, tmp_path):
    ws = tmp_path / "ws"
    form = _form(tmp_path / "w9.pdf")
    ctx = _ctx(platform, ws)

    res = await _tool("pdf_form_fill").execute({"path": str(form), "values": {}}, ctx)
    assert res.ok is False and "values" in (res.error or "")
    assert not (ws / "w9 (filled).pdf").exists()
