"""SQLite persistence (§22 default backend).

Synchronous SQLModel engine. SQLite operations are local and fast, so the async
runtime calls these directly; swapping to Postgres+pgvector is an engine-URL
change.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, event, text
from sqlmodel import Session, SQLModel, create_engine

from .events import Event
from .models import EventRecord

logger = logging.getLogger("iron_jarvis.db")


def make_engine(db_path: str | Path) -> Engine:
    path = Path(db_path)
    is_memory = str(db_path) == ":memory:"
    if not is_memory:
        path.parent.mkdir(parents=True, exist_ok=True)
    url = "sqlite://" if is_memory else f"sqlite:///{path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    # Harden SQLite for a long-lived daemon with a background-scheduler thread
    # and the async loop both writing: WAL lets readers not block writers, and a
    # generous busy_timeout makes a brief lock wait instead of raising
    # "database is locked" (which EventBus would otherwise swallow, silently
    # dropping a persisted event). In-memory DBs can't use WAL.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - exercised at runtime
        cur = dbapi_conn.cursor()
        try:
            if not is_memory:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

    return engine


#: Bump when a NON-additive migration is added to ``_MIGRATIONS``. Additive
#: column changes self-heal via ``_reconcile_additive_columns`` and need no bump.
SCHEMA_VERSION = 1

#: version -> migration callable(engine). Empty today (additive changes are
#: handled automatically); the runner exists so future non-additive migrations
#: can be applied in order at boot instead of bricking an existing DB.
_MIGRATIONS: dict[int, "callable"] = {}


#: Hot ordering/filter columns that lack an index (the *_id columns are already
#: indexed in the models). Backs the event feed + prune (created_at), list_sessions
#: ordering, and transcript queries — unindexed they full-scan a growing table.
_HOT_INDEXES = (
    ("ix_eventrecord_created_at", "eventrecord", "created_at"),
    ("ix_session_created_at", "session", "created_at"),
    ("ix_agentrun_created_at", "agentrun", "created_at"),
    ("ix_memoryrecord_created_at", "memoryrecord", "created_at"),
    # TX-01 audit timeline queries order/filter tool invocations by time over a
    # table that was previously unbounded + unindexed on created_at.
    ("ix_toolinvocation_created_at", "toolinvocation", "created_at"),
)


def _ensure_indexes(engine: Engine) -> None:
    """Create the hot-column indexes if missing (idempotent — runs every boot;
    create_all won't add a new index to an already-existing table)."""
    with engine.begin() as conn:
        for name, table, column in _HOT_INDEXES:
            try:
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))
            except Exception:  # noqa: BLE001 — a missing table/column must never brick boot
                pass


#: The FTS5 virtual table behind history search (``src/iron_jarvis/search/``).
#: Deliberately created with RAW DDL and deliberately NOT a SQLModel
#: ``table=True`` model: ``_reconcile_additive_columns`` walks
#: ``SQLModel.metadata.tables`` and would try to ``ALTER`` a *virtual* table on
#: every boot. Leaving it unmapped is what keeps the reconciler away from it.
#: Own-content (not ``content='...'`` external-content) so a delete is a plain
#: ``DELETE ... WHERE rowid IN (...)`` that no other subsystem's bulk delete can
#: corrupt — see ``search/index.py``'s module docstring for the full rationale.
_FTS_TABLE = "searchdoc_fts"
_FTS_DDL = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} "
    "USING fts5(text, tokenize='porter unicode61')"
)


def _register_search_models() -> None:
    """Put ``searchdocrecord`` into ``SQLModel.metadata`` BEFORE ``create_all``
    and ``_reconcile_additive_columns`` run.

    Nothing else imports ``search.models`` at daemon boot, so without this the
    doc table is absent from the metadata while the reconciler walks it — and a
    future ADDITIVE column on :class:`~iron_jarvis.search.models.SearchDocRecord`
    would then never self-heal on an existing ``.ironjarvis`` DB. Creating the
    table later (in :func:`_ensure_fts`) does not help: ``checkfirst=True`` sees
    a table that already exists and adds nothing. The failure is SILENT — index
    writes swallow their exceptions, so history search would just quietly index
    nothing forever. Pinned by
    ``tests/test_search_index.py::test_search_doc_table_is_registered_before_the_reconciler``.
    """
    try:
        from ..search import models as _search_models  # noqa: F401
    except Exception:  # noqa: BLE001 — search is additive; never brick boot
        logger.warning("history-search models unavailable", exc_info=True)


def _register_profile_models() -> None:
    """Put ``userprofilerecord`` into ``SQLModel.metadata`` BEFORE ``create_all``
    and ``_reconcile_additive_columns`` run — same reason as
    :func:`_register_search_models`.

    The profile is read by the prompt seams through ``ProfileStore``, which is
    imported LAZILY inside those seams (they must not pay for the package on a
    turn that has no profile). So at boot nothing has imported
    ``profile.models`` yet, the table would be missing from the metadata while
    the reconciler walks it, and a future additive column on
    :class:`~iron_jarvis.profile.models.UserProfileRecord` would never self-heal
    on an existing ``.ironjarvis`` DB — silently, since every seam swallows its
    own errors. v1.145.0 adds exactly such a column (the voice card's
    provenance), so this is load-bearing, not defensive. Pinned by
    ``tests/test_profile_v1144.py::test_profile_table_is_registered_before_the_reconciler``.
    """
    try:
        from ..profile import models as _profile_models  # noqa: F401
    except Exception:  # noqa: BLE001 — the profile is additive; never brick boot
        logger.warning("user-profile models unavailable", exc_info=True)


#: Modules whose SQLModel tables are created LAZILY — by a store's ``__init__``
#: calling ``__table__.create(checkfirst=True)`` when a route first constructs
#: it — rather than by ``create_all`` at boot.
#:
#: Every one of them is a silent-migration bug waiting to happen, and v1.151.2
#: is the third time this has bitten. The shape is always the same:
#:
#: * a FRESH database works, because the lazy ``create`` builds the table with
#:   whatever columns the model currently has — so tests, which mint a new DB
#:   per case, are green;
#: * an EXISTING database does not, because ``checkfirst=True`` sees the table
#:   and adds nothing, while ``_reconcile_additive_columns`` never had a chance
#:   to ALTER it — it walks ``SQLModel.metadata.tables``, and nothing had
#:   imported the module at boot, so the table simply was not there to walk.
#:
#: The user's daemon hit this on ``agentthreadrecord.chat_thread_id`` (v1.150.0):
#: "no such column" on every @mention, while the whole suite passed.
#:
#: Importing the module here puts its table in the metadata BEFORE create_all
#: and the reconciler run. Pinned generally by
#: ``tests/test_lazy_table_migrations.py`` — which asserts that no table in a
#: freshly-built DB is missing from the metadata, so a FOURTH one cannot
#: reintroduce this quietly.
#: ``agents.remote`` is here even though ``platform.py`` happens to import it at
#: module load: relying on that is relying on an UNRELATED module's import list
#: never changing. This module is the one that has to reconcile these tables, so
#: it takes responsibility for registering them.
_LATE_MODEL_MODULES = (
    "..agents.threads",       # AgentThreadRecord — the panel/round-table store
    "..agents.remote",        # RemoteAgentRecord
    "..memory.proposals",     # MemoryProposalRecord — the steward's queue
    "..workflows.store",      # WorkflowPinRecord
    "..context.store",        # CompactionRecord — the compaction cache
    "..worklist.models",      # WorklistItem — durable per-item checkpoints
    "..capability.models",    # CapabilityProposalRecord — the agent's asks
)


def _register_late_models() -> None:
    """Import the lazily-created tables so the reconciler can see them."""
    import importlib

    for mod in _LATE_MODEL_MODULES:
        try:
            importlib.import_module(mod, package=__package__)
        except Exception:  # noqa: BLE001 — never brick boot over a table import
            logger.warning("late model %s unavailable", mod, exc_info=True)


def _ensure_fts(engine: Engine) -> None:
    """Create the history-search substrate if missing (idempotent, every boot).

    Two halves, both best-effort:

    1. ``searchdocrecord`` — the ordinary mapped row table. Belt-and-braces:
       :func:`_register_search_models` already imported the module so
       ``create_all`` built it, but the explicit ``create(checkfirst=True)``
       covers a DB where that step was skipped, so the substrate is never one
       missing import away from silently not existing.
    2. ``searchdoc_fts`` — the FTS5 virtual table (see ``_FTS_DDL``). A SQLite
       build WITHOUT FTS5 raises here; that is expected and swallowed, and
       ``SearchIndex.available()`` then degrades search to a LIKE scan with an
       honest ``mode: "basic"`` rather than 500ing.

    Same bare-``try`` discipline as ``_ensure_indexes``: a missing table must
    never brick boot.
    """
    try:
        from ..search.models import SearchDocRecord  # registers the mapped table

        SearchDocRecord.__table__.create(bind=engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — search is additive; never brick boot
        logger.warning("history-search doc table unavailable", exc_info=True)
    try:
        with engine.begin() as conn:
            conn.execute(text(_FTS_DDL))
    except Exception:  # noqa: BLE001 — no FTS5 in this SQLite build
        logger.warning("FTS5 unavailable; history search will use the LIKE fallback")


#: Where the shared :class:`~iron_jarvis.search.SearchIndex` is parked: ON THE
#: ENGINE OBJECT itself, not in a module-level map.
#:
#: A ``WeakKeyDictionary[Engine, SearchIndex]`` was tried first and is a TRAP
#: here: ``SearchIndex`` holds ``self.engine``, so the dictionary's (strong)
#: value keeps its own (weak) key alive and the entry can never expire —
#: measured: 5 of 5 engines still reachable after every external reference was
#: dropped and ``gc.collect()`` ran twice. That immortalizes every engine ever
#: built, with its connection pool and open SQLite file handles, across a
#: 2000-test suite that mints one per daemon. Hanging the index off the engine
#: makes the lifetimes exactly identical with no global state at all.
#: Pinned by ``tests/test_search_sync.py::test_the_index_cache_does_not_pin_engines``.
_SEARCH_INDEX_ATTR = "_ironjarvis_search_index"
_SEARCH_INDEX_LOCK = threading.Lock()

#: ONE lock for every conversation write — ``PUT``/``DELETE /chat/threads/{id}``,
#: ``CommThreadStore.append`` and ``AgentThreads._append``.
#:
#: These were three independent locks until v1.142.0, which was fine while each
#: seam only touched its own row. It stopped being fine the moment all three also
#: wrote the shared history index: ``SearchIndex`` holds an internal lock across
#: its statements, so seam A (already holding SQLite's single writer) waiting for
#: the index lock, while seam B holds the index lock and waits for the writer, is
#: a genuine deadlock. It resolves only when ``busy_timeout`` fires 30s later —
#: and the failed flush then leaves B's Session unusable, so B's ``commit()``
#: raises ``PendingRollbackError`` and the user's message is GONE. Measured on
#: 12 concurrent writers: 30ms / 0 errors before the index, 66s / 2 LOST WRITES
#: with three locks, 30ms / 0 errors with one.
#:
#: Serializing all three costs nothing real — SQLite admits ONE writer anyway, and
#: a global chat lock measured FASTER than lock-free (8 concurrent PUTs to 8
#: threads: 59ms vs 153ms wall) because it replaces busy-wait retries with a
#: clean queue. RLock so a seam may nest (a route that appends and then saves).
#: Pinned by ``tests/test_search_sync.py::test_the_three_write_locks_never_deadlock_each_other``.
CONVERSATION_WRITE_LOCK = threading.RLock()

#: Ids read per keyset page when ``prune_events`` expires history-search docs.
#: Bounded on purpose — a boot prune over a large backlog must stay O(page),
#: not O(backlog). Small enough that a test can page it, big enough that a real
#: prune issues a handful of statements.
_PRUNE_ID_PAGE = 1000


def search_index(engine: Engine) -> Any:
    """The shared history-search index for *engine* — or ``None`` if the search
    package can't be imported at all.

    Lives HERE rather than in ``search/`` because the five write seams that need
    it span four packages (``daemon/routes``, ``comm``, ``agents``, and this
    module) and all of them already import ``core.db``; a per-call
    ``SearchIndex(engine)`` would instead re-run the FTS5 capability probe on
    every chat autosave, every phone message, and every round entry. The import
    is deliberately LAZY (function-local): ``search.index`` imports
    ``session_scope`` from this module, so a module-level import would close the
    cycle.

    Never raises — a caller that gets ``None`` simply skips indexing, exactly
    like every other seam guard.
    """
    try:
        index = getattr(engine, _SEARCH_INDEX_ATTR, None)
        if index is not None:
            return index
        with _SEARCH_INDEX_LOCK:
            index = getattr(engine, _SEARCH_INDEX_ATTR, None)
            if index is None:
                from ..search import SearchIndex  # lazy: breaks the import cycle

                index = SearchIndex(engine)
                try:
                    setattr(engine, _SEARCH_INDEX_ATTR, index)
                except Exception:  # noqa: BLE001 — a slotted/mock engine
                    # Un-cacheable engine (a test double with __slots__): still
                    # hand back a working index, just an unshared one.
                    logger.debug("engine will not hold the search index", exc_info=True)
            return index
    except Exception:  # noqa: BLE001 — search is additive; never break a caller
        logger.warning("history-search index unavailable for this engine", exc_info=True)
        return None


def init_db(engine: Engine) -> None:
    _register_search_models()  # MUST precede create_all + the reconciler
    _register_profile_models()  # ...and so must this one (same failure mode)
    _register_late_models()  # ...and every lazily-created table (v1.151.2)
    SQLModel.metadata.create_all(engine)
    _reconcile_additive_columns(engine)
    _ensure_indexes(engine)
    _ensure_fts(engine)
    run_migrations(engine)


def quarantine_db(db_path: str | Path, reason: str) -> "Path | None":
    """Rename a corrupt DB (and drop its -wal/-shm) so a fresh one can take its
    place. Returns the quarantine path. The corrupt file is KEPT (never deleted)
    so data can be salvaged / restored later."""
    from .ids import utcnow

    path = Path(db_path)
    try:
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    except Exception:  # noqa: BLE001
        stamp = "corrupt"
    dead = path.with_name(path.name + f".corrupt-{stamp}")
    try:
        if path.exists():
            path.replace(dead)
        for sfx in ("-wal", "-shm"):
            s = Path(str(path) + sfx)
            if s.exists():
                s.unlink()
    except OSError:
        return None
    logger.error(
        "QUARANTINED corrupt database %s -> %s (%s). Starting with a fresh DB; "
        "run `ironjarvis repair` to restore your latest backup.",
        path, dead, reason,
    )
    return dead


def _db_is_corrupt(db_path: str | Path) -> bool:
    """True ONLY if the file is a genuinely MALFORMED SQLite DB (vs a transient
    lock / disk-full / permission error). Uses a fresh raw connection so it never
    depends on a half-failed engine. A lock/busy is NOT corruption."""
    import sqlite3

    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute("PRAGMA integrity_check(1)").fetchone()
        finally:
            con.close()
        return not row or row[0] != "ok"
    except sqlite3.DatabaseError:
        return True  # "file is not a database" / header corruption
    except sqlite3.OperationalError:
        return False  # locked / busy / cannot-open — environmental, NOT corruption


#: A valid SQLite database (or a 0-byte new file) begins with this 16-byte magic.
_SQLITE_MAGIC = b"SQLite format 3\x00"


def open_db(db_path: str | Path) -> Engine:
    """Open + initialize the DB, self-healing a CONFIRMED-corrupt one so the daemon
    still boots — WITHOUT ever destroying a healthy DB.

    1. Cheap header precheck (16 bytes): a non-empty file that isn't a SQLite DB is
       quarantined BEFORE SQLAlchemy opens it (no lingering handle blocks the
       rename), then replaced with a fresh DB. This is the common "won't boot"
       header-corruption case.
    2. Otherwise try init_db. On failure, distinguish real corruption from a
       transient/environmental error (lock, disk full, read-only) via a raw
       ``integrity_check``: ONLY a confirmed-malformed file is quarantined; a
       lock/disk/permission error is re-raised LOUDLY — a healthy-but-locked or
       -full DB is NEVER truncated. Recover data with `ironjarvis repair`.
    (Data-page corruption that still boots is caught later by /diagnostics + repair.)
    """
    from sqlalchemy.exc import DatabaseError, OperationalError

    path = Path(db_path)
    is_mem = str(db_path) == ":memory:"
    if not is_mem and path.exists() and path.stat().st_size > 0:
        try:
            with open(path, "rb") as fh:
                header = fh.read(16)
        except OSError:
            header = _SQLITE_MAGIC  # can't read it here → let init_db surface why
        if header != _SQLITE_MAGIC:
            quarantine_db(path, "not a SQLite database (bad header)")

    engine = make_engine(db_path)
    err: BaseException | None = None
    try:
        init_db(engine)
        return engine
    except (DatabaseError, OperationalError) as exc:
        err = exc
        err.__traceback__ = None  # drop the frames pinning the failed sqlite handle
    engine.dispose()
    if is_mem:
        raise err
    import gc

    gc.collect()
    if not (path.exists() and _db_is_corrupt(path)):
        # Environmental (lock / disk full / read-only), NOT corruption — never
        # destroy the DB; re-attempt once so the true cause propagates loudly.
        engine = make_engine(db_path)
        init_db(engine)
        return engine
    if quarantine_db(path, "confirmed corrupt database at init") is None and path.exists():
        raise RuntimeError(
            f"corrupt database {db_path} could not be quarantined (in use?). Stop all "
            "Iron Jarvis processes and run `ironjarvis repair` to restore a backup."
        )
    engine = make_engine(db_path)
    init_db(engine)  # fresh DB on the now-free path
    return engine


def _ensure_meta(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS _ironjarvis_meta (key TEXT PRIMARY KEY, value TEXT)")
        )


def get_schema_version(engine: Engine) -> int:
    _ensure_meta(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM _ironjarvis_meta WHERE key='schema_version'")
        ).first()
    return int(row[0]) if row else 0


def set_schema_version(engine: Engine, version: int) -> None:
    _ensure_meta(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO _ironjarvis_meta(key,value) VALUES('schema_version',:v) "
                "ON CONFLICT(key) DO UPDATE SET value=:v"
            ),
            {"v": str(version)},
        )


def run_migrations(engine: Engine) -> int:
    """Apply ordered non-additive migrations beyond the recorded version.

    Returns the resulting schema version. A brand-new DB (or one created before
    versioning) is stamped at the current ``SCHEMA_VERSION`` without running any
    migration, since ``create_all`` already built the latest schema.
    """
    current = get_schema_version(engine)
    if current == 0:
        set_schema_version(engine, SCHEMA_VERSION)
        return SCHEMA_VERSION
    for version in sorted(v for v in _MIGRATIONS if current < v <= SCHEMA_VERSION):
        try:
            _MIGRATIONS[version](engine)
            set_schema_version(engine, version)
            logger.warning("applied schema migration -> v%s", version)
        except Exception:
            logger.exception("schema migration to v%s failed", version)
            break
    return get_schema_version(engine)


def prune_events(engine: Engine, older_than_days: int, vacuum: bool = False) -> int:
    """Delete EventRecord + the parallel audit tables (ToolInvocation, AgentRun,
    UndoJournal) older than N days (retention parity). Returns the total count.

    Previously only EventRecord was pruned, so the tool-invocation ledger + run
    rows grew UNBOUNDED and the audit trail went internally inconsistent (a tool
    entry whose backing event had aged out). TX-01 prunes all four on the same
    cutoff so the timeline stays consistent and bounded.

    HISTORY SEARCH (v1.142.0): the bulk ``sa_delete`` below bypasses ORM events,
    so nothing downstream can notice a run disappearing. The expiring AgentRun
    ids are therefore read FIRST — in BOUNDED keyset pages of
    :data:`_PRUNE_ID_PAGE`, never one big list, or this function would hand back
    the very O(rows) memory the bulk DELETE below exists to avoid — and handed
    to ``SearchIndex.drop_refs`` inside this same transaction, so the rows and
    their index docs expire together or not at all. Note what is NOT pruned:
    ``Session`` rows survive a prune, so their ``session`` docs are left alone
    deliberately — dropping them would blind recall to runs the app still
    lists."""
    from datetime import timedelta
    from pathlib import Path

    from sqlalchemy import delete as sa_delete
    from sqlmodel import select

    from .ids import utcnow
    from .models import AgentRun, EventRecord, ToolInvocation, UndoJournal

    # Clamp the age so a huge value can't underflow datetime (year 1) and raise
    # OverflowError; ~365,000 days (~1000 years) is already before any real event.
    cutoff = utcnow() - timedelta(days=min(max(0, older_than_days), 365_000))
    deleted = 0
    with Session(engine) as db:
        # First collect the on-disk pre-image blobs the expiring UndoJournal rows
        # reference. A pre-image is a verbatim snapshot of prior file content, so
        # deleting only the SQL row would strand that plaintext on disk (and in
        # every backup) past its retention window. Reclaim the blobs in lockstep.
        stale_refs = [
            r for r in db.exec(
                select(UndoJournal.pre_ref).where(
                    UndoJournal.created_at < cutoff, UndoJournal.pre_ref != None  # noqa: E711
                )
            ).all()
            if r
        ]
        # Drop the history-search docs for the expiring runs BEFORE the bulk
        # delete removes the rows we read them from — the index keys its docs by
        # ``ref``, and a deleted run whose docs survive is an orphan no later
        # read could ever detect. Read in bounded keyset pages so a 33k-row
        # backlog costs one page of ids, not the backlog. db= → the doc deletes
        # join THIS transaction and commit (or roll back) with the deletes below.
        index = search_index(engine)
        if index is not None:
            try:
                last_id = ""
                while True:
                    page = [
                        r for r in db.exec(
                            select(AgentRun.id)
                            .where(AgentRun.created_at < cutoff)
                            .where(AgentRun.id > last_id)
                            .order_by(AgentRun.id)  # type: ignore[arg-type]
                            .limit(_PRUNE_ID_PAGE)
                        ).all()
                        if r
                    ]
                    if not page:
                        break
                    index.drop_refs(page, db=db)
                    last_id = page[-1]
                    if len(page) < _PRUNE_ID_PAGE:
                        break
            except Exception:  # noqa: BLE001 — a prune must always finish
                logger.warning("history-search prune skipped", exc_info=True)
        # Bulk DELETE in the engine (returns rowcount) rather than materializing every
        # expired row as an ORM object and deleting one-by-one — the boot prune over a
        # large backlog was O(rows) memory + ~1.3s/33k rows.
        for model in (EventRecord, ToolInvocation, AgentRun, UndoJournal):
            result = db.execute(sa_delete(model).where(model.created_at < cutoff))
            deleted += int(result.rowcount or 0)
        db.commit()
    if stale_refs:
        # The undo blob store lives at <home>/undo/ and the DB at <home>/ironjarvis.db,
        # so the home is the DB file's parent. Best-effort unlink; never raise here.
        from ..tools.undo import delete_preimage

        try:
            home = Path(engine.url.database).parent  # type: ignore[arg-type]
            for ref in stale_refs:
                delete_preimage(home, ref)
        except Exception:  # noqa: BLE001 — blob reclamation must not fail a prune
            pass
    if vacuum:
        with engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")
    return deleted


def _reconcile_additive_columns(engine: Engine) -> None:
    """Self-heal existing SQLite DBs on ADDITIVE schema changes.

    ``create_all`` only issues ``CREATE TABLE IF NOT EXISTS`` — it never adds a
    column to an already-existing table. So shipping a new model field would
    leave every existing ``.ironjarvis`` DB with the old shape and make every
    read of that table fail with "no such column". This walks each mapped table,
    diffs the on-disk columns against the model, and ``ALTER TABLE ADD COLUMN``s
    any missing (additive) ones as nullable. Non-additive changes (renames/type
    changes/drops) are out of scope and logged loudly rather than guessed at.
    """
    try:
        with engine.connect() as conn:
            for table_name, table in SQLModel.metadata.tables.items():
                try:
                    rows = conn.execute(text(f'PRAGMA table_info("{table_name}")')).all()
                except Exception:  # table may not exist yet on a fresh DB race
                    continue
                if not rows:
                    continue
                existing = {r[1] for r in rows}  # PRAGMA table_info col 1 = name
                for col in table.columns:
                    if col.name in existing:
                        continue
                    try:
                        col_type = col.type.compile(engine.dialect)
                    except Exception:
                        col_type = "TEXT"
                    ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}'
                    try:
                        conn.execute(text(ddl))
                        conn.commit()
                        logger.warning(
                            "schema reconcile: added missing column %s.%s (%s)",
                            table_name, col.name, col_type,
                        )
                    except Exception:
                        logger.exception(
                            "schema reconcile: could not add column %s.%s — "
                            "a manual migration may be required",
                            table_name, col.name,
                        )
    except Exception:  # never block boot on the reconciler
        logger.exception("schema reconcile failed; continuing with create_all schema")


def session_scope(engine: Engine) -> Session:
    return Session(engine)


def persist_event(engine: Engine, event: Event) -> None:
    """Sync EventBus handler: append the event to the EventRecord log.

    Retries briefly on a transient lock (e.g. a `db_vacuum` EXCLUSIVE lock that
    outlasts busy_timeout) so the only durable copy of an event isn't lost — the
    EventBus dispatcher would otherwise swallow the OperationalError."""
    import time

    from sqlalchemy.exc import OperationalError

    record = EventRecord(
        id=event.id,
        type=event.type,
        session_id=event.session_id,
        payload_json=json.dumps(event.payload, default=str),
    )
    for attempt in range(5):
        try:
            with Session(engine) as db:
                db.add(record)
                db.commit()
            return
        except OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))  # 0.2,0.4,0.6,0.8s — ~2s total


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
