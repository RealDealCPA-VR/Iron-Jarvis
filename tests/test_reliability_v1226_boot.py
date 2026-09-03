"""v1.226.0 reliability wave — daemon boot, background-loop signal, shutdown,
instance identity (audit items F-B-1..F-B-5, contracts C2/C5/C6).

Each test goes RED when its fix is reverted (mutation-checked, see the
report): a mismatched secrets key used to abort the lifespan at the first
boot-time decrypt; eight of ten background loops reported nothing into
loop_health; a hand-edited ``[comm]`` of the wrong shape raised out of
build_platform; ``serve`` exited 0 when it merely ATTACHED to a daemon it did
not start; nothing checkpointed the WAL at shutdown.
"""

from __future__ import annotations

import asyncio
import inspect
import time

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from iron_jarvis.daemon import app as app_mod
from iron_jarvis.daemon import cli as cli_mod
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform


# --- F-B-1: a mismatched / regenerated secrets key never aborts boot ----------


def _boot_with_wrong_key(tmp_path, monkeypatch) -> tuple[str, object]:
    """Seed every boot-time secret consumer (Notion LTM in build_platform, the
    calendar poller + the Slack socket in lifespan), then swap the key."""
    monkeypatch.setenv("IRONJARVIS_INBOUND", "off")
    p = build_platform(str(tmp_path))
    home = p.config.home
    (home / "config.toml").write_text(
        'default_provider = "mock"\n'
        'notion_database_id = "abc"\n'
        "calendar_trigger_enabled = true\n"
        "[comm.channels.slack]\n"
        'type = "slack"\n'
        "inbound_enabled = true\n"
        'allowed_senders = ["U123"]\n'
        'app_token_secret = "slack_app_token"\n'
        'webhook_secret = "slack_hook"\n',
        encoding="utf-8",
    )
    from iron_jarvis.triggers.calendar import ICS_SECRET_KEY

    p.secrets.set("notion_token", "secret_x")
    p.secrets.set(ICS_SECRET_KEY, "https://x/cal.ics")
    p.secrets.set("slack_app_token", "xapp-1-abc")
    p.secrets.set("slack_hook", "https://hooks.slack.com/x")
    p.engine.dispose()
    (home / "secrets" / ".secrets.key").write_bytes(Fernet.generate_key())  # the wrong key
    return str(tmp_path), p


def test_mismatched_secrets_key_boots_and_diagnostics_says_so(tmp_path, monkeypatch):
    root, _ = _boot_with_wrong_key(tmp_path, monkeypatch)
    app = create_app(root)  # used to raise InvalidToken from platform.py (Notion)
    with TestClient(app) as c:  # used to raise from lifespan (calendar / slack)
        assert c.get("/health").status_code == 200
        assert c.get("/diagnostics").json()["secrets_key_valid"] is False


def test_secrets_get_reads_as_absent_and_warns_once(tmp_path, monkeypatch, caplog):
    root, _ = _boot_with_wrong_key(tmp_path, monkeypatch)
    p = build_platform(root)
    with caplog.at_level("ERROR"):
        assert p.secrets.get("notion_token") is None
        assert p.secrets.get("slack_hook") is None
        assert p.secrets.get_oauth("notion_token") is None
    hits = [r for r in caplog.records if "cannot be decrypted" in r.getMessage()]
    assert len(hits) == 1, "one loud line per process, not one per read"
    assert "secrets_key_valid" in hits[0].getMessage()
    assert p.secrets.key_valid() is False


def test_enabled_probe_that_raises_does_not_abort_lifespan(tmp_path, monkeypatch):
    """The lifespan probes are guarded independently of the secrets fix."""
    from iron_jarvis.comm.slack_socket import SlackSocketMode
    from iron_jarvis.triggers.calendar import CalendarPoller

    def _boom(self, *a, **kw):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(CalendarPoller, "enabled", _boom)
    monkeypatch.setattr(SlackSocketMode, "enabled", _boom)
    with TestClient(create_app(str(tmp_path))) as c:
        assert c.get("/health").status_code == 200


# --- F-B-4: every background loop reports into loop_health --------------------


def test_inbound_loop_failure_is_visible_on_diagnostics(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    poller = app.state.inbound_poller
    calls = {"n": 0}

    def _boom(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("poll exploded")

    monkeypatch.setattr(poller, "enabled", _boom)
    real_sleep = asyncio.sleep

    async def _fast_sleep(delay, *a, **kw):
        # Only the inbound loop's first few sleeps collapse (its 20s boot
        # settle + interval); every other loop keeps its real cadence.
        task = asyncio.current_task()
        coro = task.get_coro() if task is not None else None
        name = getattr(coro, "__qualname__", "") or ""
        if name.endswith("_inbound_loop") and calls["n"] < 3:
            return await real_sleep(0)
        return await real_sleep(delay, *a, **kw)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    with TestClient(app) as c:
        loops = {}
        for _ in range(200):
            loops = c.get("/diagnostics").json()["background_loops"]
            if "inbound" in loops:
                break
            time.sleep(0.02)
        assert "inbound" in loops, loops
        assert loops["inbound"]["ok"] is False
        assert "poll exploded" in loops["inbound"]["last_error"]
        assert loops["inbound"]["at"]
        # the scheduler start is recorded too (it used to `except: pass`)
        assert loops["scheduler"]["ok"] is True
        assert loops["scheduler"]["last_success_at"]


def test_scheduler_start_failure_is_logged_and_recorded(tmp_path, monkeypatch, caplog):
    app = create_app(str(tmp_path))

    def _boom(*a, **kw):
        raise RuntimeError("apscheduler refused")

    monkeypatch.setattr(app.state.platform.scheduler, "start", _boom)
    with caplog.at_level("ERROR"):
        with TestClient(app) as c:
            loops = c.get("/diagnostics").json()["background_loops"]
    assert loops["scheduler"]["ok"] is False
    assert "apscheduler refused" in loops["scheduler"]["last_error"]
    assert any("scheduler failed to start" in r.getMessage() for r in caplog.records)


# --- F-B-5: a hand-edited [comm] of the wrong shape --------------------------


def test_wrong_shaped_comm_channels_are_skipped_not_fatal(caplog):
    from iron_jarvis.comm.integrations import build_notifier

    with caplog.at_level("ERROR"):
        n1 = build_notifier(comm_config={"channels": "x"})  # B.md shape B
        n2 = build_notifier(comm_config={"channels": {"tg": "hook"}})  # shape C
    assert "mock" in n1.channels()  # the offline default still lands
    assert "mock" in n2.channels()
    assert "tg" not in n2.channels()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("channels must be a table" in m for m in msgs)
    assert any("[comm.channels.tg]" in m for m in msgs)


def test_wrong_shaped_comm_section_boots(tmp_path):
    p = build_platform(str(tmp_path))
    home = p.config.home
    p.engine.dispose()
    (home / "config.toml").write_text(
        'default_provider = "mock"\n[comm]\nchannels = "x"\n', encoding="utf-8"
    )
    with TestClient(create_app(str(tmp_path))) as c:
        assert c.get("/health").status_code == 200


# --- F-B-3: bounded drain + WAL checkpoint / dispose at lifespan exit ---------


def test_serve_bounds_the_graceful_drain():
    src = inspect.getsource(cli_mod.serve)
    assert "timeout_graceful_shutdown=1.0" in src


def test_lifespan_exit_checkpoints_the_wal_then_disposes(tmp_path, monkeypatch):
    seen: list[tuple] = []

    def _spy(*args, **kw):
        seen.append(("checkpoint", args, kw))

    monkeypatch.setattr(app_mod, "_checkpoint_wal", _spy)
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    disposed = []
    real_dispose = engine.dispose

    def _dispose(*a, **kw):
        disposed.append(True)
        return real_dispose(*a, **kw)

    monkeypatch.setattr(engine, "dispose", _dispose)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert seen == []  # only at exit
    assert [s[0] for s in seen] == ["checkpoint"]
    assert seen[0][1][0] is engine
    assert disposed == [True]


def test_checkpoint_wal_runs_the_pragma_and_skips_memory(tmp_path):
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("CREATE TABLE t (x)")
        conn.exec_driver_sql("INSERT INTO t VALUES (1)")
        conn.commit()
    app_mod._checkpoint_wal(engine)
    with engine.connect() as conn:
        # TRUNCATE leaves an empty WAL: (busy, log_pages, checkpointed)
        row = conn.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    assert row[1] == 0, row
    app_mod._checkpoint_wal(create_engine("sqlite://"))  # in-memory: a no-op


# --- F-B-2 / contract C2: attached-to-existing-daemon exits 75 ---------------


def test_serve_exits_75_when_an_iron_jarvis_daemon_already_listens(monkeypatch):
    monkeypatch.setattr(cli_mod, "_port_in_use", lambda *a, **kw: True)
    monkeypatch.setattr(cli_mod, "_is_ironjarvis_daemon", lambda *a, **kw: True)
    res = CliRunner().invoke(cli_mod.app, ["serve", "--port", "18799"])
    assert res.exit_code == 75, res.output
    assert "already running" in res.output


def test_serve_exits_1_when_a_foreign_program_holds_the_port(monkeypatch):
    monkeypatch.setattr(cli_mod, "_port_in_use", lambda *a, **kw: True)
    monkeypatch.setattr(cli_mod, "_is_ironjarvis_daemon", lambda *a, **kw: False)
    res = CliRunner().invoke(cli_mod.app, ["serve", "--port", "18799"])
    assert res.exit_code == 1, res.output


# --- contract C5: /health carries a per-process instance id -------------------


def test_health_instance_is_stable_per_process_and_differs_per_app(tmp_path):
    c1 = TestClient(create_app(str(tmp_path / "a")))
    h1 = c1.get("/health").json()
    h2 = c1.get("/health").json()
    assert isinstance(h1["instance"], str) and len(h1["instance"]) >= 16
    assert h1["instance"] == h2["instance"]
    assert h1["status"] == "ok" and h1["version"]  # nothing else in the gate changed
    c2 = TestClient(create_app(str(tmp_path / "b")))
    assert c2.get("/health").json()["instance"] != h1["instance"]


# --- contract C6: GET /system/activity ----------------------------------------


def test_system_activity_counts_live_sessions_and_runs(tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session, SessionStatus
    from iron_jarvis.workflows.models import WorkflowRunRecord

    app = create_app(str(tmp_path))
    c = TestClient(app)
    idle = c.get("/system/activity").json()
    assert idle == {
        "active_sessions": 0,
        "running_workflow_runs": 0,
        "writing_workflow_runs": 0,
        "busy": False,
    }
    with session_scope(app.state.platform.engine) as db:
        s = Session(task="t", status=SessionStatus.ACTIVE)
        db.add(s)
        db.add(
            WorkflowRunRecord(
                workflow_name="w", status="waiting", steps_json="[]",
                session_ids_json="[]", outputs_json="{}",
            )
        )
        db.commit()
        sid = s.id
    busy = c.get("/system/activity").json()
    assert busy["active_sessions"] == 1
    assert busy["running_workflow_runs"] == 1
    assert busy["writing_workflow_runs"] == 0  # parked on a human, not writing
    assert busy["busy"] is True
    with session_scope(app.state.platform.engine) as db:
        row = db.get(Session, sid)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()
    assert c.get("/system/activity").json()["active_sessions"] == 0


# --- D3 / D4 (review) ----------------------------------------------------------


def test_tick_survives_an_exception_whose_str_raises(tmp_path, monkeypatch):
    """_tick runs inside every loop's except branch — it must never raise."""
    app = create_app(str(tmp_path))

    class _Unprintable(Exception):
        def __str__(self):
            raise ValueError("no str for you")

    calls = {"n": 0}

    def _boom(*a, **kw):
        calls["n"] += 1
        raise _Unprintable()

    monkeypatch.setattr(app.state.platform.scheduler, "start", _boom)
    with TestClient(app) as c:  # the scheduler start try is the cheapest _tick site
        loops = c.get("/diagnostics").json()["background_loops"]
    assert loops["scheduler"]["ok"] is False
    assert loops["scheduler"]["last_error"].startswith("_Unprintable")


def test_checkpoint_wal_sets_a_short_busy_timeout_and_never_raises():
    seen: list[str] = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def exec_driver_sql(self, sql, *args, **kw):
            seen.append(sql)

    class _Engine:
        url = type("U", (), {"database": "x.db"})()

        def connect(self, *a, **kw):
            return _Conn()

    app_mod._checkpoint_wal(_Engine())
    assert seen == ["PRAGMA busy_timeout=1000", "PRAGMA wal_checkpoint(TRUNCATE)"]

    class _Raising(_Engine):
        def connect(self, *a, **kw):
            raise RuntimeError("database is locked")

    app_mod._checkpoint_wal(_Raising())  # best-effort: swallowed
