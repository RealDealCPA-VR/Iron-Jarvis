"""v1.398.0 (night shift N1, ``docs/NIGHT-SHIFT-PLAN.md``): the queue and
safe unattended runs — the pieces, one by one.

* the night window (local "HH:MM", may cross midnight) and its words;
* the settings (tolerant at load, refused in words by the writer);
* the Tonight routes: add (a folder that is not a git repository refused in
  words), list, move, edit, remove, cancel;
* the night posture (decision 4) and how a task ended (from its reply line);
* the phone: an answer to a coding session's card that is still OPEN is
  never refused for its age (the bug: refused after 300 s);
* the "Runs at once" governor counts a night task;
* a branch folder borrows the main folder's .venv / node_modules
  (decision 3) — never a link inside the worktree;
* Emergency stop: an automatic resume does not start, and a coding turn Iron
  Jarvis started by itself is stopped (a person's is not);
* keep-awake: ``/system/activity`` ``night_shift`` and the desktop's decision.

The whole night, end to end, is ``test_night_shift_run_v1398.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes import night as night_routes
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import main_env as ME
from iron_jarvis.harness import night as N
from iron_jarvis.harness import night_window as W
from iron_jarvis.settings import schema
from tests.test_build_git_v1375 import make_repo
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    asgi_get,
    new_session,
    open_turn,
    until,
    wire,
)

_ROOT = Path(__file__).resolve().parents[1]
_MAIN_JS = _ROOT / "desktop" / "main.js"


def _local(h: int, m: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 10, day, h, m).astimezone()


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def repo(tmp_path) -> Path:
    return make_repo(tmp_path / "proj")


# --------------------------------------------------------------------------- #
# 1. the window
# --------------------------------------------------------------------------- #


def test_the_window_crosses_midnight_and_says_when_it_starts():
    assert W.in_window(_local(23, 30), "23:00", "07:00")
    assert W.in_window(_local(2, 0), "23:00", "07:00")
    assert not W.in_window(_local(7, 0), "23:00", "07:00")
    assert not W.in_window(_local(15, 0), "23:00", "07:00")
    assert W.in_window(_local(13, 0), "12:00", "14:00") and not W.in_window(_local(14, 0), "12:00", "14:00")
    assert W.window_words(_local(15, 0), "23:00", "07:00") == "Starts at 11:00 PM tonight."
    assert W.window_words(_local(23, 30), "23:00", "07:00") == "Night tasks run now, until 7:00 AM."
    assert W.window_words(_local(15, 0), "01:00", "05:00") == "Starts at 1:00 AM tomorrow."
    # Equal ends: an empty window, never "all day".
    assert not W.in_window(_local(3, 0), "23:00", "23:00")
    assert "never start" in W.window_words(_local(3, 0), "23:00", "23:00")


def test_the_settings_are_tolerant_at_load_and_refused_in_words_by_the_writer(tmp_path):
    cfg = Config(home=tmp_path, project_root=tmp_path, night_window_start="7:00", night_window_end="later")
    assert (cfg.night_window_start, cfg.night_window_end) == ("07:00", "07:00")
    defaults = Config(home=tmp_path, project_root=tmp_path)
    assert (defaults.night_window_start, defaults.night_window_end) == ("23:00", "07:00")
    assert (defaults.night_max_at_once, defaults.night_task_minutes, defaults.night_card_wait_minutes) == (2, 90, 15)
    row = schema.BY_KEY["night_window_start"]
    assert row.group == "automation" and row.section == "Night shift" and row.tier == "allow"
    with pytest.raises(ValueError, match="time of day"):
        row.check("25:00")
    row.check("23:30")
    for key in ("night_window_end", "night_max_at_once", "night_task_minutes", "night_card_wait_minutes"):
        assert key in schema.daemon_keys()


def test_the_settings_page_saves_the_night_window_and_refuses_a_time_that_is_not_one(tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(str(tmp_path / "s"))
    client = TestClient(app)
    cfg = app.state.platform.config
    r = client.put("/settings/values", json={"values": {
        "night_window_start": "22:30", "night_window_end": "6:15", "night_max_at_once": 1,
        "night_task_minutes": 45, "night_card_wait_minutes": 5,
    }})
    assert r.status_code == 200, r.text
    assert (cfg.night_window_start, cfg.night_window_end) == ("22:30", "06:15")
    assert (cfg.night_max_at_once, cfg.night_task_minutes, cfg.night_card_wait_minutes) == (1, 45, 5)
    bad = client.put("/settings/values", json={"values": {"night_window_start": "25:00"}})
    assert bad.status_code == 400 and "time of day" in bad.text
    assert cfg.night_window_start == "22:30"
    listed = client.get("/build/night").json()
    assert listed["window"]["start"] == "22:30" and listed["limits"] == {
        "at_once": 1, "task_minutes": 45, "card_wait_minutes": 5,
    }


# --------------------------------------------------------------------------- #
# 2. the Tonight routes
# --------------------------------------------------------------------------- #


async def test_a_task_is_queued_listed_moved_edited_and_cancelled(app, repo, tmp_path):
    status, first = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "fix the tests"})
    assert status == 201, first
    assert first["state"] == N.QUEUED and first["folder"] == str(repo)
    _, second = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "write the docs"})
    status, listed = await asgi_get(app, "/build/night")
    assert status == 200
    assert [t["words"] for t in listed["tasks"]] == ["fix the tests", "write the docs"]
    assert listed["limits"] == {"at_once": 2, "task_minutes": 90, "card_wait_minutes": 15}
    assert listed["window"]["start"] == "23:00" and listed["window"]["words"]
    # Move the second up: it starts first now.
    status, moved = await asgi(app, "POST", f"/build/night/{second['id']}/move", {"direction": "up"})
    assert status == 200 and [t["words"] for t in moved["queued"]] == ["write the docs", "fix the tests"]
    # Edit a queued task (partial).
    status, edited = await asgi(app, "PATCH", f"/build/night/{first['id']}", {"words": "fix the failing tests"})
    assert status == 200 and edited["words"] == "fix the failing tests" and edited["folder"] == str(repo)
    # Cancel a queued one: it never starts, and says so.
    status, out = await asgi(app, "POST", f"/build/night/{first['id']}/cancel", {})
    assert status == 200 and out["task"]["state"] == N.STOPPED
    assert out["task"]["notes"][0]["text"] == N.CANCELLED_BEFORE
    # An ended task cannot be edited; it can be removed.
    status, refused = await asgi(app, "PATCH", f"/build/night/{first['id']}", {"words": "x"})
    assert status == 409 and refused["detail"] == N.ENDED_EDIT
    status, _ = await asgi(app, "DELETE", f"/build/night/{first['id']}")
    assert status == 200
    _, listed = await asgi_get(app, "/build/night")
    assert [t["id"] for t in listed["tasks"]] == [second["id"]]


async def test_a_folder_that_is_not_a_git_repository_is_refused_in_words(app, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    status, out = await asgi(app, "POST", "/build/night", {"folder": str(plain), "words": "do it"})
    assert status == 400
    assert out["detail"].startswith("A night task works on its own branch, so its folder must be in a git repository")
    status, out = await asgi(app, "POST", "/build/night", {"folder": str(tmp_path / "nope"), "words": "do it"})
    assert status == 400 and out["detail"].startswith("This folder cannot be used:")
    _, listed = await asgi_get(app, "/build/night")
    assert listed["tasks"] == []


async def test_the_model_choices_are_checked_in_words(app, repo, monkeypatch):
    monkeypatch.setattr(night_routes, "_menu_pairs", lambda d: {("anthropic", "claude-sonnet-4-5"), ("openai", "gpt-x")})
    ok, task = await asgi(app, "POST", "/build/night", {
        "folder": str(repo), "words": "w", "provider": "anthropic", "model": "claude-sonnet-4-5",
    })
    assert ok == 201 and task["provider"] == "anthropic"
    status, out = await asgi(app, "POST", "/build/night", {
        "folder": str(repo), "words": "w", "provider": "anthropic", "model": "claude-imaginary",
    })
    assert status == 400 and out["detail"] == night_routes.MODEL_UNKNOWN.format(name="anthropic · claude-imaginary")
    status, out = await asgi(app, "POST", "/build/night", {
        "folder": str(repo), "words": "w", "try_on": [{"provider": "openai", "model": "gpt-x"}],
    })
    assert status == 400 and out["detail"] == night_routes.TRY_ON_COUNT.format(n=1)
    status, out = await asgi(app, "POST", "/build/night", {
        "folder": str(repo), "words": "w", "team": True,
        "try_on": [{"provider": "openai", "model": "gpt-x"}, {"provider": "anthropic", "model": "claude-sonnet-4-5"}],
    })
    assert status == 400 and out["detail"] == night_routes.TEAM_AND_TRY_ON
    status, out = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "w", "team": True})
    assert status == 400 and out["detail"] == night_routes.NO_TEAM


# --------------------------------------------------------------------------- #
# 3. the posture and the outcome
# --------------------------------------------------------------------------- #


def test_the_night_posture_is_approve_for_me_with_the_command_tools_and_the_tasks_minutes(tmp_path):
    cfg = Config(home=tmp_path, project_root=tmp_path, night_task_minutes=45)
    out = N.posture_setup({"approval_mode": "always_ask", "granted_tools": ["web_fetch"]}, cfg)
    # Review (moved on purpose): "Approve for me" — decision 4's own words —
    # never "Don't ask"; the night's allow-list (``N.RUNS_UNASKED``) rides on
    # top in the stream lane, so a granted web_fetch still asks at night
    # (tests/test_night_shift_review_v1398.py).
    assert out["approval_mode"] == "approve_for_me"
    assert out["granted_tools"] == ["web_fetch", "shell", "process_start", "process_write"]
    assert out["cap"] == {"minutes": 45}
    # A shorter cap the session has is kept; a dollar cap rides along.
    out = N.posture_setup({"cap": {"minutes": 20, "usd": 3}}, cfg)
    assert out["cap"] == {"minutes": 20, "usd": 3.0}
    # Nothing is changed in place (the session keeps its own setup).
    original = {"approval_mode": "approve_for_me"}
    N.posture_setup(original, cfg)
    assert original == {"approval_mode": "approve_for_me"}


@pytest.mark.parametrize("line,notes,state", [
    ({"role": "assistant", "content": "Done."}, [], N.DONE),
    ({"role": "assistant", "content": "Done."}, [{"kind": "declined", "text": "x"}], N.NEEDS_YOU),
    ({"role": "assistant", "content": "", "error": "The model failed."}, [], N.FAILED),
    ({"role": "assistant", "content": "so far", "capMet": {"kind": "time", "limit": 90}}, [], N.STOPPED),
    ({"role": "assistant", "content": "part", "interrupted": True}, [], N.STOPPED),
    (None, [], N.STOPPED),
    ({"role": "assistant", "content": "Done."}, [{"kind": "emergency", "text": N.EMERGENCY_NOTE}], N.STOPPED),
])
def test_how_a_task_ended_is_read_from_its_reply_line(line, notes, state):
    got, kept = N.outcome_of({"notes": notes}, line)
    assert got == state
    if line and line.get("capMet"):
        assert kept == [("limit", "Stopped at its 90-minute limit; the reply says what it got done.")]


def test_an_event_handler_never_runs_as_the_night_tasks_turn():
    """A handler of an event published inside a night turn runs in a copy of
    its context: the night task is dropped there (like the hold and the pin),
    so work a handler starts never takes the night's card wait."""
    import contextvars

    from iron_jarvis.core import events

    token = N.set_turn(N.NightTurn(task_id="t", card_wait_s=1.0))
    try:
        def handler():
            events._drop_build_turn_scope()
            return N.current()

        assert contextvars.copy_context().run(handler) is None
        assert N.current() is not None
    finally:
        N.reset_turn(token)


def test_low_trust_reaches_the_record():
    _state, kept = N.outcome_of({"notes": []}, {
        "role": "assistant", "content": "Done.", "trust": "low", "trustReason": "a fetched page was flagged",
    })
    assert kept and kept[0][0] == "low_trust" and "a fetched page was flagged" in kept[0][1]


# --------------------------------------------------------------------------- #
# 4. the phone: an OPEN card's answer is never refused for its age
# --------------------------------------------------------------------------- #


async def test_a_phone_answer_to_a_card_still_open_after_five_minutes_is_taken(platform):
    from iron_jarvis.comm.models import PendingPromptRecord
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import utcnow
    from tests.test_comm_approvals_v1200 import (
        CHAT_CFG,
        ChatMockChannel,
        FakeApprovals,
        _msg,
        _poller,
        _prompt_status,
        _register_approval,
    )

    class OpenApprovals(FakeApprovals):
        def pending_ids(self) -> list[str]:
            return ["apr_1"]

    fake = OpenApprovals()
    platform.approvals = fake
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _o, _t, pstore, _n = _poller(platform, ch)
    rec = _register_approval(pstore)
    with session_scope(platform.engine) as db:
        row = db.get(PendingPromptRecord, rec.id)
        row.created_at = utcnow() - timedelta(seconds=11 * 60)  # a night card waits 15
        db.add(row)
        db.commit()
    res = await poller._handle("tg", ch, _msg("approve"))
    assert res["status"] == "approval_answered", res
    assert fake.calls == [("apr_1", "once")]
    assert _prompt_status(pstore, rec.id) != "expired"


# --------------------------------------------------------------------------- #
# 5. "Runs at once" counts a night task
# --------------------------------------------------------------------------- #


def test_a_night_task_holds_one_runs_at_once_slot(orchestrator):
    orchestrator.p.config.max_concurrent_sessions = 1
    assert orchestrator.try_hold_slot("night:a") is True
    assert orchestrator.try_hold_slot("night:b") is False, "the one slot is held"
    assert "night:a" in orchestrator._governed  # a parked session run sees it
    orchestrator.release_slot("night:a")
    assert orchestrator.try_hold_slot("night:b") is True
    orchestrator.p.config.max_concurrent_sessions = 0
    assert orchestrator.try_hold_slot("night:c") is True  # no cap: always (and counted)


# --------------------------------------------------------------------------- #
# 6. a branch folder borrows the main folder's environment (decision 3)
# --------------------------------------------------------------------------- #


def _fake_venv(folder: Path) -> Path:
    scripts = folder / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir(parents=True)
    (scripts / ("python.exe" if os.name == "nt" else "python")).write_bytes(b"")
    (folder / "node_modules" / ".bin").mkdir(parents=True)
    return scripts


def test_a_branch_folder_finds_the_main_folders_venv_and_node_modules_and_makes_nothing(tmp_path):
    main = tmp_path / "proj"
    sub = main / "app"
    sub.mkdir(parents=True)
    scripts = _fake_venv(main)
    work = tmp_path / "proj.ij-worktrees" / "ij-x" / "app"
    work.mkdir(parents=True)
    before = sorted(p.name for p in work.parent.rglob("*"))
    found = ME.find(str(sub), str(work), repo=str(main), worktree=str(work.parent))
    assert found is not None
    assert found.venv == str(main / ".venv") and found.venv_bin == str(scripts)
    assert found.node_bin == str(main / "node_modules" / ".bin")
    env = found.apply({"PATH": "C:\\Windows", "PYTHONHOME": "x"})
    assert env["PATH"].split(os.pathsep)[:2] == [str(scripts), str(main / "node_modules" / ".bin")]
    assert env["VIRTUAL_ENV"] == str(main / ".venv") and "PYTHONHOME" not in env
    # Never a link (or anything) inside the worktree.
    assert sorted(p.name for p in work.parent.rglob("*")) == before
    # A branch folder with its own .venv keeps it.
    _fake_venv(work.parent)
    assert ME.find(str(sub), str(work), repo=str(main), worktree=str(work.parent)) is None


def test_the_build_env_borrows_only_inside_a_branch_turn(tmp_path):
    from iron_jarvis.sandbox.native import build_env, build_turn_env

    main = tmp_path / "proj"
    main.mkdir()
    scripts = _fake_venv(main)
    work = tmp_path / "wt"
    work.mkdir()
    plain = build_env()
    assert build_turn_env() == plain, "no branch turn: exactly the Build env"
    token = B.set_turn_branch(
        {"worktree": str(work), "repo": str(main), "branch": "ij/x", "checkout": str(main)}, str(work),
    )
    try:
        env = build_turn_env()
    finally:
        B.reset_turn_branch(token)
    key = next(k for k in env if k.upper() == "PATH")
    assert env[key].split(os.pathsep)[0] == str(scripts)
    assert env["VIRTUAL_ENV"] == str(main / ".venv")


def test_the_finish_note_says_the_tests_ran_with_the_main_folders_environment(tmp_path):
    found = ME.MainEnv(venv="C:\\p\\.venv", venv_bin="C:\\p\\.venv\\Scripts")
    line = ME.tests_line(found)
    assert line.startswith("These ran in this branch folder with your main folder's environment (C:\\p\\.venv)")
    assert ME.tests_line(None) == ""


# --------------------------------------------------------------------------- #
# 7. Emergency stop covers coding sessions
# --------------------------------------------------------------------------- #


async def test_under_emergency_stop_a_turn_iron_jarvis_would_start_by_itself_is_refused(app, tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    sid = new_session(app, work)["id"]
    app.state.platform.config.autonomy_kill_switch = True
    for by in ("night", "wait", "restart"):
        with pytest.raises(build_routes.HTTPException) as err:
            await build_routes.start_build_turn(app.state.d, sid, "go on", f"bt-{by}", started_by=by)
        assert err.value.status_code == 409 and err.value.detail == build_routes.UNATTENDED_REFUSAL
    # A person's turn is a person's: it starts.
    wire(app, Script(["Hello."]))
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": "bt-person"})
    assert status == 200 and "event: done" in raw


async def test_emergency_stop_stops_a_coding_turn_iron_jarvis_started_and_leaves_a_persons(app, tmp_path):
    from tests.test_build_resume_v1391 import Hold

    work = tmp_path / "w"
    work.mkdir()
    auto_sid = new_session(app, work)["id"]
    person_sid = new_session(app, work)["id"]
    hold_auto, hold_person = Hold(), Hold()

    class TwoScripts(Script):
        async def _next(self, system, messages, tools):
            text = " ".join(str(getattr(m, "content", "")) for m in messages)
            return await (hold_auto if "AUTO" in text else hold_person)(self)

    wire(app, TwoScripts([]))
    lg = await build_routes.start_build_turn(app.state.d, auto_sid, "AUTO carry on", "bt-auto", started_by="wait")
    person = asyncio.create_task(open_turn(app, person_sid, {"text": "PERSON hi", "turn_id": "bt-me"}, timeout=60))
    await asyncio.wait_for(hold_auto.reached.wait(), 30)
    await asyncio.wait_for(hold_person.reached.wait(), 30)
    assert "bt-auto" in N.unattended_turns() and "bt-me" not in N.unattended_turns()

    orch = app.state.platform.orchestrator
    app.state.platform.config.autonomy_kill_switch = True
    stopped = await asyncio.to_thread(orch.stop_unattended)
    assert "turn:bt-auto" in stopped and "turn:bt-me" not in stopped
    await until(lambda: lg.status != "running", 30)
    assert lg.status != "running", "Emergency stop stopped the turn Iron Jarvis started"
    assert "Never said." not in json.dumps(lg.frames)
    hold_auto.gate.set()
    logs = app.state.platform.turn_logs
    assert logs.get("bt-me").status == "running", "a person's turn keeps going"
    hold_person.gate.set()
    await asyncio.wait_for(person, 60)
    await until(lambda: "bt-auto" not in N.unattended_turns(), 10)


async def test_under_emergency_stop_a_cut_off_reply_is_not_carried_on_after_a_restart(tmp_path):
    from tests.test_build_resume_v1391 import _cut_off_mid_reply, _reboot

    home = str(tmp_path / "home")
    work = tmp_path / "work"
    harness = work / ".ironjarvis" / "harness"
    harness.mkdir(parents=True)
    (harness / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
    first = create_app(home)
    sid = new_session(first, work)["id"]
    await _cut_off_mid_reply(first, sid)
    second = _reboot(home)
    second.state.platform.config.autonomy_kill_switch = True
    wire(second, Script(["Never."]))
    # The pass does not even try (the turn's own refusal is the second guard).
    tried: list = []
    real_start = build_routes.start_build_turn

    async def spy(*a, **kw):
        tried.append(kw.get("started_by"))
        return await real_start(*a, **kw)

    build_routes.start_build_turn = spy
    try:
        assert await build_routes.resume_after_restart(second.state.d) == []
    finally:
        build_routes.start_build_turn = real_start
    assert tried == []
    # Kept: with the stop off, the next boot may carry it on (Retry stays).
    from iron_jarvis.harness import reply_record as RR

    assert RR.kept(second.state.platform.engine)


async def test_a_card_outside_a_night_task_still_waits_with_no_clock(app, tmp_path, monkeypatch):
    """Every other session is byte-identical: its card says no expiry and is
    never declined by the night's wait."""
    from iron_jarvis.daemon.routes import chat as chat_routes
    from iron_jarvis.providers.adapters.base import ToolCall
    from tests.test_build_sessions_v1367 import turn_frames

    monkeypatch.setattr(N, "card_wait_seconds", lambda config: 0.2)
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    work = tmp_path / "w"
    work.mkdir()
    sid = new_session(app, work)["id"]
    wire(app, Script([[ToolCall(id="s1", name="Bash", arguments={"command": "echo hi"})], "ok"]))
    drop = asyncio.Event()
    follower = asyncio.create_task(open_turn(app, sid, {"text": "run it", "turn_id": "bt-day"}, drop=drop))
    card = await until(lambda: [d for e, d in turn_frames(app, "bt-day") if e == "approval"])
    assert card[0]["timeout_s"] == 0
    await asyncio.sleep(1.0)
    assert not [d for e, d in turn_frames(app, "bt-day") if e == "approval_resolved"], "still waiting"
    assert N.current() is None
    assert (await asgi(app, "POST", f"/chat/approvals/{card[0]['id']}", {"decision": "deny"}))[0] == 200
    lg = app.state.platform.turn_logs.get("bt-day")
    await until(lambda: lg.status != "running", 30)
    drop.set()
    await follower


# --------------------------------------------------------------------------- #
# 8. keep-awake
# --------------------------------------------------------------------------- #


async def test_system_activity_says_night_shift_while_a_task_is_queued_in_the_window(app, repo, monkeypatch):
    status, _task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "w"})
    assert status == 201
    monkeypatch.setattr(N, "CLOCK", lambda: _local(15, 0))
    _, act = await asgi_get(app, "/system/activity")
    assert act["night_shift"] is False and act["working"] is False
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    _, act = await asgi_get(app, "/system/activity")
    assert act["night_shift"] is True and act["working"] is False, "working keeps its own meaning"
    app.state.platform.config.autonomy_kill_switch = True
    _, act = await asgi_get(app, "/system/activity")
    assert act["night_shift"] is False, "nothing would start under Emergency stop"


def _js_fn(name: str) -> str:
    src = _MAIN_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = src.index(f"function {name}(")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_desktop_keeps_the_pc_awake_for_night_shift(tmp_path):
    src = _MAIN_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
    decl = next(line for line in src.splitlines() if line.startswith("const KEEP_AWAKE_MISSES_RELEASE"))
    script = "\n".join([
        decl,
        _js_fn("keepAwakeDecision"),
        "const s = { id: null, misses: 0 };",
        "const out = {",
        " night: keepAwakeDecision({ working: false, busy: true, keep_awake: true, night_shift: true }, s).want,",
        " idle: keepAwakeDecision({ working: false, busy: false, keep_awake: true, night_shift: false }, s).want,",
        " off: keepAwakeDecision({ working: false, keep_awake: false, night_shift: true }, s).want,",
        " older: keepAwakeDecision({ working: true, keep_awake: true }, s).want,",
        "};",
        "console.log(JSON.stringify(out));",
    ])
    f = tmp_path / "k.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == {
        "night": True, "idle": False, "off": False, "older": True,
    }


# --------------------------------------------------------------------------- #
# 9. a helper's ask in a night task, the checkers, the governor, the team, try on
# --------------------------------------------------------------------------- #

from tests.test_approvals_office_v1247 import _events, _session, _tc  # noqa: E402


@pytest.fixture()
def rt_night(tmp_path):
    """The approvals-office runtime fixture's shape: a real platform, its bus
    spied on, an AgentRuntime."""
    from types import SimpleNamespace

    from iron_jarvis.agents.runtime import AgentRuntime

    app = create_app(str(tmp_path / "rt"))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": str(getattr(type, "value", type)), "payload": payload or {},
                          "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(runtime=AgentRuntime(platform), platform=platform, published=published, app=app)


async def test_a_helpers_ask_in_a_night_task_waits_the_nights_wait_then_is_declined(rt_night):
    rt = rt_night
    from iron_jarvis.agents.types import get_agent_definition
    from iron_jarvis.core.models import AgentType

    task = N.add_task(rt.platform.engine, folder="C:\\x", words="w")
    token = N.set_turn(N.NightTurn(task_id=task["id"], card_wait_s=0.2, engine=rt.platform.engine))
    try:
        # Bounded: the attended no-clock wait would hold the test forever.
        deny, extra = await asyncio.wait_for(rt.runtime._pause_for_approval(
            _session("job:build-helper"), _tc("echo a"), get_agent_definition(AgentType.BUILDER), set(),
        ), 20)
    finally:
        N.reset_turn(token)
    assert deny.startswith("left for the user: run a command — nobody answered in 1 minute") and extra == set()
    assert _events(rt, "approval.requested")[0]["payload"]["timeout_s"] == 1
    N.flush_notes()
    notes = N.get_task(rt.platform.engine, task["id"])["notes"]
    assert [n["kind"] for n in notes] == ["declined"] and notes[0]["text"].startswith("Needs your OK: run a command")


def test_the_checkers_find_the_main_folders_programs_in_a_branch_turn(tmp_path):
    from iron_jarvis.harness import checkers as CK

    main = tmp_path / "proj"
    main.mkdir()
    scripts = _fake_venv(main)
    ruff = scripts / ("ruff.exe" if os.name == "nt" else "ruff")
    ruff.write_bytes(b"")
    if os.name != "nt":
        ruff.chmod(0o755)
    work = tmp_path / "wt"
    work.mkdir()
    (work / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    token = B.set_turn_branch(
        {"worktree": str(work), "repo": str(main), "branch": "ij/x", "checkout": str(main)}, str(work),
    )
    try:
        found = CK.detect(work, path_env=CK._session_path())
    finally:
        B.reset_turn_branch(token)
    got = [c for c in found if c.name == "ruff"]
    assert got and Path(got[0].program).parent == scripts


async def test_a_night_turn_past_its_limit_and_the_grace_is_stopped_by_the_loop(app, tmp_path):
    from tests.test_build_resume_v1391 import Hold

    work = tmp_path / "w"
    work.mkdir()
    sid = new_session(app, work)["id"]
    engine = app.state.platform.engine
    task = N.add_task(engine, folder=str(work), words="hold on")
    assert N.mark_running(engine, task["id"])
    N.set_session(engine, task["id"], thread_id=sid)
    hold = Hold()
    wire(app, Script([hold]))
    lg = await build_routes.start_build_turn(app.state.d, sid, "hold on", "bt-long", started_by="night")
    await asyncio.wait_for(hold.reached.wait(), 30)
    # Within its time: the loop leaves it alone — even when the TASK began
    # hours ago (a reply carried on after a restart: the time off is never
    # counted, only this turn's own).
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.ids import utcnow

    with session_scope(engine) as db:
        rec = db.get(N.NightTaskRecord, task["id"])
        rec.started_at = utcnow() - timedelta(hours=8)
        db.add(rec)
        db.commit()
    assert await night_routes.settle_running(app.state.d) == []
    from iron_jarvis.core.turns import TURNS

    assert TURNS.handle("bt-long").stopped is False, "nobody asked it to stop"
    assert lg.status == "running"
    # Past its limit and the grace (the turn's OWN time): stopped, and said.
    lg.started_at = lg.started_at - timedelta(minutes=90) - timedelta(seconds=night_routes.OVERRUN_GRACE_S + 60)
    await night_routes.settle_running(app.state.d)
    await until(lambda: lg.status != "running", 30)
    assert lg.status != "running"
    hold.gate.set()
    notes = N.get_task(engine, task["id"])["notes"]
    assert any(n["kind"] == "limit" and "ran past its 90-minute limit" in n["text"] for n in notes)


async def test_a_full_runs_at_once_cap_starts_no_night_task(app, repo, monkeypatch):
    status, task = await asgi(app, "POST", "/build/night", {"folder": str(repo), "words": "w"})
    assert status == 201
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    orch = app.state.platform.orchestrator
    app.state.platform.config.max_concurrent_sessions = 1
    assert orch.try_hold_slot("someone-else")
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [] and N.get_task(app.state.platform.engine, task["id"])["state"] == N.QUEUED
    orch.release_slot("someone-else")


async def test_the_team_rides_the_models_message_and_try_on_starts_attempts(app, repo, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import build_attempts
    from tests.test_build_sessions_v1367 import messages_of

    engine = app.state.platform.engine
    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    team = N.add_task(engine, folder=str(repo), words="TEAM: split the parser", team=True)
    script = wire(app, Script(["Done."]))
    out = await night_routes.night_pass(app.state.d)
    assert out["started"] == [team["id"]]
    await until(lambda: script.calls, 30)
    sent = [str(getattr(m, "content", "")) for m in script.calls[0]["messages"] if getattr(m, "role", "") == "user"]
    assert sent[-1].startswith("TEAM: split the parser") and night_routes.TEAM_NOTE in sent[-1]
    started = N.get_task(engine, team["id"])
    await until(lambda: any(m.get("role") == "assistant" for m in messages_of(app, started["thread_id"])), 30)
    question = next(m for m in messages_of(app, started["thread_id"]) if m["role"] == "user")
    assert question["content"] == "TEAM: split the parser", "the question line shows the user's words alone"

    # Try on: the attempts' own start, as Iron Jarvis's (started_by night).
    seen: list = []

    async def fake_maybe_start(d, thread_id, text, turn_id, body, *, started_by="person"):
        seen.append((thread_id, text, [(p.provider, p.model) for p in body.try_on], started_by))
        return SimpleNamespace(turn_id=turn_id, task=None)

    monkeypatch.setattr(build_attempts, "maybe_start", fake_maybe_start)
    repo2 = make_repo(repo.parent / "proj2")
    tried = N.add_task(engine, folder=str(repo2), words="TRY: fix it",
                       try_on=[{"provider": "anthropic", "model": "a"}, {"provider": "openai", "model": "b"}])
    out = await night_routes.night_pass(app.state.d)
    assert tried["id"] in out["started"]
    assert seen and seen[0][1] == "TRY: fix it" and seen[0][2] == [("anthropic", "a"), ("openai", "b")]
    assert seen[0][3] == "night"
