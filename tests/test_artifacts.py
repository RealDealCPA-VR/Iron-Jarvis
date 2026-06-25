"""Tests for the Artifact System (SPEC §26)."""

from __future__ import annotations

import iron_jarvis.artifacts.models  # noqa: F401  (register table before init_db)
from iron_jarvis.artifacts.models import ArtifactRecord
from iron_jarvis.artifacts.store import ArtifactStore
from iron_jarvis.core.db import init_db, make_engine, session_scope
from sqlmodel import select


def test_versioning_and_read(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    store.save("report", "v1 content")
    store.save("report", "v2 content")

    assert store.versions("report") == [1, 2]
    assert store.read("report") == b"v2 content"  # latest
    assert store.read("report", 1) == b"v1 content"
    latest = store.latest("report")
    assert latest is not None and latest.version == 2


def test_bytes_and_size(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    art = store.save("blob", b"\x00\x01\x02\x03", kind="binary")

    assert store.read("blob") == b"\x00\x01\x02\x03"
    assert art.size == 4
    assert art.kind == "binary"
    assert store.list_names() == ["blob"]


def test_engine_records_rows(tmp_path):
    engine = make_engine(str(tmp_path / "t.db"))
    init_db(engine)
    store = ArtifactStore(tmp_path / "artifacts", engine=engine)

    store.save("report", "v1 content", session_id="session_abc")
    store.save("report", "v2 content", session_id="session_abc")

    with session_scope(engine) as db:
        rows = db.exec(
            select(ArtifactRecord).where(ArtifactRecord.name == "report")
        ).all()

    assert len(rows) == 2
    assert sorted(r.version for r in rows) == [1, 2]


def test_path_traversal_is_contained(tmp_path):
    root = (tmp_path / "artifacts").resolve()
    store = ArtifactStore(root)

    art = store.save("../evil", "pwned")
    resolved = art.path.resolve()

    # The written file stays inside root — no escape via the traversal name.
    assert root in resolved.parents
    assert store.read("../evil") == b"pwned"
