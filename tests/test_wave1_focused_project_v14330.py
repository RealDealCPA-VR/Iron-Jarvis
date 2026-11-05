"""v1.433.0 (Wave 1, A3): chat cannot focus a project that is not there.

"Focused project" (``active_project_id``) is an allow-tier setting, and the
writer took any text: chat's ``config_set`` could focus an id that names no
project, or an archived one — and every new chat and run is then tagged into
a project that is not there (daemon/app's boot check warns about exactly
that, and clears one at the next start). ``POST /projects/{id}/activate``
always refused both, in words; every write path through the settings writer
now refuses them the same way. "" (no focused project) is always allowed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings.writer import SettingError
from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done


def _project(app, name: str, *, status: str = "active") -> str:
    with session_scope(app.state.platform.engine) as db:
        row = Project(name=name, status=status)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _finished(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]


async def _never(data):  # pragma: no cover
    raise AssertionError(f"an allow-tier setting asked: {data}")


@pytest.mark.asyncio
async def test_chat_refuses_an_unknown_or_archived_project_and_takes_a_real_one_and_none(tmp_path):
    from tests.test_chat_approvals_v1187 import _drive_stream

    app = create_app(str(tmp_path))
    p = app.state.platform
    real = _project(app, "Smith return")
    old = _project(app, "Closed 2025 audit", status="archived")
    p.config.active_project_id = None
    p.router.stream = _calls_stream(
        [
            ("config_set", {"key": "active_project_id", "value": "project_doesnotexist"}),
            ("config_set", {"key": "active_project_id", "value": old}),
            ("config_set", {"key": "active_project_id", "value": real}),
            ("config_set", {"key": "active_project_id", "value": ""}),
        ]
    )
    frames = await _drive_stream(app, _body("set my focused project setting"), _never)
    unknown, archived, ok, cleared = _finished(frames)
    assert unknown["ok"] is False and "Focused project: no such project." in unknown["output"]
    assert archived["ok"] is False and "Focused project: unarchive the project first." in archived["output"]
    assert ok["ok"] is True, ok
    assert cleared["ok"] is True, cleared
    assert not p.config.active_project_id, "the last call cleared it"
    # Only the real change and the clear made a card.
    cards = _done(frames)["config_cards"]
    assert [(c["old"], c["new"]) for c in cards] == [(None, real), (real, "")]


def test_every_writer_door_refuses_a_dangling_id(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    p = app.state.platform
    real = _project(app, "Jones LLC")
    w = p.config_writer
    with pytest.raises(SettingError, match="no such project"):
        w.apply({"active_project_id": "project_ghost"}, actor="test")
    # The Settings page's two doors go through the same writer.
    r = client.put("/settings", json={"values": {"active_project_id": "project_ghost"}})
    assert r.status_code == 400 and "no such project" in r.json()["detail"]
    r = client.put("/settings/values", json={"values": {"active_project_id": "project_ghost"}})
    assert r.status_code == 400 and "no such project" in r.json()["detail"]
    assert p.config.active_project_id != "project_ghost"
    w.apply({"active_project_id": real}, actor="test")
    assert p.config.active_project_id == real


def test_saving_the_value_already_set_never_fails_on_a_project_archived_since(tmp_path):
    """A full Settings save sends every field back, the focused project too:
    one archived behind the writer's back (a database edit, a restore) must
    not make the whole save fail — nothing about it changes."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    pid = _project(app, "Later archived")
    p.config_writer.apply({"active_project_id": pid}, actor="test")
    with session_scope(p.engine) as db:
        row = db.get(Project, pid)
        row.status = "archived"
        db.add(row)
        db.commit()
    p.config_writer.apply({"active_project_id": pid, "max_agent_steps": 20}, actor="test")
    assert p.config.max_agent_steps == 20


def test_an_undo_back_to_a_project_archived_since_is_skipped(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    p = app.state.platform
    first = _project(app, "First")
    second = _project(app, "Second")
    p.config.active_project_id = first
    action = p.config_writer.apply({"active_project_id": second}, actor="test").action_id
    with session_scope(p.engine) as db:
        row = db.get(Project, first)
        row.status = "archived"
        db.add(row)
        db.commit()
    assert client.post(f"/undo/{action}").status_code == 200
    assert p.config.active_project_id == second, "the undo never puts back a project that is archived now"
