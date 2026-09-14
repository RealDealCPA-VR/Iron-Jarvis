"""The reasoning level — WHICH models offer one and what each provider calls it.

v1.263.0. The user: "In the chat module I should be able to select the reasoning
level of the model if it is an option." Every vendor spells the same knob
differently — OpenAI ``reasoning_effort`` (chat completions) / ``reasoning:
{effort}`` (Responses), Anthropic extended thinking with a ``budget_tokens``,
Gemini a ``thinkingBudget``, the Claude CLI ``--effort``, the Codex CLI ``-c
model_reasoning_effort=`` — and most models have no such knob at all. This
module is the ONE place that says which (provider, model) pairs offer the
choice, so the chat composer can show the control only where it does something,
and the adapters can translate one vocabulary (``low`` / ``medium`` / ``high``)
into their own.

TWO RULES, both about honesty:

* **Absent means unsupported, never "send it and hope".** A level is offered
  only for a family known to take one. An OpenAI-compatible local endpoint gets
  the control for the reasoning families it may serve (gpt-oss, DeepSeek-R1,
  Qwen3, QwQ, Magistral, GLM-4.5/4.6, Kimi-K2-thinking, "-thinking" ids); the
  adapter still retries WITHOUT the parameter when a server refuses it, so an
  id that matched by name but not by behaviour costs one retry, not a turn.
* **The receipt says what was APPLIED.** The router reports the level on the
  route only when the adapter that actually answered supports it — a failover
  to a model with no knob reports "" and the receipt says nothing, rather than
  naming a level that never reached the wire.
"""

from __future__ import annotations

import re

#: The vocabulary the composer offers and every adapter translates. ``""`` is
#: the provider's own default — nothing sent, byte-identical to before.
LEVELS: tuple[str, ...] = ("low", "medium", "high")

#: Anthropic / Gemini thinking budgets per level, in tokens. Low is enough to
#: plan a short answer; high is the vendors' documented "deep" band.
BUDGET_TOKENS: dict[str, int] = {"low": 2048, "medium": 8192, "high": 24576}

_ANTHROPIC_NO_THINKING = re.compile(r"^claude-(2|3-opus|3-5|3-sonnet|3-haiku|instant)", re.I)
_OPENAI_REASONING = re.compile(r"^(o[1345](-|$)|gpt-5|codex|gpt-oss)", re.I)
_GEMINI_THINKING = re.compile(r"gemini-(2\.5|3)", re.I)
#: Reasoning families an OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM, a
#: fleet node) may serve. Matched anywhere in the id, case-insensitively.
_LOCAL_REASONING = re.compile(
    r"gpt-oss|deepseek-r1|deepseek-reasoner|deepseek-v3\.1|qwen3|qwq|magistral"
    r"|glm-4\.[56]|kimi-k2-thinking|-thinking|nemotron.*reason|\bo[13](-mini)?\b|gpt-5",
    re.I,
)


def normalize_level(value: object) -> str:
    """``low`` / ``medium`` / ``high`` or ``""`` — never anything else on the wire."""
    text = str(value or "").strip().lower()
    return text if text in LEVELS else ""


def reasoning_levels(provider: str, model: str) -> tuple[str, ...]:
    """The levels a (provider, model) pair offers; ``()`` when it offers none."""
    prov = (provider or "").strip().lower()
    mid = (model or "").strip()
    low = mid.lower()
    if not prov:
        return ()
    if prov == "anthropic":
        return () if (not low.startswith("claude") or _ANTHROPIC_NO_THINKING.match(low)) else LEVELS
    if prov in ("claude-cli", "codex-cli"):
        return LEVELS
    if prov == "openai":
        return LEVELS if _OPENAI_REASONING.match(low) else ()
    if prov == "google":
        return LEVELS if _GEMINI_THINKING.search(low) else ()
    if prov == "openrouter":
        vendor, _, name = low.partition("/")
        if vendor == "anthropic":
            return () if _ANTHROPIC_NO_THINKING.match(name) else LEVELS
        if vendor == "openai":
            return LEVELS if _OPENAI_REASONING.match(name) else ()
        if vendor == "google":
            return LEVELS if _GEMINI_THINKING.search(name) else ()
        return LEVELS if _LOCAL_REASONING.search(name or low) else ()
    if prov in ("ollama", "custom", "local") or prov.startswith("fleet-"):
        return LEVELS if _LOCAL_REASONING.search(low) else ()
    return ()


def supports(provider: str, model: str, level: str) -> bool:
    return bool(level) and level in reasoning_levels(provider, model)


def budget_tokens(level: str) -> int:
    """The thinking budget for a level (Anthropic / Gemini), 0 for none."""
    return BUDGET_TOKENS.get(normalize_level(level), 0)
