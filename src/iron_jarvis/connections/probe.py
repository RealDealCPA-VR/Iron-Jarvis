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

import re as _re

#: provider -> (url, auth_style). auth_style is one of:
#:   "bearer"  -> Authorization: Bearer <cred>
#:   "anthropic" -> x-api-key + anthropic-version (API keys only; the
#:                  subscription is inherited via the Claude CLI, never probed here)
#:   "google_tokeninfo" -> ?access_token=<cred> (validates an OAuth token)
_PROBES: dict[str, tuple[str, str]] = {
    "anthropic": ("https://api.anthropic.com/v1/models", "anthropic"),
    "openai": ("https://api.openai.com/v1/models", "bearer"),
    "xai": ("https://api.x.ai/v1/models", "bearer"),
    "openrouter": ("https://openrouter.ai/api/v1/models", "bearer"),
    "google": ("https://www.googleapis.com/oauth2/v3/tokeninfo", "google_tokeninfo"),
    # Pixio (creative media) — a cheap authenticated model list verifies the key.
    "pixio": ("https://beta.pixio.myapps.ai/api/v1/models", "bearer"),
    # "custom" has no fixed URL — falls to the no-probe path (connected, unverified).
}

_TIMEOUT_SECONDS = 12.0

#: v1.420.0 (C6.6): the KINDS a probe answer can be, so the route words it
#: (``daemon/routes/connections.probe_words``) instead of printing the raw
#: text. ``ok`` the credential was accepted; ``unchecked`` connected but no
#: live check exists for it; ``account`` an account sign-in (ChatGPT) that
#: a model-list probe cannot check; ``unreachable`` the provider could not be
#: reached; ``refused_key`` it answered 401/403; ``http`` it answered another
#: error status (``status``), which says nothing about the key.
PROBE_KINDS = ("ok", "unchecked", "account", "unreachable", "refused_key", "http")


class ProbeResult(tuple):
    """``(ok, detail)`` — unpacks and compares exactly like the tuple every
    caller already reads — plus the TYPED facts: ``kind`` (one of
    :data:`PROBE_KINDS`), ``status`` (the HTTP status for ``http`` /
    ``refused_key``) and ``provider``. ``detail`` stays the probe's own
    text: it is the technical line, never the headline."""

    def __new__(
        cls, ok: bool, detail: str, *, kind: str = "", status: "int | None" = None, provider: str = ""
    ) -> "ProbeResult":
        self = super().__new__(cls, (bool(ok), str(detail)))
        self.kind = kind if kind in PROBE_KINDS else ""
        self.status = status
        self.provider = provider
        return self


def kind_of(provider: str, ok: bool, detail: str) -> ProbeResult:
    """The typed facts of a probe answer that arrived as a plain
    ``(ok, detail)`` pair (an older or injected prober, the Google key probe):
    read off THIS module's own sentences only — never a provider's text — so
    it can never guess. Unknown text gives ``kind`` "". Never raises."""
    try:
        text = str(detail or "")
        low = text.lower()

        kind, status = "", None
        m = _re.search(r"probe returned http (\d{3})", low)
        if m:
            kind, status = "http", int(m.group(1))
        elif "rejected the credential" in low or "rejected the key" in low:
            kind = "refused_key"
            m = _re.search(r"\((\d{3})\)", text)
            status = int(m.group(1)) if m else 401
        elif low.startswith("could not reach ") or "probe failed" in low:
            kind = "unreachable"
        elif "via chatgpt account" in low:
            kind = "account"
        elif "no live probe available" in low:
            kind = "unchecked"
        elif ok and ("credential accepted" in low or "credential is valid" in low):
            kind = "ok"
        return ProbeResult(ok, text, kind=kind, status=status, provider=provider)
    except Exception:  # noqa: BLE001 — facts are never a new failure
        return ProbeResult(ok, str(detail or ""), provider=provider)


def _without_key(text: str, credential: str) -> str:
    """*text* with the credential the probe SENT taken out (``[key hidden]``,
    the marker ``providers.adapters.base.scrub_sent_credentials`` uses) — a
    provider's error body or a transport error can carry it back. Never
    raises."""
    try:
        cred = str(credential or "").strip()
        if len(cred) < 4:
            return text
        return str(text or "").replace(cred, "[key hidden]")
    except Exception:  # noqa: BLE001 — a scrub is never a new failure
        return text


def live_probe(provider: str, credential: str) -> tuple[bool, str]:
    """Probe a provider with a real (cheap) request. Returns ``(ok, detail)``.

    Unknown providers (no probe defined) return ``(True, ...)`` — we can't
    cheaply verify them, so we don't claim failure. Network errors return
    ``(False, ...)`` with an actionable message.
    """
    spec = _PROBES.get(provider)
    if spec is None:
        return ProbeResult(
            True,
            f"{provider} is connected (no live probe available)",
            kind="unchecked",
            provider=provider,
        )
    # A ChatGPT-account OAuth token (a JWT, not an sk- key) is NOT accepted by
    # api.openai.com — inference routes through the ChatGPT/Codex backend
    # instead (see the OpenAI adapter), so probing /v1/models would report a
    # FALSE failure for a healthy connection.
    if provider == "openai" and not credential.startswith("sk-") and credential.count(".") == 2:
        return ProbeResult(
            True,
            "openai connected via ChatGPT account (Codex backend) — "
            "run a session to verify inference",
            kind="account",
            provider=provider,
        )
    # v1.355.0 (G46): a Google API key (an AI Studio key: "AQ." or "AIza")
    # cannot be checked at tokeninfo -- that answers 400 for every key, and
    # it would put the key in a URL. Its Test lists one model with the key in
    # a header (providers/google_key, the rule the adapter's header uses). A
    # login token keeps the tokeninfo check below, byte-identical.
    if provider == "google":
        from ..providers.google_key import is_google_api_key, probe_google_key

        if is_google_api_key(credential):
            ok, detail = probe_google_key(credential)
            return kind_of(provider, ok, _without_key(detail, credential))
    url, style = spec
    import httpx  # lazy: only when a real probe runs

    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if style == "anthropic":
        # API-key-only: the Anthropic card no longer accepts an account OAuth
        # token (subscription use is inherited from the `claude` CLI, not probed).
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
        return ProbeResult(
            False,
            _without_key(f"could not reach {provider}: {type(exc).__name__}: {exc}", credential),
            kind="unreachable",
            provider=provider,
        )

    if resp.status_code == 200:
        return ProbeResult(
            True, f"{provider} reachable — credential accepted", kind="ok", provider=provider
        )
    if resp.status_code in (401, 403):
        return ProbeResult(
            False,
            f"{provider} rejected the credential ({resp.status_code}) — "
            "the key/token is invalid, revoked, or expired; reconnect it",
            kind="refused_key",
            status=resp.status_code,
            provider=provider,
        )
    if resp.status_code == 429:
        # Auth worked; we're just rate-limited. The credential is valid.
        return ProbeResult(
            True,
            f"{provider} reachable (rate-limited, but credential is valid)",
            kind="ok",
            status=429,
            provider=provider,
        )
    # Surface the provider's own error text when present, trimmed (the
    # TECHNICAL line; v1.420.0: the route words the headline from ``kind``).
    # The review (v1.420.0): a server that ECHOES the key it was sent puts it
    # in this text, and a key of a shape ``redact.mask`` does not know would
    # reach the page; the key is taken out by VALUE (the v1.382.0 rule), and
    # BEFORE the cut, so half a key never survives it.
    detail = _without_key((resp.text or "").strip().replace("\n", " "), credential)[:200]
    return ProbeResult(
        False,
        f"{provider} probe returned HTTP {resp.status_code}: {detail}",
        kind="http",
        status=resp.status_code,
        provider=provider,
    )
