from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def ctx(platform, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ToolContext(
        workspace=ws,
        session_id="s1",
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def test_write_then_read(platform, ctx):
    reg, perms = platform.registry, platform.permissions
    w = await reg.invoke("write_file", {"path": "a.txt", "content": "hi"}, ctx, perms)
    assert w.ok
    r = await reg.invoke("read_file", {"path": "a.txt"}, ctx, perms)
    assert r.ok and r.output == "hi"


async def test_edit_and_grep(platform, ctx):
    reg, perms = platform.registry, platform.permissions
    await reg.invoke("write_file", {"path": "n.txt", "content": "alpha\nbeta"}, ctx, perms)
    e = await reg.invoke("edit_file", {"path": "n.txt", "old": "beta", "new": "gamma"}, ctx, perms)
    assert e.ok
    g = await reg.invoke("grep", {"pattern": "gamma"}, ctx, perms)
    assert g.ok and "n.txt" in g.output


async def test_path_escape_blocked(platform, ctx):
    r = await platform.registry.invoke(
        "write_file", {"path": "../evil.txt", "content": "x"}, ctx, platform.permissions
    )
    assert not r.ok


async def test_shell_denied_fail_closed(platform, ctx):
    # default permission for shell is "ask"; platform has no resolver -> denied
    r = await platform.registry.invoke(
        "shell", {"command": "echo hi"}, ctx, platform.permissions
    )
    assert not r.ok
    assert "permission denied" in (r.error or "")


async def test_invocation_is_recorded(platform, ctx):
    await platform.registry.invoke(
        "write_file", {"path": "b.txt", "content": "x"}, ctx, platform.permissions
    )
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)))
    assert any(t.tool == "write_file" and t.ok for t in rows)
