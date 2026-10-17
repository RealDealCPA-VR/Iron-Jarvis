"""OpenAI Images as a :class:`MediaProvider` (v1.346.0, contract item 1).

Images only. NEVER free: the quote comes from a small static table of list
prices (``LIST_PRICES_USD``, OpenAI's published per-image prices read
2026-10-17 — ``note`` says "list price" so the card never claims the user's
own rate). The key is the one the chat adapters use — the registry hands in
``connections.credential("openai")`` — never a second store.

``POST /v1/images/generations`` is SYNCHRONOUS: the image comes back in the
answer (``b64_json``). So :meth:`start` runs the call (off the loop, bounded
by :data:`GENERATE_TIMEOUT_S` — a generation takes longer than a metadata
call) and keeps the bytes; :meth:`poll` answers ``succeeded`` at once with
that payload and :meth:`fetch` hands it over. No url, no CDN, no key leak.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import Any, Callable

from .base import HttpRequest, JobHandle, JobState, MediaError, MediaProvider, Quote

API_URL = "https://api.openai.com/v1/images/generations"

#: One SYNCHRONOUS generation may take a minute; the metadata bound (20 s)
#: would cut every real call short. Still bounded.
GENERATE_TIMEOUT_S = 120.0

DEFAULT_MODEL = "gpt-image-1"
MODELS: tuple[str, ...] = ("gpt-image-1", "dall-e-3")

#: (model, quality, size) -> USD per image. gpt-image-1 quality low|medium|
#: high; dall-e-3 quality standard|hd. Unknown combinations quote ``None``
#: ("price not known until it runs"), which still asks.
LIST_PRICES_USD: dict[tuple[str, str, str], float] = {
    ("gpt-image-1", "low", "1024x1024"): 0.011,
    ("gpt-image-1", "low", "1024x1536"): 0.016,
    ("gpt-image-1", "low", "1536x1024"): 0.016,
    ("gpt-image-1", "medium", "1024x1024"): 0.042,
    ("gpt-image-1", "medium", "1024x1536"): 0.063,
    ("gpt-image-1", "medium", "1536x1024"): 0.063,
    ("gpt-image-1", "high", "1024x1024"): 0.167,
    ("gpt-image-1", "high", "1024x1536"): 0.25,
    ("gpt-image-1", "high", "1536x1024"): 0.25,
    ("dall-e-3", "standard", "1024x1024"): 0.04,
    ("dall-e-3", "standard", "1024x1792"): 0.08,
    ("dall-e-3", "standard", "1792x1024"): 0.08,
    ("dall-e-3", "hd", "1024x1024"): 0.08,
    ("dall-e-3", "hd", "1024x1792"): 0.12,
    ("dall-e-3", "hd", "1792x1024"): 0.12,
}
_DEFAULT_QUALITY = {"gpt-image-1": "medium", "dall-e-3": "standard"}
_DEFAULT_SIZE = "1024x1024"


def _default_http(
    method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
) -> Any:
    import httpx

    timeout = httpx.Timeout(GENERATE_TIMEOUT_S, connect=10.0)
    return httpx.request(method, url, headers=headers, json=json_body, timeout=timeout)


def list_price(model: str, params: dict[str, Any]) -> float | None:
    quality = str(params.get("quality") or _DEFAULT_QUALITY.get(model, "")).lower()
    size = str(params.get("size") or _DEFAULT_SIZE).lower()
    if quality == "auto":
        quality = _DEFAULT_QUALITY.get(model, "")
    if size == "auto":
        size = _DEFAULT_SIZE
    return LIST_PRICES_USD.get((model, quality, size))


def _error_detail(payload: Any) -> tuple[str, str]:
    """(code, message) from an OpenAI error body."""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")
    return "", ""


def map_error(status: int, payload: Any, headers: dict[str, str] | None = None) -> MediaError | None:
    if 200 <= status < 300:
        return None
    code, message = _error_detail(payload)
    code = code.lower()
    if status == 401:
        return MediaError(
            "not_configured", "OpenAI rejected the API key — update it under Connections", status=status
        )
    if status == 402 or code in ("insufficient_quota", "billing_hard_limit_reached"):
        return MediaError(
            "insufficient_credits",
            "OpenAI: the account has no usage credit left" + (f" — {message}" if message else ""),
            status=status,
        )
    if code in ("content_policy_violation", "moderation_blocked") or "safety system" in message.lower():
        return MediaError(
            "content_policy",
            "OpenAI refused the request (content policy)" + (f": {message}" if message else ""),
            final=True,
            status=status,
        )
    if status == 429:
        raw = (headers or {}).get("retry-after")
        try:
            wait = max(0, int(float(raw))) if raw is not None else None
        except (TypeError, ValueError):
            wait = None
        words = "OpenAI is rate-limiting image requests"
        words += f" — wait {wait} s and try again" if wait is not None else " — wait and try again"
        return MediaError("concurrency_limit", words, retry_after_s=wait, status=status)
    return MediaError(
        "provider_error", f"OpenAI error {status}" + (f": {message}" if message else ""), status=status
    )


class OpenAIImagesProvider(MediaProvider):
    id = "openai_images"
    label = "OpenAI"

    def __init__(
        self,
        key_resolver: Callable[[], str | None] | None = None,
        http: HttpRequest | None = None,
    ) -> None:
        self._key_resolver = key_resolver or (lambda: None)
        self._http: HttpRequest = http or _default_http
        self._done: dict[str, JobState] = {}

    def _key(self) -> str | None:
        try:
            return self._key_resolver() or None
        except Exception:  # noqa: BLE001
            return None

    def kinds(self) -> set[str]:
        return {"image"}

    def available(self) -> bool:
        return bool(self._key())

    async def default_model(self, kind: str) -> str:
        if kind != "image":
            raise MediaError("unknown_model", f"OpenAI Images cannot make {kind}")
        return DEFAULT_MODEL

    async def models(self, kind: str) -> list[dict[str, Any]]:
        if kind != "image":
            return []
        return [{"id": m, "name": m, "kind": "image"} for m in MODELS]

    async def quote(self, model: str, params: dict[str, Any], kind: str) -> Quote:
        if kind != "image":
            raise MediaError("unknown_model", f"OpenAI Images cannot make {kind}")
        price = list_price(model, params)
        return Quote(
            expected_cost=price,
            currency="usd",
            free=False,
            provisional=False,
            note="list price" if price is not None else "",
            raw={"model": model, "price_usd": price},
        )

    async def start(
        self, model: str, params: dict[str, Any], kind: str, idem_key: str
    ) -> JobHandle:
        key = self._key()
        if not key:
            raise MediaError(
                "not_configured", "OpenAI is not connected — add an API key under Connections"
            )
        body: dict[str, Any] = {"model": model, "n": 1}
        body.update({k: v for k, v in params.items() if k not in ("n", "model")})
        if model.startswith("dall-e"):
            body.setdefault("response_format", "b64_json")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        def _call() -> tuple[int, Any, dict[str, str]]:
            resp = self._http("POST", API_URL, headers, body)
            status = int(getattr(resp, "status_code", 0) or 0)
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                payload = {}
            raw_headers = getattr(resp, "headers", None) or {}
            try:
                hdrs = {str(k).lower(): str(v) for k, v in dict(raw_headers).items()}
            except Exception:  # noqa: BLE001
                hdrs = {}
            return status, payload, hdrs

        status, payload, hdrs = await asyncio.to_thread(_call)
        err = map_error(status, payload, hdrs)
        if err:
            raise err
        data = payload.get("data") if isinstance(payload, dict) else None
        first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        b64 = str(first.get("b64_json") or "")
        if not b64:
            raise MediaError("provider_error", "OpenAI answered without image bytes")
        try:
            blob = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            raise MediaError("provider_error", "OpenAI answered with unreadable image bytes") from exc
        job_id = f"oai-{uuid.uuid4().hex[:12]}"
        fmt = str(body.get("output_format") or "png").lower()
        price = list_price(model, params)
        self._done[job_id] = JobState(
            "succeeded",
            payload=blob,
            ext=f".{fmt}" if fmt in ("png", "jpeg", "jpg", "webp") else ".png",
            cost_note=f"${price:.3f} list price" if price is not None else "",
            raw={"model": model},
        )
        return JobHandle(job_id=job_id, eta_s=0)

    async def poll(self, job_id: str) -> JobState:
        state = self._done.get(job_id)
        if state is None:
            return JobState("failed", error="OpenAI image jobs do not survive a restart (lost at restart)")
        return state

    async def fetch(self, state: JobState) -> bytes:
        if state.payload is None:
            raise MediaError("provider_error", "no image bytes to deliver")
        return state.payload
