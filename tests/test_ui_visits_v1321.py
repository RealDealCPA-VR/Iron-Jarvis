"""Calm UI redesign S0: page opens are recorded and countable.

``POST /ui/visit`` publishes ``ui.page_opened`` (persisted like every event);
``GET /ui/visits`` counts them per route. The route is cut to its first path
segment, so ids, queries and file names never reach the record.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import EventRecord
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.ui import normalize_route


@pytest.mark.parametrize(
    "raw, want",
    [
        ("/", "/"),
        ("/sessions", "/sessions"),
        ("/sessions/sess_abc123?tab=files#top", "/sessions"),
        ("/Settings", "/settings"),
        ("/chat?thread=t9&ask=my%20tax%20return", "/chat"),
        ("sessions", None),
        ("/../etc", None),
        ("/x y", None),
        ("", None),
    ],
)
def test_normalize_route_keeps_only_the_first_plain_segment(raw, want):
    assert normalize_route(raw) == want


def _events(client):
    with session_scope(client.app.state.platform.engine) as db:
        return [
            json.loads(r.payload_json)
            for r in db.exec(select(EventRecord).where(EventRecord.type == EventType.UI_PAGE_OPENED))
        ]


def _wait(client, n):
    for _ in range(50):
        if len(_events(client)) >= n:
            return
        time.sleep(0.05)


def test_a_visit_is_recorded_without_ids_and_counted(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/ui/visit", json={"route": "/sessions/sess_1?x=1", "via": "nav"}).json() == {
        "ok": True,
        "route": "/sessions",
    }
    client.post("/ui/visit", json={"route": "/sessions", "via": "weird"})
    client.post("/ui/visit", json={"route": "/chat"})
    _wait(client, 3)
    payloads = _events(client)
    assert {"route": "/sessions", "via": "nav"} in payloads
    assert all("sess_1" not in json.dumps(p) for p in payloads)
    counts = client.get("/ui/visits").json()["routes"]
    assert counts[0] == {"route": "/sessions", "opens": 2, "nav": 1, "link": 1}
    assert {"route": "/chat", "opens": 1, "nav": 0, "link": 1} in counts


def test_junk_is_refused(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/ui/visit", json={"route": "https://evil.example/x"}).status_code == 422
    assert client.post("/ui/visit", json={"route": r"/C:\Users\me"}).status_code == 422
