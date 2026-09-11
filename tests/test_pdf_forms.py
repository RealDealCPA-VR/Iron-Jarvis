"""C-09 — reading and filling PDF form fields.

The fixture is assembled from pypdf primitives because pypdf has no
field-CREATION helper, and it carries `/AP` appearance streams for the same
reason a real form does: without them ``update_page_form_field_values`` raises a
bare ``KeyError: '/AP'`` from inside the library. Both shapes are built here —
with appearances (the normal case) and without (the damaged-form case) — so the
engine's answer to each is pinned.

Fictional values throughout; nothing here resembles a real taxpayer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.documents import pdf_forms
from iron_jarvis.documents.pdf_forms import FormError, fill_form, list_fields


# ----------------------------------------------------------------- fixtures --


def _form(path: Path, *, with_ap: bool = True) -> Path:
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    w = PdfWriter()
    page = w.add_blank_page(width=612, height=792)
    refs: list = []

    def _stream(rect) -> DecodedStreamObject:
        s = DecodedStreamObject()
        s.set_data(b"")
        s[NameObject("/Type")] = NameObject("/XObject")
        s[NameObject("/Subtype")] = NameObject("/Form")
        s[NameObject("/BBox")] = ArrayObject([
            FloatObject(0), FloatObject(0),
            FloatObject(rect[2] - rect[0]), FloatObject(rect[3] - rect[1]),
        ])
        return s

    def widget(extra: dict, *, rect, states: tuple[str, ...] | None = None) -> None:
        base = {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/F"): NumberObject(4),
            NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
            NameObject("/Rect"): ArrayObject([FloatObject(x) for x in rect]),
        }
        if with_ap:
            if states:
                n = DictionaryObject()
                for st in states:
                    n[NameObject(st)] = w._add_object(_stream(rect))
                base[NameObject("/AP")] = DictionaryObject({NameObject("/N"): w._add_object(n)})
            else:
                base[NameObject("/AP")] = DictionaryObject(
                    {NameObject("/N"): w._add_object(_stream(rect))}
                )
        base.update(extra)
        refs.append(w._add_object(DictionaryObject(base)))

    widget({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("Name"),
        NameObject("/V"): TextStringObject(""),
    }, rect=(72, 700, 400, 720))
    widget({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("TIN"),
        NameObject("/V"): TextStringObject(""),
    }, rect=(72, 660, 400, 680))
    widget({
        NameObject("/FT"): NameObject("/Btn"),
        NameObject("/T"): TextStringObject("Exempt"),
        NameObject("/V"): NameObject("/Off"),
        NameObject("/AS"): NameObject("/Off"),
    }, rect=(72, 620, 90, 638), states=("/Off", "/Yes"))

    page[NameObject("/Annots")] = ArrayObject(refs)
    for r in refs:
        r.get_object()[NameObject("/P")] = page.indirect_reference
    w._root_object[NameObject("/AcroForm")] = w._add_object(DictionaryObject({
        NameObject("/Fields"): ArrayObject(refs),
        NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
        NameObject("/NeedAppearances"): BooleanObject(True),
    }))
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def _flat(path: Path) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=300, height=300)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def _values(path: Path) -> dict[str, str]:
    return {f["name"]: str(f.get("value") or "") for f in list_fields(path)}


# ------------------------------------------------------------- 1. reading ---


def test_the_fields_are_listed_with_their_TYPES_and_options(tmp_path):
    fields = list_fields(_form(tmp_path / "w9.pdf"))

    by_name = {f["name"]: f for f in fields}
    assert set(by_name) == {"Name", "TIN", "Exempt"}
    assert by_name["Name"]["type"] == "text"
    assert by_name["Exempt"]["type"] == "checkbox", (
        "a checkbox reported as text would be filled with the word 'yes'"
    )
    assert by_name["Exempt"]["options"] == ["/Off", "/Yes"]


def test_a_flat_pdf_is_answered_PLAINLY_not_as_a_failure(tmp_path):
    """A form with no fields is a normal thing to be handed. Reading it returns
    nothing; only FILLING it is an error, and that error says what to do."""
    flat = _flat(tmp_path / "scan.pdf")

    assert list_fields(flat) == []

    with pytest.raises(FormError) as err:
        fill_form(flat, tmp_path / "out.pdf", {"Name": "x"})
    msg = str(err.value)
    assert "no fillable form fields" in msg and "flat PDF" in msg
    assert "PDF editor" in msg, "the refusal did not say what the user can do instead"
    assert not (tmp_path / "out.pdf").exists()


# ------------------------------------------------------------- 2. filling ---


def test_filling_writes_a_COPY_and_proves_the_values_took(tmp_path):
    src = _form(tmp_path / "w9.pdf")
    before = src.read_bytes()
    dst = tmp_path / "w9 (filled).pdf"

    result = fill_form(src, dst, {
        "Name": "Northwind Consulting LLC",
        "TIN": "00-0000000",
        "Exempt": "yes",
    })

    assert result["filled"] == 3
    assert dst.is_file()
    assert src.read_bytes() == before, "the original form was modified"

    # Read back from the WRITTEN FILE — the only evidence that counts.
    back = _values(dst)
    assert back["Name"] == "Northwind Consulting LLC"
    assert back["TIN"] == "00-0000000"
    assert back["Exempt"].lstrip("/").casefold() == "yes"
    assert result["still_empty"] == []


def test_a_checkbox_takes_yes_or_no_and_refuses_nonsense(tmp_path):
    src = _form(tmp_path / "w9.pdf")

    off = fill_form(src, tmp_path / "off.pdf", {"Exempt": "no"})
    assert off["values"]["Exempt"] == "/Off"
    assert _values(tmp_path / "off.pdf")["Exempt"].lstrip("/").casefold() == "off"

    with pytest.raises(FormError) as err:
        fill_form(src, tmp_path / "bad.pdf", {"Exempt": "perhaps"})
    msg = str(err.value)
    assert "Exempt" in msg and "checkbox" in msg
    assert "/Yes" in msg or "yes/no" in msg
    assert not (tmp_path / "bad.pdf").exists()


def test_an_unknown_field_names_the_fields_that_EXIST(tmp_path):
    src = _form(tmp_path / "w9.pdf")

    with pytest.raises(FormError) as err:
        fill_form(src, tmp_path / "out.pdf", {"Taxpayer Name": "x"})

    msg = str(err.value)
    assert "Taxpayer Name" in msg
    assert "Name" in msg and "TIN" in msg and "Exempt" in msg
    assert not (tmp_path / "out.pdf").exists(), "a failed fill left a file behind"


def test_an_empty_values_map_is_refused_before_anything_is_opened(tmp_path):
    src = _form(tmp_path / "w9.pdf")
    with pytest.raises(FormError, match="at least one field"):
        fill_form(src, tmp_path / "out.pdf", {})
    assert not (tmp_path / "out.pdf").exists()


# ------------------------------------------- 3. the two honesty mechanisms --


def test_a_value_that_did_NOT_take_deletes_the_copy_and_says_so(tmp_path):
    """The redaction lesson, applied to forms: the file is re-opened and the
    values checked, and a copy that does not show them is DELETED rather than
    handed back looking filled. Simulated by making the read-back lie, because a
    real library that silently drops a value is exactly what this guards."""
    src = _form(tmp_path / "w9.pdf")
    dst = tmp_path / "out.pdf"

    monkey = [{"name": "Name", "type": "text", "value": ""}]
    original = pdf_forms.list_fields
    pdf_forms.list_fields = lambda p: monkey  # type: ignore[assignment]
    try:
        with pytest.raises(FormError) as err:
            fill_form(src, dst, {"Name": "Northwind Consulting LLC"})
    finally:
        pdf_forms.list_fields = original  # type: ignore[assignment]

    msg = str(err.value)
    assert "did not take" in msg and "Name" in msg
    assert "no file was kept" in msg
    assert not dst.exists(), (
        "a form that could not be proven filled was left on disk — the exact "
        "outcome this check exists to prevent"
    )


def test_a_form_this_app_cannot_WRITE_is_refused_in_WORDS(tmp_path):
    """A CHECKBOX with no appearance dictionary makes pypdf raise a bare
    `KeyError: '/AP'`. A traceback names nothing the user can act on, and it
    arrives after we have already told them the file HAS fillable fields.

    THE CHECKBOX IS THE CASE, and finding that out is why this test exists in
    this shape: pypdf BUILDS an appearance for a missing text field (it warns
    "Font dictionary for /Helv not found" and carries on), so the same fixture
    filled by name raises nothing at all. Only the button branch reads `/AP`
    directly.
    """
    src = _form(tmp_path / "damaged.pdf", with_ap=False)
    dst = tmp_path / "out.pdf"

    # Reading it still works — the fields are really there.
    assert {f["name"] for f in list_fields(src)} == {"Name", "TIN", "Exempt"}

    with pytest.raises(FormError) as err:
        fill_form(src, dst, {"Exempt": "yes"})

    msg = str(err.value)
    assert "damaged.pdf" in msg
    assert "could not write" in msg
    assert "Nothing was written" in msg
    assert "KeyError" in msg, "the underlying reason was hidden entirely"
    assert not dst.exists()


def test_the_source_is_never_the_destination(tmp_path):
    """Filling in place would destroy the blank form. The engine writes a copy;
    the tool layer refuses an output equal to the source (see office_tools)."""
    src = _form(tmp_path / "w9.pdf")
    dst = tmp_path / "filled.pdf"
    fill_form(src, dst, {"Name": "Aspen Ridge Veterinary"})
    assert _values(src)["Name"] == "", "the blank form was filled in place"
    assert _values(dst)["Name"] == "Aspen Ridge Veterinary"
