"""Agent-authored reusable tools: create -> persist -> reuse by future agents -> run."""

from __future__ import annotations

import sys

from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.dynamic import DynamicToolRegistry


def _ctx(p, workspace):
    return ToolContext(
        workspace=workspace,
        session_id="s1",
        agent_run_id="r1",
        config=p.config,
        event_bus=p.event_bus,
        engine=p.engine,
    )


async def test_command_tool_runs_and_is_injection_safe(platform, tmp_path):
    reg = DynamicToolRegistry(platform.engine)
    rec = reg.register(
        "echo_text",
        "echo the text param",
        [{"name": "text", "type": "string", "required": True}],
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "{text}"],
    )
    tool = reg.build_tool(rec)
    # A value containing shell metacharacters must be passed LITERALLY (shell=False).
    res = await tool.execute({"text": "hi; rm -rf /"}, _ctx(platform, tmp_path))
    assert res.ok
    assert res.output.strip() == "hi; rm -rf /"  # no shell interpretation
    # required param enforced
    bad = await tool.execute({}, _ctx(platform, tmp_path))
    assert not bad.ok and "missing required" in (bad.error or "")


async def test_render_is_single_pass_order_independent(platform, tmp_path):
    # A param VALUE that contains another param's {placeholder} must stay literal
    # (single simultaneous substitution, not sequential re-expansion).
    reg = DynamicToolRegistry(platform.engine)
    rec = reg.register(
        "echo2",
        "echo both params",
        [{"name": "a", "required": True}, {"name": "b", "required": True}],
        [sys.executable, "-c", "import sys; print(sys.argv[1], '|', sys.argv[2])", "{a}", "{b}"],
    )
    tool = reg.build_tool(rec)
    res = await tool.execute({"a": "<{b}>", "b": "X"}, _ctx(platform, tmp_path))
    assert res.ok
    assert res.output.strip() == "<{b}> | X"  # a's value NOT re-expanded to <X>


async def test_tool_create_persists_and_future_agent_reuses(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()

    # Agent A (platform p1) authors a reusable tool.
    p1 = build_platform(str(root))
    create = p1.registry.get("tool_create")
    assert create is not None  # the capability is wired
    res = await create.execute(
        {
            "name": "greet",
            "description": "print a greeting",
            "command": [sys.executable, "-c", "print('hi from a custom tool')"],
            "parameters": [],
        },
        _ctx(p1, tmp_path),
    )
    assert res.ok
    assert p1.registry.get("greet") is not None and "greet" in p1.registry.custom_names()

    # A FUTURE session/restart: a fresh platform on the same state rehydrates it.
    p2 = build_platform(str(root))
    assert p2.registry.get("greet") is not None  # reused, no re-creation
    assert "greet" in p2.registry.custom_names()

    # A default Builder agent (which never named "greet") SEES it via "custom:*".
    builder = get_agent_definition(AgentType.BUILDER)
    specs = p2.registry.specs(builder.tools)
    assert any(s["name"] == "greet" for s in specs)

    # ...and it actually runs.
    run = await p2.registry.get("greet").execute({}, _ctx(p2, tmp_path))
    assert run.ok and "hi from a custom tool" in run.output


async def test_tool_create_guards(platform, tmp_path):
    create = platform.registry.get("tool_create")
    ctx = _ctx(platform, tmp_path)
    # cannot shadow a built-in tool
    r1 = await create.execute({"name": "shell", "command": ["echo", "x"]}, ctx)
    assert not r1.ok and "built-in" in (r1.error or "")
    # invalid identifier
    r2 = await create.execute({"name": "bad name!", "command": ["echo"]}, ctx)
    assert not r2.ok
    # empty command
    r3 = await create.execute({"name": "ok_name", "command": []}, ctx)
    assert not r3.ok


def test_custom_tool_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/tools/custom").json()["tools"] == []
    created = client.post(
        "/tools/custom",
        json={
            "name": "ping_tool",
            "description": "d",
            # A program that provably exists: since v1.205.0 creation REFUSES
            # an argv[0] that doesn't resolve on PATH ("echo" is a shell
            # builtin on Windows, not a program).
            "command": [sys.executable, "-c", "print('ok')"],
        },
    )
    assert created.status_code == 200 and created.json()["name"] == "ping_tool"
    assert len(client.get("/tools/custom").json()["tools"]) == 1
    # built-in collision + invalid name rejected
    assert client.post("/tools/custom", json={"name": "shell", "command": ["x"]}).status_code == 400
    assert client.post("/tools/custom", json={"name": "bad!", "command": ["x"]}).status_code == 400
    assert client.delete("/tools/custom/ping_tool").json()["removed"] is True
    assert client.get("/tools/custom").json()["tools"] == []


async def test_tool_delete(platform, tmp_path):
    ctx = _ctx(platform, tmp_path)
    await platform.registry.get("tool_create").execute(
        {"name": "tmp_tool", "command": [sys.executable, "-c", "print(1)"]}, ctx
    )
    assert platform.registry.get("tmp_tool") is not None
    res = await platform.registry.get("tool_delete").execute({"name": "tmp_tool"}, ctx)
    assert res.ok
    assert platform.registry.get("tmp_tool") is None
    assert "tmp_tool" not in platform.registry.custom_names()
