"""v1.441.0 (lane g1): the Session tab's live controls through the REAL
routes, service and drivers, with the fake harness process
(``tests/fixtures/fake_harness_v1409.py``, never a real Claude Code or
Codex): the overview's ``background_work`` and MCP ``actions``, ``POST …/
background/{id}/stop``, ``POST …/mcp/{server}``, and a live effort change."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import tool_setup as TS
from tests._build_app_helpers_v1409 import client, frames, new_session, sent, stop_all, use_fake


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


def _task(driver, sub: str, tid: str, **extra) -> None:
    driver.on_message({"type": "system", "subtype": sub, "task_id": tid, **extra})


def _notices(view: dict[str, Any]) -> list[str]:
    return [e.get("text", "") for e in view.get("events") or [] if e.get("type") == "notice"]


async def _running_claude(c, app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_full_mode_edit_v1418", log)
    s = await new_session(c, work)
    await _say(c, s["id"])
    return s, S.service_for(app.state.platform).live[s["id"]], log


async def test_stop_a_claude_background_task_from_the_session_tab(app, work, tmp_path, monkeypatch):
    async with client(app) as c:
        s, live, log = await _running_claude(c, app, work, tmp_path, monkeypatch)
        sid = s["id"]
        _task(live.driver, "task_started", "bztz1", task_type="local_bash", description="Long ping",
              is_backgrounded=True)
        over = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
        assert over["background_work"] == [{"id": "bztz1", "title": "Long ping", "kind": "command", "can_stop": True}]
        r = await c.post(f"/build/app/sessions/{sid}/background/bztz1/stop")
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Long ping"
        reqs = [m["request"] for m in sent(log) if m.get("type") == "control_request"]
        assert {"subtype": "stop_task", "task_id": "bztz1"} in reqs
        view = (await c.get(f"/build/app/sessions/{sid}")).json()
        assert "You stopped the background command “Long ping”." in _notices(view)
        # The process (and so any reply) is untouched.
        assert live.running and not live.proc.stopped
        r = await c.post(f"/build/app/sessions/{sid}/background/nope/stop")
        assert r.status_code == 409 and "already ended" in r.json()["detail"]
    await stop_all(app)


async def test_controls_need_a_running_tool_and_a_known_session(app, work, tmp_path, monkeypatch):
    use_fake(monkeypatch, "claude_modes", tmp_path / "fake.log")
    async with client(app) as c:
        s = await new_session(c, work)
        over = (await c.get(f"/build/app/sessions/{s['id']}/overview")).json()
        assert "background_work" not in over
        r = await c.post(f"/build/app/sessions/{s['id']}/background/x/stop")
        assert r.status_code == 409 and "not running" in r.json()["detail"]
        r = await c.post(f"/build/app/sessions/{s['id']}/mcp/echo", json={"action": "off"})
        assert r.status_code == 409
        r = await c.post("/build/app/sessions/nope/background/x/stop")
        assert r.status_code == 404
    await stop_all(app)


async def test_claude_mcp_actions_offered_by_state_and_done_through_its_own_requests(app, work, tmp_path, monkeypatch):
    async with client(app) as c:
        s, live, log = await _running_claude(c, app, work, tmp_path, monkeypatch)
        sid = s["id"]
        TS.note_servers(live.driver, [{"name": "echo", "state": TS.CONNECTED},
                                      {"name": "broken", "state": TS.FAILED}])
        over = (await c.get(f"/build/app/sessions/{sid}/overview")).json()
        acts = {row["name"]: row.get("actions") for row in over["mcp"]["servers"]}
        assert acts == {"echo": ["reconnect", "off"], "broken": ["reconnect", "off"]}
        r = await c.post(f"/build/app/sessions/{sid}/mcp/echo", json={"action": "off"})
        assert r.status_code == 200, r.text
        assert "Claude Code remembers this for this folder" in r.json()["words"]
        reqs = [m["request"] for m in sent(log) if m.get("type") == "control_request"]
        assert {"subtype": "mcp_toggle", "serverName": "echo", "enabled": False} in reqs
        assert {"subtype": "mcp_status"} in reqs
        assert any("Turned off echo." in n for n in _notices((await c.get(f"/build/app/sessions/{sid}")).json()))
        # Not offered by Claude Code here / does not fit the state / no such server.
        assert (await c.post(f"/build/app/sessions/{sid}/mcp/echo", json={"action": "sign_in"})).status_code == 400
        assert (await c.post(f"/build/app/sessions/{sid}/mcp/broken", json={"action": "on"})).status_code == 409
        assert (await c.post(f"/build/app/sessions/{sid}/mcp/ghost", json={"action": "off"})).status_code == 404
        assert (await c.post(f"/build/app/sessions/{sid}/mcp/echo", json={"action": "explode"})).status_code == 400
    await stop_all(app)


async def test_claude_effort_change_reaches_the_running_process_without_a_restart(app, work, tmp_path, monkeypatch):
    async with client(app) as c:
        s, live, log = await _running_claude(c, app, work, tmp_path, monkeypatch)
        real = live.driver.control

        async def control(subtype, **fields):
            if subtype == "get_settings":
                await real(subtype, **fields)  # still reaches the tool
                return {"applied": {"effort": live.driver.effort or "medium"}}
            return await real(subtype, **fields)

        monkeypatch.setattr(live.driver, "control", control)
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "high"})
        assert r.status_code == 200 and r.json()["applied"] == {"effort": "now"}
        assert live.restart is False
        reqs = [m["request"] for m in sent(log) if m.get("type") == "control_request"]
        assert {"subtype": "apply_flag_settings", "settings": {"effortLevel": "high"}} in reqs
        assert r.json()["session"]["effort"] == "high"
        # Nothing waits for a restart: the next message reaches the same process.
        assert not live.proc.stopped
    await stop_all(app)


async def test_claude_effort_falls_back_to_a_restart_when_not_taken_live(app, work, tmp_path, monkeypatch):
    async with client(app) as c:
        s, live, _log = await _running_claude(c, app, work, tmp_path, monkeypatch)
        # The fake answers get_settings with nothing (a spent script): not proven live.
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "high"})
        assert r.json()["applied"] == {"effort": "next"}
        assert live.restart is True
    await stop_all(app)


async def test_the_harness_rows_say_which_tool_takes_effort_live(app):
    async with client(app) as c:
        rows = (await c.get("/build/app/harnesses")).json()
        rows = rows.get("harnesses", rows) if isinstance(rows, dict) else rows
        by = {r["id"]: r for r in rows}
        assert by["claude-code"]["effort_live"] is True
        assert all(by[h]["effort_live"] is False for h in by if h != "claude-code")


async def test_codex_background_terminals_are_listed_stopped_and_keep_the_session(app, work, tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "codex_signin", log)
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="codex")
        await _say(c, s["id"])
        live = svc.live[s["id"]]
        real = live.driver.request
        state = {"data": [{"itemId": "call_bg1", "processId": "87917", "command": "npm run dev --token s3cr3t",
                           "cwd": str(work)}], "nextCursor": None}

        async def request(method, params=None):
            if method == "thread/backgroundTerminals/list":
                await real(method, params)
                return dict(state)
            if method == "thread/backgroundTerminals/terminate":
                await real(method, params)
                state["data"] = []
                return {"terminated": True}
            if method == "mcpServer/oauth/login":
                await real(method, params)
                return {"authorizationUrl": "https://auth.example.com/authorize?state=x"}
            return await real(method, params)

        monkeypatch.setattr(live.driver, "request", request)
        over = (await c.get(f"/build/app/sessions/{s['id']}/overview")).json()
        (row,) = over["background_work"]
        assert row["id"] == "87917" and row["kind"] == "command"
        assert "s3cr3t" not in row["title"], "a secret-named flag's value reached the screen"
        # Background work the tool still runs keeps the process from the idle reaper.
        assert live.background_tasks == 1
        late = svc.clock() + S.IDLE_S + 1
        assert await svc.reap_once(now=late) == []
        r = await c.post(f"/build/app/sessions/{s['id']}/background/87917/stop")
        assert r.status_code == 200 and r.json()["background_work"] == []
        methods = [m.get("method") for m in sent(log)]
        assert "thread/backgroundTerminals/terminate" in methods
        assert live.background_tasks == 0
        # Codex's MCP sign-in: only for a server waiting for one; the page gets the address.
        TS.note_servers(live.driver, [{"name": "authy", "state": TS.NEEDS_SIGN_IN},
                                      {"name": "echo", "state": TS.CONNECTED}])
        over = (await c.get(f"/build/app/sessions/{s['id']}/overview")).json()
        acts = {x["name"]: x.get("actions") for x in over["mcp"]["servers"]}
        assert acts == {"authy": ["sign_in"], "echo": None}
        # The row's words point at its own menu, not at a terminal.
        assert "Sign in… in this row's menu" in over["mcp"]["servers"][0]["sign_in"]
        r = await c.post(f"/build/app/sessions/{s['id']}/mcp/authy", json={"action": "sign_in"})
        assert r.status_code == 200 and r.json()["url"] == "https://auth.example.com/authorize?state=x"
        assert (await c.post(f"/build/app/sessions/{s['id']}/mcp/echo", json={"action": "reconnect"})).status_code == 400
    await stop_all(app)
