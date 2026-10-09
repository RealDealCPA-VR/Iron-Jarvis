"""Follow-up suggestions under a chat reply (v1.323.0; idea from tambo, MIT —
no code taken).

``POST /chat/followups {messages: [{role, content}], provider?, model?}`` →
``200 {suggestions: [str], reason: str}``. ``reason`` is ``""`` when there are
suggestions, else ONE plain sentence saying why there are none. This route
never answers 500 for a model failure: a provider error, a refusal, a timeout
or an unreadable answer is ``{suggestions: [], reason: <sentence>}``.

Rules, each pinned in ``tests/test_chat_followups_v1323.py``:

* OFF unless the user switched it on (``chat_followups``, a row in
  ``settings/schema.py``, default False) — off answers ``reason: "off"`` and
  makes NO model call.
* Only the last :data:`MAX_MESSAGES` user/assistant messages are read, each
  clipped to :data:`MAX_CONTENT` characters; with no assistant reply among
  them there is nothing to suggest from (no call).
* The call goes through the platform ROUTER with the provider/model the reply
  used (``None`` for each when the caller sent none — the router's default
  route, exactly as a chat turn passes ``provider_choice or None``), with NO
  tools. So the router's own refusal rules apply (a local model's
  conversation never moves to a cloud model by fallback — v1.162.0 /
  v1.228.0); this route adds no failover of its own (never
  ``_one_shot_complete``, which walks failover candidates).
* Never a mock-generated suggestion: an explicit ``mock`` pick, a mock
  default route, or a route the mock ended up serving → ``[]`` with
  :data:`MOCK_REASON`.
* The answer is model output: parsed with ``core.jsonish`` (a JSON array, or
  ``{"suggestions": [...]}``), else bulleted / numbered lines; trimmed, kept
  only when non-empty and ≤ :data:`MAX_SUGGESTION_CHARS`, deduped
  case-insensitively, never equal to the user's last message, at most
  :data:`MAX_SUGGESTIONS`; each one is run through the prompt-injection
  scanner (``core.promptguard.scan_context``) and a flagged one is dropped.
* The call is billed like a chat completion (one ``session_id="chat"`` run
  row through ``chat_turn._persist_chat_usage``), so the extra call shows on
  the Usage page. The router call is async; parsing, scanning and the ledger
  write run off the loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ...core import jsonish
from ...core.promptguard import scan_context

log = logging.getLogger(__name__)

#: How many of the newest user/assistant messages are read.
MAX_MESSAGES = 6
#: Each message's content is clipped to this many characters.
MAX_CONTENT = 4000
#: At most this many suggestions come back.
MAX_SUGGESTIONS = 3
#: A suggestion longer than this (after trimming) is dropped, not cut.
MAX_SUGGESTION_CHARS = 120
#: What the prompt asks for (the model is told; the cap above is the bound).
ASK_CHARS = 80
#: A bound on the one extra call (the router's own timeouts still apply).
FOLLOWUP_TIMEOUT_S = 60.0

OFF_REASON = "off"
NOTHING_REASON = "nothing to suggest from"
MOCK_REASON = "the demo model does not suggest"
NO_ROUTER_REASON = "No model is connected, so there are no suggestions."
EMPTY_REASON = "The model had no follow-up questions to suggest."

SYSTEM_PROMPT = (
    "You suggest what the user might ask next in a conversation with an AI "
    "assistant. Reply with ONLY a JSON array of up to 3 strings: short "
    "follow-up questions the USER might ask the assistant next, written in the "
    f"user's own language, each at most {ASK_CHARS} characters. No numbering, "
    "no explanations, nothing outside the array. If nothing useful comes to "
    "mind, reply []."
)

_SPEAKER = {"user": "User", "assistant": "Assistant"}
#: A bulleted or numbered line: "- x", "• x", "* x", "1. x", "2) x".
_LIST_LINE = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+(.+?)\s*$")
_QUOTES = "\"'“”‘’`"
_WS = re.compile(r"\s+")


class FollowupMessage(BaseModel):
    role: str = ""
    content: str = ""


class FollowupsBody(BaseModel):
    messages: list[FollowupMessage] = Field(default_factory=list)
    provider: str = ""
    model: str = ""


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def conversation_window(messages: list[Any]) -> list[dict[str, str]]:
    """The newest :data:`MAX_MESSAGES` user/assistant messages with content,
    each clipped to :data:`MAX_CONTENT` characters. Other roles are skipped."""
    rows: list[dict[str, str]] = []
    for m in messages or []:
        role = str(getattr(m, "role", "") or "").strip().lower()
        content = getattr(m, "content", "")
        if role not in _SPEAKER or not isinstance(content, str) or not content.strip():
            continue
        rows.append({"role": role, "content": content.strip()[:MAX_CONTENT]})
    return rows[-MAX_MESSAGES:]


def build_prompt(window: list[dict[str, str]]) -> str:
    """The ONE user message the model reads: the conversation as a quoted
    transcript plus the ask. One message (never the raw turns) so a window
    that starts on an assistant turn is still a valid request everywhere."""
    lines = [f"{_SPEAKER[m['role']]}: {m['content']}" for m in window]
    transcript = "\n\n".join(lines)
    return (
        "Here is the end of the conversation:\n\n<conversation>\n"
        f"{transcript}\n</conversation>\n\n"
        "Write up to 3 short follow-up questions the user might ask next, in "
        f"the user's language, each at most {ASK_CHARS} characters, as a JSON "
        "array of strings."
    )


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().rstrip(" ?.!。？！").casefold()


def _clean(item: Any) -> str:
    if isinstance(item, dict):
        item = item.get("question") or item.get("text") or ""
    if not isinstance(item, str):
        return ""
    text = _WS.sub(" ", item).strip()
    m = _LIST_LINE.match(text)
    if m:
        text = m.group(1)
    while len(text) >= 2 and text[0] in _QUOTES and text[-1] in _QUOTES:
        text = text[1:-1].strip()
    return text


def _candidates(text: str) -> list[Any]:
    obj = jsonish.loads_lenient(text, want=(list, dict))
    if isinstance(obj, dict):
        for key in ("suggestions", "questions", "follow_ups", "followups"):
            if isinstance(obj.get(key), list):
                obj = obj[key]
                break
    if isinstance(obj, list):
        items = [i for i in obj if isinstance(i, (str, dict))]
        if any(_clean(i) for i in items):
            return items
    # Not a usable array: the bulleted / numbered lines of the answer.
    return [m.group(1) for line in text.splitlines() if (m := _LIST_LINE.match(line))]


def parse_suggestions(text: str, last_user: str = "") -> list[str]:
    """Suggestions recovered from the model's answer (see the module rules)."""
    if not isinstance(text, str) or not text.strip():
        return []
    last = _norm(last_user or "")
    out: list[str] = []
    seen: set[str] = set()
    for item in _candidates(text):
        s = _clean(item)
        if not s or len(s) > MAX_SUGGESTION_CHARS:
            continue
        key = _norm(s)
        if not key or key in seen or (last and key == last):
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


def screened(suggestions: list[str]) -> list[str]:
    """Drop every suggestion the prompt-injection scanner flags."""
    kept: list[str] = []
    for s in suggestions:
        result = scan_context(s, source="follow-up suggestion", cap=None)
        if not result.blocked:
            kept.append(s)
    return kept


def _finish(text: str, last_user: str) -> list[str]:
    return screened(parse_suggestions(text, last_user))


def _is_mock(route: Any) -> bool:
    return (
        str(getattr(route, "provider", "") or "").strip().lower() == "mock"
        or str(getattr(route, "reason", "") or "").strip().lower() == "mock"
    )


def _who(provider: str) -> str:
    return provider if provider else "The model"


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #


def register(app: FastAPI, d) -> None:
    """Attach ``POST /chat/followups``; ``d`` is the create_app deps object."""

    def _bill(route: Any) -> None:
        usage = getattr(getattr(route, "response", None), "usage", None) or {}
        try:
            from ...core.models import AgentState
            from ...eval.pricing import UsageTally
            from ..chat_turn import _persist_chat_usage

            provider = str(getattr(route, "provider", "") or "")
            model = str(getattr(route, "model", "") or "")
            tally = UsageTally()
            tally.add(provider, model, usage)
            _persist_chat_usage(
                d,
                provider=provider,
                model=model,
                state=AgentState.COMPLETED,
                completions=1,
                usage_in=tally.input_tokens,
                usage_out=tally.output_tokens,
                cost_usd=tally.cost_usd,
            )
        except Exception:  # noqa: BLE001 — accounting must never fail the answer
            log.debug("follow-up usage not recorded", exc_info=True)

    @app.post("/chat/followups")
    async def chat_followups(body: FollowupsBody) -> dict[str, Any]:
        """Up to three short questions the user might ask next, from the same
        model that wrote the reply — only when ``chat_followups`` is on. Never
        a 500 for a model failure: the reason says what happened."""
        platform = d.platform
        cfg = getattr(platform, "config", None)
        if not bool(getattr(cfg, "chat_followups", False)):
            return {"suggestions": [], "reason": OFF_REASON}

        window = conversation_window(body.messages)
        if not any(m["role"] == "assistant" for m in window):
            return {"suggestions": [], "reason": NOTHING_REASON}
        last_user = next((m["content"] for m in reversed(window) if m["role"] == "user"), "")

        provider = (body.provider or "").strip()
        model = (body.model or "").strip()
        # Never a mock-made suggestion — and no call at all when the mock is
        # what would answer (an explicit pick, or the untouched mock default).
        if provider.lower() == "mock" or (
            not provider and str(getattr(cfg, "default_provider", "") or "").strip().lower() == "mock"
        ):
            return {"suggestions": [], "reason": MOCK_REASON}

        router = getattr(platform, "router", None)
        if router is None:
            return {"suggestions": [], "reason": NO_ROUTER_REASON}

        from ...providers.adapters.base import LLMMessage

        bound = asyncio.timeout(FOLLOWUP_TIMEOUT_S)
        try:
            async with bound:
                route = await router.complete(
                    provider=provider or None,
                    model=model or None,
                    system=SYSTEM_PROMPT,
                    messages=[LLMMessage(role="user", content=build_prompt(window))],
                    # EMPTY LIST, never None: no tools, and the adapters build
                    # their tool payload with `for t in tools`.
                    tools=[],
                    task_class="chat",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a model failure is a reason, never a 500
            # Only OUR bound earns the "too long" words: on 3.11+ a
            # TimeoutError the provider raised is the same class (v1.228.0).
            if isinstance(exc, TimeoutError) and bound.expired():
                return {
                    "suggestions": [],
                    "reason": f"{_who(provider)} took too long, so there are no suggestions this time.",
                }
            log.info("follow-up suggestions: the model call failed (%s)", type(exc).__name__)
            return {
                "suggestions": [],
                "reason": f"{_who(provider)} did not answer, so there are no suggestions this time.",
            }

        await asyncio.to_thread(_bill, route)
        if _is_mock(route):
            return {"suggestions": [], "reason": MOCK_REASON}

        text = getattr(getattr(route, "response", None), "text", "") or ""
        try:
            suggestions = await asyncio.to_thread(_finish, text, last_user)
        except Exception:  # noqa: BLE001 — an unreadable answer is a reason, never a 500
            log.warning("follow-up suggestions: could not read the answer", exc_info=True)
            suggestions = []
        if not suggestions:
            return {"suggestions": [], "reason": EMPTY_REASON}
        return {"suggestions": suggestions, "reason": ""}
