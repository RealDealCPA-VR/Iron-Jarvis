"""The two IJ harness tools that need a PERSON in the session (v1.367.0, plan
Phase 4): ``ask_user`` (Claude Code's ``AskUserQuestion``, Codex's
``request_user_input``) and ``exit_plan_mode`` (Claude Code's
``ExitPlanMode``). The dialect table (:mod:`.dialects`) shows each family its
own name and shape and translates the call onto these.

``ask_user`` — asks the user one to four short questions and WAITS for the
answer. It needs somebody who can see the question: it reaches the turn's
side channel through the interaction scope the STREAM lane sets around every
``registry.invoke`` (``mcp.interact.current_scope().ask``, the SAME machinery
an app's question uses — ``daemon/mcp_turn.TurnInteractions``), so the
question goes out as an ``ask_user`` frame while the call is parked, and
``POST /build/sessions/{id}/answer`` settles it. No clock (the v1.247.0
attended-ask rule: ``deadline_exempt``): an answer, Stop, the orphan guard or
the turn's end are the only ways out. With no scope (the POST lane, an agent
run, a client that draws no cards) it refuses at once, in words — it is never
armed there anyway.

``exit_plan_mode`` — in a session's PLAN mode, presents the plan and asks to
leave plan mode. It is ASK tier and once-only: the stream lane cards it in
every posture (auto-approve included), offers Allow once / Deny only, and no
grant of any width covers it (the v1.350.0 ``approve_plan`` card's rules).
Allow runs it: the tool itself WRITES NOTHING (like ``plan_update``, the plan
rides its result's ``data``); the Build turn that sees it succeed switches
the session to act mode and keeps the plan on the session
(``daemon/routes/build``). v1.402.0 ("approve once, then off to the races"):
Allow means "approve AND carry it out" — when this plan turn ends cleanly the
daemon itself starts the act turn (``routes/build.run_approved_plan``), so
the user never has to send a message to start the work. Deny refuses the
call: the session stays in plan mode.
"""

from __future__ import annotations

import re
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult
from .dialects import ASK_TOOL_RULE

#: Questions in one ask, options per question, and the text caps.
MAX_QUESTIONS = 4
MAX_OPTIONS = 8
MAX_QUESTION_CHARS = 500
MAX_HEADER_CHARS = 40
MAX_LABEL_CHARS = 120
MAX_DESCRIPTION_CHARS = 300
#: The plan a model hands exit_plan_mode, at most.
MAX_PLAN_CHARS = 20_000
MAX_PLAN_STEPS = 50

#: The words the model reads when nobody can see its question.
NOBODY_TO_ASK = (
    "Nobody can answer a question in this turn (only a Build session's live "
    "conversation can ask the user). Decide yourself and say what you assumed."
)
#: ...when the turn was stopped before the user answered.
STOPPED_BEFORE_ANSWER = "The turn was stopped before the user answered your question."
#: ...when the user chose not to answer.
SKIPPED = "The user skipped the question. Decide yourself and say what you assumed."
#: v1.398.0 (night shift N1): the answer to a NIGHT task's question nobody
#: answered in the night's wait.
UNANSWERED_AT_NIGHT = (
    "Nobody answered: this task runs at night with nobody watching. Do not ask again. Decide "
    "yourself, carry on with the rest of the task, and say at the end what you assumed."
)
#: What exit_plan_mode's result tells the model after the user's Allow.
#: v1.402.0: the approval STARTS the work — as soon as this turn ends,
#: Iron Jarvis carries the plan out in act mode by itself, with no new
#: message from the user (``routes/build.run_approved_plan``).
PLAN_APPROVED = (
    "The user approved your plan. This turn stays read-only and ends now: as "
    "soon as it ends, Iron Jarvis starts carrying the plan out in act mode, "
    "without waiting for another message from the user. End this turn now with "
    "one short line saying you are starting on the plan."
)
#: exit_plan_mode called outside a Build session.
NOT_A_SESSION = "Plan mode exists only in a Build session."


# --------------------------------------------------------------------------- #
# ask_user
# --------------------------------------------------------------------------- #


def _one_line(value: Any, cap: int) -> str:
    return " ".join(str(value or "").split())[:cap]


def _option(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, str):
        label = _one_line(raw, MAX_LABEL_CHARS)
        return {"label": label, "description": ""} if label else None
    if isinstance(raw, dict):
        label = _one_line(raw.get("label") or raw.get("title") or "", MAX_LABEL_CHARS)
        if not label:
            return None
        return {"label": label, "description": _one_line(raw.get("description"), MAX_DESCRIPTION_CHARS)}
    return None


def _question(raw: Any, n: int) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"question": raw}
    if not isinstance(raw, dict):
        raise ValueError(f"question {n} must be an object with `question`")
    text = _one_line(raw.get("question") or raw.get("title") or "", MAX_QUESTION_CHARS)
    if not text:
        raise ValueError(f"question {n} has no `question` text")
    options_in = raw.get("options") or []
    if not isinstance(options_in, list):
        raise ValueError(f"question {n}: `options` must be a list")
    options = [o for o in (_option(x) for x in options_in) if o is not None][:MAX_OPTIONS]
    qid = re.sub(r"[^A-Za-z0-9_\-]", "", str(raw.get("id") or ""))[:40] or f"q{n}"
    multi = raw.get("multi", raw.get("multiSelect", raw.get("multi_select", False)))
    return {
        "id": qid,
        "question": text,
        "header": _one_line(raw.get("header"), MAX_HEADER_CHARS),
        "options": options,
        "multi": bool(multi is True or (isinstance(multi, str) and multi.lower() == "true")),
    }


def parse_questions(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The questions of an ``ask_user`` call: ``questions`` (1 to
    :data:`MAX_QUESTIONS`) or the single form (``question`` + ``options`` +
    ``multi``). ``ValueError`` in words for anything else. Ids are made
    unique (``q1``…) so an answer can be keyed by them."""
    many = args.get("questions")
    if many is not None:
        if not isinstance(many, list) or not many:
            raise ValueError("`questions` must be a list of one to four questions")
        if len(many) > MAX_QUESTIONS:
            raise ValueError(f"ask at most {MAX_QUESTIONS} questions at once (got {len(many)})")
        out = [_question(q, n) for n, q in enumerate(many, 1)]
    elif args.get("question") is not None:
        out = [_question({
            "question": args.get("question"), "options": args.get("options") or [],
            "multi": args.get("multi", False), "header": args.get("header"),
        }, 1)]
    else:
        raise ValueError("give `question` (or `questions`, one to four of them)")
    seen: set[str] = set()
    for n, q in enumerate(out, 1):
        if q["id"] in seen:
            q["id"] = f"q{n}"
        seen.add(q["id"])
    return out


def _answer_words(value: Any) -> str:
    if isinstance(value, list):
        parts = [_one_line(v, 2000) for v in value if _one_line(v, 2000)]
        return ", ".join(parts)
    return _one_line(value, 4000)


def answer_text(questions: list[dict[str, Any]], answer: Any) -> str:
    """What the model reads: one line per question. ``answer`` is a string
    (one question), a list (one multi-choice question) or ``{question id or
    index: str | list}``. A question with no answer says so."""
    by_key = answer if isinstance(answer, dict) else {}

    def raw_for(n: int, q: dict[str, Any]) -> Any:
        return by_key.get(q["id"], by_key.get(str(n - 1), by_key.get(q["question"])))

    if len(questions) == 1:
        raw = raw_for(1, questions[0]) if isinstance(answer, dict) else answer
        words = _answer_words(raw) if raw is not None else ""
        return f"The user answered: {words}" if words else "The user gave no answer."
    lines = ["The user answered:"]
    for n, q in enumerate(questions, 1):
        raw = raw_for(n, q)
        words = _answer_words(raw) if raw is not None else ""
        lines.append(f"- {q['question']} {words or '(no answer)'}")
    return "\n".join(lines)


class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Ask the user one to four short questions and wait for their answer. Each "
        "question may offer options (the user can always answer in their own words "
        "instead); `multi` lets them pick several. " + ASK_TOOL_RULE + " Find "
        "everything else out yourself with your other tools, decide the rest, and "
        "say what you assumed."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    #: Waits for a PERSON (the attended-ask rule, v1.247.0): a per-call
    #: deadline would turn "the user is reading the question" into a failure.
    #: Stop, the orphan guard and the turn's end still end the wait.
    deadline_exempt = True
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One question, in a sentence."},
            "options": {
                "type": "array",
                "description": "Choices to offer (the user may still answer in their own words).",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["label"],
                },
            },
            "multi": {"type": "boolean", "description": "Let the user pick several options."},
            "questions": {
                "type": "array",
                "description": "Several questions at once (one to four), each with question, options, multi.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "header": {"type": "string"},
                        "question": {"type": "string"},
                        "options": {"type": "array", "items": {}},
                        "multi": {"type": "boolean"},
                    },
                    "required": ["question"],
                },
            },
        },
        "required": [],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            questions = parse_questions(args or {})
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        from ..mcp.interact import current_scope

        scope = current_scope()
        ask = getattr(scope, "ask", None) if scope is not None else None
        if ask is None:
            return ToolResult(ok=False, error=NOBODY_TO_ASK)
        outcome, answer = await ask(questions)
        if outcome == "answered":
            return ToolResult(
                ok=True,
                output=answer_text(questions, answer),
                data={"questions": [q["question"] for q in questions], "answer": answer},
            )
        if outcome == "skipped":
            return ToolResult(ok=True, output=SKIPPED, data={"skipped": True})
        if outcome == "unanswered":
            # v1.398.0 (night shift N1): a night task's question nobody
            # answered in the night's wait (``harness/night``).
            return ToolResult(ok=True, output=UNANSWERED_AT_NIGHT, data={"unanswered": True})
        return ToolResult(ok=False, error=STOPPED_BEFORE_ANSWER)


# --------------------------------------------------------------------------- #
# exit_plan_mode
# --------------------------------------------------------------------------- #

_STEP_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")


def plan_steps(text: str) -> list[dict[str, str]]:
    """The plan's list items (``- step`` / ``1. step`` / ``- [ ] step``) as
    plan steps, all pending — the shape ``plan_update`` keeps, so the plan
    pane reads an approved plan the same way it reads a step list."""
    steps: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        m = _STEP_LINE.match(line)
        if not m:
            continue
        step = _one_line(m.group(1), 500)
        if step:
            steps.append({"step": step, "status": "pending"})
        if len(steps) >= MAX_PLAN_STEPS:
            break
    return steps


class ExitPlanModeTool(Tool):
    name = "exit_plan_mode"
    description = (
        "Present your plan and ask the user to leave plan mode. The user reads the "
        "plan on a card and approves it or not. Approved: this turn ends and Iron "
        "Jarvis starts carrying the plan out in act mode at once. Declined: stay in "
        "plan mode and revise the plan with the user."
    )
    #: It writes nothing (the session's mode is switched by the Build turn that
    #: sees the approved call land) — the plan rides the result's data.
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    #: ``plan`` is OPTIONAL: Claude Code 2.1.306's ExitPlanMode takes no
    #: argument (the plan is what the reply says, which the user reads above
    #: the card), and its dialect passes nothing; the Build turn then keeps
    #: the reply's text as the plan.
    input_schema = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": (
                    "The plan, in markdown: what you will change and how you will check it "
                    "(leave it out when your reply already says it)."
                ),
            },
        },
        "required": [],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .sessions import thread_of

        if not thread_of(getattr(ctx, "session_id", "")):
            return ToolResult(ok=False, error=NOT_A_SESSION)
        text = str((args or {}).get("plan") or "").strip()
        cut = len(text) > MAX_PLAN_CHARS
        text = text[:MAX_PLAN_CHARS]
        steps = plan_steps(text)
        return ToolResult(
            ok=True,
            output=PLAN_APPROVED,
            data={"plan": steps, "plan_text": text, "cut": cut, "mode": "act"},
        )


def session_tools() -> list[Tool]:
    """The two tools this module defines (registered beside the Phase 1 set)."""
    return [AskUserTool(), ExitPlanModeTool()]


__all__ = [
    "AskUserTool",
    "ExitPlanModeTool",
    "NOBODY_TO_ASK",
    "PLAN_APPROVED",
    "answer_text",
    "parse_questions",
    "plan_steps",
    "session_tools",
]
