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
#: GPT-6 (v1.330.0): every gpt-6 id OpenAI lists documents reasoning.effort
#: (developers.openai.com/api/docs/models/<id>, read 2026-10-10:
#: gpt-6-astra, gpt-6-sol, gpt-6-luna, gpt-6.1-sol).
_OPENAI_REASONING = re.compile(r"^(o[1345](-|$)|gpt-5|gpt-6([.-]|$)|codex|gpt-oss)", re.I)
_GEMINI_THINKING = re.compile(r"gemini-(2\.5|3)", re.I)
#: Reasoning families an OpenAI-compatible endpoint (Ollama, vLLM, LiteLLM, a
#: fleet node) may serve. Matched anywhere in the id, case-insensitively.
_LOCAL_REASONING = re.compile(
    r"gpt-oss|deepseek-r1|deepseek-reasoner|deepseek-v3\.1|qwen3|qwq|magistral"
    r"|glm-4\.[56]|kimi-k2-thinking|-thinking|nemotron.*reason|\bo[13](-mini)?\b|gpt-5|gpt-6",
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


# --------------------------------------------------------------------------- #
# v1.330.0: WHICH Anthropic thinking spelling a Claude model takes
# --------------------------------------------------------------------------- #
#
# Anthropic's Messages API has two spellings of "think this hard", and each
# model takes only some of them. Read 2026-10-10 from Anthropic's own docs:
#
#   platform.claude.com/docs/en/build-with-claude/extended-thinking:
#     "Extended thinking (`thinking.type: "enabled"` with `budget_tokens`) is
#      deprecated on the Claude 4.6 models (requests using it still succeed).
#      Claude 4.7 and later models do not support it and reject requests that
#      use it, returning a 400 error. On Claude 4.5 and earlier models that
#      support thinking, extended thinking is the only available thinking
#      mode. Claude Mythos Preview supports both modes. Where both modes are
#      available, use adaptive thinking instead."
#     "The mapping is small: remove `budget_tokens`, set `thinking: {type:
#      "adaptive"}`, and control reasoning depth with `output_config:
#      {effort: ...}` instead of a token budget."
#   .../thinking-troubleshooting (per-model table): "Extended only" for
#     Claude Opus 4.5, Claude Haiku 4.5, Claude Sonnet 4.5 (each rejects
#     "adaptive" with a 400); "Earlier Claude 4 models (Claude Opus 4.1,
#     Claude Sonnet 4, and Claude Opus 4) support extended thinking only."
#     Every other row (Fable 5.1, Mythos 5.1, Fable 5, Mythos 5, Mythos
#     Preview, Opus 5.5, Opus 5, Opus 4.8, Opus 4.7, Sonnet 5.5, Sonnet 5,
#     Haiku 5.5, Opus 4.6, Sonnet 4.6) takes "adaptive".
#   .../effort: `output_config.effort` takes low / medium / high on every
#     one of those adaptive models (its supportedModels list).
#
# So the BUDGET list is the closed one: no new model will ever be "4.5 or
# earlier", while every model released since 4.7 rejects a budget. A Claude
# id not in the list gets the adaptive spelling. Claude 3.7 Sonnet is the
# first thinking model and took a budget only (it predates "adaptive").
BUDGET = "budget"
ADAPTIVE = "adaptive"

#: Bare ids (``_bare_claude``: no date, no ``[1m]``, no ``-latest``) of the
#: Claude models that take ONLY ``thinking: {type: "enabled", budget_tokens}``.
_ANTHROPIC_BUDGET_THINKING: frozenset[str] = frozenset(
    {
        "claude-3-7-sonnet",
        "claude-sonnet-4",
        "claude-sonnet-4-0",
        "claude-opus-4",
        "claude-opus-4-0",
        "claude-opus-4-1",
        "claude-sonnet-4-5",
        "claude-opus-4-5",
        "claude-haiku-4-5",
    }
)


def anthropic_thinking_mode(model: str) -> str:
    """``budget`` for the Claude models that take only a thinking budget
    (4.5 and earlier), ``adaptive`` for every other Claude model (4.6 and
    later, Fable, Mythos): ``thinking: {type: "adaptive"}`` plus
    ``output_config.effort``. Only the Anthropic adapter asks."""
    low = (model or "").strip().lower()
    return BUDGET if _bare_claude(low) in _ANTHROPIC_BUDGET_THINKING else ADAPTIVE


# --------------------------------------------------------------------------- #
# v1.330.0: the level a model runs at when NOTHING is sent
# --------------------------------------------------------------------------- #
#
# The composer's chip used to read "Reasoning" with no pick, so the user could
# not tell what level the model would use. ``reasoning_default`` answers that
# question, under the same honesty rule as the table above: ABSENT ("") MEANS
# UNKNOWN, NEVER A GUESS. A value is given only where the vendor DOCUMENTS the
# default for that exact model, and only for the route Iron Jarvis really
# takes (an API key's request, which sends nothing when no level is picked).
#
# Values: ``low`` / ``medium`` / ``high`` (the composer's own words), plus
#   * ``off``  - the model does not think unless asked (Anthropic's older
#                Messages API models; OpenAI's ``none`` default);
#   * ``auto`` - thinking is on and the model decides how much (Gemini 2.5's
#                documented "On" with no level).
# Neither of those two is a level the composer SENDS: picking still sends only
# low / medium / high, and "Default" still sends nothing (byte-identical).

#: Every value ``reasoning_default`` may answer, "" (unknown) aside.
OFF = "off"
AUTO = "auto"
DEFAULTS: tuple[str, ...] = LEVELS + (OFF, AUTO)

#: Anthropic Messages API, keyed by the bare id (no date, no ``[1m]``).
#: Source: Anthropic's extended/adaptive thinking and effort docs
#: (platform.claude.com/docs/en/build-with-claude/extended-thinking, .../effort),
#: as summarised in the claude-api reference cached 2026-09-25:
#:   * Claude 3.7 Sonnet and the 4.x family through Opus 4.8 run WITHOUT
#:     thinking when the request omits ``thinking`` ("Omitting thinking: runs
#:     without thinking" for Opus 4.8/4.7; "set adaptive explicitly" for 4.6;
#:     "no thinking" for Haiku 4.5 and older) -> ``off``.
#:   * Opus 5, Sonnet 5, Sonnet 5.5, Fable 5 / 5.1 and Mythos 5 / 5.1 run
#:     ADAPTIVE thinking when it is omitted, at ``output_config.effort``'s
#:     default, documented as ``high`` on every current model except Opus 5.5
#:     -> ``high``.
#:   * Opus 5.5: thinking cannot be turned off and effort defaults to
#:     ``medium`` -> ``medium``.
#:   * Read 2026-10-10 (platform.claude.com/docs/en/build-with-claude/effort
#:     and .../thinking-troubleshooting, "Thinking support, defaults, and
#:     rejected configurations by model"):
#:       - Claude Haiku 5.5 (``claude-haiku-5-5``): thinking "On" by default
#:         and "Claude Opus 5.5 and Claude Haiku 5.5 default to medium"
#:         -> ``medium``;
#:       - Claude Mythos Preview (``claude-mythos-preview``): thinking
#:         "Always on" and it supports effort, whose `high` level is "The
#:         default on every model that supports effort except Claude Opus 5.5
#:         and Claude Haiku 5.5" -> ``high``.
#: A Claude id not listed here is a model this table has not read docs for
#: -> "" (unknown), never a family guess.
_ANTHROPIC_DEFAULTS: dict[str, str] = {
    "claude-3-7-sonnet": OFF,
    "claude-sonnet-4": OFF,
    "claude-sonnet-4-0": OFF,
    "claude-opus-4": OFF,
    "claude-opus-4-0": OFF,
    "claude-opus-4-1": OFF,
    "claude-sonnet-4-5": OFF,
    "claude-opus-4-5": OFF,
    "claude-haiku-4-5": OFF,
    "claude-sonnet-4-6": OFF,
    "claude-opus-4-6": OFF,
    "claude-opus-4-7": OFF,
    "claude-opus-4-8": OFF,
    "claude-opus-5": "high",
    "claude-sonnet-5": "high",
    "claude-sonnet-5-5": "high",
    "claude-fable-5": "high",
    "claude-fable-5-1": "high",
    "claude-mythos-5": "high",
    "claude-mythos-5-1": "high",
    "claude-opus-5-5": "medium",
    "claude-haiku-5-5": "medium",
    "claude-mythos-preview": "high",
}

#: OpenAI API (Chat Completions ``reasoning_effort`` / Responses
#: ``reasoning.effort``), keyed by the bare id (no ``-YYYY-MM-DD`` snapshot).
#: Sources, read 2026-10-10:
#:   * the API reference's effort note at the GPT-5.1 launch: "gpt-5.1
#:     defaults to none ... All models before gpt-5.1 default to medium" ->
#:     o1, o3, o3-mini, o4-mini, gpt-5, gpt-5-mini, gpt-5-nano = ``medium``;
#:   * developers.openai.com/docs/models/<id> "Reasoning.effort supports: ..."
#:     with the "(default)" marker: gpt-5.1, gpt-5.2, gpt-5.4, gpt-5.4-mini =
#:     none (``off``); gpt-5.5, gpt-5.6, gpt-5.4-pro = ``medium``;
#:     gpt-5.5-pro = ``high``; gpt-5-pro "defaults to (and only supports)
#:     high" = ``high``.
#:   * the same pages for GPT-6, read 2026-10-10: gpt-6-sol and gpt-6-luna
#:     "`reasoning.effort` supports `none`, `low`, `medium` (default), `high`,
#:     `xhigh`, and `max`."; gpt-6.1-sol "supports `low`, `medium` (default),
#:     `high`, `xhigh`, and `max`." -> ``medium``.
#: Not listed on purpose (no documented default found): the -codex models,
#: gpt-5.2-pro, o1-mini/-preview, o1-pro/o3-pro, gpt-6-astra (its page lists
#: `low`, `medium`, `high`, `xhigh`, and `max` with no "(default)" marker).
_OPENAI_DEFAULTS: dict[str, str] = {
    "o1": "medium",
    "o3": "medium",
    "o3-mini": "medium",
    "o4-mini": "medium",
    "gpt-5": "medium",
    "gpt-5-mini": "medium",
    "gpt-5-nano": "medium",
    "gpt-5-pro": "high",
    "gpt-5.1": OFF,
    "gpt-5.2": OFF,
    "gpt-5.4": OFF,
    "gpt-5.4-mini": OFF,
    "gpt-5.4-pro": "medium",
    "gpt-5.5": "medium",
    "gpt-5.5-pro": "high",
    "gpt-5.6": "medium",
    "gpt-6-sol": "medium",
    "gpt-6-luna": "medium",
    "gpt-6.1-sol": "medium",
}

#: Gemini API, keyed by the bare id (no ``models/`` prefix). Source:
#: ai.google.dev/gemini-api/docs/thinking, the default-thinking table, read
#: 2026-10-10: gemini-2.5-pro / gemini-2.5-flash "On" (dynamic, no level) =
#: ``auto``; gemini-2.5-flash-lite "Off"; gemini-3-pro-preview,
#: gemini-3.1-pro-preview, gemini-3-flash-preview "On (high)";
#: gemini-3.6-flash, gemini-3.8-flash "On (medium)". gemini-3.5-flash-lite is
#: "On (minimal)", a level the composer has no word for -> left out ("").
_GEMINI_DEFAULTS: dict[str, str] = {
    "gemini-2.5-pro": AUTO,
    "gemini-2.5-flash": AUTO,
    "gemini-2.5-flash-lite": OFF,
    "gemini-3-pro-preview": "high",
    "gemini-3.1-pro-preview": "high",
    "gemini-3-flash-preview": "high",
    "gemini-3.6-flash": "medium",
    "gemini-3.8-flash": "medium",
}

_DATE_8 = re.compile(r"-\d{8}$")
_DATE_ISO = re.compile(r"-\d{4}-\d{2}-\d{2}$")
#: Gemini preview snapshots ("gemini-2.5-flash-preview-05-20") are the same
#: model as their bare id; the documented default applies to them too.
_GEMINI_SNAPSHOT = re.compile(r"-preview-\d{2}-\d{2}(-\d{4})?$|-latest$")


def _bare_claude(low: str) -> str:
    """``claude-opus-4-8[1m]`` / ``claude-haiku-4-5-20251001`` -> the bare id."""
    mid = low.removesuffix("[1m]")
    mid = mid.removesuffix("-latest")
    return _DATE_8.sub("", mid)


def reasoning_default(provider: str, model: str, *, served_by: str | None = None) -> str:
    """The level ``(provider, model)`` runs at when NO level is sent, or ``""``.

    ``""`` means unknown: the composer then keeps its plain "Reasoning" label
    and says the model decides. A value is returned only for a model that
    OFFERS levels (``reasoning_levels``) and whose vendor documents the
    default (the three tables above). ``served_by`` is the CLI a keyless API
    provider is really served through (``ProviderManager.inherited_from``):
    such a turn runs on the CLI's own settings, which depend on the user's
    account and config, so the answer is ``""``. The Claude CLI and the Codex
    CLI document no single default (``claude --help``: "--effort <level>
    Effort level for the current session" with no default; Codex's config
    reference: "Available levels depend on the model and client"), and a
    local or OpenAI-compatible endpoint answers with whatever its server is
    configured to do, so all of those are ``""`` as well.
    """
    prov = (provider or "").strip().lower()
    low = (model or "").strip().lower()
    if not prov or not low or served_by:
        return ""
    if not reasoning_levels(provider, model):
        return ""
    if prov == "anthropic":
        return _ANTHROPIC_DEFAULTS.get(_bare_claude(low), "")
    if prov == "openai":
        return _OPENAI_DEFAULTS.get(_DATE_ISO.sub("", low), "")
    if prov == "google":
        bare = _GEMINI_SNAPSHOT.sub("", low.removeprefix("models/"))
        return _GEMINI_DEFAULTS.get(bare, "")
    return ""
