"""Concurrency / data-integrity regression tests (the concurrency-lens fixes).

These actually exercise concurrent access (threads + asyncio.gather) to lock in the
fixes for: double-approve double-execution (H1), the config.toml write race (M1),
and the rolling-stats lost-update (M2). Offline (mock provider).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.config import _read_toml, persist_config_values
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType
from iron_jarvis.improvement.models import AgentStatRecord
from iron_jarvis.motivation import IntentEngine
from iron_jarvis.platform import build_platform


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


@pytest.fixture
def engine(platform):
    eng = IntentEngine(platform, orchestrator=Orchestrator(platform))
    platform.intent = eng
    return eng


# --- H1: concurrent Approve runs the proposal exactly once --------------------


async def test_concurrent_approve_runs_proposal_once(platform, engine):
    platform.config.autonomy_enabled = True  # level stays "suggest" → a proposal, no auto-run
    engine.add_goal("do a concurrency-safe thing", priority=5)
    out = await engine.deliberate()
    pid = out["proposal_id"]
    goal_id = engine.get_proposal(pid).goal_id

    # Two simultaneous approvals of the SAME proposal (double-click / retry / 2 tabs).
    results = await asyncio.gather(
        engine.approve(pid, wait=True),
        engine.approve(pid, wait=True),
        return_exceptions=True,
    )
    ran = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(ran) == 1, "exactly one approval should have executed"
    assert len(errs) == 1 and isinstance(errs[0], ValueError)
    # Budget booked exactly once, not twice.
    assert engine.get_goal(goal_id).actions_taken == 1


# --- M1: concurrent config.toml writes lose no keys and never 500 -------------


def test_concurrent_config_writes_preserve_all_keys(tmp_path):
    errors: list[Exception] = []

    def writer(i: int) -> None:
        try:
            persist_config_values(tmp_path, {f"k{i}": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors}"
    doc = _read_toml(tmp_path / "config.toml")
    # No lost update: every writer's key survived the read-modify-write race.
    assert all(doc.get(f"k{i}") == i for i in range(24))
    assert not list((tmp_path).glob("config.toml.*tmp*"))  # temps cleaned up


# --- M2: rolling-stats increment loses nothing under concurrent record_outcome -


def test_record_outcome_no_lost_increment_under_threads(platform):
    from sqlmodel import select

    from iron_jarvis.improvement.models import OutcomeRecord

    orch = Orchestrator(platform)
    K = 8
    # Distinct builder sessions (the real lost-update race is across different
    # sessions sharing ONE AgentStatRecord, not the same session — record_outcome
    # is idempotent per-session).
    sids = [asyncio.run(orch.run(f"task {i}", AgentType.BUILDER)).id for i in range(K)]

    # Clear their auto-recorded outcomes so we can re-record all K concurrently,
    # then capture the (already-incremented) baseline.
    with session_scope(platform.engine) as db:
        for o in db.exec(select(OutcomeRecord)):
            db.delete(o)
        db.commit()
        base = db.get(AgentStatRecord, "builder").session_count

    def rec(sid: str) -> None:
        platform.improvement.record_outcome(sid)

    threads = [threading.Thread(target=rec, args=(s,)) for s in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with session_scope(platform.engine) as db:
        final = db.get(AgentStatRecord, "builder").session_count
    assert final - base == K  # every increment landed (no read-modify-write loss)


# --- M3: secrets key rotation is crash-recoverable ----------------------------


def test_secrets_rotate_roundtrip(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine
    from iron_jarvis.secrets.manager import SecretsManager

    engine = make_engine(tmp_path / "x.db")
    init_db(engine)
    sm = SecretsManager(tmp_path, engine)
    sm.set("k", "v", kind="api_key")
    sm.rotate_key()
    assert sm.get("k") == "v" and sm.key_valid() is True


def test_secrets_rotate_recovery_promotes_staged_new_key(tmp_path):
    """Simulate a crash AFTER the re-encrypt commit but BEFORE the key promote:
    the DB ciphertext is under the new key, which sits staged at .new while the
    live key file is wrong. Boot recovery must promote .new and restore access."""
    from cryptography.fernet import Fernet

    from iron_jarvis.core.db import init_db, make_engine
    from iron_jarvis.secrets.manager import SecretsManager

    engine = make_engine(tmp_path / "x.db")
    init_db(engine)
    sm = SecretsManager(tmp_path, engine)
    sm.set("k", "v", kind="api_key")
    key_path = sm.root / ".secrets.key"
    good = key_path.read_bytes()  # the key the DB ciphertext is under
    (sm.root / ".secrets.key.new").write_bytes(good)  # staged, awaiting promote
    key_path.write_bytes(Fernet.generate_key())  # live key is WRONG (promote didn't run)

    sm2 = SecretsManager(tmp_path, engine)  # __init__ runs _recover_key
    assert sm2.key_valid() is True
    assert sm2.get("k") == "v"
    assert not (sm.root / ".secrets.key.new").exists()  # consumed by recovery


# --- M4: browser vault tolerates a corrupt blob -------------------------------


def test_browser_vault_load_tolerates_corrupt_blob(tmp_path):
    from iron_jarvis.providers.vault import BrowserVault

    v = BrowserVault(tmp_path / "browser")
    v.store("claude", {"cookies": "x"})
    (tmp_path / "browser" / "claude" / "session.enc").write_bytes(b"not a fernet token")
    assert v.load("claude") is None  # degrades to "not logged in", never raises


# --- M6: auto-backup archives a CONSISTENT DB snapshot ------------------------


def test_backup_db_snapshot_is_consistent(platform, tmp_path):
    import sqlite3
    import tarfile

    from iron_jarvis.maintenance import create_backup

    out = tmp_path / "b.tar.gz"
    create_backup(platform.config.home, out, engine=platform.engine, include_keys=True)
    names = tarfile.open(out).getnames()
    assert any(n.endswith("ironjarvis.db") for n in names)
    assert not any(n.endswith("ironjarvis.db-wal") for n in names)  # live WAL not archived

    ex = tmp_path / "ex"
    tarfile.open(out).extractall(ex, filter="data")
    dbf = next(p for p in ex.rglob("ironjarvis.db"))
    con = sqlite3.connect(str(dbf))
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        con.close()
    assert ok == "ok"  # the snapshot restores to a valid, consistent database


# --- Round 1: cancel-race HIGH, executing-strand reconcile, delete cascade -----


async def test_run_session_honors_a_cancel_that_won_the_race(platform):
    import os

    from iron_jarvis.core.models import SessionStatus

    orch = Orchestrator(platform)
    session = await orch.create_session("do real work", AgentType.BUILDER)
    # No task is registered yet (the create→register window): cancel marks the row
    # CANCELLED via cancel_session's else-branch.
    orch.cancel_session(session.id)
    assert orch.get_session(session.id).status is SessionStatus.CANCELLED

    result = await orch.run_session(session.id)
    # run_session must NOT run the agent — status stays CANCELLED and no work ran.
    assert result.status is SessionStatus.CANCELLED
    assert not os.path.exists(os.path.join(session.workspace_path, "RESULT.md"))


async def test_reconcile_resets_executing_proposals(platform):
    eng = IntentEngine(platform, orchestrator=Orchestrator(platform))
    platform.intent = eng
    platform.config.autonomy_enabled = True
    eng.add_goal("a goal")
    out = await eng.deliberate()
    pid = out["proposal_id"]
    # Simulate a crash mid-approve: the claim committed 'executing' but _book never ran.
    eng._claim_for_execution(pid)
    assert eng.get_proposal(pid).status == "executing"

    n = eng.reconcile_executing_proposals()
    assert n == 1
    assert eng.get_proposal(pid).status == "pending"  # retryable again


def test_delete_session_cascades_outcome_and_review(platform):
    from sqlmodel import select

    from iron_jarvis.core.models import PendingReviewRecord
    from iron_jarvis.improvement.models import OutcomeRecord

    orch = Orchestrator(platform)
    sid = asyncio.run(orch.run("a task", AgentType.BUILDER)).id
    with session_scope(platform.engine) as db:
        assert db.exec(select(OutcomeRecord).where(OutcomeRecord.session_id == sid)).first() is not None

    orch.delete_session(sid)
    with session_scope(platform.engine) as db:
        assert db.exec(select(OutcomeRecord).where(OutcomeRecord.session_id == sid)).first() is None
        assert db.exec(
            select(PendingReviewRecord).where(PendingReviewRecord.session_id == sid)
        ).first() is None


# --- Round 2: terminals lock, double-continue guard, embedding upsert ----------


def test_terminal_manager_concurrent_create_list_kill_is_safe():
    from iron_jarvis.terminals.backend import FakeBackend
    from iron_jarvis.terminals.manager import TerminalManager

    m = TerminalManager(max_sessions=1000)
    errors: list[Exception] = []

    def churn() -> None:
        try:
            for _ in range(15):
                s = m.create(backend=FakeBackend())
                m.list()  # iterate while others create/kill
                m.kill(s.id)
        except Exception as exc:  # noqa: BLE001 — e.g. dict changed size during iteration
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"terminal manager raced: {errors}"


def test_terminal_create_cap_not_overshot_under_concurrency():
    from iron_jarvis.terminals.backend import FakeBackend
    from iron_jarvis.terminals.manager import TerminalManager

    m = TerminalManager(max_sessions=5)
    created: list[object] = []
    lock = threading.Lock()

    def grab() -> None:
        try:
            s = m.create(backend=FakeBackend())
            with lock:
                created.append(s)
        except RuntimeError:
            pass  # hit the cap — expected

    threads = [threading.Thread(target=grab) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for s in m.list() if s["alive"]) <= 5  # cap never overshot


async def test_concurrent_continue_refuses_the_second(platform):
    orch = Orchestrator(platform)
    prev = await orch.run("first task", AgentType.BUILDER)  # finished, non-git
    results = await asyncio.gather(
        orch.continue_session(prev.id, "follow-up A"),
        orch.continue_session(prev.id, "follow-up B"),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception)]
    errs = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 1 and len(errs) == 1 and isinstance(errs[0], ValueError)


def test_embedding_cache_concurrent_first_write_does_not_raise(tmp_path):
    from iron_jarvis.core.db import init_db, make_engine
    from iron_jarvis.memory.embedding_cache import EmbeddingStore

    engine = make_engine(tmp_path / "e.db")
    init_db(engine)
    store = EmbeddingStore(engine)
    errors: list[Exception] = []

    def put() -> None:
        try:
            store.put("identical text", [0.1, 0.2, 0.3], model="m")  # same unique key
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=put) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"embedding put raised under concurrency: {errors}"


# --- Round 3: connection connect/disconnect stays consistent ------------------


def test_connection_connect_disconnect_consistent_under_threads(platform):
    reg = platform.connections
    errors: list[Exception] = []

    def connect() -> None:
        try:
            for _ in range(25):
                reg.set_api_key("anthropic", "sk-x")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def disconnect() -> None:
        try:
            for _ in range(25):
                reg.disconnect("anthropic")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads: list[threading.Thread] = []
    for _ in range(2):
        threads.append(threading.Thread(target=connect))
        threads.append(threading.Thread(target=disconnect))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    # Final state must be CONSISTENT: connected ⇒ a credential exists; disconnected
    # ⇒ none. The race left "connected with no credential" before the lock.
    status = {s["provider"]: s for s in reg.status()}["anthropic"]
    cred = reg.credential("anthropic")
    if status["connected"]:
        assert cred is not None
    else:
        assert cred is None
