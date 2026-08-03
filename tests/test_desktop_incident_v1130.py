"""v1.130.0 — desktop-shell incident intake (the renderer-watchdog's learn lane).

The Electron watchdog reports renderer freezes/crashes/GPU deaths to
POST /system/incident; the route publishes ``desktop.incident`` onto the event
bus, whose persist handler lands it in EventRecord — so a frozen window leaves
queryable evidence instead of nothing (the 2026-08-03 incident's exact gap).
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord
from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _incident_rows(client) -> list[EventRecord]:
    engine = client.app.state.platform.engine
    # The bus's persist handler is near-instant but not guaranteed inline —
    # poll briefly instead of asserting on a race.
    for _ in range(20):
        with session_scope(engine) as db:
            rows = list(
                db.exec(select(EventRecord).where(EventRecord.type == "desktop.incident"))
            )
        if rows:
            return rows
        time.sleep(0.05)
    return []


def test_incident_lands_in_the_event_log(client):
    r = client.post(
        "/system/incident",
        json={"kind": "renderer-frozen", "detail": "heartbeat: 3 pings missed"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "kind": "renderer-frozen"}
    rows = _incident_rows(client)
    assert len(rows) == 1
    payload = json.loads(rows[0].payload_json)
    assert payload == {"kind": "renderer-frozen", "detail": "heartbeat: 3 pings missed"}


def test_incident_inputs_are_clamped_not_trusted(client):
    r = client.post(
        "/system/incident",
        json={"kind": "  GPU crash!!<script> ", "detail": "x " * 600},
    )
    assert r.status_code == 200
    out = r.json()
    # kind: lowercased, only [alnum-_] survives, capped at 40.
    assert out["kind"] == "gpucrashscript"
    rows = _incident_rows(client)
    payload = json.loads(rows[0].payload_json)
    assert len(payload["detail"]) <= 500


def test_blank_kind_becomes_unknown(client):
    r = client.post("/system/incident", json={"kind": "  !!  "})
    assert r.status_code == 200
    assert r.json()["kind"] == "unknown"
