"""v1.409.0 — Build's Code view: its processes. The pool (cap 6, the oldest
IDLE one stopped first), the 15-minute reaper, the daemon's shutdown, a resume
of the SAME harness session after a stop, the environment (a Build
terminal's: markers and the daemon's token dropped, the account's home set,
the user's own keys kept), and Continue on the next account (a Claude
conversation COPIED into the next account's home, then resumed there).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, starts, stop_all, until, use_fake


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


async def _say(c, sid, turn_id="t1", text="ready?"):
    r = await c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": turn_id})
    return frames(r.text)


async def test_resume_after_a_stop_reopens_the_same_claude_session(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        await _say(c, s["id"])
        await until(lambda: store.get(app.state.platform.engine, s["id"]).harness_session_id)
        hid = store.get(app.state.platform.engine, s["id"]).harness_session_id
        await svc.reap_once(now=svc.clock() + S.IDLE_S + 1)
        assert svc.live[s["id"]].proc is None, "the idle reaper did not stop it"
        await _say(c, s["id"], "t2")
    runs = starts(log)
    assert len(runs) == 2
    assert "--session-id" in runs[0]["argv"] and "--resume" not in runs[0]["argv"]
    assert runs[1]["argv"][runs[1]["argv"].index("--resume") + 1] == hid
    for a in ("--permission-prompts", "--permission-prompt-tool", "--include-partial-messages"):
        assert a in runs[0]["argv"]
    assert runs[0]["argv"][runs[0]["argv"].index("--permission-mode") + 1] == "default"
    assert runs[0]["cwd"] == str(work)
    await stop_all(app)


async def test_resume_after_a_stop_reopens_the_same_codex_thread(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_resume", log)
    async with client(app) as c:
        row = store.create(app.state.platform.engine, harness="codex", folder=str(work),
                           harness_session_id="01a189c4-9316-7d30-b03e-8a6c95d7bacd")
        evs = await _say(c, row.id)
    assert evs[-1]["stop"] == "completed"
    msgs = sent(log)
    assert not any(m.get("method") == "thread/start" for m in msgs)
    resume = next(m for m in msgs if m.get("method") == "thread/resume")["params"]
    assert resume["threadId"] == "01a189c4-9316-7d30-b03e-8a6c95d7bacd"
    await stop_all(app)


async def test_the_reaper_never_stops_a_busy_or_a_recent_process(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}))
        await until(lambda: svc.live.get(s["id"]) and svc.live[s["id"]].asks)
        assert await svc.reap_once(now=svc.clock() + 10 * S.IDLE_S) == [], "a session waiting on the user was reaped"
        ask = next(iter(svc.live[s["id"]].asks))
        await c.post(f"/build/app/sessions/{s['id']}/answer", json={"request_id": ask, "decision": "deny"})
        await asyncio.wait_for(task, 30)
        assert await svc.reap_once(now=svc.clock() + S.IDLE_S - 5) == []
        assert await svc.reap_once(now=svc.clock() + S.IDLE_S + 1) == [s["id"]]
    await stop_all(app)


async def test_the_pool_stops_the_oldest_idle_process_and_refuses_when_all_are_busy(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    monkeypatch.setattr(S, "MAX_LIVE", 2)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        a = await new_session(c, work)
        b = await new_session(c, work)
        cc = await new_session(c, work)
        await _say(c, a["id"], "ta")
        await _say(c, b["id"], "tb")
        assert svc.live[a["id"]].running and svc.live[b["id"]].running
        await _say(c, cc["id"], "tc")
        assert svc.live[a["id"]].proc is None, "the oldest idle process was not the one stopped"
        assert svc.live[b["id"]].running and svc.live[cc["id"]].running
        # every live one busy (waiting on the user): a fourth is refused in words
        for sid in (b["id"], cc["id"]):
            svc.live[sid].asks["x"] = S.Ask("x", "approval", "Write")
        evs = await _say(c, a["id"], "ta2")
        assert evs[-1]["stop"] == "failed"
        assert any(e["type"] == "error" and "Six" in e["title"] for e in evs)
        for sid in (b["id"], cc["id"]):
            svc.live[sid].asks.clear()
    await stop_all(app)


async def test_shutdown_stops_every_process_and_ends_a_running_turn_kept(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_deny", tmp_path / "fake.log")
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "x", "turn_id": "t"}))
        await until(lambda: svc.live.get(s["id"]) and svc.live[s["id"]].asks)
        proc = svc.live[s["id"]].proc
        await svc.shutdown()
        assert not proc.alive and proc.proc.poll() is not None
        evs = frames((await asyncio.wait_for(task, 30)).text)
    assert evs[-1] == {**evs[-1], "type": "done", "stop": "interrupted"}
    assert any(e["type"] == "resolved" and e["decision"] == "expired" for e in evs), "the open card was not closed"
    log_events = (Path(app.state.platform.config.home) / "hosted" / s["id"] / "events.jsonl").read_text(encoding="utf-8")
    assert '"stop": "interrupted"' in log_events, "what the turn wrote was not kept"


async def test_the_lifespan_shutdown_reaches_the_code_view():
    src = (Path(__file__).parents[1] / "src" / "iron_jarvis" / "daemon" / "app.py").read_text(encoding="utf-8")
    assert '_hosted = getattr(platform, "hosted", None)' in src and "_hosted.shutdown()" in src


async def test_the_environment_is_a_terminals_with_the_accounts_home(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_modes", log)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("IRONJARVIS_MCP_TOKEN", "daemon-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "users-own-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "users-anthropic-key")
    home1 = tmp_path / "acc1"
    home1.mkdir()
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(home1),
           "unset": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]}
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec))
    plain = store.create(app.state.platform.engine, harness="claude-code", folder=str(work))
    async with client(app) as c:
        await _say(c, row.id)
        await _say(c, plain.id, "t2")
    env1, env2 = starts(log)[0]["env"], starts(log)[1]["env"]
    for env in (env1, env2):
        assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env and "IRONJARVIS_MCP_TOKEN" not in env
        assert env.get("OPENAI_API_KEY") == "users-own-key"
    assert env1["CLAUDE_CONFIG_DIR"] == str(home1) and "ANTHROPIC_API_KEY" not in env1
    assert env2.get("ANTHROPIC_API_KEY") == "users-anthropic-key", "this PC's login keeps the user's own keys"
    await stop_all(app)


# --------------------------------------------------------------------------- #
# account limits and Continue on the next account
# --------------------------------------------------------------------------- #
class _Client:
    def __init__(self, profiles, states):
        self._p, self._s = profiles, states
        self.signals = []

    def profiles(self):
        return self._p

    def states(self):
        return self._s

    def signal(self, pid, text=""):
        self.signals.append((pid, text))
        return {"parked": True}


class _Svc:
    enabled = True

    def __init__(self, client):
        self.client = client

    def lease_client(self, timeout_s=None):
        return self.client


def _profiles(tmp_path):
    h1, h2 = tmp_path / "acc1", tmp_path / "acc2"
    h1.mkdir(exist_ok=True)
    h2.mkdir(exist_ok=True)
    profiles = [
        {"id": "p1", "provider": "anthropic", "lane": "cli", "title": "Work", "order": 0, "cli": {"home": str(h1)}},
        {"id": "p2", "provider": "anthropic", "lane": "cli", "title": "Personal", "order": 1, "cli": {"home": str(h2)}},
    ]
    return h1, h2, profiles


async def test_an_account_limit_is_an_error_with_continue_on_and_it_continues(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_limit", log)
    h1, h2, profiles = _profiles(tmp_path)
    fake = _Client(profiles, {"p1": {"status": "ready"}, "p2": {"status": "ready"}})
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(h1), "unset": []}
    sid = "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21"
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec), harness_session_id=sid)
    conv = h1 / "projects" / "C--work-demo" / f"{sid}.jsonl"
    conv.parent.mkdir(parents=True)
    conv.write_text('{"type":"user"}\n', encoding="utf-8")
    async with client(app) as c:
        evs = await _say(c, row.id)
        err = [e for e in evs if e["type"] == "error"]
        assert len(err) == 1 and err[0]["fix_kind"] == "account_limit" and err[0]["account"] == "p1"
        assert "Continue on another account" in err[0]["fix"]
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["account"] == "p2" and body["account_name"] == "Personal"
    copied = h2 / "projects" / "C--work-demo" / f"{sid}.jsonl"
    assert copied.read_bytes() == conv.read_bytes() and conv.exists(), "the conversation is COPIED, never moved"
    assert fake.signals and fake.signals[0][0] == "p1" and "session limit" in fake.signals[0][1]
    after = store.get(app.state.platform.engine, row.id)
    assert after.account == "p2" and store.account_record(after)["home"] == str(h2)
    await stop_all(app)


async def test_continue_on_refuses_in_words(app, work, tmp_path, monkeypatch):
    h1, h2, profiles = _profiles(tmp_path)
    eng = app.state.platform.engine
    codex = store.create(eng, harness="codex", folder=str(work), harness_session_id="01a189c4-9316-7d30-b03e-8a6c95d7bacd")
    mine = store.create(eng, harness="claude-code", folder=str(work),
                        harness_session_id="11111111-2222-3333-4444-555555555555")
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(h1), "unset": []}
    acct = store.create(eng, harness="claude-code", folder=str(work), account="p1", account_json=json.dumps(rec),
                        harness_session_id="11111111-2222-3333-4444-555555555555")
    fresh = store.create(eng, harness="claude-code", folder=str(work), account="p1", account_json=json.dumps(rec))
    only_one = _Client(profiles[:1], {"p1": {"status": "ready"}})
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(only_one), raising=False)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{codex.id}/continue-on", json={})
        assert r.status_code == 409 and "Claude Code sessions only" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{mine.id}/continue-on", json={})
        assert r.status_code == 409 and "this PC's login" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{fresh.id}/continue-on", json={})
        assert r.status_code == 409 and "no conversation yet" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{acct.id}/continue-on", json={})
        assert r.status_code == 409 and "no other Claude account" in r.json()["detail"]
        parked = _Client(profiles, {"p1": {"status": "ready"}, "p2": {"status": "unauthenticated"}})
        monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(parked), raising=False)
        r = await c.post(f"/build/app/sessions/{acct.id}/continue-on", json={"account": "p2"})
        assert r.status_code == 409 and "cannot take over" in r.json()["detail"]
    assert only_one.signals == [] and parked.signals == [], "nothing was told before a refusal"
    assert not list((h2).rglob("*.jsonl")), "nothing was copied before a refusal"
