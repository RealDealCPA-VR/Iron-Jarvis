"""v1.249.0 (R-05): backups also go to a second drive, generated media included.

Every archive lived inside the home, on the same disk as the data it protects,
and the generated-media library (artifacts/ + creative-thumbs/, paid
generations) was in no backup at all. A mirror folder now gets:

- a copy of each new archive after every automatic AND manual backup, pruned to
  the same keep count — touching ONLY files named like the app's own archives;
- an INCREMENTAL copy of the media library under <mirror>/media (new or
  changed files by size + modified time), bounded, and saying when it stopped
  early;
- a recorded outcome the Backups card, the doctor and the Overview read. A copy
  that fails never fails the local backup.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis import maintenance
from iron_jarvis.daemon.app import create_app
from iron_jarvis.onboarding.doctor import runtime_checks

ARCHIVE_GLOB = "ironjarvis-backup-*.tar.gz"


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "state" / ".ironjarvis"
    (home / "memory").mkdir(parents=True)
    (home / "config.toml").write_text("default_provider = 'mock'\n", encoding="utf-8")
    (home / "memory" / "note.md").write_text("fictional note\n", encoding="utf-8")
    return home


def _media(home: Path) -> None:
    (home / "artifacts" / "sub").mkdir(parents=True, exist_ok=True)
    (home / "creative-thumbs").mkdir(parents=True, exist_ok=True)
    (home / "artifacts" / "a.png").write_bytes(b"x" * 10)
    (home / "artifacts" / "sub" / "b.mp4").write_bytes(b"y" * 20)
    (home / "creative-thumbs" / "t.jpg").write_bytes(b"z" * 5)


def _archive(home: Path, name: str = "ironjarvis-backup-20260911-120000.tar.gz") -> Path:
    p = home / "backups" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"archive bytes")
    return p


# --------------------------------------------------------------- the archive copy


def test_the_newest_archive_is_copied_and_only_the_apps_own_files_are_pruned(tmp_path):
    home = _home(tmp_path)
    mirror = tmp_path / "second-drive"
    mirror.mkdir()
    (mirror / "notes.txt").write_text("the user's own file", encoding="utf-8")
    (mirror / "ironjarvis-backup-old.zip").write_bytes(b"not an app archive")
    for i, day in enumerate(("20260901", "20260902", "20260903")):
        old = mirror / f"ironjarvis-backup-{day}-000000.tar.gz"
        old.write_bytes(b"old")
        t = time.time() - 86400 * (4 - i)
        os.utime(old, (t, t))
    archive = _archive(home)

    state = maintenance.mirror_backup(home, archive, str(mirror), keep=2, media=False)

    assert state["ok"] is True
    names = sorted(p.name for p in mirror.glob(ARCHIVE_GLOB))
    assert names == ["ironjarvis-backup-20260903-000000.tar.gz", archive.name]
    assert state["pruned"] == 2
    # Never deleted: files the app did not put there.
    assert (mirror / "notes.txt").read_text(encoding="utf-8") == "the user's own file"
    assert (mirror / "ironjarvis-backup-old.zip").exists()
    # No half-written temp copy is left behind.
    assert not list(mirror.glob("*.tmp"))


# --------------------------------------------------------------- media, incremental


def test_media_is_copied_once_then_only_what_changed(tmp_path):
    home = _home(tmp_path)
    _media(home)
    dest = tmp_path / "second-drive" / "media"

    first = maintenance.mirror_media(home, dest)
    assert first["copied"] == 3 and first["failed"] == 0 and not first["truncated"]
    assert (dest / "artifacts" / "sub" / "b.mp4").read_bytes() == b"y" * 20
    assert (dest / "creative-thumbs" / "t.jpg").exists()

    second = maintenance.mirror_media(home, dest)
    assert second["copied"] == 0 and second["unchanged"] == 3

    (home / "artifacts" / "a.png").write_bytes(b"x" * 11)  # a changed file
    third = maintenance.mirror_media(home, dest)
    assert third["copied"] == 1 and third["unchanged"] == 2
    assert (dest / "artifacts" / "a.png").read_bytes() == b"x" * 11


def test_a_media_pass_is_bounded_and_says_it_stopped_early(tmp_path):
    home = _home(tmp_path)
    _media(home)
    dest = tmp_path / "second-drive" / "media"

    capped = maintenance.mirror_media(home, dest, max_files=2)
    assert capped["copied"] == 2 and capped["truncated"] is True

    rest = maintenance.mirror_media(home, dest, max_files=2)
    assert rest["copied"] == 1 and rest["truncated"] is False


# --------------------------------------------------------------- never fails the backup


def test_a_missing_folder_is_recorded_and_the_local_backup_still_succeeds(tmp_path):
    home = _home(tmp_path)
    gone = tmp_path / "unplugged" / "backups"
    cfg = SimpleNamespace(backup_mirror_dir=str(gone), backup_mirror_media=True)

    out = maintenance.run_auto_backup(home, keep=3, config=cfg)

    assert out.exists()  # the local backup is unaffected
    st = maintenance.mirror_status(home, cfg)
    assert st["configured"] and st["missing"]
    assert st["last"]["ok"] is False
    assert "does not exist" in st["last"]["error"]
    health = maintenance.mirror_loop_health(st)
    assert health["ok"] is False and "plugged in" in health["last_error"]


def test_a_backup_copies_archive_and_media_when_the_folder_is_there(tmp_path):
    home = _home(tmp_path)
    _media(home)
    mirror = tmp_path / "second-drive"
    mirror.mkdir()
    cfg = SimpleNamespace(backup_mirror_dir=str(mirror), backup_mirror_media=True)

    out = maintenance.run_auto_backup(home, keep=3, config=cfg)

    assert (mirror / out.name).read_bytes() == out.read_bytes()
    assert (mirror / "media" / "artifacts" / "a.png").exists()
    st = maintenance.mirror_status(home, cfg)
    assert st["last"]["ok"] is True and st["last"]["archive"] == out.name
    assert st["last"]["media"]["copied"] == 3
    assert maintenance.mirror_loop_health(st)["ok"] is True
    # Media off: the archive is still copied, media is not.
    other = tmp_path / "third"
    other.mkdir()
    maintenance.run_auto_backup(
        home, keep=3, config=SimpleNamespace(backup_mirror_dir=str(other), backup_mirror_media=False)
    )
    assert list(other.glob(ARCHIVE_GLOB)) and not (other / "media").exists()


def test_status_for_a_different_folder_is_not_shown_as_this_folders(tmp_path):
    home = _home(tmp_path)
    a = tmp_path / "a"
    a.mkdir()
    maintenance.mirror_backup(home, _archive(home), str(a), media=False)
    b = tmp_path / "b"
    b.mkdir()
    st = maintenance.mirror_status(home, SimpleNamespace(backup_mirror_dir=str(b), backup_mirror_media=True))
    assert st["last"] is None
    off = maintenance.mirror_status(home, SimpleNamespace(backup_mirror_dir="", backup_mirror_media=True))
    assert off["configured"] is False and maintenance.mirror_loop_health(off) is None


# --------------------------------------------------------------- the settings door


def test_settings_refuses_a_folder_that_cannot_hold_the_copies(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    home = client.app.state.platform.config.home

    missing = tmp_path / "no-such-drive"
    r = client.put("/settings", json={"values": {"backup_mirror_dir": str(missing)}})
    assert r.status_code == 400 and "backup copy folder" in r.json()["detail"]

    inside = Path(home) / "copies"
    inside.mkdir(parents=True)
    r = client.put("/settings", json={"values": {"backup_mirror_dir": str(inside)}})
    assert r.status_code == 400 and "own data folder" in r.json()["detail"]

    good = tmp_path / "second-drive"
    good.mkdir()
    r = client.put("/settings", json={"values": {"backup_mirror_dir": f"  {good}  ", "backup_mirror_media": False}})
    assert r.status_code == 200, r.text
    s = client.get("/settings").json()["settings"]
    assert s["backup_mirror_dir"] == str(good) and s["backup_mirror_media"] is False

    r = client.put("/settings", json={"values": {"backup_mirror_dir": ""}})
    assert r.status_code == 200 and client.get("/settings").json()["settings"]["backup_mirror_dir"] == ""


def test_back_up_now_copies_to_the_second_drive_and_the_card_can_read_it(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _media(Path(platform.config.home))
    mirror = tmp_path / "second-drive"
    mirror.mkdir()
    assert client.put("/settings", json={"values": {"backup_mirror_dir": str(mirror)}}).status_code == 200

    r = client.post("/diagnostics/repair", json={"action": "backup_now"})
    assert r.status_code == 200, r.text
    body = r.json()
    name = Path(body["result"]).name
    assert body["mirror"]["last"]["ok"] is True and body["mirror"]["last"]["archive"] == name
    assert (mirror / name).exists()
    assert (mirror / "media" / "artifacts" / "sub" / "b.mp4").exists()

    listing = client.get("/maintenance/backups").json()
    assert listing["mirror"]["dir"] == str(mirror) and listing["mirror"]["missing"] is False
    loops = client.get("/diagnostics").json()["background_loops"]
    assert loops["backup_mirror"]["ok"] is True

    # Switching the copy off retires its loop entry.
    client.put("/settings", json={"values": {"backup_mirror_dir": ""}})
    assert "backup_mirror" not in client.get("/diagnostics").json()["background_loops"]


# --------------------------------------------------------------- the health line


def test_the_doctor_names_a_missing_drive(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    mirror = tmp_path / "second-drive"
    mirror.mkdir()
    client.put("/settings", json={"values": {"backup_mirror_dir": str(mirror)}})

    ok = [c for c in runtime_checks(platform) if c["name"] == "backup_mirror"]
    assert ok and ok[0]["ok"] is True

    mirror.rename(tmp_path / "unplugged")
    bad = [c for c in runtime_checks(platform) if c["name"] == "backup_mirror"]
    assert bad and bad[0]["ok"] is False
    assert str(mirror) in bad[0]["detail"] and "plugged in" in bad[0]["detail"]


def test_no_health_line_when_no_copy_is_set_up(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    names = [c["name"] for c in runtime_checks(client.app.state.platform)]
    assert "backup_mirror" not in names


# --------------------------------------------------------------- the automatic loop


def _read(rel: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_automatic_backup_loop_hands_the_live_config_to_the_copy():
    src = _read("src/iron_jarvis/daemon/app.py")
    loop = src[src.index("async def _auto_backup_loop"):]
    loop = loop[: loop.index("backup_task = asyncio.create_task")]
    assert "config=platform.config" in loop
    assert 'loop_health["backup_mirror"]' in loop and "mirror_loop_health(" in loop
    # Still off the event loop.
    assert "await asyncio.to_thread(\n                            run_auto_backup," in loop
