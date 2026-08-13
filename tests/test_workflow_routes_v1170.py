"""Workflow routes + store, v1.170.0 (P2 — routes-and-store).

Frozen contracts under test:
  1. POST /workflows/run accepts {name} ALONE — stored steps + project pin
     resolve server-side via WorkflowStore.load_def; 404 unknown; optional
     ``inputs`` forwarded to the engine ONLY when the caller sent them.
  3. PATCH /workflows/{name} renames (steps untouched, pin row MOVED),
     404 unknown, 409 on a taken new_name; response = GET's shape.
  4. POST /workflows/runs/{id}/resume — atomic interrupted->resuming claim,
     409 for any other status, full flat run record back, engine resume
     helper spawned in the background.
Plus: save-time step-shape validation (422 naming the field; loading stays
lenient), prune_runs / POST /workflows/runs/prune (keep-newest over the
PRUNABLE finished statuses; ``interrupted`` is resumable so it survives the
keep window and falls only to an age threshold; ids-only bulk delete), the
additive ``offset`` on GET /workflows/runs, reflex rules following a rename
(the name-binding orphan class), and the rename race mapping to the 409.

The engine seams P1 owns (``inputs=`` on create_record/run_record, the
``resume_interrupted`` helper) are exercised through monkeypatched engine
methods here: these tests pin what the ROUTE sends across the seam, which is
exactly P2's contract, independent of when P1's implementation lands.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta

# Register the workflow tables on SQLModel.metadata before create_app's
# init_db runs (the repo-wide test pattern). Must stay at the top.
import iron_jarvis.workflows.models  # noqa: F401

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import dumps, session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.engine import WorkflowEngine
from iron_jarvis.workflows.models import WorkflowRunRecord
from iron_jarvis.workflows.store import WorkflowStore, prune_runs

STEPS = [{"name": "s1", "agent": "builder", "task": "first thing"}]


def _save(client, name, steps=None, project_id=None, description=""):
    body = {"name": name, "steps": steps if steps is not None else STEPS,
            "description": description}
    if project_id is not None:
        body["project_id"] = project_id
    r = client.post("/workflows", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _engine_of(client):
    return client.app.state.platform.engine


def _seed_run(client, status, name="wf", started_at=None, finished=None):
    rec = WorkflowRunRecord(
        workflow_name=name,
        status=status,
        steps_json=dumps(
            [
                {"name": "s1", "agent": "builder", "task": "a", "kind": "agent",
                 "on_failure": "halt", "group": None, "args": {}, "message": ""},
                {"name": "s2", "agent": "builder", "task": "b", "kind": "agent",
                 "on_failure": "halt", "group": None, "args": {}, "message": ""},
            ]
        ),
        outputs_json=dumps({"s1": {"status": "completed", "summary": "done"}}),
        session_ids_json="[]",
        started_at=started_at or utcnow(),
        finished_at=finished,
    )
    with session_scope(_engine_of(client)) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id


def _wait_for(cond, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# --- Contract 1: name-only run -------------------------------------------


def test_name_only_run_resolves_stored_steps_and_pin(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        _save(client, "saved-wf", project_id="proj-x")
        r = client.post("/workflows/run", json={"name": "saved-wf"})
        assert r.status_code == 200, r.text
        rec = r.json()
        assert rec["status"] == "running"
        assert rec["workflow_name"] == "saved-wf"
        # The STORED steps ran — the server resolved them, nobody re-posted them.
        assert [s["name"] for s in json.loads(rec["steps_json"])] == ["s1"]
        # The def's project pin reached the run record (load_def composition).
        assert rec["project_id"] == "proj-x"
        # Let the background run settle so the test app shuts down clean.
        _wait_for(
            lambda: client.get(f"/workflows/runs/{rec['id']}").json()["status"]
            in ("completed", "failed", "cancelled", "interrupted"),
            seconds=20,
        )


def test_name_only_unknown_workflow_404s(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/workflows/run", json={"name": "nope"})
        assert r.status_code == 404
        assert "nope" in r.json()["detail"]


def test_name_only_blank_project_id_forces_unpinned(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        _save(client, "pinned-wf", project_id="proj-y")
        rec = client.post(
            "/workflows/run", json={"name": "pinned-wf", "project_id": ""}
        ).json()
        assert rec["project_id"] is None
        _wait_for(
            lambda: client.get(f"/workflows/runs/{rec['id']}").json()["status"]
            in ("completed", "failed", "cancelled", "interrupted"),
            seconds=20,
        )


def test_legacy_name_plus_steps_still_runs_adhoc(tmp_path):
    """{name, steps} must NOT consult the store — an ad-hoc run of an unsaved
    name worked before v1.170.0 and must keep working byte-identically."""
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post(
            "/workflows/run", json={"name": "never-saved", "steps": STEPS}
        )
        assert r.status_code == 200
        rec = r.json()
        assert rec["status"] == "running"
        _wait_for(
            lambda: client.get(f"/workflows/runs/{rec['id']}").json()["status"]
            in ("completed", "failed", "cancelled", "interrupted"),
            seconds=20,
        )


def test_empty_body_still_400s(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/workflows/run", json={}).status_code == 400


# --- Contract 1/5 seam: inputs forwarded only when present ----------------


def _stub_engine(monkeypatch, captured):
    def fake_create(self, wf, **kwargs):
        captured["create"] = kwargs
        rec = WorkflowRunRecord(
            workflow_name=wf.name, status="running",
            steps_json=dumps([{"name": "s1"}]),
        )
        with session_scope(self.platform.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    async def fake_run(self, rec, wf, **kwargs):
        captured["run"] = kwargs
        return rec

    monkeypatch.setattr(WorkflowEngine, "create_record", fake_create)
    monkeypatch.setattr(WorkflowEngine, "run_record", fake_run)


def test_inputs_forwarded_to_create_and_run(tmp_path, monkeypatch):
    captured: dict = {}
    _stub_engine(monkeypatch, captured)
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post(
            "/workflows/run",
            json={"name": "adhoc", "steps": STEPS,
                  "inputs": {"Client": "Acme", "Year": "2025"}},
        )
        assert r.status_code == 200
        assert captured["create"] == {"inputs": {"Client": "Acme", "Year": "2025"}}
        assert _wait_for(lambda: "run" in captured)
        assert captured["run"] == {"inputs": {"Client": "Acme", "Year": "2025"}}


def test_no_inputs_means_no_inputs_kwarg_at_all(tmp_path, monkeypatch):
    """Absent inputs must cross the seam as NO kwarg — not ``inputs=None`` —
    so the legacy engine call stays byte-identical (kills the mutant that
    always forwards the field)."""
    captured: dict = {}
    _stub_engine(monkeypatch, captured)
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/workflows/run", json={"name": "adhoc", "steps": STEPS})
        assert r.status_code == 200
        assert captured["create"] == {}
        assert _wait_for(lambda: "run" in captured)
        assert captured["run"] == {}


# --- Contract 3: PATCH /workflows/{name} ----------------------------------


def test_patch_rename_moves_def_and_pin(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _save(client, "old-name", project_id="proj-1", description="the desc")
    r = client.patch("/workflows/old-name", json={"new_name": "new-name"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "new-name"
    assert body["description"] == "the desc"  # untouched
    assert json.loads(body["steps_json"]) == STEPS  # steps untouched
    assert body["project_id"] == "proj-1"  # the pin MOVED with the name
    # Response shape == GET's shape (contract 3), and the rename is durable.
    got = client.get("/workflows/new-name")
    assert got.status_code == 200
    assert set(body) == set(got.json())
    assert client.get("/workflows/old-name").status_code == 404
    store = WorkflowStore(_engine_of(client))
    assert store.get_project_id("old-name") is None
    assert store.get_project_id("new-name") == "proj-1"


def test_patch_rename_conflict_409_and_untouched(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _save(client, "alpha")
    _save(client, "beta")
    r = client.patch("/workflows/alpha", json={"new_name": "beta"})
    assert r.status_code == 409
    assert "beta" in r.json()["detail"]
    # Nothing moved: both defs still resolve under their own names.
    assert client.get("/workflows/alpha").status_code == 200
    assert client.get("/workflows/beta").status_code == 200


def test_patch_description_only_keeps_name_and_steps(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _save(client, "wf-d", description="old words")
    body = client.patch(
        "/workflows/wf-d", json={"description": "new words"}
    ).json()
    assert body["name"] == "wf-d"
    assert body["description"] == "new words"
    assert json.loads(body["steps_json"]) == STEPS
    # And a later rename with description ABSENT leaves the new description.
    body2 = client.patch("/workflows/wf-d", json={"new_name": "wf-d2"}).json()
    assert body2["description"] == "new words"


def test_patch_unknown_404_blank_422_self_rename_ok(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert (
        client.patch("/workflows/ghost", json={"description": "x"}).status_code
        == 404
    )
    _save(client, "same")
    assert (
        client.patch("/workflows/same", json={"new_name": "   "}).status_code
        == 422
    )
    # Renaming onto yourself is a no-op, never a conflict.
    r = client.patch("/workflows/same", json={"new_name": "same"})
    assert r.status_code == 200
    assert r.json()["name"] == "same"


def test_patch_rename_retargets_reflex_rules(tmp_path):
    """RUNTIME-CONFIRMED orphan class: ReflexRule.target binds the workflow BY
    NAME and the router resolves load_def(rule.target) at fire time — a rename
    that left the rule behind failed every later webhook/comm trigger SILENTLY
    ({ok:false, "no saved workflow 'intake'"}). The binding must move in the
    same transaction as the def."""
    from iron_jarvis.reflex.store import ReflexStore

    client = TestClient(create_app(str(tmp_path)))
    _save(client, "intake", project_id="proj-1")
    _save(client, "other-wf")
    eng = _engine_of(client)
    rstore = ReflexStore(eng)
    hook = rstore.add(name="hook", source="webhook", match="intake-hook",
                      action="workflow", target="intake")
    sess = rstore.add(name="sess", source="comm", match="intake",
                      action="session", target="intake")
    other = rstore.add(name="other", source="webhook", match="o",
                       action="workflow", target="other-wf")
    r = client.patch("/workflows/intake", json={"new_name": "client-intake"})
    assert r.status_code == 200, r.text
    # The workflow binding followed the rename; a session rule sharing the
    # string and a rule bound to ANOTHER def are both untouched (mutation
    # targets: drop the action filter or the target filter and these fail).
    assert rstore.get(hook.id).target == "client-intake"
    assert rstore.get(sess.id).target == "intake"
    assert rstore.get(other.id).target == "other-wf"
    # The binding still RESOLVES — the exact silent orphan the fix kills.
    assert WorkflowStore(eng).load_def(rstore.get(hook.id).target) is not None


def test_patch_rename_race_integrityerror_maps_to_valueerror(tmp_path, monkeypatch):
    """Clash detection is check-then-write: a concurrent rename/save can claim
    the target name between the clash SELECT and the commit, and the UNIQUE
    constraint then fires. The store must surface the SAME ValueError the
    pre-check raises (the route's existing 409 mapping covers it), never leak
    a raw IntegrityError -> 500."""
    import sqlmodel
    from sqlalchemy.exc import IntegrityError

    client = TestClient(create_app(str(tmp_path)))
    _save(client, "race-src")
    store = WorkflowStore(_engine_of(client))
    real_commit = sqlmodel.Session.commit
    armed = {"on": True}

    def raced_commit(session):
        if armed["on"]:
            armed["on"] = False  # fires exactly once: on patch()'s own commit
            raise IntegrityError(
                "UNIQUE constraint failed: workflowrecord.name", None,
                Exception("unique"),
            )
        return real_commit(session)

    monkeypatch.setattr(sqlmodel.Session, "commit", raced_commit)
    with pytest.raises(ValueError, match="race-dst"):
        store.patch("race-src", new_name="race-dst")
    monkeypatch.setattr(sqlmodel.Session, "commit", real_commit)
    # The loser's row is untouched: still fetchable under its old name only.
    assert store.get("race-src") is not None
    assert store.get("race-dst") is None


# --- Contract 4: POST /workflows/runs/{id}/resume -------------------------


def test_resume_claims_interrupted_and_spawns_helper(tmp_path, monkeypatch):
    seen: list[str] = []

    async def fake_resume(self, rec):
        seen.append(rec.id)
        return rec

    # raising=False: the helper is P1's engine seam and may land after us.
    monkeypatch.setattr(
        WorkflowEngine, "resume_interrupted", fake_resume, raising=False
    )
    with TestClient(create_app(str(tmp_path))) as client:
        run_id = _seed_run(client, "interrupted", finished=utcnow())
        r = client.post(f"/workflows/runs/{run_id}/resume")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == run_id
        assert body["status"] == "resuming"
        # The reconciler stamped finished_at; a resuming run is not finished.
        assert body["finished_at"] is None
        # Full FLAT run record (contract 4): same keys as the detail route.
        assert set(body) == set(client.get(f"/workflows/runs/{run_id}").json())
        assert _wait_for(lambda: seen == [run_id])


def test_resume_409_for_every_non_interrupted_status(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    for status in ("running", "waiting", "completed", "failed", "cancelled"):
        run_id = _seed_run(client, status)
        r = client.post(f"/workflows/runs/{run_id}/resume")
        assert r.status_code == 409, status
        assert status in r.json()["detail"]
        # The compare-and-set must not have touched the row (a resumed
        # WAITING run would double-drive the tail).
        with session_scope(_engine_of(client)) as db:
            assert db.get(WorkflowRunRecord, run_id).status == status


def test_resume_unknown_404_and_double_resume_409(tmp_path, monkeypatch):
    async def fake_resume(self, rec):
        return rec

    monkeypatch.setattr(
        WorkflowEngine, "resume_interrupted", fake_resume, raising=False
    )
    with TestClient(create_app(str(tmp_path))) as client:
        assert client.post("/workflows/runs/wfrun_none/resume").status_code == 404
        run_id = _seed_run(client, "interrupted", finished=utcnow())
        assert client.post(f"/workflows/runs/{run_id}/resume").status_code == 200
        # The atomic claim made the first caller the only caller.
        assert client.post(f"/workflows/runs/{run_id}/resume").status_code == 409


# --- Save-time step-shape validation --------------------------------------


def test_save_rejects_unknown_kind_naming_the_field(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/workflows",
        json={
            "name": "bad-kind",
            "steps": [
                {"name": "ok", "agent": "builder", "task": "t"},
                {"name": "typo", "kind": "toool", "tool": "read_file"},
            ],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "steps[1].kind" in detail and "toool" in detail
    # NOT saved — a 422 must not half-persist the def.
    assert client.get("/workflows/bad-kind").status_code == 404


def test_save_rejects_unknown_on_failure_naming_the_field(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/workflows",
        json={
            "name": "bad-of",
            "steps": [{"name": "s", "task": "t", "on_failure": "retyr"}],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "steps[0].on_failure" in detail and "retyr" in detail
    assert client.get("/workflows/bad-of").status_code == 404


def test_save_accepts_normalizable_and_absent_shape_fields(tmp_path):
    """' Tool '/'ASK' normalize to real kinds (the loader lowercases — not a
    rewrite); absent/empty means the default, exactly as before v1.170.0."""
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/workflows",
        json={
            "name": "fine",
            "steps": [
                {"name": "a", "kind": " Tool ", "tool": "read_file",
                 "on_failure": "SKIP"},
                {"name": "b", "kind": "ASK", "message": "go?"},
                {"name": "c", "agent": "builder", "task": "legacy shape"},
                {"name": "d", "kind": "", "task": "empty means default"},
            ],
        },
    )
    assert r.status_code == 200, r.text


def test_store_save_and_load_stay_lenient_for_old_rows(tmp_path):
    """The strictness is the SAVE ROUTE's, not the store's: rows written
    before v1.170.0 (or by agents through the store) keep loading, with the
    loader's old coercion to the default kind."""
    client = TestClient(create_app(str(tmp_path)))
    store = WorkflowStore(_engine_of(client))
    store.save("old-row", [{"name": "s", "task": "t", "kind": "toool"}])
    wf = store.load_def("old-row")
    assert wf is not None
    assert wf.steps[0].kind == "agent"  # coerced, never an error


# --- prune_runs + POST /workflows/runs/prune ------------------------------


def _seed_history(client):
    """3 prunable finished runs (newest last) + one RECENT interrupted run +
    one of each live status, live/interrupted all OLDER than every finished
    run — the strongest mutation target: any prune that orders or filters
    wrongly eats a live row, the resumable row, or the wrong finished ones."""
    base = utcnow() - timedelta(days=2)
    live_ids = {
        status: _seed_run(
            client, status, name=f"live-{status}",
            started_at=base - timedelta(hours=9),
        )
        for status in ("running", "waiting", "cancelling", "resuming")
    }
    interrupted_id = _seed_run(
        client, "interrupted", name="int-recent",
        started_at=base - timedelta(hours=10), finished=utcnow(),
    )
    finished_ids = [
        _seed_run(
            client, status, name=f"fin-{i}",
            started_at=base + timedelta(hours=i), finished=utcnow(),
        )
        for i, status in enumerate(("completed", "failed", "cancelled"))
    ]
    return live_ids, finished_ids, interrupted_id


def test_prune_runs_deletes_oldest_finished_only(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    live_ids, finished_ids, interrupted_id = _seed_history(client)
    deleted = prune_runs(_engine_of(client), keep=1)
    assert deleted == 2
    with session_scope(_engine_of(client)) as db:
        remaining = {r.id for r in db.exec(select(WorkflowRunRecord))}
    # The 2 OLDEST finished are gone; the newest finished survives.
    assert finished_ids[0] not in remaining and finished_ids[1] not in remaining
    assert finished_ids[2] in remaining
    # Every live row survives even though all are the OLDEST rows in the DB,
    # and the RESUMABLE interrupted row survives the keep window too.
    assert set(live_ids.values()) <= remaining
    assert interrupted_id in remaining


def test_prune_runs_noop_when_under_keep(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _seed_history(client)
    assert prune_runs(_engine_of(client), keep=500) == 0
    assert prune_runs(_engine_of(client), keep=3) == 0  # exactly at the cap


def test_prune_interrupted_survives_window_but_ages_out(tmp_path):
    """'interrupted' is RESUMABLE (contract 4 renders a Resume button): the
    keep window may NEVER take it — even keep=0 — or Resume 404s on a run the
    user can still see and the partial progress is unrecoverable. Only genuine
    abandonment (the age threshold) prunes it."""
    client = TestClient(create_app(str(tmp_path)))
    recent = _seed_run(
        client, "interrupted", name="int-recent",
        started_at=utcnow() - timedelta(days=2), finished=utcnow(),
    )
    ancient = _seed_run(
        client, "interrupted", name="int-ancient",
        started_at=utcnow() - timedelta(days=30), finished=utcnow(),
    )
    assert prune_runs(_engine_of(client), keep=0) == 1
    assert client.get(f"/workflows/runs/{ancient}").status_code == 404
    assert client.get(f"/workflows/runs/{recent}").status_code == 200


def test_prune_runs_bulk_deletes_without_loading_blobs(tmp_path):
    """The stale query selects IDS ONLY and ONE bulk DELETE takes them: prune
    runs against the biggest backlogs by definition, and loading every stale
    row's steps_json/outputs_json blobs (or deleting row-by-row) is exactly
    the payload the boot path must never carry (the v1.153.1 lesson)."""
    from sqlalchemy import event

    client = TestClient(create_app(str(tmp_path)))
    _seed_history(client)
    eng = _engine_of(client)
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(eng, "before_cursor_execute", _capture)
    try:
        assert prune_runs(eng, keep=1) == 2
    finally:
        event.remove(eng, "before_cursor_execute", _capture)
    deletes = [s for s in statements if s.lstrip().upper().startswith("DELETE")]
    assert len(deletes) == 1  # one bulk DELETE, not one per stale row
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert selects and all("steps_json" not in s for s in selects)


def test_prune_route_reports_and_clamps(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _, finished_ids, interrupted_id = _seed_history(client)
    body = client.post("/workflows/runs/prune", params={"keep": 2}).json()
    assert body == {"deleted": 1, "keep": 2}
    # Deleted the oldest finished one; detail 404s now.
    assert client.get(f"/workflows/runs/{finished_ids[0]}").status_code == 404
    assert client.get(f"/workflows/runs/{finished_ids[1]}").status_code == 200
    # Negative keep clamps to 0 (delete ALL prunable finished; live rows and
    # the resumable interrupted row still safe).
    body = client.post("/workflows/runs/prune", params={"keep": -5}).json()
    assert body["keep"] == 0 and body["deleted"] == 2
    live = client.get("/workflows/runs").json()["runs"]
    assert {r["status"] for r in live} == {
        "running", "waiting", "cancelling", "resuming", "interrupted"
    }
    assert interrupted_id in {r["id"] for r in live}


def test_prune_route_default_is_500(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    _seed_history(client)
    assert client.post("/workflows/runs/prune").json() == {
        "deleted": 0, "keep": 500,
    }


# --- GET /workflows/runs offset -------------------------------------------


def test_runs_offset_pages_past_the_newest(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    base = utcnow()
    ids = [
        _seed_run(client, "completed", name=f"r{i}",
                  started_at=base - timedelta(minutes=i), finished=utcnow())
        for i in range(5)
    ]  # ids[0] is the NEWEST
    page = client.get(
        "/workflows/runs", params={"limit": 2, "offset": 2}
    ).json()["runs"]
    assert [r["id"] for r in page] == [ids[2], ids[3]]
    # offset=0 (the default) is byte-identical to the old first page.
    first = client.get("/workflows/runs", params={"limit": 2}).json()["runs"]
    assert [r["id"] for r in first] == [ids[0], ids[1]]
    # Past the end: empty, not an error.
    assert client.get(
        "/workflows/runs", params={"offset": 99}
    ).json()["runs"] == []


def test_runs_offset_composes_with_status_filter(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    base = utcnow()
    done = [
        _seed_run(client, "completed", name=f"c{i}",
                  started_at=base - timedelta(minutes=2 * i), finished=utcnow())
        for i in range(3)
    ]
    for i in range(3):  # interleaved noise the filter must skip
        _seed_run(client, "failed", name=f"f{i}",
                  started_at=base - timedelta(minutes=2 * i + 1),
                  finished=utcnow())
    page = client.get(
        "/workflows/runs",
        params={"status": "completed", "offset": 1, "limit": 1},
    ).json()["runs"]
    assert [r["id"] for r in page] == [done[1]]
    assert page[0]["status"] == "completed"
