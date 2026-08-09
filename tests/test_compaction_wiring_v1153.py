"""Compaction, wired into the two lanes that answer to different people.

``test_compaction_v1153.py`` covers the engine — thresholds, the content
address, and the verification pass that lets a model-written summary near a
prompt at all. This file covers the WIRING, which is where the same feature
usually ships as dead code:

* every chat turn reports how full the window is, so the client can draw a gauge
  and — in the 70% band — offer the choice, without the daemon acting;
* a stored summary actually reaches the provider, and reaches it as SYSTEM text
  rather than as a message someone appears to have said;
* the explicit "compact now" route stores something the next ordinary turn picks
  up with no further model call;
* an install with no real model refuses to fabricate one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.context import compaction as C
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _compaction_store


def _client(tmp_path, window: int | None = None) -> TestClient:
    client = TestClient(create_app(str(tmp_path)))
    if window:
        client.put(
            "/settings", json={"values": {"model_context_windows": {"mock": window}}}
        )
    return client


def _chat(client: TestClient, messages):
    return client.post("/chat", json={"messages": messages})


def _spy_on_provider(client: TestClient, monkeypatch, sink: list):
    """Capture the (system, messages) the adapter is really handed."""
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_stream = adapter.stream

        def stream(*, system, messages, tools):
            sink.append((system, list(messages)))
            return real_stream(system=system, messages=messages, tools=tools)

        adapter.stream = stream
        # POST /chat is the NON-streaming lane and goes through complete();
        # spying on stream() alone captures nothing there.
        real_complete = adapter.complete

        async def complete(*, system, messages, tools):
            sink.append((system, list(messages)))
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = complete
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)


def _seed(client: TestClient, messages, body: str) -> int:
    """Store a compaction for the prefix *messages* would produce."""
    covered = messages[: len(messages) - C.KEEP_RECENT]
    key = C.prefix_key([f"{m['role']}\x1e{m['content']}" for m in covered])
    _compaction_store(client.app.state.platform).put(
        key, summary=C.render(f"GOAL:\n- {body}\n"), covers=len(covered)
    )
    return len(covered)


# --------------------------------------------------------------------------- #
# (1) THE SIGNAL: tell the user at 70%, act alone only at the ceiling.
# --------------------------------------------------------------------------- #
def test_every_turn_reports_how_full_the_window_is(tmp_path):
    """The gauge is not an error path. Reporting only once a conversation is in
    trouble leaves the client nothing to draw until it is too late to choose."""
    r = _chat(_client(tmp_path), [{"role": "user", "content": "hello"}])
    assert r.status_code == 200
    ctx = r.json()["context"]
    assert ctx["level"] == "ok"
    assert 0 <= ctx["percent"] < 70
    assert ctx["suggest_at"] == 70
    assert 90 <= ctx["auto_at"] <= 95


def test_a_filling_window_says_suggest_and_does_nothing_else(tmp_path):
    """The 70% band is a SIGNAL. Acting here would take the choice away, which
    is the entire thing that separates this from silent compaction."""
    client = _client(tmp_path, window=4000)
    # Sized against the REAL system prompt, which is itself ~500 tokens: the
    # history has to push the total past 70% of 4000 on its own.
    msgs = [
        {"role": "user", "content": "x" * 9000},
        {"role": "assistant", "content": "understood"},
    ]
    ctx = _chat(client, msgs).json()["context"]
    assert ctx["level"] == "suggest"
    assert ctx["compacted"] is False


def test_the_v1146_context_fields_all_survive(tmp_path):
    """v1.153.0 EXTENDS the existing `context` key instead of adding a rival
    one, so a client written against v1.146.0 must not notice the change."""
    ctx = _chat(_client(tmp_path), [{"role": "user", "content": "hi"}]).json()["context"]
    for key in ("window", "used", "headroom", "dropped", "tools_trimmed", "clipped"):
        assert key in ctx, f"v1.146.0's `{key}` disappeared"


# --------------------------------------------------------------------------- #
# (2) THE WIRING — without these the module is dead code.
# --------------------------------------------------------------------------- #
def test_a_stored_compaction_replaces_the_history_it_covers(tmp_path, monkeypatch):
    client = _client(tmp_path)
    msgs = [{"role": "user", "content": f"turn {i} about ledgers"} for i in range(14)]
    covered = _seed(client, msgs, "THE-SEEDED-MARKER")

    seen: list = []
    _spy_on_provider(client, monkeypatch, seen)
    r = _chat(client, msgs)
    assert r.status_code == 200
    assert seen, "the turn never reached the adapter"

    system, sent = seen[0]
    assert "THE-SEEDED-MARKER" in system, "the summary never reached the prompt"
    assert len(sent) <= C.KEEP_RECENT, f"covered history still sent ({len(sent)} msgs)"
    assert not any((m.content or "").startswith("turn 0 ") for m in sent)
    assert r.json()["context"]["compacted"] is True
    assert r.json()["context"]["covers"] == covered


def test_the_summary_is_system_text_not_a_message(tmp_path, monkeypatch):
    """As a message it would read as something a participant actually said."""
    client = _client(tmp_path)
    msgs = [{"role": "user", "content": f"message {i}"} for i in range(14)]
    _seed(client, msgs, "UNIQUE-SUMMARY-BODY")

    seen: list = []
    _spy_on_provider(client, monkeypatch, seen)
    _chat(client, msgs)
    assert seen
    _system, sent = seen[0]
    assert not any("UNIQUE-SUMMARY-BODY" in (m.content or "") for m in sent)


def test_the_task_prefix_is_not_compacted_when_nothing_is_stored(tmp_path, monkeypatch):
    """No stored summary and no ceiling reached: the turn is exactly what it
    was before this feature existed."""
    client = _client(tmp_path)
    msgs = [{"role": "user", "content": f"m{i}"} for i in range(14)]
    seen: list = []
    _spy_on_provider(client, monkeypatch, seen)
    r = _chat(client, msgs)
    assert r.json()["context"]["compacted"] is False
    assert seen and len(seen[0][1]) == len(msgs)


# --------------------------------------------------------------------------- #
# (3) THE USER'S CHOICE: POST /chat/compact.
# --------------------------------------------------------------------------- #
def test_compacting_a_short_conversation_is_refused(tmp_path):
    r = _client(tmp_path).post(
        "/chat/compact", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert r.status_code == 400
    assert "not enough" in r.json()["detail"]


def test_a_mock_only_install_refuses_to_fabricate_a_summary(tmp_path):
    """A fabricated summary would be read back as an authoritative account of
    the conversation on every later turn — worse than no compaction at all."""
    r = _client(tmp_path).post(
        "/chat/compact",
        json={"messages": [{"role": "user", "content": f"m{i}"} for i in range(14)]},
    )
    assert r.status_code == 400
    assert "real model" in r.json()["detail"]


def test_a_cached_compaction_is_returned_without_a_second_model_call(tmp_path):
    """The 70% offer clicked twice must not bill twice."""
    client = _client(tmp_path)
    msgs = [{"role": "user", "content": f"invoice {i}"} for i in range(14)]
    _seed(client, msgs, "already-summarized")
    r = client.post("/chat/compact", json={"messages": msgs})
    assert r.status_code == 200
    assert r.json()["cached"] is True
    assert "already-summarized" in r.json()["summary"]


# --------------------------------------------------------------------------- #
# (4) THE STORE.
# --------------------------------------------------------------------------- #
def test_the_compaction_table_exists_on_an_EXISTING_database(tmp_path):
    """The v1.151.2 lesson: a table created only by ``__table__.create`` is
    invisible to the additive-column reconciler, so it must be registered in
    ``core.db._LATE_MODEL_MODULES`` or it lands on fresh test DBs and on no
    real install. Asserted on the registration list itself because that is the
    thing that was missing last time.
    """
    from iron_jarvis.core.db import _LATE_MODEL_MODULES

    assert "..context.store" in _LATE_MODEL_MODULES


def test_a_stored_compaction_survives_a_daemon_restart(tmp_path):
    """It is a cache, but a cache that evaporated every boot would re-bill the
    user for the same summary on the first turn after every restart."""
    client = _client(tmp_path)
    msgs = [{"role": "user", "content": f"persist {i}"} for i in range(14)]
    _seed(client, msgs, "SURVIVES-RESTART")

    reopened = TestClient(create_app(str(tmp_path)))
    covered = msgs[: len(msgs) - C.KEEP_RECENT]
    key = C.prefix_key([f"{m['role']}\x1e{m['content']}" for m in covered])
    rec = _compaction_store(reopened.app.state.platform).get(key)
    assert rec is not None and "SURVIVES-RESTART" in rec.summary


def test_an_unknown_key_is_a_miss_not_a_crash(tmp_path):
    store = _compaction_store(_client(tmp_path).app.state.platform)
    assert store.get("no-such-key") is None
    assert store.get("") is None


# --------------------------------------------------------------------------- #
# (5) SETTINGS.
# --------------------------------------------------------------------------- #
def test_the_thresholds_are_user_tunable(tmp_path):
    from iron_jarvis.daemon.chat_turn import _compaction_thresholds

    client = _client(tmp_path)
    client.put(
        "/settings",
        json={"values": {"context_compaction": {"suggest_at": 0.5, "auto_at": 0.8}}},
    )
    d = client.app.state.platform
    suggest, auto = _compaction_thresholds(type("D", (), {"platform": d})())
    assert suggest == pytest.approx(0.5)
    assert auto == pytest.approx(0.8)


def test_a_ceiling_below_the_signal_is_corrected(tmp_path):
    """Otherwise every conversation compacts the instant it is offered."""
    from iron_jarvis.daemon.chat_turn import _compaction_thresholds

    client = _client(tmp_path)
    client.put(
        "/settings",
        json={"values": {"context_compaction": {"suggest_at": 0.9, "auto_at": 0.3}}},
    )
    d = client.app.state.platform
    suggest, auto = _compaction_thresholds(type("D", (), {"platform": d})())
    assert auto > suggest


def test_compaction_can_be_turned_off_entirely(tmp_path):
    from iron_jarvis.daemon.chat_turn import _compaction_enabled

    client = _client(tmp_path)
    client.put(
        "/settings", json={"values": {"context_compaction": {"enabled": False}}}
    )
    d = client.app.state.platform
    assert _compaction_enabled(type("D", (), {"platform": d})()) is False
