"""v1.446.0 — ease-of-use review items 20, 21, 22: nothing an automation does
is silent where the user looks.

* 20 — a reflex rule that could not START its action (its workflow deleted,
  its remote agent gone or not answering, Emergency stop on, the action
  raised) publishes ``reflex.failed`` with plain words. Once per failure
  EPISODE (the same failure again says nothing new), never for a fire a
  person is watching (``attended``, a ``manual:`` rule, a ``comm`` rule whose
  message gets the reply), and a successful fire stays quiet. It is a
  default phone alert, and "This PC" stays quiet for it (the bell / the tray
  say it — the one-notice fixture holds the three equal).
* 21 — a folder watcher's NEW suggestion publishes ONE ``autonomy.proposed``
  (``source: "sentinel"``, the watcher's name) from the worker thread the
  sweep runs on; a change folded into a suggestion already waiting publishes
  nothing. Never a phone alert.
* 22 — a workflow run whose step was set to "skip if it fails" and failed
  finishes ``completed_with_failures`` (never "completed"), its
  ``workflow.completed`` says so and names the first such step's run, and the
  bell's rule (``notifier.bell_pings``) rings it.
"""

from __future__ import annotations

import iron_jarvis.sentinels.models  # noqa: F401
import iron_jarvis.workflows.models  # noqa: F401

import asyncio
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import (
    DEFAULT_ALERT_EVENTS,
    Notifier,
    bell_attention,
    bell_pings,
    format_event,
    tray_says,
)
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.reflex import ReflexRouter
from iron_jarvis.workflows.engine import (
    PART_FAILED_STATUS,
    TERMINAL_RUN_STATUSES,
    Step,
    WorkflowDef,
    WorkflowEngine,
)


def _failed(platform) -> list[dict]:
    return [dict(e.payload) for e in platform.event_bus.history if e.type == EventType.REFLEX_FAILED]


class _Orch:
    """The router asks only ``emergency_stop_reason`` of a workflow/remote rule."""

    def __init__(self, why: str = "") -> None:
        self.why = why

    def emergency_stop_reason(self, origin: str) -> str:
        return self.why


# --------------------------------------------------------------------------- #
# 20 — a rule that could not start rings, once per episode
# --------------------------------------------------------------------------- #


def test_a_webhook_rule_whose_workflow_is_gone_is_announced_in_plain_words(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    assert client.post("/webhooks", json={"slug": "gh"}).status_code == 200
    client.post(
        "/reflex/rules",
        json={"name": "on-push", "source": "webhook", "match": "gh", "action": "workflow", "target": "nightly-close"},
    )
    client.post("/webhooks/gh", json={"text": "push"})
    [ev] = _failed(platform)
    assert ev["rule"] == "on-push" and ev["action"] == "workflow" and ev["target"] == "nightly-close"
    assert ev["reason"] == "the workflow “nightly-close” it starts is no longer saved"
    # The rule's own row keeps the router's short words (unchanged).
    row = client.get("/reflex/rules").json()["rules"][0]
    assert row["last_error"] == "no saved workflow 'nightly-close'"
    # The same failure again: the row says it, nobody is told twice.
    client.post("/webhooks/gh", json={"text": "push again"})
    assert len(_failed(platform)) == 1


async def test_a_fixed_rule_that_breaks_again_is_a_new_episode(tmp_path):
    platform = build_platform(str(tmp_path))
    router = ReflexRouter(platform, _Orch())
    rule = router.store.add(name="r", source="webhook", match="x", action="workflow", target="gone")
    await router.execute(router.store.get(rule.id), {})
    # A good fire in between clears the row's error …
    router.store.mark_result(rule.id, ok=True, detail="workflow nightly wfrun_1")
    await router.execute(router.store.get(rule.id), {})
    # … so the next failure is news again.
    assert len(_failed(platform)) == 2


async def test_emergency_stop_is_said_once_with_its_own_sentence(tmp_path):
    platform = build_platform(str(tmp_path))
    why = "Emergency stop is on, so rules do not start."
    router = ReflexRouter(platform, _Orch(why))
    rule = router.store.add(name="nightly", source="email", match="", action="workflow", target="w")
    for _ in range(3):
        out = await router.execute(router.store.get(rule.id), {"text": "mail"})
        assert out["ok"] is False
    [ev] = _failed(platform)
    assert ev["reason"] == why


async def test_a_watched_fire_a_comm_rule_and_a_manual_run_are_never_announced(tmp_path):
    platform = build_platform(str(tmp_path))
    router = ReflexRouter(platform, _Orch())
    test_rule = router.store.add(name="t", source="webhook", match="x", action="workflow", target="gone")
    await router.execute(router.store.get(test_rule.id), {}, attended=True)
    comm_rule = router.store.add(name="c", source="comm", match="inv", action="workflow", target="gone")
    await router.execute(router.store.get(comm_rule.id), {"text": "inv"})
    await router.start("workflow", target="gone")
    assert _failed(platform) == []
    # The rows still say it.
    assert router.store.get(test_rule.id).last_error == "no saved workflow 'gone'"


async def test_a_raised_start_error_reads_in_plain_words_never_a_class_name(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    router = ReflexRouter(platform, _Orch())
    rule = router.store.add(name="boom", source="webhook", match="x", action="workflow", target="w")

    def explode(*a, **kw):
        raise KeyError("query")

    monkeypatch.setattr(router, "_run_workflow", explode)
    await router.execute(router.store.get(rule.id), {})
    [ev] = _failed(platform)
    assert "KeyError" not in ev["reason"] and ev["reason"].strip()


async def test_a_successful_fire_stays_quiet(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path))
    router = ReflexRouter(platform, _Orch())
    rule = router.store.add(name="ok", source="webhook", match="x", action="workflow", target="w")
    monkeypatch.setattr(router, "_run_workflow", lambda r, c: {"ok": True, "kind": "workflow", "run_id": "wfr"})
    out = await router.execute(router.store.get(rule.id), {})
    assert out["ok"] is True and _failed(platform) == []


async def test_a_remote_agent_that_is_down_is_announced_after_the_hand_off(tmp_path, monkeypatch):
    from iron_jarvis.agents import remote as remote_mod

    platform = build_platform(str(tmp_path))
    launched: list[Any] = []
    router = ReflexRouter(platform, _Orch(), spawn_bg=lambda sid, coro: launched.append(coro))
    rule = router.store.add(name="hand", source="comm", match="go", action="remote_agent", target="hermes")

    async def run(self, record, task, resolver, **kw):
        return {"ok": False, "result": "", "detail": "request failed: [Errno 111] Connection refused"}

    monkeypatch.setattr(remote_mod.RemoteAgentRegistry, "get", lambda self, name: SimpleNamespace(name=name))
    monkeypatch.setattr(remote_mod.RemoteAgentRegistry, "run", run)
    out = await router.execute(router.store.get(rule.id), {"text": "go"})
    assert out["ok"] is True and _failed(platform) == []  # the start itself worked
    await launched[0]
    # A comm rule's reply said "started" — the late failure is news for it too.
    [ev] = _failed(platform)
    assert ev["reason"] == "the remote agent “hermes” could not be reached"
    assert router.store.get(rule.id).last_error.startswith("remote agent 'hermes' failed")


def test_the_phone_hears_a_failed_rule_and_this_pc_stays_quiet(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: sent.append(message))
    n = Notifier()
    phone = MockChannel()
    n.add_channel("phone", phone)
    n.add_channel("this-pc", DesktopChannel({"type": "desktop"}))
    ev = {
        "type": EventType.REFLEX_FAILED,
        "session_id": None,
        "payload": {"rule": "invoice-run", "reason": "the workflow “invoice-flow” it starts is no longer saved"},
    }
    assert EventType.REFLEX_FAILED in DEFAULT_ALERT_EVENTS
    n.on_event(ev)
    assert phone.sent == ["Your rule invoice-run couldn't start: the workflow “invoice-flow” it starts is no longer saved"]
    assert sent == []
    assert bell_pings(ev) and bell_attention(ev) and tray_says(ev)


# --------------------------------------------------------------------------- #
# 21 — a watcher's new suggestion is told once, quietly
# --------------------------------------------------------------------------- #


def _scanner(state: dict):
    return lambda path, pattern=None: dict(state)


def _proposed(platform) -> list[dict]:
    return [dict(e.payload) for e in platform.event_bus.history if e.type == EventType.AUTONOMY_PROPOSED]


def test_a_watchers_new_suggestion_publishes_one_event_with_its_name(tmp_path):
    platform = build_platform(str(tmp_path))
    svc, intent = platform.sentinels, platform.intent
    svc.add("scans", path="/watch")
    fs: dict[str, float] = {}
    scan = _scanner(fs)
    svc.poll_once(intent, scanner=scan)  # baseline: nothing
    assert _proposed(platform) == []
    fs["/watch/K-1.pdf"] = 1.0
    [created] = svc.poll_once(intent, scanner=scan)
    [ev] = _proposed(platform)
    assert ev["source"] == "sentinel" and ev["sentinel"] == "scans"
    assert ev["proposal_id"] == created.id and "K-1.pdf" in ev["rationale"]
    # A second change folds into the suggestion still waiting: nothing new.
    fs["/watch/W-2.pdf"] = 2.0
    svc.poll_once(intent, scanner=scan)
    assert len(_proposed(platform)) == 1
    # Never a phone alert (suggest-don't-act).
    assert EventType.AUTONOMY_PROPOSED not in DEFAULT_ALERT_EVENTS


async def test_the_sweep_on_its_worker_thread_reaches_a_live_subscriber(tmp_path):
    """The daemon runs the sweep with ``asyncio.to_thread``: the event must
    reach the dashboard's socket queue on the daemon's loop."""
    platform = build_platform(str(tmp_path))
    svc, intent = platform.sentinels, platform.intent
    svc.add("inbox", path="/w")
    fs: dict[str, float] = {}
    scan = _scanner(fs)
    got: list[Any] = []
    ready = asyncio.Event()

    async def listen() -> None:
        stream = platform.event_bus.subscribe()
        ready.set()
        async for ev in stream:
            got.append(ev)
            if ev.type == EventType.AUTONOMY_PROPOSED:
                return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0)
    await ready.wait()
    await asyncio.sleep(0.05)  # the generator registers its queue on first step
    await asyncio.to_thread(svc.poll_once, intent, scanner=scan)
    fs["/w/a.txt"] = 1.0
    await asyncio.to_thread(svc.poll_once, intent, scanner=scan)
    assert [e["sentinel"] for e in _proposed(platform)] == ["inbox"]
    await asyncio.wait_for(listener, 10)
    assert any(g.type == EventType.AUTONOMY_PROPOSED and g.payload["sentinel"] == "inbox" for g in got)


def test_a_sweep_from_a_plain_thread_publishes_too(tmp_path):
    platform = build_platform(str(tmp_path))
    svc, intent = platform.sentinels, platform.intent
    svc.add("t", path="/t")
    fs: dict[str, float] = {}
    scan = _scanner(fs)
    svc.poll_once(intent, scanner=scan)
    fs["/t/x"] = 1.0
    th = threading.Thread(target=svc.poll_once, args=(intent,), kwargs={"scanner": scan})
    th.start()
    th.join(30)
    assert [e["sentinel"] for e in _proposed(platform)] == ["t"]


# --------------------------------------------------------------------------- #
# 22 — a workflow with a skipped failed step says "part failed"
# --------------------------------------------------------------------------- #


def _completed(platform) -> list[dict]:
    return [dict(e.payload) for e in platform.event_bus.history if e.type == EventType.WORKFLOW_COMPLETED]


async def test_a_skipped_failure_finishes_part_failed_and_rings(tmp_path):
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="nightly",
        steps=[
            Step(name="Flaky", kind="tool", tool="no_such_tool", on_failure="skip"),
            Step(name="Tell", kind="notify", message="done"),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == PART_FAILED_STATUS == "completed_with_failures"
    assert PART_FAILED_STATUS in TERMINAL_RUN_STATUSES
    outs = json.loads(rec.outputs_json)
    assert outs["Flaky"]["status"] == "failed" and outs["Tell"]["status"] == "completed"
    [ev] = _completed(platform)
    assert ev["status"] == PART_FAILED_STATUS
    # A tool step has no run of its own: the workflow's row is its one notice.
    assert ev["failed_session"] == ""
    assert ev["failed_steps"] == ["Flaky"]
    event = {"type": EventType.WORKFLOW_COMPLETED, "session_id": None, "payload": ev}
    assert bell_pings(event) and bell_attention(event)
    assert format_event(event) == (
        "Your workflow nightly finished, but part of it failed — "
        "Its step “Flaky” failed. The Workflows page shows why."
    )


async def test_a_clean_run_stays_completed_and_a_halt_stays_failed(tmp_path):
    platform = build_platform(str(tmp_path))
    clean = await WorkflowEngine(platform).run(
        WorkflowDef(name="clean", steps=[Step(name="Tell", kind="notify", message="hi")])
    )
    halted = await WorkflowEngine(platform).run(
        WorkflowDef(name="halt", steps=[Step(name="Boom", kind="tool", tool="no_such_tool")])
    )
    assert clean.status == "completed" and halted.status == "failed"
    statuses = {e["workflow"]: e["status"] for e in _completed(platform)}
    assert statuses == {"clean": "completed", "halt": "failed"}
    done = {"type": EventType.WORKFLOW_COMPLETED, "payload": {"status": "completed"}}
    assert bell_pings(done) is False


@pytest.mark.parametrize("status", ["completed", PART_FAILED_STATUS, "failed", "cancelled"])
def test_every_finished_status_is_terminal(status):
    assert status in TERMINAL_RUN_STATUSES


# --------------------------------------------------------------------------- #
# The readers outside this lane's files (the coordinator's patch): every one
# treats a part-failed run as FINISHED, and a Test press is a watched fire.
# --------------------------------------------------------------------------- #


def test_a_finished_part_failed_run_cannot_be_cancelled_and_is_prunable(tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.workflows.models import WorkflowRunRecord
    from iron_jarvis.workflows.store import PRUNABLE_RUN_STATUSES

    client = TestClient(create_app(str(tmp_path)))
    with session_scope(client.app.state.platform.engine) as db:
        rec = WorkflowRunRecord(workflow_name="w", status=PART_FAILED_STATUS)
        db.add(rec)
        db.commit()
        run_id = rec.id
    r = client.post(f"/workflows/runs/{run_id}/cancel")
    assert r.status_code == 409, r.text
    with session_scope(client.app.state.platform.engine) as db:
        assert db.get(WorkflowRunRecord, run_id).status == PART_FAILED_STATUS
    assert PART_FAILED_STATUS in PRUNABLE_RUN_STATUSES


def test_an_answered_parked_schedule_that_part_failed_reads_part_failed():
    from iron_jarvis.scheduling import run_outcome as ro

    status, detail = ro.workflow_answered(PART_FAILED_STATUS, "Nightly")
    assert status == ro.STATUS_PARTIAL and "part of it failed" in detail


def test_the_reflex_pages_test_press_is_never_announced(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    rule = client.post(
        "/reflex/rules",
        json={"name": "t", "source": "webhook", "match": "x", "action": "workflow", "target": "gone"},
    ).json()
    out = client.post(f"/reflex/rules/{rule['id']}/test").json()
    assert out["ok"] is False
    assert _failed(client.app.state.platform) == []
