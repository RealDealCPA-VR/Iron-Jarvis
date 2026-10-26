"""v1.395.0 (the long-jobs plan's Phase 6, decision 7): THE PLANNER CAN ASK
FOR A ROLE at both helper doors, and every piece lands on the member's RECORD.

* ``delegate`` and ``spawn_agent`` take ``role`` (or ``role:<id>`` as the
  agent — the dialects' own helper tools carry no role argument); the team
  router picks the member, and the call runs it exactly as a named member
  runs (its own model, held);
* a NAMED member still runs exactly as in Phase 5 (a role beside the name only
  says what the piece is, for its record);
* an unknown role, or one no usable member holds, is ONE plain sentence and
  starts nothing — never a silent run as a builder or a non-member;
* the record: each piece's row (``agents/team_record``), the finish check
  credited to the helpers that changed files, and a reviewer's verdict on the
  code it reviewed;
* the reason rides the helper's row, the delegation event and the answer;
* ``GET /team`` shows each member's record per role in plain words.

Every model call goes through the REAL router to recording doubles
(``tests/_fakes/team_world_v1395``).
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.agents import team_record as TR
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, Session
from iron_jarvis.harness import helpers as H
from iron_jarvis.providers import router as router_mod
from iron_jarvis.tools.base import ToolContext
from tests._fakes.team_world_v1395 import LOCAL, make_world, member


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)


@pytest.fixture
def w(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch)
    member(world.p, "glm", LOCAL[0], LOCAL[1], roles=("read", "code"))
    member(world.p, "claude-code", "claude-cli", "subscription", roles=("code", "review"))
    member(world.p, "codex", "codex-cli", "subscription", roles=("code", "test"))
    return world


async def _parent(p) -> str:
    s = await Orchestrator(p).create_session(
        "the parent's task", AgentType.SUPERVISOR, provider="google", model="gemini-3.8-flash"
    )
    return s.id


def _ctx(p, sid: str, tmp_path) -> ToolContext:
    return ToolContext(workspace=tmp_path, session_id=sid, agent_run_id="parent-run",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


def _sessions(p) -> list[Session]:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(Session)))
        for r in rows:
            db.expunge(r)
        return rows


def _runs(p) -> list[TR.TeamRoleRun]:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(TR.TeamRoleRun)))
        for r in rows:
            db.expunge(r)
        return rows


# --------------------------------------------------------------------------- #
# a role at both doors
# --------------------------------------------------------------------------- #


async def test_delegate_with_a_role_runs_the_picked_member_on_its_own_model(w, tmp_path):
    sid = await _parent(w.p)
    res = await w.p.registry.get("delegate").execute(
        {"role": "read", "task": "Read app.py and summarize it."}, _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    assert res.data["target"] == "custom:glm"
    assert (res.data["role"], res.data["reason"]) == ("read", "local: no record yet for reading")
    # It ran on the member's own model, held there.
    calls = [c for c in w.log if c["task"].startswith("Read app.py")]
    assert calls and all(c["provider"] == "ollama" and c["hold"] == LOCAL for c in calls)
    # The piece is on glm's record for reading, in this work's scope.
    rows = _runs(w.p)
    assert [(r.member, r.role, r.finished, r.scope) for r in rows] == [("glm", "read", True, sid)]
    assert rows[0].session_id == res.data["child_session_id"]
    # The delegation event says the role and why.
    started = [e for e in w.p.event_bus.history if e.type == EventType.DELEGATION_STARTED]
    assert started[-1].payload["role"] == "read"
    assert started[-1].payload["reason"] == "local: no record yet for reading"


async def test_role_as_the_agent_name_is_the_same_ask(w, tmp_path):
    sid = await _parent(w.p)
    res = await w.p.registry.get("delegate").execute(
        {"agent_type": "role:code", "task": "Write util.py."}, _ctx(w.p, sid, tmp_path))
    assert res.ok and res.data["target"] == "custom:glm" and res.data["role"] == "code"
    res = await w.p.registry.get("spawn_agent").execute(
        {"agent": "role:test", "task": "Run the tests."}, _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    assert res.data["agent"] == "codex" and res.data["role"] == "test"
    assert res.data["reason"] == "Codex (Codex Plus): no local member holds testing"


async def test_spawn_agent_takes_a_role_and_runs_the_member_on_its_account(w, tmp_path):
    sid = await _parent(w.p)
    w.sampler.waiting["ollama"] = 2  # the local box is busy
    res = await w.p.registry.get("spawn_agent").execute(
        {"role": "code", "task": "Write cli.py.", "piece": "cli"}, _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    assert res.data["agent"] == "claude-code"
    assert res.data["reason"] == "Claude Code (Work Max): the local box is busy"
    calls = [c for c in w.log if c["task"].startswith("Write cli.py")]
    assert calls and all(c["provider"] == "claude-cli" for c in calls)
    # Pinned to the account the router chose (lowest known usage).
    assert {c["pin"].profile_id for c in calls} == {"c-work"}
    row = _runs(w.p)[0]
    assert (row.member, row.role, row.piece) == ("claude-code", "code", "cli")


async def test_spawn_agent_with_neither_an_agent_nor_a_role_is_said(w, tmp_path):
    sid = await _parent(w.p)
    before = len(_sessions(w.p))
    res = await w.p.registry.get("spawn_agent").execute({"task": "Do it."}, _ctx(w.p, sid, tmp_path))
    assert not res.ok and res.error.startswith("missing required: agent — spawn_agent needs agent")
    assert len(_sessions(w.p)) == before


async def test_a_named_member_runs_as_before_and_a_role_beside_it_only_labels_it(w, tmp_path):
    sid = await _parent(w.p)
    res = await w.p.registry.get("delegate").execute(
        {"agent_type": "custom:codex", "role": "code", "task": "Write x.py."}, _ctx(w.p, sid, tmp_path))
    assert res.ok and res.data["target"] == "custom:codex"
    assert "reason" not in res.data  # named: the router did not pick
    assert [(r.member, r.role) for r in _runs(w.p)] == [("codex", "code")]
    # Named with no role: exactly Phase 5 — no record row.
    res = await w.p.registry.get("delegate").execute(
        {"agent_type": "custom:glm", "task": "Read y.py."}, _ctx(w.p, sid, tmp_path))
    assert res.ok and len(_runs(w.p)) == 1
    # Named with a role it does not hold: it runs; nothing is credited.
    res = await w.p.registry.get("delegate").execute(
        {"agent_type": "custom:glm", "role": "review", "task": "Review z.py."}, _ctx(w.p, sid, tmp_path))
    assert res.ok and len(_runs(w.p)) == 1


async def test_an_unknown_role_is_one_sentence_and_starts_nothing(w, tmp_path):
    sid = await _parent(w.p)
    before = len(_sessions(w.p))
    for door, args in (("delegate", {"role": "design", "task": "t"}),
                       ("delegate", {"agent_type": "role:design", "task": "t"}),
                       ("spawn_agent", {"role": "design", "task": "t"})):
        res = await w.p.registry.get(door).execute(args, _ctx(w.p, sid, tmp_path))
        assert not res.ok and res.error.startswith("'design' is not a team role"), (door, res.error)
    assert len(_sessions(w.p)) == before and not w.log


async def test_no_usable_member_is_refused_in_words_never_a_builder_run(w, tmp_path):
    sid = await _parent(w.p)
    w.svc.set_percent("x-plus", 97, resets_in=3000)
    before = len(_sessions(w.p))
    res = await w.p.registry.get("delegate").execute(
        {"role": "test", "task": "Run the tests."}, _ctx(w.p, sid, tmp_path))
    assert not res.ok
    assert res.error.startswith("No team member can take testing now: skipped Codex (Codex Plus): 97%")
    assert res.data["state"] == "not_started" and res.data["waited"] is True
    assert res.data["reason"].startswith("waited: every cloud account that can take testing is near its limit")
    assert len(_sessions(w.p)) == before and not w.log


# --------------------------------------------------------------------------- #
# the record: crashes, the finish check, the reviewer
# --------------------------------------------------------------------------- #


async def test_a_piece_whose_run_crashed_is_a_failed_piece(w, tmp_path):
    sid = await _parent(w.p)
    w.fail.add("ollama")
    res = await w.p.registry.get("delegate").execute(
        {"role": "read", "task": "Read it."}, _ctx(w.p, sid, tmp_path))
    assert not res.ok
    assert [(r.member, r.finished) for r in _runs(w.p)] == [("glm", False)]


async def test_a_reviewers_verdict_lands_on_the_code_it_reviewed(w, tmp_path):
    sid = await _parent(w.p)
    ok = await w.p.registry.get("delegate").execute(
        {"role": "code", "task": "Write util.py."}, _ctx(w.p, sid, tmp_path))
    assert ok.ok and ok.data["target"] == "custom:glm"
    w.answers["claude-cli"] = "The change misses the edge case.\nVERDICT: changes needed"
    review = await w.p.registry.get("delegate").execute(
        {"role": "review", "task": "Review util.py."}, _ctx(w.p, sid, tmp_path))
    assert review.ok and review.data["target"] == "custom:claude-code"
    # The reviewer was asked for its verdict line.
    asked = [c["task"] for c in w.log if c["provider"] == "claude-cli"]
    assert asked and TR.VERDICT_ASK in asked[0]
    rows = {r.role: r for r in _runs(w.p)}
    assert rows["code"].review == TR.REVIEW_BAD
    assert not TR.succeeded(rows["code"].finished, rows["code"].check_result, rows["code"].review)
    # Only work before the review is judged by it.
    later = await w.p.registry.get("delegate").execute(
        {"role": "code", "task": "Write more."}, _ctx(w.p, sid, tmp_path))
    assert later.ok
    newest = [r for r in _runs(w.p) if r.role == "code" and r.session_id == later.data["child_session_id"]]
    assert newest and newest[0].review == ""


def test_the_verdict_line_is_read_only_when_it_is_there():
    assert TR.verdict_of("ok\nVERDICT: approved") == TR.REVIEW_GOOD
    assert TR.verdict_of("**Verdict:** changes needed.") == TR.REVIEW_BAD
    assert TR.verdict_of("VERDICT: approved\n...\nVERDICT: changes needed") == TR.REVIEW_BAD
    assert TR.verdict_of("Looks fine to me.") == ""


async def test_the_finish_check_is_credited_to_the_helpers_that_changed_files(w, tmp_path):
    """``FinishCheck.record`` puts the reply's verdict on the record of every
    helper of the reply that CHANGED files (the check's own helper gathering,
    ``finish._helper_sessions``) — and on no other."""
    import json

    from iron_jarvis.core.models import ToolInvocation, UndoJournal
    from iron_jarvis.harness import finish as F

    mark = F.ledger_mark(w.p.engine)
    thread = "t-reply"
    with session_scope(w.p.engine) as db:
        for sid, wrote in (("h-wrote", True), ("h-read", False)):
            db.add(Session(id=sid, task="t", agent_type=AgentType.BUILDER,
                           options_json=json.dumps({H.BUILD_SESSION_OPTION: thread})))
            inv = ToolInvocation(session_id=sid, agent_run_id="r", tool="write_file" if wrote else "read_file")
            db.add(inv)
            db.flush()
            if wrote:
                db.add(UndoJournal(action_id=inv.id, session_id=sid, kind="file_delete"))
        db.commit()
    for sid in ("h-wrote", "h-read"):
        TR.note_run(w.p.engine, member="glm", role="code", session_id=sid, scope=f"build:{thread}", finished=True)
    assert F.helpers_that_changed(w.p.engine, f"build:{thread}", mark) == ["h-wrote"]
    fc = F.FinishCheck(engine=w.p.engine, home=tmp_path, event_bus=w.p.event_bus,
                       session_id=f"build:{thread}", folder=tmp_path, mark=mark, request="r")
    fc.ran = True
    fc.tests = F.TESTS_FAILED
    out = await fc.record()
    assert out["tests"] == F.TESTS_FAILED
    got = {r.session_id: r.check_result for r in _runs(w.p)}
    assert got == {"h-wrote": TR.CHECK_FAILED, "h-read": ""}


# --------------------------------------------------------------------------- #
# the reason on the helper's row (a coding session)
# --------------------------------------------------------------------------- #


async def test_the_helper_row_carries_the_role_and_why(w, tmp_path):
    sid = "build:t-row"
    frames: list[dict] = []
    parent = H.HelperParent(session_id=sid, folder=str(tmp_path), provider="claude-cli",
                            model="claude-sonnet-4-5", tools=("read_file",))
    ctx = _ctx(w.p, sid, tmp_path)
    with H.helper_call(parent, "c1", "delegate", {"agent_type": "role:read", "task": "Read it."},
                       lambda ev, data: frames.append(data)):
        res = await w.p.registry.get("delegate").execute(
            {"agent_type": "role:read", "task": "Read it."}, ctx)
    assert res.ok, res.error
    said = [f for f in frames if f.get("reason")]
    assert said and said[0]["role"] == "read"
    assert said[0]["reason"] == "local: no record yet for reading"
    # Every later frame of the row keeps it.
    assert frames[-1]["reason"] == "local: no record yet for reading"


async def test_a_refused_role_says_it_waited_on_the_row(w, tmp_path):
    sid = "build:t-wait"
    frames: list[dict] = []
    w.svc.set_percent("x-plus", 90, resets_in=600)
    parent = H.HelperParent(session_id=sid, folder=str(tmp_path), provider="claude-cli",
                            model="claude-sonnet-4-5", tools=("read_file",))
    with H.helper_call(parent, "c2", "delegate", {"role": "test", "task": "t"},
                       lambda ev, data: frames.append(data)):
        res = await w.p.registry.get("delegate").execute({"role": "test", "task": "t"}, _ctx(w.p, sid, tmp_path))
    assert not res.ok
    assert frames[-1]["reason"].startswith("waited: every cloud account that can take testing is near its limit")


def test_the_saved_line_keeps_the_role_and_the_reason():
    from iron_jarvis.daemon.routes.build import _ReplyWatch

    watch = _ReplyWatch()
    watch.see_helper({"id": "c1", "agent": "custom:glm", "task": "Read it.", "state": "done",
                      "role": "read", "reason": "local: good record for reading"})
    row = watch.helper_lines("done")[0]
    assert (row["role"], row["reason"]) == ("read", "local: good record for reading")


# --------------------------------------------------------------------------- #
# GET /team: each member's record per role, in words
# --------------------------------------------------------------------------- #


def test_the_team_view_shows_each_members_record_per_role(w):
    from iron_jarvis.daemon.routes.team import team_view
    from tests._fakes.team_world_v1395 import note

    note(w.p, "glm", "read", [True, True, True, True])
    note(w.p, "glm", "code", [True, False, False])
    view = team_view(w.p)
    glm = next(m for m in view["members"] if m["name"] == "glm")
    assert glm["records"]["read"]["words"] == "good (4 of 4 passed)"
    assert glm["records"]["read"]["status"] == "good"
    assert glm["records"]["code"]["words"] == "failed the last 2 in a row (1 of 3 passed)"
    codex = next(m for m in view["members"] if m["name"] == "codex")
    assert codex["records"] == {
        "code": {"runs": 0, "passed": 0, "streak": 0, "status": "unknown", "words": "no record yet"},
        "test": {"runs": 0, "passed": 0, "streak": 0, "status": "unknown", "words": "no record yet"},
    }
