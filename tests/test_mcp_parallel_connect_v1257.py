"""S-01 (v1.257.0): MCP packs connect AT ONCE, not one after another.

Plain words: if you have several tool packs installed, opening Iron Jarvis used
to wait for each one to say hello in turn. Now it greets them all together, so
the wait is about as long as the slowest single pack instead of all of them
added up.

HOW THE CONCURRENCY IS PROVEN (v1.277.0): by CONSTRUCTION, not by a stopwatch.
The fake transport build waits at a ``threading.Barrier`` that only releases
once TWO builds are inside it at the same moment. A serial loop parks the first
build there alone until the barrier's timeout breaks it, so serial code cannot
pass; concurrent code passes however starved the machine is. The v1.257.0 cut
asserted a wall-clock RATIO (four packs under twice one pack) and went red on
the release runner at 3.21x with nothing wrong -- a ratio between two sleeps is
still a measurement of the scheduler, and a loaded scheduler starts four threads
late enough to look serial.

Fully offline: FakeTransport, and a barrier.
"""

from __future__ import annotations

import threading
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


def test_several_packs_connect_at_once_not_one_after_another(monkeypatch):
    """The whole point of S-01, proven by construction (v1.277.0).

    Every build waits at a two-party barrier. Four packs: the first two builds
    meet and release, then the next two. A serial loop never has two builds in
    flight, so its first build waits alone until the barrier times out and
    breaks — the failure names itself. No clock is compared with any other.
    """
    barrier = threading.Barrier(2, timeout=5.0)
    broken: list[str] = []

    def _build(cfg, _resolver):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            broken.append(cfg.get("name") or "mcp")
            raise
        return FakeTransport({"tools/list": TOOLS_LIST})

    monkeypatch.setattr(mcp_tools_mod, "_build_transport", _build)

    tools_four = mcp_tools([_pack(f"p{i}") for i in range(4)])

    assert not broken, (
        f"builds {broken} waited alone at the barrier until it broke - the packs "
        "were connected one after another, not together"
    )
    assert len(tools_four) == 8, "all four packs loaded — this is not a speedup by skipping"


def test_one_pack_alone_still_connects(monkeypatch):
    """A single pack needs no companion: the pool is sized to the config, and one
    build must not wait for a second that never comes."""
    _slow_transport(monkeypatch, lambda _name: 0.0)
    _, tools_one = _elapsed([_pack("solo")])
    assert len(tools_one) == 2, "the fake pack really did hand over its two tools"


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

    real_pool = concurrent.futures.ThreadPoolExecutor

    def _no_pools(*a, **kw):
        # asyncio's default executor (thread_name_prefix "asyncio") is the
        # hop `MCPClient` makes for a blocking transport since v1.291.0
        # (io-02); it is not the connect fan-out this pin is about.
        if kw.get("thread_name_prefix") == "asyncio":
            return real_pool(*a, **kw)
        raise AssertionError("a single pack must not build a thread pool")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _no_pools)
    _slow_transport(monkeypatch, lambda _name: 0.0)

    tools = mcp_tools([_pack("solo")])
    assert [t.name for t in tools] == ["mcp__solo__search", "mcp__solo__fetch"]
