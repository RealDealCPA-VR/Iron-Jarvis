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
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

_CACHE_TTL = 600.0  # 10 min — model lists move slowly; pickers load often
#: A fetch that learned NOTHING (failed, no key, an empty listing) is asked
#: again after this (v1.332.0). It used to be remembered for the full 10
#: minutes, so a server started a moment after the first picker load stayed
#: invisible for ten minutes.
_NEGATIVE_TTL = 60.0
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_lock = threading.Lock()
#: Keys with a background refresh in flight (one refresh per key at a time).
_refreshing: set[str] = set()
#: Bumped by :func:`clear` and by a background refresh that CHANGED a list,
#: so the model catalog (``routes.connections``) knows its rows are older
#: than what discovery now holds.
_generation = 0


def generation() -> int:
    """A counter that moves whenever cached lists were dropped or changed."""
    return _generation


#: A listing gets 4 s to answer but only 1.5 s to CONNECT (v1.332.0). A dead
#: local port takes the whole budget to refuse on Windows (measured 4.19 s),
#: and a fleet node is now asked on the first catalog build, so the connect
#: cap keeps a dead endpoint from costing more than the old Ollama probe did.
_TIMEOUT = httpx.Timeout(4.0, connect=1.5)


def _get_json(url: str, headers: dict[str, str]) -> Any:
    from ..core import http as _http

    resp = _http.get(url, headers=headers, timeout=_TIMEOUT)
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


def _openai_compatible_models(base_url: str, key: str = "") -> list[str]:
    """``GET <base>/v1/models`` on ANY OpenAI-compatible server (LM Studio,
    vLLM, LocalAI, llama.cpp, Ollama's /v1 shim, private gateways). Accepts a
    bare host, a ``/v1`` base, or a full chat URL — same normalization as the
    provider manager."""
    u = (base_url or "").strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")].rstrip("/")
    if not u.endswith("/v1"):
        u += "/v1"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = _get_json(f"{u}/models", headers)
    return [str(m.get("id")) for m in data.get("data", []) if m.get("id")]


def _status_of(exc: BaseException) -> int:
    """The HTTP status an ``httpx.HTTPStatusError`` carries, else 0."""
    resp = getattr(exc, "response", None)
    try:
        return int(getattr(resp, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def ollama_fallback_allowed(exc: BaseException, ollama_ok: bool | None) -> bool:
    """May a listing that failed with *exc* be retried at Ollama's native
    ``/api/tags`` (the gateway-key fix)?

    * ``ollama_ok`` True (the Ollama settings slot, a node detected as Ollama)
      → yes;
    * False (a server known to be LiteLLM / vLLM / OpenAI-compatible) → never:
      a LiteLLM gateway 404s every ``/api/*`` address;
    * None (nothing known) → only when the server ANSWERED 404 — an Ollama old
      enough to predate its ``/v1`` shim. Never after an auth refusal
      (401/403: Ollama has no keys, so that server is not Ollama) and never
      after a transport failure (a dead host is asked once, not twice)."""
    if ollama_ok is True:
        return True
    if ollama_ok is False:
        return False
    return _status_of(exc) == 404


def _ollama_host(base_url: str) -> str:
    host = (base_url or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1"):
        if host.endswith(suffix):
            host = host[: -len(suffix)].rstrip("/")
            break
    return host


def list_endpoint_models(base_url: str, key: str = "", *, ollama_ok: bool | None = None) -> list[str]:
    """UNCACHED model listing for a user-entered endpoint (the setup form's
    probe — the user shouldn't have to know their server's model ids by
    heart). Tries the OpenAI-compatible ``/v1/models`` first, then Ollama's
    native ``/api/tags``; raises the original error when both fail so the
    caller can surface it honestly. The gateway-key fix: never Ollama's
    address for a server known not to be Ollama (``ollama_ok`` False), nor
    after an auth refusal (401/403 — Ollama has no keys)."""
    if not (base_url or "").strip():
        raise ValueError("base_url is required")
    try:
        return _openai_compatible_models(base_url, key)
    except Exception as exc:
        if ollama_ok is False or _status_of(exc) in (401, 403):
            raise
        # Bare Ollama hosts may predate the /v1 shim — try the native API.
        return _ollama_models(_ollama_host(base_url))


_ANTHROPIC_VERSION = "2023-06-01"
#: Rows per page and the most pages followed for an Anthropic-compatible
#: listing (the same 1,000-id ceiling the Connections form's probe keeps).
_ANTHROPIC_PAGE_LIMIT = 100
_ANTHROPIC_MAX_PAGES = 10


def _anthropic_compatible_models(base_url: str, key: str = "") -> list[str]:
    """``GET <base>/v1/models`` the Anthropic way (v1.332.0): ``x-api-key`` +
    ``anthropic-version``, paged by ``after_id`` while ``has_more``. A pasted
    messages or models URL is cut back to its base first (the same rule as
    the Connections form's probe, ``routes.connections._anthropic_models_url``)."""
    u = (base_url or "").strip().rstrip("/")
    for suffix in ("/messages", "/models"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    url = f"{u}/models" if u.endswith("/v1") else f"{u}/v1/models"
    headers = {"anthropic-version": _ANTHROPIC_VERSION}
    if key:
        headers["x-api-key"] = key
    ids: list[str] = []
    after = ""
    for _ in range(_ANTHROPIC_MAX_PAGES):
        query = f"?limit={_ANTHROPIC_PAGE_LIMIT}"
        if after:
            query += "&after_id=" + quote(after, safe="")
        data = _get_json(url + query, headers)
        rows = data.get("data") if isinstance(data, dict) else None
        page = [str(m.get("id")) for m in rows or [] if isinstance(m, dict) and m.get("id")]
        for mid in page:
            if mid not in ids:
                ids.append(mid)
        if not page or data.get("has_more") is not True:
            break
        nxt = str(data.get("last_id") or page[-1])
        if nxt == after:
            break
        after = nxt
    return ids


def _fleet_endpoint_models(
    base_url: str, key: str, protocol: str, ollama_ok: bool | None = None
) -> list[str]:
    """One fleet endpoint's live model ids, asked the way it chats (v1.332.0):
    ``anthropic`` → the Anthropic Models API; anything else → the
    OpenAI-compatible ``/v1/models``, then Ollama's native ``/api/tags`` only
    when :func:`ollama_fallback_allowed` says so (the gateway-key fix: it used
    to follow ANY error status — a keyed LiteLLM gateway's 401 included — so
    the gateway was asked Ollama's address it 404s, on every catalog refresh)."""
    if protocol == "anthropic":
        return _anthropic_compatible_models(base_url, key)
    try:
        return _openai_compatible_models(base_url, key)
    except Exception as exc:
        import httpx

        if not isinstance(exc, httpx.HTTPStatusError):
            raise
        if not ollama_fallback_allowed(exc, ollama_ok):
            raise
        return _ollama_models(_ollama_host(base_url))


def _fetch(
    provider: str,
    credential: Callable[[], str | None],
    base_url: str,
    protocol: str,
    ollama_ok: bool | None = None,
) -> list[str]:
    """Ask the provider NOW. Never raises: a failure is ``[]`` (nothing learned)."""
    ids: list[str] = []
    try:
        if provider == "ollama" and base_url:
            ids = _ollama_models(base_url)
        elif provider == "custom" and base_url:
            ids = _openai_compatible_models(base_url, credential() or "")
        elif provider.startswith("fleet-") and base_url:
            # v1.332.0: a fleet endpoint ("fleet-<id>") is asked for its real
            # list. Before this every one fell through to the key branch below,
            # matched no provider, and listed only its default model.
            ids = _fleet_endpoint_models(base_url, credential() or "", protocol, ollama_ok)
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
    return ids


def _store(cache_key: str, ids: list[str], *, at: float) -> bool:
    """Remember ``ids`` for ``cache_key``; True when that changed the list."""
    rows = [{"model": i} for i in ids]
    with _lock:
        old = _cache.get(cache_key)
        _cache[cache_key] = (at, rows)
    return old is None or old[1] != rows


def _refresh_in_background(
    cache_key: str,
    provider: str,
    credential: Callable[[], str | None],
    base_url: str,
    protocol: str,
    ollama_ok: bool | None = None,
) -> None:
    """Start ONE refresh thread for ``cache_key`` unless one is running."""
    with _lock:
        if cache_key in _refreshing:
            return
        _refreshing.add(cache_key)

    def _run() -> None:
        global _generation
        try:
            ids = _fetch(provider, credential, base_url, protocol, ollama_ok)
            if _store(cache_key, ids, at=time.monotonic()):
                with _lock:
                    _generation += 1
        except Exception:  # noqa: BLE001 — a refresh never breaks anything
            log.debug("background model discovery failed", exc_info=True)
        finally:
            with _lock:
                _refreshing.discard(cache_key)

    try:
        threading.Thread(target=_run, name=f"discover-{provider}", daemon=True).start()
    except Exception:  # noqa: BLE001 — no thread: the next caller tries again
        with _lock:
            _refreshing.discard(cache_key)


def discover_models(
    provider: str,
    credential: Callable[[], str | None],
    *,
    base_url: str = "",
    protocol: str = "openai",
    ollama_ok: bool | None = None,
) -> list[str]:
    """Live model ids for one provider (cached). Empty list = nothing learned
    (caller keeps the curated entries).

    v1.332.0: a list older than its TTL (10 min when something was learned,
    :data:`_NEGATIVE_TTL` when nothing was) is returned AT ONCE and refreshed
    behind it, one refresh per key, so a picker never waits on a provider it
    already has an answer from; only a key never asked (or :func:`clear`-ed)
    waits for the fetch. ``protocol`` is a fleet endpoint's
    (``FleetNode.protocol``); ``ollama_ok`` (the gateway-key fix) is the
    registry's ``ollama_style_ok`` for its address — whether a failed listing
    may be retried at Ollama's ``/api/tags``."""
    now = time.monotonic()
    # Key by provider AND endpoint URL: a re-pointed local/custom endpoint is a
    # DIFFERENT server, so its cache entry must miss — with a provider-only key
    # the old endpoint's model list kept serving for the full TTL (live-hit
    # 2026-07-18: a freshly saved endpoint showed the previous endpoint's models).
    cache_key = f"{provider}|{base_url}"
    if protocol and protocol != "openai":
        cache_key += f"|{protocol}"
    with _lock:
        hit = _cache.get(cache_key)
    if hit is not None:
        ids = [m["model"] for m in hit[1]]
        ttl = _CACHE_TTL if ids else _NEGATIVE_TTL
        if now - hit[0] >= ttl:
            _refresh_in_background(cache_key, provider, credential, base_url, protocol, ollama_ok)
        return ids
    ids = _fetch(provider, credential, base_url, protocol, ollama_ok)
    _store(cache_key, ids, at=now)
    return ids


def clear(provider: str = "") -> None:
    """Forget cached lists, so the next picker load asks again (Re-detect, a
    connection or an endpoint changed): every list, or only ``provider``'s
    (any URL). The catalog sees the generation move."""
    global _generation
    with _lock:
        if provider:
            for key in [k for k in _cache if k.split("|", 1)[0] == provider]:
                del _cache[key]
        else:
            _cache.clear()
        _generation += 1


def clear_cache() -> None:  # test hook (the app calls clear())
    clear()
