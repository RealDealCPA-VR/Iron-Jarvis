"""Auto model routing (§6 — the routing model).

When the user selects **Auto**, a cheap user-chosen *routing model* classifies each
request into a difficulty TIER (light / standard / heavy) and the router sends it
to the best CONNECTED model for that tier. This module is the pure-logic half:

* a cost/capability RANK for known model families (cheaper/lighter = lower),
* :func:`cheapest` — the suggested routing model (typically the cheapest),
* :func:`derive_tiers` — a sensible light/standard/heavy mapping over whatever is
  connected,
* a lightweight :func:`heuristic_tier` pre-pass (so obvious requests skip the
  classifier call entirely) and the classifier prompt + :func:`parse_tier`.

No I/O, no adapters — trivially testable. The router (``ModelRouter``) and the
platform wire these into a live ``auto_route`` callable.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable


class LatencyTracker:
    """EWMA of successful-completion latency per ``(provider, model)``.

    Used as a SECONDARY signal: when Auto has multiple equally-cheap candidates
    for a tier, the faster-observed one wins. Pure in-memory, process-local, and
    self-contained — no persistence, no I/O. ``alpha`` weights the newest sample
    (0.3 => a moderately sticky average that still tracks a provider slowing
    down). Unknown pairs return ``None`` so callers can treat "no data" as
    neutral rather than infinitely slow.
    """

    def __init__(self, alpha: float = 0.3) -> None:
        self._alpha = alpha
        self._ewma: dict[tuple[str, str], float] = {}

    def record(self, provider: str, model: str, seconds: float) -> None:
        if seconds < 0:
            return
        key = (str(provider), str(model or ""))
        prev = self._ewma.get(key)
        self._ewma[key] = seconds if prev is None else (
            self._alpha * seconds + (1 - self._alpha) * prev
        )

    def ewma(self, provider: str, model: str) -> float | None:
        return self._ewma.get((str(provider), str(model or "")))


#: Process-global latency tracker. The router records into it on every
#: successful ``complete()``; :func:`derive_tiers` reads it (via an injected
#: accessor) to break ties among equally-cheap tier candidates.
LATENCY = LatencyTracker()

#: The three difficulty tiers, cheapest → most capable.
TIERS = ("light", "standard", "heavy")

#: Cost/capability rank by model-id SUBSTRING (checked longest-first). Lower =
#: cheaper & lighter. Flat-rate subscription CLIs are treated as cheap (they cost
#: nothing marginal), which makes them great default routing models.
_RANK_RULES: tuple[tuple[str, int], ...] = (
    ("mock", 0),
    ("subscription", 1),      # claude-cli / codex-cli — flat-rate, $0 marginal
    ("mini", 1), ("nano", 1), ("flash", 1), ("haiku", 1), ("fable", 1),
    ("fast", 1), ("lite", 1), ("small", 1), ("8b", 1), ("7b", 1),
    ("openrouter/auto", 1),
    ("sonnet", 2), ("gpt-4o", 2), ("gpt-4.1", 2), ("1.5-pro", 2),
    ("grok-build", 2), ("code", 2), ("medium", 2), ("13b", 2), ("mixtral", 2),
    ("opus", 3), ("gpt-5", 3), ("grok-4", 3), ("ultra", 3), ("70b", 3),
    ("large", 3), ("o1", 3), ("o3", 3),
)

#: Rank for a model whose id matches none of the rules — assume mid-tier.
_DEFAULT_RANK = 2

#: Tie-break preference among equally-cheap models for the SUGGESTED routing
#: model: a flat-rate CLI or a local model first, then metered minis.
_SUGGEST_PREFERENCE = (
    "claude-cli", "codex-cli", "ollama", "custom", "openrouter",
    "google", "openai", "anthropic", "xai",
)


def model_rank(provider: str, model: str) -> int:
    """Cost/capability rank of a ``(provider, model)`` — lower is cheaper/lighter.

    A model that advertises a CHEAP tier suffix (mini / nano / flash / fast /
    haiku / lite …) is cheap regardless of its base family, so we take the MINIMUM
    rank among every matched token: "gpt-4o-mini" matches both "gpt-4o" (2) and
    "mini" (1) → 1, while a bare "gpt-4o" stays 2.
    """
    hay = f"{provider} {model}".lower()
    matched = [rank for token, rank in _RANK_RULES if token in hay]
    return min(matched) if matched else _DEFAULT_RANK


def _pm(entry: Any) -> "tuple[str, str] | None":
    """Coerce a connected-model entry ({'provider','model'} or a (p,m) tuple)."""
    if isinstance(entry, dict):
        p, m = entry.get("provider"), entry.get("model")
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        p, m = entry
    else:
        return None
    if not p:
        return None
    return str(p), str(m or "")


def _real(connected: Iterable[Any]) -> list[tuple[str, str]]:
    """Connected (provider, model) pairs, excluding the offline mock."""
    out: list[tuple[str, str]] = []
    for e in connected:
        pm = _pm(e)
        if pm and pm[0] != "mock":
            out.append(pm)
    return out


def connected_real_models(provider_manager: Any, config: Any) -> list[dict]:
    """Live pool of connected, real (non-mock) ``{provider, model}`` options that
    Auto routes among + derives its tiers from. Shared by the platform's
    ``auto_route`` and the ``/routing`` endpoint so both see the same set."""
    from ..agents.dynamic import available_models

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in available_models():
        p, mm = m.get("provider"), m.get("model")
        try:
            if p == "mock" or not provider_manager.available(p):
                continue
        except Exception:  # noqa: BLE001 — a probe failure just skips that entry
            continue
        key = (str(p), str(mm))
        if key in seen:
            continue
        seen.add(key)
        out.append({"provider": p, "model": mm})
    try:
        if getattr(config, "ollama_base_url", None) and provider_manager.available("ollama"):
            out.append({"provider": "ollama", "model": config.ollama_model})
        if getattr(config, "custom_base_url", None) and provider_manager.available("custom"):
            out.append({"provider": "custom", "model": config.custom_model or "default"})
    except Exception:  # noqa: BLE001
        pass
    return out


def cheapest(connected: Iterable[Any]) -> "tuple[str, str] | None":
    """The suggested routing model: the cheapest connected model, breaking ties by
    :data:`_SUGGEST_PREFERENCE`. ``None`` when nothing real is connected."""
    reals = _real(connected)
    if not reals:
        return None

    def key(pm: tuple[str, str]) -> tuple[int, int]:
        prov = pm[0]
        pref = _SUGGEST_PREFERENCE.index(prov) if prov in _SUGGEST_PREFERENCE else 99
        return (model_rank(*pm), pref)

    return min(reals, key=key)


def derive_tiers(
    connected: Iterable[Any],
    latency: Callable[[str, str], "float | None"] | None = None,
) -> dict[str, tuple[str, str]]:
    """A light/standard/heavy mapping over the connected models: light = cheapest,
    heavy = most capable, standard = something in between (falling back to the
    neighbours when only one or two distinct models are connected).

    LATENCY-AWARE (opt-in): when ``latency`` is supplied it is a SECONDARY sort
    key after cost rank, so among several equally-cheap models the faster-
    observed one is chosen for its tier. With ``latency=None`` (the default) the
    ordering is byte-for-byte as before — existing callers/tests are unaffected.
    """
    reals = _real(connected)
    if not reals:
        return {}

    def sort_key(pm: tuple[str, str]) -> tuple[float, float]:
        rank = float(model_rank(*pm))
        if latency is None:
            return (rank, 0.0)
        # No data => neutral (0.0), so an unmeasured model is neither favoured
        # nor penalised versus another of the same rank until a sample lands.
        lat = latency(*pm)
        return (rank, lat if lat is not None else 0.0)

    ranked = sorted(reals, key=sort_key)
    light = ranked[0]
    heavy = ranked[-1]
    # Prefer a genuine mid model; else the one just above light; else light.
    mids = [pm for pm in ranked if model_rank(*pm) == 2]
    standard = mids[0] if mids else (ranked[len(ranked) // 2] if len(ranked) > 1 else light)
    return {"light": light, "standard": standard, "heavy": heavy}


def parse_pm(value: str) -> "tuple[str, str] | None":
    """Parse a ``"provider:model"`` (or ``"provider"``) string."""
    if not value or ":" not in value:
        return (value.strip(), "") if value and value.strip() else None
    prov, _, model = value.partition(":")
    prov = prov.strip()
    return (prov, model.strip()) if prov else None


def format_pm(pm: "tuple[str, str] | None") -> str:
    if not pm:
        return ""
    return f"{pm[0]}:{pm[1]}" if pm[1] else pm[0]


def parse_tiers_json(raw: str) -> dict[str, tuple[str, str]]:
    """Parse a ``routing_tiers_json`` override into {tier: (provider, model)}."""
    import json

    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    out: dict[str, tuple[str, str]] = {}
    if isinstance(data, dict):
        for tier in TIERS:
            pm = parse_pm(str(data.get(tier, "")))
            if pm:
                out[tier] = pm
    return out


# --------------------------------------------------------------------------- #
# Classification.
# --------------------------------------------------------------------------- #
CLASSIFY_SYSTEM = (
    "You are a request router. Read the user's request and reply with EXACTLY ONE "
    "word — its difficulty tier:\n"
    "  light    = quick facts, chit-chat, formatting, tiny edits.\n"
    "  standard = moderate reasoning, short code, summaries, normal tasks.\n"
    "  heavy    = deep reasoning, long/complex code, architecture, multi-step "
    "planning, or anything with tools/agents.\n"
    "Answer with only: light, standard, or heavy."
)


def classify_input(messages: list[Any], *, max_chars: int = 2000) -> str:
    """The compact text handed to the routing model — the latest user turn(s)."""
    parts: list[str] = []
    for m in reversed(messages):
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "")
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
        if role == "user" and content:
            parts.append(str(content))
            break
    if not parts:  # no user turn — use whatever last message there is
        for m in reversed(messages):
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            if content:
                parts.append(str(content))
                break
    return " ".join(parts)[:max_chars]


_WORD_RE = re.compile(r"\b(light|standard|heavy)\b", re.I)


def parse_tier(text: str, default: str = "standard") -> str:
    """Extract the tier word from a classifier reply; ``default`` when unclear."""
    m = _WORD_RE.search(text or "")
    return m.group(1).lower() if m else default


#: Task classes that are inherently heavy (real agent work / coding) — skip the
#: classifier and route heavy directly.
_HEAVY_TASK_CLASSES = frozenset({"builder", "maintainer", "supervisor", "automation"})


def heuristic_tier(
    messages: list[Any],
    tools: list[Any] | None,
    task_class: str | None,
) -> "str | None":
    """A zero-cost pre-pass that decides the OBVIOUS cases so they skip the
    classifier call. Returns a tier, or ``None`` when the routing model should
    decide.

    * tools armed, or an agent/coding task class -> ``heavy``.
    * a single short user message with no code markers -> ``light``.
    * otherwise -> ``None`` (ambiguous; ask the routing model).
    """
    if tools:
        return "heavy"
    if task_class and task_class.lower() in _HEAVY_TASK_CLASSES:
        return "heavy"
    text = classify_input(messages)
    if not text:
        return "light"
    low = text.lower()
    code_markers = ("```", "def ", "class ", "function ", "import ", "select ", "npm ", "git ")
    # Signals a request is NON-trivial even when it's short — leave it to the
    # routing model rather than shortcutting to light.
    complexity = (
        "explain", "why", "how ", "design", "architect", "depth", "compare",
        "tradeoff", "trade-off", "analy", "implement", "debug", "optimi",
        "refactor", "plan", "strateg", "prove", "derive", "review", "in detail",
        "step by step", "pros and cons",
    )
    # ONLY the obviously trivial fast-paths to light: very short, single-line, no
    # code, no complexity words. Everything else asks the routing model.
    if (
        len(text) <= 80
        and text.count("\n") == 0
        and not any(mk in low for mk in code_markers)
        and not any(w in low for w in complexity)
    ):
        return "light"
    return None
