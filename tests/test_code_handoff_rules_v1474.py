"""v1.474.0 — Hand off: the account group, Emergency stop, and the words the
MCP server says about what it serves."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import carry_on as CO
from iron_jarvis.hosted import handoff as HO
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.tools.base import ToolContext
from tests._build_app_helpers_v1409 import client, new_session, stop_all, until, use_fake


class _Proxy:
    enabled = True

    def __init__(self, profiles, states):
        self._cache = (profiles, states)

    def cached_accounts(self):
        return self._cache


def _profile(pid: str, proxy: str, order: int) -> dict:
    return {"id": pid, "provider": proxy, "lane": "cli", "order": order, "title": pid}


def _svc(groups, states) -> SimpleNamespace:
    profiles = [_profile("a1", "anthropic", 1), _profile("c1", "openai", 1), _profile("c2", "openai", 2)]
    platform = SimpleNamespace(config=SimpleNamespace(account_groups=groups), iron_proxy=_Proxy(profiles, states))
    return SimpleNamespace(platform=platform)


READY = {"status": "ready"}


def _parent(account: str = "a1", harness: str = "claude-code") -> SimpleNamespace:
    return SimpleNamespace(harness=harness, account=account)


def test_a_helper_of_another_tool_stays_in_the_same_account_group():
    svc = _svc({"Work": ["a1", "c2"], "Personal": ["c1"]}, {"a1": READY, "c1": READY, "c2": READY})
    # c1 comes first in Iron-Proxy's order but is in another group.
    assert HO.helper_account(svc, _parent(), "codex") == "c2"


def test_no_account_of_the_group_can_take_it_is_a_refusal_never_another_group():
    svc = _svc({"Work": ["a1", "c2"], "Personal": ["c1"]},
               {"a1": READY, "c1": READY, "c2": {"status": "disabled"}})
    with pytest.raises(S.Refused) as got:
        HO.helper_account(svc, _parent(), "codex")
    assert "Work" in str(got.value) and "nothing was handed over" in str(got.value)


def test_the_same_tool_keeps_the_account_and_an_ungrouped_or_own_sign_in_takes_the_tools_choice():
    svc = _svc({"Work": ["a1", "c2"]}, {"a1": READY, "c1": READY, "c2": READY})
    assert HO.helper_account(svc, _parent(), "claude-code") == "a1"
    assert HO.helper_account(svc, _parent(account="zz"), "codex") == ""  # in no group
    assert HO.helper_account(svc, _parent(account=""), "codex") == ""  # this PC's own login
    assert HO.helper_account(svc, _parent(), "pi") == ""  # Pi signs in by itself


def test_unread_accounts_refuse_rather_than_guess():
    svc = _svc({"Work": ["a1", "c2"]}, {})
    svc.platform.iron_proxy = SimpleNamespace(enabled=True, cached_accounts=lambda: None)
    with pytest.raises(S.Refused) as got:
        HO.helper_account(svc, _parent(), "codex")
    assert str(got.value) == HO.GROUP_NOT_READ


@pytest.mark.asyncio
async def test_emergency_stop_refuses_a_new_hand_off(tmp_path: Path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    use_fake(monkeypatch, "claude_full_mode_edit_v1418", tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    try:
        async with client(app) as c:
            parent = (await new_session(c, work))["id"]
            await c.patch(f"/build/app/sessions/{parent}", json={"capabilities": {"handoff": True}})
        p = app.state.platform
        p.config.autonomy_kill_switch = True
        ctx = ToolContext(workspace=work, session_id=f"mcp:code:{parent}", agent_run_id="", config=p.config,
                          event_bus=p.event_bus, engine=p.engine)
        r = await p.registry.get(HO.TOOL_START).execute({"task": "x"}, ctx)
        assert not r.ok and r.error == HO.EMERGENCY_STOP
        assert store.helpers_of(p.engine, parent) == []
    finally:
        app.state.platform.config.autonomy_kill_switch = False
        await stop_all(app)


@pytest.mark.asyncio
async def test_emergency_stop_stops_a_working_helper_but_not_a_reply_the_user_started(tmp_path: Path):
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        loop = asyncio.get_running_loop()

        def running(sid: str, turn_id: str):
            row = store.create(app.state.platform.engine, harness="claude-code", folder=str(tmp_path))
            live = svc._live(row)
            turn = S.HostedTurn(turn_id, row.id)
            turn.task = loop.create_future()
            live.turn = turn
            return row.id

        helper = running("h", "handoff-abc")
        users = running("u", "user-turn")
        svc.handoff_turns[helper] = "handoff-abc"
        stopped: list[str] = []

        async def spy(sid):
            stopped.append(sid)
            return {"ok": True}

        svc.interrupt = spy  # type: ignore[method-assign]
        got = CO.stop_unattended_turns(app.state.platform)
        assert got == ["build_app:handoff-abc"]
        await until(lambda: stopped)
        assert stopped == [helper] and users not in stopped
    finally:
        await stop_all(app)


def test_the_server_says_it_serves_the_hand_off_tools():
    from iron_jarvis.mcpserver.server import MCP_SCOPE_LINE, capability_refused_message

    for name in HO.TOOL_NAMES:
        assert name in MCP_SCOPE_LINE
    assert "hand-off tools" in capability_refused_message("write_file")
    # A hand-off tool refused in a Code session names its box.
    assert "Hand off" in capability_refused_message(HO.TOOL_START, "code:app_1")


@pytest.mark.asyncio
async def test_a_start_makes_the_helper_on_the_account_the_group_rule_chose(tmp_path: Path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    use_fake(monkeypatch, "claude_full_mode_edit_v1418", tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    try:
        svc = S.service_for(app.state.platform)
        async with client(app) as c:
            parent = (await new_session(c, work))["id"]
            await c.patch(f"/build/app/sessions/{parent}", json={"capabilities": {"handoff": True}})
        asked: list[tuple[str, str]] = []

        def chose(svc_, prow, want):
            asked.append((prow.id, want))
            return "from-the-group-rule"

        made: list[dict] = []
        real_create = svc.create

        async def create(body):
            made.append(dict(body))
            return await real_create({**body, "account": ""})

        monkeypatch.setattr(HO, "helper_account", chose)
        monkeypatch.setattr(svc, "create", create)
        p = app.state.platform
        ctx = ToolContext(workspace=work, session_id=f"mcp:code:{parent}", agent_run_id="", config=p.config,
                          event_bus=p.event_bus, engine=p.engine)
        r = await p.registry.get(HO.TOOL_START).execute({"tool": "codex", "task": "x"}, ctx)
        assert r.ok, r.error
        assert asked == [(parent, "codex")]
        assert made[0]["account"] == "from-the-group-rule"
    finally:
        await stop_all(app)
