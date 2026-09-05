"""v1.231.0 (audit Wave 5, AE6/AE15/AE7) — reflex rules, webhooks and sentinels
fail HONESTLY: on the row, in the ack, in the reply, on the timeline.

Converted from ``tests/_audit_20260904/test_a5_reflex_sentinel.py`` (the four
repros) and extended with the surfaces the fix added.

  * AE6  — a rule whose workflow was deleted writes ``last_error`` on its row
           (and a later good fire clears it + writes ``last_result``); the
           webhook ack answers ``{fired, failed: [{rule, error}]}`` and never
           counts a failed start as a fire; a phone keyword rule that matched
           but could not start replies ``Rule "<name>" could not start: …``
           and does NOT fall through to a free-form session.
  * AE15 — a bad signature answers 401 and an unknown slug 404 (body keeps
           ``ok: false``) and publishes ``webhook.rejected {slug, reason}``.
  * AE7  — ``default_scanner`` raises ``FileNotFoundError`` for a vanished
           root; ``check`` keeps the old baseline and stamps ``last_error``
           ("root unreachable since <t>", stable across ticks, cleared by the
           next good scan), so a replug proposes nothing for untouched files.
"""

from __future__ import annotations

import iron_jarvis.sentinels.models  # noqa: F401
import iron_jarvis.workflows.models  # noqa: F401

from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.notifier import Notifier
from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.reflex import ReflexRouter
from iron_jarvis.reflex.router import summarize_fires
from iron_jarvis.sentinels.service import SentinelService
from iron_jarvis.sentinels.watcher import default_scanner


# --------------------------------------------------------------------------- #
# AE7 — sentinel: the watched root vanishes.
# --------------------------------------------------------------------------- #


def test_default_scanner_raises_for_a_missing_root_but_not_for_a_glob(tmp_path):
    with pytest.raises(FileNotFoundError):
        default_scanner(str(tmp_path / "gone"))
    with pytest.raises(FileNotFoundError):
        default_scanner(str(tmp_path / "gone"), "**/*.md")
    # A path that is itself a glob can legitimately match nothing.
    assert default_scanner(str(tmp_path / "*.log")) == {}


def test_sentinel_root_vanishing_keeps_the_baseline_and_a_replug_refires_nothing(tmp_path):
    platform = build_platform(str(tmp_path))
    root = tmp_path / "usb"
    root.mkdir()
    (root / "1099-B Schwab.pdf").write_text("a")
    (root / "W-2 Acme.pdf").write_text("b")
    svc = SentinelService(platform.engine)  # the REAL stat scanner
    svc.add("intake", path=str(root), task="triage new intake scans")
    assert svc.check("intake") == []  # baseline
    assert svc.check("intake") == []  # steady state
    assert len(svc.get("intake").decoded_state().get("seen", {})) == 2
    assert svc.get("intake").last_error is None

    parked = tmp_path / "unplugged"
    root.rename(parked)  # the USB stick / OneDrive folder is gone
    assert svc.check("intake") == []
    rec = svc.get("intake")
    assert len(rec.decoded_state().get("seen", {})) == 2, (
        "baseline wiped by the missing root: " + rec.last_state_json
    )
    assert rec.last_error and rec.last_error.startswith("root unreachable since "), rec.last_error
    since = rec.last_error
    assert rec.last_checked_at is not None  # the tick still stamps the check

    assert svc.check("intake") == []  # a second tick keeps the FIRST failure's time
    assert svc.get("intake").last_error == since

    parked.rename(root)  # plugged back in
    assert svc.check("intake") == [], "replug re-fired for untouched files"
    assert svc.get("intake").last_error is None  # a good scan clears the note
    assert svc.poll_once(platform.intent) == []

    # A file that really is new after the replug still fires.
    (root / "1098 Mortgage.pdf").write_text("c")
    changed = svc.check("intake")
    assert [c["change"] for c in changed] == ["new"]


def test_sentinel_may_be_added_for_a_root_that_is_not_there_yet(tmp_path):
    platform = build_platform(str(tmp_path))
    svc = SentinelService(platform.engine)
    later = tmp_path / "later"
    svc.add("later", path=str(later))  # no ValueError: the row will say so
    assert svc.check("later") == []
    rec = svc.get("later")
    assert rec.last_checked_at is None  # baseline NOT consumed
    assert rec.last_error and rec.last_error.startswith("root unreachable since ")
    later.mkdir()
    (later / "a.txt").write_text("x")
    assert svc.check("later") == []  # first real scan = baseline, fires nothing
    assert svc.get("later").last_error is None


def test_sentinels_route_carries_last_error(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    root = tmp_path / "watched"
    root.mkdir()
    assert client.post("/sentinels", json={"name": "w", "path": str(root)}).status_code == 200
    platform.sentinels.check("w")
    root.rename(tmp_path / "gone")
    platform.sentinels.check("w")
    row = next(s for s in client.get("/sentinels").json()["sentinels"] if s["name"] == "w")
    assert row["last_error"].startswith("root unreachable since "), row


# --------------------------------------------------------------------------- #
# AE6 — reflex rule → deleted workflow.
# --------------------------------------------------------------------------- #


def test_summarize_fires_counts_only_started_rules():
    fired = [
        {"rule": "a", "ok": True, "kind": "workflow"},
        {"rule": "b", "ok": False, "error": "no saved workflow 'x'"},
    ]
    assert summarize_fires(fired) == {
        "fired": 1,
        "failed": [{"rule": "b", "error": "no saved workflow 'x'"}],
        "reflexes_fired": 1,
    }
    assert summarize_fires([]) == {"fired": 0, "failed": [], "reflexes_fired": 0}


def test_webhook_does_not_count_a_failed_reflex_as_fired_and_the_row_says_why(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.post("/webhooks", json={"slug": "gh"}).status_code == 200
    rule = client.post(
        "/reflex/rules",
        json={
            "name": "on-push",
            "source": "webhook",
            "match": "gh",
            "action": "workflow",
            "target": "nightly-close",  # deleted since
        },
    ).json()
    r = client.post("/webhooks/gh", json={"text": "push"})
    assert r.status_code == 200
    body = r.json()
    events = [e for e in client.app.state.platform.event_bus.history if e.type == "reflex.fired"]
    assert events and events[-1].payload["ok"] is False
    assert "no saved workflow" in events[-1].payload["detail"]
    # The ack must not claim a fire that failed — and must NAME the failure.
    assert body["ok"] is True
    assert body["fired"] == 0 and body["reflexes_fired"] == 0, body
    assert body["failed"] == [{"rule": "on-push", "error": "no saved workflow 'nightly-close'"}]
    # And the rule the user looks at must carry the failure, uncounted.
    row = next(x for x in client.get("/reflex/rules").json()["rules"] if x["id"] == rule["id"])
    assert row["last_error"] == "no saved workflow 'nightly-close'"
    assert row["fire_count"] == 0 and row["last_fired_at"] is None


def test_a_later_good_fire_clears_last_error_and_writes_last_result(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    rule = platform.reflex.add(
        name="ping", source="comm", match="ping", action="workflow", target="missing"
    )
    platform.reflex.mark_result(rule.id, ok=False, detail="no saved workflow 'missing'")
    assert platform.reflex.get(rule.id).last_error == "no saved workflow 'missing'"
    platform.reflex.mark_result(rule.id, ok=True, detail="workflow nightly wfrun_1")
    row = platform.reflex.get(rule.id)
    assert row.last_error is None
    assert row.last_result == "workflow nightly wfrun_1"
    assert row.fire_count == 1 and row.last_fired_at is not None


class _Channel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def is_authorized(self, sender_id: Any) -> bool:
        return True

    def inbound_enabled(self) -> bool:
        return True

    def has_credentials(self) -> bool:
        return True

    def send(self, message: str, **kw: Any) -> dict[str, Any]:
        self.sent.append((message, kw))
        return {"ok": True}


async def test_comm_keyword_rule_whose_workflow_was_deleted_tells_the_sender(tmp_path):
    platform = build_platform(str(tmp_path))
    orch = Orchestrator(platform)
    router = ReflexRouter(platform, orch)
    router.store.add(
        name="invoice-run",
        source="comm",
        match="invoice",
        action="workflow",
        target="invoice-flow",  # deleted
    )
    ch = _Channel()
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(
        notifier, orch, platform.engine, event_bus=platform.event_bus, reflex_router=router
    )
    res = await poller._handle(
        "tg", ch, InboundMessage(sender_id="1", text="run the invoice flow please", update_id=1, reply_to="1")
    )
    # No free-form session: the broken rule is reported, not worked around.
    assert orch.list_sessions() == [], f"a free-form session was spawned (status={res.get('status')})"
    assert res["status"] == "reflex_failed" and res["fired"] == 0
    assert res["failed"] == [{"rule": "invoice-run", "error": "no saved workflow 'invoice-flow'"}]
    assert any(
        'Rule "invoice-run" could not start: no saved workflow \'invoice-flow\'' in m
        for m, _ in ch.sent
    ), ch.sent


# --------------------------------------------------------------------------- #
# AE15 — webhook refused: real status + webhook.rejected on the timeline.
# --------------------------------------------------------------------------- #


def test_webhook_with_a_bad_signature_is_refused_with_a_401_and_an_event(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.secrets.set("gh_secret", "s3cr3t")
    assert client.post("/webhooks", json={"slug": "signed", "secret_name": "gh_secret"}).status_code == 200
    r = client.post(
        "/webhooks/signed", json={"text": "x"}, headers={"X-IronJarvis-Signature": "deadbeef"}
    )
    assert r.status_code == 401, (r.status_code, r.json())
    assert r.json()["ok"] is False and "signature" in r.json()["error"]
    assert not any(e.type == "webhook.received" for e in platform.event_bus.history)
    rejected = [e for e in platform.event_bus.history if e.type == EventType.WEBHOOK_REJECTED]
    assert rejected and rejected[-1].payload == {"slug": "signed", "reason": "bad_signature"}


def test_unknown_webhook_slug_is_a_404_with_an_event(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    r = client.post("/webhooks/nobody-home", json={"text": "x"})
    assert r.status_code == 404, (r.status_code, r.json())
    assert r.json()["ok"] is False
    rejected = [e for e in platform.event_bus.history if e.type == "webhook.rejected"]
    assert rejected and rejected[-1].payload == {"slug": "nobody-home", "reason": "unknown_slug"}


def test_a_good_signature_still_answers_200(tmp_path):
    from iron_jarvis.webhooks.security import sign

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.secrets.set("gh_secret", "s3cr3t")
    assert client.post("/webhooks", json={"slug": "signed", "secret_name": "gh_secret"}).status_code == 200
    raw = b'{"text": "x"}'  # the route verifies the RAW bytes it received
    r = client.post(
        "/webhooks/signed",
        content=raw,
        headers={
            "content-type": "application/json",
            "X-IronJarvis-Signature": sign(raw, "s3cr3t"),
        },
    )
    assert r.status_code == 200 and r.json()["ok"] is True, r.json()
    assert not any(e.type == "webhook.rejected" for e in platform.event_bus.history)
