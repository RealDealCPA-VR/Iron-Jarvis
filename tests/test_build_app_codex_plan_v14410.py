"""v1.441.0: Codex's own Plan in Build › Code.

MEASURED (codex 0.157.0; ``hosted.codex`` holds the reading): ``turn/start``
takes ``collaborationMode: {mode: "plan", settings: {model, …}}`` (the
experimental API Iron Jarvis already opts into), the thread keeps it across
turns, and the plan arrives as its own ``plan`` item (``item/plan/delta``,
``item/completed``) before ``turn/completed``. Codex's plan mode is
instructions only: read-only is Iron Jarvis's (Ask first's posture, and every
file change declined at once in Plan). The plan card is the one every tool's
plan uses; Allow leaves Plan for Ask first and sends Codex's own
"Implement the plan." in the same reply.

Each test replays the measured turn (``fixtures/build_app_v14410/codex_plan
*.json``, masked) through the real fake harness, driver, service and routes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted import words as W
from iron_jarvis.hosted.driver import Ask, AskRefused
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, until, use_fake, wait_pending

FIX = Path(__file__).parent / "fixtures" / "build_app_v14410"
THREAD_MODEL = "gpt-5.6-terra"  # the model the recorded thread said it runs on


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _fake(monkeypatch, tmp_path, scenario: str) -> Path:
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_allow", log)
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(FIX / f"{scenario}.json"))
    return log


def _turn_starts(log: Path) -> list[dict]:
    return [m for m in sent(log) if m.get("method") == "turn/start"]


async def _plan_turn(app, work, tmp_path, monkeypatch, scenario="codex_plan"):
    log = _fake(monkeypatch, tmp_path, scenario)
    c = client(app)
    s = await new_session(c, work, harness="codex", mode="plan")
    task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn",
                                        json={"text": "Plan adding a line", "turn_id": "t"}))
    return c, s, task, log


def test_plan_is_offered_for_codex_with_what_it_does():
    assert "plan" in X.MODES and "plan" not in X.HIDDEN_MODES
    assert X.MODES["plan"] == X.MODES["ask"], "Plan runs on Ask first's posture: every change and command asks"
    about = X.MODE_ABOUT["plan"]
    assert "changes no file" in about and "approve its plan" in about


async def test_a_plan_turn_sends_codexs_plan_mode_with_the_threads_model(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _plan_turn(app, work, tmp_path, monkeypatch)
    try:
        await wait_pending(c, s["id"])
    finally:
        task.cancel()
        await c.aclose()
        await stop_all(app)
    first = _turn_starts(log)[0]["params"]
    assert first["approvalPolicy"] == "untrusted"
    assert first["collaborationMode"] == {"mode": "plan", "settings": {
        "model": THREAD_MODEL, "reasoning_effort": None, "developer_instructions": None}}


async def test_the_plan_is_the_reply_and_its_card_waits_before_the_reply_ends(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _plan_turn(app, work, tmp_path, monkeypatch)
    try:
        card = (await wait_pending(c, s["id"]))[0]
        assert not task.done(), "the reply ended before the plan was answered"
        assert card["title"] == W.PLAN_CARD_TITLE
        assert card["plan"].startswith("1. Open `a.txt`")
        assert card["then_mode"] == "ask" and card.get("once_only") is True and card["can_session"] is False
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": card["request_id"], "decision": "deny"})
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await c.aclose()
        await stop_all(app)
    text = "".join(e.get("delta", "") for e in evs if e["type"] == "text")
    assert "append a new line containing `two`" in text, "the plan's own words are the reply"
    assert any(e["type"] == "text_done" and e["text"].startswith("1. Open") for e in evs)
    done = [e for e in evs if e["type"] == "done"]
    assert done and done[-1]["stop"] == "completed"
    assert any(e["type"] == "notice" and e["text"] == W.CODEX_PLAN_KEPT for e in evs)
    assert len(_turn_starts(log)) == 1, "a 'no' starts nothing in Codex"


async def test_allow_leaves_plan_for_ask_first_and_implements_in_the_same_reply(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _plan_turn(app, work, tmp_path, monkeypatch)
    try:
        card = (await wait_pending(c, s["id"]))[0]
        r = await c.post(f"/build/app/sessions/{s['id']}/answer",
                         json={"request_id": card["request_id"], "decision": "allow"})
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 30)).text)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()
    finally:
        await c.aclose()
        await stop_all(app)
    starts = _turn_starts(log)
    assert len(starts) == 2
    second = starts[1]["params"]
    assert second["input"][0]["text"] == X.IMPLEMENT_TEXT
    assert second["collaborationMode"]["mode"] == "default", "the thread is taken out of Plan"
    assert second["approvalPolicy"] == "untrusted"
    assert [e["stop"] for e in evs if e["type"] == "done"] == ["completed"], "ONE reply, ended once"
    assert any(e["type"] == "text_done" and e["text"] == "Added the line." for e in evs)
    assert any(e["type"] == "notice" and "now runs in Ask first" in e["text"] for e in evs)
    assert row["session"]["mode"] == "ask" if "session" in row else row["mode"] == "ask"


async def test_stop_while_the_plan_card_waits_ends_the_reply_and_starts_nothing(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _plan_turn(app, work, tmp_path, monkeypatch)
    try:
        await wait_pending(c, s["id"])
        await c.post(f"/build/app/sessions/{s['id']}/interrupt")
        evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await c.aclose()
        await stop_all(app)
    assert [e["stop"] for e in evs if e["type"] == "done"] == ["interrupted"]
    assert len(_turn_starts(log)) == 1
    assert not any(e["type"] == "notice" and e["text"] == W.CODEX_PLAN_KEPT for e in evs)


async def test_a_file_change_in_plan_is_declined_at_once_and_never_a_card(app, work, tmp_path, monkeypatch):
    c, s, task, log = await _plan_turn(app, work, tmp_path, monkeypatch, scenario="codex_plan_edit")
    try:
        card = (await wait_pending(c, s["id"]))[0]
        assert card["title"] == W.PLAN_CARD_TITLE, "the only card is the plan's"
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": card["request_id"], "decision": "deny"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
    finally:
        await c.aclose()
        await stop_all(app)
    answers = [m for m in sent(log) if "method" not in m and m.get("id") == 0 and "result" in m]
    assert [a["result"] for a in answers] == [{"decision": "decline"}]
    assert [e for e in evs if e["type"] == "approval"][0]["title"] == W.PLAN_CARD_TITLE
    assert any(e["type"] == "notice" and "a.txt" in e["text"] and "nothing was changed" in e["text"] for e in evs)


async def test_a_session_that_never_planned_sends_no_collaboration_mode(app, work, tmp_path, monkeypatch):
    """Byte-identical to v1.440.0 for every session not in Plan."""
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_allow", log)
    c = client(app)
    s = await new_session(c, work, harness="codex")
    task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}))
    try:
        await until(lambda: _turn_starts(log))
    finally:
        task.cancel()
        await c.aclose()
        await stop_all(app)
    assert "collaborationMode" not in _turn_starts(log)[0]["params"]


# -- the driver's own rules (no process) ---------------------------------------
def _driver(mode="plan"):
    got: list = []

    async def write(obj):
        got.append({"wrote": obj})
        if obj.get("method") == "turn/start":
            d._replies.settle(obj["id"], {"turn": {"id": "tu2"}})

    d = X.CodexDriver(write=write, emit=lambda e: got.append(e), ask=lambda a: got.append(a),
                      on_session_id=lambda s: None, mode=mode)
    d.session_id = "th"
    d.turn_id = "tu"
    d._thread_model = "m1"
    return d, got


async def test_a_reopened_thread_left_in_plan_is_taken_out_once():
    d, got = _driver(mode="ask")
    d._collab = "plan"  # what thread/resume said
    await d.send_user("go on", [])
    await d.send_user("and again", [])
    starts = [g["wrote"]["params"] for g in got if isinstance(g, dict) and g.get("wrote", {}).get("method") == "turn/start"]
    assert starts[0]["collaborationMode"]["mode"] == "default"
    assert "collaborationMode" not in starts[1], "taken out ONCE"


async def test_the_plan_card_refuses_any_other_answer_and_sends_codex_nothing():
    d, got = _driver()
    d.turn_open = True
    d._turn_mode = "plan"
    d._plan_text = "1. do it"
    d._turn_completed({"status": "completed"})
    ask = next(a for a in got if isinstance(a, Ask))
    assert ask.data["plan"] is True and ask.native is None
    assert not any(isinstance(e, dict) and e.get("type") == "done" for e in got)
    with pytest.raises(AskRefused):
        d.prepare_answer(ask, "allow_session", "", None)
    assert d.prepare_answer(ask, "allow", "", None) is None


async def test_a_plan_turn_with_no_plan_ends_as_usual():
    d, got = _driver()
    d.turn_open = True
    d._turn_mode = "plan"
    d._turn_completed({"status": "completed"})
    assert not any(isinstance(a, Ask) for a in got)
    assert [e["stop"] for e in got if isinstance(e, dict) and e.get("type") == "done"] == ["completed"]


async def test_auto_review_stays_hidden_with_a_plain_reason():
    why = X.HIDDEN_MODES["auto"]
    assert "reviewer" in why
    for word in ("sandbox", "approvalsreviewer", "auto_review", "guardian"):
        assert word not in why.lower()


#: What the REAL daemon sends for these (the plan card, OpenCode's mode rows
#: with an agent of the user's): the dashboard decodes the same file
#: (``dashboard/__tests__/build-app-plan-agents-v1441-0.test.tsx``).
#: Regenerate after an intended change with ``IJ_REGEN_BUILD_APP_V1441=1``.
GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-plan-agents-v1441-0.json"


async def test_the_dashboards_seam_is_what_the_daemon_sends(app, work, tmp_path, monkeypatch):
    import json
    import os

    from iron_jarvis.hosted import opencode as O
    from iron_jarvis.hosted import service as S

    c, s, task, log = await _plan_turn(app, work, tmp_path, monkeypatch)
    try:
        card = dict((await wait_pending(c, s["id"]))[0])
    finally:
        task.cancel()
        await c.aclose()
        await stop_all(app)
    card["request_id"] = "ask_1"
    card["at"] = "2026-11-06T00:00:00Z"
    monkeypatch.setattr(O, "LAST_AGENTS", {})
    O.note_agents("C:/work/demo", [{"name": "docs", "description": "Writes docs only"}])
    doc = {"_about": ("Written by the REAL daemon: Codex's plan card (a replay of the measured plan turn) and "
                      "OpenCode's mode rows with one agent of the user's. Regenerate with "
                      "IJ_REGEN_BUILD_APP_V1441=1."),
           "codex_plan_card": card,
           "opencode_modes": S.mode_rows("opencode"),
           "codex_modes": S.mode_rows("codex"),
           "codex_hidden": S.hidden_mode_rows("codex")}
    if os.environ.get("IJ_REGEN_BUILD_APP_V1441") == "1":
        GOLDEN.write_bytes((json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == doc, "regenerate with IJ_REGEN_BUILD_APP_V1441=1"
