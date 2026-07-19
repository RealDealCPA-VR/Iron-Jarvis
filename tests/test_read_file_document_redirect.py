"""Reading a document must JUST WORK, whichever read tool is used.

Live-hit 2026-07-19: a user added .docx files to a project folder and asked
about them. The model called ``read_file``, got a bare ``UnicodeDecodeError:
'utf-8' codec can't decode byte 0xf2``, and — with an error it could not act
on — told the user the files were "binary .docx files that the filter blocked
during document parsing". There is no such filter, and the documents extract
perfectly.

The first fix was a redirect error naming ``read_document``. The better fix,
and the one here, is for ``read_file`` to simply extract the text itself: the
user's expectation is that the app reads their office documents, and making
that contingent on the model choosing the right tool is a trap it already fell
into. The extracted text is LABELLED, because it is a lossy read-only view —
writing it back with ``write_file`` would destroy the real document.
"""

from __future__ import annotations

import asyncio
import zipfile

import pytest

from iron_jarvis.documents import write_document
from iron_jarvis.tools.base import ToolContext
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


# --- the office formats an office app must handle ------------------------------


@pytest.mark.parametrize(
    "name,content,fmt",
    [
        ("notes.docx", "# Heading\n\nWord body text.", "Word"),
        ("book.xlsx", [["Client", "Fee"], ["Acme", 1200]], "Excel"),
        ("deck.pptx", "# Slide one\n\nDeck body.", "PowerPoint"),
        ("report.pdf", "# PDF title\n\nPDF body text.", "PDF"),
    ],
)
def test_read_file_extracts_real_office_documents(tmp_path, name, content, fmt):
    write_document(str(tmp_path / name), content)
    res = _read(tmp_path, name)
    assert res.ok is True, res.error
    assert res.data["extracted"] is True
    assert res.data["format"] == fmt
    # The label is load-bearing: this view cannot be written back with write_file.
    assert "read-only view" in res.output
    assert "write_document" in res.output


def test_extracted_text_carries_the_documents_real_content(tmp_path):
    write_document(str(tmp_path / "brief.docx"), "# Q3 Review\n\nRevenue rose 12%.")
    out = _read(tmp_path, "brief.docx").output
    assert "Q3 Review" in out and "Revenue rose 12%" in out


def test_a_zip_backed_docx_is_never_reported_as_a_decode_error(tmp_path):
    """The exact live failure: a real .docx is a zip, so plain UTF-8 decoding
    blows up. No raw codec error may ever reach a model again — that is what it
    turned into an invented 'filter'."""
    path = tmp_path / "hand-made.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:t>hi</w:t></w:document>")
    res = _read(tmp_path, "hand-made.docx")
    assert "codec" not in (res.error or "")
    assert "0xf2" not in (res.error or "")
    if not res.ok:  # a malformed docx may legitimately fail — but nameably
        assert "cannot read" in res.error


# --- plain text is untouched ----------------------------------------------------


@pytest.mark.parametrize("name", ["notes.md", "a.txt", "cfg.json", "script.py"])
def test_plain_text_files_are_read_verbatim_with_no_label(tmp_path, name):
    (tmp_path / name).write_text("# hello\nworld", encoding="utf-8")
    res = _read(tmp_path, name)
    assert res.ok is True
    assert res.output == "# hello\nworld"  # byte-for-byte, no extraction banner
    assert not res.data.get("extracted")


def test_missing_file_keeps_its_own_error(tmp_path):
    res = _read(tmp_path, "nope.txt")
    assert res.ok is False and "no such file" in res.error


def test_a_directory_is_not_treated_as_a_document(tmp_path):
    (tmp_path / "sub").mkdir()
    res = _read(tmp_path, "sub")
    assert res.ok is False and "no such file" in res.error


def test_unknown_binary_fails_nameably_not_with_a_traceback(tmp_path):
    (tmp_path / "mystery.dat").write_bytes(b"\xff\xfe\x00\x01\xf2\xf3")
    res = _read(tmp_path, "mystery.dat")
    if not res.ok:
        assert "cannot read" in res.error
        assert "codec" not in res.error


def test_description_tells_the_model_documents_are_handled():
    desc = ReadFileTool.description
    assert "Word" in desc and "PDF" in desc


def test_chat_tool_round_budget_fits_real_document_work():
    """Explore -> correct a wrong tool choice -> read -> answer needs more than
    three executing rounds; the live report ran out mid-task."""
    from iron_jarvis.daemon.routes.chat import _MAX_TOOL_ROUNDS

    assert _MAX_TOOL_ROUNDS >= 6
