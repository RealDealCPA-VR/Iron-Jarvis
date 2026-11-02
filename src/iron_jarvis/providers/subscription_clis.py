"""THE ONE TABLE of the subscription CLIs (v1.426.0, Wave 6 lane B, E5).

Claude Code, Codex and the Grok CLI are the providers a person SIGNS IN to
(a plan, not a key): Iron-Proxy holds their accounts, Build › Code drives
them as harnesses, the router treats them as flat-rate, the failure words
name them. Before this module the same per-provider facts were typed out in
a dozen places — ``iron_proxy/accounts.PROVIDERS`` / ``VENDOR_OF`` /
``_DEFAULT_HOME_DIRS``, ``iron_proxy/service.JARVIS_TO_PROXY``,
``hosted/words.PROVIDERS`` / ``PROXY``, ``agents/model_team._PLAN_CLIS`` /
``_CLI_LABELS``, ``providers/failure_words.SIGN_IN_CLIS``,
``providers/cli_auth.CLI_BINARIES`` / ``SIGN_IN_BINARIES`` and the "is a CLI"
tuples in ``providers/manager`` and ``daemon/routes/connections`` — so a
fourth CLI meant finding every copy. Each of those names is now READ from
here; ``tests/test_subscription_clis_v14260.py`` pins every derived table to
its OLD literal value (the refactor changes no behaviour), and the
dashboard's twin ``dashboard/lib/subscriptionClis.ts`` is GENERATED from this
table (``scripts/gen_subscription_clis.py``, ``--check`` in that test).

Dependency-free on purpose (imported by the providers, Iron-Proxy, the
hosted harnesses and the routes; it must never pull any of them in).

What is NOT here (said so the next reader does not look for it): the
routing ORDER (``router._FAILOVER_ORDER`` / ``manager._AUTO_DEFAULT_ORDER``
interleave these with the APIs — a routing decision, not a fact about the
CLI), the friendly cloud names in ``providers/egress._FRIENDLY`` (they name
the API providers too: "Claude" for both ``claude-cli`` and ``anthropic``),
each CLI's sign-in SENTENCE (``cli_auth.SIGN_IN_FIX``, keyed by binary) and
the Build terminal Launch catalog (``terminals/ai_clis``: every AI CLI,
subscription or not).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionCli:
    """One subscription CLI, every fact the app keeps about it."""

    #: Iron Jarvis's provider id (routing, /models, the ledger).
    provider: str
    #: The word a Build session's account list groups by — the CLI's own
    #: name for itself (``claude`` / ``codex`` / ``grok``).
    vendor: str
    #: Iron-Proxy's provider whose accounts this CLI runs as.
    proxy: str
    #: The environment variable naming the CLI's home (one account = one home).
    home_var: str
    #: The CLI's OWN default home under the user's profile.
    home_dir: str
    #: Build › Code's harness id for it.
    harness: str
    #: The program it runs as.
    binary: str
    #: Does ``cli_auth`` PROBE its sign-in? (Grok's session file is read
    #: instead — ``cli_detect.grok_session`` — never probed.)
    probed: bool
    #: The name a person knows the provider by (``failure_words.PLAIN_NAMES``,
    #: the dashboard's ``providerDisplay``).
    name: str
    #: The label on an Iron-Proxy account of this provider.
    account_label: str
    #: The short label the model team uses for a member on this CLI.
    team_label: str


#: The table, in the order every derived list keeps.
SUBSCRIPTION_CLIS: tuple[SubscriptionCli, ...] = (
    SubscriptionCli("claude-cli", "claude", "anthropic", "CLAUDE_CONFIG_DIR", ".claude", "claude-code",
                    "claude", True, "Claude Code", "Claude", "Claude Code"),
    SubscriptionCli("codex-cli", "codex", "openai", "CODEX_HOME", ".codex", "codex",
                    "codex", True, "Codex", "ChatGPT (Codex)", "Codex"),
    SubscriptionCli("grok-cli", "grok", "xai", "GROK_HOME", ".grok", "grok",
                    "grok", False, "Grok CLI", "Grok", "Grok"),
)

#: Every subscription CLI's provider id, in table order.
PROVIDERS: tuple[str, ...] = tuple(c.provider for c in SUBSCRIPTION_CLIS)
#: The same, as a set (the "is this a subscription CLI" question).
PROVIDER_SET: frozenset[str] = frozenset(PROVIDERS)
#: provider -> Iron-Proxy provider.
PROVIDER_TO_PROXY: dict[str, str] = {c.provider: c.proxy for c in SUBSCRIPTION_CLIS}
#: provider -> the vendor word (Build's account list).
VENDOR_OF: dict[str, str] = {c.provider: c.vendor for c in SUBSCRIPTION_CLIS}
#: Build › Code harness id -> provider.
HARNESS_TO_PROVIDER: dict[str, str] = {c.harness: c.provider for c in SUBSCRIPTION_CLIS}
#: Build › Code harness id -> Iron-Proxy provider.
HARNESS_TO_PROXY: dict[str, str] = {c.harness: c.proxy for c in SUBSCRIPTION_CLIS}
#: provider -> binary, for the CLIs whose sign-in ``cli_auth`` probes.
PROBED_BINARIES: dict[str, str] = {c.provider: c.binary for c in SUBSCRIPTION_CLIS if c.probed}
#: provider -> binary, for every CLI with a sign-in sentence.
SIGN_IN_BINARIES: dict[str, str] = {c.provider: c.binary for c in SUBSCRIPTION_CLIS}
#: home variable -> the CLI's own default home folder name.
HOME_DIRS: dict[str, str] = {c.home_var: c.home_dir for c in SUBSCRIPTION_CLIS}
#: provider -> the name a person knows it by.
NAMES: dict[str, str] = {c.provider: c.name for c in SUBSCRIPTION_CLIS}
#: provider -> the model team's short label.
TEAM_LABELS: dict[str, str] = {c.provider: c.team_label for c in SUBSCRIPTION_CLIS}


def by_provider(provider: str) -> SubscriptionCli | None:
    """The row for ``provider`` (None: not a subscription CLI)."""
    return next((c for c in SUBSCRIPTION_CLIS if c.provider == provider), None)


def by_harness(harness: str) -> SubscriptionCli | None:
    """The row for a Build › Code harness id (None: Pi, OpenCode, …)."""
    return next((c for c in SUBSCRIPTION_CLIS if c.harness == harness), None)


def twin_rows() -> list[dict[str, object]]:
    """The rows the dashboard's generated twin holds (every field, in table
    order) — what ``scripts/gen_subscription_clis.py`` writes."""
    return [
        {
            "provider": c.provider, "vendor": c.vendor, "proxy": c.proxy, "homeVar": c.home_var,
            "homeDir": c.home_dir, "harness": c.harness, "binary": c.binary, "probed": c.probed,
            "name": c.name, "accountLabel": c.account_label, "teamLabel": c.team_label,
        }
        for c in SUBSCRIPTION_CLIS
    ]
