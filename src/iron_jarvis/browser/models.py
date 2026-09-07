"""The one persisted row the Browser capability owns: a paired browser (plan §4.3).

A :class:`BrowserPairing` is the durable half of the D06A trust relationship
between the Iron Jarvis browser add-on and this daemon. It holds the SHA-256 of
the pairing token and never the token itself, which is the whole point of the
table: nothing in this repository keeps a recoverable secret outside the Fernet
vault, and a pairing token in a plaintext column would be one — readable in the
SQLite file, in every backup under ``<home>/backups/``, and in any diagnostics
bundle the user sends. The plaintext exists for exactly the length of one
``browser.paired`` frame (see :mod:`iron_jarvis.browser.pairing`).

The silent failure this shape prevents: a column named ``token`` would still
*work*. Pairing would succeed, the socket would authenticate, every test would
pass, and the leak would only be discovered by someone reading a backup. A hash
column cannot be misused that way — there is nothing in it to replay.

Downloads are deliberately **not** a table (D23/D24): a completed download is an
event plus a ledger row, so the ledger stays the single authoritative action log.

Registration: this table is created lazily by
:class:`~iron_jarvis.browser.pairing.PairingStore` (belt and braces) and the
coordinator must ALSO add ``"..browser.models"`` to
``core.db._LATE_MODEL_MODULES``. Without that entry a lazily-created table lands
on every fresh test DB and on no real install — the v1.151.2 lesson, which cost
a release when ``agentthreadrecord.chat_thread_id`` was missing on the user's
daemon while the whole suite was green. This module therefore stays
import-light: nothing heavy may sit behind a table registration.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class BrowserPairing(SQLModel, table=True):
    """One browser that has been paired with this install (plan §4.3, verbatim).

    A row survives ``POST /browser/disconnect`` — that ends the live socket and
    keeps the credential — and is retired by ``POST /browser/forget``, which
    stamps :attr:`revoked_at` so the next connect starts pairing again. Rows are
    revoked rather than deleted so "this browser was forgotten on the 6th" stays
    answerable; a deleted row makes a support question unanswerable.
    """

    id: str = Field(default_factory=lambda: new_id("bpair"), primary_key=True)
    #: SHA-256 hex of the pairing token. NEVER the plaintext — see the module
    #: docstring. Verification hashes the candidate and compares digests.
    token_sha256: str = ""
    #: The add-on's own id, as reported on ``browser.hello``. Public material.
    extension_id: str = ""
    #: Human label for the card, e.g. ``"Chrome on VR-DESKTOP"``.
    label: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    #: Touched on every successful verify, so the card can say when this browser
    #: was last seen without keeping a second liveness store.
    last_seen_at: datetime | None = None
    #: Set by Forget. A non-null value means the credential is dead: verification
    #: refuses it, which is why the column is read on EVERY verify rather than
    #: only when listing rows.
    revoked_at: datetime | None = None


__all__ = ["BrowserPairing"]
