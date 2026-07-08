"""Connection Registry — the one clean entry point for connecting LLMs.

Connecting a provider must be FLAWLESS and CLEAR: pick a provider, give it an API
key *or* run the OAuth 2.0 + PKCE flow, and it becomes "connected". Credentials
are written **only** to the encrypted secrets vault; this registry persists just
the connection *state* (:class:`ConnectionRecord`) and never stores or returns a
secret value through its status/listing surfaces.

The registry is transport- and config-injected so it runs fully offline in tests:

* ``secrets`` — the :class:`~iron_jarvis.secrets.manager.SecretsManager` (or any
  object exposing ``get/set/set_oauth/get_oauth/delete``).
* ``http_factory`` — returns an HTTP client (``.post(url, data=, headers=)``) used
  for token exchange/refresh. Defaults to a thin ``httpx.Client``.
* ``oauth_app`` — ``oauth_app(provider) -> {client_id, client_secret, redirect_uri}``
  resolving the registered OAuth *app* credentials (from secrets/config).
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import ConnectionRecord
from .oauth import OAuthClient
from .specs import BUILTIN_SPECS, ConnectionSpec

#: ``oauth_app(provider) -> {"client_id", "client_secret", "redirect_uri"}``
OAuthAppResolver = Callable[[str], dict]


def _account_label(token: dict) -> str:
    """Human label for the connected account, ALWAYS a string.

    Anthropic returns ``account`` as a DICT ({uuid, email_address}); binding
    that into the TEXT column crashed the final "mark connected" write AFTER
    the token was already vaulted (live-hit 2026-07-01). Normalize every shape.
    """
    acct = token.get("account")
    if isinstance(acct, dict):
        acct = (
            acct.get("email_address")
            or acct.get("email")
            or acct.get("name")
            or acct.get("uuid")
            or ""
        )
    return str(acct or token.get("email") or "")


def _jwt_claim_email(id_token: str) -> str:
    """Best-effort ``email`` claim from a JWT, for DISPLAY only.

    No signature verification — the token arrived from the provider over TLS
    and this value gates nothing (it labels the connection in the UI).
    """
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return str(claims.get("email") or "")
    except Exception:  # noqa: BLE001 — display-only, never fail the flow
        return ""


def _default_http_factory():
    import httpx  # lazy: only needed for real network token exchange

    return httpx.Client(timeout=30)


def _no_oauth_app(provider: str) -> dict:
    return {}


class ConnectionRegistry:
    """Connect / disconnect / test LLM providers; secrets stay in the vault."""

    def __init__(
        self,
        engine: Engine,
        secrets,
        *,
        http_factory: Callable[[], object] | None = None,
        oauth_app: OAuthAppResolver | None = None,
        prober: "Callable[[str, str], tuple[bool, str]] | None" = None,
    ) -> None:
        self.engine = engine
        self.secrets = secrets
        self._http_factory = http_factory or _default_http_factory
        self._oauth_app = oauth_app or _no_oauth_app
        # Optional live reachability probe (provider, credential) -> (ok, detail).
        # When None (the default, and in the offline test suite), test() stays
        # presence-only; the platform wires a real network probe in production.
        self._prober = prober
        self._specs: dict[str, ConnectionSpec] = dict(BUILTIN_SPECS)
        # state -> {"verifier", "provider", "created_at"} for in-flight OAuth flows
        self._pending: dict[str, dict] = {}
        # The connect/disconnect/oauth endpoints are sync `def` → FastAPI runs them
        # on concurrent threadpool threads. Serialize the cross-store mutations
        # (vault write + ConnectionRecord upsert must commit together) and the
        # _pending dict so a connect racing a disconnect on one provider can't leave
        # status='connected' with no credential, and two starts can't race _pending.
        import threading

        self._lock = threading.RLock()

    # --- specs ------------------------------------------------------------

    def register(self, spec: ConnectionSpec) -> None:
        """Register (or override) a provider spec — e.g. a generic OAuth one."""
        self._specs[spec.provider] = spec

    def specs(self) -> list[ConnectionSpec]:
        """All known provider specs, ordered by provider id."""
        return [self._specs[k] for k in sorted(self._specs)]

    def get_spec(self, provider: str) -> ConnectionSpec | None:
        return self._specs.get(provider)

    # --- status (NEVER leaks secret values) -------------------------------

    def status(self) -> list[dict]:
        """Per-provider connection status. Carries names/scopes, never secrets."""
        out: list[dict] = []
        for spec in self.specs():
            record = self._get_record(spec.provider)
            status = record.status if record else "disconnected"
            connected = status == "connected"
            account = record.account if record else ""
            # Pixio honors a PIXIO_API_KEY env fallback everywhere it's used
            # (tools, publish, the studio's env injection) — the card must not
            # call a working setup "disconnected" (live-hit 2026-07-07).
            if (
                spec.provider == "pixio"
                and not connected
                and os.environ.get("PIXIO_API_KEY", "").strip()
            ):
                status, connected = "connected", True
                account = "PIXIO_API_KEY (environment)"
            if record and record.scopes_json and record.scopes_json not in ("[]", ""):
                scopes = _loads_list(record.scopes_json)
            else:
                scopes = list(spec.scopes)
            out.append(
                {
                    "provider": spec.provider,
                    "display_name": spec.display_name,
                    "method": spec.method,
                    "supports_oauth": spec.supports_oauth,
                    "supports_api_key": spec.supports_api_key,
                    "oauth_help": spec.oauth_help,
                    # Manual-code providers (Anthropic) show the user a code to
                    # paste back instead of redirecting to a local callback.
                    "oauth_manual_code": spec.oauth_manual_code,
                    "key_help": spec.key_help,
                    "connected": connected,
                    "status": status,
                    "account": account,
                    "scopes": scopes,
                }
            )
        return out

    # --- API key ----------------------------------------------------------

    def set_api_key(self, provider: str, key: str) -> ConnectionRecord:
        """Store an API key in the vault and mark the provider connected."""
        spec = self._require_spec(provider)
        if not spec.supports_api_key:
            raise ValueError(
                f"provider '{provider}' does not accept an API key (use OAuth login)"
            )
        secret_name = spec.key_secret_name or f"{provider}_api_key"
        with self._lock:  # vault write + status row commit together (atomic connect)
            self.secrets.set(secret_name, key, kind="api_key")
            return self._upsert(
                provider,
                method="api_key",
                status="connected",
                secret_name=secret_name,
                connected_at=utcnow(),
            )

    # --- OAuth 2.0 + PKCE -------------------------------------------------

    def start_oauth(self, provider: str) -> dict:
        """Begin an OAuth flow: returns ``{authorization_url, state}``.

        Generates a PKCE verifier/challenge + CSRF ``state``, stashes the verifier
        keyed by ``state`` (server-side, in memory), and marks the provider as
        ``needs_auth``. Raises if the provider is not OAuth or has no client id.
        """
        spec = self._require_spec(provider)
        if not spec.supports_oauth:
            raise ValueError(f"provider '{provider}' does not support OAuth login")
        app = self._oauth_app(provider) or {}
        # Fall back to the spec's embedded PUBLIC client id (the provider's own
        # CLI app id) so account login works with no app registration.
        client_id = app.get("client_id") or spec.oauth_client_id
        if not client_id:
            raise ValueError(
                f"no OAuth client configured for '{provider}' — set its client id"
            )
        verifier, challenge = OAuthClient.pkce_pair()
        state = OAuthClient.new_state()
        with self._lock:  # serialize _pending prune+insert (no dict-changed-size race)
            self._prune_pending()  # bound _pending so a drive-by GET can't grow memory
            self._pending[state] = {
                "verifier": verifier,
                "provider": provider,
                "created_at": utcnow(),
            }
            url = OAuthClient.authorization_url(
                spec,
                client_id=client_id,
                # A custom-registered app carries its own redirect; the embedded
                # public client falls back to ITS registered redirect (the OAuth
                # server hard-rejects any redirect_uri not on the client's list).
                redirect_uri=app.get("redirect_uri") or spec.oauth_redirect_uri,
                state=state,
                code_challenge=challenge,
            )
            self._upsert(provider, method="oauth", status="needs_auth")
            return {
                "authorization_url": url,
                "state": state,
                "manual_code": spec.oauth_manual_code,
            }

    def complete_oauth(
        self, provider: str, *, code: str, state: str
    ) -> ConnectionRecord:
        """Finish an OAuth flow: exchange ``code`` for a token, store it, connect.

        Looks up the PKCE verifier by ``state`` (raising on an unknown/expired
        state), exchanges the code at the provider's token endpoint, stores the
        token dict in the vault, and marks the provider connected.

        MANUAL-CODE flow (``spec.oauth_manual_code``): the user pastes the code
        the provider displayed, which embeds the state as ``code#state`` — split
        it here so the paste-code UI only has to send one field.
        """
        spec = self._require_spec(provider)
        code = (code or "").strip()
        if "#" in code and not state:
            code, _, state = code.partition("#")
            code, state = code.strip(), state.strip()
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or pending.get("provider") != provider:
            raise ValueError("unknown or expired OAuth state")
        app = self._oauth_app(provider) or {}
        client_id = app.get("client_id") or spec.oauth_client_id
        http = self._http_factory()
        minted_key = ""
        try:
            token = OAuthClient.exchange_code(
                spec,
                code=code,
                code_verifier=pending["verifier"],
                client_id=client_id,
                client_secret=app.get("client_secret") or "",
                redirect_uri=app.get("redirect_uri") or spec.oauth_redirect_uri,
                http=http,
                # Anthropic's manual-code token exchange requires the state field.
                state=state if spec.oauth_manual_code else "",
            )
            # Defense-in-depth: never persist a failed/empty exchange as a
            # credential (that would flip the provider to "connected" but
            # always fall to mock).
            token = dict(token or {})
            if "error" in token or not token.get("access_token"):
                raise ValueError(
                    "OAuth token exchange failed: "
                    + (
                        token.get("error_description")
                        or token.get("error")
                        or "no access_token in token response"
                    )
                )
            # KEY MINT (OpenAI Codex flow): the account token can't call
            # api.openai.com — exchange the id_token for a REAL API key while
            # the HTTP client is still open. The mint only works for accounts
            # WITH an API organization; a subscription-only ChatGPT account
            # gets "Invalid ID token: missing organization_id" — that is NOT a
            # failure of the login, so fall back to storing the OAuth token,
            # which the OpenAI adapter routes to the ChatGPT (Codex) backend
            # for subscription-billed inference.
            if spec.oauth_key_exchange:
                id_token = token.get("id_token") or ""
                try:
                    if not id_token:
                        raise ValueError("no id_token in the token response")
                    minted_key = OAuthClient.mint_api_key(
                        spec, id_token=id_token, client_id=client_id, http=http
                    )
                except ValueError:
                    minted_key = ""  # no API org — ChatGPT-backend fallback
        finally:
            _close(http)

        if spec.oauth_key_exchange and minted_key:
            # Store the MINTED KEY as the credential (kind api_key) — the OAuth
            # token itself is deliberately NOT persisted (it can't run
            # inference, and credential() would prefer it over the key).
            account = _jwt_claim_email(token.get("id_token") or "")
            secret_name = spec.key_secret_name or f"{provider}_api_key"
            with self._lock:
                self.secrets.set(secret_name, minted_key, kind="api_key")
                return self._upsert(
                    provider,
                    method="oauth",
                    status="connected",
                    secret_name=secret_name,
                    account=account,
                    scopes_json=json.dumps(list(spec.scopes)),
                    connected_at=utcnow(),
                )

        token = self._stamp_expiry(token)
        secret_name = f"{provider}_oauth"
        scope = token.get("scope")
        scopes = scope.split() if isinstance(scope, str) and scope else list(spec.scopes)
        # ChatGPT-backend fallback tokens carry the email in the id_token JWT.
        account = _account_label(token) or _jwt_claim_email(token.get("id_token") or "")
        with self._lock:  # token write + status row commit together (atomic connect)
            self.secrets.set_oauth(secret_name, token)
            return self._upsert(
                provider,
                method="oauth",
                status="connected",
                secret_name=secret_name,
                account=account,
                scopes_json=json.dumps(scopes),
                connected_at=utcnow(),
            )

    def loopback_redirect(self, provider: str) -> tuple[int, str] | None:
        """``(port, path)`` for a one-shot RFC 8252 loopback listener, or None.

        Only embedded public clients registered against a FIXED localhost port
        (OpenAI's :1455) need one. Custom user-registered apps redirect to the
        daemon's own ``/oauth/{provider}/callback``; manual-code providers
        (Anthropic) never redirect anywhere.
        """
        spec = self.get_spec(provider)
        if spec is None or not spec.supports_oauth or spec.oauth_manual_code:
            return None
        app = self._oauth_app(provider) or {}
        if app.get("client_id") or app.get("redirect_uri"):
            return None  # custom app — the daemon callback / user redirect handles it
        redirect = spec.oauth_redirect_uri
        if not redirect.startswith(("http://localhost", "http://127.0.0.1")):
            return None
        parsed = urlparse(redirect)
        return (parsed.port or 80, parsed.path or "/")

    # --- credential resolution -------------------------------------------

    def credential(self, provider: str) -> str | None:
        """Return the usable credential: API key, or a fresh OAuth access token.

        For OAuth, transparently refreshes an expired token when a
        ``refresh_token`` is available. Returns ``None`` if nothing is stored.
        """
        spec = self.get_spec(provider)
        if spec is None:
            return None
        # Prefer a connected OAuth account token; otherwise fall back to an API key.
        if spec.supports_oauth:
            token = self._oauth_access_token(provider, spec)
            if token:
                return token
        if spec.supports_api_key:
            return self.secrets.get(spec.key_secret_name or f"{provider}_api_key")
        if spec.method == "oauth":  # oauth-only spec with no embedded client
            return self._oauth_access_token(provider, spec)
        return None

    def has_credential(self, provider: str) -> bool:
        """Presence-only credential check — NEVER refreshes (safe on the loop).

        For ``api_key`` providers, reports whether the vault holds the key. For
        ``oauth`` providers, reports whether a stored token exists WITHOUT calling
        :meth:`_oauth_access_token` (i.e. no blocking network refresh). Wire this
        into :class:`ProviderManager` as the ``presence_resolver`` so availability
        / health checks stay off the network.
        """
        spec = self.get_spec(provider)
        if spec is None:
            return False
        if spec.supports_oauth:
            token = self.secrets.get_oauth(f"{provider}_oauth")
            if token and token.get("access_token"):
                return True
        if spec.supports_api_key:
            return bool(self.secrets.get(spec.key_secret_name or f"{provider}_api_key"))
        if spec.method == "oauth":
            token = self.secrets.get_oauth(f"{provider}_oauth")
            return bool(token and token.get("access_token"))
        return False

    def _oauth_access_token(
        self, provider: str, spec: ConnectionSpec
    ) -> str | None:
        secret_name = f"{provider}_oauth"
        token = self.secrets.get_oauth(secret_name)
        if not token:
            return None
        if _is_expired(token) and token.get("refresh_token"):
            app = self._oauth_app(provider) or {}
            http = self._http_factory()
            fresh = None
            try:
                fresh = OAuthClient.refresh(
                    spec,
                    refresh_token=token["refresh_token"],
                    client_id=app.get("client_id") or spec.oauth_client_id,
                    client_secret=app.get("client_secret") or "",
                    http=http,
                )
            except Exception:
                # A failed refresh must not pollute the stored token with an
                # error body — leave the existing token untouched and return it.
                fresh = None
            finally:
                _close(http)
            if fresh and not fresh.get("error") and fresh.get("access_token"):
                merged = {**token, **fresh}
                # A refresh response often omits the refresh_token — keep the old one.
                if not merged.get("refresh_token"):
                    merged["refresh_token"] = token.get("refresh_token")
                merged = self._stamp_expiry(merged, force=True)
                self.secrets.set_oauth(secret_name, merged)
                token = merged
        return token.get("access_token")

    # --- test / disconnect ------------------------------------------------

    def test(self, provider: str) -> dict:
        """Probe a connection: ``{ok, detail}`` with a clear, user-facing detail."""
        spec = self.get_spec(provider)
        if spec is None:
            return {"ok": False, "detail": f"unknown provider '{provider}'"}
        if provider == "mock":
            return {"ok": True, "detail": "mock is always connectable (offline)"}
        record = self._get_record(provider)
        # Pixio's env fallback counts as connected (see status()) — Test must
        # probe the key that's actually in use, not report "not connected".
        env_key = (
            os.environ.get("PIXIO_API_KEY", "").strip() if provider == "pixio" else ""
        )
        if (record is None or record.status != "connected") and not env_key:
            return {
                "ok": False,
                "detail": (
                    f"{spec.display_name} is not connected — "
                    "connect it on the Connections page"
                ),
            }
        cred = self.credential(provider) or env_key
        if not cred:
            return {
                "ok": False,
                "detail": (
                    f"{spec.display_name} has no stored credential — "
                    "reconnect it on the Connections page"
                ),
            }
        # Real reachability probe (when wired): actually hit the provider so a
        # bad/expired/revoked credential is caught HERE, not at the first session
        # (where it would silently fall back to mock). Presence-only when no prober.
        if self._prober is not None:
            try:
                ok, detail = self._prober(provider, cred)
                return {"ok": bool(ok), "detail": detail}
            except Exception as exc:  # a probe error must never crash Test
                return {
                    "ok": False,
                    "detail": f"{spec.display_name}: probe failed ({exc})",
                }
        return {"ok": True, "detail": f"{spec.display_name} is connected"}

    def disconnect(self, provider: str) -> ConnectionRecord:
        """Drop the stored credential and mark the provider disconnected."""
        spec = self._require_spec(provider)
        record = self._get_record(provider)
        # Clear EVERY credential this provider may hold (OAuth token + API key),
        # so a provider connected by either method is fully disconnected.
        names = {
            (record.secret_name if record else "") or "",
            _default_secret_name(spec),
        }
        if spec.supports_oauth:
            names.add(f"{provider}_oauth")
        if spec.supports_api_key:
            names.add(spec.key_secret_name or f"{provider}_api_key")
        with self._lock:  # credential delete + status row commit together
            for name in filter(None, names):
                try:
                    self.secrets.delete(name)
                except Exception:  # deleting an absent secret must not break disconnect
                    pass
            return self._upsert(
                provider,
                method=spec.method,
                status="disconnected",
                account="",
                connected_at=None,
            )

    # --- internals --------------------------------------------------------

    #: In-flight OAuth states expire / are capped so an unauthenticated drive-by
    #: GET /oauth/{provider}/start cannot grow ``_pending`` without bound.
    _OAUTH_PENDING_TTL = timedelta(minutes=10)
    _OAUTH_PENDING_CAP = 256

    def _prune_pending(self) -> None:
        cutoff = utcnow() - self._OAUTH_PENDING_TTL
        for st in [
            s for s, v in self._pending.items()
            if not v.get("created_at") or v["created_at"] < cutoff
        ]:
            self._pending.pop(st, None)
        if len(self._pending) > self._OAUTH_PENDING_CAP:
            oldest = sorted(
                self._pending.items(), key=lambda kv: kv[1].get("created_at") or utcnow()
            )[: len(self._pending) - self._OAUTH_PENDING_CAP]
            for st, _ in oldest:
                self._pending.pop(st, None)

    def _require_spec(self, provider: str) -> ConnectionSpec:
        spec = self._specs.get(provider)
        if spec is None:
            raise KeyError(f"unknown provider '{provider}'")
        return spec

    def _stamp_expiry(self, token: dict, *, force: bool = False) -> dict:
        """Record an absolute ``expires_at`` from a relative ``expires_in``."""
        expires_in = token.get("expires_in")
        if expires_in and (force or "expires_at" not in token):
            token["expires_at"] = (
                utcnow() + timedelta(seconds=int(expires_in))
            ).isoformat()
        return token

    def _get_record(self, provider: str) -> ConnectionRecord | None:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(ConnectionRecord).where(ConnectionRecord.provider == provider)
            ).first()
            if row is not None:
                db.expunge(row)
            return row

    def _upsert(self, provider: str, **fields) -> ConnectionRecord:
        with session_scope(self.engine) as db:
            row = db.exec(
                select(ConnectionRecord).where(ConnectionRecord.provider == provider)
            ).first()
            if row is None:
                row = ConnectionRecord(provider=provider)
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row


# --- module helpers -------------------------------------------------------


def _default_secret_name(spec: ConnectionSpec) -> str:
    if spec.method == "oauth":
        return f"{spec.provider}_oauth"
    return spec.key_secret_name or f"{spec.provider}_api_key"


def _loads_list(raw: str) -> list:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


#: Refresh an OAuth token this many seconds BEFORE its hard expiry, to absorb
#: clock skew and avoid a 401 on a request that straddles the expiry boundary.
_EXPIRY_LEEWAY = timedelta(seconds=60)


def _is_expired(token: dict) -> bool:
    exp = token.get("expires_at")
    if not exp:
        return False
    try:
        dt = datetime.fromisoformat(exp)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return utcnow() >= (dt - _EXPIRY_LEEWAY)


def _close(http) -> None:
    close = getattr(http, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # closing a transport must never break the flow
            pass
