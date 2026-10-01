"""Give an agent a job and the job waits for it (v1.296.0, wave 2 — the HTTP
doors in ``daemon/routes/assignments.py`` + the inbox/roster/project seams).

Drives the REAL app factory (``create_app``) over a ``TestClient``; every
assertion is about what a user of the Agents or Projects page can reach:

* ``POST /assignments`` queues a job for a builtin or a custom agent (201,
  ``created: true``); re-posting the same ``idempotency_key`` coalesces onto
  the queued row (200, ``created: false``, ``coalesced_count`` 1);
* every refusal is a plain-words 422 (unknown assignee, the supervisor, a
  priority outside -10..10, a payload key that is not one of the five);
* ``GET /assignments`` filters by assignee / project / status; ``GET
  /assignments/{id}`` 404s on an unknown id;
* cancel flips a queued row and, for a RUNNING row, stops its session through
  the orchestrator FIRST (mutation-pinned); unblock resets a blocked row;
  retry makes a NEW row with ``source == "retry:<old id>"``; a transition the
  store refuses is a 409;
* ``GET /agents/{name}/inbox`` resolves a bare custom name or its roster form
  and answers the queue + health; roster rows carry ``health``;
* ``POST /projects/{id}/task`` with an ``assignee`` queues an assignment (202)
  carrying the project id and the project's root as ``workspace_root`` and
  makes NO Session row; without one it starts a session exactly as before.

The dispatcher's ``tick`` is held to a no-op for the whole fixture: these are
route tests, and a dispatcher claiming the row under an assertion would turn
"queued" into "done" at random.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app

NAME = "tax-reader"
ROSTER = f"custom:{NAME}"

HIRE = {
    "name": NAME,
    "system_prompt": "read tax documents",
    "tools": ["read_file"],
    "description": "reads client tax documents",
    "provider": "mock",
    "model": "mock-1",
    "base_type": "researcher",
}

ROW_KEYS = {
    "id", "project_id", "assignee", "task", "title", "priority", "status",
    "source", "reason", "payload", "idempotency_key", "coalesced_count",
    "attempts", "failure_count", "blocked_reason", "held_reason", "depth",
    "session_id", "last_error", "created_at", "claimed_at", "started_at",
    "finished_at", "updated_at",
}
INBOX_KEYS = {"queued", "claimed", "running", "blocked", "recent"}
HEALTH_KEYS = {
    "last_run_at", "last_outcome", "last_error", "last_wake_at",
    "queued", "running", "blocked",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    try:
        from iron_jarvis.assignments.dispatcher import AssignmentDispatcher

        async def _no_tick(self):  # the loop stays armed; nothing is claimed
            return None

        monkeypatch.setattr(AssignmentDispatcher, "tick", _no_tick)
    except ImportError:
        pass
    with TestClient(create_app(str(tmp_path))) as c:
        r = c.post("/agents", json=HIRE)
        assert r.status_code == 200, r.text
        yield c


def _store(client):
    store = getattr(client.app.state.platform, "assignments", None)
    assert store is not None, "the real app factory must wire platform.assignments"
    return store


def _create(client, **over):
    body = {"assignee": NAME, "task": "read the K-1 and list the partners", **over}
    r = client.post("/assignments", json=body)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["created"] is True
    return data["assignment"]


def _detail_text(r) -> str:
    """A refusal's words, whether FastAPI's body-validation list or a sentence."""
    detail = r.json()["detail"]
    return detail if isinstance(detail, str) else " ".join(str(e.get("msg", e)) for e in detail)


def _mk_project(client, tmp_path):
    root = tmp_path / "acme"
    root.mkdir(exist_ok=True)
    r = client.post("/projects", json={"name": "Acme", "root": str(root)})
    assert r.status_code == 200, r.text
    return r.json()


def _sessions_for_project(client, project_id) -> list[Session]:
    with session_scope(client.app.state.platform.engine) as db:
        return list(db.exec(select(Session).where(Session.project_id == project_id)))


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
def test_create_queues_a_job_for_a_custom_agent(client):
    row = _create(client, reason="monthly close", priority=3)
    assert ROW_KEYS <= set(row), sorted(ROW_KEYS - set(row))
    assert row["assignee"] == ROSTER, "a bare custom name resolves to its roster name"
    assert row["status"] == "queued"
    assert row["source"] == "user"
    assert row["reason"] == "monthly close"
    assert row["priority"] == 3
    assert row["payload"] == {}
    assert row["session_id"] == "" and row["created_at"]
    assert client.get(f"/assignments/{row['id']}").json()["assignment"]["id"] == row["id"]


def test_create_accepts_a_builtin_and_the_roster_form(client):
    assert _create(client, assignee="builder")["assignee"] == "builder"
    assert _create(client, assignee=ROSTER)["assignee"] == ROSTER


def test_same_idempotency_key_coalesces_onto_the_queued_row(client):
    first = _create(client, idempotency_key="close-2026-09")
    r = client.post(
        "/assignments",
        json={"assignee": NAME, "task": "read the K-1", "idempotency_key": "close-2026-09"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["created"] is False
    assert data["assignment"]["id"] == first["id"]
    assert data["assignment"]["coalesced_count"] == 1
    rows = client.get(f"/assignments?assignee={NAME}").json()["assignments"]
    assert len(rows) == 1, "a coalesced re-post makes no second row"


def test_create_wakes_the_dispatcher(client, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        client.app.state.platform,
        "assignment_dispatcher",
        SimpleNamespace(wake=lambda: calls.append("wake")),
        raising=False,
    )
    _create(client)
    assert calls == ["wake"]


def test_create_payload_keeps_the_five_session_knobs(client, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    row = _create(
        client,
        payload={
            "allow_tools": ["read_file", " write_file "],
            "workspace_root": str(folder),
            "max_steps": 5,
            "provider": "mock",
            "model": "mock-1",
        },
    )
    assert row["payload"] == {
        "allow_tools": ["read_file", "write_file"],
        "workspace_root": str(folder),
        "max_steps": 5,
        "provider": "mock",
        "model": "mock-1",
    }


# --------------------------------------------------------------------------- #
# Refusals, in plain words
# --------------------------------------------------------------------------- #
def test_unknown_assignee_is_a_422_with_a_sentence(client):
    r = client.post("/assignments", json={"assignee": "nobody", "task": "x"})
    assert r.status_code == 422, r.text
    assert "unknown assignee 'nobody'" in r.json()["detail"]
    assert "Agents page" in r.json()["detail"]
    assert client.get("/assignments").json()["assignments"] == []


def test_the_supervisor_cannot_take_an_assignment(client):
    r = client.post("/assignments", json={"assignee": "supervisor", "task": "x"})
    assert r.status_code == 422, r.text
    assert "supervisor" in r.json()["detail"]


def test_a_store_refusal_is_a_422_with_its_sentence(client):
    # The planner is a builtin the ROUTE admits but the store refuses (it can
    # delegate); the store's own sentence must reach the user, not a 500.
    r = client.post("/assignments", json={"assignee": "planner", "task": "x"})
    assert r.status_code == 422, r.text
    assert "delegate" in r.json()["detail"]


@pytest.mark.parametrize("priority", [11, -11, 2.5, True])
def test_priority_outside_the_band_is_a_422(client, priority):
    r = client.post("/assignments", json={"assignee": NAME, "task": "x", "priority": priority})
    assert r.status_code == 422, r.text
    assert "priority must be" in _detail_text(r)


def test_a_stray_payload_key_is_refused_by_name(client):
    r = client.post(
        "/assignments",
        json={"assignee": NAME, "task": "x", "payload": {"max_steps": 3, "foo": 1}},
    )
    assert r.status_code == 422, r.text
    words = _detail_text(r)
    assert "'foo'" in words and "only allow_tools, workspace_root, max_steps, provider, model" in words
    assert client.get("/assignments").json()["assignments"] == []


def test_an_unusable_workspace_root_is_refused_like_the_spawn_route(client, tmp_path):
    missing = tmp_path / "nowhere"
    r = client.post(
        "/assignments",
        json={"assignee": NAME, "task": "x", "payload": {"workspace_root": str(missing)}},
    )
    assert r.status_code == 400, r.text
    assert "workspace_root must be an existing" in r.json()["detail"]
    assert str(missing) in r.json()["detail"]


def test_blank_task_and_blank_assignee_are_422(client):
    assert client.post("/assignments", json={"assignee": NAME, "task": "  "}).status_code == 422
    assert client.post("/assignments", json={"assignee": "", "task": "x"}).status_code == 422


# --------------------------------------------------------------------------- #
# List + get
# --------------------------------------------------------------------------- #
def test_list_filters_by_assignee_project_and_status(client):
    a = _create(client, project_id="p-1")
    b = _create(client, assignee="builder", project_id="p-2")
    c = _create(client, project_id="p-1")
    client.post(f"/assignments/{c['id']}/cancel")

    ids = lambda r: {row["id"] for row in r.json()["assignments"]}  # noqa: E731
    assert ids(client.get("/assignments")) == {a["id"], b["id"], c["id"]}
    assert ids(client.get(f"/assignments?assignee={NAME}")) == {a["id"], c["id"]}
    assert ids(client.get(f"/assignments?assignee={ROSTER}")) == {a["id"], c["id"]}
    assert ids(client.get("/assignments?assignee=builder")) == {b["id"]}
    assert ids(client.get("/assignments?project_id=p-1")) == {a["id"], c["id"]}
    assert ids(client.get("/assignments?status=cancelled")) == {c["id"]}
    assert ids(client.get("/assignments?status=queued,cancelled&project_id=p-1")) == {a["id"], c["id"]}
    assert len(client.get("/assignments?limit=1").json()["assignments"]) == 1


def test_list_refuses_a_typo_status_and_a_bad_limit(client):
    r = client.get("/assignments?status=qeued")
    assert r.status_code == 422 and "unknown status 'qeued'" in r.json()["detail"]
    assert client.get("/assignments?limit=0").status_code == 422
    r = client.get("/assignments?assignee=nobody")
    assert r.status_code == 422 and "unknown assignee" in r.json()["detail"]


def test_get_unknown_id_is_404(client):
    r = client.get("/assignments/asg_nope")
    assert r.status_code == 404
    assert "asg_nope" in r.json()["detail"]
    assert client.post("/assignments/asg_nope/cancel").status_code == 404
    assert client.post("/assignments/asg_nope/unblock").status_code == 404
    assert client.post("/assignments/asg_nope/retry").status_code == 404


# --------------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------------- #
def test_cancel_a_queued_row(client):
    row = _create(client)
    r = client.post(f"/assignments/{row['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["assignment"]["status"] == "cancelled"
    assert r.json()["assignment"]["finished_at"]
    again = client.post(f"/assignments/{row['id']}/cancel")
    assert again.status_code == 409, again.text
    assert "cancelled" in again.json()["detail"]


def test_cancel_a_running_row_stops_its_session_first(client, monkeypatch):
    store = _store(client)
    row = _create(client)
    assert store.claim_next(ROSTER).id == row["id"]
    assert store.start(row["id"], "sess-live").status == "running"
    stopped: list[str] = []
    d = client.app.state.d
    monkeypatch.setattr(d.orchestrator, "cancel_session", lambda sid: stopped.append(sid))
    r = client.post(f"/assignments/{row['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["assignment"]["status"] == "cancelled"
    assert stopped == ["sess-live"], "the session must be stopped BEFORE the row flips"


def test_cancel_a_running_row_whose_session_already_settled(client, monkeypatch):
    store = _store(client)
    row = _create(client)
    store.claim_next(ROSTER)
    store.start(row["id"], "sess-gone")

    def _settled(sid):
        raise ValueError(f"session '{sid}' is already completed")

    monkeypatch.setattr(client.app.state.d.orchestrator, "cancel_session", _settled)
    r = client.post(f"/assignments/{row['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["assignment"]["status"] == "cancelled"


def _fail_three_times(client, store) -> dict:
    """Drive one job to BLOCKED through the store's own breaker: three failed
    runs in a row, each retry carrying the failure count forward."""
    row = _create(client)
    current = row["id"]
    for n in range(3):
        assert store.claim_next(ROSTER).id == current
        store.start(current, f"sess-{n}")
        record = store.finish(current, ok=False, error=f"boom {n}")
        if n < 2:
            assert record.status == "failed", record.status
            r = client.post(f"/assignments/{current}/retry")
            assert r.status_code == 201, r.text
            current = r.json()["assignment"]["id"]
    assert record.status == "blocked"
    return client.get(f"/assignments/{current}").json()["assignment"]


def test_unblock_a_blocked_row(client):
    store = _store(client)
    blocked = _fail_three_times(client, store)
    assert blocked["status"] == "blocked"
    assert "3 failed runs in a row" in blocked["blocked_reason"]
    r = client.post(f"/assignments/{blocked['id']}/unblock")
    assert r.status_code == 200, r.text
    row = r.json()["assignment"]
    assert row["status"] == "queued"
    assert row["failure_count"] == 0 and row["blocked_reason"] == ""
    again = client.post(f"/assignments/{blocked['id']}/unblock")
    assert again.status_code == 409, again.text


def test_retry_makes_a_new_row_from_a_finished_one(client):
    old = _create(client, reason="monthly close", priority=2, payload={"max_steps": 4})
    client.post(f"/assignments/{old['id']}/cancel")
    r = client.post(f"/assignments/{old['id']}/retry")
    assert r.status_code == 201, r.text
    new = r.json()["assignment"]
    assert new["id"] != old["id"]
    assert new["source"] == f"retry:{old['id']}"
    assert new["status"] == "queued"
    assert new["assignee"] == ROSTER
    assert new["reason"] == "monthly close" and new["priority"] == 2
    assert new["payload"] == {"max_steps": 4}
    # A row still waiting cannot be retried — it has not finished.
    r = client.post(f"/assignments/{new['id']}/retry")
    assert r.status_code == 409, r.text


# --------------------------------------------------------------------------- #
# The inbox and the roster
# --------------------------------------------------------------------------- #
def test_inbox_for_a_custom_agent_by_bare_or_roster_name(client):
    row = _create(client)
    for name in (NAME, ROSTER):
        r = client.get(f"/agents/{name}/inbox")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["assignee"] == ROSTER
        assert set(data["inbox"]) == INBOX_KEYS
        assert [x["id"] for x in data["inbox"]["queued"]] == [row["id"]]
        assert data["inbox"]["running"] == [] and data["inbox"]["recent"] == []
        assert HEALTH_KEYS <= set(data["health"]), sorted(HEALTH_KEYS - set(data["health"]))
        assert data["health"]["queued"] == 1 and data["health"]["running"] == 0


def test_inbox_for_a_builtin_and_an_unknown_name(client):
    r = client.get("/agents/builder/inbox")
    assert r.status_code == 200, r.text
    assert r.json()["assignee"] == "builder"
    r = client.get("/agents/nobody/inbox")
    assert r.status_code == 404, r.text
    assert "unknown assignee 'nobody'" in r.json()["detail"]


def test_roster_rows_carry_health(client):
    r = client.get("/agents/roster")
    assert r.status_code == 200, r.text
    rows = r.json()["roster"]
    assert rows, "the roster must list at least the builtins"
    assert all("health" in row for row in rows), [row["name"] for row in rows if "health" not in row]
    mine = {row["name"]: row for row in rows}[ROSTER]
    assert mine["health"] is None or isinstance(mine["health"], dict)


# --------------------------------------------------------------------------- #
# The project task door
# --------------------------------------------------------------------------- #
def test_project_task_with_an_assignee_queues_an_assignment(client, tmp_path):
    p = _mk_project(client, tmp_path)
    r = client.post(
        f"/projects/{p['id']}/task",
        json={
            "text": "inventory the files",
            "output": "xlsx",
            "filename": "inventory",
            "assignee": NAME,
            "allow_tools": ["write_document"],
            "max_steps": 6,
        },
    )
    assert r.status_code == 202, r.text
    data = r.json()
    assert data["queued"] is True
    assert data["output"] == "xlsx"
    assert data["target_path"].endswith("inventory.xlsx")
    row = data["assignment"]
    assert row["assignee"] == ROSTER
    assert row["status"] == "queued"
    assert row["project_id"] == p["id"]
    assert row["source"] == "project" and row["reason"] == "project task"
    assert row["payload"]["workspace_root"] == str(Path(p["root"]))
    assert row["payload"]["allow_tools"] == ["write_document"]
    assert row["payload"]["max_steps"] == 6
    assert "inventory.xlsx" in row["task"] and "write_document" in row["task"]
    assert _sessions_for_project(client, p["id"]) == [], "no session until the agent is free"
    assert client.get(f"/assignments/{row['id']}").status_code == 200


def test_project_task_without_an_assignee_starts_a_session_as_before(client, tmp_path):
    p = _mk_project(client, tmp_path)
    r = client.post(f"/projects/{p['id']}/task", json={"text": "summarize this folder"})
    assert r.status_code == 200, r.text
    view = r.json()
    assert "assignment" not in view and "queued" not in view
    assert view["project_id"] == p["id"] and view["output"] == "chat"
    assert [s.id for s in _sessions_for_project(client, p["id"])] == [view["id"]]
    assert client.get("/assignments").json()["assignments"] == []


def test_project_task_with_an_unknown_assignee_is_422(client, tmp_path):
    p = _mk_project(client, tmp_path)
    r = client.post(f"/projects/{p['id']}/task", json={"text": "x", "assignee": "nobody"})
    assert r.status_code == 422, r.text
    assert "unknown assignee 'nobody'" in r.json()["detail"]
    assert _sessions_for_project(client, p["id"]) == []
    r = client.post(f"/projects/{p['id']}/task", json={"text": "x", "assignee": "supervisor"})
    assert r.status_code == 422, r.text
