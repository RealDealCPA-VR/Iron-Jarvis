"""Webhook signature helpers (HMAC-SHA256).

Inbound webhooks verify a signature the external caller computed over the raw
request body; outbound webhooks sign the payload we POST. Both sides share the
same hashing + canonicalization so a roundtrip verifies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
