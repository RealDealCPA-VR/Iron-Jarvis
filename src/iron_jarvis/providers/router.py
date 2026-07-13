"""Model Router (§6).

Selects a ``(provider, model)`` for a request from policy/availability and
executes the completion. Fails over to the offline ``mock`` provider when the
requested provider is unavailable or errors, emitting ``provider.failed`` (§31).

Reliability spine (best-in-class routing):

* **Typed classification** — :func:`is_transient_error` decides transient-vs-
  permanent by exception TYPE + HTTP status (via :class:`ProviderError`), not by
  substring-matching an error body (which false-positives on token counts/ids).
* **Capability-aware routing** — a tool-using request never lands on a text-only
  adapter (e.g. codex-cli) that would silently return ``tool_calls=[]`` and stall
  the agent loop; images prefer a vision-capable adapter.
* **Circuit breaker** — a provider that fails N times in a row is skipped for a
  short cooldown (half-open probe after), so a dead provider stops absorbing
  latency on every request.
* **Failover** — a transient primary failure fans out across the OTHER connected
  providers (CLI-first arbitrage), deduped by resolved-adapter IDENTITY so the
  inherited alias (anthropic→claude-cli) isn't retried twice.
"""

from __future__ import annotations

import asyncio
import random
import re
import subprocess
import time
from collections.abc import AsyncIterator
from typing import Any, Callable, Optional

from ..core.events import EventBus, EventType
from . import routing as _routing
from .adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    TRANSIENT_STATUS,
)
from .manager import ProviderManager

#: httpx is an adapter dependency, but guard the import so a stripped-down
#: environment (no HTTP providers installed) still imports the router — the
#: type-based transient checks below just skip the httpx branch when absent.
try:  # pragma: no cover — trivial import guard
    import httpx as _httpx
except Exception:  # noqa: BLE001
    _httpx = None  # type: ignore[assignment]

#: Self-tuning hook (§6 phase-1): given a task class (the agent type, or ``None``),
#: return the ``(provider, model)`` of a LOCAL model that has *proven itself* for
#: that class — or ``None`` to leave routing untouched. Wired by the platform from
#: config (``prefer_local_when_capable``) + eval/observability. When this is
#: ``None`` (the default) routing is byte-for-byte identical to before, so the
#: mock/default path and the offline test suite are unchanged.
LocalOracle = Callable[[Optional[str]], "Optional[tuple[str, str]]"]

#: Auto routing hook (§6 — the routing model). Given the request, returns a
#: routing DECISION dict ``{provider, model, tier, classifier}`` naming the real
#: model to serve it — or ``None`` to let the router fall back. Invoked ONLY when
#: the resolved provider is ``"auto"`` (the user selected Auto), so with Auto off
#: routing is byte-for-byte unchanged. Async: it may call a cheap classifier.
AutoRoute = Callable[..., "Any"]

#: Word-boundary phrases marking a TRANSIENT failure in an error MESSAGE — the
#: fallback path for a plain ``RuntimeError`` that never became a
#: :class:`ProviderError` (e.g. an SDK we don't type, or a legacy caller). We do
#: NOT match bare status-code digits here: numbers appear in token counts / ids
#: inside error bodies and would misclassify a permanent 400 as transient. Real
#: HTTP failures carry their status on :class:`ProviderError` instead.
#: Leading-boundary only (no trailing ``\b``): rate-limit wording is frequently
#: underscore-joined ("rate_limit_error", "overloaded_error"), and ``_`` is a
#: word char so a trailing ``\b`` would never fire there. The LEADING ``\b`` is
#: what prevents matching a token/id substring; that's sufficient.
_TRANSIENT_PHRASE_RE = re.compile(
    r"(?:"
    r"\brate[\s_-]?limit|\bratelimit|"
    r"\boverload|\btoo many requests|"
    r"\bservice unavailable|\btemporarily unavailable|\bunavailable right now|"
    r"\btimed?[\s_-]?out|\btimeout|"
    r"\bconnection (?:error|reset|refused|aborted|closed)|"
    r"\bbad gateway|\bgateway timeout"
    r")",
    re.IGNORECASE,
)

#: Failover candidate order when the wanted provider is down/rate-limited.
#: SUBSCRIPTION ARBITRAGE: the flat-rate CLI providers (claude-cli / codex-cli /
#: grok-cli — a logged-in local CLI, $0 marginal cost) are tried BEFORE the
#: metered APIs, so rate-limit spillover lands on plans you already pay for. This
#: mirrors ``ProviderManager._AUTO_DEFAULT_ORDER`` exactly so the auto-default
#: pick and the failover order can never disagree. (The capability filter still
#: excludes text-only codex-cli / grok when a request carries tools.)
_FAILOVER_ORDER = (
    "claude-cli", "codex-cli", "grok-cli",
    "anthropic", "openai", "google", "xai", "openrouter", "ollama", "custom",
)


def is_transient_error(exc: Exception) -> bool:
    """Classify a provider failure as transient (retry / fail over) or permanent.

    Order of evidence, strongest first:
      1. a typed :class:`ProviderError` — its ``transient`` flag / HTTP status is
         authoritative (set at the adapter from the real status + Retry-After);
      2. the exception TYPE — timeouts and connection drops (asyncio/httpx/
         subprocess/builtin) are always transient regardless of message;
      3. a word-boundary phrase match on the message (rate-limit / overload /
         timeout wording) — the fallback for untyped errors.
    """
    # 1) Typed provider error — authoritative.
    if isinstance(exc, ProviderError):
        if exc.transient:
            return True
        if exc.status_code is not None:
            return exc.status_code in TRANSIENT_STATUS
        # A status-less ProviderError falls through to the phrase check below.
    # 2) By exception TYPE — network/timeout failures are inherently transient.
    if isinstance(
        exc,
        (asyncio.TimeoutError, TimeoutError, ConnectionError, subprocess.TimeoutExpired),
    ):
        return True
    if _httpx is not None and isinstance(
        exc, (_httpx.TimeoutException, _httpx.ConnectError, _httpx.TransportError)
    ):
        return True
    # 3) Word-boundary phrase fallback (NO bare 3-digit status matching).
    return bool(_TRANSIENT_PHRASE_RE.search(str(exc)))


# --------------------------------------------------------------------------- #
# Circuit breaker + capability helpers.
# --------------------------------------------------------------------------- #
class ProviderHealth:
    """Per-provider circuit breaker (CLOSED → OPEN → HALF-OPEN → CLOSED).

    After ``threshold`` consecutive failures a provider is OPENed for
    ``cooldown`` seconds and skipped during resolution/failover — a dead provider
    stops costing every request a full timeout. Once the cooldown elapses it goes
    HALF-OPEN: the next attempt is allowed as a probe; success closes the circuit
    (counters reset), a failure re-opens it for a fresh cooldown. Any success
    resets the streak, so a provider that merely blipped never trips.
    """

    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self._clock = clock
        self._fails: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def allow(self, provider: str) -> bool:
        """True when a call to ``provider`` is permitted (CLOSED or HALF-OPEN)."""
        opened = self._opened_at.get(provider)
        if opened is None:
            return True
        # Cooldown elapsed → HALF-OPEN: allow a single probe through.
        return (self._clock() - opened) >= self.cooldown

    def is_open(self, provider: str) -> bool:
        return not self.allow(provider)

    def record_success(self, provider: str) -> None:
        self._fails.pop(provider, None)
        self._opened_at.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        n = self._fails.get(provider, 0) + 1
        self._fails[provider] = n
        if n >= self.threshold:
            # OPEN, or re-open a failed half-open probe: fresh cooldown from now.
            self._opened_at[provider] = self._clock()


def _capabilities(adapter: Any) -> dict[str, Any]:
    """Read an adapter's ``capabilities()`` defensively — a fake/stub adapter
    (some tests) may not implement it, in which case we assume a full API-class
    model (tool_use + vision) so it is never wrongly excluded."""
    fn = getattr(adapter, "capabilities", None)
    if not callable(fn):
        return {}
    try:
        return fn() or {}
    except Exception:  # noqa: BLE001
        return {}


def _supports_tools(adapter: Any) -> bool:
    return bool(_capabilities(adapter).get("tool_use", True))


def _supports_vision(adapter: Any) -> bool:
    return bool(_capabilities(adapter).get("vision", True))


def _wants_images(messages: list[LLMMessage]) -> bool:
    return any(getattr(m, "images", None) for m in messages)


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
        auto_route: AutoRoute | None = None,
        health: ProviderHealth | None = None,
        deadline_s: float = 180.0,
    ) -> None:
        self.manager = manager
        # Auto routing (opt-in): consulted only when the resolved provider is
        # "auto". None (default) => the "auto" pseudo-provider is never selected,
        # so this is inert and routing is identical to before.
        self._auto_route = auto_route
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
        # Circuit breaker + timing shared across requests (process-lived).
        self.health = health or ProviderHealth()
        self._clock = time.monotonic
        # Overall per-request budget: bounds the same-adapter retry backoff so a
        # sticky provider fails over promptly instead of burning the whole turn.
        self._deadline_s = deadline_s
        #: Set by :meth:`_resolve` so :meth:`complete` can report HOW the primary
        #: was chosen on ``provider.routed`` without changing _resolve's public
        #: 3-tuple return (the self-tuning tests unpack exactly three values).
        self._resolve_reason = "default"

    @property
    def default_provider(self) -> str:
        dp = self._default_provider
        return dp() if callable(dp) else dp

    # -- availability snapshot ---------------------------------------------
    def _safe_available(self, provider: str) -> bool:
        try:
            return bool(self.manager.available(provider))
        except Exception:  # noqa: BLE001 — a probe failure just means "not available"
            return False

    def _snapshot(self) -> set[str]:
        """Snapshot the AVAILABLE real-provider set ONCE per ``complete()``.

        ``available()`` for the CLI providers hits PATH/disk; the old failover
        loop re-probed every candidate on the event loop. Taking the set once and
        reusing it keeps the loop off synchronous I/O."""
        provs = set(_FAILOVER_ORDER)
        provs.add(self.default_provider)
        return {p for p in provs if p != "mock" and self._safe_available(p)}

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
        self._resolve_reason = "explicit" if provider else "default"
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
                    self._resolve_reason = "local-oracle"
                    return self.manager.get(lprov, lmodel), lprov, False

        wanted = provider or self.default_provider
        if wanted != "mock" and not self.manager.available(wanted):
            return self.manager.get("mock"), wanted, True
        return self.manager.get(wanted, model), wanted, False

    def _first_available_real(self, *, need_tools: bool = False) -> str | None:
        """The strongest connected REAL provider (capability-ordered failover
        list), used as the Auto fallback so a request never drops to mock while a
        real model is connected. Skips OPEN circuits and, when the request has
        tools, providers whose adapter can't call tools."""
        for p in _FAILOVER_ORDER:
            if p == "mock" or not self._safe_available(p) or not self.health.allow(p):
                continue
            if need_tools:
                try:
                    if not _supports_tools(self.manager.get(p)):
                        continue
                except Exception:  # noqa: BLE001
                    continue
            return p
        return None

    # -- capability enforcement --------------------------------------------
    def _first_capable(
        self, *, need_tools: bool, need_vision: bool, exclude: LLMAdapter, avail: set[str]
    ) -> LLMAdapter | None:
        """First AVAILABLE, circuit-CLOSED, capability-satisfying REAL adapter to
        REPLACE a primary that can't serve the request. Prefers the default
        provider, then CLI-first failover order; when images are present a
        vision-capable adapter wins but a merely tool-capable one is kept as a
        fallback (better a text answer about the image than a stalled loop)."""
        order: list[str] = []
        dp = self.default_provider
        if dp and dp != "mock":
            order.append(dp)
        order += [p for p in _FAILOVER_ORDER if p != dp]
        vision_fallback: LLMAdapter | None = None
        for p in order:
            if p == "mock" or p not in avail or not self.health.allow(p):
                continue
            try:
                alt = self.manager.get(p)
            except Exception:  # noqa: BLE001
                continue
            if alt is exclude or alt.provider == exclude.provider:
                continue
            if need_tools and not _supports_tools(alt):
                continue
            if need_vision and not _supports_vision(alt):
                if vision_fallback is None:
                    vision_fallback = alt
                continue
            return alt
        return vision_fallback

    def _enforce_capabilities(
        self, adapter: LLMAdapter, need_tools: bool, need_vision: bool, avail: set[str]
    ) -> LLMAdapter | None:
        """Return a replacement adapter when the primary can't satisfy the
        request's hard capability (tools), else ``None`` to keep it. A tool-using
        request MUST NOT run on a text-only adapter (it returns tool_calls=[] and
        silently breaks the agent loop). Vision is a softer preference."""
        if need_tools and not _supports_tools(adapter):
            return self._first_capable(
                need_tools=True, need_vision=need_vision, exclude=adapter, avail=avail
            )
        if need_vision and not _supports_vision(adapter):
            return self._first_capable(
                need_tools=need_tools, need_vision=True, exclude=adapter, avail=avail
            )
        return None

    async def _resolve_auto(
        self, system, messages, tools, task_class
    ) -> tuple[LLMAdapter, str, bool, "dict | None"]:
        """Auto route: ask the routing model for a target, else fall back to the
        strongest available real provider. Returns (adapter, wanted, downgraded,
        routed_event | None)."""
        need_tools = bool(tools)
        decision: dict | None = None
        if self._auto_route is not None:
            try:
                decision = await self._auto_route(system, messages, tools, task_class)
            except Exception:  # never let routing break a request
                decision = None
        if decision:
            tp = str(decision.get("provider") or "")
            tm = decision.get("model") or None
            if tp and tp != "mock" and self.manager.available(tp):
                return self.manager.get(tp, tm), tp, False, {
                    "tier": decision.get("tier", ""),
                    "provider": tp,
                    "model": tm or "",
                    "classifier": decision.get("classifier", ""),
                }
        # Fallback: the strongest connected real provider (its own default model).
        fp = self._first_available_real(need_tools=need_tools)
        if fp is not None:
            return self.manager.get(fp), fp, False, {
                "tier": (decision or {}).get("tier", "") if decision else "",
                "provider": fp,
                "model": "",
                "classifier": (decision or {}).get("classifier", "") if decision else "",
                "fallback": True,
            }
        # Nothing real connected → offline mock (downgraded surfaces the banner).
        return self.manager.get("mock"), "auto", True, None

    # -- execution helpers -------------------------------------------------
    async def _timed_complete(
        self, adapter: LLMAdapter, *, system, messages, tools
    ) -> LLMResponse:
        """Run a completion and, on SUCCESS, feed the observed latency into the
        per-(provider,model) EWMA so Auto can prefer the faster of two equally-
        cheap candidates. A failure records nothing (it raises before the note)."""
        t0 = self._clock()
        resp = await adapter.complete(system=system, messages=messages, tools=tools)
        try:
            _routing.LATENCY.record(adapter.provider, adapter.model, self._clock() - t0)
        except Exception:  # noqa: BLE001 — telemetry must never break a request
            pass
        return resp

    async def _attempt_with_retry(
        self, adapter: LLMAdapter, *, system, messages, tools, deadline: float
    ) -> LLMResponse:
        """First attempt + up to 2 SAME-ADAPTER retries on a transient blip.

        Backoff = ``max(exponential, Retry-After)`` with ±50% jitter (thundering-
        herd guard); a retry is skipped when it would blow the router deadline, so
        a sticky provider fails over promptly instead of eating the whole turn."""
        delay = 1.5
        attempt = 0
        while True:
            try:
                return await self._timed_complete(
                    adapter, system=system, messages=messages, tools=tools
                )
            except Exception as exc:  # noqa: BLE001 — classified below
                if not is_transient_error(exc) or attempt >= 2:
                    raise
                retry_after = getattr(exc, "retry_after", None) if isinstance(
                    exc, ProviderError
                ) else None
                wait = max(delay, retry_after or 0.0) * random.uniform(0.5, 1.5)
                if self._clock() + wait >= deadline:
                    raise  # retrying would exceed the budget → fail over now
                attempt += 1
                await asyncio.sleep(wait)
                delay *= 2.5

    async def _emit_routed(
        self, requested_arg, adapter, reason, routed_payload, session_id
    ) -> None:
        """Publish a structured ``provider.routed`` for a REAL route. (A route to
        mock is an offline/downgrade signal carried by ``provider.downgraded``, so
        we don't also emit a routed event for it.) Auto merges its
        tier/provider/model/classifier fields so existing consumers keep working."""
        requested = requested_arg or ("auto" if routed_payload is not None else self.default_provider)
        payload = {
            "requested": requested,
            "resolved_provider": adapter.provider,
            "resolved_model": adapter.model,
            "reason": reason,
        }
        if routed_payload:
            payload.update(routed_payload)
        await self.event_bus.publish(
            EventType.PROVIDER_ROUTED, payload, session_id=session_id
        )

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
        # AUTO ROUTING: only when the resolved provider is the "auto" pseudo-
        # provider (the user selected Auto). Any other path is byte-for-byte the
        # prior behaviour — an explicit provider/model is always honoured as-is.
        routed_payload: dict | None = None
        if (provider or self.default_provider) == "auto":
            adapter, wanted, downgraded, routed_payload = await self._resolve_auto(
                system, messages, tools, task_class
            )
            reason = "auto-tier"
        else:
            adapter, wanted, downgraded = self._resolve(provider, model, task_class)
            reason = self._resolve_reason

        need_tools = bool(tools)
        need_vision = _wants_images(messages)
        avail = self._snapshot()

        # CAPABILITY-AWARE ROUTING (the critical bug): a tool-using request must
        # never resolve to a text-only adapter (codex-cli, inherited-openai) that
        # silently returns tool_calls=[] and stalls the agent loop. Swap to the
        # first tool-capable connected provider; images prefer a vision-capable
        # one. Only re-route a REAL resolved adapter (never the mock/downgrade).
        if not downgraded and adapter.provider != "mock":
            repl = self._enforce_capabilities(adapter, need_tools, need_vision, avail)
            if repl is not None:
                adapter = repl
                reason = "failover"

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
        elif (
            adapter.provider == "mock"
            and provider != "mock"  # only warn about a mock DEFAULT, not an explicit ask
            and self.manager.has_available_api_provider()
        ):
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

        # A structured provider.routed for EVERY real route (explicit/default/
        # auto-tier/local-oracle/failover). Mock offline/downgrade already emits
        # provider.downgraded, so we skip a redundant routed event there.
        if adapter.provider != "mock":
            await self._emit_routed(provider, adapter, reason, routed_payload, session_id)

        deadline = self._clock() + self._deadline_s
        tried_ids: set[int] = set()
        tried_providers: set[str] = set()
        try:
            response = await self._attempt_with_retry(
                adapter, system=system, messages=messages, tools=tools, deadline=deadline
            )
            self.health.record_success(adapter.provider)
            return RouteResult(response, adapter.provider, adapter.model)
        except Exception as exc:
            transient = is_transient_error(exc)
            self.health.record_failure(adapter.provider)
            tried_ids.add(id(adapter))
            tried_providers.add(adapter.provider)
            await self.event_bus.publish(
                EventType.PROVIDER_FAILED,
                {"provider": adapter.provider, "error": f"{type(exc).__name__}: {exc}"},
                session_id=session_id,
            )
            # (A) DEFAULT-PROVIDER FALLBACK — runs even for a NON-transient primary
            # failure: a self-tuned LOCAL pick (or an explicit provider) that's
            # down must fall back to the healthy cloud default. IMPORTANT: use the
            # default provider's OWN default model (passing the failed provider's
            # model id across — anthropic asked to run "gpt-4o" — just fails
            # again). Deduped by resolved-adapter IDENTITY so the inherited alias
            # (default "anthropic" → claude-cli when it equals the failed primary)
            # is skipped, not retried.
            dp = self.default_provider
            if (
                dp != "mock"
                and dp not in tried_providers
                and self._safe_available(dp)
                and self.health.allow(dp)
            ):
                alt = None
                try:
                    alt = self.manager.get(dp)
                except Exception:  # noqa: BLE001
                    alt = None
                if (
                    alt is not None
                    and id(alt) not in tried_ids
                    and alt.provider not in tried_providers
                    and (not need_tools or _supports_tools(alt))
                ):
                    try:
                        response = await self._timed_complete(
                            alt, system=system, messages=messages, tools=tools
                        )
                        self.health.record_success(alt.provider)
                        await self.event_bus.publish(
                            EventType.PROVIDER_FAILOVER,
                            {"from": adapter.provider, "to": alt.provider, "reason": "provider down"},
                            session_id=session_id,
                        )
                        return RouteResult(response, alt.provider, alt.model)
                    except Exception as dexc:  # noqa: BLE001 — the default failed too
                        self.health.record_failure(alt.provider)
                        tried_ids.add(id(alt))
                        tried_providers.add(alt.provider)
                        await self.event_bus.publish(
                            EventType.PROVIDER_FAILED,
                            {"provider": alt.provider, "error": f"{type(dexc).__name__}: {dexc}"},
                            session_id=session_id,
                        )
            # (B) SIDEWAYS FAILOVER — TRANSIENT only (rate-limit arbitrage): when
            # the primary is momentarily overloaded (e.g. the Claude Max window is
            # exhausted because Claude Code shares it), try the OTHER connected
            # real providers before giving up. Filtered by the availability
            # snapshot, the circuit breaker, capability (tools ⇒ skip text-only
            # codex-cli/grok), and resolved-adapter identity dedup.
            if transient:
                for p in _FAILOVER_ORDER:
                    if p in tried_providers or p == "mock" or p not in avail:
                        continue
                    if not self.health.allow(p):
                        continue
                    try:
                        alt = self.manager.get(p)
                    except Exception:  # noqa: BLE001
                        continue
                    if id(alt) in tried_ids or alt.provider in tried_providers:
                        continue
                    if need_tools and not _supports_tools(alt):
                        continue
                    try:
                        response = await self._timed_complete(
                            alt, system=system, messages=messages, tools=tools
                        )
                        self.health.record_success(alt.provider)
                        await self.event_bus.publish(
                            EventType.PROVIDER_FAILOVER,
                            {"from": adapter.provider, "to": alt.provider, "reason": "rate limited"},
                            session_id=session_id,
                        )
                        return RouteResult(response, alt.provider, alt.model)
                    except Exception:  # noqa: BLE001 — try the next candidate
                        self.health.record_failure(alt.provider)
                        tried_ids.add(id(alt))
                        tried_providers.add(alt.provider)
                        continue
            # NEVER fabricate: when the caller wanted a REAL provider, surface the
            # failure (the session fails with the provider's actual error) instead
            # of silently returning mock's scripted output as if it were an answer
            # — that fabrication reads as "the app is lying to me". The mock
            # fallback remains only for the offline/mock-default path.
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

    # -- streaming execution helpers (FX-01) -------------------------------
    async def _stream_one(
        self,
        adapter: LLMAdapter,
        *,
        system,
        messages,
        tools,
        deadline: float,
        retry: bool,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a SINGLE candidate adapter, yielding its raw frames.

        With ``retry=True`` (the primary attempt, the streaming twin of
        :meth:`_attempt_with_retry`) a TRANSIENT failure that lands BEFORE any
        frame is yielded is retried on the same adapter up to twice, with
        ``max(exponential, Retry-After)`` ±50%-jittered backoff bounded by the
        router deadline. The moment a frame is yielded the attempt is committed:
        a subsequent error — or a permanent one, or retry exhaustion —
        propagates, so the caller never retries or fails over a live stream. With
        ``retry=False`` (the failover candidates, the twin of
        :meth:`_timed_complete`) it is a single straight pass-through."""
        if not retry:
            async for frame in adapter.stream(
                system=system, messages=messages, tools=tools
            ):
                yield frame
            return
        delay = 1.5
        attempt = 0
        while True:
            yielded = False
            try:
                async for frame in adapter.stream(
                    system=system, messages=messages, tools=tools
                ):
                    yielded = True
                    yield frame
                return
            except Exception as exc:  # noqa: BLE001 — classified below
                # Committed (a frame already went out), permanent, or budget spent
                # → propagate; a live stream is never retried.
                if yielded or not is_transient_error(exc) or attempt >= 2:
                    raise
                retry_after = getattr(exc, "retry_after", None) if isinstance(
                    exc, ProviderError
                ) else None
                wait = max(delay, retry_after or 0.0) * random.uniform(0.5, 1.5)
                if self._clock() + wait >= deadline:
                    raise  # retrying would exceed the budget → fail over now
                attempt += 1
                await asyncio.sleep(wait)
                delay *= 2.5

    def _record_stream_latency(self, adapter: LLMAdapter, t0: float) -> None:
        """Feed the end-to-end stream duration into the per-(provider,model) EWMA
        (the same telemetry :meth:`_timed_complete` records on a completion),
        guarded so telemetry never breaks a request."""
        try:
            _routing.LATENCY.record(adapter.provider, adapter.model, self._clock() - t0)
        except Exception:  # noqa: BLE001 — telemetry must never break a request
            pass

    @staticmethod
    def _enrich_final(frame: dict[str, Any], adapter: LLMAdapter) -> dict[str, Any]:
        """Tag the terminal ``final`` frame with the provider+model that ACTUALLY
        served it (which may differ from the primary after a failover) so a
        streaming consumer gets the same routing truth ``RouteResult`` carries.
        Every other frame passes through untouched."""
        if isinstance(frame, dict) and frame.get("type") == "final":
            return {**frame, "provider": adapter.provider, "model": adapter.model}
        return frame

    async def stream(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        system: str,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        session_id: str | None = None,
        task_class: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Token-streaming twin of :meth:`complete` (FX-01).

        Resolves + capability-enforces + emits ``provider.routed`` /
        ``provider.downgraded`` EXACTLY as :meth:`complete`, then delegates to
        ``adapter.stream(...)`` and passes its frames straight through, ENRICHING
        the terminal ``final`` frame with the serving ``provider``+``model``.

        Failover invariant: a candidate may only be swapped BEFORE the first frame
        reaches the caller. If the chosen adapter raises before yielding anything,
        the SAME candidate chain :meth:`complete` uses — default-provider fallback
        (transient AND permanent), then TRANSIENT-only sideways failover across
        ``_FAILOVER_ORDER``, filtered by availability + circuit breaker +
        capability + resolved-adapter identity, honouring Retry-After — is tried on
        a fresh adapter. Once ANY frame has been yielded the route is committed: a
        later error PROPAGATES; the router never silently swaps providers
        mid-stream. Never fabricates: if every real candidate fails before the
        first frame it raises the same honest error :meth:`complete` does (only the
        offline/mock-default path may fall through to mock's scripted stream)."""
        # ---- resolve (identical to complete's preflight) --------------------
        routed_payload: dict | None = None
        if (provider or self.default_provider) == "auto":
            adapter, wanted, downgraded, routed_payload = await self._resolve_auto(
                system, messages, tools, task_class
            )
            reason = "auto-tier"
        else:
            adapter, wanted, downgraded = self._resolve(provider, model, task_class)
            reason = self._resolve_reason

        need_tools = bool(tools)
        need_vision = _wants_images(messages)
        avail = self._snapshot()

        # Capability-aware routing: never stream a tool request to a text-only
        # adapter (it returns tool_calls=[] and stalls the agent loop).
        if not downgraded and adapter.provider != "mock":
            repl = self._enforce_capabilities(adapter, need_tools, need_vision, avail)
            if repl is not None:
                adapter = repl
                reason = "failover"

        if downgraded:
            await self.event_bus.publish(
                EventType.PROVIDER_DOWNGRADED,
                {
                    "requested": wanted,
                    "used": "mock",
                    "reason": "not connected — connect a model on the Connections page",
                },
                session_id=session_id,
            )
        elif (
            adapter.provider == "mock"
            and provider != "mock"
            and self.manager.has_available_api_provider()
        ):
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

        if adapter.provider != "mock":
            await self._emit_routed(provider, adapter, reason, routed_payload, session_id)

        # ---- streaming execution: failover ONLY before the first frame ------
        deadline = self._clock() + self._deadline_s
        tried_ids: set[int] = set()
        tried_providers: set[str] = set()
        committed = False  # True once any frame reached the caller → no swap

        # (Primary) — same-adapter retry on a transient blip before first frame.
        t0 = self._clock()
        try:
            async for frame in self._stream_one(
                adapter, system=system, messages=messages, tools=tools,
                deadline=deadline, retry=True,
            ):
                committed = True
                yield self._enrich_final(frame, adapter)
            self._record_stream_latency(adapter, t0)
            self.health.record_success(adapter.provider)
            return
        except Exception as exc:  # noqa: BLE001 — classified below
            if committed:
                raise  # already streaming this provider — never swap mid-stream
            primary_exc = exc
            transient = is_transient_error(exc)
            self.health.record_failure(adapter.provider)
            tried_ids.add(id(adapter))
            tried_providers.add(adapter.provider)
            await self.event_bus.publish(
                EventType.PROVIDER_FAILED,
                {"provider": adapter.provider, "error": f"{type(exc).__name__}: {exc}"},
                session_id=session_id,
            )

        # (A) DEFAULT-PROVIDER FALLBACK — runs for transient AND permanent primary
        # failures (a down local/explicit pick must reach the healthy default),
        # deduped by resolved-adapter identity so an inherited alias isn't retried.
        dp = self.default_provider
        if (
            dp != "mock"
            and dp not in tried_providers
            and self._safe_available(dp)
            and self.health.allow(dp)
        ):
            alt = None
            try:
                alt = self.manager.get(dp)
            except Exception:  # noqa: BLE001
                alt = None
            if (
                alt is not None
                and id(alt) not in tried_ids
                and alt.provider not in tried_providers
                and (not need_tools or _supports_tools(alt))
            ):
                t0 = self._clock()
                try:
                    async for frame in self._stream_one(
                        alt, system=system, messages=messages, tools=tools,
                        deadline=deadline, retry=False,
                    ):
                        committed = True
                        yield self._enrich_final(frame, alt)
                    self._record_stream_latency(alt, t0)
                    self.health.record_success(alt.provider)
                    await self.event_bus.publish(
                        EventType.PROVIDER_FAILOVER,
                        {"from": adapter.provider, "to": alt.provider, "reason": "provider down"},
                        session_id=session_id,
                    )
                    return
                except Exception as dexc:  # noqa: BLE001 — the default failed too
                    if committed:
                        raise
                    self.health.record_failure(alt.provider)
                    tried_ids.add(id(alt))
                    tried_providers.add(alt.provider)
                    await self.event_bus.publish(
                        EventType.PROVIDER_FAILED,
                        {"provider": alt.provider, "error": f"{type(dexc).__name__}: {dexc}"},
                        session_id=session_id,
                    )

        # (B) SIDEWAYS FAILOVER — TRANSIENT only (rate-limit arbitrage across the
        # OTHER connected providers), same filters complete() applies.
        if transient:
            for p in _FAILOVER_ORDER:
                if p in tried_providers or p == "mock" or p not in avail:
                    continue
                if not self.health.allow(p):
                    continue
                try:
                    alt = self.manager.get(p)
                except Exception:  # noqa: BLE001
                    continue
                if id(alt) in tried_ids or alt.provider in tried_providers:
                    continue
                if need_tools and not _supports_tools(alt):
                    continue
                t0 = self._clock()
                try:
                    async for frame in self._stream_one(
                        alt, system=system, messages=messages, tools=tools,
                        deadline=deadline, retry=False,
                    ):
                        committed = True
                        yield self._enrich_final(frame, alt)
                    self._record_stream_latency(alt, t0)
                    self.health.record_success(alt.provider)
                    await self.event_bus.publish(
                        EventType.PROVIDER_FAILOVER,
                        {"from": adapter.provider, "to": alt.provider, "reason": "rate limited"},
                        session_id=session_id,
                    )
                    return
                except Exception:  # noqa: BLE001 — try the next candidate
                    if committed:
                        raise
                    self.health.record_failure(alt.provider)
                    tried_ids.add(id(alt))
                    tried_providers.add(alt.provider)
                    continue

        # NEVER fabricate: a real wanted provider that failed before the first
        # frame surfaces its honest error (identical wording to complete()); only
        # the offline/mock-default path may fall through to mock's scripted stream.
        if wanted != "mock":
            if transient:
                raise RuntimeError(
                    "every connected model is rate-limited or unavailable "
                    f"right now — wait a minute and try again ({adapter.provider}: {primary_exc})"
                ) from primary_exc
            raise primary_exc
        fallback = self.manager.get("mock")
        if fallback is adapter:
            raise primary_exc
        async for frame in fallback.stream(
            system=system, messages=messages, tools=tools
        ):
            yield self._enrich_final(frame, fallback)

    # TODO(followup): a daily budget/cost ledger, response caching, and hard
    # context-window-fit filtering are deferred — none belongs solely in the
    # router and each needs its own surface.
