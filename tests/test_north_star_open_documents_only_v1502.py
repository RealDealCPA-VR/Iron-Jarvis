"""v1.502.0 (north-star plan 2.5): "Open in its app" opens documents only.

THE DEFECT. ``POST /documents/open`` handed ANY readable file to the OS's
own "open" (``os.startfile`` on Windows). For a ``.bat``, ``.hta``, ``.exe``,
``.lnk``, ``.js`` or ``.ps1`` that is not "open", it is RUN — and the path can
come from a model's reply, a downloaded file or a file a client sent.

THE RULE. A folder opens (in the file manager), and a file whose type is on
the document / picture / media allow list opens in its app. Anything else —
an allow list, not a deny list, so an unknown type is refused too — answers a
plain refusal that says to open its folder instead, and nothing is started.
Driven through the real app factory; only the OS launcher is replaced (the
module-level ``_open_native`` seam).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import documents as docroutes

REFUSED = (".bat", ".cmd", ".hta", ".exe", ".lnk", ".js", ".vbs", ".ps1", ".scr",
           ".msi", ".jar", ".py", ".com", ".reg", ".url", ".wsf", ".appref-ms", "")
OPENS = (".docx", ".xlsx", ".pdf", ".csv", ".txt", ".md", ".png", ".jpg", ".mp3", ".mp4", ".pptx")


@pytest.fixture()
def opened(monkeypatch):
    launched: list[str] = []
    monkeypatch.setattr(docroutes, "_open_native", lambda p: launched.append(p))
    return launched


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.mark.parametrize("suffix", REFUSED)
def test_a_program_or_unknown_type_is_refused_and_never_started(client, opened, tmp_path, suffix):
    f = tmp_path / f"run-me{suffix}"
    f.write_bytes(b"echo hello\r\n")
    res = client.post("/documents/open", json={"path": str(f)})
    assert res.status_code == 415, res.text
    detail = res.json()["detail"]
    assert "open its folder" in detail.lower(), detail
    assert opened == [], "a program was handed to the OS to open"


@pytest.mark.parametrize("suffix", [".BAT", ".Exe"])
def test_the_type_is_read_whatever_its_case(client, opened, tmp_path, suffix):
    f = tmp_path / f"loud{suffix}"
    f.write_bytes(b"x")
    assert client.post("/documents/open", json={"path": str(f)}).status_code == 415
    assert opened == []


@pytest.mark.parametrize("suffix", OPENS)
def test_a_document_picture_or_media_file_still_opens(client, opened, tmp_path, suffix):
    f = tmp_path / f"file{suffix}"
    f.write_bytes(b"x")
    res = client.post("/documents/open", json={"path": str(f)})
    assert res.status_code == 200, res.text
    assert opened == [str(f)]


def test_a_folder_still_opens(client, opened, tmp_path):
    folder = tmp_path / "work.bat"  # a folder's name is not a type
    folder.mkdir()
    res = client.post("/documents/open", json={"path": str(folder)})
    assert res.status_code == 200 and res.json()["folder"] is True
    assert opened == [str(folder)]
