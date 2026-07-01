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


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    _reconcile_additive_columns(engine)
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


def open_db(db_path: str | Path) -> Engine:
    """Open + initialize the DB, SELF-HEALING a corrupt one so the daemon ALWAYS
    boots. If init fails because the file is malformed (header/schema corruption),
    the corrupt DB is quarantined and a fresh one is created — the daemon comes up
    (empty) instead of being wedged, and the quarantined file + auto-backups allow
    recovery via `ironjarvis repair` / `ironjarvis restore`."""
    from sqlalchemy.exc import DatabaseError, OperationalError

    engine = make_engine(db_path)
    try:
        init_db(engine)
        return engine
    except (DatabaseError, OperationalError) as exc:
        if str(db_path) == ":memory:":
            raise
        reason = f"init: {type(exc).__name__}: {exc}"
        engine.dispose()
        # The exception's traceback pins the failed sqlite3 connection (and thus the
        # OS file handle), which blocks the rename on Windows — drop it, then GC.
        exc = None
        import gc

        gc.collect()
        quarantine_db(db_path, reason)
        engine = make_engine(db_path)
        try:
            init_db(engine)  # fresh DB
        except (DatabaseError, OperationalError):
            # Quarantine couldn't free the path (locked) — last resort: truncate to
            # 0 bytes (an empty file is a valid new SQLite DB) and retry.
            engine.dispose()
            gc.collect()
            with open(db_path, "wb"):
                pass
            for sfx in ("-wal", "-shm"):
                s = Path(str(db_path) + sfx)
                try:
                    if s.exists():
                        s.unlink()
                except OSError:
                    pass
            engine = make_engine(db_path)
            init_db(engine)
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

    from sqlmodel import select

    from .ids import utcnow
    from .models import EventRecord

    # Clamp the age so a huge value can't underflow datetime (year 1) and raise
    # OverflowError; ~365,000 days (~1000 years) is already before any real event.
    cutoff = utcnow() - timedelta(days=min(max(0, older_than_days), 365_000))
    with Session(engine) as db:
        rows = list(db.exec(select(EventRecord).where(EventRecord.created_at < cutoff)))
        for r in rows:
            db.delete(r)
        db.commit()
    if vacuum:
        with engine.connect() as conn:
            conn.exec_driver_sql("VACUUM")
    return len(rows)


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
