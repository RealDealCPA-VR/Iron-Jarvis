"""External MCP tools are EXECUTABLE end-to-end (§ external tool consumption).

Proves the whole chain for MCP-as-native-tools, offline but REAL: a genuine
line-delimited JSON-RPC stdio MCP server (``tests/fixtures/echo_mcp_server.py``)
is spawned as a child process, its tools are surfaced as ``mcp__<server>__<tool>``
native tools, and they:

  * register under the SEPARATE ``mcp:*`` allowlist sentinel (distinct from
    ``custom:*``);
  * appear in ``/tools`` and ``/mcp/servers`` (with ``tools_loaded``/``tool_names``);
  * connect-test via ``/mcp/servers/{name}/test``;
  * actually RUN through ``ToolRegistry.invoke`` (permission-gated), returning the
    remote result;
  * are OFFERED to the agents whose loadout carries ``mcp:*`` (Builder) and NOT to
    those without it (Reviewer);
  * are permission-gated (headless denies ``mcp_call`` unless a server is trusted
    with ``auto_approve``), and SURVIVE a restart (a fresh app on the same root
    re-registers them from persisted config);
  * unregister live on delete.

Everything is a pure-python subprocess, so no network and no third-party server.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.core.models import AgentType
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult
from iron_jarvis.tools.registry import ToolRegistry

#: The real stdio MCP server fixture (advertises exactly ``echo`` + ``add``).
FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #
class _FakeTool(Tool):
    """A trivial in-memory tool (no-op execute) for white-box registry tests."""

    def __init__(self, name: str, permission_key: str = "") -> None:
        self.name = name
        self.description = f"fake {name}"
        self.input_schema = {"type": "object", "properties": {}}
        self.permission_key = permission_key

    async def execute(self, args, ctx) -> ToolResult:  # pragma: no cover - never run
        return ToolResult(ok=True, output="ok")


def _echo_body(auto_approve: bool = False) -> dict:
    """POST body that registers the real echo stdio server as ``echo``."""
    return {
        "name": "echo",
        "command": sys.executable,
        "args": [FIXTURE],
        "auto_approve": auto_approve,
    }


def _tool_names(client: TestClient) -> list[str]:
    return [t["name"] for t in client.get("/tools").json()["tools"]]


# --------------------------------------------------------------------------- #
# (1) Registry unit: mcp:* and custom:* sentinels are SEPARATE.
# --------------------------------------------------------------------------- #
def test_mcp_sentinel_is_separate_from_custom():
    reg = ToolRegistry()
    reg.register(_FakeTool("mcp__echo__echo", "mcp_call"), mcp=True)
    reg.register(_FakeTool("my_custom_tool"), custom=True)

    # The MCP tool is tracked as an MCP tool.
    assert reg.mcp_names() == ["mcp__echo__echo"]
    assert "my_custom_tool" not in reg.mcp_names()

    # "mcp:*" reaches the MCP tool but NOT the custom one.
    mcp_specs = {s["name"] for s in reg.specs(["mcp:*"])}
    assert "mcp__echo__echo" in mcp_specs
    assert "my_custom_tool" not in mcp_specs

    # "custom:*" reaches the custom tool but NOT the MCP one (separation).
    custom_specs = {s["name"] for s in reg.specs(["custom:*"])}
    assert "my_custom_tool" in custom_specs
    assert "mcp__echo__echo" not in custom_specs


# --------------------------------------------------------------------------- #
# (2) Registry unit: mcp_names(server) filters; unregister drops it.
# --------------------------------------------------------------------------- #
def test_mcp_names_by_server_and_unregister():
    reg = ToolRegistry()
    reg.register(_FakeTool("mcp__srv__a", "mcp_call"), mcp=True)
    reg.register(_FakeTool("mcp__srv__b", "mcp_call"), mcp=True)
    reg.register(_FakeTool("mcp__other__z", "mcp_call"), mcp=True)

    assert reg.mcp_names("srv") == ["mcp__srv__a", "mcp__srv__b"]
    assert reg.mcp_names("other") == ["mcp__other__z"]

    assert reg.unregister("mcp__srv__a") is True
    assert reg.mcp_names("srv") == ["mcp__srv__b"]
    assert "mcp__srv__a" not in reg.mcp_names()
    # unregister is idempotent-safe: a second call reports "absent".
    assert reg.unregister("mcp__srv__a") is False


# --------------------------------------------------------------------------- #
# (3) HTTP: adding the real fixture server loads BOTH tools live.
# --------------------------------------------------------------------------- #
def test_add_server_loads_two_tools_live(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        r = client.post("/mcp/servers", json=_echo_body(auto_approve=True))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["added"] is True
        assert body["tools_loaded"] == 2
        assert body["auto_approve"] is True

        # Both remote tools are now native tools in the loadout surface.
        names = _tool_names(client)
        assert "mcp__echo__echo" in names
        assert "mcp__echo__add" in names

        # /mcp/servers annotates the server with live tool counts + names.
        servers = client.get("/mcp/servers").json()["servers"]
        echo = next(s for s in servers if s["name"] == "echo")
        assert echo["tools_loaded"] == 2
        assert "echo" in echo["tool_names"] and "add" in echo["tool_names"]


# --------------------------------------------------------------------------- #
# (4) HTTP: the connect-test probe actually reaches the server.
# --------------------------------------------------------------------------- #
def test_server_test_probe_connects(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        client.post("/mcp/servers", json=_echo_body())
        t = client.post("/mcp/servers/echo/test").json()
        assert t["ok"] is True
        assert t["count"] == 2
        assert "echo" in t["tools"] and "add" in t["tools"]
        assert t["error"] is None


# --------------------------------------------------------------------------- #
# (5) The tool really EXECUTES through the registry (permission-gated).
# --------------------------------------------------------------------------- #
def test_mcp_tool_executes_through_registry(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        client.post("/mcp/servers", json=_echo_body())
        platform = client.app.state.platform
        reg = platform.registry

        ws = tmp_path / "ws"
        ws.mkdir()
        ctx = ToolContext(
            workspace=ws,
            session_id="s1",
            agent_run_id="r1",
            config=platform.config,
            event_bus=platform.event_bus,
            engine=platform.engine,
        )

        # Grant mcp_call for this invocation via the sanctioned per-session grant
        # path, then run it. (mcp_call is a deny-floor tool: an agent-definition
        # override can no longer raise it to "allow" — see permissions.py F1/F2 —
        # so a per-task grant must go through session_allow.)
        res = asyncio.run(
            reg.invoke(
                "mcp__echo__echo",
                {"text": "hello"},
                ctx,
                platform.permissions,
                session_allow=["mcp_call"],
            )
        )
        assert res.ok is True, res.error
        assert "hello" in res.output

        # And the arithmetic tool round-trips a real computed result.
        res2 = asyncio.run(
            reg.invoke(
                "mcp__echo__add",
                {"a": 2, "b": 3},
                ctx,
                platform.permissions,
                session_allow=["mcp_call"],
            )
        )
        assert res2.ok is True, res2.error
        assert "5" in res2.output


# --------------------------------------------------------------------------- #
# (6) Agent loadouts: mcp:* agents are OFFERED the tools; others are not.
# --------------------------------------------------------------------------- #
def test_agent_loadout_offers_mcp_only_to_mcp_agents(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        client.post("/mcp/servers", json=_echo_body())
        reg = client.app.state.platform.registry

        builder = {
            s["name"] for s in reg.specs(get_agent_definition(AgentType.BUILDER).tools)
        }
        assert any(n.startswith("mcp__echo__") for n in builder)

        reviewer = {
            s["name"] for s in reg.specs(get_agent_definition(AgentType.REVIEWER).tools)
        }
        assert not any(n.startswith("mcp__echo__") for n in reviewer)


# --------------------------------------------------------------------------- #
# (7) Permission gating + restart survival.
# --------------------------------------------------------------------------- #
def test_permission_gating_and_restart_survival(tmp_path):
    # First boot: no server yet -> headless resolver denies mcp_call.
    with TestClient(create_app(str(tmp_path))) as client:
        add = client.post("/mcp/servers", json=_echo_body(auto_approve=True)).json()
        assert add["tools_loaded"] == 2

        # The FIRST app's resolver was composed BEFORE the server existed, so it
        # still fail-closes mcp_call (auto_approve only applies at boot).
        perms = client.app.state.platform.permissions
        assert perms.authorize("mcp_call", {}).allowed is False

    # Second boot on the SAME root: config persisted the auto_approve server, so
    # the resolver is now composed to trust mcp_call — AND the tools are
    # re-registered from persisted config (restart survival).
    with TestClient(create_app(str(tmp_path))) as client2:
        perms2 = client2.app.state.platform.permissions
        assert perms2.authorize("mcp_call", {}).allowed is True

        servers = client2.get("/mcp/servers").json()["servers"]
        echo = next(s for s in servers if s["name"] == "echo")
        assert echo["tools_loaded"] == 2
        # The live tools are back in the registry after the "restart".
        assert set(client2.app.state.platform.registry.mcp_names()) == {
            "mcp__echo__echo",
            "mcp__echo__add",
        }


# --------------------------------------------------------------------------- #
# (8) Delete unregisters the live tools immediately.
# --------------------------------------------------------------------------- #
def test_delete_unregisters_live_tools(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        client.post("/mcp/servers", json=_echo_body())
        reg = client.app.state.platform.registry
        assert len(reg.mcp_names()) == 2

        d = client.delete("/mcp/servers/echo").json()
        assert d["removed"] == "echo"
        assert d["tools_unloaded"] == 2

        # Gone from the registry and from the model-facing /tools surface.
        assert reg.mcp_names() == []
        assert "mcp__echo__echo" not in _tool_names(client)
        # A second delete 404s (the server config is gone too).
        assert client.delete("/mcp/servers/echo").status_code == 404
