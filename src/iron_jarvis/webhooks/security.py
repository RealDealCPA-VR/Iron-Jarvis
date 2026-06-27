"""Webhook signature helpers (HMAC-SHA256).

Inbound webhooks verify a signature the external caller computed over the raw
request body; outbound webhooks sign the payload we POST. Both sides share the
same hashing + canonicalization so a roundtrip verifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


def canonical_bytes(body: Any) -> bytes:
    """Deterministic JSON encoding used when no raw body is available.

    Sorted keys + compact separators so signing and verification agree even if
    the dict was rebuilt with a different key order.
    """
    return json.dumps(
        body, default=str, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sign(payload: bytes, secret: str) -> str:
    """Return the hex HMAC-SHA256 of ``payload`` keyed by ``secret``."""
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify(payload: bytes, secret: str, signature: str | None) -> bool:
    """Constant-time signature check.

    If no secret is configured the webhook is unauthenticated and any request is
    accepted. With a secret, a missing/empty signature is rejected. A leading
    ``sha256=`` prefix (a common convention) is tolerated.
    """
    if not secret:
        return True
    if not signature:
        return False
    candidate = signature
    if candidate.startswith("sha256="):
        candidate = candidate.split("=", 1)[1]
    expected = sign(payload, secret)
    return hmac.compare_digest(expected, candidate)


# --- v2: timestamped signatures with replay/skew protection (opt-in) ----------


def sign_v2(timestamp: str | int, payload: bytes, secret: str) -> str:
    """Hex HMAC-SHA256 over ``{timestamp}.{payload}`` keyed by ``secret``.

    Binding the timestamp into the MAC means a captured signature is only valid
    for its original timestamp, so an old request can't be replayed (the caller
    additionally enforces a freshness window via :func:`verify_signed`).
    """
    mac_input = f"{timestamp}.".encode("utf-8") + payload
    return hmac.new(secret.encode("utf-8"), mac_input, hashlib.sha256).hexdigest()


def verify_signed(
    timestamp: str | int | None,
    payload: bytes,
    secret: str,
    signature: str | None,
    max_skew: int = 300,
) -> bool:
    """Constant-time check of a v2 timestamped signature.

    Like :func:`verify`: an empty secret accepts anything. Otherwise a missing
    timestamp/signature is rejected, the timestamp must parse as an int and lie
    within ``max_skew`` seconds of now, and the MAC must match. A leading
    ``sha256=`` prefix is tolerated. Caller is responsible for nonce/replay
    caching beyond the freshness window.
    """
    if not secret:
        return True
    if not signature or timestamp is None:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > max_skew:
        return False
    candidate = signature
    if candidate.startswith("sha256="):
        candidate = candidate.split("=", 1)[1]
    expected = sign_v2(ts, payload, secret)
    return hmac.compare_digest(expected, candidate)
