"""v1.351.0 lane 1B, G04: the bell remembers what happened while the window
was closed — the daemon half.

The bell (dashboard/components/NotificationBell.tsx) reads, on its first
mount, ``GET /audit?type=<every type toActivity maps>&since=<lastSeen>`` and
maps each entry back through ``toActivity``. That needs two things of the
audit timeline, both pinned here through the REAL app factory:

* ``type`` takes a comma-separated LIST (one type behaves exactly as before);
* every entry carries ``type`` — the raw event type (``tool.executed`` /
  ``tool.denied`` for a ledger row) beside its ``kind`` bucket.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    AgentRun,
    EventRecord,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval import observability

_BASE = datetime(2026, 10, 18, 6, 0, 0)
_REPO = Path(__file__).resolve().parents[1]


def _bell_activity_types() -> list[str]:
    """``BELL_ACTIVITY_TYPES`` as the dashboard source spells it (CRLF-
    normalised at the reader — CI checks out with autocrlf)."""
    src = (_REPO / "dashboard" / "components" / "NotificationBell.tsx").read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n")
    block = re.search(
        r"export const BELL_ACTIVITY_TYPES: readonly string\[\] = \[(.*?)\n\];", src, re.S
    )
    assert block, "BELL_ACTIVITY_TYPES not found in NotificationBell.tsx"
    return re.findall(r'^\s*"([a-z_.]+)",', block.group(1), re.M)


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _event(db, eid: str, etype: str, minutes: int, payload: str = "{}", sid: str | None = "s1"):
    db.add(
        EventRecord(
            id=eid,
            type=etype,
            session_id=sid,
            payload_json=payload,
            created_at=_BASE + timedelta(minutes=minutes),
        )
    )


def _seed(engine) -> None:
    with session_scope(engine) as db:
        db.add(
            Session(
                id="s1",
                origin="schedule:morning-brief",
                status=SessionStatus.FAILED,
                created_at=_BASE,
            )
        )
        db.add(AgentRun(id="r1", session_id="s1", created_at=_BASE))
        # Before the bell was last opened (06:30) …
        _event(db, "e_old", "session.completed", 10, '{"status": "failed", "ok": false}')
        # … and after it.
        _event(
            db,
            "e_7am",
            "session.completed",
            60,
            '{"status": "failed", "ok": false, "origin": "schedule:morning-brief"}',
        )
        _event(db, "e_media", "media.failed", 61, '{"kind": "image", "error": "no credit"}', None)
        _event(db, "e_routed", "provider.routed", 62, '{"model": "m"}')
        _event(db, "e_llm", "llm.completed", 63, '{"input_tokens": 1, "output_tokens": 1}')
        db.add(
            ToolInvocation(
                id="t_ok",
                session_id="s1",
                agent_run_id="r1",
                tool="read_file",
                verdict=PermissionMode.ALLOW,
                ok=True,
                reversibility="readonly",
                created_at=_BASE + timedelta(minutes=64),
            )
        )
        db.commit()


def test_a_type_list_returns_exactly_those_types_newest_first_with_their_type(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform.engine)
    r = client.get(
        "/audit",
        params={"type": "session.completed,media.failed", "since": "2026-10-18T06:30:00Z"},
    )
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["entries"]] == ["e_media", "e_7am"]
    assert [e["type"] for e in body["entries"]] == ["media.failed", "session.completed"]
    # The bell maps the entry as it maps a live event: session id + payload.
    seven = body["entries"][1]
    assert seven["session_id"] == "s1"
    assert seven["payload"]["origin"] == "schedule:morning-brief"


def test_since_drops_what_came_before_the_bell_was_last_opened(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform.engine)
    everything = client.get("/audit", params={"type": "session.completed,media.failed"}).json()
    assert {e["id"] for e in everything["entries"]} == {"e_old", "e_7am", "e_media"}
    later = client.get(
        "/audit",
        params={"type": "session.completed,media.failed", "since": "2026-10-18T06:30:00Z"},
    ).json()
    assert "e_old" not in {e["id"] for e in later["entries"]}


def test_one_type_behaves_exactly_as_before(tmp_path):
    """Control: a single type is the old path (and now names its type)."""
    client = _client(tmp_path)
    _seed(client.app.state.platform.engine)
    body = client.get("/audit", params={"type": "media.failed"}).json()
    assert [e["id"] for e in body["entries"]] == ["e_media"]
    assert body["entries"][0]["type"] == "media.failed"
    tools = client.get("/audit", params={"type": "tool.executed"}).json()
    assert [e["id"] for e in tools["entries"]] == ["t_ok"]
    assert tools["entries"][0]["type"] == "tool.executed"


def test_a_list_can_name_the_ledger_and_events_together(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform.engine)
    body = client.get(
        "/audit",
        params={"type": "tool.executed,media.failed", "since": "2026-10-18T06:30:00Z"},
    ).json()
    assert [(e["id"], e["type"]) for e in body["entries"]] == [
        ("t_ok", "tool.executed"),
        ("e_media", "media.failed"),
    ]


def test_a_list_respects_the_kind_filter_and_unknown_names_match_nothing(tmp_path):
    client = _client(tmp_path)
    _seed(client.app.state.platform.engine)
    # kind=lifecycle admits session.completed, never media.failed.
    body = client.get(
        "/audit", params={"type": "session.completed,media.failed", "kind": "lifecycle"}
    ).json()
    assert {e["type"] for e in body["entries"]} == {"session.completed"}
    none = client.get("/audit", params={"type": "nothing.here,nor.this"}).json()
    assert none["entries"] == []


def test_the_type_list_is_bounded_and_deduplicated():
    names = [f"t{i}.x" for i in range(60)]
    got = observability._type_list(",".join(["a.b", " a.b ", "", *names]))
    assert got[0] == "a.b"
    assert got.count("a.b") == 1
    assert len(got) == observability._TYPE_LIST_MAX
    assert observability._type_list(None) == []
    assert observability._type_list(" , ") == []


def test_the_bells_own_list_fits_and_every_type_comes_back(tmp_path):
    """Contract with the dashboard: the list the bell sends (read from its
    source) is under the cap, and an event of EACH type is returned."""
    types = _bell_activity_types()
    assert len(types) >= 18, types  # anti-vacuity: the scan found the list
    assert "session.completed" in types and "workflow.completed" in types
    assert len(types) <= observability._TYPE_LIST_MAX
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    with session_scope(engine) as db:
        for i, t in enumerate(types):
            _event(db, f"ev_{i}", t, 100 + i, "{}", None)
        db.commit()
    body = client.get(
        "/audit", params={"type": ",".join(types), "since": "2026-10-18T07:00:00Z", "limit": 50}
    ).json()
    assert sorted(e["type"] for e in body["entries"]) == sorted(types)
