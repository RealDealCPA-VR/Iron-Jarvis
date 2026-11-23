"""Leap 05-a (R1.3): the start hub, a folder and a download start a rule, and
a rule that starts too often pauses itself and says so once a day.

* every old caller (the webhook handlers, an inbound message, a Slack event,
  a calendar event) reaches the router THROUGH the hub, with the same call
  it made before (same method, same arguments), and the hub's waiters see it;
* a file rule fires once per SETTLED file: not for a half-written one (it
  grew between two scans), not twice after a restart; the 21st file of one
  scan waits for the next;
* a finished, verified download starts a download rule of that site, in the
  careful mode (D-05.2); an unverified one or another site's starts nothing;
* the 13th start in an hour pauses the rule (D-05.3), the pause is said once
  a day per rule, a paused file rule's files wait, Resume starts it again.

Through the real app (``create_app``), its store, router and watcher service;
a run's launch is closed (the session row is what is asserted, never a model).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling.knobs import session_options
from iron_jarvis.triggers import starts

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

ROOT = Path(__file__).resolve().parents[1]
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    router = c.app.state.reflex_router
    # The run's launch is closed: the session row is the start under test.
    router._launch = lambda coro, _sid: coro.close()
    yield c, p, router
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _sessions(p) -> list[Session]:
    with session_scope(p.engine) as db:
        from sqlmodel import select

        return list(db.exec(select(Session).order_by(Session.created_at)))


# --------------------------------------------------------------------------- #
# 1. Every old caller reaches the router through the hub, the same way.
# --------------------------------------------------------------------------- #

#: The files that started rules before the hub existed, and the direct router
#: calls they made. None of them may call the router's entry points itself.
CALLERS = (
    "src/iron_jarvis/comm/inbound.py",
    "src/iron_jarvis/comm/slack_socket.py",
    "src/iron_jarvis/triggers/calendar.py",
    "src/iron_jarvis/daemon/app.py",
    "src/iron_jarvis/daemon/routes/comm.py",
    "src/iron_jarvis/webhooks/tools.py",
)
_DIRECT = re.compile(r"\.on_(?:webhook|signal|slack|calendar|email|comm|file|download)\(")


def test_no_old_caller_calls_the_router_past_the_hub():
    for rel in CALLERS:
        text = (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "hub_for(" in text, f"{rel} does not go through the start hub"
        hits = [m.group(0) for m in _DIRECT.finditer(text)]
        assert not hits, f"{rel} calls the router directly: {hits}"


class _Recorder:
    """A router double: records each entry point's call exactly."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def on_webhook(self, slug, body):
        self.calls.append(("on_webhook", slug, body))
        return []

    async def on_signal(self, source, context):
        self.calls.append(("on_signal", source, dict(context)))
        return [{"ok": True, "kind": "session", "rule": "r1", "rule_id": "r1"}]

    async def on_slack(self, *, text, channel="", sender=""):
        self.calls.append(("on_slack", text, channel, sender))
        return []

    async def on_calendar(self, *, title, start="", description=""):
        self.calls.append(("on_calendar", title, start, description))
        return [{"ok": True}]


def _watched(router) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    starts.hub_for(router).add_waiter("*", lambda kind, ctx: seen.append((kind, ctx)))
    return seen


def test_a_calendar_event_reaches_the_router_through_the_hub_unchanged():
    from iron_jarvis.triggers import models as _models  # registers the table
    from iron_jarvis.triggers.calendar import CalendarPoller

    assert _models.CalendarFiredRecord
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    ics = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:e-1\r\nSUMMARY:Standup\r\n"
        f"DTSTART:{soon.strftime('%Y%m%dT%H%M%SZ')}\r\nDESCRIPTION:Daily\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    platform = SimpleNamespace(
        config=SimpleNamespace(calendar_trigger_enabled=True, calendar_lead_minutes=15),
        secrets=SimpleNamespace(get=lambda name: "https://cal.example.invalid/f.ics"),
    )
    rec = _Recorder()
    seen = _watched(rec)
    poller = CalendarPoller(platform, rec, engine, http_get=lambda _u: ics)
    asyncio.run(poller.poll_once())
    assert len(rec.calls) == 1
    name, title, start, description = rec.calls[0]
    assert (name, title, description) == ("on_calendar", "Standup", "Daily") and start
    assert [k for k, _ in seen] == ["calendar"] and seen[0][1]["title"] == "Standup"


def test_an_inbound_message_reaches_the_router_through_the_hub_unchanged(tmp_path):
    from iron_jarvis.comm.base import InboundMessage
    from iron_jarvis.comm.inbound import InboundPoller

    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    rec = _Recorder()
    seen = _watched(rec)
    poller = InboundPoller(p.notifier, c.app.state.orchestrator, p.engine, reflex_router=rec)

    class _Ch:
        reflex_source = "email"
        sent: list = []

        def is_authorized(self, _s):
            return True

        def send(self, message, **kw):
            self.sent.append(message)
            return {"ok": True}

    msg = InboundMessage(sender_id="boss@example.invalid", text="invoice attached", update_id=1,
                         reply_to="boss@example.invalid")
    asyncio.run(poller._handle("mail", _Ch(), msg))
    assert rec.calls == [("on_signal", "email", {
        "text": "invoice attached", "body": "invoice attached",
        "sender": "boss@example.invalid", "from": "boss@example.invalid", "slug": "",
    })]
    assert seen and seen[0][0] == "email"


def test_slack_and_webhook_reach_the_router_through_the_hub_unchanged():
    rec = _Recorder()
    seen = _watched(rec)
    hub = starts.hub_for(rec)
    asyncio.run(hub.signal("slack", fields={"text": "deploy", "channel": "C1", "sender": "U1"}))
    asyncio.run(hub.signal("webhook", fields={"slug": "gh", "body": {"ref": "main"}}))
    asyncio.run(hub.signal("comm", {"text": "hi", "body": "hi", "slug": ""}))
    assert rec.calls == [
        ("on_slack", "deploy", "C1", "U1"),
        ("on_webhook", "gh", {"ref": "main"}),
        ("on_signal", "comm", {"text": "hi", "body": "hi", "slug": ""}),
    ]
    assert [k for k, _ in seen] == ["slack", "webhook", "phone"]
    assert starts.hub_for(rec) is hub, "a router keeps one hub"


def test_the_apps_webhook_handler_starts_its_rule_through_the_hub(desk):
    c, p, router = desk
    p.reflex.add(name="note", source="webhook", match="note", action="session", task_template="Handle: {body}")
    seen = _watched(router)
    out = c.post("/webhooks", json={"slug": "note", "direction": "inbound"})
    assert out.status_code in (200, 201), out.text
    handler = p.inbound_webhooks._handlers["note"]  # noqa: SLF001 — the route's own handler
    ack = asyncio.run(handler({"x": 1}))
    assert ack["fired"] == 1, ack
    assert [k for k, _ in seen] == ["webhook"]


# --------------------------------------------------------------------------- #
# 2. A file rule: once per settled file, never half-written, never twice.
# --------------------------------------------------------------------------- #


def _file_rule(p, folder: Path, **kw):
    rule = p.reflex.add(
        name=kw.pop("name", "Sort intake"), source="file", match="", action="session",
        task_template=kw.pop("task", "Sort {file} by sender"),
        start={"path": str(folder)}, workspace_root=str(folder), **kw,
    )
    starts.ensure_file_watcher(p, rule)
    return rule


def _scan(p, router) -> None:
    """One watcher scan, on its own thread, with the daemon's real callback
    (the hub on this loop)."""

    async def go():
        loop = asyncio.get_running_loop()
        p.sentinels.rule_fire = starts.file_rule_fire(lambda: starts.hub_for(router), lambda: loop)
        await asyncio.to_thread(p.sentinels.poll_once, None, rules_only=True)

    asyncio.run(go())


def _started_on(p) -> list[str]:
    """The file names each rule run started on (its task names the file)."""
    out = []
    for s in _sessions(p):
        if str(s.origin or "").startswith("reflex:"):
            out.append(s.task)
    return out


def test_a_file_rule_fires_once_per_settled_file_never_half_written_never_twice(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Intake"
    folder.mkdir()
    (folder / "old.pdf").write_bytes(b"already here")
    rule = _file_rule(p, folder)
    _scan(p, router)  # the baseline: what is already there is left alone
    assert _started_on(p) == []
    (folder / "a.pdf").write_bytes(b"a" * 10)
    (folder / "big.pdf").write_bytes(b"b" * 10)  # still being written
    _scan(p, router)  # both new, neither settled yet
    assert _started_on(p) == []
    with open(folder / "big.pdf", "ab") as fh:  # it grew: not settled
        fh.write(b"b" * 4096)
    _scan(p, router)
    tasks = _started_on(p)
    assert len(tasks) == 1 and "a.pdf" in tasks[0] and "big.pdf" not in tasks[0], tasks
    _scan(p, router)  # big.pdf is the same on two scans now
    tasks = _started_on(p)
    assert len(tasks) == 2 and "big.pdf" in tasks[1], tasks
    # The rule's run: its folder, its rule's id (the scope of "Always for
    # this rule"), stamped by the rule.
    run = [s for s in _sessions(p) if str(s.origin or "").startswith("reflex:")][0]
    assert run.origin == "reflex:Sort intake"
    assert session_options(run)["rule_id"] == rule.id
    assert Path(run.workspace_path).resolve() == folder.resolve()
    # A restart (a fresh watcher service on the same database) starts nothing again.
    from iron_jarvis.sentinels.service import SentinelService

    p.sentinels = SentinelService(p.engine)
    _scan(p, router)
    assert len(_started_on(p)) == 2
    assert p.reflex.get(rule.id).fire_count == 2


def test_a_file_still_growing_with_the_same_time_does_not_count_until_its_size_holds(desk, tmp_path):
    """A synced or copied file can keep its time while it grows: the size
    must hold on two scans too."""
    c, p, router = desk
    folder = tmp_path / "Synced"
    folder.mkdir()
    _file_rule(p, folder)
    path = str(folder / "big.pdf")
    sizes = iter([None, 10.0, 4096.0, 8192.0, 8192.0])
    handed: list[str] = []

    def scanner(_path, _glob=None):
        size = next(sizes)
        return {} if size is None else {path: [1_700_000_000.0, size]}

    def fire(watcher, items):
        handed.extend(Path(i["path"]).name for i in items)
        return [True] * len(items)

    p.sentinels.rule_fire = fire
    for _ in range(4):
        p.sentinels.poll_once(None, rules_only=True, scanner=scanner)
    assert handed == [], "the same time, a new size each scan: not settled"
    p.sentinels.poll_once(None, rules_only=True, scanner=scanner)
    assert handed == ["big.pdf"]


def test_the_21st_file_of_one_scan_waits_for_the_next(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Busy"
    folder.mkdir()
    rule = _file_rule(p, folder, max_fires_per_hour=60)
    handed: list[list[str]] = []

    def fire(watcher, items):
        assert watcher.rule_id == rule.id
        handed.append([Path(i["path"]).name for i in items])
        return [True] * len(items)

    p.sentinels.rule_fire = fire
    p.sentinels.poll_once(None, rules_only=True)  # baseline
    for n in range(21):
        (folder / f"f{n:02}.txt").write_text(str(n))
    p.sentinels.poll_once(None, rules_only=True)  # new, settling
    p.sentinels.poll_once(None, rules_only=True)  # settled: 20 of 21
    assert [len(h) for h in handed] == [20]
    p.sentinels.poll_once(None, rules_only=True)
    assert [len(h) for h in handed] == [20, 1]
    assert set(handed[0]) | set(handed[1]) == {f"f{n:02}.txt" for n in range(21)}
    p.sentinels.poll_once(None, rules_only=True)
    assert [len(h) for h in handed] == [20, 1], "nothing twice"


def test_a_file_that_is_never_decided_waits_and_is_not_marked_seen(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Wait"
    folder.mkdir()
    _file_rule(p, folder)
    answers = iter([[False], [True]])
    handed: list[str] = []

    def fire(watcher, items):
        handed.extend(Path(i["path"]).name for i in items)
        return next(answers)

    p.sentinels.rule_fire = fire
    p.sentinels.poll_once(None, rules_only=True)
    (folder / "x.txt").write_text("x")
    p.sentinels.poll_once(None, rules_only=True)
    p.sentinels.poll_once(None, rules_only=True)  # not decided (paused): waits
    p.sentinels.poll_once(None, rules_only=True)  # decided now
    p.sentinels.poll_once(None, rules_only=True)
    assert handed == ["x.txt", "x.txt"]


# --------------------------------------------------------------------------- #
# 3. A finished download starts a download rule, carefully.
# --------------------------------------------------------------------------- #


def _download(p, router, payload: dict, *, times: int = 1) -> None:
    from iron_jarvis.core.events import Event

    async def go():
        loop = asyncio.get_running_loop()
        handler = starts.download_handler(lambda: starts.hub_for(router), lambda: loop)
        for _ in range(times):
            await asyncio.to_thread(handler, Event(type="browser.download_completed", payload=payload))
        for _ in range(50):
            await asyncio.sleep(0)  # let the sent start run on this loop
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(go())


def test_a_verified_download_from_its_site_starts_the_rule_carefully(desk, tmp_path):
    c, p, router = desk
    got = tmp_path / "Downloads" / "W-9 form.pdf"
    got.parent.mkdir()
    got.write_bytes(b"%PDF")
    p.reflex.add(name="File tax forms", source="download", match="", action="session",
                 task_template="File {file}", start={"match_site": "irs.gov"})
    # The same download reported twice starts it once.
    _download(p, router, {"download_id": 7, "filename": str(got), "local_path": str(got),
                          "source_url": "https://www.irs.gov/pub/fw9.pdf?token=x"}, times=2)
    runs = [s for s in _sessions(p) if str(s.origin or "") == "reflex:File tax forms"]
    assert len(runs) == 1
    assert runs[0].trust == "low", "D-05.2: work a download starts begins in the careful mode"
    assert "W-9 form.pdf" in runs[0].task and "token" not in runs[0].task
    # Another site, or a download with no verified path, starts nothing.
    _download(p, router, {"filename": str(got), "local_path": str(got), "source_url": "https://example.com/a.pdf"})
    _download(p, router, {"filename": "W-9 form.pdf", "source_url": "https://www.irs.gov/x.pdf"})
    assert len([s for s in _sessions(p) if str(s.origin or "") == "reflex:File tax forms"]) == 1


# --------------------------------------------------------------------------- #
# 4. The hourly limit: the 13th start pauses the rule, said once a day.
# --------------------------------------------------------------------------- #


def _bus_events(p, kind: str) -> list[dict]:
    return [e.payload for e in p.event_bus.history if e.type == kind]


def test_the_thirteenth_start_in_an_hour_pauses_the_rule_and_says_so_once_a_day(desk):
    c, p, router = desk
    clock = {"now": NOON}
    router.clock = lambda: clock["now"]
    rule = p.reflex.add(name="ping", source="webhook", match="ping", action="session", task_template="Note it")
    hub = starts.hub_for(router)

    def fire():
        return asyncio.run(hub.signal("webhook", fields={"slug": "ping", "body": "x"}))[0]

    for n in range(12):
        clock["now"] = NOON + timedelta(minutes=n)
        assert fire()["ok"] is True, n
    clock["now"] = NOON + timedelta(minutes=20)
    held = fire()
    assert held["ok"] is False and held["held"] is True and held["paused"] is True
    assert held["error"] == "Paused: it started 12 times in an hour."
    row = p.reflex.get(rule.id)
    assert row.paused_at is not None and row.fire_count == 12 and not row.last_error
    assert len(_bus_events(p, "reflex.paused")) == 1
    paused = _bus_events(p, "reflex.paused")[0]
    assert paused["rule_id"] == rule.id and paused["reason"] == held["error"]
    # Still paused: held, and not said again.
    clock["now"] = NOON + timedelta(hours=3)
    assert fire()["held"] is True
    assert len(_bus_events(p, "reflex.paused")) == 1
    # Resume: it starts again; a second pause the SAME day is not said again.
    assert c.post(f"/reflex/rules/{rule.id}/resume").status_code == 200
    for n in range(12):
        clock["now"] = NOON + timedelta(hours=3, minutes=n)
        assert fire()["ok"] is True
    assert fire()["held"] is True
    assert len(_bus_events(p, "reflex.paused")) == 1, "at most once a day per rule"
    # The next day it is said again.
    assert c.post(f"/reflex/rules/{rule.id}/resume").status_code == 200
    for n in range(12):
        clock["now"] = NOON + timedelta(days=1, minutes=n)
        assert fire()["ok"] is True
    assert fire()["held"] is True
    assert len(_bus_events(p, "reflex.paused")) == 2
    # Keep it paused: the rule is off, and says nothing more.
    out = c.post(f"/reflex/rules/{rule.id}/keep-paused")
    assert out.status_code == 200 and out.json()["enabled"] is False and out.json()["paused_at"] is None


def test_an_hour_later_it_may_start_again_and_a_press_is_never_held(desk):
    c, p, router = desk
    clock = {"now": NOON}
    router.clock = lambda: clock["now"]
    rule = p.reflex.add(name="tick", source="webhook", match="tick", action="session",
                        task_template="Note it", max_fires_per_hour=2)
    hub = starts.hub_for(router)
    for n in range(2):
        clock["now"] = NOON + timedelta(minutes=n)
        assert asyncio.run(hub.signal("webhook", fields={"slug": "tick", "body": ""}))[0]["ok"]
    clock["now"] = NOON + timedelta(minutes=61, seconds=1)
    assert asyncio.run(hub.signal("webhook", fields={"slug": "tick", "body": ""}))[0]["ok"], "the hour moved on"
    # A person's Run now is neither held nor counted.
    for _ in range(3):
        assert c.post(f"/reflex/rules/{rule.id}/test").json()["ok"] is True
    assert p.reflex.get(rule.id).paused_at is None


def test_a_paused_file_rules_files_wait_and_go_after_resume(desk, tmp_path):
    c, p, router = desk
    folder = tmp_path / "Paused"
    folder.mkdir()
    rule = _file_rule(p, folder, max_fires_per_hour=1)
    _scan(p, router)
    (folder / "one.txt").write_text("1")
    (folder / "two.txt").write_text("2")
    _scan(p, router)
    _scan(p, router)  # one starts, the second finds it at its limit: paused
    assert len(_started_on(p)) == 1
    assert p.reflex.get(rule.id).paused_at is not None
    _scan(p, router)  # still paused: the file waits
    assert len(_started_on(p)) == 1
    assert c.post(f"/reflex/rules/{rule.id}/resume").status_code == 200
    _scan(p, router)
    assert len(_started_on(p)) == 2, "the waiting file went once the rule was resumed"
