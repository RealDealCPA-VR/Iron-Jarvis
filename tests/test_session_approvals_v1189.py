"""v1.189.0 — the folder rides the escalation, and the approval reaches chat.

Both halves diagnosed from ONE measured run (session_a63b0a4f, the rename
acceptance job escalated from chat, 27 real tax documents):

* `rename_file` and `write_file` refused every path as outside the workspace —
  because the session worked in a SCRATCH dir. Chat's tools operate in the
  grounded folder; the session the turn escalated into lost it. `POST
  /sessions` (and the dynamic spawn) now carry `workspace_root`, validated by
  ONE predicate (`fs_policy.usable_workspace_root`) and honestly 400'd when
  invalid — a job silently rehomed to a scratch dir is the failure this field
  exists to close.

* `shell` died on the headless resolver three times ("nothing here could
  ask"), the blocked agent filed a capability request for a tool it already
  had, and the user found THAT on the Tools page while watching chat. The
  runtime now PAUSES an interactive-origin run on an ask-tier call — the same
  registry, the same `POST /chat/approvals/{id}` answer route as chat's
  v1.187.0 ask — publishing `approval.requested`/`approval.resolved` tagged
  with the session id so the card renders under the turn the user is watching.
  Runs with nobody present (schedule/autonomy/comm/reflex origins) keep the
  instant honest denial.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.agents.runtime as runtime_mod
from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.fs_policy import usable_workspace_root
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


# --------------------------------------------------------------------------- #
# A. The folder rides the escalation.
# --------------------------------------------------------------------------- #


def test_post_sessions_runs_directly_in_the_named_folder(tmp_path):
    client = _client(tmp_path)
    folder = tmp_path / "messy tax documents"
    folder.mkdir()

    r = client.post(
        "/sessions",
        json={"task": "rename everything", "wait": False,
              "workspace_root": str(folder)},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    got = client.get(f"/sessions/{sid}").json()["session"]
    # DIRECTLY in the user's folder — not a scratch dir named after the
    # session. This is the whole fix: rename_file confines to the workspace,
    # so the workspace must be the folder the job is about.
    assert got["workspace_path"] == str(folder)


def test_an_invalid_folder_is_an_honest_400_not_a_silent_scratch_dir(tmp_path):
    client = _client(tmp_path)
    for bad in ("relative/path", str(tmp_path / "does-not-exist")):
        r = client.post(
            "/sessions",
            json={"task": "t", "wait": False, "workspace_root": bad},
        )
        assert r.status_code == 400, bad
        assert "workspace_root" in r.json()["detail"]
    # Blank = not specified = today's scratch-workspace behaviour, unchanged.
    r = client.post("/sessions", json={"task": "t", "wait": False,
                                       "workspace_root": ""})
    assert r.status_code == 200


def test_the_dynamic_spawn_carries_the_folder_too(tmp_path):
    client = _client(tmp_path)
    folder = tmp_path / "client folder"
    folder.mkdir()
    client.post(
        "/agents",
        json={"name": "renamer", "system_prompt": "rename well", "tools": []},
    )

    r = client.post(
        "/agents/renamer/spawn",
        json={"task": "rename everything", "wait": False,
              "workspace_root": str(folder)},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    got = client.get(f"/sessions/{sid}").json()["session"]
    assert got["workspace_path"] == str(folder)


def test_one_predicate_answers_every_door(tmp_path):
    folder = tmp_path / "ok"
    folder.mkdir()
    assert usable_workspace_root(str(folder))
    assert not usable_workspace_root("relative/x")
    assert not usable_workspace_root(str(tmp_path / "missing"))
    assert not usable_workspace_root(str(folder / "a-file-not-a-dir"))


# --------------------------------------------------------------------------- #
# B. The runtime pauses, and the chat route answers.
# --------------------------------------------------------------------------- #


def _session(origin="chat"):
    """Defaults to the CHAT origin — presence asserted, like the page does."""
    return SimpleNamespace(id="session_test", origin=origin)


def _tc(name="shell", args=None):
    return SimpleNamespace(name=name, arguments=args or {"command": "echo hi"})


@pytest.fixture
def rt(tmp_path):
    """A real platform's runtime, with the bus's publishes collected."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": type, "payload": payload or {},
                          "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    runtime = AgentRuntime(platform)
    return SimpleNamespace(runtime=runtime, platform=platform,
                           published=published, app=app)


def _agent_def():
    return get_agent_definition(AgentType.BUILDER)


@pytest.mark.asyncio
async def test_an_interactive_run_pauses_and_a_grant_lets_the_call_run(rt):
    allow: set = set()

    async def answer_once():
        # Wait for the request to be filed, then answer it the way the chat
        # page's card does — THROUGH the shared registry.
        for _ in range(200):
            req = next(
                (p for p in rt.published if p["type"] == "approval.requested"),
                None,
            )
            if req:
                assert req["session_id"] == "session_test"
                assert req["payload"]["tool"] == "shell"
                assert rt.platform.approvals.resolve(
                    req["payload"]["approval_id"], "once"
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the pause never published its request")

    answerer = asyncio.create_task(answer_once())
    deny, extra = await rt.runtime._pause_for_approval(
        _session(), _tc(), _agent_def(), allow
    )
    await answerer

    assert deny == ""
    assert "shell" in extra, "a 'once' grant must cover exactly this call"
    assert allow == set(), "'once' must not become a standing grant"
    resolved = [p for p in rt.published if p["type"] == "approval.resolved"]
    assert resolved and resolved[0]["payload"]["decision"] == "once"


@pytest.mark.asyncio
async def test_conversation_widens_the_run_grant_in_place(rt):
    allow: set = set()

    async def answer():
        for _ in range(200):
            req = next(
                (p for p in rt.published if p["type"] == "approval.requested"),
                None,
            )
            if req:
                rt.platform.approvals.resolve(
                    req["payload"]["approval_id"], "conversation"
                )
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(answer())
    deny, extra = await rt.runtime._pause_for_approval(
        _session(), _tc(), _agent_def(), allow
    )
    await task

    assert deny == "" and extra == set()
    assert "shell" in allow, "the run's own grant set must widen IN PLACE"
    # …and the next identical call never pauses: covered by the grant.
    before = len([p for p in rt.published if p["type"] == "approval.requested"])
    deny2, _ = await rt.runtime._pause_for_approval(
        _session(), _tc(), _agent_def(), allow
    )
    after = len([p for p in rt.published if p["type"] == "approval.requested"])
    assert deny2 == "" and after == before


@pytest.mark.asyncio
async def test_a_denial_is_the_users_and_says_so(rt):
    async def answer():
        for _ in range(200):
            req = next(
                (p for p in rt.published if p["type"] == "approval.requested"),
                None,
            )
            if req:
                rt.platform.approvals.resolve(req["payload"]["approval_id"], "deny")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(answer())
    deny, extra = await rt.runtime._pause_for_approval(
        _session(), _tc(), _agent_def(), set()
    )
    await task

    assert "declined" in deny and extra == set()


@pytest.mark.asyncio
async def test_nobody_answering_is_a_bounded_honest_timeout(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.1)
    deny, extra = await rt.runtime._pause_for_approval(
        _session(), _tc(), _agent_def(), set()
    )
    assert "timed out" in deny and "allow_tools" in deny
    resolved = [p for p in rt.published if p["type"] == "approval.resolved"]
    assert resolved and resolved[0]["payload"]["decision"] == "timeout"


@pytest.mark.asyncio
async def test_headless_origins_never_pause(rt, monkeypatch):
    """A 3am schedule parking five minutes per ask punishes the schedule for
    the user being asleep — those lanes keep the instant honest denial whose
    message already names allow_tools as the up-front grant path. And the
    ALLOWLIST is the load-bearing half: the first cut deny-listed known
    headless origins and treated UNATTRIBUTED as watched, which parked every
    origin-less session — headless API callers and the offline suite included
    — for five silent minutes per ask. Presence is asserted, never assumed.

    The timeout is patched tiny so a MUTATED gate fails this test in 0.1s with
    a timeout-deny instead of hanging the suite for four unanswered pauses."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.1)
    for origin in ("schedule:nightly", "autonomy", "comm:telegram", "reflex:x",
                   None, "", "mystery:new-surface"):
        deny, extra = await rt.runtime._pause_for_approval(
            _session(origin=origin), _tc(), _agent_def(), set()
        )
        assert deny == "" and extra == set(), origin
    assert not any(p["type"] == "approval.requested" for p in rt.published)


@pytest.mark.asyncio
async def test_an_allowed_tool_never_pauses(rt):
    """The pause is for the ASK tier only — a policy-allowed read must not
    stop a run to ask about itself."""
    deny, extra = await rt.runtime._pause_for_approval(
        _session(), _tc("read_file", {"path": "x.txt"}), _agent_def(), set()
    )
    assert deny == "" and extra == set()
    assert not any(p["type"] == "approval.requested" for p in rt.published)


def test_the_chat_route_answers_a_session_pause(tmp_path):
    """ONE registry, ONE answer route: a pause filed by the RUNTIME resolves
    through POST /chat/approvals/{id} — the exact button the chat card posts.
    This is what makes the card in chat able to answer a session's ask."""
    client = _client(tmp_path)
    platform = client.app.state.platform

    async def file_and_answer():
        ap_id, fut = platform.approvals.request("shell", {"command": "x"})
        r = client.post(f"/chat/approvals/{ap_id}", json={"decision": "once"})
        assert r.status_code == 200
        return await asyncio.wait_for(fut, timeout=5)

    assert asyncio.run(file_and_answer()) == "once"