"""v1.417.0 (C1.6, the user's decision D2) — Accept edits and Auto survive an
approved plan's act turn and a PERSON's "Resume when ready".

``unwatched_mode`` mapped both to Ask first for every coding-session turn
Iron Jarvis starts itself. A person's press is the watch: the plan they just
allowed, and the Resume when ready they pressed (a wait not armed by itself),
keep the posture they chose. The night, CI and a restart resume still fall
back. Driven through ``routes/build.run_approved_plan`` and
``start_build_turn`` with the lane replaced by a recorder of the body it gets.
"""

from __future__ import annotations

import pytest

from iron_jarvis.core import auto_approve as A
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as B
from iron_jarvis.harness import sessions as S
from tests.test_build_sessions_v1367 import new_session, until

PLAN = "My plan:\n1. Change x in app.py\n2. Run the tests"


@pytest.mark.parametrize("mode", ["accept_edits", "auto"])
@pytest.mark.parametrize("started_by", ["person", "wait"])
def test_a_press_keeps_the_eased_posture(mode, started_by):
    assert A.unwatched_mode(mode, started_by=started_by, continuation=True, pressed=True) == mode


@pytest.mark.parametrize("mode", ["accept_edits", "auto"])
@pytest.mark.parametrize("started_by", ["restart", "night", "ci"])
def test_the_night_ci_and_a_restart_still_fall_back_even_if_pressed(mode, started_by):
    assert A.unwatched_mode(mode, started_by=started_by, continuation=True, pressed=True) == "approve_for_me"


@pytest.fixture()
def build_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    (tmp_path / "cc").mkdir()
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.chat_files_root = str(tmp_path / "chat-files")
    work = tmp_path / "work"
    work.mkdir()
    (work / "app.py").write_text("x = 1\n", encoding="utf-8")
    yield a, work
    B._PLAN_RUNS.clear()


def _record_posture(monkeypatch) -> list[str]:
    seen: list[str] = []

    async def fake_stream(platform, personas, body, **kw):
        seen.append(str(body.approval_mode or ""))

        async def gen():
            yield 'event: done\ndata: {"reply": "ok"}\n\n'

        return gen()

    monkeypatch.setattr(B, "stream_chat_turn", fake_stream)
    return seen


@pytest.mark.parametrize("mode", ["accept_edits", "auto"])
async def test_an_approved_plans_act_turn_runs_under_the_chosen_posture(build_app, monkeypatch, mode):
    app, work = build_app
    seen = _record_posture(monkeypatch)
    sid = new_session(app, work, approval_mode=mode)["id"]
    S.append_lines(app.state.platform.engine, sid, [
        {"role": "user", "content": "plan it", "turnId": "p1"},
        {"role": "assistant", "content": PLAN + "\n\nReady.", "turnId": "p1"},
    ])
    B._PLAN_RUNS[sid] = {"from": "p1", "taken": False}
    lg = await B.run_approved_plan(app.state.d, sid, "p1")
    assert lg is not None
    await until(lambda: lg.status != "running", 30)
    assert seen == [mode]


@pytest.mark.parametrize("auto_armed,want", [(False, "auto"), (True, "approve_for_me")],
                         ids=["the-person-pressed-it", "armed-by-itself"])
async def test_resume_when_ready_keeps_the_posture_only_when_a_person_pressed_it(
        build_app, monkeypatch, auto_armed, want):
    app, work = build_app
    seen = _record_posture(monkeypatch)
    monkeypatch.setattr(B.S, "take_wait", lambda *a, **kw: True)
    sid = new_session(app, work, approval_mode="auto")["id"]
    wait = {"account": "acc-1", "armed_at": "2026-10-31T00:00:00+00:00", **({"auto": True} if auto_armed else {})}
    lg = await B.start_build_turn(app.state.d, sid, B.CONTINUE_TEXT, "t-wait", continuation=True,
                                  resume_of=wait, started_by="wait")
    await until(lambda: lg.status != "running", 30)
    assert seen == [want]


async def test_a_night_turn_still_falls_back(build_app, monkeypatch):
    app, work = build_app
    seen = _record_posture(monkeypatch)
    sid = new_session(app, work, approval_mode="auto")["id"]
    lg = await B.start_build_turn(app.state.d, sid, "go on", "t-night", continuation=True,
                                  started_by="restart", pressed=True)
    await until(lambda: lg.status != "running", 30)
    assert seen == ["approve_for_me"]
