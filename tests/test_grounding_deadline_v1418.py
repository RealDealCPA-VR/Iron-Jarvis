"""v1.418.0 (C3.4): a turn's grounding has a SOFT DEADLINE, and a coding
session's project map is CACHED, so the first word of a reply never waits on
the slowest hop or on a walk of the whole folder.

Three guarantees, each pinned without a wall-clock bar (a stalled hop waits
on an Event the test holds; walks are COUNTED through spies):

1. A grounding hop still running at ``chat_turn.GROUNDING_SOFT_S`` is left out
   of THIS turn — the turn starts (the model is asked) while the hop is still
   blocked, and the hop is NAMED on the receipt (``context.late``), in BOTH
   lanes. The required hops (the project block, the folder's instructions,
   the user's connector toggles) are never dropped. A turn whose hops all
   answer carries no ``late`` key (its receipt is as before).
2. The project map of an unchanged folder is served from a cache keyed by
   the folder's TOP TWO LEVELS: the second turn makes no walk of the folder
   (``project_map._walk`` is not called; at most one listing per top-level
   folder is read to check nothing there changed). A change at those levels
   rebuilds it.
3. A Build reply's second turn walks the folder ONCE (the finish check's own
   snapshot), never a second time for the map.
"""

from __future__ import annotations

import os
import threading

import pytest

import iron_jarvis.daemon.chat_turn as chat_turn
from iron_jarvis.harness import folder_changes as FC
from iron_jarvis.harness import project_map as PM
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_finish_lanes_v1386 import _body, _stream, env  # noqa: F401 — the fixture
from tests.test_wave3_chat_lanes_v1311 import LANES, _client, _post_turn, _scripted_router


# --------------------------------------------------------------------------- #
# 1. the soft deadline
# --------------------------------------------------------------------------- #


def test_the_soft_deadline_is_two_and_a_half_seconds_and_names_the_required_hops():
    assert chat_turn.GROUNDING_SOFT_S == 2.5
    assert chat_turn.GROUNDING_REQUIRED_HOPS == {"project", "folder_rules", "connector"}


@pytest.mark.parametrize("lane", LANES)
def test_a_stalled_hop_lets_the_turn_start_and_names_itself(tmp_path, monkeypatch, lane):
    """The memory fabric (a remote base, say) never answers while the turn
    runs: the model is still asked, the fabric's grounding is not in the
    prompt, and ``context.late`` names it. The hop is released only AFTER the
    turn has answered — so the turn cannot have waited for it."""
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 0.2)
    client = _client(tmp_path)
    platform = client.app.state.platform
    asked = threading.Event()
    entered = threading.Event()
    released_by_the_model: list[bool] = []

    class _StalledFabric:
        def ground(self, query, **kw):
            # Blocked until the MODEL is asked: a turn that waits for this hop
            # can never ask the model, so it waits out the bound below and
            # the hop's text lands in the prompt (red on the old code).
            entered.set()
            released_by_the_model.append(asked.wait(10))
            return "\n\nFAB-LATE"

        def __getattr__(self, item):
            raise AttributeError(item)

    monkeypatch.setattr(platform, "fabric", _StalledFabric())
    seen = _scripted_router(platform, monkeypatch, [], "ok.")
    real_complete, real_stream = platform.router.complete, platform.router.stream

    async def complete(**kw):
        asked.set()
        return await real_complete(**kw)

    async def stream(**kw):
        asked.set()
        async for f in real_stream(**kw):
            yield f

    monkeypatch.setattr(platform.router, "complete", complete)
    monkeypatch.setattr(platform.router, "stream", stream)
    body, _frames = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "anything about the ledger?"}],
    })
    assert entered.is_set(), "the fabric hop never ran"
    assert seen["systems"], "the model was never asked"
    assert "FAB-LATE" not in seen["systems"][0]
    assert "fabric" in (body["context"].get("late") or []), body["context"]


@pytest.mark.parametrize("lane", LANES)
def test_a_turn_whose_hops_all_answered_carries_no_late_key(tmp_path, monkeypatch, lane):
    client = _client(tmp_path)
    _scripted_router(client.app.state.platform, monkeypatch, [], "ok.")
    body, _frames = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert "late" not in body["context"], body["context"]


@pytest.mark.parametrize("lane", LANES)
def test_the_required_hops_are_never_dropped(tmp_path, monkeypatch, lane):
    """With a deadline of ZERO every optional hop that has not answered at
    once is late — but the project block and the connector toggles are still
    awaited and still in the prompt, and are never named late."""
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 0.0)
    client = _client(tmp_path)
    pid = client.post("/projects", json={"name": "Deadline"}).json()["id"]
    hold = threading.Event()

    def _conn(*a, **k):
        hold.wait(1.0)  # a slow connector memory read; it is awaited anyway
        return "\n\nCONN-MEMORY"

    import iron_jarvis.daemon.routes.chat as chat_routes

    for mod in (chat_turn, chat_routes):
        monkeypatch.setattr(mod, "_connector_memory_block", _conn, raising=False)
        monkeypatch.setattr(mod, "_resolve_connectors", lambda d, b: ([], ["brain"]), raising=False)
    seen = _scripted_router(client.app.state.platform, monkeypatch, [], "ok.")
    body, _frames = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "what is in this project?"}],
        "project_id": pid,
        "connectors": ["brain"],
    })
    system = seen["systems"][0]
    assert "# Project: Deadline" in system
    assert "CONN-MEMORY" in system
    late = body["context"].get("late") or []
    assert not ({"project", "connector", "folder_rules"} & set(late)), late


def test_a_hop_that_raises_in_time_still_raises_in_hop_order(tmp_path, monkeypatch):
    """The deadline does not swallow a required hop's error: the connector
    resolution's failure is re-raised as it always was."""
    client = _client(tmp_path)
    import iron_jarvis.daemon.routes.chat as chat_routes

    def _boom(d, b):
        raise RuntimeError("connector resolution failed")

    for mod in (chat_turn, chat_routes):
        monkeypatch.setattr(mod, "_resolve_connectors", _boom, raising=False)
    seen = _scripted_router(client.app.state.platform, monkeypatch, [], "ok.")
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code >= 500 and not seen["systems"], (r.status_code, r.text)


# --------------------------------------------------------------------------- #
# 2. the cached project map
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _fresh_map_cache():
    PM.forget()
    yield
    PM.forget()


def _proj(tmp_path):
    ws = tmp_path / "proj"
    (ws / "shop" / "rules").mkdir(parents=True)
    (ws / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (ws / "shop" / "cart.py").write_text("class Cart:\n    pass\n", encoding="utf-8")
    (ws / "shop" / "rules" / "tax.py").write_text("def tax(x):\n    return x\n", encoding="utf-8")
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "junk.js").write_text("x", encoding="utf-8")
    return ws


class _Spies:
    def __init__(self, monkeypatch):
        self.walks = 0
        self.scandirs: list[str] = []
        real_walk = PM._walk
        real_scandir = os.scandir

        def walk(base):
            self.walks += 1
            return real_walk(base)

        def scandir(path="."):
            self.scandirs.append(os.path.normcase(str(path)))
            return real_scandir(path)

        monkeypatch.setattr(PM, "_walk", walk)
        monkeypatch.setattr(PM.os, "scandir", scandir)


async def test_the_second_turn_walks_nothing(tmp_path, monkeypatch):
    ws = _proj(tmp_path)
    spies = _Spies(monkeypatch)
    first = await PM.for_turn(ws, window=None)
    assert spies.walks == 1
    spies.scandirs.clear()
    again = await PM.for_turn(ws, window=None)
    assert again.text == first.text
    assert spies.walks == 1, "the second turn walked the folder again"
    # At most the folder's own listing and one per top-level folder it keeps
    # (here: shop/) — never node_modules, never shop/rules/.
    listed = set(spies.scandirs)
    assert listed <= {os.path.normcase(str(ws)), os.path.normcase(str(ws / "shop"))}, listed
    assert len(spies.scandirs) <= 2


async def test_a_change_in_the_top_two_levels_rebuilds_the_map(tmp_path, monkeypatch):
    ws = _proj(tmp_path)
    spies = _Spies(monkeypatch)
    await PM.for_turn(ws, window=None)
    # A new file at the top level.
    (ws / "new.py").write_text("def new():\n    pass\n", encoding="utf-8")
    m = await PM.for_turn(ws, window=None)
    assert "new.py — new()" in m.text and spies.walks == 2
    # A file in a top-level folder changes (its time moves on).
    cart = ws / "shop" / "cart.py"
    cart.write_text("class Cart:\n    pass\n\n\ndef refund():\n    pass\n", encoding="utf-8")
    os.utime(cart, ns=(cart.stat().st_atime_ns, cart.stat().st_mtime_ns + 5_000_000_000))
    m = await PM.for_turn(ws, window=None)
    assert "refund()" in m.text and spies.walks == 3
    # A file added two folders down changes its folder's time (a level-two entry).
    (ws / "shop" / "rules" / "vat.py").write_text("def vat():\n    pass\n", encoding="utf-8")
    rules = ws / "shop" / "rules"
    os.utime(rules, ns=(rules.stat().st_atime_ns, rules.stat().st_mtime_ns + 5_000_000_000))
    m = await PM.for_turn(ws, window=None)
    assert "vat.py" in m.text and spies.walks == 4


async def test_the_map_cache_is_bounded_and_versioned(tmp_path):
    assert PM.MAP_CACHE_MAX == 64
    assert isinstance(PM.MAP_VERSION, int)
    for i in range(PM.MAP_CACHE_MAX + 5):
        d = tmp_path / f"p{i}"
        d.mkdir()
        (d / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
        PM.build(d, limit=PM.share_chars(None))
    assert len(PM._MAP_CACHE) == PM.MAP_CACHE_MAX
    # Another version of the map never serves an older version's text.
    key = next(iter(PM._MAP_CACHE))
    assert key[-1] == PM.MAP_VERSION


async def test_a_map_with_names_left_unread_is_never_cached(tmp_path, monkeypatch):
    ws = _proj(tmp_path)
    monkeypatch.setattr(PM, "NAMES_DEADLINE_S", 0.0)
    PM.build(ws, limit=PM.share_chars(None))
    assert not PM._MAP_CACHE, "a map with names left unread was cached"


# --------------------------------------------------------------------------- #
# 3. one walk per turn for the finish check
# --------------------------------------------------------------------------- #


async def test_the_second_build_turn_walks_the_folder_once(env, monkeypatch):  # noqa: F811
    platform, ws, wire = env
    walks: list[str] = []
    real_take, real_walk = FC.take, PM._walk
    monkeypatch.setattr(FC, "take", lambda folder, **kw: (walks.append("snapshot"), real_take(folder, **kw))[1])
    monkeypatch.setattr(PM, "_walk", lambda base: (walks.append("map"), real_walk(base))[1])
    FC.forget_kept()
    read = [ToolCall(id="r", name="read_file", arguments={"path": "app.py"})]
    wire([read, "X is 1."])
    await _stream(platform, _body(ws, text="what is X in app.py?"))
    first = list(walks)
    walks.clear()
    wire([read, "Still 1."])
    await _stream(platform, _body(ws, text="and now?"))
    assert first.count("map") == 1
    assert walks == ["snapshot"], walks


# --------------------------------------------------------------------------- #
# review (v1.418.0): the top-two-levels check is BOUNDED like the walk
# --------------------------------------------------------------------------- #


def test_the_top_signature_stops_at_its_entry_cap_and_the_turn_walks_instead(tmp_path, monkeypatch):
    """A folder whose top two levels hold more entries than the check's cap
    (a data folder with 100,000 files at level two) must not be listed whole
    before EVERY turn — the check gives up (None: the bounded walk decides,
    as before) and stops listing at once."""
    ws = tmp_path / "big"
    for i in range(30):
        (ws / f"d{i:02d}").mkdir(parents=True)
        (ws / f"d{i:02d}" / "a.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(PM, "TOP_SIGNATURE_MAX_ENTRIES", 10, raising=False)
    spies = _Spies(monkeypatch)
    assert PM._top_signature(ws) is None
    assert len(spies.scandirs) <= 2, spies.scandirs


def test_the_top_signature_stops_at_the_walks_deadline(tmp_path, monkeypatch):
    ws = tmp_path / "slow"
    for i in range(5):
        (ws / f"d{i}").mkdir(parents=True)
    clock = iter(range(0, 1000, 10))  # every reading 10 s later than the last
    monkeypatch.setattr(PM, "_now", lambda: next(clock))
    spies = _Spies(monkeypatch)
    assert PM._top_signature(ws) is None
    assert len(spies.scandirs) <= 2, spies.scandirs


def test_a_small_folder_still_has_a_signature(tmp_path):
    ws = _proj(tmp_path)
    assert isinstance(PM._top_signature(ws), str)

