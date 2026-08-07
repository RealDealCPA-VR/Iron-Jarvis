"""Chat memory grounding — the v1.141.0 repair, PINNED (Pair X).

The day-one bug this file exists for: chat's fabric-grounding block called
``fabric.ground(..., sources=[...])`` while ``ground()`` had no ``sources``
kwarg — a TypeError on every single turn, swallowed by a bare
``except Exception: pass``. No test ever asserted the grounded block reached
the model, so chat shipped ungrounded for its entire life. This suite:

  * THE PINNING TEST: a chat turn whose platform holds matching memory MUST
    carry "# Relevant from memory" in the system prompt the model receives —
    on POST /chat AND the /chat/stream mirror (the exact assertion whose
    absence hid the bug);
  * WIRING PIN: the chat call passes ``sources=`` + the composed recall query
    into ``ground`` (captured kwargs — a signature regression fails loudly);
  * fabric.ground forwards ``sources=`` to recall (None = all, unchanged);
  * the composed recall query rule (short-message accretion, 3-message cap,
    long messages unchanged, " \\n " join);
  * grounding failures LOG (log.exception) and never break the turn — in
    both lanes;
  * pull tools: AUTO_SAFE_TOOLS grew recall/ltm_search/ltm_append/
    remember_preference, the memory sentence rule fires on remember/recall
    intent and NOT on "memory usage of the process";
  * reviewer + supervisor agent definitions carry ``recall``;
  * cross-pair guards: the memory_index_block injection (fake module both
    ways) and the default-persona resolution helper;
  * project-block parity (root line + recent-activity recap) + lock-step
    source parity between chat_turn.py and routes/chat.py.

Fully offline; the router is monkeypatched to capture the system prompt.
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _compose_recall_query, _resolve_persona
from iron_jarvis.memory.fabric import MemoryFabric
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _capture_complete(platform, monkeypatch, seen: dict, reply: str = "ok"):
    """Route every completion to a fake that records the system prompt."""

    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        seen["system"] = system
        seen["messages"] = list(messages)
        return RouteResult(LLMResponse(text=reply), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)


def _capture_stream(platform, seen: dict, reply: str = "ok"):
    """Instance-attribute router.stream stub (the FX-01 test idiom) that
    records the system prompt, then yields one token + the final frame."""

    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        seen["system"] = system
        yield {"type": "text", "text": reply}
        yield {"type": "final", "response": LLMResponse(text=reply),
               "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream


def _seed_memory(p) -> None:
    """Deterministic matches for 'rust invoice markdown table' in three
    stores: a lesson, a past session, and a long-term note."""
    p.learning.note_preference("Always summarize invoices in a markdown table")
    with session_scope(p.engine) as db:
        db.add(Session(task="Draft the Rust invoice summary",
                       summary="Produced invoices.md with a markdown table"))
        db.commit()
    src = p.ltm.default_source()
    if src:
        p.ltm.append("Rust invoice rules",
                     "Rust invoices are summarized in a markdown table",
                     source=src)


_QUESTION = "How should I format the rust invoice markdown table today?"


# --------------------------------------------------------------------------- #
# (1) THE PINNING TEST — the assertion whose absence hid the day-one bug.
# --------------------------------------------------------------------------- #
def test_chat_turn_with_matching_memory_grounds_the_system_prompt(
    tmp_path, monkeypatch
):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_memory(p)
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen)

        r = client.post(
            "/chat", json={"messages": [{"role": "user", "content": _QUESTION}]}
        )
        assert r.status_code == 200
        # THE assertion: the grounded block actually reached the model.
        assert "# Relevant from memory" in seen["system"]
        assert "markdown table" in seen["system"]


def test_stream_turn_with_matching_memory_grounds_the_system_prompt(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_memory(p)
        seen: dict = {}
        _capture_stream(p, seen)

        r = client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": _QUESTION}]},
        )
        assert r.status_code == 200
        assert "event: done" in r.text
        # Same pin for the stream mirror — grounding reaches the model there too.
        assert "# Relevant from memory" in seen["system"]


# --------------------------------------------------------------------------- #
# (2) Wiring pin: chat calls ground with sources= and the composed query.
#     A future ground() signature regression fails HERE, not silently.
# --------------------------------------------------------------------------- #
def test_chat_passes_sources_and_composed_query_to_ground(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen)
        got: dict = {}

        def fake_ground(query, k=4, *, project_id=None, sources=None,
                        char_budget=1200):
            got.update(query=query, project_id=project_id, sources=sources)
            return "\n\n# Relevant from memory\n- [note] pinned: wiring"

        monkeypatch.setattr(p.fabric, "ground", fake_ground)
        r = client.post(
            "/chat", json={"messages": [{"role": "user", "content": _QUESTION}]}
        )
        assert r.status_code == 200
        # Project knowledge rides separately, so "knowledge" is excluded here.
        assert got["sources"] == [
            "files", "notes", "memory", "lessons", "sessions", "chats",
        ]
        assert got["query"] == _QUESTION            # >= 6 tokens: unchanged
        assert "pinned: wiring" in seen["system"]   # the returned block lands


# --------------------------------------------------------------------------- #
# (3) fabric.ground: sources forwarding (unit) + never-raise unchanged.
# --------------------------------------------------------------------------- #
def test_ground_forwards_sources_to_recall_none_means_all():
    fab = MemoryFabric()
    seen: dict = {}

    def fake_recall(query, k=6, *, project_id=None, sources=None, min_score=0.0):
        seen["sources"] = sources
        return []

    fab.recall = fake_recall  # type: ignore[method-assign]
    assert fab.ground("q", sources=["notes"]) == ""
    assert seen["sources"] == ["notes"]
    assert fab.ground("q") == ""
    assert seen["sources"] is None  # existing callers: all stores, unchanged


def test_ground_sources_filter_shapes_the_block(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        _seed_memory(p)
        only_sessions = p.fabric.ground(
            "rust invoice markdown table", sources=["sessions"]
        )
        assert "[past run]" in only_sessions
        assert "[lesson]" not in only_sessions
        everything = p.fabric.ground("rust invoice markdown table")
        assert "[lesson]" in everything  # None = every store (old behavior)


def test_ground_never_raises_even_when_recall_explodes():
    fab = MemoryFabric()

    def boom(*a, **kw):
        raise RuntimeError("store on fire")

    fab.recall = boom  # type: ignore[method-assign]
    assert fab.ground("anything", sources=["notes"]) == ""


# --------------------------------------------------------------------------- #
# (4) The composed recall query rule (deterministic, documented on the
#     helper): last user message; while < 6 fabric tokens, prepend the
#     previous user message; max 3 messages; " \n " join, oldest first.
# --------------------------------------------------------------------------- #
def _msgs(*pairs):
    return [SimpleNamespace(role=r, content=c) for r, c in pairs]


def test_recall_query_long_message_is_unchanged():
    m = _msgs(("user", "old context about taxes"),
              ("user", "please reconcile the quarterly rust invoice tables now"))
    assert _compose_recall_query(m) == (
        "please reconcile the quarterly rust invoice tables now"
    )


def test_recall_query_short_followup_accretes_previous_user_message():
    m = _msgs(
        ("user", "Tell me about the rust invoice markdown table project"),
        ("assistant", "It renders invoices as markdown tables."),
        ("user", "and the totals?"),
    )
    # 3 tokens < 6 -> the previous USER message is prepended (assistant
    # messages never participate), oldest first, " \n " join.
    assert _compose_recall_query(m) == (
        "Tell me about the rust invoice markdown table project \n and the totals?"
    )


def test_recall_query_caps_at_three_user_messages():
    m = _msgs(("user", "one"), ("user", "two"), ("user", "three"), ("user", "ok?"))
    # Even though the composition never reaches 6 tokens, accretion stops at 3.
    assert _compose_recall_query(m) == "two \n three \n ok?"


def test_recall_query_stops_accreting_once_six_tokens_reached():
    m = _msgs(
        ("user", "never reached because the middle message is long enough"),
        ("user", "the quarterly rust invoice project"),  # 5 tokens
        ("user", "totals?"),                             # 1 token -> 6 combined
    )
    assert _compose_recall_query(m) == (
        "the quarterly rust invoice project \n totals?"
    )


def test_recall_query_no_user_messages_is_empty():
    assert _compose_recall_query(_msgs(("assistant", "hello"))) == ""
    assert _compose_recall_query([]) == ""


def test_recall_query_first_message_short_uses_what_exists():
    # A short FIRST message has no prior to accrete — used as-is, no crash.
    assert _compose_recall_query(_msgs(("user", "hi"))) == "hi"


def test_recall_query_prepended_messages_are_capped_not_the_last():
    # Reviewer fix (v1.141.0): an accreted EARLIER message is clipped to 500
    # chars so a pasted wall of text can't balloon every grounding call keyed
    # off the composed query.
    wall = "invoice " * 3000  # ~24k chars
    m = _msgs(("user", wall), ("user", "totals?"))
    q = _compose_recall_query(m)
    assert q.endswith(" \n totals?")
    assert len(q) <= 500 + len(" \n totals?")
    assert q.startswith(wall[:500])
    # The LAST message is NEVER clipped — a long final message stays the
    # byte-identical pre-v1.141.0 query even at pasted-wall size.
    assert _compose_recall_query(_msgs(("user", wall))) == wall


def test_short_followup_reaches_ground_with_accreted_query(tmp_path, monkeypatch):
    """End-to-end: the SHORT follow-up recalls on the conversation's subject."""
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen)
        got: dict = {}

        def fake_ground(query, k=4, *, project_id=None, sources=None,
                        char_budget=1200):
            got["query"] = query
            return ""

        monkeypatch.setattr(p.fabric, "ground", fake_ground)
        r = client.post("/chat", json={"messages": [
            {"role": "user",
             "content": "Tell me about the rust invoice markdown table project"},
            {"role": "assistant", "content": "Sure — it uses markdown."},
            {"role": "user", "content": "and the totals?"},
        ]})
        assert r.status_code == 200
        assert got["query"] == (
            "Tell me about the rust invoice markdown table project"
            " \n and the totals?"
        )


# --------------------------------------------------------------------------- #
# (5) Grounding failures LOG (never silently pass) and never break the turn —
#     both lanes.
# --------------------------------------------------------------------------- #
def test_grounding_failure_logs_and_turn_survives(tmp_path, monkeypatch, caplog):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen, reply="still fine")

        def boom(*a, **kw):
            raise TypeError("ground() got an unexpected keyword argument 'sources'")

        monkeypatch.setattr(p.fabric, "ground", boom)
        with caplog.at_level(logging.ERROR, logger="iron_jarvis.daemon.chat_turn"):
            r = client.post(
                "/chat",
                json={"messages": [{"role": "user", "content": _QUESTION}]},
            )
        assert r.status_code == 200
        assert r.json()["reply"] == "still fine"
        recs = [rec for rec in caplog.records if "grounding failed" in rec.message]
        assert recs, "the failure must be LOGGED, not swallowed by a bare pass"
        assert recs[0].exc_info is not None  # full traceback (log.exception)


def test_stream_grounding_failure_logs_and_turn_survives(
    tmp_path, monkeypatch, caplog
):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        seen: dict = {}
        _capture_stream(p, seen, reply="still fine")

        def boom(*a, **kw):
            raise TypeError("ground() got an unexpected keyword argument 'sources'")

        monkeypatch.setattr(p.fabric, "ground", boom)
        with caplog.at_level(
            logging.ERROR, logger="iron_jarvis.daemon.routes.chat"
        ):
            r = client.post(
                "/chat/stream",
                json={"messages": [{"role": "user", "content": _QUESTION}]},
            )
        assert r.status_code == 200
        assert "event: done" in r.text and "still fine" in r.text
        recs = [rec for rec in caplog.records if "grounding failed" in rec.message]
        assert recs and recs[0].exc_info is not None


# --------------------------------------------------------------------------- #
# (6) Pull tools: the safe set + the memory sentence rule.
# --------------------------------------------------------------------------- #
def test_memory_tools_joined_the_auto_safe_set():
    assert {"recall", "ltm_search", "ltm_append", "remember_preference"} \
        <= AUTO_SAFE_TOOLS


def test_remember_intent_arms_recall_and_ltm_append():
    picked = select_auto_tools("remember this: invoices go out on Fridays")
    assert "recall" in picked
    assert "ltm_append" in picked


def test_what_do_we_know_arms_recall():
    picked = select_auto_tools("what do we know about the henderson account?")
    assert "recall" in picked


def test_memory_usage_of_the_process_does_not_fire_the_memory_rule():
    picked = select_auto_tools("memory usage of the process")
    assert "recall" not in picked
    assert "ltm_append" not in picked


def test_possessive_memory_fires_recall_but_bare_memory_does_not():
    # Reviewer fix (v1.141.0): the original "memor" branch carried a trailing
    # \b, so \bmemor\b matched NO real word ("memory" fails the boundary) —
    # dead code. The possessive-scoped branch restores the spec's intent
    # ("your/my/our memory" = what we know, never RAM) without reviving the
    # diagnostics false-positive above.
    picked = select_auto_tools("what's in your memory about the henderson deal?")
    assert "recall" in picked
    assert "recall" not in select_auto_tools("is 16GB of memory enough here?")


# --------------------------------------------------------------------------- #
# (7) Reviewer + supervisor can pull memory too.
# --------------------------------------------------------------------------- #
def test_reviewer_and_supervisor_definitions_carry_recall():
    from iron_jarvis.agents.types import get_agent_definition

    assert "recall" in get_agent_definition(AgentType.REVIEWER).tools
    assert "recall" in get_agent_definition(AgentType.SUPERVISOR).tools


# --------------------------------------------------------------------------- #
# (8) Cross-pair guard: the memory_index_block injection (Pair Y builds the
#     module; the injection is guarded so either landing order is green).
#     A fake module in sys.modules exercises BOTH the inject and the
#     never-break paths regardless of whether the real module exists yet.
# --------------------------------------------------------------------------- #
def _fake_index_module(fn):
    mod = types.ModuleType("iron_jarvis.memory.index_block")
    mod.memory_index_block = fn
    return mod


def test_memory_index_block_is_injected_when_available(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen)
        monkeypatch.setitem(
            sys.modules,
            "iron_jarvis.memory.index_block",
            _fake_index_module(
                lambda platform, *, project_id=None, limit_titles=6:
                "# What I can remember\n- base: brain (INDEX-SENTINEL)"
            ),
        )
        r = client.post(
            "/chat", json={"messages": [{"role": "user", "content": "hello there"}]}
        )
        assert r.status_code == 200
        assert "INDEX-SENTINEL" in seen["system"]


def test_broken_memory_index_block_never_breaks_a_turn(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen, reply="fine")

        def boom(platform, *, project_id=None, limit_titles=6):
            raise RuntimeError("index exploded")

        monkeypatch.setitem(
            sys.modules, "iron_jarvis.memory.index_block", _fake_index_module(boom)
        )
        r = client.post(
            "/chat", json={"messages": [{"role": "user", "content": "hello there"}]}
        )
        assert r.status_code == 200
        assert r.json()["reply"] == "fine"


# --------------------------------------------------------------------------- #
# (9) Cross-pair guard: default persona resolution (Pair Z's config field +
#     resolve_prompt kwarg — the helper is green in either landing order and
#     keeps the precedence contract: override of the default slug WINS).
# --------------------------------------------------------------------------- #
def test_resolve_persona_default_and_precedence(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        from iron_jarvis.personas import PersonaStore

        store = PersonaStore(p.engine)
        builtins = {
            "assistant": {"prompt": "DEFAULT-ASSISTANT"},
            "accountant": {"prompt": "BUILTIN-CPA"},
        }
        # No explicit persona, no default -> the assistant builtin (unchanged).
        assert _resolve_persona(store, builtins, "", "") == "DEFAULT-ASSISTANT"
        # No explicit persona + a configured default -> the default's prompt.
        assert _resolve_persona(store, builtins, "", "accountant") == "BUILTIN-CPA"
        # An explicit persona always beats the default (free text verbatim).
        assert _resolve_persona(
            store, builtins, "You are a pirate.", "accountant"
        ) == "You are a pirate."
        # A user OVERRIDE of the default's slug wins over the raw builtin —
        # the precedence quirk Pair Z's store change fixes; the guarded
        # helper preserves it in both landing orders.
        store.upsert("accountant", title="A", description="", prompt="OVERRIDDEN-CPA")
        assert _resolve_persona(store, builtins, "", "accountant") == "OVERRIDDEN-CPA"


def test_configured_default_persona_reaches_the_chat_system_prompt(
    tmp_path, monkeypatch
):
    with TestClient(create_app(str(tmp_path))) as client:
        p = client.app.state.platform
        # Pair Z's field hasn't necessarily landed on Config — plant it the way
        # getattr will read it (object.__setattr__ bypasses pydantic's
        # validate_assignment for a not-yet-declared field).
        object.__setattr__(p.config, "default_persona", "accountant")
        seen: dict = {}
        _capture_complete(p, monkeypatch, seen)
        r = client.post(
            "/chat", json={"messages": [{"role": "user", "content": "hi there"}]}
        )
        assert r.status_code == 200
        assert "CPA" in seen["system"]  # the builtin accountant persona applied
        # An explicit persona still wins over the configured default.
        r2 = client.post("/chat", json={
            "messages": [{"role": "user", "content": "hi there"}],
            "persona": "You are a pirate. Answer in pirate speak.",
        })
        assert r2.status_code == 200
        assert "pirate" in seen["system"] and "CPA" not in seen["system"]


# --------------------------------------------------------------------------- #
# (10) Project-block parity (Y.5, implemented here): root line + the last-5
#      recent-activity recap in the agent runtime's exact line format — both
#      lanes.
# --------------------------------------------------------------------------- #
def _project_with_history(client, tmp_path):
    root = tmp_path / "projroot"
    root.mkdir(exist_ok=True)
    p = client.app.state.platform
    pid = client.post(
        "/projects", json={"name": "Parity", "root": str(root)}
    ).json()["id"]
    with session_scope(p.engine) as db:
        db.add(Session(task="Build the intake form", summary="Shipped intake v1",
                       project_id=pid))
        db.add(Session(task="Fix the export bug", summary="", project_id=pid))
        db.commit()
    return pid, str(root)


def test_chat_project_block_has_root_and_recent_activity(tmp_path, monkeypatch):
    with TestClient(create_app(str(tmp_path))) as client:
        pid, root = _project_with_history(client, tmp_path)
        seen: dict = {}
        _capture_complete(client.app.state.platform, monkeypatch, seen)
        r = client.post("/chat", json={
            "messages": [{"role": "user", "content": "where were we?"}],
            "project_id": pid,
        })
        assert r.status_code == 200
        system = seen["system"]
        assert f"Project folder: {root}" in system
        assert "Recent activity in this project (newest first):" in system
        # The exact runtime line format: "- [status] task: summary".
        assert "- [active] Build the intake form: Shipped intake v1" in system
        assert "- [active] Fix the export bug: (no summary)" in system


def test_stream_project_block_has_root_and_recent_activity(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        pid, root = _project_with_history(client, tmp_path)
        seen: dict = {}
        _capture_stream(client.app.state.platform, seen)
        r = client.post("/chat/stream", json={
            "messages": [{"role": "user", "content": "where were we?"}],
            "project_id": pid,
        })
        assert r.status_code == 200
        system = seen["system"]
        assert f"Project folder: {root}" in system
        assert "Recent activity in this project (newest first):" in system
        assert "- [active] Build the intake form: Shipped intake v1" in system


# --------------------------------------------------------------------------- #
# (11) Lock-step source parity: every new prep site exists in BOTH files with
#      its mirror discipline intact.
# --------------------------------------------------------------------------- #
def test_stream_mirror_carries_every_new_prep_site():
    turn_src = (_SRC / "daemon" / "chat_turn.py").read_text(encoding="utf-8")
    stream_src = (_SRC / "daemon" / "routes" / "chat.py").read_text(encoding="utf-8")
    for needle in (
        "_compose_recall_query(body.messages)",
        'sources=["files", "notes", "memory", "lessons", "sessions", "chats"]',
        "memory_index_block",
        "_resolve_persona(",
        "Recent activity in this project (newest first):",
        "log.exception",
        "MIRROR NOTE",
    ):
        assert needle in turn_src, f"chat_turn.py lost: {needle}"
        assert needle in stream_src, f"routes/chat.py (stream mirror) lost: {needle}"
