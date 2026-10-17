"""Pixio as a :class:`MediaProvider` (v1.346.0, contract item 1).

Speaks the same public API the ``pixio_*`` tools speak, through the SAME
helpers (:mod:`.pixio_http`) — never a second transport. The facts this
module encodes (the pixio-skill, mirrored 2026-10-07):

* ``POST /generations/estimate {modelId, params}`` → ``{quote: {status:
  measured|provisional, expectedDebit, listCost, planDiscount, allowance:
  {applied, slug, label, dailyLimit, remainingToday, nextAllowanceAt} | null},
  note}``. FREE iff ``expectedDebit == 0`` AND the service says why — an
  allowance applied (Maker's free daily generations) or the model is covered
  by the plan (``planDiscount`` with no ``notCoveredBy``). A ``provisional``
  quote is a number, not a promise: never free, worded "estimate".
* ``POST /generate {modelId, params}`` with an ``Idempotency-Key`` header
  (``ij-<job id>``) → 202 ``{contentId}``: QUEUED, not done.
* ``GET /generations/{id}`` → ``status`` pending|processing|succeeded|failed,
  ``outputUrl`` (public, expiring CDN — downloaded WITHOUT the key),
  ``billing.creditsCharged``.
* Errors ``{error, code}``: ``insufficient_credits`` (402: availableCredits /
  requiredCredits / shortfall), ``content_policy`` (422, final, ``inputHint``),
  ``concurrency_limit`` (429 + Retry-After, account-wide), ``provider_error``.
  ``DELETE /generations/{id}`` does NOT cancel, so nothing here cancels.
* Model ids start with ``pixio/``; each has a kind/category.
* ``GET /pricing`` lists ``yourCredits`` per model — the default model for a
  kind is the CHEAPEST by that figure (the user's own price), falling back
  to the first model of that kind from ``GET /models``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

from .base import (
    MODELS_CACHE_S,
    HttpRequest,
    JobHandle,
    JobState,
    MediaError,
    MediaProvider,
    Quote,
)
from .pixio_http import (
    HttpUpload,
    KeyResolver,
    api_call,
    default_http,
    detail,
    env_key_resolver,
    ext_from_url,
    output_url,
    pixio_publish,
)

#: How long a kind usually takes on Pixio (seconds) when the service gives no
#: eta of its own. Images are inline-short; everything else goes to the
#: background job (``media_inline_wait_s`` decides, not this table).
DEFAULT_ETA_S: dict[str, int] = {"image": 20, "video": 300, "audio": 90, "music": 120}

_KIND_WORDS = {
    "music": ("music", "song"),
    "audio": ("audio", "sound", "sfx", "speech", "voice", "tts"),
    "video": ("video", "clip", "animation"),
    "image": ("image", "picture", "photo", "illustration"),
}


def model_kind(model: dict[str, Any]) -> str:
    """One of KINDS for a ``/models`` row, judged from its kind/category/type
    words (and, last, its id); "" when nothing says."""
    text = " ".join(
        str(model.get(k) or "")
        for k in ("kind", "category", "type", "modality", "output", "outputType")
    ).lower()
    if not text:
        text = str(model.get("id") or model.get("modelId") or "").lower()
    for kind, words in _KIND_WORDS.items():
        if any(w in text for w in words):
            return kind
    return ""


def _model_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else (
        (payload.get("models") or payload.get("data") or payload.get("pricing") or [])
        if isinstance(payload, dict)
        else []
    )
    return [r for r in rows if isinstance(r, dict)]


def _model_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("modelId") or row.get("model") or "")


def _num(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _retry_after(headers: dict[str, str], payload: Any) -> int | None:
    raw = headers.get("retry-after")
    if raw is None and isinstance(payload, dict):
        raw = payload.get("retryAfter") or payload.get("retry_after")
    try:
        return max(0, int(float(str(raw)))) if raw is not None else None
    except (TypeError, ValueError):
        return None


def map_error(status: int, payload: Any, headers: dict[str, str] | None = None) -> MediaError | None:
    """A Pixio refusal in plain words (``None`` for 2xx). The words carry the
    service's own detail text and numbers — never a key."""
    if 200 <= status < 300:
        return None
    headers = headers or {}
    body = payload if isinstance(payload, dict) else {}
    code = str(body.get("code") or "").lower()
    text = detail(body)
    if status == 401:
        return MediaError(
            "not_configured",
            "Pixio rejected the API key — update the 'pixio' secret or PIXIO_API_KEY",
            status=status,
        )
    if status == 402 or code == "insufficient_credits":
        available = _num(body.get("availableCredits"))
        required = _num(body.get("requiredCredits"))
        shortfall = _num(body.get("shortfall"))
        if shortfall is None and available is not None and required is not None:
            shortfall = max(0.0, required - available)
        words = "Pixio: not enough credits"
        if shortfall is not None:
            words += f" — {shortfall:g} more needed"
            if available is not None and required is not None:
                words += f" ({available:g} available, {required:g} required)"
        elif text:
            words += f" — {text}"
        return MediaError("insufficient_credits", words, shortfall=shortfall, status=status)
    if status == 422 or code == "content_policy":
        hint = str(body.get("inputHint") or body.get("input_hint") or "")
        words = "Pixio refused the request (content policy)"
        if text:
            words += f": {text}"
        if hint:
            words += f" — {hint}"
        return MediaError("content_policy", words, input_hint=hint, final=True, status=status)
    if status == 429 or code == "concurrency_limit":
        wait = _retry_after(headers, body)
        words = "Pixio is already running a generation for this account"
        words += f" — wait {wait} s and try again" if wait is not None else " — wait and try again"
        return MediaError("concurrency_limit", words, retry_after_s=wait, status=status)
    if status == 404:
        return MediaError("unknown_model", f"Pixio: not found{': ' + text if text else ''}", status=status)
    return MediaError(
        "provider_error", f"Pixio error {status}{': ' + text if text else ''}", status=status
    )


class PixioProvider(MediaProvider):
    id = "pixio"
    label = "Pixio"

    def __init__(
        self,
        key_resolver: KeyResolver | None = None,
        http: HttpRequest | None = None,
        http_upload: HttpUpload | None = None,
        *,
        config: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key_resolver = key_resolver or env_key_resolver
        self._http: HttpRequest = http or default_http
        self._http_upload = http_upload
        self._config = config
        self._clock = clock
        self._models_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._models_lock = asyncio.Lock()

    # -- key -------------------------------------------------------------------
    def _key(self) -> str | None:
        try:
            return self._key_resolver() or None
        except Exception:  # noqa: BLE001 — a flaky resolver must not crash the provider
            return None

    def _need_key(self) -> str:
        key = self._key()
        if not key:
            raise MediaError(
                "not_configured",
                "Pixio is not connected — add a secret named 'pixio' (Settings → "
                "Connections) or set PIXIO_API_KEY",
            )
        return key

    def _public_http(self) -> HttpRequest | None:
        return self._http

    # -- contract --------------------------------------------------------------
    def kinds(self) -> set[str]:
        return {"image", "video", "audio", "music"}

    def available(self) -> bool:
        return bool(self._key())

    async def _api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        key = self._need_key()
        answer = await api_call(self._http, method, path, key, body, extra_headers)
        err = map_error(answer.status, answer.payload, answer.headers)
        if err:
            raise err
        return answer.payload, answer.headers

    async def models(self, kind: str) -> list[dict[str, Any]]:
        rows = await self._all_models()
        return [r for r in rows if model_kind(r) == kind]

    async def _all_models(self) -> list[dict[str, Any]]:
        now = self._clock()
        cached = self._models_cache
        if cached and now - cached[0] < MODELS_CACHE_S:
            return cached[1]
        async with self._models_lock:
            cached = self._models_cache
            if cached and self._clock() - cached[0] < MODELS_CACHE_S:
                return cached[1]
            payload, _ = await self._api("GET", "/api/v1/models")
            rows = _model_rows(payload)
            self._models_cache = (self._clock(), rows)
            return rows

    async def default_model(self, kind: str) -> str:
        pinned = {}
        try:
            pinned = dict(getattr(self._config, "media_default_models", None) or {})
        except Exception:  # noqa: BLE001 — a config double without the field
            pinned = {}
        chosen = str(pinned.get(kind) or "").strip()
        if chosen:
            return chosen
        of_kind = await self.models(kind)
        ids = {_model_id(r) for r in of_kind if _model_id(r)}
        if not ids:
            raise MediaError(
                "unknown_model", f"no {kind} model is visible to this Pixio account"
            )
        cheapest: tuple[float, str] | None = None
        try:
            pricing, _ = await self._api("GET", "/api/v1/pricing")
            for row in _model_rows(pricing):
                mid = _model_id(row)
                price = _num(row.get("yourCredits"))
                if price is None:
                    price = _num(row.get("listCredits"))
                if mid in ids and price is not None and (cheapest is None or price < cheapest[0]):
                    cheapest = (price, mid)
        except MediaError:
            cheapest = None
        if cheapest:
            return cheapest[1]
        return next(_model_id(r) for r in of_kind if _model_id(r))

    async def quote(self, model: str, params: dict[str, Any], kind: str) -> Quote:
        payload, _ = await self._api(
            "POST", "/api/v1/generations/estimate", {"modelId": model, "params": params}
        )
        body = payload if isinstance(payload, dict) else {}
        q = body.get("quote") if isinstance(body.get("quote"), dict) else body
        expected = _num(q.get("expectedDebit"))
        provisional = str(q.get("status") or "").lower() == "provisional"
        allowance = q.get("allowance") if isinstance(q.get("allowance"), dict) else None
        allowance_applied = bool(allowance and allowance.get("applied"))
        plan_covered = bool(q.get("planDiscount")) and not q.get("notCoveredBy")
        free = (
            expected is not None
            and expected == 0
            and not provisional
            and (allowance_applied or plan_covered)
        )
        label = ""
        if free and allowance_applied and allowance is not None:
            name = str(allowance.get("label") or allowance.get("slug") or "").strip()
            left = allowance.get("remainingToday")
            parts = ["Maker"]
            if name:
                parts[0] = f"Maker: {name}"
            if left is not None:
                parts.append(f"{left} left today")
            label = ", ".join(parts) if name else (f"Maker: {left} left today" if left is not None else "Maker")
        elif free:
            label = "covered by your plan"
        note = "estimate" if provisional else ("your price" if expected is not None else "")
        return Quote(
            expected_cost=expected,
            currency="credits",
            free=free,
            free_label=label,
            provisional=provisional,
            note=note,
            raw=body,
        )

    async def start(
        self, model: str, params: dict[str, Any], kind: str, idem_key: str
    ) -> JobHandle:
        payload, _ = await self._api(
            "POST",
            "/api/v1/generate",
            {"providerId": "pixio", "modelId": model, "params": params},
            {"Idempotency-Key": idem_key},
        )
        body = payload if isinstance(payload, dict) else {}
        job_id = str(body.get("contentId") or body.get("id") or "")
        if not job_id:
            raise MediaError("provider_error", "Pixio accepted the request but returned no id")
        eta = _num(body.get("etaSeconds") or body.get("eta_s") or body.get("eta"))
        return JobHandle(job_id=job_id, eta_s=int(eta) if eta is not None else DEFAULT_ETA_S.get(kind))

    async def poll(self, job_id: str) -> JobState:
        try:
            payload, _ = await self._api("GET", f"/api/v1/generations/{job_id}")
        except MediaError as exc:
            if exc.code == "unknown_model":
                return JobState("failed", error="Pixio no longer knows this generation", raw={})
            raise
        body = payload if isinstance(payload, dict) else {}
        raw = str(body.get("status") or "").lower()
        billing = body.get("billing") if isinstance(body.get("billing"), dict) else {}
        charged = _num(billing.get("creditsCharged")) if billing else None
        cost_note = (
            ("free" if charged == 0 else f"{charged:g} credits charged") if charged is not None else ""
        )
        if raw == "succeeded":
            url = output_url(body)
            return JobState(
                "succeeded", output_url=url, cost_note=cost_note, ext=ext_from_url(url) if url else "", raw=body
            )
        if raw == "failed":
            return JobState(
                "failed", error=detail(body) or "Pixio reported the generation failed", cost_note=cost_note, raw=body
            )
        if raw in ("processing", "running"):
            return JobState("running", raw=body)
        return JobState("pending", raw=body)

    @property
    def has_upload(self) -> bool:
        return True

    async def upload(self, path: Any, *, mime: str = "") -> str:
        key = self._need_key()
        p = Path(path)

        def _read_and_publish() -> str:
            blob = p.read_bytes()
            return pixio_publish(
                key,
                blob=blob,
                filename=p.name,
                mime=mime,
                endpoint="media",
                http_upload=self._http_upload,
            )

        try:
            return await asyncio.to_thread(_read_and_publish)
        except RuntimeError as exc:
            raise MediaError("provider_error", str(exc)) from exc
