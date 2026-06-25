"""Adversarial daemon probes — end-to-end regressions for the red-team audit.

These exercise the *wired daemon* (create_app + TestClient), not modules in
isolation:

  * supervised delegation works headless (no interactive approver),
  * git-native run -> review -> approve flows over HTTP and cleans up worktrees,
  * the WebSocket event stream actually streams,
  * error paths return clean status codes (bad layer -> 400, not 500).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.workflows.models  # noqa: F401  (register table for build_platform)
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.permissions import headless_ask_resolver


# --- 1. Supervised delegation, headless (the core suspected gap) -------------


async def test_supervised_delegation_headless_through_orchestrator(tmp_path):
    """Supervisor delegates end-to-end WITHOUT manually overriding permissions.

    The only wiring is the headless ask-resolver the daemon uses; ``delegate``
    is still ``ask`` in the policy, but the resolver auto-approves it.
    """
    p = build_platform(str(tmp_path), ask_resolver=headless_ask_resolver())
    # Sanity: we did NOT touch the permission policy — delegate is still "ask".
    assert p.config.permissions["delegate"] == "ask"

    session = await Orchestrator(p).run(
        "Coordinate building a thing", AgentType.SUPERVISOR
    )
    assert session.status is SessionStatus.COMPLETED

    with session_scope(p.engine) as db:
        runs = list(db.exec(select(AgentRun)))
    supervisor = [r for r in runs if r.session_id == session.id]
    assert supervisor and supervisor[0].agent_type is AgentType.SUPERVISOR
    children = [r for r in runs if r.parent_id == supervisor[0].id]
    assert children, "supervisor must spawn a delegated child run"
    assert children[0].state is AgentState.COMPLETED
    assert children[0].provider == "mock"


def test_supervised_delegation_headless_over_http(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    created = client.post(
        "/sessions",
        json={"task": "coordinate a build", "agent_type": "supervisor", "wait": True},
    ).json()
    assert created["status"] == "completed"

    detail = client.get(f"/sessions/{created['id']}").json()
    tools = detail["transcript"]["tools"]
    # The supervisor actually invoked delegate, and it was allowed (not denied).
    delegate_calls = [t for t in tools if t["tool"] == "delegate"]
    assert delegate_calls, "supervisor should have called delegate over HTTP"
    assert all(t["ok"] for t in delegate_calls)
    assert all(t["verdict"] != "deny" for t in delegate_calls)


def test_shell_stays_fail_closed_under_headless_resolver():
    """The headless resolver must NOT open up genuinely dangerous tools."""
    resolver = headless_ask_resolver()
    assert resolver("delegate", {}) is True
    assert resolver("shell", {}) is False
    assert resolver("anything_else", {}) is False


# --- 2. Error paths ----------------------------------------------------------


def test_memory_bad_layer_returns_400(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/memory", json={"layer": "bogus", "key": "k", "text": "t"})
    assert r.status_code == 400
    assert "layer" in r.json()["detail"]


def test_daemon_error_paths(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/sessions/nope").status_code == 404
    assert client.get("/artifacts/nope").status_code == 404
    assert client.get("/memory/project/missing").status_code == 404
    assert client.post("/workflows/run", json={}).status_code == 400
    # an unknown agent_type does not crash — it falls back to builder
    ok = client.post("/sessions", json={"task": "x", "agent_type": "zzz", "wait": True})
    assert ok.status_code == 200 and ok.json()["agent_type"] == "builder"


# --- 3. Workflow run over HTTP ----------------------------------------------


def test_workflow_run_over_http(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    rec = client.post(
        "/workflows/run",
        json={
            "name": "wf",
            "steps": [
                {"name": "s1", "agent": "builder", "task": "create a file"},
                {"name": "s2", "agent": "builder", "task": "create another"},
            ],
        },
    ).json()
    assert rec["status"] == "completed"
    runs = client.get("/workflows/runs").json()["runs"]
    assert any(r["id"] == rec["id"] for r in runs)


# --- 4. WebSocket /events streams -------------------------------------------


def test_events_websocket_streams(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    with client.websocket_connect("/events") as ws:
        created = client.post(
            "/sessions", json={"task": "make a file", "wait": True}
        ).json()
        seen: list[dict] = []
        for _ in range(40):  # all events are already queued; stop at the last one
            msg = ws.receive_json()
            seen.append(msg)
            if msg["type"] == "session.completed":
                break
    types = [m["type"] for m in seen]
    assert "session.created" in types
    assert "tool.executed" in types
    assert "session.completed" in types
    # events carry the right session id
    assert any(m["session_id"] == created["id"] for m in seen)


# --- 5. Git-native review/approve over HTTP + worktree cleanup --------------


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)

    def g(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    g("init", "-q")
    (repo / ".gitignore").write_text(".ironjarvis/\n", encoding="utf-8")
    (repo / "README.md").write_text("hello", encoding="utf-8")
    g("add", "-A")
    g("commit", "-qm", "base")


def _worktree_list(repo: Path) -> str:
    return subprocess.run(
        ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True
    ).stdout


def test_git_native_review_approve_over_http(tmp_path, monkeypatch):
    repo = tmp_path / "proj"
    _init_git_repo(repo)
    monkeypatch.setenv("IRONJARVIS_GIT_NATIVE", "1")

    client = TestClient(create_app(str(repo)))
    created = client.post(
        "/sessions", json={"task": "create a summary file", "wait": True}
    ).json()
    sid = created["id"]

    # A review exists (git-native is on) and the base is untouched pre-approval.
    review = client.get(f"/sessions/{sid}/review")
    assert review.status_code == 200, review.text
    body = review.json()
    assert any("RESULT.md" in f for f in body["changed_files"])
    assert body["branch"].startswith("ironjarvis/session-")
    assert body["risk"] in {"low", "medium", "high"}
    assert (repo / "README.md").read_text(encoding="utf-8") == "hello"
    assert not (repo / "RESULT.md").exists()

    branch = body["branch"]
    # Pre-approval the session's worktree is a registered worktree.
    assert sid in _worktree_list(repo)

    approved = client.post(f"/reviews/{sid}/approve").json()
    assert "merged" in approved

    # Post-approval: change landed on base, and the worktree+branch were cleaned
    # up (no accumulation/leak), and the review is gone.
    assert (repo / "RESULT.md").exists()
    assert sid not in _worktree_list(repo)
    branches = subprocess.run(
        ["git", "branch", "--list", branch], cwd=repo, capture_output=True, text=True
    ).stdout
    assert branch not in branches
    assert client.get(f"/sessions/{sid}/review").status_code == 404


def test_review_404_when_git_native_disabled(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    created = client.post("/sessions", json={"task": "x", "wait": True}).json()
    assert client.get(f"/sessions/{created['id']}/review").status_code == 404
