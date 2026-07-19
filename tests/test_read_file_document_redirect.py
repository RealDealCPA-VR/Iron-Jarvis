"""read_file must REDIRECT on documents, never dead-end (live-hit 2026-07-19).

A user added .docx files to a project folder and asked about them. The model
called ``read_file``, got a bare ``UnicodeDecodeError: 'utf-8' codec can't
decode byte 0xf2``, and — with an error it could not act on — told the user the
files were "binary .docx files that the filter blocked during document
parsing". There is no such filter, and ``read_document`` extracts all three
files perfectly. An unactionable error is what invites a fabricated
explanation, so the fix is an error that names the next step.
"""

from __future__ import annotations

import asyncio
import zipfile

from iron_jarvis.tools.base import ToolContext, safe_path
from iron_jarvis.tools.builtins import ReadFileTool


def _ctx(tmp_path):
    class _Cfg:
        pass

    return ToolContext(
        workspace=tmp_path,
        session_id="t",
        agent_run_id="t",
        config=_Cfg(),
        event_bus=None,
        engine=None,
    )


def _read(tmp_path, name):
    return asyncio.run(ReadFileTool().execute({"path": name}, _ctx(tmp_path)))


def _docx(path):
    """A real (minimal) .docx — a zip, so genuinely not UTF-8 decodable."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:t>hi</w:t></w:document>")


def test_docx_is_redirected_to_read_document(tmp_path):
    _docx(tmp_path / "notes.docx")
    res = _read(tmp_path, "notes.docx")
    assert res.ok is False
    assert "read_document" in res.error
    assert "Word" in res.error
    # The model must not burn its remaining rounds re-trying the same call.
    assert "Do not retry" in res.error


def test_every_office_format_points_at_read_document(tmp_path):
    for name in ("a.pdf", "b.xlsx", "c.pptx", "d.doc", "e.rtf", "f.odt"):
        (tmp_path / name).write_bytes(b"\x00\xf2binary")
        res = _read(tmp_path, name)
        assert res.ok is False, name
        assert "read_document" in res.error, name


def test_binary_without_a_known_extension_still_redirects(tmp_path):
    """The extension check is the fast path; undecodable bytes are caught too —
    the model still gets a next step instead of a decode traceback."""
    (tmp_path / "mystery.dat").write_bytes(b"\xff\xfe\x00\x01\xf2\xf3")
    res = _read(tmp_path, "mystery.dat")
    assert res.ok is False
    assert "read_document" in res.error
    assert "codec" not in res.error  # no raw UnicodeDecodeError leaks out


def test_plain_text_still_reads_normally(tmp_path):
    (tmp_path / "notes.md").write_text("# hello\nworld", encoding="utf-8")
    res = _read(tmp_path, "notes.md")
    assert res.ok is True
    assert res.output == "# hello\nworld"


def test_missing_file_keeps_its_own_error(tmp_path):
    res = _read(tmp_path, "nope.txt")
    assert res.ok is False
    assert "no such file" in res.error


def test_description_steers_toward_read_document():
    """The description is the model's first chance to choose correctly."""
    desc = ReadFileTool.description
    assert "read_document" in desc
    assert "TEXT" in desc or "text" in desc


def test_a_directory_is_not_reported_as_a_document(tmp_path):
    (tmp_path / "sub").mkdir()
    res = _read(tmp_path, "sub")
    assert res.ok is False
    assert "no such file" in res.error


def test_chat_tool_round_budget_fits_real_document_work():
    """Explore -> correct a wrong tool choice -> read -> answer needs more than
    three executing rounds; the live report ran out mid-task."""
    from iron_jarvis.daemon.routes.chat import _MAX_TOOL_ROUNDS

    assert _MAX_TOOL_ROUNDS >= 6
