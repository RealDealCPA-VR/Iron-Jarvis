# ruff: noqa: F811 — the fixtures are imported from the join tests and named as arguments.
"""A looked-up memory search asks first only when a memory base is really off
this PC (v1.490.2).

Since v1.479.0 a memory search (`recall`, `ltm_search`) that a lookup brought
always showed a card, because a memory base MAY be on another machine. On an
install whose only memory is its own notes folder nothing can leave, and that
card was the most common one on messages that asked for nothing. Now the
install is looked at: every base a folder on this PC, and the embedding
server (if one is set) on the user's own network = it joins like any reader
that stays on this PC. Anything else, or anything that cannot be told, asks
as before.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.ltm.base import LTMConnector
from tests.test_capability_join_v1479 import _join, desk  # noqa: F401 — fixture

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

QUERY = "search long-term memory notes"


class Remote(LTMConnector):
    name = "notion"

    def search(self, query, k=5):
        return []

    def append(self, title, content):
        return "r"


def _platform(app):
    return app.state.platform


def test_an_install_with_only_its_own_notes_stays_here(desk):
    app, _ws = desk
    p = _platform(app)
    assert J.memory_stays_here(p) is True
    for name in sorted(J.MEMORY_BASE_READERS):
        assert J.asks_when_found(p.registry, name, p) is False, name


def test_a_base_off_this_pc_asks_first(desk):
    app, _ws = desk
    p = _platform(app)
    p.ltm.register(Remote())
    assert J.memory_stays_here(p) is False
    for name in sorted(J.MEMORY_BASE_READERS):
        assert J.asks_when_found(p.registry, name, p) is True, name


@pytest.mark.parametrize("provider,url,stays", [
    ("auto", "", True),
    ("auto", "http://127.0.0.1:11434", True),
    ("auto", "http://192.168.1.40:11434/v1", True),
    ("ollama", "https://embeddings.example.com", False),
    ("auto", "https://embeddings.example.com/v1", False),
    ("mock", "https://embeddings.example.com", True),  # the offline embedder calls nothing
])
def test_the_embedding_server_counts_too(desk, provider, url, stays):
    app, _ws = desk
    p = _platform(app)
    p.config.embedder_provider = provider
    p.config.ollama_base_url = url
    assert J.memory_stays_here(p) is stays


def test_what_cannot_be_told_asks(desk):
    app, _ws = desk
    p = _platform(app)
    # No platform handed over (an older caller): it asks.
    assert J.asks_when_found(p.registry, "recall") is True
    assert J.memory_stays_here(None) is False
    assert J.memory_stays_here(SimpleNamespace(config=p.config)) is False  # no memory manager

    def boom():
        raise RuntimeError("the list of bases cannot be read")

    assert J.memory_stays_here(SimpleNamespace(config=p.config, ltm=SimpleNamespace(connectors=boom))) is False
    assert J.memory_stays_here(SimpleNamespace(ltm=p.ltm)) is False  # no settings to read


def test_only_the_memory_readers_are_judged_this_way(desk):
    """A web tool reaches the internet whatever the memory bases are, and a
    tool that changes something always asks."""
    app, _ws = desk
    p = _platform(app)
    assert J.memory_stays_here(p) is True
    assert J.asks_when_found(p.registry, "web_search", p) is True
    assert J.asks_when_found(p.registry, "ltm_append", p) is True
    assert J.asks_when_found(p.registry, "history_search", p) is False


def test_the_join_grants_it_here_and_asks_for_it_with_a_base_elsewhere(desk):
    app, _ws = desk
    joined, state, _ = _join(app, QUERY)
    assert "ltm_search" in joined, joined
    assert "ltm_search" in state["armed"] and state["overrides"]["ltm_search"] == "allow"
    assert "ltm_search" not in state["ask_armed"] and "ltm_search" not in state["found_asks"]

    _platform(app).ltm.register(Remote())
    joined, state, _ = _join(app, QUERY, turn_id="turn-unit-remote")
    assert "ltm_search" in joined
    assert "ltm_search" in state["ask_armed"] and "ltm_search" in state["found_asks"]
    assert "ltm_search" not in state["armed"] and state["overrides"]["ltm_search"] == "ask"


def test_where_nobody_can_be_asked_it_joins_only_when_it_stays_here(desk):
    app, _ws = desk
    joined, state, _ = _join(app, QUERY, can_ask=False)
    assert "ltm_search" in joined and "ltm_search" in state["armed"]
    _platform(app).ltm.register(Remote())
    joined, state, _ = _join(app, QUERY, can_ask=False, turn_id="turn-unit-remote")
    assert "ltm_search" not in joined and "ltm_search" not in state["armed"]


def test_the_lookups_own_line_says_the_same(desk):
    app, _ws = desk
    p = _platform(app)
    here, _ = CT_TOOL.answer(p, QUERY, 5)
    line = next(ln for ln in here.splitlines() if "tool ltm_search." in ln)
    assert "approval card" not in line, line
    p.ltm.register(Remote())
    away, _ = CT_TOOL.answer(p, QUERY, 5)
    line = next(ln for ln in away.splitlines() if "tool ltm_search." in ln)
    assert "approval card" in line, line
