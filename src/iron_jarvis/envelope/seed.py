"""Instant-on seed: a usable CapabilityProfile in ~1s from endpoint introspection.

Ported from IronCore ``envelope/seed.py``. An unprobed local model would run
on the conservative floor while the battery measures it; two cheap signals
already exist the moment an endpoint is configured — Ollama's ``/api/show``
(real context window, capabilities incl. vision and tools) and the generic
OpenAI-compatible ``/v1/models`` listing — so :func:`seed_profile` assembles a
*provisional but usable* profile from them.

The seed is deliberately optimistic where the endpoint gives a signal: the
battery corrects it within seconds, and the loop's retries absorb an
over-optimistic seed. It is NEVER cached — only measured profiles are saved
(``probed_at=None`` here keeps the model "unprobed", so the battery still
runs), and the store's merge refuses to restore out of a ``seeded`` record.

Resilience: every call is best-effort with a bounded timeout;
``seed_profile`` NEVER raises and returns ``None`` when nothing answered —
the caller keeps the floor default in that case, honestly labeled.
"""

from __future__ import annotations

from typing import Any

import httpx

from iron_jarvis.envelope.profile import CapabilityProfile

#: cap an unmeasured advertised window: never seed an honest_context beyond
#: what a battery has confirmed the server will actually hold coherently.
_UNMEASURED_HONEST_CAP = 32768

#: whole-seed time budget — introspection is boot-path work and a hung
#: endpoint must cost ~nothing.
_TIMEOUT = httpx.Timeout(4.0)


def _root(base_url: str) -> str:
    """Host root for an endpoint URL: accepts a bare host, a ``/v1`` base, or
    a full ``/v1/chat/completions`` URL (same stripping ladder as
    ``fleet/probes.normalize_root``, kept local so this package stays
    dependency-light)."""
    url = (base_url or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")].rstrip("/")
    return url


def _context_length(model_info: dict[str, Any]) -> int | None:
    """Ollama reports the window under an architecture-prefixed key
    (``llama.context_length``, ``qwen3.context_length``, ...) — match the
    suffix rather than hardcoding a family."""
    for key, value in model_info.items():
        if str(key).endswith(".context_length"):
            try:
                length = int(value)
            except (TypeError, ValueError):
                continue
            if length > 0:
                return length
    return None


async def _seed_from_ollama(
    client: httpx.AsyncClient, root: str, model_id: str, profile: CapabilityProfile
) -> bool:
    """Fill ``profile`` from ``POST /api/show``. True when the endpoint
    answered as an Ollama server (whatever fields it carried)."""
    resp = await client.post(f"{root}/api/show", json={"model": model_id})
    if resp.status_code < 200 or resp.status_code >= 300:
        return False
    body = resp.json()
    if not isinstance(body, dict):
        return False
    window = _context_length(body.get("model_info") or {})
    if window:
        profile.context_window = window
        profile.honest_context = min(window, _UNMEASURED_HONEST_CAP)
    capabilities = body.get("capabilities") or []
    if isinstance(capabilities, list):
        # a server that omits the array honestly keeps the floor defaults
        profile.vision = "vision" in capabilities
        if "tools" in capabilities:
            # a usable seed: clears the native bar so the ladder does not
            # park a tool-capable model on "none" while the battery runs.
            profile.tool_protocols = {"native": 0.95}
    return True


async def _seed_from_litellm(
    client: httpx.AsyncClient,
    root: str,
    model_id: str,
    profile: CapabilityProfile,
    headers: dict[str, str],
) -> str:
    """Fill ``profile`` from a LiteLLM proxy's OWN answer, ``GET /model/info``
    (the gateway-key fix): ``supports_vision``, ``supports_function_calling``
    and ``max_input_tokens`` for the entry whose ``model_name`` is
    *model_id* (wildcards the way ``fleet/upstreams`` treats them). A field
    the proxy does not state stays the floor default — never guessed.

    Answers ``"litellm"`` (it is a proxy; the profile took what it said),
    ``"refused"`` (401/403: a keyed server — so NOT Ollama, which has no
    keys), or ``"no"`` (not a proxy, or no answer)."""
    from ..fleet.upstreams import parse_model_info

    resp = await client.get(f"{root}/model/info", headers=headers)
    if resp.status_code in (401, 403):
        return "refused"
    if resp.status_code < 200 or resp.status_code >= 300:
        return "no"
    table = parse_model_info(resp.json())
    if table is None:
        return "no"
    caps = table.caps(model_id)
    window = caps.get("context_window")
    if window:
        profile.context_window = int(window)
        profile.honest_context = min(int(window), _UNMEASURED_HONEST_CAP)
    if caps.get("vision") is not None:
        profile.vision = bool(caps["vision"])
    if caps.get("tool_use") is True:
        # the same usable seed Ollama's "tools" capability gives
        profile.tool_protocols = {"native": 0.95}
    return "litellm"


async def _seed_from_openai_compat(
    client: httpx.AsyncClient,
    root: str,
    model_id: str,
    profile: CapabilityProfile,
    headers: dict[str, str] | None = None,
) -> bool:
    """Fallback introspection: ``GET /v1/models`` presence only. A generic
    OpenAI-compatible server owes us nothing more; ``max_model_len`` is read
    when the listing happens to carry it (vLLM does)."""
    resp = await client.get(f"{root}/v1/models", headers=headers or {})
    if resp.status_code < 200 or resp.status_code >= 300:
        return False
    body = resp.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return False
    for entry in data:
        if not isinstance(entry, dict) or str(entry.get("id") or "") != model_id:
            continue
        try:
            window = int(entry.get("max_model_len") or 0)
        except (TypeError, ValueError):
            window = 0
        if window > 0:
            profile.context_window = window
            profile.honest_context = min(window, _UNMEASURED_HONEST_CAP)
    return True


async def seed_profile(
    provider: str,
    model_id: str,
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    headers: dict[str, str] | None = None,
    ollama: bool | None = None,
) -> CapabilityProfile | None:
    """Introspect ``base_url`` into a provisional ``source="seeded"`` profile,
    or ``None`` when the endpoint answered no introspection call.

    Pure of side effects (nothing is saved — the seed is provisional by
    contract) and NEVER raises. ``client`` is injectable so the offline suite
    seeds against ``httpx.MockTransport``; the caller keeps ownership of an
    injected client, while the default one is closed here.

    THE GATEWAY-KEY FIX: every request carries *headers* (the endpoint's
    saved key, ``fleet/endpoint_auth``), and the ORDER follows what the
    endpoint is. *ollama* is ``True`` for the Ollama slot / a node detected
    as Ollama (Ollama's ``/api/show`` first, as before), ``False`` for a
    server known not to be Ollama (never asked ``/api/show``), ``None`` when
    unknown. A non-Ollama endpoint is asked LiteLLM's own ``/model/info``
    first — the proxy states vision, tools and the window per model, which
    the Ollama-way read never found (the user's gateway serves a ``vision``
    model that read as blind). An auth refusal there means a keyed server
    whose key we lack: nothing more is asked (every other door would refuse
    too) and the seed answers ``None`` — nothing is guessed.
    """
    root = _root(base_url)
    if not root:
        return None
    profile = CapabilityProfile(model_id=model_id, provider=provider, source="seeded")
    own_client = client is None
    hdrs = dict(headers or {})
    from ..core import http as _http  # v1.332.0: the shared TLS context

    active = client or _http.async_client(timeout=_TIMEOUT)
    try:
        if ollama is not True:
            try:
                said = await _seed_from_litellm(active, root, model_id, profile, hdrs)
            except Exception:  # noqa: BLE001 — best-effort introspection, try the next door
                said = "no"
            if said == "litellm":
                return profile
            if said == "refused":
                # A keyed server that refused the key we have (or that we
                # have none for): every other door would be refused too, so
                # nothing more is asked and nothing is guessed.
                return None
        if ollama is not False:
            try:
                if await _seed_from_ollama(active, root, model_id, profile):
                    return profile
            except Exception:  # noqa: BLE001 — best-effort introspection, try the next door
                pass
        try:
            if await _seed_from_openai_compat(active, root, model_id, profile, hdrs):
                return profile
        except Exception:  # noqa: BLE001
            pass
        return None
    finally:
        if own_client:
            try:
                await active.aclose()
            except Exception:  # noqa: BLE001 — closing must not turn a seed into a raise
                pass
