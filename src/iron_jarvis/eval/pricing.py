"""Static, approximate model price table for cost/usage analytics (§30).

The prices below are **approximate and editable** — they track the public
list prices (USD per 1,000,000 tokens) for the major hosted providers at the
time of writing and are intended for rough cost estimation in the dashboard,
not billing. Update :data:`PRICES` when a provider changes pricing or you add a
new model. Local/mock providers (``mock``, ``ollama``) cost nothing.

Matching is best-effort: :func:`cost_for` looks up the exact ``(provider,
model)`` pair first, then falls back to the longest matching ``model`` *prefix*
within the same provider (e.g. ``claude-opus-4-8`` matches the ``claude-opus``
entry). An unknown provider/model resolves to ``0.0`` and never raises.

SUBSCRIPTION WORK IS PRICED, AND SAID TO BE AN EQUIVALENT (v1.300.0). A run
served by the logged-in ``claude`` CLI (``claude-cli``, which is also what a
keyless ``anthropic`` is served through and what the router then reports)
used to price at ``0.0`` — so a custom agent's DOLLAR allowance never counted
subscription work and the Usage page showed $0 for it. ``claude-cli`` now
prices against the Anthropic rows by model family (:data:`PRICE_ALIASES`,
:data:`_MODEL_ALIASES`). Those figures are LIST-PRICE EQUIVALENTS for a flat
subscription, not a charge: :func:`is_list_price_equivalent` says so for every
surface that shows them.

:func:`step_cost` is what a run adds per completion: the adapter's own
``usage["cost_usd"]`` when it reported one (the Claude CLI reports
``total_cost_usd`` per call), else :func:`cost_for_usage` over the tokens —
cache-aware when the usage carries cache keys (reads at
:data:`CACHE_READ_MULTIPLIER` of the input price, 5-minute writes at
:data:`CACHE_WRITE_MULTIPLIER`, unless the row names its own ``cache_read`` /
``cache_write`` price; the Claude CLI writes 1-HOUR cache, so ``claude-cli``
writes are priced at :data:`CACHE_WRITE_1H_MULTIPLIER`). :class:`UsageTally`
sums a turn's completions.

WHAT IS AN EQUIVALENT, AND WHAT IS MONEY (v1.300.0 review follow-ups).
:func:`is_list_price_equivalent` is MODEL-aware: a ``claude-cli`` model the
live picker marks ``usage_credits`` (Fable on every plan but Max, or a picker
row that says "usage credit") draws pay-as-you-go credits — real money — so
it is NOT an equivalent and lands in billed totals. The catalog read is
``claude_models``' non-spawning one; pricing never starts a process.
:func:`recorded_cost` prices an OLD subscription row (no stored cost) at 0:
before v1.300.0 that work was free by the rules then in force, and re-pricing
it would trip an agent's dollar allowance the day the upgrade installs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

#: ``(provider, model_or_prefix) -> {"input": $/1M tokens, "output": $/1M tokens}``.
#: Prices are approximate list prices and meant to be edited in place. Keys are
#: lowercased; ``model`` may be a full id or a prefix (longest prefix wins).
PRICES: dict[tuple[str, str], dict[str, float]] = {
    # --- Anthropic Claude (per-1M-token list prices) ---------------------
    ("anthropic", "claude-fable-5"): {"input": 10.0, "output": 50.0},
    ("anthropic", "claude-mythos-5"): {"input": 10.0, "output": 50.0},
    ("anthropic", "claude-opus"): {"input": 5.0, "output": 25.0},
    ("anthropic", "claude-sonnet"): {"input": 3.0, "output": 15.0},
    ("anthropic", "claude-haiku"): {"input": 1.0, "output": 5.0},
    # Claude 5.x generation (v1.300.0). Longest prefix wins, so these sit
    # beside the family rows above: Opus 5.5 is cheaper than the Opus line and
    # reads its cache at its own rate; Fable 5.1 keeps Fable 5's token price but
    # reads cache at $0.25; Sonnet 5 is cheaper than Sonnet 4.6 (the generic
    # ``claude-sonnet`` row). Sonnet 5.5 has no published price here yet — it
    # matches ``claude-sonnet-5``; edit when it does. When these serve a flat
    # subscription (``claude-cli``) the figure is a LIST-PRICE EQUIVALENT, not
    # a charge (see :func:`is_list_price_equivalent`).
    ("anthropic", "claude-opus-5-5"): {"input": 4.0, "output": 20.0, "cache_read": 0.20},
    ("anthropic", "claude-fable-5-1"): {"input": 10.0, "output": 50.0, "cache_read": 0.25},
    ("anthropic", "claude-sonnet-5"): {"input": 2.0, "output": 10.0},
    # Older Claude 3.x families (still seen in historical rows).
    ("anthropic", "claude-3-opus"): {"input": 15.0, "output": 75.0},
    ("anthropic", "claude-3-5-sonnet"): {"input": 3.0, "output": 15.0},
    ("anthropic", "claude-3-haiku"): {"input": 0.25, "output": 1.25},
    # --- OpenAI GPT (approximate) ----------------------------------------
    ("openai", "gpt-4o-mini"): {"input": 0.15, "output": 0.60},
    ("openai", "gpt-4o"): {"input": 2.5, "output": 10.0},
    ("openai", "gpt-4-turbo"): {"input": 10.0, "output": 30.0},
    ("openai", "gpt-4"): {"input": 30.0, "output": 60.0},
    ("openai", "gpt-3.5"): {"input": 0.5, "output": 1.5},
    ("openai", "gpt-5"): {"input": 1.25, "output": 10.0},
    ("openai", "gpt"): {"input": 2.5, "output": 10.0},  # generic gpt-* fallback
    # --- Google Gemini (approximate) -------------------------------------
    ("google", "gemini-1.5-flash"): {"input": 0.075, "output": 0.30},
    ("google", "gemini-1.5-pro"): {"input": 1.25, "output": 5.0},
    ("google", "gemini-2.0-flash"): {"input": 0.10, "output": 0.40},
    ("google", "gemini-2.5-pro"): {"input": 1.25, "output": 10.0},
    ("google", "gemini"): {"input": 1.25, "output": 5.0},  # generic gemini-* fallback
    # --- Local / offline providers cost nothing --------------------------
    ("mock", ""): {"input": 0.0, "output": 0.0},
    ("ollama", ""): {"input": 0.0, "output": 0.0},
}

#: Per-1M scaling: prices are quoted per 1,000,000 tokens.
_PER_TOKENS = 1_000_000

#: A provider that serves ANOTHER vendor's models prices on that vendor's rows
#: (v1.300.0). ``claude-cli`` is the logged-in Claude subscription: its figure
#: is a list-price EQUIVALENT (:data:`LIST_PRICE_EQUIVALENT_PROVIDERS`).
PRICE_ALIASES: dict[str, str] = {"claude-cli": "anthropic"}

#: Providers whose priced figure is a list-price EQUIVALENT of work a flat
#: subscription covers — never a charge. Surfaces say "included in your
#: subscription" beside it.
LIST_PRICE_EQUIVALENT_PROVIDERS = frozenset({"claude-cli"})

#: Bare CLI model aliases (``claude --model opus``) -> the family row.
_MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus",
    "sonnet": "claude-sonnet",
    "haiku": "claude-haiku",
    "fable": "claude-fable-5",
    "mythos": "claude-mythos-5",
}

#: Prompt-cache multipliers on the INPUT price (Anthropic's published shape):
#: a cache read costs 10%, a 5-minute cache write 125%. A row may name its own
#: ``cache_read`` / ``cache_write`` $/1M instead (Opus 5.5, Fable 5.1).
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25
#: A 1-HOUR cache write costs 200% of the input price. The Claude CLI writes
#: 1-hour cache (its requests carry ``cache_control: {type: "ephemeral",
#: ttl: "1h"}``; live Sonnet figures solve to 2x), so its table FALLBACK uses
#: this rate (a row may name ``cache_write_1h``). The API keeps the 5-minute
#: rate. Only a fallback: the CLI's own ``total_cost_usd`` wins when present.
CACHE_WRITE_1H_MULTIPLIER = 2.0
#: Providers whose cache writes are 1-hour writes.
ONE_HOUR_CACHE_PROVIDERS = frozenset({"claude-cli"})


def _match(provider: str, model: str) -> dict[str, float] | None:
    """Return the price entry for ``provider``/``model``, best-effort.

    Tries an exact ``(provider, model)`` match, then the longest ``model``
    prefix registered for that provider. Returns ``None`` if nothing matches.
    """
    provider = (provider or "").lower().strip()
    model = (model or "").lower().strip()
    # A provider that serves another vendor's models prices on that vendor's
    # rows (v1.300.0): the Claude CLI's subscription is priced at Anthropic's
    # list price, as an EQUIVALENT. Bare CLI aliases name a family.
    provider = PRICE_ALIASES.get(provider, provider)
    if provider == "anthropic":
        model = _MODEL_ALIASES.get(model, model)

    exact = PRICES.get((provider, model))
    if exact is not None:
        return exact

    # Longest matching prefix within the same provider wins (so that
    # "claude-opus-4-8" prefers the "claude-opus" entry over a broader one).
    best: dict[str, float] | None = None
    best_len = -1
    for (p, key), price in PRICES.items():
        if p != provider:
            continue
        if model.startswith(key) and len(key) > best_len:
            best, best_len = price, len(key)
    return best


def _tok(usage: Mapping, key: str) -> int:
    """A non-negative int off ``usage[key]``; anything unreadable is 0."""
    try:
        value = usage.get(key, 0)
        if isinstance(value, bool):
            return 0
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _priced(price: Mapping, usage: Mapping, *, one_hour_cache: bool = False) -> float:
    """USD for one completion's ``usage`` against one price row.

    THE CONTRACT (``LLMResponse.usage``): ``input_tokens`` is the TOTAL prompt,
    and ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` are PARTS
    of it. The uncached remainder pays the full input price. A usage whose
    cache parts exceed the total (an adapter reporting the raw Messages API
    shape, where ``input_tokens`` is already the uncached remainder) is read
    that way instead of going negative.
    """
    total_in = _tok(usage, "input_tokens")
    out = _tok(usage, "output_tokens")
    read = _tok(usage, "cache_read_input_tokens")
    write = _tok(usage, "cache_creation_input_tokens")
    uncached = total_in if read + write > total_in else total_in - read - write
    in_price = float(price.get("input", 0.0))
    read_price = float(price.get("cache_read", in_price * CACHE_READ_MULTIPLIER))
    if one_hour_cache:
        write_price = float(price.get("cache_write_1h", in_price * CACHE_WRITE_1H_MULTIPLIER))
    else:
        write_price = float(price.get("cache_write", in_price * CACHE_WRITE_MULTIPLIER))
    cost = (
        uncached * in_price
        + read * read_price
        + write * write_price
        + out * float(price.get("output", 0.0))
    ) / _PER_TOKENS
    return float(cost)


def cost_for(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    """Estimate USD cost for a run's token usage. Never raises.

    Best-effort match by ``provider`` + ``model`` prefix against
    :data:`PRICES`. Unknown provider/model (or non-numeric/negative token
    counts) resolve to ``0.0`` rather than raising — cost analytics must never
    crash the runtime. Cache-blind: a caller holding a whole usage dict uses
    :func:`cost_for_usage` (or :func:`step_cost`).
    """
    try:
        price = _match(provider, model)
        if price is None:
            return 0.0
        in_tok = max(0, int(input_tokens or 0))
        out_tok = max(0, int(output_tokens or 0))
        return _priced(price, {"input_tokens": in_tok, "output_tokens": out_tok})
    except (TypeError, ValueError):
        return 0.0


def cost_for_usage(provider: str, model: str, usage: Mapping | None) -> float:
    """:func:`cost_for` over a whole usage dict, CACHE-AWARE. Never raises.

    Cache reads cost :data:`CACHE_READ_MULTIPLIER` of the input price and
    cache writes :data:`CACHE_WRITE_MULTIPLIER` (the 5-minute write), unless
    the matched row names its own ``cache_read`` / ``cache_write`` price. A
    provider in :data:`ONE_HOUR_CACHE_PROVIDERS` (the Claude CLI) writes
    1-hour cache: :data:`CACHE_WRITE_1H_MULTIPLIER` (or ``cache_write_1h``).
    """
    try:
        if not isinstance(usage, Mapping):
            return 0.0
        price = _match(provider, model)
        if price is None:
            return 0.0
        one_hour = (provider or "").lower().strip() in ONE_HOUR_CACHE_PROVIDERS
        return _priced(price, usage, one_hour_cache=one_hour)
    except Exception:  # noqa: BLE001 — cost analytics must never crash a run
        return 0.0


def native_cost(usage: Mapping | None) -> float | None:
    """The adapter's OWN ``usage["cost_usd"]`` when it is a finite number
    ``>= 0``, else ``None`` (absent, a bool, NaN, negative, unreadable)."""
    if not isinstance(usage, Mapping) or "cost_usd" not in usage:
        return None
    value = usage.get("cost_usd")
    if value is None or isinstance(value, bool):
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(cost) or cost < 0:
        return None
    return cost


def step_cost(provider: str, model: str, usage: Mapping | None) -> float:
    """What ONE completion adds to a run's ``cost_usd``. Never raises.

    The adapter's own figure wins (:func:`native_cost` — the Claude CLI's
    ``total_cost_usd``); otherwise :func:`cost_for_usage` prices the tokens.
    """
    native = native_cost(usage)
    if native is not None:
        return native
    return cost_for_usage(provider, model, usage)


def recorded_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int, stored: Any
) -> float:
    """A RECORDED row's dollars (v1.300.0): its stored ``cost_usd`` when that
    is a finite number > 0 (the sum of its completions' :func:`step_cost`),
    else :func:`cost_for` over its tokens — a METERED row older than the
    column reads NULL/0 and is priced exactly as before. Never raises. The ONE
    rule for every reader of Session/AgentRun spend (an agent's allowance, a
    goal's budget, the goal digest, the usage rollup).

    UPGRADE DAY: a :func:`is_list_price_equivalent` row with NO stored cost
    is 0.0, never re-priced from its tokens. Before v1.300.0 subscription work
    was free by the rules then in force; every new row stores the CLI's own
    figure (or :func:`step_cost`'s table fallback) when it is written. A
    usage-credits row is money, so it is priced like any metered row."""
    try:
        value = float(stored or 0.0)
    except (TypeError, ValueError, OverflowError):
        value = 0.0
    if value > 0 and math.isfinite(value):
        return value
    if is_list_price_equivalent(provider, model):
        return 0.0
    return cost_for(provider, model, input_tokens, output_tokens)


def draws_usage_credits(model: str) -> bool:
    """Does the Claude CLI bill ``model`` to pay-as-you-go USAGE CREDITS?

    Reads the live picker's ``usage_credits`` mark through ``claude_models``'
    NON-spawning read (``_lookup`` / ``_default_row`` over ``_known()``:
    in-process, else disk, else the pinned table — which never claims
    credits). The CLI's default ids (``""``, ``subscription``, ``default``,
    ``auto``) resolve to the picker's default row. Never raises and never
    starts a process; anything unreadable is False."""
    try:
        from ..providers import claude_models

        name = str(model or "").strip()
        if name.lower() in claude_models.DEFAULT_IDS:
            row = claude_models._default_row()
        else:
            _canon, row = claude_models._lookup(name)
        return bool(isinstance(row, Mapping) and row.get("usage_credits"))
    except Exception:  # noqa: BLE001 — a catalog hiccup costs the refinement only
        return False


def is_list_price_equivalent(provider: str, model: str = "") -> bool:
    """True when ``provider``/``model``'s figure is a LIST-PRICE EQUIVALENT of
    work a flat subscription already covers — shown, and counted against an
    agent's allowance, but never billed.

    MODEL-AWARE: a subscription model that draws pay-as-you-go usage credits
    (:func:`draws_usage_credits`) is real money, so it is NOT an equivalent —
    it lands in billed totals and fleet cloud spend, and the receipt does not
    say "list". ``model`` omitted means the CLI's own default. Never raises."""
    try:
        if str(provider or "").lower().strip() not in LIST_PRICE_EQUIVALENT_PROVIDERS:
            return False
    except Exception:  # noqa: BLE001
        return False
    return not draws_usage_credits(model)


def is_priced(provider: str, model: str) -> bool:
    """Does :data:`PRICES` know this provider/model at all?"""
    try:
        return _match(provider, model) is not None
    except Exception:  # noqa: BLE001
        return False


class UsageTally:
    """A turn's usage summed over its completions (v1.300.0).

    :meth:`add` takes each completion's ``provider``/``model``/``usage`` and
    returns the :func:`step_cost` it added; :meth:`as_dict` is the receipt's
    ``usage`` object — ``input_tokens`` and ``output_tokens`` always, the cache
    counts only when some completion reported them, ``cost_usd`` only when
    some completion was priced (a native figure or a known price row), and
    ``list_price_equivalent: true`` only when a subscription served it. Only
    keys that are KNOWN — an unknown is absent, never a fabricated 0.
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cost_usd = 0.0
        self.cache_known = False
        self.cost_known = False
        self.list_price_equivalent = False

    def add(self, provider: str, model: str, usage: Mapping | None) -> float:
        if not isinstance(usage, Mapping):
            usage = {}
        self.input_tokens += _tok(usage, "input_tokens")
        self.output_tokens += _tok(usage, "output_tokens")
        if "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
            self.cache_known = True
            self.cache_read_input_tokens += _tok(usage, "cache_read_input_tokens")
            self.cache_creation_input_tokens += _tok(usage, "cache_creation_input_tokens")
        cost = step_cost(provider, model, usage)
        self.cost_usd += cost
        if native_cost(usage) is not None or is_priced(provider, model):
            self.cost_known = True
        if is_list_price_equivalent(provider, model):
            self.list_price_equivalent = True
        return cost

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        if self.cache_known:
            out["cache_read_input_tokens"] = self.cache_read_input_tokens
            out["cache_creation_input_tokens"] = self.cache_creation_input_tokens
        if self.cost_known:
            out["cost_usd"] = round(self.cost_usd, 6)
        if self.list_price_equivalent:
            out["list_price_equivalent"] = True
        return out
