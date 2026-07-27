"""Deterministic, confirm-first PII redaction over HTTP (v1.106.0).

REPORTED: "when using the PII redaction tool, it didn't seem to work at all and
it routed to a folder without asking me where to put this … the PII tool did not
show me which items it recognized as PII data for my approval."

All three were true, and they shared one cause: redaction was only reachable
through an AGENT. The confirm-first contract lived in a tool description and a
skill playbook — advice a model may or may not take — and the shipped
pii-redaction skill went straight to ``redact_pii``, so the approval list was
never shown and the destination silently defaulted (to the WORKSPACE ROOT when
the source lived outside it, which is the "routed to a folder" part).

The engine itself was never the problem: a 12-candidate tax organizer redacts
with zero leaks, valid docx, bold runs and tables intact. These routes put that
engine behind a UI the user drives, so the approval step cannot be skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

SAMPLE = (
    "Taxpayer: Robert J. Alvarez\n"
    "SSN: 412-88-7391\n"
    "Email: r.alvarez@northwindcpa.com\n"
    "Phone: (617) 555-0142\n"
)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def doc(tmp_path):
    p = tmp_path / "client_docs" / "organizer.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def _scan(client, doc, **kw):
    return client.post("/documents/redact/scan", json={"path": str(doc), **kw})


# --- step 1: the approval list ----------------------------------------------


def test_scan_lists_what_it_found(client, doc):
    r = _scan(client, doc)
    assert r.status_code == 200
    vals = {f["value"] for f in r.json()["findings"]}
    assert "412-88-7391" in vals
    assert "r.alvarez@northwindcpa.com" in vals


def test_every_finding_is_reviewable(client, doc):
    """The UI renders a checkbox per finding, so each needs a stable id, a
    human label, the value, how many times it occurs and where."""
    for f in _scan(client, doc).json()["findings"]:
        assert {"id", "label", "value", "count", "category", "context"} <= set(f)


def test_scan_offers_the_destination_before_anything_is_written(client, doc):
    """The "routed to a folder without asking" half: the default lands BESIDE
    the source and is returned up front so the UI can show and edit it."""
    out = _scan(client, doc).json()["default_output_path"]
    assert out.endswith("organizer.redacted.txt")
    assert str(doc.parent) in out


def test_scan_catches_names_only_a_human_spots(client, doc):
    r = _scan(client, doc, extra_terms=["Robert J. Alvarez"])
    assert "Robert J. Alvarez" in {f["value"] for f in r.json()["findings"]}


def test_a_user_supplied_term_is_labelled_as_one(client, doc):
    """The review badge and the in-document replacement have DIFFERENT jobs.
    "custom" renders as [REDACTED] inside a file — right there — but as a badge
    beside a value the user typed it reads as a status, not as "this is the one
    that came from you". Verified together so the split can't quietly collapse.
    """
    r = _scan(client, doc, extra_terms=["Robert J. Alvarez"])
    f = next(x for x in r.json()["findings"] if x["value"] == "Robert J. Alvarez")
    assert f["category"] == "custom"
    assert f["label"] == "TERM"

    from iron_jarvis.documents.redact import mask_text

    masked, _ = mask_text(SAMPLE, style="label", only_terms=["Robert J. Alvarez"])
    assert "[REDACTED]" in masked  # the DOCUMENT still says REDACTED


def test_scan_writes_nothing(client, doc, tmp_path):
    before = {p.name for p in doc.parent.iterdir()}
    _scan(client, doc)
    assert {p.name for p in doc.parent.iterdir()} == before


def test_scan_rejects_an_unknown_category(client, doc):
    assert _scan(client, doc, categories=["nope"]).status_code == 400


def test_scan_404s_on_a_missing_file(client, tmp_path):
    r = client.post("/documents/redact/scan", json={"path": str(tmp_path / "nope.txt")})
    assert r.status_code == 404


# --- step 2: redact exactly what was approved --------------------------------


def _apply(client, doc, terms, **kw):
    return client.post(
        "/documents/redact/apply", json={"path": str(doc), "terms": terms, **kw}
    )


def test_only_confirmed_items_are_redacted(client, doc):
    """The heart of the contract. Ticking the SSN must NOT quietly take the
    email along with it."""
    r = _apply(client, doc, ["412-88-7391"])
    assert r.status_code == 200
    out = (doc.parent / "organizer.redacted.txt").read_text(encoding="utf-8")
    assert "412-88-7391" not in out
    assert "r.alvarez@northwindcpa.com" in out  # never approved — still there


def test_an_empty_confirmation_is_refused(client, doc):
    """Load-bearing: the engine treats an empty `only_terms` as "auto-detect
    everything", so passing [] straight through would redact items the user
    explicitly did not tick — the exact surprise being fixed here."""
    r = _apply(client, doc, [])
    assert r.status_code == 400
    assert not (doc.parent / "organizer.redacted.txt").exists()


def test_output_keeps_the_source_format(client, doc):
    assert _apply(client, doc, ["412-88-7391"]).json()["name"].endswith(".txt")


def test_the_output_goes_where_the_user_asked(client, doc, tmp_path):
    dest = tmp_path / "somewhere else" / "clean.txt"
    r = _apply(client, doc, ["412-88-7391"], output_path=str(dest))
    assert r.status_code == 200
    assert dest.is_file()
    assert "412-88-7391" not in dest.read_text(encoding="utf-8")


def test_the_original_is_never_modified(client, doc):
    _apply(client, doc, ["412-88-7391"])
    assert doc.read_text(encoding="utf-8") == SAMPLE


def test_it_refuses_to_overwrite_the_source(client, doc):
    assert _apply(client, doc, ["412-88-7391"], output_path=str(doc)).status_code == 400
    assert doc.read_text(encoding="utf-8") == SAMPLE


def test_it_will_not_clobber_an_existing_file_by_default(client, doc, tmp_path):
    dest = tmp_path / "taken.txt"
    dest.write_text("do not lose me", encoding="utf-8")
    assert _apply(client, doc, ["412-88-7391"], output_path=str(dest)).status_code == 409
    assert dest.read_text(encoding="utf-8") == "do not lose me"


def test_overwrite_is_available_when_asked_for(client, doc, tmp_path):
    dest = tmp_path / "taken.txt"
    dest.write_text("replace me", encoding="utf-8")
    r = _apply(client, doc, ["412-88-7391"], output_path=str(dest), overwrite=True)
    assert r.status_code == 200
    assert "replace me" not in dest.read_text(encoding="utf-8")


def test_a_relative_destination_is_refused(client, doc):
    """Ambiguous relative paths are how a file ends up somewhere the user did
    not choose. Make the caller be explicit."""
    assert _apply(client, doc, ["412-88-7391"], output_path="out.txt").status_code == 400


def test_styles_are_honoured(client, doc, tmp_path):
    dest = tmp_path / "labelled.txt"
    _apply(client, doc, ["412-88-7391"], style="label", output_path=str(dest))
    assert "[SSN]" in dest.read_text(encoding="utf-8")


def test_an_unknown_style_is_refused(client, doc):
    assert _apply(client, doc, ["x"], style="neon").status_code == 400


def test_the_result_reports_what_it_did(client, doc):
    body = _apply(client, doc, ["412-88-7391", "r.alvarez@northwindcpa.com"]).json()
    assert body["total"] == 2
    assert body["counts"] and body["path"].endswith(".txt")


def test_docx_keeps_its_formatting(client, tmp_path):
    """The user asked for "a file that is redacted in the same format as the
    document provided" — for Office files that means the real thing, not a
    text dump."""
    docx = pytest.importorskip("docx")
    src = tmp_path / "organizer.docx"
    d = docx.Document()
    d.add_paragraph("SSN: 412-88-7391")
    d.add_paragraph().add_run("Bold line").bold = True
    d.add_table(rows=1, cols=1).cell(0, 0).text = "Email: r.alvarez@northwindcpa.com"
    d.save(str(src))

    client_ = TestClient(create_app(str(tmp_path / "home2")))
    r = client_.post(
        "/documents/redact/apply",
        json={"path": str(src), "terms": ["412-88-7391", "r.alvarez@northwindcpa.com"]},
    )
    assert r.status_code == 200
    out = docx.Document(str(tmp_path / "organizer.redacted.docx"))
    text = "\n".join(p.text for p in out.paragraphs)
    text += out.tables[0].cell(0, 0).text
    assert "412-88-7391" not in text
    assert "r.alvarez@northwindcpa.com" not in text
    assert any(run.bold for p in out.paragraphs for run in p.runs)  # styling intact
    assert out.tables  # structure intact


# ---------------------------------------------------------------------------
# v1.107.0 — "give me options with buttons as to where to store the file".
#
# Chat runs its tools inside a confined workspace (the uploads scratch dir, or
# the grounded project folder), so anything it produces lands THERE by
# construction — right for confinement, useless as a place to find a finished
# document. The preview panel now offers real destinations.
# ---------------------------------------------------------------------------


def test_places_are_real_folders_on_this_machine(client):
    places = client.get("/documents/places").json()["places"]
    assert all({"key", "label", "path"} <= set(p) for p in places)
    for p in places:
        assert Path(p["path"]).is_dir()


def test_a_produced_file_can_be_saved_where_the_user_points(client, doc, tmp_path):
    dest = tmp_path / "Desktop"
    dest.mkdir()
    r = client.post(
        "/documents/save-copy", json={"source": str(doc), "dest_dir": str(dest)}
    )
    assert r.status_code == 200
    assert (dest / doc.name).read_text(encoding="utf-8") == SAMPLE
    assert doc.is_file()  # a COPY — the original stays put


def test_saving_can_rename(client, doc, tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    client.post(
        "/documents/save-copy",
        json={"source": str(doc), "dest_dir": str(dest), "name": "clean.txt"},
    )
    assert (dest / "clean.txt").is_file()


def test_it_will_not_silently_replace_a_file(client, doc, tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / doc.name).write_text("mine", encoding="utf-8")
    r = client.post(
        "/documents/save-copy", json={"source": str(doc), "dest_dir": str(dest)}
    )
    assert r.status_code == 409
    assert (dest / doc.name).read_text(encoding="utf-8") == "mine"


def test_replacing_is_possible_once_asked_for(client, doc, tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / doc.name).write_text("mine", encoding="utf-8")
    r = client.post(
        "/documents/save-copy",
        json={"source": str(doc), "dest_dir": str(dest), "overwrite": True},
    )
    assert r.status_code == 200
    assert (dest / doc.name).read_text(encoding="utf-8") == SAMPLE


def test_saving_onto_itself_is_refused(client, doc):
    r = client.post(
        "/documents/save-copy", json={"source": str(doc), "dest_dir": str(doc.parent)}
    )
    assert r.status_code == 400


def test_a_missing_folder_is_404_not_a_silent_mkdir(client, doc, tmp_path):
    """"Save to Desktop" must not invent a Desktop — a file the user cannot
    find is the whole complaint being fixed."""
    r = client.post(
        "/documents/save-copy",
        json={"source": str(doc), "dest_dir": str(tmp_path / "nope")},
    )
    assert r.status_code == 404
    assert not (tmp_path / "nope").exists()


def test_a_relative_folder_is_refused(client, doc):
    r = client.post(
        "/documents/save-copy", json={"source": str(doc), "dest_dir": "Desktop"}
    )
    assert r.status_code == 400
