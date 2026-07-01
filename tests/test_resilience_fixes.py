"""Resilience / self-correction regression tests (the chaos-lens fixes).

Covers: corrupt-DB self-heal at boot (open_db quarantine), backups excluding
disposable workspaces, self-update tagging the pre-update commit for a reliable
rollback, and restore/backup not needing the platform. Offline.
"""

from __future__ import annotations

import tarfile

from sqlmodel import Session, select

from iron_jarvis.core.db import open_db, quarantine_db
from iron_jarvis.core.models import EventRecord
from iron_jarvis.core.updates import RunResult, apply_update
from iron_jarvis.maintenance import create_backup


# --- CHAOS-DB-1: a corrupt DB self-heals so the daemon still boots -------------


def test_open_db_self_heals_a_corrupt_database(tmp_path):
    db = tmp_path / "ironjarvis.db"
    open_db(db).dispose()  # first open creates a valid DB

    db.write_bytes(b"this is definitely not a sqlite database " * 20)  # header corruption
    engine = open_db(db)  # must recover (NOT raise) — the daemon boots
    with Session(engine) as s:  # the recovered engine is usable + empty
        assert s.exec(select(EventRecord)).all() == []
    engine.dispose()


def test_open_db_never_touches_a_healthy_database(tmp_path):
    # Regression guard (CHAOS-DB-TRUNC-1): a healthy DB must be left ALONE — no
    # false quarantine, no truncation, data preserved.
    from iron_jarvis.core.db import _db_is_corrupt

    db = tmp_path / "ironjarvis.db"
    engine = open_db(db)
    with Session(engine) as s:
        s.add(EventRecord(id="e1", type="t", session_id=None, payload_json="{}"))
        s.commit()
    engine.dispose()

    assert _db_is_corrupt(db) is False
    engine = open_db(db)  # re-open a HEALTHY DB
    with Session(engine) as s:
        assert s.exec(select(EventRecord)).all(), "data must survive re-open"
    engine.dispose()
    assert not any(p.name.startswith("ironjarvis.db.corrupt-") for p in tmp_path.iterdir())


def test_db_is_corrupt_distinguishes_real_corruption(tmp_path):
    from iron_jarvis.core.db import _db_is_corrupt

    db = tmp_path / "ironjarvis.db"
    open_db(db).dispose()
    assert _db_is_corrupt(db) is False  # healthy
    db.write_bytes(b"definitely not a sqlite database " * 30)
    assert _db_is_corrupt(db) is True  # malformed


def test_home_for_honors_ironjarvis_home(tmp_path, monkeypatch):
    # Regression guard (MPFIT-1): recovery commands must target the SAME home the
    # daemon uses, incl. the shared IRONJARVIS_HOME brain.
    from iron_jarvis.daemon.cli import _home_for

    shared = tmp_path / "shared-brain"
    monkeypatch.setenv("IRONJARVIS_HOME", str(shared))
    assert _home_for(str(tmp_path / "anyproject")) == shared.resolve()
    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    assert _home_for(str(tmp_path / "proj")) == (tmp_path / "proj").resolve() / ".ironjarvis"


def test_quarantine_db_preserves_the_corrupt_file(tmp_path):
    # The offline recovery path (daemon down, no live handle) renames the corrupt
    # DB aside so data can be salvaged/restored, and drops its WAL sidecars.
    db = tmp_path / "ironjarvis.db"
    db.write_bytes(b"corrupt-bytes")
    (tmp_path / "ironjarvis.db-wal").write_bytes(b"stale")
    dead = quarantine_db(db, "test")
    assert dead is not None and dead.exists() and dead.read_bytes() == b"corrupt-bytes"
    assert not db.exists() and not (tmp_path / "ironjarvis.db-wal").exists()


# --- GROW-2: backups exclude the unbounded workspaces scratch -----------------


def test_backup_excludes_disposable_workspaces(platform, tmp_path):
    home = platform.config.home
    (home / "workspaces" / "sess1").mkdir(parents=True, exist_ok=True)
    (home / "workspaces" / "sess1" / "scratch.bin").write_text("x" * 1000)
    (home / "memory").mkdir(parents=True, exist_ok=True)
    (home / "memory" / "note.md").write_text("keep me")

    out = tmp_path / "b.tar.gz"
    create_backup(home, out, engine=platform.engine, include_keys=True)
    names = tarfile.open(out).getnames()
    assert not any("/workspaces/" in n or n.endswith("scratch.bin") for n in names)
    assert any(n.endswith("note.md") for n in names)  # durable state IS kept


# --- RESIL-1: self-update tags the pre-update commit for a reliable rollback ---


def test_apply_update_tags_pre_update_commit(tmp_path):
    calls: list[list[str]] = []

    def runner(cmd, cwd):
        calls.append(list(cmd))
        j = " ".join(cmd)
        if j == "git rev-parse HEAD":
            return RunResult(0, "abc1234\n", "")
        return RunResult(0, "", "")

    apply_update(tmp_path, runner=runner, run_tests=False)
    assert ["git", "tag", "-f", "ironjarvis/pre-update", "abc1234"] in calls


# --- MP-1/MP-2: IRONJARVIS_HOME gives ONE brain across all projects -----------


def test_ironjarvis_home_shares_one_brain_across_projects(tmp_path, monkeypatch):
    from iron_jarvis.core.config import load_config

    shared = tmp_path / "shared-brain"
    monkeypatch.setenv("IRONJARVIS_HOME", str(shared))
    a = load_config(tmp_path / "projectA")
    b = load_config(tmp_path / "projectB")
    # One shared home (DB / secrets / memory / sessions) for every project...
    assert a.home == shared.resolve() == b.home
    assert a.db_path == b.db_path
    # ...while the per-invocation project_root still differs (per-project work).
    assert a.project_root != b.project_root


def test_default_home_is_isolated_per_project(tmp_path, monkeypatch):
    from iron_jarvis.core.config import load_config

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    a = load_config(tmp_path / "projA")
    b = load_config(tmp_path / "projB")
    assert a.home != b.home  # default: each project fully isolated (unchanged)


# --- MED-RESIL2-1: a wrong-typed config value doesn't brick boot --------------


def test_load_config_self_heals_wrong_typed_value(tmp_path, monkeypatch):
    from iron_jarvis.core.config import load_config

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    (home / "config.toml").write_text('max_agent_steps = "lots"\ndefault_model = "claude-x"\n')
    cfg = load_config(tmp_path)  # must NOT raise on the type error
    assert cfg.max_agent_steps == 12  # invalid value dropped → default
    assert cfg.default_model == "claude-x"  # valid value preserved


# --- MED-SYNCMOCK-1: the CLI warns when a run falls back to mock ---------------


def test_cli_run_signals_mock_fallback(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from iron_jarvis.daemon.cli import app

    monkeypatch.delenv("IRONJARVIS_HOME", raising=False)
    result = CliRunner().invoke(app, ["run", "write a short note", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "MOCK" in result.stdout  # headless caller now sees the mock-fallback signal
