"""v1.333.0 (lane F) — the daemon side of "the dashboard must not eat the PC".

* F3: ``GET /sessions/{id}`` built and sent the WHOLE transcript (~130 KB on
  a heavy session) on every status poll — the project task strip every 2 s,
  the desktop quick task every 4 s. ``?transcript=0`` is the slim read: the
  same NESTED shape (``{session, transcript}``), ``transcript`` None, and the
  transcript is never built. The default read is unchanged.
* F11: the home's status line and the bell's ``GET /sessions/interrupted``
  filtered and ordered on ``Session.interrupted_at`` with no index (a scan of
  every session on each poll); ``_HOT_INDEXES`` now carries one, and the real
  query reads it. ``/fs/files``'s memo keeps its 2 s bound (a longer one would
  show a change in a subfolder later — lead decision) and is dropped early
  when the folder's own mtime moves, which can only make an answer fresher.

Counts only (transcript builds, walks, query plans) — never wall-clock.
"""

from __future__ import annotations

import os
import types
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import Session as SessionRow, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import fsbrowse
from iron_jarvis.daemon.routes.sessions import interrupted_rows


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _seed(engine, sid: str, status: SessionStatus = SessionStatus.ACTIVE, **fields) -> None:
    with session_scope(engine) as db:
        db.add(SessionRow(id=sid, task=f"task {sid}", status=status, **fields))
        db.commit()


# --------------------------------------------------------------------------
# F3 — the slim session read
# --------------------------------------------------------------------------


def test_the_slim_session_read_keeps_the_nested_shape_and_builds_no_transcript(tmp_path, monkeypatch):
    with _client(tmp_path) as client:
        _seed(client.app.state.platform.engine, "s-slim")
        orch = client.app.state.orchestrator
        built: list[str] = []
        real = orch.transcript

        def _counting(session_id, *a, **kw):
            built.append(session_id)
            return real(session_id, *a, **kw)

        monkeypatch.setattr(orch, "transcript", _counting)

        slim = client.get("/sessions/s-slim?transcript=0")
        assert slim.status_code == 200, slim.text
        body = slim.json()
        assert set(body) == {"session", "transcript"}, "the shape stays NESTED"
        assert body["session"]["id"] == "s-slim"
        assert body["session"]["status"] == "active"
        assert body["transcript"] is None
        assert built == [], "the slim read built the transcript anyway"

        # The default read is unchanged: the transcript is built and sent.
        full = client.get("/sessions/s-slim")
        assert full.status_code == 200
        assert isinstance(full.json()["transcript"], dict)
        assert built == ["s-slim"]


def test_the_slim_read_of_a_missing_session_is_still_a_404(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/sessions/nope?transcript=0")
        assert r.status_code == 404


# --------------------------------------------------------------------------
# F11 — the interrupted-jobs query reads an index
# --------------------------------------------------------------------------


def test_the_interrupted_jobs_query_reads_the_interrupted_at_index(tmp_path):
    with _client(tmp_path) as client:
        engine = client.app.state.platform.engine
        _seed(
            engine,
            "s-cut",
            status=SessionStatus.FAILED,  # what the boot reconcile writes
            interrupted_at=utcnow() - timedelta(hours=1),
        )
        with engine.connect() as conn:
            names = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='session'")
                )
            }
        assert "ix_session_interrupted_at" in names

        # Capture the REAL statement interrupted_rows() sends, then ask SQLite
        # how it would run it.
        seen: list[tuple[str, object]] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            if "interrupted_at" in statement and statement.lstrip().upper().startswith("SELECT"):
                seen.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            with session_scope(engine) as db:
                rows = interrupted_rows(db)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
        assert [r.id for r in rows] == ["s-cut"]
        assert seen, "interrupted_rows sent no query"
        statement, params = seen[0]
        with engine.connect() as conn:
            raw = conn.connection.dbapi_connection
            plan = [str(r) for r in raw.execute("EXPLAIN QUERY PLAN " + statement, params).fetchall()]
        assert any("ix_session_interrupted_at" in line for line in plan), plan


# --------------------------------------------------------------------------
# F11 — /fs/files: the memo outlives one poll, a top-level change ends it
# --------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _counting_walks(monkeypatch):
    import iron_jarvis.fsbrowser as fsb

    walks: list[str] = []
    real = fsb.list_files_recursive

    def _walk(path, *a, **kw):
        walks.append(path)
        return real(path, *a, **kw)

    monkeypatch.setattr(fsb, "list_files_recursive", _walk)
    return walks


def _fresh_memo(monkeypatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(fsbrowse, "time", types.SimpleNamespace(monotonic=clock.monotonic))
    monkeypatch.setattr(fsbrowse, "_files_memo", {})
    monkeypatch.setattr(fsbrowse, "_files_key_locks", {})
    return clock


def test_the_files_memo_keeps_its_two_second_bound(tmp_path, monkeypatch):
    # The mtime check may only make an answer FRESHER: an unchanged folder is
    # still answered from the memo inside 2 s and walked again after it — a
    # longer bound would show a change in a subfolder later than before.
    assert fsbrowse.FILES_MEMO_S == 2.0
    clock = _fresh_memo(monkeypatch)
    walks = _counting_walks(monkeypatch)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    folder = str(tmp_path)

    fsbrowse._files_listing(folder, 4, 600, False)
    assert len(walks) == 1
    clock.now += 1.5  # a second pane on the same folder, inside the memo
    fsbrowse._files_listing(folder, 4, 600, False)
    assert len(walks) == 1, "an unchanged folder inside 2 s walked the tree again"
    clock.now += 1.0  # 2.5 s after the walk: aged out, exactly as before
    fsbrowse._files_listing(folder, 4, 600, False)
    assert len(walks) == 2, "the memo answered past its 2 s bound"


def test_a_new_top_level_file_ends_the_memo_at_once(tmp_path, monkeypatch):
    clock = _fresh_memo(monkeypatch)
    walks = _counting_walks(monkeypatch)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    folder = str(tmp_path)

    first = fsbrowse._files_listing(folder, 4, 600, False)
    assert [f["rel"] for f in first["files"]] == ["a.txt"]
    (tmp_path / "brand_new.txt").write_text("b", encoding="utf-8")
    # Make the folder's mtime move even on a coarse-timestamp filesystem.
    st = os.stat(folder)
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    clock.now += 1.0
    second = fsbrowse._files_listing(folder, 4, 600, False)
    assert len(walks) == 2
    assert "brand_new.txt" in [f["rel"] for f in second["files"]]


def test_a_missing_folder_is_never_answered_from_the_memo(tmp_path, monkeypatch):
    _fresh_memo(monkeypatch)
    gone = str(tmp_path / "nope")
    for _ in range(2):
        try:
            fsbrowse._files_listing(gone, 4, 600, False)
        except (FileNotFoundError, NotADirectoryError):
            pass
        else:  # pragma: no cover - the walk must refuse a missing folder
            raise AssertionError("a missing folder listed")
    assert fsbrowse._files_memo == {}
