"""A project can be bound to specific memory bases (v1.110.0).

REQUESTED: "maybe connect a specific memory base to a specific project if
desired."

"If desired" is the load-bearing part: binding nothing must keep searching
everything, because that is what every existing project does today and a
silent narrowing would look like memory loss.

The binding is only worth anything if it reaches GROUNDING — a setting the
recall path ignores is decoration. These tests drive MemoryFabric directly for
that reason, not just the route that stores the value.
"""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.fabric import MemoryFabric


class FakeLTM:
    """Two bases with distinct content, so a leak is visible in the result."""

    def __init__(self):
        self.calls: list[str | None] = []
        self.docs = {
            "clientA": [{"title": "Alvarez engagement", "snippet": "alvarez basis study", "ref": "a1"}],
            "clientB": [{"title": "Nguyen engagement", "snippet": "nguyen basis study", "ref": "b1"}],
        }

    def sources(self):
        return list(self.docs)

    def search(self, query, k=5, source=None):
        self.calls.append(source)
        if source is None:
            return [h for hits in self.docs.values() for h in hits]
        if source not in self.docs:
            raise ValueError(f"unknown LTM source '{source}'")
        return self.docs[source]


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


@pytest.fixture
def project(client) -> str:
    return client.post("/projects", json={"name": "Alvarez 2025"}).json()["id"]


def _fabric(client, ltm) -> MemoryFabric:
    return MemoryFabric(ltm=ltm, engine=client.app.state.platform.engine)


def _bind(client, project, names):
    return client.patch(f"/projects/{project}", json={"memory_sources": names})


# --- storing the binding -----------------------------------------------------


def test_a_project_starts_bound_to_nothing(client, project):
    assert client.get(f"/projects/{project}").json()["project"]["memory_sources"] == ""


def test_binding_is_rejected_for_a_base_that_does_not_exist(client, project):
    """A typo would otherwise bind the project to nothing and present as a
    project that mysteriously recalls nothing."""
    r = _bind(client, project, ["clietnA"])
    assert r.status_code == 400
    assert "unknown memory base" in r.json()["detail"]


def test_the_error_says_what_IS_available(client, project):
    assert "available" in _bind(client, project, ["nope"]).json()["detail"]


# --- the part that matters: grounding ----------------------------------------


def test_unbound_projects_still_search_every_base(client, project):
    """The "if desired" half. Existing projects bind nothing, and must behave
    exactly as before."""
    ltm = FakeLTM()
    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=project)
    refs = {h.ref for h in hits}
    assert refs == {"a1", "b1"}
    assert ltm.calls == [None]  # one merged search, not a per-base sweep


def test_binding_narrows_recall_to_the_chosen_base(client, project, monkeypatch):
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    assert _bind(client, project, ["clientA"]).status_code == 200

    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=project)
    refs = {h.ref for h in hits}
    assert refs == {"a1"}
    assert "b1" not in refs  # the other client's notes never surface
    assert ltm.calls == ["clientA"]


def test_several_bases_can_be_bound(client, project, monkeypatch):
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    _bind(client, project, ["clientA", "clientB"])
    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=project)
    assert {h.ref for h in hits} == {"a1", "b1"}


def test_clearing_the_binding_restores_every_base(client, project, monkeypatch):
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    _bind(client, project, ["clientA"])
    assert _bind(client, project, []).status_code == 200
    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=project)
    assert {h.ref for h in hits} == {"a1", "b1"}


def test_a_deleted_base_does_not_break_grounding(client, project, monkeypatch):
    """Deleting a source must not silently kill recall for every project that
    named it — LTMManager.search RAISES on an unknown name, so an unguarded
    loop would take the whole notes branch down with it."""
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    _bind(client, project, ["clientA", "clientB"])
    del ltm.docs["clientA"]  # the base goes away after the binding was made

    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=project)
    assert {h.ref for h in hits} == {"b1"}  # the surviving base still answers


def test_grounding_text_reflects_the_binding(client, project, monkeypatch):
    """ground() is what actually reaches the model."""
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    _bind(client, project, ["clientA"])
    text = _fabric(client, ltm).ground("basis study", project_id=project)
    assert "Alvarez" in text
    assert "Nguyen" not in text


def test_a_chat_outside_any_project_is_unaffected(client, monkeypatch):
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=None)
    assert {h.ref for h in hits} == {"a1", "b1"}


def test_corrupt_stored_json_falls_back_to_every_base(client, project, monkeypatch):
    """Degrade toward recalling too much, never toward silence: a project that
    recalls nothing reads as broken far more than one that recalls broadly."""
    from sqlmodel import Session as _S

    from iron_jarvis.core.models import Project

    engine = client.app.state.platform.engine
    with _S(engine) as db:
        p = db.get(Project, project)
        p.memory_sources = "{not json"
        db.add(p)
        db.commit()

    ltm = FakeLTM()
    hits = _fabric(client, ltm).recall("basis study", sources=["notes"], project_id=project)
    assert {h.ref for h in hits} == {"a1", "b1"}


def test_the_binding_round_trips_as_json(client, project, monkeypatch):
    ltm = FakeLTM()
    monkeypatch.setattr(client.app.state.platform, "ltm", ltm)
    _bind(client, project, ["clientA"])
    stored = client.get(f"/projects/{project}").json()["project"]["memory_sources"]
    assert json.loads(stored) == ["clientA"]
