"""Backups must exclude the regenerable media library (artifacts/creative-thumbs)
so a snapshot captures the state that matters (DB/config/secrets) without ballooning."""

from __future__ import annotations

import tarfile

from iron_jarvis.maintenance import create_backup


def test_backup_keeps_state_and_drops_media(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    (home / "ironjarvis.db").write_text("DBDATA")
    (home / "config.toml").write_text("[core]\n")
    (home / "artifacts").mkdir()
    (home / "artifacts" / "big.bin").write_bytes(b"\0" * 5_000_000)  # ~5 MB of media
    (home / "creative-thumbs").mkdir()
    (home / "creative-thumbs" / "t.jpg").write_bytes(b"\0" * 1_000_000)

    out = tmp_path / "b.tar.gz"
    _, n = create_backup(home, out)

    names = tarfile.open(out).getnames()
    # the state that matters is IN
    assert any(x.endswith("ironjarvis.db") for x in names)
    assert any(x.endswith("config.toml") for x in names)
    # the regenerable media library is OUT
    assert not any("/artifacts/" in x or x.endswith("/artifacts") for x in names)
    assert not any("creative-thumbs" in x for x in names)
    assert not any(x.endswith("big.bin") for x in names)
    assert not any(x.endswith("t.jpg") for x in names)

    # and the archive stays small — media (~6 MB) was the whole point of excluding it
    assert out.stat().st_size < 500_000
    assert n == 2  # only db + config
