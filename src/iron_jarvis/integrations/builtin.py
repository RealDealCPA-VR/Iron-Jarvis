"""Built-in example integrations.

Self-contained, dependency-light integrations that demonstrate the framework
and serve as offline test fixtures:

* :class:`MockIntegration` — always-healthy, no external calls.
* :class:`RestApiIntegration` — health-checks a REST endpoint via an *injected*
  ``http_get`` callable (defaulting to a thin ``httpx`` wrapper). Tests inject a
  fake so no real network is touched. Since v1.357.0 (G84) it also makes ONE
  call to a path under its own address (:meth:`RestApiIntegration.call`) —
  the engine of the ``rest_call`` tool.

``register_builtins(registry)`` wires both into an :class:`IntegrationRegistry`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import Integration, IntegrationSpec
from .registry import IntegrationRegistry

#: ``http_get(url, headers=None) -> dict`` returning at least ``ok`` and,
#: optionally, ``status_code``.
HttpGet = Callable[..., dict]


# --- Mock --------------------------------------------------------------------

MOCK_SPEC = IntegrationSpec(
    id="mock",
    kind="mock",
    display_name="Mock Integration",
    description="A self-contained example integration for tests and demos.",
    required_secrets=[],
    config_schema={"type": "object", "properties": {}},
)


class MockIntegration(Integration):
    """An integration that is always reachable (no external calls)."""

    def test_connection(self) -> dict:
        return {"ok": True, "detail": "the example integration is always healthy"}

    def capabilities(self) -> list[str]:
        return ["mock.ping", "mock.echo"]


# --- Generic REST API --------------------------------------------------------

REST_SPEC = IntegrationSpec(
    id="rest_api",
    kind="rest",
    display_name="Generic REST API",
    description="Connect to an external REST API and health-check it with a GET.",
    # The auth secret is named per-instance via config.auth_secret, so it is not
    # a fixed required secret of the spec itself.
    required_secrets=[],
    config_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "auth_secret": {
                "type": "string",
                "description": "Name of the secret holding the bearer token.",
            },
        },
        "required": ["base_url"],
    },
)


def _httpx_get(url: str, headers: dict[str, str] | None = None) -> dict:
    """Default real HTTP GET wrapper (imported lazily to keep import cheap)."""
    import httpx

    resp = httpx.get(url, headers=headers or {}, timeout=10)
    return {
        "ok": resp.is_success,
        "status_code": resp.status_code,
        "text": resp.text,
    }


class RestApiIntegration(Integration):
    """Health-check an external REST API over an injected HTTP client."""

    def _http_get(self) -> HttpGet:
        # Injected client wins; otherwise fall back to the real httpx wrapper.
        client = self.config.get("http_get")
        return client if callable(client) else _httpx_get

    def test_connection(self) -> dict:
        base_url = self.config.get("base_url")
        if not base_url:
            return {"ok": False, "detail": "missing 'base_url' in config"}

        # SSRF guard: the daemon issues this GET from its trusted loopback host, so
        # refuse private/loopback/link-local/metadata targets (169.254.169.254 etc.)
        # unless the config explicitly opts in — the same guard the webhooks use.
        from ..webhooks.validate import assert_safe_webhook_url

        try:
            assert_safe_webhook_url(
                base_url, allow_internal=bool(self.config.get("allow_internal"))
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"refused unsafe base_url: {exc}"}

        headers: dict[str, str] = {}
        auth_secret = self.config.get("auth_secret")
        token = self.secret(auth_secret) if auth_secret else None
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            result = self._http_get()(base_url, headers=headers)
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

        ok = bool(result.get("ok"))
        status = result.get("status_code")
        detail = f"GET {base_url}"
        if status is not None:
            detail += f" -> {status}"
        if token:
            detail += " (authenticated)"
        return {"ok": ok, "detail": detail}

    def call(self, method: str, path: str, body: Any = None) -> dict:
        """ONE request to this hookup (v1.357.0, G84) — the ``rest_call``
        tool's engine. BLOCKING: the caller runs it off the event loop.

        The address is the hookup's own ``base_url`` plus *path*, and nothing
        else (:func:`build_call_url` refuses a web address, ``..`` and
        anything that is not a path under the base); the same SSRF guard as
        :meth:`test_connection` (``allow_internal`` from the hookup's config);
        the key through the injected secret resolver, sent as ``Authorization:
        Bearer`` exactly as the Test button sends it; redirects are NOT
        followed (the key never goes to another address); bounded per phase
        (:data:`CALL_CONNECT_S` / :data:`CALL_READ_S`), in total
        (:data:`CALL_TOTAL_S`) and in bytes read (:data:`CALL_MAX_BYTES`).

        Returns ``{ok, status, reason, content_type, text, truncated, url,
        error}``. The key's value is replaced in every string returned (an
        echoing service or an error naming the header can never hand it back);
        the caller still masks the text. Never raises."""
        out: dict[str, Any] = {
            "ok": False, "status": None, "reason": "", "content_type": "",
            "text": "", "truncated": False, "url": "", "error": "",
        }
        verb = str(method or "GET").strip().upper() or "GET"
        if verb not in CALL_METHODS:
            out["error"] = f"{verb} is not a method a call can send (use {', '.join(CALL_METHODS)})"
            return out
        base_url = str(self.config.get("base_url") or "").strip()
        if not base_url:
            out["error"] = "it has no web address saved"
            return out
        try:
            url = build_call_url(base_url, path)
        except PathRefused as exc:
            out["error"] = str(exc)
            return out
        out["url"] = url
        from ..webhooks.validate import assert_safe_webhook_url

        try:
            assert_safe_webhook_url(url, allow_internal=bool(self.config.get("allow_internal")))
        except Exception:  # noqa: BLE001 — the guard's own words name webhooks
            out["error"] = (
                "its address points inside this network, which a call to a saved "
                "web service does not reach unless the service allows it"
            )
            return out

        auth_secret = self.config.get("auth_secret")
        token = self.secret(auth_secret) if auth_secret else None
        headers: dict[str, str] = {"Accept": "application/json, text/plain;q=0.9, */*;q=0.5"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        def scrub(text: str) -> str:
            # A key is never shorter than this; a one-letter "key" replaced
            # everywhere would scramble the answer instead of hiding anything.
            return text.replace(token, "[hidden]") if token and len(token) >= 6 else text

        import time

        import httpx

        from ..core import http as _http

        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None and verb != "GET":
            kwargs["json"] = body
        started = time.monotonic()
        try:
            timeout = httpx.Timeout(CALL_READ_S, connect=CALL_CONNECT_S)
            with _http.client(timeout=timeout, follow_redirects=False) as client:
                with client.stream(verb, url, **kwargs) as resp:
                    out["status"] = resp.status_code
                    out["reason"] = str(resp.reason_phrase or "")
                    out["content_type"] = str(resp.headers.get("content-type", ""))[:120]
                    raw = bytearray()
                    for chunk in resp.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > CALL_MAX_BYTES:
                            del raw[CALL_MAX_BYTES:]
                            out["truncated"] = True
                            break
                        if time.monotonic() - started > CALL_TOTAL_S:
                            out["truncated"] = True
                            break
                    out["text"] = scrub(bytes(raw).decode(resp.encoding or "utf-8", errors="replace"))
        except httpx.TimeoutException:
            out["error"] = f"it did not answer within {int(CALL_READ_S)} seconds"
            return out
        except httpx.HTTPError as exc:
            out["error"] = scrub(f"could not reach it ({type(exc).__name__})")
            return out
        except Exception as exc:  # noqa: BLE001 — a call never crashes the turn
            out["error"] = scrub(f"the call failed ({type(exc).__name__})")
            return out
        status = int(out["status"] or 0)
        if 300 <= status < 400:
            out["error"] = (
                f"it answered with a redirect ({status}) to another address, which "
                "is not followed, so the key goes nowhere else"
            )
            return out
        out["ok"] = 200 <= status < 300
        return out

    def capabilities(self) -> list[str]:
        # v1.357.0 (G84): ``rest.call`` — :meth:`call`, the rest_call tool.
        return ["rest.get", "rest.call"]


# --- one call to a hookup (v1.357.0, G84) ------------------------------------

#: The methods :meth:`RestApiIntegration.call` sends. GET reads; every other
#: one changes something on the user's service, and the rest_call tool asks
#: before each of those (integrations/tools.py).
CALL_METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE")
#: Seconds to connect / to wait between bytes, and for the whole answer.
CALL_CONNECT_S = 5.0
CALL_READ_S = 20.0
CALL_TOTAL_S = 30.0
#: The most of an answer that is read (bytes); the tool shows far less.
CALL_MAX_BYTES = 2_000_000


class PathRefused(ValueError):
    """A ``path`` that is not a path under the hookup's own address."""


def build_call_url(base_url: str, path: str) -> str:
    """The hookup's ``base_url`` with *path* under it — never another address.

    *path* is a PATH (``/contacts/42``, ``contacts?email=a@b.c``): a web
    address (``://``), a protocol-relative ``//host``, a backslash, a space or
    control character, and a ``.`` / ``..`` segment (plain or percent-encoded)
    are refused with one sentence. The two parts are joined by hand (never
    ``urljoin``, which lets a path replace the host), a query is added to the
    base's own, a fragment is dropped, and the result is checked to keep the
    base's scheme, host and path prefix. Raises :class:`PathRefused`."""
    from urllib.parse import unquote, urlsplit, urlunsplit

    raw = str(path or "").strip()
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise PathRefused("the path has a space or a control character in it (percent-encode it)")
    if "\\" in raw:
        raise PathRefused("the path has a backslash in it")
    if "://" in raw or raw.startswith("//"):
        raise PathRefused(
            "give a path on the service (like /contacts/42), not a web address; "
            "its own saved address is always used"
        )
    raw = raw.split("#", 1)[0]
    p, _, q = raw.partition("?")
    if any(seg in (".", "..") for seg in unquote(unquote(p)).split("/")):
        raise PathRefused("the path cannot step outside the service's address ('..')")
    base = urlsplit(str(base_url or "").strip())
    if base.scheme.lower() not in ("http", "https") or not base.netloc:
        raise PathRefused("the service's saved address is not a web address")
    base_path = base.path.rstrip("/")
    tail = p.lstrip("/")
    new_path = f"{base_path}/{tail}" if tail else (base.path or "/")
    query = "&".join(x for x in (base.query, q) if x)
    url = urlunsplit((base.scheme, base.netloc, new_path, query, ""))
    got = urlsplit(url)
    if got.netloc != base.netloc or got.scheme != base.scheme or not got.path.startswith(base_path):
        raise PathRefused("the path would leave the service's address")
    return url


# --- registration ------------------------------------------------------------

def register_builtins(registry: IntegrationRegistry) -> None:
    """Register the self-contained example integrations."""
    registry.register(
        MOCK_SPEC,
        lambda config, resolver: MockIntegration(config, resolver),
    )
    registry.register(
        REST_SPEC,
        lambda config, resolver: RestApiIntegration(config, resolver),
    )
