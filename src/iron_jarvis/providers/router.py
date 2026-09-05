"""Model Router (§6).

Selects a ``(provider, model)`` for a request from policy/availability and
executes the completion. Fails over to the offline ``mock`` provider when the
requested provider is unavailable or errors, emitting ``provider.failed`` (§31).

Reliability spine (best-in-class routing):

* **Typed classification** — :func:`is_transient_error` decides transient-vs-
  permanent by exception TYPE + HTTP status (via :class:`ProviderError`), not by
  substring-matching an error body (which false-positives on token counts/ids).
* **Capability-aware routing** — a tool-using request never RAW-lands on a
  text-only adapter that would silently return ``tool_calls=[]`` and stall the
  agent loop: since v1.131.0 the chosen adapter is wrapped in the prompted-tools
  scaffold (``adapters/prompted_tools.py``) so it serves the loop itself; images
  still prefer a vision-capable adapter (no prompt makes a text model see).
* **Circuit breaker** — a provider that fails N times in a row is skipped for a
  short cooldown (half-open probe after), so a dead provider stops absorbing
  latency on every request.
* **Failover** — a transient primary failure fans out across the OTHER connected
  providers (CLI-first arbitrage), deduped by resolved-adapter IDENTITY so the
  inherited alias (anthropic→claude-cli) isn't retried twice. ONE EXCEPTION, and
  it is a privacy rule rather than a reliability one: a LOCAL endpoint that
  never ANSWERED — never reached, or reached and silent past the timeout —
  refuses instead of failing over (:meth:`ModelRouter._refuses_failover`,
  :func:`local_failure_kind`) — see v1.162.0 and
  :meth:`ModelRouter._unavailable_error`. Since v1.228.0 that rule is a
  POLICY (``config.local_primary_policy``, default ``"refuse"``): under
  ``refuse`` a local primary that ANSWERED with an error (429/5xx/404) refuses
  too, so a conversation never leaves the machine unless the user chose
  ``failover`` in Settings; and a local primary with a base_url is pre-probed
  (:meth:`ModelRouter._local_liveness`, ``GET /v1/models``, ~2.5 s) so a dead
  box refuses at once instead of after the same-adapter retry ladder.
* **Honest failover reason** (v1.228.0) — ``provider.failover.reason``, and the
  ``from``/``why`` fields on :class:`RouteResult` / the stream's final frame,
  are DERIVED from the exception (:func:`failure_reason`), never guessed at
  the publish site.
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
from .adapters.prompted_tools import PromptedToolsAdapter
from .guided import GuidedToolsAdapter, profile_supports_guided
from .local import is_local_provider
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


#: Message shapes meaning the request NEVER REACHED the endpoint — nothing was
#: sent to a server, because no server answered the socket. Deliberately WIDER
#: than :data:`_TRANSIENT_PHRASE_RE` is precise, since the only consumer is
#: :func:`is_unreachable_error` under a LOCAL primary, where a false positive
#: means "refuse instead of substituting a cloud provider" — the fail-closed
#: direction. httpx words a refused local port as "All connection attempts
#: failed"; Windows words it "actively refused it".
_UNREACHABLE_PHRASE_RE = re.compile(
    r"(?:"
    r"connection refused|actively refused|"
    r"all connection attempts failed|"
    r"(?:failed|unable|could not|couldn't|cannot|can't) to? ?connect|"
    r"\bconnect(?:ion)? (?:error|timed? ?out)|"
    r"no route to host|network is unreachable|"
    r"name or service not known|nodename nor servname|getaddrinfo failed|"
    r"\bnot running\b|\bis down\b"
    r")",
    re.IGNORECASE,
)


def is_unreachable_error(exc: Exception) -> bool:
    """True when *exc* says we never reached the endpoint at all.

    The distinction that matters for a LOCAL model (v1.162.0): a server that
    ANSWERED — even with 429/500 — is up, and failing that request over is
    ordinary reliability work. A server that never answered is simply not
    running, and substituting a different provider for it moves the user's
    conversation off their own hardware. A typed :class:`ProviderError` carrying
    a status therefore always reads as REACHED (that status came from the
    server); everything else is judged by exception type, then by message.
    """
    if isinstance(exc, ProviderError) and exc.status_code is not None:
        return False
    # ConnectionRefusedError/ConnectionResetError/ConnectionAbortedError all
    # land here, as does anything an adapter raises as a bare ConnectionError.
    if isinstance(exc, ConnectionError):
        return True
    if _httpx is not None and isinstance(
        exc, (_httpx.ConnectError, _httpx.ConnectTimeout)
    ):
        return True
    return bool(_UNREACHABLE_PHRASE_RE.search(str(exc)))


def local_failure_kind(exc: Exception) -> str | None:
    """Why a LOCAL primary's failure REFUSES instead of failing over — or None.

    ``is_unreachable_error`` alone is not enough, and the gap is the more common
    local failure of the two. It is scoped to "never reached" (connect-shaped
    errors), while ``OpenAIAdapter._client()`` builds its ``httpx.AsyncClient``
    with a 60s timeout and that ONE adapter serves the ``ollama`` AND ``custom``
    slots. A local box that is UP but SLOW — cold-loading a 30B/70B into VRAM,
    a long prefill, a long generation — blows 60s and raises
    ``httpx.ReadTimeout``: transient BY TYPE, not unreachable, so the guard did
    not fire and fallback (A) handed the whole conversation to the cloud
    default. A dead server is a one-time ConnectError the user notices; a
    cold-loading one times out silently and repeatedly.

    Three kinds, because the refusal message must not claim more than we know:

    * ``"unreachable"`` — nothing ever answered the socket (not running);
    * ``"timeout"`` — the endpoint took the request and did not answer in time;
    * ``"interrupted"`` — the transport broke mid-request (``ReadError``,
      ``RemoteProtocolError``: the server died while we were talking to it).

    ``None`` means fail over exactly as before. THE "IT ANSWERED, SO IT IS UP"
    RULE IS UNCHANGED and is checked first: a ``ProviderError`` carrying a
    status (429/500/…) came FROM the server, so it is ordinary reliability
    arbitrage. :func:`is_unreachable_error` itself is deliberately NOT widened —
    it is also the cloud-side predicate, and this broadening is local-only.
    """
    if isinstance(exc, ProviderError) and exc.status_code is not None:
        return None
    if is_unreachable_error(exc):
        return "unreachable"
    # asyncio.TimeoutError IS TimeoutError on 3.11+; both listed for clarity.
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    if _httpx is not None:
        if isinstance(exc, _httpx.TimeoutException):
            return "timeout"
        # TransportError is the base of NetworkError/ProtocolError/ProxyError —
        # every way httpx says "the connection itself failed", as opposed to a
        # response the server actually sent.
        if isinstance(exc, _httpx.TransportError):
            return "interrupted"
    return None


def failure_reason(exc: Exception) -> str:
    """The one-phrase WHY for a provider failure, derived from the exception.

    Before v1.228.0 the two failover publish sites hard-coded their reason
    ("provider down" for the default fallback, "rate limited" for the
    sideways hop), so a local box answering 500 was disclosed to the phone
    and the ledger as "rate limited". The reason is now read off the
    failure itself, in ONE place, and the same string rides the event, the
    :class:`RouteResult` ``why`` field and the chat receipt:

    * a transport shape → :func:`local_failure_kind`'s word
      (``unreachable`` / ``timeout`` / ``interrupted``);
    * a typed :class:`ProviderError` carrying a status → ``"http <status>"``;
    * anything else → ``"transient error"`` when it classifies transient,
      else ``"error"`` (the default fallback runs for permanent failures
      too, and calling a 400 "transient" would be one more guess).
    """
    kind = local_failure_kind(exc)
    if kind:
        return kind
    if isinstance(exc, ProviderError) and exc.status_code is not None:
        return f"http {exc.status_code}"
    return "transient error" if is_transient_error(exc) else "error"


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


def wrap_prompted_tools(adapter: LLMAdapter) -> LLMAdapter:
    """The v1.131.0 wrap decision for a TOOL-carrying request whose resolved
    adapter can't call tools natively: scaffold it with
    :class:`PromptedToolsAdapter` so the CHOSEN model serves the agent loop via
    the prompted contract, instead of rerouting to a different provider (the
    "I picked my local model and got someone else" complaint — the same honesty
    v1.125.0 established for chat) or silently stalling on ``tool_calls=[]``
    when no native-capable provider is connected. Idempotent, and a no-op for a
    natively tool-capable adapter, so double application can't stack prompts.

    Deliberately the ROUTER's seam, not ``ProviderManager.get()``: the manager
    can't know whether a request carries tools, and its direct consumers must
    keep seeing the honest inner adapter — fleet verify probes an endpoint's
    NATIVE tool_calls (a wrapped probe would record a fabricated capability),
    and chat's explicit text-only-pick check reads the same truth.
    """
    if isinstance(adapter, PromptedToolsAdapter) or _supports_tools(adapter):
        return adapter
    return PromptedToolsAdapter(adapter)


#: Liveness pre-probe budget (v1.228.0): long enough for a busy box to list
#: its models, short enough that a dead one refuses before the user wonders.
_LIVENESS_TIMEOUT_S = 2.5


def _short_detail(text: str, wanted: str, status: "int | None") -> str:
    """The endpoint's own error detail for the answered-error refusal, minus
    the adapter's ``"<provider> API error <status>:"`` prefix (the refusal
    already says that) and capped so a proxy's stack dump stays readable."""
    t = (text or "").strip()
    t = re.sub(rf"^{re.escape(wanted)}\s+API error\s+\d+\s*:\s*", "", t, flags=re.I)
    if status is not None:
        t = re.sub(rf"^(?:HTTP\s+)?{status}\s*:\s*", "", t, flags=re.I)
    return t[:240] + ("…" if len(t) > 240 else "")


def _disclosed_reason(reason: str, serving_provider: str) -> str:
    """The route reason a caller may DISCLOSE to the user (v1.165.0).

    The resolver's reason, with ONE override: an answer served by the offline
    mock is always labelled ``"mock"``. On a fresh install the default provider
    IS the mock, so its resolver reason is ``"default"`` — the majority case —
    and a scripted answer that hides inside the majority case is exactly how
    "Done. Wrote RESULT.md" read as finished work. The mock never gets to be
    ordinary."""
    return "mock" if serving_provider == "mock" else reason


class RouteResult:
    """What answered — and, since v1.165.0, WHY.

    ``provider``/``model`` are the adapter that ACTUALLY served the request
    (post-failover, post-wrap). The two optional fields carry the route story
    so a chat reply can disclose it server-side (the dashboard's "answered by
    X" chip used to compute this client-side against the EXPLICIT pick only,
    which is silent on the default route — the exact gap that let a mock
    answer pass without a signal):

    * ``requested`` — the provider the caller EXPLICITLY asked for, ``""``
      when the caller took the default route (chat sends no provider, so ""
      is chat's normal value; the default's name is already in ``provider``).
    * ``reason``    — how the serving adapter was chosen. One of:
      ``"explicit"`` / ``"default"`` / ``"failover"`` / ``"prompted-tools"``
      / ``"auto-tier"`` / ``"local-oracle"`` / ``"mock"`` — the same strings
      ``provider.routed`` has always carried, threaded onto the result
      instead of recomputed. ``"mock"`` always wins when the mock served
      (see :func:`_disclosed_reason`).

    Both fields default (``""`` / ``"default"``) so every pre-existing
    3-positional constructor call — including test stubs — keeps working.

    Since v1.228.0 a ``"failover"`` answer also says WHAT failed and WHY
    (additive, both ``""`` otherwise): ``from_provider`` is the primary that
    failed (the wire key is ``from`` — a Python keyword, hence the attribute
    name) and ``why`` is :func:`failure_reason`'s word for its failure. On the
    default route ``requested`` is ``""`` by contract, so without these the
    receipt could only say "answered by claude-cli — failover" and never name
    the user's own endpoint that was skipped."""

    def __init__(
        self,
        response: LLMResponse,
        provider: str,
        model: str,
        requested: str = "",
        reason: str = "default",
        from_provider: str = "",
        why: str = "",
    ) -> None:
        self.response = response
        self.provider = provider
        self.model = model
        self.requested = requested
        self.reason = reason
        self.from_provider = from_provider
        self.why = why


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
        strict_pin: "Callable[[], bool] | None" = None,
        local_policy: "Callable[[], str] | None" = None,
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
        #: Live flag (config.strict_model_pin): when ON, an EXPLICITLY named
        #: provider must answer or the request fails honestly — no capability
        #: swap, no cross-provider failover, no mock. Same-provider retries
        #: still apply. Default-route requests are unaffected.
        self._strict_pin: Callable[[], bool] = strict_pin or (lambda: False)
        #: Live flag (config.local_primary_policy, v1.228.0): "refuse" (the
        #: default, and what ANY value other than "failover" reads as — the
        #: fail-closed direction) means a LOCAL primary that answered with an
        #: error refuses by name instead of handing the turn to another
        #: provider; "failover" keeps the pre-v1.228.0 arbitrage. Settings
        #: toggle, no restart.
        self._local_policy: Callable[[], str] = local_policy or (lambda: "refuse")

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

    def _fleet_names(self) -> list[str]:
        """Runtime-registered local endpoints ("fleet-<id>") — folded into the
        candidate pool so the user's own hardware is a real failover target.
        getattr-guarded: a bare test manager without the helper just yields []."""
        fn = getattr(self.manager, "runtime_provider_names", None)
        if fn is None:
            return []
        try:
            return list(fn())
        except Exception:  # noqa: BLE001 — candidate discovery never breaks routing
            return []

    def _candidate_order(self) -> list[str]:
        """The failover candidate order: the static ladder, then every
        runtime-registered fleet endpoint. Fleet nodes come AFTER the built-ins
        (frontier/CLI quality first) but are no longer invisible — before this,
        a healthy verified endpoint could never absorb failover unless it was
        the configured default provider."""
        return list(_FAILOVER_ORDER) + self._fleet_names()

    def _snapshot(self) -> set[str]:
        """Snapshot the AVAILABLE real-provider set ONCE per ``complete()``.

        ``available()`` for the CLI providers hits PATH/disk; the old failover
        loop re-probed every candidate on the event loop. Taking the set once and
        reusing it keeps the loop off synchronous I/O."""
        provs = set(self._candidate_order())
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

    def _unavailable_error(
        self,
        wanted: str,
        pinned: bool,
        *,
        kind: str = "unreachable",
        exc: Exception | None = None,
    ) -> ProviderError:
        """The honest refusal for a REAL provider that isn't connected (v1.162.0).

        WHY THIS REPLACED A MOCK ANSWER. The old default route handed the turn to
        the offline mock, whose scripted reply is "Done. Wrote RESULT.md
        summarizing the task." A user whose local fleet endpoint was down got
        exactly that, and it reads as completed work. Worse, that mock does not
        merely SAY it wrote the file — it emits a real ``write_file`` tool call,
        so with a document tool armed the fabrication reaches the DISK.

        Only an EXPLICIT pick was refused before, and only under the strict pin;
        the default route (which is what chat uses — it sends no provider) fell
        through to the mock. A default is not a weaker preference than an
        explicit pick, it is the SAME choice made once in Settings.

        Substituting a different provider is deliberately NOT done here: this
        machine holds client tax documents, and quietly moving a chat from a
        local endpoint to a cloud API is a privacy decision the user makes, not
        a fallback the router picks. Explicitly choosing another model in the UI
        still works exactly as before.

        *kind* (:func:`local_failure_kind`) keeps the wording HONEST about what
        actually happened. "isn't connected right now" is true of a server that
        never answered the socket and a FABRICATION for one that accepted the
        connection and then ran past the adapter's 60s read timeout — the router
        knows it connected, and telling the user their endpoint is disconnected
        sends them to debug the wrong thing (they restart a server that was
        never down instead of waiting out a model load).

        ``"answered_error"`` (v1.228.0, ``local_primary_policy=refuse``) is the
        fourth kind: the endpoint is UP and said so — a 429/5xx/404 — and the
        policy still forbids a stand-in. That refusal quotes the status and
        the endpoint's own detail (*exc*) and names the setting, because the
        user may legitimately want the old behaviour back and must be able to
        find the switch from the error alone.
        """
        if kind == "answered_error":
            status = getattr(exc, "status_code", None) if exc is not None else None
            what = f"answered HTTP {status}" if status else "answered with an error"
            detail = _short_detail(str(exc) if exc is not None else "", wanted, status)
            text = f"{wanted} {what}"
            if detail:
                text += f": {detail}"
            text += " — no substitute used on purpose (local_primary_policy=refuse)."
            if pinned:
                text += " Strict model pin is on as well."
            text += (
                " Check that endpoint or pick another model for this chat and"
                " retry; set local_primary_policy to failover in Settings if you"
                " want another provider to stand in."
            )
            return ProviderError(text)
        lead = {
            "timeout": f"{wanted} didn't respond in time",
            "interrupted": f"the connection to {wanted} dropped mid-request",
        }.get(kind, f"{wanted} isn't connected right now")
        detail = f"{lead}, so this turn was not answered."
        if pinned:
            detail += " No substitute was tried because strict model pin is on."
        else:
            detail += (
                " No substitute was used on purpose — a stand-in answer would"
                " look like real work that never happened."
            )
        fix = {
            # It IS up — the honest advice is time (a cold 30B/70B load), not a
            # restart of something that never went down.
            "timeout": (
                " Give it time to finish loading, or pick another model for"
                " this chat, and retry."
            ),
            "interrupted": (
                " Check that endpoint, or pick another model for this chat,"
                " and retry."
            ),
        }.get(
            kind,
            " Bring that endpoint back up, or pick another model for this"
            " chat, and retry.",
        )
        return ProviderError(detail + fix)

    def _refuses_failover(self, provider: str, exc: Exception) -> str | None:
        """The refusal KIND when *provider* is LOCAL and *exc* is transport-shaped.

        THE RUN-STAGE HALF OF THE v1.162.0 REFUSAL. That guarantee was
        implemented only as the PRE-RUN availability check, which can refuse
        just what it already knows is down — and "a base_url is configured" was
        the whole of ``available("ollama")``, so a dead Ollama/LM-Studio read as
        connected. The connect then raised ``httpx.ConnectError``, which
        :func:`is_transient_error` classifies transient by TYPE, and the
        failover ladder handed the ENTIRE conversation to the next connected
        CLOUD provider — this box holds client tax documents, and moving a chat
        off the user's own hardware is their privacy decision, not a routing
        fallback (asked and confirmed 2026-08-11). Disclosure came only after
        the data had left the machine.

        Narrow on purpose, so cloud→cloud arbitrage is untouched: the primary
        must be LOCAL (``providers/local.is_local_provider`` — the one
        definition), and the failure must be TRANSPORT-shaped
        (:func:`local_failure_kind` — never reached, no answer in time, or the
        connection broke mid-request). A local endpoint that ANSWERED — 429,
        500, "model not found" — still fails over exactly as before, and
        :func:`is_unreachable_error` is left alone so the cloud side is
        untouched. Returns the kind (truthy) so the refusal can say which of
        the three actually happened.

        v1.228.0 (audit R1): "a local endpoint that ANSWERED still fails
        over" was the designed behaviour above, and it was wrong for the
        daily driver — the live 2026-08-28 event was a LiteLLM proxy
        answering 500 because ITS upstream GPU box was unreachable, and the
        conversation went to claude-cli. The premise "it answered, so it is
        up" is false for a proxy. So the answered case is now a POLICY:
        under ``local_primary_policy=refuse`` (the default) a local primary
        that failed for ANY reason refuses, kind ``"answered_error"`` for the
        answered shapes; under ``failover`` the pre-v1.228.0 table stands.
        Transport shapes refuse under both. Cloud primaries are untouched,
        and Auto stays the one exception (guarded by the callers).
        """
        if not is_local_provider(provider):
            return None
        kind = local_failure_kind(exc)
        if kind:
            return kind
        return "answered_error" if self._local_refuses_answered() else None

    def _local_refuses_answered(self) -> bool:
        """The live policy, read fail-closed: only the exact word
        ``"failover"`` re-enables substitution for a local primary that
        answered with an error; anything else (unset, a typo, a future value
        this build does not know) refuses."""
        try:
            policy = str(self._local_policy() or "")
        except Exception:  # noqa: BLE001 — a broken flag reads as the default
            policy = ""
        return policy.strip().lower() != "failover"

    async def _publish_not_connected(
        self, wanted: str, session_id: str | None, *, kind: str = "unreachable"
    ) -> None:
        """Banner event for an unconnected provider. Published BEFORE the raise so
        the dashboard still shows "connect a model" alongside the error.

        The reason follows the same honesty rule as :meth:`_unavailable_error`:
        an endpoint that connected and then timed out is not "not connected",
        and the banner is read as a diagnosis. ``used`` stays ``"none"`` in every
        case — nothing answered, and nothing stood in."""
        reason = {
            "timeout": "no answer in time — that endpoint accepted the"
            " connection but never replied",
            "interrupted": "the connection dropped mid-request — that endpoint"
            " stopped answering",
            "answered_error": "answered with an error — that endpoint is up but"
            " could not serve this turn; no substitute used"
            " (local_primary_policy=refuse)",
        }.get(kind, "not connected — connect a model on the Connections page")
        await self.event_bus.publish(
            EventType.PROVIDER_DOWNGRADED,
            {"requested": wanted, "used": "none", "reason": reason},
            session_id=session_id,
        )

    # -- liveness pre-probe (v1.228.0, audit R5) ---------------------------
    @staticmethod
    def _innermost(adapter: LLMAdapter) -> LLMAdapter:
        """The real transport adapter under the prompted/guided tool wraps."""
        inner = adapter
        for _ in range(4):
            nxt = getattr(inner, "inner", None)
            if nxt is None:
                return inner
            inner = nxt
        return inner

    @staticmethod
    def _models_url(endpoint: str) -> str | None:
        """``{root}/v1/models`` for an OpenAI-compatible endpoint URL, given
        either a bare host, a ``/v1`` base or the full ``/chat/completions``
        URL the adapter holds (same stripping ladder as
        ``fleet/probes.normalize_root``; the listing lives above ``/v1``)."""
        u = (endpoint or "").strip().rstrip("/")
        if not u.startswith(("http://", "https://")):
            return None
        if u.endswith("/chat/completions"):
            u = u[: -len("/chat/completions")].rstrip("/")
        if u.endswith("/v1"):
            u = u[: -len("/v1")].rstrip("/")
        return f"{u}/v1/models"

    async def _local_liveness(self, adapter: LLMAdapter) -> str | None:
        """A ~2.5 s ``GET /v1/models`` against a LOCAL primary BEFORE the
        real attempt. Returns the refusal kind (``"unreachable"`` /
        ``"timeout"``) when the endpoint is provably not serving, else None.

        WHY: a dead local box used to cost the whole same-adapter retry
        ladder — three 60 s read timeouts plus backoff, ~184 s — before the
        honest refusal appeared, because the adapter's client only learns
        the box is dead by waiting for it. The ladder itself STAYS: it is
        what rescues a box that is up but cold-loading a 30B/70B (see
        :func:`local_failure_kind`), and a live probe answer is exactly the
        signal that the ladder is worth running.

        Deliberately narrow so the offline suite and every cloud path are
        untouched: only a local provider (``is_local_provider``), only when
        the transport adapter carries an http(s) endpoint (fake adapters
        have none), through the adapter's OWN client (a test that injected a
        fake client probes the fake). Only a CONNECT-shaped failure or a
        timeout counts as dead; any answer — 401, 404, 500 — means a server
        is listening, and anything else (a fake client without ``get``, an
        odd transport) is INCONCLUSIVE and lets the real attempt decide. A
        false "isn't connected" would be as dishonest as the wait it saves.
        """
        if not is_local_provider(adapter.provider):
            return None
        inner = self._innermost(adapter)
        url = self._models_url(str(getattr(inner, "_endpoint", "") or ""))
        if url is None:
            return None
        make_client = getattr(inner, "_client", None)
        if not callable(make_client):
            return None
        try:
            client = make_client()
            get = getattr(client, "get", None)
            if not callable(get):
                return None
            await asyncio.wait_for(
                get(url, timeout=_LIVENESS_TIMEOUT_S), timeout=_LIVENESS_TIMEOUT_S + 0.5
            )
            return None
        except (asyncio.TimeoutError, TimeoutError):
            return "timeout"
        except ConnectionError:
            return "unreachable"
        except Exception as exc:  # noqa: BLE001 — classified by type below
            if _httpx is not None:
                if isinstance(exc, (_httpx.ConnectError, _httpx.ConnectTimeout)):
                    return "unreachable"
                if isinstance(exc, _httpx.TimeoutException):
                    return "timeout"
            return None

    async def _refuse_if_dead(
        self, adapter: LLMAdapter, *, auto_selected: bool, pinned: bool, session_id
    ) -> None:
        """Run :meth:`_local_liveness` for a non-Auto, non-mock primary and
        raise the honest refusal (same event + wording as the run-stage
        refusal) when the box is dead. Auto is skipped: it is the one route
        where a dead local pick may be replaced, and the run-stage guard
        already honours that. MIRROR NOTE (lock-step): called from BOTH
        complete() and stream() right before ``provider.routed``."""
        if auto_selected or adapter.provider == "mock":
            return
        dead = await self._local_liveness(adapter)
        if dead:
            await self._publish_not_connected(adapter.provider, session_id, kind=dead)
            raise self._unavailable_error(adapter.provider, pinned, kind=dead)

    def _wrap_for_tools(self, adapter: LLMAdapter) -> LLMAdapter:
        """The v1.131.0 wrap decision, envelope-gated onto its two rungs
        (v1.203.0, C2). Same seam, same disclosure: either rung routes as
        ``reason == "prompted-tools"`` (user-configured automation — the
        chosen model keeps the request and serves the loop itself).

        A text-only adapter whose CAPABILITY ENVELOPE is measured, CURRENT-
        generation, and mechanically selects ``strict_json``
        (:func:`~..guided.profile_supports_guided`) gets the
        :class:`~..guided.GuidedToolsAdapter` — real server-side constrained
        decoding, with an honest ladder-down to the fenced contract inside
        the wrapper itself. EVERYTHING ELSE — unmeasured, stale-generation,
        trusted, no ``capability_profile`` on the manager (bare test
        managers), a profile read that raises — falls through to
        :func:`wrap_prompted_tools` exactly as before, byte-identical. The
        profile comes from ``manager.capability_profile`` (cached, one stat
        per call, never raises) — NEVER loaded from disk here: this method
        sits on the hot path of every tool-carrying request.

        Native-capable adapters never reach this method (the seam is guarded
        by ``not _supports_tools``), and the belt below keeps even a direct
        caller from wrapping one — bending a frontier run is the catastrophic
        direction, and it stays pinned shut.
        """
        if isinstance(adapter, PromptedToolsAdapter) or _supports_tools(adapter):
            return adapter
        profiler = getattr(self.manager, "capability_profile", None)
        if callable(profiler):
            try:
                profile = profiler(adapter.provider, adapter.model)
            except Exception:  # noqa: BLE001 — the envelope must never break routing
                profile = None
            if profile is not None and profile_supports_guided(profile):
                return GuidedToolsAdapter(adapter)
        return wrap_prompted_tools(adapter)

    def _first_available_real(self, *, need_tools: bool = False) -> str | None:
        """The strongest connected REAL provider (capability-ordered failover
        list), used as the Auto fallback so a request never drops to mock while a
        real model is connected. Skips OPEN circuits and, when the request has
        tools, providers whose adapter can't call tools."""
        for p in self._candidate_order():
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
        order += [p for p in self._candidate_order() if p != dp]
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
        if fp is None and need_tools:
            # Only text-only providers are connected (a local-fleet-only box):
            # take the strongest anyway — the capability block downstream wraps
            # it in the prompted-tools scaffold. Before v1.131.0 this dropped a
            # tool request to MOCK while a real model was connected.
            fp = self._first_available_real(need_tools=False)
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
        #: Captured HERE (not off ``reason``, which the capability block below
        #: rewrites): Auto is the ONE case where the user delegated the choice of
        #: model to the router, so an unreachable local pick may be replaced.
        auto_selected = (provider or self.default_provider) == "auto"
        if auto_selected:
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

        # AN UNAVAILABLE REAL PROVIDER IS AN ERROR, NEVER A MOCK ANSWER
        # (v1.162.0). This used to fire only for an EXPLICIT pick under the
        # strict pin; the default route fell through to the offline mock and
        # returned its scripted "Done. Wrote RESULT.md summarizing the task."
        # See _unavailable_error for why that is worse than it looks.
        # `downgraded` is set only when a REAL provider was wanted and is not
        # available, so an intentional mock default (offline demos, the test
        # suite) is untouched.
        pinned = bool(provider) and provider != "auto" and self._strict_pin()
        if downgraded:
            await self._publish_not_connected(wanted, session_id)
            raise self._unavailable_error(wanted, pinned)

        # CAPABILITY-AWARE ROUTING. Tools (v1.131.0): a tool-carrying request
        # that resolved to a text-only adapter is WRAPPED in the prompted-tools
        # scaffold — the CHOSEN model keeps the request and drives the loop via
        # the fenced-JSON contract. (Before: rerouted to a different provider,
        # or a silent tool_calls=[] stall when no capable provider existed.)
        # Vision keeps the reroute: no prompt makes a text model see, so images
        # still prefer a vision-capable adapter. Only a REAL resolved adapter
        # (never the mock/downgrade). Under the pin the user's pick keeps the
        # request — tools are offered to the chosen adapter RAW, no wrap (an
        # unverified local endpoint often CAN call tools natively; the verify
        # chip in Connections is the way to prove it). Since v1.203.0 the wrap
        # is envelope-gated onto its two rungs (see _wrap_for_tools): a
        # measured current-generation strict_json profile upgrades the same
        # seam to guided decoding; everything else keeps the fenced contract
        # byte-identical. MIRROR NOTE (lock-step): stream() carries the
        # identical seam — edit both or neither.
        if not pinned and not downgraded and adapter.provider != "mock":
            if need_tools and not _supports_tools(adapter):
                adapter = self._wrap_for_tools(adapter)
                reason = "prompted-tools"
            repl = self._enforce_capabilities(adapter, need_tools, need_vision, avail)
            if repl is not None:
                adapter = repl
                reason = "failover"

        # (An unconnected provider already raised above; the only mock that
        # reaches here is one the user actually chose or defaulted to.)
        if (
            adapter.provider == "mock"
            and provider != "mock"  # only warn about a mock DEFAULT, not an explicit ask
            and (
                self.manager.has_available_api_provider()
                # A box whose only real provider is a local endpoint still
                # counts as connected (getattr: bare test managers lack it).
                or bool(getattr(self.manager, "has_available_real_endpoint", lambda: False)())
            )
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
        # LIVENESS PRE-PROBE (v1.228.0): a dead LOCAL primary refuses NOW, not
        # after the retry ladder. MIRROR NOTE (lock-step): both lanes.
        await self._refuse_if_dead(
            adapter, auto_selected=auto_selected, pinned=pinned, session_id=session_id
        )

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
            # ROUTE DISCLOSURE (v1.165.0): thread the SAME requested/reason the
            # provider.routed event carries onto the result, so the chat lanes
            # can disclose them without re-deriving routing truth client-side.
            # MIRROR NOTE (lock-step): stream() carries the identical
            # disclosure on its terminal `final` frame via _enrich_final —
            # edit both or neither.
            return RouteResult(
                response, adapter.provider, adapter.model,
                requested=provider or "",
                reason=_disclosed_reason(reason, adapter.provider),
            )
        except Exception as exc:
            transient = is_transient_error(exc)
            # The ONE derived reason every disclosure below carries (v1.228.0).
            why = failure_reason(exc)
            self.health.record_failure(adapter.provider)
            tried_ids.add(id(adapter))
            tried_providers.add(adapter.provider)
            await self.event_bus.publish(
                EventType.PROVIDER_FAILED,
                {"provider": adapter.provider, "error": f"{type(exc).__name__}: {exc}"},
                session_id=session_id,
            )
            # STRICT MODEL PIN: the explicit pick failed — surface ITS error
            # verbatim rather than answering from a different provider.
            if pinned:
                raise
            # A LOCAL ENDPOINT THAT NEVER ANSWERED REFUSES — it never fails
            # over, whether it was never reached or merely never replied in
            # time. Same refusal, same event as the pre-run check (v1.162.0),
            # worded for what actually happened; see _refuses_failover for why
            # substituting here would be a privacy decision the router is not
            # allowed to make.
            # MIRROR NOTE (lock-step): stream() carries the identical guard
            # immediately after its own pin check — edit both or neither.
            refusal = None if auto_selected else self._refuses_failover(adapter.provider, exc)
            if refusal:
                await self._publish_not_connected(adapter.provider, session_id, kind=refusal)
                raise self._unavailable_error(
                    adapter.provider, pinned, kind=refusal, exc=exc
                ) from exc
            # (A) DEFAULT-PROVIDER FALLBACK — runs even for a NON-transient primary
            # failure: an explicit provider that ANSWERED WITH AN ERROR must
            # still reach the healthy default. (A local pick that was never
            # reached refused above and never gets here.) IMPORTANT: use the
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
                            {"from": adapter.provider, "to": alt.provider, "reason": why},
                            session_id=session_id,
                        )
                        # A failover answered — disclose it as such (v1.165.0).
                        # Through _disclosed_reason like EVERY terminal site:
                        # stream()'s _enrich_final applies the mock-wins rule
                        # unconditionally, so this lane must too or the
                        # "reason=='mock' iff provider=='mock'" invariant holds
                        # in one lane only. MIRROR NOTE (lock-step): stream()
                        # fallback (A).
                        return RouteResult(
                            response, alt.provider, alt.model,
                            requested=provider or "",
                            reason=_disclosed_reason("failover", alt.provider),
                            from_provider=adapter.provider, why=why,
                        )
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
                for p in self._candidate_order():
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
                            {"from": adapter.provider, "to": alt.provider, "reason": why},
                            session_id=session_id,
                        )
                        # Sideways failover answered — disclose it (v1.165.0).
                        # Through _disclosed_reason for the same lock-step
                        # reason as fallback (A) above. MIRROR NOTE
                        # (lock-step): stream() fallback (B).
                        return RouteResult(
                            response, alt.provider, alt.model,
                            requested=provider or "",
                            reason=_disclosed_reason("failover", alt.provider),
                            from_provider=adapter.provider, why=why,
                        )
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
            # The mock answered after the chosen mock failed — still "mock"
            # (v1.165.0). MIRROR NOTE (lock-step): stream()'s mock tail.
            return RouteResult(
                response, fallback.provider, fallback.model,
                requested=provider or "", reason="mock",
            )

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
    def _enrich_final(
        frame: dict[str, Any],
        adapter: LLMAdapter,
        requested: str = "",
        reason: str = "default",
        from_provider: str = "",
        why: str = "",
    ) -> dict[str, Any]:
        """Tag the terminal ``final`` frame with the provider+model that ACTUALLY
        served it (which may differ from the primary after a failover) so a
        streaming consumer gets the same routing truth ``RouteResult`` carries —
        since v1.165.0 that INCLUDES the route story (``requested``/``reason``,
        same semantics and same "mock always wins" rule as ``RouteResult``), so
        the stream lane can disclose WHO answered and WHY exactly like the
        non-stream lane. MIRROR NOTE (lock-step): complete() carries the
        identical disclosure on its RouteResult returns — edit both or neither.
        Every other frame passes through untouched."""
        if isinstance(frame, dict) and frame.get("type") == "final":
            return {
                **frame,
                "provider": adapter.provider,
                "model": adapter.model,
                "requested": requested,
                "reason": _disclosed_reason(reason, adapter.provider),
                # v1.228.0: the failed primary + the derived reason (both ""
                # unless this is a failover) — the stream twin of RouteResult's
                # from_provider/why. Wire key is "from" to match the chat lanes.
                "from": from_provider,
                "why": why,
            }
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
        auto_selected = (provider or self.default_provider) == "auto"
        if auto_selected:
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

        # STRICT MODEL PIN — the STREAM path must honor the same guarantees as
        # complete(): an explicitly named provider answers or fails honestly.
        # Before this guard, a pinned-but-unavailable provider STREAMED the
        # offline mock's scripted output (a fabrication hole complete() never
        # had), and a pinned pick with tools was silently swapped away.
        # Same refusal as complete() (v1.162.0): an unconnected REAL provider is
        # an error, never the mock's scripted answer. MIRROR NOTE (lock-step):
        # complete() carries the identical guard — edit both or neither. This
        # lane matters MORE, not less: it is the one the user watches token by
        # token, so a fabricated stream reads as a model genuinely working.
        pinned = bool(provider) and provider != "auto" and self._strict_pin()
        if downgraded:
            await self._publish_not_connected(wanted, session_id)
            raise self._unavailable_error(wanted, pinned)

        # Capability-aware routing — same contract as complete(): a tool
        # request on a text-only adapter is wrapped in the prompted-tools
        # scaffold (v1.131.0; the wrapper streams via the base single-chunk
        # default), envelope-gated onto its two rungs since v1.203.0
        # (_wrap_for_tools — MIRROR NOTE, lock-step with complete(): edit
        # both or neither), vision keeps the reroute. Under the pin the
        # user's pick keeps the request, tools offered RAW (no wrap).
        if not pinned and not downgraded and adapter.provider != "mock":
            if need_tools and not _supports_tools(adapter):
                adapter = self._wrap_for_tools(adapter)
                reason = "prompted-tools"
            repl = self._enforce_capabilities(adapter, need_tools, need_vision, avail)
            if repl is not None:
                adapter = repl
                reason = "failover"

        # (An unconnected provider already raised above.)
        if (
            adapter.provider == "mock"
            and provider != "mock"
            and (
                self.manager.has_available_api_provider()
                # A box whose only real provider is a local endpoint still
                # counts as connected (getattr: bare test managers lack it).
                or bool(getattr(self.manager, "has_available_real_endpoint", lambda: False)())
            )
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

        # LIVENESS PRE-PROBE (v1.228.0): a dead LOCAL primary refuses NOW, not
        # after the retry ladder. MIRROR NOTE (lock-step): both lanes.
        await self._refuse_if_dead(
            adapter, auto_selected=auto_selected, pinned=pinned, session_id=session_id
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
                # Route disclosure rides the final frame (v1.165.0) — the
                # stream twin of complete()'s RouteResult fields.
                yield self._enrich_final(frame, adapter, provider or "", reason)
            self._record_stream_latency(adapter, t0)
            self.health.record_success(adapter.provider)
            return
        except Exception as exc:  # noqa: BLE001 — classified below
            if committed:
                raise  # already streaming this provider — never swap mid-stream
            primary_exc = exc
            transient = is_transient_error(exc)
            # The ONE derived reason every disclosure below carries (v1.228.0).
            why = failure_reason(exc)
            self.health.record_failure(adapter.provider)
            tried_ids.add(id(adapter))
            tried_providers.add(adapter.provider)
            await self.event_bus.publish(
                EventType.PROVIDER_FAILED,
                {"provider": adapter.provider, "error": f"{type(exc).__name__}: {exc}"},
                session_id=session_id,
            )
            # STRICT MODEL PIN: the explicit pick failed — surface ITS error
            # verbatim rather than streaming from a different provider.
            if pinned:
                raise
            # A LOCAL ENDPOINT THAT NEVER ANSWERED REFUSES — it never fails over.
            # MIRROR NOTE (lock-step): complete() carries the identical guard.
            # This lane matters MORE, not less: it is the one chat streams, so
            # it is the lane that shipped the conversation to a cloud API — and
            # `committed` is still False when the FIRST TOKEN never arrives,
            # which is exactly what a slow local box does.
            refusal = None if auto_selected else self._refuses_failover(adapter.provider, exc)
            if refusal:
                await self._publish_not_connected(adapter.provider, session_id, kind=refusal)
                raise self._unavailable_error(
                    adapter.provider, pinned, kind=refusal, exc=exc
                ) from exc

        # (A) DEFAULT-PROVIDER FALLBACK — runs for transient AND permanent primary
        # failures (an explicit pick that ANSWERED WITH AN ERROR must reach the
        # healthy default; an unreached local pick refused above and never gets
        # here), deduped by resolved-adapter identity so an alias isn't retried.
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
                        # A failover answered — disclose it as such (v1.165.0).
                        # MIRROR NOTE (lock-step): complete() fallback (A).
                        yield self._enrich_final(
                            frame, alt, provider or "", "failover", adapter.provider, why
                        )
                    self._record_stream_latency(alt, t0)
                    self.health.record_success(alt.provider)
                    await self.event_bus.publish(
                        EventType.PROVIDER_FAILOVER,
                        {"from": adapter.provider, "to": alt.provider, "reason": why},
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
            for p in self._candidate_order():
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
                        # Sideways failover answered — disclose it (v1.165.0).
                        # MIRROR NOTE (lock-step): complete() fallback (B).
                        yield self._enrich_final(
                            frame, alt, provider or "", "failover", adapter.provider, why
                        )
                    self._record_stream_latency(alt, t0)
                    self.health.record_success(alt.provider)
                    await self.event_bus.publish(
                        EventType.PROVIDER_FAILOVER,
                        {"from": adapter.provider, "to": alt.provider, "reason": why},
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
            # The mock answered after the chosen mock failed — still "mock"
            # (v1.165.0). MIRROR NOTE (lock-step): complete()'s mock tail.
            yield self._enrich_final(frame, fallback, provider or "", "mock")

    # TODO(followup): a daily budget/cost ledger, response caching, and hard
    # context-window-fit filtering are deferred — none belongs solely in the
    # router and each needs its own surface.
