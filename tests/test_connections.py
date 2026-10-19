"""LLM Connections + OAuth tests (fully offline).

Covers: PKCE S256 correctness, API-key connect (no plaintext in the DB row), the
OAuth 2.0 + PKCE happy path against an injected fake token endpoint, state CSRF
rejection, and test()/disconnect() behaviour. The secrets vault and HTTP
transport are both faked so nothing touches the network or real encryption.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest

import iron_jarvis.connections.models  # noqa: F401  (register table before init_db)
from iron_jarvis.connections import (
    ConnectionRecord,
    ConnectionRegistry,
    ConnectionSpec,
    OAuthClient,
)
from iron_jarvis.connections.models import ConnectionRecord as RecordTable
from iron_jarvis.core.db import init_db, make_engine, session_scope
from sqlmodel import select


# --- fakes ---------------------------------------------------------------


class FakeSecrets:
    """In-memory stand-in for SecretsManager (get/set/set_oauth/get_oauth/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, name, value, kind="generic", description=""):
        self.store[name] = value
        return {"name": name, "kind": kind}

    def get(self, name):
        return self.store.get(name)

    def set_oauth(self, name, token, description=""):
        self.store[name] = json.dumps(token)
        return {"name": name, "kind": "oauth"}

    def get_oauth(self, name):
        raw = self.store.get(name)
        return json.loads(raw) if raw is not None else None

    def delete(self, name):
        return self.store.pop(name, None) is not None


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttp:
    """Records ``.post`` calls and returns a canned token response."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, url, data=None, headers=None):
        self.calls.append({"url": url, "data": dict(data or {}), "headers": dict(headers or {})})
        return FakeResponse(self.payload)


TOKEN_RESPONSE = {
    "access_token": "ya29.fake-access-token",
    "refresh_token": "1//fake-refresh-token",
    "expires_in": 3600,
    "scope": "openid email",
    "token_type": "Bearer",
    "email": "user@example.com",
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def secrets():
    return FakeSecrets()


@pytest.fixture
def http():
    return FakeHttp(TOKEN_RESPONSE)


@pytest.fixture
def oauth_app():
    def resolve(provider):
        return {
            "client_id": "client-123.apps.googleusercontent.com",
            "client_secret": "shh-secret",
            "redirect_uri": "http://localhost:8765/oauth/google/callback",
        }

    return resolve


@pytest.fixture
def registry(engine, secrets, http, oauth_app):
    return ConnectionRegistry(
        engine,
        secrets,
        http_factory=lambda: http,
        oauth_app=oauth_app,
    )


# --- PKCE ----------------------------------------------------------------


def test_pkce_pair_s256_round_trip():
    verifier, challenge = OAuthClient.pkce_pair()
    # Re-deriving the S256 challenge from the verifier must match exactly.
    expected = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert challenge == expected
    assert "=" not in challenge  # no base64 padding
    assert OAuthClient.pkce_pair()[0] != verifier  # high-entropy, unique


# --- API key -------------------------------------------------------------


def test_set_api_key_connects_and_stores_in_vault_only(registry, secrets, engine):
    rec = registry.set_api_key("anthropic", "sk-x")
    assert isinstance(rec, ConnectionRecord)

    by_provider = {s["provider"]: s for s in registry.status()}
    assert by_provider["anthropic"]["connected"] is True
    assert registry.credential("anthropic") == "sk-x"

    # The credential lives in the vault, keyed by the spec's secret name.
    assert secrets.get("anthropic_api_key") == "sk-x"

    # The DB row stores NO secret value: no field carries the key, and the
    # serialized row never contains the plaintext.
    with session_scope(engine) as db:
        row = db.exec(
            select(RecordTable).where(RecordTable.provider == "anthropic")
        ).first()
    assert row is not None
    assert "sk-x" not in json.dumps(row.model_dump(), default=str)
    assert not hasattr(row, "key")
    assert row.secret_name == "anthropic_api_key"  # only the NAME is persisted


def test_set_api_key_rejects_oauth_provider(registry):
    # v1.355.0 (G46): Google (Gemini) TAKES a key now, so the oauth-only
    # provider this pins is a memory drive (Dropbox has no key field at all).
    with pytest.raises(ValueError):
        registry.set_api_key("dropbox", "sk-x")


# --- OAuth 2.0 + PKCE happy path -----------------------------------------


def test_oauth_happy_path(registry, http, secrets):
    started = registry.start_oauth("google")
    url = started["authorization_url"]
    state = started["state"]

    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["client-123.apps.googleusercontent.com"]
    assert qs["state"] == [state]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["response_type"] == ["code"]
    assert "code_challenge" in qs
    assert "openid" in qs["scope"][0] and "email" in qs["scope"][0]
    challenge = qs["code_challenge"][0]

    # The provider is now mid-flow.
    by_provider = {s["provider"]: s for s in registry.status()}
    assert by_provider["google"]["status"] == "needs_auth"

    rec = registry.complete_oauth("google", code="abc", state=state)
    assert rec.status == "connected"

    # The token endpoint was hit with the auth-code grant + PKCE verifier + code.
    assert len(http.calls) == 1
    sent = http.calls[0]
    assert sent["url"] == "https://oauth2.googleapis.com/token"
    assert sent["data"]["grant_type"] == "authorization_code"
    assert sent["data"]["code"] == "abc"
    code_verifier = sent["data"]["code_verifier"]
    assert code_verifier
    # PKCE binding holds: S256(code_verifier) == the challenge sent earlier.
    assert _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest()) == challenge

    # Token stored in the vault; status connected; credential is the access token.
    assert secrets.get_oauth("google_oauth")["access_token"] == "ya29.fake-access-token"
    by_provider = {s["provider"]: s for s in registry.status()}
    assert by_provider["google"]["connected"] is True
    assert by_provider["google"]["account"] == "user@example.com"
    assert registry.credential("google") == "ya29.fake-access-token"


def test_complete_oauth_unknown_state_raises(registry):
    registry.start_oauth("google")
    with pytest.raises(ValueError):
        registry.complete_oauth("google", code="abc", state="not-a-real-state")


# --- test() / disconnect() ----------------------------------------------


def test_test_reports_connected_disconnected_and_mock(registry):
    # disconnected -> ok False with a clear, user-facing message
    result = registry.test("anthropic")
    assert result["ok"] is False
    assert "Connections page" in result["detail"]

    # connected -> ok True
    registry.set_api_key("anthropic", "sk-x")
    assert registry.test("anthropic")["ok"] is True

    # mock -> always ok, even with nothing connected
    assert registry.test("mock")["ok"] is True


def test_disconnect_flips_status_and_drops_secret(registry, secrets):
    registry.set_api_key("anthropic", "sk-x")
    assert secrets.get("anthropic_api_key") == "sk-x"

    registry.disconnect("anthropic")

    by_provider = {s["provider"]: s for s in registry.status()}
    assert by_provider["anthropic"]["connected"] is False
    assert by_provider["anthropic"]["status"] == "disconnected"
    assert secrets.get("anthropic_api_key") is None  # vault entry removed
    assert registry.credential("anthropic") is None


def test_status_never_leaks_secret_values(registry):
    registry.set_api_key("anthropic", "sk-super-secret-value")
    assert "sk-super-secret-value" not in json.dumps(registry.status())


# --- generic oauth spec / unknown provider -------------------------------


def test_start_oauth_without_client_id_raises(engine, secrets, http):
    reg = ConnectionRegistry(
        engine, secrets, http_factory=lambda: http, oauth_app=lambda p: {}
    )
    with pytest.raises(ValueError):
        reg.start_oauth("google")


def test_specs_includes_builtins(registry):
    providers = {s.provider for s in registry.specs()}
    assert {"anthropic", "openai", "google", "mock"} <= providers
    assert isinstance(registry.get_spec("google"), ConnectionSpec)
    # v1.355.0 (G46): Gemini's primary way is an AI Studio key; the account
    # login stays offered beside it.
    google = registry.get_spec("google")
    assert google.method == "api_key"
    assert google.supports_api_key and google.supports_oauth
