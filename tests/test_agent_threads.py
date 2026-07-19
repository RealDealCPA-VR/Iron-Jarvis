"""Agent threads: cross-source panels with roles (the Agents page redesign).

A thread's participants are agents from any approved source — builtin types,
dynamic agents, registered remote agents — each holding a role assigned at
setup. One /say = one speaking ROUND: every participant answers in panel
order, each seeing the replies before it (that ordering is what makes it a
conversation, not N parallel answers). A failing participant contributes an
honest error entry and never sinks the round. Offline throughout."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.threads import clean_participants
from iron_jarvis.daemon.app import create_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _panel():
    return [
        {"source": "builtin", "name": "planner", "role": "lead"},
        {"source": "builtin", "name": "reviewer", "role": "critic"},
    ]


# --- participant validation -----------------------------------------------------


def test_clean_participants_normalizes_and_keys():
    out = clean_participants([{"source": "builtin", "name": "planner", "role": ""}])
    assert out[0]["key"] == "builtin:planner"
    assert out[0]["role"] == "participant"  # blank role gets an honest default


@pytest.mark.parametrize(
    "raw,msg",
    [
        ([], "at least one"),
        ([{"source": "alien", "name": "x"}], "unknown agent source"),
        ([{"source": "builtin", "name": ""}], "name is required"),
        (
            [
                {"source": "builtin", "name": "planner"},
                {"source": "builtin", "name": "planner"},
            ],
            "already in this thread",
        ),
    ],
)
def test_clean_participants_rejects_bad_panels(raw, msg):
    with pytest.raises(ValueError, match=msg):
        clean_participants(raw)


# --- CRUD -----------------------------------------------------------------------


def test_thread_crud_round_trip(client):
    r = client.post(
        "/agents/threads", json={"title": "Panel", "participants": _panel()}
    )
    assert r.status_code == 200
    tid = r.json()["id"]
    assert [p["key"] for p in r.json()["participants"]] == [
        "builtin:planner",
        "builtin:reviewer",
    ]

    rows = client.get("/agents/threads").json()["threads"]
    assert rows[0]["id"] == tid
    assert "messages" not in rows[0]  # list rows stay light
    assert rows[0]["message_count"] == 0

    # Edit the panel: roles are reassignable after setup.
    upd = client.put(
        f"/agents/threads/{tid}/participants",
        json={"participants": [{"source": "builtin", "name": "planner", "role": "scribe"}]},
    )
    assert upd.status_code == 200
    assert upd.json()["participants"][0]["role"] == "scribe"

    assert client.delete(f"/agents/threads/{tid}").json()["deleted"] == tid
    assert client.get(f"/agents/threads/{tid}").status_code == 404


def test_bad_panel_is_a_400_not_a_500(client):
    r = client.post(
        "/agents/threads", json={"participants": [{"source": "alien", "name": "x"}]}
    )
    assert r.status_code == 400
    assert "unknown agent source" in r.json()["detail"]


# --- the speaking round ---------------------------------------------------------


def test_say_runs_one_round_in_panel_order(client):
    tid = client.post(
        "/agents/threads", json={"title": "T", "participants": _panel()}
    ).json()["id"]
    r = client.post(f"/agents/threads/{tid}/say", json={"message": "Ship Friday?"})
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e["who"] for e in entries] == ["user", "builtin:planner", "builtin:reviewer"]
    for e in entries[1:]:
        assert e["content"]  # the offline mock answers; empty never counts as ok
        assert e["role"] in ("lead", "critic")

    # Persisted: the transcript survives a reload.
    got = client.get(f"/agents/threads/{tid}").json()
    assert got["message_count"] == 3
    assert got["messages"][0]["who"] == "user"


def test_empty_message_continues_the_panel_without_a_user_turn(client):
    tid = client.post(
        "/agents/threads", json={"participants": _panel()}
    ).json()["id"]
    client.post(f"/agents/threads/{tid}/say", json={"message": "topic"})
    r = client.post(f"/agents/threads/{tid}/say", json={"message": ""})
    entries = r.json()["entries"]
    assert [e["who"] for e in entries] == ["builtin:planner", "builtin:reviewer"]


def test_each_speaker_sees_the_replies_before_it(client, monkeypatch):
    """Panel order is the whole point: the second agent's prompt must contain
    the first agent's answer from THIS round."""
    from iron_jarvis.agents import threads as threads_mod

    seen: list[str] = []
    orig = threads_mod.AgentThreads._speak_local

    async def spy(self, p, others, transcript, d):
        seen.append(f"{p['name']}::{transcript}")
        return f"{p['name']} says hello"

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_local", spy)
    tid = client.post("/agents/threads", json={"participants": _panel()}).json()["id"]
    client.post(f"/agents/threads/{tid}/say", json={"message": "hi panel"})
    assert len(seen) == 2
    assert "planner says hello" not in seen[0]
    assert "planner says hello" in seen[1]  # reviewer saw planner's fresh reply
    assert "planner (lead)" in seen[1]  # labelled with name + role


def test_a_failing_participant_is_an_honest_entry_not_a_sunk_round(client, monkeypatch):
    from iron_jarvis.agents import threads as threads_mod

    async def flaky(self, p, others, transcript, d):
        if p["name"] == "planner":
            raise RuntimeError("provider exploded")
        return "reviewer still answers"

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_local", flaky)
    tid = client.post("/agents/threads", json={"participants": _panel()}).json()["id"]
    r = client.post(f"/agents/threads/{tid}/say", json={"message": "go"})
    entries = r.json()["entries"]
    planner = next(e for e in entries if e["who"] == "builtin:planner")
    reviewer = next(e for e in entries if e["who"] == "builtin:reviewer")
    assert "provider exploded" in planner["error"]
    assert planner["content"] == ""  # never a fabricated answer
    assert reviewer["content"] == "reviewer still answers"


def test_dynamic_agent_participates_with_its_own_persona(client, monkeypatch):
    client.post(
        "/agents",
        json={"name": "taxpro", "system_prompt": "You are a sharp tax accountant."},
    )
    from iron_jarvis.agents import threads as threads_mod

    systems: list[str] = []
    orig_speak = threads_mod.AgentThreads._speak_local

    async def spy(self, p, others, transcript, d):
        if p["source"] == "dynamic":
            row = d.platform.agents_registry.get(p["name"])
            systems.append(self._system_for(p, others, row.system_prompt))
        return "ok"

    monkeypatch.setattr(threads_mod.AgentThreads, "_speak_local", spy)
    tid = client.post(
        "/agents/threads",
        json={"participants": [{"source": "dynamic", "name": "taxpro", "role": "lead"}]},
    ).json()["id"]
    client.post(f"/agents/threads/{tid}/say", json={"message": "hello"})
    assert systems and "sharp tax accountant" in systems[0]
    assert "the lead in a panel" in systems[0]


def test_missing_dynamic_agent_fails_honestly(client):
    tid = client.post(
        "/agents/threads",
        json={"participants": [{"source": "dynamic", "name": "ghost", "role": "lead"}]},
    ).json()["id"]
    r = client.post(f"/agents/threads/{tid}/say", json={"message": "hello"})
    entry = r.json()["entries"][-1]
    assert "no longer exists" in entry["error"]


def test_say_on_missing_thread_is_404(client):
    assert client.post("/agents/threads/nope/say", json={"message": "x"}).status_code == 404
