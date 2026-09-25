"""v1.292.0 (deep review platform-02 + platform-08) — the Maintenance card and
the backup agree about the media library.

platform-02: 'Clear generated media' MOVES the library into ``<home>/trash``
(v1.256.0, so the press is recoverable). ``create_backup`` skipped the media
folders but not the trash, so every nightly archive after a clear carried the
700 MB the press was meant to free — and ``mirror_backup`` copied it again.

platform-08: 'What Iron Jarvis is keeping' walked a fixed list of folders and
never counted session workspaces, uploads, the remote inbox, living documents
or written documents — the ones that grow unbounded — so the card could say
0 bytes with gigabytes under the home. Now those are rows, plus an 'Everything
else' catch-all, so ``total_bytes`` is what the disk would say.

Real functions on a real temporary home: no stubs of the layer under test.
"""

from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.maintenance import (
    _MEDIA_DIRS,
    _REPORT_REST_DIR,
    TRASH_DIRNAME,
    clear_media,
    create_backup,
    storage_report,
)


def _aged(path: Path, days: float, payload: bytes = b"v" * 1000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    when = time.time() - days * 86400
    os.utime(path, (when, when))
    return path


def _disk_bytes(home: Path) -> int:
    """An independent accounting of the home — plain rglob, no shared helper."""
    return sum(f.stat().st_size for f in home.rglob("*") if f.is_file())


# --------------------------------------------------------------------------- #
# platform-02 — cleared media never rides into a backup
# --------------------------------------------------------------------------- #
def test_a_backup_taken_after_a_clear_carries_nothing_from_the_trash(tmp_path):
    home = tmp_path / ".ironjarvis"
    (home / "config.toml").parent.mkdir(parents=True)
    (home / "config.toml").write_text("[core]\n")
    _aged(home / "artifacts" / "video" / "clip.mp4", 40, b"\0" * 300_000)

    res = clear_media(home, 30)
    assert res["moved"] == 1
    trashed = list((home / TRASH_DIRNAME).rglob("clip.mp4"))
    assert len(trashed) == 1, "the clear moved it into the trash (still on disk)"

    out, n = create_backup(home, tmp_path / "after-clear.tar.gz")
    with tarfile.open(out) as t:
        names = [m.name for m in t.getmembers()]

    assert not any(f"/{TRASH_DIRNAME}/" in x for x in names), names
    assert not any(x.endswith("clip.mp4") for x in names), names
    # Anti-vacuity: the archive is not empty — the real state is still in it.
    assert any(x.endswith("config.toml") for x in names), names
    assert n == 1, "config.toml only: the trashed clip is not counted either"


# --------------------------------------------------------------------------- #
# platform-08 — the report counts the folders that grow, and the total is the disk
# --------------------------------------------------------------------------- #
def test_the_report_counts_every_growing_folder_and_the_total_is_the_disk(tmp_path):
    home = tmp_path / ".ironjarvis"
    grown = {
        "workspaces": _aged(home / "workspaces" / "sess_1" / "out.bin", 3, b"w" * 4096),
        "uploads": _aged(home / "uploads" / "client-1040.pdf", 3, b"u" * 2048),
        "remote-inbox": _aged(home / "remote-inbox" / "agent" / "report.bin", 3, b"r" * 1024),
        "livedocs": _aged(home / "livedocs" / "brief.md", 3, b"l" * 512),
        "documents": _aged(home / "documents" / "memo.docx", 3, b"d" * 256),
    }
    # Something under a folder NOBODY listed, plus a top-level stray, plus the
    # database sidecars — all must still be in the total.
    _aged(home / "some-future-feature" / "cache.bin", 3, b"f" * 128)
    _aged(home / ".snapshot-leftover.db", 3, b"s" * 64)
    (home / "ironjarvis.db").write_bytes(b"D" * 100)
    (home / "ironjarvis.db-wal").write_bytes(b"W" * 10)

    rep = storage_report(home)
    by_dir = {c["dir"]: c for c in rep["categories"]}

    for dirname, path in grown.items():
        assert dirname in by_dir, f"{dirname} is a row"
        assert by_dir[dirname]["bytes"] == path.stat().st_size, dirname
        assert by_dir[dirname]["files"] == 1, dirname
        assert by_dir[dirname]["clearable"] is False, f"{dirname} must never be offered to clear"
        assert by_dir[dirname]["label"], "a plain-language label, not the folder name"

    rest = by_dir[_REPORT_REST_DIR]
    assert rest["label"] == "Everything else"
    assert rest["clearable"] is False
    assert rest["bytes"] == 128 + 64, "the unlisted folder and the stray, not the DB"
    assert rest["files"] == 2
    assert rest["newest"], "the catch-all reports a newest time like every other row"
    assert by_dir["ironjarvis.db"]["bytes"] == 110, "the DB and its WAL are the Database row"

    # THE INVARIANT: the total is the disk. An independent walk, not the helper.
    assert rep["total_bytes"] == _disk_bytes(home)
    assert rep["total_bytes"] == sum(c["bytes"] for c in rep["categories"])
    assert rep["total_bytes"] > 0


def test_the_new_rows_do_not_widen_what_clear_media_touches(tmp_path):
    """Anti-vacuity for the scope. A file in every new row is old enough to be
    cleared and is COUNTED by the report — and the clear leaves it alone."""
    assert _MEDIA_DIRS == ("artifacts", "creative-thumbs"), "the clear's scope is pinned"
    home = tmp_path / ".ironjarvis"
    kept = [
        _aged(home / "workspaces" / "sess" / "big.bin", 400, b"w" * 5000),
        _aged(home / "uploads" / "old.pdf", 400, b"u" * 5000),
        _aged(home / "remote-inbox" / "agent" / "old.bin", 400, b"r" * 5000),
        _aged(home / "livedocs" / "old.md", 400, b"l" * 5000),
        _aged(home / "documents" / "old.docx", 400, b"d" * 5000),
    ]
    media = _aged(home / "artifacts" / "old.mp4", 400, b"v" * 5000)

    before = {c["dir"]: c for c in storage_report(home)["categories"]}
    assert before["workspaces"]["bytes"] == 5000, "counted before the clear"

    res = clear_media(home, 30)
    assert res["moved"] == 1, "only the media file, however old the others are"
    assert not media.exists()
    for p in kept:
        assert p.exists(), f"{p.parent.name}/{p.name} must survive a media clear"

    after = {c["dir"]: c for c in storage_report(home)["categories"]}
    for dirname in ("workspaces", "uploads", "remote-inbox", "livedocs", "documents"):
        assert after[dirname]["bytes"] == 5000, f"{dirname} still counted, still on disk"
        assert after[dirname]["clearable"] is False
    # Review: the sidebar browser's Chromium profile is named, not buried in
    # "Everything else", and it is never offered to clear.
    assert after["browser"]["clearable"] is False
    assert [c["dir"] for c in storage_report(home)["categories"] if c["clearable"]] == list(
        _MEDIA_DIRS
    )


def test_the_route_serves_the_new_rows_and_a_disk_matching_total(tmp_path):
    """Through the REAL app factory: the card reads /maintenance/storage, so the
    rows have to come out of the route, not just the function."""
    app = create_app(str(tmp_path))
    home = app.state.platform.config.home
    _aged(home / "workspaces" / "sess" / "out.bin", 1, b"w" * 3000)
    _aged(home / "remote-inbox" / "agent" / "in.bin", 1, b"r" * 700)

    with TestClient(app) as c:
        rep = c.get("/maintenance/storage")
        assert rep.status_code == 200, rep.text
        body = rep.json()
        by_dir = {r["dir"]: r for r in body["categories"]}
        assert by_dir["workspaces"]["bytes"] == 3000
        assert by_dir["remote-inbox"]["bytes"] == 700
        assert by_dir[_REPORT_REST_DIR]["label"] == "Everything else"
        assert body["total_bytes"] == sum(r["bytes"] for r in body["categories"])
        assert body["total_bytes"] >= 3700
        # Never offered to clear: the dry run counts media only.
        assert body["candidates"]["files"] == 0
