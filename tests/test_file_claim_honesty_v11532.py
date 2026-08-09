"""A reply cannot claim a file that no tool wrote (v1.153.2).

From a live report: the user redacted a K-1, was told the output had been saved,
asked for the exact path, went there — and no such file existed. The tool ledger
settles it. On that day only ``redact_scan`` ran (it lists candidates and writes
NOTHING); ``redact_pii`` was never called at all. The file had never existed.

The existing ``_creation_honesty_note`` could not catch it: it keys off the
USER's phrasing, and "redact this K-1" matches no create-a-file pattern. So the
check here is on the reply's own CLAIM, judged against what actually ran —
the same principle as ``agents/outcome`` and the compaction verifier: the record
decides, never the prose.

The second defect the same report exposed is covered in
``test_document_tools_report_absolute_paths``: when ``redact_pii`` DOES run, it
used to report a workspace-RELATIVE path, which for an upload is a bare
filename. That is the same failure wearing a different hat — the file exists,
but nothing tells the user where.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.chat_turn import _claimed_write_note


# --------------------------------------------------------------------------- #
# (1) THE REPORTED CASE.
# --------------------------------------------------------------------------- #
def test_a_claimed_file_after_only_a_scan_is_flagged():
    """The exact shape of the report: redact_scan ran, redact_pii did not, and
    the reply announced a saved output."""
    reply = (
        "I've redacted the K-1 and saved it as "
        "CENTRAL_FLORIDA_2021_K-1.redacted.pdf in your uploads folder."
    )
    note = _claimed_write_note(reply, ["redact_scan", "read_document"])
    assert note
    assert "does not exist" in note
    assert "redacted.pdf" in note


def test_the_note_names_the_file_it_is_talking_about():
    note = _claimed_write_note("Saved to report.docx.", ["read_document"])
    assert "`report.docx`" in note


@pytest.mark.parametrize(
    "reply",
    [
        "I wrote the summary to summary.md.",
        "Created invoice.xlsx with the totals.",
        "The redacted copy has been saved as k1.redacted.pdf.",
        "Exported everything to output.csv for you.",
        "Generated deck.pptx from your outline.",
    ],
)
def test_assertive_claims_are_caught(reply):
    assert _claimed_write_note(reply, ["read_document"])


# --------------------------------------------------------------------------- #
# (2) IT MUST NOT CRY WOLF. A false accusation on a turn that DID write the
#     file is its own trust failure — and the likelier one, since most turns
#     that mention a filename wrote it.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tool", ["write_document", "redact_pii", "excel_edit",
                                  "convert_document", "batch_documents", "write_file"])
def test_no_note_when_a_writing_tool_actually_ran(tool):
    reply = "Saved to report.docx."
    assert _claimed_write_note(reply, [tool]) == ""


@pytest.mark.parametrize(
    "reply",
    [
        # Present-tense offers never match the pattern at all: the verb list is
        # deliberately past tense, because only a past-tense verb asserts that
        # the file now EXISTS.
        "I can save it to report.docx if you'd like.",
        "Would you like me to write summary.md?",
        "I could export this to deck.pptx.",
        "Say the word and I will generate invoice.xlsx.",
        "To create report.docx, arm the document tools.",
        "I cannot write to k1.pdf without permission.",
    ],
)
def test_present_tense_offers_are_not_claims(reply):
    assert _claimed_write_note(reply, ["read_document"]) == ""


@pytest.mark.parametrize(
    "reply",
    [
        # These DO use the claim verbs, and only the negation guard separates
        # them from a real claim. Contradicting a model that correctly said it
        # wrote nothing would be its own small betrayal of trust.
        "No file was created — output.csv does not exist yet.",
        "I have not created report.docx.",
        "The result was not saved to summary.md.",
        "Nothing was written to invoice.xlsx.",
        "I could not generate deck.pptx without the source.",
        "I didn't produce k1.redacted.pdf because you cancelled.",
    ],
)
def test_denials_are_not_claims(reply):
    assert _claimed_write_note(reply, ["read_document"]) == "", (
        "a model correctly reporting that it wrote nothing must not be "
        "contradicted by a note saying nothing was written"
    )


def test_a_reply_with_no_filename_is_left_alone():
    assert _claimed_write_note("Here are the four PII candidates.", ["redact_scan"]) == ""


def test_an_empty_reply_is_not_a_crash():
    assert _claimed_write_note("", []) == ""
    assert _claimed_write_note("Saved to x.pdf", []) != ""


def test_discussing_a_file_the_user_supplied_is_not_a_claim():
    """Reading and describing an input file must not be reported as a write."""
    reply = "I read your K-1.pdf and found four PII candidates."
    assert _claimed_write_note(reply, ["read_document"]) == ""


# --------------------------------------------------------------------------- #
# (3) THE SET IT JUDGES AGAINST.
# --------------------------------------------------------------------------- #
def test_both_chat_lanes_actually_append_the_note():
    """The check is worthless unwired, and nothing else here would notice:
    every other test calls the function directly. Asserted on the source of
    BOTH lanes because ``/chat`` and ``/chat/stream`` are a lock-step mirror —
    a guard that lands in one lane only is half a guard.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"
    for rel in ("chat_turn.py", "routes/chat.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "_claimed_write_note(reply, tools_used)" in src, (
            f"{rel} never appends the claim-check note"
        )


def test_the_writing_tool_set_covers_every_tool_that_writes_a_file():
    """redact_pii / convert_document / batch_documents were missing, so a REAL
    redaction counted as 'nothing written' and the honesty note contradicted a
    turn that had done the work."""
    from iron_jarvis.daemon.chat_turn import _FILE_WRITING_TOOLS

    for name in ("write_document", "write_file", "excel_edit", "excel_apply_spec",
                 "redact_pii", "convert_document", "batch_documents"):
        assert name in _FILE_WRITING_TOOLS, f"{name} writes files but is not listed"


# --------------------------------------------------------------------------- #
# (4) THE SECOND DEFECT: say WHERE, in full.
# --------------------------------------------------------------------------- #
def test_document_tools_report_absolute_paths():
    """A workspace-relative path is a BARE FILENAME whenever the output lands in
    the workspace root — which is exactly what happens for an uploaded source.
    The model relays it verbatim and the user looks next to the original."""
    import inspect

    from iron_jarvis.documents import tools as T

    for cls in (T.RedactPiiTool, T.WriteDocumentTool, T.ConvertDocumentTool):
        src = inspect.getsource(cls)
        assert "abs_path" in src, f"{cls.__name__} does not report an absolute path"


def test_redaction_tells_the_user_where_it_saved(tmp_path, monkeypatch):
    """End to end on the real tool: the reported location must be findable."""
    import asyncio

    from iron_jarvis.documents import tools as T
    from iron_jarvis.tools.base import ToolContext

    src = tmp_path / "in.txt"
    src.write_text("call me on 555-123-4567 any time", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )
    res = asyncio.run(T.RedactPiiTool().execute({"path": str(src)}, ctx))
    assert res.ok, res.error

    from pathlib import Path

    reported = res.data["abs_path"]
    assert Path(reported).is_file(), f"reported {reported} but nothing is there"
    assert reported in res.output, "the message must state the full location"
