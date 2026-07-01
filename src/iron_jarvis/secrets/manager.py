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
from ..core.logging import get_logger
from .models import SecretRecord

#: Recognised secret kinds (free-form ``kind`` is allowed; these are the canon).
KINDS = ("api_key", "oauth", "token", "password", "generic")

_log = get_logger("secrets")


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
        # Reconcile a key rotation interrupted by a crash (a leftover .new file).
        try:
            self._recover_key()
        except Exception:  # noqa: BLE001 — recovery must never block boot
            _log.exception("secrets key recovery failed")

    @staticmethod
    def _atomic_write_key(path: Path, data: bytes) -> None:
        """Write a keyfile crash-safely (unique temp + os.replace) with 0600 perms
        so the Fernet key is never world-readable."""
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            os.chmod(tmp, 0o600)  # owner-only before it becomes the live key
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _key_file_decrypts(self, key_path: Path) -> bool:
        """True if the key at ``key_path`` decrypts a stored secret (or none exist)."""
        if not key_path.exists():
            return False
        with session_scope(self.engine) as db:
            row = db.exec(select(SecretRecord).limit(1)).first()
            enc = row.enc_value if row is not None else None
        if enc is None:
            return True
        try:
            Fernet(key_path.read_bytes()).decrypt(enc.encode("utf-8"))
            return True
        except Exception:  # noqa: BLE001
            return False

    def _recover_key(self) -> None:
        """Finish or discard a rotation interrupted between commit and key promote.

        A leftover ``.secrets.key.new`` means rotate_key didn't complete its final
        atomic promote. Decide by trial-decrypt which key matches the DB ciphertext:
        current already works → drop ``.new``; ``.new`` works (commit landed) →
        promote it; neither but ``.bak`` works (commit never landed) → restore .bak."""
        import os

        key_path = self.root / ".secrets.key"
        new_path = key_path.with_name(key_path.name + ".new")
        bak = key_path.with_name(key_path.name + ".bak")
        if not new_path.exists():
            return
        if self._key_file_decrypts(key_path):
            pass  # promote already happened (or wasn't needed)
        elif self._key_file_decrypts(new_path):
            os.replace(new_path, key_path)  # commit landed; finish the promote
            return
        elif bak.exists() and self._key_file_decrypts(bak):
            self._atomic_write_key(key_path, bak.read_bytes())  # commit never landed
        try:
            os.unlink(new_path)
        except OSError:
            pass

    # -- encryption ---------------------------------------------------------
    def _fernet(self) -> Fernet:
        key_path = self.root / ".secrets.key"
        if not key_path.exists():
            # First run (no key, no secrets) → generate. But if encrypted secrets
            # ALREADY exist while the key is gone (e.g. a key-less restore), a new
            # key can't decrypt them — generating silently masks the loss. We still
            # generate so the daemon BOOTS (never brick the process the user needs
            # to recover from), but log loudly; the lost state is then surfaced by
            # ``key_valid()`` / the /diagnostics ``secrets_key_valid`` flag.
            if self._has_secret_rows():
                _log.error(
                    "secrets key %s is MISSING but encrypted secrets exist — "
                    "generating a new key; stored credentials cannot be decrypted "
                    "until the original .secrets.key is restored (re-enter keys to "
                    "fix). See /diagnostics secrets_key_valid.",
                    key_path,
                )
            self._atomic_write_key(key_path, Fernet.generate_key())  # 0600, not world-readable
        return Fernet(key_path.read_bytes())

    def _has_secret_rows(self) -> bool:
        with session_scope(self.engine) as db:
            return db.exec(select(SecretRecord).limit(1)).first() is not None

    def key_valid(self) -> bool:
        """True if the stored key can actually decrypt an existing secret.

        Returns True when there are no secrets yet (nothing to validate). A real
        trial-decrypt of one row catches the lost/mismatched-key condition that
        ``.secrets.key`` *existence* alone cannot — the one signal that reveals a
        key-less restore (vs. a freshly regenerated wrong key reading as present)."""
        with session_scope(self.engine) as db:
            row = db.exec(select(SecretRecord).limit(1)).first()
            enc = row.enc_value if row is not None else None
        if enc is None:
            return True
        try:
            self._decrypt(enc)
            return True
        except Exception:  # noqa: BLE001 — InvalidToken (or any decrypt failure)
            return False

    def _encrypt(self, value: str) -> str:
        return self._fernet().encrypt(value.encode("utf-8")).decode("utf-8")

    def _decrypt(self, token: str) -> str:
        return self._fernet().decrypt(token.encode("utf-8")).decode("utf-8")

    def rotate_key(self) -> int:
        """Re-encrypt every secret under a fresh Fernet key, CRASH-SAFELY.

        Staging order so a crash at any point is recoverable on next boot
        (:meth:`_recover_key`): the new key is persisted to ``.secrets.key.new`` and
        the old key backed up to ``.secrets.key.bak`` BEFORE the DB commit, then the
        new ciphertext is committed, then ``.new`` is atomically promoted to the
        live key. A crash before commit leaves old key + old ciphertext intact; a
        crash after commit but before promote is finished from ``.new`` on boot."""
        import os

        key_path = self.root / ".secrets.key"
        new_path = key_path.with_name(key_path.name + ".new")
        bak = key_path.with_name(key_path.name + ".bak")
        old = self._fernet()
        new_key = Fernet.generate_key()
        new = Fernet(new_key)
        with session_scope(self.engine) as db:
            rows = list(db.exec(select(SecretRecord)))
            for r in rows:
                plain = old.decrypt(r.enc_value.encode("utf-8"))
                r.enc_value = new.encrypt(plain).decode("utf-8")
                db.add(r)
            # Persist new key (staged) + back up the old one BEFORE committing the
            # new ciphertext, so a crash leaves a recoverable (.new + .bak) state.
            if key_path.exists():
                self._atomic_write_key(bak, key_path.read_bytes())
            self._atomic_write_key(new_path, new_key)
            try:
                db.commit()
            except Exception:
                try:  # commit failed → DB still holds OLD ciphertext; drop staged key
                    os.unlink(new_path)
                except OSError:
                    pass
                raise
        # Commit landed (DB now holds NEW ciphertext) → promote the new key atomically.
        os.replace(new_path, key_path)
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
