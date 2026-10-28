"""v1.403.0 integrate — the coding-session team's SEAM: the real daemon's
answers, fed through the screen's decoders.

The screen (Builder E) was built against stand-ins of the contract; the daemon
(Builder D) built the routes. This file asks the REAL routes and writes what
they answer to ``dashboard/__tests__/fixtures/session-team-seams-v1403.json``;
``dashboard/__tests__/session-team-seams-v1403.test.ts`` decodes that file with
``lib/sessionTeam`` / ``lib/buildSessions`` and asserts nothing is dropped. A
shape change on either side fails one of the two. Regenerate with
``IJ_REGEN_TEAM_SEAMS=1`` after an intended change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import team as T

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "session-team-seams-v1403.json"
REGEN = os.environ.get("IJ_REGEN_TEAM_SEAMS") == "1"
CATALOG = [
    {"provider": "ollama", "model": "qwen3:8b", "available": True, "on_this_network": True, "name": "Qwen3 8B"},
    {"provider": "codex-cli", "model": "gpt-6-astra", "available": True, "on_this_network": False},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import connections

    monkeypatch.setattr(connections, "selectable_models", lambda d: [dict(r) for r in CATALOG])
    return TestClient(create_app(str(tmp_path / "home")))


def _stable(value):
    """Drop what changes run to run (ids, times, paths) so the fixture is stable."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()
                if k not in ("id", "created_at", "updated_at", "at", "folder", "path", "workspace_dir",
                             "thread_id", "title")}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def test_the_real_answers_are_what_the_screen_decodes(client, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    worker = client.post("/team/members/from-model",
                         json={"provider": "ollama", "model": "qwen3:8b", "role": "worker"})
    assert worker.status_code == 200, worker.text
    reviewer = client.post("/team/members/from-model",
                           json={"provider": "codex-cli", "model": "gpt-6-astra", "role": "reviewer"})
    assert reviewer.status_code == 200, reviewer.text
    w, r = worker.json(), reviewer.json()
    assert w["name"] != r["name"] and "code" in w["roles"] and "review" in r["roles"]

    created = client.post("/build/sessions", json={"folder": str(work)})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    empty_folder = client.get("/build/folders/team", params={"folder": ""})
    assert empty_folder.status_code == 200, "an empty folder must answer 200, or the screen hides the Team control"
    patched = client.patch(f"/build/sessions/{sid}",
                           json={"team": {"workers": [w["name"]], "reviewer": r["name"]}})
    assert patched.status_code == 200, patched.text
    view = patched.json()
    assert "team" in view, "the PATCH answer must be the full view, with its team"
    put = client.put("/build/folders/team",
                     json={"folder": str(work), "team": {"workers": [w["name"]], "reviewer": r["name"]}})
    assert put.status_code == 200, put.text
    got_folder = client.get("/build/folders/team", params={"folder": str(work)})
    assert got_folder.status_code == 200 and got_folder.json()["team"], got_folder.text

    # The turn record a team reply carries (the real builder), and a finish
    # record with two reviews.
    platform = client.app.state.platform
    team = T.resolve(platform, T.SessionTeam.of({"workers": [w["name"]], "reviewer": r["name"]}))
    turn = T.turn_for(platform, team, worker_tools=("read_file", "write_file"),
                      lead_provider="claude-cli", lead_model="claude-opus-5-5")
    turn.note_piece(w["name"])
    turn.no_worker("code", "Qwen3 8B is at its limit.")
    reply_team = T.saved_record(turn.record())
    finish = F.saved_record({"tests": "passed", "command": "pytest -q", "removed": 0, "kept": 0, "points": 1,
                             "unmet": 0, "after": "",
                             "review": {"state": "done", "member": r["name"], "model": "GPT-6 Astra",
                                        "verdict": "approved", "rounds": 2}})
    assert finish and finish.get("review", {}).get("rounds") == 2, finish

    got = _stable({
        "from_model_worker": w, "from_model_reviewer": r,
        # Only the view's TEAM parts: the rest of a view (its session id, its
        # turn) changes run to run and is not this seam.
        "view": {"team": view.get("team"), "setup": {"team": (view.get("setup") or {}).get("team")}},
        "folder": got_folder.json(), "empty_folder": empty_folder.json(),
        "reply_team": reply_team, "finish": finish,
    })
    if REGEN or not FIXTURE.exists():
        FIXTURE.write_bytes((json.dumps(got, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == got, (
        "the daemon's answers changed — regenerate with IJ_REGEN_TEAM_SEAMS=1 and re-run the "
        "dashboard's session-team-seams-v1403 test"
    )
