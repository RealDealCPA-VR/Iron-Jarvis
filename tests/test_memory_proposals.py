"""Memory housekeeping proposals (v1.143.0) — the steward's suggest-only lane.

Offline throughout. The one principle under test, from every angle:

    the steward may ADD memory freely; every REVISION waits for a click.

Proves:
  * the record/store lifecycle — mint -> pending -> approved / dismissed, and
    a DISMISSED signature is SUPPRESSED so the same suggestion never nags;
  * approve APPLIES the change through real note files, RECORDS what it did,
    and journals a TX-01 inverse so ``POST /undo/{id}`` puts the notes back;
  * approve on a base this daemon cannot rewrite (no ``.dir`` — Notion / MCP /
    cloud) is an HONEST error that leaves the suggestion pending, never a
    silent "approved";
  * a payload can never escape its memory base (``../`` is refused);
  * route shapes + 404/409 mapping, and ``/memory/review`` is NOT shadowed by
    learning.py's ``GET /memory/{layer}/{key}`` catch-all;
  * ``POST /memory/review/run`` is an honest 400 under the mock-only default
    and opens a REAL session once a real adapter exists;
  * the weekly schedule template exists, is wired to the ``task`` kind, and is
    strictly OPT-IN — booting the daemon creates no schedule;
  * every read path never raises (a broken engine degrades to empty);
  * ``memory_propose`` — the ONE seam that makes any of this reachable in
    production — is registered, armed, advertised to the agent type the review
    session actually runs as, and files rows a REAL agent session can raise and
    the user can then approve.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm.base import MarkdownDirConnector
from iron_jarvis.ltm.manager import LongTermMemory
from iron_jarvis.memory.proposals import (
    KINDS,
    MemoryProposalRecord,
    MemoryProposalStore,
    signature_for,
)
from iron_jarvis.memory.proposal_tools import MemoryProposeTool, memory_proposal_tools
from iron_jarvis.templates import (
    MEMORY_REVIEW_SCHEDULE,
    MEMORY_REVIEW_TASK,
    SCHEDULE_TEMPLATES,
    schedule_template,
)


# --- fixtures ---------------------------------------------------------------


class _CloudBase:
    """A search/append-only base (Notion / MCP / HTTP-RAG shape): no ``.dir``."""

    name = "notion"

    def search(self, query, k=5):
        return []

    def append(self, title, content):
        return "notion://page"


@pytest.fixture
def notes(tmp_path) -> Path:
    d = tmp_path / "base"
    d.mkdir()
    (d / "alpha.md").write_text("# Alpha\n\nThe original fact.\n", encoding="utf-8")
    (d / "alpha-copy.md").write_text("# Alpha copy\n\nThe original fact.\n", encoding="utf-8")
    (d / "old.md").write_text("# Old\n\nOut of date.\n", encoding="utf-8")
    return d


@pytest.fixture
def ltm(notes) -> LongTermMemory:
    manager = LongTermMemory()
    connector = MarkdownDirConnector(notes)
    connector.name = "brain"
    manager.register(connector)
    manager.register(_CloudBase())
    return manager


@pytest.fixture
def store(tmp_path, ltm) -> MemoryProposalStore:
    engine = make_engine(tmp_path / "proposals.db")
    init_db(engine)  # the tool ledger the undo journal writes into lives here too
    return MemoryProposalStore(engine, ltm=ltm, home=str(tmp_path / "home"))


def _mint(store, **over):
    payload = {
        "kind": "duplicate",
        "base": "brain",
        "refs": ["alpha", "alpha-copy"],
        "rationale": "Both notes say the same thing.",
        "suggested_action": "Keep “alpha” and remove the copy.",
        "payload": {"remove_refs": ["alpha-copy"]},
    }
    payload.update(over)
    return store.create(**payload)


# --- records + lifecycle ----------------------------------------------------


def test_the_four_kinds_are_all_revisions(store):
    # Additions are NOT proposals — they are ltm_append calls. Every kind here
    # deletes or rewrites something, which is why every one needs a click.
    assert set(KINDS) == {"duplicate", "stale", "contradiction", "merge"}
    with pytest.raises(ValueError):
        _mint(store, kind="addition")
    with pytest.raises(ValueError):
        _mint(store, refs=[])


def test_mint_then_list_pending(store):
    record = _mint(store)
    assert isinstance(record, MemoryProposalRecord)
    assert record.status == "pending"
    assert record.decided_at is None
    assert record.decoded_refs() == ["alpha", "alpha-copy"]
    assert record.decoded_payload()["remove_refs"] == ["alpha-copy"]
    assert record.signature == signature_for("duplicate", "brain", ["alpha", "alpha-copy"])
    assert [r.id for r in store.list()] == [record.id]
    assert [r.id for r in store.list(status="pending")] == [record.id]
    assert store.stats() == {
        "pending": 1,
        "approved": 0,
        "dismissed": 0,
        "total": 1,
        "by_kind": {"duplicate": 1},
    }


def test_signature_ignores_ref_order(store):
    assert signature_for("merge", "brain", ["b", "a"]) == signature_for(
        "merge", "brain", ["a", "b"]
    )


def test_a_pending_signature_is_not_raised_twice(store):
    assert _mint(store) is not None
    assert _mint(store, refs=["alpha-copy", "alpha"]) is None  # same set, same signature
    assert len(store.list()) == 1


def test_dismiss_suppresses_the_same_suggestion_for_good(store):
    record = _mint(store)
    dismissed = store.dismiss(record.id)
    assert dismissed.status == "dismissed"
    assert dismissed.decided_at is not None
    assert store.suppressed(record.signature) is True
    # "not this" sticks: the steward may not re-raise it on the next run.
    assert _mint(store) is None
    assert store.stats()["pending"] == 0
    assert store.stats()["dismissed"] == 1


def test_dismiss_is_honest_about_unknown_and_decided(store):
    with pytest.raises(ValueError, match="no such proposal"):
        store.dismiss("mprop_nope")
    record = _mint(store)
    store.dismiss(record.id)
    with pytest.raises(ValueError, match="already dismissed"):
        store.dismiss(record.id)


# --- approve: what it actually does -----------------------------------------


def test_approve_duplicate_removes_the_copy_and_records_it(store, notes):
    record = _mint(store)
    decided, result = store.approve(record.id)

    assert result.ok is True
    assert decided.status == "approved"
    assert decided.decided_at is not None
    assert not (notes / "alpha-copy.md").exists()  # the copy is gone
    assert (notes / "alpha.md").exists()  # the survivor is untouched
    applied = decided.decoded_applied()
    assert applied["ok"] is True
    assert applied["changed"] == ["Removed “alpha-copy”"]
    assert applied["undoable"] is True
    assert len(applied["undo_ids"]) == 1


def test_approve_merge_writes_the_surviving_text_and_removes_the_rest(store, notes):
    record = _mint(
        store,
        kind="merge",
        refs=["alpha", "alpha-copy"],
        payload={
            "survivor_ref": "alpha",
            "text": "# Alpha\n\nThe merged fact.",
            "remove_refs": ["alpha-copy"],
        },
    )
    _decided, result = store.approve(record.id)
    assert result.ok is True
    assert (notes / "alpha.md").read_text(encoding="utf-8") == "# Alpha\n\nThe merged fact.\n"
    assert not (notes / "alpha-copy.md").exists()
    assert result.changed == ["Rewrote “alpha”", "Removed “alpha-copy”"]


def test_approve_stale_can_simply_remove_the_note(store, notes):
    record = _mint(
        store,
        kind="stale",
        refs=["old"],
        rationale="Superseded months ago.",
        payload={"remove_refs": ["old"]},
    )
    _decided, result = store.approve(record.id)
    assert result.ok is True
    assert not (notes / "old.md").exists()


def test_approve_contradiction_rewrites_in_place(store, notes):
    record = _mint(
        store,
        kind="contradiction",
        refs=["old"],
        payload={"survivor_ref": "old", "text": "# Old\n\nThe corrected fact."},
    )
    _decided, result = store.approve(record.id)
    assert result.ok is True
    assert "corrected" in (notes / "old.md").read_text(encoding="utf-8")


def test_approve_creates_the_survivor_when_it_does_not_exist_yet(store, notes):
    record = _mint(
        store,
        kind="merge",
        refs=["alpha", "alpha-copy"],
        payload={
            "survivor_ref": "Combined Alpha",
            "text": "# Combined Alpha\n\nBoth facts.",
            "remove_refs": ["alpha", "alpha-copy"],
        },
    )
    _decided, result = store.approve(record.id)
    assert result.ok is True
    assert (notes / "combined-alpha.md").is_file()
    assert not (notes / "alpha.md").exists()


def test_approve_never_removes_the_note_it_just_wrote(store, notes):
    # A model that lists the survivor in remove_refs must not delete its own
    # output — the merged note has to survive.
    record = _mint(
        store,
        kind="merge",
        refs=["alpha"],
        payload={
            "survivor_ref": "alpha",
            "text": "# Alpha\n\nMerged.",
            "remove_refs": ["alpha", "alpha-copy"],
        },
    )
    _decided, result = store.approve(record.id)
    assert result.ok is True
    assert (notes / "alpha.md").read_text(encoding="utf-8") == "# Alpha\n\nMerged.\n"
    assert not (notes / "alpha-copy.md").exists()


def test_approve_is_honest_and_leaves_it_pending_on_an_unsupported_base(store, notes):
    record = _mint(store, base="notion", refs=["some page"])
    decided, result = store.approve(record.id)
    assert result.ok is False
    assert "outside this computer" in result.error
    assert decided.status == "pending"  # never a silent approval
    assert (notes / "alpha-copy.md").exists()  # and nothing was touched
    # It stays reviewable — the user can act on it in Notion and dismiss.
    assert [r.id for r in store.list(status="pending")] == [record.id]


def test_approve_is_honest_when_the_base_is_not_connected(store):
    record = _mint(store, base="ghost", refs=["a"])
    _decided, result = store.approve(record.id)
    assert result.ok is False
    assert "isn’t connected" in result.error


def test_approve_refuses_an_empty_payload(store):
    record = _mint(store, kind="stale", refs=["old"], payload={})
    _decided, result = store.approve(record.id)
    assert result.ok is False
    assert "nothing to apply" in result.error


def test_a_payload_can_never_escape_the_memory_base(store, tmp_path, notes):
    outside = tmp_path / "secret.md"
    outside.write_text("do not touch", encoding="utf-8")
    record = _mint(
        store,
        kind="stale",
        refs=["secret"],
        payload={"remove_refs": [str(outside)]},
    )
    _decided, result = store.approve(record.id)
    assert result.ok is False
    assert "outside this memory base" in result.error
    assert outside.exists()


def test_approve_is_honest_about_unknown_and_decided(store):
    with pytest.raises(ValueError, match="no such proposal"):
        store.approve("mprop_nope")
    record = _mint(store)
    store.approve(record.id)
    with pytest.raises(ValueError, match="already approved"):
        store.approve(record.id)


# --- undo: the promise the UI makes -----------------------------------------


def test_approving_journals_a_real_tx01_inverse(store, notes):
    record = _mint(
        store,
        kind="merge",
        refs=["alpha", "alpha-copy"],
        payload={
            "survivor_ref": "alpha",
            "text": "# Alpha\n\nMerged.",
            "remove_refs": ["alpha-copy"],
        },
    )
    _decided, result = store.approve(record.id)
    assert len(result.undo_ids) == 2

    with session_scope(store.engine) as db:
        for action_id in result.undo_ids:
            inv = db.get(ToolInvocation, action_id)
            journal = db.get(UndoJournal, action_id)
            assert inv is not None and journal is not None
            # The inverse rides the SAME tool POST /undo replays.
            assert inv.tool == "ltm_append"
            assert inv.reversibility == "reversible"
            assert journal.reversible is True
            assert journal.kind in ("memory_restore", "memory_delete_file")
            assert json.loads(journal.pre_inline)["mode"] == "text"


def test_a_base_without_files_promises_no_undo(store):
    assert store.describe_base("brain") == {
        "can_apply": True,
        "undoable": True,
        "note": "Applying this is undoable — it lands on the Time travel list.",
    }
    cloud = store.describe_base("notion")
    assert cloud["can_apply"] is False
    assert cloud["undoable"] is False


async def test_the_journaled_inverse_actually_restores_the_notes(store, notes, tmp_path):
    """The undo promise, end to end: replay the captured inverse through the
    real ``ltm_append`` tool and the notes come back byte-identical."""
    from iron_jarvis.ltm.tools import LTMAppendTool
    from iron_jarvis.tools.base import ToolContext

    before_alpha = (notes / "alpha.md").read_text(encoding="utf-8")
    before_copy = (notes / "alpha-copy.md").read_text(encoding="utf-8")
    record = _mint(
        store,
        kind="merge",
        refs=["alpha", "alpha-copy"],
        payload={
            "survivor_ref": "alpha",
            "text": "# Alpha\n\nMerged.",
            "remove_refs": ["alpha-copy"],
        },
    )
    _decided, result = store.approve(record.id)
    assert result.ok is True

    tool = LTMAppendTool(store.ltm)
    ctx = ToolContext(
        workspace=tmp_path / "ws",
        session_id="memory-review",
        agent_run_id="",
        # revert only reads ``config.home`` (to drop the pre-image blob).
        config=SimpleNamespace(home=Path(store.home)),
        event_bus=None,
        engine=store.engine,
    )
    with session_scope(store.engine) as db:
        journals = [db.get(UndoJournal, a) for a in result.undo_ids]
    for journal in journals:
        undo = {
            "kind": journal.kind,
            "reversible": True,
            "pre_ref": journal.pre_ref,
            "pre_inline": journal.pre_inline,
            "pre_sha256": journal.pre_sha256,
            "post_sha256": journal.post_sha256,
        }
        out = await tool.revert(undo, ctx)
        assert out.ok, out.error

    assert (notes / "alpha.md").read_text(encoding="utf-8") == before_alpha
    assert (notes / "alpha-copy.md").read_text(encoding="utf-8") == before_copy


# --- never-raise ------------------------------------------------------------


def test_reads_never_raise_on_a_broken_engine(tmp_path):
    broken = MemoryProposalStore(SimpleNamespace(), ltm=None, home=None)
    assert broken.list() == []
    assert broken.get("x") is None
    assert broken.suppressed("sig") is False
    assert broken.stats()["pending"] == 0
    # describe_base with no ltm is an honest "can't", not an exception.
    assert broken.describe_base("brain")["can_apply"] is False


def test_create_returns_none_instead_of_raising_on_a_broken_engine():
    broken = MemoryProposalStore(SimpleNamespace())
    assert broken.create(kind="stale", base="brain", refs=["a"]) is None


# --- the schedule template --------------------------------------------------


def test_the_weekly_template_is_wired_to_a_real_agent_session():
    assert MEMORY_REVIEW_SCHEDULE in SCHEDULE_TEMPLATES
    assert MEMORY_REVIEW_SCHEDULE["label"] == "Memory review — weekly"
    # kind "task" IS the v1.119 path: a fire opens a real agent session.
    assert MEMORY_REVIEW_SCHEDULE["kind"] == "task"
    assert MEMORY_REVIEW_SCHEDULE["cron"] == "0 9 * * 1"
    assert MEMORY_REVIEW_SCHEDULE["task"] == MEMORY_REVIEW_TASK
    assert schedule_template("memory-review-weekly") is MEMORY_REVIEW_SCHEDULE
    assert schedule_template("nope") is None


def test_the_template_prompt_forbids_unapproved_deletion():
    lowered = MEMORY_REVIEW_TASK.lower()
    assert "ltm_append" in lowered
    assert "never delete or rewrite an existing note yourself" in lowered
    for kind in KINDS:
        assert kind in lowered


def test_the_template_is_opt_in_no_schedule_is_created_on_boot(tmp_path, client):
    # Booting the daemon must not install it — a steward that scheduled itself
    # would be exactly the autonomy this feature refuses.
    names = [s["name"] for s in client.get("/schedules").json()["schedules"]]
    assert MEMORY_REVIEW_SCHEDULE["name"] not in names
    assert client.get("/memory/review").json()["template"]["installed"] is False


def test_seeding_starter_prompts_never_creates_a_schedule(tmp_path):
    from iron_jarvis.templates import TemplateStore

    engine = make_engine(tmp_path / "t.db")
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(engine)
    seeded = TemplateStore(engine).seed_starters()
    names = [t.name for t in TemplateStore(engine).list()]
    assert seeded == 3
    assert MEMORY_REVIEW_SCHEDULE["label"] not in names


def test_installing_the_template_is_one_ordinary_schedule_post(client):
    template = client.get("/memory/review").json()["template"]
    body = {
        "name": template["name"],
        "cron": template["cron"],
        "kind": template["kind"],
        "payload": {"task": template["task"]},
    }
    assert client.post("/schedules", json=body).status_code == 200
    assert client.get("/memory/review").json()["template"]["installed"] is True


# --- routes -----------------------------------------------------------------


@pytest.fixture
def client(tmp_path) -> TestClient:
    """A real daemon whose built-in ``brain`` base holds the fixture notes."""
    brain = Path(tmp_path) / ".ironjarvis" / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    (brain / "alpha.md").write_text("# Alpha\n\nThe original fact.\n", encoding="utf-8")
    (brain / "alpha-copy.md").write_text("# Alpha copy\n\nThe original fact.\n", encoding="utf-8")
    (brain / "old.md").write_text("# Old\n\nOut of date.\n", encoding="utf-8")
    return TestClient(create_app(str(tmp_path)))


def _daemon_store(client) -> MemoryProposalStore:
    """The daemon's own store, over the daemon's own engine + bases."""
    platform = client.app.state.platform
    return MemoryProposalStore(
        platform.engine, ltm=platform.ltm, home=platform.config.home
    )


def _file_one(client, **over) -> str:
    record = _mint(_daemon_store(client), **over)
    assert record is not None
    return record.id


def test_the_overview_serves_everything_the_card_binds_to(client):
    body = client.get("/memory/review").json()
    assert set(body) == {"proposals", "pending", "stats", "steward", "template"}
    assert body["proposals"] == []
    assert body["pending"] == 0
    assert body["stats"]["pending"] == 0
    assert body["steward"]["available"] in (True, False)
    assert body["template"]["name"] == "memory-review-weekly"


def test_the_review_route_is_not_shadowed_by_the_memory_catch_all(client):
    # learning.py owns GET /memory/{layer}/{key}. If ordering ever regressed,
    # this would 404/500 as "no such memory key" instead of serving the card.
    res = client.get("/memory/review")
    assert res.status_code == 200
    assert "proposals" in res.json()


def test_proposals_come_back_flat_with_the_honesty_fields(client, tmp_path):
    pid = _file_one(client)
    body = client.get("/memory/review").json()
    assert body["pending"] == 1
    view = body["proposals"][0]
    assert view["id"] == pid
    assert view["status"] == "pending"
    assert view["kind"] == "duplicate"
    assert view["base"] == "brain"
    assert view["refs"] == ["alpha", "alpha-copy"]
    assert view["rationale"]
    assert view["removes"] == 1
    assert view["rewrites"] is False
    assert view["can_apply"] is True
    assert view["undoable"] is True
    assert view["base_note"]


def test_approve_and_dismiss_routes(client):
    pid = _file_one(client)
    res = client.post(f"/memory/review/{pid}/approve")
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    assert res.json()["applied"]["ok"] is True
    # A double-click is 409 ("already approved"), never a 404 "it vanished".
    assert client.post(f"/memory/review/{pid}/approve").status_code == 409

    pid2 = _file_one(client, kind="stale", refs=["old"], payload={"remove_refs": ["old"]})
    res = client.post(f"/memory/review/{pid2}/dismiss")
    assert res.status_code == 200
    assert res.json()["status"] == "dismissed"
    assert client.post(f"/memory/review/{pid2}/dismiss").status_code == 409


def test_unknown_proposal_is_404_on_both_decisions(client):
    assert client.post("/memory/review/mprop_nope/approve").status_code == 404
    assert client.post("/memory/review/mprop_nope/dismiss").status_code == 404


def test_every_review_route_resolves_to_its_own_handler(client):
    """Ordering, pinned on the LIVE app rather than on the registration order.

    ``/memory/review`` sits under the same prefix as learning.py's
    ``GET /memory/{layer}/{key}``, and ``reset``/``run`` are literal segments
    living beside ``{proposal_id}``. A regression in any of those would show up
    as a plausible-looking 404 or a "no such proposal: reset".
    """
    paths = {
        (r.path, tuple(sorted(r.methods or [])))
        for r in client.app.routes
        if getattr(r, "path", "").startswith("/memory/review")
    }
    assert ("/memory/review", ("GET",)) in paths
    assert ("/memory/review/run", ("POST",)) in paths
    assert ("/memory/review/reset", ("POST",)) in paths
    assert ("/memory/review/{proposal_id}/approve", ("POST",)) in paths
    assert ("/memory/review/{proposal_id}/dismiss", ("POST",)) in paths

    # …and they actually answer, rather than being swallowed by a sibling.
    assert client.get("/memory/review").status_code == 200
    assert client.post("/memory/review/reset").status_code in (200, 503)
    assert client.post("/memory/review/run").status_code == 400  # honest, mock-only
    # The catch-all still owns everything that ISN'T review.
    assert client.get("/memory/session/whatever").status_code == 404


def test_a_half_applied_approve_reaches_the_user_through_the_route(client, monkeypatch):
    """The 409 has to carry what already happened, not just what failed."""
    real_unlink = Path.unlink
    calls = {"n": 0}

    def _flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("locked by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _flaky)
    pid = _file_one(
        client,
        kind="merge",
        refs=["alpha", "alpha-copy", "old"],
        payload={"remove_refs": ["alpha-copy", "old"]},
    )
    res = client.post(f"/memory/review/{pid}/approve")
    assert res.status_code == 409
    assert "Part of this was already done" in res.json()["detail"]

    monkeypatch.undo()
    view = client.get("/memory/review").json()["proposals"][0]
    assert view["status"] == "pending"
    assert view["partial"] is True
    assert view["applied"]["changed"] == ["Removed “alpha-copy”"]


def test_the_view_caps_a_huge_replacement_body(client):
    pid = _file_one(
        client,
        kind="contradiction",
        refs=["old"],
        payload={"survivor_ref": "old", "text": "x" * 50_000},
    )
    view = next(
        p for p in client.get("/memory/review").json()["proposals"] if p["id"] == pid
    )
    assert view["payload"]["text_truncated"] is True
    assert view["payload"]["text_length"] == 50_000
    assert len(view["payload"]["text"]) == 4000
    # …and approving still writes the FULL text (the cap is a view concern).
    assert client.post(f"/memory/review/{pid}/approve").status_code == 200
    brain = Path(client.app.state.platform.config.home) / "brain"
    assert len((brain / "old.md").read_text(encoding="utf-8")) == 50_001


def test_approve_on_an_unsupported_base_is_409_with_the_reason(client, tmp_path):
    record = _daemon_store(client).create(
        kind="stale",
        base="not-a-real-base",
        refs=["page"],
        rationale="stale",
        payload={"remove_refs": ["page"]},
    )
    res = client.post(f"/memory/review/{record.id}/approve")
    assert res.status_code == 409
    assert "connected" in res.json()["detail"]
    # Still pending — the user can fix the base and try again.
    assert client.get("/memory/review").json()["pending"] == 1


def test_run_is_an_honest_400_under_the_offline_mock(client):
    res = client.post("/memory/review/run")
    assert res.status_code == 400
    assert "Connections" in res.json()["detail"]


class _FakeAdapter:  # deliberately NOT MockLLMAdapter
    provider = "anthropic"
    model = "claude-opus-4-8"


def test_run_with_nothing_to_review_never_fires_a_session(client, monkeypatch):
    """An EMPTY window is an honest "nothing new", not a session.

    The steward's own contract: ``build_task`` returns "" for an empty window,
    and asking a model to curate nothing is how memory fills with invented
    facts. A fresh install has no conversations, so this is the default path.
    """
    monkeypatch.setattr(client.app.state.platform.providers, "get", lambda *a, **k: _FakeAdapter())
    body = client.post("/memory/review/run").json()
    assert body["started"] is False
    assert body["session_id"] == ""
    assert "Nothing new to review" in body["note"]
    assert client.get("/sessions").json()["sessions"] == []


def test_run_opens_a_real_session_when_there_is_something_to_review(client, monkeypatch):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.memory.steward import MemorySteward

    monkeypatch.setattr(client.app.state.platform.providers, "get", lambda *a, **k: _FakeAdapter())
    monkeypatch.setattr(
        MemorySteward, "build_task", lambda self, window: "Review my memory please."
    )

    # The route spawns the run in the background; we assert the SESSION was
    # opened, not that a fake model finished a real curation loop.
    async def _noop_run(self, session_id):
        return self.get_session(session_id)

    monkeypatch.setattr(Orchestrator, "run_session", _noop_run)
    res = client.post("/memory/review/run")
    assert res.status_code == 200
    body = res.json()
    assert body["started"] is True
    assert body["session_id"]
    assert body["task"].startswith("Review my memory please.")
    # …plus the filing bridge, because a prompt that never names the tool
    # produces a review nobody can see (see the memory_propose section below).
    assert "memory_propose" in body["task"]


def test_a_completed_review_records_a_steward_run(client, monkeypatch):
    """The bookkeeping loop is closed: the run route records the run when the
    session finishes, so the review CURSOR only ever advances on a real
    completed review (record_run enforces the "ok" half)."""
    import time

    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.memory.steward import MemorySteward

    monkeypatch.setattr(client.app.state.platform.providers, "get", lambda *a, **k: _FakeAdapter())
    monkeypatch.setattr(
        MemorySteward, "build_task", lambda self, window: "Review my memory please."
    )

    async def _noop_run(self, session_id):
        return self.get_session(session_id)

    monkeypatch.setattr(Orchestrator, "run_session", _noop_run)
    assert client.post("/memory/review/run").json()["started"] is True

    steward = MemorySteward(client.app.state.platform)
    for _ in range(50):  # the run records on the background task's completion
        runs = steward.runs()
        if runs:
            break
        time.sleep(0.05)
    assert runs, "the finished review never recorded a steward run"
    assert runs[0]["session_id"]


def test_a_steward_that_cannot_compose_a_plan_still_never_invents_a_review(
    client, monkeypatch
):
    from iron_jarvis.memory.steward import MemorySteward

    def _boom(self, window):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(client.app.state.platform.providers, "get", lambda *a, **k: _FakeAdapter())
    monkeypatch.setattr(MemorySteward, "build_task", _boom)
    body = client.post("/memory/review/run").json()
    assert body["started"] is False
    assert "could not be composed" in body["note"]
    assert client.get("/sessions").json()["sessions"] == []


def test_without_a_steward_the_durable_template_prompt_still_runs(client, monkeypatch):
    """Landing-order insurance: with memory/steward.py absent this lane still
    works, using the schedule template's own self-contained prompt.

    Both halves of "absent" are simulated since v1.143.0: the platform attaches
    a shared ``memory_steward`` at build time, and the route falls back to
    importing the module itself — so a build genuinely without the steward has
    neither."""
    import builtins

    from iron_jarvis.agents.orchestrator import Orchestrator

    monkeypatch.setattr(client.app.state.platform.providers, "get", lambda *a, **k: _FakeAdapter())
    monkeypatch.setattr(client.app.state.platform, "memory_steward", None)
    real_import = builtins.__import__

    def _no_steward(name, *args, **kwargs):
        if name.endswith("memory.steward") or name == "iron_jarvis.memory.steward":
            raise ImportError("steward not landed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_steward)

    async def _noop_run(self, session_id):
        return self.get_session(session_id)

    monkeypatch.setattr(Orchestrator, "run_session", _noop_run)
    body = client.post("/memory/review/run").json()
    assert body["started"] is True
    assert body["steward"] is False
    assert body["task"] == MEMORY_REVIEW_TASK


# --- the apply path is the only destructive code in this release -------------
#
# Its payload is MODEL OUTPUT steered by conversation text the user did not
# write, and approving it deletes files. So it is attacked rather than sampled.


@pytest.mark.parametrize(
    "ref",
    [
        "../../secret.md",
        "..\\..\\secret.md",  # Windows separator
        "../secret.md",
        "./../secret.md",
        "sub/../../secret.md",
        "....//secret.md",
        "..%2fsecret.md",  # url-encoded traversal is a literal name, not a hop
        "../secret.md",  # unicode escapes for the same two dots
    ],
)
def test_a_relative_ref_can_never_leave_the_memory_base(store, tmp_path, ref):
    outside = tmp_path / "secret.md"
    outside.write_text("do not touch", encoding="utf-8")
    record = _mint(store, kind="stale", refs=["secret"], payload={"remove_refs": [ref]})
    _decided, result = store.approve(record.id)
    assert outside.exists(), f"{ref!r} escaped the base"
    assert result.ok is False


def test_an_absolute_ref_can_neither_delete_nor_overwrite_outside_the_base(store, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("do not touch", encoding="utf-8")
    removal = _mint(store, kind="stale", refs=["a"], payload={"remove_refs": [str(outside)]})
    assert store.approve(removal.id)[1].ok is False
    write = _mint(
        store,
        kind="contradiction",
        refs=["b"],
        payload={"survivor_ref": str(outside), "text": "pwned"},
    )
    assert store.approve(write.id)[1].ok is False
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_a_symlinked_note_inside_the_base_is_still_outside_it(store, tmp_path, notes):
    """The containment check resolves before it compares, so a link planted in
    the base cannot be used as a handle on a file elsewhere."""
    outside = tmp_path / "secret.md"
    outside.write_text("do not touch", encoding="utf-8")
    try:
        os.symlink(outside, notes / "link.md")
    except (OSError, NotImplementedError) as exc:  # pragma: no cover — CI perms
        pytest.skip(f"symlinks unavailable here: {exc}")
    removal = _mint(store, kind="stale", refs=["link"], payload={"remove_refs": ["link"]})
    assert store.approve(removal.id)[1].ok is False
    write = _mint(
        store, kind="contradiction", refs=["link"],
        payload={"survivor_ref": "link", "text": "pwned"},
    )
    assert store.approve(write.id)[1].ok is False
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_a_base_whose_folder_is_itself_a_symlink_still_works(tmp_path):
    """The mirror image: containment must not become a REFUSAL of the ordinary
    case where someone's vault lives behind a link."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    link = tmp_path / "linked-base"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover — CI perms
        pytest.skip(f"symlinks unavailable here: {exc}")
    manager = LongTermMemory()
    connector = MarkdownDirConnector(link)
    connector.name = "brain"
    manager.register(connector)
    engine = make_engine(tmp_path / "linked.db")
    init_db(engine)
    linked = MemoryProposalStore(engine, ltm=manager, home=str(tmp_path / "home"))
    record = _mint(linked, kind="stale", refs=["alpha"], payload={"remove_refs": ["alpha"]})
    _decided, result = linked.approve(record.id)
    assert result.ok is True
    assert not (real / "alpha.md").exists()


def test_a_failed_write_leaves_the_note_byte_identical(store, notes, monkeypatch):
    """Stage-then-replace, attacked at the replace: a crash mid-apply must not
    lose the note we are consolidating INTO."""
    import iron_jarvis.memory.proposals as proposals_module

    before = (notes / "alpha.md").read_text(encoding="utf-8")

    def _explode(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(proposals_module.os, "replace", _explode)
    record = _mint(
        store, kind="merge", refs=["alpha"],
        payload={"survivor_ref": "alpha", "text": "# Alpha\n\nMerged."},
    )
    decided, result = store.approve(record.id)
    assert result.ok is False
    assert decided.status == "pending"
    assert (notes / "alpha.md").read_text(encoding="utf-8") == before
    # …and no half-written staging file left behind in the user's vault.
    assert [p.name for p in notes.iterdir() if ".tmp" in p.name] == []


def test_a_half_applied_approve_says_so_instead_of_reading_as_untouched(
    store, notes, monkeypatch
):
    """A removal that fails PART-WAY leaves real changes on disk. Reporting a
    bare "could not remove X" would let the user believe nothing happened."""
    calls = {"n": 0}
    real_unlink = Path.unlink

    def _flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("locked by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _flaky)
    record = _mint(
        store,
        kind="merge",
        refs=["alpha", "alpha-copy", "old"],
        payload={
            "survivor_ref": "alpha",
            "text": "# Alpha\n\nMerged.",
            "remove_refs": ["alpha-copy", "old"],
        },
    )
    decided, result = store.approve(record.id)
    assert result.ok is False
    assert result.partial is True
    assert result.changed == ["Rewrote “alpha”", "Removed “alpha-copy”"]
    assert "Part of this was already done" in result.error
    assert "Time travel" in result.error
    # Still pending (retry is right) — but the record now CARRIES what happened.
    assert decided.status == "pending"
    applied = decided.decoded_applied()
    assert applied["partial"] is True
    assert applied["undo_ids"] == result.undo_ids
    assert (notes / "old.md").exists()


def test_an_approve_that_changes_nothing_is_never_reported_as_a_change(store, notes):
    """"Already gone" is not work. Counting it as work is how an approve that
    did literally nothing came back ok=True and the card said "Memory updated"."""
    record = _mint(store, kind="stale", refs=["ghost"], payload={"remove_refs": ["ghost"]})
    decided, result = store.approve(record.id)
    assert result.ok is False
    assert result.changed == []
    assert result.skipped == ["“ghost” was already gone"]
    assert "already gone" in result.error
    assert decided.status == "pending"  # dismissible, never a fake success


def test_a_re_approved_suggestion_whose_work_is_done_refuses_honestly(store, notes):
    """approve deliberately does NOT suppress, so the same signature can come
    back. The second approve must not answer "Memory updated" for a no-op."""
    first = _mint(store)
    assert store.approve(first.id)[1].ok is True
    again = _mint(store)
    assert again is not None, "an approved signature is not suppressed (by design)"
    _decided, result = store.approve(again.id)
    assert result.ok is False
    assert "already gone" in result.error


# --- suppression, fuzzed the way a MODEL varies its own output ---------------


@pytest.mark.parametrize(
    "variant",
    [
        ["alpha-copy", "alpha"],  # order
        ["Alpha", "ALPHA-COPY"],  # case
        [" alpha ", "alpha-copy "],  # whitespace
        ["alpha.md", "alpha-copy.md"],  # filename vs title
        ["alpha/", "alpha-copy/"],  # trailing slash
        ["./alpha", "./alpha-copy"],  # relative prefix
        ["alpha", "alpha-copy", "alpha"],  # a repeated ref
        ["alpha\\", "alpha-copy"],  # windows separator
    ],
)
def test_dismissed_stays_dismissed_however_the_model_respells_it(store, variant):
    """"Not this" has to stick harder than exact-string equality: the steward
    re-derives every suggestion from scratch and has no obligation to spell a
    note the way it did last week."""
    record = _mint(store)
    store.dismiss(record.id)
    assert _mint(store, refs=variant) is None, f"{variant!r} re-raised a dismissal"


def test_a_long_ref_list_still_signs_uniquely(store):
    """The caps allow 20 refs of 500 chars — far past the signature column. A
    TRUNCATED signature would make two long suggestions sharing a prefix sign
    identically, silently swallowing the second."""
    shared = [f"{'note-' * 30}{i}" for i in range(15)]
    first = signature_for("merge", "brain", shared + ["zulu"])
    second = signature_for("merge", "brain", shared + ["yankee"])
    assert len(first) <= 500 and len(second) <= 500
    assert first != second
    assert first == signature_for("merge", "brain", ["zulu"] + list(reversed(shared)))

    record = _mint(store, kind="merge", refs=shared + ["zulu"])
    store.dismiss(record.id)
    assert _mint(store, kind="merge", refs=shared + ["yankee"]) is not None
    assert _mint(store, kind="merge", refs=shared + ["zulu"]) is None


def test_suppression_still_distinguishes_genuinely_different_notes(store):
    """The other side of the trade: an over-broad normalization would swallow a
    real second suggestion, which is worse than one extra card."""
    record = _mint(store, refs=["work/alpha", "work/alpha-copy"])
    store.dismiss(record.id)
    assert _mint(store, refs=["home/alpha", "home/alpha-copy"]) is not None
    # …and a different KIND about the same notes is a different suggestion.
    assert signature_for("merge", "brain", ["a"]) != signature_for("stale", "brain", ["a"])
    assert signature_for("merge", "brain", ["a"]) != signature_for("merge", "vault", ["a"])


def test_approving_deliberately_does_not_suppress(store):
    record = _mint(store)
    store.approve(record.id)
    assert store.suppressed(record.signature) is False


# --- undo, attacked -----------------------------------------------------------


async def test_undo_refuses_when_the_restored_note_was_edited_afterwards(
    store, notes, tmp_path
):
    """The drift guard: undo must never clobber an edit the user made after the
    approve. (Applies to the REWRITE half — the removal half has no post-state
    to compare, and re-writing a note back is idempotent by construction.)"""
    from iron_jarvis.ltm.tools import LTMAppendTool
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.undo import RevertConflict

    record = _mint(
        store, kind="contradiction", refs=["old"],
        payload={"survivor_ref": "old", "text": "# Old\n\nCorrected."},
    )
    _decided, result = store.approve(record.id)
    assert result.ok and len(result.undo_ids) == 1

    (notes / "old.md").write_text("# Old\n\nI edited this myself.\n", encoding="utf-8")

    with session_scope(store.engine) as db:
        journal = db.get(UndoJournal, result.undo_ids[0])
        undo = {
            "kind": journal.kind, "reversible": True, "pre_ref": journal.pre_ref,
            "pre_inline": journal.pre_inline, "pre_sha256": journal.pre_sha256,
            "post_sha256": journal.post_sha256,
        }
    ctx = ToolContext(
        workspace=tmp_path / "ws", session_id="memory-review", agent_run_id="",
        config=SimpleNamespace(home=Path(store.home)), event_bus=None,
        engine=store.engine,
    )
    with pytest.raises(RevertConflict):
        await LTMAppendTool(store.ltm).revert(undo, ctx)
    assert "I edited this myself" in (notes / "old.md").read_text(encoding="utf-8")


async def test_undoing_twice_is_safe(store, notes, tmp_path):
    """A double-click on Undo: the removal half is idempotent, the rewrite half
    refuses the second time (its post-state no longer matches)."""
    from iron_jarvis.ltm.tools import LTMAppendTool
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.undo import RevertConflict

    before = (notes / "alpha-copy.md").read_text(encoding="utf-8")
    record = _mint(store)
    _decided, result = store.approve(record.id)
    ctx = ToolContext(
        workspace=tmp_path / "ws", session_id="memory-review", agent_run_id="",
        config=SimpleNamespace(home=Path(store.home)), event_bus=None,
        engine=store.engine,
    )
    tool = LTMAppendTool(store.ltm)
    for attempt in (1, 2):
        with session_scope(store.engine) as db:
            journal = db.get(UndoJournal, result.undo_ids[0])
            undo = {
                "kind": journal.kind, "reversible": True, "pre_ref": journal.pre_ref,
                "pre_inline": journal.pre_inline, "pre_sha256": journal.pre_sha256,
                "post_sha256": journal.post_sha256,
            }
        try:
            out = await tool.revert(undo, ctx)
        except RevertConflict:  # pragma: no cover — the rewrite half's answer
            out = None
        assert out is None or out.ok, f"attempt {attempt} was neither ok nor a refusal"
    assert (notes / "alpha-copy.md").read_text(encoding="utf-8") == before


async def test_undo_after_the_base_folder_moved_is_an_honest_error(store, notes, tmp_path):
    """Nobody promised undo survives the user moving their vault — but it must
    say so rather than half-restoring somewhere."""
    from iron_jarvis.ltm.tools import LTMAppendTool
    from iron_jarvis.tools.base import ToolContext

    record = _mint(store)
    _decided, result = store.approve(record.id)
    with session_scope(store.engine) as db:
        journal = db.get(UndoJournal, result.undo_ids[0])
        undo = {
            "kind": journal.kind, "reversible": True, "pre_ref": journal.pre_ref,
            "pre_inline": journal.pre_inline, "pre_sha256": journal.pre_sha256,
            "post_sha256": journal.post_sha256,
        }
    moved = tmp_path / "moved-away"
    notes.rename(moved)
    ctx = ToolContext(
        workspace=tmp_path / "ws", session_id="memory-review", agent_run_id="",
        config=SimpleNamespace(home=Path(store.home)), event_bus=None,
        engine=store.engine,
    )
    out = await LTMAppendTool(store.ltm).revert(undo, ctx)
    assert out.ok is False
    assert "could not restore" in out.error
    assert not notes.exists()  # nothing was recreated at the old path


def test_the_undoable_badge_is_only_shown_where_undo_is_real(store):
    """The card promises Time travel off ``describe_base``. That promise has to
    match what approving actually journals."""
    assert store.describe_base("brain")["undoable"] is True
    record = _mint(store)
    _decided, result = store.approve(record.id)
    assert result.undoable is True and result.undo_ids

    # A base with no local files: no promise, and no approve either.
    cloud = store.describe_base("notion")
    assert cloud["undoable"] is False and cloud["can_apply"] is False

    # And a store with nowhere to keep pre-images cannot promise undo either:
    # the change still happens, but ``undoable`` is reported honestly.
    homeless = MemoryProposalStore(store.engine, ltm=store.ltm, home=None)
    second = homeless.create(
        kind="stale", base="brain", refs=["old"], rationale="r",
        suggested_action="a", payload={"remove_refs": ["old"]},
    )
    _decided, result = homeless.approve(second.id)
    assert result.ok is True
    assert result.undoable is False and result.undo_ids == []


# --- memory_propose: the seam that makes the whole feature reachable ---------
#
# Everything above this line could be true of a feature nothing in production
# can trigger. The store, the routes, the card and the undo lane were all built
# before anything could CALL ``MemoryProposalStore.create`` from an agent
# session — so the queue could only ever be filled by a test, and a shipped
# steward would have run weekly, forever, raising nothing.


def _propose_args(**over):
    args = {
        "kind": "duplicate",
        "base": "brain",
        "refs": ["alpha", "alpha-copy"],
        "rationale": "Both notes record the same fact.",
        "suggested_action": "Keep “alpha” and remove the copy.",
        "remove_refs": ["alpha-copy"],
    }
    args.update(over)
    return args


def _ctx(platform, session_id: str = "session_probe"):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(
        workspace=Path(platform.config.home) / "ws",
        session_id=session_id,
        agent_run_id="run_probe",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def test_the_tool_is_registered_armed_and_advertised_to_the_review_session(client):
    """Four independent wirings, each of which silently kills the feature alone.

    A tool can be written, registered, and STILL be uncallable: the agent
    runtime advertises exactly ``registry.specs(agent_def.tools)``, and the
    permission engine is fail-closed, so a missing entry in either table is an
    invisible "the steward proposes nothing, forever".
    """
    from iron_jarvis.agents.types import get_agent_definition
    from iron_jarvis.core.models import AgentType
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

    platform = client.app.state.platform
    assert "memory_propose" in {t["name"] for t in platform.registry.specs()}

    # The review route opens an ordinary session, so BUILDER is the definition
    # that actually decides whether the steward can file anything.
    builder = get_agent_definition(AgentType.BUILDER)
    advertised = {t["name"] for t in platform.registry.specs(builder.tools)}
    assert "memory_propose" in advertised
    # v1.142's history_search shipped into the registry but into NO agent
    # definition — and the steward's prompt tells the session to use it.
    assert "history_search" in advertised

    # Fail-closed regression: no permission entry -> "ask" -> DENY in the
    # headless daemon, which is exactly how a scheduled steward files nothing
    # and nobody sees an error.
    assert platform.config.permissions.get("memory_propose") == "allow"
    decision = platform.permissions.authorize("memory_propose", {})
    assert decision.allowed is True

    # Chat must NOT file housekeeping mid-conversation — a suggestion the user
    # never asked for, raised from a turn about something else.
    assert "memory_propose" not in AUTO_SAFE_TOOLS


async def test_the_tool_files_a_row_tagged_with_the_calling_session(client):
    """The accounting seam: ``run_id`` is the live session id, which is the only
    reason ``proposals_raised`` is a read number rather than a guess."""
    platform = client.app.state.platform
    result = await platform.registry.invoke(
        "memory_propose",
        _propose_args(),
        _ctx(platform, "session_abc"),
        platform.permissions,
    )
    assert result.ok, result.error
    assert result.data["filed"] is True
    assert result.data["run_id"] == "session_abc"
    assert "Nothing has changed yet" in result.output

    view = client.get("/memory/review").json()["proposals"][0]
    assert view["id"] == result.data["id"]
    assert view["run_id"] == "session_abc"
    assert view["status"] == "pending"
    assert view["remove_refs"] == ["alpha-copy"]

    # …and it survives the whole way to a real change on disk.
    brain = Path(platform.config.home) / "brain"
    assert client.post(f"/memory/review/{view['id']}/approve").status_code == 200
    assert not (brain / "alpha-copy.md").exists()
    assert (brain / "alpha.md").exists()


async def test_a_real_agent_session_raises_a_proposal_the_user_can_approve(client, monkeypatch):
    """END TO END through the runtime: a scripted model calls the tool inside a
    real agent session, the suggestion appears in GET /memory/review, approving
    it changes the note on disk, and the steward's run accounting counts it."""
    from iron_jarvis.core.models import SessionStatus
    from iron_jarvis.memory.steward import MemorySteward
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    platform = client.app.state.platform
    scripted = MockLLMAdapter(
        script=[
            LLMResponse(
                tool_calls=[
                    ToolCall(id="c1", name="memory_propose", arguments=_propose_args())
                ]
            ),
            LLMResponse(text="Filed one housekeeping suggestion for you to approve."),
        ]
    )
    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: scripted)

    orchestrator = client.app.state.orchestrator
    session = await orchestrator.create_session(
        "Review my memory.", origin="memory-review"
    )
    done = await orchestrator.run_session(session.id)
    assert done.status is SessionStatus.COMPLETED

    # The tool really ran inside the session (not around it).
    transcript = orchestrator.transcript(session.id)
    assert any(t["tool"] == "memory_propose" and t["ok"] for t in transcript["tools"])

    body = client.get("/memory/review").json()
    assert body["pending"] == 1
    view = body["proposals"][0]
    assert view["run_id"] == session.id
    assert view["kind"] == "duplicate"

    # The steward's accounting reads THIS session's rows, not an estimate.
    steward = MemorySteward(platform)
    steward.record_run(ok=True, session_id=session.id, proposals_raised=1, outcome="done")
    assert steward.stats()["proposals_raised"] == 1

    brain = Path(platform.config.home) / "brain"
    assert client.post(f"/memory/review/{view['id']}/approve").status_code == 200
    assert not (brain / "alpha-copy.md").exists()


def test_the_recorded_run_counts_what_the_session_actually_filed(client, monkeypatch):
    """The loop, closed at BOTH ends: the session files through the tool, and
    the run the route records reports that count — not an estimate, and not the
    zero it would report if ``run_id`` were ever anything but the session id."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import SessionStatus
    from iron_jarvis.memory.steward import MemorySteward

    platform = client.app.state.platform
    platform.search_index.sync_thread(
        "thr_c", kind="chat", title="Filing deadlines", project_id="",
        entries=[{"role": "user", "content": "March 15, every year.",
                  "at": datetime.now(timezone.utc) - timedelta(days=1), "seq": 0}],
    )
    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: _FakeAdapter())

    async def _files_a_proposal(self, session_id):
        # What ``memory_propose`` does, minus the tool wrapper (proved
        # separately above): the ROW carries the live session id, which is the
        # only thing ``_count_proposals`` can see.
        MemoryProposalStore(platform.engine, ltm=platform.ltm).create(
            **{k: v for k, v in _propose_args().items() if k != "remove_refs"},
            payload={"remove_refs": ["alpha-copy"]},
            run_id=session_id,
        )
        session = self.get_session(session_id)
        session.status = SessionStatus.COMPLETED
        session.summary = "Filed one suggestion."
        return session

    monkeypatch.setattr(Orchestrator, "run_session", _files_a_proposal)
    started = client.post("/memory/review/run").json()

    steward = MemorySteward(platform)
    for _ in range(60):
        runs = steward.runs()
        if runs:
            break
        # TestClient drives the event loop per REQUEST, so a background task
        # that awaits anything only advances while a request is in flight.
        client.get("/health")
        time.sleep(0.05)
    assert runs and runs[0]["ok"] is True
    assert runs[0]["proposals_raised"] == 1
    assert client.get("/memory/review").json()["proposals"][0]["run_id"] == (
        started["session_id"]
    )


async def test_the_tool_refuses_what_the_apply_path_would_refuse_later(client):
    """Every refusal here is one the store would otherwise raise HOURS later,
    with the user watching an unappliable card."""
    platform = client.app.state.platform
    ctx = _ctx(platform)

    async def call(**over):
        return await platform.registry.invoke(
            "memory_propose", _propose_args(**over), ctx, platform.permissions
        )

    assert not (await call(kind="addition")).ok
    assert not (await call(base="")).ok
    assert not (await call(refs=[])).ok
    assert not (await call(rationale="")).ok
    assert not (await call(suggested_action="")).ok
    # text without a survivor is the store's own "nothing to write it to".
    assert "survivor_ref" in (await call(text="new body")).error
    # a suggestion that says nothing to do
    assert not (await call(remove_refs=[])).ok
    # a base this daemon has never heard of would be junk in the queue.
    unknown = await call(base="notion-that-isnt-connected")
    assert not unknown.ok
    assert "no memory base called" in unknown.error
    assert "brain" in unknown.error


async def test_the_tool_refuses_to_delete_every_copy_of_a_duplicate(client):
    """A "these are duplicates, remove both" slip destroys the fact itself, and
    the apply path would carry it out exactly as written."""
    platform = client.app.state.platform
    result = await platform.registry.invoke(
        "memory_propose",
        _propose_args(remove_refs=["alpha", "alpha-copy"]),
        _ctx(platform),
        platform.permissions,
    )
    assert result.ok is False
    assert "would delete the fact itself" in result.error
    assert client.get("/memory/review").json()["proposals"] == []


async def test_the_tool_never_files_a_removal_of_the_note_it_rewrites(client):
    platform = client.app.state.platform
    result = await platform.registry.invoke(
        "memory_propose",
        _propose_args(
            kind="merge",
            survivor_ref="alpha",
            text="# Alpha\n\nMerged.",
            remove_refs=["alpha", "alpha-copy"],
        ),
        _ctx(platform),
        platform.permissions,
    )
    assert result.ok, result.error
    view = client.get("/memory/review").json()["proposals"][0]
    assert view["remove_refs"] == ["alpha-copy"]  # the survivor was dropped
    assert view["survivor_ref"] == "alpha"
    assert view["rewrites"] is True


async def test_a_suppressed_suggestion_is_reported_not_silently_succeeded(client):
    """``create`` returns None for BOTH "suppressed" and "the DB broke" — opposite
    instructions for the caller. The tool must disambiguate, or a model reads a
    success message as proof the user will see something they never will."""
    platform = client.app.state.platform
    ctx = _ctx(platform)
    first = await platform.registry.invoke(
        "memory_propose", _propose_args(), ctx, platform.permissions
    )
    assert first.data["filed"] is True
    client.post(f"/memory/review/{first.data['id']}/dismiss")

    again = await platform.registry.invoke(
        "memory_propose",
        # …and re-spelled, the way a fresh run naturally would.
        _propose_args(refs=["alpha-copy.md", "./alpha"]),
        ctx,
        platform.permissions,
    )
    assert again.ok is True  # not an error: there is nothing for the model to fix
    assert again.data["filed"] is False
    assert again.data["reason"] == "suppressed"
    assert "dismissed it before" in again.output
    assert client.get("/memory/review").json()["proposals"][0]["status"] == "dismissed"


async def test_a_broken_queue_is_an_honest_failure_not_a_fake_filing(tmp_path):
    """The other half of the None ambiguity: a store that cannot write must not
    come back as "filed"."""
    from iron_jarvis.tools.base import ToolContext

    tool = MemoryProposeTool(MemoryProposalStore(SimpleNamespace()))
    ctx = ToolContext(
        workspace=tmp_path,
        session_id="s",
        agent_run_id="",
        config=SimpleNamespace(home=tmp_path),
        event_bus=None,
        engine=None,
    )
    result = await tool.execute(_propose_args(), ctx)
    assert result.ok is False
    assert "could not be saved" in result.error


def test_the_tool_factory_binds_one_store():
    store = MemoryProposalStore(SimpleNamespace())
    tools = memory_proposal_tools(store)
    assert [t.name for t in tools] == ["memory_propose"]
    assert tools[0].store is store
    assert tools[0].perm_key() == "memory_propose"
    # It returns our own confirmation, not planted text — no fence needed.
    assert tools[0].returns_untrusted_content is False


def test_every_review_prompt_tells_the_session_how_to_file(client):
    """The prompt seam. A curation agent that is never told to CALL the tool
    writes its housekeeping into prose, and the user sees nothing."""
    from iron_jarvis.daemon.routes.memory_review import with_filing_instructions

    assert "memory_propose" in MEMORY_REVIEW_TASK  # the durable schedule prompt
    assert with_filing_instructions(MEMORY_REVIEW_TASK) == MEMORY_REVIEW_TASK

    # A CUSTOM task string that does not name the tool gets the bridge appended…
    bridged = with_filing_instructions("Read my history and curate.")
    assert bridged.startswith("Read my history and curate.")
    assert "memory_propose" in bridged
    # …exactly once, so a prompt that already names it is never duplicated.
    assert with_filing_instructions(bridged) == bridged
    assert with_filing_instructions("") == ""


def test_the_stewards_own_prompt_disables_the_filing_bridge(client):
    """v1.143.0 fixed the seam AT THE SOURCE: the steward's step 4 now names
    ``memory_propose`` itself, so the bridge — designed to stop firing the day
    that happened — must be a NO-OP on every prompt the steward composes.

    Proved on a REAL built task (fence, conversation list and all), not just on
    the preamble constant: appending trusted instructions after the untrusted
    fence is safe but redundant, and a second copy of the filing rules would be
    the first thing to drift out of sync with the first.
    """
    from iron_jarvis.daemon.routes.memory_review import (
        FILING_INSTRUCTIONS,
        PROPOSE_TOOL,
        with_filing_instructions,
    )
    from iron_jarvis.memory.steward import TASK_PREAMBLE, MemorySteward

    # The route's tool name is RESOLVED from the steward, so the self-disable
    # condition and the prompt that disables it can never drift apart.
    from iron_jarvis.memory import steward as _steward_mod

    assert PROPOSE_TOOL == _steward_mod.PROPOSE_TOOL == "memory_propose"

    assert PROPOSE_TOOL in TASK_PREAMBLE
    assert with_filing_instructions(TASK_PREAMBLE) == TASK_PREAMBLE

    platform = client.app.state.platform
    platform.search_index.sync_thread(
        "thr_bridge", kind="chat", title="S-corp election", project_id="",
        entries=[{"role": "user", "content": "We file by March 15.",
                  "at": datetime.now(timezone.utc) - timedelta(days=1), "seq": 0}],
    )
    task = MemorySteward(platform).plan()["task"]
    assert task and PROPOSE_TOOL in task
    assert with_filing_instructions(task) == task
    assert FILING_INSTRUCTIONS not in task  # nothing was appended, not even once


# --- the run/record loop, proved with a session that really completes --------


def test_a_completed_review_advances_the_review_point(client, monkeypatch):
    """The bookkeeping loop, END TO END and empirically: index a conversation,
    run the review, and the watermark MOVES — otherwise every manual review
    re-reads the same history forever and re-writes the same notes."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import SessionStatus
    from iron_jarvis.memory.steward import MemorySteward

    platform = client.app.state.platform
    when = datetime.now(timezone.utc) - timedelta(days=2)
    platform.search_index.sync_thread(
        "thr_1",
        kind="chat",
        title="S-corp election",
        project_id="",
        entries=[
            {"role": "user", "content": "We file the S-corp election by March 15.",
             "at": when, "seq": 0},
        ],
    )
    steward = MemorySteward(platform)
    assert steward.cursor() == ""

    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: _FakeAdapter())

    async def _completed(self, session_id):
        session = self.get_session(session_id)
        session.status = SessionStatus.COMPLETED
        session.summary = "Saved 1 note."
        return session

    monkeypatch.setattr(Orchestrator, "run_session", _completed)
    body = client.post("/memory/review/run").json()
    assert body["started"] is True
    # The window's own conversations reached the prompt, WITH the filing bridge.
    assert "S-corp election" in body["task"]
    assert "memory_propose" in body["task"]

    for _ in range(60):
        runs = steward.runs()
        if runs:
            break
        time.sleep(0.05)
    assert runs, "the finished review never recorded a steward run"
    assert runs[0]["ok"] is True
    assert runs[0]["session_id"] == body["session_id"]
    assert runs[0]["conversations"] == 1
    assert runs[0]["refs"] == ["thr_1"]  # a real list, not the string "[]"
    assert steward.cursor(), "a completed review did not advance the review point"

    stats = client.get("/memory/review").json()["steward"]["stats"]
    assert stats["successful_runs"] == 1
    assert stats["cursor_note"], "the watermark's known limitation is not surfaced"


def test_a_crashed_review_records_a_failure_and_never_advances(client, monkeypatch):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.memory.steward import MemorySteward

    platform = client.app.state.platform
    platform.search_index.sync_thread(
        "thr_2", kind="chat", title="A chat", project_id="",
        entries=[{"role": "user", "content": "something durable",
                  "at": datetime.now(timezone.utc) - timedelta(days=1), "seq": 0}],
    )
    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: _FakeAdapter())

    async def _boom(self, session_id):
        raise RuntimeError("the provider died mid-review")

    monkeypatch.setattr(Orchestrator, "run_session", _boom)
    assert client.post("/memory/review/run").json()["started"] is True

    steward = MemorySteward(platform)
    for _ in range(60):
        runs = steward.runs()
        if runs:
            break
        time.sleep(0.05)
    assert runs
    assert runs[0]["ok"] is False
    assert "provider died" in runs[0]["outcome"]
    assert steward.cursor() == ""  # a failed review cannot move the watermark


def test_a_weekly_review_the_schedule_could_not_record_still_shows_up(
    client, monkeypatch
):
    """The reconciler's job AFTER v1.143.0 — the safety net, not the main road.

    ``platform._dispatch_scheduled`` now recognises the memory-review schedule
    and records its own run. It can only do that when the steward can PLAN the
    fire; when ``plan()`` fails, the dispatcher deliberately falls back to the
    durable template prompt and records nothing rather than risk the scheduler
    thread. That fire must still reach the card, which is what this proves —
    the original failure it guards against being: the session completed with
    "Saved 2 notes." and ``steward.runs()`` was ``[]``, so the card said "No
    review has run yet" forever, however many weeks it ran.
    """
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.core.models import Session, SessionStatus
    from iron_jarvis.memory.steward import MemorySteward

    platform = client.app.state.platform
    monkeypatch.setattr(platform.providers, "get", lambda *a, **k: _FakeAdapter())

    def _no_plan(self, **kwargs):
        raise RuntimeError("the steward could not plan this review")

    monkeypatch.setattr(MemorySteward, "plan", _no_plan)

    async def _completed(self, session_id):
        with session_scope(platform.engine) as db:
            row = db.get(Session, session_id)
            row.status = SessionStatus.COMPLETED
            row.summary = "Saved 2 notes."
            db.add(row)
            db.commit()
        # …and the session filed a suggestion while it ran.
        MemoryProposalStore(platform.engine, ltm=platform.ltm).create(
            kind="duplicate", base="brain", refs=["alpha", "alpha-copy"],
            rationale="Both notes say the same thing.",
            suggested_action="Keep “alpha”.",
            payload={"remove_refs": ["alpha-copy"]},
            run_id=session_id,
        )
        return self.get_session(session_id)

    monkeypatch.setattr(Orchestrator, "run_session", _completed)

    template = client.get("/memory/review").json()["template"]
    client.post("/schedules", json={
        "name": template["name"], "cron": template["cron"], "kind": template["kind"],
        "payload": {"task": template["task"]},
    })
    fired = client.post(f"/schedules/{template['name']}/run").json()
    assert fired["last_status"] == "ok"
    session_id = fired["last_session_id"]

    stats = client.get("/memory/review").json()["steward"]["stats"]
    assert stats["successful_runs"] == 1
    assert stats["last_run_at"], "a weekly review left no trace on the card"
    assert stats["last_session_id"] == session_id
    assert stats["proposals_raised"] == 1

    # Idempotent: reading the card again must not double-count it.
    again = client.get("/memory/review").json()["steward"]["stats"]
    assert again["successful_runs"] == 1
    assert again["proposals_raised"] == 1
    # …and it did NOT invent a review point for a prompt that covered no window.
    assert MemorySteward(platform).cursor() == ""


async def test_the_reconciler_keeps_its_hands_off_the_manual_lane(client):
    """The manual lane records ITSELF, a fraction of a second after the session
    row flips to completed. A card refreshed inside that window must not
    reconcile it first with no cursor — ``record_run``'s idempotence would then
    drop the real one, and the review point it had just earned would be lost."""
    from iron_jarvis.core.models import Session, SessionStatus
    from iron_jarvis.memory.steward import MemorySteward

    platform = client.app.state.platform
    orchestrator = client.app.state.orchestrator
    session = await orchestrator.create_session("Review.", origin="memory-review")
    with session_scope(platform.engine) as db:
        row = db.get(Session, session.id)
        row.status = SessionStatus.COMPLETED
        db.add(row)
        db.commit()

    client.get("/memory/review")  # the refresh that used to race
    assert MemorySteward(platform).runs() == []

    # …and the lane's own record still lands, cursor and all.
    MemorySteward(platform).record_run(
        ok=True, cursor="2026-05-05T00:00:00+00:00|4", session_id=session.id
    )
    assert MemorySteward(platform).cursor() == "2026-05-05T00:00:00+00:00|4"


def test_the_review_point_can_be_reset_from_the_card(client, monkeypatch):
    """The escape hatch the ``cursor_note`` limitation tells the user about."""
    from iron_jarvis.memory.steward import MemorySteward

    steward = MemorySteward(client.app.state.platform)
    steward.record_run(ok=True, cursor="2026-01-01T00:00:00+00:00|9", session_id="s1")
    assert steward.cursor()

    res = client.post("/memory/review/reset")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert steward.cursor() == ""
    # It is auditable, not invisible: the move is itself a recorded run.
    assert any(r["kind"] == "reset" for r in steward.runs())
