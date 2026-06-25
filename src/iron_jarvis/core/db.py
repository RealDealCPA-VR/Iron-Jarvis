"""SQLite persistence (§22 default backend).

Synchronous SQLModel engine. SQLite operations are local and fast, so the async
runtime calls these directly; swapping to Postgres+pgvector is an engine-URL
change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from .events import Event
from .models import EventRecord


def make_engine(db_path: str | Path) -> Engine:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_scope(engine: Engine) -> Session:
    return Session(engine)


def persist_event(engine: Engine, event: Event) -> None:
    """Sync EventBus handler: append the event to the EventRecord log."""
    record = EventRecord(
        id=event.id,
        type=event.type,
        session_id=event.session_id,
        payload_json=json.dumps(event.payload, default=str),
    )
    with Session(engine) as db:
        db.add(record)
        db.commit()


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
