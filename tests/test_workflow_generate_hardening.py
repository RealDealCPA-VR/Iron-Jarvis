"""Workflow GENERATE + terminal-button hardening (T-C).

Covers the string-aware JSON extraction helper, the mock-default honesty
hint (connect a model, not "try rephrasing"), and collision-safe generated
names. Offline via the mock default; extraction + naming are unit-tested on
the factored module-level helpers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import (
    _extract_workflow_json,
    _unique_workflow_name,
    create_app,
)
from iron_jarvis.workflows.store import WorkflowStore


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


# --- _extract_workflow_json (string-aware) -----------------------------------


def test_extract_fenced_json_block():
    text = 'Here you go:\n```json\n{"name": "x", "steps": [{"task": "a"}]}\n```\nEnjoy!'
    wf = _extract_workflow_json(text)
    assert wf["name"] == "x"
    assert wf["steps"][0]["task"] == "a"


def test_extract_bare_object_ignores_braces_inside_strings():
    # Braces/quotes inside step text must not corrupt the balance counter.
    text = 'sure -> {"name": "y", "steps": [{"task": "use {curly} braces here"}]} done'
    wf = _extract_workflow_json(text)
    assert wf["name"] == "y"
    assert wf["steps"][0]["task"] == "use {curly} braces here"


def test_extract_truncated_reply_raises():
    # A reply whose object never closes cannot be parsed — honest failure.
    with pytest.raises(ValueError):
        _extract_workflow_json('{"name": "z", "steps": [{"task": "do a thin')


# --- mock-default honesty ----------------------------------------------------


def test_generate_on_mock_default_returns_connect_hint(tmp_path):
    client = _client(tmp_path)
    r = client.post("/workflows/generate", json={"description": "back up my notes"})
    assert r.status_code == 400
    detail = r.json()["detail"].lower()
    assert "connect a model" in detail
    # NOT the misleading "try rephrasing" path.
    assert "rephrasing" not in detail


# --- collision-safe generated names ------------------------------------------


def test_unique_name_suffixes_on_collision(tmp_path):
    client = _client(tmp_path)
    engine = client.app.state.platform.engine
    store = WorkflowStore(engine)
    store.save("x", [{"name": "s", "agent": "builder", "task": "t"}])

    # Generated (non-explicit) name that collides gets suffixed, never clobbers.
    assert _unique_workflow_name(store, "x", explicit=False) == "x-2"
    # Refinement (explicit) keeps the same name so save() upserts the row.
    assert _unique_workflow_name(store, "x", explicit=True) == "x"

    # A second collision walks past -2.
    store.save("x-2", [{"name": "s", "agent": "builder", "task": "t"}])
    assert _unique_workflow_name(store, "x", explicit=False) == "x-3"

    # The fallback name is normalized + still collision-safe.
    assert _unique_workflow_name(store, "Generated Workflow!", explicit=False) == "generated-workflow"
