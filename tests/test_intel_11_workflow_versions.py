"""The intelligence plan, release 11: WORKFLOW VERSIONS (new, small) so saved
steps can be put back, and a workflow's own FOLDER. Through the served
routes of the real app factory.

Pinned, each with the way it would fail:

* a save that changes the steps keeps what they were as an earlier version;
  a save that changes nothing keeps none;
* putting a version back restores its steps, description and folder, and
  keeps what it replaced as a version in turn (a restore can be put back);
* a version is put back only through the one save check every door applies
  (``workflows/checks.step_problem``): steps that could not run are refused
  in its words, and nothing changes;
* a version of another workflow is never put back here; a removed
  workflow's versions go with it;
* at most ``VERSIONS_MAX`` are kept, the oldest dropped first;
* a re-save that says nothing of the folder keeps it (the Workflows page
  knows no folder), and a run of a workflow with a folder works in it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon.app import create_app
from iron_jarvis.workflows.engine import WorkflowEngine
from iron_jarvis.workflows.models import VERSIONS_MAX, WorkflowVersionRecord
from iron_jarvis.workflows.store import WorkflowStore

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]


def _steps(text: str) -> list[dict]:
    return [{"name": "Write", "kind": "tool", "tool": "write_file", "args": {"path": "out.txt", "content": text}}]


@pytest.fixture
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "work"
    folder.mkdir()
    return SimpleNamespace(app=app, platform=app.state.platform, engine=app.state.platform.engine,
                           client=TestClient(app), folder=folder)


def _save(desk, steps, description="weekly") -> None:
    res = desk.client.post("/workflows", json={"name": "weekly", "steps": steps, "description": description})
    assert res.status_code == 200, res.text


def _versions(desk) -> list[dict]:
    res = desk.client.get("/workflows/weekly/versions")
    assert res.status_code == 200, res.text
    return res.json()["versions"]


def test_a_change_keeps_what_it_was_and_no_change_keeps_nothing(desk):
    _save(desk, _steps("one"))
    assert _versions(desk) == []
    _save(desk, _steps("one"))  # the same: nothing kept
    assert _versions(desk) == []
    _save(desk, _steps("two"))
    (v,) = _versions(desk)
    assert v["steps"] == 1 and v["description"] == "weekly"


def test_putting_a_version_back_restores_it_and_keeps_what_it_replaced(desk):
    _save(desk, _steps("one"))
    _save(desk, _steps("two"))
    (v,) = _versions(desk)
    res = desk.client.post(f"/workflows/weekly/versions/{v['id']}/restore")
    assert res.status_code == 200, res.text
    assert json.loads(res.json()["steps_json"])[0]["args"]["content"] == "one"
    newest = _versions(desk)[0]
    back = desk.client.post(f"/workflows/weekly/versions/{newest['id']}/restore")
    assert back.status_code == 200
    assert json.loads(back.json()["steps_json"])[0]["args"]["content"] == "two"  # the restore was put back


def test_a_version_whose_steps_could_not_run_is_refused_in_the_save_check_s_words(desk):
    _save(desk, _steps("one"))
    rec = WorkflowStore(desk.engine).get("weekly")
    with session_scope(desk.engine) as db:
        db.add(WorkflowVersionRecord(workflow_id=rec.id, steps_json=json.dumps(
            [{"name": "Odd", "kind": "tools", "tool": "write_file"}])))
        db.commit()
    bad = [v for v in _versions(desk) if v["steps"] == 1][0]
    res = desk.client.post(f"/workflows/weekly/versions/{bad['id']}/restore")
    assert res.status_code == 422 and "steps[0]" in res.json()["detail"]
    assert json.loads(WorkflowStore(desk.engine).get("weekly").steps_json)[0]["args"]["content"] == "one"


def test_another_workflow_s_version_is_never_put_back_here(desk):
    _save(desk, _steps("one"))
    _save(desk, _steps("two"))
    desk.client.post("/workflows", json={"name": "other", "steps": _steps("x")})
    desk.client.post("/workflows", json={"name": "other", "steps": _steps("y")})
    other = desk.client.get("/workflows/other/versions").json()["versions"][0]
    assert desk.client.post(f"/workflows/weekly/versions/{other['id']}/restore").status_code == 404


def test_a_removed_workflow_s_versions_go_with_it(desk):
    _save(desk, _steps("one"))
    _save(desk, _steps("two"))
    assert desk.client.delete("/workflows/weekly").status_code == 200
    with session_scope(desk.engine) as db:
        assert list(db.exec(select(WorkflowVersionRecord))) == []
    assert desk.client.get("/workflows/weekly/versions").status_code == 404


def test_at_most_versions_max_are_kept(desk):
    for n in range(VERSIONS_MAX + 3):
        _save(desk, _steps(f"v{n}"))
    got = _versions(desk)
    assert len(got) == VERSIONS_MAX


def test_a_resave_that_says_nothing_of_the_folder_keeps_it_and_a_run_works_there(desk):
    store = WorkflowStore(desk.engine)
    store.save("weekly", _steps("one"), "weekly", folder=str(desk.folder))
    _save(desk, _steps("two"))  # the page knows no folder
    rec = store.get("weekly")
    assert rec.folder == str(desk.folder)
    wf = store.load_def("weekly")
    assert wf.folder == str(desk.folder)
    run = asyncio.run(WorkflowEngine(desk.platform, None).run(wf))
    assert run.status == "completed", run.outputs_json
    assert (desk.folder / "out.txt").read_text(encoding="utf-8") == "two"


def test_a_folder_that_is_gone_is_said_on_the_run(desk, tmp_path):
    store = WorkflowStore(desk.engine)
    store.save("weekly", _steps("one"), "weekly", folder=str(tmp_path / "gone"))
    run = asyncio.run(WorkflowEngine(desk.platform, None).run(store.load_def("weekly")))
    assert "not there any more" in run.notes_json
