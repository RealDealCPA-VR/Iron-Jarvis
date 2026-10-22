"""v1.370.0 review (IJ harness Phase 7): the library's reflection is RATIONED.

On a Claude Code or Codex plan the reflection spends the user's own plan
limits on work they did not ask for, so the review pinned:

* ONE call per IDLE PERIOD, never one per turn: a finished turn is only
  noted; the session is reflected on once it has been idle for
  ``REFLECT_IDLE_S`` (a new turn stops the wait), or when it is archived —
  over every turn noted since the last reflection;
* never while the session's account is at its limit (a limit card, a
  "Resume when ready" wait, Iron-Proxy's word), never when Iron-Proxy says
  the account has used ``ACCOUNT_BUSY_PERCENT`` or more of a window — that
  stretch waits for a later idle period, nothing spent;
* bounded: the prompt is cut to ``PROMPT_CHARS`` (the harness notes first),
  the call to ``REFLECT_TIMEOUT_S``;
* a turn whose trust was lowered is never learned from;
* an edit only to a harness file the model was shown WHOLE (it writes the
  full new text: a cut copy would be written back);
* the setting's help names the session's model AND account and the plan
  limits, and its minutes are the code's.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from iron_jarvis.harness import library as L
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import asgi, messages_of, new_session, open_turn, wire
from tests.test_harness_library_v1370 import GOOD, WORK_MOVES, LibScript, _rows


@pytest.fixture(autouse=True)
def _fresh_library_state(monkeypatch):
    L.shutdown()
    monkeypatch.setattr(L, "AUTO_REFLECT", True)
    yield
    L.shutdown()


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
    return folder


TWO_TURNS = [
    *WORK_MOVES,
    [ToolCall(id="c3", name="Write", arguments={"file_path": "notes.txt", "content": "n\n"})],
    "Wrote notes.txt.",
]


async def _turn(app, sid, text, turn_id):
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw


def _session(app, work, reflection=GOOD, moves=None):
    sid = new_session(app, work, approval_mode="yolo")["id"]
    script = wire(app, LibScript(list(moves or TWO_TURNS), reflection))
    return sid, script


# --------------------------------------------------------------------------- #
# one call per idle period
# --------------------------------------------------------------------------- #


async def test_two_turns_then_idle_make_ONE_reflection_over_both(app, work, monkeypatch):
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    # The turn only NOTED: no model call, a wait armed about an hour ahead.
    assert script.reflections == []
    state = L.status_of(sid)
    assert state["turns"] == 1 and state["due_at"] and not state["running"]
    due = datetime.fromisoformat(state["due_at"].replace("Z", "+00:00"))
    # (two clocks read microseconds apart: a second of slack either way)
    assert timedelta(minutes=55) < due - datetime.now(timezone.utc) <= timedelta(hours=1, seconds=1)
    await _turn(app, sid, "take notes", f"{sid}-b")
    await asyncio.sleep(0)
    assert script.reflections == [] and L.status_of(sid)["turns"] == 2
    # The idle wait ends: ONE call, over both turns.
    task = L.flush(app.state.platform, sid)
    assert task is not None
    await L.drain()
    assert len(script.reflections) == 1
    prompt = script.reflections[0]["prompt"]
    assert "TURN 1 OF 2" in prompt and "TURN 2 OF 2" in prompt
    assert "fix add" in prompt and "take notes" in prompt
    rows = _rows(app)
    assert [r.kind for r in rows] == ["lesson"]
    # The proposal carries the NEWEST turn (a rewind to either takes it).
    assert rows[0].turn_id == f"{sid}-b"
    assert json.loads(rows[0].evidence_json)["turns"] == [f"{sid}-a", f"{sid}-b"]
    assert L.status_of(sid) == {"due_at": None, "running": False, "turns": 0}


async def test_the_idle_wait_fires_by_itself_and_a_new_turn_restarts_it(app, work, monkeypatch):
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    first = L._TIMERS[sid]
    # A turn starting stops the wait (the session is not idle).
    L.note_turn_started(sid)
    await asyncio.sleep(0)
    assert first.cancelled() and L.status_of(sid)["due_at"] is None
    assert L.status_of(sid)["turns"] == 1  # the noted turn is kept
    # A short wait fires by itself: one reflection, over the noted turn.
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 0.05)
    await _turn(app, sid, "take notes", f"{sid}-b")
    await L.drain()
    assert len(script.reflections) == 1 and "TURN 2 OF 2" in script.reflections[0]["prompt"]


async def test_a_running_turn_holds_the_reflection_back_and_the_wait_starts_again(app, work, monkeypatch):
    from iron_jarvis.daemon.routes import build as build_routes

    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    build_routes._STARTING.add(sid)
    try:
        monkeypatch.setattr(L, "REFLECT_IDLE_S", 0.05)
        assert L.flush(app.state.platform, sid) is None  # not idle: nothing now
        await asyncio.sleep(0)
        assert script.reflections == [] and L.status_of(sid)["turns"] == 1
        assert L.status_of(sid)["due_at"] is not None  # ...and it waits again
    finally:
        build_routes._STARTING.discard(sid)
    await L.drain()
    assert len(script.reflections) == 1


async def test_archiving_a_session_reflects_on_it_now(app, work, monkeypatch):
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    status, _ = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {})
    assert status == 200
    await L.drain()
    assert len(script.reflections) == 1


async def test_the_library_view_says_when_the_next_reflection_is_due(app, work, monkeypatch):
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, _ = _session(app, work)
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/library")
    assert status == 200 and view["reflection"] == {"due_at": None, "running": False, "turns": 0}
    await _turn(app, sid, "fix add", f"{sid}-a")
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/library")
    assert view["reflection"]["due_at"] and view["reflection"]["turns"] == 1


async def test_a_rewind_takes_its_turns_out_of_the_waiting_stretch(app, work, monkeypatch):
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    await _turn(app, sid, "take notes", f"{sid}-b")
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": f"{sid}-b"})
    assert status == 200, out
    assert L.status_of(sid)["turns"] == 1
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/rewind", {"to": f"{sid}-a"})
    assert status == 200, out
    assert L.status_of(sid) == {"due_at": None, "running": False, "turns": 0}
    await L.drain()
    assert script.reflections == []


def test_the_setting_says_it_uses_the_sessions_model_and_account():
    from iron_jarvis.settings.schema import SETTINGS

    row = next(r for r in SETTINGS if r.key == "build_reflect")
    words = row.help
    assert "session's own model" in words and "session's own account" in words
    assert "plan's limits" in words and "near its limit" in words
    assert f"{int(L.REFLECT_IDLE_S // 60)} minutes" in words
    from pathlib import Path

    handbook = (Path(__file__).resolve().parents[1] / "docs" / "HANDBOOK.md").read_text(encoding="utf-8")
    handbook = " ".join(handbook.split())
    assert f"idle for {int(L.REFLECT_IDLE_S // 60)} minutes" in handbook
    assert f"used {L.ACCOUNT_BUSY_PERCENT}% or more of a window" in handbook
    assert "never one per reply" in handbook and "session's own account" in handbook


# --------------------------------------------------------------------------- #
# never at (or near) the account's limit
# --------------------------------------------------------------------------- #


class _Proxy:
    """Iron-Proxy's CACHED snapshot only (never a call)."""

    enabled = True

    def __init__(self, status="ready", percent=None):
        state: dict = {"status": status}
        if status == "parked":
            state["parkedUntil"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        if percent is not None:
            state["windows"] = [{
                "usedPercent": percent, "windowMins": 300,
                "resetsAt": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            }]
        self._cache = ([{"id": "acc-1", "title": "Work", "provider": "anthropic", "order": 0}],
                       {"acc-1": state})

    def cached_accounts(self):
        return self._cache


async def _noted(app, work, monkeypatch, **setup):
    monkeypatch.setattr(L, "REFLECT_IDLE_S", 3600.0)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    if setup:
        S.update(app.state.platform.engine, sid, setup=setup)
    return sid, script


@pytest.mark.parametrize("setup", [
    {"account_limit": {"account": "acc-1", "kind": "usage"}},
    {"account_wait": {"armed_at": "2026-10-22T00:00:00Z", "account": "acc-1"}},
])
async def test_a_session_whose_account_is_at_its_limit_is_not_spent(app, work, monkeypatch, setup):
    sid, script = await _noted(app, work, monkeypatch, **setup)
    out = await L.reflect(app.state.platform, sid, f"{sid}-a")
    assert out.reason == L.REASON_ACCOUNT_LIMIT and script.reflections == [] and out.minted == []
    # The idle wait's stretch is KEPT for a later idle period (nothing spent).
    L.flush(app.state.platform, sid)
    await L.drain()
    assert script.reflections == [] and L.status_of(sid)["turns"] == 1


@pytest.mark.parametrize("proxy,why", [
    (_Proxy(status="parked"), L.REASON_ACCOUNT_LIMIT),
    (_Proxy(percent=85), L.REASON_ACCOUNT_BUSY.format(percent=85)),
    (_Proxy(percent=L.ACCOUNT_BUSY_PERCENT), L.REASON_ACCOUNT_BUSY.format(percent=L.ACCOUNT_BUSY_PERCENT)),
])
async def test_iron_proxys_word_at_or_near_the_limit_stops_it(app, work, monkeypatch, proxy, why):
    app.state.platform.iron_proxy = proxy
    sid, script = await _noted(app, work, monkeypatch, account="acc-1", account_name="Work")
    out = await L.reflect(app.state.platform, sid, f"{sid}-a")
    assert out.reason == why and script.reflections == []


def test_room_on_the_account_or_no_word_is_no_refusal():
    from types import SimpleNamespace

    assert L.account_refusal(SimpleNamespace(iron_proxy=_Proxy(percent=40)), {"account": "acc-1"}) == ""
    assert L.account_refusal(SimpleNamespace(iron_proxy=_Proxy()), {"account": "acc-1"}) == ""
    assert L.account_refusal(SimpleNamespace(iron_proxy=None), {"account": "acc-1"}) == ""
    assert L.account_refusal(SimpleNamespace(iron_proxy=None), {}) == ""


# --------------------------------------------------------------------------- #
# bounded, never learned from flagged content, never a cut copy written back
# --------------------------------------------------------------------------- #


async def test_the_prompt_is_bounded_and_keeps_the_notes_whole(app, work, monkeypatch):
    long_q = "x" * 40_000
    sid, script = _session(app, work)
    await _turn(app, sid, long_q, f"{sid}-a")
    hdir = work / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    (hdir / "memory.md").write_text("Tests run with `echo 3 passed`.\n", encoding="utf-8")
    await L.reflect(app.state.platform, sid, f"{sid}-a")
    prompt = script.reflections[0]["prompt"]
    assert len(prompt) <= L.PROMPT_CHARS
    assert prompt.endswith("--- memory.md ---\nTests run with `echo 3 passed`.\n")


def test_the_prompt_bound_holds_for_a_long_stretch():
    """Eight turns, thirty steps with long arguments and outputs, three
    notes files: still at most PROMPT_CHARS, and the notes come whole at the
    end (the edit rule needs them whole)."""
    turns = [{"turn_id": f"t{i}", "question": "q" * 9000, "reply": "r" * 9000} for i in range(8)]
    steps = [{"tool": "shell", "args": {"command": "c" * 299}, "ok": True, "output": "o" * L.STEP_OUTPUT_CHARS,
              "turn": i % 8} for i in range(L.PROMPT_STEPS)]
    ev = L.Evidence(thread_id="x", turn_id="t7", folder="f", question="", reply="", route={},
                    transcript="", paths=set(), steps=steps, turns=turns)
    notes = {"memory.md": "m" * 1500, "tool_guidance.md": "g" * 1500, "guardrails.md": "Never push.\n"}
    prompt = L.reflect_prompt(ev, notes)
    assert len(prompt) <= L.PROMPT_CHARS
    assert prompt.endswith("--- tool_guidance.md ---\n" + "g" * 1500)
    assert "--- guardrails.md ---\nNever push.\n" in prompt and ("m" * 1500) in prompt


async def test_the_call_has_a_time_limit(app, work, monkeypatch):
    monkeypatch.setattr(L, "REFLECT_TIMEOUT_S", 0.05)
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    real = script.complete

    async def slow(**kw):
        if kw.get("system") == L.REFLECT_SYSTEM:
            await asyncio.sleep(5)
        return await real(**kw)

    script.complete = slow
    app.state.platform.router.complete = slow
    out = await L.reflect(app.state.platform, sid, f"{sid}-a")
    assert out.reason == L.REASON_MODEL_FAILED and out.minted == []


async def test_a_turn_whose_trust_was_lowered_is_never_learned_from(app, work, monkeypatch):
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    eng = app.state.platform.engine
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(eng) as db:
        row = db.get(ChatThreadRecord, sid)
        msgs = json.loads(row.messages_json)
        for m in msgs:
            if m["role"] == "assistant":
                m["trust"] = "low"
        row.messages_json = json.dumps(msgs)
        db.add(row)
        db.commit()
    out = await L.reflect(app.state.platform, sid, f"{sid}-a")
    assert out.reason == L.REASON_LOW_TRUST and script.reflections == []


async def test_no_edit_to_a_file_the_model_saw_cut(app, work, monkeypatch):
    """A memory.md longer than the reflector is shown is never offered for an
    edit: the edit is the FULL new text, and a model shown a cut copy writes
    the cut copy back — the rest of the user's notes would be gone after an
    Accept."""
    hdir = work / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    long_notes = "".join(f"note {i}: keep me\n" for i in range(400))  # ~7,000 characters
    assert len(long_notes) > L.LAYER_FILE_CHARS
    (hdir / "memory.md").write_text(long_notes, encoding="utf-8")
    cut = long_notes[: L.LAYER_FILE_CHARS]
    reflection = {"lesson": "add() lives in calc.py.",
                  "edit": {"file": "memory.md", "text": cut + "add() lives in calc.py.\n"}}
    sid, script = _session(app, work, reflection=reflection)
    await _turn(app, sid, "fix add", f"{sid}-a")
    out = await L.reflect(app.state.platform, sid, f"{sid}-a")
    assert [r.kind for r in out.minted] == ["lesson"]
    assert "do not propose an edit to it" in script.reflections[0]["prompt"]
    assert long_notes not in script.reflections[0]["prompt"]


# --------------------------------------------------------------------------- #
# the stretch's evidence: honest mock, the turn boundary
# --------------------------------------------------------------------------- #


async def test_a_demo_turn_in_the_stretch_is_left_out(app, work, monkeypatch):
    sid, script = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    await _turn(app, sid, "take notes", f"{sid}-b")
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(app.state.platform.engine) as db:
        row = db.get(ChatThreadRecord, sid)
        msgs = json.loads(row.messages_json)
        for m in msgs:
            if m["role"] == "assistant" and m["turnId"] == f"{sid}-a":
                m["route"] = {"provider": "mock", "model": "mock", "reason": "mock"}
        row.messages_json = json.dumps(msgs)
        db.add(row)
        db.commit()
    ev = L.stretch_evidence(app.state.platform.engine, sid, [f"{sid}-a", f"{sid}-b"])
    assert ev.turn_ids == [f"{sid}-b"] and "fix add" not in ev.transcript
    only_demo = L.stretch_evidence(app.state.platform.engine, sid, [f"{sid}-a"])
    assert only_demo.mock_only is True
    out = await L.reflect(app.state.platform, sid, [f"{sid}-a"])
    assert out.reason == L.REASON_MOCK and script.reflections == []


async def test_a_tool_row_stamped_with_the_next_questions_time_stays_in_its_own_turn(app, work):
    """On a coarse clock the previous turn's last tool row can carry the very
    stamp of the next question: it is still the previous turn's work (it was
    recorded before that turn's reply line)."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord, ToolInvocation
    from sqlmodel import select

    sid, _ = _session(app, work)
    await _turn(app, sid, "fix add", f"{sid}-a")
    await _turn(app, sid, "take notes", f"{sid}-b")
    eng = app.state.platform.engine
    msgs = messages_of(app, sid)
    q_b = next(m for m in msgs if m["turnId"] == f"{sid}-b" and m["role"] == "user")
    tie = datetime.fromisoformat(q_b["at"].replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    with session_scope(eng) as db:
        edit = next(r for r in db.exec(select(ToolInvocation)).all() if r.tool == "edit_file")
        edit.created_at = tie
        db.add(edit)
        # The coarse clock: turn a's reply line, written right after its last
        # tool row, carries the same tick as turn b's question.
        row = db.get(ChatThreadRecord, sid)
        lines = json.loads(row.messages_json)
        for m in lines:
            if m["turnId"] == f"{sid}-a" and m["role"] == "assistant":
                m["at"] = q_b["at"]
        row.messages_json = json.dumps(lines)
        db.add(row)
        db.commit()
    ev = L.stretch_evidence(eng, sid, [f"{sid}-b"])
    assert all(s["tool"] != "edit_file" for s in ev.steps), ev.steps
    assert not any("calc.py" in f for f in ev.changed_files)


# --------------------------------------------------------------------------- #
# Accept writes only inside the folder, and only there
# --------------------------------------------------------------------------- #


def _proposed_edit(app, folder, before=""):
    from iron_jarvis.harness.library_models import HarnessLibraryRecord

    return L._mint(app.state.platform.engine, HarnessLibraryRecord(
        folder_key=L.folder_key(folder), folder=str(folder), kind="layer_edit", text="why",
        file="memory.md", before=before, after="add() lives in calc.py.\n", signature="edit-sig",
        thread_id="t", turn_id="x"))


def _spy_writes(monkeypatch) -> list:
    seen: list = []
    real = L._atomic_write

    def spy(path, text):
        seen.append(path)
        return real(path, text)

    monkeypatch.setattr(L, "_atomic_write", spy)
    return seen


async def test_accept_never_writes_through_a_harness_folder_that_leads_outside(app, work, tmp_path, monkeypatch):
    import os

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    try:
        import _winapi as w

        w.CreateJunction(str(outside), str(work / ".ironjarvis"))
    except (ImportError, OSError):
        try:
            os.symlink(outside, work / ".ironjarvis", target_is_directory=True)
        except OSError:
            pytest.skip("no junction or symlink can be made here")
    row = _proposed_edit(app, work)
    seen = _spy_writes(monkeypatch)
    status, body = await asgi(app, "POST", f"/build/library/{row.id}/accept")
    assert status == 409 and "leads outside the folder" in body["detail"], body
    assert seen == [] and list(outside.rglob("*")) == []
    assert L.get(app.state.platform.engine, row.id).status == "proposed"


async def test_accept_never_writes_through_a_harness_file_that_is_a_link(app, work, tmp_path, monkeypatch):
    import os

    hdir = work / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    away = tmp_path / "away.md"
    try:
        os.symlink(away, hdir / "memory.md")  # dangling: exists() is False
    except OSError:
        pytest.skip("no symlink can be made here")
    row = _proposed_edit(app, work)
    seen = _spy_writes(monkeypatch)
    status, body = await asgi(app, "POST", f"/build/library/{row.id}/accept")
    assert status == 409 and "leads outside the folder" in body["detail"], body
    assert seen == [] and not away.exists()


async def test_accept_of_an_edit_for_a_folder_that_is_gone_says_so(app, tmp_path):
    gone = tmp_path / "moved-away"
    gone.mkdir()
    row = _proposed_edit(app, gone)
    gone.rmdir()
    status, body = await asgi(app, "POST", f"/build/library/{row.id}/accept")
    assert status == 409 and body["detail"].startswith("The folder cannot be written now"), body
    assert not gone.exists()
