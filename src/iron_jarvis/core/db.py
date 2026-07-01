"""SQLite persistence (§22 default backend).

Synchronous SQLModel engine. SQLite operations are local and fast, so the async
runtime calls these directly; swapping to Postgres+pgvector is an engine-URL
change.
"""

from __future__ import annotations

import json
import logging
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


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    _reconcile_additive_columns(engine)
    _ensure_indexes(engine)
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
    """Delete EventRecord rows older than N days (retention). Returns the count."""
    from datetime import timedelta

    from sqlalchemy import delete as sa_delete

    from .ids import utcnow
    from .models import EventRecord

    # Clamp the age so a huge value can't underflow datetime (year 1) and raise
    # OverflowError; ~365,000 days (~1000 years) is already before any real event.
    cutoff = utcnow() - timedelta(days=min(max(0, older_than_days), 365_000))
    with Session(engine) as db:
        # Bulk DELETE in the engine (returns rowcount) rather than materializing every
        # expired row as an ORM object and deleting one-by-one — the boot prune over a
        # large backlog was O(rows) memory + ~1.3s/33k rows.
        result = db.execute(sa_delete(EventRecord).where(EventRecord.created_at < cutoff))
        db.commit()
        deleted = int(result.rowcount or 0)
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
