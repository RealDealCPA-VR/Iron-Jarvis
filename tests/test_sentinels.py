"""Sentinels ("always-on watchers") tests — fully offline + deterministic.

Safety is the whole point, so the suite proves the guarantees:
  * OFF by default (no runner, no proposals on boot, registry empty).
  * SUGGEST-ONLY: a fired Sentinel mints exactly ONE backlog proposal
    (source="sentinel"), NEVER a session.
  * DEPENDENCY-LIGHT + injectable: the filesystem watcher is driven by an
    injected scanner (no watchdog, no real polling cadence, no network).
  * Restart survival: last_state persists and a FRESH registry rehydrates it
    without re-firing for changes already seen.
  * CRUD + the file-trigger wiring + the agent tool + the HTTP endpoints.
"""

from __future__ import annotations

# Register the SentinelRecord table on SQLModel.metadata BEFORE init_db. Top of file.
import iron_jarvis.sentinels.models  # noqa: F401

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.sentinels.service import SentinelService
from iron_jarvis.sentinels.tools import SentinelAddTool, sentinel_tools
from iron_jarvis.sentinels.watcher import default_scanner, diff_state
from iron_jarvis.tools.base import ToolContext


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


def _scanner(state: dict):
    """A deterministic scanner reading a mutable {path: mtime} dict."""

    def scan(path, pattern=None):
        return dict(state)

    return scan


def _ctx(platform, tmp_path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s_test",
        agent_run_id="r_test",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


# -- 1. OFF by default --------------------------------------------------------


def test_off_by_default_build_platform(platform):
    # A freshly built platform has the registry but is OFF and empty — no watcher
    # ran, no proposal was minted on build.
    assert platform.config.sentinels_enabled is False
    assert platform.sentinels is not None
    assert platform.sentinels.list() == []
    assert platform.intent.list_proposals() == []


def test_off_by_default_daemon_boot_creates_no_proposals(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    listing = client.get("/sentinels").json()
    assert listing["enabled"] is False
    assert listing["sentinels"] == []
    # No sentinel loop ran on boot, so the backlog is empty.
    assert client.get("/proposals").json()["proposals"] == []
    # A manual poll is gated by opt-in: it no-ops while disabled.
    poll = client.post("/sentinels/poll").json()
    assert poll == {"ran": False, "reason": "sentinels_disabled", "proposals": []}


# -- 2. SUGGEST-ONLY: a proposal, never a session ----------------------------


def test_file_sentinel_fires_one_suggest_only_proposal(platform):
    svc, intent = platform.sentinels, platform.intent
    svc.add("notes", path="/watch", task="review the changed notes")
    fs: dict[str, float] = {}
    scan = _scanner(fs)

    # First poll = baseline: pre-existing state is "already seen" — nothing fires.
    assert svc.poll_once(intent, scanner=scan) == []
    assert intent.list_proposals() == []

    # A new file appears -> exactly ONE suggest-only proposal.
    fs["/watch/a.md"] = 100.0
    created = svc.poll_once(intent, scanner=scan)
    assert len(created) == 1
    props = intent.list_proposals(status="pending")
    assert len(props) == 1
    p = props[0]
    # Next leaps 03-a (moved on purpose): a suggestion from the record.
    assert p.source == "notice" and p.kind == "new_files"
    assert p.status == "pending"  # never auto-executed
    assert p.session_id is None  # NOT a session
    assert p.decoded_action()["task"] == "review the changed notes"
    assert p.risk == "low"
    # The Sentinel proposed only — the orchestrator was never invoked.
    assert intent.orchestrator is None


def test_multiple_changes_mint_a_single_proposal(platform):
    svc, intent = platform.sentinels, platform.intent
    svc.add("bulk", path="/d")
    fs: dict[str, float] = {}
    scan = _scanner(fs)
    svc.poll_once(intent, scanner=scan)  # baseline

    fs["/d/a"] = 1.0
    fs["/d/b"] = 1.0
    fs["/d/c"] = 1.0
    svc.poll_once(intent, scanner=scan)
    # Three changed files -> ONE summarising proposal, not three.
    assert len(intent.list_proposals(status="pending")) == 1


# -- 3. No re-fire for unchanged ---------------------------------------------


def test_no_refire_for_unchanged_file(platform):
    svc, intent = platform.sentinels, platform.intent
    svc.add("w", path="/w")
    fs = {"/w/a.txt": 5.0}
    scan = _scanner(fs)

    svc.poll_once(intent, scanner=scan)  # baseline records a.txt
    fs["/w/b.txt"] = 6.0  # change after baseline
    assert len(svc.poll_once(intent, scanner=scan)) == 1
    n_before = len(intent.list_proposals(status="pending"))

    # Poll again with NOTHING changed -> no diff, nothing minted.
    assert svc.poll_once(intent, scanner=scan) == []
    assert len(intent.list_proposals(status="pending")) == n_before


def test_modified_mtime_is_detected(platform):
    svc = platform.sentinels
    svc.add("m", path="/m")
    fs = {"/m/x": 1.0}
    scan = _scanner(fs)
    assert svc.check("m", scanner=scan) == []  # baseline
    fs["/m/x"] = 2.0  # touched
    changed = svc.check("m", scanner=scan)
    assert [c["change"] for c in changed] == ["modified"]


# -- 4. Restart survival: state rehydrates, no re-fire -----------------------


def test_last_state_survives_a_fresh_registry(platform):
    svc = platform.sentinels
    svc.add("r", path="/r")
    fs = {"/r/a.txt": 1.0}
    scan = _scanner(fs)
    svc.check("r", scanner=scan)  # baseline persists seen={a.txt}

    # A brand-new registry over the SAME engine (simulates a daemon restart).
    svc2 = SentinelService(platform.engine)
    assert svc2.check("r", scanner=scan) == []  # a.txt already seen — no re-fire

    fs["/r/b.txt"] = 2.0  # a genuinely new file after restart fires once
    changed = svc2.check("r", scanner=scan)
    assert [c["path"] for c in changed] == ["/r/b.txt"]


def test_load_returns_persisted_sentinels(platform):
    svc = platform.sentinels
    svc.add("a", path="/a")
    svc.add("b", path="/b")
    svc2 = SentinelService(platform.engine)
    assert {s.name for s in svc2.load()} == {"a", "b"}


# -- 5. CRUD -----------------------------------------------------------------


def test_crud(platform):
    svc = platform.sentinels
    rec = svc.add("c", path="/c", glob="*.md", task="t", risk="med")
    assert rec.id.startswith("sentinel_")
    assert rec.decoded_config() == {"path": "/c", "glob": "*.md"}
    assert rec.risk == "med"

    assert svc.get("c").name == "c"
    assert svc.get("missing") is None
    assert {s.name for s in svc.list()} == {"c"}

    disabled = svc.set_enabled("c", False)
    assert disabled is not None and disabled.enabled is False
    assert svc.set_enabled("missing", True) is None

    assert svc.remove("c") is True
    assert svc.get("c") is None
    assert svc.remove("missing") is False


def test_add_validation(platform):
    svc = platform.sentinels
    with pytest.raises(ValueError):
        svc.add("", path="/x")  # empty name
    with pytest.raises(ValueError):
        svc.add("k", path="/x", kind="email")  # not wired in this slice
    with pytest.raises(ValueError):
        svc.add("nopath", path="")  # file sentinel needs a path

    svc.add("dupe", path="/x")
    with pytest.raises(ValueError):
        svc.add("dupe", path="/y")  # duplicate name


def test_disabled_sentinel_is_not_polled(platform):
    svc, intent = platform.sentinels, platform.intent
    svc.add("off", path="/o")
    svc.set_enabled("off", False)
    fs = {"/o/a": 1.0}
    scan = _scanner(fs)
    # Disabled -> skipped entirely; not even baselined, never fires.
    assert svc.poll_once(intent, scanner=scan) == []
    assert intent.list_proposals() == []


# -- 6. add_backlog helper (suggest-only minting primitive) ------------------


def test_add_backlog_is_suggest_only_and_dedupes(platform):
    intent = platform.intent
    p1 = intent.add_backlog(title="Noticed X", task="look at X", source="sentinel")
    assert p1 is not None
    assert p1.source == "sentinel"
    assert p1.status == "pending"
    assert p1.session_id is None

    # Same title+source while pending -> reuse, don't pile up.
    p2 = intent.add_backlog(title="Noticed X", task="look at X", source="sentinel")
    assert p2.id == p1.id
    assert len(intent.list_proposals(status="pending")) == 1

    # An empty title is a no-op (never raises).
    assert intent.add_backlog(title="  ", task="x") is None


# -- 7. the old trigger handler table is retired (leap 05-b) ----------------


def test_the_old_trigger_stubs_are_retired_and_a_folder_starts_work_through_a_rule():
    """Leap 05-b: ``workflows/triggers``' handler table (``register_trigger``,
    its ``file`` handler that made a suggest-only watcher, the stubs that
    raised "not implemented" for webhook / email / calendar / api, and the
    unused ``CronScheduler``) is gone: nothing in the app called it. A folder
    starts work through a rule (``reflex_create`` source ``file``, its own
    watcher) and every kind goes through the one start hub."""
    from iron_jarvis import workflows
    from iron_jarvis.workflows import triggers as T

    for name in ("register_trigger", "file_trigger", "TRIGGER_HANDLERS", "manual_handler",
                 "webhook_handler", "email_handler", "calendar_handler", "api_handler", "CronScheduler"):
        assert not hasattr(T, name), name
    assert "CronScheduler" not in workflows.__all__
    # What stays: reading a [[triggers]] block and checking a cron line.
    assert T.parse_triggers({"triggers": [{"name": "n", "schedule": "0 8 * * *", "workflow": "w"}]})[0].kind == "cron"
    assert T.validate_cron("0 8 * * *") and not T.validate_cron("nope")


# -- 8. agent tool -----------------------------------------------------------


def test_sentinel_tools_factory(platform):
    assert [t.name for t in sentinel_tools(platform)] == ["sentinel_add"]


async def test_sentinel_add_tool(platform, tmp_path):
    tool = SentinelAddTool(platform)
    ctx = _ctx(platform, tmp_path)
    res = await tool.execute(
        {"name": "docs", "path": str(tmp_path), "task": "review docs"}, ctx
    )
    assert res.ok
    assert res.data["name"] == "docs"
    assert platform.sentinels.get("docs") is not None

    # Duplicate -> ok=False, surfaced as an error (not an exception).
    dup = await tool.execute({"name": "docs", "path": str(tmp_path)}, ctx)
    assert dup.ok is False and dup.error


# -- 9. HTTP endpoints -------------------------------------------------------


def test_sentinel_endpoints_crud(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    created = client.post(
        "/sentinels", json={"name": "w", "path": str(tmp_path), "task": "t"}
    ).json()
    assert created["name"] == "w"
    assert created["enabled"] is True

    listing = client.get("/sentinels").json()
    assert [s["name"] for s in listing["sentinels"]] == ["w"]

    # Duplicate -> 400.
    dup = client.post("/sentinels", json={"name": "w", "path": str(tmp_path)})
    assert dup.status_code == 400

    # v1.504.0 (north-star plan 4.8): + the change history row's id (its Undo).
    gone = client.delete("/sentinels/w").json()
    assert gone.pop("action_id", "").startswith("tool_")
    assert gone == {"deleted": "w"}
    assert client.delete("/sentinels/w").status_code == 404


# -- 10. the real (default) scanner is dependency-light + correct ------------


def test_default_scanner_detects_files(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.md").write_text("z", encoding="utf-8")

    # Directory scan = immediate file children only.
    flat = default_scanner(str(tmp_path))
    assert set(flat) == {str(tmp_path / "a.md"), str(tmp_path / "b.txt")}

    # A recursive glob pattern reaches the subdirectory.
    md = default_scanner(str(tmp_path), "**/*.md")
    assert str(sub / "c.md") in md


def test_diff_state_new_and_modified():
    prev = {"a": 1.0, "b": 1.0}
    cur = {"a": 1.0, "b": 2.0, "c": 1.0}  # b modified, c new, a unchanged
    out = {d["path"]: d["change"] for d in diff_state(prev, cur)}
    assert out == {"b": "modified", "c": "new"}


# -- swarm-review fixes: fs-policy guard + no-lost-change --------------------
def test_add_rejects_protected_path(platform):
    # The watcher must not be pointable at the Fernet secret/key dirs (fs_policy).
    import pytest as _pytest

    with _pytest.raises(ValueError):
        platform.sentinels.add("snoop", path=str(platform.config.home / "secrets"))


def test_change_while_proposal_pending_is_not_lost(platform):
    svc, intent = platform.sentinels, platform.intent
    svc.add("acc", path="/w")
    fs: dict[str, float] = {}
    scan = _scanner(fs)
    svc.poll_once(intent, scanner=scan)  # baseline
    fs["/w/a.md"] = 1.0
    svc.poll_once(intent, scanner=scan)  # first change -> one proposal
    assert len(intent.list_proposals(status="pending")) == 1
    # A DISTINCT new file while that proposal is still pending must NOT be lost.
    fs["/w/b.md"] = 1.0
    svc.poll_once(intent, scanner=scan)
    pending = intent.list_proposals(status="pending")
    assert len(pending) == 1  # stable title -> still one proposal
    assert "b.md" in pending[0].rationale  # ...refreshed to the newest change


# -- next leaps 03-a: a watcher's suggestion carries facts and counts ---------
def test_a_watchers_suggestion_carries_facts_and_counts_toward_the_days_limit(platform):
    """The names of the new files and "since when" are its facts (never a
    path on the page), a Yes runs in the watched folder, and it counts toward
    "Suggestions about your work: most in a day" like any other."""
    from iron_jarvis.motivation import notices

    svc, intent = platform.sentinels, platform.intent
    svc.add("intake", path="/clients/hartleys/intake", task="file the new statements")
    fs: dict[str, float] = {}
    scan = _scanner(fs)
    svc.poll_once(intent, scanner=scan)  # baseline
    fs["/clients/hartleys/intake/March statement.pdf"] = 1.0
    fs["/clients/hartleys/intake/April statement.pdf"] = 1.0
    [p] = svc.poll_once(intent, scanner=scan)
    assert p.title == "Your watcher intake saw 2 new or changed files in intake"
    facts = [f["text"] for f in p.decoded_evidence()]
    assert facts[0].startswith("New or changed: ") and "March statement.pdf" in facts[0]
    assert facts[1].startswith("Since ")
    assert facts[2] == "Watched folder: intake"
    assert p.decoded_action()["how"] == "run" and p.folder == "/clients/hartleys/intake"
    # A later change while it waits folds into it and keeps the first "since".
    fs["/clients/hartleys/intake/May statement.pdf"] = 1.0
    svc.poll_once(intent, scanner=scan)
    [again] = intent.list_proposals(status="pending")
    assert again.id == p.id and "3 new or changed files" in again.title
    assert [f for f in again.decoded_evidence() if f.get("role") == "since"][0]["at"] ==         [f for f in p.decoded_evidence() if f.get("role") == "since"][0]["at"]
    assert notices.made_today(platform.engine) == 1
    # Counted: with the day's limit reached, the next watcher suggests nothing.
    intent.reject(p.id)
    platform.config.notice_max_per_day = 1
    svc.set_enabled("intake", False)
    svc.add("second", path="/clients/smith/intake")
    fs2: dict[str, float] = {}
    svc.poll_once(intent, scanner=_scanner(fs2))
    fs2["/clients/smith/intake/a.pdf"] = 1.0
    assert svc.poll_once(intent, scanner=_scanner(fs2)) == []
    # 03-a review: held back, never lost. The change waits on the watcher's
    # row; a sweep inside the 24 hours still makes nothing, and the first
    # sweep after they are up suggests it, with no new file since.
    assert svc.poll_once(intent, scanner=_scanner(fs2)) == []
    _age_suggestions(platform, hours=25)
    [held] = svc.poll_once(intent, scanner=_scanner(fs2))
    assert held.title == "Your watcher second saw 1 new or changed file in intake"
    assert "a.pdf" in held.decoded_evidence()[0]["text"]
    assert svc.poll_once(intent, scanner=_scanner(fs2)) == []  # made once, then cleared
    intent.reject(held.id)
    # Its kind's switch stops it too.
    platform.config.notice_max_per_day = 10
    platform.config.notice_new_files = False
    fs2["/clients/smith/intake/b.pdf"] = 1.0
    assert svc.poll_once(intent, scanner=_scanner(fs2)) == []



def _age_suggestions(platform, *, hours: float) -> None:
    """Move every suggestion's ``created_at`` back (the day's limit counts
    the last 24 hours from the rows)."""
    from datetime import timedelta

    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.motivation.models import ProposalRecord

    with session_scope(platform.engine) as db:
        for r in db.exec(select(ProposalRecord)):
            r.created_at = r.created_at - timedelta(hours=hours)
            db.add(r)
        db.commit()
