"""PDF redaction keeps the document (v1.154.0).

Reported after opening a redacted return: "it did not keep its same format
persistence… due to the formatting issue, would have been unusable." Measured on
that document, the old rebuild path turned 29 pages into 37, changed the page
size to A4, substituted every font, and dropped all 30 form rules. The PII was
genuinely gone and the artifact was not a tax return.

The old design was a fair choice between two bad options — pypdf cannot edit
page content, and a black box painted over live text is a FAKE redaction
because the text underneath stays extractable. This adds the third option:
pikepdf rewrites the content stream so the glyphs are really deleted, while
every other page object survives.

THE TEST THAT MATTERS is :func:`test_a_pdf_that_cannot_be_verified_is_refused`.
Everything else here is fidelity, and fidelity is the FEATURE; that one is the
guarantee. A PDF that looks redacted while still carrying the SSN is worse than
any ugly rebuild, so the module re-reads what it wrote and refuses to hand back
a file it cannot prove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pikepdf = pytest.importorskip("pikepdf")
pdfplumber = pytest.importorskip("pdfplumber")

from iron_jarvis.documents.pdf_redact import (  # noqa: E402
    RedactionUnverified,
    redact_pdf,
)
from iron_jarvis.documents.redact import find_pii_spans, redact_file  # noqa: E402


def _make_pdf(path: Path, lines: list[str]) -> Path:
    """A small PDF with a standard font and a drawn rule, so the fidelity
    assertions have real page furniture to preserve."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for ln in lines:
        pdf.cell(0, 8, ln, new_x="LMARGIN", new_y="NEXT")
    pdf.line(10, 100, 200, 100)  # a form rule to preserve
    pdf.output(str(path))
    return path


# --------------------------------------------------------------------------- #
# (1) THE GUARANTEE.
# --------------------------------------------------------------------------- #
def test_the_text_is_really_gone_not_merely_covered(tmp_path):
    """A black box over live text is a fake redaction — the defining mistake
    this module must not make."""
    src = _make_pdf(tmp_path / "in.pdf", ["Name: Jane Roe", "SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"
    redact_pdf(src, dst, values=["123-45-6789"])

    with pdfplumber.open(str(dst)) as doc:
        text = "\n".join((p.extract_text() or "") for p in doc.pages)
    assert "123-45-6789" not in text


def test_a_pdf_that_cannot_be_verified_is_refused(tmp_path, monkeypatch):
    """If the rewrite silently misses a value, NOTHING is handed back. The
    output is deleted and the caller falls back to a method whose guarantee it
    can state."""
    src = _make_pdf(tmp_path / "in.pdf", ["SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"

    # A transform that changes nothing at all — the worst realistic failure.
    monkeypatch.setattr(
        "iron_jarvis.documents.pdf_redact._rewrite_operands",
        lambda ops, replace: (ops, 0),
    )
    with pytest.raises(RedactionUnverified):
        redact_pdf(src, dst, values=["123-45-6789"], draw_boxes=False)
    assert not dst.exists(), "a file that could not be verified must not survive"


def test_the_refusal_names_what_survived(tmp_path, monkeypatch):
    src = _make_pdf(tmp_path / "in.pdf", ["SSN: 123-45-6789"])
    monkeypatch.setattr(
        "iron_jarvis.documents.pdf_redact._rewrite_operands",
        lambda ops, replace: (ops, 0),
    )
    with pytest.raises(RedactionUnverified, match="123-45-6789"):
        redact_pdf(src, tmp_path / "o.pdf", values=["123-45-6789"], draw_boxes=False)


# --------------------------------------------------------------------------- #
# (2) THE DOCUMENT SURVIVES — the reason this release exists.
# --------------------------------------------------------------------------- #
def test_page_count_size_and_rules_are_preserved(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", ["Client: Jane Roe", "SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"
    redact_pdf(src, dst, values=["123-45-6789"])

    with pdfplumber.open(str(src)) as a, pdfplumber.open(str(dst)) as b:
        assert len(a.pages) == len(b.pages)
        assert (a.pages[0].width, a.pages[0].height) == (
            b.pages[0].width,
            b.pages[0].height,
        )
        assert len(a.pages[0].lines) == len(b.pages[0].lines), "a form rule was lost"


def test_the_untargeted_text_is_left_alone(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", ["Client: Jane Roe", "SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"
    redact_pdf(src, dst, values=["123-45-6789"])
    with pdfplumber.open(str(dst)) as doc:
        text = doc.pages[0].extract_text() or ""
    assert "Client" in text and "Jane Roe" in text


def test_black_boxes_are_actually_painted(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", ["SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"
    redact_pdf(src, dst, values=["123-45-6789"])
    with pdfplumber.open(str(src)) as a, pdfplumber.open(str(dst)) as b:
        assert len(b.pages[0].rects) > len(a.pages[0].rects)


def test_a_longer_value_wins_over_the_name_inside_it(tmp_path):
    """Redacting "Roe" before "Jane Roe" would leave the first name showing."""
    src = _make_pdf(tmp_path / "in.pdf", ["Client: Jane Roe"])
    dst = tmp_path / "out.pdf"
    redact_pdf(src, dst, values=["Roe", "Jane Roe"])
    with pdfplumber.open(str(dst)) as doc:
        text = doc.pages[0].extract_text() or ""
    assert "Jane" not in text


def test_no_values_is_a_clean_error(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", ["nothing here"])
    with pytest.raises(ValueError):
        redact_pdf(src, tmp_path / "o.pdf", values=[])


# --------------------------------------------------------------------------- #
# (3) DETECTION NO LONGER STRADDLES LINE BREAKS.
# --------------------------------------------------------------------------- #
def test_a_pattern_never_matches_across_a_line_break():
    """The separators in these patterns include ``\\s``, which matches a
    NEWLINE — so a trailing number and the next line's number were welded into
    a "phone number". On the reported tax return SIX of seven phone hits were
    that, and real financial figures were being blacked out.
    """
    text = "Ownership percentage\n1096\n100.0000\nTotal"
    spans = find_pii_spans(text)
    for start, end, _cat in spans:
        assert "\n" not in text[start:end], (
            f"a match spans a line break: {text[start:end]!r}"
        )


def test_a_real_phone_on_one_line_is_still_found():
    """The fix must not have blinded the detector."""
    spans = find_pii_spans("Call the office at 718-414-5561 for details")
    assert any(cat == "phone" for _s, _e, cat in spans)


def test_a_real_ssn_is_still_found():
    spans = find_pii_spans("SSN: 123-45-6789")
    assert any(cat == "ssn" for _s, _e, cat in spans)


def test_spans_are_absolute_offsets_into_the_whole_text():
    """Detection is chunked per line; the coordinates must not be."""
    text = "line one\nline two\nSSN: 123-45-6789\n"
    spans = find_pii_spans(text)
    assert spans
    for start, end, _cat in spans:
        assert text[start:end] == "123-45-6789"


# --------------------------------------------------------------------------- #
# (4) THE CALLER'S CHOICE: keep the document, or keep the guarantee.
# --------------------------------------------------------------------------- #
def test_redact_file_uses_the_in_place_path_and_says_so(tmp_path):
    src = _make_pdf(tmp_path / "in.pdf", ["Client: Jane Roe", "SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"
    counts, note = redact_file(src, dst, style="black")
    assert counts.get("ssn") == 1
    assert "in place" in note.lower()
    with pdfplumber.open(str(src)) as a, pdfplumber.open(str(dst)) as b:
        assert len(a.pages) == len(b.pages)


def test_redact_file_falls_back_and_explains_why(tmp_path, monkeypatch):
    """When the document cannot be edited safely the old rebuild still runs —
    'truly gone, layout approximate' beats 'looks right, still leaks' — and the
    note now says which path produced the file."""
    src = _make_pdf(tmp_path / "in.pdf", ["SSN: 123-45-6789"])
    dst = tmp_path / "out.pdf"

    def _boom(*a, **k):
        from iron_jarvis.documents.pdf_redact import UnsupportedPdf

        raise UnsupportedPdf("embedded CID font")

    monkeypatch.setattr("iron_jarvis.documents.pdf_redact.redact_pdf", _boom)
    counts, note = redact_file(src, dst, style="black")
    assert dst.is_file()
    assert "REBUILT" in note
    assert "CID font" in note
    with pdfplumber.open(str(dst)) as doc:
        assert "123-45-6789" not in "\n".join(
            (p.extract_text() or "") for p in doc.pages
        )


def test_an_uppercase_street_address_is_detected():
    """Tax documents are UPPERCASE. The street suffixes were case-sensitive, so
    "5059 ALAMANDA DR" on a real K-1 was never detected and a client's home
    address stayed in a file the user believed was redacted."""
    spans = find_pii_spans("NICHOLAS GIORDANO\n5059 ALAMANDA DR\nMELBOURNE, FL")
    assert any(cat == "address" for _s, _e, cat in spans)


def test_mixed_case_addresses_still_work():
    spans = find_pii_spans("Office at 276 Fifth Avenue, Suite 704")
    assert any(cat == "address" for _s, _e, cat in spans)
