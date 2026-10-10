"""v1.329.0, calm chat wave 9 (K4): the mission's route note is plain
sentences, and a refusal names the endpoint the way the user named it.

The verification audit after wave 8 found the last mission dash on screen:
``agents/mission.py::_route_note`` said "<who> ran on the offline mock model
— no real model answered." and, with more than one route change, glued
"— N route changes in this mission." onto the first sentence. Both are plain
sentences now.

And since wave 8 (J1) the router's ``provider.downgraded`` refusal carries
``label`` (the name the user gave a fleet endpoint) beside ``requested``
(the raw ``fleet-<id>``, kept for the logs). The note prefers ``label`` when
it is there, so the mission screen never shows ``fleet-7f3a…`` where the
banner and the chat say "Lab box". An older event without ``label`` still
names ``requested``.

These drive the real ``mission_view`` over a real platform store (the same
seeding the v1.309.0 mission view tests use), plus ``_route_note`` directly
for the mock and the many-changes wording.
"""

from __future__ import annotations

import json
import re

from iron_jarvis.agents.mission import _route_note, mission_view
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    EventRecord,
    Session,
    SessionStatus,
)
from iron_jarvis.platform import build_platform

MISSION = "job:mission"
#: A dash used as an aside: an em or en dash with a space on either side.
ASIDE = re.compile("(^|\\s)[—–](\\s|$)")


def _seed_root(engine) -> str:
    with session_scope(engine) as db:
        root = Session(
            task="write the quarterly report",
            agent_type=AgentType.SUPERVISOR,
            status=SessionStatus.COMPLETED,
            origin=MISSION,
            provider="fleet-7f3a9c",
            model="glm-5.3-flash",
            options_json=json.dumps({"deliverable": True}),
        )
        db.add(root)
        db.commit()
        root_id = root.id
        db.add(AgentRun(
            session_id=root_id, agent_type=AgentType.SUPERVISOR,
            provider="fleet-7f3a9c", model="glm-5.3-flash", state=AgentState.COMPLETED,
        ))
        db.commit()
    return root_id


def _event(engine, etype, session_id: str, payload: dict) -> None:
    with session_scope(engine) as db:
        db.add(EventRecord(
            id=new_id("evt"), type=str(getattr(etype, "value", etype)), session_id=session_id,
            payload_json=json.dumps(payload), created_at=utcnow(),
        ))
        db.commit()


def _note(engine, root: str, platform) -> str:
    note = mission_view(platform, root)["session"].get("route_note")
    assert isinstance(note, str), note
    return note


def test_a_refusal_names_the_endpoint_by_its_label(tmp_path):
    """``used: "none"`` with a ``label``: the label is named, the raw id is
    not, and the router's own reason rides in parentheses."""
    p = build_platform(str(tmp_path / "home"))
    root = _seed_root(p.engine)
    _event(p.engine, EventType.PROVIDER_DOWNGRADED, root, {
        "requested": "fleet-7f3a9c", "used": "none", "reason": "not connected", "label": "Lab box",
    })
    note = _note(p.engine, root, p)
    assert note == "Jarvis could not use Lab box (not connected).", note
    assert "fleet-7f3a9c" not in note


def test_an_older_refusal_without_a_label_still_names_what_was_requested(tmp_path):
    """Control: an event written before ``label`` existed keeps naming the
    requested provider (the v1.309.0 contract)."""
    p = build_platform(str(tmp_path / "home"))
    root = _seed_root(p.engine)
    _event(p.engine, EventType.PROVIDER_DOWNGRADED, root, {
        "requested": "local-fleet", "used": "none", "reason": "unreachable",
    })
    assert _note(p.engine, root, p) == "Jarvis could not use local-fleet (unreachable)."


def test_a_blank_label_falls_back_to_requested():
    """A label that is empty (an endpoint the user never named) is not a
    name: the requested provider is said instead, never an empty gap."""
    note = _route_note(
        [(EventType.PROVIDER_DOWNGRADED, "s1", json.dumps({
            "requested": "fleet-abc", "used": "none", "reason": "", "label": "",
        }))],
        {"s1": "Researcher"},
    )
    assert note == "Researcher could not use fleet-abc."


def test_the_mock_note_is_two_plain_sentences():
    note = _route_note(
        [(EventType.PROVIDER_DOWNGRADED, "s1", json.dumps({
            "requested": "mock (default)", "used": "mock", "reason": "no model connected",
        }))],
        {"s1": "Researcher"},
    )
    assert note == "Researcher ran on the mock model. No real model answered."
    assert not ASIDE.search(note), note


def test_many_route_changes_are_counted_in_their_own_sentence():
    """The newest change is named, the count follows as its own sentence,
    and nothing is glued on with a dash."""
    events = [
        (EventType.PROVIDER_FAILOVER, "s1", json.dumps({
            "from": "claude-cli", "to": "codex-cli", "reason": "http 500",
        })),
        (EventType.PROVIDER_DOWNGRADED, "s2", json.dumps({
            "requested": "fleet-abc", "used": "none", "reason": "unreachable", "label": "Lab box",
        })),
    ]
    note = _route_note(events, {"s1": "Researcher", "s2": "Builder"})
    assert note == (
        "Researcher's model claude-cli failed (http 500); codex-cli answered instead."
        " There were 2 route changes in this mission."
    ), note
    assert not ASIDE.search(note), note


def test_no_route_note_sentence_carries_a_dash_aside():
    """Every shape the note can take, read together: no spaced em or en dash."""
    shapes = [
        {"requested": "mock (default)", "used": "mock", "reason": "x"},
        {"requested": "fleet-abc", "used": "none", "reason": "timeout", "label": "Lab box"},
        {"requested": "local-fleet", "used": "none", "reason": ""},
    ]
    for payload in shapes:
        note = _route_note(
            [(EventType.PROVIDER_DOWNGRADED, "s1", json.dumps(payload))] * 2, {"s1": "Jarvis"}
        )
        assert note and not ASIDE.search(note), (payload, note)
