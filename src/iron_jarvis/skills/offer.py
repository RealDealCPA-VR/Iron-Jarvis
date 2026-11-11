"""Skills that fit a chat message, offered to the model without a "/"
(v1.473.0).

THE GAP. A user asked chat to "create a skill for tax advisement"; chat made
it. The next tax question ignored it: in plain chat a skill reached a turn
only when the user picked it with "/", so a skill nobody remembered to pick
did nothing. (A coding session has listed the skills to its model since
v1.418.0.)

WHAT THIS DOES. For each chat message, a few skills whose own words fit the
message are NAMED to the model (name + one-line description) and
``skill_load`` is armed beside them, so the model can read the one that fits
and follow it. Nothing is injected whole and nothing is chosen for the model:
it is the list a person would glance at.

HOW A SKILL FITS (deterministic, one pass, no model call):

* the user's OWN skills (made on the Skills page, in chat, by a recording or
  an approved proposal) fit when ONE telling word of the message is in the
  skill's name, description or when-to-use line;
* every other skill (bundled, or another program's) needs more: a telling
  word of its NAME, or three of its description.

A "telling" word is not a common one (:data:`_COMMON`) and not one that most
skills here use. Words match by their first five letters, so "deduct" finds
"deductions".

A message that fits nothing adds nothing: the prompt and the tool list are
exactly what they were.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

#: The most skills one message is offered.
OFFER_MAX = 3
#: Only this much of a message is read.
_MAX_CHARS = 4000
#: A description is cut here in the offer.
_DESC_CHARS = 200
#: Letters two words must share at the front to count as the same word.
_STEM = 5

_WORD = re.compile(r"[a-z][a-z0-9]{2,}")

#: Words that say nothing about WHICH skill fits.
_COMMON = frozenset("""
about above after again against all also always and another any are around
ask asked back because been before being below best better between both but
can cannot could did does doing done down during each either else every
few for from further get gets give given goes going good got had has have
having help her here hers him his how however into its just keep kind know
last later less let like list look made make makes many may maybe might mine
more most much must near need needs never new next none not now off often
once one only onto other others our ours out over own part per please put
rather really right said same say says see seem set shall she should show
since some something soon still such sure take than that the their theirs
them then there these they thing things this those though through thus too
took toward under until upon use used uses using very want wants was way
ways well went were what when where whether which while who whom whose why
will with within without would yes yet you your yours
able add added answer based check client clients create created data
document documents each file files find first following format help info
information item items jarvis iron step steps task tasks text thanks thank
tell tool tools user users work works working write written question
questions request requests result results skill skills example examples
review reviews report reports make update change open save run start today
tomorrow yesterday week month year day time name names number numbers page
pages note notes plan plans
""".split())


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _stem(word: str) -> str:
    return word[:_STEM] if len(word) > _STEM else word


def _telling(text: str) -> "set[str]":
    return {_stem(w) for w in _words(text) if w not in _COMMON}


def is_own(skill: Any, home: Any) -> bool:
    """A skill of the user's own: it lives in ``<home>/skills`` (made on the
    Skills page, in chat, by a recording or an approved proposal)."""
    try:
        if home is None:
            return False
        root = Path(home) / "skills"
        return Path(getattr(skill, "dir", "")).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def own_skill_names(registry: Any, home: Any) -> list[str]:
    """The names of the user's own skills (for "change my … skill")."""
    try:
        return [s.name for s in registry.list() if is_own(s, home)]
    except Exception:  # noqa: BLE001
        return []


def fitting_skills(registry: Any, text: str, home: Any = None, k: int = OFFER_MAX) -> list[Any]:
    """The skills worth naming to the model for *text*, best first, at most
    *k*. Never raises; [] when nothing fits."""
    try:
        said = _telling(str(text or "")[:_MAX_CHARS])
        if not said:
            return []
        skills = [
            s for s in registry.list()
            if getattr(s, "model_invocable", True) and not getattr(s, "archived_at", "")
        ]
        if not skills:
            return []
        parts: list[tuple[Any, set[str], set[str]]] = []
        seen: dict[str, int] = {}
        for skill in skills:
            name = _telling(str(skill.name).replace("-", " ").replace("_", " "))
            body = _telling(
                f"{getattr(skill, 'description', '')} {getattr(skill, 'when_to_use', '')}"
            )
            parts.append((skill, name, body))
            for word in name | body:
                seen[word] = seen.get(word, 0) + 1
        # A word most skills here use tells them apart no better than "the".
        crowd = max(3, len(skills) // 3)
        scored: list[tuple[int, str, Any]] = []
        for skill, name, body in parts:
            name_hits = {w for w in said & name if seen.get(w, 0) <= crowd}
            body_hits = {w for w in (said & body) - name_hits if seen.get(w, 0) <= crowd}
            own = is_own(skill, home)
            if own:
                fits = bool(name_hits or body_hits)
            else:
                fits = bool(name_hits) or len(body_hits) >= 3
            if not fits:
                continue
            score = 3 * len(name_hits) + len(body_hits) + (2 if own else 0)
            scored.append((-score, str(skill.name), skill))
        scored.sort(key=lambda row: (row[0], row[1]))
        return [skill for _, _, skill in scored[: max(1, int(k))]]
    except Exception:  # noqa: BLE001 — a matcher fault offers nothing
        return []


OFFER_HEAD = (
    "# Skills that may fit this request\n"
    "These are saved skills: written know-how for a kind of task. If one fits "
    "what the user asked, call skill_load with its name BEFORE you answer and "
    "follow what it says. If none fits, ignore them and do not mention them."
)


def offer_block(skills: Iterable[Any]) -> str:
    """The lines a model reads for *skills*, or "". BLOCKING: a skill that
    did not come from the user is scanned first (``native.shown_entry``)."""
    from .native import shown_entry

    lines: list[str] = []
    for skill in skills:
        try:
            shown = shown_entry(skill)
        except Exception:  # noqa: BLE001 — one unreadable skill is left out
            shown = None
        if shown is None:
            continue
        name, desc = shown
        desc = desc if len(desc) <= _DESC_CHARS else desc[: _DESC_CHARS - 1] + "…"
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    if not lines:
        return ""
    return OFFER_HEAD + "\n" + "\n".join(lines)
