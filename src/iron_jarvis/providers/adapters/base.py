"""Provider-agnostic LLM interface (§6).

The Model Router and Agent Runtime speak only this vocabulary; concrete vendors
(Anthropic, browser-session providers, the offline mock) implement ``LLMAdapter``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: HTTP statuses that mark a TRANSIENT provider failure (rate limit / momentary
#: overload / gateway blip) — safe to retry or fail over. Single source of truth
#: shared by the adapters (when they build a :class:`ProviderError`) and the
#: router's :func:`is_transient_error` classifier. Deliberately excludes 4xx auth
#: / bad-request codes (400/401/403/404/422): those are permanent and must raise.
TRANSIENT_STATUS: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


def parse_retry_after(value: Any) -> float | None:
    """Best-effort parse of a ``Retry-After`` header into SECONDS.

    Accepts the two RFC-7231 forms — a delta-seconds integer (``"30"``) or an
    HTTP-date (``"Wed, 21 Oct 2026 07:28:00 GMT"``) — and returns ``None`` for
    anything unparseable, so a malformed header never crashes the backoff math.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return max(0.0, float(s))
    except (TypeError, ValueError):
        pass
    try:  # HTTP-date form → seconds from now
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:  # noqa: BLE001 — unparseable date → no hint
        return None


class ProviderError(RuntimeError):
    """A typed provider-call failure carrying the HTTP status and retry hint.

    The router classifies failures by TYPE + status (not by string-matching an
    error body, which false-positives on token counts/ids). Adapters raise this
    on an HTTP error so the router can (a) decide transient-vs-permanent
    deterministically and (b) honour a server-sent ``Retry-After`` in its
    backoff. ``transient`` defaults from :data:`TRANSIENT_STATUS` when not given,
    so a caller can just pass ``status_code`` and get the right classification.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        if transient is None:
            transient = status_code in TRANSIENT_STATUS if status_code is not None else False
        self.transient = bool(transient)


def provider_error_from_response(provider: str, resp: Any, detail: str) -> ProviderError:
    """Build a :class:`ProviderError` from an httpx-style error ``resp``.

    Reads the status code and (case-insensitively) the ``Retry-After`` header;
    both are guarded so a bare/fake response object (as tests inject) degrades to
    ``status_code=None`` rather than raising.
    """
    status = getattr(resp, "status_code", None)
    retry_after = None
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            retry_after = parse_retry_after(headers.get("retry-after"))
        except Exception:  # noqa: BLE001 — odd header mapping → no hint
            retry_after = None
    return ProviderError(
        f"{provider} API error {status}: {detail}",
        status_code=status,
        retry_after=retry_after,
    )


@dataclass
class LLMMessage:
    role: str  # "user" | "assistant" | "tool"
    content: str = ""
    tool_call_id: str | None = None  # for role == "tool"
    name: str | None = None  # tool name, for role == "tool"
    #: present on assistant turns that requested tool use (so multi-step tool
    #: loops can be replayed faithfully to vendors that require it).
    tool_calls: list["ToolCall"] = field(default_factory=list)
    #: optional image parts on a user turn (multimodal). Each is
    #: ``{"data_b64": <base64>, "media_type": "image/png"|"image/jpeg"|...}``.
    images: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_use" | "max_tokens"
    #: token accounting for this completion (0/0 for the offline mock).
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0}
    )

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMAdapter(ABC):
    provider: str = ""
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        ...

    def capabilities(self) -> dict[str, Any]:
        # Default = a full API-class model: it can call tools AND accept inline
        # images. Text-only or vision-less adapters (the subscription CLIs)
        # override this so the router's capability-aware routing never lands a
        # tool-using request on an adapter that silently returns tool_calls=[].
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": True,
            "vision": True,
        }
