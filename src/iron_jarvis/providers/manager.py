"""Provider Manager (§5).

Registers provider adapters lazily and reports health. ``mock`` is always
available (offline). API providers (``anthropic``/``openai``/``google``) become
available the moment a real credential exists — resolved from the Connections
layer / secrets vault (or, for Anthropic, the ANTHROPIC_API_KEY env var). This is
what makes "connect a model and it just works" true. Browser-session providers
(§7, §10) surface via the vault.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from ..envelope import store as envelope_store
from ..envelope.profile import CapabilityProfile, trusted_profile
from .adapters.anthropic import AnthropicAdapter
from .adapters.base import LLMAdapter
from .adapters.google import GoogleAdapter
from .adapters.mock import MockLLMAdapter
from .adapters.openai import OpenAIAdapter
from .local import is_local_provider
from .vault import BrowserVault

CredentialResolver = Callable[[str], "str | None"]
#: Presence-only check (NO network refresh) used for availability/health.
PresenceResolver = Callable[[str], bool]
AdapterFactory = Callable[..., LLMAdapter]

#: API providers whose availability is gated on a real credential.
API_PROVIDERS = ("anthropic", "openai", "google", "xai", "openrouter")

#: xAI (Grok) is OpenAI-compatible, so it routes through the OpenAI adapter with
#: a base_url override (same pattern as a local Ollama server).
XAI_ENDPOINT = "https://api.x.ai/v1/chat/completions"

#: OpenRouter — one key routes every lab's models (OpenAI-compatible aggregator).
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


def _normalize_ollama_url(url: str | None) -> str | None:
    """Accept a host, a ``/v1`` base, or a full chat URL → the chat endpoint.

    Any OpenAI-compatible server (Ollama, Ollama Cloud, LM Studio, vLLM...)
    serves chat at ``<host>/v1/chat/completions``. Users naturally enter
    ``http://localhost:11434`` or ``.../v1``; without this the adapter POSTs to
    the URL verbatim and every call 404s. Mirrors the host-normalization the
    embeddings layer already does on the same value.
    """
    if not url:
        return url
    u = url.strip().rstrip("/")
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


class ProviderManager:
    def __init__(
        self,
        vault: BrowserVault | None = None,
        default_model: str = "claude-opus-4-8",
        credential_resolver: CredentialResolver | None = None,
        presence_resolver: PresenceResolver | None = None,
        ollama_base_url: str | None = None,
        ollama_model: str = "llama3.1",
        custom_base_url: str | None = None,
        custom_model: str = "",
        grok_cli_available: Callable[[], bool] | None = None,
        inherit_cli_logins: bool = False,
        dynamic_available: Callable[[str], bool | None] | None = None,
        opencode_allowed: Callable[[], list[str]] | None = None,
        envelope_home: "Path | str | None" = None,
    ) -> None:
        # Capability-envelope store home (profiles live at
        # ``<home>/envelopes/<provider>__<model>.json``). None on a bare
        # ProviderManager() — hermetic: no disk is ever consulted and every
        # untrusted provider reads as the default floor profile. The platform
        # passes ``config.home``.
        self._envelope_home: Path | None = Path(envelope_home) if envelope_home else None
        #: ``capability_profile`` cache: (provider, model) -> (store-file
        #: signature, profile). Signature is ``(st_mtime_ns, st_size)`` of the
        #: profile file, or None when it does not exist — see
        #: ``capability_profile`` for why a signature beats a TTL here.
        self._profile_cache: dict[
            tuple[str, str], tuple[tuple[int, int] | None, CapabilityProfile]
        ] = {}
        #: Resolver for the LOCAL models OpenCode may serve. Injected (and
        #: defaulting to "none") so a bare ProviderManager() never shells out.
        self._opencode_resolver: Callable[[], list[str]] = opencode_allowed or (lambda: [])
        self._opencode_cache: list[str] | None = None
        #: Availability oracle for providers registered at RUNTIME (the local
        #: fleet). Returns None for names it doesn't own, so every built-in
        #: provider keeps its existing logic. Injected rather than name-branched
        #: so a bare ProviderManager() stays hermetic — and it MUST be a cached
        #: read: available() runs per provider per request in the router.
        self._dynamic_available = dynamic_available
        self.vault = vault
        # Keyless subscription inheritance (anthropic->claude-cli, openai->
        # codex-cli). OPT-IN so a bare unit-test ProviderManager stays hermetic:
        # otherwise `available("anthropic")` would flip on merely because the
        # `claude` binary is on PATH, making availability env-dependent (present
        # on a dev box, absent in CI). The platform passes inherit_cli_logins=True.
        self._inherit_cli = inherit_cli_logins
        self._default_model = default_model
        self._credential_resolver = credential_resolver
        # Local OpenAI-compatible (Ollama) endpoint: when set, the "ollama"
        # provider is available and routes through OpenAIAdapter(base_url=...).
        # Normalized so a host-only URL ("http://localhost:11434") still resolves
        # to the real /v1/chat/completions endpoint instead of 404-ing.
        # `or None` — CONSTRUCTOR/RECONFIGURE PARITY (v1.204.0 live finding):
        # config.toml stores a cleared endpoint as "" (TOML has no null), and
        # _normalize_ollama_url passes "" through untouched. configure_local
        # already collapsed "" to None, but the constructor did not — so every
        # BOOT resurrected a "Local Ollama" the user never installed
        # (available() gates on `is None`, and "" slipped past it) until the
        # first Settings save re-ran the reconfigure path. Both slots.
        self._ollama_base_url = _normalize_ollama_url(ollama_base_url) or None
        self._ollama_model = ollama_model
        # CUSTOM OpenAI-compatible endpoint (Ollama Cloud / LM Studio / vLLM /
        # any aggregator) — same normalization; key is OPTIONAL (resolved from
        # the vault when connected, keyless local servers just work).
        self._custom_base_url = _normalize_ollama_url(custom_base_url) or None
        self._custom_model = custom_model
        # Live availability probe for the locally-installed Grok CLI, INJECTED by
        # the platform (reads ~/.grok). Kept out of the manager itself so unit
        # tests that build a bare ProviderManager() stay hermetic — a bare manager
        # reports grok-cli unavailable regardless of what's installed on the box.
        self._grok_cli_available_fn = grok_cli_available
        # Presence-only resolver for availability/health: when wired it avoids a
        # blocking OAuth refresh on the async loop. Falls back to the (possibly
        # refreshing) credential check when None, preserving legacy behavior.
        self._presence_resolver = presence_resolver
        self._factories: dict[str, AdapterFactory] = {}
        self._cache: dict[tuple[str, str | None], LLMAdapter] = {}
        self.register("mock", lambda model=None: MockLLMAdapter())
        self.register(
            "anthropic",
            lambda model=None: AnthropicAdapter(
                model=model or default_model, credential=lambda: self._cred("anthropic")
            ),
        )
        self.register(
            "openai",
            lambda model=None: OpenAIAdapter(
                model=model or "gpt-4o-mini", credential=lambda: self._cred("openai")
            ),
        )
        self.register(
            "google",
            lambda model=None: GoogleAdapter(
                model=model or "gemini-1.5-flash",
                credential=lambda: self._cred("google"),
                # google connects via OAuth (specs.py method="oauth"): the
                # credential is an access token, sent as Authorization: Bearer.
                oauth=True,
            ),
        )
        # xAI (Grok) — OpenAI-compatible hosted API; routes through the OpenAI
        # adapter pointed at api.x.ai. Availability is gated on a real credential
        # (an xAI API key, or an OAuth token if xAI later ships a public client).
        self.register(
            "xai",
            lambda model=None: OpenAIAdapter(
                model=model or "grok-2-latest",
                base_url=XAI_ENDPOINT,
                credential=lambda: self._cred("xai"),
                provider_name="xai",
            ),
        )
        # OpenRouter — one key, every lab's models, OpenAI-compatible. Model ids
        # are namespaced ("x-ai/grok-code-fast-1", "openrouter/auto"...).
        self.register(
            "openrouter",
            lambda model=None: OpenAIAdapter(
                model=model or "openrouter/auto",
                base_url=OPENROUTER_ENDPOINT,
                credential=lambda: self._cred("openrouter"),
                provider_name="openrouter",
            ),
        )
        # Local "ollama" provider — an OpenAI-compatible server reached over a
        # configured base_url, needing no API key. Always registered so get()
        # works once configured; availability is gated on ollama_base_url.
        self.register(
            "ollama",
            lambda model=None: OpenAIAdapter(
                model=model or self._ollama_model,
                base_url=self._ollama_base_url,
                api_key=None,
                provider_name="ollama",
            ),
        )
        # CUSTOM endpoint — user-pointed OpenAI-compatible server/aggregator
        # (Ollama Cloud, LM Studio, vLLM, llama.cpp...). Key optional: resolved
        # from the vault when the user connected one on the Connections page.
        self.register(
            "custom",
            lambda model=None: OpenAIAdapter(
                model=model or self._custom_model or "default",
                base_url=self._custom_base_url,
                credential=lambda: self._cred("custom"),
                provider_name="custom",
            ),
        )
        # LOCALLY-INSTALLED CLI provider: Grok (xAI's `grok` CLI). Detected on
        # disk (~/.grok) rather than configured — routes through its own account
        # session against the CLI chat proxy. Always registered so get() works
        # the moment the CLI is installed+logged-in; availability is a LIVE check
        # of the on-disk session (see available()), so it lights up/greys out
        # without a daemon restart. The adapter import is lazy to avoid pulling
        # the CLI stack into every manager construction.
        self.register("grok-cli", lambda model=None: self._make_grok_cli(model))
        # Subscription CLIs (§arbitrage): a logged-in `claude` / `codex` binary
        # is a FLAT-RATE provider — headless print-mode, no API key, the CLI
        # owns auth + model churn. Text-only (no tool calls) by design.
        self.register("claude-cli", lambda model=None: self._make_subprocess_cli("claude-cli", model))
        self.register("codex-cli", lambda model=None: self._make_subprocess_cli("codex-cli", model))
        # OpenCode CLI — LOCAL MODELS ONLY. Unlike the two above (whose whole
        # point is a paid subscription), OpenCode can reach hosted/paid models
        # too, so the adapter refuses anything not proven to run on the user's
        # own hardware. `opencode_allowed` is injected so the manager stays
        # hermetic and tests can pin the list.
        self.register(
            "opencode-cli",
            lambda model=None: self._make_opencode_cli(model),
        )

    #: Keyless subscription INHERITANCE. When 'anthropic'/'openai' has no API key
    #: but the provider's own CLI is logged in, a request resolves to the
    #: inherited CLI adapter (the sanctioned path — the CLI owns auth) instead of
    #: the raw API. The API-KEY path is never affected: a stored key always takes
    #: the raw adapter, byte-for-byte as before.
    _INHERIT_ALIAS = {"anthropic": "claude-cli", "openai": "codex-cli"}

    def _make_subprocess_cli(self, which: str, model: str | None = None) -> LLMAdapter:
        from .adapters.subprocess_cli import make_claude_cli, make_codex_cli

        if which == "claude-cli":
            return make_claude_cli(model=model or "subscription")
        return make_codex_cli(model=model or "subscription")

    def _opencode_allowed(self) -> list[str]:
        """The LOCAL models OpenCode may serve here (cached per manager).

        Cached because ``available()`` is on the routing hot path and the
        underlying detection shells out to ``opencode models`` and may probe a
        proxy. Call ``refresh_opencode()`` after the user changes the allowlist.
        """
        if self._opencode_cache is None:
            try:
                self._opencode_cache = list(self._opencode_resolver())
            except Exception:  # noqa: BLE001 — detection never breaks routing
                self._opencode_cache = []
        return self._opencode_cache

    def refresh_opencode(self) -> None:
        """Drop the cached local-model list (settings changed / re-scan)."""
        self._opencode_cache = None
        for key in [k for k in self._cache if k[0] == "opencode-cli"]:
            self._cache.pop(key, None)

    def _make_opencode_cli(self, model: str | None = None) -> LLMAdapter:
        from .adapters.opencode_cli import OpencodeCliAdapter

        return OpencodeCliAdapter(model=model or "", allowed=self._opencode_allowed)

    @staticmethod
    def _cli_binary_present(binary: str) -> bool:
        """Availability for subscription CLIs — the binary on PATH (or the
        common per-user bin dirs the terminals launcher already scans)."""
        try:
            from ..terminals.ai_clis import _find  # shared detection heuristics

            return _find(binary) is not None
        except Exception:  # noqa: BLE001
            import shutil

            return shutil.which(binary) is not None

    def _make_grok_cli(self, model: str | None) -> LLMAdapter:
        from .adapters.grok_cli import GrokCliAdapter

        return GrokCliAdapter(model=model or "grok-build")

    def _grok_cli_available(self) -> bool:
        """Availability for the locally-installed Grok CLI via the injected
        probe. A bare manager (no probe wired — the unit-test path) reports
        unavailable, so availability never depends on the host's ~/.grok."""
        if self._grok_cli_available_fn is None:
            return False
        try:
            return bool(self._grok_cli_available_fn())
        except Exception:  # noqa: BLE001
            return False

    def _cred(self, name: str) -> str | None:
        """Resolve a live credential for an API provider (vault/connections → env)."""
        if self._credential_resolver is not None:
            try:
                cred = self._credential_resolver(name)
                if cred:
                    return cred
            except Exception:
                pass
        if name == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        return None

    def _present(self, name: str) -> bool:
        """Presence-only availability for an API provider — NEVER refreshes.

        Prefers the injected ``presence_resolver`` (e.g. the Connections layer's
        ``has_credential``, which only checks the vault). With no presence
        resolver wired, falls back to the existing credential check so behavior
        is unchanged. The ANTHROPIC_API_KEY env var is always honored (no I/O).
        """
        if self._presence_resolver is not None:
            try:
                if self._presence_resolver(name):
                    return True
            except Exception:
                pass
        elif self._credential_resolver is not None:
            try:
                if self._credential_resolver(name):
                    return True
            except Exception:
                pass
        if name == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        return False

    def register(self, name: str, factory: AdapterFactory) -> None:
        self._factories[name] = factory
        for key in [k for k in self._cache if k[0] == name]:
            self._cache.pop(key, None)

    def unregister(self, name: str) -> None:
        """Remove a RUNTIME-registered provider (a deleted fleet endpoint).
        Without this, a deleted endpoint's factory lingers and the provider
        reads as available — a ghost the router may still pick."""
        self._factories.pop(name, None)
        for key in [k for k in self._cache if k[0] == name]:
            self._cache.pop(key, None)

    def runtime_provider_names(self) -> list[str]:
        """The RUNTIME-registered local endpoint providers ("fleet-<id>"),
        sorted for stable ordering. The router folds these into its failover
        candidate pool — without this, a healthy verified fleet endpoint was
        invisible to every failover/replacement path unless it happened to be
        the configured default provider."""
        return sorted(n for n in self._factories if n.startswith("fleet-"))

    def configure_local(
        self,
        *,
        ollama_base_url: str | None = None,
        ollama_model: str | None = None,
        custom_base_url: str | None = None,
        custom_model: str | None = None,
    ) -> None:
        """Re-point the local/custom OpenAI-compatible endpoints LIVE.

        The constructor captured these from config at boot; without this, a
        user saving an endpoint in Settings/Connections got a provider that
        stayed unavailable (and adapters bound to stale URLs/models) until the
        next daemon restart. put_settings calls this on any change to the four
        keys. Cached adapter instances for the two providers are dropped so the
        next get() builds against the new values."""
        # `or None`: clearing a field in Settings sends "" — availability checks
        # `is not None`, so an empty string must mean "not configured".
        self._ollama_base_url = _normalize_ollama_url(ollama_base_url) or None
        if ollama_model:
            self._ollama_model = ollama_model
        self._custom_base_url = _normalize_ollama_url(custom_base_url) or None
        self._custom_model = custom_model or ""
        for provider in ("ollama", "custom"):
            for key in [k for k in self._cache if k[0] == provider]:
                self._cache.pop(key, None)

    #: Fleet-sampler reachability keys for the CONFIG-SEEDED local endpoints.
    #: ``ollama_base_url`` / ``custom_base_url`` are rendered by the fleet
    #: registry as nodes whose ids are exactly ``ollama``/``custom``
    #: (``fleet/registry.seeded``), and the sampler records their live
    #: reachability there — so the oracle that answers for every ``fleet-*``
    #: provider also holds an observation about these two.
    #:
    #: ONLY ``custom`` is listed, and that asymmetry is the whole point: the
    #: sampler's verdict is only admissible here when its probe asks the SAME
    #: question the router will ask. The registry seeds the ollama slot with
    #: ``kind="ollama"`` (``fleet/registry.py``), so ``fleet/probes._probe_ollama``
    #: demands ``GET /api/ps`` — Ollama's NATIVE api, which this manager never
    #: uses: the slot's adapter is an ``OpenAIAdapter`` on
    #: ``/v1/chat/completions``, and LM Studio / llama.cpp / vLLM answer that
    #: happily while 404ing ``/api/ps``. A False there would refuse a WORKING
    #: endpoint on every turn, forever. The custom slot has no ``kind``, so it is
    #: probed as OpenAI-compatible (``GET /v1/models``) — the same protocol
    #: surface the adapter uses — which makes its verdict admissible, but only
    #: for a KEYLESS endpoint (see ``available``). The plain provider name is
    #: still tried first, so an oracle that measures these slots DIRECTLY (a real
    #: chat round-trip rather than a fleet probe) wins over anything here.
    _ENDPOINT_ORACLE_ALIAS = {"custom": "fleet-custom"}

    def _endpoint_reachable(self, name: str) -> bool | None:
        """Last-known reachability of a configured local endpoint, or ``None``.

        ``None`` means UNKNOWN (no oracle wired, sampling off, never probed) and
        the caller then keeps the historic config-presence answer — so a bare
        ``ProviderManager()`` and the whole offline suite are byte-for-byte
        unchanged. NEVER a network call: like ``dynamic_available`` itself this
        is a cached dict read, because ``available()`` runs per provider per
        request on the event loop.

        A ``False`` IS NOT FRESH, and callers must weigh that before refusing on
        it. The fleet sampler arms a backoff ladder after 3 consecutive failures
        (``fleet/sampler._BACKOFF_STEPS``, topping out at 600s), so an endpoint
        the user has just restarted can keep reading unreachable for up to TEN
        MINUTES. That is tolerable only because the run-stage guard
        (``ModelRouter._refuses_failover``) is what actually enforces the
        no-silent-failover rule: it measures the real request, with the real
        credential, against the real endpoint, and so cannot be stale or wrong.
        This oracle only lets the UI/router refuse a little earlier — never let
        it become the sole basis for telling the user their server is down.
        """
        oracle = self._dynamic_available
        if oracle is None:
            return None
        for key in (name, self._ENDPOINT_ORACLE_ALIAS.get(name)):
            if not key:
                continue
            try:
                verdict = oracle(key)
            except Exception:  # noqa: BLE001 — a bad oracle never breaks routing
                continue
            if verdict is not None:
                return bool(verdict)
        return None

    def available(self, name: str) -> bool:
        if name in API_PROVIDERS:
            if self._present(name):
                return True
            # Keyless inheritance: usable if the provider's own CLI is logged in.
            alias = self._INHERIT_ALIAS.get(name) if self._inherit_cli else None
            return bool(alias and self.available(alias))
        if name == "ollama":
            # Local provider: available only once a base_url is configured —
            # plus, when the oracle has a DIRECT opinion about this slot, the
            # server must actually be up. CONFIGURED IS NOT CONNECTED: a dead
            # Ollama read "available", so v1.162.0's refusal never fired for it;
            # the connect then raised httpx.ConnectError, which classifies
            # transient by TYPE, and failover shipped the whole conversation to
            # a cloud API. The FLEET-PROBE verdict is deliberately NOT consulted
            # for this slot (see _ENDPOINT_ORACLE_ALIAS: it probes Ollama's
            # native /api/ps, which an LM Studio / llama.cpp / vLLM server this
            # slot works fine with does not serve). The run-stage guard
            # ModelRouter._refuses_failover is what closes the leak either way.
            if self._ollama_base_url is None:
                return False
            return self._endpoint_reachable(name) is not False
        if name == "custom":
            # Custom endpoint: gated on the base_url, NOT a key (keyless local
            # servers are the common case; a vault key is used when present).
            if self._custom_base_url is None:
                return False
            # A KEYED endpoint's unreachable verdict is NOT evidence: the fleet
            # sampler probes with no Authorization header (fleet/sampler passes
            # the default probe_node getter), and fleet/probes._fetch turns ANY
            # non-2xx — a 401 included — into "unreachable". Ollama Cloud and
            # every keyed aggregator would therefore read as down on every turn,
            # permanently, and a credentialed verify from the Connections page
            # is undone by the next sampler pass. A false "your endpoint isn't
            # connected" is exactly as dishonest as the leak this gate exists
            # for, so the probe never gets to speak about a keyed slot.
            if self._present("custom"):
                return True
            return self._endpoint_reachable(name) is not False
        if name == "grok-cli":
            # Locally-installed Grok CLI: live on-disk session check.
            return self._grok_cli_available()
        if name == "claude-cli":
            return self._cli_binary_present("claude")
        if name == "codex-cli":
            return self._cli_binary_present("codex")
        if name == "opencode-cli":
            # Installed AND at least one model that actually runs locally —
            # an OpenCode with only hosted models is not available HERE, and
            # saying otherwise would offer the user a provider that refuses
            # every request.
            return self._cli_binary_present("opencode") and bool(self._opencode_allowed())
        if self._dynamic_available is not None:
            try:
                verdict = self._dynamic_available(name)
            except Exception:  # noqa: BLE001 — a bad oracle never breaks routing
                verdict = None
            if verdict is not None:
                return bool(verdict)
        return name in self._factories

    def has_available_real_endpoint(self) -> bool:
        """True when at least one RUNTIME fleet endpoint is available — folded
        into the mock-trap detector so a box whose ONLY real provider is a
        local endpoint still counts as 'a real provider is connected'."""
        return any(self.available(n) for n in self.runtime_provider_names())

    def has_available_api_provider(self) -> bool:
        """True if at least one REAL (non-mock) provider is connected/available.

        Used by the router to detect the "default is still mock while a real
        provider is connected" trap and emit a downgrade signal instead of
        silently returning fabricated mock output.
        """
        return (
            any(self.available(p) for p in API_PROVIDERS)
            or self.available("ollama")
            or self.available("custom")
            or self.available("grok-cli")
        )

    #: When Auto routing is the default, a ONE-SHOT utility caller (skill apply,
    #: terminal assist, intake, …) may ask for the "auto" pseudo-provider without
    #: a request to classify. Resolve it to the cheapest available REAL provider
    #: (flat-rate CLIs / local first), so those callers just work instead of
    #: KeyError-ing. The router's per-request auto path is unaffected — it never
    #: calls get("auto").
    _AUTO_DEFAULT_ORDER = (
        "claude-cli", "codex-cli", "ollama", "custom", "openrouter",
        "google", "openai", "anthropic", "xai",
    )

    def _auto_concrete_default(self) -> tuple[str, "str | None"]:
        for p in self._AUTO_DEFAULT_ORDER:
            if self.available(p):
                return (p, None)  # the provider's own default model
        return ("mock", None)

    def get(self, name: str, model: str | None = None) -> LLMAdapter:
        if name == "auto":
            name, model = self._auto_concrete_default()
        # Keyless inheritance: route a Claude/OpenAI request with NO API key to
        # the logged-in CLI (sanctioned). A stored API key keeps the raw adapter.
        alias = self._INHERIT_ALIAS.get(name) if self._inherit_cli else None
        if alias and not self._present(name) and self.available(alias):
            name = alias
        if name not in self._factories:
            raise KeyError(f"unknown provider '{name}'")
        key = (name, model)
        if key not in self._cache:
            factory = self._factories[name]
            try:  # model-aware factories take the model; legacy ones take nothing
                self._cache[key] = factory(model)
            except TypeError:
                self._cache[key] = factory()
        return self._cache[key]

    # ------------------------------------------------------------------ #
    # Capability envelope (v1.201.0, Wave A3)
    #
    # ACCESSORS ONLY. Nothing in routing/failover/availability/tool-arming
    # consults these yet — tempting as it is to let `available()` or the
    # router peek at a profile, the loop deliberately does not bend in Wave A
    # (that is Wave B: B1 arm_for_task, B2 should_decompose). The single
    # behavior change this wave is the context window, and that consult lives
    # in `daemon/chat_turn._context_window` (pin > MEASURED envelope > fleet
    # probe > None), which both chat lanes and the agent runtime share.
    # ------------------------------------------------------------------ #

    def is_trusted_provider(self, name: str) -> bool:
        """THE single trusted-provider oracle. Every surface deciding whether
        a provider gets the trusted envelope — this class, the envelope
        routes, any later wave — must call THIS method, never derive its own
        set: two oracles drift (the envelope route's private copy disagreed
        on ``mock`` and would disagree on every future CLI).

        Cloud/CLI providers (and the offline mock) are trusted BY
        CONSTRUCTION — never probed, zero loop-bending. Derived from this
        file's own taxonomy rather than a second string list that can drift:
        ``API_PROVIDERS`` is the hosted-API set, every subscription/terminal
        CLI is registered under a ``*-cli`` name (grok-cli, claude-cli,
        codex-cli, opencode-cli), and ``mock`` must be trusted so the offline
        suite and the first-run demo see zero envelope behavior. Everything
        else (ollama/custom/fleet-*/runtime registrations) is a local endpoint
        the quick battery may measure."""
        n = (name or "").strip()
        return n in API_PROVIDERS or n == "mock" or n.endswith("-cli")

    def capability_profile(self, provider: str, model: str) -> CapabilityProfile:
        """The capability envelope for ``(provider, model)``. NEVER raises.

        Cloud/CLI/mock providers -> ``trusted_profile()`` by construction
        (see ``is_trusted_provider``); the store is not even consulted for
        them. Everything else -> the stored profile under
        ``<home>/envelopes``, or the default floor ``CapabilityProfile`` when
        nothing was ever measured (or when no ``envelope_home`` is wired —
        the bare-manager/unit-test path).

        CACHING: in-memory per (provider, model), invalidated by the store
        file's ``(st_mtime_ns, st_size)`` signature — chosen over a TTL
        because a probe that just completed must be visible on the very next
        turn (a 30s TTL would let one more turn plan against the stale
        window), and because tests can assert invalidation deterministically
        instead of sleeping. Cost per call is one ``stat``; the JSON parse
        only happens when the file actually changed. The returned profile is
        the CACHED instance — treat it as read-only (every consumer here only
        reads; call ``.copy()`` before mutating).
        """
        provider = (provider or "").strip()
        model = (model or "").strip()
        if self.is_trusted_provider(provider):
            return trusted_profile(provider, model)
        try:
            home = self._envelope_home
            if home is None:
                return CapabilityProfile(model_id=model, provider=provider)
            path = envelope_store.profile_path(home, provider, model)
            try:
                st = path.stat()
                sig: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
            except OSError:  # missing/unreadable = unprobed
                sig = None
            key = (provider, model)
            cached = self._profile_cache.get(key)
            if cached is not None and cached[0] == sig:
                return cached[1]
            profile: CapabilityProfile | None = None
            if sig is not None:
                # load_profile never raises; a corrupt file answers None (and
                # is quarantined), which reads as the floor below.
                profile = envelope_store.load_profile(home, provider, model)
            if profile is None:
                profile = CapabilityProfile(model_id=model, provider=provider)
            self._profile_cache[key] = (sig, profile)
            return profile
        except Exception:  # noqa: BLE001 — per contract: never raises
            return CapabilityProfile(model_id=model, provider=provider)

    def measured_context_window(self, provider: str, model: str) -> "int | None":
        """The MEASURED honest context window (tokens), or ``None`` when the
        envelope has no authority over the window. Only a measured profile —
        ``probed``/``partial``/``tuned`` WITH a ``probed_at`` stamp, i.e.
        ``profile.is_measured()`` — AND whose ``honest_context`` itself
        carries a battery's evidence (``field_measured("honest_context")``)
        may speak: a ``seeded`` honest_context is a capped optimistic guess,
        ``trusted_profile`` is documented as NOT a window authority (the
        pin -> probe -> default chain keeps that job for cloud/CLI), and the
        default floor would silently shrink every unprobed local model to
        4096. ``None`` keeps the caller's existing ladder byte-identical.
        Never raises.

        THE PER-FIELD GATE IS THE WAVE-A SHIP-BLOCKER'S TOMBSTONE (recorded
        in docs/IRONCORE-INTEGRATION.md): the profile-level ``probed`` stamp
        means THE BATTERY RAN, not that every field was measured — the quick
        battery delivers exactly chars_per_token, json_adherence and the two
        tool_protocols rungs and NEVER honest_context, so gating on
        ``is_measured()`` alone let one Measure click speak the 4096 floor as
        a measured window and shrink a 128k model's ``_context_window``.
        Until the deep CTX battery ships, NO quick-battery profile can alter
        a window: only a profile whose ``measured_fields`` names
        ``honest_context`` ever answers here."""
        try:
            profile = self.capability_profile(provider, model)
            if profile.is_measured() and profile.field_measured("honest_context"):
                n = int(profile.honest_context)
                if n > 0:
                    return n
        except Exception:  # noqa: BLE001 — the window ladder must never break
            pass
        return None

    def health(self) -> list[dict]:
        rows = [
            {
                "provider": name,
                "available": self.available(name),
                # v1.148.0: "local" comes from providers/local.is_local_provider
                # — ONE definition, shared with the router's local-first ladder
                # and the usage rollup. This list used to be inline here and
                # included grok-cli, which is a client for xAI's HOSTED API:
                # the picker showed it under "runs on your hardware" while every
                # token left the building.
                "class": (
                    "api"
                    if name in API_PROVIDERS
                    else "local"
                    if is_local_provider(name)
                    # Subscription CLIs are their own thing — labelling them
                    # "mock" read like they weren't real (v1.124.0).
                    else "cli"
                    if name in ("claude-cli", "codex-cli", "grok-cli")
                    else "mock"
                ),
            }
            for name in sorted(self._factories)
        ]
        if self.vault is not None:
            for entry in self.vault.providers():
                rows.append(
                    {
                        "provider": entry["provider"],
                        "available": entry["logged_in"],
                        "class": "browser",
                    }
                )
        return rows
