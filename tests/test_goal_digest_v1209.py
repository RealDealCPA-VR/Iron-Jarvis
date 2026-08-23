"""Goal DIGEST + goal NEWS (G2, v1.209.0) — offline, deterministic.

Pinned here:

* ``compose_digest`` composes each figure from a record a model cannot edit:
  iterations from ``goal.iteration_completed`` EventRecords (chosen over a
  ``spent.iterations`` delta — a cumulative counter has no history to window),
  spend from the goal's session ROWS (the same recorded truth
  ``_settle_spend`` bills from), results from the session summary + ledger
  file harvest, asks held from the approval requested/resolved join, state
  changes from satisfied/tripped events;
* the window is respected (old records invisible), a quiet goal is ABSENT,
  and an empty digest is honestly empty — shape intact, no invented rows;
* determinism: two calls over the same records and the same ``now`` return
  IDENTICAL dicts;
* the notifier alerts goal.satisfied / goal.tripped / goal.iteration_refused
  by default, in the house voice, and deliberately does NOT alert the
  iteration heartbeats (the approval.resolved precedent: routine motion is a
  log, not news);
* the notifier/digest string literals are pinned against the engine's
  constants so the mirrored copies cannot drift;
* ``GET /goals/digest`` answers the digest — and is NOT swallowed by
  ``GET /goals/{goal_id}`` (registration order, flagged in routes/goals.py).
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.comm import MockChannel, Notifier
from iron_jarvis.comm.notifier import (
    DEFAULT_ALERT_EVENTS,
    GOAL_ITERATION_REFUSED_EVENT,
    GOAL_SATISFIED_EVENT,
    GOAL_TRIPPED_EVENT,
    format_event,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import new_uid, utcnow
from iron_jarvis.core.models import EventRecord, Session, SessionStatus
from iron_jarvis.goals import digest as digest_mod
from iron_jarvis.goals.digest import compose_digest
from iron_jarvis.goals.engine import (
    GOAL_ITERATION_COMPLETED,
    GOAL_ITERATION_REFUSED,
    GOAL_ITERATION_STARTED,
    GOAL_SATISFIED,
    GOAL_TRIPPED,
)
from iron_jarvis.goals.store import GoalStore

TOKENS_BUDGET = {"max_tokens": 1_000_000}


# --------------------------------------------------------------------------- #
# seeding helpers — records only, no engine run (the digest reads records)
# --------------------------------------------------------------------------- #


def _goal(platform, name="Inbox zero"):
    return GoalStore(platform.engine).create(
        name=name, contract_text="do a thing", budget=TOKENS_BUDGET
    )


def _event(platform, type_, payload, *, session_id=None, at=None):
    with session_scope(platform.engine) as db:
        db.add(
            EventRecord(
                id=new_uid("evt"),
                type=type_,
                session_id=session_id,
                payload_json=json.dumps(payload),
                created_at=at or utcnow(),
            )
        )
        db.commit()


def _session(platform, goal_id, *, in_tok=100, out_tok=50, summary="did stuff", at=None):
    row = Session(
        task="iterate",
        origin=f"goal:{goal_id}",
        provider="mock",
        model="m",
        status=SessionStatus.COMPLETED,
        input_tokens=in_tok,
        output_tokens=out_tok,
        summary=summary,
        created_at=at or utcnow(),
    )
    with session_scope(platform.engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


# --------------------------------------------------------------------------- #
# composition from seeded records
# --------------------------------------------------------------------------- #


def test_digest_composes_every_figure_from_records(platform):
    now = utcnow()
    active = _goal(platform, "Inbox zero")
    _goal(platform, "Quiet goal")  # no activity — must be ABSENT
    gid = active.id

    # Two iterations inside the window (sessions + completion events)…
    sid1 = _session(platform, gid, in_tok=100, out_tok=50, summary="first pass",
                    at=now - timedelta(hours=2))
    sid2 = _session(platform, gid, in_tok=200, out_tok=100, summary="second pass",
                    at=now - timedelta(hours=1))
    _event(platform, GOAL_ITERATION_COMPLETED,
           {"goal_id": gid, "iteration": 1, "ok": True, "status": "completed"},
           session_id=sid1, at=now - timedelta(hours=2))
    _event(platform, GOAL_ITERATION_COMPLETED,
           {"goal_id": gid, "iteration": 2, "ok": True, "status": "completed"},
           session_id=sid2, at=now - timedelta(hours=1))
    # …and one OUTSIDE it (session + event, both 48h old): invisible.
    old_sid = _session(platform, gid, in_tok=999, out_tok=999,
                       at=now - timedelta(hours=48))
    _event(platform, GOAL_ITERATION_COMPLETED,
           {"goal_id": gid, "iteration": 0, "ok": True, "status": "completed"},
           session_id=old_sid, at=now - timedelta(hours=48))

    # An ask HELD while unattended (timeout), and one GRANTED (not held).
    _event(platform, "approval.requested",
           {"approval_id": "apr_1", "tool": "shell", "args": {}, "timeout_s": 300},
           session_id=sid2, at=now - timedelta(minutes=55))
    _event(platform, "approval.resolved",
           {"approval_id": "apr_1", "tool": "shell", "decision": "timeout"},
           session_id=sid2, at=now - timedelta(minutes=50))
    _event(platform, "approval.requested",
           {"approval_id": "apr_2", "tool": "write_file", "args": {}, "timeout_s": 300},
           session_id=sid2, at=now - timedelta(minutes=45))
    _event(platform, "approval.resolved",
           {"approval_id": "apr_2", "tool": "write_file", "decision": "once"},
           session_id=sid2, at=now - timedelta(minutes=44))

    # A satisfied transition inside the window.
    _event(platform, GOAL_SATISFIED, {"goal_id": gid, "iteration": 2},
           session_id=sid2, at=now - timedelta(minutes=30))

    d = compose_digest(platform, hours=24, now=now)

    assert d["since"] == (now - timedelta(hours=24)).isoformat()
    assert d["hours"] == 24
    assert [g["id"] for g in d["goals"]] == [gid]  # quiet goal absent
    g = d["goals"][0]
    assert g["name"] == "Inbox zero"
    assert g["ran"] == 2  # the 48h-old completion is outside the window
    # Spend from the session ROWS in the window only (100+50 + 200+100).
    assert g["spent"]["tokens"] == 450
    assert g["spent"]["dollars"] == 0.0  # mock provider has no price — honest 0
    # Results: per window session, ordered by created_at — recorded summary,
    # ledger file harvest (nothing seeded in the ledger → honestly empty).
    assert [r["session_id"] for r in g["results"]] == [sid1, sid2]
    assert [r["summary"] for r in g["results"]] == ["first pass", "second pass"]
    assert all(r["files"] == [] for r in g["results"])
    # Asks held: the timeout, not the grant.
    assert len(g["asks_held"]) == 1
    held = g["asks_held"][0]
    assert held["approval_id"] == "apr_1"
    assert held["tool"] == "shell"
    assert held["decision"] == "timeout"
    assert held["at"]  # timestamped
    # State changes: the satisfied transition.
    assert g["state_changes"] == [
        {"to": "satisfied", "reason": "", "at": g["state_changes"][0]["at"]}
    ]


def test_tripped_state_change_carries_the_reason(platform):
    now = utcnow()
    goal = _goal(platform)
    _event(platform, GOAL_TRIPPED,
           {"goal_id": goal.id, "reason": "3 failures in 30 minutes", "failures": 3},
           at=now - timedelta(hours=3))

    d = compose_digest(platform, hours=24, now=now)

    assert [g["id"] for g in d["goals"]] == [goal.id]
    assert d["goals"][0]["state_changes"] == [
        {
            "to": "tripped",
            "reason": "3 failures in 30 minutes",
            "at": d["goals"][0]["state_changes"][0]["at"],
        }
    ]
    assert d["goals"][0]["ran"] == 0  # tripping is not an iteration


def test_window_respected_goal_with_only_old_activity_is_absent(platform):
    now = utcnow()
    goal = _goal(platform)
    old = now - timedelta(hours=30)
    sid = _session(platform, goal.id, at=old)
    _event(platform, GOAL_ITERATION_COMPLETED,
           {"goal_id": goal.id, "iteration": 1, "ok": True}, session_id=sid, at=old)
    _event(platform, GOAL_SATISFIED, {"goal_id": goal.id, "iteration": 1}, at=old)

    d = compose_digest(platform, hours=24, now=now)
    assert d["goals"] == []
    # Widen the window and the same records appear — the window is the filter.
    wide = compose_digest(platform, hours=48, now=now)
    assert [g["id"] for g in wide["goals"]] == [goal.id]
    assert wide["goals"][0]["ran"] == 1


def test_digest_is_deterministic_two_calls_identical(platform):
    now = utcnow()
    goal = _goal(platform)
    sid = _session(platform, goal.id, at=now - timedelta(hours=1))
    _event(platform, GOAL_ITERATION_COMPLETED,
           {"goal_id": goal.id, "iteration": 1, "ok": True},
           session_id=sid, at=now - timedelta(hours=1))
    _event(platform, GOAL_TRIPPED, {"goal_id": goal.id, "reason": "boom"},
           at=now - timedelta(minutes=10))

    first = compose_digest(platform, hours=24, now=now)
    second = compose_digest(platform, hours=24, now=now)

    assert first == second  # pure function of the records + now


def test_empty_digest_shape_is_honest(platform):
    now = utcnow()
    d = compose_digest(platform, hours=24, now=now)
    assert d == {
        "since": (now - timedelta(hours=24)).isoformat(),
        "hours": 24,
        "goals": [],
    }
    # Bounds clamp instead of crashing or 400ing — a sloppy window still gets
    # a truthful report.
    assert compose_digest(platform, hours=0, now=now)["hours"] == 1
    assert compose_digest(platform, hours=10**6, now=now)["hours"] == 720
    assert compose_digest(platform, hours="garbage", now=now)["hours"] == 24


# --------------------------------------------------------------------------- #
# the mirrored literals cannot drift (notifier + digest vs the engine)
# --------------------------------------------------------------------------- #


def test_goal_event_literals_pinned_against_the_engine():
    assert GOAL_SATISFIED_EVENT == GOAL_SATISFIED
    assert GOAL_TRIPPED_EVENT == GOAL_TRIPPED
    assert GOAL_ITERATION_REFUSED_EVENT == GOAL_ITERATION_REFUSED
    assert digest_mod._ITERATION_COMPLETED == GOAL_ITERATION_COMPLETED
    assert digest_mod._SATISFIED == GOAL_SATISFIED
    assert digest_mod._TRIPPED == GOAL_TRIPPED


# --------------------------------------------------------------------------- #
# notifier: goal news alerts by default; heartbeats deliberately do not
# --------------------------------------------------------------------------- #


def test_goal_news_in_default_alerts_and_heartbeats_excluded():
    assert GOAL_SATISFIED in DEFAULT_ALERT_EVENTS
    assert GOAL_TRIPPED in DEFAULT_ALERT_EVENTS
    assert GOAL_ITERATION_REFUSED in DEFAULT_ALERT_EVENTS
    # Routine heartbeats are a log, not news (the approval.resolved precedent)
    # — a daily goal must not buzz the phone twice every morning forever.
    assert GOAL_ITERATION_STARTED not in DEFAULT_ALERT_EVENTS
    assert GOAL_ITERATION_COMPLETED not in DEFAULT_ALERT_EVENTS


def test_format_event_goal_lines_house_voice():
    assert format_event(
        {"type": "goal.satisfied", "payload": {"goal_id": "goal_x", "name": "Inbox zero"}}
    ) == "✅ Goal satisfied: Inbox zero"
    assert format_event(
        {"type": "goal.tripped",
         "payload": {"goal_id": "goal_x", "name": "Inbox zero",
                     "reason": "3 failures in 30 minutes"}}
    ) == "🛑 Goal breaker tripped: Inbox zero — 3 failures in 30 minutes"
    assert format_event(
        {"type": "goal.iteration_refused",
         "payload": {"goal_id": "goal_x", "name": "Inbox zero",
                     "reason": "budget exhausted: max_tokens"}}
    ) == "⏸ Goal run refused: Inbox zero — budget exhausted: max_tokens"


def test_format_event_goal_lines_degrade_without_name_or_reason():
    # The engine's payloads carry `name` since v1.209.0, so this is the
    # DEGRADATION path, not the normal one: a pre-v1.209.0 EventRecord
    # replayed through the formatter (or a defensive hand-fed event) has no
    # name, and the line names the exact goal by id rather than inventing
    # anything.
    assert format_event(
        {"type": "goal.satisfied", "payload": {"goal_id": "goal_abc123"}}
    ) == "✅ Goal satisfied: goal_abc123"
    assert format_event(
        {"type": "goal.tripped", "payload": {"goal_id": "goal_abc123"}}
    ) == "🛑 Goal breaker tripped: goal_abc123"  # no reason → no dangling dash
    assert format_event({"type": "goal.iteration_refused", "payload": {}}) == (
        "⏸ Goal run refused: a goal"
    )


def test_on_event_routes_goal_news_to_channels_by_default():
    mock = MockChannel()
    notifier = Notifier()  # DEFAULT event set — nothing explicit
    notifier.add_channel("mock", mock)

    results = notifier.on_event(
        {"type": "goal.tripped",
         "payload": {"goal_id": "goal_x", "name": "Inbox zero", "reason": "boom"}}
    )
    assert results is not None and results["mock"]["ok"]
    assert len(mock.sent) == 1 and "Inbox zero" in mock.sent[0]

    # A heartbeat is IGNORED — no message, no result.
    assert notifier.on_event(
        {"type": "goal.iteration_completed",
         "payload": {"goal_id": "goal_x", "iteration": 3, "ok": True}}
    ) is None
    assert len(mock.sent) == 1


# --------------------------------------------------------------------------- #
# route: GET /goals/digest (and it is not swallowed by /goals/{goal_id})
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(platform) -> TestClient:
    from iron_jarvis.daemon.routes import goals as goals_routes

    app = FastAPI()
    goals_routes.register(app, SimpleNamespace(platform=platform))
    return TestClient(app)


def test_get_goals_digest_route_answers_the_digest(platform, client):
    now = utcnow()
    goal = _goal(platform)
    sid = _session(platform, goal.id, at=now - timedelta(hours=1))
    _event(platform, GOAL_ITERATION_COMPLETED,
           {"goal_id": goal.id, "iteration": 1, "ok": True},
           session_id=sid, at=now - timedelta(hours=1))

    res = client.get("/goals/digest", params={"hours": 5})

    # A 404 "goal not found" here would mean GET /goals/{goal_id} swallowed
    # the path as goal_id="digest" — the registration-order trap the route's
    # placement comment in routes/goals.py exists to prevent.
    assert res.status_code == 200
    digest = res.json()["digest"]
    assert digest["hours"] == 5
    assert set(digest) == {"since", "hours", "goals"}
    assert [g["id"] for g in digest["goals"]] == [goal.id]
    assert digest["goals"][0]["ran"] == 1
