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


async def _seed_from_openai_compat(
    client: httpx.AsyncClient, root: str, model_id: str, profile: CapabilityProfile
) -> bool:
    """Fallback introspection: ``GET /v1/models`` presence only. A generic
    OpenAI-compatible server owes us nothing more; ``max_model_len`` is read
    when the listing happens to carry it (vLLM does)."""
    resp = await client.get(f"{root}/v1/models")
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
) -> CapabilityProfile | None:
    """Introspect ``base_url`` into a provisional ``source="seeded"`` profile,
    or ``None`` when the endpoint answered neither introspection call.

    Pure of side effects (nothing is saved — the seed is provisional by
    contract) and NEVER raises. ``client`` is injectable so the offline suite
    seeds against ``httpx.MockTransport``; the caller keeps ownership of an
    injected client, while the default one is closed here.
    """
    root = _root(base_url)
    if not root:
        return None
    profile = CapabilityProfile(model_id=model_id, provider=provider, source="seeded")
    own_client = client is None
    active = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        try:
            if await _seed_from_ollama(active, root, model_id, profile):
                return profile
        except Exception:  # noqa: BLE001 — best-effort introspection, try the next door
            pass
        try:
            if await _seed_from_openai_compat(active, root, model_id, profile):
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
