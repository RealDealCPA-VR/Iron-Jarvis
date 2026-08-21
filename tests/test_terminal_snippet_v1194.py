"""Screen snippets land ON DISK for a Build pane (POST /terminals/{id}/snippet).

A ConPTY pane has no image channel; the CLIs read images off disk. These pin
the four real defects: same-name collision, a non-image body, an oversize body
(with the limit NAMED), and the file actually existing with the SNIFFED
extension in the pane's own folder.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import terminals as term_routes
from iron_jarvis.terminals.backend import FakeBackend


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: FakeBackend()
    )
    return TestClient(create_app(str(tmp_path)))


def _png(rgb: tuple[int, int, int]) -> bytes:
    """A real 1x1 PNG — the sniffer reads magic bytes, and different colours
    give different CONTENT (so a digest suffix must differ)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = bytes([0, *rgb])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode()


def _pane(client, cwd=None):
    return client.post("/terminals", json={"cwd": str(cwd)} if cwd else {}).json()


def test_snippet_writes_into_the_panes_own_folder(tmp_path, monkeypatch):
    """The path is real, the extension comes from the CONTENT, and the file
    sits inside the pane's cwd so a confined CLI can actually read it."""
    work = tmp_path / "work"
    work.mkdir()
    client = _app(tmp_path, monkeypatch)
    t = _pane(client, work)
    r = client.post(
        f"/terminals/{t['id']}/snippet",
        # The name LIES about the type on purpose: content decides.
        json={"filename": "clip.txt", "content_b64": _b64(_png((1, 2, 3)))},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    p = Path(body["path"])
    assert p.is_file(), "the returned path must exist on disk"
    assert p.suffix == ".png", body
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert body["location"] == "pane"
    assert "note" not in body  # nothing degraded, so nothing to report
    assert body["mime"] == "image/png"
    assert body["bytes"] == len(_png((1, 2, 3)))
    assert work in p.parents
    assert ".ironjarvis" in p.parts  # dotted subfolder, not project clutter
    # A pane's cwd is normally a git repo with an AI CLI running `git add -A`
    # in it, and these are screenshots of client material: the dotted folder
    # must ignore ITSELF, or the next commit sweeps them into the worktree.
    assert (work / ".ironjarvis" / ".gitignore").read_text().strip() == "*"
    assert body["reference"], "a pane needs SOMETHING to type"
    assert str(p) in body["reference"]


def test_two_snippets_named_the_same_do_not_collide(tmp_path, monkeypatch):
    """THE defect: Win+Shift+S hands every capture the same name. The second
    snippet must not overwrite the first."""
    work = tmp_path / "work"
    work.mkdir()
    client = _app(tmp_path, monkeypatch)
    t = _pane(client, work)
    first = client.post(
        f"/terminals/{t['id']}/snippet",
        json={"filename": "image.png", "content_b64": _b64(_png((10, 20, 30)))},
    ).json()
    second = client.post(
        f"/terminals/{t['id']}/snippet",
        json={"filename": "image.png", "content_b64": _b64(_png((200, 100, 50)))},
    ).json()
    assert first["path"] != second["path"], "same filename clobbered the first snippet"
    assert Path(first["path"]).is_file() and Path(second["path"]).is_file()
    assert Path(first["path"]).read_bytes() != Path(second["path"]).read_bytes()


def test_non_image_body_is_refused(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    t = _pane(client)
    r = client.post(
        f"/terminals/{t['id']}/snippet",
        json={"filename": "screenshot.png", "content_b64": _b64(b"not an image at all")},
    )
    assert r.status_code == 415, r.text
    assert "PNG" in r.json()["detail"]


def test_oversize_body_is_refused_and_names_the_limit(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    t = _pane(client)
    monkeypatch.setattr(term_routes, "_SNIPPET_MAX_BYTES", 1024 * 1024)
    big = _b64(b"\x89PNG\r\n\x1a\n" + b"\0" * (2 * 1024 * 1024))
    r = client.post(
        f"/terminals/{t['id']}/snippet",
        json={"filename": "image.png", "content_b64": big},
    )
    assert r.status_code == 413, r.text
    assert "limit is 1 MB" in r.json()["detail"], r.json()["detail"]

    # And the size it reports must not be FLOORED into its own limit: a 1.4MB
    # paste refused with "too large (~1 MB); limit is 1 MB" states a size that
    # is inside the limit it claims to enforce, which reads as a bug.
    over = _b64(b"\x89PNG\r\n\x1a\n" + b"\0" * (1024 * 1024 * 2 // 5 + 1024 * 1024))
    detail = client.post(
        f"/terminals/{t['id']}/snippet",
        json={"filename": "image.png", "content_b64": over},
    ).json()["detail"]
    assert "1.4 MB" in detail, detail


def test_falls_back_to_uploads_and_says_so(tmp_path, monkeypatch):
    """A pane whose folder is gone still gets its snippet — and is TOLD the
    file did not land beside its work."""
    work = tmp_path / "gone"
    work.mkdir()
    client = _app(tmp_path, monkeypatch)
    t = _pane(client, work)
    work.rmdir()
    body = client.post(
        f"/terminals/{t['id']}/snippet",
        json={"filename": "image.png", "content_b64": _b64(_png((7, 7, 7)))},
    ).json()
    assert body["location"] == "uploads"
    assert "does not exist" in body["note"]
    p = Path(body["path"])
    assert p.is_file() and p.parent.name == "uploads"


def test_unknown_terminal_is_404(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    r = client.post(
        "/terminals/nope/snippet",
        json={"filename": "image.png", "content_b64": _b64(_png((1, 1, 1)))},
    )
    assert r.status_code == 404
    # The ROUTE's 404, not FastAPI's no-such-path 404 (which is what a missing
    # feature returns — this assertion is what makes the test mutation-proof).
    assert r.json()["detail"] == "no such terminal"
