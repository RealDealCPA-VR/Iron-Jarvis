"""v1.231.0 (audit Wave 5, task 5A) — ONE execution seam: every door runs in
the project's folder, stamps its origin, and may ask.

Converted from ``tests/_audit_20260904/test_a7_execution_seam.py`` (all four
doors) and the project-only case of ``test_q3_escalation_workspace.py``.

Every automation ends in ``Orchestrator.create_session``, and each door used
to hand it a different subset of project / root / origin. Measured at HEAD:

* AE1/T4 — a schedule, a reflex rule, a goal iteration and a phone
  escalation passed ``project_id`` ONLY, so the session ran in
  ``workspaces/<sid>`` and refused its own project files as "escapes the
  session workspace" (the 2026-08-19 shape), while the Projects door passed
  the root and ran in the folder.
* AE17 (+N2/N3/N4) — reflex, comm and autonomy stamped no origin, and the
  runtime's ask allowlist admitted none of schedule/workflow/reflex/comm/
  autonomy, so none of those sessions could ever pause on an ask-tier tool:
  the phone-approval machinery (v1.200.0) was unreachable from a
  phone-started job; ``routes/comm.py`` also dropped the thread's project.

Now ``create_session`` resolves a project-tagged session's folder through
``fs_policy.root_problem`` — the ONE definition the Projects route and
``usable_workspace_root`` share — records an unusable root on the row, every
door stamps ``<door>:<name>``, and ``ASKING_ORIGINS`` admits the doors whose
asks the bell + phone fan-out delivers (an unanswered ask ends ``needs_you``).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import iron_jarvis.workflows.models  # noqa: F401
import iron_jarvis.motivation.models  # noqa: F401

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.agents.runtime as runtime_mod
import iron_jarvis.core.fs_policy as fs_policy_mod
from iron_jarvis.agents.orchestrator import FOLDER_NOTE_PREFIX, Orchestrator
from iron_jarvis.agents.outcome import OUTCOME_NEEDS_YOU
from iron_jarvis.agents.runtime import ASKING_ORIGINS, PAUSE_TIMEOUT_REASON, AgentRuntime
from iron_jarvis.agents.types import AgentType, get_agent_definition
from iron_jarvis.comm import InboundMessage, InboundPoller, MockChannel, Notifier
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.fs_policy import root_problem, usable_workspace_root
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord, Project, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.projects import _root_problem
from iron_jarvis.goals.engine import GoalEngine
from iron_jarvis.motivation.engine import IntentEngine
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.reflex import ReflexRouter


def _same(a: str, b: Path) -> bool:
    return Path(a).resolve() == b.resolve()


def _project(platform, root: Path | None, name: str = "Acme") -> str:
    with session_scope(platform.engine) as db:
        p = Project(name=name, root=str(root) if root is not None else "")
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id


def _row(engine, sid: str) -> Session:
    with session_scope(engine) as db:
        return db.get(Session, sid)


async def _no_run(self, session_id, definition=None):
    return self.get_session(session_id)


# --------------------------------------------------------------------------- #
# A. THE FOLDER — every door lands where the Projects door does.
# --------------------------------------------------------------------------- #


def test_scheduled_project_task_runs_in_the_project_folder_like_the_projects_door(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    root = tmp_path / "client-folder"
    root.mkdir()
    pid = client.post("/projects", json={"name": "Acme", "root": str(root)}).json()["id"]

    # The Projects door (reference behaviour): the task runs IN the folder.
    direct = client.post(f"/projects/{pid}/task", json={"text": "tidy the folder"}).json()
    direct_sid = direct.get("session_id") or direct.get("id")
    direct_session = client.get(f"/sessions/{direct_sid}").json()["session"]
    assert _same(direct_session["workspace_path"], root)
    assert str(direct_session.get("origin") or "").startswith("project:")

    client.post(
        "/schedules",
        json={
            "name": "nightly-tidy",
            "cron": "0 3 * * *",
            "kind": "task",
            "payload": {"task": "tidy the folder", "project_id": pid},
        },
    )
    fired = client.post("/schedules/nightly-tidy/run").json()
    assert fired["last_status"] == "ok", fired
    sched = client.get(f"/sessions/{fired['last_session_id']}").json()["session"]
    assert sched["project_id"] == pid
    assert sched["origin"] == "schedule:nightly-tidy"
    # Same task, same project, a different door: the deliverables of the
    # nightly run land in the SAME folder the Projects door uses.
    assert _same(sched["workspace_path"], root), sched["workspace_path"]


async def test_reflex_session_rule_runs_in_the_project_folder_and_names_its_origin(tmp_path):
    platform = build_platform(str(tmp_path))
    root = tmp_path / "client-folder"
    root.mkdir()
    pid = _project(platform, root)
    orch = Orchestrator(platform)
    router = ReflexRouter(platform, orch, spawn_bg=lambda sid, coro: coro.close())
    rule = router.store.add(
        name="missing-1099",
        source="comm",
        match="1099",
        action="session",
        project_id=pid,
        task_template="the client sent: {text}",
    )
    res = await router.execute(rule, {"text": "here is the missing 1099", "body": "", "slug": ""})
    assert res["ok"] is True
    session = orch.get_session(res["session_id"])
    assert session.project_id == pid
    assert _same(session.workspace_path, root), session.workspace_path
    assert session.origin == "reflex:missing-1099"


async def test_goal_iteration_runs_in_the_project_folder(platform, orchestrator, monkeypatch, tmp_path):
    root = tmp_path / "client-folder"
    root.mkdir()
    pid = _project(platform, root)
    engine = GoalEngine(platform, orchestrator)
    goal = engine.store.create(
        name="g", contract_text="keep the folder tidy", project_id=pid, budget={"max_tokens": 10_000}
    )
    seen: dict = {}

    async def grab(session):
        seen["workspace"] = session.workspace_path
        seen["origin"] = session.origin
        session.status = SessionStatus.COMPLETED
        session.summary = "tidy"
        session.finished_at = utcnow()
        orchestrator._save(session)
        return session

    monkeypatch.setattr(engine, "_run_session", grab)
    result = await engine.run_iteration(goal.id)
    assert result["ok"] is True
    assert seen["origin"] == f"goal:{goal.id}"
    assert _same(seen["workspace"], root), seen["workspace"]


def test_post_sessions_with_only_a_project_id_runs_in_the_project_folder(tmp_path, monkeypatch):
    """The comm escalation and any API caller pass project_id but NO
    workspace_root. Chat's own tools run in the project root for the same
    project, so the escalated session does too — else every absolute project
    path is refused as 'escapes the session workspace'."""
    monkeypatch.setattr(Orchestrator, "run_session", _no_run)
    client = TestClient(create_app(str(tmp_path / "home")))
    root = tmp_path / "Test Folder"
    root.mkdir()
    (root / "w2.pdf").write_bytes(b"%PDF-1.4 fake")
    pid = client.post("/projects", json={"name": "Tester", "root": str(root)}).json()["id"]

    r = client.post("/sessions", json={"task": "rename these files", "wait": False, "project_id": pid})
    assert r.status_code == 200, r.text
    ws = Path(_row(client.app.state.platform.engine, r.json()["id"]).workspace_path)
    assert ws == root, f"session bound to {ws} instead of the project folder"

    from iron_jarvis.tools.base import safe_path

    # file_search returns ABSOLUTE paths; the file tools accept them here.
    assert safe_path(ws, str(root / "w2.pdf")) == (root / "w2.pdf").resolve()


async def test_an_explicit_workspace_root_still_wins_over_the_project_root(platform, tmp_path):
    proj_root = tmp_path / "project-root"
    proj_root.mkdir()
    other = tmp_path / "somewhere-else"
    other.mkdir()
    pid = _project(platform, proj_root)
    orch = Orchestrator(platform)
    s = await orch.create_session("t", AgentType.BUILDER, project_id=pid, workspace_root=str(other))
    assert _same(s.workspace_path, other)
    assert s.summary == "", "a usable explicit root carries no folder note"


async def test_a_project_without_a_folder_stays_scratch_with_no_note(platform):
    pid = _project(platform, None)
    orch = Orchestrator(platform)
    s = await orch.create_session("t", AgentType.BUILDER, project_id=pid)
    assert Path(s.workspace_path).resolve().parent == platform.config.workspaces_dir.resolve()
    assert s.summary == "", "chat-only by design: no folder, no note"


async def test_an_unusable_project_root_is_recorded_on_the_row_and_kept_under_the_result(
    platform, tmp_path
):
    """A root that is SET but unusable is never silently swapped for a
    scratch dir: the reason rides ``summary`` from creation (the session-row
    twin of the workflow run's v1.225.0 note) and every finalizer keeps it
    under the result."""
    root = tmp_path / "gone-folder"
    root.mkdir()
    pid = _project(platform, root, name="Acme Tax")
    shutil.rmtree(root)  # the drive was unplugged / the folder was moved
    orch = Orchestrator(platform)

    s = await orch.create_session("write the summary", AgentType.BUILDER, project_id=pid)

    assert Path(s.workspace_path).resolve().parent == platform.config.workspaces_dir.resolve()
    assert s.summary.startswith(FOLDER_NOTE_PREFIX), s.summary
    assert "Acme Tax" in s.summary and "does not exist" in s.summary
    assert "scratch workspace" in s.summary

    done = await orch.run_session(s.id)  # the mock writes RESULT.md and finishes
    assert done.status is SessionStatus.COMPLETED
    assert not done.summary.startswith(FOLDER_NOTE_PREFIX), "the result comes first"
    assert done.summary.rstrip().endswith(s.summary), "…and the note is kept under it"
    assert _row(platform.engine, s.id).summary == done.summary, "persisted"


async def test_a_vanished_project_row_is_recorded_too(platform):
    orch = Orchestrator(platform)
    s = await orch.create_session("t", AgentType.BUILDER, project_id="project_ghost")
    assert s.summary.startswith(FOLDER_NOTE_PREFIX)
    assert "no longer exists" in s.summary


async def test_a_failed_run_keeps_the_folder_note_under_the_failure(platform, tmp_path, monkeypatch):
    root = tmp_path / "gone"
    root.mkdir()
    pid = _project(platform, root)
    shutil.rmtree(root)
    orch = Orchestrator(platform)
    s = await orch.create_session("t", AgentType.BUILDER, project_id=pid)

    async def boom(self, session, agent_def):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(AgentRuntime, "run", boom)
    with pytest.raises(RuntimeError):
        await orch.run_session(s.id)
    row = _row(platform.engine, s.id)
    assert row.status is SessionStatus.FAILED
    assert row.summary.startswith("Session failed: RuntimeError")
    assert row.summary.rstrip().endswith(s.summary)


def test_one_definition_behind_every_door(tmp_path, monkeypatch):
    """``fs_policy.root_problem`` is THE door: the Projects route's
    ``_root_problem`` and ``usable_workspace_root`` both delegate to it, so
    the two can never answer differently (the v1.189.0 principle)."""
    folder = tmp_path / "ok"
    folder.mkdir()
    assert root_problem(str(folder)) is None and _root_problem(str(folder)) is None
    assert usable_workspace_root(str(folder))
    assert "does not exist" in (_root_problem(str(tmp_path / "missing")) or "")
    assert _root_problem("relative/x") == root_problem("relative/x") == "folder must be an absolute path"

    monkeypatch.setattr(fs_policy_mod, "root_problem", lambda root: "nope, not today")
    assert _root_problem(str(folder)) == "nope, not today"
    assert usable_workspace_root(str(folder)) is False


# --------------------------------------------------------------------------- #
# B. THE ORIGIN — every door names itself.
# --------------------------------------------------------------------------- #


class _Channel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def is_authorized(self, sender_id) -> bool:
        return True

    def inbound_enabled(self) -> bool:
        return True

    def has_credentials(self) -> bool:
        return True

    def send(self, message: str, **kw) -> dict:
        self.sent.append(message)
        return {"ok": True}


async def test_comm_one_shot_session_names_its_origin(tmp_path):
    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    notifier = Notifier()
    ch = _Channel()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus)
    res = await poller._handle(
        "tg", ch, InboundMessage(sender_id="1", text="summarize my day", update_id=1, reply_to="1")
    )
    session = orch.get_session(res["session_id"])
    assert session.origin == "comm:tg"


class _ChatChannel(MockChannel):
    supports_inbound = True

    def has_credentials(self) -> bool:
        return True

    def poll(self, offset: int = 0, *, timeout: int = 0):
        return [], offset


def test_comm_escalation_route_carries_the_threads_project_and_its_origin(tmp_path, monkeypatch):
    """``POST /comm/threads/{id}/send`` escalation used to drop the thread's
    project (N3) and stamp no origin; it now mirrors ``comm/inbound.py``."""
    monkeypatch.setattr(Orchestrator, "run_session", _no_run)

    async def escalating(platform_, personas, body):
        return {
            "reply": "needs an agent", "provider": "mock", "model": "m",
            "tools_used": [], "escalate": True, "escalate_reason": "real work",
        }

    with TestClient(create_app(str(tmp_path / "home"))) as client:
        app = client.app
        root = tmp_path / "client-folder"
        root.mkdir()
        pid = client.post("/projects", json={"name": "Acme", "root": str(root)}).json()["id"]
        ch = _ChatChannel({"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]})
        app.state.platform.notifier.add_channel("tg", ch)
        app.state.inbound_poller.chat_turn = escalating
        store = app.state.comm_thread_store
        t = store.resolve("tg", "777", "Val")
        with session_scope(app.state.platform.engine) as db:
            rec = db.get(ChatThreadRecord, t.id)
            rec.project_id = pid
            db.add(rec)
            db.commit()

        r = client.post(f"/comm/threads/{t.id}/send", json={"text": "rename the client files"})
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        row = _row(app.state.platform.engine, sid)
        assert row.project_id == pid
        assert row.origin == "comm:tg"
        assert _same(row.workspace_path, root), "…and so it runs in the project folder"


async def test_autonomy_auto_execution_stamps_its_origin(tmp_path):
    platform = build_platform(str(tmp_path))
    engine = IntentEngine(platform, orchestrator=Orchestrator(platform))
    platform.intent = engine
    platform.config.autonomy_enabled = True
    platform.config.autonomy_level = "act_all"
    g = engine.add_goal("draft a note", autonomy_level="act_all")
    engine._deliberator = lambda ctx: {
        "goal_id": g.id, "title": "do it", "rationale": "r",
        "agent_type": "builder", "task": "write NOTES.md", "risk": "low",
    }
    out = await engine.deliberate(wait=True)
    assert out["executed"] is True
    assert engine.orchestrator.get_session(out["session_id"]).origin == "autonomy"


# --------------------------------------------------------------------------- #
# C. THE ASK — an automation session may pause; nobody answering = needs_you.
# --------------------------------------------------------------------------- #


@pytest.fixture
def rt(tmp_path):
    """A real platform's runtime, with the bus's publishes collected."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    published: list[dict[str, Any]] = []
    real_publish = platform.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        published.append({"type": type, "payload": payload or {}, "session_id": session_id})
        return await real_publish(type, payload, session_id=session_id, **kw)

    platform.event_bus.publish = spy
    return SimpleNamespace(
        runtime=AgentRuntime(platform), platform=platform, published=published,
        app=app, client=TestClient(app),
    )


def _session(origin):
    return SimpleNamespace(id="session_test", origin=origin)


def _tc(name="shell", args=None):
    return SimpleNamespace(name=name, arguments=args or {"command": "mv a b"})


def _requests(rt):
    return [p for p in rt.published if p["type"] == "approval.requested"]


WIDENED = ("schedule:nightly", "workflow:weekly-report", "reflex:missing-1099", "comm:telegram", "autonomy")


def test_the_allowlist_names_every_door():
    for origin in WIDENED:
        assert origin.startswith(ASKING_ORIGINS), origin


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", WIDENED)
async def test_an_automation_session_pauses_and_an_unanswered_ask_times_out(rt, monkeypatch, origin):
    """Each widened origin publishes ``approval.requested`` (what the bell and
    the phone read) and, unanswered, resolves ``timeout`` — the receipt the
    v1.227.0 outcome derives ``needs_you`` from — instead of the instant
    headless denial."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.1)
    deny, extra = await rt.runtime._pause_for_approval(
        _session(origin), _tc(), get_agent_definition(AgentType.BUILDER), set()
    )
    assert deny == PAUSE_TIMEOUT_REASON and extra == set()
    reqs = _requests(rt)
    assert len(reqs) == 1 and reqs[0]["session_id"] == "session_test"
    assert reqs[0]["payload"]["tool"] == "shell"
    resolved = [p for p in rt.published if p["type"] == "approval.resolved"]
    assert resolved and resolved[-1]["payload"]["decision"] == "timeout"


@pytest.mark.asyncio
async def test_a_phone_answer_lets_the_automation_call_run(rt):
    async def answer():
        for _ in range(200):
            reqs = _requests(rt)
            if reqs:
                assert rt.platform.approvals.resolve(reqs[0]["payload"]["approval_id"], "once")
                return
            await asyncio.sleep(0.01)
        raise AssertionError("the pause never published its request")

    task = asyncio.create_task(answer())
    deny, extra = await rt.runtime._pause_for_approval(
        _session("comm:telegram"), _tc(), get_agent_definition(AgentType.BUILDER), set()
    )
    await task
    assert deny == "" and "shell" in extra


@pytest.mark.asyncio
async def test_a_bare_platform_with_no_approvals_registry_keeps_the_instant_denial(rt, monkeypatch):
    monkeypatch.setattr(rt.platform, "approvals", None)
    deny, extra = await rt.runtime._pause_for_approval(
        _session("schedule:nightly"), _tc(), get_agent_definition(AgentType.BUILDER), set()
    )
    assert deny == "" and extra == set(), "nothing to ask through: invoke's headless resolver decides"
    assert not _requests(rt)


@pytest.mark.asyncio
async def test_delegate_never_parks_an_automation_session(rt, monkeypatch):
    """The daemon's own resolver grants ``delegate``/``spawn_agent`` with
    nobody present, so a supervisor decomposing a phone job must not sit five
    minutes on a question the app answers itself."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.1)
    for name in ("delegate", "spawn_agent"):
        deny, extra = await rt.runtime._pause_for_approval(
            _session("comm:telegram"), _tc(name, {"task": "x", "agent_type": "builder"}),
            get_agent_definition(AgentType.SUPERVISOR), set(),
        )
        assert deny == "" and extra == set(), name
    assert not _requests(rt)


def _shell_then_done():
    rounds = {"n": 0}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        i = rounds["n"]
        rounds["n"] += 1
        if i == 0:
            resp = LLMResponse(text="", tool_calls=[
                ToolCall(id="c0", name="shell", arguments={"command": "mv a b"}),
            ])
        else:
            resp = LLMResponse(text="Task incomplete — 0 of 1 renamed, pending your approval.")
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.mark.asyncio
async def test_an_unanswered_schedule_ask_ends_as_needs_you_not_a_silent_denial(rt, monkeypatch):
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 0.3)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="schedule:nightly")

    done = await orch.run_session(sess.id)

    assert done.status is SessionStatus.COMPLETED
    assert done.outcome == OUTCOME_NEEDS_YOU
    assert len(_requests(rt)) == 1, "the ask was announced (bell + phone read this)"
    detail = rt.client.get(f"/sessions/{sess.id}").json()["session"]
    assert detail["outcome"] == OUTCOME_NEEDS_YOU


@pytest.mark.asyncio
async def test_a_comm_started_session_asks_back_and_the_bell_lists_it(rt, monkeypatch):
    """A phone message starts a session; its ask-tier call pauses and the ask
    reaches ``GET /chat/approvals/pending`` — the list the NotificationBell
    renders and the v1.200.0 phone fan-out reads — tagged with the session.
    Unanswered, the job ends ``needs_you`` and the phone hears the result."""
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 1.5)
    rt.platform.router.stream = _shell_then_done()
    orch = Orchestrator(rt.platform)
    notifier = Notifier()
    ch = _Channel()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(
        notifier, orch, rt.platform.engine, event_bus=rt.platform.event_bus,
        agent_type=AgentType.BUILDER,
    )
    handled = asyncio.create_task(poller._handle(
        "tg", ch, InboundMessage(sender_id="1", text="rename the files", update_id=1, reply_to="1")
    ))
    listed = None
    for _ in range(100):
        await asyncio.sleep(0.02)
        if not _requests(rt):
            continue
        listed = rt.client.get("/chat/approvals/pending").json()["approvals"]
        if listed:
            break
    assert listed, "the bell never listed the phone-started session's ask"
    sid = _requests(rt)[0]["session_id"]
    mine = next((a for a in listed if a.get("session_id") == sid), None)
    assert mine is not None and mine.get("tool") == "shell", listed

    res = await handled
    row = _row(rt.platform.engine, res["session_id"])
    assert row.origin == "comm:tg" and row.outcome == OUTCOME_NEEDS_YOU
    assert ch.sent, "the phone still hears how it ended"
