"""Built-in example integrations.

Self-contained, dependency-light integrations that demonstrate the framework
and serve as offline test fixtures:

* :class:`MockIntegration` — always-healthy, no external calls.
* :class:`RestApiIntegration` — health-checks a REST endpoint via an *injected*
  ``http_get`` callable (defaulting to a thin ``httpx`` wrapper). Tests inject a
  fake so no real network is touched.

``register_builtins(registry)`` wires both into an :class:`IntegrationRegistry`.
"""

from __future__ import annotations

from collections.abc import Callable

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
        return {"ok": True, "detail": "mock integration is always healthy"}

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

    def capabilities(self) -> list[str]:
        return ["rest.get"]


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
