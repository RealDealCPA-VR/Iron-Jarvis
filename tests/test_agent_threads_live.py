"""Agent threads go LIVE and DIRECTED (v1.140.0).

Per-speaker persistence: every round entry lands in the DB atomically as it
happens (user turn first, then each speaker) and publishes
AGENT_THREAD_UPDATED {thread_id, who, entries} best-effort — a bus failure
never breaks the round. @-mentions direct who speaks (name / role / key name
part, case-insensitive, panel order; no match → everyone). Disabled/missing
remote participants are skipped honestly before any network call. The /say
response stays backward-compatible and gains additive "spoke" + "skipped".
Offline throughout.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import threads as threads_mod
from iron_jarvis.agents.remote import RemoteAgentRegistry
from iron_jarvis.agents.threads import AgentThreads
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


@pytest.fixture()
def client(app):
    return TestClient(app)


def _panel():
    return [
        {"source": "builtin", "name": "planner", "role": "lead"},
        {"source": "builtin", "name": "reviewer", "role": "critic"},
    ]


def _panel3():
    return _panel() + [
        {"source": "builtin", "name": "hermes-mac-mini", "role": "researcher"}
    ]


def _canned(monkeypatch):
    async def speak(self, p, others, transcript, d):
        return f"{p['name']} answers"

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_local", speak)


def _mk_thread(client, participants):
    return client.post("/agents/threads", json={"participants": participants}).json()[
        "id"
    ]


# --- the event contract ---------------------------------------------------------


def test_event_type_wire_name_is_pinned():
    assert EventType.AGENT_THREAD_UPDATED == "agent_thread.updated"


def test_event_published_per_entry_including_the_user_turn(app, client, monkeypatch):
    _canned(monkeypatch)
    seen = []
    app.state.platform.event_bus.add_handler(
        lambda ev: seen.append(ev) if ev.type == "agent_thread.updated" else None
    )
    tid = _mk_thread(client, _panel())
    r = client.post(f"/agents/threads/{tid}/say", json={"message": "hello panel"})
    assert r.status_code == 200
    assert [e.payload["who"] for e in seen] == [
        "user",
        "builtin:planner",
        "builtin:reviewer",
    ]
    # entries = the thread's new TOTAL message count after each append.
    assert [e.payload["entries"] for e in seen] == [1, 2, 3]
    assert all(e.payload["thread_id"] == tid for e in seen)


def test_bus_failure_never_breaks_the_round(app, client, monkeypatch):
    _canned(monkeypatch)
    tid = _mk_thread(client, _panel())

    async def boom(*a, **k):
        raise RuntimeError("bus down")

    monkeypatch.setattr(app.state.platform.event_bus, "publish", boom)
    r = client.post(f"/agents/threads/{tid}/say", json={"message": "still works?"})
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e["who"] for e in entries] == [
        "user",
        "builtin:planner",
        "builtin:reviewer",
    ]
    # And the entries were still persisted despite the dead bus.
    got = client.get(f"/agents/threads/{tid}").json()
    assert got["message_count"] == 3


# --- per-speaker persistence ----------------------------------------------------


def test_each_entry_is_persisted_before_the_next_speaker_runs(client, monkeypatch):
    """LIVE means the DB already holds the previous entries when a speaker
    starts — not one batch write at the end of the round."""
    holder: dict[str, str] = {}
    seen_db: list[tuple[str, list[str]]] = []

    async def spy(self, p, others, transcript, d):
        rec = self.get(holder["tid"])
        whos = [m["who"] for m in json.loads(rec.messages_json or "[]")]
        seen_db.append((p["name"], whos))
        return f"{p['name']} answers"

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_local", spy)
    holder["tid"] = _mk_thread(client, _panel())
    client.post(f"/agents/threads/{holder['tid']}/say", json={"message": "go"})
    assert seen_db[0] == ("planner", ["user"])  # user turn already durable
    assert seen_db[1] == ("reviewer", ["user", "builtin:planner"])


def test_concurrent_say_rounds_keep_the_json_blob_intact(app, monkeypatch):
    """Two simultaneous /say calls on ONE thread (separate TestClients = two
    real threads, two event loops) must interleave whole entries, never
    corrupt the blob. A barrier aligns the two rounds at every speaker so the
    per-entry read→extend→commit races head-on — this test FAILS (lost
    entries) if the module append lock is removed."""
    import threading as _threading

    barrier = _threading.Barrier(2, timeout=5)

    async def slow(self, p, others, transcript, d):
        try:
            # Rendezvous with the other round so the appends that follow
            # collide as tightly as the lock allows.
            await asyncio.get_running_loop().run_in_executor(None, barrier.wait)
        except _threading.BrokenBarrierError:
            pass  # the other round finished — race window already exercised
        return f"{p['name']} answers"

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_local", slow)
    c1, c2 = TestClient(app), TestClient(app)
    tid = _mk_thread(c1, _panel3())
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [
            ex.submit(c.post, f"/agents/threads/{tid}/say", json={"message": m})
            for c, m in ((c1, "round A"), (c2, "round B"))
        ]
        results = [f.result() for f in futs]
    barrier.abort()  # release any last straggler
    assert all(r.status_code == 200 for r in results)
    # The RAW stored blob parses and holds every entry from both rounds.
    rec = AgentThreads(app.state.platform.engine).get(tid)
    msgs = json.loads(rec.messages_json)
    assert len(msgs) == 8  # 2 user turns + 2×3 speakers
    assert sum(1 for m in msgs if m["who"] == "user") == 2
    assert sum(1 for m in msgs if m["who"] == "builtin:planner") == 2
    assert sum(1 for m in msgs if m["who"] == "builtin:reviewer") == 2
    assert sum(1 for m in msgs if m["who"] == "builtin:hermes-mac-mini") == 2


# --- directed rounds (@-mentions) -----------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        # single mention by name
        ("@planner what's next?", ["builtin:planner"]),
        # multiple mentions
        ("@planner @reviewer weigh in", ["builtin:planner", "builtin:reviewer"]),
        # mention order never beats panel order
        ("@reviewer then @planner", ["builtin:planner", "builtin:reviewer"]),
        # role-based mention
        ("@critic tear this apart", ["builtin:reviewer"]),
        # case-insensitive
        ("@PLANNER caps still count", ["builtin:planner"]),
        # hyphenated name as-is
        ("@hermes-mac-mini status?", ["builtin:hermes-mac-mini"]),
        # quoted mention
        ('@"hermes-mac-mini" quoted too', ["builtin:hermes-mac-mini"]),
        # no mention matched anyone → everyone (today's behavior)
        (
            "hey @nobody, thoughts?",
            ["builtin:planner", "builtin:reviewer", "builtin:hermes-mac-mini"],
        ),
        # unmatched mentions are ignored when at least one matched
        ("@nobody @planner go", ["builtin:planner"]),
        # zero mentions → everyone
        (
            "plain message",
            ["builtin:planner", "builtin:reviewer", "builtin:hermes-mac-mini"],
        ),
        # sentence-ending punctuation never de-targets a mention
        ("@planner.", ["builtin:planner"]),
        ("@planner, and @reviewer.", ["builtin:planner", "builtin:reviewer"]),
        ("@hermes-mac-mini.", ["builtin:hermes-mac-mini"]),
        ("ship it @planner-", ["builtin:planner"]),
        # a mid-word @ is an address, not a mention: nobody is directed,
        # so the round falls back to everyone
        (
            "reach me at email@example.com",
            ["builtin:planner", "builtin:reviewer", "builtin:hermes-mac-mini"],
        ),
        # ...even when the domain would alias a participant's role
        (
            "mail me at someone@critic today",
            ["builtin:planner", "builtin:reviewer", "builtin:hermes-mac-mini"],
        ),
        # an email must not drown out a real mention either
        ("cc bob@critic — @planner take this", ["builtin:planner"]),
        # punctuation BEFORE the @ is fine (parentheses, quotes)
        ("(@planner)", ["builtin:planner"]),
    ],
)
def test_directed_rounds_pick_the_speakers(client, monkeypatch, message, expected):
    _canned(monkeypatch)
    tid = _mk_thread(client, _panel3())
    r = client.post(f"/agents/threads/{tid}/say", json={"message": message})
    body = r.json()
    speakers = [e["who"] for e in body["entries"] if e["who"] != "user"]
    assert speakers == expected
    assert body["spoke"] == expected


def test_mentioned_message_stays_verbatim_in_the_transcript(client, monkeypatch):
    _canned(monkeypatch)
    tid = _mk_thread(client, _panel())
    client.post(f"/agents/threads/{tid}/say", json={"message": "@planner ship it"})
    got = client.get(f"/agents/threads/{tid}").json()
    assert got["messages"][0]["content"] == "@planner ship it"


def test_empty_continue_message_still_means_everyone(client, monkeypatch):
    _canned(monkeypatch)
    tid = _mk_thread(client, _panel())
    client.post(f"/agents/threads/{tid}/say", json={"message": "@planner solo"})
    r = client.post(f"/agents/threads/{tid}/say", json={"message": ""})
    assert [e["who"] for e in r.json()["entries"]] == [
        "builtin:planner",
        "builtin:reviewer",
    ]


# --- offline-remote skip --------------------------------------------------------


def _remote_panel():
    return [
        {"source": "remote", "name": "mini", "role": "scout"},
        {"source": "builtin", "name": "planner", "role": "lead"},
    ]


def test_disabled_remote_is_skipped_before_any_network_call(app, client, monkeypatch):
    _canned(monkeypatch)
    RemoteAgentRegistry(app.state.platform.engine).upsert(
        "mini", "http://127.0.0.1:9", "http-task", enabled=False
    )

    async def never(self, *a, **k):  # the fast-path must not reach transport
        raise AssertionError("_speak_remote must not run for a disabled remote")

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_remote", never)
    tid = _mk_thread(client, _remote_panel())
    body = client.post(
        f"/agents/threads/{tid}/say", json={"message": "check in"}
    ).json()
    entry = next(e for e in body["entries"] if e["who"] == "remote:mini")
    assert entry["content"] == ""
    assert entry["error"] == "mini is offline (disabled) — skipped."
    assert body["skipped"] == ["remote:mini"]
    assert body["spoke"] == ["builtin:planner"]


def test_unregistered_remote_is_skipped_too(client, monkeypatch):
    _canned(monkeypatch)

    async def never(self, *a, **k):
        raise AssertionError("_speak_remote must not run for a missing remote")

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_remote", never)
    tid = _mk_thread(client, [{"source": "remote", "name": "ghost", "role": "scout"}])
    body = client.post(f"/agents/threads/{tid}/say", json={"message": "hello"}).json()
    entry = body["entries"][-1]
    assert entry["error"] == "ghost is offline (not registered) — skipped."
    assert body["skipped"] == ["remote:ghost"]
    assert body["spoke"] == []


def test_enabled_but_failing_remote_keeps_the_honest_error_path(
    app, client, monkeypatch
):
    _canned(monkeypatch)
    RemoteAgentRegistry(app.state.platform.engine).upsert(
        "mini", "http://127.0.0.1:9", "http-task", enabled=True
    )

    async def fail(self, p, transcript, d, record=None):
        assert record is not None  # run_round hands over the preloaded row
        raise RuntimeError("endpoint exploded")

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_remote", fail)
    tid = _mk_thread(client, _remote_panel())
    body = client.post(f"/agents/threads/{tid}/say", json={"message": "go"}).json()
    entry = next(e for e in body["entries"] if e["who"] == "remote:mini")
    assert "mini couldn't answer: endpoint exploded" in entry["error"]
    assert body["skipped"] == []  # it TRIED — that's spoke, not skipped
    assert body["spoke"] == ["remote:mini", "builtin:planner"]


# --- the /say response contract -------------------------------------------------


def test_say_response_contract_entries_spoke_skipped(client, monkeypatch):
    _canned(monkeypatch)
    tid = _mk_thread(client, _panel())
    body = client.post(f"/agents/threads/{tid}/say", json={"message": "hello"}).json()
    assert set(body) >= {"entries", "spoke", "skipped"}
    assert [e["who"] for e in body["entries"]] == [
        "user",
        "builtin:planner",
        "builtin:reviewer",
    ]
    assert body["spoke"] == ["builtin:planner", "builtin:reviewer"]
    assert body["skipped"] == []
