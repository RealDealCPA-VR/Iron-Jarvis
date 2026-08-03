"""Skill-learning daemon wiring (v1.135.0) — routes, glue, and the step-4 hook.

Offline throughout. Proves:
  * GET /skills/learning serves the overview the UI binds to (all five keys)
    and is NOT shadowed by agents.py's GET /skills/{name} catch-all;
  * approve/reject return the proposal FLAT with honest 404/409 mapping, and
    approve writes the skill (edited body_md wins);
  * POST /skills/learning/distill is an honest 400 under the mock-only default
    (never a fabricated skill) and reports ``distilled`` with a real adapter;
  * PATCH /skills/learning/settings is a REAL persisted setting (survives a
    daemon reload; None means "leave alone");
  * a completing session reaches skill_learning.observe_session (orchestrator
    step 4) and a raising engine never breaks the run;
  * the SESSION_COMPLETED bus handler schedules a distill sweep only with a
    real provider + the toggle on — and skips silently under mock;
  * the on_proposal callback publishes ``skill.proposal_created``.

External skill roots (~/.claude, ~/.codex) are stubbed to empty so the
registry is builtin + user only — hermetic on a dev box full of real skills.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import Event, EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.skills import framework
from iron_jarvis.skills.learning import _signature
from iron_jarvis.skills.learning_models import (
    SkillCandidateRecord,
    SkillProposalRecord,
)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    # Hermetic registry: builtin + user roots only (Pair A's pattern) — the
    # real ~/.claude on this dev box would leak dozens of skills into
    # slug-uniqueness and shadowing assertions.
    monkeypatch.setattr(framework, "external_skill_roots", lambda: [])
    monkeypatch.setattr(framework, "marketplace_catalog_dirs", lambda home=None: [])
    return TestClient(create_app(str(tmp_path)))


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter)."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text: str):
        self._text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages})
        return LLMResponse(text=self._text)


def _skill_md(
    name: str = "vendor-ledger-reconciliation",
    description: str = "Use when reconciling vendor ledgers against bank statements.",
    body: str = (
        "# Steps\n\n"
        "1. Pull the vendor ledger with read_file.\n"
        "2. Compare balances with run_code.\n"
        "3. Write the reconciliation summary with write_file."
    ),
) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _seed_proposal(platform, *, name: str = "vendor-ledger-reconciliation") -> str:
    rec = SkillProposalRecord(
        kind="create",
        skill_name=name,
        description="Use when reconciling vendor ledgers.",
        body_md=_skill_md(name),
        source_session_ids=json.dumps(["ses_1"]),
        signature=_signature("reconcile vendor ledgers"),
    )
    rid = rec.id
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
    return rid


def _seed_candidate(platform, task: str = "reconcile q3 vendor ledger balances") -> str:
    cand = SkillCandidateRecord(
        session_id="ses_1", task=task, kind="create", signature=_signature(task)
    )
    cid = cand.id
    with session_scope(platform.engine) as db:
        db.add(cand)
        db.commit()
    return cid


def _use_real_adapter(client, text: str) -> _FakeAdapter:
    """Route every provider resolution to a real-adapter stand-in."""
    fake = _FakeAdapter(text)
    client.app.state.platform.providers.get = lambda provider, model=None: fake
    return fake


# --- overview + route-order shadowing ----------------------------------------


def test_overview_serves_all_five_ui_keys(client):
    r = client.get("/skills/learning")
    assert r.status_code == 200
    out = r.json()
    # The UI reads exactly these keys (BINDING CONTRACT ADDENDA 3+4).
    assert out["enabled"] is True
    assert out["auto_approve"] is False
    assert out["proposals"] == []
    assert out["stats"] == []
    assert out["pending_candidates"] == 0
    assert out["pending_proposals"] == 0


def test_learning_path_is_not_shadowed_by_the_skill_catch_all(client):
    """agents.py's GET /skills/{name} would 404 'no such skill' (or serve a
    SkillDetail) if it were registered first — the overview must win."""
    r = client.get("/skills/learning")
    assert r.status_code == 200
    out = r.json()
    assert "pending_candidates" in out and "proposals" in out
    assert "instructions" not in out  # not the SkillDetail shape


def test_the_skill_catch_all_still_serves_real_skills(client):
    """Registering learning first must not break GET /skills/{name} itself."""
    client.post(
        "/skills",
        json={"name": "invoice-chaser", "description": "d", "instructions": "1. Go."},
    )
    r = client.get("/skills/invoice-chaser")
    assert r.status_code == 200
    assert r.json()["instructions"] == "1. Go."
    assert client.get("/skills/definitely-not-a-skill").status_code == 404


def test_overview_lists_proposals_with_status(client):
    p = client.app.state.platform
    rid = _seed_proposal(p)
    out = client.get("/skills/learning").json()
    assert [row["id"] for row in out["proposals"]] == [rid]
    assert out["proposals"][0]["status"] == "pending"
    assert out["pending_proposals"] == 1


def test_proposals_endpoint_wraps_in_proposals_key(client):
    rid = _seed_proposal(client.app.state.platform)
    r = client.get("/skills/proposals")
    assert r.status_code == 200
    assert [row["id"] for row in r.json()["proposals"]] == [rid]


# --- approve / reject ---------------------------------------------------------


def test_approve_writes_the_skill_and_returns_flat(client):
    p = client.app.state.platform
    rid = _seed_proposal(p)
    r = client.post(f"/skills/proposals/{rid}/approve", json={})
    assert r.status_code == 200
    out = r.json()  # FLAT — the POST /sessions convention
    assert out["id"] == rid and out["status"] == "approved"
    assert out["decided_at"] is not None
    md = (
        p.config.home / "skills" / "vendor-ledger-reconciliation" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Compare balances with run_code" in md
    assert p.skills.get("vendor-ledger-reconciliation") is not None  # repopulated


def test_approve_with_edited_body_md_wins(client):
    p = client.app.state.platform
    rid = _seed_proposal(p)
    edited = _skill_md(body="# Steps\n\n1. The user's own edited procedure wins.")
    r = client.post(f"/skills/proposals/{rid}/approve", json={"body_md": edited})
    assert r.status_code == 200
    sk = p.skills.get("vendor-ledger-reconciliation")
    assert sk is not None
    assert "edited procedure wins" in sk.instructions


def test_approve_unknown_is_404_and_decided_is_409(client):
    p = client.app.state.platform
    assert client.post("/skills/proposals/skp_nope/approve", json={}).status_code == 404
    rid = _seed_proposal(p)
    assert client.post(f"/skills/proposals/{rid}/approve", json={}).status_code == 200
    # A double-click must read as "already decided", not "it vanished".
    r = client.post(f"/skills/proposals/{rid}/approve", json={})
    assert r.status_code == 409
    assert "already" in r.json()["detail"]


def test_reject_returns_flat_and_double_reject_is_409(client):
    p = client.app.state.platform
    rid = _seed_proposal(p)
    r = client.post(f"/skills/proposals/{rid}/reject")
    assert r.status_code == 200
    assert r.json()["id"] == rid and r.json()["status"] == "rejected"
    assert client.post(f"/skills/proposals/{rid}/reject").status_code == 409
    assert client.post("/skills/proposals/skp_nope/reject").status_code == 404
    # Rejection never writes to disk.
    assert p.skills.get("vendor-ledger-reconciliation") is None


# --- distill (real provider only) ---------------------------------------------


def test_distill_on_mock_default_refuses_honestly(client):
    _seed_candidate(client.app.state.platform)
    r = client.post("/skills/learning/distill")
    assert r.status_code == 400
    assert "connect a model" in r.json()["detail"]
    # The candidate is untouched — it keeps queueing for a real provider.
    out = client.get("/skills/learning").json()
    assert out["pending_candidates"] == 1 and out["proposals"] == []


def test_distill_with_real_adapter_reports_distilled_count(client):
    p = client.app.state.platform
    _seed_candidate(p)
    fake = _use_real_adapter(client, _skill_md())
    r = client.post("/skills/learning/distill")
    assert r.status_code == 200
    out = r.json()
    assert out["distilled"] == 1  # the UI reads exactly this key
    assert out["reviewed"] == 1 and out["dismissed"] == 0
    assert len(out["proposals"]) == 1
    assert fake.calls  # the model was genuinely consulted
    rows = client.get("/skills/proposals").json()["proposals"]
    assert rows[0]["skill_name"] == "vendor-ledger-reconciliation"
    assert rows[0]["status"] == "pending"  # suggest-only — nothing on disk yet
    assert p.skills.get("vendor-ledger-reconciliation") is None


def test_distill_with_nothing_queued_is_a_clean_zero(client):
    _use_real_adapter(client, _skill_md())
    r = client.post("/skills/learning/distill")
    assert r.status_code == 200
    assert r.json()["distilled"] == 0 and r.json()["reviewed"] == 0


# --- settings (real persisted, v1.127 pattern) --------------------------------


def test_settings_patch_persists_across_a_reload(client, tmp_path, monkeypatch):
    r = client.patch(
        "/skills/learning/settings", json={"enabled": False, "auto_approve": True}
    )
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "auto_approve": True}
    # The reported class of bug is "the checkbox doesn't stick" — prove
    # persistence with a FRESH daemon, not just the in-memory object.
    fresh = TestClient(create_app(str(tmp_path)))
    out = fresh.get("/skills/learning").json()
    assert out["enabled"] is False and out["auto_approve"] is True


def test_settings_none_means_leave_alone(client):
    client.patch("/skills/learning/settings", json={"auto_approve": True})
    r = client.patch("/skills/learning/settings", json={})
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "auto_approve": True}
    # Flipping one switch must not blank the other.
    r = client.patch("/skills/learning/settings", json={"enabled": False})
    assert r.json() == {"enabled": False, "auto_approve": True}


def test_settings_page_sees_both_keys(client):
    """_SETTINGS_KEYS carries both flags so the generic Settings surface
    (validate-on-copy + undo journal) covers them too."""
    settings = client.get("/settings").json()["settings"]
    assert settings["skill_learning_enabled"] is True
    assert settings["skill_learning_auto_approve"] is False


# --- orchestrator step 4 ------------------------------------------------------


def test_a_completing_session_reaches_observe_session(client):
    p = client.app.state.platform
    seen: list[str] = []
    p.skill_learning.observe_session = lambda session: seen.append(
        getattr(session, "id", session)
    )
    r = client.post("/sessions", json={"task": "say hello", "wait": True})
    assert r.status_code == 200
    assert seen == [r.json()["id"]]


def test_a_raising_engine_never_breaks_the_run(client):
    p = client.app.state.platform

    def _boom(session):
        raise RuntimeError("poisoned engine")

    p.skill_learning.observe_session = _boom
    r = client.post("/sessions", json={"task": "say hello", "wait": True})
    assert r.status_code == 200
    assert r.json()["status"] in ("completed", "failed")  # run finalized normally


# --- the SESSION_COMPLETED distill trigger ------------------------------------


def _session_handler(platform):
    """The registered bus handler (proves registration as a side effect)."""
    handlers = [
        h
        for h in platform.event_bus._handlers  # noqa: SLF001
        if getattr(h, "__name__", "") == "_on_skill_session_completed"
    ]
    assert len(handlers) == 1
    return handlers[0]


def _record_distill(platform) -> list:
    calls: list = []

    async def _fake_distill(complete, *, limit=3):
        calls.append(complete)
        return {"reviewed": 0, "proposals": [], "dismissed": 0}

    platform.skill_learning.distill_candidates = _fake_distill
    return calls


async def test_handler_never_distills_under_mock(client):
    """Mock-only install: the sweep must exit before any model call — a
    fabricated skill draft would poison future runs (crystallize's rule)."""
    p = client.app.state.platform
    calls = _record_distill(p)
    handler = _session_handler(p)
    handler(Event(type=EventType.SESSION_COMPLETED, payload={"status": "completed"}))
    await asyncio.sleep(0.05)
    assert calls == []


async def test_handler_distills_with_a_real_provider(client):
    p = client.app.state.platform
    calls = _record_distill(p)
    _use_real_adapter(client, _skill_md())
    handler = _session_handler(p)
    handler(Event(type=EventType.SESSION_COMPLETED, payload={"status": "completed"}))
    await asyncio.sleep(0.05)
    assert len(calls) == 1


async def test_handler_respects_the_enabled_toggle(client):
    p = client.app.state.platform
    calls = _record_distill(p)
    _use_real_adapter(client, _skill_md())
    p.config.skill_learning_enabled = False
    handler = _session_handler(p)
    handler(Event(type=EventType.SESSION_COMPLETED, payload={"status": "completed"}))
    await asyncio.sleep(0.05)
    assert calls == []


async def test_handler_ignores_other_event_types(client):
    p = client.app.state.platform
    calls = _record_distill(p)
    _use_real_adapter(client, _skill_md())
    handler = _session_handler(p)
    handler(Event(type=EventType.TOOL_EXECUTED, payload={}))
    await asyncio.sleep(0.05)
    assert calls == []


# --- the minted-proposal event ------------------------------------------------


async def test_on_proposal_publishes_the_event(client):
    p = client.app.state.platform
    cb = p.skill_learning.on_proposal
    assert cb is not None  # create_app wired the callback
    cb(
        SimpleNamespace(
            id="skp_x", kind="create", skill_name="vendor-recon", status="pending"
        )
    )
    for _ in range(50):  # the publish rides a scheduled task — poll briefly
        await asyncio.sleep(0.01)
        if any(e.type == "skill.proposal_created" for e in p.event_bus.history):
            break
    events = [e for e in p.event_bus.history if e.type == "skill.proposal_created"]
    assert len(events) == 1
    assert events[0].payload == {
        "proposal_id": "skp_x",
        "kind": "create",
        "skill_name": "vendor-recon",
        "auto": False,
    }


async def test_on_proposal_marks_auto_approved(client):
    p = client.app.state.platform
    p.skill_learning.on_proposal(
        SimpleNamespace(
            id="skp_y", kind="refine", skill_name="vendor-recon", status="approved"
        )
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        if any(e.type == "skill.proposal_created" for e in p.event_bus.history):
            break
    events = [e for e in p.event_bus.history if e.type == "skill.proposal_created"]
    assert events and events[-1].payload["auto"] is True


def test_notifications_can_deliver_the_event():
    """The Notifications per-destination routing validates against
    DEFAULT_ALERT_EVENTS — the new event must be a legal checkbox, with copy
    that reads like a suggestion, not a log line."""
    from iron_jarvis.comm.notifier import DEFAULT_ALERT_EVENTS, format_event

    assert "skill.proposal_created" in DEFAULT_ALERT_EVENTS
    line = format_event(
        Event(
            type=EventType.SKILL_PROPOSAL_CREATED,
            payload={"proposal_id": "skp_1", "kind": "create", "skill_name": "vendor-recon"},
        )
    )
    assert line == "New skill suggested: vendor-recon — review it on the Skills page"


def test_notification_copy_says_so_when_auto_approved():
    """auto=True means the skill is ALREADY on disk (the explicit auto-approve
    setting) — telling the user to "review it" would point at an empty review
    queue. The payload comment in create_app promises the alert says so."""
    from iron_jarvis.comm.notifier import format_event

    line = format_event(
        Event(
            type=EventType.SKILL_PROPOSAL_CREATED,
            payload={
                "proposal_id": "skp_1",
                "kind": "create",
                "skill_name": "vendor-recon",
                "auto": True,
            },
        )
    )
    assert line == (
        "New skill added automatically: vendor-recon — see it on the Skills page"
    )


# --- the LIVE dispatch path (bus → to_thread → _live_rearm loop) --------------
#
# In the running daemon the bus dispatches sync handlers via asyncio.to_thread,
# so the handler NEVER sees a running loop in its own thread: the only branch
# that executes in production is RuntimeError → _live_rearm["loop"] →
# run_coroutine_threadsafe. The async tests above exercise the create_task
# branch; these pin the one that actually ships.


def test_handler_reaches_the_lifespan_loop_from_a_foreign_thread(tmp_path, monkeypatch):
    """A session finalizing off-loop (APScheduler task-kind fires, bus
    to_thread dispatch) must still trigger the sweep via the lifespan loop."""
    import time

    monkeypatch.setattr(framework, "external_skill_roots", lambda: [])
    monkeypatch.setattr(framework, "marketplace_catalog_dirs", lambda home=None: [])
    with TestClient(create_app(str(tmp_path))) as client:  # lifespan sets the loop
        p = client.app.state.platform
        calls = _record_distill(p)
        _use_real_adapter(client, _skill_md())
        handler = _session_handler(p)
        # This test thread has NO running loop — exactly the to_thread reality.
        handler(Event(type=EventType.SESSION_COMPLETED, payload={"status": "completed"}))
        for _ in range(100):
            if calls:
                break
            time.sleep(0.02)
        assert len(calls) == 1


def test_handler_degrades_silently_with_no_loop_anywhere(client):
    """Bare create_app (no lifespan → no _live_rearm loop), called from a
    thread with no running loop: the handler must do NOTHING — no raise into
    the bus, no un-awaited coroutine (the sweep coroutine is never created)."""
    import gc
    import warnings

    p = client.app.state.platform
    calls = _record_distill(p)
    handler = _session_handler(p)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        handler(Event(type=EventType.SESSION_COMPLETED, payload={"status": "completed"}))
        gc.collect()
    assert calls == []
    assert not [w for w in caught if "never awaited" in str(w.message)]


def test_on_proposal_with_no_loop_drops_silently(client):
    """The dead/None-loop path must close the publish coroutine — no raise
    into the engine, no un-awaited-coroutine warning, no event."""
    import gc
    import warnings

    p = client.app.state.platform
    cb = p.skill_learning.on_proposal
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cb(
            SimpleNamespace(
                id="skp_z", kind="create", skill_name="quiet", status="pending"
            )
        )
        gc.collect()
    assert not [w for w in caught if "never awaited" in str(w.message)]
    assert not any(e.type == "skill.proposal_created" for e in p.event_bus.history)
