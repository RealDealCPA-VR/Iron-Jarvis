"""v1.409.0 — the independent review of Build's Code view (R1–R10 and the
lows), each finding pinned against the real routes, service and parser.

R1 Codex's "Allow for this session" only when Codex offers it · R2 one
harness conversation never runs in two processes · R3 continue-on / delete /
archive hold a claim, and a stop always ends its turn · R4 one card, one
answer · R5 the full log is not rewritten on every flush (see
test_build_app_v1409) · R6 last_error is on the row when done arrives · R7 a
key split across deltas is masked (see test_build_app_v1409) · R8 nothing a
harness started outlives it · R9 continue-on refuses before Iron-Proxy is
told · R10 the tray names a waiting Code-view session.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.codex import CodexDriver
from tests._build_app_helpers_v1409 import (
    FIX,
    client,
    frames,
    new_session,
    sent,
    starts,
    stop_all,
    until,
    use_fake,
    wait_pending,
)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def _write_claude(home: Path, folder: Path, sid: str, text: str = "hi") -> Path:
    path = home / "projects" / "C--demo" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "sessionId": sid, "cwd": str(folder),
                                "timestamp": "2026-10-29T10:00:00Z",
                                "message": {"role": "user", "content": text}}) + "\n", encoding="utf-8")
    return path


def _write_codex(home: Path, folder: Path, tid: str) -> Path:
    path = home / "sessions" / "2026" / "10" / "29" / f"rollout-2026-10-29T10-00-00-{tid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timestamp": "2026-10-29T10:00:00Z", "type": "session_meta",
                                "payload": {"id": tid, "cwd": str(folder), "timestamp": "2026-10-29T10:00:00Z"}})
                    + "\n", encoding="utf-8")
    return path


class _Pane:
    def __init__(self, cwd, tail, cli="claude", sid="11111111-2222-3333-4444-555555555555",
                 accounts=None, created_at=None, alive=True, pane_id="pane1"):
        self.id = pane_id
        self.cwd = str(cwd)
        self.shell = "pwsh.exe"
        self.agent_cli = cli
        self.resume_cli = None
        self.claude_session_id = sid
        self.accounts = accounts or {}
        self.created_at = created_at or datetime.now(timezone.utc) - timedelta(minutes=5)
        self.alive = alive
        self._tail = tail

    def output_tail(self):
        return self._tail


EXITED = "Bye!\nPS C:\\work> "
RUNNING = "────────────────\n> fix it\n? for shortcuts"


def _panes(app, monkeypatch, panes: dict):
    monkeypatch.setattr(app.state.platform.terminals, "get", lambda pid: panes.get(pid))
    monkeypatch.setattr(app.state.platform.terminals, "list",
                        lambda: [{"id": k, "alive": p.alive, "agent_cli": p.agent_cli} for k, p in panes.items()])


# --------------------------------------------------------------------------- #
# R1
# --------------------------------------------------------------------------- #
async def test_r1_codex_session_allow_only_when_codex_offers_it():
    recorded = json.loads((FIX / "codex_allow.json").read_text(encoding="utf-8"))
    req = next(s["out"] for s in recorded["steps"]
               if (s.get("out") or {}).get("method") == "item/commandExecution/requestApproval")
    assert "acceptForSession" not in req["params"]["availableDecisions"]  # the recorded fact
    sent_: list = []

    async def _w(obj):
        sent_.append(obj)

    asks: list = []
    drv = CodexDriver(write=_w, emit=lambda e: None, ask=asks.append, on_session_id=lambda s: None,
                      mode="ask", cwd="C:\\work\\demo")
    drv.on_message(req)
    assert asks[0].event["can_session"] is False and asks[0].event["once_only"] is True
    from iron_jarvis.hosted.driver import AskRefused

    with pytest.raises(AskRefused):
        await drv.answer(asks[0], "allow_session", "", None)
    assert sent_ == [], "acceptForSession was sent although Codex did not offer it"
    offered = json.loads(json.dumps(req))
    offered["params"]["availableDecisions"] = ["accept", "acceptForSession", "decline"]
    drv.on_message(offered)
    assert asks[1].event["can_session"] is True
    await drv.answer(asks[1], "allow_session", "", None)
    assert sent_[-1]["result"] == {"decision": "acceptForSession"}


# --------------------------------------------------------------------------- #
# R2
# --------------------------------------------------------------------------- #
async def test_r2_one_conversation_is_one_row_and_one_process(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    hid = "f15bf227-950c-49cb-bfb0-a77b94dacbbf"
    _write_claude(home, work, hid)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, work, harness_session_id=hid)
        r = await c.post("/build/app/sessions", json={"harness": "claude-code", "folder": str(work),
                                                      "harness_session_id": hid})
        assert r.status_code == 409 and "already open" in r.json()["detail"]
        # A second row on the same conversation (made before this check):
        # it never starts a process while the first one runs.
        b = store.create(app.state.platform.engine, harness="claude-code", folder=str(work),
                         harness_session_id=hid)
        ta = asyncio.ensure_future(c.post(f"/build/app/sessions/{a['id']}/turn", json={"text": "x", "turn_id": "ta"}))
        ask = (await wait_pending(c, a["id"]))[0]
        r = await c.post(f"/build/app/sessions/{b.id}/turn", json={"text": "x", "turn_id": "tb"})
        assert r.status_code == 409 and "already open" in r.json()["detail"]
        assert b.id not in svc.live or not svc.live[b.id].running
        await c.post(f"/build/app/sessions/{a['id']}/answer", json={"request_id": ask["request_id"], "decision": "deny"})
        await asyncio.wait_for(ta, 30)
    await stop_all(app)


async def test_r2_a_build_terminal_running_the_conversation_holds_it(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    hid = "11111111-2222-3333-4444-555555555555"
    pane = _Pane(work, RUNNING, sid=hid)
    _panes(app, monkeypatch, {"pane1": pane})
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), harness_session_id=hid)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/turn", json={"text": "x", "turn_id": "t"})
        assert r.status_code == 409 and "Build terminal" in r.json()["detail"]
        pane._tail = EXITED
        evs = frames((await c.post(f"/build/app/sessions/{row.id}/turn", json={"text": "x", "turn_id": "t2"})).text)
        assert evs[-1]["type"] == "done"
    await stop_all(app)


async def test_r2_open_in_terminal_holds_the_row_until_that_cli_exits(app, work, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import terminals as T

    panes: dict = {}

    def launch(platform, body, **kw):
        panes["term_1"] = _Pane(work, RUNNING, sid=kw.get("claude_session_id"), pane_id="term_1")
        return SimpleNamespace(id="term_1")

    monkeypatch.setattr(T, "launch_pane", launch)
    _panes(app, monkeypatch, panes)
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "ready?", "turn_id": "t1"})
        await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
        assert (await c.post(f"/build/app/sessions/{s['id']}/open-terminal")).json() == {"terminal_id": "term_1"}
        assert store.get(app.state.platform.engine, s["id"]).terminal_id == "term_1"
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "more", "turn_id": "t2"})
        assert r.status_code == 409 and "/exit" in r.json()["detail"]
        assert not svc.live[s["id"]].running, "a second process resumed the session the terminal runs"
        panes["term_1"]._tail = EXITED  # the user typed /exit there
        evs = frames((await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "more", "turn_id": "t3"})).text)
        assert evs[-1]["type"] == "done"
        assert store.get(app.state.platform.engine, s["id"]).terminal_id == ""
    assert len(starts(log)) == 2
    await stop_all(app)


async def test_r2_from_terminal_reuses_the_row_and_reads_the_panes_own_codex_home(app, work, tmp_path, monkeypatch):
    acc_home = tmp_path / "acc-codex"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "default-codex"))
    tid = "01a189c4-9316-7d30-b03e-8a6c95d7bacd"
    _write_codex(acc_home, work, tid)
    _write_codex(tmp_path / "default-codex", work, "01a189c4-0000-7d30-b03e-8a6c95d7bacd")
    pane = _Pane(work, EXITED, cli="codex", sid=None,
                 accounts={"openai": {"id": "o1", "title": "Plus", "source": "iron-proxy",
                                      "home": str(acc_home), "unset": []}})
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["harness"] == "codex" and made["harness_session_id"] == tid and made["account"] == "o1"
        again = (await c.post("/build/app/from-terminal/p")).json()
        assert again["id"] == made["id"] and again["reused"] is True
        _write_codex(acc_home, work, "01a189c4-1111-7d30-b03e-8a6c95d7bacd")
        store.update(app.state.platform.engine, made["id"], archived_at=datetime.now(timezone.utc))
        store.delete(app.state.platform.engine, made["id"])
        r = await c.post("/build/app/from-terminal/p")
        assert r.status_code == 409 and "cannot tell" in r.json()["detail"], "never a guess among two threads"


async def test_from_terminal_checks_the_folder(app, tmp_path, monkeypatch):
    _panes(app, monkeypatch, {"p": _Pane(tmp_path / "gone", EXITED)})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
    assert r.status_code == 400 and r.json()["detail"]


# --------------------------------------------------------------------------- #
# R3
# --------------------------------------------------------------------------- #
async def test_r3_a_message_during_continue_on_is_refused_and_a_stop_ends_its_turn(app, work, tmp_path, monkeypatch):
    from iron_jarvis.iron_proxy import accounts as _acc
    from iron_jarvis.terminals import pane_accounts as PA

    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work),
                       harness_session_id="f15bf227-950c-49cb-bfb0-a77b94dacbbf")
    lease = _acc.make_lease(PA.SPECS["anthropic"], "p2", "Two", str(tmp_path / "h2"),
                            sorted(PA.PANE_OWN_KEYS["anthropic"]))
    gate = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_plan(self, row, account, limit, since):
        asyncio.run_coroutine_threadsafe(_wait(gate), loop).result(10)
        return lease, Path("x"), None, "p1"

    async def _wait(ev):
        await ev.wait()

    monkeypatch.setattr(S.HostedService, "_continue_blocking", slow_plan)
    monkeypatch.setattr(S.HostedService, "_carry", lambda self, *a: None)
    async with client(app) as c:
        cont = asyncio.ensure_future(c.post(f"/build/app/sessions/{row.id}/continue-on", json={}))
        await asyncio.sleep(0.2)
        r = await c.post(f"/build/app/sessions/{row.id}/turn", json={"text": "x", "turn_id": "t1"})
        assert r.status_code == 409 and "moving to another account" in r.json()["detail"]
        gate.set()
        assert (await cont).status_code == 200
    # A stop ALWAYS ends the running turn and closes its cards.
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{row.id}/turn", json={"text": "x", "turn_id": "t2"}))
        await wait_pending(c, row.id)
        await svc._stop_process(svc.live[row.id], why="testing")
        evs = frames((await asyncio.wait_for(task, 15)).text)
    assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}
    assert any(e["type"] == "resolved" and e["decision"] == "expired" for e in evs)
    await stop_all(app)


# --------------------------------------------------------------------------- #
# R4
# --------------------------------------------------------------------------- #
async def test_r4_one_card_is_answered_once(app, work):
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work))
    svc = S.service_for(app.state.platform)
    live = svc._live(row)
    told = []

    class _Driver:
        async def answer(self, ask, decision, message, answers):
            await asyncio.sleep(0.2)
            told.append(decision)
            return decision

    live.driver = _Driver()
    live.asks["ask_x"] = S.Ask("ask_x", "approval", "shell", native=1, can_session=False)
    got = await asyncio.gather(svc.answer(row.id, "ask_x", "allow"), svc.answer(row.id, "ask_x", "deny"),
                               return_exceptions=True)
    assert len(told) == 1, f"the harness was told {told} for one card ({got})"
    assert sum(isinstance(g, S.Refused) and g.status == 404 for g in got) == 1


async def test_r4_a_refused_answer_puts_the_card_back(app, work):
    from iron_jarvis.hosted.driver import AskRefused

    row = store.create(app.state.platform.engine, harness="codex", folder=str(work))
    svc = S.service_for(app.state.platform)
    live = svc._live(row)

    class _Driver:
        async def answer(self, ask, decision, message, answers):
            raise AskRefused("Choose an answer for the question first.")

    live.driver = _Driver()
    live.asks["ask_q"] = S.Ask("ask_q", "question", "request_user_input", native=1)
    with pytest.raises(S.Refused):
        await svc.answer(row.id, "ask_q", "allow")
    assert "ask_q" in live.asks, "a refused answer lost the card"


# --------------------------------------------------------------------------- #
# R6
# --------------------------------------------------------------------------- #
async def test_r6_last_error_is_on_the_row_when_done_arrives(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_signin", tmp_path / "fake.log")
    real = store.update

    def slow_update(engine, sid, **fields):
        if "last_error" in fields:
            time.sleep(0.5)
        return real(engine, sid, **fields)

    monkeypatch.setattr(store, "update", slow_update)
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t1"})
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    await stop_all(app)
    assert row["last_error"].endswith("isn't signed in")


# --------------------------------------------------------------------------- #
# R8
# --------------------------------------------------------------------------- #
def test_r8_the_harness_job_lets_no_member_leave(monkeypatch, tmp_path):
    from iron_jarvis.core import procs
    from iron_jarvis.hosted.process import HarnessProcess

    got = {}

    def spy(argv, **kw):
        got.update(kw)
        raise OSError("not started in this test")

    monkeypatch.setattr(procs, "spawn", spy)
    with pytest.raises(OSError):
        HarnessProcess(["x"], env={}, cwd=str(tmp_path), name="t").start(asyncio.new_event_loop(),
                                                                         lambda o: None, lambda c: None)
    assert got["breakaway_ok"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobs")
def test_r8_a_program_started_through_git_bash_dies_with_the_session(tmp_path):
    from iron_jarvis.hosted.process import HarnessProcess

    bash = next((c for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe")
                 if os.path.exists(c)), None)
    if bash is None:
        pytest.skip("no Git Bash")
    pidf = tmp_path / "pid.txt"
    py = sys.executable.replace("\\", "/")
    script = (f"'{py}' -c \"import os,time;open(r'{pidf}','w').write(str(os.getpid()));time.sleep(60)\" & "
              "sleep 30")
    proc = HarnessProcess([bash, "-c", script], env=dict(os.environ), cwd=str(tmp_path), name="review")
    loop = asyncio.new_event_loop()
    try:
        proc.start(loop, lambda o: None, lambda c: None)
        end = time.time() + 20
        while not pidf.exists() or not pidf.read_text().strip():
            assert time.time() < end, "the grandchild never started"
            time.sleep(0.1)
        pid = int(pidf.read_text())
        t0 = time.time()
        proc.stop()
        took = time.time() - t0
        assert took < 15, f"stop() blocked {took:.0f} s"
        time.sleep(1.0)
        import psutil

        alive = psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        if alive:
            psutil.Process(pid).kill()
        assert not alive, "a program Claude's Bash tool started outlived the session"
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# R9
# --------------------------------------------------------------------------- #
async def test_r9_continue_on_refuses_before_it_tells_iron_proxy(app, work, tmp_path, monkeypatch):
    from iron_jarvis.terminals.limit_state import PaneLimit
    from tests.test_build_app_pool_v1409 import _Client, _profiles, _Svc

    h1, h2, profiles = _profiles(tmp_path)
    fake = _Client(profiles, {"p1": {"status": "ready"}, "p2": {"status": "ready"}})
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(h1), "unset": []}
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec), harness_session_id="6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21")
    svc = S.service_for(app.state.platform)
    svc._live(row).limit = PaneLimit(line="You've hit your session limit")
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
    assert r.status_code == 409 and "not in this account's folder" in r.json()["detail"]
    assert fake.signals == []
    assert store.get(app.state.platform.engine, row.id).account == "p1"


async def test_r9_a_diverged_copy_is_refused_before_anything_is_told(app, work, tmp_path, monkeypatch):
    from iron_jarvis.terminals.limit_state import PaneLimit
    from tests.test_build_app_pool_v1409 import _Client, _profiles, _Svc

    h1, h2, profiles = _profiles(tmp_path)
    fake = _Client(profiles, {"p1": {"status": "ready"}, "p2": {"status": "ready"}})
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    sid = "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21"
    _write_claude(h1, work, sid, "mine")
    _write_claude(h2, work, sid, "a different one")
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(h1), "unset": []}
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec), harness_session_id=sid)
    S.service_for(app.state.platform)._live(row).limit = PaneLimit(line="You've hit your session limit")
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
    assert r.status_code == 409 and "different copy" in r.json()["detail"]
    assert fake.signals == []


# --------------------------------------------------------------------------- #
# R10
# --------------------------------------------------------------------------- #
def test_r10_the_tray_names_a_waiting_code_view_session(tmp_path):
    import tests.test_chat_lanes_v13510 as L

    if shutil.which("node") is None:
        pytest.skip("no node")
    row = {"id": "build_app:app_1", "kind": "chat", "thread_id": "app_1", "tool": "Bash",
           "requested_at": "x", "mission_id": None, "project_id": None, "surface": "build_app",
           "app_session": "app_1", "harness": "claude-code"}
    out = L._plan([{"approvals": [row], "now": 0}, {"approvals": [row], "now": 10 * 60 * 60 * 1000}], tmp_path)
    note = out[0]["notify"][0]
    assert note.get("chat") is not True and "chat" not in note["title"].lower()
    assert note["title"] == "Claude Code is waiting for your OK"
    assert note["href"] == "/terminals?view=code&code=app_1"
    assert note["body"].endswith("click to open the session.")
    later = out[1]["notify"]
    assert later and later[0]["title"] == "Claude Code is still waiting for your OK"


# --------------------------------------------------------------------------- #
# lows
# --------------------------------------------------------------------------- #
def test_a_turns_replay_is_merged_masked_and_bounded(monkeypatch):
    monkeypatch.setattr(S, "MAX_TURN_CHARS", 10)
    t = S.HostedTurn("t", "s")
    for d in ("here: sk-a", "nt-api03-" + "Q" * 40, "more", "and more"):
        t.add({"type": "text", "item": "i", "delta": d})
    t.add({"type": "text_done", "item": "i", "text": "done"})
    assert [e["type"] for e in t.events] == ["text", "text_done"]
    assert "Q" * 30 not in t.events[0]["delta"] and "and more" not in t.events[0]["delta"]


def test_claude_never_offers_a_session_allow_that_switches_on_full_access():
    from iron_jarvis.hosted.claude import ClaudeDriver

    got = []
    d = ClaudeDriver(write=None, emit=got.append, ask=lambda a: got.append(a), on_session_id=lambda s: None,
                     mode="ask")
    d.on_message({"type": "control_request", "request_id": "r1", "request": {
        "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"},
        "permission_suggestions": [{"type": "setMode", "mode": "bypassPermissions", "destination": "session"}]}})
    ask = got[0]
    assert ask.can_session is False and ask.event["once_only"] is True
    d.on_message({"type": "control_request", "request_id": "r2", "request": {
        "subtype": "can_use_tool", "tool_name": "Write", "input": {"file_path": "a.txt", "content": "x"},
        "description": "a.txt",
        "permission_suggestions": [{"type": "setMode", "mode": "acceptEdits", "destination": "session"}]}})
    ask2 = got[1]
    assert ask2.can_session is True and ask2.data["sets_mode"] == "edits"
    assert "switches Claude Code to Auto edits" in ask2.event["detail"]


async def test_allow_for_this_session_that_changes_the_mode_updates_the_row(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}))
        ask = (await wait_pending(c, s["id"]))[0]
        assert "Auto edits" in ask["detail"]
        await c.post(f"/build/app/sessions/{s['id']}/answer",
                     json={"request_id": ask["request_id"], "decision": "allow_session"})
        evs = frames((await asyncio.wait_for(task, 30)).text)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    assert row["mode"] == "edits"
    assert any(e["type"] == "notice" and "Auto edits" in e["text"] for e in evs)
    await stop_all(app)


async def test_a_codex_mode_change_applies_next_and_says_so(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "codex_allow", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="codex")
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}))
        await wait_pending(c, s["id"])
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"mode": "full", "confirm": True})
        assert r.json()["applied"] == {"mode": "next"}
        assert any(e["type"] == "notice" and "next message" in e["text"] for e in svc.turn("t").events)
        task.cancel()
    await stop_all(app)


def test_a_turn_a_crash_cut_off_gets_its_done_at_boot(tmp_path):
    from iron_jarvis.hosted import events as E

    log_ = E.EventLog(tmp_path, "app_cut")
    log_.append(E.make("user", text="hi", turn="t1"))
    log_.append(E.make("tool", item="x", kind="read", name="Read", title="Read a", input="", status="running",
                       turn="t1"))
    log_.close()
    ok = E.EventLog(tmp_path, "app_ok")
    ok.append(E.make("user", text="hi", turn="t1"))
    ok.append(E.make("done", stop="completed", turn="t1"))
    ok.close()
    assert S.reconcile_cut_turns(tmp_path) == 1
    events, _, _ = E.EventLog(tmp_path, "app_cut").read()
    assert events[-1]["type"] == "done" and events[-1]["stop"] == "interrupted" and events[-1]["turn"] == "t1"
    assert events[-2]["type"] == "error"
    assert S.reconcile_cut_turns(tmp_path) == 0, "a closed turn is closed once"


def test_the_boot_reconcile_is_hooked_off_the_loop():
    src = (Path(__file__).parents[1] / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread(_hosted_reconcile, platform.config.home)" in src


async def test_the_interrupt_route_answers_quickly_even_when_the_harness_does_not(app, work, monkeypatch):
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work))
    svc = S.service_for(app.state.platform)
    monkeypatch.setattr(S, "INTERRUPT_WAIT_S", 0.3)
    live = svc._live(row)

    class _Proc:
        alive = True
        stopped = False

    class _Driver:
        async def interrupt(self):
            await asyncio.sleep(30)

    live.proc, live.driver = _Proc(), _Driver()
    live.turn = S.HostedTurn("t", row.id)
    t0 = time.monotonic()
    assert (await svc.interrupt(row.id)) == {"ok": True, "running": True}
    assert time.monotonic() - t0 < 3
    live.proc, live.driver = None, None


async def test_binary_lookup_runs_off_the_loop(app, work, tmp_path, monkeypatch):
    import threading

    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    loop_thread = threading.get_ident()
    seen = []
    real = S.binary_for
    monkeypatch.setattr(S, "binary_for", lambda h: (seen.append(threading.get_ident()), real(h))[1])
    async with client(app) as c:
        s = await new_session(c, work)
        await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"})
    assert seen and all(t != loop_thread for t in seen)
    await stop_all(app)


async def test_the_disk_listing_reads_the_chosen_accounts_home(app, work, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "default-claude"))
    acc = tmp_path / "acc1"
    _write_claude(acc, work, "33333333-2222-3333-4444-555555555555", "on the work account")
    _write_claude(tmp_path / "default-claude", work, "44444444-2222-3333-4444-555555555555", "on this PC")

    class _Svc:
        enabled = True

        def cached_accounts(self):
            return ([{"id": "p1", "provider": "anthropic", "lane": "cli", "title": "Work",
                      "cli": {"home": str(acc)}}], {"p1": {"status": "ready"}})

    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(), raising=False)
    async with client(app) as c:
        mine = (await c.get(f"/build/app/sessions?folder={work}&disk=1")).json()["disk"]
        theirs = (await c.get(f"/build/app/sessions?folder={work}&disk=1&account=p1")).json()["disk"]
    assert [r["title"] for r in mine if r["harness"] == "claude-code"] == ["on this PC"]
    assert [r["title"] for r in theirs if r["harness"] == "claude-code"] == ["on the work account"]
