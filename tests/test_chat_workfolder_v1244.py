"""v1.244.0 — a chat with no project gets a folder of its own.

The user's report: "When I attach documents in the chat module and ask for a
task to be completed, I often get a lagging delay, a request for information
and then a completed screen with absolutely no output ... Projects are great,
but sometimes I just need to attach a document and ask for some work."

Replayed on the live model (an expense PDF, "put these into an Excel workbook
grouped by category"): with no project the chat had no folder and no file
tools, handed the job to an agent in a hidden AppData scratch folder, which
built the right workbook, stopped twice to ask to run Python, ran out of steps
and reported "Task failed" beside a file nobody could find.

This file pins the daemon half: POST /documents/workfolder makes a dated,
visible, writable folder (``config.chat_files_root``, default
``Documents\\Iron Jarvis``), copies the conversation's uploads into it, keeps a
folder the chat can already work in, and refuses to become a general
file-copy primitive.
"""

from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import userdirs
from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.workfolder import (
    copy_uploads_into,
    folder_title,
    make_chat_workfolder,
)


# --- the pure helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("HarborPoint_Q1_Expenses.pdf", "HarborPoint Q1 Expenses"),
        ("2021 K-1 Giordano.PDF", "2021 K-1 Giordano"),
        ('a<b>:c"d|e?f*g.docx', "a b c d e f g"),
        ("Put these expenses into a workbook", "Put these expenses into a workbook"),
        ("  ...  ", "Chat files"),
        ("", "Chat files"),
        ("CON", "Chat files"),
        ("con.txt", "Chat files"),
    ],
)
def test_folder_title_is_readable_and_windows_safe(raw, want):
    assert folder_title(raw) == want


def test_folder_title_is_capped():
    assert len(folder_title("x" * 300 + ".pdf")) <= 60


def test_the_folder_is_dated_and_never_reused(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    root = tmp_path / "Iron Jarvis"
    first = make_chat_workfolder(root, "Expenses.pdf", [], uploads, today=date(2026, 9, 11))
    second = make_chat_workfolder(root, "Expenses.pdf", [], uploads, today=date(2026, 9, 11))
    assert Path(first["path"]).name == "2026-09-11 Expenses"
    assert Path(second["path"]).name == "2026-09-11 Expenses (2)"
    assert Path(first["path"]).is_dir() and Path(second["path"]).is_dir()


def test_only_the_apps_own_uploads_are_copied(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    upload = uploads / "report.pdf"
    upload.write_bytes(b"%PDF-1.4 fake")
    elsewhere = tmp_path / "secret.txt"
    elsewhere.write_text("not an attachment")
    folder = tmp_path / "conv"
    folder.mkdir()

    copied, skipped = copy_uploads_into(
        folder, [str(upload), str(elsewhere), str(uploads / "gone.pdf"), "relative.pdf"], uploads
    )

    assert [c["name"] for c in copied] == ["report.pdf"]
    assert (folder / "report.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert copied[0]["source"] == str(upload)
    reasons = {Path(s["source"]).name: s["reason"] for s in skipped}
    assert reasons == {
        "secret.txt": "not an attached upload",
        "gone.pdf": "file no longer exists",
        "relative.pdf": "not an absolute path",
    }
    assert not (folder / "secret.txt").exists()


def test_a_name_clash_keeps_both_files(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.pdf").write_bytes(b"new")
    folder = tmp_path / "conv"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"old")
    copied, _ = copy_uploads_into(folder, [str(uploads / "a.pdf")], uploads)
    assert copied[0]["name"] == "a (2).pdf"
    assert (folder / "a.pdf").read_bytes() == b"old"


def test_documents_dir_falls_back_off_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(userdirs.sys, "platform", "linux")
    monkeypatch.setattr(userdirs.Path, "home", classmethod(lambda cls: tmp_path))
    assert userdirs.documents_dir() == tmp_path  # no Documents folder -> home
    (tmp_path / "Documents").mkdir()
    assert userdirs.documents_dir() == tmp_path / "Documents"


def test_the_default_root_is_documents_iron_jarvis(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    cfg = client.app.state.platform.config
    monkeypatch.setattr(userdirs, "documents_dir", lambda: tmp_path / "Docs")
    assert cfg.chat_files_root == ""
    assert cfg.chat_files_dir == tmp_path / "Docs" / "Iron Jarvis"
    cfg.chat_files_root = str(tmp_path / "Elsewhere")
    assert cfg.chat_files_dir == tmp_path / "Elsewhere"


# --- the route ---------------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path):
    client = TestClient(create_app(str(tmp_path / "root")))
    client.app.state.platform.config.chat_files_root = str(tmp_path / "Iron Jarvis")
    return client, tmp_path / "Iron Jarvis"


def _upload(client, name: str, data: bytes) -> str:
    res = client.post(
        "/documents/upload",
        json={"filename": name, "content_b64": base64.b64encode(data).decode()},
    )
    assert res.status_code == 200, res.text
    return res.json()["path"]


def test_attaching_with_no_folder_makes_one_and_copies_the_file(app_client):
    client, root = app_client
    path = _upload(client, "HarborPoint_Q1_Expenses.pdf", b"%PDF fake bytes")

    res = client.post(
        "/documents/workfolder",
        json={"files": [path], "title": "HarborPoint_Q1_Expenses.pdf"},
    )

    assert res.status_code == 200, res.text
    body = res.json()
    folder = Path(body["path"])
    assert body["created"] is True
    assert folder.parent == root
    assert folder.name.endswith(" HarborPoint Q1 Expenses")
    assert [f["name"] for f in body["files"]] == ["HarborPoint_Q1_Expenses.pdf"]
    assert Path(body["files"][0]["path"]).read_bytes() == b"%PDF fake bytes"
    assert body["files"][0]["source"] == path
    assert body["note"] == ""


def test_a_folder_the_chat_can_already_work_in_is_kept(app_client, tmp_path):
    client, root = app_client
    mine = tmp_path / "Clients" / "Smith"
    mine.mkdir(parents=True)
    path = _upload(client, "w2.pdf", b"x")

    body = client.post(
        "/documents/workfolder", json={"files": [path], "prefer": str(mine)}
    ).json()

    assert body == {"path": str(mine), "created": False, "files": [], "skipped": [], "note": ""}
    assert not root.exists()  # nothing was made, nothing was copied


def test_a_folder_the_app_cannot_use_is_replaced_and_the_user_is_told(app_client, tmp_path):
    client, root = app_client
    gone = tmp_path / "no" / "such" / "folder"
    path = _upload(client, "w2.pdf", b"x")

    body = client.post(
        "/documents/workfolder",
        json={"files": [path], "title": "w2.pdf", "prefer": str(gone)},
    ).json()

    assert body["created"] is True
    assert Path(body["path"]).parent == root
    assert str(gone) in body["note"]
    assert body["path"] in body["note"]


def test_later_attachments_join_the_conversations_folder(app_client):
    client, root = app_client
    first = _upload(client, "jan.pdf", b"jan")
    made = client.post("/documents/workfolder", json={"files": [first], "title": "jan.pdf"}).json()
    second = _upload(client, "feb.pdf", b"feb")

    body = client.post(
        "/documents/workfolder", json={"files": [second], "into": made["path"]}
    ).json()

    assert body["created"] is False
    assert body["path"] == made["path"]
    assert (Path(made["path"]) / "feb.pdf").read_bytes() == b"feb"
    assert sorted(p.name for p in root.iterdir()) == [Path(made["path"]).name]  # no second folder


def test_into_must_be_a_folder_this_route_made(app_client, tmp_path):
    client, _ = app_client
    outside = tmp_path / "Desktop"
    outside.mkdir()
    path = _upload(client, "x.pdf", b"x")

    res = client.post("/documents/workfolder", json={"files": [path], "into": str(outside)})

    assert res.status_code == 400
    assert not (outside / "x.pdf").exists()


def test_a_file_that_is_not_an_upload_is_reported_not_copied(app_client, tmp_path):
    client, _ = app_client
    private = tmp_path / "private.txt"
    private.write_text("keep out")

    body = client.post(
        "/documents/workfolder", json={"files": [str(private)], "title": "x"}
    ).json()

    assert body["files"] == []
    assert body["skipped"] == [{"source": str(private), "reason": "not an attached upload"}]
    assert not (Path(body["path"]) / "private.txt").exists()


def test_a_root_that_cannot_be_created_is_refused_by_name(app_client, tmp_path):
    client, _ = app_client
    blocker = tmp_path / "blocker"
    blocker.write_text("a FILE where the root folder should be")
    client.app.state.platform.config.chat_files_root = str(blocker / "Iron Jarvis")

    res = client.post("/documents/workfolder", json={"files": [], "title": "x"})

    assert res.status_code == 409
    assert str(blocker) in res.json()["detail"]


# --- the words the model is given --------------------------------------------------


def test_the_working_folder_is_not_called_a_build_pane_or_a_project(tmp_path):
    """Replayed: with the folder in place, the model told the user the workbook
    was "in your project folder" in a chat with no project — the grounding block
    said the chat was "opened from a Build terminal pane", true only when Build
    panes were the one surface that bound a folder."""
    from iron_jarvis.daemon.chat_turn import _workspace_grounding_block

    block = _workspace_grounding_block(str(tmp_path), (tmp_path, True))
    assert "# Working folder (bound by the user)" in block  # the header v1.210 pins
    assert f"working in the folder: {tmp_path}" in block
    assert "Build terminal pane" not in block
    assert "not a project" in block
    assert '"this project"' not in block
