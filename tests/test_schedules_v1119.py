"""v1.119.0 — Schedules: from timer to teammate.

Behavioral coverage for the three legs of the batch:
  S1  the ``task`` kind: a schedule fires a REAL agent session (origin-tagged,
      project-bindable) instead of requiring a pre-authored workflow;
  S2  result delivery: every fire reports its outcome to the user's
      destinations (all of them by default, narrowable, silenceable);
  S3  outcome truth: the row records how the last fire went + which session it
      spawned, and run-now RETURNS the outcome instead of just ``{ran: name}``.

Offline: the mock provider drives the session; the mock channel records
delivery. No cron ticks are awaited — everything goes through run-now.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import MockChannel
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling.models import KINDS


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _mock_channel(client) -> MockChannel:
    notifier = client.app.state.platform.notifier
    for ch in notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("mock channel missing from offline notifier")


# --------------------------------------------------------------------------- #
# S1 — the task kind
# --------------------------------------------------------------------------- #


def test_task_is_a_first_class_kind():
    # The primary kind exists and leads; losing it would gut the module.
    assert "task" in KINDS


def test_task_schedule_runs_a_real_session_with_origin(client):
    r = client.post(
        "/schedules",
        json={
            "name": "morning-brief",
            "cron": "0 8 * * *",
            "kind": "task",
            "payload": {"task": "Summarize yesterday and today's plan."},
        },
    )
    assert r.status_code == 200

    ran = client.post("/schedules/morning-brief/run")
    assert ran.status_code == 200
    body = ran.json()
    # Run-now returns the OUTCOME, not just an ack.
    assert body["last_status"] == "ok"
    assert body["last_session_id"], "task fire must record the session it spawned"

    # The spawned session is real, findable, and origin-tagged to its schedule.
    detail = client.get(f"/sessions/{body['last_session_id']}").json()
    assert detail["session"]["origin"] == "schedule:morning-brief"
    assert detail["session"]["status"] == "completed"


def test_task_schedule_carries_project_binding(client):
    proj = client.post("/projects", json={"name": "Taxes"}).json()
    pid = proj.get("id") or proj.get("project", {}).get("id")
    client.post(
        "/schedules",
        json={
            "name": "weekly-taxes",
            "cron": "0 9 * * 5",
            "kind": "task",
            "payload": {"task": "Review open items.", "project_id": pid},
        },
    )
    body = client.post("/schedules/weekly-taxes/run").json()
    assert body["last_status"] == "ok"
    detail = client.get(f"/sessions/{body['last_session_id']}").json()
    assert detail["session"]["project_id"] == pid


def test_task_schedule_without_text_rejected_at_add_time(client):
    # Fail at ADD time, not at 3am fire time.
    r = client.post(
        "/schedules",
        json={"name": "empty", "cron": "0 8 * * *", "kind": "task", "payload": {}},
    )
    assert r.status_code == 400
    assert "task" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# S2 — result delivery to destinations
# --------------------------------------------------------------------------- #


def test_fire_delivers_result_to_destinations(client):
    client.post(
        "/schedules",
        json={
            "name": "digest",
            "cron": "0 17 * * 5",
            "kind": "task",
            "payload": {"task": "Write the Friday digest."},
        },
    )
    mock = _mock_channel(client)
    before = len(mock.sent)
    client.post("/schedules/digest/run")
    fresh = mock.sent[before:]
    assert any("digest" in m and "done" in m for m in fresh), fresh


def test_notify_false_silences_delivery(client):
    client.post(
        "/schedules",
        json={
            "name": "quiet",
            "cron": "0 8 * * *",
            "kind": "task",
            "payload": {"task": "Do it quietly.", "notify": False},
        },
    )
    mock = _mock_channel(client)
    before = len(mock.sent)
    client.post("/schedules/quiet/run")
    # The generic session.completed alert (a separate, pre-existing pathway)
    # may still fire; what notify=False silences is the SCHEDULE delivery.
    assert not any("quiet" in m and "Schedule" in m for m in mock.sent[before:])


def test_unknown_destination_rejected_at_add_time(client):
    r = client.post(
        "/schedules",
        json={
            "name": "typo-dest",
            "cron": "0 8 * * *",
            "kind": "task",
            "payload": {"task": "x", "notify_channels": ["telegramm"]},
        },
    )
    assert r.status_code == 400
    assert "telegramm" in r.json()["detail"]
    assert "Notifications" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# S3 — outcome truth on the row (including honest failure)
# --------------------------------------------------------------------------- #


def test_row_records_outcome_after_fire(client):
    client.post(
        "/schedules",
        json={
            "name": "truth",
            "cron": "0 8 * * *",
            "kind": "task",
            "payload": {"task": "Report the truth."},
        },
    )
    client.post("/schedules/truth/run")
    rows = client.get("/schedules").json()["schedules"]
    row = next(t for t in rows if t["name"] == "truth")
    assert row["last_status"] == "ok"
    assert row["last_detail"]  # a human detail, not blank
    assert row["last_session_id"].startswith("session_")
    assert row["last_run"] is not None  # a recorded failure still counts as ran


def test_failed_fire_records_error_and_delivers_failure(client):
    # A workflow schedule pointing at a workflow that doesn't exist: the classic
    # silent 3am failure. It must (a) mark the row, (b) tell the user, and
    # (c) still stamp last_run instead of vanishing into the scheduler thread.
    client.post(
        "/schedules",
        json={
            "name": "broken",
            "cron": "0 3 * * *",
            "kind": "workflow",
            "payload": {"workflow": "does-not-exist"},
        },
    )
    mock = _mock_channel(client)
    before = len(mock.sent)
    body = client.post("/schedules/broken/run").json()
    assert body["last_status"] == "error"
    assert "does-not-exist" in body["last_detail"]
    row = next(
        t for t in client.get("/schedules").json()["schedules"] if t["name"] == "broken"
    )
    assert row["last_status"] == "error"
    assert row["last_run"] is not None
    fresh = mock.sent[before:]
    assert any("FAILED" in m and "broken" in m for m in fresh), fresh


def test_event_kind_still_works_and_records(client):
    client.post(
        "/schedules",
        json={
            "name": "pinger",
            "cron": "0 8 * * *",
            "kind": "event",
            "payload": {"type": "custom.ping", "notify": False},
        },
    )
    body = client.post("/schedules/pinger/run").json()
    assert body["last_status"] == "ok"
    assert "custom.ping" in body["last_detail"]
