"""v1.309.0 wave 1, track B — the mission VIEW and its routes.

Each test pins one user-visible behaviour from the wave-1 findings
(restart-orphans-children-no-mission-recovery, interrupted-mission-no-continue,
mission-dead-end-no-followup, mission-poll-recomputes-whole-ledger,
mission-poll-n-plus-one, mission-list-says-working-while-blocked,
mission-no-route-receipt, mission-no-model-disclosure,
remote-teammates-invisible) and the shared contracts 5, 6 and 7:

* contract 5 — ``GET /sessions/{id}/mission`` session block gains
  ``objective`` / ``interrupted`` / ``continued_as`` / ``route_note``; every
  member gains ``provider`` / ``model``; a member's DENIED ask reads "needed
  your OK to use <tool>" in activity, not as a failure; remote teammates
  appear as members (one per handoff).
* contract 6 — ``GET /missions`` rows gain ``objective`` and ``waiting``;
  ``POST /missions/{id}/retry-failed`` resets the failed worklist items and
  starts a continuation in ONE server-side step.
* contract 7 — ``GET /sessions/interrupted`` lists only the mission ROOT,
  never its delegated children.
* the 2 s poll is cheap: the statement count does not grow with the team,
  no statement reads ``toolinvocation.output``, and the route answers a weak
  ETag (bodiless 304 on a repeat).

Driven through the REAL app factory (``create_app`` + ``TestClient``, whose
lifespan runs the real boot reconcile), the real orchestrator, registry and
worklist store. The LLM is scripted. Rows the platform itself writes (a
Session, an AgentRun, a ToolInvocation, an EventRecord) are sometimes seeded
directly when the behaviour under test is the VIEW of an already-written
ledger — that is exactly what ``mission_view`` reads.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event

from iron_jarvis.agents.mission import mission_view
from iron_jarvis.agents.outcome import session_result
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id, utcnow
from iron_jarvis.core.models import (
    AgentRun,
    AgentState,
    AgentType,
    EventRecord,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

MISSION = "job:mission"
MEMBER = "job:mission-member"


# --------------------------------------------------------------------------- #
# scripted models
# --------------------------------------------------------------------------- #


class _Quick(MockLLMAdapter):
    """Every run answers at once with a short report — no tools."""

    async def complete(self, *, system, messages, tools, **kw):
        return LLMResponse(text="# Report\n\nall done", finish_reason="stop")


class _Writers(MockLLMAdapter):
    """Supervisor hands ``part-1.md`` then ``part-2.md`` to two builders,
    then reports. Each builder writes the file its task names."""

    async def complete(self, *, system, messages, tools, **kw):
        tool_msgs = [m for m in messages if m.role == "tool"]
        if "As the Supervisor" in system:
            if len(tool_msgs) == 0:
                return _delegate("d1", "write part-1.md with the findings")
            if len(tool_msgs) == 1:
                return _delegate("d2", "write part-2.md with the pricing")
            return LLMResponse(text="# Report\n\nboth parts written", finish_reason="stop")
        if tool_msgs:
            return LLMResponse(text="wrote it", finish_reason="stop")
        first = str(getattr(messages[0], "content", "") or "")
        found = re.search(r"part-\d\.md", first)
        name = found.group(0) if found else "part-x.md"
        return LLMResponse(
            tool_calls=[ToolCall(id="w1", name="write_file",
                                 arguments={"path": name, "content": f"# {name}\n"})],
            finish_reason="tool_use",
        )


def _delegate(call_id: str, task: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=call_id, name="delegate",
                             arguments={"agent_type": "builder", "task": task})],
        finish_reason="tool_use",
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _app(tmp_path, adapter=_Quick):
    app = create_app(str(tmp_path))
    app.state.platform.providers.register("mock", lambda model=None: adapter())
    return app


def _wait(c: TestClient, sid: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = c.get(f"/sessions/{sid}").json()["session"]
        if row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"session {sid} never finished")


def _seed_mission(
    engine,
    *,
    task: str = "write the quarterly report",
    status: SessionStatus = SessionStatus.COMPLETED,
    options: dict | None = None,
    provider: str = "mock",
    model: str = "mock-1",
    members: list[dict] | None = None,
) -> tuple[str, str, list[str]]:
    """A mission root (+ its coordinator run) and its members, each linked by
    ``AgentRun.parent_id`` exactly as ``delegate`` links them. Returns
    ``(root_id, root_run_id, member_ids)``."""
    with session_scope(engine) as db:
        root = Session(
            task=task, agent_type=AgentType.SUPERVISOR, status=status,
            origin=MISSION, provider=provider, model=model,
            options_json=json.dumps(options if options is not None else {"deliverable": True}),
        )
        db.add(root)
        db.commit()
        root_id = root.id
        root_run = AgentRun(
            session_id=root_id, agent_type=AgentType.SUPERVISOR, provider=provider,
            model=model,
            state=AgentState.COMPLETED if status is SessionStatus.COMPLETED else AgentState.RUNNING,
        )
        db.add(root_run)
        db.commit()
        root_run_id = root_run.id
        ids: list[str] = []
        for spec in members or []:
            m = Session(
                task=spec.get("task", "a part"),
                agent_type=spec.get("agent_type", AgentType.BUILDER),
                agent_name=spec.get("agent_name", "builder"),
                status=spec.get("status", SessionStatus.COMPLETED),
                origin=spec.get("origin", MEMBER),
                provider=spec.get("provider", "mock"),
                model=spec.get("model", "mock-1"),
                summary=spec.get("summary", "did my part"),
            )
            db.add(m)
            db.commit()
            ids.append(m.id)
            db.add(AgentRun(
                session_id=m.id, parent_id=root_run_id,
                agent_type=spec.get("agent_type", AgentType.BUILDER),
                provider=m.provider, model=m.model,
                state=spec.get("run_state", AgentState.COMPLETED), steps=1,
            ))
            db.commit()
    return root_id, root_run_id, ids


def _event(engine, etype, session_id: str, payload: dict, at=None) -> None:
    with session_scope(engine) as db:
        db.add(EventRecord(
            id=new_id("evt"), type=str(getattr(etype, "value", etype)), session_id=session_id,
            payload_json=json.dumps(payload), created_at=at or utcnow(),
        ))
        db.commit()


def _invocation(engine, session_id: str, tool: str, args: dict, *, ok: bool,
                verdict=PermissionMode.ALLOW, output: str = "", at=None) -> str:
    with session_scope(engine) as db:
        row = ToolInvocation(
            session_id=session_id, agent_run_id="r", tool=tool,
            args_json=json.dumps(args), verdict=verdict, ok=ok, output=output,
            reversibility="reversible", created_at=at or utcnow(),
        )
        db.add(row)
        db.commit()
        return row.id


def _file_ask(platform, session_id: str, tool: str = "shell") -> str:
    """File a REAL pending ask in the platform's one approvals registry, the
    way the runtime's pause does (``request`` needs a running loop)."""

    async def _go() -> str:
        approval_id, _fut = platform.approvals.request(tool, {}, session_id=session_id)
        return approval_id

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_go())
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# contract 7 — a restart offers Continue on the mission ROOT only
# --------------------------------------------------------------------------- #


def _seed_restart(app) -> dict[str, str]:
    """Rows left ACTIVE by a crash/update mid-mission, written BEFORE the app
    boots so the lifespan's real ``reconcile_interrupted_sessions`` tags them.

    * ``root`` — the mission coordinator;
    * ``kid_origin`` — a member stamped ``job:mission-member`` (contract 1)
      that died BEFORE its AgentRun row was written (no parent link at all);
    * ``kid_linked`` — a pre-wave member: origin None, known only by its
      ``AgentRun.parent_id`` link;
    * ``solo`` — an ordinary standalone job (the CONTROL: still offered).
    """
    engine = app.state.platform.engine
    root, root_run, (kid_linked,) = _seed_mission(
        engine, status=SessionStatus.ACTIVE,
        members=[{"status": SessionStatus.ACTIVE, "origin": None,
                  "run_state": AgentState.RUNNING}],
    )
    with session_scope(engine) as db:
        kid_origin = Session(task="the other part", agent_type=AgentType.BUILDER,
                             status=SessionStatus.ACTIVE, origin=MEMBER)
        solo = Session(task="rename my files", agent_type=AgentType.BUILDER,
                       status=SessionStatus.ACTIVE, origin=None)
        db.add(kid_origin)
        db.add(solo)
        db.commit()
        out = {"root": root, "kid_linked": kid_linked,
               "kid_origin": kid_origin.id, "solo": solo.id}
    return out


def test_restart_offers_continue_on_the_mission_root_only(tmp_path):
    """restart-orphans-children: the bell/Overview listed N+1 'jobs stopped by
    a restart', each child continuable as a standalone builder run."""
    app = _app(tmp_path)
    ids = _seed_restart(app)
    with TestClient(app) as c:
        listed = {r["id"] for r in c.get("/sessions/interrupted").json()["sessions"]}
    # anti-vacuity: the boot reconcile really did run over all four rows
    with session_scope(app.state.platform.engine) as db:
        for key in ("root", "kid_origin", "kid_linked", "solo"):
            assert db.get(Session, ids[key]).status is SessionStatus.FAILED, key
    assert ids["root"] in listed
    assert ids["solo"] in listed, "a standalone job must still be offered Continue"
    assert ids["kid_origin"] not in listed, "a job:mission-member child was offered Continue"
    assert ids["kid_linked"] not in listed, "a delegated child (AgentRun.parent_id) was offered Continue"


def test_mission_view_says_it_was_interrupted(tmp_path):
    """interrupted-mission-no-continue: the screen needs ``interrupted`` to
    offer Continue. A mission that finished normally is NOT interrupted."""
    app = _app(tmp_path)
    ids = _seed_restart(app)
    finished, _, _ = _seed_mission(app.state.platform.engine, task="an older one")
    with TestClient(app) as c:
        cut = c.get(f"/sessions/{ids['root']}/mission").json()
        fine = c.get(f"/sessions/{finished}/mission").json()
    assert cut["session"].get("interrupted") is True, cut["session"]
    assert fine["session"].get("interrupted") is False, fine["session"]


# --------------------------------------------------------------------------- #
# contract 5 — objective / continued_as
# --------------------------------------------------------------------------- #


def test_the_objective_shown_is_the_users_words_not_the_recap(tmp_path):
    """A continuation's ``Session.task`` is the model-facing recap; the screen
    and the Recent list must show ``options.objective`` (Track A writes it)
    and fall back to ``task`` when there is none."""
    app = _app(tmp_path)
    engine = app.state.platform.engine
    recap = (
        "make it shorter\n\n[Continuing an earlier session. Original task: "
        "'write the quarterly report'. Prior result: done The earlier workspace "
        "files are available in your workspace.]"
    )
    follow, _, _ = _seed_mission(
        engine, task=recap, options={"deliverable": True, "objective": "make it shorter"})
    plain, _, _ = _seed_mission(engine, task="write the quarterly report")
    with TestClient(app) as c:
        rows = {r["id"]: r for r in c.get("/missions").json()["missions"]}
        view = c.get(f"/sessions/{follow}/mission").json()
        plain_view = c.get(f"/sessions/{plain}/mission").json()
    assert rows[follow]["objective"] == "make it shorter", rows[follow]["objective"]
    assert rows[plain]["objective"] == "write the quarterly report"
    assert view["session"].get("objective") == "make it shorter", view["session"]
    # the recap stays model-facing, untouched
    assert view["session"]["task"] == recap
    assert plain_view["session"].get("objective") == "write the quarterly report"


def test_a_follow_up_through_continue_is_listed_by_the_follow_up(tmp_path):
    """mission-dead-end-no-followup, end to end: continuing a mission lists the
    new round under the USER's follow-up, and the old mission points at it.
    Needs Track A's contract 3 (options.objective written by continue_session)
    as well as this track's read of it."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "write the quarterly report"}).json()["id"]
        _wait(c, sid)
        new = c.post(f"/sessions/{sid}/continue", json={"message": "add a pricing section"})
        assert new.status_code == 200, new.text
        nid = new.json()["id"]
        _wait(c, nid)
        rows = {r["id"]: r for r in c.get("/missions").json()["missions"]}
        view = c.get(f"/sessions/{nid}/mission").json()
        old = c.get(f"/sessions/{sid}/mission").json()
    assert rows[nid]["objective"] == "add a pricing section", rows[nid]["objective"]
    assert view["session"].get("objective") == "add a pricing section"
    assert old["session"].get("continued_as") == nid, old["session"]


def test_continued_as_names_the_newest_continuation(tmp_path):
    """interrupted-mission-no-continue: the old screen must link to its
    continuation ('Continued in -> open'). Two follow-ups: the NEWEST wins; the
    continuation itself is continued by nobody (null, not its parent's id)."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "write the quarterly report"}).json()["id"]
        _wait(c, sid)
        before = c.get(f"/sessions/{sid}/mission").json()["session"]
        first = c.post(f"/sessions/{sid}/continue", json={"message": "shorter"}).json()["id"]
        _wait(c, first)
        after_one = c.get(f"/sessions/{sid}/mission").json()["session"]
        second = c.post(f"/sessions/{sid}/continue", json={"message": "add pricing"}).json()["id"]
        _wait(c, second)
        after_two = c.get(f"/sessions/{sid}/mission").json()["session"]
        own = c.get(f"/sessions/{second}/mission").json()["session"]
    assert "continued_as" in before and before["continued_as"] is None, before
    assert after_one["continued_as"] == first
    assert after_two["continued_as"] == second
    assert own["continued_as"] is None


# --------------------------------------------------------------------------- #
# contract 6 — the Recent list says when a mission is waiting on the user
# --------------------------------------------------------------------------- #


def test_missions_list_says_waiting_when_parked_on_an_ask(tmp_path):
    """mission-list-says-working-while-blocked: a mission whose coordinator OR
    any member is parked on an ask is ``waiting``; an idle running mission is
    not, and an ask filed under an unrelated session does not count."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        p = app.state.platform
        # seeded AFTER boot so the reconcile leaves them ACTIVE
        root_ask, _, _ = _seed_mission(p.engine, task="root asks", status=SessionStatus.ACTIVE)
        member_ask, _, (kid,) = _seed_mission(
            p.engine, task="member asks", status=SessionStatus.ACTIVE,
            members=[{"status": SessionStatus.ACTIVE, "run_state": AgentState.WAITING}],
        )
        idle, _, (idle_kid,) = _seed_mission(
            p.engine, task="idle", status=SessionStatus.ACTIVE,
            members=[{"status": SessionStatus.ACTIVE, "run_state": AgentState.RUNNING}],
        )
        with session_scope(p.engine) as db:
            stranger = Session(task="unrelated", status=SessionStatus.ACTIVE)
            db.add(stranger)
            db.commit()
            stranger_id = stranger.id
        _file_ask(p, root_ask)
        _file_ask(p, kid, "write_file")
        _file_ask(p, stranger_id)
        rows = {r["id"]: r for r in c.get("/missions").json()["missions"]}
    assert rows[root_ask].get("waiting") is True, rows[root_ask]
    assert rows[member_ask].get("waiting") is True, rows[member_ask]
    assert rows[idle].get("waiting") is False, rows[idle]


# --------------------------------------------------------------------------- #
# contract 5 — which model answered, and whether a failover touched it
# --------------------------------------------------------------------------- #


def test_every_member_discloses_its_provider_and_model(tmp_path):
    """mission-no-model-disclosure: a card must be able to say which model
    did that part — per member, not only for the coordinator."""
    p = build_platform(str(tmp_path / "home"))
    root, _, (kid,) = _seed_mission(
        p.engine, provider="claude-cli", model="claude-opus-4-8",
        members=[{"provider": "codex-cli", "model": "gpt-5.5"}],
    )
    view = mission_view(p, root)
    assert view["session"]["provider"] == "claude-cli"
    assert view["session"]["model"] == "claude-opus-4-8"
    m = view["members"][0]
    assert m["session_id"] == kid
    assert m.get("provider") == "codex-cli", m
    assert m.get("model") == "gpt-5.5", m


def test_route_note_names_a_failover_on_a_member(tmp_path):
    """mission-no-route-receipt: a teammate's model failed over — the screen
    gets ONE plain sentence naming both providers. A mission with only an
    ordinary routed event says nothing (``""``)."""
    p = build_platform(str(tmp_path / "home"))
    root, _, (kid,) = _seed_mission(p.engine, members=[{}])
    _event(p.engine, EventType.PROVIDER_FAILOVER, kid,
           {"from": "claude-cli", "to": "codex-cli", "reason": "http 500"})
    quiet, _, (quiet_kid,) = _seed_mission(p.engine, task="calm", members=[{}])
    _event(p.engine, EventType.PROVIDER_ROUTED, quiet_kid,
           {"provider": "claude-cli", "model": "claude-opus-4-8"})

    note = mission_view(p, root)["session"].get("route_note")
    calm = mission_view(p, quiet)["session"].get("route_note")
    assert isinstance(note, str) and note, f"no route note: {note!r}"
    assert "claude-cli" in note and "codex-cli" in note, note
    assert "\n" not in note, "one plain sentence, not a blob"
    assert calm == "", calm


def test_route_note_names_a_downgrade_on_the_coordinator(tmp_path):
    """The router's ``provider.downgraded`` (the requested provider could not
    be used) on the coordinator is also a route note naming that provider."""
    p = build_platform(str(tmp_path / "home"))
    root, _, _ = _seed_mission(p.engine)
    _event(p.engine, EventType.PROVIDER_DOWNGRADED, root,
           {"requested": "local-fleet", "used": "none", "reason": "unreachable"})
    note = mission_view(p, root)["session"].get("route_note")
    assert isinstance(note, str) and "local-fleet" in note, note


# --------------------------------------------------------------------------- #
# contract 5 — a denied ask is not a failure
# --------------------------------------------------------------------------- #


def test_a_members_denied_ask_reads_as_needing_your_ok(tmp_path):
    """A member's ask that nobody could answer is recorded ``verdict=ask,
    ok=False``; the activity log must say "needed your OK to use write file",
    never "could not write" (that reads as the agent failing). A genuine tool
    failure (verdict allow) keeps its failure words — the control.

    v1.309.0 review: the refusal is seeded as the registry WRITES it — the
    ledger row AND its ``tool.denied`` event (``kind`` "permission denied",
    ``invocation_id`` = the row). The view reads WHO refused from that event:
    the breaker / roster gate / low trust share ``verdict=ask`` but asked no
    one (``test_a_refusal_nobody_was_asked_about_never_reads_as_an_ask``)."""
    p = build_platform(str(tmp_path / "home"))
    root, _, (kid,) = _seed_mission(p.engine, members=[{"agent_name": "builder"}])
    t0 = utcnow()
    _invocation(p.engine, kid, "edit_file", {"path": "C:/work/notes.md"}, ok=False,
                output="no such text in notes.md", at=t0)
    reason = "write_file needs approval and nothing here could ask — grant it ..."
    inv = _invocation(
        p.engine, kid, "write_file", {"path": "C:/work/report.md"}, ok=False,
        verdict=PermissionMode.ASK, at=t0 + timedelta(seconds=1), output=reason,
    )
    _event(p.engine, EventType.TOOL_DENIED, kid,
           {"tool": "write_file", "mode": "ask", "reason": reason,
            "invocation_id": inv, "kind": "permission denied"},
           at=t0 + timedelta(seconds=1))
    view = mission_view(p, root)
    texts = [a["text"] for a in view["activity"]]
    assert "Builder needed your OK to use write file" in texts, texts
    assert not any("could not write" in t for t in texts), texts
    assert "Builder could not edit notes.md" in texts, texts  # control
    member = view["members"][0]
    assert member["activity"] == "needed your OK to use write file", member["activity"]


# --------------------------------------------------------------------------- #
# contract 5 — remote teammates: one card per handoff
# --------------------------------------------------------------------------- #


def test_two_handoffs_to_one_remote_are_two_teammates(tmp_path):
    """remote-teammates-invisible: remote handoffs (delegation events with
    ``agent: remote:*`` and no child session) are members — keyed per HANDOFF,
    not per agent name, so two jobs given to one remote are two cards, each
    with its own task and outcome, both counted in progress."""
    p = build_platform(str(tmp_path / "home"))
    root, root_run, _ = _seed_mission(p.engine)
    t0 = utcnow()
    for i, (task, ok) in enumerate((("summarise the filings", True), ("draft the letter", False))):
        base = t0 + timedelta(seconds=10 * i)
        _event(p.engine, EventType.DELEGATION_STARTED, root,
               {"parent_run_id": root_run, "child_session_id": "", "agent": "remote:hermes",
                "task": task}, at=base)
        _event(p.engine, EventType.DELEGATION_COMPLETED, root,
               {"parent_run_id": root_run, "child_run_id": None, "child_session_id": "",
                "agent": "remote:hermes", "ok": ok,
                "result": "done" if ok else "the endpoint said no"},
               at=base + timedelta(seconds=1))
    view = mission_view(p, root)
    remotes = [m for m in view["members"] if m["kind"] == "remote"]
    assert len(remotes) == 2, view["members"]
    by_task = {m["task"]: m for m in remotes}
    assert by_task["summarise the filings"]["status"] == "done"
    assert by_task["draft the letter"]["status"] == "failed"
    assert all(m["name"] == "hermes" for m in remotes)
    assert view["progress"] == {"done": 1, "total": 2}


# --------------------------------------------------------------------------- #
# contract 6 — retry the failed worklist items in one server-side step
# --------------------------------------------------------------------------- #


def _board_with(app, sid: str, statuses: dict[str, str]) -> str:
    store = app.state.platform.worklist
    board = store.root_session_for(sid)
    store.add(board, [(k, k) for k in statuses])
    for key, status in statuses.items():
        if status != "pending":
            store.finish(board, key, status=status)
    return board


def test_retry_failed_resets_the_failed_items_and_starts_a_continuation(tmp_path):
    """mission-dead-end-no-followup: 'Retry the N failed items' must not be a
    two-request dance that can leave items reset with no run. ONE POST resets
    the mission's failed items and answers the continuation's row."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
        _wait(c, sid)
        board = _board_with(app, sid, {"a.pdf": "done", "b.pdf": "failed", "c.pdf": "failed"})
        res = c.post(f"/missions/{sid}/retry-failed")
        assert res.status_code in (200, 201), res.text
        row = res.json()
        assert row["id"] != sid
        assert row["origin"] == MISSION, "the retry must still be a mission"
        _wait(c, row["id"])
        items = {i.key: i.status for i in app.state.platform.worklist.items(board)}
        old = c.get(f"/sessions/{sid}/mission").json()["session"]
    assert items["a.pdf"] == "done", "a done item is never touched"
    assert items["b.pdf"] != "failed" and items["c.pdf"] != "failed", items
    assert old.get("continued_as") == row["id"], old


def test_retry_of_a_retry_names_the_original_objective_once(tmp_path):
    """Review note: the objective wrapped itself on every retry ("Retry the 1
    failed item: Retry the 2 failed items: file the 1099s"). A retry of a
    retry names the user's own objective once, with the CURRENT count."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
        _wait(c, sid)
        board = _board_with(app, sid, {"a.pdf": "done", "b.pdf": "failed", "c.pdf": "failed"})
        first = c.post(f"/missions/{sid}/retry-failed").json()
        _wait(c, first["id"])
        store = app.state.platform.worklist
        for key in ("b.pdf", "c.pdf"):
            store.finish(board, key, status="done" if key == "b.pdf" else "failed")
        res = c.post(f"/missions/{first['id']}/retry-failed")
        assert res.status_code in (200, 201), res.text
        second = res.json()
        _wait(c, second["id"])
        view = c.get(f"/sessions/{second['id']}/mission").json()
    got = view["session"].get("objective")
    assert got == "Retry the 1 failed item: file the 1099s", got


def test_retry_failed_refuses_unknown_and_nothing_failed(tmp_path):
    """404 for an unknown mission; 409 when nothing failed (all done, or no
    worklist at all) — and a refusal starts NO session."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        clean = c.post("/missions", json={"objective": "file the W-2s"}).json()["id"]
        bare = c.post("/missions", json={"objective": "write a memo"}).json()["id"]
        _wait(c, clean)
        _wait(c, bare)
        _board_with(app, clean, {"a.pdf": "done", "b.pdf": "done"})
        before = len(c.get("/missions").json()["missions"])
        unknown = c.post("/missions/nope/retry-failed")
        nothing = c.post(f"/missions/{clean}/retry-failed")
        no_board = c.post(f"/missions/{bare}/retry-failed")
        after = len(c.get("/missions").json()["missions"])
    assert unknown.status_code == 404, unknown.text
    assert nothing.status_code == 409, nothing.text
    assert no_board.status_code == 409, no_board.text
    assert after == before, "a refused retry must not start a mission"


# --------------------------------------------------------------------------- #
# the 2 s poll is cheap
# --------------------------------------------------------------------------- #

_OUTPUT = "x" * 4000  # ToolInvocation.output is capped at 4,000 chars
_CALLS_PER_MEMBER = 50


def _seed_busy_mission(engine, n_members: int) -> str:
    root, _, kids = _seed_mission(
        engine, task=f"busy mission with {n_members}",
        members=[{"agent_name": "builder"} for _ in range(n_members)],
    )
    t0 = utcnow()
    with session_scope(engine) as db:
        for k, kid in enumerate(kids):
            for i in range(_CALLS_PER_MEMBER):
                db.add(ToolInvocation(
                    session_id=kid, agent_run_id="r", tool="read_file",
                    args_json=json.dumps({"path": f"C:/work/file-{i}.txt"}),
                    ok=True, output=_OUTPUT, reversibility="readonly",
                    created_at=t0 + timedelta(milliseconds=k * 1000 + i),
                ))
        db.commit()
    return root


class _Statements:
    """Every SQL statement the engine executes inside the ``with`` block."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.seen: list[str] = []

    def _hook(self, conn, cursor, statement, parameters, context, executemany):
        self.seen.append(statement)

    def __enter__(self) -> "_Statements":
        sa_event.listen(self.engine, "before_cursor_execute", self._hook)
        return self

    def __exit__(self, *exc) -> None:
        sa_event.remove(self.engine, "before_cursor_execute", self._hook)


def _measure(p, root: str) -> tuple[list[str], dict[str, Any]]:
    with _Statements(p.engine) as st:
        view = mission_view(p, root)
    return st.seen, view


def test_mission_poll_statement_count_does_not_grow_with_the_team(tmp_path):
    """mission-poll-n-plus-one: one poll ran the tree walk's ``db.get`` per
    member plus a full ``session_result`` (≈5 statements) per member. The
    count must be the SAME for a 2-member and an 8-member team (a ratio pin,
    never a wall-clock one)."""
    p = build_platform(str(tmp_path / "home"))
    warm = _seed_busy_mission(p.engine, 1)
    small = _seed_busy_mission(p.engine, 2)
    large = _seed_busy_mission(p.engine, 8)
    mission_view(p, warm)  # first-call imports / lazy setup out of the count
    n_small, v_small = _measure(p, small)
    n_large, v_large = _measure(p, large)
    assert len(v_small["members"]) == 2 and len(v_large["members"]) == 8
    assert len(n_large) == len(n_small), (
        f"{len(n_small)} statements for 2 members, {len(n_large)} for 8"
    )


def test_mission_poll_never_reads_tool_outputs(tmp_path):
    """mission-poll-recomputes-whole-ledger: the view uses tool/args/ok/
    session/time/undo_of only; a poll must not pull ``ToolInvocation.output``
    (up to 4,000 chars a row, a few hundred rows a mission, every 2 s)."""
    p = build_platform(str(tmp_path / "home"))
    root = _seed_busy_mission(p.engine, 4)
    seen, view = _measure(p, root)
    assert len(view["members"]) == 4 and view["activity"], "anti-vacuity: the view was built"
    readers = [s for s in seen if re.search(r"toolinvocation\.output\b", s, re.IGNORECASE)]
    assert not readers, "a poll read tool outputs:\n" + "\n---\n".join(readers[:3])


def test_mission_route_answers_an_etag_and_a_304(tmp_path):
    """An unchanged tick is a bodiless 304 (the ``/sessions`` pattern,
    ``_etagged_json``); the 200 body is the same view as before. An unknown id
    is still ``found: false``."""
    app = _app(tmp_path)
    root, _, _ = _seed_mission(app.state.platform.engine, members=[{}])
    with TestClient(app) as c:
        first = c.get(f"/sessions/{root}/mission")
        tag = first.headers.get("etag")
        again = c.get(f"/sessions/{root}/mission", headers={"If-None-Match": tag or "none"})
        missing = c.get("/sessions/nope/mission")
    assert first.status_code == 200 and first.json()["found"] is True
    assert tag, "the mission view must carry an ETag"
    assert again.status_code == 304, again.status_code
    assert again.content == b""
    assert missing.json() == {"found": False, "session_id": "nope"}


def test_team_documents_match_the_ledger_after_the_rewrite(tmp_path):
    """GOLDEN regression guard (passes today, must stay green): whatever
    replaces the per-member ``session_result`` keeps its exact documents
    semantics — each member's ``files`` is its ``session_result`` documents,
    and the deliverable lists the coordinator's files first, then each
    member's in team order, without duplicates. Driven by a real team run
    whose builders write files through the real registry + undo journal."""
    app = _app(tmp_path, _Writers)
    with TestClient(app) as c:
        res = c.post("/missions", json={
            "objective": "write the two-part report", "allow_tools": ["write_file"]})
        sid = res.json()["id"]
        done = _wait(c, sid)
        assert done["status"] == "completed", done
        view = c.get(f"/sessions/{sid}/mission").json()
    engine = app.state.platform.engine
    expected: list[str] = []
    for m in view["members"]:
        files = [str(f) for f in session_result(engine, m["session_id"]).get("documents") or []]
        assert m["files"] == files[:20], (m["name"], m["files"], files)
        for f in files:
            if f not in expected:
                expected.append(f)
    for f in session_result(engine, sid).get("documents") or []:
        if str(f) not in expected:
            expected.insert(0, str(f))
    names = sorted(f.replace("\\", "/").rsplit("/", 1)[-1] for f in view["deliverable"]["documents"])
    assert names == ["part-1.md", "part-2.md"], view["deliverable"]["documents"]
    assert view["deliverable"]["documents"] == expected[:30]


# --------------------------------------------------------------------------- #
# v1.309.0 review round
# --------------------------------------------------------------------------- #


def test_a_refusal_nobody_was_asked_about_never_reads_as_an_ask(tmp_path):
    """HONEST COPY (review BLOCKING). ``registry.invoke`` records EVERY
    caller-supplied refusal as ``verdict=ask, ok=False`` — the roster gate
    ("not armed") and the runtime's repeated-failure breaker ("refused") as
    well as a real ask. Driven through the REAL registry: neither may read
    "needed your OK" (``read_file`` is an allow-tier tool; nobody was asked).
    A real headless ask through the same registry still does (anti-vacuity),
    and an ``ask`` call the user APPROVED whose tool then failed (no
    ``tool.denied`` event) reads as the failure it is."""
    from iron_jarvis.tools.base import ToolContext

    p = build_platform(str(tmp_path / "home"))
    root, _, (kid,) = _seed_mission(p.engine, members=[{"agent_name": "builder"}])
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(workspace=ws, session_id=kid, agent_run_id="r", config=p.config,
                      event_bus=p.event_bus, engine=p.engine)

    async def _go():
        armed = await p.registry.invoke(
            "write_file", {"path": "a.md", "content": "x"}, ctx, p.permissions,
            allowed_names={"read_file"})
        broke = await p.registry.invoke(
            "read_file", {"path": "missing.md"}, ctx, p.permissions,
            deny_reason="repeated-failure breaker — `read_file` has now failed 3 times",
            deny_label="refused")
        asked = await p.registry.invoke(
            "read_file", {"path": "x.md"}, ctx, p.permissions,
            deny_reason="the user declined this call when asked",
            deny_label="permission denied")
        return armed, broke, asked

    loop = asyncio.new_event_loop()
    try:
        armed, broke, asked = loop.run_until_complete(_go())
    finally:
        loop.close()
    assert not (armed.ok or broke.ok or asked.ok)
    with session_scope(p.engine) as db:
        verdicts = {str(getattr(r.verdict, "value", r.verdict)) for r in db.exec(
            __import__("sqlmodel").select(ToolInvocation))}
    assert verdicts == {"ask"}, "the premise: all three share the ask verdict"
    _invocation(p.engine, kid, "excel_edit", {"path": "C:/work/book.xlsx"}, ok=False,
                verdict=PermissionMode.ASK, output="the workbook is locked")
    view = mission_view(p, root)
    lines = [(a["text"], a["tone"]) for a in view["activity"]]
    texts = [t for t, _ in lines]
    asks = [t for t, tone in lines if "needed your OK" in t]
    assert asks == ["Builder needed your OK to use read file"], lines
    assert "Builder tried write file, a tool it was not given — nothing ran" in texts, texts
    assert "Builder was stopped from repeating a read file call that kept failing" in texts, texts
    assert "Builder could not update the workbook book.xlsx" in texts, texts
    assert all(tone != "ask" for t, tone in lines if "needed your OK" not in t), lines


def test_retry_failed_runs_where_the_first_run_left_its_files(tmp_path):
    """Review BLOCKING: a plain rerun of a scratch-workspace mission got a
    FRESH, empty folder, so the retry could not see a file the first run made.
    The retry must run in the mission's own folder — proven by the retry's
    model READING a file that exists only there (the first run could not).
    Still the same task (the worklist board is keyed by it)."""

    class _Reader(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            tool_msgs = [m for m in messages if m.role == "tool"]
            if not tool_msgs:
                return LLMResponse(
                    tool_calls=[ToolCall(id="r1", name="read_file",
                                         arguments={"path": "first-run.md"})],
                    finish_reason="tool_use",
                )
            return LLMResponse(text=f"saw: {tool_msgs[-1].content}", finish_reason="stop")

    app = _app(tmp_path, _Reader)
    engine = app.state.platform.engine
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
        _wait(c, sid)
        with session_scope(engine) as db:
            first = db.get(Session, sid)
            ws, task = first.workspace_path, first.task
        from pathlib import Path

        (Path(ws) / "first-run.md").write_text("PRODUCED-BY-RUN-ONE", encoding="utf-8")
        _board_with(app, sid, {"a.pdf": "done", "b.pdf": "failed"})
        res = c.post(f"/missions/{sid}/retry-failed")
        assert res.status_code in (200, 201), res.text
        rid = res.json()["id"]
        done = _wait(c, rid)
        with session_scope(engine) as db:
            retry = db.get(Session, rid)
            retry_ws, retry_task = retry.workspace_path, retry.task
    assert retry_ws == ws, (retry_ws, ws)
    assert retry_task == task, "same task = same worklist board"
    assert "PRODUCED-BY-RUN-ONE" in (done.get("summary") or ""), done.get("summary")
    assert not (Path(ws).parent / rid).exists(), "the unused scratch folder is removed"


def test_a_resume_keeps_the_missions_own_objective(tmp_path):
    """Review MINOR: Continue after a restart (the bell, the mission screen)
    posts the APP's instruction; with ``resume: true`` the new round is listed
    by the mission's objective, not by that instruction. Control: a plain
    follow-up still lists the user's own words (contract 3)."""
    app = _app(tmp_path)
    with TestClient(app) as c:
        sid = c.post("/missions", json={"objective": "file the 1099s"}).json()["id"]
        _wait(c, sid)
        res = c.post(f"/sessions/{sid}/continue", json={
            "message": "Continue where you left off — Iron Jarvis restarted.",
            "resume": True, "wait": True})
        assert res.status_code == 200, res.text
        rid = res.json()["id"]
        follow = c.post(f"/sessions/{rid}/continue", json={"message": "add a cover note"}).json()["id"]
        rows = {r["id"]: r for r in c.get("/missions").json()["missions"]}
        view = c.get(f"/sessions/{rid}/mission").json()["session"]
        old = c.get(f"/sessions/{sid}/mission").json()["session"]
    assert rows[rid]["objective"] == "file the 1099s", rows[rid]
    assert view["objective"] == "file the 1099s", view
    assert old["continued_as"] == rid
    assert rows[follow]["objective"] == "add a cover note", rows[follow]


def test_continued_as_only_considers_later_missions(tmp_path):
    """Review MINOR: the 2 s lookup is bounded to mission rows created no
    earlier than the mission (no whole-table LIKE per tick). A non-mission row
    that names the mission in its options is never its continuation."""
    p = build_platform(str(tmp_path / "home"))
    root, _, _ = _seed_mission(p.engine)
    with session_scope(p.engine) as db:
        db.add(Session(task="a chat", agent_type=AgentType.BUILDER, origin=None,
                       options_json=json.dumps({"continued_from": root})))
        db.commit()
    with _Statements(p.engine) as st:
        view = mission_view(p, root)
    assert view["session"]["continued_as"] is None
    likes = [q for q in st.seen if " LIKE " in q.upper()]
    assert likes and all("origin" in q and "created_at >=" in q for q in likes), likes
