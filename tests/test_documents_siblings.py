"""GET /documents/siblings — what "Compare with…" offers (C-07).

The preview panel needs the OTHER documents a file sits beside, and it gets them
from the folder rather than from the page: a conversation's files live together
since v1.244.0, so the folder IS the conversation's file list. That choice is
what makes the feature reachable without the chat page having to hand the panel
anything — so these tests pin the contract the panel relies on: comparable files
only, never the file itself, read-gated, sorted, capped, and a missing folder
answered as "nothing" rather than an error.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import routes as _routes
from iron_jarvis.daemon.app import create_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "root")))


def _folder(tmp_path: Path, names: list[str]) -> Path:
    folder = tmp_path / "2026-09-11 Northwind"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")
    return folder


def _names(body: dict) -> list[str]:
    return [f["name"] for f in body["files"]]


def test_the_other_documents_in_the_folder_are_offered(client, tmp_path):
    folder = _folder(tmp_path, [
        "engagement 2025.docx", "engagement 2024.docx", "fees.xlsx", "notes.txt",
    ])
    here = folder / "engagement 2025.docx"

    res = client.get("/documents/siblings", params={"path": str(here)})

    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {"files", "truncated"}
    # Sorted by name, case-insensitively — a stable order for a <select>.
    assert _names(body) == ["engagement 2024.docx", "fees.xlsx", "notes.txt"]
    assert body["truncated"] is False
    # Every entry carries the absolute path the comparison will be run on.
    assert all(Path(f["path"]).is_file() for f in body["files"])
    assert str(folder) in body["files"][0]["path"]


def test_the_file_ITSELF_is_never_offered(client, tmp_path):
    """Comparing a document with itself is the one answer that is never useful,
    and it is the default a naive listing would produce."""
    folder = _folder(tmp_path, ["only.docx"])

    res = client.get("/documents/siblings", params={"path": str(folder / "only.docx")})

    assert res.status_code == 200
    assert res.json()["files"] == []


def test_files_the_comparison_cannot_read_are_left_out(client, tmp_path):
    folder = _folder(tmp_path, [
        "letter.docx", "photo.png", "archive.zip", "installer.exe", "book.xls",
        "sheet.csv", "deck.pptx",
    ])
    (folder / "subfolder").mkdir()

    res = client.get("/documents/siblings", params={"path": str(folder / "letter.docx")})

    assert _names(res.json()) == ["book.xls", "deck.pptx", "sheet.csv"], (
        "an offer the comparison engine cannot honour is worse than no offer"
    )


def test_a_folder_that_is_not_there_is_EMPTY_not_an_error(client, tmp_path):
    """The panel asks for siblings on every preview, including of a file whose
    folder has since gone. That must not paint an error over a working preview."""
    res = client.get(
        "/documents/siblings", params={"path": str(tmp_path / "gone" / "file.docx")}
    )
    assert res.status_code == 200
    assert res.json() == {"files": [], "truncated": False}


def test_the_cap_is_REPORTED_when_it_bites(client, tmp_path):
    folder = _folder(tmp_path, [f"doc {i:03d}.docx" for i in range(70)])

    res = client.get("/documents/siblings", params={"path": str(folder / "doc 000.docx")})

    body = res.json()
    assert len(body["files"]) == 50
    assert body["truncated"] is True, (
        "a silently short list reads as the whole folder"
    )


def test_a_path_the_file_policy_refuses_is_a_403(client, tmp_path, monkeypatch):
    """The same gate every read goes through. Patched at the route's own name so
    the refusal is asserted rather than a protected path being guessed at."""
    folder = _folder(tmp_path, ["a.docx", "b.docx"])
    monkeypatch.setattr(
        _routes.documents, "fs_read_ok",
        lambda p: (False, "path is not readable under the file policy"),
    )

    res = client.get("/documents/siblings", params={"path": str(folder / "a.docx")})

    assert res.status_code == 403
    assert "file policy" in res.json()["detail"]


def test_a_sibling_the_policy_refuses_is_dropped_from_the_list(client, tmp_path):
    """The gate runs per ENTRY too: the folder may hold something this install is
    not allowed to read, and offering it would produce a 403 on click."""
    folder = _folder(tmp_path, ["a.docx", "secret.docx"])
    real = _routes.documents.fs_read_ok

    def gate(p):
        return (False, "refused") if Path(str(p)).name == "secret.docx" else real(p)

    _routes.documents.fs_read_ok = gate  # type: ignore[assignment]
    try:
        res = client.get("/documents/siblings", params={"path": str(folder / "a.docx")})
    finally:
        _routes.documents.fs_read_ok = real  # type: ignore[assignment]

    assert res.status_code == 200
    assert _names(res.json()) == []
