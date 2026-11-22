"""Leap 06-a review 3: a rehearsal is never remembered as past work, and a
report the user kept never changes.

A rehearsal's run changed nothing, yet its closing words say what it was
told to carry on as if it had done ("Renamed the files."). Two readers still
took them as past work:

* the history index — the daemon's hourly backfill indexed the run, so
  ``history_search`` and the memory's automatic recall told a chat it had
  renamed the W2s; the recall's word scan (used whenever the index has no
  hit) read the run straight from the table;
* Run again (and a follow-up) on a rehearsal's run copied its rehearsal, so
  the new run joined the OLD report: a report the user kept then said
  something they never saw.

Everything goes through the REAL daemon (``create_app``: orchestrator,
runtime, registry, ledger, history index and memory fabric); only the model
is a scripted double, and nothing leaves this PC. Each test has its control.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from iron_jarvis.core.db import search_index, session_scope
from iron_jarvis.core.models import AgentType
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.fabric import MemoryFabric, _tokens
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal_runs as RR
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.rehearsal import rehearsal_of

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]

TASK = "Rename the W2 PDFs to Name - W2 2025.pdf."
QUERY = "renamed W2 files"


def _scripted(steps, closing: str = "Renamed the files.", seen: list | None = None):
    state = {"n": 0}

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        if seen is not None:
            seen.append(" ".join(str(getattr(m, "content", "") or "") for m in (messages or [])))
        i = state["n"]
        state["n"] += 1
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text=closing)
            yield {"type": "text", "text": closing}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


async def _settled(p, rid: str) -> dict[str, Any]:
    # Polls a condition (never asserts a duration).
    deadline = time.monotonic() + 60
    view = await asyncio.to_thread(RR.report, p, rid)
    while view["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        view = await asyncio.to_thread(RR.report, p, rid)
    return view


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.notifier.notify = lambda *a, **k: None  # type: ignore[method-assign]
    return app, p


async def _rehearse_the_renames(p, folder, project_id: str = "") -> tuple[str, str]:
    """A schedule rehearsal that would rename a W2: ``(rehearsal id, run id)``."""
    payload = {"task": TASK, "workspace_root": str(folder), "notify": False}
    if project_id:
        payload["project_id"] = project_id
    p.scheduler.add_task("Sort the W2s", "0 8 * * *", kind="task", payload=payload)
    p.router.stream = _scripted([[ToolCall(id="c0", name="rename_file",
                                           arguments={"path": "2025_W2_smith.pdf",
                                                      "new_path": "Smith - W2 2025.pdf"})]])
    started = await RR.start(p, "schedule", "Sort the W2s")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert (folder / "2025_W2_smith.pdf").exists(), "the rehearsal renamed the file"
    (sid,) = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(started["id"]))
    with session_scope(p.engine) as db:
        assert db.get(SessionRow, sid).summary == "Renamed the files."  # the false words
    return started["id"], sid


async def _backfill(index) -> None:
    """What the daemon's ``_fts_backfill_loop`` does (60 s after start, then
    hourly): index every run that has no history doc yet."""
    cursor = None
    for _ in range(50):
        res = await asyncio.to_thread(index.backfill, 200, cursor)
        cursor = res.get("cursor")
        if res.get("done"):
            return
    raise AssertionError("the backfill did not finish")


async def _history_refs(p, tmp_path) -> list[str]:
    tool = p.registry.get("history_search")
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                      event_bus=p.event_bus, engine=p.engine)
    told = await tool.execute({"query": QUERY}, ctx)
    assert told.ok, told.error
    return [h["ref"] for h in (told.data or {}).get("hits", [])]


async def _recalled(p) -> list[str]:
    fabric = MemoryFabric.from_platform(p)
    hits = await asyncio.to_thread(lambda: fabric.recall(QUERY, k=6, sources=["sessions"]))
    return [h.ref for h in hits]


async def test_a_rehearsal_is_never_found_as_past_work(daemon, tmp_path):
    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    _rid, sid = await _rehearse_the_renames(p, folder)
    index = search_index(p.engine)

    # Before any backfill: the recall's word scan (the index has no hit).
    assert sid not in await _recalled(p), "the memory recalled a rehearsal from the run table"
    # After the hourly backfill: neither the index, history_search nor recall.
    await _backfill(index)
    assert sid not in [h.ref for h in index.search(QUERY, kinds=["session"], limit=20)]
    assert sid not in await _history_refs(p, tmp_path), "history_search found a rehearsal"
    assert sid not in await _recalled(p), "the memory recalled a rehearsal from the index"

    # Control: the same work run for real in that folder IS found by both.
    p.router.stream = _scripted([], closing="Renamed the files.")
    real = await app.state.orchestrator.create_session(
        TASK, AgentType.BUILDER, workspace_root=str(folder), origin="schedule:Sort the W2s",
    )
    await app.state.orchestrator.run_session(real.id)
    await _backfill(index)
    assert real.id in await _history_refs(p, tmp_path)
    assert real.id in await _recalled(p)
    # …and the word scan itself still finds a real run and never a rehearsal.
    fabric = MemoryFabric.from_platform(p)
    lexical = [h.ref for h in await asyncio.to_thread(fabric._sessions_lexical, 10, _tokens(QUERY))]
    assert real.id in lexical and sid not in lexical


async def test_the_hourly_pass_opens_no_write_for_a_rehearsals_run(daemon, tmp_path, monkeypatch):
    """Review 6 (a note): a rehearsal's run never gets a history doc, so each
    hourly pass found it "not indexed yet" and opened a write to replace
    nothing. The pass passes it over; a real run with no doc is still
    indexed (control). ``sync_session`` itself, when it is called anyway
    (the post-run step, a forced pass), still writes no doc for it."""
    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    _rid, sid = await _rehearse_the_renames(p, folder)
    p.router.stream = _scripted([], closing="Renamed the files.")
    real = await app.state.orchestrator.create_session(TASK, AgentType.BUILDER, workspace_root=str(folder))
    await app.state.orchestrator.run_session(real.id)
    index = search_index(p.engine)
    index.drop_session(real.id)  # a real run the post-run step missed
    synced: list[str] = []
    sync = index.sync_session
    monkeypatch.setattr(index, "sync_session", lambda row, **kw: synced.append(str(row.id)) or sync(row, **kw))
    await _backfill(index)
    assert real.id in synced, "control: the pass indexes a real run with no doc"
    assert sid not in synced, "the pass opened a write for a rehearsal's run"
    with session_scope(p.engine) as db:
        row = db.get(SessionRow, sid)
        db.expunge(row)
    assert sync(row) == 0, "sync_session wrote a doc for a rehearsal's run"
    assert sid not in [h.ref for h in index.search(QUERY, kinds=["session"], limit=20)]
    assert real.id in [h.ref for h in index.search(QUERY, kinds=["session"], limit=20)]


async def test_app_search_lists_a_rehearsal_as_one_never_with_its_closing_words(daemon, tmp_path):
    """Review 4: ``app_search`` (the Guide's "where is my …?", one of chat's
    safe reads) lists the newest runs with their closing words. A rehearsal's
    run is still found, so the person can open it, but as a rehearsal that
    changed nothing: never "completed … Renamed the files."."""
    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    _rid, sid = await _rehearse_the_renames(p, folder)
    # Control: the same work run for real in that folder, with its own words.
    p.router.stream = _scripted([], closing="Renamed the W2s for real.")
    real = await app.state.orchestrator.create_session(
        TASK, AgentType.BUILDER, workspace_root=str(folder), origin="schedule:Sort the W2s",
    )
    await app.state.orchestrator.run_session(real.id)

    tool = p.registry.get("app_search")
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                      event_bus=p.event_bus, engine=p.engine)
    told = await tool.execute({"query": "W2", "kinds": ["session"]}, ctx)
    assert told.ok, told.error
    hits = {h["id"]: h for h in (told.data or {}).get("hits", [])}
    assert sid in hits and real.id in hits, told.output
    assert hits[sid]["detail"] == "rehearsed; builder; a rehearsal: nothing was changed", hits[sid]
    assert "Renamed the files." not in told.output, told.output
    assert "Renamed the W2s for real." in hits[real.id]["detail"]  # control


async def test_a_rehearsals_old_history_doc_is_dropped(daemon, tmp_path):
    """A doc a rehearsal's run already has (an index written before this
    release, or a forced repair) is dropped the next time it is synced."""
    from types import SimpleNamespace

    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    _rid, sid = await _rehearse_the_renames(p, folder)
    index = search_index(p.engine)
    with session_scope(p.engine) as db:
        row = db.get(SessionRow, sid)
        db.expunge(row)
    # The doc the old code wrote (the same row, without its own options).
    old = SimpleNamespace(id=sid, task=row.task, summary=row.summary, result="", project_id="",
                          finished_at=row.finished_at, created_at=row.created_at, options_json="{}")
    assert await asyncio.to_thread(index.sync_session, old) == 1, "control: a run is indexed"
    assert sid in [h.ref for h in index.search(QUERY, kinds=["session"], limit=20)]
    # While that doc is there, the memory's recall still drops the run.
    assert sid not in await _recalled(p), "the memory recalled a rehearsal from an old doc"
    assert await asyncio.to_thread(index.sync_session, row) == 0
    assert sid not in [h.ref for h in index.search(QUERY, kinds=["session"], limit=20)]


# --------------------------------------------------------------------------- #
# The project's "Recent activity" block (every chat turn and run in it)
# --------------------------------------------------------------------------- #

RECENT_HEAD = "Recent activity in this project (newest first):"


def _project(p, root) -> str:
    from iron_jarvis.core.models import Project

    with session_scope(p.engine) as db:
        proj = Project(name="Acme", root=str(root))
        db.add(proj)
        db.commit()
        return proj.id


def _recent_block(system: str) -> list[str]:
    assert RECENT_HEAD in system, system[-1500:]
    tail = system.split(RECENT_HEAD, 1)[1].lstrip("\n").split("\n")
    return [ln for ln in tail[:10] if ln.startswith("- [")]


async def test_a_projects_recent_activity_says_a_rehearsal_changed_nothing(daemon, tmp_path):
    """A rehearsal of a project's schedule is in the block every run of the
    project reads, but as a rehearsal: never its closing words."""
    from iron_jarvis.agents.runtime import AgentRuntime

    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    pid = _project(p, folder)
    _rid, sid = await _rehearse_the_renames(p, folder, project_id=pid)
    # Control: a real run of the same work in the project, with its words.
    p.router.stream = _scripted([], closing="Renamed the W2s for real.")
    real = await app.state.orchestrator.create_session(TASK, AgentType.BUILDER, project_id=pid)
    await app.state.orchestrator.run_session(real.id)

    with session_scope(p.engine) as db:
        me = SessionRow(task="the run asking", project_id=pid)
        db.add(me)
        db.commit()
        db.refresh(me)
        db.expunge(me)
    lines = _recent_block(await asyncio.to_thread(AgentRuntime(p)._project_context, me))
    assert any("Renamed the W2s for real." in ln for ln in lines), lines  # control
    assert f"- [rehearsed] {TASK}: (a rehearsal: nothing was changed)" in lines, lines
    assert not any("Renamed the files." in ln for ln in lines), lines


def test_both_chat_lanes_say_a_rehearsal_changed_nothing(tmp_path):
    """The same block in both chat lanes (POST /chat and /chat/stream)."""
    import json

    from fastapi.testclient import TestClient

    from iron_jarvis.core.ids import utcnow
    from iron_jarvis.core.models import SessionStatus
    from iron_jarvis.providers.router import RouteResult

    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    p = app.state.platform
    pid = _project(p, tmp_path)
    with session_scope(p.engine) as db:
        for task, summary, options in (
            (TASK, "Renamed the files.", {"rehearsal_id": "reh_example"}),
            ("Sort the 1099s.", "Sorted the 1099s for real.", {}),
        ):
            db.add(SessionRow(task=task, summary=summary, project_id=pid, status=SessionStatus.COMPLETED,
                              options_json=json.dumps(options), finished_at=utcnow()))
        db.commit()
    seen: list[str] = []

    async def fake_complete(*, system, messages, **kw):
        seen.append(system)
        return RouteResult(LLMResponse(text="Done."), "mock", "mock")

    async def fake_stream(*, system, messages, **kw):
        seen.append(system)
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    p.router.complete = fake_complete
    p.router.stream = fake_stream
    body = {"messages": [{"role": "user", "content": "did you rename the W2s?"}],
            "project_id": pid, "auto_tools": False}
    for route in ("/chat", "/chat/stream"):
        before = len(seen)
        r = client.post(route, json=body)
        assert r.status_code == 200, r.text
        assert len(seen) > before, route
        lines = _recent_block(seen[-1])
        assert any("Sorted the 1099s for real." in ln for ln in lines), (route, lines)  # control
        assert f"- [rehearsed] {TASK}: (a rehearsal: nothing was changed)" in lines, (route, lines)
        assert not any("Renamed the files." in ln for ln in lines), (route, lines)


# --------------------------------------------------------------------------- #
# Run again, and a follow-up, on a rehearsal's run
# --------------------------------------------------------------------------- #


async def _kept_rehearsal(p, folder) -> tuple[str, str, dict]:
    p.scheduler.add_task("Sort", "0 8 * * *", kind="task",
                         payload={"task": "Rename a.pdf to b.pdf.", "workspace_root": str(folder),
                                  "notify": False})
    p.router.stream = _scripted([[ToolCall(id="c0", name="rename_file",
                                           arguments={"path": "a.pdf", "new_path": "b.pdf"})]])
    started = await RR.start(p, "schedule", "Sort")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    kept = await RR.keep(p, started["id"])
    assert kept["kept_at"] and kept["sentence"] == "This would rename 1 file. Nothing was changed."
    (sid,) = RR.sessions_of(p, RR.RehearsalStore(p.engine).get(started["id"]))
    return started["id"], sid, kept


def _second_run_calls():
    return [[ToolCall(id="c1", name="rename_file", arguments={"path": "a.pdf", "new_path": "c.pdf"}),
             ToolCall(id="c2", name="write_file", arguments={"path": "log.txt", "content": "x"})]]


async def _assert_new_rehearsal(p, folder, rid: str, kept: dict, new_session_id: str) -> None:
    with session_scope(p.engine) as db:
        row = db.get(SessionRow, new_session_id)
        new_rid = rehearsal_of(row)
        outcome = row.outcome
    assert new_rid and new_rid != rid, "the new run joined the old rehearsal"
    assert outcome == "rehearsed", outcome
    assert sorted(x.name for x in folder.iterdir()) == ["a.pdf"], "the new run changed a file"
    # The kept report never changes.
    after = await asyncio.to_thread(RR.report, p, rid)
    for key in ("status", "sentence", "would_total", "kept_at", "would"):
        assert after[key] == kept[key], (key, after[key], kept[key])
    # The new run has its own report, of the same schedule.
    fresh = await _settled(p, new_rid)
    assert fresh["status"] == "ready" and fresh["subject"] == kept["subject"], fresh
    # The two held calls of one step run at the same time, so the sentence's
    # order is theirs: compare what it counts, not the order it says it in.
    assert fresh["counts"] == {"move": 1, "write": 1}, fresh["counts"]
    assert fresh["sentence"] in (
        "This would rename 1 file and write 1 file. Nothing was changed.",
        "This would write 1 file and rename 1 file. Nothing was changed.",
    ), fresh["sentence"]
    assert fresh["kept_at"] is None


async def test_run_again_on_a_rehearsal_is_a_new_rehearsal_and_the_kept_report_stays(daemon, tmp_path):
    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF a")
    rid, sid, kept = await _kept_rehearsal(p, folder)
    orch = app.state.orchestrator
    p.router.stream = _scripted(_second_run_calls())
    again = await orch.rerun_session(sid)
    await orch.run_session(again.id)
    await _assert_new_rehearsal(p, folder, rid, kept, again.id)


async def test_a_follow_up_on_a_rehearsal_is_a_new_rehearsal_told_nothing_changed(daemon, tmp_path):
    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF a")
    rid, sid, kept = await _kept_rehearsal(p, folder)
    orch = app.state.orchestrator
    seen: list[str] = []
    p.router.stream = _scripted(_second_run_calls(), seen=seen)
    follow = await orch.continue_session(sid, "Also write a log.")
    await orch.run_session(follow.id)
    await _assert_new_rehearsal(p, folder, rid, kept, follow.id)
    assert any("Prior result (a rehearsal: nothing was changed): Renamed the files." in s for s in seen), (
        "the follow-up was told the rehearsal's closing words as a real result"
    )


# --------------------------------------------------------------------------- #
# What the report says (review 3 notes)
# --------------------------------------------------------------------------- #


def _hook(p) -> list:
    hooks = p.outbound_webhooks
    hooks.allow_internal = True
    posted: list = []
    hooks.http_post = lambda url, payload, headers: posted.append(payload.get("type")) or {"status": 200}
    hooks.register("zap", "http://127.0.0.1:9/hook", ["session.completed", "agent.completed"])
    return posted


async def test_a_schedule_that_starts_no_run_says_why_and_no_webhook_hears_it(daemon, monkeypatch):
    from iron_jarvis.templates import MEMORY_REVIEW_SCHEDULE

    app, p = daemon
    posted = _hook(p)
    monkeypatch.setattr(p.memory_steward, "plan", lambda: {"empty": True, "reason": "no new chats"})
    p.scheduler.add_task("Weekly memory look", "0 9 * * 1", kind="task",
                         payload={"task": "Review memory.", "template": MEMORY_REVIEW_SCHEDULE["id"],
                                  "notify_channels": ["slack"]})
    started = await RR.start(p, "schedule", "Weekly memory look")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert view["detail"] == "Nothing new to review — no new chats.", view
    # The real fire would still say so where the schedule reports; no task
    # finished, so no webhook would hear one.
    assert [w["words"] for w in view["would"]] == ["send the result to Slack"], view["would"]
    assert view["sentence"] == "This would send 1 message. Nothing was changed."
    assert posted == []
    words = RR._report_words(view, "schedule", "Weekly memory look")
    assert "Nothing new to review — no new chats." in words


async def test_a_rules_webhook_row_is_written_before_its_run_starts(daemon, monkeypatch):
    """The report never grows after it says ready: the webhook a rule's task
    would tell is listed before the run is launched."""
    from iron_jarvis.reflex.router import ReflexRouter

    app, p = daemon
    _hook(p)
    seen: list = []
    real = ReflexRouter.rehearse

    async def spy(self, rule, context, *, rehearsal):
        with session_scope(p.engine) as db:
            from sqlmodel import select

            from iron_jarvis.core.models import ToolInvocation

            seen.append([r.tool for r in db.exec(select(ToolInvocation).where(
                ToolInvocation.session_id == rehearsal))])
        return await real(self, rule, context, rehearsal=rehearsal)

    monkeypatch.setattr(ReflexRouter, "rehearse", spy)
    p.reflex.add(name="Mail in", source="webhook", match="mail-in", action="session",
                 target="", task_template="File {text}", enabled=True)
    p.router.stream = _scripted([], closing="Filed.")
    started = await RR.start(p, "rule", "Mail in")
    assert seen == [["outbound_webhook"]], seen
    view = await _settled(p, started["id"])
    assert "tell another app through the webhook “zap” that the task finished" in [
        w["words"] for w in view["would"]
    ]


async def test_a_secret_in_an_item_is_masked_on_the_card_and_the_phone(daemon, tmp_path):
    app, p = daemon
    folder = tmp_path / "w"
    folder.mkdir()
    p.scheduler.add_task("Push", "0 1 * * *", kind="task",
                         payload={"task": "Push the report.", "workspace_root": str(folder), "notify": False})
    # Not shaped like any real credential (the repository is public).
    token = "example-only-value"
    p.router.stream = _scripted([[ToolCall(id="s", name="shell", arguments={
        "command": f"git push https://me:{token}@example.invalid/r.git"})]], closing="Pushed.")
    started = await RR.start(p, "schedule", "Push")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    said = " ".join(w["words"] + " " + w["target"] for w in view["would"])
    assert "git push" in said, "control: the command is listed"
    assert token not in said, said
    assert token not in RR._report_words(view, "schedule", "Push")


async def test_a_page_offers_its_open_reports_again(daemon, tmp_path, monkeypatch):
    """A reload (or a second Rehearse) never loses a report: each schedule's
    newest rehearsal comes back while it runs, or until it is kept. Review 6:
    a kept one's card is not even built when the page opens."""
    app, p = daemon
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF a")
    p.scheduler.add_task("Other", "0 7 * * *", kind="task",
                         payload={"task": "Say hello.", "workspace_root": str(folder), "notify": False})
    p.router.stream = _scripted([], closing="Hello.")
    other = await RR.start(p, "schedule", "Other")
    await _settled(p, other["id"])
    rid, _sid, _kept = await _kept_rehearsal(p, folder)   # "Sort": ready, then kept
    built: list[str] = []
    real_report = RR.report
    monkeypatch.setattr(RR, "report", lambda pl, rid: built.append(rid) or real_report(pl, rid))
    shown = await asyncio.to_thread(RR.open_reports, p, "schedule")
    assert [v["id"] for v in shown] == [other["id"]], [(v["id"], v["subject"]["name"]) for v in shown]
    assert built == [other["id"]], "a kept report's card was built"
    assert await asyncio.to_thread(RR.open_reports, p, "rule") == []
    with pytest.raises(RR.RehearsalRefused):
        RR.open_reports(p, "workflow")


def test_the_pages_read_their_open_reports_through_the_route(tmp_path):
    """``GET /rehearsals?kind=`` is what Rules and Schedules read when they
    open; an unknown kind is refused in words."""
    from fastapi.testclient import TestClient

    client = TestClient(create_app(str(tmp_path / "home")))
    r = client.get("/rehearsals", params={"kind": "schedule"})
    assert r.status_code == 200 and r.json() == {"rehearsals": []}, r.text
    r = client.get("/rehearsals", params={"kind": "workflow"})
    assert r.status_code == 400 and "rule or a schedule" in r.text, r.text


async def test_run_again_on_a_real_run_is_no_rehearsal(daemon, tmp_path):
    """Control: a real run's Run again carries no rehearsal."""
    app, p = daemon
    p.router.stream = _scripted([], closing="Done.")
    orch = app.state.orchestrator
    real = await orch.create_session("Say hello.", AgentType.BUILDER)
    await orch.run_session(real.id)
    again = await orch.rerun_session(real.id)
    with session_scope(p.engine) as db:
        assert rehearsal_of(db.get(SessionRow, again.id)) == ""
    assert RR.renew_in(p.engine, {}) == ""


def _two_runs(p, **fields) -> None:
    """One real run and one REHEARSAL's run, the same in every other way."""
    import json

    with session_scope(p.engine) as db:
        for sid, options in (("s_real", {}), ("s_rehearsed", {"rehearsal_id": "reh_records"})):
            db.add(SessionRow(id=sid, task=TASK, agent_type=AgentType.BUILDER,
                              options_json=json.dumps(options), summary="Renamed the files.", **fields))
        db.commit()


def test_a_rehearsals_run_never_teaches_the_agents_coach(daemon):
    """The coach reads an agent's recent runs (``coach/signals.recent_runs``)
    to suggest how it could work better. A rehearsal's run did nothing and
    was told to carry on as if it had, so it is never one of them. (The
    rebase review: this filter was pinned by nothing.)"""
    from iron_jarvis.coach.signals import recent_runs

    app, p = daemon
    _two_runs(p, agent_name="Bookkeeper")
    assert [r.id for r in recent_runs(p, "Bookkeeper")] == ["s_real"]


def _out_of_steps(p, roster: str, at, *, rehearsal: str = "", n: int = 4) -> None:
    """``n`` runs of ``roster`` that used all 10 steps unfinished (the coach's
    "scope-creep" pattern, as 17-a's own tests make it)."""
    import json
    from datetime import timedelta

    from iron_jarvis.core.models import AgentRun, SessionStatus, ToolInvocation

    options = {"rehearsal_id": rehearsal} if rehearsal else {}
    with session_scope(p.engine) as db:
        for i in range(n):
            sess = SessionRow(task=f"sort the reports folder ({i})", agent_type=AgentType.BUILDER,
                              agent_name=roster, status=SessionStatus.FAILED, max_steps=10,
                              summary="out of steps", provider="claude-cli", model="claude-sonnet-4-6",
                              options_json=json.dumps(options))
            sess.created_at = at + timedelta(minutes=i)
            sess.finished_at = sess.created_at + timedelta(minutes=1)
            db.add(sess)
            run = AgentRun(session_id=sess.id, steps=10, agent_type=AgentType.BUILDER,
                           provider="claude-cli", model="claude-sonnet-4-6")
            db.add(run)
            db.add(ToolInvocation(session_id=sess.id, agent_run_id=run.id, tool="read_file", ok=True,
                                  output="ok", args_json=json.dumps({"path": f"report-{i}.pdf"})))
        db.commit()


def test_the_night_review_never_coaches_an_agent_from_a_rehearsals_runs(daemon):
    """17-a's night review (master v1.559.0) gathers yesterday's runs ITSELF,
    not through ``coach/signals.recent_runs``, and runs the coach (one model
    request) for an agent whose runs show a pattern. A rehearsal's runs did
    nothing, so they never make such a job (found on the rebase onto 17-a:
    ``improve/night_review._coach_jobs`` read them). The control, the same
    runs for real, does."""
    from datetime import datetime, timedelta, timezone

    from iron_jarvis.improve import night_review as NR

    app, p = daemon
    at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)
    _out_of_steps(p, "Steady", at)
    _out_of_steps(p, "Rehearsed", at, rehearsal="reh_night")
    jobs = NR._coach_jobs(p, at - timedelta(hours=1), at + timedelta(hours=1))
    assert [j["target"] for j in jobs] == ["Steady"], jobs


def test_the_memory_review_card_never_records_a_rehearsal_as_a_review(tmp_path):
    """Opening the memory review card records each finished review run the
    schedule did not record itself (``_reconcile_unrecorded_reviews``). A
    rehearsal of the review schedule is no review: it is never recorded, so
    the review point never moves for it. (The rebase review: this filter was
    pinned by nothing.)"""
    from fastapi.testclient import TestClient

    from iron_jarvis.core.models import SessionStatus
    from iron_jarvis.memory.steward import MemorySteward
    from iron_jarvis.templates import MEMORY_REVIEW_SCHEDULE

    client = TestClient(create_app(str(tmp_path / "home")))
    p = client.app.state.platform
    _two_runs(p, origin=f"schedule:{MEMORY_REVIEW_SCHEDULE['name']}", status=SessionStatus.COMPLETED)
    assert client.get("/memory/review").status_code == 200
    assert [r["session_id"] for r in MemorySteward(p).runs()] == ["s_real"]
