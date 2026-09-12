"""S-01 (v1.257.0): MCP packs connect AT ONCE, not one after another.

Plain words: if you have several tool packs installed, opening Iron Jarvis used
to wait for each one to say hello in turn. Now it greets them all together, so
the wait is about as long as the slowest single pack instead of all of them
added up.

HOW THE SLOWNESS IS REPRODUCED: by DELAYING the one step that actually waits --
building the transport -- not by raising a bound and hoping. Every timing
assertion here is a RATIO between two measurements taken on the same machine in
the same test (one pack vs several), never an absolute wall-clock threshold,
because an absolute threshold measures the hardware and goes red on CI.

Fully offline: FakeTransport, and a deliberate sleep.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from iron_jarvis.mcp import FakeTransport, mcp_tools
from iron_jarvis.mcp import tools as mcp_tools_mod

TOOLS_LIST = {
    "tools": [
        {"name": "search", "description": "Search.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "fetch", "description": "Fetch.", "inputSchema": {"type": "object", "properties": {}}},
    ]
}

#: Big enough that four of them added up is unmistakable next to one of them,
#: small enough that the whole file stays quick.
DELAY = 0.12


@pytest.fixture(autouse=True)
def _fresh_load_records():
    mcp_tools_mod._LOAD_STATUS.clear()
    yield
    mcp_tools_mod._LOAD_STATUS.clear()


def _pack(name: str) -> dict[str, Any]:
    return {"name": name, "command": "ij-fake-launcher", "args": []}


def _slow_transport(monkeypatch, delay_for) -> None:
    """Make building a pack's transport WAIT, which is what a real handshake does.

    `delay_for` maps a pack name to its delay, so one pack can be made slower
    than another and the test can tell completion order from config order.
    """

    def _build(cfg, _resolver):
        time.sleep(delay_for(cfg.get("name") or "mcp"))
        return FakeTransport({"tools/list": TOOLS_LIST})

    monkeypatch.setattr(mcp_tools_mod, "_build_transport", _build)


def _elapsed(configs) -> tuple[float, list]:
    start = time.perf_counter()
    tools = mcp_tools(configs)
    return time.perf_counter() - start, tools


def test_several_packs_cost_about_one_pack_not_the_sum_of_them(monkeypatch):
    """The whole point of S-01. Serially this is 4x one pack; together it is ~1x."""
    _slow_transport(monkeypatch, lambda _name: DELAY)

    one, tools_one = _elapsed([_pack("solo")])
    four, tools_four = _elapsed([_pack(f"p{i}") for i in range(4)])

    assert len(tools_one) == 2, "the fake pack really did hand over its two tools"
    assert len(tools_four) == 8, "all four packs loaded — this is not a speedup by skipping"

    # RATIO, not a wall clock. Serial gives ~4.0; concurrent gives ~1.0-1.3.
    # 2.0 sits far from both, so this is not a coin flip on a busy runner.
    assert four < one * 2.0, (
        f"four packs took {four:.3f}s against one pack's {one:.3f}s "
        f"(ratio {four / one:.2f}x) - that is the serial sum, not a concurrent connect"
    )


def test_tools_come_back_in_config_order_even_when_a_later_pack_answers_first(monkeypatch):
    """Concurrency must not reorder the registry.

    The FIRST pack in the config is made the SLOWEST, so completion order is the
    exact reverse of config order. A fan-out that collected results as they
    finished would return them backwards.
    """
    order = ["alpha", "beta", "gamma"]
    delays = {"alpha": DELAY * 2, "beta": DELAY, "gamma": 0.0}
    _slow_transport(monkeypatch, lambda name: delays[name])

    tools = mcp_tools([_pack(n) for n in order])

    servers: list[str] = []
    for tool in tools:
        if tool.name.split("__")[1] not in servers:
            servers.append(tool.name.split("__")[1])
    assert servers == order, (
        f"packs came back as {servers} but the config says {order} — "
        "results are being collected in completion order"
    )


def test_a_slow_pack_does_not_eat_another_packs_timeout_budget(monkeypatch):
    """The per-pack ceiling stays PER PACK and is never summed.

    Three packs that all hang must cost about ONE timeout, not three. Serially
    the same three cost 3x the ceiling, which is how a single wedged pack used
    to turn into a boot that looked broken.
    """
    monkeypatch.setenv("IRONJARVIS_MCP_CONNECT_TIMEOUT", "0.4")
    _slow_transport(monkeypatch, lambda _name: 5.0)  # never answers in time

    one, tools_one = _elapsed([_pack("hang-solo")])
    three, tools_three = _elapsed([_pack(f"hang{i}") for i in range(3)])

    assert tools_one == [] and tools_three == [], "a hung pack is skipped, not waited on forever"
    assert three < one * 2.0, (
        f"three hung packs took {three:.3f}s against one's {one:.3f}s "
        f"(ratio {three / one:.2f}x) - the timeouts are being paid one after another"
    )


def test_a_failing_pack_still_records_its_own_reason_beside_a_healthy_one(monkeypatch):
    """R-02's plain-words record survives concurrency, per pack, unclobbered.

    Concurrent writes to one shared status dict would be the obvious way to
    break this: two packs finishing together and one overwriting the other's
    row, so the Tools page shows a healthy pack as broken or vice versa.
    """

    def _build(cfg, _resolver):
        if cfg.get("name") == "broken":
            raise FileNotFoundError("npx not found")
        time.sleep(DELAY)  # the healthy one finishes LAST, after the failure
        return FakeTransport({"tools/list": TOOLS_LIST})

    monkeypatch.setattr(mcp_tools_mod, "_build_transport", _build)

    tools = mcp_tools([_pack("broken"), _pack("healthy")])

    assert [t.name for t in tools] == ["mcp__healthy__search", "mcp__healthy__fetch"]

    bad = mcp_tools_mod.load_status("broken")
    good = mcp_tools_mod.load_status("healthy")
    assert bad is not None and good is not None, "both packs kept a record of their own"
    assert bad["tools_loaded"] == 0
    assert bad["reason"], "a failed pack still says why, in plain words"
    assert good["tools_loaded"] == 2
    assert good["last_error"] is None, "the healthy pack's row was not overwritten by the failure"


def test_one_pack_takes_no_pool_at_all(monkeypatch):
    """The common case must run the same path it always ran.

    Proven by making a thread pool IMPOSSIBLE to create: with a single pack the
    load still succeeds, so no pool was involved.
    """
    import concurrent.futures

    def _no_pools(*_a, **_kw):
        raise AssertionError("a single pack must not build a thread pool")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _no_pools)
    _slow_transport(monkeypatch, lambda _name: 0.0)

    tools = mcp_tools([_pack("solo")])
    assert [t.name for t in tools] == ["mcp__solo__search", "mcp__solo__fetch"]
