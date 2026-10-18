"""v1.351.0, wave 1 lane D (G11): Open on a chat's work folder opens it.

A no-project chat shows its work folder as a chip with an "Open" button, which
posts the folder to POST /documents/open. That route only knew FILES
(``_preview_path`` 404'd "no such file" on a directory), so the one button that
shows a chat's saved work answered with an error line every time.

Now a folder that passes the same gates (absolute, policy-readable, not a
protected root) opens in the OS file manager; a file opens exactly as before.
Driven through the REAL app factory and the REAL workfolder route; only the OS
launcher is replaced (the module-level ``_open_native`` seam the existing
open test uses), so no window really opens.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import documents as docroutes


@pytest.fixture()
def opened(monkeypatch):
    launched: list[str] = []
    monkeypatch.setattr(docroutes, "_open_native", lambda p: launched.append(p))
    return launched


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    # A chat's folders live under chat_files_dir (<Documents>\Iron Jarvis by
    # default) — point it into the test's own folder.
    c.app.state.platform.config.chat_files_root = str(tmp_path / "Iron Jarvis")
    return c


def _attach(client: TestClient, name: str, data: bytes) -> str:
    res = client.post(
        "/documents/upload",
        json={"filename": name, "content_b64": base64.b64encode(data).decode()},
    )
    assert res.status_code == 200, res.text
    return res.json()["path"]


def test_open_on_a_chats_work_folder_opens_that_folder(client, opened, tmp_path):
    # The user attaches a file in a no-project chat: the app makes its folder.
    upload = _attach(client, "receipts.pdf", b"%PDF fake")
    made = client.post("/documents/workfolder", json={"files": [upload], "title": "receipts.pdf"})
    assert made.status_code == 200, made.text
    folder = made.json()["path"]
    assert Path(folder).is_dir()

    # ...and presses Open on the folder chip (the page posts exactly this).
    res = client.post("/documents/open", json={"path": folder})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["folder"] is True
    assert body["app"] in ("File Explorer", "Finder", "your file manager")
    assert opened == [folder], "the folder itself is handed to the OS, once"


def test_a_file_still_opens_with_its_app(client, opened, tmp_path):
    doc = tmp_path / "memo.docx"
    doc.write_bytes(b"PK\x03\x04fake")
    res = client.post("/documents/open", json={"path": str(doc)})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "app": "Word"}
    assert opened == [str(doc)]


def test_the_same_gates_still_hold_for_a_folder(client, opened, tmp_path):
    platform = client.app.state.platform
    # A protected root (the vault's folder) is refused, folder or not.
    secrets_dir = platform.config.home / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    assert client.post("/documents/open", json={"path": str(secrets_dir)}).status_code == 403
    # A relative path is refused before anything is looked at.
    assert client.post("/documents/open", json={"path": "Iron Jarvis"}).status_code == 400
    # A folder that does not exist is still a 404, never an open.
    ghost = tmp_path / "no-such-folder"
    assert client.post("/documents/open", json={"path": str(ghost)}).status_code == 404
    assert opened == []


def test_a_launcher_failure_is_said_not_swallowed(client, monkeypatch, tmp_path):
    def boom(_p: str) -> None:
        raise OSError("no file manager here")

    monkeypatch.setattr(docroutes, "_open_native", boom)
    folder = tmp_path / "work"
    folder.mkdir()
    res = client.post("/documents/open", json={"path": str(folder)})
    assert res.status_code == 422
    assert "could not open" in res.json()["detail"]
