"""The text branch of ``redact_file`` must not corrupt the document it copies.

It used to read every .txt/.csv/.tsv/.html/.log (and extensionless) source with
``read_text(encoding="utf-8", errors="replace")``, so a cp1252/latin-1 legacy
export — the normal shape of an office/tax file — had every accented character
rewritten to U+FFFD and WRITTEN INTO the redacted copy. The module's contract is
"only matched characters change", and ``read_document`` on the same file decoded
it correctly, so the corruption was visible only in the deliverable the user
shares. These tests pin the decode, the byte-level round trip of everything that
is not PII, and the two things the fix must not break on the way: the source's
own line endings and a UTF-8 BOM.
"""

from __future__ import annotations

from iron_jarvis.documents.redact import redact_file


def test_cp1252_source_keeps_its_accented_text_and_still_masks_the_ssn(tmp_path):
    src = tmp_path / "client_notes.txt"
    dst = tmp_path / "client_notes.redacted.txt"
    original = "Müller — Bäckerstraße 12\nSSN 123-45-6789\nSchlußprüfung: café\n"
    src.write_bytes(original.encode("cp1252"))

    counts, _note = redact_file(src, dst, style="black")

    assert counts.get("ssn_labeled") or counts.get("ssn")
    out = dst.read_text(encoding="utf-8")
    assert "�" not in out  # the collateral corruption
    assert "Müller — Bäckerstraße 12" in out
    assert "Schlußprüfung: café" in out
    assert "123-45-6789" not in out and "█" in out
    # Everything that is not PII survives unchanged, line for line.
    assert out.splitlines()[0] == original.splitlines()[0]
    assert out.splitlines()[2] == original.splitlines()[2]


def test_latin1_extensionless_source_is_decoded_too(tmp_path):
    """The branch is ``suffix in _TEXT_SUFFIXES or suffix == ""``."""
    src = tmp_path / "notes"
    dst = tmp_path / "notes.redacted"
    src.write_bytes("José García wrote to jose@example.com\n".encode("cp1252"))

    counts, _note = redact_file(src, dst, style="label")

    assert counts.get("email") == 1
    out = dst.read_text(encoding="utf-8")
    assert out == "José García wrote to [EMAIL]\n"


def test_utf8_source_is_unchanged_by_the_new_decode(tmp_path):
    src = tmp_path / "list.csv"
    dst = tmp_path / "list.redacted.csv"
    src.write_text("name,ssn\nJosé,123-45-6789\n", encoding="utf-8")

    counts, _note = redact_file(src, dst, style="remove", extra_terms=["José"])

    assert counts == {"ssn": 1, "custom": 1}
    assert dst.read_text(encoding="utf-8") == "name,ssn\n,\n"


def test_crlf_line_endings_survive_byte_for_byte(tmp_path):
    """The decode is byte-exact, so the write must not re-translate newlines
    (the default would turn each CRLF into CR CRLF on Windows)."""
    src = tmp_path / "export.csv"
    dst = tmp_path / "export.redacted.csv"
    src.write_bytes(b"name,ssn\r\nJane,123-45-6789\r\n")

    redact_file(src, dst, style="label")

    raw = dst.read_bytes()
    assert b"\r\r" not in raw
    assert raw == b"name,ssn\r\nJane,[SSN]\r\n"


def test_utf8_bom_is_preserved(tmp_path):
    """A BOM-less CSV opens in the legacy codepage in Excel — dropping the BOM
    would mojibake exactly the file this fix keeps intact."""
    src = tmp_path / "bom.csv"
    dst = tmp_path / "bom.redacted.csv"
    src.write_bytes(b"\xef\xbb\xbf" + "name,ssn\nJosé,123-45-6789\n".encode("utf-8"))

    redact_file(src, dst, style="label")

    raw = dst.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    # ...and the BOM is not glued onto the first header cell.
    assert raw[3:].decode("utf-8").startswith("name,ssn")
    assert "José" in raw.decode("utf-8-sig")
