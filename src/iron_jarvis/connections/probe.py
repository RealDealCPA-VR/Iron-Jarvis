"""Live reachability probes for provider connections.

A real network check so the Connections "Test" button validates the *credential*
(catching a typo'd, revoked, or expired key, or an OAuth token inference will
reject) instead of merely confirming a DB row exists. Injected into
:class:`~iron_jarvis.connections.registry.ConnectionRegistry` as its ``prober``;
the offline test suite leaves it unset, so ``test()`` stays presence-only and
fully offline there.

Each probe is a cheap, read-only GET to the provider's models/tokeninfo endpoint
with a short timeout — no tokens are spent and nothing is mutated.
"""

from __future__ import annotations

#: provider -> (url, auth_style). auth_style is one of:
#:   "bearer"  -> Authorization: Bearer <cred>
#:   "anthropic" -> x-api-key (or Bearer for sk-ant-oat) + anthropic-version
#:   "google_tokeninfo" -> ?access_token=<cred> (validates an OAuth token)
_PROBES: dict[str, tuple[str, str]] = {
    "anthropic": ("https://api.anthropic.com/v1/models", "anthropic"),
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "xai": ("https://api.x.ai/v1/models", "bearer"),
    "google": ("https://www.googleapis.com/oauth2/v3/tokeninfo", "google_tokeninfo"),
}

_TIMEOUT_SECONDS = 12.0


def live_probe(provider: str, credential: str) -> tuple[bool, str]:
    """Probe a provider with a real (cheap) request. Returns ``(ok, detail)``.

    Unknown providers (no probe defined) return ``(True, ...)`` — we can't
    cheaply verify them, so we don't claim failure. Network errors return
    ``(False, ...)`` with an actionable message.
    """
    spec = _PROBES.get(provider)
    if spec is None:
        return True, f"{provider} is connected (no live probe available)"
    # A ChatGPT-account OAuth token (a JWT, not an sk- key) is NOT accepted by
    # api.openai.com — inference routes through the ChatGPT/Codex backend
    # instead (see the OpenAI adapter), so probing /v1/models would report a
    # FALSE failure for a healthy connection.
    if provider == "openai" and not credential.startswith("sk-") and credential.count(".") == 2:
        return True, (
            "openai connected via ChatGPT account (Codex backend) — "
            "run a session to verify inference"
        )
    url, style = spec
    import httpx  # lazy: only when a real probe runs

    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if style == "anthropic":
        if credential.startswith("sk-ant-oat"):
            headers["Authorization"] = f"Bearer {credential}"
            headers["anthropic-beta"] = "oauth-2025-04-20"
        else:
            headers["x-api-key"] = credential
        headers["anthropic-version"] = "2023-06-01"
    elif style == "bearer":
        headers["Authorization"] = f"Bearer {credential}"
    elif style == "google_tokeninfo":
        params["access_token"] = credential

    try:
        resp = httpx.get(
            url, headers=headers, params=params, timeout=_TIMEOUT_SECONDS
        )
    except httpx.HTTPError as exc:
        return False, f"could not reach {provider}: {type(exc).__name__}: {exc}"

    if resp.status_code == 200:
        return True, f"{provider} reachable — credential accepted"
    if resp.status_code in (401, 403):
        return False, (
            f"{provider} rejected the credential ({resp.status_code}) — "
            "the key/token is invalid, revoked, or expired; reconnect it"
        )
    if resp.status_code == 429:
        # Auth worked; we're just rate-limited. The credential is valid.
        return True, f"{provider} reachable (rate-limited, but credential is valid)"
    # Surface the provider's own error text when present, trimmed.
    detail = (resp.text or "").strip().replace("\n", " ")[:200]
    return False, f"{provider} probe returned HTTP {resp.status_code}: {detail}"
