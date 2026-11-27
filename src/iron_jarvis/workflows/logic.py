"""The "If" block's comparisons, worked out by the app itself (idea 09, 09-a).

No model and no ``eval``: a condition is data, ``{left, op, right}``, or one
level of ``{all: [...]}`` / ``{any: [...]}`` of those. ``left`` and ``right``
are texts that may name earlier values (``{{Read total.data.total}}``); they
are filled in from the run's own record with the engine's one template
reader, then compared here. The same condition on the same record gives the
same answer every time.

Numbers are read tolerantly ("5,000.00", "$5,000", "-12.5") but never
guessed: a side that is not a number for a number comparison raises
:class:`ConditionError` with a sentence, and the If step FAILS with it (its
own "if this fails" then decides what the run does). "5.000,00" is not read
as a number: whether that is five or five thousand is a guess.

``matches`` is a wildcard pattern (``*`` any run of characters, ``?`` one
character), case-insensitive, over the whole value — never a regular
expression, so text from outside cannot make the comparison run for ever.

Idea 08 (goals) reuses :func:`read_number` where a number must be read.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Callable

#: Every comparison, in the order the canvas lists them.
OPS: tuple[str, ...] = (
    "is",
    "is_not",
    "contains",
    "over",
    "under",
    "at_least",
    "at_most",
    "empty",
    "not_empty",
    "matches",
)
#: The comparisons that need two numbers.
NUMBER_OPS: frozenset[str] = frozenset({"over", "under", "at_least", "at_most"})
#: The comparisons that read only the left side.
ONE_SIDED_OPS: frozenset[str] = frozenset({"empty", "not_empty"})

#: How each comparison is said (the card's line, a step's summary).
OP_WORDS: dict[str, str] = {
    "is": "is",
    "is_not": "is not",
    "contains": "contains",
    "over": "is over",
    "under": "is under",
    "at_least": "is at least",
    "at_most": "is at most",
    "empty": "is empty",
    "not_empty": "is not empty",
    "matches": "matches",
}

#: The most of a value a comparison reads (a summary is 1,500 characters at
#: most and a step's data 8,000; this keeps a pattern match short whatever
#: the value is).
MAX_COMPARED = 10_000
#: The most leaf checks in one ``all`` / ``any``.
MAX_CHECKS = 10

_NUMBER = re.compile(
    r"""^\s*
    (?P<neg>[-+−])?\s*
    [$€£¥]?\s*
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)
    \s*%?\s*$""",
    re.VERBOSE,
)
_PAREN_NEGATIVE = re.compile(r"^\s*\((?P<inner>[^()]*)\)\s*$")


class ConditionError(ValueError):
    """A condition that cannot be worked out, with the sentence the If step
    fails with."""


def read_number(text: Any) -> float | None:
    """*text* as a number, read tolerantly, or None when it is not one.

    "5,000.00", "5000", "$5,000", "-12.5", "(250)" (a negative amount, as
    accounts write it), "15%" (15). Thousands are only ever grouped by
    commas in threes; "5.000,00" and "5,00" are not numbers here."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None
    negative = False
    paren = _PAREN_NEGATIVE.match(raw)
    if paren:
        negative = True
        raw = paren.group("inner")
    m = _NUMBER.match(raw)
    if not m:
        return None
    try:
        value = float(m.group("num").replace(",", ""))
    except ValueError:
        return None
    if m.group("neg") in ("-", "−"):
        negative = not negative
    return -value if negative else value


def _blank(text: str) -> bool:
    return text.strip() in ("", "[]", "{}", "null")


def _shown(text: str) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= 60 else one[:57] + "…"


def _leaf(cond: dict, render: Callable[[str], str]) -> tuple[bool, str]:
    op = str(cond.get("op") or "").strip().lower()
    if op not in OPS:
        raise ConditionError(
            f"the comparison “{cond.get('op')}” is not one Iron Jarvis knows"
        )
    left = render(str(cond.get("left") if cond.get("left") is not None else ""))[:MAX_COMPARED]
    right = render(str(cond.get("right") if cond.get("right") is not None else ""))[:MAX_COMPARED]
    words = OP_WORDS[op]
    if op in ONE_SIDED_OPS:
        held = _blank(left) if op == "empty" else not _blank(left)
        return held, f"“{_shown(left)}” {words}"
    if op in NUMBER_OPS:
        a, b = read_number(left), read_number(right)
        for side, value in ((left, a), (right, b)):
            if value is None:
                raise ConditionError(
                    f"“{_shown(side)}” is not a number, so it cannot be compared with "
                    f"“{words}”. Nothing was guessed"
                )
        assert a is not None and b is not None
        held = {
            "over": a > b,
            "under": a < b,
            "at_least": a >= b,
            "at_most": a <= b,
        }[op]
        return held, f"“{_shown(left)}” {words} “{_shown(right)}”"
    if op in ("is", "is_not"):
        a, b = read_number(left), read_number(right)
        same = (a == b) if (a is not None and b is not None) else (
            left.strip().casefold() == right.strip().casefold()
        )
        held = same if op == "is" else not same
        return held, f"“{_shown(left)}” {words} “{_shown(right)}”"
    if op == "contains":
        held = right.strip().casefold() in left.casefold() if right.strip() else False
        return held, f"“{_shown(left)}” {words} “{_shown(right)}”"
    # matches: a wildcard pattern over the whole value (never a regex).
    held = fnmatch.fnmatchcase(left.strip().casefold(), right.strip().casefold())
    return held, f"“{_shown(left)}” {words} “{_shown(right)}”"


def explain(condition: Any, render: Callable[[str], str]) -> tuple[bool, str]:
    """``(held, words)``: whether *condition* holds on the values *render*
    fills in, and one line that says what was compared. Raises
    :class:`ConditionError` (with the sentence) when it cannot be worked
    out."""
    if not isinstance(condition, dict) or not condition:
        raise ConditionError("the If has no condition to check")
    for joiner in ("all", "any"):
        if joiner in condition:
            parts = condition.get(joiner)
            if not isinstance(parts, list) or not parts:
                raise ConditionError(f"the If's “{joiner}” has no checks in it")
            if len(parts) > MAX_CHECKS:
                raise ConditionError(f"the If has more than {MAX_CHECKS} checks")
            results = []
            for part in parts:
                if not isinstance(part, dict) or ("all" in part or "any" in part):
                    raise ConditionError(
                        "the If's checks are one level deep: each one compares two values"
                    )
                results.append(_leaf(part, render))
            held = all(r[0] for r in results) if joiner == "all" else any(r[0] for r in results)
            n = len(results)
            if joiner == "all":
                words = (
                    f"all {n} checks held" if held
                    else f"not all of the {n} checks held"
                )
            else:
                words = (
                    f"at least one of the {n} checks held" if held
                    else f"none of the {n} checks held"
                )
            return held, words
    return _leaf(condition, render)


def evaluate(condition: Any, scope: "dict[str, Any] | Callable[[str], str]") -> bool:
    """Whether *condition* holds. *scope* is the run's values (a step's
    outputs, as the engine records them) or a function that fills a text in.
    Raises :class:`ConditionError` with a sentence when it cannot be
    worked out (a missing comparison, a side that is not a number for a
    number comparison)."""
    if callable(scope):
        render = scope
    else:
        from .engine import render_template

        values = scope

        def render(text: str) -> str:
            return render_template(text, values)

    return explain(condition, render)[0]


def condition_texts(condition: Any) -> list[str]:
    """The texts of *condition* that are filled in from other steps' values
    (what ``checks.missing_values`` reads for an If)."""
    out: list[str] = []
    if not isinstance(condition, dict):
        return out
    parts = []
    for joiner in ("all", "any"):
        if isinstance(condition.get(joiner), list):
            parts.extend(p for p in condition[joiner] if isinstance(p, dict))
    if not parts:
        parts = [condition]
    for p in parts:
        for side in ("left", "right"):
            v = p.get(side)
            if isinstance(v, str):
                out.append(v)
    return out


def condition_problem(condition: Any) -> str | None:
    """Why *condition* cannot be a condition, in a few words, or None (the
    save-time check; values are not filled in here)."""
    if not isinstance(condition, dict) or not condition:
        return "an If needs a condition: a value, a comparison and what to compare it with"
    parts: list[Any]
    joined = [j for j in ("all", "any") if j in condition]
    if joined:
        parts = condition.get(joined[0]) if isinstance(condition.get(joined[0]), list) else []
        if not parts:
            return f"the If's “{joined[0]}” has no checks in it"
        if len(parts) > MAX_CHECKS:
            return f"an If may hold at most {MAX_CHECKS} checks"
    else:
        parts = [condition]
    for p in parts:
        if not isinstance(p, dict) or "all" in p or "any" in p:
            return "an If's checks are one level deep: each one compares two values"
        op = str(p.get("op") or "").strip().lower()
        if op not in OPS:
            return (
                f"the comparison {p.get('op')!r} is not one of " + "|".join(OPS)
            )
        if not str(p.get("left") or "").strip():
            return "an If's check needs a value to look at (for example {{Read total}})"
        if op not in ONE_SIDED_OPS and not str(p.get("right") if p.get("right") is not None else "").strip():
            return f"an If's “{OP_WORDS[op]}” check needs a value to compare with"
    return None


def describe(condition: Any) -> str:
    """The condition as written, in words (no values filled in): the draft
    card's and the canvas's line, "{{Read total}} is over 5000"."""
    if not isinstance(condition, dict):
        return ""
    for joiner, word in (("all", " and "), ("any", " or ")):
        if isinstance(condition.get(joiner), list):
            return word.join(describe(p) for p in condition[joiner] if isinstance(p, dict))
    op = str(condition.get("op") or "").strip().lower()
    words = OP_WORDS.get(op, op)
    left = str(condition.get("left") or "").strip()
    if op in ONE_SIDED_OPS:
        return f"{left} {words}".strip()
    return f"{left} {words} {str(condition.get('right') or '').strip()}".strip()
