"""The ONE way to get request headers for a model endpoint the user saved
(the gateway-key fix).

The user's LiteLLM gateway has required a key since 2026-10-21. Chat sent it
(the adapters read the saved credential), but every background read of the
same server — the fleet sampler's pass, kind detection, the capability seed,
the OpenCode passthrough check, the router's liveness probe — went out with no
``Authorization`` header and was answered ``401 No api key passed in``, tens of
thousands of times. Each of those callers now asks THIS module for its headers,
so they all send the very key the chat calls send:

* :func:`node_key` is the key lookup: a node's ``api_key_name`` read from the
  vault through the resolver the platform hands the fleet registry
  (``secrets.get``). The custom slot's node carries ``custom_api_key`` — the
  same vault name the Connections page saves the key under
  (``ConnectionSpec.key_secret_name``) and the ``custom`` chat adapter reads.
  A topology child (a backend a proxy reported) never gets a key: its address
  is ANOTHER server, and the proxy's key is not its key.
* :func:`auth_headers` is the one spelling: ``Authorization: Bearer`` for an
  OpenAI-compatible node, ``x-api-key`` + ``anthropic-version`` for an
  Anthropic-compatible one (v1.329.0).
* :func:`root_key` is how a bare URL (OpenCode's provider address, a typed
  form address, the envelope seed) is matched to the saved endpoint it belongs
  to — scheme, host, port and path above ``/v1`` — so a key only ever goes to
  the server it was saved for.

The key is never logged, never put in an error, never returned by a route.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlsplit

from .probes import normalize_root

#: The Anthropic Messages API version header (the one value it documents; the
#: chat adapter, the probes and the "Fetch available models" form send it).
ANTHROPIC_VERSION = "2023-06-01"

#: ``name -> value | None``: the vault read (``secrets.get``).
SecretResolver = Callable[[str], Any]


def auth_headers(key: str, protocol: str = "openai") -> dict[str, str]:
    """The request headers that carry *key* to an endpoint speaking
    *protocol*. ``{}`` for no key on an OpenAI-compatible endpoint (a keyless
    local server is the common case); an Anthropic-compatible endpoint always
    gets its version header."""
    key = (key or "").strip()
    if protocol == "anthropic":
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if key:
            headers["x-api-key"] = key
        return headers
    return {"Authorization": f"Bearer {key}"} if key else {}


def node_key(node: Any, resolver: SecretResolver | None) -> str:
    """The saved key for a configured endpoint node, or ``""``.

    Reads ``node.api_key_name`` from the vault. A topology child (it has a
    ``parent_id``) answers ``""`` — see the module docstring. Never raises: a
    vault that cannot answer is "no key", never a crash."""
    if node is None or getattr(node, "parent_id", ""):
        return ""
    name = str(getattr(node, "api_key_name", "") or "").strip()
    if not name or resolver is None:
        return ""
    try:
        return str(resolver(name) or "").strip()
    except Exception:  # noqa: BLE001 — a vault fault is "no key"
        return ""


def node_headers(node: Any, resolver: SecretResolver | None) -> dict[str, str]:
    """:func:`auth_headers` for a node's own key and protocol."""
    protocol = str(getattr(node, "protocol", "openai") or "openai")
    return auth_headers(node_key(node, resolver), protocol)


def root_key(url: str) -> str:
    """A comparable root for an endpoint address: ``scheme://host:port/path``
    with the path cut above ``/v1`` (and ``/chat/completions``), the scheme and
    host lower-cased and the default port filled in. ``""`` when *url* names
    no host. Two addresses of one server (``…:4000/v1`` and ``…:4000``) give
    the same root; a different port or host never does."""
    root = normalize_root(url or "")
    if not root:
        return ""
    try:
        parts = urlsplit(root if "//" in root else f"http://{root}")
        scheme = (parts.scheme or "http").lower()
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        return ""
    if not host:
        return ""
    return f"{scheme}://{host}:{port}{parts.path.rstrip('/')}"


__all__ = [
    "ANTHROPIC_VERSION",
    "SecretResolver",
    "auth_headers",
    "node_headers",
    "node_key",
    "root_key",
]
