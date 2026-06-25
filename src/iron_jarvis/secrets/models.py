"""Secrets persistence model (§7 secret handling, §22 SQLite backend).

One ``SecretRecord`` row per named credential. The value is stored only as
``enc_value`` — Fernet ciphertext encoded as base64 text — so the plaintext is
never written to the database. Swapping SQLite for another backend is an
engine-URL change (§22); the encryption layer is unaffected.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class SecretRecord(SQLModel, table=True):
    """An encrypted-at-rest secret (api_key | oauth | token | password | generic)."""

    id: str = Field(default_factory=lambda: new_id("sec"), primary_key=True)
    name: str = Field(unique=True, index=True)
    kind: str = "generic"  # api_key | oauth | token | password | generic
    description: str = ""
    enc_value: str = ""  # Fernet ciphertext, base64 text — never plaintext
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
