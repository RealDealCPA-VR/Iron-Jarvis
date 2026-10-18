"""v1.334.0 — Everything › Status is one picture, read in ONE call.

``GET /ui/system-map`` answers every part of the app as SET UP / USED in the
last 7 days / IN USE now, grouped like the Directory, plus the status line's
counts, a 24-hour heartbeat and the vitals. Driven through the real app
factory; rows are seeded with the real models.
"""

from __future__ import annotations

import subprocess
from datetime import timedelta

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import ChatThreadRecord, EventRecord, Project, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import system_map as sm


def _nodes(body):
    return {n["key"]: n for a in body["areas"] for n in a["nodes"]}


def test_a_fresh_install_draws_every_star_and_none_falls_back(tmp_path):
    app = create_app(str(tmp_path))
    body = TestClient(app).get("/ui/system-map").json()
    assert [a["key"] for a in body["areas"]] == ["work", "automations", "system", "knowledge", "setup"]
    nodes = _nodes(body)
    # A star whose store raised is drawn as a bare fallback with no detail
    # line. Every star must have computed for real on a fresh install.
    fell_back = [k for k, n in nodes.items() if not n["detail"]]
    assert fell_back == []
    assert len(nodes) >= 25
    for n in nodes.values():
        assert n["href"].startswith("/")
        assert set(n) >= {"setup", "used", "live", "attention", "label"}
    assert len(body["heartbeat"]) == 24 and all(isinstance(v, int) for v in body["heartbeat"])
    assert body["vitals"]["disk_free"] > 0
    assert set(body["status"]) == {"running", "failing_loops", "failing_packs", "provider_failures_24h"}
    # Nothing has happened yet: nothing is in use.
    assert all(n["live"] == 0 for n in nodes.values())


def test_seeded_work_lights_the_right_stars(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    now = utcnow()
    with session_scope(app.state.platform.engine) as db:
        p = Project(name="Books")
        db.add(p)
        db.flush()
        db.add(Session(task="running", status=SessionStatus.ACTIVE, project_id=p.id))
        db.add(Session(task="done", status=SessionStatus.COMPLETED))
        db.add(Session(task="old", status=SessionStatus.COMPLETED, created_at=now - timedelta(days=30)))
        db.add(ChatThreadRecord(title="hi"))
        db.add(EventRecord(id=new_id("evt"), type="reflex.fired", payload_json="{}"))
        db.commit()
    body = client.get("/ui/system-map").json()
    nodes = _nodes(body)
    assert nodes["projects"]["setup"] == 1
    assert nodes["projects"]["used"] == 1
    assert nodes["projects"]["live"] == 1
    assert nodes["chats"]["setup"] == 1 and nodes["chats"]["used"] == 1
    # The 30-day-old run is not "this week".
    assert nodes["sessions"]["used"] == 2 and nodes["sessions"]["live"] == 1
    assert body["vitals"]["runs_7d"] == 2 and body["vitals"]["runs_ok_7d"] == 1
    # The running one is not finished: 1 of 1 finished runs went well.
    assert body["vitals"]["runs_done_7d"] == 1
    assert nodes["reflexes"]["used"] == 1
    assert body["status"]["running"] == 1


def test_failing_loops_and_packs_are_named_on_the_map_and_in_the_status(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    d = app.state.d
    d.loop_health["sampler"] = {"ok": False, "last_error": "boom"}
    d.loop_health["backup"] = {"ok": True}
    d.platform.config.mcp_servers = [{"name": "github", "command": "x"}]
    import iron_jarvis.mcp.tools as mcp_tools

    monkeypatch.setattr(mcp_tools, "load_status", lambda name: {"state": "failed", "last_error": "no token"})
    body = TestClient(app).get("/ui/system-map").json()
    assert body["status"]["failing_loops"] == [{"name": "sampler", "error": "boom"}]
    assert body["status"]["failing_packs"] == [{"name": "github", "error": "no token"}]
    tools = _nodes(body)["tools"]
    assert tools["attention"] == "Didn't start: github"
    assert tools["setup"] == 1


def test_heartbeat_buckets_by_hour_oldest_first(tmp_path):
    app = create_app(str(tmp_path))
    now = utcnow().replace(minute=30, second=0, microsecond=0)
    with session_scope(app.state.platform.engine) as db:
        for _ in range(3):
            db.add(EventRecord(id=new_id("evt"), type="x", payload_json="{}", created_at=now))
        db.add(EventRecord(id=new_id("evt"), type="x", payload_json="{}", created_at=now - timedelta(hours=5)))
        db.add(EventRecord(id=new_id("evt"), type="x", payload_json="{}", created_at=now - timedelta(hours=30)))
        db.commit()
        hb = sm.heartbeat(db, now)
    assert len(hb) == 24
    assert hb[-1] == 3
    assert hb[-6] == 1
    # Older than the window: not drawn.
    assert sum(hb) == 4


def test_the_map_never_raises(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))

    def boom(*_a, **_k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(sm, "session_scope", boom)
    r = TestClient(app).get("/ui/system-map")
    assert r.status_code == 200
    assert r.json()["areas"] == []


def test_reading_the_map_starts_no_process(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    started: list[object] = []
    real_init = subprocess.Popen.__init__

    def spy(self, *a, **k):
        started.append(a[0] if a else k.get("args"))
        return real_init(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    body = sm.system_map(app.state.d)
    assert body["areas"]
    assert started == []
    # CONTROL: the spy does see a process start.
    subprocess.run(["cmd", "/c", "exit", "0"] if __import__("os").name == "nt" else ["true"], check=False)
    assert len(started) == 1
    del client


def test_a_saved_profile_lights_you_after_a_real_db_round_trip(tmp_path):
    """SQLite returns updated_at NAIVE; comparing it to an aware cutoff raised
    and the star silently fell back to "not set up" (caught on the scratch
    stack, where the profile had been saved days earlier)."""
    from iron_jarvis.profile.store import ProfileStore

    app = create_app(str(tmp_path))
    ProfileStore(app.state.platform.engine).save({"about": "A bookkeeper in Seattle."})
    you = _nodes(TestClient(app).get("/ui/system-map").json())["you"]
    assert you["detail"]
    assert you["setup"] == 1 and you["used"] == 1


def test_a_failed_schedule_and_a_tripped_goal_turn_their_stars_amber(tmp_path):
    from iron_jarvis.goals.models import GoalContractRecord
    from iron_jarvis.scheduling.models import ScheduledTaskRecord

    app = create_app(str(tmp_path))
    client = TestClient(app)
    nodes = _nodes(client.get("/ui/system-map").json())
    assert nodes["schedules"]["attention"] is None and nodes["goals"]["attention"] is None
    with session_scope(app.state.platform.engine) as db:
        # v1.351.0 (G02): the value the scheduler REALLY writes for a failed
        # run is "error" — this pin used to plant "failed", which nothing
        # writes, so the flag it proved could never fire on a real install.
        db.add(ScheduledTaskRecord(name="nightly", cron="0 2 * * *", last_status="error", last_run=utcnow()))
        db.add(GoalContractRecord(name="inbox zero", state="tripped"))
        db.commit()
    nodes = _nodes(client.get("/ui/system-map").json())
    assert nodes["schedules"]["attention"] == "1 schedule failed on its last run"
    assert nodes["schedules"]["setup"] == 1 and nodes["schedules"]["used"] == 1
    assert nodes["goals"]["attention"] == "1 goal stopped by its safety breaker"


def test_a_loop_that_reports_the_old_error_key_is_still_named_with_its_reason(tmp_path):
    """v1.229.0 kept ``error`` as an alias of ``last_error``; the map honours
    it (the dashboard pin for this moved here with the read)."""
    app = create_app(str(tmp_path))
    app.state.d.loop_health["rehydrate_goals"] = {"ok": False, "error": "RuntimeError: goals exploded"}
    body = TestClient(app).get("/ui/system-map").json()
    assert {"name": "rehydrate_goals", "error": "RuntimeError: goals exploded"} in body["status"]["failing_loops"]
