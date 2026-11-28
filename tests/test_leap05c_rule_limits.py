"""Leap 05-c (R3.3): a rule runs hands-off inside the Limits approved once.

"Keep it" on a rehearsal of a rule writes its Limits, read from that
rehearsal's ledger by code: the folders its runs may write in, and one
rule-scoped standing OK (any arguments, 30 days) per step that would have
asked. Driven through the REAL daemon, the real registry gate and the real
file tools; only the model is a double.

* a rule kept after its rehearsal runs twice with no ask, and a third run that
  reaches outside its folders is refused BY THE REGISTRY, stops there, ends
  "Needs you" and says why on the rule's row;
* removing the rule revokes its OKs (``GrantStore.revoke_scope``);
* an edit of what it does drops the Limits and the OKs Keep it made (not an
  "Always" the user gave on a card); a rename keeps them;
* a helper agent the run starts writes only where the run may;
* a rehearsal carries no Limits, so a new Keep it can widen them;
* a deny floor tool gets no OK and the Limits line says it still asks.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents import outcome as O
from iron_jarvis.agents import runtime as runtime_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.grants import args_hash
from iron_jarvis.core.models import EventRecord, ToolInvocation
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.deliverables.scope import LIMITS_LEAD, write_refusal
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.reflex import limits as L
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.triggers import starts

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(240)]


def _scripted(steps, calls: list | None = None):
    state = {"n": 0}

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        i = state["n"]
        state["n"] += 1
        if calls is not None:
            calls.append(i)
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text="Done.")
            yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


def _write(cid: str, path: str, content: str = "x") -> ToolCall:
    return ToolCall(id=cid, name="write_file", arguments={"path": path, "content": content})


async def _rehearse(p, name: str) -> dict:
    view = await RR.start(p, "rule", name)
    deadline = time.monotonic() + 60
    while view["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        view = await asyncio.to_thread(RR.report, p, view["id"])
    assert view["status"] == "ready", view
    return view


async def _fire(p, router, slug: str) -> SessionRow:
    out = await starts.hub_for(router).signal("webhook", fields={"slug": slug, "body": "x"})
    assert out and out[0]["ok"], out
    sid = out[0]["session_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with session_scope(p.engine) as db:
            row = db.get(SessionRow, sid)
            if row is not None and row.finished_at is not None and row.outcome is not None:
                db.expunge(row)
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"the run {sid} did not finish")


def _asks(p, sid: str) -> int:
    with session_scope(p.engine) as db:
        return len(list(db.exec(select(EventRecord).where(
            EventRecord.type == EventType.APPROVAL_REQUESTED, EventRecord.session_id == sid,
        ))))


@pytest.fixture()
def quick_asks(monkeypatch):
    # An ask nobody answers ends in seconds, not five minutes, so a missing
    # OK fails this file fast instead of hanging it.
    monkeypatch.setattr(runtime_mod, "SESSION_APPROVAL_TIMEOUT_S", 2)


async def _kept_rule(tmp_path, p, *, task="File {text}", steps=None, slug="file-it", name="File it"):
    intake = tmp_path / "Intake"
    intake.mkdir(exist_ok=True)
    p.permissions._base["write_file"] = "ask"  # its write asks, as an ask-tier step does
    # The builder does the work (a rule's default supervisor hands writing
    # to a helper; that path is pinned below).
    rule = p.reflex.add(name=name, source="webhook", match=slug, action="session",
                        task_template=task, workspace_root=str(intake), agent_name="builder")
    p.router.stream = _scripted(steps if steps is not None else [[_write("w1", "a.txt", "a")]])
    view = await _rehearse(p, name)
    assert [w["would"] for w in view["would"] if w["kind"] == "write"] == ["ask"], view
    assert view["limits"] and view["limits"]["folders"] == [str(intake)], view
    assert "Writes only in Intake" in view["limits"]["words"], view
    assert not (intake / "a.txt").exists()
    kept = await RR.keep(p, view["id"])
    assert kept["kept_at"]
    return p.reflex.get(rule.id), intake


async def test_a_kept_rule_runs_twice_with_no_ask_and_a_third_outside_its_limits_stops(tmp_path, quick_asks):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    router = app.state.reflex_router
    rule, intake = await _kept_rule(tmp_path, p)
    kept = L.decoded(rule)
    assert kept["folders"] == [str(intake)]
    assert len(kept["grants"]) == 1
    (grant,) = [p.grants.get(g) for g in kept["grants"]]
    assert (grant.scope_kind, grant.scope_id, grant.tool, grant.args_hash) == ("rule", rule.id, "write_file", "")
    assert grant.created_by == L.GRANT_BY and grant.expires_at is not None
    # Listed in plain words under "Allowed without asking", never the tool's name.
    assert grant.label == "Write files (kept after its rehearsal)", grant.label
    words = starts.describe_for(p, rule)["limits_words"]
    assert words.startswith("Writes only in Intake;"), words
    assert "It may write files without asking." in words, words

    # Two runs inside the Limits: no ask, the files are written.
    for i, fname in enumerate(("b.txt", "c.txt")):
        p.router.stream = _scripted([[_write(f"w{i}", fname, fname)]])
        run = await _fire(p, router, "file-it")
        assert run.outcome == O.OUTCOME_COMPLETED, (run.outcome, run.summary)
        assert _asks(p, run.id) == 0, "a kept OK did not lift the ask"
        assert (intake / fname).read_text() == fname

    # A third run reaches outside: the registry refuses, the run STOPS.
    outside = tmp_path / "Elsewhere"
    outside.mkdir()
    calls: list[int] = []
    p.router.stream = _scripted([
        [_write("o1", str(outside / "x.txt"), "x")],
        [_write("o2", "after.txt", "after")],  # never reached
    ], calls)
    run = await _fire(p, router, "file-it")
    assert not (outside / "x.txt").exists()
    assert not (intake / "after.txt").exists(), "the run went on after its limits stopped it"
    assert calls == [0], "the model was asked again after the stop"
    assert run.outcome == O.OUTCOME_NEEDS_YOU
    assert run.summary.startswith(L.STOP_LEAD) and "x.txt" in run.summary, run.summary
    ending = O.run_ending(run)
    assert ending.words == "Needs you" and ending.reason.startswith(L.STOP_LEAD), ending
    # A run with no Limits whose closing words merely BEGIN so keeps the
    # ledger's own reason.
    spoof = SessionRow(task="x", status=run.status, outcome=O.OUTCOME_NEEDS_YOU, summary=run.summary)
    assert not O.run_ending(spoof).reason.startswith(L.STOP_LEAD)
    # Nor does a run WITH Limits that needs you for another reason (an ask
    # nobody answered): only the runtime's own stamp makes it the reason.
    assert json.loads(run.options_json or "{}").get(L.STOPPED_OPTION) is True
    unstamped = {k: v for k, v in json.loads(run.options_json).items() if k != L.STOPPED_OPTION}
    asked = SessionRow(task="x", status=run.status, outcome=O.OUTCOME_NEEDS_YOU, summary=run.summary,
                       options_json=json.dumps(unstamped))
    assert L.LIMITS_OPTION in unstamped
    assert not O.run_ending(asked).reason.startswith(L.STOP_LEAD)
    with session_scope(p.engine) as db:
        (row,) = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == run.id)))
    assert row.ok is False and row.output.startswith(LIMITS_LEAD)
    stop = L.stop_of(p.reflex.get(rule.id))
    assert stop and "x.txt" in stop["words"] and stop["href"] == f"/sessions/{run.id}", stop

    # The next start takes the row's "Needs you" away.
    p.router.stream = _scripted([[_write("w9", "d.txt", "d")]])
    run = await _fire(p, router, "file-it")
    assert run.outcome == O.OUTCOME_COMPLETED
    assert L.stop_of(p.reflex.get(rule.id)) is None

    # Removing the rule revokes its OKs.
    assert p.reflex.remove(rule.id)
    assert p.grants.get(grant.id).revoked_at is not None
    assert p.grants.list(scope_kind="rule", scope_id=rule.id) == []


async def test_a_run_split_into_plan_steps_stops_at_its_limits_with_no_retry_and_no_later_step(
    tmp_path, monkeypatch, quick_asks,
):
    # A bulk-shaped rule ("rename every file that lands in Intake") runs as
    # plan steps (``decompose.should_decompose``). A write outside its
    # Limits must end the WHOLE run: the step is not checked or retried (the
    # model is not asked again) and the later steps never run.
    from iron_jarvis.agents import decompose as D

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    router = app.state.reflex_router
    rule, intake = await _kept_rule(tmp_path, p)
    outside = tmp_path / "Elsewhere"
    outside.mkdir()

    async def three_steps(*a, **kw):
        return [D.PlanStep(goal=f"Rename file {i}") for i in (1, 2, 3)]

    checks: list[int] = []

    async def no_check(*a, **kw):
        checks.append(1)
        return True, "", "none"

    monkeypatch.setattr(D, "should_decompose", lambda *a, **kw: True)
    monkeypatch.setattr(D, "plan_task", three_steps)
    monkeypatch.setattr(D, "verify_step", no_check)
    calls: list[int] = []
    p.router.stream = _scripted([
        [_write("o1", str(outside / "x.txt"), "x")],
        [_write("o2", "after.txt", "after")],  # never reached
        [_write("o3", "later.txt", "later")],  # never reached
    ], calls)
    run = await _fire(p, router, "file-it")
    assert not (outside / "x.txt").exists()
    assert not (intake / "after.txt").exists() and not (intake / "later.txt").exists()
    assert calls == [0], "the model was asked again after the stop (a retry or a later step)"
    assert checks == [], "the stopped step was checked (and could be retried)"
    assert run.outcome == O.OUTCOME_NEEDS_YOU, (run.outcome, run.summary)
    assert run.summary.startswith(L.STOP_LEAD) and "x.txt" in run.summary, run.summary
    assert O.run_ending(run).reason.startswith(L.STOP_LEAD)
    stop = L.stop_of(p.reflex.get(rule.id))
    assert stop and stop["href"] == f"/sessions/{run.id}", stop


async def test_keep_it_on_a_rehearsal_of_what_the_rule_no_longer_does_is_refused(tmp_path):
    # Rehearse, THEN change what the rule does, then press Keep it on the
    # old report: the Limits it would write were approved for the old task,
    # so nothing is kept (no folders, no OK) and the words say why.
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    intake = tmp_path / "Intake"
    intake.mkdir()
    p.permissions._base["write_file"] = "ask"
    rule = p.reflex.add(name="File it", source="webhook", match="file-it", action="session",
                        task_template="File {text}", workspace_root=str(intake), agent_name="builder")
    p.router.stream = _scripted([[_write("w1", "a.txt", "a")]])
    view = await _rehearse(p, "File it")
    assert view["limits"], view
    # A rename keeps the report good.
    p.reflex.update(rule.id, name="File it now")
    assert (await asyncio.to_thread(RR.report, p, view["id"]))["limits"]
    p.reflex.update(rule.id, task_template="Delete everything in {text}")
    stale = await asyncio.to_thread(RR.report, p, view["id"])
    assert stale["limits"] is None, "the card offered limits for what the rule no longer does"
    with pytest.raises(RR.RehearsalRefused) as refused:
        await RR.keep(p, view["id"])
    assert refused.value.status == 409 and "Rehearse it again" in refused.value.words
    after = p.reflex.get(rule.id)
    assert L.decoded(after) == {} and after.rehearsed_at is None
    assert p.grants.list(scope_kind="rule", scope_id=rule.id) == []


async def test_an_edit_of_what_it_does_drops_the_limits_and_the_oks_keep_it_made(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    rule, _intake = await _kept_rule(tmp_path, p)
    (kept_ok,) = L.decoded(rule)["grants"]
    card_ok = p.grants.create("rule", rule.id, "edit_file",
                              args_hash("edit_file", {"path": "a.txt"}), "edit a.txt")
    # A rename and a new hourly limit keep them.
    p.reflex.update(rule.id, name="File it now", max_fires_per_hour=6)
    # The same task, and the same start written again in another key order,
    # are no change.
    p.reflex.update(rule.id, task_template="File {text}")
    from iron_jarvis.reflex.models import ReflexRule

    with session_scope(p.engine) as db:
        row = db.get(ReflexRule, rule.id)
        row.start_json = '{"a": 1, "b": 2}'
        db.add(row)
        db.commit()
    p.reflex.update(rule.id, start_json={"b": 2, "a": 1})
    assert L.decoded(p.reflex.get(rule.id))["grants"] == [kept_ok]
    assert p.grants.get(kept_ok).revoked_at is None
    # What it does changes: the Limits and the OK Keep it made go.
    p.reflex.update(rule.id, task_template="Delete everything in {text}")
    after = p.reflex.get(rule.id)
    assert L.decoded(after) == {}
    assert p.grants.get(kept_ok).revoked_at is not None
    assert p.grants.get(card_ok.id).revoked_at is None, "an Always from a card was taken"
    assert "Writes only" not in starts.describe_for(p, after)["limits_words"]


async def test_a_helper_writes_only_where_the_rules_run_may(tmp_path, quick_asks):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    router = app.state.reflex_router
    intake = tmp_path / "Intake"
    intake.mkdir()
    # A rule's default supervisor hands the writing to a builder. It has no
    # folder of its own: Keep it pins the one its rehearsal worked in (an
    # Iron Jarvis work folder, so each helper gets its own folder too).
    rule = p.reflex.add(name="Sort it", source="webhook", match="sort-it", action="session",
                        task_template="Sort {text}")

    def _delegating(path: str, extra=()):
        return _scripted([
            [ToolCall(id="d1", name="delegate",
                      arguments={"agent_type": "builder", "task": f"Write {path} saying hi."})],
            [_write("h1", path, "hi"), *extra],
        ])

    p.router.stream = _delegating(str(intake / "sorted.txt"))
    view = await _rehearse(p, "Sort it")
    await RR.keep(p, view["id"])
    assert L.decoded(p.reflex.get(rule.id))["folders"] == [str(intake)]
    assert not (intake / "sorted.txt").exists()
    started = RR.RehearsalStore.sessions(RR.RehearsalStore(p.engine).get(view["id"]))
    with session_scope(p.engine) as db:
        pinned = db.get(SessionRow, started[0]).workspace_path
    assert pinned and p.reflex.get(rule.id).workspace_root == pinned

    outside = tmp_path / "Elsewhere"
    outside.mkdir()
    target = outside / "helper.txt"
    calls: list[int] = []
    p.router.stream = _scripted([
        [ToolCall(id="d1", name="delegate",
                  arguments={"agent_type": "builder", "task": f"Write {target} saying hi."})],
        [_write("h1", str(target), "hi"), _write("h2", "scratch.txt", "own folder")],
        # Never reached: the helper's stop stops the run that started it.
        [ToolCall(id="d2", name="delegate",
                  arguments={"agent_type": "builder", "task": "Write y.txt saying hi."})],
        [_write("h3", str(intake / "y.txt"), "y")],
    ], calls)
    run = await _fire(p, router, "sort-it")
    assert not target.exists(), "a helper wrote outside the rule's limits"
    # The helper's stop carries to the run that started it: its model is not
    # asked again, no second helper runs, and the run ends "Needs you" for
    # that reason (not "Part failed", not an unanswered ask).
    assert calls == [0, 1], f"the run went on after its helper stopped: {calls}"
    assert not (intake / "y.txt").exists(), "a second helper ran after the stop"
    assert run.outcome == O.OUTCOME_NEEDS_YOU, (run.outcome, run.summary)
    assert run.summary.startswith(L.STOP_LEAD) and "helper.txt" in run.summary, run.summary
    ending = O.run_ending(run)
    assert ending.words == "Needs you" and ending.reason.startswith(L.STOP_LEAD), ending
    with session_scope(p.engine) as db:
        (hand_off,) = list(db.exec(select(ToolInvocation).where(
            ToolInvocation.session_id == run.id, ToolInvocation.tool == "delegate")))
    assert hand_off.ok is False and hand_off.output.startswith(LIMITS_LEAD), hand_off.output[:200]
    assert "nobody answered" not in hand_off.output
    stop = L.stop_of(p.reflex.get(rule.id))
    assert stop and stop["href"] == f"/sessions/{run.id}" and "helper.txt" in stop["words"], stop
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == "write_file")))
        refused = [r for r in rows if r.output.startswith(LIMITS_LEAD)]
        child = db.get(SessionRow, refused[0].session_id) if refused else None
        opts = json.loads(child.options_json or "{}") if child else {}
        own = [r for r in rows if r.session_id == (child.id if child else "") and r.ok]
    assert refused and child is not None and child.id != run.id, [(r.session_id, r.ok, r.output[:80]) for r in rows]
    assert opts.get(L.LIMITS_OPTION) == {"rule_id": rule.id, "folders": [str(intake)], "helper": True}
    # A helper may always write in its own work folder.
    assert Path(child.workspace_path).resolve() != intake.resolve()
    assert own and (Path(child.workspace_path) / "scratch.txt").exists()
    assert L.stop_of(p.reflex.get(rule.id)) is not None


async def test_a_helper_working_right_in_the_rules_folder_has_its_writes_there_approved(tmp_path, quick_asks):
    # The rule works right in the user's folder, so its helper is handed
    # that folder too. What the helper wrote there in the rehearsal is
    # approved like any other write: the real run writes it again, unasked
    # and unstopped.
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    router = app.state.reflex_router
    intake = tmp_path / "Intake"
    reports = tmp_path / "Reports"
    intake.mkdir()
    reports.mkdir()
    rule = p.reflex.add(name="Sort it", source="webhook", match="sort-it", action="session",
                        task_template="Sort {text}", workspace_root=str(intake))

    def _delegating(*writes):
        return _scripted([
            [ToolCall(id="d1", name="delegate",
                      arguments={"agent_type": "builder", "task": "Write the files."})],
            list(writes),
        ])

    # (A write outside the folder a run works in is refused by the file tool
    # itself in a real run; a rehearsal holds it before that, so it lists it.)
    p.router.stream = _delegating(_write("h1", str(intake / "a.txt"), "a"),
                                  _write("h2", str(reports / "b.txt"), "b"))
    view = await _rehearse(p, "Sort it")
    await RR.keep(p, view["id"])
    kept = L.decoded(p.reflex.get(rule.id))
    assert sorted(kept["folders"]) == sorted([str(intake), str(reports)]), kept
    p.router.stream = _delegating(_write("h3", str(intake / "a.txt"), "a"))
    run = await _fire(p, router, "sort-it")
    with session_scope(p.engine) as db:
        rows = [(r.tool, r.ok, (r.output or "")[:300]) for r in db.exec(
            select(ToolInvocation).where(ToolInvocation.rehearsed == False))]  # noqa: E712
    assert (intake / "a.txt").read_text() == "a", rows
    assert run.outcome == O.OUTCOME_COMPLETED, (run.outcome, run.summary)
    assert L.stop_of(p.reflex.get(rule.id)) is None


async def test_a_helpers_step_that_nothing_could_ask_about_is_never_promised(tmp_path):
    # A rule's DEFAULT worker (the supervisor) hands the work to a helper,
    # and a helper's step that would ask is refused (nothing could ask about
    # it), in its rehearsal as in a real run. Keep it must not promise it:
    # no OK is made for it, the Limits line does not say "without asking",
    # and the folder it would have written in is not approved.
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    intake = tmp_path / "Intake"
    intake.mkdir()
    p.permissions._base["write_file"] = "ask"
    rule = p.reflex.add(name="Sort it", source="webhook", match="sort-it", action="session",
                        task_template="Sort {text}", workspace_root=str(intake))
    p.router.stream = _scripted([
        [ToolCall(id="d1", name="delegate",
                  arguments={"agent_type": "builder", "task": "Write first.txt saying hi."})],
        [_write("h1", str(tmp_path / "Elsewhere" / "first.txt"), "hi")],
    ])
    view = await _rehearse(p, "Sort it")
    assert len(view["refused"]) == 1 and not [w for w in view["would"] if w["kind"] == "write"], view
    # A refused step's folder is not approved either.
    assert view["limits"]["folders"] == [str(intake)], view
    assert "without asking" not in ((view.get("limits") or {}).get("words") or ""), view
    await RR.keep(p, view["id"])
    after = p.reflex.get(rule.id)
    assert L.decoded(after)["grants"] == [] and L.decoded(after)["folders"] == [str(intake)]
    assert p.grants.list(scope_kind="rule", scope_id=rule.id) == []
    assert "without asking" not in starts.describe_for(p, after)["limits_words"]


async def test_a_rule_with_no_folder_keeps_its_rehearsals_folder_and_clearing_runs_leaves_it(tmp_path):
    # A rule with no folder of its own works, after Keep it, in the folder
    # Iron Jarvis made for its rehearsal. Clearing finished runs ("Clear
    # completed") must not move that folder to the trash: the rule's next
    # runs work there.
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.permissions._base["write_file"] = "ask"
    rule = p.reflex.add(name="Note it", source="webhook", match="note-it", action="session",
                        task_template="Note {text}", agent_name="builder")
    p.router.stream = _scripted([[_write("n1", "note.txt", "n")]])
    view = await _rehearse(p, "Note it")
    await RR.keep(p, view["id"])
    after = p.reflex.get(rule.id)
    folder = str(after.workspace_root or "")
    from iron_jarvis.agents.orchestrator import is_managed_workspace

    assert folder and is_managed_workspace(p.config, folder), folder
    assert L.decoded(after)["folders"] == [folder]
    Path(folder, "kept.txt").write_text("k")
    for sid in RR.sessions_of(p, RR.RehearsalStore(p.engine).get(view["id"])):
        p.orchestrator.delete_session(sid)
    assert Path(folder, "kept.txt").read_text() == "k", "the rule's folder went to the trash"


async def test_a_rehearsal_carries_no_limits_so_keep_it_again_widens_them(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    rule, intake = await _kept_rule(tmp_path, p)
    (first_ok,) = L.decoded(rule)["grants"]
    reports = tmp_path / "Reports"
    reports.mkdir()
    p.router.stream = _scripted([[_write("r1", str(reports / "sum.txt"), "s")]])
    view = await _rehearse(p, "File it")
    assert view["refused"] == [], "the old limits refused a step of the rehearsal"
    sessions = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(view["id"]))
    with session_scope(p.engine) as db:
        opts = json.loads(db.get(SessionRow, sessions[0]).options_json or "{}")
    assert L.LIMITS_OPTION not in opts
    await RR.keep(p, view["id"])
    kept = L.decoded(p.reflex.get(rule.id))
    assert kept["folders"] == [str(reports)]
    assert p.grants.get(first_ok).revoked_at is not None, "the earlier Keep it's OK outlived it"
    assert len([g for g in kept["grants"] if p.grants.get(g).revoked_at is None]) == 1


async def test_a_deny_floor_tool_gets_no_ok_and_still_asks(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    intake = tmp_path / "Intake"
    intake.mkdir()
    rule = p.reflex.add(name="Run it", source="webhook", match="run-it", action="session",
                        task_template="Run {text}", workspace_root=str(intake), agent_name="builder")
    p.router.stream = _scripted([[ToolCall(id="s1", name="shell", arguments={"command": "echo hi > out.txt"})]])
    view = await _rehearse(p, "Run it")
    shells = [w for w in view["would"] if w["kind"] == "run"]
    assert shells and shells[0]["would"] == "ask", view
    assert "To run commands it still asks you each time." in view["limits"]["words"]
    await RR.keep(p, view["id"])
    after = p.reflex.get(rule.id)
    kept = L.decoded(after)
    assert kept["grants"] == [] and kept["asks"] == ["shell"]
    assert [g for g in p.grants.list(scope_kind="rule", scope_id=rule.id)] == []
    assert "To run commands it still asks you each time." in starts.describe_for(p, after)["limits_words"]


def test_an_ok_for_another_kind_of_step_never_says_it_runs_commands():
    # Commands never get an OK, so a "run"-kind OK is some other step: the
    # Limits line and the OK's label say so in plain words.
    probe = type("_R", (), {"limits_json": json.dumps({"folders": ["C:/Intake"], "kinds": ["write", "run"]})})()
    said = L.words(probe)
    assert "It may write files and take the other steps it rehearsed without asking." in said, said
    assert "run commands" not in said
    assert L.ok_label("write_file", "write") == "Write files (kept after its rehearsal)"
    assert "_" not in L.ok_label("todo_add", "run")


def test_every_step_that_runs_commands_or_code_keeps_asking():
    for name in ("shell", "run_code", "code_run", "process_start", "process_write", "repl", "pane_send"):
        assert L.always_asks(name), name
    assert not L.always_asks("write_file")


async def test_a_step_that_runs_code_gets_no_ok_and_still_asks(tmp_path, monkeypatch):
    tool = "run_code"
    args = {"language": "python", "code": "open('C:/elsewhere/out.txt', 'w').write('x')"}
    # Code a step runs can write anywhere, and the Limits cannot read where:
    # like the shell, it gets no OK, so "Writes only in ..." stays true and
    # the Handbook's "running commands still ask every time" too.
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    intake = tmp_path / "Intake"
    intake.mkdir()
    p.permissions._base[tool] = "ask"
    # A low-trust run (the default for a door from outside) keeps code away
    # entirely; the person may trust these doors fully.
    p.config.comm_trust = "full"
    # An agent that holds the tool (a custom agent may; the builder does not).
    armed = runtime_mod.arm_for_task
    monkeypatch.setattr(runtime_mod, "arm_for_task",
                        lambda *a, **kw: [*armed(*a, **kw), tool])
    rule = p.reflex.add(name="Code it", source="webhook", match="code-it", action="session",
                        task_template="Code {text}", workspace_root=str(intake), agent_name="builder")
    p.router.stream = _scripted([[ToolCall(id="c1", name=tool, arguments=args)]])
    view = await _rehearse(p, "Code it")
    runs = [w for w in view["would"] if w["kind"] == "run"]
    assert runs and runs[0]["would"] == "ask", view
    assert "without asking" not in view["limits"]["words"], view
    assert "To run commands it still asks you each time." in view["limits"]["words"], view
    await RR.keep(p, view["id"])
    after = p.reflex.get(rule.id)
    kept = L.decoded(after)
    assert kept["grants"] == [] and kept["asks"] == [tool], kept
    assert p.grants.list(scope_kind="rule", scope_id=rule.id) == []
    assert "To run commands it still asks you each time." in starts.describe_for(p, after)["limits_words"]


def _app_tool():
    """An app's tool (``mcp__<app>__<tool>``): its permission key is the
    shared ``mcp_call``, which is on the deny floor."""
    from iron_jarvis.tools.base import Tool, ToolResult

    class FakeAppTool(Tool):
        name = "mcp__fakeapp__create_item"
        description = "Create an item in the fake app."
        input_schema = {"type": "object", "properties": {"title": {"type": "string"}}}
        permission_key = "mcp_call"

        async def execute(self, args, ctx):
            return ToolResult(ok=True, output="made")

    return FakeAppTool()


@pytest.mark.parametrize("tool, args, still", [
    # An app's tool: a standing OK for any arguments is never honoured on its
    # deny floor key, so promising "without asking" would be untrue.
    ("mcp__fakeapp__create_item", {"title": "x"}, "To use your apps it still asks you each time."),
    # File steps whose target the Limits cannot read: their writes are never
    # refused at the Limits, so an OK would let them write anywhere.
    ("download_manage", {"download": "dl_1", "action": "move", "to": "C:/Elsewhere"},
     "To move or rename files it still asks you each time."),
    ("excel_live_edit", {"workbook": "Book1.xlsx", "ops": [{"op": "set", "range": "A1", "value": "1"}]},
     "To change files it still asks you each time."),
])
async def test_a_step_an_ok_could_not_keep_inside_the_limits_gets_none_and_still_asks(
    tmp_path, monkeypatch, tool, args, still,
):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    intake = tmp_path / "Intake"
    intake.mkdir()
    if tool.startswith("mcp__"):
        p.registry.register(_app_tool(), mcp=True)
    registered = p.registry.get(tool)
    assert registered is not None, f"{tool} is not registered"
    p.permissions._base[registered.perm_key()] = "ask"
    p.permissions._base[tool] = "ask"
    p.config.comm_trust = "full"
    armed = runtime_mod.arm_for_task
    monkeypatch.setattr(runtime_mod, "arm_for_task",
                        lambda *a, **kw: [*armed(*a, **kw), tool])
    rule = p.reflex.add(name="Do it", source="webhook", match="do-it", action="session",
                        task_template="Do {text}", workspace_root=str(intake), agent_name="builder")
    p.router.stream = _scripted([[ToolCall(id="t1", name=tool, arguments=args)]])
    view = await _rehearse(p, "Do it")
    assert [w for w in view["would"] if w["would"] == "ask"], view
    assert "without asking" not in view["limits"]["words"], view
    assert still in view["limits"]["words"], view
    await RR.keep(p, view["id"])
    after = p.reflex.get(rule.id)
    kept = L.decoded(after)
    assert kept["grants"] == [] and kept["asks"] == [tool], kept
    assert p.grants.list(scope_kind="rule", scope_id=rule.id) == []
    said = starts.describe_for(p, after)["limits_words"]
    assert still in said and "without asking" not in said, said


def test_steps_whose_target_the_limits_cannot_read_keep_asking():
    for name in ("download_manage", "excel_live_edit"):
        assert L.always_asks(name), name
    for name in ("write_file", "edit_file", "rename_file", "apply_patch", "todo_add"):
        assert not L.always_asks(name), name
    assert L.always_asks("mcp__fakeapp__create_item", _app_tool())


def test_the_registrys_check_inside_and_outside_the_limits(tmp_path):
    root = tmp_path / "Intake"
    root.mkdir()
    scope = L.scope_for({L.LIMITS_OPTION: {"rule_id": "r1", "folders": [str(root)]}}, root)
    assert scope is not None and scope.limits_of == "r1"
    assert write_refusal("write_file", {"path": "a.txt"}, root, scope) == ""
    assert write_refusal("write_file", {"path": "sub/sort.py"}, root, scope) == "", "a script inside is fine"
    words = write_refusal("write_file", {"path": str(tmp_path / "x.txt")}, root, scope)
    assert words.startswith(LIMITS_LEAD) and "x.txt" in words
    assert write_refusal("shell", {"command": f"copy a.txt {tmp_path / 'y.txt'}"}, root, scope).startswith(LIMITS_LEAD)
    # No option, or one without a rule: no scope (every other run unchanged).
    assert L.scope_for({}, root) is None
    assert L.scope_for({L.LIMITS_OPTION: {"folders": [str(root)]}}, root) is None
    assert O.LIMITS_STOP_LEAD == L.STOP_LEAD


def test_a_helper_handed_the_users_own_folder_gets_no_more_than_its_parent(tmp_path):
    # A helper works in a folder Iron Jarvis made for it (it may write
    # there), or, when its parent works right in the user's folder, in THAT
    # folder. When the Limits do not include that folder, the helper may not
    # write there either: it never gets more than the run that started it.
    from types import SimpleNamespace

    cfg = SimpleNamespace(workspaces_dir=tmp_path / "home" / "workspaces")
    users = tmp_path / "Documents"
    reports = tmp_path / "Reports"
    managed = cfg.workspaces_dir / "session_helper"
    for d in (users, reports, managed):
        d.mkdir(parents=True)
    opts = {L.LIMITS_OPTION: {"rule_id": "r1", "folders": [str(reports)], "helper": True}}

    def _session(ws):
        return SessionRow(task="x", workspace_path=str(ws), options_json=json.dumps(opts))

    own = runtime_mod._rule_limits_scope(_session(managed), managed, cfg)
    assert write_refusal("write_file", {"path": "scratch.txt"}, managed, own) == ""
    handed = runtime_mod._rule_limits_scope(_session(users), users, cfg)
    assert write_refusal("write_file", {"path": "a.txt"}, users, handed).startswith(LIMITS_LEAD)
    assert write_refusal("write_file", {"path": str(reports / "r.txt")}, users, handed) == ""


def test_the_rules_list_carries_its_limits_its_stop_and_its_oks(tmp_path):
    from fastapi.testclient import TestClient

    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    try:
        intake = tmp_path / "Intake"
        intake.mkdir()
        rule = p.reflex.add(name="File it", source="webhook", match="file-it", action="session",
                            task_template="File {text}", workspace_root=str(intake))
        ok = p.grants.create("rule", rule.id, "write_file", "", "write_file: any arguments", 30,
                             created_by=L.GRANT_BY)
        from iron_jarvis.reflex.models import ReflexRule

        with session_scope(p.engine) as db:
            row = db.get(ReflexRule, rule.id)
            row.limits_json = json.dumps({"folders": [str(intake)], "grants": [ok.id], "kinds": ["write"],
                                          "asks": ["shell"], "kept_at": "2026-11-27T09:00:00+00:00"})
            db.add(row)
            db.commit()
        L.note_stop(p, rule.id, L.STOP_LEAD + " it would have written D:/out/x.txt.", "/sessions/s1")
        (got,) = [r for r in c.get("/reflex/rules").json()["rules"] if r["id"] == rule.id]
        assert got["limits"] == {"folders": [str(intake)], "kept_at": "2026-11-27T09:00:00+00:00"}
        assert got["limits_stop"]["words"].startswith(L.STOP_LEAD) and got["limits_stop"]["href"] == "/sessions/s1"
        assert [g["id"] for g in got["standing_grants"]] == [ok.id]
        assert "limits_json" not in got and "limits_stop_json" not in got
        assert got["limits_words"].startswith("Writes only in Intake;")
        assert "It may write files without asking." in got["limits_words"]
        assert "To run commands it still asks you each time." in got["limits_words"]
        # A removed OK drops out of the line (the words name only live ones).
        c.post(f"/grants/{ok.id}/revoke")
        (got,) = [r for r in c.get("/reflex/rules").json()["rules"] if r["id"] == rule.id]
        assert "without asking" not in got["limits_words"]
        assert got["standing_grants"] == []
    finally:
        try:
            p.scheduler.shutdown()
        except Exception:  # noqa: BLE001
            pass
