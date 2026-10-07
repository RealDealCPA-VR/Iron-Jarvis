"""v1.311.0 review of the deferred MCP boot: a turn that starts while packs are
still loading WAITS a bounded moment for them, and if any are still starting it
SAYS so on the Tools seam — a short tool list must never read as complete
(the model would otherwise tell the user a pack tool "does not exist").

Pinned at the helper (``mcp.tools.wait_for_starting_packs`` /
``packs_starting_note``) and end to end through BOTH chat lanes of the real
app factory (lock-step), with only the router scripted.
"""

from __future__ import annotations

import asyncio


import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import tools as mcp_tools
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

PACK = "slowpack-v1311"


@pytest.fixture(autouse=True)
def _clean_status():
    yield
    mcp_tools._LOAD_STATUS.pop(PACK, None)


class _Plat:
    def __init__(self, ready=None):
        if ready is not None:
            self.mcp_ready = ready


# --------------------------------------------------------------------------- #
# the helper
# --------------------------------------------------------------------------- #
def test_a_pack_that_finishes_inside_the_wait_leaves_no_note():
    async def scenario():
        ready = asyncio.Event()
        mcp_tools.mark_starting([{"name": PACK}])

        def _finish():
            mcp_tools._LOAD_STATUS[PACK]["state"] = "ready"
            ready.set()

        asyncio.get_running_loop().call_later(0.05, _finish)
        return await mcp_tools.wait_for_starting_packs(_Plat(ready), timeout_s=5)

    assert asyncio.run(scenario()) == []


def test_a_pack_still_starting_after_the_bound_is_named():
    async def scenario():
        mcp_tools.mark_starting([{"name": PACK}])
        return await mcp_tools.wait_for_starting_packs(_Plat(asyncio.Event()), timeout_s=0.05)

    still = asyncio.run(scenario())
    assert still == [PACK]
    note = mcp_tools.packs_starting_note(still)
    assert PACK in note and "still starting" in note
    assert "never that the tool does not exist" in note


def test_control_a_platform_that_loaded_synchronously_is_never_told():
    """`_LOAD_STATUS` is process-wide: a CLI-built platform (no lifespan, no
    `mcp_ready`) or one whose load finished must not inherit another's
    starting record — and must not wait."""
    mcp_tools.mark_starting([{"name": PACK}])
    done = asyncio.Event()
    done.set()
    assert asyncio.run(mcp_tools.wait_for_starting_packs(_Plat())) == []
    assert asyncio.run(mcp_tools.wait_for_starting_packs(_Plat(done))) == []
    assert mcp_tools.packs_starting_note([]) == ""


# --------------------------------------------------------------------------- #
# both chat lanes (lock-step)
# --------------------------------------------------------------------------- #
def _router_capturing_system(platform, monkeypatch) -> list[str]:
    systems: list[str] = []

    async def fake_complete(*, system, messages, tools, **kw):
        systems.append(system)
        return RouteResult(LLMResponse(text="ok.", tool_calls=[], usage={}), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        systems.append(system)
        resp = LLMResponse(text="ok.", tool_calls=[], usage={})
        yield {"type": "text", "text": "ok."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    return systems


def _turn(client, lane):
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    if lane == "post":
        r = client.post("/chat", json=payload)
    else:
        r = client.post("/chat/stream", json=payload)
    assert r.status_code == 200, r.text
    if lane == "stream":
        assert "event: done" in r.text, r.text[-400:]


@pytest.mark.parametrize("lane", ("post", "stream"))
def test_a_turn_during_pack_startup_names_the_starting_pack(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    systems = _router_capturing_system(platform, monkeypatch)
    monkeypatch.setattr(mcp_tools, "PACKS_TURN_WAIT_S", 0.05)
    platform.mcp_ready = asyncio.Event()  # the lifespan's load is "still running"
    mcp_tools.mark_starting([{"name": PACK}])
    _turn(client, lane)
    assert systems, "the router was never reached"
    assert PACK in systems[0] and "still starting" in systems[0], systems[0][-600:]


@pytest.mark.parametrize("lane", ("post", "stream"))
def test_control_no_note_once_the_packs_are_up(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    systems = _router_capturing_system(platform, monkeypatch)
    ready = asyncio.Event()
    ready.set()
    platform.mcp_ready = ready
    mcp_tools.mark_starting([{"name": PACK}])  # a stale record must not leak in
    _turn(client, lane)
    assert systems and "still starting" not in systems[0]


def test_the_lanes_and_the_runtime_share_the_one_helper():
    """Lock-step by construction: both lanes reach the note through the shared
    grounding helper's field, and the agent runtime calls the same pair."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
    lane_post = (root / "daemon" / "chat_turn.py").read_text(encoding="utf-8")
    lane_stream = (root / "daemon" / "routes" / "chat.py").read_text(encoding="utf-8")
    runtime = (root / "agents" / "runtime.py").read_text(encoding="utf-8")
    assert "out.packs_starting = await wait_for_starting_packs(platform)" in lane_post
    assert "note = packs_starting_note(self.packs_starting)" in lane_post
    for src in (lane_post, lane_stream):
        assert "system += _grounding.after_skill()" in src
    assert "await wait_for_starting_packs(self.p)" in runtime
    assert "packs_starting_note(packs_starting)" in runtime



def test_redetect_looks_again_for_a_cli_installed_a_moment_ago(tmp_path, monkeypatch):
    """Review (v1.311.0): the PATH lookup is memoised now, so a CLI the user
    installed seconds ago read as "not installed" on Re-detect until the memo
    expired. POST /providers/rescan forgets the memo first."""
    import time

    import iron_jarvis.providers.manager as mgr

    # tests/conftest.py stubs the presence check to False suite-wide; this
    # test is ABOUT the memo, so it puts the real one back for itself.
    monkeypatch.setattr(
        mgr.ProviderManager, "_cli_binary_present", staticmethod(mgr.cli_binary_present)
    )
    client = TestClient(create_app(str(tmp_path)))
    with mgr._CLI_PRESENCE_LOCK:
        mgr._CLI_PRESENCE["claude"] = (False, time.monotonic())  # fresh "absent"
    installed = {"claude"}
    monkeypatch.setattr(mgr, "_probe_cli_binary", lambda b: b in installed)
    r = client.post("/providers/rescan")
    assert r.status_code == 200, r.text
    with mgr._CLI_PRESENCE_LOCK:
        got = mgr._CLI_PRESENCE.get("claude")
    assert got is not None and got[0] is True, got
    mgr.invalidate_cli_presence()


def test_a_starting_pack_reads_as_starting_not_broken(tmp_path):
    """Review (v1.311.0): in the seconds a pack loads after boot, the Tools row
    and the doctor said "0 tools" / "didn't start". Both now say starting."""
    import importlib
    from types import SimpleNamespace

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    mcp_tools.mark_starting([{"name": PACK}])
    plat = SimpleNamespace(
        config=SimpleNamespace(mcp_servers=[{"name": PACK, "command": "uvx"}]),
        registry=SimpleNamespace(mcp_names=lambda name: []),
    )
    row = doctor.check_mcp(plat)
    assert row["ok"] is True, row
    assert "still starting" in row["detail"] and PACK in row["detail"], row

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": PACK, "command": "uvx", "args": []}]
    rows = client.get("/mcp/servers").json()["servers"]
    mine = [r for r in rows if r["name"] == PACK]
    assert mine and mine[0]["state"] == "starting", mine


def test_a_retry_closes_the_client_of_the_tools_it_replaces(tmp_path):
    """Review (v1.311.0): a Retry unregistered a pack's tools and never closed
    their client, orphaning its stdio child — and with packs loading after
    boot, a Retry in that window replaces the boot load's tools."""
    from iron_jarvis.mcp.client import FakeTransport

    tools_list = {"tools": [{"name": "fetch", "description": "f", "inputSchema": {"type": "object"}}]}
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "srv", "transport_obj": FakeTransport({"tools/list": tools_list})}]
    assert client.post("/mcp/servers/srv/reload").json()["ok"] is True
    first = platform.registry.get(platform.registry.mcp_names("srv")[0])
    closed: list[int] = []
    first.client.close = lambda: closed.append(1)  # type: ignore[method-assign]
    assert client.post("/mcp/servers/srv/reload").json()["ok"] is True
    assert closed == [1], "the replaced tools' client must be closed exactly once"
    now = platform.registry.get(platform.registry.mcp_names("srv")[0])
    assert now is not first
