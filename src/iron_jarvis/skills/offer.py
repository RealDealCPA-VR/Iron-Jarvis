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

HOW A SKILL FITS (deterministic, one pass, no model call). Three kinds:

* OWN: a skill made in Iron Jarvis (the Skills page, chat, a recording, an
  approved proposal). It fits when ONE telling word of the message is in its
  name, description or when-to-use line. The user made it here to be used
  here.
* OTHER: a skill the user keeps in another program's folder (Claude Code,
  Codex and so on), or one bundled with Iron Jarvis. It needs its NAME in the
  message: two telling words of the name, or one together with a word of its
  description, or one that hardly any other skill here uses.
* VENDOR: a skill that SHIPPED with another program (it carries a licence
  file, sits in a plugin or a system folder, or is on :data:`_VENDOR_NAMES`).
  Never offered here: it is written for that program's own tools ("use the
  docs connector", "run pypdf in the sandbox") and Iron Jarvis has tools of
  its own for the same work. The user can still pick one with "/".

MEASURED on the owner's PC (43 skills, none made in Iron Jarvis, 2026-11-11):
the first cut (first five letters of a word; a name word OR three description
words for every skill) offered a skill on 11 of 40 everyday messages, most of
them wrong ("summarize this document" offered a meeting-transcript skill,
"rename every pdf" a vendor's PDF skill). The table that holds this rule is
``tests/test_script_power_matchers_v1473.py``.

A "telling" word is not a common one (:data:`_COMMON`) and not one that most
skills here use. Words match after their ending is taken off
(:func:`_stem`): "deduct" finds "deductions", "invoice" finds "invoices";
"state" does not find "statement".

A message that fits nothing adds nothing: the prompt and the tool list are
exactly what they were.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

#: The most skills one message is offered.
OFFER_MAX = 3
#: Only this much of a message is read.
_MAX_CHARS = 4000
#: A description is cut here in the offer.
_DESC_CHARS = 200
#: A single name word is enough for an OTHER skill when at most this many
#: skills here use that word at all.
_RARE = 2

_WORD = re.compile(r"[a-z][a-z0-9]{2,}")

#: Endings taken off a word before two words are compared (longest first).
_ENDINGS = ("ions", "ings", "ion", "ing", "ers", "er", "ed", "es", "s")

#: Skills that ship with another program and carry no licence file to tell
#: them by. Hand-kept: a name missing here only means that skill is judged by
#: the OTHER rule (its name must be in the message).
_VENDOR_NAMES = frozenset({
    "docs", "docx", "pdf", "pptx", "xlsx", "google-workspace", "morning",
    "deep-research", "import-memory", "consolidate-memory", "explain-usage",
    "setup-claude", "built-in-browser", "chrome-browser", "computer-use",
    "skill-creator", "skill-installer", "imagegen", "openai-docs",
    "review-agent", "dataviz", "artifact-design", "artifact-diagramming",
    "artifact-capabilities",
})
#: Folder names that mark a vendor's own skills.
_VENDOR_PARTS = frozenset({".system", "plugins"})
_LICENCE_FILES = ("LICENSE.txt", "LICENSE", "LICENSE.md", "LICENCE.txt", "LICENCE")

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
build call convert draft generate produce prepare read turn try tried wanted
""".split())


def _words(text: str) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _stem(word: str) -> str:
    """*word* without a plural or verb ending, so "invoices", "invoiced" and
    "invoice" are one word. One ending only, and never down to two letters."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            word = word[: -len(ending)]
            break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word


def _telling(text: str) -> "set[str]":
    return {_stem(w) for w in _words(text) if w not in _COMMON}


def _norm(path: Any) -> str:
    """A path as text for a prefix test. No disk access (the skills folder
    may be a slow place, and this runs for every skill on every message)."""
    return os.path.normcase(os.path.abspath(str(path)))


def is_own(skill: Any, home: Any) -> bool:
    """A skill of the user's own: it lives in ``<home>/skills`` (made on the
    Skills page, in chat, by a recording or an approved proposal)."""
    try:
        if home is None or not getattr(skill, "dir", None):
            return False
        root = _norm(Path(home) / "skills")
        own = _norm(skill.dir)
        return own == root or own.startswith(root + os.sep)
    except (OSError, ValueError, TypeError):
        return False


#: ``{skill folder: shipped with another program}``, kept for the process:
#: the answer needs one look at the folder.
_VENDOR_SEEN: "dict[str, bool]" = {}


def is_vendor(skill: Any) -> bool:
    """A skill that SHIPPED with another program, as against one the user
    wrote or installed for themselves. Never raises; "cannot tell" is False
    (the skill is then judged by the stricter OTHER rule, never offered
    freely)."""
    try:
        if str(getattr(skill, "source", "") or "") in ("builtin", "user"):
            return False
        if str(getattr(skill, "name", "") or "").strip().lower() in _VENDOR_NAMES:
            return True
        folder = getattr(skill, "dir", None)
        if not folder:
            return False
        key = _norm(folder)
        seen = _VENDOR_SEEN.get(key)
        if seen is None:
            parts = {p.lower() for p in Path(key).parts}
            seen = bool(parts & _VENDOR_PARTS)
            if not seen:
                try:
                    seen = any((Path(folder) / name).is_file() for name in _LICENCE_FILES)
                except OSError:
                    seen = False
            if len(_VENDOR_SEEN) > 4096:
                _VENDOR_SEEN.clear()
            _VENDOR_SEEN[key] = seen
        return seen
    except Exception:  # noqa: BLE001
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
        scored: list[tuple[int, int, str, Any]] = []
        for skill, name, body in parts:
            name_hits = {w for w in said & name if seen.get(w, 0) <= crowd}
            body_hits = {w for w in (said & body) - name_hits if seen.get(w, 0) <= crowd}
            if not (name_hits or body_hits):
                continue
            own = is_own(skill, home)
            if own:
                fits = True
            elif is_vendor(skill):
                fits = False
            else:
                fits = len(name_hits) >= 2 or (
                    len(name_hits) == 1
                    and (bool(body_hits) or seen.get(next(iter(name_hits)), 0) <= _RARE)
                )
            if not fits:
                continue
            score = 3 * len(name_hits) + len(body_hits)
            # The user's own skills lead, then the better match, then the name.
            scored.append((0 if own else 1, -score, str(skill.name), skill))
        scored.sort(key=lambda row: row[:3])
        return [row[3] for row in scored[: max(1, int(k))]]
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
