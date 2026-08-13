"""v1.169.0 P1 — project heartbeat: GET /schedules exposes the decoded project_id.

The project surface answers "what runs on my behalf in this project, and did
last night's run succeed?" by filtering the schedules list on the payload's
``project_id``. The list endpoint now decodes that one key server-side and
puts it on every row (ADDITIVE — nothing existing moves or disappears).

What is guarded, each with a silent failure mode:
  - the decoded value is the EXACT payload string (a wrong/blank decode makes
    a project's schedules invisible on its surface — the row still exists on
    the Schedules page, so nobody notices);
  - a payload without a project binding yields ``""``, never a fabricated id;
  - a NON-STRING project_id is refused, never coerced (``str(123)`` could
    phantom-match a real project id named "123");
  - an unparseable payload blob must not 500 the whole list — one corrupt row
    would take down the Schedules page AND every project surface at once;
  - the existing row fields (the v1.119.0 outcome truth the Schedules page
    renders) survive unchanged — the additive proof.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling.models import ScheduledTaskRecord


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _add(client, name: str, payload: dict, kind: str = "task") -> None:
    r = client.post(
        "/schedules",
        json={"name": name, "cron": "0 9 * * *", "kind": kind, "payload": payload},
    )
    assert r.status_code == 200, r.text


def _row(client, name: str) -> dict:
    rows = client.get("/schedules").json()["schedules"]
    return next(t for t in rows if t["name"] == name)


def test_list_exposes_decoded_project_id(client):
    proj = client.post("/projects", json={"name": "Taxes"}).json()
    pid = proj.get("id") or proj.get("project", {}).get("id")
    assert pid
    _add(client, "weekly-taxes", {"task": "Review open items.", "project_id": pid})
    # The VALUE, not just presence — a blank decode hides the row from its
    # project surface while everything else still looks green.
    assert _row(client, "weekly-taxes")["project_id"] == pid


def test_project_id_empty_when_payload_has_none(client):
    _add(client, "unbound", {"task": "No project here."})
    assert _row(client, "unbound")["project_id"] == ""


def test_non_string_project_id_is_refused_not_coerced(client):
    _add(client, "numeric", {"task": "x", "project_id": 123})
    row = _row(client, "numeric")
    assert row["project_id"] == ""
    assert row["project_id"] != "123"  # coercion could phantom-match a project


def test_unparseable_payload_yields_empty_and_the_list_survives(client):
    _add(client, "fine", {"task": "healthy row"})
    _add(client, "mangled", {"task": "about to be corrupted"})
    # Corrupt the blob directly in the DB — the decode must degrade to "" for
    # this ROW, not 500 the whole list.
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        rec = db.exec(
            select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == "mangled")
        ).first()
        rec.payload_json = "{not json"
        db.add(rec)
        db.commit()
    r = client.get("/schedules")
    assert r.status_code == 200
    rows = {t["name"]: t for t in r.json()["schedules"]}
    assert rows["mangled"]["project_id"] == ""
    assert rows["fine"]["name"] == "fine"  # the healthy row still lists


def test_non_object_json_payload_yields_empty_and_the_list_survives(client):
    # VALID JSON that is not an object ("[]", '"x"', "3") parses fine, so the
    # except-clause never fires — without an isinstance-dict guard the route
    # calls .get on a list/str/int and 500s the ENTIRE list (reviewer-confirmed
    # live repro). Same DB-corruption threat model as the unparseable test
    # above, different code path.
    _add(client, "healthy", {"task": "still fine"})
    corrupt = {"as-array": "[]", "as-string": '"x"', "as-number": "3"}
    for name in corrupt:
        _add(client, name, {"task": "about to become non-object JSON"})
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        for name, blob in corrupt.items():
            rec = db.exec(
                select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == name)
            ).first()
            rec.payload_json = blob
            db.add(rec)
        db.commit()
    r = client.get("/schedules")
    assert r.status_code == 200, r.text
    rows = {t["name"]: t for t in r.json()["schedules"]}
    for name in corrupt:
        assert rows[name]["project_id"] == ""
    assert rows["healthy"]["name"] == "healthy"  # the healthy row still lists


def test_decode_is_kind_agnostic(client):
    # The SERVER decodes for every kind; filtering task-kind rows is the
    # frontend's job (a workflow bound to a project still tells the truth).
    _add(client, "wf", {"workflow": "some-flow", "project_id": "proj_x"}, kind="workflow")
    assert _row(client, "wf")["project_id"] == "proj_x"


def test_existing_row_fields_survive_unchanged(client):
    _add(client, "additive-proof", {"task": "Prove nothing broke.", "project_id": "proj_y"})
    row = _row(client, "additive-proof")
    # The v1.119.0 outcome-truth fields the Schedules page renders are intact…
    for key in (
        "name",
        "cron",
        "kind",
        "enabled",
        "next_run",
        "last_run",
        "trigger_type",
        "payload_json",
        "last_status",
        "last_detail",
        "last_session_id",
    ):
        assert key in row, f"additive change dropped {key!r}"
    assert row["cron"] == "0 9 * * *"
    assert row["kind"] == "task"
    # …and the payload blob itself is untouched (the Schedules page still
    # parses it client-side for its "what this does" line).
    import json

    assert json.loads(row["payload_json"]) == {
        "task": "Prove nothing broke.",
        "project_id": "proj_y",
    }


def test_run_now_outcome_still_reports_after_decode_change(client):
    # End-to-end through the changed route: fire a project-bound schedule and
    # read the row back — outcome truth AND the decoded binding coexist.
    proj = client.post("/projects", json={"name": "Heartbeat"}).json()
    pid = proj.get("id") or proj.get("project", {}).get("id")
    _add(client, "beat", {"task": "Report the heartbeat.", "project_id": pid})
    ran = client.post("/schedules/beat/run").json()
    assert ran["last_status"] == "ok"
    row = _row(client, "beat")
    assert row["project_id"] == pid
    assert row["last_status"] == "ok"
    assert row["last_session_id"].startswith("session_")
