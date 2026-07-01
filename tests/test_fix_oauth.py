"""Regression tests for the OAuth / Google-credential audit fixes (offline).

Covers three confirmed findings:

* (6) ``GoogleAdapter`` must send an OAuth access token as ``Authorization:
  Bearer`` (not ``x-goog-api-key``), while a true api_key connection keeps
  ``x-goog-api-key``.
* (7) A failed token exchange (HTTP 4xx / ``{"error": ...}`` / missing
  ``access_token``) must RAISE and never be persisted as a "connected"
  credential.
* (8) Availability/health checks must be presence-only: ``has_credential`` and a
  ``presence_resolver``-wired ``ProviderManager`` never trigger a network
  refresh.

Everything is faked (HTTP transport + secrets vault) so nothing touches the
network or real encryption, mirroring ``test_new_adapters.py`` /
``test_connections.py``.
"""

from __future__ import annotations

import json

import pytest

import iron_jarvis.connections.models  # noqa: F401  (register table before init_db)
from iron_jarvis.connections import ConnectionRegistry
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.providers.adapters.base import LLMMessage
from iron_jarvis.providers.adapters.google import GoogleAdapter
from iron_jarvis.providers.manager import ProviderManager


# --- fakes ---------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeAsyncHTTP:
    """Async ``post`` recorder returning a canned response (adapter transport)."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    async def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return FakeResponse(self._payload)

    @property
    def last(self) -> dict:
        return self.calls[-1]


class FakeSyncHTTP:
    """Sync ``post`` recorder for token exchange/refresh (registry transport).

    Records BOTH body encodings: ``data`` (form, the RFC default) and ``json``
    (Anthropic's console token endpoint) — the ``json`` param shadows the module
    only inside this method, which never uses it.
    """

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url, data=None, headers=None, json=None):
        self.calls.append(
            {"url": url, "data": dict(data or {}), "json": dict(json or {})}
        )
        return FakeResponse(self.payload, self.status_code)

    def close(self):
        self.closed = True


class BoomHTTP:
    """Any network use is a hard failure — proves presence checks stay offline."""

    def post(self, *a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("network refresh attempted during a presence-only check")


class FakeSecrets:
    """In-memory SecretsManager stand-in (get/set/set_oauth/get_oauth/delete)."""

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


def _oauth_app(provider):
    return {
        "client_id": "client-123.apps.googleusercontent.com",
        "client_secret": "shh-secret",
        "redirect_uri": "http://localhost:8765/oauth/google/callback",
    }


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return e


@pytest.fixture
def secrets():
    return FakeSecrets()


def _registry(engine, secrets, http):
    return ConnectionRegistry(
        engine, secrets, http_factory=lambda: http, oauth_app=_oauth_app
    )


_GEMINI_OK = {
    "candidates": [
        {"content": {"role": "model", "parts": [{"text": "hi"}]}, "finishReason": "STOP"}
    ]
}


# --- Finding 6: Google credential header --------------------------------


async def test_google_oauth_sends_authorization_bearer():
    http = FakeAsyncHTTP(_GEMINI_OK)
    adapter = GoogleAdapter(api_key="ya29.access-token", http=http, oauth=True)
    await adapter.complete(
        system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
    )
    assert http.last["headers"]["Authorization"] == "Bearer ya29.access-token"
    # the api_key header must NOT be sent for an OAuth credential
    assert "x-goog-api-key" not in http.last["headers"]


async def test_google_api_key_still_sends_x_goog_api_key():
    http = FakeAsyncHTTP(_GEMINI_OK)
    adapter = GoogleAdapter(api_key="g-test", http=http)  # oauth defaults False
    await adapter.complete(
        system="", messages=[LLMMessage(role="user", content="hi")], tools=[]
    )
    assert http.last["headers"]["x-goog-api-key"] == "g-test"
    assert "Authorization" not in http.last["headers"]


# --- Finding 7: failed token exchange must not "connect" -----------------


def test_complete_oauth_raises_on_error_body_and_does_not_connect(engine, secrets):
    http = FakeSyncHTTP({"error": "invalid_grant", "error_description": "bad code"}, 400)
    registry = _registry(engine, secrets, http)
    state = registry.start_oauth("google")["state"]

    with pytest.raises(ValueError):
        registry.complete_oauth("google", code="abc", state=state)

    # nothing was persisted and the provider is NOT connected
    assert secrets.get_oauth("google_oauth") is None
    by_provider = {s["provider"]: s for s in registry.status()}
    assert by_provider["google"]["connected"] is False
    assert by_provider["google"]["status"] != "connected"


def test_complete_oauth_raises_on_missing_access_token(engine, secrets):
    http = FakeSyncHTTP({"token_type": "Bearer", "expires_in": 3600}, 200)  # no token
    registry = _registry(engine, secrets, http)
    state = registry.start_oauth("google")["state"]

    with pytest.raises(ValueError):
        registry.complete_oauth("google", code="abc", state=state)
    assert secrets.get_oauth("google_oauth") is None


def test_exchange_code_raises_on_http_error():
    from iron_jarvis.connections import OAuthClient
    from iron_jarvis.connections.specs import BUILTIN_SPECS

    http = FakeSyncHTTP({"error": "invalid_client"}, 401)
    with pytest.raises(ValueError):
        OAuthClient.exchange_code(
            BUILTIN_SPECS["google"],
            code="c",
            code_verifier="v",
            client_id="id",
            client_secret="sec",
            redirect_uri="uri",
            http=http,
        )


def test_refresh_raises_on_error_body():
    from iron_jarvis.connections import OAuthClient
    from iron_jarvis.connections.specs import BUILTIN_SPECS

    http = FakeSyncHTTP({"error": "invalid_grant"}, 400)
    with pytest.raises(ValueError):
        OAuthClient.refresh(
            BUILTIN_SPECS["google"],
            refresh_token="rt",
            client_id="id",
            client_secret="sec",
            http=http,
        )


# --- Finding 8: presence-only availability (no network refresh) ----------


def test_has_credential_oauth_presence_only_no_refresh(engine, secrets):
    # An expired token WITH a refresh_token would normally trigger a refresh —
    # has_credential must report presence WITHOUT touching the network.
    secrets.set_oauth(
        "google_oauth",
        {
            "access_token": "stale",
            "refresh_token": "rt",
            "expires_at": "2000-01-01T00:00:00+00:00",  # long expired
        },
    )
    registry = _registry(engine, secrets, BoomHTTP())
    assert registry.has_credential("google") is True  # no AssertionError from BoomHTTP


def test_has_credential_false_when_absent(engine, secrets):
    registry = _registry(engine, secrets, BoomHTTP())
    assert registry.has_credential("google") is False  # oauth, nothing stored
    assert registry.has_credential("anthropic") is False  # api_key, nothing stored


def test_has_credential_api_key_presence(engine, secrets):
    registry = _registry(engine, secrets, BoomHTTP())
    registry.set_api_key("anthropic", "sk-x")
    assert registry.has_credential("anthropic") is True


# --- Anthropic manual-code flow (redirect-URI fix) ------------------------
# The public Claude Code client hard-rejects any unregistered redirect_uri
# ("Redirect URI http://localhost:8787/... is not supported by client"), so the
# flow now uses the registered console callback + a pasted code#state.


_ANTHROPIC_TOKEN_OK = {
    "access_token": "sk-ant-oat01-fresh",
    "refresh_token": "sk-ant-ort01-r",
    "expires_in": 3600,
}


def _anthropic_registry(engine, secrets, http):
    # No custom OAuth app registered — mimics the platform resolver's behavior
    # for embedded public clients (empty redirect -> spec.oauth_redirect_uri).
    return ConnectionRegistry(
        engine,
        secrets,
        http_factory=lambda: http,
        oauth_app=lambda p: {"client_id": None, "client_secret": None, "redirect_uri": ""},
    )


def test_anthropic_authorize_url_uses_registered_console_redirect(engine, secrets):
    registry = _anthropic_registry(engine, secrets, FakeSyncHTTP(_ANTHROPIC_TOKEN_OK))
    out = registry.start_oauth("anthropic")
    url = out["authorization_url"]
    assert out["manual_code"] is True
    assert "redirect_uri=https%3A%2F%2Fconsole.anthropic.com%2Foauth%2Fcode%2Fcallback" in url
    assert "localhost" not in url  # the daemon callback must NEVER be sent
    assert "code=true" in url  # display-the-code switch
    assert "access_type=" not in url  # Google-ism must not leak in


def test_anthropic_manual_code_splits_state_and_exchanges_json(engine, secrets):
    http = FakeSyncHTTP(_ANTHROPIC_TOKEN_OK)
    registry = _anthropic_registry(engine, secrets, http)
    state = registry.start_oauth("anthropic")["state"]

    # The user pastes exactly what claude.ai displayed: "<code>#<state>".
    rec = registry.complete_oauth("anthropic", code=f"the-auth-code#{state}", state="")
    assert rec.status == "connected"

    sent = http.calls[0]
    assert sent["url"] == "https://console.anthropic.com/v1/oauth/token"
    body = sent["json"]  # JSON body — Anthropic's endpoint rejects form encoding
    assert sent["data"] == {}
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "the-auth-code"  # state split off the pasted blob
    assert body["state"] == state  # ...and sent as its own field
    assert body["redirect_uri"] == "https://console.anthropic.com/oauth/code/callback"
    assert body["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert "client_secret" not in body  # public PKCE client has no secret
    assert secrets.get_oauth("anthropic_oauth")["access_token"] == "sk-ant-oat01-fresh"


def test_anthropic_dict_account_is_normalized_not_crashed(engine, secrets):
    # LIVE-HIT regression (2026-07-01): Anthropic returns account as a DICT
    # ({uuid, email_address}); binding it into the TEXT account column raised
    # sqlite3.ProgrammingError on the final "mark connected" write — AFTER the
    # token was already vaulted, stranding a working token behind needs_auth.
    token = {
        "access_token": "sk-ant-oat01-live",
        "refresh_token": "sk-ant-ort01-r",
        "expires_in": 3600,
        "scope": "user:inference user:profile",
        "account": {
            "uuid": "254b8f09-81f5-4cdb-bd3d-eb8c49192f42",
            "email_address": "user@example.com",
        },
    }
    http = FakeSyncHTTP(token)
    registry = _anthropic_registry(engine, secrets, http)
    state = registry.start_oauth("anthropic")["state"]

    rec = registry.complete_oauth("anthropic", code=f"code#{state}", state="")
    assert rec.status == "connected"  # the write no longer crashes
    assert rec.account == "user@example.com"  # dict normalized to the email
    assert secrets.get_oauth("anthropic_oauth")["access_token"] == "sk-ant-oat01-live"


def test_anthropic_manual_code_bad_state_still_raises(engine, secrets):
    registry = _anthropic_registry(engine, secrets, FakeSyncHTTP(_ANTHROPIC_TOKEN_OK))
    registry.start_oauth("anthropic")
    with pytest.raises(ValueError):
        registry.complete_oauth("anthropic", code="code#wrong-state", state="")


# --- OpenAI Codex flow: loopback redirect + API-key mint -------------------


class SeqHTTP:
    """Sync ``post`` recorder returning a QUEUE of canned responses (multi-step
    flows: code exchange, then the key-mint token exchange)."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url, data=None, headers=None, json=None):
        self.calls.append(
            {"url": url, "data": dict(data or {}), "json": dict(json or {})}
        )
        return FakeResponse(self.payloads.pop(0))

    def close(self):
        self.closed = True


def _fake_jwt(claims: dict) -> str:
    """header.payload.signature — only the (unverified) payload matters here."""
    import base64

    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{seg}.sig"


def _openai_registry(engine, secrets, http):
    # No custom OAuth app — mimics the platform resolver for embedded clients.
    return ConnectionRegistry(
        engine,
        secrets,
        http_factory=lambda: http,
        oauth_app=lambda p: {"client_id": None, "client_secret": None, "redirect_uri": ""},
    )


def test_openai_login_mints_api_key(engine, secrets):
    id_tok = _fake_jwt({"email": "user@example.com"})
    http = SeqHTTP(
        [
            # 1) code exchange: account token + id_token (can't call the API)
            {"access_token": "chatgpt-token", "id_token": id_tok,
             "refresh_token": "r1", "expires_in": 3600},
            # 2) RFC 8693 token exchange: the minted REAL API key
            {"access_token": "sk-proj-minted-key"},
        ]
    )
    registry = _openai_registry(engine, secrets, http)
    state = registry.start_oauth("openai")["state"]

    rec = registry.complete_oauth("openai", code="auth-code", state=state)
    assert rec.status == "connected"
    assert rec.account == "user@example.com"  # display label from the id_token

    assert len(http.calls) == 2
    mint = http.calls[1]["data"]
    assert mint["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert mint["requested_token"] == "openai-api-key"
    assert mint["subject_token"] == id_tok
    assert mint["subject_token_type"] == "urn:ietf:params:oauth:token-type:id_token"

    # The MINTED KEY is the credential; the account token is NOT persisted
    # (credential() would prefer it — and it can't run inference).
    assert secrets.get("openai_api_key") == "sk-proj-minted-key"
    assert secrets.get_oauth("openai_oauth") is None
    assert registry.credential("openai") == "sk-proj-minted-key"
    assert registry.has_credential("openai") is True


def test_openai_login_without_org_falls_back_to_chatgpt_backend(engine, secrets):
    # LIVE-HIT (2026-07-01): a subscription-only ChatGPT account has no API
    # organization, so the key mint fails with "Invalid ID token: missing
    # organization_id". That is NOT a login failure — the flow now stores the
    # OAuth token instead, which the OpenAI adapter routes to the ChatGPT
    # (Codex) backend for subscription-billed inference.
    id_tok = _fake_jwt({"email": "user@example.com"})
    http = SeqHTTP(
        [
            {"access_token": "chatgpt-token", "id_token": id_tok,
             "refresh_token": "r1", "expires_in": 3600},
            # the mint REJECTS the org-less id_token (the live error shape)
            {"error": {"message": "Invalid ID token: missing organization_id",
                       "code": "invalid_subject_token"}},
        ]
    )
    registry = _openai_registry(engine, secrets, http)
    state = registry.start_oauth("openai")["state"]

    rec = registry.complete_oauth("openai", code="auth-code", state=state)
    assert rec.status == "connected"
    assert rec.account == "user@example.com"  # label from the id_token claim
    # The OAuth token IS the credential now (no minted key exists).
    assert secrets.get("openai_api_key") is None
    assert secrets.get_oauth("openai_oauth")["access_token"] == "chatgpt-token"
    assert registry.credential("openai") == "chatgpt-token"


def test_openai_login_without_id_token_still_connects_via_backend(engine, secrets):
    # No id_token at all -> mint impossible -> same ChatGPT-backend fallback.
    http = SeqHTTP([{"access_token": "chatgpt-token", "expires_in": 3600}])
    registry = _openai_registry(engine, secrets, http)
    state = registry.start_oauth("openai")["state"]

    rec = registry.complete_oauth("openai", code="c", state=state)
    assert rec.status == "connected"
    assert secrets.get_oauth("openai_oauth")["access_token"] == "chatgpt-token"


def test_google_keeps_offline_access_params(engine, secrets):
    # The Google-isms moved from the OAuth client into Google's OWN spec — they
    # are what make Google return a refresh_token, so they must survive.
    http = FakeSyncHTTP(_ANTHROPIC_TOKEN_OK)
    registry = _registry(engine, secrets, http)
    url = registry.start_oauth("google")["authorization_url"]
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_provider_manager_availability_is_presence_only(engine, secrets):
    # Expired token present -> available True via presence resolver, no refresh.
    secrets.set_oauth(
        "google_oauth",
        {"access_token": "stale", "refresh_token": "rt",
         "expires_at": "2000-01-01T00:00:00+00:00"},
    )
    registry = _registry(engine, secrets, BoomHTTP())
    pm = ProviderManager(
        credential_resolver=registry.credential,  # would refresh if used
        presence_resolver=registry.has_credential,  # but availability uses this
    )
    # Must not raise (BoomHTTP) — i.e. availability never refreshed.
    assert pm.available("google") is True
    assert pm.available("openai") is False
    assert pm.available("mock") is True
    # health() also stays presence-only
    rows = {r["provider"]: r for r in pm.health()}
    assert rows["google"]["available"] is True
