"""v1.341.1: a restore retries a briefly-held database before giving up.

v1.341.0's Release gate (a 46-minute runner) went red on
``test_restore_replaces_config_and_db_then_schedules_the_stop``: "the database
is still held open ([WinError 32] … ironjarvis.db-wal)". The route disposes
the engine and then deletes the ``-wal`` — but ``Engine.dispose()`` closes
only the connections checked IN; a background loop holding one for a few
milliseconds at that instant keeps the WAL open, and on a slow machine the
window is wide. The restore aborts BEFORE anything moves on that error (the
WAL unlink and DB replace are its first step), so the route now disposes
again and retries a bounded number of times before answering 409.

These drive the route with the real app and a ``restore_backup_live`` that
refuses N times then succeeds (deterministic — no clock), and a mutation of
the loop (one attempt) fails the first test with the gate's exact 409.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis import maintenance as maintenance_mod
from iron_jarvis.daemon import app as app_mod
from iron_jarvis.daemon.routes import settings as settings_routes
from iron_jarvis.daemon.app import create_app


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(settings_routes, "_RESTORE_RETRY_S", 0.0)
    monkeypatch.setattr(app_mod, "_graceful_stop", lambda: None)


def _backup(c: TestClient) -> str:
    assert c.post("/diagnostics/repair", json={"action": "backup_now"}).json()["ok"] is True
    return c.get("/maintenance/backups").json()["backups"][0]["name"]


def test_a_briefly_held_database_is_retried_and_the_restore_succeeds(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    real = maintenance_mod.restore_backup_live
    calls: list[int] = []

    def flaky(home, archive):
        calls.append(1)
        if len(calls) <= 3:
            raise PermissionError(32, "The process cannot access the file", "ironjarvis.db-wal")
        return real(home, archive)

    monkeypatch.setattr(maintenance_mod, "restore_backup_live", flaky)
    with TestClient(app) as c:
        name = _backup(c)
        engine = app.state.d.platform.engine
        disposes: list[int] = []
        real_dispose = engine.dispose
        monkeypatch.setattr(engine, "dispose", lambda *a, **k: (disposes.append(1), real_dispose(*a, **k))[1])
        r = c.post("/maintenance/restore", json={"name": name})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True and r.json()["restored_from"] == name
    assert len(calls) == 4  # three refusals, then the real restore
    # The engine is disposed BEFORE EVERY attempt — a connection that was
    # checked out during the last try is back in the pool and closed by this one.
    assert len(disposes) >= 4


def test_a_database_still_held_after_every_attempt_is_a_409_and_nothing_moved(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    calls: list[int] = []

    def held(home, archive):
        calls.append(1)
        raise PermissionError(32, "The process cannot access the file", "ironjarvis.db-wal")

    monkeypatch.setattr(maintenance_mod, "restore_backup_live", held)
    with TestClient(app) as c:
        name = _backup(c)
        r = c.post("/maintenance/restore", json={"name": name})
        assert r.status_code == 409
        assert "still held open" in r.json()["detail"]
        # Bounded: exactly the cap, never forever.
        assert len(calls) == settings_routes._RESTORE_ATTEMPTS
        # Nothing was replaced: the home still answers.
        assert c.get("/maintenance/backups").status_code == 200


def test_the_retry_is_bounded_in_time():
    # ~2 s of patience at most — a database truly in use must still be told
    # so promptly, and the cap is what makes the 409 honest.
    assert settings_routes._RESTORE_ATTEMPTS * settings_routes._RESTORE_RETRY_S <= 3.0
    assert settings_routes._RESTORE_ATTEMPTS >= 10
