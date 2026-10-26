"""v1.395.0 REVIEW of the team router (the long-jobs plan's Phase 6).

Each pin here was RED on the doer's merged code and names the defect it holds
shut:

* decision 8's floor ("never start new work on an account at 95%") holds for a
  member NAMED by the planner too — the 80% rule is the router's preference,
  the 95% floor is not one;
* an unpinned run's named subscription member is pinned to an account below
  the floor (Iron-Proxy's own pick takes the first ready account in its order,
  whatever its usage);
* the user's ACCOUNT GROUPS are never crossed by the pieces of one piece of
  work: once a piece of it runs on an account of a group, every later piece of
  the same work stays in that group (even after a restart: the record keeps
  the account);
* a BURST of pieces is counted before the vendor's numbers arrive — one round
  cannot push one account past the threshold;
* the demo model is never routed to;
* a member's record is ITS MODEL's record (decision 7: "that model's record"):
  a member moved to another model starts with no record;
* a pay-per-use refusal names the setting and never claims an account was
  near its limit.
"""

from __future__ import annotations

import threading

import pytest

from iron_jarvis.agents import model_team as T
from iron_jarvis.agents import team_record as TR
from iron_jarvis.iron_proxy.pin import AccountPin
from tests._fakes.team_world_v1395 import LOCAL, make_world, member


@pytest.fixture
def w(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


def groups(w):
    """work: Claude Work Max + Codex Work; personal: Claude Spare + Codex Home."""
    w.svc.add("x-work", "openai", "Codex Work", percent=60)
    w.svc.add("x-home", "openai", "Codex Home", percent=5)
    w.p.config.account_groups = {"work": ["c-work", "x-work"], "personal": ["c-spare", "x-home"]}


# --------------------------------------------------------------------------- #
# 1. the 95% floor holds for a member named directly
# --------------------------------------------------------------------------- #


def test_a_named_member_on_an_account_at_95_percent_is_refused_with_the_reset(w):
    rec = member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 97, resets_in=5400)
    caller = AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    pick = T.named_account(w.p, rec, caller, scope="s-1", coding=True)
    assert pick.pin is None
    assert pick.refusal.startswith("'claude-code' would run on Claude Code (Work Max), which is at 97% of its "
                                   "5-hour window")
    assert "nothing new starts on an account at 95% or more" in pick.refusal
    assert "It resets at " in pick.refusal


def test_a_named_member_below_the_floor_runs_where_phase_5_put_it(w):
    rec = member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 88)  # past the router's 80%, below the floor
    caller = AccountPin(profile_id="c-work", ij_provider="claude-cli", title="Work Max")
    pick = T.named_account(w.p, rec, caller, scope="s-1", coding=True)
    assert pick.refusal == "" and pick.pin is caller


def test_an_unpinned_runs_named_member_is_pinned_below_the_floor(w):
    """Iron-Proxy's normal pick is the FIRST ready account in its order —
    whatever its usage. An unpinned run's named member is pinned to the first
    account below the floor instead."""
    rec = member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 99)
    pick = T.named_account(w.p, rec, None, scope="s-1", coding=False)
    assert pick.refusal == ""
    assert pick.pin is not None and pick.pin.profile_id == "c-spare"
    # ...and every account at or past the floor: refused in words.
    w.svc.set_percent("c-spare", 96, resets_in=600)
    pick = T.named_account(w.p, rec, None, scope="s-2", coding=False)
    assert pick.pin is None and "at 99% of its 5-hour window" in pick.refusal


def test_a_queued_member_on_an_account_at_the_floor_is_held_in_words(w):
    rec = member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 97, resets_in=5400)
    w.svc.set_percent("c-spare", 98, resets_in=900)
    why = T.queued_refusal(w.p, rec)
    assert "nothing new starts on an account at 95% or more" in why


# --------------------------------------------------------------------------- #
# 2. account groups are never crossed by one piece of work
# --------------------------------------------------------------------------- #


def test_the_pieces_of_one_unpinned_work_stay_in_one_account_group(w):
    groups(w)
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    member(w.p, "codex", "codex-cli", "subscription", roles=("test",))
    first = T.route_role(w.p, "code", scope="mission-1", piece="a")
    assert first.pin.profile_id == "c-work"  # 12%: the work starts in 'work'
    # The next piece is for Codex: Codex Home (personal, 5%) is the lowest —
    # but this work runs in 'work', so it stays there.
    second = T.route_role(w.p, "test", scope="mission-1", piece="b")
    assert second.record.name == "codex" and second.pin.profile_id == "x-work"
    # Another piece of work is free to start in either group.
    other = T.route_role(w.p, "test", scope="mission-2", piece="b")
    assert other.pin.profile_id == "x-home"


def test_a_named_member_keeps_to_the_group_its_work_started_in(w):
    groups(w)
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    codex = member(w.p, "codex", "codex-cli", "subscription", roles=("test",))
    T.route_role(w.p, "code", scope="mission-1", piece="a")  # 'work'
    pick = T.named_account(w.p, codex, None, scope="mission-1", coding=False)
    assert pick.refusal == "" and pick.pin.profile_id == "x-work"


def test_the_group_of_a_work_is_read_back_from_the_record_after_a_restart(w):
    groups(w)
    member(w.p, "codex", "codex-cli", "subscription", roles=("test",))
    TR.note_run(w.p.engine, member="claude-code", role="code", session_id="h1", scope="build:s9",
                finished=True, provider="claude-cli", model="subscription", account="c-work")
    T._placements(w.p).forget()  # a restart: nothing in memory
    pick = T.route_role(w.p, "test", scope="build:s9", piece="b")
    assert pick.pin.profile_id == "x-work"


def test_a_member_whose_own_account_is_in_another_group_than_the_work_is_skipped(w):
    groups(w)
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    member(w.p, "home-codex", "codex-cli", "subscription", roles=("test",), account="x-home")
    T.route_role(w.p, "code", scope="mission-1", piece="a")  # 'work'
    pick = T.route_role(w.p, "test", scope="mission-1", piece="b")
    assert pick.record is None
    assert "another account group" in pick.refusal


# --------------------------------------------------------------------------- #
# 3. a burst is counted before the numbers arrive
# --------------------------------------------------------------------------- #


def test_a_burst_of_pieces_never_piles_past_the_threshold_on_one_account(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 70)
    picks = [T.route_role(w.p, "code", scope="s", piece=f"p{i}") for i in range(8)]
    on_work = [p for p in picks if p.record is not None and p.pin.profile_id == "c-work"]
    # 70% + 5 points a piece: two pieces take it to the 80% threshold.
    assert len(on_work) == 2
    # The burst spread to the other account — which has no numbers yet, so it
    # takes at most UNKNOWN_BURST_MAX before its own numbers arrive.
    on_spare = [p for p in picks if p.record is not None and p.pin.profile_id == "c-spare"]
    assert len(on_spare) == T.UNKNOWN_BURST_MAX
    waited = [p for p in picks if p.record is None]
    assert waited and "pieces just started there" in waited[0].refusal


def test_placements_expire_once_the_numbers_can_have_caught_up(w, monkeypatch):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 70)
    w.svc.set_percent("c-spare", 75)
    clock = [1000.0]
    monkeypatch.setattr(T, "CLOCK", lambda: clock[0])
    for i in range(2):
        assert T.route_role(w.p, "code", scope="s", piece=f"p{i}").pin.profile_id == "c-work"
    assert T.route_role(w.p, "code", scope="s", piece="p2").pin.profile_id == "c-spare"
    clock[0] += T.PLACED_RECENT_S + 1
    assert T.route_role(w.p, "code", scope="s", piece="p3").pin.profile_id == "c-work"


def test_concurrent_picks_are_counted_one_at_a_time(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 70)
    w.svc.set_percent("c-spare", 70)
    barrier = threading.Barrier(6)
    out: list = []

    def go(i):
        barrier.wait()
        out.append(T.route_role(w.p, "code", scope="s", piece=f"p{i}"))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    per = {}
    for p in out:
        if p.record is not None:
            per[p.pin.profile_id] = per.get(p.pin.profile_id, 0) + 1
    assert per == {"c-work": 2, "c-spare": 2}


# --------------------------------------------------------------------------- #
# 4. the demo model is never routed to
# --------------------------------------------------------------------------- #


def test_the_demo_model_is_never_routed_to(w):
    w.p.config.default_provider = "mock"
    member(w.p, "helper", "", "", roles=("read",))  # runs on whoever hands it work
    member(w.p, "demo", "mock", "", roles=("read",))
    pick = T.route_role(w.p, "read")
    assert pick.record is None
    assert "the demo model" in pick.refusal


# --------------------------------------------------------------------------- #
# 5. a member's record is its MODEL's record
# --------------------------------------------------------------------------- #


def test_a_member_moved_to_another_model_starts_with_no_record(w):
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("code",))
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    for i in range(3):
        TR.note_run(w.p.engine, member="glm", role="code", session_id=f"g{i}", scope="s", finished=False,
                    provider=LOCAL[0], model=LOCAL[1])
    assert T.route_role(w.p, "code").record.name == "claude-code"
    w.p.agents_registry.register("glm", "You are glm.", [], provider=LOCAL[0], model="qwen3:32b")
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "glm" and pick.reason == "local: no record yet for coding"
    # Settings › Model team reads the record the same way.
    from iron_jarvis.daemon.routes.team import team_view

    glm = next(m for m in team_view(w.p)["members"] if m["name"] == "glm")
    assert glm["records"]["code"]["words"] == "no record yet"
    # ...and back on its old model, its old record is its record again.
    w.p.agents_registry.register("glm", "You are glm.", [], provider=LOCAL[0], model=LOCAL[1])
    glm = next(m for m in team_view(w.p)["members"] if m["name"] == "glm")
    assert glm["records"]["code"]["words"] == "failed the last 3 in a row (0 of 3 passed)"


# --------------------------------------------------------------------------- #
# 6. pay per use: the words name the setting
# --------------------------------------------------------------------------- #


def test_a_pay_per_use_only_team_at_the_default_cap_is_told_where_the_limit_is(w):
    member(w.p, "gpt", "openai", "gpt-5", roles=("code",))
    pick = T.route_role(w.p, "code")
    assert pick.record is None and not pick.waited
    assert "near its limit" not in pick.refusal
    assert "Settings › Models › Model team" in pick.refusal


# --------------------------------------------------------------------------- #
# 8. routing never starts a process and never makes a request
# --------------------------------------------------------------------------- #


def test_routing_with_the_real_availability_never_spawns_or_calls_out(tmp_path, monkeypatch):
    """The v1.344.0 reader pattern: spy on every way out of the process while
    the router asks the REAL ``ProviderManager.available`` about members on
    every kind of provider — a CLI (sign-in verdict cached, never probed),
    OpenCode (a COLD model list: ``opencode models`` would run), a keyless API
    name served by a CLI, a local box, a pay-per-use API."""
    import socket
    import subprocess

    import httpx

    from iron_jarvis.core import procs
    from iron_jarvis.platform import build_platform
    from iron_jarvis.providers import cli_auth
    from iron_jarvis.providers import manager as M
    from tests._fakes.team_world_v1395 import FakeSvc

    p = build_platform(str(tmp_path))
    p.config.ollama_base_url = "http://127.0.0.1:11434"
    p.providers.configure_local(ollama_base_url="http://127.0.0.1:11434", ollama_model=LOCAL[1],
                                custom_base_url="", custom_model="")
    svc = FakeSvc()
    svc.add("c-work", "anthropic", "Work Max", percent=12)
    svc.add("x-plus", "openai", "Codex Plus", percent=40)
    p.iron_proxy = svc
    monkeypatch.setattr(M, "cli_binary_present", lambda binary: True)
    # (the suite's conftest stubs the manager's own presence check off)
    monkeypatch.setattr(M.ProviderManager, "_cli_binary_present", staticmethod(lambda binary: True))
    p.providers._opencode_cache = None
    resolved: list[str] = []
    monkeypatch.setattr(p.providers, "_opencode_resolver", lambda: resolved.append("opencode models") or ["x"])
    started: list = []
    monkeypatch.setattr(cli_auth.DEFAULT_PROBE, "_start", lambda b: started.append(("probe", b)))

    def boom(name):
        def f(*a, **k):
            started.append((name, a[:1]))
            raise AssertionError(f"routing reached {name}")
        return f

    monkeypatch.setattr(subprocess.Popen, "__init__", boom("subprocess.Popen"))
    for fn in ("spawn", "run", "run_async", "run_to_files"):
        monkeypatch.setattr(procs, fn, boom(f"procs.{fn}"))
    monkeypatch.setattr(httpx.Client, "send", boom("httpx.Client.send"))
    monkeypatch.setattr(httpx.AsyncClient, "send", boom("httpx.AsyncClient.send"))
    monkeypatch.setattr(socket.socket, "connect", boom("socket.connect"))
    reg = p.agents_registry
    reg.register("glm", "g", [], provider=LOCAL[0], model=LOCAL[1], roles=["read"])
    reg.register("cc", "c", [], provider="claude-cli", model="subscription", roles=["code"])
    reg.register("cx", "x", [], provider="codex-cli", model="subscription", roles=["code", "test"])
    reg.register("oc", "o", [], provider="opencode-cli", model="", roles=["review"])
    reg.register("api", "a", [], provider="anthropic", model="claude-sonnet-4-5", roles=["review"])
    reg.register("gpt", "p", [], provider="openai", model="gpt-5", roles=["test"])
    for role in ("read", "code", "review", "test"):
        T.route_role(p, role, scope="s", piece=role)
    assert started == [] and resolved == []



async def _parent(p, **kw) -> str:
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import AgentType

    s = await Orchestrator(p).create_session(
        "the parent's task", AgentType.SUPERVISOR, provider="google", model="gemini-3.8-flash", **kw
    )
    return s.id


def _ctx(p, sid: str, tmp_path):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=tmp_path, session_id=sid, agent_run_id="parent-run",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


def _children(p, sid: str) -> list:
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Session

    with session_scope(p.engine) as db:
        return [r.id for r in db.exec(select(Session)) if r.id != sid]


def _args(door: str, name: str, task: str) -> dict:
    return ({"agent_type": f"custom:{name}", "task": task} if door == "delegate"
            else {"agent": name, "task": task})


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_door_refuses_a_named_member_whose_account_is_at_the_floor(w, tmp_path, door):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 97, resets_in=5400)
    w.svc.set_percent("c-spare", 99, resets_in=900)
    sid = await _parent(w.p)
    res = await w.p.registry.get(door).execute(_args(door, "claude-code", "Write util.py."),
                                               _ctx(w.p, sid, tmp_path))
    assert not res.ok
    assert "nothing new starts on an account at 95% or more" in res.error
    assert _children(w.p, sid) == [] and not [c for c in w.log if c["provider"] == "claude-cli"]


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_door_pins_an_unpinned_runs_named_member_below_the_floor(w, tmp_path, door):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 99)
    sid = await _parent(w.p)
    res = await w.p.registry.get(door).execute(_args(door, "claude-code", "Write util.py."),
                                               _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    calls = [c for c in w.log if c["provider"] == "claude-cli"]
    assert calls and all(c["pin"] is not None and c["pin"].profile_id == "c-spare" for c in calls)


# --------------------------------------------------------------------------- #
# 7. the record: what counts, and the reviewer's verdict
# --------------------------------------------------------------------------- #


def _runs(p) -> list:
    from sqlalchemy import literal_column
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope

    with session_scope(p.engine) as db:
        rows = list(db.exec(select(TR.TeamRoleRun).order_by(literal_column("teamrolerun.rowid"))))
        for r in rows:
            db.expunge(r)
        return rows


def _ending(monkeypatch, *, state, result):
    """The child's run ends as ``state`` with ``result`` (its model answered;
    the run's own ending is then rewritten — what a step limit, a declined
    plan or a silent model leaves)."""
    from iron_jarvis.agents import runtime as R

    real = R.AgentRuntime.run

    async def run(self, session, agent_def, parent_id=None):
        out = await real(self, session, agent_def, parent_id=parent_id)
        out.state = state
        out.result = result
        return out

    monkeypatch.setattr(R.AgentRuntime, "run", run)


async def test_a_piece_that_ends_failed_by_itself_counts_against_the_member(w, tmp_path, monkeypatch):
    from iron_jarvis.core.models import AgentState

    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("read",))
    _ending(monkeypatch, state=AgentState.FAILED, result="stopped: reached max steps before completion")
    sid = await _parent(w.p)
    res = await w.p.registry.get("delegate").execute({"role": "read", "task": "Read it."},
                                                     _ctx(w.p, sid, tmp_path))
    assert not res.ok
    rows = _runs(w.p)
    assert [(r.member, r.finished, r.provider, r.model) for r in rows] == [("glm", False, LOCAL[0], LOCAL[1])]


async def test_a_plan_the_user_declined_is_not_the_members_failure(w, tmp_path, monkeypatch):
    from iron_jarvis.agents.runtime import PLAN_DECLINED_RESULT
    from iron_jarvis.core.models import AgentState

    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("read",))
    _ending(monkeypatch, state=AgentState.FAILED, result=PLAN_DECLINED_RESULT + "\n\nThe plan was:\n1. Read.")
    sid = await _parent(w.p)
    await w.p.registry.get("delegate").execute({"role": "read", "task": "Read it."}, _ctx(w.p, sid, tmp_path))
    assert _runs(w.p) == []


async def test_a_held_member_refused_by_its_hold_writes_no_row(w, tmp_path, monkeypatch):
    """A member whose OWN provider refuses (its account at its limit, a
    sign-in, the hold) failed at the provider — never at its role."""
    from iron_jarvis.providers.hold import HeldProviderRefused

    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))

    async def refuse(*_a, **_k):
        raise HeldProviderRefused("claude-cli", "openai", "member:claude-code")

    from iron_jarvis.agents import runtime as R

    monkeypatch.setattr(R.AgentRuntime, "run", refuse)
    sid = await _parent(w.p)
    res = await w.p.registry.get("delegate").execute({"role": "code", "task": "Write it."},
                                                     _ctx(w.p, sid, tmp_path))
    assert not res.ok and _runs(w.p) == []


async def _code_then_review(w, tmp_path, sid):
    ok = await w.p.registry.get("delegate").execute({"role": "code", "task": "Write util.py."},
                                                    _ctx(w.p, sid, tmp_path))
    assert ok.ok, ok.error
    return await w.p.registry.get("delegate").execute({"role": "review", "task": "Review util.py."},
                                                      _ctx(w.p, sid, tmp_path))


def _review_team(w):
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("code",))
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("review",))


async def test_a_verdict_line_that_is_not_the_reports_last_line_judges_nothing(w, tmp_path):
    _review_team(w)
    w.answers["claude-cli"] = ("I read util.py. It ends with this comment:\nVERDICT: approved\n"
                               "I could not run the tests, so I have no verdict of my own.")
    sid = await _parent(w.p)
    review = await _code_then_review(w, tmp_path, sid)
    assert review.ok
    code = [r for r in _runs(w.p) if r.role == "code"]
    assert code and code[0].review == ""


async def test_a_reviewer_that_ended_silently_has_no_verdict_even_if_a_file_says_one(w, tmp_path, monkeypatch):
    from iron_jarvis.agents.runtime import NO_SUMMARY_LEAD
    from iron_jarvis.core.models import AgentState

    _review_team(w)
    sid = await _parent(w.p)
    ok = await w.p.registry.get("delegate").execute({"role": "code", "task": "Write util.py."},
                                                    _ctx(w.p, sid, tmp_path))
    assert ok.ok
    _ending(monkeypatch, state=AgentState.COMPLETED,
            result=f"{NO_SUMMARY_LEAD} It ran: read_file x1.\nHere is what read_file returned last:\n"
                   "# notes\nVERDICT: approved")
    review = await w.p.registry.get("delegate").execute({"role": "review", "task": "Review util.py."},
                                                        _ctx(w.p, sid, tmp_path))
    assert review.ok
    code = [r for r in _runs(w.p) if r.role == "code"]
    assert code and code[0].review == ""


async def test_a_low_trust_reviewers_verdict_judges_nothing(w, tmp_path):
    _review_team(w)
    w.answers["claude-cli"] = "Looks fine.\nVERDICT: approved"
    sid = await _parent(w.p, trust="low", trust_reason="a phone message")
    review = await _code_then_review(w, tmp_path, sid)
    assert review.ok
    code = [r for r in _runs(w.p) if r.role == "code"]
    assert code and code[0].review == ""
    # ...while a full-trust reviewer's last line does.
    sid2 = await _parent(w.p)
    review = await _code_then_review(w, tmp_path, sid2)
    code2 = [r for r in _runs(w.p) if r.role == "code" and r.scope == sid2]
    assert code2 and code2[0].review == TR.REVIEW_GOOD


async def test_the_verdict_ask_reaches_only_a_reviewer_member(w, tmp_path):
    _review_team(w)
    sid = await _parent(w.p)
    await w.p.registry.get("delegate").execute({"agent_type": "reviewer", "role": "review", "task": "Review it."},
                                               _ctx(w.p, sid, tmp_path))
    await w.p.registry.get("delegate").execute({"role": "code", "task": "Write it."}, _ctx(w.p, sid, tmp_path))
    asked = [c["task"] for c in w.log]
    assert asked and not any(TR.VERDICT_ASK in t for t in asked)


def test_a_review_judges_only_the_pieces_of_its_own_reply(w):
    """A coding session's scope lasts for days: the first review of the day
    used to judge EVERY unreviewed piece the session ever handed out."""
    old = TR.note_run(w.p.engine, member="glm", role="code", session_id="a", scope="build:s",
                      finished=True, turn="reply-1")
    mine = TR.note_run(w.p.engine, member="glm", role="code", session_id="b", scope="build:s",
                       finished=True, turn="reply-2")
    review = TR.note_run(w.p.engine, member="claude-code", role="review", session_id="r", scope="build:s",
                         finished=True, turn="reply-2")
    assert old and mine
    assert TR.credit_review(w.p.engine, scope="build:s", review_row=review, verdict=TR.REVIEW_BAD) == 1
    got = {r.session_id: r.review for r in _runs(w.p) if r.role == "code"}
    assert got == {"a": "", "b": TR.REVIEW_BAD}


# --------------------------------------------------------------------------- #
# 9. pay per use: a member a MODEL names is under the limit too
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("door", ["delegate", "spawn_agent"])
async def test_a_model_naming_a_pay_per_use_member_at_the_default_limit_is_refused(w, tmp_path, door):
    member(w.p, "gpt", "openai", "gpt-5", roles=("code",))
    assert w.p.config.team_paid_cap_usd == 0.0
    sid = await _parent(w.p)
    res = await w.p.registry.get(door).execute(_args(door, "gpt", "Write util.py."), _ctx(w.p, sid, tmp_path))
    assert not res.ok
    assert res.error.startswith("'gpt' is a pay-per-use member, and your model team's pay-per-use limit is $0")
    assert "Settings › Models › Model team" in res.error
    assert _children(w.p, sid) == [] and not [c for c in w.log if c["provider"] == "openai"]
    # Under a limit the user set, it runs.
    w.p.config.team_paid_cap_usd = 5.0
    res = await w.p.registry.get(door).execute(_args(door, "gpt", "Write util.py."), _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error


async def test_a_task_the_user_queued_for_a_pay_member_runs_and_one_an_agent_queued_is_held(w):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.assignments.dispatcher import AssignmentDispatcher
    from iron_jarvis.core.models import AgentType

    orch = Orchestrator(w.p)
    w.p.orchestrator = orch
    member(w.p, "gpt", "openai", "gpt-5", roles=("code",))
    parent = await orch.create_session("parent", AgentType.BUILDER, provider="google", model="gemini-3.8-flash")
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    by_agent, _ = w.p.assignments.create("custom:gpt", "Write a.py.", source=f"agent:{parent.id}")
    out = await d.tick()
    assert out["held"] == 1 and not out["started"]
    assert "pay-per-use limit is $0" in w.p.assignments.get(by_agent.id).held_reason
    w.p.assignments.cancel(by_agent.id)
    by_user, _ = w.p.assignments.create("custom:gpt", "Write b.py.")
    out = await d.tick()
    await d.drain()
    assert out["started"], (out, w.p.assignments.get(by_user.id).held_reason)
    # (The harness serves a keyless "openai" through its Codex double; the
    # member's own model is what the run is held to.)
    assert [c for c in w.log if c["hold"] == ("openai", "gpt-5") and "Write b.py" in c["task"]], w.log


async def test_a_queued_member_at_the_floor_is_held_never_struck_and_starts_when_it_drops(w):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.assignments.dispatcher import AssignmentDispatcher

    w.p.orchestrator = Orchestrator(w.p)
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 97, resets_in=5400)
    w.svc.set_percent("c-spare", 98, resets_in=900)
    d = AssignmentDispatcher(w.p, interval_s=0.05)
    rec, _ = w.p.assignments.create("custom:claude-code", "Write it.")
    out = await d.tick()
    assert out["held"] == 1 and not out["started"]
    row = w.p.assignments.get(rec.id)
    assert "nothing new starts on an account at 95% or more" in row.held_reason
    assert row.status == "queued" and int(row.failure_count or 0) == 0
    w.svc.set_percent("c-spare", 40)
    await d.tick()
    await d.drain()
    calls = [c for c in w.log if c["task"].startswith("Write it")]
    assert calls and all(c["pin"] is not None and c["pin"].profile_id == "c-spare" for c in calls)


async def test_a_routed_burst_through_the_door_spreads_over_the_accounts(w, tmp_path):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 72)
    w.svc.set_percent("c-spare", 72)
    sid = await _parent(w.p)
    for i in range(4):
        res = await w.p.registry.get("delegate").execute(
            {"role": "code", "task": f"Piece {i}: write m{i}.py.", "piece": f"m{i}"}, _ctx(w.p, sid, tmp_path))
        assert res.ok, res.error
    accounts = {}
    for c in w.log:
        if c["provider"] == "claude-cli":
            accounts.setdefault(c["task"][:7], c["pin"].profile_id)
    assert sorted(accounts.values()) == ["c-spare", "c-spare", "c-work", "c-work"]
