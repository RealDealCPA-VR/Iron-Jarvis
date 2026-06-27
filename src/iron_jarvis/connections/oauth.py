"""OAuth 2.0 + PKCE client (Authorization Code flow with S256).

A small, dependency-light helper that builds authorization URLs and exchanges /
refreshes tokens. The HTTP transport is **injected** (`http`) so the flow is
fully testable offline: any object exposing ``.post(url, data=..., headers=...)``
that returns a response with ``.json()`` / ``.status_code`` works (an
``httpx.Client`` satisfies this directly; tests pass a fake).

PKCE (RFC 7636): a high-entropy ``code_verifier`` is generated, its S256
challenge (``base64url(sha256(verifier))`` without ``=`` padding) is sent on the
authorization request, and the raw verifier is sent on the token exchange. This
binds the redirect to the client without a shared secret being exposed in the
browser redirect.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
from urllib.parse import urlencode

from .specs import ConnectionSpec


def _b64url(raw: bytes) -> str:
    """base64url-encode ``raw`` with no ``=`` padding (RFC 7636 §A)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _require_token(resp) -> dict:
    """Return the token dict, or raise ``ValueError`` on a failed exchange.

    A failed token exchange (non-2xx, an OAuth ``{"error": ...}`` body, or a
    payload missing ``access_token``) must NOT be persisted as a credential —
    raise so the caller surfaces the failure instead of going "connected but
    always mock".
    """
    data = resp.json()
    status = getattr(resp, "status_code", 200)
    if status >= 400 or not isinstance(data, dict) or "error" in data or not data.get(
        "access_token"
    ):
        detail = None
        if isinstance(data, dict):
            detail = data.get("error_description") or data.get("error")
        raise ValueError(f"token exchange failed: {detail or f'HTTP {status}'}")
    return data


class OAuthClient:
    """Stateless OAuth 2.0 + PKCE helper (Authorization Code, S256)."""

    @staticmethod
    def pkce_pair() -> tuple[str, str]:
        """Return ``(code_verifier, code_challenge)`` for the S256 method.

        ``code_challenge == base64url(sha256(code_verifier))`` with no padding.
        """
        verifier = _b64url(_secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return verifier, challenge

    @staticmethod
    def new_state() -> str:
        """Return an opaque, high-entropy CSRF ``state`` value."""
        return _secrets.token_urlsafe(24)

    @staticmethod
    def authorization_url(
        spec: ConnectionSpec,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
    ) -> str:
        """Build the provider's authorization URL for the PKCE auth-code flow."""
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(spec.scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{spec.auth_url}?{urlencode(params)}"

    @staticmethod
    def exchange_code(
        spec: ConnectionSpec,
        *,
        code: str,
        code_verifier: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        http,
    ) -> dict:
        """Exchange an authorization ``code`` for a token dict at ``token_url``."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }
        resp = http.post(
            spec.token_url,
            data=data,
            headers={"Accept": "application/json"},
        )
        return _require_token(resp)

    @staticmethod
    def refresh(
        spec: ConnectionSpec,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        http,
    ) -> dict:
        """Exchange a ``refresh_token`` for a fresh token dict at ``token_url``."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        resp = http.post(
            spec.token_url,
            data=data,
            headers={"Accept": "application/json"},
        )
        # A successful refresh always returns a fresh access_token; treat a
        # non-2xx / {"error": ...} / missing access_token response as a failure
        # rather than silently caching a bad/empty token.
        return _require_token(resp)
