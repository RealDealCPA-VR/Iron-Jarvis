"""Your own models from chat (the intelligence plan, release 9):
``model_report`` and ``model_measure``.

* ``model_report`` (allow, reads): "What is this model good at?" — the same
  plain lines the Model servers card shows (``envelope/plain``), for one of
  the user's own models or all of them; a figure nothing measured is said as
  not measured yet, never guessed.
* ``model_measure`` (allow): "Measure my model." — presses the SAME Measure
  button (``routes/envelope._start_measure`` via ``d.start_measure``): made-up
  text only, against the user's own model server, in the background. A cloud
  model is refused in words (there is nothing of yours to measure there).

Armed by the message's own words (:func:`model_tools_for`, read by
``daemon.chat_turn.settings_ask_tools`` for both lanes) and found by the
capability lookup.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

READER = "model_report"
WRITER = "model_measure"
HREF = "/fleet"
PLACE = "Model servers"
NEEDS_THE_APP = "This can only be done while the Iron Jarvis app is running."
SCAN_CHARS = 2000
MAX_MODELS = 12


def _d(platform: Any) -> Any:
    from . import records as R

    return R._d(platform)  # noqa: SLF001 — the records module's own deps lookup


def _own_models(d: Any) -> list[tuple[str, str]]:
    """The user's own models Iron Jarvis knows: every connected one that is
    not a cloud or subscription provider, then every one with a stored
    measurement. BLOCKING. Never raises."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    pm = d.platform.providers
    try:
        from ..providers.routing import connected_real_models

        for row in connected_real_models(pm, d.platform.config):
            key = (str(row.get("provider") or ""), str(row.get("model") or ""))
            if key[0] and key[1] and key not in seen and not pm.is_trusted_provider(key[0]):
                seen.add(key)
                out.append(key)
    except Exception:  # noqa: BLE001
        pass
    # The model a configured slot sends by default (Ollama on this PC, the
    # "your own model server" slot), even before its server's list was read.
    try:
        from ..providers.egress import default_model_of

        for slot in ("ollama", "custom"):
            if not pm.available(slot):
                continue
            key = (slot, str(default_model_of(slot, pm) or ""))
            if key[1] and key not in seen:
                seen.add(key)
                out.append(key)
    except Exception:  # noqa: BLE001
        pass
    try:
        import json

        from ..envelope.store import envelope_dir

        for path in sorted(envelope_dir(Path(d.platform.config.home)).glob("*.json")):
            if path.name.endswith(".outcomes.json") or path.name == "trials.json":
                continue
            # The file name is sanitized; the profile inside names the model.
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                key = (str(data.get("provider") or ""), str(data.get("model_id") or ""))
            except Exception:  # noqa: BLE001 — a broken file names nothing
                continue
            if key[0] and key[1] and key not in seen and not pm.is_trusted_provider(key[0]):
                seen.add(key)
                out.append(key)
    except Exception:  # noqa: BLE001
        pass
    return out


def _pick(models: list[tuple[str, str]], ref: str) -> list[tuple[str, str]]:
    want = " ".join(str(ref or "").split()).lower()
    if not want:
        return models
    exact = [m for m in models if want in (m[1].lower(), f"{m[0]}/{m[1]}".lower())]
    if exact:
        return exact
    return [m for m in models if want in m[1].lower()]


def _report_lines(d: Any, provider: str, model: str) -> str:
    """One model's plain lines. BLOCKING."""
    from ..envelope.plain import plain_lines
    from ..envelope.store import load_profile

    profile = load_profile(Path(d.platform.config.home), provider, model)
    guided = bool(d.platform.providers.reply_guided(provider, model))
    lines = plain_lines(profile, guided)
    if lines:
        return f"{model}: " + " ".join(lines)
    return f"{model}: not measured yet; {_next_words(d, provider, model)}"


#: Release 9 review: what happens next for a model not measured yet, TRUE for
#: this PC and this model (never "tried by itself" where it will not be).
WILL_BE_TRIED = "it will be tried by itself the next time this PC is idle, or press Measure on Model servers"
PRESS_MEASURE = "press Measure on Model servers to measure it, or ask me to"


def _next_words(d: Any, provider: str, model: str) -> str:
    """BLOCKING-cheap. The idle trial tries a model only on Windows, only
    while it waits in line, and not while its server's routing is unknown."""
    import sys

    from ..envelope import autotrial as T

    trials = getattr(d, "auto_trials", None)
    if sys.platform != "win32" or trials is None:
        return PRESS_MEASURE
    try:
        with trials._lock:  # noqa: SLF001 — one read of its own entry
            entry = trials._models.get(trials._key(provider, model))  # noqa: SLF001
        if entry is not None and entry.state == T.PENDING and entry.why != "not known yet":
            return WILL_BE_TRIED
    except Exception:  # noqa: BLE001
        pass
    return PRESS_MEASURE


class ModelReportTool(Tool):
    name = READER
    description = (
        "What one of the user's OWN models (on this PC or their network: Ollama, LM Studio, a model "
        "server) is good at, in the plain words the Model servers page shows: whether it uses tools "
        "well, how many pages it holds at once, and how many words a second it writes; and whether "
        "Iron Jarvis is guiding it step by step because it had trouble with tools. Each figure is "
        "said only when it was measured; otherwise it says not measured yet. `model` is a model's "
        "name; empty = all of the user's own models. Reads only; to measure one, call model_measure. "
        "Cloud models are not measured."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "A model's name, like qwen3:8b; empty = all of them."},
        },
    }
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        ref = str((args or {}).get("model") or "")
        try:
            models = await asyncio.to_thread(_own_models, d)
            chosen = _pick(models, ref)[:MAX_MODELS]
            if not chosen:
                if ref:
                    return ToolResult(
                        ok=False,
                        error=(
                            f"“{ref}” is not one of your own models (only models on this PC or your "
                            "network are measured; cloud models are not)."
                        ),
                        data={"door": HREF},
                    )
                return ToolResult(
                    ok=True,
                    output=f"There is no model of your own connected yet ({PLACE} is where one is added).",
                    data={"door": HREF},
                )
            lines = [await asyncio.to_thread(_report_lines, d, p, m) for p, m in chosen]
        except Exception:  # noqa: BLE001 — said in words, never a traceback
            return ToolResult(ok=False, error="Your models could not be read just now.", data={"door": HREF})
        head = f"What your own models are good at ({PLACE}):"
        return ToolResult(
            ok=True,
            output="\n".join([head, *(f"- {line}" for line in lines)]),
            data={"door": HREF, "models": [{"provider": p, "model": m} for p, m in chosen]},
        )


class ModelMeasureTool(Tool):
    name = WRITER
    description = (
        "Measure one of the user's OWN models now (the Measure button on Model servers): Iron Jarvis "
        "sends it made-up text only, nothing of the user's, and checks how well it uses tools, how much "
        "it holds and how fast it writes. It runs in the background for a minute or two on the user's "
        "own model server and the Model servers page updates when it finishes; it is never finished "
        "when this returns, so do not say it is. `model` is a model's name; empty = the only one, if "
        "there is just one. Cloud models are never measured."
    )
    input_schema = {
        "type": "object",
        "properties": {"model": {"type": "string", "description": "A model's name, like qwen3:8b."}},
    }
    # It ACTS: it uses the user's own model server for a minute or two and
    # replaces the model's stored measurement (the old one is not kept), so
    # it never reads as a read and a rehearsal holds it.
    reversibility = Reversibility.IRREVERSIBLE
    risk_class = RiskClass.PAGE_ACTION

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        d = _d(self.platform)
        start = getattr(d, "start_measure", None) if d is not None else None
        if d is None or not callable(start):
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        ref = str((args or {}).get("model") or "")
        models = await asyncio.to_thread(_own_models, d)
        chosen = _pick(models, ref)
        if not chosen:
            return ToolResult(
                ok=False,
                error=(
                    f"“{ref}” is not one of your own models: only models on this PC or your network "
                    "are measured, never a cloud model." if ref else
                    f"There is no model of your own connected yet ({PLACE} is where one is added)."
                ),
                data={"door": HREF},
            )
        if len(chosen) > 1:
            names = ", ".join(m for _p, m in chosen[:MAX_MODELS])
            return ToolResult(ok=False, error=f"Which one? Your own models are: {names}.", data={"door": HREF})
        provider, model = chosen[0]
        try:
            await start(provider, model)
        except Exception as exc:  # noqa: BLE001 — said in plain words (400/409)
            return ToolResult(ok=False, error=_refusal_words(model, exc), data={"door": HREF})
        return ToolResult(
            ok=True,
            output=(
                f"Started measuring {model} with made-up text only. It runs in the background on your "
                f"own model server and is not finished yet; {PLACE} shows the result when it is. "
                "A new measurement also starts afresh the count Iron Jarvis keeps of its trouble "
                "with tools."
            ),
            data={"door": HREF, "provider": provider, "model": model},
        )


def _refusal_words(model: str, exc: Exception) -> str:
    """Measure's refusal in plain words (the route's details carry engine
    words: provider names, "base_url", event names)."""
    from ..daemon.routes.envelope import NOT_YOURS_WORDS

    status = getattr(exc, "status_code", None)
    detail = str(getattr(exc, "detail", "") or "")
    if status == 409:
        return f"{model} is being measured already; {PLACE} shows the result when it finishes."
    if detail == NOT_YOURS_WORDS:
        return detail
    if "trusted" in detail:
        return f"Iron Jarvis measures only your own models; {model} is a cloud model."
    if "base_url" in detail:
        return f"No model server address is saved for {model}; add it on {PLACE} first."
    return f"{model} could not be measured just now."


def model_tools(platform: Any) -> list[Tool]:
    return [ModelReportTool(platform), ModelMeasureTool(platform)]


# --------------------------------------------------------------------------- #
# the words that bring them to a reply (pure, linear)
# --------------------------------------------------------------------------- #
_MODEL_NOUN = re.compile(
    r"\b(?:models?|llms?|ollama|lm\s?studio|qwen[\w.:-]*|llama[\w.:-]*|gemma[\w.:-]*|gpt-oss[\w.:-]*"
    r"|mistral[\w.:-]*|deepseek[\w.:-]*|phi-?\d[\w.:-]*)\b",
    re.IGNORECASE,
)
_ABOUT = re.compile(
    r"\b(?:good\s+at|bad\s+at|how\s+(?:fast|quick|well|much|many\s+pages)|uses?\s+tools"
    r"|words?\s+(?:a|per)\s+second|tokens?\s+(?:a|per)\s+second|capable|capabilit(?:y|ies)"
    r"|context\s+(?:window|length|size)|holds?|speed)\b",
    re.IGNORECASE,
)
_MEASURE = re.compile(r"\b(?:measure|benchmark|test|try\s+out|check)\b", re.IGNORECASE)


def model_tools_for(text: str) -> list[str]:
    """The model tools a message asks for: the reader when it asks what a
    model is good at (or how fast, how much it holds); the writer too when it
    asks to measure one."""
    t = str(text or "")[:SCAN_CHARS]
    if not t.strip() or not _MODEL_NOUN.search(t):
        return []
    measuring = _MEASURE.search(t) is not None
    if not (measuring or _ABOUT.search(t)):
        return []
    return [READER, WRITER] if measuring else [READER]
