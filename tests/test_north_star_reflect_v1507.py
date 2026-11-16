"""North-star fix 7.3 (v1.507.0): "Reflect" leaves suggestions that can be answered.

``POST /improvement/reflect`` returned suggestions the Memory page rendered as
plain text: not stored, no Keep, not in Improvements, gone on reload. Now each
is stored through the same path as what went wrong in a chat
(``preferences.mint_noticed``: status proposed, origin "noticed", the
never-ask-twice rule and the open limit), source "reflected", with the
low-scoring runs it came from as evidence. It waits in Improvements and on the
Memory page, reaches no prompt until kept, and once kept is walled by those
runs. The route still says honestly when no model answered: a demo model's
reply is never a suggestion, and the scores' own suggestions say where they
came from.

Real app and routes; the reflector and the router are stubs.
"""

from __future__ import annotations

import iron_jarvis.eval.models  # noqa: F401
import iron_jarvis.improvement.models  # noqa: F401

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.models import Evaluation
from iron_jarvis.learning import preferences as prefs
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.workmap.clients import store, wall
from tests.test_north_star_lessons_wall_v1501 import H, S, _map, _turn

collect = importlib.import_module("iron_jarvis.inbox.collect")


def _low_run(platform, task: str, folder: str = "") -> str:
    sess = Session(task=task, agent_type=AgentType.BUILDER, status=SessionStatus.FAILED,
                   workspace_path=folder or None)
    sid = sess.id
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(Evaluation(session_id=sid, agent_run_id="r", completion=0.0, tool_success_rate=0.0))
        db.commit()
    platform.improvement.record_outcome(sid, lessons_applied=[])
    return sid


@pytest.fixture()
def scene(tmp_path):
    app = create_app(str(tmp_path / "d"))
    platform = app.state.platform
    runs = [_low_run(platform, "Sort the March photos by date"), _low_run(platform, "Draft the April summary")]
    return TestClient(app), platform, runs


CANNED = [
    {"kind": "lesson", "target": "builder", "suggestion": "Check each photo's date against its file name first."},
    {"kind": "prompt", "target": "builder", "suggestion": "Say which figures are estimates."},
]


def _reflected(platform):
    with session_scope(platform.engine) as db:
        return list(db.exec(select(LessonRecord).where(LessonRecord.source == prefs.SOURCE_REFLECTED)))


def test_reflect_stores_each_suggestion_with_the_runs_as_evidence(scene):
    client, platform, runs = scene
    platform.improvement._reflector = lambda ctx: CANNED
    out = client.post("/improvement/reflect").json()
    assert out["applied"] is False and out["reviewed"] == 2 and out["made_by"] == "model"
    assert [s["status"] for s in out["suggestions"]] == ["suggested", "suggested"]
    rows = {r.id: r for r in _reflected(platform)}
    assert {s["id"] for s in out["suggestions"]} == set(rows)
    for row in rows.values():
        assert (row.status, row.origin) == ("proposed", "noticed")
        links = {e["link"] for e in prefs.evidence_of(row)}
        assert links == {f"/sessions/{sid}" for sid in runs}
        assert {e["where"] for e in prefs.evidence_of(row)} == {"run"}
        assert "Sort the March photos by date" in {e["quote"] for e in prefs.evidence_of(row)}
    # Improvements lists them, with Keep and Not this on their own routes.
    items = [i for i in collect.collect(SimpleNamespace(platform=platform))["items"] if i["ref"] in rows]
    assert len(items) == 2
    assert all(i["accept"]["path"].endswith("/keep") for i in items)
    assert any("From an agent run that went badly" in line for i in items for line in i["evidence"])
    # The Memory page lists them as suggestions; nothing reaches a prompt yet.
    suggested = {p["id"] for p in client.get("/memory/preferences").json()["suggested"]}
    assert set(rows) <= suggested
    assert "photo's date" not in platform.learning.apply_to_prompt("")
    # Kept, it reaches the prompt.
    first = out["suggestions"][0]["id"]
    assert client.post(f"/memory/preferences/{first}/keep").status_code == 200
    assert "Check each photo's date against its file name first." in platform.learning.apply_to_prompt("")


def test_never_asked_twice_and_the_open_limit(scene):
    client, platform, _runs = scene
    platform.improvement._reflector = lambda ctx: CANNED
    client.post("/improvement/reflect")
    again = client.post("/improvement/reflect").json()
    assert [s["status"] for s in again["suggestions"]] == ["already_waiting", "already_waiting"]
    assert len(_reflected(platform)) == 2
    platform.improvement._reflector = lambda ctx: [
        {"kind": "lesson", "suggestion": "Read the whole folder before counting it."},
        {"kind": "lesson", "suggestion": "Name the folder every file was saved in."},
    ]
    more = client.post("/improvement/reflect").json()
    assert [s["status"] for s in more["suggestions"]] == ["suggested", "full"]  # three may wait at once
    assert "id" not in more["suggestions"][1]


def test_a_demo_models_reply_is_never_a_suggestion_and_the_route_says_so(scene):
    client, platform, _runs = scene

    async def demo(**kw):
        return RouteResult(LLMResponse(text='[{"kind": "lesson", "suggestion": "DEMO ANSWER"}]'), "mock", "mock")

    platform.router.complete = demo
    out = client.post("/improvement/reflect").json()
    assert out["made_by"] == "scores"
    assert "No model answered" in out["note"]
    assert all("DEMO ANSWER" not in s["suggestion"] for s in out["suggestions"])
    # Lead review: what the scores alone wrote is shown, never stored — kept,
    # "Recent builder sessions scored low; …" would be told as a lesson.
    assert out["suggestions"] and {s["status"] for s in out["suggestions"]} == {"from_scores"}
    assert out["stored"] == 0 and all("id" not in s for s in out["suggestions"])
    assert _reflected(platform) == []


def test_a_real_models_reply_is_used(scene):
    client, platform, _runs = scene

    async def real(**kw):
        return RouteResult(
            LLMResponse(text='[{"kind": "lesson", "suggestion": "Count the files twice."}]'), "anthropic", "m"
        )

    platform.router.complete = real
    out = client.post("/improvement/reflect").json()
    assert out["made_by"] == "model" and "note" not in out
    assert [r.text for r in _reflected(platform)] == ["Count the files twice."]


def test_a_kept_one_is_walled_by_the_runs_it_came_from(tmp_path, monkeypatch):
    cmap = _map()
    monkeypatch.setattr(store, "load_map", lambda e: cmap)
    app = create_app(str(tmp_path / "d"))
    platform = app.state.platform
    _low_run(platform, "Prepare the report", folder=H + r"\2026")
    platform.improvement._reflector = lambda ctx: [{"kind": "lesson", "suggestion": "Use metric units in every table."}]
    out = TestClient(app).post("/improvement/reflect").json()
    prefs.keep(platform.engine, out["suggestions"][0]["id"])

    def told(turn):
        with wall.turn_scope(turn):
            return platform.learning.apply_to_prompt("")

    assert "metric units" in told(_turn(platform.engine, H))
    assert "metric units" not in told(_turn(platform.engine, S))
