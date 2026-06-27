"""Secrets Manager (§7, §10) — the shared, Fernet-encrypted credential vault.

Mirrors :class:`~iron_jarvis.providers.vault.BrowserVault`'s encryption approach:
a Fernet key persisted beside the store (``<home>/secrets/.secrets.key``,
generated on first use), used to encrypt every value at rest. Plaintext is
returned **only** by the explicit, server-side ``get``/``get_oauth`` methods;
``list`` exposes metadata (name/kind/description) but never the value.
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import SecretRecord

#: Recognised secret kinds (free-form ``kind`` is allowed; these are the canon).
KINDS = ("api_key", "oauth", "token", "password", "generic")


class SecretsManager:
    """Encrypted-at-rest store for API keys, OAuth logins, and tokens.

    The Fernet key lives at ``<home>/secrets/.secrets.key`` (created with the
    directory on first use), exactly mirroring ``BrowserVault``. Values are
    encrypted on write into ``SecretRecord.enc_value`` and only decrypted by the
    server-side ``get``/``get_oauth`` accessors.
    """

    def __init__(self, home: str | Path, engine: Engine) -> None:
        self.root = Path(home) / "secrets"
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = engine

    # -- encryption ---------------------------------------------------------
    def _fernet(self) -> Fernet:
        key_path = self.root / ".secrets.key"
        if not key_path.exists():
            key_path.write_bytes(Fernet.generate_key())
        return Fernet(key_path.read_bytes())

    def _encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, token: str) -> str:
        return self._fernet().decrypt(token.encode("utf-8")).decode("utf-8")

    def rotate_key(self) -> int:
        """Re-encrypt every secret under a fresh Fernet key (keeping the old key
        as ``.secrets.key.bak``). Returns the number of secrets rotated. Either
        the new key + re-encrypted rows both land, or the old key is restored."""
        key_path = self.root / ".secrets.key"
        old = self._fernet()
        new_key = Fernet.generate_key()
        new = Fernet(new_key)
        bak = key_path.parent / (key_path.name + ".bak")
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(SecretRecord)))
            for r in rows:
                plain = old.decrypt(r.enc_value.encode("utf-8"))
                r.enc_value = new.encrypt(plain).decode("utf-8")
                db.add(r)
            if key_path.exists():
                bak.write_bytes(key_path.read_bytes())
            key_path.write_bytes(new_key)
            try:
                db.commit()
            except Exception:
                if bak.exists():  # roll the key back so existing rows decrypt
                    key_path.write_bytes(bak.read_bytes())
                raise
        return len(rows)

    # -- write --------------------------------------------------------------
    def set(
        self,
        name: str,
        value: str,
        kind: str = "generic",
        description: str = "",
    ) -> SecretRecord:
        """Upsert by ``name``: encrypt ``value`` into ``enc_value``, bump ``updated_at``."""
        enc = self._encrypt(value)
        with session_scope(self.engine) as db:
            row = db.exec(select(SecretRecord).where(SecretRecord.name == name)).first()
            if row is None:
                row = SecretRecord(
                    name=name, kind=kind, description=description, enc_value=enc
                )
            else:
                row.kind = kind
                row.description = description
                row.enc_value = enc
                row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)  # re-load expired attrs before the session closes
            return row

    def set_oauth(
        self, name: str, token: dict, description: str = ""
    ) -> SecretRecord:
        """Store an OAuth token dict (JSON-encoded then encrypted), kind=``oauth``."""
        return self.set(
            name, json.dumps(token), kind="oauth", description=description
        )

    # -- read (server-side only) -------------------------------------------
    def get(self, name: str) -> str | None:
        """Decrypt and return the plaintext value, or ``None`` if absent."""
        row = self._find(name)
        return self._decrypt(row.enc_value) if row is not None else None

    def get_oauth(self, name: str) -> dict | None:
        """Decrypt and JSON-parse an OAuth token dict, or ``None`` if absent."""
        raw = self.get(name)
        return json.loads(raw) if raw is not None else None

    # -- metadata -----------------------------------------------------------
    def exists(self, name: str) -> bool:
        return self._find(name) is not None

    def delete(self, name: str) -> bool:
        """Delete a secret by name; returns True if a row was removed."""
        with session_scope(self.engine) as db:
            row = db.exec(select(SecretRecord).where(SecretRecord.name == name)).first()
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True

    def list(self) -> list[dict]:
        """List secrets as metadata only — NEVER includes the decrypted value."""
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(SecretRecord).order_by(SecretRecord.name)))
        return [
            {
                "name": r.name,
                "kind": r.kind,
                "description": r.description,
                "has_value": True,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]

    # -- internals ----------------------------------------------------------
    def _find(self, name: str) -> SecretRecord | None:
        with session_scope(self.engine) as db:
            return db.exec(
                select(SecretRecord).where(SecretRecord.name == name)
            ).first()
