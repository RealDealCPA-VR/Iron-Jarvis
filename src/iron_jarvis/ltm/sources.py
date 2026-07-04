"""User-configurable custom long-term-memory sources (§21 extension).

Lets a user register *their own* LTM stores at runtime — a markdown/Obsidian
folder anywhere on disk, or a Notion database — and have them persist across
restarts. Each source is a :class:`LTMSourceRecord` row; on boot
:func:`load_custom_sources` rebuilds every row into a live
:class:`~iron_jarvis.ltm.base.LTMConnector` and registers it on the platform's
:class:`~iron_jarvis.ltm.manager.LongTermMemory`.

Secrets (the Notion token) are never stored here — only the *name* of the secret
to resolve lazily — so the encrypted SecretsManager stays the single source of
truth. The Notion HTTP client is injected (``http_factory``) so nothing here ever
opens a socket in tests.

Importing this module registers the ``LTMSourceRecord`` table on the shared
SQLModel metadata BEFORE ``init_db`` runs (the package ``__init__`` imports it),
so the table is created on platform boot.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from .base import LTMConnector
from .brain import MarkdownBrainConnector
from .dropbox import DropboxConnector
from .gdrive import GoogleDriveConnector
from .http_rag import HttpRagConfig, HttpRagConnector
from .notion import NotionConnector
from .onedrive import OneDriveConnector
from .ssh import SSHBrainConnector

if TYPE_CHECKING:  # avoid heavy imports at module load
    from sqlalchemy import Engine

    from .manager import LongTermMemory

#: The kinds of custom source a user may register.
#:  * markdown/notion/ssh — local + existing networked stores.
#:  * google_drive/onedrive/dropbox — cloud drives (OAuth via the Connections
#:    registry; files downloaded, text-extracted, chunked + embedded locally).
#:  * http_rag — any external/offsite RAG service reachable over HTTP (the
#:    remote does its own ranking; we normalise its hits). The universal
#:    "point Iron Jarvis at my own RAG" option.
CLOUD_DRIVE_KINDS: tuple[str, ...] = ("google_drive", "onedrive", "dropbox")
SOURCE_KINDS: tuple[str, ...] = (
    "markdown",
    "notion",
    "ssh",
    *CLOUD_DRIVE_KINDS,
    "http_rag",
)


class LTMSourceRecord(SQLModel, table=True):
    """A persisted, user-defined long-term-memory source."""

    id: str = Field(default_factory=lambda: new_id("ltmsrc"), primary_key=True)
    name: str = Field(index=True, unique=True)
    kind: str = "markdown"  # see SOURCE_KINDS
    path: str = ""  # local path (markdown) / remote path (ssh) / folder scope (cloud)
    database_id: str = ""  # Notion database id (notion sources)
    token_secret: str = ""  # vault key: Notion token / SSH password / http_rag bearer
    # SSH (remote) source fields:
    host: str = ""  # ssh host
    port: int = 22  # ssh port
    username: str = ""  # ssh username
    key_path: str = ""  # local private-key file (alternative to a password)
    # Offsite HTTP RAG source:
    endpoint_url: str = ""  # query URL of an external RAG service (http_rag)
    config_json: str = ""  # JSON blob of HttpRagConfig overrides (http_rag)
    created_at: datetime = Field(default_factory=utcnow)


class CustomSourceStore:
    """CRUD over persisted custom LTM sources (:class:`LTMSourceRecord`)."""

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # -- helpers ----------------------------------------------------------
    def _fetch(self, db, name: str) -> LTMSourceRecord | None:
        return db.exec(
            select(LTMSourceRecord).where(LTMSourceRecord.name == name)
        ).first()

    # -- CRUD -------------------------------------------------------------
    def add(
        self,
        name: str,
        kind: str,
        path: str = "",
        database_id: str = "",
        token_secret: str = "",
        *,
        host: str = "",
        port: int = 22,
        username: str = "",
        key_path: str = "",
        endpoint_url: str = "",
        config_json: str = "",
    ) -> LTMSourceRecord:
        """Create or update a custom source (upsert by unique ``name``).

        Raises ``ValueError`` on a blank name or an unknown ``kind``.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("custom LTM source name is required")
        if kind not in SOURCE_KINDS:
            raise ValueError(
                f"unknown LTM source kind {kind!r}; expected one of {SOURCE_KINDS}"
            )
        if kind == "ssh" and not (host.strip() and path.strip()):
            raise ValueError("an ssh source needs a host and a remote path")
        if kind == "http_rag" and not endpoint_url.strip():
            raise ValueError("an http_rag source needs an endpoint_url")
        with session_scope(self.engine) as db:
            record = self._fetch(db, name)
            if record is None:
                record = LTMSourceRecord(name=name)
            record.kind = kind
            record.path = path
            record.database_id = database_id
            record.token_secret = token_secret
            record.host = host
            record.port = int(port or 22)
            record.username = username
            record.key_path = key_path
            record.endpoint_url = endpoint_url
            record.config_json = config_json
            db.add(record)
            db.commit()
            db.refresh(record)
        return record

    def list(self) -> list[LTMSourceRecord]:
        """Return all persisted custom sources, oldest first."""
        with session_scope(self.engine) as db:
            return list(
                db.exec(select(LTMSourceRecord).order_by(LTMSourceRecord.created_at))
            )

    def get(self, name: str) -> LTMSourceRecord | None:
        """Return the custom source named ``name`` (or None)."""
        with session_scope(self.engine) as db:
            return self._fetch(db, name)

    def remove(self, name: str) -> bool:
        """Delete a custom source. Returns False if it did not exist."""
        with session_scope(self.engine) as db:
            record = self._fetch(db, name)
            if record is None:
                return False
            db.delete(record)
            db.commit()
        return True


#: Cloud-drive kind -> connector class. All share one constructor signature.
_CLOUD_CONNECTORS: dict[str, type] = {
    "google_drive": GoogleDriveConnector,
    "onedrive": OneDriveConnector,
    "dropbox": DropboxConnector,
}


def connector_from_record(
    rec: LTMSourceRecord,
    *,
    secret_resolver: Callable[[str], str | None],
    http_factory: Callable[[], Any],
    credential_resolver: Callable[[str], str | None] | None = None,
    embedder: Any = None,
) -> LTMConnector:
    """Build a live :class:`LTMConnector` from a persisted source record.

    * ``markdown`` -> a :class:`MarkdownBrainConnector` over ``rec.path``.
    * ``notion``   -> a :class:`NotionConnector` whose token is resolved lazily
      from ``rec.token_secret`` via ``secret_resolver``.
    * ``ssh``      -> a :class:`SSHBrainConnector` over SFTP.
    * ``google_drive``/``onedrive``/``dropbox`` -> a cloud-drive connector whose
      OAuth access token is resolved lazily from the Connections registry via
      ``credential_resolver`` (auto-refreshing) keyed by the drive's provider id
      (== ``rec.kind``); ``rec.path`` scopes it to a folder, ``embedder`` powers
      semantic ranking of the files it downloads.
    * ``http_rag`` -> an :class:`HttpRagConnector` delegating to an external RAG
      endpoint (``rec.endpoint_url`` + ``rec.config_json`` overrides); its bearer,
      if any, is resolved from ``rec.token_secret``.

    The connector's ``name`` is set to ``rec.name`` (overriding the class default)
    so a user can register several sources of the same kind under distinct names.
    HTTP clients are produced by ``http_factory`` (a sync ``httpx.Client``).
    """
    if rec.kind == "markdown":
        conn: LTMConnector = MarkdownBrainConnector(Path(rec.path))
    elif rec.kind == "notion":
        conn = NotionConnector(
            rec.database_id,
            token_resolver=lambda: secret_resolver(rec.token_secret),
            http=http_factory(),
        )
    elif rec.kind == "ssh":
        conn = SSHBrainConnector(
            rec.host,
            rec.path,  # remote path
            port=rec.port,
            username=rec.username,
            password_resolver=lambda: secret_resolver(rec.token_secret),
            key_path=rec.key_path,
        )
    elif rec.kind in _CLOUD_CONNECTORS:
        if credential_resolver is None:
            raise ValueError(
                f"a {rec.kind!r} source needs a credential resolver "
                "(connect the drive on the Connections page first)"
            )
        provider = rec.kind  # provider id in the Connections registry
        conn = _CLOUD_CONNECTORS[rec.kind](
            token_resolver=lambda: credential_resolver(provider),
            http=http_factory(),
            folder=rec.path,
            embedder=embedder,
        )
    elif rec.kind == "http_rag":
        import json

        try:
            overrides = json.loads(rec.config_json) if rec.config_json else {}
        except (ValueError, TypeError):
            overrides = {}
        token_secret = rec.token_secret
        conn = HttpRagConnector(
            name=rec.name,
            endpoint_url=rec.endpoint_url,
            http=http_factory(),
            token_resolver=(
                (lambda: secret_resolver(token_secret)) if token_secret else None
            ),
            config=HttpRagConfig.from_dict(overrides),
        )
    else:  # pragma: no cover — add() validates kind, but stay defensive
        raise ValueError(f"unknown LTM source kind {rec.kind!r}")
    conn.name = rec.name
    return conn


def load_custom_sources(
    ltm: "LongTermMemory",
    engine: "Engine",
    *,
    secret_resolver: Callable[[str], str | None],
    http_factory: Callable[[], Any],
    credential_resolver: Callable[[str], str | None] | None = None,
    embedder: Any = None,
) -> "LongTermMemory":
    """Register every persisted custom source onto ``ltm``. Returns ``ltm``.

    A source that fails to build (e.g. a cloud drive whose OAuth was revoked) is
    skipped with a logged warning rather than aborting boot — one broken source
    must not take down every other memory store.
    """
    import logging

    store = CustomSourceStore(engine)
    for rec in store.list():
        try:
            ltm.register(
                connector_from_record(
                    rec,
                    secret_resolver=secret_resolver,
                    http_factory=http_factory,
                    credential_resolver=credential_resolver,
                    embedder=embedder,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("iron_jarvis.ltm.sources").warning(
                "skipping LTM source %r (%s): %s", rec.name, rec.kind, exc
            )
    return ltm
