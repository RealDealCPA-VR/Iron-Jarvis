"""A tool asks the USER for a detail only they have (v1.489.0).

Seen live on the practice set, again and again: "I have Ollama running on the
old machine in the storeroom, could you add it" was looked up, the right tool
(`model_server_add`) joined the reply, and the model then asked in words for
the address, because the tool cannot be called without one. The reply ended
with no tool call, the user had to answer in chat, and the next reply had to
find the tool all over again.

Now a tool may name its ``user_details`` (``Tool.user_details``): arguments
only the user can give. The model is told it may leave them out
(:func:`relaxed_spec`); when it does, a chat that can show a question card
asks the user on a card BEFORE the approval card (:func:`fill_missing`, called
by the stream lane), and the answer is put into the call's arguments. So the
approval card, the spend check, the Local only rule and the ledger all see the
WHOLE call, exactly as if the model had typed the detail.

Rules:

* The questions are the TOOL's own fixed words, never the model's.
* Never a secret (a key or a password goes on the secure card).
* The user's Decline is an answer: the call is not run, not carded, and the
  model is told not to ask again in words.
* Where no question card can be shown (the POST lane, a Build terminal's chat,
  the browser sidebar), nothing is asked here: the registry's own check (or
  the tool) tells the model to ask the user in words (:func:`ask_in_words`).
* A call that will be refused anyway (not armed, set to Never run) asks
  nothing.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("iron_jarvis.tools.details")

#: Who the question card says is asking.
ASKER = "Iron Jarvis"
#: The longest answer taken from the card for one detail.
MAX_ANSWER_CHARS = 2000
#: The most details one call may ask for (a form, not an interview).
MAX_QUESTIONS = 4

#: The user pressed Decline (or left the card): the model's tool result.
DECLINED = (
    "The user was asked for {what} on a card and did not give it, so nothing "
    "was changed. Do not ask for it again in words; say that it was not set "
    "up and that they can ask again when they have it."
)
#: The card could not be shown or was cut off (Stop, the reply ended).
NOT_ASKED = (
    "The user could not be asked for {what} just now, so nothing was changed."
)


def _names(keys: list[str]) -> str:
    quoted = [f"`{k}`" for k in keys]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " or " + quoted[-1]


def spec_sentence(keys: list[str]) -> str:
    """What the model is told about the details it may leave out."""
    it = "it" if len(keys) == 1 else "them"
    return (
        f" If the user has not given {_names(keys)}, call this WITHOUT {it} "
        f"(do not ask in words first, and never guess {it}): the user is "
        f"asked for {it} on a card."
    )


def relaxed_spec(tool: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """*spec* for a tool with ``user_details``: the details are no longer
    REQUIRED in what the model is shown, and the description says so. The
    tool's own ``input_schema`` is never touched (deep copy). Never raises:
    a spec that cannot be relaxed is handed back as it is."""
    try:
        keys = [k for k in (tool.user_details or {}) if isinstance(k, str)]
        if not keys:
            return spec
        out = dict(spec)
        schema = copy.deepcopy(spec.get("input_schema") or {})
        required = schema.get("required")
        if isinstance(required, list):
            left = [k for k in required if k not in keys]
            if left:
                schema["required"] = left
            else:
                schema.pop("required", None)
        out["input_schema"] = schema
        own = str(getattr(tool, "user_details_note", "") or "").strip()
        out["description"] = str(spec.get("description") or "") + (
            " " + own if own else spec_sentence(keys)
        )
        return out
    except Exception:  # noqa: BLE001 — a spec we cannot relax still works
        return spec


def missing_for(tool: Any, args: Any, ctx: Any = None) -> dict[str, dict[str, Any]]:
    """``tool.missing_details`` that never raises (a fault asks nothing)."""
    if tool is None or not getattr(tool, "user_details", None):
        return {}
    try:
        found = tool.missing_details(args if isinstance(args, dict) else {}, ctx)
    except Exception:  # noqa: BLE001
        log.warning("missing_details failed for %s", getattr(tool, "name", "?"), exc_info=True)
        return {}
    if not isinstance(found, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, question in found.items():
        if isinstance(key, str) and isinstance(question, dict) and len(out) < MAX_QUESTIONS:
            out[key] = question
    return out


def what_words(missing: dict[str, dict[str, Any]]) -> str:
    """The details in words, for the model ("the server's address")."""
    parts = [str(q.get("what") or q.get("title") or k).strip().rstrip("?.") for k, q in missing.items()]
    parts = [p[0].lower() + p[1:] if p and not p[:2].isupper() else p for p in parts if p]
    if not parts:
        return "a detail"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def ask_in_words(tool: Any, missing: dict[str, dict[str, Any]]) -> str:
    """The failed result where no card can be shown: ask in words."""
    return (
        f"{getattr(tool, 'name', 'This tool')} still needs {what_words(missing)}, "
        "and this chat cannot show the user a question card. Ask the user for "
        "it in words, then make the call again with it."
    )


def ask_params(tool: Any, missing: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The question card for *missing*, in the shape the chat's card reads
    (``mcp.interact.elicitation_fields``): one field per detail."""
    props: dict[str, Any] = {}
    for key, question in missing.items():
        prop: dict[str, Any] = {
            "type": str(question.get("type") or "string"),
            "title": str(question.get("title") or key)[:200],
        }
        if question.get("description"):
            prop["description"] = str(question["description"])[:500]
        if isinstance(question.get("enum"), list) and question["enum"]:
            prop["enum"] = list(question["enum"])
            if isinstance(question.get("enumNames"), list):
                prop["enumNames"] = list(question["enumNames"])
        props[key] = prop
    lead = str(getattr(tool, "user_details_lead", "") or "").strip()
    if not lead:
        lead = "One detail is needed first." if len(props) == 1 else "A few details are needed first."
    return {
        "message": lead,
        "requestedSchema": {"type": "object", "properties": props, "required": list(props)},
    }


def _clean(value: Any, question: dict[str, Any]) -> Any:
    """One answer from the card, or ``None`` when it is no answer."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value if str(question.get("type") or "string") != "string" else None
    if not isinstance(value, str):
        return None
    text = value.strip()[:MAX_ANSWER_CHARS]
    if not text:
        return None
    choices = question.get("enum")
    if isinstance(choices, list) and choices and text not in choices:
        return None
    return text


async def fill_missing(
    registry: Any,
    calls: list,
    turn_tools: Any,
    elicit: Callable[[str, str, dict], Awaitable[dict]],
    *,
    ctx: Any = None,
    refused: Callable[[Any, Any], bool] | None = None,
) -> dict[int, str]:
    """Ask the user for the details the round's calls leave out.

    For each call (in the model's order) whose tool names ``user_details``
    that the arguments lack, ``elicit(call_id, ASKER, params)`` shows ONE
    question card and waits (no clock: an answer, Decline, Stop or the turn's
    end). An accepted answer is written INTO ``call.arguments`` — the lane
    then cards and runs the whole call as usual. Returns ``{id(call):
    sentence}`` for the calls that must not run (the user declined, or could
    not be asked): the lane answers those with the sentence, no card.

    ``refused(call, tool)`` True = the call is refused anyway; nothing is
    asked. Never raises.
    """
    out: dict[int, str] = {}
    allowed = set(turn_tools or ())
    for call in calls or []:
        try:
            name = getattr(call, "name", "")
            if name not in allowed:
                continue
            tool = registry.get(name)
            if tool is None or not getattr(tool, "user_details", None):
                continue
            # Off the loop: a tool's check may read one row (the project's
            # folder) to know whether a folder has to be asked for.
            missing = await asyncio.to_thread(missing_for, tool, getattr(call, "arguments", None), ctx)
            if not missing:
                continue
            if refused is not None and refused(call, tool):
                continue
            answer = await elicit(str(getattr(call, "id", "") or ""), ASKER, ask_params(tool, missing))
            action = answer.get("action") if isinstance(answer, dict) else ""
            if action != "accept":
                sentence = DECLINED if action == "decline" else NOT_ASKED
                out[id(call)] = sentence.format(what=what_words(missing))
                continue
            content = answer.get("content") if isinstance(answer.get("content"), dict) else {}
            args = call.arguments if isinstance(call.arguments, dict) else {}
            still: dict[str, dict[str, Any]] = {}
            for key, question in missing.items():
                value = _clean(content.get(key), question)
                if value is None:
                    still[key] = question
                else:
                    args[key] = value
            call.arguments = args
            if still:
                out[id(call)] = DECLINED.format(what=what_words(still))
        except Exception:  # noqa: BLE001 — a fault here must never stop a turn
            log.warning("asking for a missing detail failed", exc_info=True)
    return out


__all__ = [
    "ASKER",
    "DECLINED",
    "MAX_ANSWER_CHARS",
    "MAX_QUESTIONS",
    "NOT_ASKED",
    "ask_in_words",
    "ask_params",
    "fill_missing",
    "missing_for",
    "relaxed_spec",
    "spec_sentence",
    "what_words",
]
