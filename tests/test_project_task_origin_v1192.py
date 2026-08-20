"""v1.192.0 — a Projects task ASSERTS its watching human (P15).

`AgentRuntime._pause_for_approval` (v1.189.0) only pauses a run whose ORIGIN
states that somebody is present, and its allowlist has always named a Projects
task (``"project…"``). That branch was DEAD: `POST /projects/{id}/task` called
`create_session` with no `origin` at all — unlike `routes/sessions.py` and
`routes/agents.py`, which pass `body.origin` — so an in-folder project task ran
UNATTRIBUTED. An ask-tier tool call (shell in the user's own folder) never
published `approval.requested`; it went straight to the headless resolver and
was denied, while the user sat on the Projects page watching a run report
"permission denied" for work they would have approved.

These tests pin BOTH halves of the pairing, because either alone is useless:
the route must stamp `project:<project id>`, and that exact string must be what
the runtime's allowlist admits. A test that only asserted the literal would go
green if someone narrowed the runtime prefix, so the second test feeds the
route's OWN output into the real `_pause_for_approval`.

Note the renderer half is a separate contract: `ProjectApprovals` on the
project page consumes `approval.requested` (membership resolved via the
session's `project_id`). Without a renderer this stamp would convert an instant
honest denial into a silent 300s timeout-deny — strictly worse.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app


def _post_project_task(root) -> tuple[str, dict]:
    """Create a project with a real folder, post an in-folder task, and hand
    back ``(project_id, the session view)`` the route returned."""
    root.mkdir(parents=True, exist_ok=True)
    folder = root / "acme"
    folder.mkdir()
    # `with` keeps the app loop alive so the spawned session actually runs
    # instead of being cancelled at per-request teardown.
    with TestClient(create_app(str(root / "state"))) as client:
        project = client.post(
            "/projects", json={"name": "Acme", "root": str(folder)}
        ).json()
        r = client.post(
            f"/projects/{project['id']}/task",
            json={"text": "convert these files and run the script", "output": "chat"},
        )
        assert r.status_code == 200, r.text
        view = r.json()
        # Let the mock-backed run settle so teardown is clean.
        deadline = time.time() + 15
        while time.time() < deadline:
            got = client.get(f"/sessions/{view['id']}").json()
            if (got.get("session") or got).get("status") in (
                "completed",
                "failed",
                "cancelled",
            ):
                break
            time.sleep(0.2)
        return project["id"], view


def test_a_projects_task_stamps_a_project_origin(tmp_path):
    project_id, view = _post_project_task(tmp_path / "a")
    # The project id rides IN the origin so the audit timeline can say WHICH
    # project started the run, not merely that some project did.
    assert view["origin"] == f"project:{project_id}"


def test_the_persisted_session_carries_the_stamp(tmp_path):
    """The stamp must survive the write — the runtime reads it off the row it
    loads, not off the route's response."""
    root = tmp_path / "b"
    root.mkdir()
    folder = root / "acme"
    folder.mkdir()
    with TestClient(create_app(str(root / "state"))) as client:
        project = client.post(
            "/projects", json={"name": "Acme", "root": str(folder)}
        ).json()
        sid = client.post(
            f"/projects/{project['id']}/task",
            json={"text": "inventory the folder", "output": "chat"},
        ).json()["id"]
        deadline = time.time() + 15
        while time.time() < deadline:
            got = client.get(f"/sessions/{sid}").json()["session"]
            if got["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.2)
        assert got["origin"] == f"project:{project['id']}"


@pytest.mark.asyncio
async def test_the_stamped_origin_is_what_the_pause_allowlist_admits(tmp_path):
    """THE PAIRING. The origin the route actually produces is fed to the real
    `_pause_for_approval`: an ask-tier call must PAUSE and publish
    `approval.requested` tagged with the session id, and the user's answer must
    be the one that decides. Before the stamp, origin was None and this
    returned instantly with no request published — the headless denial the
    Projects surface has been living with."""
    _project_id, view = await asyncio.to_thread(_post_project_task, tmp_path / "c")
    origin = view["origin"] or ""

    app = create_app(str(tmp_path / "rt"))
    platform = app.state.platform
    published: list[dict] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append(
            {"type": type, "payload": payload or {}, "session_id": session_id}
        )
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    runtime = AgentRuntime(platform)

    async def answer_deny():
        for _ in range(300):
            req = next(
                (p for p in published if p["type"] == "approval.requested"), None
            )
            if req:
                assert req["session_id"] == "session_project_task"
                assert req["payload"]["tool"] == "shell"
                platform.approvals.resolve(req["payload"]["approval_id"], "deny")
                return
            await asyncio.sleep(0.01)
        raise AssertionError(
            "an in-folder Projects task never asked — origin "
            f"{origin!r} does not satisfy the pause allowlist"
        )

    answerer = asyncio.create_task(answer_deny())
    deny, extra = await runtime._pause_for_approval(
        SimpleNamespace(id="session_project_task", origin=origin),
        SimpleNamespace(name="shell", arguments={"command": "python verify.py"}),
        get_agent_definition(AgentType.BUILDER),
        set(),
    )
    await answerer

    # A real human answered — not the headless resolver's "nothing here could
    # ask", and not the 300s timeout.
    assert "declined" in deny and extra == set()
