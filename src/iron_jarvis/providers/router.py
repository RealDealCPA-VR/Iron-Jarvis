"""Model Router (§6).

Selects a ``(provider, model)`` for a request from policy/availability and
executes the completion. Fails over to the offline ``mock`` provider when the
requested provider is unavailable or errors, emitting ``provider.failed`` (§31).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from ..core.events import EventBus, EventType
from .adapters.base import LLMAdapter, LLMMessage, LLMResponse
from .manager import ProviderManager

#: Self-tuning hook (§6 phase-1): given a task class (the agent type, or ``None``),
#: return the ``(provider, model)`` of a LOCAL model that has *proven itself* for
#: that class — or ``None`` to leave routing untouched. Wired by the platform from
#: config (``prefer_local_when_capable``) + eval/observability. When this is
#: ``None`` (the default) routing is byte-for-byte identical to before, so the
#: mock/default path and the offline test suite are unchanged.
LocalOracle = Callable[[Optional[str]], "Optional[tuple[str, str]]"]

#: Substrings marking a TRANSIENT provider failure (rate limit / momentary
#: overload) worth retrying or failing over — never auth/model errors. Single
#: source of truth; the daemon's one-shot helpers import this via
#: :func:`is_transient_error`.
_TRANSIENT_MARKERS = ("429", "rate_limit", "rate limit", "overloaded", "529", "503")

#: Failover candidate order when the wanted provider is rate-limited.
#: SUBSCRIPTION ARBITRAGE: the flat-rate CLI providers (claude-cli/codex-cli —
#: a logged-in local CLI, $0 marginal cost) are tried before the remaining
#: METERED APIs, so rate-limit spillover lands on plans you already pay for.
_FAILOVER_ORDER = (
    "anthropic", "openai", "claude-cli", "codex-cli",
    "google", "xai", "openrouter", "grok-cli", "ollama", "custom",
)


def is_transient_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


class RouteResult:
    def __init__(self, response: LLMResponse, provider: str, model: str) -> None:
        self.response = response
        self.provider = provider
        self.model = model


class ModelRouter:
    def __init__(
        self,
        manager: ProviderManager,
        default_provider: "str | Callable[[], str]",
        event_bus: EventBus,
        *,
        local_oracle: LocalOracle | None = None,
    ) -> None:
        self.manager = manager
        # Resolve the default provider LIVE on every request: accept either a
        # plain string or a zero-arg callable (the platform passes
        # ``lambda: config.default_provider``). Switching the model in the UI then
        # reaches provider-less callers — routing, the motivation/improvement
        # loops — WITHOUT a daemon restart (otherwise they stay on the boot
        # default, which is "mock" out of the box).
        self._default_provider = default_provider
        self.event_bus = event_bus
        # OFF by default: with no oracle, _resolve behaves exactly as before.
        self._local_oracle = local_oracle

    @property
    def default_provider(self) -> str:
        dp = self._default_provider
        return dp() if callable(dp) else dp

    def _resolve(
        self, provider: str | None, model: str | None, task_class: str | None = None
    ) -> tuple[LLMAdapter, str, bool]:
        """Return (adapter, requested_provider, downgraded_to_mock).

        Self-tuning (opt-in): only when the caller is using the *default* route
        (no explicit provider, or the default provider) AND an oracle is wired
        AND it nominates a LOCAL model that is actually available, prefer that
        local model for this task class. An explicit non-default provider choice
        is always honored as-is; an unavailable/declined local pick falls through
        to the unchanged routing below.
        """
        if self._local_oracle is not None and (
            provider is None or provider == self.default_provider
        ):
            try:
                pick = self._local_oracle(task_class)
            except Exception:  # never let the oracle break routing
                pick = None
            if pick is not None:
                lprov, lmodel = pick
                if lprov != "mock" and self.manager.available(lprov):
                    return self.manager.get(lprov, lmodel), lprov, False

        wanted = provider or self.default_provider
        if wanted != "mock" and not self.manager.available(wanted):
            return self.manager.get("mock"), wanted, True
        return self.manager.get(wanted, model), wanted, False

    async def complete(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        session_id: str | None = None,
        task_class: str | None = None,
    ) -> RouteResult:
        adapter, wanted, downgraded = self._resolve(provider, model, task_class)
        if downgraded:
            # Never silently fake it: tell the user their model isn't connected.
            await self.event_bus.publish(
                EventType.PROVIDER_DOWNGRADED,
                {
                    "requested": wanted,
                    "used": "mock",
                    "reason": "not connected — connect a model on the Connections page",
                },
                session_id=session_id,
            )
        elif adapter.provider == "mock" and self.manager.has_available_api_provider():
            # The mock-trap: the default provider is still "mock" while a REAL
            # provider is connected, so output would be fabricated with no signal.
            # Surface it loudly (the dashboard banners on PROVIDER_DOWNGRADED).
            await self.event_bus.publish(
                EventType.PROVIDER_DOWNGRADED,
                {
                    "requested": "mock (default)",
                    "used": "mock",
                    "reason": (
                        "your default provider is 'mock' but a real provider is "
                        "connected — set it as your default on the Connections page"
                    ),
                },
                session_id=session_id,
            )
        try:
            # TRANSIENT-AWARE first attempt: a 429/overloaded blip retries the
            # SAME adapter (2 extra attempts, short backoff) before any
            # fallback — most rate limits clear in seconds.
            delay = 1.5
            attempt = 0
            while True:
                try:
                    response = await adapter.complete(
                        system=system, messages=messages, tools=tools
                    )
                    return RouteResult(response, adapter.provider, adapter.model)
                except Exception as exc:  # noqa: BLE001 — classified below
                    if not is_transient_error(exc) or attempt >= 2:
                        raise
                    attempt += 1
                    await asyncio.sleep(delay)
                    delay *= 2.5
        except Exception as exc:
            transient = is_transient_error(exc)
            await self.event_bus.publish(
                EventType.PROVIDER_FAILED,
                {"provider": adapter.provider, "error": f"{type(exc).__name__}: {exc}"},
                session_id=session_id,
            )
            # Before failing, try the real DEFAULT provider: a self-tuned LOCAL
            # pick (or an explicit provider) that's down must fall back to the
            # healthy cloud default. IMPORTANT: with the default provider's OWN
            # default model — passing the failed provider's model id across
            # (e.g. anthropic asked to run "gpt-4o") just fails again.
            if (
                adapter.provider != self.default_provider
                and self.default_provider != "mock"
                and self.manager.available(self.default_provider)
            ):
                try:
                    alt = self.manager.get(self.default_provider)
                    response = await alt.complete(
                        system=system, messages=messages, tools=tools
                    )
                    return RouteResult(response, alt.provider, alt.model)
                except Exception:  # noqa: BLE001 — the default failed too
                    pass
            # RATE-LIMIT FAILOVER: when the failure is transient (e.g. the
            # Claude Max window is exhausted because Claude Code shares it),
            # try the OTHER connected real providers before giving up — a
            # working gpt-5.5/gemini answer beats a failed session.
            if transient:
                for p in _FAILOVER_ORDER:
                    if p in (adapter.provider, self.default_provider) or p == "mock":
                        continue
                    if not self.manager.available(p):
                        continue
                    try:
                        alt = self.manager.get(p)
                        response = await alt.complete(
                            system=system, messages=messages, tools=tools
                        )
                        await self.event_bus.publish(
                            "provider.failover",
                            {"from": adapter.provider, "to": alt.provider, "reason": "rate limited"},
                            session_id=session_id,
                        )
                        return RouteResult(response, alt.provider, alt.model)
                    except Exception:  # noqa: BLE001 — try the next candidate
                        continue
            # NEVER fabricate: when the caller wanted a REAL provider, surface
            # the failure (the session fails with the provider's actual error)
            # instead of silently returning mock's scripted output as if it were
            # an answer — that fabrication reads as "the app is lying to me".
            # The mock fallback remains only for the offline/mock-default path.
            if wanted != "mock":
                if transient:
                    raise RuntimeError(
                        "every connected model is rate-limited or unavailable "
                        f"right now — wait a minute and try again ({adapter.provider}: {exc})"
                    ) from exc
                raise
            fallback = self.manager.get("mock")
            if fallback is adapter:
                raise
            response = await fallback.complete(
                system=system, messages=messages, tools=tools
            )
            return RouteResult(response, fallback.provider, fallback.model)
