"""The Skill Curator (v1.297.0): agent-made skills get provenance and a
lifecycle — usage counted, stale ones ARCHIVED (moved, never deleted), pinned
ones exempt, the user's own never touched by the sweep.

Offline, on ``build_platform(tmp_path)`` with a hermetic registry (builtin +
user roots only). Pins, in order:

1. the frontmatter round-trips provenance AND unknown keys; a plain SKILL.md
   reads as user/unpinned;
2. ``skill_create`` stamps agent + session; ``approve`` stamps proposal;
3. ``.archive`` is skipped by discovery (one-level AND recursive), so an
   archived skill is not listed, not searchable and not injected;
4. ``inject`` counts ``inject_count`` and NOT ``use_count``;
5. candidates: user never, too young never, pinned never, used recently never,
   old + unused yes, old + idle yes;
6. dry run moves nothing; a real sweep backs up BEFORE the first move, moves,
   stamps ``archived_at``, rescans, writes ``.curator.json``;
7. restore brings it back (and refuses a folder collision); an archive-name
   collision gets a suffix; pin/unpin rewrite the frontmatter and keep mtime;
8. the routes, through the REAL app factory;
9. the loop ticks ok and swallows a raised sweep.
"""

from __future__ import annotations

import asyncio
import json
import os
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.platform import build_platform
from iron_jarvis.skills import framework
from iron_jarvis.skills.curator import SkillCurator
from iron_jarvis.skills.learning import SkillLearningEngine
from iron_jarvis.skills.learning_models import SkillProposalRecord, SkillStatRecord
from iron_jarvis.skills.loader import (
    SKILL_FILE,
    load_skill,
    read_skill_md,
    save_skill,
    update_frontmatter,
)
from iron_jarvis.skills.tools import SkillCreateTool
from iron_jarvis.tools.base import ToolContext

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BODY = "1. Open the ledger.\n2. Reconcile every line."


# --------------------------------------------------------------------------- #
# fixtures + helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path, monkeypatch):
    # Hermetic registry: a dev box's ~/.claude and ~/.codex hold dozens of
    # skills that would make name/count assertions depend on host state.
    monkeypatch.setattr(framework, "external_skill_roots", lambda: [])
    monkeypatch.setattr(framework, "marketplace_catalog_dirs", lambda home=None: [])
    return build_platform(str(tmp_path))


@pytest.fixture
def curator(platform) -> SkillCurator:
    return SkillCurator(
        platform.config.home,
        platform.skills,
        platform.skill_learning,
        config=platform.config,
        now=lambda: NOW,
    )


def _root(platform) -> Path:
    return platform.config.home / "skills"


def _age(skill_dir: Path, days: float) -> None:
    """Make the SKILL.md ``days`` old relative to NOW (created_at = mtime)."""
    ts = (NOW - timedelta(days=days)).timestamp()
    os.utime(skill_dir / SKILL_FILE, (ts, ts))


def _make(platform, name, *, created_by=None, age_days=0.0, pinned=None) -> Path:
    d = save_skill(
        _root(platform), name, f"{name} description", BODY,
        created_by=created_by, pinned=pinned,
    )
    _age(d, age_days)
    platform.skills.repopulate(platform.config.home, None)
    return d


def _stat(platform, name, *, use=0, inject=0, used_days_ago=None, injected_days_ago=None):
    with session_scope(platform.engine) as db:
        row = db.get(SkillStatRecord, name) or SkillStatRecord(skill_name=name)
        row.use_count = use
        row.inject_count = inject
        if used_days_ago is not None:
            row.last_used_at = NOW - timedelta(days=used_days_ago)
        if injected_days_ago is not None:
            row.last_injected_at = NOW - timedelta(days=injected_days_ago)
        db.add(row)
        db.commit()


def _stat_row(platform, name) -> SkillStatRecord | None:
    with session_scope(platform.engine) as db:
        row = db.get(SkillStatRecord, name)
        if row is not None:
            db.refresh(row)
            db.expunge(row)
        return row


def _names(cands) -> set[str]:
    return {c["name"] for c in cands}


# --------------------------------------------------------------------------- #
# 1. frontmatter round-trip
# --------------------------------------------------------------------------- #


def test_frontmatter_round_trips_provenance_and_unknown_keys(platform):
    root = _root(platform)
    d = save_skill(root, "ledger-fix", "desc", BODY, created_by="agent",
                   created_session="sess-1", pinned=True)
    # A key nobody in this codebase knows about must survive every rewrite.
    update_frontmatter(d, author_note="hand-written by a reviewer")
    # Overwrite in place with NO provenance passed: the file's own survives.
    save_skill(root, "ledger-fix", "desc v2", "new body")
    meta, body = read_skill_md(d)
    assert meta["name"] == "ledger-fix" and meta["description"] == "desc v2"
    assert body.strip() == "new body"
    assert meta["created_by"] == "agent"
    assert meta["created_session"] == "sess-1"
    assert meta["pinned"] is True
    assert meta["author_note"] == "hand-written by a reviewer"

    sk = load_skill(d)
    assert (sk.created_by, sk.created_session, sk.pinned, sk.archived_at) == (
        "agent", "sess-1", True, ""
    )
    # An explicit value WINS over the file's.
    save_skill(root, "ledger-fix", "desc", BODY, pinned=False, created_by="proposal")
    sk = load_skill(d)
    assert sk.pinned is False and sk.created_by == "proposal"
    assert read_skill_md(d)[0]["author_note"] == "hand-written by a reviewer"


def test_plain_skill_md_reads_as_user_unpinned(platform):
    d = _root(platform) / "plain"
    d.mkdir(parents=True)
    (d / SKILL_FILE).write_text("---\nname: plain\ndescription: d\n---\n\nbody\n", encoding="utf-8")
    sk = load_skill(d)
    assert sk.created_by == "user" and sk.created_session == "" and sk.pinned is False
    # A user's own save writes NO provenance keys — their files stay as they were.
    d2 = save_skill(_root(platform), "mine", "d", BODY)
    meta, _ = read_skill_md(d2)
    assert set(meta) == {"name", "description"}
    with pytest.raises(ValueError):
        save_skill(_root(platform), "x", "d", BODY, created_by="robot")


# --------------------------------------------------------------------------- #
# 2. who stamps what
# --------------------------------------------------------------------------- #


def test_skill_create_tool_stamps_agent_and_session(platform, tmp_path):
    tool = SkillCreateTool(platform.skills, platform.config)
    ctx = ToolContext(
        workspace=tmp_path, session_id="sess-agent-42", agent_run_id="run1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    res = asyncio.run(tool.execute(
        {"name": "Vendor Recon", "description": "d", "instructions": BODY}, ctx
    ))
    assert res.ok, res.error
    sk = platform.skills.get("Vendor Recon")
    assert sk is not None
    assert sk.created_by == "agent" and sk.created_session == "sess-agent-42"
    assert sk.pinned is False


def test_skill_create_tool_never_flips_a_user_skill_to_agent_made(platform, tmp_path):
    """REVIEW v1.297.0: `skill_create` overwrites an existing slug in place
    (v1.90.0 contract). The provenance stamp must land on a NEW file only —
    stamping "agent" on an overwrite would turn the user's own skill into a
    sweep candidate the moment an agent rewrote it (and strip its pin)."""
    mine = save_skill(_root(platform), "Vendor Recon", "mine", BODY, pinned=True)
    assert read_skill_md(mine)[0].get("created_by") is None  # the user's: no key
    tool = SkillCreateTool(platform.skills, platform.config)
    ctx = ToolContext(
        workspace=tmp_path, session_id="sess-agent-43", agent_run_id="run1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    res = asyncio.run(tool.execute(
        {"name": "Vendor Recon", "description": "rewritten", "instructions": "NEW BODY"}, ctx
    ))
    assert res.ok, res.error
    meta, body = read_skill_md(mine)
    assert body.strip() == "NEW BODY" and meta["description"] == "rewritten"
    assert meta.get("created_by") is None and meta.get("created_session") is None
    assert meta["pinned"] is True
    sk = platform.skills.get("Vendor Recon")
    assert sk is not None and sk.created_by == "user" and sk.pinned is True
    # And a brand-new slug from the same tool IS stamped (the positive control).
    res = asyncio.run(tool.execute(
        {"name": "Brand New", "description": "d", "instructions": BODY}, ctx
    ))
    assert res.ok, res.error
    assert platform.skills.get("Brand New").created_by == "agent"


def test_approve_stamps_proposal_with_its_first_session(platform):
    learning = SkillLearningEngine(platform)
    rec = SkillProposalRecord(
        kind="create", skill_name="weekly-report", description="d",
        body_md="---\nname: weekly-report\ndescription: Use weekly.\n---\n\n# Steps\n\n1. Gather.\n2. Send.\n",
        signature="create::weekly report", source_session_ids=json.dumps(["sess-p1", "sess-p2"]),
    )
    pid = rec.id
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
    out = learning.approve(pid)
    assert out.status == "approved"
    sk = platform.skills.get("weekly-report")
    assert sk is not None
    assert sk.created_by == "proposal" and sk.created_session == "sess-p1"

    # A REFINE that overwrites the user's own file keeps it the user's.
    _make(platform, "mine")
    rec2 = SkillProposalRecord(
        kind="refine", skill_name="mine", description="d",
        body_md="---\nname: mine\ndescription: better\n---\n\nimproved body\n",
        signature="refine::mine", source_session_ids=json.dumps(["sess-r"]),
    )
    pid2 = rec2.id
    with session_scope(platform.engine) as db:
        db.add(rec2)
        db.commit()
    learning.approve(pid2)
    sk2 = platform.skills.get("mine")
    assert sk2.instructions == "improved body" and sk2.created_by == "user"


# --------------------------------------------------------------------------- #
# 3. the archive is out of service everywhere
# --------------------------------------------------------------------------- #


def test_archive_dir_is_skipped_by_discovery_search_and_inject(platform):
    parked = _root(platform) / ".archive" / "old-trick"
    parked.mkdir(parents=True)
    (parked / SKILL_FILE).write_text(
        "---\nname: old-trick\ndescription: zebra giraffe\narchived_at: '2026-01-01T00:00:00+00:00'\n---\n\nSECRET OLD BODY\n",
        encoding="utf-8",
    )
    _make(platform, "live-one")  # repopulates
    reg = platform.skills
    assert reg.get("live-one") is not None
    assert reg.get("old-trick") is None
    assert "old-trick" not in {s.name for s in reg.list()}
    assert reg.search("zebra giraffe") == []
    assert reg.inject("SYS", ["old-trick"]) == "SYS"
    # The recursive walk (external roots / extra paths) skips it too: an extra
    # path pointing at the skills folder itself must not resurrect it.
    reg2 = framework.SkillRegistry().discover_recursive(_root(platform), source="custom")
    assert reg2.get("live-one") is not None
    assert reg2.get("old-trick") is None


# --------------------------------------------------------------------------- #
# 4. inject is counted as an injection, not a use
# --------------------------------------------------------------------------- #


def test_inject_counts_inject_count_and_not_use_count(platform):
    _make(platform, "counted")
    assert platform.skills.on_inject is not None, "platform must bind the seam"
    out = platform.skills.inject("SYS", ["counted", "no-such-skill"])
    assert "## counted" in out
    platform.skills.inject("SYS", ["counted"])
    platform.skill_learning.flush_injected()
    row = _stat_row(platform, "counted")
    assert row is not None
    assert row.inject_count == 2
    assert row.last_injected_at is not None
    assert row.use_count == 0 and row.last_used_at is None and row.score_sum == 0.0
    assert _stat_row(platform, "no-such-skill") is None
    # The stats view carries the new counter beside the old ones.
    view = {v["skill_name"]: v for v in platform.skill_learning.stats()["skills"]}
    assert view["counted"]["inject_count"] == 2 and view["counted"]["use_count"] == 0


def test_inject_returns_before_the_count_lands(platform):
    """The count is written OFF the caller's thread (inject runs in async
    code): with the one-thread executor held, inject() returns, the row is
    untouched, and the count lands only once the executor is released."""
    import threading

    from iron_jarvis.skills import learning as learning_mod

    _make(platform, "counted")
    gate = threading.Event()
    held = learning_mod._INJECT_EXECUTOR.submit(gate.wait)  # parks the one worker
    try:
        done = threading.Event()

        def call():
            platform.skills.inject("SYS", ["counted"])
            done.set()

        threading.Thread(target=call, daemon=True).start()
        assert done.wait(5.0), "inject() blocked behind the executor"
        assert _stat_row(platform, "counted") is None  # nothing landed yet
    finally:
        gate.set()
    held.result(timeout=5.0)
    platform.skill_learning.flush_injected()
    row = _stat_row(platform, "counted")
    assert row is not None and row.inject_count == 1 and row.use_count == 0


# --------------------------------------------------------------------------- #
# 5. candidates
# --------------------------------------------------------------------------- #


def test_candidates_apply_every_exemption(platform, curator):
    _make(platform, "user-old", created_by=None, age_days=400)          # the user's: never
    _make(platform, "agent-young", created_by="agent", age_days=3)       # too young
    _make(platform, "agent-old", created_by="agent", age_days=20)        # yes: old, never active
    _make(platform, "proposal-old", created_by="proposal", age_days=20)  # yes
    _make(platform, "agent-pinned", created_by="agent", age_days=90, pinned=True)
    _make(platform, "agent-fresh-use", created_by="agent", age_days=90)
    _stat(platform, "agent-fresh-use", use=3, used_days_ago=2)
    _make(platform, "agent-fresh-inject", created_by="agent", age_days=90)
    _stat(platform, "agent-fresh-inject", inject=1, injected_days_ago=5)
    _make(platform, "agent-idle", created_by="agent", age_days=90)
    _stat(platform, "agent-idle", use=1, used_days_ago=45)

    cands = curator.candidates()
    assert _names(cands) == {"agent-old", "proposal-old", "agent-idle"}
    by = {c["name"]: c for c in cands}
    assert "never loaded or injected" in by["agent-old"]["reason"]
    assert by["agent-old"]["age_days"] == pytest.approx(20, abs=0.1)
    assert by["agent-idle"]["reason"].startswith("not loaded or injected for 45")
    assert by["agent-idle"]["idle_days"] == pytest.approx(45, abs=0.1)
    assert by["agent-idle"]["use_count"] == 1
    for c in cands:
        assert c["created_by"] in ("agent", "proposal") and c["pinned"] is False
    # The user's skill is exempt by provenance, not by age or activity.
    assert "user-old" not in _names(cands)


# --------------------------------------------------------------------------- #
# 6. the sweep
# --------------------------------------------------------------------------- #


def test_sweep_dry_run_moves_nothing(platform, curator):
    d = _make(platform, "agent-old", created_by="agent", age_days=20)
    _make(platform, "keep-me")
    out = curator.sweep(dry_run=True)
    assert out["dry_run"] is True
    assert out["archived"] == [] and out["would_archive"] == ["agent-old"]
    assert out["backup"] is None and "keep-me" in out["kept"]
    assert d.is_dir() and not curator.archive_root.exists()
    assert platform.skills.get("agent-old") is not None
    assert not (platform.config.home / "backups").exists()


def test_sweep_backs_up_first_then_moves_stamps_rescans_and_records(platform, curator):
    d = _make(platform, "agent-old", created_by="agent", age_days=20)
    old_mtime = (d / SKILL_FILE).stat().st_mtime
    _make(platform, "keep-me")
    (d / "scripts").mkdir()
    (d / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")

    out = curator.sweep()
    assert out["archived"] == ["agent-old"] and out["dry_run"] is False
    assert "keep-me" in out["kept"] and "agent-old" not in out["kept"]

    # The backup: a tarball of the skills folder as it was BEFORE the move.
    backup = Path(out["backup"])
    assert backup.is_file() and backup.parent == platform.config.home / "backups"
    assert backup.name.startswith("skills-") and backup.name.endswith(".tar.gz")
    with tarfile.open(backup) as tar:
        names = tar.getnames()
    assert any(n.replace("\\", "/").endswith("skills/agent-old/SKILL.md") for n in names)
    assert any(n.replace("\\", "/").endswith("skills/agent-old/scripts/run.py") for n in names)
    assert not any(".archive" in n for n in names)

    # Moved, not deleted — the whole folder, the stamp in its frontmatter.
    assert not d.exists()
    parked = curator.archive_root / "agent-old"
    assert (parked / SKILL_FILE).is_file() and (parked / "scripts" / "run.py").is_file()
    meta, body = read_skill_md(parked)
    assert meta["archived_at"] == NOW.isoformat() and meta["archived_from"] == "agent-old"
    assert meta["created_by"] == "agent" and body.strip() == BODY
    assert (parked / SKILL_FILE).stat().st_mtime == pytest.approx(old_mtime, abs=1)

    # Rescanned: gone from the registry, listed in the archive.
    assert platform.skills.get("agent-old") is None
    assert "agent-old" not in {s.name for s in platform.skills.list()}
    assert platform.skills.get("keep-me") is not None
    arch = curator.archived()
    assert [a["name"] for a in arch] == ["agent-old"]
    assert arch[0]["archived_at"] == NOW.isoformat()

    # The run record.
    state = json.loads(curator.state_path.read_text(encoding="utf-8"))
    assert state["last_sweep_at"] == NOW.isoformat()
    assert state["last_result"]["archived"] == ["agent-old"]
    st = curator.status()
    assert st["last_sweep_at"] == NOW.isoformat() and st["archived"] == 1
    assert st["candidates"] == 0 and st["enabled"] is True
    assert st["settings"]["curator_min_age_days"] == 14

    # Nothing to do: no second tarball.
    before = sorted((platform.config.home / "backups").iterdir())
    out2 = curator.sweep()
    assert out2["archived"] == [] and out2["backup"] is None
    assert sorted((platform.config.home / "backups").iterdir()) == before


# --------------------------------------------------------------------------- #
# 7. restore, collisions, pin
# --------------------------------------------------------------------------- #


def test_restore_brings_it_back_and_refuses_a_folder_collision(platform, curator):
    _make(platform, "agent-old", created_by="agent", age_days=20)
    curator.sweep()
    assert platform.skills.get("agent-old") is None

    view = curator.restore("agent-old")
    assert view is not None and view["archived_at"] is None
    assert view["created_by"] == "agent"
    live = _root(platform) / "agent-old"
    assert (live / SKILL_FILE).is_file() and not (curator.archive_root / "agent-old").exists()
    meta, _ = read_skill_md(live)
    assert "archived_at" not in meta and "archived_from" not in meta
    assert platform.skills.get("agent-old") is not None
    assert curator.restore("agent-old") is None  # nothing left in the archive

    # Archive by hand, then the user makes a NEW live skill in that folder.
    assert curator.archive("agent-old") is not None
    _make(platform, "agent-old")  # a fresh user-authored one in the same slug
    with pytest.raises(ValueError, match="already uses the folder"):
        curator.restore("agent-old")
    assert (curator.archive_root / "agent-old" / SKILL_FILE).is_file()  # untouched


def test_archive_name_collision_gets_a_stamp_suffix(platform, curator):
    _make(platform, "twice", created_by="agent", age_days=20)
    assert curator.archive("twice")["folder"] == "twice"
    _make(platform, "twice", created_by="agent", age_days=20)
    second = curator.archive("twice")
    assert second["folder"] == f"twice-{NOW.strftime('%Y%m%d-%H%M%S')}"
    assert {a["folder"] for a in curator.archived()} == {"twice", second["folder"]}
    assert curator.archive("twice") is None  # nothing live by that name now


def test_manual_archive_takes_the_users_own_skill(platform, curator):
    _make(platform, "mine")  # user-authored: never a sweep candidate...
    assert curator.candidates() == []
    out = curator.archive("mine")  # ...but the user may park it by hand
    assert out is not None and out["created_by"] == "user"
    assert platform.skills.get("mine") is None


def test_pin_and_unpin_rewrite_the_frontmatter_and_keep_created_at(platform, curator):
    d = _make(platform, "agent-old", created_by="agent", age_days=20)
    mtime = (d / SKILL_FILE).stat().st_mtime
    assert _names(curator.candidates()) == {"agent-old"}

    view = curator.pin("agent-old")
    assert view["pinned"] is True
    assert read_skill_md(d)[0]["pinned"] is True
    assert platform.skills.get("agent-old").pinned is True
    assert curator.candidates() == []
    assert (d / SKILL_FILE).stat().st_mtime == pytest.approx(mtime, abs=1)

    view = curator.unpin("agent-old")
    assert view["pinned"] is False
    assert _names(curator.candidates()) == {"agent-old"}
    assert curator.pin("nope") is None and curator.unpin("nope") is None


# --------------------------------------------------------------------------- #
# 8. routes, through the real app factory
# --------------------------------------------------------------------------- #


def _ensure_registered(app) -> bool:
    """True when app.py registered the module itself. Otherwise register it
    here — and move its routes AHEAD of agents.py's ``/skills/{name}``
    catch-all, which is exactly where app.py must register it too."""
    if any(getattr(r, "path", "") == "/skills/curator" for r in app.routes):
        return True
    from iron_jarvis.daemon.routes import skills_curator

    before = len(app.router.routes)
    skills_curator.register(app, app.state.d)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = added
    return False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(framework, "external_skill_roots", lambda: [])
    monkeypatch.setattr(framework, "marketplace_catalog_dirs", lambda home=None: [])
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))
    app.state.curator_wired_by_app = _ensure_registered(app)
    with TestClient(app) as c:
        yield c


def test_routes_overview_run_pin_archive_restore(client):
    platform = client.app.state.platform
    cur = platform.skill_curator
    assert cur is not None
    cur._now = lambda: NOW
    _make(platform, "agent-old", created_by="agent", age_days=20)
    _make(platform, "mine")

    r = client.get("/skills/curator")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["last_sweep_at"] is None
    assert [c["name"] for c in body["candidates"]] == ["agent-old"]
    assert body["archived"] == [] and body["settings"]["curator_stale_after_days"] == 30

    r = client.post("/skills/curator/run", json={"dry_run": True})
    assert r.status_code == 200 and r.json()["would_archive"] == ["agent-old"]
    assert r.json()["archived"] == []
    assert client.get("/skills/agent-old").status_code == 200  # still live

    r = client.post("/skills/curator/agent-old/pin")
    assert r.status_code == 200 and r.json() == {"ok": True, "skill": r.json()["skill"]}
    assert r.json()["skill"]["pinned"] is True
    assert client.get("/skills/curator").json()["candidates"] == []
    r = client.post("/skills/curator/agent-old/unpin")
    assert r.status_code == 200 and r.json()["skill"]["pinned"] is False

    r = client.post("/skills/curator/run")
    assert r.status_code == 200, r.text
    assert r.json()["archived"] == ["agent-old"] and r.json()["backup"]
    assert client.get("/skills/agent-old").status_code == 404
    over = client.get("/skills/curator").json()
    assert [a["name"] for a in over["archived"]] == ["agent-old"]
    assert over["last_sweep_at"] == NOW.isoformat()

    r = client.post("/skills/curator/agent-old/restore")
    assert r.status_code == 200 and r.json()["skill"]["archived_at"] is None
    assert client.get("/skills/agent-old").status_code == 200

    # By hand, the user's own skill goes too.
    r = client.post("/skills/curator/mine/archive")
    assert r.status_code == 200 and r.json()["skill"]["created_by"] == "user"
    assert client.get("/skills/mine").status_code == 404
    # Collision on restore → 409 with the sentence.
    _make(platform, "mine")
    r = client.post("/skills/curator/mine/restore")
    assert r.status_code == 409 and "already uses the folder" in r.json()["detail"]

    for verb in ("pin", "unpin", "archive", "restore"):
        r = client.post(f"/skills/curator/no-such-skill/{verb}")
        assert r.status_code == 404, (verb, r.text)
        assert "no skill named 'no-such-skill'" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# 9. the loop
# --------------------------------------------------------------------------- #


def test_run_forever_reports_each_cycle_and_swallows_a_raise(platform, curator):
    ticks: list[tuple[bool, str]] = []
    calls = {"n": 0}

    def sweep(dry_run=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk said no")
        return {"archived": [], "kept": [], "backup": None}

    curator.sweep = sweep  # type: ignore[method-assign]

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(curator.run_forever(
            stop, lambda ok, exc: ticks.append((ok, str(exc or ""))),
            first_delay_s=0.0, interval_s=0.01,
        ))
        for _ in range(200):
            if len(ticks) >= 3:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(drive())
    assert ticks[0] == (False, "disk said no")
    assert ticks[1] == (True, "") and ticks[2] == (True, "")
    assert calls["n"] >= 3  # the loop outlived the raise


def test_run_forever_skips_the_sweep_when_switched_off(platform, curator, monkeypatch):
    monkeypatch.setattr(curator, "settings", lambda: {
        "curator_enabled": False, "curator_min_age_days": 14,
        "curator_stale_after_days": 30, "curator_interval_hours": 24,
    })
    ran = []
    curator.sweep = lambda dry_run=False: ran.append(1)  # type: ignore[method-assign]
    ticks = []

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(curator.run_forever(
            stop, lambda ok, exc: ticks.append(ok), first_delay_s=0.0, interval_s=0.01
        ))
        for _ in range(100):
            if len(ticks) >= 2:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    asyncio.run(drive())
    assert ticks[:2] == [True, True] and ran == []
