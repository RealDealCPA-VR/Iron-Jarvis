"""Page-level PDF engine (documents/pdf_pages): spec parser, arrange, split,
pdf_info.

Every fixture PDF is BUILT in-test with pypdf (blank pages of distinct sizes —
page WIDTH doubles as an identity marker so merge/reorder order is asserted by
re-opening outputs and reading mediabox widths). Deterministic, offline.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject

from iron_jarvis.documents.pdf_pages import (
    BLANK_PAGE,
    US_LETTER,
    ArrangeInput,
    arrange,
    parse_page_spec,
    pdf_info,
    split,
)

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    HAS_CRYPTO = False


# --- fixtures ------------------------------------------------------------------


def make_pdf(
    path: Path,
    sizes: list[tuple[float, float]],
    *,
    rotations: "list[int] | None" = None,
    metadata: "dict[str, str] | None" = None,
    password: "str | None" = None,
    algorithm: str = "RC4-128",
) -> Path:
    writer = PdfWriter()
    for i, (w, h) in enumerate(sizes):
        page = writer.add_blank_page(width=w, height=h)
        if rotations and rotations[i]:
            page[NameObject("/Rotate")] = NumberObject(rotations[i])
    if metadata:
        writer.add_metadata(metadata)
    if password:
        writer.encrypt(user_password=password, algorithm=algorithm)
    with open(path, "wb") as fh:
        writer.write(fh)
    return path


def widths(path: Path, password: "str | None" = None) -> list[float]:
    """Re-open ``path`` and return every page's mediabox width — the honesty
    probe used to assert page ORDER, not just count."""
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt(password or "")
    return [round(float(p.mediabox.width), 1) for p in reader.pages]


def rotations_of(path: Path) -> list[int]:
    reader = PdfReader(str(path))
    return [int(p.get("/Rotate", 0)) % 360 for p in reader.pages]


# --- parse_page_spec -----------------------------------------------------------


def test_parse_single_pages_and_lists():
    assert parse_page_spec("1", 9) == [(0, 0)]
    assert parse_page_spec("1,3,5", 9) == [(0, 0), (2, 0), (4, 0)]


def test_parse_ranges_end_and_all():
    assert parse_page_spec("2-4", 9) == [(1, 0), (2, 0), (3, 0)]
    assert parse_page_spec("7-end", 9) == [(6, 0), (7, 0), (8, 0)]
    assert parse_page_spec("end", 9) == [(8, 0)]
    assert parse_page_spec("all", 3) == [(0, 0), (1, 0), (2, 0)]


def test_parse_backwards_range_is_reversed():
    assert parse_page_spec("5-2", 9) == [(4, 0), (3, 0), (2, 0), (1, 0)]
    assert parse_page_spec("3-1", 3) == [(2, 0), (1, 0), (0, 0)]


def test_parse_rotation_suffixes():
    assert parse_page_spec("2@90", 9) == [(1, 90)]
    assert parse_page_spec("2-3@180", 9) == [(1, 180), (2, 180)]
    assert parse_page_spec("all@270", 2) == [(0, 270), (1, 270)]
    assert parse_page_spec("end@90", 4) == [(3, 90)]


def test_parse_blank_token():
    assert parse_page_spec("blank", 9) == [(BLANK_PAGE, 0)]
    assert parse_page_spec("1,blank@180,2", 9) == [(0, 0), (BLANK_PAGE, 180), (1, 0)]


def test_parse_case_and_whitespace_tolerant():
    assert parse_page_spec(" ALL ", 2) == [(0, 0), (1, 0)]
    assert parse_page_spec("1 , 3-4 , End", 5) == [(0, 0), (2, 0), (3, 0), (4, 0)]


def test_parse_out_of_range_error_names_real_count():
    with pytest.raises(ValueError, match="page 12 is out of range"):
        parse_page_spec("12", 9)
    with pytest.raises(ValueError, match="has 9 pages"):
        parse_page_spec("3-12", 9)
    with pytest.raises(ValueError, match="has 1 page"):
        parse_page_spec("2", 1)


def test_parse_garbage_and_empty_errors():
    with pytest.raises(ValueError, match="empty page spec"):
        parse_page_spec("", 9)
    with pytest.raises(ValueError, match="empty page spec"):
        parse_page_spec("   ", 9)
    with pytest.raises(ValueError, match="empty token"):
        parse_page_spec("1,,2", 9)
    with pytest.raises(ValueError, match="invalid page token"):
        parse_page_spec("abc", 9)
    with pytest.raises(ValueError, match="invalid page token"):
        parse_page_spec("1-", 9)
    with pytest.raises(ValueError, match="invalid page token"):
        parse_page_spec("end-3", 9)  # ranges start with a NUMBER, per grammar


def test_parse_zero_page_and_bad_rotation_errors():
    with pytest.raises(ValueError, match="1-based"):
        parse_page_spec("0", 9)
    with pytest.raises(ValueError, match="invalid rotation"):
        parse_page_spec("2@45", 9)
    with pytest.raises(ValueError, match="invalid rotation"):
        parse_page_spec("2@0", 9)


# --- arrange -------------------------------------------------------------------


def test_arrange_merges_inputs_in_order_with_honest_report(tmp_path: Path):
    a = make_pdf(tmp_path / "a.pdf", [(100, 700), (110, 700)])
    b = make_pdf(tmp_path / "b.pdf", [(200, 700), (210, 700), (220, 700)])
    out = tmp_path / "merged.pdf"
    report = arrange([ArrangeInput(path=a), ArrangeInput(path=b)], out)
    assert widths(out) == [100, 110, 200, 210, 220]
    assert report.path == str(out)
    assert report.pages == 5  # re-opened, not assumed
    assert report.inputs == [
        {"path": str(a), "pages": 2, "used": 2},
        {"path": str(b), "pages": 3, "used": 3},
    ]
    assert report.to_dict()["pages"] == 5


def test_arrange_accepts_plain_dict_inputs(tmp_path: Path):
    a = make_pdf(tmp_path / "a.pdf", [(100, 700), (110, 700)])
    out = tmp_path / "out.pdf"
    report = arrange([{"path": str(a), "pages_spec": "2"}], out)
    assert widths(out) == [110]
    assert report.pages == 1


def test_arrange_reorder_duplicate_reverse(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700), (200, 700), (300, 700)])
    out = tmp_path / "out.pdf"
    arrange([ArrangeInput(path=src, pages_spec="3,1,1,3-1")], out)
    assert widths(out) == [300, 100, 100, 300, 200, 100]


def test_arrange_delete_by_omission(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700), (200, 700), (300, 700)])
    out = tmp_path / "out.pdf"
    report = arrange([ArrangeInput(path=src, pages_spec="1,3")], out)
    assert widths(out) == [100, 300]
    assert report.pages == 2
    assert report.inputs[0]["used"] == 2
    assert report.inputs[0]["pages"] == 3  # the file still HAS 3 pages


def test_arrange_rotation_persists_and_is_additive(tmp_path: Path):
    src = make_pdf(
        tmp_path / "src.pdf", [(100, 700), (200, 700)], rotations=[270, 0]
    )
    out = tmp_path / "out.pdf"
    arrange([ArrangeInput(path=src, pages_spec="1@180,2@90,2")], out)
    # 270 + 180 = 450 → normalized 90; 0 + 90 = 90; untouched page stays 0.
    assert rotations_of(out) == [90, 90, 0]


def test_arrange_blank_sizing_letter_first_then_previous(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(400, 500)])
    out = tmp_path / "out.pdf"
    report = arrange([ArrangeInput(path=src, pages_spec="blank,1,blank")], out)
    reader = PdfReader(str(out))
    sizes = [
        (float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages
    ]
    assert sizes[0] == US_LETTER  # first selection → US-Letter
    assert sizes[1] == (400.0, 500.0)
    assert sizes[2] == (400.0, 500.0)  # matches the PREVIOUS selected page
    assert report.pages == 3
    assert report.inputs[0]["used"] == 1  # blanks belong to no input


def test_arrange_crop_shrinks_mediabox_and_cropbox(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(500, 1000)])
    out = tmp_path / "out.pdf"
    arrange(
        [ArrangeInput(path=src)],
        out,
        crop={"top": 10, "right": 20, "bottom": 10, "left": 20},
    )
    page = PdfReader(str(out)).pages[0]
    for box in (page.mediabox, page.cropbox):
        assert float(box.left) == pytest.approx(100.0)
        assert float(box.bottom) == pytest.approx(100.0)
        assert float(box.right) == pytest.approx(400.0)
        assert float(box.top) == pytest.approx(900.0)


def test_arrange_crop_validation_errors(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(500, 1000)])
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError, match="left \\+ right"):
        arrange([ArrangeInput(path=src)], out, crop={"left": 50, "right": 50})
    with pytest.raises(ValueError, match="crop bottom must be a percentage"):
        arrange([ArrangeInput(path=src)], out, crop={"bottom": -5})
    with pytest.raises(ValueError, match="unknown crop key"):
        arrange([ArrangeInput(path=src)], out, crop={"middle": 5})
    assert not out.exists()  # validation failures never write anything


def test_arrange_metadata_roundtrip(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    out = tmp_path / "out.pdf"
    arrange(
        [ArrangeInput(path=src)],
        out,
        metadata={"title": "Q3 Pack", "author": "RD", "subject": "Merged"},
    )
    info = PdfReader(str(out)).metadata
    assert info.title == "Q3 Pack"
    assert info.author == "RD"
    assert info.subject == "Merged"
    with pytest.raises(ValueError, match="unknown metadata key"):
        arrange([ArrangeInput(path=src)], tmp_path / "x.pdf", metadata={"keywords": "no"})


def test_arrange_password_protected_input_rc4(tmp_path: Path):
    src = make_pdf(
        tmp_path / "locked.pdf", [(100, 700), (200, 700)], password="pw",
        algorithm="RC4-128",
    )
    out = tmp_path / "out.pdf"
    with pytest.raises(ValueError, match="password-protected"):
        arrange([ArrangeInput(path=src)], out)
    with pytest.raises(ValueError, match="wrong password"):
        arrange([ArrangeInput(path=src, password="nope")], out)
    report = arrange([ArrangeInput(path=src, password="pw")], out)
    assert report.pages == 2
    assert widths(out) == [100, 200]


@pytest.mark.skipif(not HAS_CRYPTO, reason="AES needs the cryptography package")
def test_arrange_password_protected_input_aes(tmp_path: Path):
    src = make_pdf(
        tmp_path / "aes.pdf", [(100, 700)], password="pw", algorithm="AES-256"
    )
    out = tmp_path / "out.pdf"
    report = arrange([ArrangeInput(path=src, password="pw")], out)
    assert report.pages == 1
    assert widths(out) == [100]


def test_arrange_encrypts_output(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700), (200, 700)])
    out = tmp_path / "locked-out.pdf"
    report = arrange(
        [ArrangeInput(path=src)],
        out,
        encrypt_password="s3cret",
        metadata={"title": "Locked Pack"},  # metadata + encryption combined
    )
    reader = PdfReader(str(out))
    assert reader.is_encrypted
    assert report.pages == 2  # verified THROUGH the password
    assert widths(out, password="s3cret") == [100, 200]
    reader = PdfReader(str(out))
    reader.decrypt("s3cret")
    assert reader.metadata.title == "Locked Pack"


def test_arrange_refuses_over_2000_pages(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    out = tmp_path / "out.pdf"
    spec = ",".join(["blank"] * 2001)
    with pytest.raises(ValueError, match="2000"):
        arrange([ArrangeInput(path=src, pages_spec=spec)], out)
    assert not out.exists()


def test_arrange_corrupt_input_specific_error(tmp_path: Path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf at all" * 10)
    with pytest.raises(ValueError, match="not a valid PDF"):
        arrange([ArrangeInput(path=bad)], tmp_path / "out.pdf")
    with pytest.raises(ValueError, match="file not found"):
        arrange([ArrangeInput(path=tmp_path / "missing.pdf")], tmp_path / "o.pdf")


def test_arrange_never_touches_inputs(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700), (200, 700)])
    before = src.read_bytes()
    arrange([ArrangeInput(path=src, pages_spec="2-1@90")], tmp_path / "out.pdf")
    assert src.read_bytes() == before
    with pytest.raises(ValueError, match="never overwritten"):
        arrange([ArrangeInput(path=src)], src)  # output onto an input → refused
    assert src.read_bytes() == before


def test_arrange_empty_selection_error(tmp_path: Path):
    empty = tmp_path / "empty.pdf"
    with open(empty, "wb") as fh:
        PdfWriter().write(fh)  # a real PDF with zero pages
    with pytest.raises(ValueError, match="selection is empty"):
        arrange([ArrangeInput(path=empty, pages_spec="all")], tmp_path / "o.pdf")


def test_arrange_atomicity_failure_leaves_nothing(tmp_path: Path, monkeypatch):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    before = src.read_bytes()
    out = tmp_path / "out.pdf"

    def boom(self, stream, *a, **k):
        stream.write(b"%PDF-partial garbage")  # half-written temp
        raise RuntimeError("disk died mid-write")

    monkeypatch.setattr(PdfWriter, "write", boom)
    with pytest.raises(RuntimeError, match="disk died"):
        arrange([ArrangeInput(path=src)], out)
    assert not out.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "src.pdf"]
    assert leftovers == []  # no temp file, no partial output
    assert src.read_bytes() == before


# --- split ---------------------------------------------------------------------


def test_split_ranges_mode(tmp_path: Path):
    src = make_pdf(
        tmp_path / "doc.pdf",
        [(100, 700), (110, 700), (120, 700), (130, 700), (140, 700)],
    )
    out_dir = tmp_path / "parts"
    report = split(src, out_dir, mode={"ranges": ["1-2", "3-end"]})
    assert [Path(o["path"]).name for o in report.outputs] == [
        "doc-part01.pdf",
        "doc-part02.pdf",
    ]
    assert [o["pages"] for o in report.outputs] == [2, 3]
    assert widths(out_dir / "doc-part01.pdf") == [100, 110]
    assert widths(out_dir / "doc-part02.pdf") == [120, 130, 140]
    assert report.to_dict() == {"outputs": report.outputs}


def test_split_every_mode(tmp_path: Path):
    src = make_pdf(tmp_path / "doc.pdf", [(100 + 10 * i, 700) for i in range(5)])
    report = split(src, tmp_path / "parts", mode={"every": 2})
    assert [o["pages"] for o in report.outputs] == [2, 2, 1]
    assert widths(Path(report.outputs[2]["path"])) == [140]


def test_split_per_page_mode(tmp_path: Path):
    src = make_pdf(tmp_path / "doc.pdf", [(100, 700), (200, 700), (300, 700)])
    report = split(src, tmp_path / "parts", mode={"per_page": True})
    assert [o["pages"] for o in report.outputs] == [1, 1, 1]
    assert [widths(Path(o["path"])) for o in report.outputs] == [[100], [200], [300]]


def test_split_never_clobbers_existing_files(tmp_path: Path):
    src = make_pdf(tmp_path / "doc.pdf", [(100, 700), (200, 700)])
    out_dir = tmp_path / "parts"
    out_dir.mkdir()
    existing = out_dir / "doc-part01.pdf"
    existing.write_bytes(b"precious pre-existing bytes")
    report = split(src, out_dir, mode={"per_page": True})
    assert existing.read_bytes() == b"precious pre-existing bytes"
    names = sorted(Path(o["path"]).name for o in report.outputs)
    assert names == ["doc-part01-2.pdf", "doc-part02.pdf"]
    assert widths(out_dir / "doc-part01-2.pdf") == [100]


def test_split_mode_validation_errors(tmp_path: Path):
    src = make_pdf(tmp_path / "doc.pdf", [(100, 700)])
    with pytest.raises(ValueError, match="exactly one"):
        split(src, tmp_path / "p", mode={})
    with pytest.raises(ValueError, match="exactly ONE"):
        split(src, tmp_path / "p", mode={"every": 1, "per_page": True})
    with pytest.raises(ValueError, match="unknown split mode"):
        split(src, tmp_path / "p", mode={"chunks": 3})
    with pytest.raises(ValueError, match="positive integer"):
        split(src, tmp_path / "p", mode={"every": 0})
    with pytest.raises(ValueError, match="non-empty list"):
        split(src, tmp_path / "p", mode={"ranges": []})
    with pytest.raises(ValueError, match="per_page must be true"):
        split(src, tmp_path / "p", mode={"per_page": False})


def test_split_refuses_over_2000_pages(tmp_path: Path):
    src = make_pdf(tmp_path / "big.pdf", [(100, 700)] * 2001)
    with pytest.raises(ValueError, match="2000"):
        split(src, tmp_path / "parts", mode={"every": 1000})
    assert not (tmp_path / "parts").exists()


def test_split_password_and_corrupt(tmp_path: Path):
    locked = make_pdf(tmp_path / "locked.pdf", [(100, 700), (200, 700)], password="pw")
    with pytest.raises(ValueError, match="password-protected"):
        split(locked, tmp_path / "p", mode={"per_page": True})
    report = split(locked, tmp_path / "p", mode={"per_page": True}, password="pw")
    assert [o["pages"] for o in report.outputs] == [1, 1]
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"nope")
    with pytest.raises(ValueError, match="not a valid PDF"):
        split(bad, tmp_path / "p2", mode={"per_page": True})


# --- pdf_info ------------------------------------------------------------------


def test_pdf_info_reports_sizes_and_metadata(tmp_path: Path):
    src = make_pdf(
        tmp_path / "doc.pdf",
        [(612, 792), (200.5, 400.25)],
        metadata={"/Title": "Ledger", "/Author": "RD"},
    )
    info = pdf_info(src)
    assert info["path"] == str(src)
    assert info["pages"] == 2
    assert info["encrypted"] is False
    assert info["page_sizes"] == [
        {"width": 612.0, "height": 792.0},
        {"width": 200.5, "height": 400.25},
    ]
    assert info["metadata"]["title"] == "Ledger"
    assert info["metadata"]["author"] == "RD"


def test_pdf_info_encrypted_flag_and_honest_error(tmp_path: Path):
    src = make_pdf(tmp_path / "locked.pdf", [(100, 700)], password="pw")
    with pytest.raises(ValueError, match="password-protected"):
        pdf_info(src)
    with pytest.raises(ValueError, match="wrong password"):
        pdf_info(src, password="nope")
    info = pdf_info(src, password="pw")
    assert info["encrypted"] is True
    assert info["pages"] == 1


# --- reviewer regression battery -------------------------------------------------


def test_parse_end_on_zero_page_file_is_honest():
    with pytest.raises(ValueError, match="has 0 pages"):
        parse_page_spec("end", 0)


def test_arrange_blank_only_selection_is_one_page_output(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    out = tmp_path / "out.pdf"
    report = arrange([ArrangeInput(path=src, pages_spec="blank")], out)
    assert report.pages == 1
    assert report.inputs[0] == {"path": str(src), "pages": 1, "used": 0}
    reader = PdfReader(str(out))
    assert (
        float(reader.pages[0].mediabox.width),
        float(reader.pages[0].mediabox.height),
    ) == US_LETTER


def test_arrange_exactly_2000_pages_is_allowed(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    out = tmp_path / "out.pdf"
    spec = ",".join(["blank"] * 1999 + ["1"])
    report = arrange([ArrangeInput(path=src, pages_spec=spec)], out)
    assert report.pages == 2000  # the limit is inclusive: 2000 OK, 2001 refused


def test_arrange_crop_follows_display_rotation(tmp_path: Path):
    """Crop is DISPLAY-relative: 'top' means the top the user sees.

    With /Rotate 90 (clockwise display) the displayed top edge is the raw
    LEFT edge; 180 -> raw bottom; 270 -> raw right.
    """
    src = make_pdf(tmp_path / "src.pdf", [(500, 1000)])
    expected_raw_box = {  # crop {"top": 10} on a 500x1000 raw page
        "1@90": (50.0, 0.0, 500.0, 1000.0),  # raw left cropped by 10% of 500
        "1@180": (0.0, 100.0, 500.0, 1000.0),  # raw bottom cropped
        "1@270": (0.0, 0.0, 450.0, 1000.0),  # raw right cropped
        "1": (0.0, 0.0, 500.0, 900.0),  # unrotated: raw top cropped
    }
    for spec, expected in expected_raw_box.items():
        out = tmp_path / f"out-{spec.replace('@', '-')}.pdf"
        arrange([ArrangeInput(path=src, pages_spec=spec)], out, crop={"top": 10})
        page = PdfReader(str(out)).pages[0]
        for box in (page.mediabox, page.cropbox):
            got = (
                float(box.left), float(box.bottom),
                float(box.right), float(box.top),
            )
            assert got == pytest.approx(expected), spec


def test_arrange_crop_respects_preexisting_source_rotation(tmp_path: Path):
    # The SOURCE page already carries /Rotate 90 — no @delta in the spec.
    src = make_pdf(tmp_path / "rot.pdf", [(500, 1000)], rotations=[90])
    out = tmp_path / "out.pdf"
    arrange([ArrangeInput(path=src)], out, crop={"top": 10})
    box = PdfReader(str(out)).pages[0].mediabox
    assert float(box.left) == pytest.approx(50.0)  # displayed top = raw left
    assert float(box.top) == pytest.approx(1000.0)  # raw top untouched


def test_arrange_crop_non_origin_mediabox(tmp_path: Path):
    """Some PDFs have a mediabox whose lower-left is NOT (0, 0)."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=800)
    page.mediabox = RectangleObject((100, 50, 600, 850))
    src = tmp_path / "offset.pdf"
    with open(src, "wb") as fh:
        writer.write(fh)
    out = tmp_path / "out.pdf"
    arrange(
        [ArrangeInput(path=src)], out, crop={"left": 10, "bottom": 20}
    )
    box = PdfReader(str(out)).pages[0].mediabox
    assert float(box.left) == pytest.approx(150.0)  # 100 + 10% of 500
    assert float(box.bottom) == pytest.approx(210.0)  # 50 + 20% of 800
    assert float(box.right) == pytest.approx(600.0)
    assert float(box.top) == pytest.approx(850.0)


def make_inherited_mediabox_pdf(path: Path) -> Path:
    """A page with NO /MediaBox of its own — inherited from the /Pages node."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 400] >>",
        b"<< /Type /Page /Parent 2 0 R >>",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(buf.tell())
        buf.write(b"%d 0 obj\n%s\nendobj\n" % (i, body))
    xref_at = buf.tell()
    buf.write(b"xref\n0 4\n0000000000 65535 f \n")
    for off in offsets:
        buf.write(b"%010d 00000 n \n" % off)
    buf.write(
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % xref_at
    )
    path.write_bytes(buf.getvalue())
    return path


def test_inherited_mediabox_page_survives_info_and_arrange(tmp_path: Path):
    src = make_inherited_mediabox_pdf(tmp_path / "inherited.pdf")
    info = pdf_info(src)
    assert info["pages"] == 1
    assert info["page_sizes"] == [{"width": 300.0, "height": 400.0}]
    out = tmp_path / "out.pdf"
    report = arrange([ArrangeInput(path=src, pages_spec="1,blank")], out)
    assert report.pages == 2
    sizes = [
        (float(p.mediabox.width), float(p.mediabox.height))
        for p in PdfReader(str(out)).pages
    ]
    assert sizes[0] == (300.0, 400.0)
    assert sizes[1] == (300.0, 400.0)  # blank sized like the inherited page


def test_arrange_refuses_relative_alias_of_input(tmp_path: Path, monkeypatch):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="never overwritten"):
        arrange([ArrangeInput(path="src.pdf")], src.resolve())


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive paths are Windows")
def test_arrange_refuses_case_alias_of_input_windows(tmp_path: Path):
    src = make_pdf(tmp_path / "src.pdf", [(100, 700)])
    with pytest.raises(ValueError, match="never overwritten"):
        arrange([ArrangeInput(path=src)], Path(str(src).upper()))
    assert widths(src) == [100]  # input untouched


def test_owner_only_password_opens_transparently(tmp_path: Path):
    """Blank USER password (owner-only lock) opens without a password, like
    every viewer does; an explicit empty-string password behaves the same."""
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="own", algorithm="RC4-128")
    src = tmp_path / "owner-only.pdf"
    with open(src, "wb") as fh:
        writer.write(fh)
    info = pdf_info(src)
    assert info["encrypted"] is True
    assert info["pages"] == 1
    assert pdf_info(src, password="")["pages"] == 1
    report = arrange([ArrangeInput(path=src)], tmp_path / "out.pdf")
    assert report.pages == 1


def test_split_rollback_removes_parts_keeps_preexisting(
    tmp_path: Path, monkeypatch
):
    src = make_pdf(tmp_path / "doc.pdf", [(100, 700), (200, 700), (300, 700)])
    out_dir = tmp_path / "parts"
    out_dir.mkdir()
    pre = out_dir / "doc-part02.pdf"
    pre.write_bytes(b"precious pre-existing bytes")
    real_write = PdfWriter.write
    calls = {"n": 0}

    def flaky(self, stream, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk died on part 2")
        return real_write(self, stream, *a, **k)

    monkeypatch.setattr(PdfWriter, "write", flaky)
    with pytest.raises(RuntimeError, match="part 2"):
        split(src, out_dir, mode={"per_page": True})
    # All parts or none: part 1 rolled back, no tmp orphans, and the
    # PRE-EXISTING clashing file is byte-identical.
    assert sorted(p.name for p in out_dir.iterdir()) == ["doc-part02.pdf"]
    assert pre.read_bytes() == b"precious pre-existing bytes"
