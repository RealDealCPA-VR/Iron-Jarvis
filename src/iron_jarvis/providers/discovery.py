"""LIVE model discovery — the picker shows what providers actually serve.

The curated catalog (agents/dynamic.KNOWN_MODELS) is the offline baseline, but
it goes stale (new models missing, retired ids lingering). This module queries
each CONNECTED provider's real model list — Anthropic ``/v1/models``, OpenAI
``/v1/models`` (API-key accounts), Ollama ``/api/tags`` — merges it with the
curated set, and caches the result briefly. Failures degrade to the curated
list; discovery must never break the picker.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

_CACHE_TTL = 600.0  # 10 min — model lists move slowly; pickers load often
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _get_json(url: str, headers: dict[str, str]) -> Any:
    import httpx

    resp = httpx.get(url, headers=headers, timeout=4)
    resp.raise_for_status()
    return resp.json()


def _anthropic_models(key: str) -> list[str]:
    data = _get_json(
        "https://api.anthropic.com/v1/models?limit=100",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    return [str(m.get("id")) for m in data.get("data", []) if m.get("id")]


def _openai_models(key: str) -> list[str]:
    if not key.startswith("sk-"):
        return []  # ChatGPT-account JWTs can't list api.openai.com models
    data = _get_json(
        "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}
    )
    ids = [str(m.get("id")) for m in data.get("data", []) if m.get("id")]
    # Keep CHAT-capable families; drop embeddings/audio/image/moderation noise.
    keep = ("gpt-4", "gpt-5", "o1", "o3", "o4", "chatgpt")
    drop = ("embedding", "audio", "tts", "whisper", "image", "dall-e", "moderation",
            "realtime", "transcribe", "search")
    return [
        i for i in ids
        if i.startswith(keep) and not any(d in i for d in drop)
    ]


#: OpenRouter serves 300+ models — surface only the families the user actually
#: wants in pickers (plus the auto router). Extend as tastes change.
_OPENROUTER_KEEP = ("glm", "minimax", "deepseek", "auto")


def _openrouter_models(key: str) -> list[str]:
    data = _get_json(
        "https://openrouter.ai/api/v1/models", {"Authorization": f"Bearer {key}"}
    )
    ids = [str(m.get("id")) for m in data.get("data", []) if m.get("id")]
    kept = [i for i in ids if any(k in i.lower() for k in _OPENROUTER_KEEP)]
    if "openrouter/auto" not in kept:
        kept.insert(0, "openrouter/auto")
    return kept[:25]


def _ollama_models(base_url: str) -> list[str]:
    data = _get_json(f"{base_url.rstrip('/')}/api/tags", {})
    return [str(m.get("name")) for m in data.get("models", []) if m.get("name")]


def discover_models(
    provider: str, credential: Callable[[], str | None], *, base_url: str = ""
) -> list[str]:
    """Live model ids for one provider (cached). Empty list = nothing learned
    (caller keeps the curated entries)."""
    now = time.monotonic()
    hit = _cache.get(provider)
    if hit and now - hit[0] < _CACHE_TTL:
        return [m["model"] for m in hit[1]]
    ids: list[str] = []
    try:
        if provider == "ollama" and base_url:
            ids = _ollama_models(base_url)
        else:
            key = credential() or ""
            if not key:
                ids = []
            elif provider == "anthropic":
                ids = _anthropic_models(key)
            elif provider == "openai":
                ids = _openai_models(key)
            elif provider == "openrouter":
                ids = _openrouter_models(key)
    except Exception as exc:  # noqa: BLE001 — degrade to curated, never break
        log.debug("model discovery failed for %s: %s", provider, exc)
        ids = []
    _cache[provider] = (now, [{"model": i} for i in ids])
    return ids


def clear_cache() -> None:  # test hook
    _cache.clear()
