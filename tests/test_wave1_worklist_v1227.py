"""Wave 1 (v1.227.0), lane BE-2 — the worklist tells the truth about who holds
what, and a run that ENDS hands its claims back.

Converted from the 2026-09-04 audit reproductions (``test_worklist_lease_audit``
W1/W2a/W3, ``test_q2_restart_reconcile``, the worklist half of
``test_q1b_cancel_race_worklist``). Live facts behind them: 55 worklist rows
app-wide sat in ``doing``, every one claimed by a run whose AgentRun was
COMPLETED, because ``WorklistStore.release_run`` had ZERO callers; and
session_2fd7 was told four times in a row that its OWN 18-item claim was
"being worked on right now … do NOT redo them".

Every test asserts a VALUE that the fix produces and the old code did not, so
reverting any one edit turns its test red:

* ``worklist_next`` re-offers the caller's own held rows (A3) and keeps the
  "another run" wording only for foreign run ids;
* completion, crash, cancel and the boot reconcile all release claims (A8);
* the boot reconcile settles AgentRun rows FAILED "interrupted by a daemon
  restart" with ``finished_at`` (RT2);
* ``reset_failed`` (store + ``POST /sessions/{id}/worklist/reset-failed``)
  re-opens exactly the failed rows and 404s for a session with no board.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentState, AgentType
from iron_jarvis.core.models import Session as SessionRow, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.worklist.models import DOING, DONE, FAILED, PENDING, WorklistItem

BOARD = "root-session"


def ctx_for(platform, tmp_path, session_id="s1", run_id="r1") -> ToolContext:
    return ToolContext(
        workspace=Path(tmp_path),
        session_id=session_id,
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _hold_then_done(gate: asyncio.Event):
    """A router stream that parks until ``gate`` is set, then finishes."""

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        await gate.wait()
        yield {"type": "final", "response": LLMResponse(text="done."),
               "provider": "mock", "model": "mock"}

    return fake_stream


async def _run_id_for(platform, session_id: str) -> str:
    for _ in range(400):
        with session_scope(platform.engine) as db:
            run = db.exec(select(AgentRun).where(AgentRun.session_id == session_id)).first()
            if run is not None:
                return run.id
        await asyncio.sleep(0.01)
    raise AssertionError("no AgentRun row appeared")


def _statuses(platform, board: str) -> dict[str, str]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(WorklistItem).where(WorklistItem.board_id == board)))
        return {r.key: r.status for r in rows}


# --------------------------------------------------------------------------- #
# A3 — the caller's own claim is not "another run".
# --------------------------------------------------------------------------- #
async def test_worklist_next_hands_a_run_back_the_rows_it_already_holds(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path, run_id="run-me")
    await platform.registry.invoke(
        "worklist_add", {"items": [f"C:/f/{i}.pdf" for i in range(8)]}, ctx, platform.permissions
    )
    first = await platform.registry.invoke("worklist_next", {"count": 8}, ctx, platform.permissions)
    assert len(first.data["claimed"]) == 8
    for key in ("C:/f/0.pdf", "C:/f/1.pdf"):
        await platform.registry.invoke(
            "worklist_done", {"key": key, "status": "done"}, ctx, platform.permissions
        )

    again = await platform.registry.invoke("worklist_next", {}, ctx, platform.permissions)

    assert again.ok and again.data["claimed"] == [], "nothing changes hands — it already did"
    assert again.data["held_by_me"] == 6
    assert again.data["held_by_others"] == 0
    assert sorted(i["key"] for i in again.data["held"]) == [f"C:/f/{i}.pdf" for i in range(2, 8)]
    text = again.output
    assert "You already hold 6 of these" in text
    for i in range(2, 8):
        assert f"C:/f/{i}.pdf" in text, "the rows are handed back BY NAME"
    assert "another run" not in text.lower(), "its own claim must not be described as foreign"
    assert "worklist_done" in text


async def test_worklist_next_keeps_the_other_run_wording_for_foreign_claims(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path, run_id="run-me")
    board = platform.worklist.board_id_for(ctx.session_id, ctx.agent_run_id, ctx.workspace)
    platform.worklist.add(board, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", "")])
    platform.worklist.claim(board, "someone-else", 2)

    out = await platform.registry.invoke("worklist_next", {}, ctx, platform.permissions)

    assert out.data["claimed"] == [] and out.data["held_by_me"] == 0
    assert out.data["held_by_others"] == 2 and out.data["held"] == []
    assert "another run" in out.output
    assert "You already hold" not in out.output
    assert "worklist_done" in out.output and "pending" in out.output


async def test_worklist_next_names_both_own_and_foreign_holdings(platform, tmp_path):
    ctx = ctx_for(platform, tmp_path, run_id="run-me")
    board = platform.worklist.board_id_for(ctx.session_id, ctx.agent_run_id, ctx.workspace)
    platform.worklist.add(board, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", ""), ("C:/f/c.pdf", "")])
    platform.worklist.claim(board, "someone-else", 1)  # a.pdf (ordered by key)
    platform.worklist.claim(board, "run-me", 2)  # b.pdf, c.pdf

    out = await platform.registry.invoke("worklist_next", {}, ctx, platform.permissions)

    assert out.data["held_by_me"] == 2 and out.data["held_by_others"] == 1
    assert "You already hold 2 of these" in out.output
    assert "1 more are held by another run" in out.output
    assert "C:/f/a.pdf" not in out.output, "a foreign row is never listed as the caller's"


def test_held_by_reads_only_that_runs_live_claims(platform):
    store = platform.worklist
    store.add(BOARD, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", ""), ("C:/f/c.pdf", "")])
    store.claim(BOARD, "mine", 2)
    store.claim(BOARD, "theirs", 1)
    store.finish(BOARD, "C:/f/a.pdf", status=DONE)  # a done row is nobody's claim
    mine = store.held_by(BOARD, "mine")
    assert [r.key for r in mine] == ["C:/f/b.pdf"]
    assert store.held_by(BOARD, "") == []
    assert store.held_by("other-board", "mine") == []


# --------------------------------------------------------------------------- #
# A8 — every finalize path hands the run's claims back.
# --------------------------------------------------------------------------- #
async def test_a_completed_run_releases_its_worklist_claims(platform):
    store = platform.worklist
    gate = asyncio.Event()
    platform.router.stream = _hold_then_done(gate)
    orch = Orchestrator(platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    run_id = await _run_id_for(platform, sess.id)
    board = store.board_for_root(sess.id, sess.workspace_path)
    store.add(board, [(f"C:/f/{i}.pdf", "") for i in range(5)])
    got, _ = store.claim(board, run_id, 5)
    assert len(got) == 5 and store.summary(board)["doing"] == 5

    gate.set()
    done = await task

    assert done.status is SessionStatus.COMPLETED
    summary = store.summary(board)
    assert summary["doing"] == 0 and summary["pending"] == 5, (
        f"finished run {run_id} still holds {summary['doing']} items"
    )
    assert all(r.claimed_by == "" for r in store.items(board))


async def test_a_crashed_run_releases_its_worklist_claims(platform):
    store = platform.worklist
    orch = Orchestrator(platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    with session_scope(platform.engine) as db:
        db.add(AgentRun(id="run-crashing", session_id=sess.id, state=AgentState.RUNNING))
        db.commit()
    board = store.board_for_root(sess.id, sess.workspace_path)
    store.add(board, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", "")])
    assert len(store.claim(board, "run-crashing", 2)[0]) == 2

    async def boom(session, agent_def):
        raise RuntimeError("provider blew up")

    orch.runtime.run = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await orch.run_session(sess.id)

    assert orch.get_session(sess.id).status is SessionStatus.FAILED
    summary = store.summary(board)
    assert summary["doing"] == 0 and summary["pending"] == 2


async def test_a_cancelled_run_releases_its_worklist_claims(platform):
    store = platform.worklist
    gate = asyncio.Event()  # never set: the run parks until it is cancelled
    platform.router.stream = _hold_then_done(gate)
    orch = Orchestrator(platform)
    sess = await orch.create_session("rename the files", AgentType.BUILDER, origin="chat")
    task = asyncio.create_task(orch.run_session(sess.id))
    run_id = await _run_id_for(platform, sess.id)
    board = store.board_for_root(sess.id, sess.workspace_path)
    store.add(board, [(f"C:/x/{k}.pdf", f"{k}.pdf") for k in "abc"])
    assert len(store.claim(board, run_id, 3)[0]) == 3

    orch.cancel_session(sess.id)
    with pytest.raises(asyncio.CancelledError):
        await task

    assert orch.get_session(sess.id).status is SessionStatus.CANCELLED
    assert store.summary(board)["doing"] == 0
    # The rerun wins every item at once — no 15-minute stale window, and
    # nothing "reclaimed" because nothing was still held.
    won, reclaimed = store.claim(board, "run_of_the_rerun", 3, stale_seconds=900)
    assert len(won) == 3 and reclaimed == 0


# --------------------------------------------------------------------------- #
# RT2 / W3 — the boot reconcile settles the run rows AND releases their claims.
# --------------------------------------------------------------------------- #
def _crashed_rows(p, n: int) -> list[tuple[SessionRow, AgentRun]]:
    """N sessions left ACTIVE with a RUNNING AgentRun — what a killed daemon
    leaves behind."""
    out = []
    with session_scope(p.engine) as db:
        for i in range(n):
            ws = p.config.workspaces_dir / f"crash-{i}"
            ws.mkdir(parents=True, exist_ok=True)
            s = SessionRow(
                task=f"rename the files in folder {i}",
                status=SessionStatus.ACTIVE,
                workspace_path=str(ws),
                origin="chat",
            )
            r = AgentRun(session_id=s.id, state=AgentState.RUNNING, steps=3)
            db.add(s)
            db.add(r)
            db.commit()
            db.refresh(s)
            db.refresh(r)
            db.expunge(s)
            db.expunge(r)
            out.append((s, r))
    return out


def test_reconcile_settles_agentruns_with_the_session(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    rows = _crashed_rows(p, 3)
    with session_scope(p.engine) as db:
        q = SessionRow(task="queued job", status=SessionStatus.QUEUED, workspace_path="")
        db.add(q)
        db.commit()
        db.refresh(q)
        qid = q.id

    orch = Orchestrator(p)  # the fresh process: nothing is _running
    assert orch.reconcile_interrupted_sessions() == 4

    for s, r in rows:
        row = orch.get_session(s.id)
        assert row.status is SessionStatus.FAILED
        assert row.summary == "interrupted by a daemon restart"
        assert row.finished_at is not None
        with session_scope(p.engine) as db:
            run = db.get(AgentRun, r.id)
            assert run.state is AgentState.FAILED, f"AgentRun {r.id} left {run.state}"
            assert run.finished_at is not None
            assert run.result == "interrupted by a daemon restart"
    # What the UI reads: GET /sessions/{id} -> transcript.runs[].state
    state = orch.transcript(rows[0][0].id)["runs"][0]["state"]
    assert getattr(state, "value", state) == "failed"
    q = orch.get_session(qid)
    assert q.status is SessionStatus.FAILED and "interrupted" in q.summary


def test_reconcile_never_touches_a_run_that_already_ended(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    (s, _r), = _crashed_rows(p, 1)
    with session_scope(p.engine) as db:
        db.add(AgentRun(id="run-earlier", session_id=s.id, state=AgentState.COMPLETED,
                        result="done earlier"))
        db.commit()
    Orchestrator(p).reconcile_interrupted_sessions()
    with session_scope(p.engine) as db:
        earlier = db.get(AgentRun, "run-earlier")
        assert earlier.state is AgentState.COMPLETED and earlier.result == "done earlier"


def test_reconcile_reoffers_the_items_held_by_an_interrupted_run(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    (s, r), = _crashed_rows(p, 1)
    store = p.worklist
    board = store.board_id_for(s.id, r.id, s.workspace_path)
    store.add(board, [(f"C:/x/{k}.pdf", f"{k}.pdf") for k in "abcd"])
    assert len(store.claim(board, r.id, 4)[0]) == 4

    Orchestrator(p).reconcile_interrupted_sessions()

    assert store.summary(board)["doing"] == 0, "the ghost's claims were not released"
    # The resumed job (same task -> same board) is handed the work at once,
    # inside the stale window, with nothing to "reclaim".
    won, reclaimed = store.claim(board, "run_after_restart", 4, stale_seconds=900)
    assert len(won) == 4 and reclaimed == 0


def test_reconcile_releases_claims_on_a_session_keyed_board(platform):
    """W3 as the audit wrote it: a board keyed on the raw root session id."""
    store = platform.worklist
    with session_scope(platform.engine) as db:
        db.add(SessionRow(id="crashed-session", task="rename", agent_type=AgentType.BUILDER,
                          status=SessionStatus.ACTIVE, workspace_path=""))
        db.add(AgentRun(id="run-crashed", session_id="crashed-session",
                        state=AgentState.RUNNING))
        db.commit()
    store.add(BOARD, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", "")])
    store.claim(BOARD, "run-crashed", 2)

    assert Orchestrator(platform).reconcile_interrupted_sessions() == 1
    with session_scope(platform.engine) as db:
        assert db.get(SessionRow, "crashed-session").status is SessionStatus.FAILED
        assert db.get(AgentRun, "run-crashed").state is AgentState.FAILED
    assert _statuses(platform, BOARD) == {"C:/f/a.pdf": PENDING, "C:/f/b.pdf": PENDING}


async def test_queued_session_is_failed_not_rerun_after_restart(tmp_path):
    """Design confirmation (kept from the audit): a QUEUED row behind
    max_concurrent_sessions is FAILED 'interrupted by a daemon restart' on
    boot — never re-queued."""
    p = build_platform(str(tmp_path / "home"))
    object.__setattr__(p.config, "max_concurrent_sessions", 1)
    orch = Orchestrator(p)
    gate = asyncio.Event()

    async def hold():
        await gate.wait()

    a = await orch.create_session("first", AgentType.BUILDER)
    b = await orch.create_session("second", AgentType.BUILDER)
    t = orch.spawn_managed(a.id, hold())
    assert t is not None
    assert orch.spawn_managed(b.id, hold()) is None
    assert orch.get_session(b.id).status is SessionStatus.QUEUED
    orch.shutdown_queue()
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    fresh = Orchestrator(p)
    fresh.reconcile_interrupted_sessions()
    rb = fresh.get_session(b.id)
    assert rb.status is SessionStatus.FAILED
    assert rb.summary == "interrupted by a daemon restart"
    assert not fresh._queued and not fresh._running
    assert (p.config.workspaces_dir / b.id).is_dir()
    fresh.delete_session(b.id)
    assert not (p.config.workspaces_dir / b.id).exists()


# --------------------------------------------------------------------------- #
# Re-run the failed items.
# --------------------------------------------------------------------------- #
def test_reset_failed_reopens_failed_rows_and_leaves_the_rest_alone(platform):
    store = platform.worklist
    store.add(BOARD, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", ""), ("C:/f/c.pdf", ""), ("C:/f/d.pdf", "")])
    store.claim(BOARD, "run-1", 3)  # a, b, c
    store.finish(BOARD, "C:/f/a.pdf", status=DONE, result_key="C:/f/a-renamed.pdf")
    store.finish(BOARD, "C:/f/b.pdf", status=FAILED, note="unreadable scan")
    store.finish(BOARD, "C:/f/c.pdf", status=FAILED, note="locked")
    store.add("other-board", [("C:/g/z.pdf", "")])
    store.claim("other-board", "run-9", 1)
    store.finish("other-board", "C:/g/z.pdf", status=FAILED)

    assert store.reset_failed(BOARD) == 2

    assert _statuses(platform, BOARD) == {
        "C:/f/a.pdf": DONE, "C:/f/b.pdf": PENDING, "C:/f/c.pdf": PENDING, "C:/f/d.pdf": PENDING,
    }
    b = store.get(BOARD, "C:/f/b.pdf")
    assert b.claimed_by == "" and b.claim_token == "" and b.claimed_at is None
    assert b.note == "unreadable scan", "the reason it failed is kept for the next holder"
    assert store.get(BOARD, "C:/f/a.pdf").result_key == "C:/f/a-renamed.pdf"
    assert _statuses(platform, "other-board") == {"C:/g/z.pdf": FAILED}, "board-scoped"
    assert store.reset_failed(BOARD) == 0, "idempotent"
    # The re-opened rows go out through the ordinary claim path.
    won, _ = store.claim(BOARD, "run-2", 5)
    assert [r.key for r in won] == ["C:/f/b.pdf", "C:/f/c.pdf", "C:/f/d.pdf"]


def _seed_session(engine, sid: str, task: str = "rename the files") -> None:
    with session_scope(engine) as db:
        db.add(SessionRow(id=sid, task=task, status=SessionStatus.COMPLETED, workspace_path=""))
        db.commit()


def test_reset_failed_route_reopens_this_sessions_failed_items(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    client = TestClient(app)
    store = platform.worklist
    _seed_session(platform.engine, "sess-1")
    board = store.root_session_for("sess-1")
    store.add(board, [("C:/f/a.pdf", ""), ("C:/f/b.pdf", ""), ("C:/f/c.pdf", "")])
    store.claim(board, "run-1", 3)
    store.finish(board, "C:/f/a.pdf", status=DONE)
    store.finish(board, "C:/f/b.pdf", status=FAILED)
    store.finish(board, "C:/f/c.pdf", status=FAILED)

    r = client.post("/sessions/sess-1/worklist/reset-failed")
    assert r.status_code == 200, r.text
    assert r.json() == {"reset": 2, "board_id": board}

    panel = client.get("/worklist/sess-1").json()
    assert panel["board_id"] == board, "the panel and the reset door name the same board"
    assert panel["summary"]["pending"] == 2 and panel["summary"]["failed"] == 0
    assert panel["summary"]["done"] == 1
    assert client.post("/sessions/sess-1/worklist/reset-failed").json()["reset"] == 0


def test_reset_failed_route_404s_without_a_board_or_a_session(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    client = TestClient(app)
    _seed_session(platform.engine, "sess-empty", task="a one-file edit")

    r = client.post("/sessions/sess-empty/worklist/reset-failed")
    assert r.status_code == 404 and "worklist" in r.json()["detail"]
    assert client.post("/sessions/nope/worklist/reset-failed").status_code == 404
