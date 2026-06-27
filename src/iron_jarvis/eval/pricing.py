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
"""

from __future__ import annotations

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


def _match(provider: str, model: str) -> dict[str, float] | None:
    """Return the price entry for ``provider``/``model``, best-effort.

    Tries an exact ``(provider, model)`` match, then the longest ``model``
    prefix registered for that provider. Returns ``None`` if nothing matches.
    """
    provider = (provider or "").lower().strip()
    model = (model or "").lower().strip()

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


def cost_for(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    """Estimate USD cost for a run's token usage. Never raises.

    Best-effort match by ``provider`` + ``model`` prefix against
    :data:`PRICES`. Unknown provider/model (or non-numeric/negative token
    counts) resolve to ``0.0`` rather than raising — cost analytics must never
    crash the runtime.
    """
    try:
        price = _match(provider, model)
        if price is None:
            return 0.0
        in_tok = max(0, int(input_tokens or 0))
        out_tok = max(0, int(output_tokens or 0))
        cost = (
            in_tok * price.get("input", 0.0) + out_tok * price.get("output", 0.0)
        ) / _PER_TOKENS
        return float(cost)
    except (TypeError, ValueError):
        return 0.0
