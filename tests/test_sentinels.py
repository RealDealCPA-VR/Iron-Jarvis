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
from iron_jarvis.workflows.triggers import TriggerSpec, register_trigger


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
    assert p.source == "sentinel"
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


# -- 7. file trigger wiring --------------------------------------------------


def test_file_trigger_registers_a_sentinel(platform):
    spec = TriggerSpec(
        kind="file",
        name="csv_watch",
        workflow="ingest",
        extra={"path": "/data", "glob": "*.csv", "task": "ingest new csvs"},
    )
    rec = register_trigger(spec, lambda: None, sentinels=platform.sentinels)
    assert rec.name == "csv_watch"
    got = platform.sentinels.get("csv_watch")
    assert got is not None
    assert got.decoded_config() == {"path": "/data", "glob": "*.csv"}
    assert got.task == "ingest new csvs"


def test_file_trigger_requires_a_sentinel_service():
    spec = TriggerSpec(kind="file", name="t", workflow="w", extra={"path": "/x"})
    with pytest.raises(ValueError):
        register_trigger(spec, lambda: None)  # no sentinels -> clear error


def test_file_trigger_requires_a_path(platform):
    spec = TriggerSpec(kind="file", name="t", workflow="w", extra={})
    with pytest.raises(ValueError):
        register_trigger(spec, lambda: None, sentinels=platform.sentinels)


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

    assert client.delete("/sentinels/w").json() == {"deleted": "w"}
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
