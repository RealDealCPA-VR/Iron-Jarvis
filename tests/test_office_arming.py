"""Arming the office tools from what the user actually typed (C-03/C-07/C-09).

A tool nothing arms does not exist for a chat turn. The sentence over an
attachment usually names no file type — "replace Smith with Jones", "fill this
in", "what changed?" — so the ATTACHMENT is what says which tool can do the job,
and that is the half these tests cover (the wording rules are covered by
tests/test_autoselect_*.py, the consent gate by
tests/test_change_intent_guard_v1196.py).

The two writers keep the change-intent gate: an edit verb standing after a
question marker ("why did you change the fee?") must arm NOTHING that writes.
``compare_documents`` is read-only and needs no gate — but it does need TWO
documents, or "what changed?" would arm a comparison with nothing to compare.
"""

from __future__ import annotations

import pytest

from iron_jarvis.documents.attachment_rag import change_verbs_wanted
from iron_jarvis.tools.autoselect import select_auto_tools


def armed(text: str, attachments: list[str] | None = None) -> list[str]:
    return select_auto_tools(text, attachments=attachments, cap=99)


# ------------------------------------------------- 1. the attachment speaks --


@pytest.mark.parametrize(
    "sentence",
    [
        "replace Smith with Jones",
        "change the fee to $3,000",
        "update the address on page 1",
        "fix the client name throughout",
        "swap the old partner name for the new one",
    ],
)
def test_an_edit_request_over_a_WORD_file_arms_docx_edit(sentence):
    """None of these sentences says "Word", ".docx" or "document" — the attached
    file is the only thing that can tell the app which tool applies."""
    got = armed(sentence, ["engagement letter.docx"])
    assert "docx_edit" in got, f"{sentence!r} armed {got}"
    # Reading is still armed alongside it — the model has to see the text first.
    assert "read_document" in got


def test_the_same_words_in_a_QUESTION_arm_nothing_that_writes():
    """The change-intent gate, on the attachment path. Each of these is someone
    ASKING about an edit, and an app that edits the letter in response has done
    something nobody requested."""
    # NOT IN THIS LIST, DELIBERATELY: "what would you update in this letter?".
    # `_ENQUIRY`'s modal branch excludes "you" on purpose — "could you add a
    # column" is a REQUEST, not a question — so "would you <verb>" arms, and
    # that is the module's documented choice rather than an oversight here. The
    # sentence is genuinely ambiguous in English (asking an opinion vs. asking
    # for the edit), and an edit still lands as a COPY that has to be accepted,
    # so the established reading stands. Recorded as a known limit.
    for question in (
        "why did you change the fee in the engagement letter?",
        "did you replace the client name already?",
        "she wants to know if you can change the fee",
        "is the fee in this letter correct?",
    ):
        got = armed(question, ["engagement letter.docx"])
        assert "docx_edit" not in got, f"{question!r} armed a writer: {got}"


def test_a_fill_request_over_a_PDF_arms_the_form_pair(tmp_path):
    for sentence in ("fill this in", "complete the w-9 with my details", "populate the form"):
        got = armed(sentence, ["w9.pdf"])
        assert "pdf_form_fill" in got, f"{sentence!r} armed {got}"
        # The reader rides along: the model needs the field NAMES to fill them.
        assert "pdf_form_fields" in got, (
            "the filler was armed without the tool that says what the fields are"
        )


def test_a_question_about_a_form_arms_no_filler():
    got = armed("what fields does this form have?", ["w9.pdf"])
    assert "pdf_form_fill" not in got


def test_two_documents_and_a_compare_question_arm_the_comparison():
    for sentence in (
        "what changed?",
        "compare these",
        "what are the differences between these two",
        "diff these files",
    ):
        got = armed(sentence, ["2024 return.pdf", "2025 return.pdf"])
        assert "compare_documents" in got, f"{sentence!r} armed {got}"


def test_ONE_document_never_arms_a_comparison():
    """A comparison needs two sides. Armed on a single file it can only fail, and
    a tool that can only fail is worse than one that was never offered."""
    got = armed("what changed?", ["2025 return.pdf"])
    assert "compare_documents" not in got, got


def test_a_comparison_is_read_only_so_a_QUESTION_is_enough():
    """Unlike the two writers, no imperative is required here — asking is the
    normal way this request arrives."""
    got = armed("what's different between last year's and this year's?",
                ["2024.xlsx", "2025.xlsx"])
    assert "compare_documents" in got


# ------------------------------------------- 2. a plain read stays a read ----


@pytest.mark.parametrize(
    ("question", "attachment"),
    [
        ("summarize this", "engagement letter.docx"),
        ("what does this say?", "w9.pdf"),
        ("thanks!", "engagement letter.docx"),
        ("read this and tell me the fee", "engagement letter.docx"),
    ],
)
def test_a_plain_read_arms_the_reader_and_NO_office_writer(question, attachment):
    got = armed(question, [attachment])
    assert "read_document" in got, got
    for writer in ("docx_edit", "pdf_form_fill"):
        assert writer not in got, f"{question!r} armed {writer}: {got}"


def test_the_SENTENCE_path_still_works_with_no_attachment_at_all():
    """The other half of each rule: a sentence that names the document type needs
    no attachment (an agent run, or a chat turn about a file already on disk).
    This is what keeps the attachment bumps from being the only door."""
    assert "docx_edit" in armed("correct the fee in this engagement letter")
    assert "docx_edit" in armed("change the letterhead on that memo")
    assert "compare_documents" in armed("compare these two letters")
    assert "compare_documents" in armed(
        "what's different between last year's return and this year's return?"
    )
    assert "compare_documents" in armed("how do these two documents differ?")


def test_with_NO_attachment_the_bumps_do_not_fire():
    """The attachment is the whole signal here. Without one, these sentences are
    about something else entirely ("replace the light bulb")."""
    for sentence in ("replace Smith with Jones", "fill this in", "what changed?"):
        got = armed(sentence)
        for name in ("docx_edit", "pdf_form_fill", "pdf_form_fields", "compare_documents"):
            assert name not in got, f"{sentence!r} with no attachment armed {name}"


def test_the_file_type_decides_WHICH_writer():
    """A .docx must not arm the PDF filler and vice versa — each refusal would be
    a wasted round trip the user watches."""
    word = armed("fill in the fee and the date", ["engagement letter.docx"])
    assert "pdf_form_fill" not in word
    pdf = armed("change the amount on this form", ["w9.pdf"])
    assert "docx_edit" not in pdf


# ---------------------------------------- 3. the consent gate agrees with it --


def test_the_attachment_consent_gate_lets_the_edit_through():
    """`change_verbs_wanted` is what the chat lane actually asks before arming a
    change tool over someone's file. It must reach the same verdict as the
    scorer, or the tool is armed and never offered (or offered and never armed)."""
    wanted = change_verbs_wanted(
        ".docx", "change the fee to $3,000", attachments=["engagement letter.docx"]
    )
    assert "docx_edit" in wanted, wanted

    assert change_verbs_wanted(
        ".docx", "why did you change the fee?", attachments=["engagement letter.docx"]
    ) == []
    assert change_verbs_wanted(
        ".docx", "summarize this", attachments=["engagement letter.docx"]
    ) == []


def test_the_pdf_gate_offers_the_filler_for_a_fill_request():
    wanted = change_verbs_wanted(".pdf", "fill in this w-9", attachments=["w9.pdf"])
    assert "pdf_form_fill" in wanted, wanted
    assert change_verbs_wanted(".pdf", "what does this say?", attachments=["w9.pdf"]) == []
