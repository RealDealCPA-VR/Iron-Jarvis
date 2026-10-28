"""v1.403.0 — THE CODING-SESSION TEAM: its routes and its data.

* ``POST /team/members/from-model`` makes — or REUSES — the model-team member
  on a model, for a session's role (a worker gains code, test and read; a
  reviewer gains review), never clobbering what a reused member has (its
  prompt, account, allowance), and refuses the demo model, an unknown or
  unreachable model, and a cloud model in a Local only session — 409 in one
  sentence.
* ``setup.team`` is validated at PATCH / POST (a 400 in one sentence for an
  unknown member, more than two workers, a worker without Writing code, a
  reviewer without Reviewing, the same member as both), partial like every
  PATCH field, whitelisted by ``_clean_setup``, copied by Branch from here,
  kept by Rewind.
* The folder's default team (``BuildFolderRecord.team_json``, additive — the
  reconciler adds the column to an older database) starts a new session in
  that folder unless the body says otherwise; ``GET /build/folders/team``
  answers 200 for every folder, an empty one too.
* The session view carries ``team`` ONLY when a team is set, its members'
  states from cached reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness import team as T

CATALOG = [
    {"provider": "ollama", "model": "qwen3:8b", "available": True, "on_this_network": True, "name": "Qwen3 8B"},
    {"provider": "codex-cli", "model": "gpt-6-astra", "available": True, "on_this_network": False},
    {"provider": "fleet-x", "model": "down-model", "available": False, "on_this_network": True},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import connections

    monkeypatch.setattr(connections, "selectable_models", lambda d: [dict(r) for r in CATALOG])
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def registry(client):
    return client.app.state.platform.agents_registry


def team_members(client) -> None:
    reg = registry(client)
    reg.register("qwen", "You are qwen.", [], provider="ollama", model="qwen3:8b", roles=["code", "test", "read"])
    reg.register("glm", "You are glm.", [], provider="ollama", model="glm", roles=["code"])
    reg.register("rev", "You are rev.", [], provider="codex-cli", model="gpt-6-astra", roles=["review"])
    reg.register("reader", "You read.", [], provider="ollama", model="reader", roles=["read"])


def new_session(client, folder: Path, **extra) -> dict:
    r = client.post("/build/sessions", json={"folder": str(folder), **extra})
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# POST /team/members/from-model
# --------------------------------------------------------------------------- #


def test_from_model_makes_a_worker_then_reuses_it_as_the_reviewer(client):
    r = client.post("/team/members/from-model", json={"provider": "ollama", "model": "qwen3:8b", "role": "worker"})
    assert r.status_code == 200, r.text
    made = r.json()
    assert made == {"name": "qwen3-8b", "provider": "ollama", "model": "qwen3:8b", "label": "Qwen3 8B",
                    "roles": ["read", "code", "test"], "created": True}
    again = client.post("/team/members/from-model",
                        json={"provider": "ollama", "model": "qwen3:8b", "role": "reviewer"}).json()
    assert again["created"] is False and again["name"] == "qwen3-8b"
    assert again["roles"] == ["read", "code", "review", "test"]
    # Asking for the same role again adds nothing and makes nothing.
    third = client.post("/team/members/from-model",
                        json={"provider": "ollama", "model": "qwen3:8b", "role": "worker"}).json()
    assert third["created"] is False and third["roles"] == again["roles"]
    assert [m.name for m in registry(client).list()] == ["qwen3-8b"]


def test_a_reused_member_keeps_everything_else_it_has(client):
    reg = registry(client)
    reg.register("mine", "My own careful prompt.", ["read_file"], base_type="builder",
                 description="mine, set up by hand", provider="codex-cli", model="gpt-6-astra",
                 roles=["code"], account="acct-1", allowance_usd=5.0, max_steps=33)
    out = client.post("/team/members/from-model",
                      json={"provider": "codex-cli", "model": "gpt-6-astra", "role": "reviewer"}).json()
    assert out["name"] == "mine" and out["created"] is False and out["roles"] == ["code", "review"]
    rec = reg.get("mine")
    assert rec.system_prompt == "My own careful prompt." and json.loads(rec.tools_json) == ["read_file"]
    assert rec.description == "mine, set up by hand" and rec.account == "acct-1"
    assert rec.allowance_usd == 5.0 and rec.max_steps == 33


def test_a_name_taken_by_another_agent_is_never_reused_for_another_model(client):
    registry(client).register("qwen3-8b", "taken", [], provider="ollama", model="other-model", roles=["read"])
    out = client.post("/team/members/from-model",
                      json={"provider": "ollama", "model": "qwen3:8b", "role": "worker"}).json()
    assert out["created"] is True and out["name"] == "qwen3-8b-2"


@pytest.mark.parametrize("payload,status,words", [
    ({"provider": "mock", "model": "mock", "role": "worker"}, 409, "The demo model cannot be on a team"),
    ({"provider": "ollama", "model": "nope", "role": "worker"}, 409, "is not one of the models"),
    ({"provider": "fleet-x", "model": "down-model", "role": "worker"}, 409, "is not connected right now"),
    ({"provider": "codex-cli", "model": "gpt-6-astra", "role": "worker", "local_only": True}, 409,
     "this session is Local only"),
    ({"provider": "ollama", "model": "qwen3:8b", "role": "boss"}, 400, "a worker or as the reviewer"),
])
def test_from_model_refusals_are_one_sentence(client, payload, status, words):
    r = client.post("/team/members/from-model", json=payload)
    assert r.status_code == status and words in r.json()["detail"], r.text
    assert registry(client).list() == []


def test_a_local_only_session_named_by_thread_refuses_a_cloud_model(client, work):
    sid = new_session(client, work)["id"]
    assert client.patch(f"/build/sessions/{sid}", json={"local_only": True}).status_code == 200
    r = client.post("/team/members/from-model",
                    json={"provider": "codex-cli", "model": "gpt-6-astra", "role": "reviewer", "thread_id": sid})
    assert r.status_code == 409 and "Local only" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# the session's team: PATCH / POST / view
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("team,words", [
    ({"workers": ["ghost"]}, "There is no member of your model team named ghost."),
    ({"workers": ["qwen", "glm", "reader"]}, "at most 2 workers"),
    ({"workers": ["reader"]}, "reader cannot be a worker: it does not hold the Writing code role."),
    ({"workers": ["qwen"], "reviewer": "glm"}, "glm cannot be the reviewer"),
    ({"workers": ["qwen"], "reviewer": "qwen"}, "qwen cannot be both a worker and the reviewer."),
    ({"workers": ["qwen", "qwen"]}, "qwen is listed twice."),
    ({"workers": "qwen"}, "A team's workers are a list of member names."),
])
def test_patch_refuses_a_team_in_one_sentence(client, work, team, words):
    team_members(client)
    sid = new_session(client, work)["id"]
    r = client.patch(f"/build/sessions/{sid}", json={"team": team})
    assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert "team" not in client.get(f"/build/sessions/{sid}").json()


def test_patch_sets_keeps_and_clears_a_team_and_the_view_says_it(client, work):
    team_members(client)
    sid = new_session(client, work)["id"]
    solo = client.get(f"/build/sessions/{sid}").json()
    assert "team" not in solo and "team" not in solo["setup"]
    r = client.patch(f"/build/sessions/{sid}", json={"team": {"workers": ["qwen", "glm"], "reviewer": "rev"}})
    assert r.status_code == 200
    view = r.json()
    assert view["setup"]["team"] == {"workers": ["qwen", "glm"], "reviewer": "rev"}
    team = view["team"]
    assert set(team) == {"lead", "workers", "reviewer"}
    assert set(team["lead"]) == {"provider", "model", "label"}
    assert [w["name"] for w in team["workers"]] == ["qwen", "glm"]
    assert team["workers"][0]["provider"] == "ollama" and team["workers"][0]["model"] == "qwen3:8b"
    assert team["reviewer"]["name"] == "rev" and team["reviewer"]["model"] == "gpt-6-astra"
    for m in [*team["workers"], team["reviewer"]]:
        assert set(m) == {"name", "provider", "model", "label", "state"}
        assert m["state"] in ("ready", "busy", "limit", "unavailable", "unknown")
    # PARTIAL: another field leaves the team alone.
    view = client.patch(f"/build/sessions/{sid}", json={"title": "renamed"}).json()
    assert view["setup"]["team"]["workers"] == ["qwen", "glm"]
    # No workers, or null: solo.
    view = client.patch(f"/build/sessions/{sid}", json={"team": {"workers": [], "reviewer": "rev"}}).json()
    assert "team" not in view and "team" not in view["setup"]
    client.patch(f"/build/sessions/{sid}", json={"team": {"workers": ["qwen"]}})
    view = client.patch(f"/build/sessions/{sid}", json={"team": None}).json()
    assert "team" not in view


def test_a_paused_member_reads_unavailable(client, work):
    team_members(client)
    registry(client).set_paused("glm", "a day off")
    sid = new_session(client, work, team={"workers": ["qwen", "glm"]})["id"]
    view = client.get(f"/build/sessions/{sid}").json()
    states = {m["name"]: m["state"] for m in view["team"]["workers"]}
    assert states["glm"] == "unavailable"
    assert view["team"]["reviewer"] is None


def test_a_folders_default_team_starts_a_new_session_unless_the_body_says_otherwise(client, work):
    team_members(client)
    empty = client.get("/build/folders/team", params={"folder": ""})
    assert empty.status_code == 200 and empty.json() == {"folder": "", "team": None}
    assert client.get("/build/folders/team", params={"folder": str(work)}).json()["team"] is None
    r = client.put("/build/folders/team", json={"folder": str(work), "team": {"workers": ["ghost"]}})
    assert r.status_code == 400
    r = client.put("/build/folders/team", json={"folder": str(work), "team": {"workers": ["qwen"], "reviewer": "rev"}})
    assert r.status_code == 200 and r.json()["team"] == {"workers": ["qwen"], "reviewer": "rev"}
    assert client.get("/build/folders/team", params={"folder": str(work)}).json()["team"] == {
        "workers": ["qwen"], "reviewer": "rev"}
    # Omitted: the folder's team.
    assert new_session(client, work)["setup"]["team"] == {"workers": ["qwen"], "reviewer": "rev"}
    # Said: the body's (null = solo).
    assert "team" not in new_session(client, work, team=None)
    assert new_session(client, work, team={"workers": ["glm"]})["setup"]["team"] == {"workers": ["glm"],
                                                                                    "reviewer": ""}
    bad = client.post("/build/sessions", json={"folder": str(work), "team": {"workers": ["reader"]}})
    assert bad.status_code == 400 and "Writing code" in bad.json()["detail"]
    # The folder's display name is untouched by its team; a rename keeps the team.
    assert client.post("/build/folders/rename", json={"path": str(work), "name": "My work"}).status_code == 200
    assert client.get("/build/folders/team", params={"folder": str(work)}).json()["team"]["workers"] == ["qwen"]
    # Cleared.
    assert client.put("/build/folders/team", json={"folder": str(work), "team": None}).json()["team"] is None
    assert "team" not in new_session(client, work)


def test_branch_from_here_copies_the_team_and_rewind_keeps_it(client, work):
    team_members(client)
    engine = client.app.state.platform.engine
    sid = new_session(client, work, team={"workers": ["qwen"], "reviewer": "rev"})["id"]
    S.append_lines(engine, sid, [
        {"role": "user", "content": "do it", "turnId": "t1"},
        {"role": "assistant", "content": "done", "turnId": "t1"},
        {"role": "user", "content": "again", "turnId": "t2"},
        {"role": "assistant", "content": "done again", "turnId": "t2"},
    ])
    r = client.post(f"/build/sessions/{sid}/branch", json={"turn_id": "t1", "at": "reply"})
    assert r.status_code == 201, r.text
    branched = r.json()
    new_id = branched.get("id") or branched.get("session", {}).get("id")
    view = client.get(f"/build/sessions/{new_id}").json()
    assert view["setup"]["team"] == {"workers": ["qwen"], "reviewer": "rev"} and view["team"]
    out = S.cut_transcript(engine, sid, "t2", 2, None)
    assert out["ok"], out
    assert client.get(f"/build/sessions/{sid}").json()["setup"]["team"]["workers"] == ["qwen"]


def test_clean_setup_whitelists_the_team_by_its_own_rule():
    from iron_jarvis.daemon.routes.chat import _clean_setup

    raw = {"team": {"workers": [" qwen ", 7, "glm", "x", "qwen"], "reviewer": "glm", "extra": 1}}
    assert json.loads(_clean_setup(raw)) == {"team": {"workers": ["qwen", "glm"], "reviewer": ""}}
    assert _clean_setup({"team": {"workers": []}}) == ""
    assert _clean_setup({"team": "qwen"}) == ""


def test_the_reconciler_adds_team_json_to_an_older_database(tmp_path):
    from sqlalchemy import text

    from iron_jarvis.core.db import init_db, make_engine, session_scope

    engine = make_engine(tmp_path / "old.db")
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE buildfolderrecord (key VARCHAR NOT NULL PRIMARY KEY, path VARCHAR, '
            'name VARCHAR, updated_at DATETIME)'))
        conn.execute(text("INSERT INTO buildfolderrecord (key, path, name) VALUES ('c:/w', 'C:/w', 'W')"))
    init_db(engine)
    with session_scope(engine) as db:
        cols = [row[1] for row in db.exec(text("PRAGMA table_info(buildfolderrecord)")).all()]
    assert "team_json" in cols
    assert T.folder_team(engine, "C:/w") is None
    T.set_folder_team(engine, "C:/w", {"workers": ["a"], "reviewer": ""})
    assert T.folder_team(engine, "C:/w") == {"workers": ["a"], "reviewer": ""}
