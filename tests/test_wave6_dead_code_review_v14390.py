"""Wave 6, group E review (v1.439.0): coverage of LIVE code that the
round-table route removal took with it.

``POST /projects/{id}/world/room`` was the only door whose pins checked how
the SERVER seats a project's team: in team order, a builtin as itself, a
custom agent as its ``base_type``, a remote as an advisor, and a member the
roster no longer knows not at all (``world.team_seats`` / ``seat_role``). The
door is gone, but the rule is not: ``PUT /projects/{id}/team`` re-seats an
existing project room through ``world.sync_room`` -> ``team_seats``, and a
NEWCOMER takes exactly that seat. The builder's rewritten pin re-seated only
builtins, so ``seat_role`` could seat every custom agent and remote as a bare
"participant" and the suite stayed green. These pins drive the live route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from tests._fakes.rooms import rooms


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _project(client, name="Acme") -> str:
    r = client.post("/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seats(client, room_id: str) -> list[tuple[str, str]]:
    parts = client.get(f"/agents/threads/{room_id}").json()["participants"]
    return [(p["key"], p["role"]) for p in parts]


def test_a_team_change_seats_each_newcomer_with_its_roster_role(client):
    assert client.post(
        "/agents", json={"name": "taxpro", "system_prompt": "tax", "base_type": "researcher"}
    ).status_code == 200
    assert client.post(
        "/agents/remote", json={"name": "hermes", "base_url": "http://127.0.0.1:9"}
    ).status_code == 200
    pid = _project(client)
    # A project room the backend made, with one seat the user set by hand.
    room = rooms(client).post(
        "/agents/threads",
        json={"participants": [{"source": "builtin", "name": "builder", "role": "lead"}],
              "project_id": pid},
    ).json()

    r = client.put(
        f"/projects/{pid}/team",
        json={"members": ["builder", "taxpro", "remote:hermes", "reviewer"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["room_synced"] == room["id"]
    # Team order; the stayer keeps the role the user set; each newcomer sits
    # with its ROSTER role: a custom agent as its base type, a remote as an
    # advisor, a builtin as itself.
    assert _seats(client, room["id"]) == [
        ("builtin:builder", "lead"),
        ("dynamic:taxpro", "researcher"),
        ("remote:hermes", "advisor"),
        ("builtin:reviewer", "reviewer"),
    ]


def test_a_member_the_roster_does_not_know_is_never_seated(client):
    """``team_seats`` leaves out a name the roster cannot match (a seat for it
    could only ever answer "no longer exists"). Driven through the team
    route's re-seat, the one live caller."""
    pid = _project(client)
    room = rooms(client).post(
        "/agents/threads",
        json={"participants": [{"source": "builtin", "name": "builder", "role": "builder"}],
              "project_id": pid},
    ).json()
    from iron_jarvis.projects import world

    # The team route refuses an unknown name before saving, so the
    # roster-skip rule is reached the way a member that LEFT the roster
    # after the team was saved reaches it: sync_room with the saved list.
    platform = client.app.state.platform
    assert world.sync_room(platform, pid, ["builder", "ghost-agent", "reviewer"]) == room["id"]
    assert _seats(client, room["id"]) == [
        ("builtin:builder", "builder"),
        ("builtin:reviewer", "reviewer"),
    ]
