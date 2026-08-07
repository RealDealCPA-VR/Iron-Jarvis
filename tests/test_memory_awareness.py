"""Memory awareness index + comm project reach (v1.141.0, Pair Y).

Covers:

* ``memory_index_block`` composition — bases w/ honest cheap counts, mcp
  bases by name only (NO network), memory-graph layer counts, project
  binding + bound-base recent TITLES (never content), title limit, char
  budget, one-line hygiene, ``""`` when nothing, never-raise on poisoned /
  radioactive platforms (the roster's bar).
* runtime injection for ALL agent types (a reviewer — not just the
  delegation types the roster gates on).
* comm project threading end-to-end — a project-tagged comm thread rides
  its project_id into every ChatBody turn AND into the escalated session
  (which then carries the project-context spine in its system prompt).
* thread-store heal semantics: a dashboard-deleted, project-tagged thread
  re-mints WITH its project; ``/new`` retire is a deliberate fresh start.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import InboundMessage, MockChannel, Notifier
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm.brain import MarkdownBrainConnector
from iron_jarvis.ltm.mcp_brain import McpBrainConnector
from iron_jarvis.memory import index_block as index_block_mod
from iron_jarvis.memory.index_block import memory_index_block

_HEADER = "# What I can remember"
#: "when available" is load-bearing honesty — chat only arms `recall` when
#: the memory sentence rule fires; agents always have it (see index_block).
_CLOSING = (
    "Search these with the recall tool when available"
    " before assuming something isn't known."
)


@pytest.fixture(autouse=True)
def _fresh_scan_cache():
    """The folder-scan cache is module-level (it exists to serve REAL turns);
    tests must never see another test's scan."""
    index_block_mod._scan_cache.clear()
    yield
    index_block_mod._scan_cache.clear()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_project(platform, **kwargs: Any) -> str:
    with session_scope(platform.engine) as db:
        project = Project(name=kwargs.pop("name", "P"), **kwargs)
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id


def _titles_line(block: str) -> list[str]:
    for line in block.splitlines():
        if line.startswith("- Recent notes: "):
            return [t for t in line[len("- Recent notes: ") :].split("; ") if t]
    return []


class _TripwireMcp(McpBrainConnector):
    """An mcp-kind base that EXPLODES on any use — the block must list it by
    name+kind without ever touching search/append/list (all would be network)."""

    def search(self, query: str, k: int = 5):  # pragma: no cover — must not run
        raise AssertionError("memory_index_block touched the network (search)")

    def append(self, title: str, content: str):  # pragma: no cover
        raise AssertionError("memory_index_block touched the network (append)")

    def list_items(self, limit: int = 60):  # pragma: no cover
        raise AssertionError("memory_index_block touched the network (list_items)")


# --------------------------------------------------------------------------- #
# block composition
# --------------------------------------------------------------------------- #
def test_block_lists_bases_counts_graph_and_titles(platform):
    platform.ltm.append("Alpha Plan", "the alpha content body", source="brain")
    platform.ltm.append("Beta Notes", "the beta content body", source="brain")
    platform.memory.write("user", "pref", "likes coffee")

    block = memory_index_block(platform)

    assert block.startswith(_HEADER)
    assert block.rstrip().endswith(_CLOSING)
    assert "brain (markdown, 2 notes)" in block
    assert "- Memory graph: 1 user" in block
    # Titles only — NEVER content. (A markdown store's title is the file
    # stem, i.e. the slugified note title — same identity /ltm/browse shows.)
    assert "alpha-plan" in block and "beta-notes" in block
    assert "alpha content" not in block and "beta content" not in block
    # No leading blank lines — callers join with "\n\n" (roster contract).
    assert not block.startswith("\n")


def test_counts_singular_and_empty(platform, tmp_path):
    platform.ltm.append("Only One", "x", source="brain")
    empty = MarkdownBrainConnector(tmp_path / "empty-vault")
    empty.name = "vault"
    platform.ltm.register(empty)

    block = memory_index_block(platform)
    assert "brain (markdown, 1 note)" in block  # singular, honest
    assert "vault (markdown, empty)" in block  # 0 is said, not fabricated


def test_mcp_base_listed_by_name_only_and_no_network(platform):
    platform.ltm.register(_TripwireMcp("mybrain", url="http://127.0.0.1:9"))
    block = memory_index_block(platform)
    # Name + kind, NO count (counting an MCP brain is a network call).
    assert "mybrain (mcp)" in block
    assert "mybrain (mcp," not in block


def test_unknown_connector_kind_lists_bare_name(platform):
    platform.ltm.register(SimpleNamespace(name="mystery"))
    block = memory_index_block(platform)
    assert "mystery" in block
    assert "mystery (" not in block  # no guessed kind, no guessed count


def test_project_binding_note_and_bound_titles_only(platform, tmp_path):
    work = MarkdownBrainConnector(tmp_path / "work-notes")
    work.name = "work"
    platform.ltm.register(work)
    platform.ltm.append("Brain Only Note", "x", source="brain")
    platform.ltm.append("Work Note One", "y", source="work")
    pid = _make_project(platform, memory_sources=json.dumps(["work"]))

    block = memory_index_block(platform, project_id=pid)
    assert "- This project searches bases: work" in block
    assert "work-note-one" in _titles_line(block)
    assert "brain-only-note" not in _titles_line(block)  # bound bases only

    # Unbound (no project): titles draw from ALL local bases, no binding note.
    block2 = memory_index_block(platform)
    assert "This project searches" not in block2
    assert "brain-only-note" in _titles_line(block2)
    assert "work-note-one" in _titles_line(block2)


def test_title_limit_default_and_override(platform):
    for i in range(8):
        platform.ltm.append(f"Note Number {i}", "x", source="brain")
    assert len(_titles_line(memory_index_block(platform))) == 6  # default cap
    assert len(_titles_line(memory_index_block(platform, limit_titles=2))) == 2
    assert _titles_line(memory_index_block(platform, limit_titles=0)) == []


def test_block_stays_within_budget_and_one_lines_names(platform):
    # A hostile base name must not escape the bullet list as a bare line.
    platform.ltm.register(SimpleNamespace(name="bad\nname base"))
    for i in range(10):
        platform.ltm.register(
            SimpleNamespace(name=f"some-quite-long-connector-name-{i:02d}")
        )
    for i in range(8):
        platform.ltm.append(
            f"A Really Quite Long Note Title For Budgeting {i}", "x", source="brain"
        )
    platform.memory.write("user", "k", "v")
    pid = _make_project(platform, memory_sources=json.dumps(["brain"]))

    block = memory_index_block(platform, project_id=pid)
    assert len(block) <= 700
    assert "bad\nname" not in block
    assert "bad name base" in block


# --------------------------------------------------------------------------- #
# empty + never-raise (the roster reviewer's bar)
# --------------------------------------------------------------------------- #
def test_returns_empty_string_when_nothing_to_say():
    assert memory_index_block(None) == ""
    assert memory_index_block(object()) == ""
    assert memory_index_block(SimpleNamespace(ltm=None, memory=None)) == ""


class _Poisoned:
    def connectors(self):
        raise RuntimeError("ltm is on fire")

    def list(self, layer):
        raise RuntimeError("memory is on fire")

    LAYERS = ("session", "project", "user", "org")


def test_poisoned_stores_never_raise():
    p = SimpleNamespace(ltm=_Poisoned(), memory=_Poisoned(), fabric=None)
    assert memory_index_block(p) == ""
    assert memory_index_block(p, project_id="proj_x") == ""


def test_radioactive_platform_never_raises():
    # The PLATFORM ITSELF blows up on any attribute access — getattr defaults
    # don't save you from a raising __getattr__ (the roster's exact probe).
    class _Radioactive:
        def __getattr__(self, item):
            raise RuntimeError("attribute access is on fire")

    assert memory_index_block(_Radioactive()) == ""
    assert memory_index_block(_Radioactive(), project_id="p", limit_titles=3) == ""


def test_one_broken_store_costs_only_its_line(platform, monkeypatch):
    platform.memory.write("user", "k", "still here")
    monkeypatch.setattr(
        platform.ltm, "connectors", lambda: (_ for _ in ()).throw(RuntimeError("x"))
    )
    block = memory_index_block(platform)
    assert "- Memory graph: 1 user" in block  # graph survives a poisoned ltm
    assert "Long-term bases" not in block


def test_project_binding_note_alone_is_not_a_block():
    # A bound project over bases nobody can see would be an index of nothing —
    # the binding line must not keep the block alive on its own.
    p = SimpleNamespace(
        ltm=None,
        memory=None,
        fabric=SimpleNamespace(_project_bases=lambda pid: ["ghost-base"]),
    )
    assert memory_index_block(p, project_id="p1") == ""


class _MidGlobBoom(MarkdownBrainConnector):
    """A local markdown base whose glob dies MIDWAY (drive yanked, permission
    wall behind a junction). The block must survive, list the base without a
    count, and leak none of the partially-scanned files."""

    def _files(self):  # type: ignore[override]
        yield self.dir / "seen-before-the-crash.md"
        raise OSError("device went away mid-glob")


class _PropBoom(MarkdownBrainConnector):
    """Nastier: the ``_files`` ATTRIBUTE ACCESS itself raises."""

    @property
    def _files(self):  # type: ignore[override]
        raise RuntimeError("attribute access exploded")


def test_files_raising_mid_glob_survives_and_stays_honest(platform, tmp_path):
    boom = _MidGlobBoom(tmp_path / "yanked")
    boom.name = "yanked"
    platform.ltm.register(boom)
    prop = _PropBoom(tmp_path / "cursed")
    prop.name = "cursed"
    platform.ltm.register(prop)
    platform.ltm.append("Solid Note", "x", source="brain")

    block = memory_index_block(platform)
    assert "yanked (markdown)" in block  # listed, kind known, NO count
    assert "yanked (markdown," not in block
    assert "cursed (markdown)" in block
    assert "cursed (markdown," not in block
    assert "brain (markdown, 1 note)" in block  # a healthy base is unaffected
    assert "seen-before-the-crash" not in block  # partial scans contribute nothing


def test_budget_and_never_raise_under_unicode_adversaries(platform):
    for i in range(6):
        platform.ltm.register(SimpleNamespace(name=("メモ帳🧠ключ" * 30) + str(i)))
    for i in range(8):
        platform.ltm.append(f"Note {i} " + "标题いろは🧠" * 12, "x", source="brain")
    platform.memory.write("user", "k", "v")
    block = memory_index_block(platform)
    assert block.startswith(_HEADER)
    assert len(block) <= 700


# --------------------------------------------------------------------------- #
# folder-scan cache — cheapness under load (measured: an uncached scan of a
# 2000-note vault cost ~81ms PER TURN on Windows; 10k notes ~600ms)
# --------------------------------------------------------------------------- #
def _brain_conn(platform):
    return next(c for c in platform.ltm.connectors() if c.name == "brain")


def test_scan_cache_avoids_reglobbing_within_ttl(platform, monkeypatch):
    platform.ltm.append("Cached Note", "x", source="brain")
    conn = _brain_conn(platform)
    calls = {"n": 0}
    real_files = conn._files

    def counting():
        calls["n"] += 1
        return real_files()

    monkeypatch.setattr(conn, "_files", counting)
    b1 = memory_index_block(platform)
    b2 = memory_index_block(platform)
    assert "brain (markdown, 1 note)" in b1
    assert b1 == b2
    assert calls["n"] == 1  # the second turn is served from the cache


def test_scan_cache_sees_a_new_append_immediately(platform):
    """"remember this", then asking on the next turn, must show the fresh count
    and title — never the pre-append scan."""
    platform.ltm.append("First Note", "x", source="brain")
    assert "brain (markdown, 1 note)" in memory_index_block(platform)
    platform.ltm.append("Second Note", "y", source="brain")
    block = memory_index_block(platform)
    assert "brain (markdown, 2 notes)" in block
    assert "second-note" in _titles_line(block)


def test_a_new_append_is_seen_even_when_the_folder_mtime_NEVER_MOVES(
    platform, monkeypatch
):
    """The deterministic version of the test above — and the reason v1.146.1
    exists.

    The cache used to bust its entry by comparing the FOLDER's mtime, on the
    assumption that writing a file moves it. On NTFS that is unreliable:
    timestamps come off the ~15.6ms system-clock tick and directory metadata is
    updated lazily, so two appends inside one tick leave ``st_mtime``
    byte-identical and the cache serves the pre-append scan. That made the test
    above fail ~37% of the time on Windows (measured 9/24 on v1.143.0) and, in
    the product, made a just-saved note invisible to "what I can remember" for
    up to the full 60s TTL.

    Freezing the mtime turns that 37% into 100%: this test fails every time on
    the old mtime-only check and passes every time on the append-epoch check.
    """
    import os

    conn = _brain_conn(platform)
    conn.dir.mkdir(parents=True, exist_ok=True)  # before the freeze
    real_stat = Path.stat

    def frozen_stat(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self != conn.dir:  # the FOLDER only; note files keep real mtimes
            return st
        # A REAL stat_result with one field substituted — a stand-in object
        # would break every other consumer (exists(), is_dir(), mkdir()).
        return os.stat_result(
            (
                st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                st.st_gid, st.st_size, int(st.st_atime), 1_000_000,
                int(st.st_ctime),
            )
        )

    monkeypatch.setattr(Path, "stat", frozen_stat)

    platform.ltm.append("First Note", "x", source="brain")
    assert "brain (markdown, 1 note)" in memory_index_block(platform)
    platform.ltm.append("Second Note", "y", source="brain")
    block = memory_index_block(platform)
    assert "brain (markdown, 2 notes)" in block, "the stale pre-append scan was served"
    assert "second-note" in _titles_line(block)


def test_the_append_epoch_does_not_bust_the_cache_on_a_plain_turn(platform, monkeypatch):
    """The other half: the fix must not quietly disable the cache. With no
    append between turns the epoch is unchanged, so the glob still runs once."""
    platform.ltm.append("Cached Note", "x", source="brain")
    conn = _brain_conn(platform)
    calls = {"n": 0}
    real_files = conn._files

    def counting():
        calls["n"] += 1
        return real_files()

    monkeypatch.setattr(conn, "_files", counting)
    memory_index_block(platform)
    memory_index_block(platform)
    memory_index_block(platform)
    assert calls["n"] == 1


def test_an_unavailable_append_epoch_leaves_the_cache_as_it_was(platform, monkeypatch):
    """A constant epoch degrades to the pre-v1.146.1 behaviour rather than
    breaking a turn — the same never-raise discipline as every other injection."""
    monkeypatch.setattr(
        index_block_mod,
        "_append_epoch",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    platform.ltm.append("Some Note", "x", source="brain")
    # _append_epoch itself swallows; this pins the CALLER surviving a raiser too.
    assert isinstance(memory_index_block(platform), str)


def test_scan_cache_expires_after_ttl(platform, monkeypatch):
    platform.ltm.append("Some Note", "x", source="brain")
    memory_index_block(platform)
    assert index_block_mod._scan_cache
    # Age every entry past the TTL (folder mtime unchanged) -> full re-scan.
    for key, (stamp, dmt, entries, epoch) in list(index_block_mod._scan_cache.items()):
        index_block_mod._scan_cache[key] = (
            stamp - index_block_mod._SCAN_TTL_SECONDS - 1,
            dmt,
            entries,
            epoch,
        )
    conn = _brain_conn(platform)
    calls = {"n": 0}
    real_files = conn._files

    def counting():
        calls["n"] += 1
        return real_files()

    monkeypatch.setattr(conn, "_files", counting)
    memory_index_block(platform)
    assert calls["n"] == 1


def test_scan_cache_stays_bounded(tmp_path):
    conns = []
    for i in range(index_block_mod._SCAN_CACHE_MAX + 5):
        c = MarkdownBrainConnector(tmp_path / f"vault-{i}")
        c.name = f"vault{i}"
        conns.append(c)
    p = SimpleNamespace(
        ltm=SimpleNamespace(connectors=lambda: list(conns)),
        memory=None,
        fabric=None,
    )
    block = memory_index_block(p)
    assert "vault0 (markdown, empty)" in block  # every vault was scanned...
    assert len(index_block_mod._scan_cache) <= index_block_mod._SCAN_CACHE_MAX


# --------------------------------------------------------------------------- #
# runtime injection — ALL agent types
# --------------------------------------------------------------------------- #
def _spy_systems(platform, monkeypatch) -> list[str]:
    """Capture EVERY system prompt any adapter receives (the
    test_projects_spine pattern, list-shaped: a supervisor run may make
    several model calls and child sessions get their own prompts)."""
    systems: list[str] = []
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            systems.append(system)
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)
    return systems


def test_runtime_injects_index_for_all_agent_types(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.ltm.append("Zeta Marker Note", "zzz", source="brain")
    systems = _spy_systems(platform, monkeypatch)

    # A reviewer is deliberately included: the ROSTER only injects for
    # supervisor/planner, but memory awareness is for EVERY type.
    for agent_type in ("builder", "reviewer", "planner"):
        systems.clear()
        r = client.post(
            "/sessions", json={"task": "do x", "wait": True, "agent_type": agent_type}
        )
        assert r.status_code == 200
        assert systems, agent_type
        assert _HEADER in systems[0], agent_type
        assert "zeta-marker-note" in systems[0], agent_type
        assert _CLOSING in systems[0], agent_type
        if agent_type == "planner":
            # Injection ORDER: awareness lands after lessons, BEFORE the
            # capability roster (the roster only exists for planner/supervisor).
            roster = "# Who can take this work"
            assert roster in systems[0]
            assert systems[0].index(_HEADER) < systems[0].index(roster)


def test_runtime_survives_a_raising_index(tmp_path, monkeypatch):
    from iron_jarvis.memory import index_block as mod

    monkeypatch.setattr(
        mod,
        "memory_index_block",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/sessions", json={"task": "do x", "wait": True})
    assert r.status_code == 200 and r.json()["status"] == "completed"


# --------------------------------------------------------------------------- #
# the wave's headline, combined: a REAL desktop chat turn shows BOTH the
# awareness index (Pair Y's module through Pair X's guarded injection) AND —
# when a note matches — the repaired fabric grounding, in one system prompt.
# --------------------------------------------------------------------------- #
def test_chat_turn_carries_awareness_and_grounding_together(tmp_path, monkeypatch):
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.router import RouteResult

    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        p.ltm.append(
            "Rust Invoice Rules",
            "Rust invoices are summarized in a markdown table",
            source="brain",
        )
        p.learning.note_preference("Always summarize invoices in a markdown table")
        seen: dict = {}

        async def fake_complete(
            *, provider=None, model=None, system, messages, tools, task_class
        ):
            seen["system"] = system
            return RouteResult(LLMResponse(text="ok"), "mock", "mock")

        monkeypatch.setattr(p.router, "complete", fake_complete)
        r = client.post(
            "/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "How should I format the rust invoice markdown table?",
                    }
                ]
            },
        )
        assert r.status_code == 200
        system = seen["system"]
        assert _HEADER in system  # the inventory ("what exists")
        assert "rust-invoice-rules" in system  # ...naming the note's TITLE
        assert "# Relevant from memory" in system  # the retrieval ("what matches")
        # Stable order: awareness (inventory) precedes grounded snippets.
        assert system.index(_HEADER) < system.index("# Relevant from memory")


# --------------------------------------------------------------------------- #
# comm project reach (fakes mirror test_comm_full_chat)
# --------------------------------------------------------------------------- #
class ChatMockChannel(MockChannel):
    supports_inbound = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.updates: list[InboundMessage] = []

    def has_credentials(self) -> bool:
        return True

    def poll(self, offset: int = 0, *, timeout: int = 0):
        msgs = [
            m for m in self.updates if m.update_id is None or m.update_id >= offset
        ]
        nxt = offset
        for m in msgs:
            if isinstance(m.update_id, int):
                nxt = max(nxt, m.update_id + 1)
        return msgs, nxt


CHAT_CFG = {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}


def _msg(text: str, update_id: int = 1) -> InboundMessage:
    return InboundMessage(
        sender_id="777", text=text, update_id=update_id, reply_to="777"
    )


def _fake_turn(reply: str = "ok", **extra: Any):
    async def turn(platform, personas, body) -> dict[str, Any]:
        turn.calls.append(body)
        return {
            "reply": reply,
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": False,
            "escalate_reason": "",
            **extra,
        }

    turn.calls = []
    return turn


def _chat_poller(platform, ch, turn):
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    orch = Orchestrator(platform)
    store = CommThreadStore(platform.engine)
    poller = InboundPoller(
        notifier,
        orch,
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=store,
        chat_turn=turn,
        personas={},
        platform=platform,
    )
    return poller, orch, store


def _tag_thread(platform, thread_id: str, project_id: str) -> None:
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, thread_id)
        row.project_id = project_id
        db.add(row)
        db.commit()


async def test_comm_chat_turn_carries_the_threads_project(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn()
    poller, _orch, _store = _chat_poller(platform, ch, turn)

    r1 = await poller._handle("tg", ch, _msg("hello", update_id=1))
    assert turn.calls[0].project_id == ""  # untagged thread → untagged turn

    pid = _make_project(platform, name="Comm Spine")
    _tag_thread(platform, r1["thread_id"], pid)
    await poller._handle("tg", ch, _msg("again", update_id=2))
    assert turn.calls[1].project_id == pid  # tagged thread rides into ChatBody


async def test_escalated_comm_session_carries_project_and_spine(
    platform, monkeypatch
):
    ch = ChatMockChannel(dict(CHAT_CFG))
    turn = _fake_turn()
    poller, orch, _store = _chat_poller(platform, ch, turn)
    r1 = await poller._handle("tg", ch, _msg("hi", update_id=1))
    pid = _make_project(platform, name="Comm Esc", brief="COMM-SPINE-MARKER-42")
    _tag_thread(platform, r1["thread_id"], pid)

    systems = _spy_systems(platform, monkeypatch)

    async def escalating(platform_, personas, body):
        return {
            "reply": "needs an agent",
            "provider": "mock",
            "model": "m",
            "tools_used": [],
            "escalate": True,
            "escalate_reason": "multi-step",
        }

    poller.chat_turn = escalating
    res = await poller._handle("tg", ch, _msg("build the report", update_id=2))

    assert res["status"] == "chat_escalated"
    session = next(s for s in orch.list_sessions() if s.id == res["session_id"])
    assert session.project_id == pid  # the session is IN the project
    # ... and therefore ran with the project-context spine in its prompt.
    spined = [s for s in systems if "COMM-SPINE-MARKER-42" in s]
    assert spined
    assert all(_HEADER in s for s in spined)  # memory awareness rides along


async def test_untagged_escalation_stays_project_free(platform):
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, orch, _store = _chat_poller(
        platform, ch, _fake_turn("go", escalate=True, escalate_reason="x")
    )
    res = await poller._handle("tg", ch, _msg("do it", update_id=1))
    assert res["status"] == "chat_escalated"
    session = next(s for s in orch.list_sessions() if s.id == res["session_id"])
    assert session.project_id is None


# --------------------------------------------------------------------------- #
# heal keeps the project binding
# --------------------------------------------------------------------------- #
def _delete_thread(platform, thread_id: str) -> None:
    with session_scope(platform.engine) as db:
        db.delete(db.get(ChatThreadRecord, thread_id))
        db.commit()


def test_heal_keeps_project_binding(platform):
    store = CommThreadStore(platform.engine)
    pid = _make_project(platform, name="Heal")
    t1 = store.resolve("tg", "777", "Val")
    _tag_thread(platform, t1.id, pid)
    assert store.resolve("tg", "777").id == t1.id  # re-resolve sees the tag

    _delete_thread(platform, t1.id)  # dashboard tidy-up
    t2 = store.resolve("tg", "777")
    assert t2.id != t1.id
    assert t2.project_id == pid  # the fresh thread KEEPS the project
    with session_scope(platform.engine) as db:  # persisted, not just in-memory
        assert db.get(ChatThreadRecord, t2.id).project_id == pid


def test_heal_without_prior_binding_stays_untagged(platform):
    store = CommThreadStore(platform.engine)
    t1 = store.resolve("tg", "777")
    _delete_thread(platform, t1.id)
    assert store.resolve("tg", "777").project_id is None


def test_untag_then_heal_does_not_resurrect_the_project(platform):
    store = CommThreadStore(platform.engine)
    pid = _make_project(platform, name="Untag")
    t1 = store.resolve("tg", "777")
    _tag_thread(platform, t1.id, pid)
    store.resolve("tg", "777")  # cache sees the tag
    _tag_thread(platform, t1.id, None)  # user removes the project
    store.resolve("tg", "777")  # cache refreshes to the CURRENT truth
    _delete_thread(platform, t1.id)
    assert store.resolve("tg", "777").project_id is None


def test_retire_is_a_deliberate_fresh_start(platform):
    store = CommThreadStore(platform.engine)
    pid = _make_project(platform, name="Retire")
    t1 = store.resolve("tg", "777")
    _tag_thread(platform, t1.id, pid)
    store.resolve("tg", "777")
    store.retire("tg", "777")  # "/new"
    t2 = store.resolve("tg", "777")
    assert t2.id != t1.id
    assert t2.project_id is None  # no project carry-over on an explicit /new
    with session_scope(platform.engine) as db:  # the old thread row survives
        assert db.get(ChatThreadRecord, t1.id) is not None
        assert db.get(ChatThreadRecord, t1.id).project_id == pid


def test_identities_do_not_share_project_cache(platform):
    store = CommThreadStore(platform.engine)
    pid = _make_project(platform, name="Iso")
    t_a = store.resolve("tg", "111")
    _tag_thread(platform, t_a.id, pid)
    store.resolve("tg", "111")
    t_b = store.resolve("tg", "222")
    _delete_thread(platform, t_b.id)
    assert store.resolve("tg", "222").project_id is None  # 111's tag stays 111's
