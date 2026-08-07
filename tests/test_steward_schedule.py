"""The WEEKLY memory review — the path this feature actually ships on (v1.143.0).

``POST /memory/review/run`` was always windowed; the schedule was not. A ``task``
schedule stores its prompt on the row, so every Monday fire re-read history with
NO cursor: the same conversations offered again, the same notes re-written, and a
review point that never moved however many weeks it ran. Pair M2's reconciliation
made those fires VISIBLE on the card; it could not make them windowed, and said
so.

This file pins the fix in ``platform._dispatch_scheduled``:

* the fire's prompt is built from ``steward.plan()`` — the windowed one — not
  from the static template text stored on the schedule row;
* an EMPTY window SKIPS the fire entirely. No session, no run, no cursor move.
  Asking a model to curate nothing is how memory fills with invented facts, and
  a weekly job hits an empty window most weeks;
* the run is RECORDED by the schedule itself, with the plan's cursor, the
  session id, and the READ counts (successful ``ltm_append`` calls; proposals
  filed under this ``run_id``) — so the review point advances on the shipping
  path and not only on the manual button;
* it is identified by NAME, never by matching the task TEXT;
* nothing here can break the scheduler thread, and the reconciliation in
  ``routes/memory_review.py`` must not double-count what the schedule recorded.

Offline: a fake non-mock adapter satisfies the "real model" gate and
``run_session`` is stubbed, so no model is ever called.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.proposals import MemoryProposalStore
from iron_jarvis.memory.steward import MemorySteward
from iron_jarvis.templates import MEMORY_REVIEW_SCHEDULE, MEMORY_REVIEW_TASK

NOW = datetime.now(timezone.utc)


@pytest.fixture
def client(tmp_path) -> TestClient:
    brain = Path(tmp_path) / ".ironjarvis" / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    (brain / "alpha.md").write_text("# Alpha\n\nThe original fact.\n", encoding="utf-8")
    (brain / "alpha-copy.md").write_text("# Alpha copy\n\nSame fact.\n", encoding="utf-8")
    return TestClient(create_app(str(tmp_path)))


class _FakeAdapter:  # deliberately NOT MockLLMAdapter
    provider = "anthropic"
    model = "claude-opus-4-8"


def _seed_history(client, ref: str = "thr_weekly") -> None:
    """One unreviewed conversation, so the window is not empty."""
    client.app.state.platform.search_index.sync_thread(
        ref,
        kind="chat",
        title="S-corp election timing",
        project_id="",
        entries=[
            {
                "role": "user",
                "content": "We file the S-corp election by March 15 every year.",
                "at": NOW - timedelta(days=2),
                "seq": 0,
            }
        ],
    )


def _install(client, name: str | None = None, task: str | None = None) -> str:
    template = client.get("/memory/review").json()["template"]
    body = {
        "name": name or template["name"],
        "cron": template["cron"],
        "kind": template["kind"],
        "payload": {"task": task if task is not None else template["task"]},
    }
    assert client.post("/schedules", json=body).status_code == 200
    return body["name"]


def _stub_session(client, monkeypatch, *, notes: int = 0, proposals: int = 0,
                  status=SessionStatus.COMPLETED, summary: str = "Saved notes."):
    """``run_session`` that completes the row and leaves REAL ledger rows behind.

    The counts the schedule records must be read off the session's own tool
    ledger and proposal rows — never estimated, never parsed out of prose — so
    the stub writes exactly those rows and the assertions read them back.
    """
    from iron_jarvis.agents.orchestrator import Orchestrator

    platform = client.app.state.platform

    async def _run(self, session_id):
        with session_scope(platform.engine) as db:
            row = db.get(Session, session_id)
            row.status = status
            row.summary = summary
            db.add(row)
            for _ in range(notes):
                db.add(
                    ToolInvocation(
                        session_id=session_id,
                        agent_run_id="",
                        tool="ltm_append",
                        ok=True,
                    )
                )
            db.commit()
        store = MemoryProposalStore(platform.engine, ltm=platform.ltm)
        for i in range(proposals):
            store.create(
                kind="duplicate",
                base="brain",
                refs=["alpha", f"alpha-copy-{i}"],
                rationale="Both notes say the same thing.",
                suggested_action="Keep “alpha”.",
                payload={"remove_refs": [f"alpha-copy-{i}"]},
                run_id=session_id,
            )
        return self.get_session(session_id)

    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: _FakeAdapter())
    monkeypatch.setattr(Orchestrator, "run_session", _run)


def _session_task(client, session_id: str) -> str:
    with session_scope(client.app.state.platform.engine) as db:
        return db.get(Session, session_id).task


# --------------------------------------------------------------------------- #
# the fire runs the WINDOWED prompt, not the stored one
# --------------------------------------------------------------------------- #
def test_the_weekly_fire_runs_the_stewards_windowed_prompt(client, monkeypatch):
    """The seam. Before this, the schedule handed the model the durable template
    text — a prompt with no cursor, no conversation list, and no idea what had
    already been reviewed."""
    _seed_history(client)
    _stub_session(client, monkeypatch)
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"
    task = _session_task(client, fired["last_session_id"])

    # It is the STEWARD's prompt: the window's own conversation, fenced.
    assert "S-corp election timing" in task
    assert "ref thr_weekly" in task
    assert "[UNTRUSTED CONTENT" in task
    assert "memory_propose" in task  # …and it can still file housekeeping
    # …and NOT the static text stored on the schedule row.
    assert task != MEMORY_REVIEW_TASK


def test_the_schedule_is_identified_by_name_not_by_its_task_text(client, monkeypatch):
    """A schedule that merely QUOTES the template must not start moving the
    review cursor, and a user who edits one word of the memory-review prompt
    must not silently lose their windowed reviews."""
    _seed_history(client)
    _stub_session(client, monkeypatch, notes=3)
    name = _install(client, name="my-own-weekly-thing", task=MEMORY_REVIEW_TASK)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"
    # Same words, different schedule: it ran the STORED prompt, untouched…
    assert _session_task(client, fired["last_session_id"]) == MEMORY_REVIEW_TASK
    steward = MemorySteward(client.app.state.platform)
    # …and it did not record a review or advance anybody's review point. (The
    # card's reconciliation is scoped to the memory-review origin, so it stays
    # out of this too.)
    client.get("/memory/review")
    assert steward.runs() == []
    assert steward.cursor() == ""


# --------------------------------------------------------------------------- #
# an empty window skips the fire
# --------------------------------------------------------------------------- #
def test_an_empty_window_skips_the_fire_entirely(client, monkeypatch):
    """Most weeks there is nothing new. Firing anyway hands a model an empty
    curation task, and a model asked to curate nothing invents something — the
    exact failure the whole steward exists to prevent."""
    _stub_session(client, monkeypatch)  # would complete, if it ever ran
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"  # a skipped week is not a failure…
    assert "nothing new to review" in fired["last_detail"]  # …and says why
    assert fired["last_session_id"] == ""

    assert client.get("/sessions").json()["sessions"] == []  # no session at all
    steward = MemorySteward(client.app.state.platform)
    assert steward.runs() == []  # no run row for a review that did not happen
    assert steward.cursor() == ""  # and nothing to advance past


def test_a_switched_off_steward_skips_the_fire_and_says_so(client, monkeypatch):
    """Turning curation off must actually stop the weekly review. Falling back
    to the stored prompt here would review anyway, and the row would report a
    week that read history the user asked it not to."""
    _seed_history(client)
    _stub_session(client, monkeypatch, notes=1)
    # ``Config`` does not declare the flag yet (the steward reads it defensively
    # so it starts working the moment anyone does), so the switch is thrown
    # where the dispatcher actually reads it: through ``plan()``.
    monkeypatch.setattr(MemorySteward, "enabled", lambda self: False)
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"
    assert fired["last_detail"] == "memory review is switched off, so nothing ran"
    assert fired["last_session_id"] == ""
    assert client.get("/sessions").json()["sessions"] == []


def test_a_skipped_week_does_not_stop_the_next_one(client, monkeypatch):
    """The skip is a no-op, not a latch: history arriving after it still gets
    reviewed on the following fire."""
    _stub_session(client, monkeypatch, notes=1)
    name = _install(client)
    assert "nothing new" in client.post(f"/schedules/{name}/run").json()["last_detail"]

    _seed_history(client)
    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_session_id"]
    assert "S-corp election timing" in _session_task(client, fired["last_session_id"])


# --------------------------------------------------------------------------- #
# the record_run closure — ONE row, real counts, a real cursor
# --------------------------------------------------------------------------- #
def test_the_weekly_fire_records_its_own_run_and_advances_the_review_point(
    client, monkeypatch
):
    _seed_history(client)
    _stub_session(client, monkeypatch, notes=2, proposals=1, summary="Saved 2 notes.")
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"

    steward = MemorySteward(client.app.state.platform)
    runs = steward.runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["ok"] is True
    assert run["kind"] == "review"
    assert run["session_id"] == fired["last_session_id"]
    assert run["conversations"] == 1
    assert run["refs"] == ["thr_weekly"]
    # READ counts, off the session's own ledgers — the same helpers the manual
    # lane uses, so the two lanes can never report different numbers.
    assert run["notes_added"] == 2
    assert run["proposals_raised"] == 1
    assert run["outcome"] == "Saved 2 notes."
    # The review point MOVED — the whole reason the windowed prompt matters.
    assert run["cursor"] and steward.cursor() == run["cursor"]


def test_two_weekly_fires_never_re_review_the_same_conversation(client, monkeypatch):
    """The cursor is only worth advancing if the NEXT fire respects it."""
    _seed_history(client, "thr_one")
    _stub_session(client, monkeypatch, notes=1)
    name = _install(client)
    first = client.post(f"/schedules/{name}/run").json()
    assert "thr_one" in _session_task(client, first["last_session_id"])

    _seed_history(client, "thr_two")
    second = client.post(f"/schedules/{name}/run").json()
    task = _session_task(client, second["last_session_id"])
    assert "ref thr_two" in task
    assert "ref thr_one" not in task  # already reviewed, never offered again


def test_a_failed_weekly_review_is_recorded_and_never_advances_the_point(
    client, monkeypatch
):
    """A crashed review must be VISIBLE (the card shows failures) and must not
    move the watermark — otherwise a fire that read nothing would mark the
    history it never read as reviewed."""
    from iron_jarvis.agents.orchestrator import Orchestrator

    _seed_history(client)
    platform = client.app.state.platform
    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: _FakeAdapter())

    async def _boom(self, session_id):
        raise RuntimeError("the provider died mid-review")

    monkeypatch.setattr(Orchestrator, "run_session", _boom)
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "error"

    steward = MemorySteward(client.app.state.platform)
    runs = steward.runs()
    assert len(runs) == 1
    assert runs[0]["ok"] is False
    assert "provider died" in runs[0]["outcome"]
    assert steward.cursor() == ""
    # …and the conversation is still on offer for the next fire.
    assert steward.window().refs() == ["thr_weekly"]


# --------------------------------------------------------------------------- #
# no double counting with the card's reconciliation
# --------------------------------------------------------------------------- #
def test_the_card_reconciliation_does_not_double_count_the_schedules_own_run(
    client, monkeypatch
):
    """Both lanes now write to the same ledger, so the one thing that must be
    proved is that they do not both write the SAME fire.

    Two independent guards: ``_reconcile_unrecorded_reviews`` skips a session id
    it already sees in ``steward.runs()``, and ``record_run`` refuses a second
    successful row for a session id it has already recorded. Reading the card
    repeatedly — which is what a dashboard poll does — must therefore leave the
    numbers, the run count and the review point exactly where they were.
    """
    _seed_history(client)
    _stub_session(client, monkeypatch, notes=2, proposals=1)
    name = _install(client)
    fired = client.post(f"/schedules/{name}/run").json()

    steward = MemorySteward(client.app.state.platform)
    cursor = steward.cursor()
    assert cursor

    first = client.get("/memory/review").json()["steward"]["stats"]
    assert first["runs"] == 1 and first["successful_runs"] == 1
    assert first["notes_added"] == 2 and first["proposals_raised"] == 1

    for _ in range(3):  # the dashboard polls this card
        again = client.get("/memory/review").json()["steward"]["stats"]
    assert again == first
    runs = steward.runs()
    assert len(runs) == 1  # EXACTLY one run row for one fire
    assert runs[0]["session_id"] == fired["last_session_id"]
    assert steward.cursor() == cursor  # one cursor advance, and it stayed put


# --------------------------------------------------------------------------- #
# nothing here may break the scheduler thread
# --------------------------------------------------------------------------- #
def test_a_broken_steward_still_fires_the_schedule(client, monkeypatch):
    """v1.119's discipline: a fire records its outcome and swallows, so the
    scheduler thread survives. Curation is additive — when the steward cannot
    plan, the fire falls back to the stored prompt exactly as it did before."""
    _seed_history(client)
    _stub_session(client, monkeypatch, notes=1)

    def _no_plan(self, **kwargs):
        raise RuntimeError("the steward exploded")

    monkeypatch.setattr(MemorySteward, "plan", _no_plan)
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"
    assert _session_task(client, fired["last_session_id"]) == MEMORY_REVIEW_TASK


def test_a_steward_that_cannot_record_still_leaves_the_fire_successful(
    client, monkeypatch
):
    """Bookkeeping is the LAST thing a review does and the least important: a
    recorder that raises must not turn a completed review into a failed fire."""
    _seed_history(client)
    _stub_session(client, monkeypatch, notes=1)

    def _no_record(self, **kwargs):
        raise RuntimeError("the ledger is on fire")

    monkeypatch.setattr(MemorySteward, "record_run", _no_record)
    name = _install(client)

    fired = client.post(f"/schedules/{name}/run").json()
    assert fired["last_status"] == "ok"
    assert fired["last_session_id"]


def test_the_platform_shares_one_steward_with_the_review_card(client):
    """The schedule and the card must read the same ledger.

    The route resolves ``platform.memory_steward`` first and only builds its own
    when that is missing, so attaching it here is what keeps the two lanes on one
    steward — one lazily-created run table, one index probe, one review point.
    """
    platform = client.app.state.platform
    assert isinstance(platform.memory_steward, MemorySteward)
    assert platform.memory_steward.p is platform

    # A run recorded through the PLATFORM's steward is the run the card shows.
    platform.memory_steward.record_run(
        ok=True, cursor="2026-05-05T00:00:00+00:00|4", session_id="s_shared",
        notes_added=3, outcome="Saved 3 notes.",
    )
    stats = client.get("/memory/review").json()["steward"]["stats"]
    assert stats["successful_runs"] == 1
    assert stats["notes_added"] == 3
    assert stats["last_session_id"] == "s_shared"


def test_the_template_still_installs_nothing_on_its_own(client):
    """The schedule stays strictly OPT-IN — a boot never installs it, however
    much the dispatcher now knows about it."""
    assert client.get("/schedules").json()["schedules"] == []
    assert client.get("/memory/review").json()["template"]["installed"] is False
    assert MEMORY_REVIEW_SCHEDULE["kind"] == "task"
