"""@mention an agent from chat (v1.150.0).

REQUESTED: "use something like @ to reach out to a remote or a preconfigured
agent registered in the agents module to do a specific task, and even chat with
multiple at the same time — and when the agents chat with each other those
inter-agent chats would also appear in the agents module."

Almost none of this is new machinery, and that is the design: ``agents/threads``
already ran cross-source panels (built-in / custom / remote) where each agent
sees the previous speakers' answers, already directed rounds with @mentions,
already persisted live. What was missing was the CONNECTION — chat could not
reach it, and a panel started from chat had no home on the Agents page.

So the properties worth pinning are the joins, not the round:

* a mention resolves through the SAME roster the delegation prompt reads
  (:func:`test_the_picker_and_the_delegation_roster_are_one_list`), or the
  picker and the model would disagree about who exists;
* the panel is an ORDINARY agent thread, which is what makes the inter-agent
  transcript appear under Agents with no extra plumbing
  (:func:`test_the_panel_appears_in_the_agents_module`);
* one panel per chat thread, so an agent mentioned in turn 3 can see what was
  said in turn 2 (:func:`test_an_agent_mentioned_later_joins_the_same_room`).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.threads import parse_mentions
from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


# --------------------------------------------------------------------------- #
# (1) Mention parsing — ONE definition, shared with the round.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("@builder draft it", ["builder"]),
        ("@builder @reviewer both please", ["builder", "reviewer"]),
        ('ask @"Two Words" about it', ["two words"]),
        ("@builder. and then?", ["builder"]),          # sentence punctuation
        ("@hermes-mac-mini status?", ["hermes-mac-mini"]),
        ("@builder @builder twice", ["builder"]),      # deduplicated
    ],
)
def test_mentions_are_parsed(text, expected):
    assert parse_mentions(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "mail me at someone@example.com",   # an address, not a mention
        "planner@critic is not a mention",  # glued to a word
        "the rate is 40@ per unit",
        "no mentions here at all",
        "",
    ],
)
def test_non_mentions_are_left_alone(text):
    assert parse_mentions(text) == []


# --------------------------------------------------------------------------- #
# (2) The picker and the delegation roster are the same list.
# --------------------------------------------------------------------------- #
def test_the_picker_and_the_delegation_roster_are_one_list(tmp_path):
    """Two catalogs would drift, and then a user could mention someone the model
    cannot delegate to (or vice versa)."""
    client = _client(tmp_path)
    picker = {a["name"] for a in client.get("/agents/mentionable").json()["agents"]}
    roster = {r["name"] for r in client.get("/agents/roster").json()["roster"]}
    assert picker == roster
    assert "builder" in picker


def test_the_picker_carries_what_the_dropdown_needs(tmp_path):
    agents = _client(tmp_path).get("/agents/mentionable").json()["agents"]
    one = next(a for a in agents if a["mention"] == "builder")
    assert one["kind"] == "builtin" and one["source"] == "builtin"
    assert one["healthy"] is True
    assert one["description"]


def test_an_offline_remote_is_listed_not_hidden(tmp_path):
    """"my agent isn't in the list" is a worse failure than "it says offline"."""
    client = _client(tmp_path)
    r = client.post(
        "/agents/remote",
        json={
            "name": "ghost-box",
            "base_url": "http://127.0.0.1:9/unreachable",
            "kind": "http-task",
        },
    )
    assert r.status_code in (200, 201), r.text
    agents = client.get("/agents/mentionable").json()["agents"]
    ghost = next((a for a in agents if a["mention"] == "ghost-box"), None)
    assert ghost is not None, "a registered remote must be mentionable"
    assert ghost["kind"] == "remote"


# --------------------------------------------------------------------------- #
# (3) The round itself: only the mentioned agents speak, in order.
# --------------------------------------------------------------------------- #
def test_only_the_mentioned_agents_answer(tmp_path):
    client = _client(tmp_path)
    body = client.post(
        "/chat/panel",
        json={"message": "@builder @reviewer draft the intake form", "chat_thread_id": "c1"},
    ).json()
    assert body["spoke"] == ["builtin:builder", "builtin:reviewer"]
    who = [e["who"] for e in body["entries"]]
    assert who[0] == "user", "the user's own turn opens the transcript"
    assert who[1:] == ["builtin:builder", "builtin:reviewer"]


def test_a_message_with_no_mentions_is_refused_by_the_panel_route(tmp_path):
    """The CLIENT decides which lane a message takes; this route exists only for
    the mentioned lane and says so rather than quietly answering as chat."""
    r = _client(tmp_path).post("/chat/panel", json={"message": "just a question"})
    assert r.status_code == 400


def test_an_unmatched_mention_is_reported_not_swallowed(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/chat/panel",
        json={"message": "@builder and @nobody-here look at this", "chat_thread_id": "c1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["unknown_mentions"] == ["nobody-here"]
    assert body["spoke"] == ["builtin:builder"], "the real one still answers"


def test_mentioning_only_unknown_agents_is_an_honest_404(tmp_path):
    r = _client(tmp_path).post(
        "/chat/panel", json={"message": "@nobody @nothing hello"}
    )
    assert r.status_code == 404
    assert "nobody" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# (4) THE LINKAGE — the inter-agent conversation lives in the Agents module.
# --------------------------------------------------------------------------- #
def test_the_panel_appears_in_the_agents_module(tmp_path):
    client = _client(tmp_path)
    body = client.post(
        "/chat/panel",
        json={"message": "@builder @reviewer draft the form", "chat_thread_id": "c1"},
    ).json()

    threads = client.get("/agents/threads").json()["threads"]
    assert len(threads) == 1
    thread = threads[0]
    assert thread["id"] == body["thread_id"]
    assert {p["name"] for p in thread["participants"]} == {"builder", "reviewer"}

    # ...and the full inter-agent transcript is there, not just a pointer.
    detail = client.get(f"/agents/threads/{thread['id']}").json()
    who = [m["who"] for m in detail["messages"]]
    assert who == ["user", "builtin:builder", "builtin:reviewer"]


def test_an_agent_mentioned_later_joins_the_same_room(tmp_path):
    """Turn 3's @planner must SEE what was said in turns 1-2 — that only works
    if it is one thread, which is the whole reason the panel is bound to the
    chat thread instead of being minted per message."""
    client = _client(tmp_path)
    first = client.post(
        "/chat/panel", json={"message": "@builder draft it", "chat_thread_id": "c1"}
    ).json()
    second = client.post(
        "/chat/panel", json={"message": "@planner what did builder miss?", "chat_thread_id": "c1"}
    ).json()

    assert second["thread_id"] == first["thread_id"], "a second room would lose history"
    assert second["spoke"] == ["builtin:planner"], "only the newly mentioned one speaks"

    detail = client.get(f"/agents/threads/{first['thread_id']}").json()
    assert {p["name"] for p in detail["participants"]} == {"builder", "planner"}
    assert [m["who"] for m in detail["messages"]] == [
        "user", "builtin:builder", "user", "builtin:planner",
    ]


def test_separate_chat_threads_get_separate_panels(tmp_path):
    client = _client(tmp_path)
    a = client.post(
        "/chat/panel", json={"message": "@builder one", "chat_thread_id": "c1"}
    ).json()
    b = client.post(
        "/chat/panel", json={"message": "@builder two", "chat_thread_id": "c2"}
    ).json()
    assert a["thread_id"] != b["thread_id"]
    assert len(client.get("/agents/threads").json()["threads"]) == 2


def test_an_unsaved_chat_still_gets_a_panel(tmp_path):
    """A brand-new conversation has no thread id yet; mentioning an agent must
    still work rather than 400 on a missing binding."""
    r = _client(tmp_path).post("/chat/panel", json={"message": "@builder hello"})
    assert r.status_code == 200
    assert r.json()["spoke"] == ["builtin:builder"]


def test_a_panel_started_on_the_agents_page_is_unaffected(tmp_path):
    """chat_thread_id is additive: threads made the old way keep working and are
    never adopted by a chat conversation."""
    client = _client(tmp_path)
    made = client.post(
        "/agents/threads",
        json={
            "title": "manual panel",
            "participants": [{"source": "builtin", "name": "builder", "role": "lead"}],
        },
    ).json()
    from_chat = client.post(
        "/chat/panel", json={"message": "@builder hi", "chat_thread_id": "c1"}
    ).json()
    assert from_chat["thread_id"] != made["id"]
    assert len(client.get("/agents/threads").json()["threads"]) == 2


# --------------------------------------------------------------------------- #
# (5) Schema: the additive column self-heals on an existing DB.
# --------------------------------------------------------------------------- #
def test_the_chat_binding_column_is_additive(tmp_path):
    """AgentThreadRecord gained chat_thread_id — an existing .ironjarvis DB must
    self-heal through _reconcile_additive_columns rather than 'no such column'."""
    from sqlalchemy import text

    from iron_jarvis.core.db import open_db

    engine = open_db(str(tmp_path / "t.db"))
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text('PRAGMA table_info("agentthreadrecord")'))}
    assert "chat_thread_id" in cols
