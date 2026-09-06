"""The pool waits LONGER than SQLite's busy_timeout (CL6, audit Wave 6, v1.232.0).

Converted from ``tests/_audit_20260904/test_db_audit.py::
test_pool_shape_and_30_concurrent_writers_under_an_external_lock``, which
measured the bug: with QueuePool's default 30 s wait equal to
``busy_timeout``, 30 writers behind a 32 s lock produced 15 "QueuePool limit
... connection timed out" and 2 "database is locked" — half the callers
blamed a connection leak for one stuck writer. Now ``pool_timeout`` sits
5 s above ``busy_timeout`` so the holders fail first and hand their
connections on: every caller that fails says "database is locked", and the
stuck writer is logged ONCE.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import Counter

import pytest
from sqlalchemy import text

from iron_jarvis.core.db import (
    BUSY_TIMEOUT_MS,
    init_db,
    make_engine,
    pool_timeout_for,
    session_scope,
)
from iron_jarvis.core.models import EventRecord


def _engine(tmp_path, **kw):
    p = tmp_path / ".ironjarvis" / "ironjarvis.db"
    eng = make_engine(p, **kw)
    init_db(eng)
    return eng, p


def _hold_lock(p, hold_s: float) -> threading.Thread:
    """An OUTSIDE writer (a VACUUM / a stuck thread) holds the write lock."""
    locker = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN IMMEDIATE")
    locker.execute(
        "INSERT INTO eventrecord(id,type,payload_json,created_at) "
        "VALUES('lock','x','{}',CURRENT_TIMESTAMP)"
    )

    def _release():
        time.sleep(hold_s)
        locker.execute("COMMIT")
        locker.close()

    t = threading.Thread(target=_release, daemon=True)
    t.start()
    return t


def _hammer(eng, n: int = 30) -> list[str]:
    errors: list[str] = []
    lock = threading.Lock()

    def _writer(i: int):
        try:
            with session_scope(eng) as db:
                db.add(EventRecord(id=f"w{i}", type="audit", payload_json="{}"))
                db.commit()
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    ths = [threading.Thread(target=_writer, args=(i,)) for i in range(n)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=120)
    return errors


def test_the_pool_waits_longer_than_busy_timeout(tmp_path):
    eng, _ = _engine(tmp_path)
    assert BUSY_TIMEOUT_MS == 30_000
    assert pool_timeout_for(BUSY_TIMEOUT_MS) == 35.0
    # The production engine, not a test override: 35 s, above the 30 s
    # busy_timeout (SQLAlchemy's default would be 30.0 — equal, the bug).
    assert eng.pool._timeout == 35.0
    with eng.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30_000
    assert eng.pool._timeout > 30_000 / 1000
    # In-memory engines take no pool timeout at all (thread-local pool).
    assert make_engine(":memory:") is not None


def test_a_lock_past_busy_timeout_reads_as_database_is_locked_for_every_caller(
    tmp_path, caplog,
):
    """The same 30-writer hammer, on a short busy_timeout so it runs in
    seconds: with the pool waiting longer than SQLite, no caller ever sees
    the pool's "connection timed out" — only "database is locked" — and the
    stuck writer is logged once, not thirty times."""
    eng, p = _engine(tmp_path, busy_timeout_ms=500)
    assert eng.pool._timeout == pytest.approx(5.5)
    holder = _hold_lock(p, hold_s=1.5)
    with caplog.at_level(logging.WARNING, logger="iron_jarvis.db"):
        errors = _hammer(eng)
    holder.join(timeout=10)
    kinds = Counter(e.split(":")[0] for e in errors)
    assert errors, "a lock past busy_timeout must fail somebody"
    assert kinds.get("TimeoutError", 0) == 0, kinds
    assert all("database is locked" in e for e in errors), errors
    stuck = [r for r in caplog.records if "stuck past busy_timeout" in r.getMessage()]
    assert len(stuck) == 1, [r.getMessage() for r in caplog.records]
    with session_scope(eng) as db:
        n = db.execute(text("select count(*) from eventrecord where type='audit'")).scalar()
    assert n == 30 - len(errors)


# DELIBERATELY NOT TESTED HERE: the audit's original 33-second hammer on the
# PRODUCTION timeouts. It asserted that a 32 s lock must fail somebody, which
# is a statement about how fast this machine schedules 30 threads, not about
# the fix — on a fast box every writer got through the 2 s window after the
# lock lifted and the assertion read as a regression. What it was for is
# pinned deterministically above: the production timeout VALUES by
# ``test_the_pool_waits_longer_than_busy_timeout`` (35 s pool over a 30 s
# busy_timeout, read off the real engine), and the BEHAVIOUR by
# ``test_a_lock_past_busy_timeout_reads_as_database_is_locked_for_every_caller``
# (the same 30-writer hammer on a 500 ms busy_timeout, in ~2 s). See the
# no-absolute-wall-clock-thresholds rule.
