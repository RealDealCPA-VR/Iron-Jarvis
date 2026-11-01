"""THE STANDARD A HELPER IS JUDGED BY (v1.421.0, Wave 1 — audit gap A3).

The live record (2026-11-01): a coding session's workers never saw what
their work is judged against. A helper's system prompt was its definition's
own words, the branch line and — for a team worker — the worker rules; it
never saw the folder's instruction files (``AGENTS.md`` / ``CLAUDE.md``, which
the LEAD gets through ``projects/folder_rules``), the folder's harness layer
(its guardrails, and the four-rule floor learned the hard way — a model once
ended every Python program on the user's PC with ``taskkill /im``), the
lessons the user accepted for this folder, or what the finish check and the
reviewer would look for. Workers then broke rules nobody told them.

THE SECTION (:class:`HelperStandard`), in this order, each part only when
present:

1. the folder's rule files — the very text the LEAD's turn holds (already
   read once through the ONE reader and promptguard-scanned; never read a
   second time here);
2. the guardrails — the floor (``components.DEFAULT_GUARDRAILS``, which a
   folder can never remove), the folder's own ``guardrails.md`` (scanned when
   the layer was read) and the closing line when the folder speaks;
3. the lessons the user accepted for this folder (``harness/library``,
   scanned);
4. the folder's ``sub_agents/<name>.md`` brief whose name matches the
   helper — its member name, the agent it was asked for, its role (and the
   role's noun: code → coder …), its type — scanned here
   (``promptguard``) and cut to :data:`BRIEF_CHARS` with the cut said;
5. how the session's work is checked — worded from what the FINISH CHECK of
   this turn really does (``harness/finish``: its folder switch, the tests,
   files nobody asked for, the request's points, the project's own checkers
   when the turn found any, a reviewer only when one is set for the session)
   — never a check that does not run.

ONCE PER REPLY, CACHE-STABLE: built off the loop when the lead's turn is
built (``chat_turn._build_harness``, both lanes), carried on the turn's
``HelperParent`` (``chat_turn.helper_base``) and appended by
``helpers.helper_definition`` to every helper of that reply — byte-identical
for every helper the same brief matches. BOUNDED: the whole section is at most
:data:`STANDARD_CAP` characters (twice the lead's own folder-rules cap,
``folder_rules.CHAT_RULES_CAP``); the parts above share what the fixed parts
leave (a fair share, smallest first — the folder-rules reader's own rule),
each cut part is trimmed in its middle with the cut shown and NAMED on one
line. Nothing reaches a helper that did not reach its lead (Local only
included): every part is the lead's own, but the brief, which the lead reads
from the same folder with its tools.

A helper outside a Build session, or with no folder, gets no section at all —
its prompt is exactly what it was.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: The whole section's ceiling (characters): twice the lead's folder-rules
#: cap (``projects/folder_rules.CHAT_RULES_CAP`` = 8,000) — the rules may take
#: about half, the guardrails, lessons, brief and checks the rest.
STANDARD_CAP = 16_000
#: One matching brief's ceiling (``components.MAX_SUB_AGENT_CHARS`` is the
#: file's own, 8,000): the rest is in the file, which the helper can read.
BRIEF_CHARS = 3_000

STANDARD_HEADER = "# The standard for this work"
STANDARD_INTRO = (
    "This work is part of a coding session in this folder. The session's work, yours included, "
    "is held to the standard below: follow it."
)
GUARDRAILS_HEADING = "## Guardrails"
BRIEF_HEADING = "## Your brief from this folder ({rel})"
BRIEF_CUT = "(Shortened here: read the whole brief at {rel}.)"
CHECKS_HEADING = "## How the session's work is checked"
CHECKS_LEAD = "Before the session's reply ends, Iron Jarvis checks the change once:"
CHECKS_CLOSE = "In your report, say what you checked."
#: The parts the cut line can name.
PART_NAMES = {
    "rules": "the folder's instruction files",
    "guardrails": "the folder's guardrails",
    "lessons": "the lessons",
}
CUT_LINE = "(Shortened to fit: {parts}. The whole text is in the folder.)"

#: The check items, worded from what ``harness/finish`` really does.
CHECK_REQUEST = "every point of the user's request is met"
CHECK_TESTS = "the project's tests pass (the last test command run in this reply is run again)"
CHECK_NEW_FILES = "no file is left behind that the request does not ask for"
CHECK_CHECKERS = "the project's own checkers ({names}) find nothing in the changed files"
CHECK_REVIEW = "a reviewer reads the change against the request and the folder's rules above"

#: A role's noun, for a brief named after it ("tester.md" for a test piece).
ROLE_NOUNS = {"code": "coder", "test": "tester", "review": "reviewer", "read": "researcher"}

_NAME_PART = re.compile(r"[^a-z0-9_-]+")


def _name(raw: Any) -> str:
    """A candidate name the way a brief's file is named (lower case,
    letters, digits, - and _): ``custom:QA Bot`` -> ``qa-bot``."""
    text = str(raw or "").strip().lower()
    for prefix in ("custom:", "role:", "builtin:", "dynamic:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return _NAME_PART.sub("-", text).strip("-")


def _fair_caps(lengths: list[int], total: int) -> list["int | None"]:
    """The folder-rules reader's own fair share (``folder_rules._shares``):
    None (no cut) for every part when they all fit; else smallest first, so a
    short part keeps all of its text."""
    from ..projects.folder_rules import _shares

    return _shares(lengths, max(0, total))


@dataclass(frozen=True)
class HelperStandard:
    """One reply's standard for its helpers (built by :func:`build`)."""

    #: The shared part: header, intro, rules, guardrails, lessons, cut line.
    head: str
    #: ``(name, rel, text, cut)`` per brief in the folder — scanned and
    #: capped; ``cut`` True when it was shortened.
    briefs: tuple[tuple[str, str, str, bool], ...] = ()
    #: The checks section ("" when the finish check would check nothing).
    checks: str = ""

    def brief_for(self, names: Iterable[Any]) -> "tuple[str, str, str, bool] | None":
        """The first brief whose name is one of ``names`` (in that order)."""
        have = {b[0]: b for b in self.briefs}
        for raw in names:
            key = _name(raw)
            if key and key in have:
                return have[key]
        return None

    def for_helper(self, names: Iterable[Any] = ()) -> str:
        """The section a helper's system prompt ends with (``"\\n\\n"`` +
        the parts in order). ``names``: the helper's names, best first."""
        parts = [self.head]
        brief = self.brief_for(names)
        if brief is not None:
            name, rel, text, cut = brief
            parts.append(
                BRIEF_HEADING.format(rel=rel) + "\n" + text
                + ("\n" + BRIEF_CUT.format(rel=rel) if cut else "")
            )
        if self.checks:
            parts.append(self.checks)
        return "\n\n" + "\n\n".join(p for p in parts if p)


def _checks(*, finish: Any, low_trust: bool, checkers: Any, reviewer: bool) -> str:
    """The checks section from what this turn's finish check really does."""
    if finish is None or not bool(getattr(finish, "enabled", True)):
        return ""
    items: list[str] = []
    if getattr(finish, "request", True):
        items.append(CHECK_REQUEST)
    if getattr(finish, "tests", True) and not low_trust:
        # (In low trust the check runs no command — never promised then.)
        items.append(CHECK_TESTS)
    if getattr(finish, "new_files", True):
        items.append(CHECK_NEW_FILES)
    names = [str(getattr(c, "name", "") or "") for c in (getattr(checkers, "checkers", None) or ())]
    names = [n for n in dict.fromkeys(names) if n]
    if names and not low_trust:
        items.append(CHECK_CHECKERS.format(names=", ".join(names)))
    if reviewer and getattr(finish, "review", True) and not low_trust:
        items.append(CHECK_REVIEW)
    if not items:
        return ""
    return CHECKS_HEADING + "\n" + CHECKS_LEAD + "\n" + "\n".join(f"- {i}" for i in items) + "\n" + CHECKS_CLOSE


def build(
    *,
    rules_text: str = "",
    layer: Any = None,
    library: Iterable[str] = (),
    finish: Any = None,
    checkers: Any = None,
    reviewer: bool = False,
    low_trust: bool = False,
    event_bus: Any = None,
    session_id: "str | None" = "chat",
) -> HelperStandard:
    """The standard (see the module docstring). ``rules_text`` is the lead's
    own rendered folder-rules section; ``layer`` the turn's validated harness
    layer (``components.ProjectLayer``; a layer that is not used contributes
    nothing); ``library`` its accepted lessons; ``finish`` its finish switch;
    ``checkers`` the project checkers it found (``harness/checkers``);
    ``reviewer`` True when a reviewer will read this reply's change. BLOCKING
    (the brief scans): callers hop it off the loop. Never raises — a fault
    gives the floor and the checks alone."""
    from . import components as C

    try:
        return _build(
            rules_text=rules_text, layer=layer, library=library, finish=finish,
            checkers=checkers, reviewer=reviewer, low_trust=low_trust,
            event_bus=event_bus, session_id=session_id,
        )
    except Exception:  # noqa: BLE001 — a helper's prompt never breaks a turn
        log.warning("the helpers' standard could not be built; the floor only", exc_info=True)
        head = "\n\n".join([
            STANDARD_HEADER + "\n" + STANDARD_INTRO,
            GUARDRAILS_HEADING + "\n" + "\n".join(f"- {g}" for g in C.DEFAULT_GUARDRAILS),
        ])
        return HelperStandard(head=head)


def _build(*, rules_text, layer, library, finish, checkers, reviewer, low_trust, event_bus, session_id):
    from ..core.promptguard import cap_text, publish_blocked, scan_context
    from . import components as C

    used = bool(getattr(layer, "used", False))
    texts = dict(getattr(layer, "texts", None) or {}) if used else {}
    rules = str(rules_text or "").strip()
    extra_guard = str(texts.get("guardrails", "") or "").strip()
    lessons = C.library_text(library)
    checks = _checks(finish=finish, low_trust=low_trust, checkers=checkers, reviewer=reviewer)

    # The briefs: scanned like every folder text that reaches a prompt, and
    # cut to BRIEF_CHARS (the cut said — the file holds the rest).
    briefs: list[tuple[str, str, str, bool]] = []
    for agent in (getattr(layer, "sub_agents", None) or ()) if used else ():
        body = str(getattr(agent, "body", "") or "").strip()
        if not body:
            continue
        result = scan_context(body, source=f"project harness sub-agent {agent.name}", cap=BRIEF_CHARS)
        if result.blocked:
            publish_blocked(event_bus, session_id, result)
        text = result.text.strip()
        if text:
            briefs.append((str(agent.name), str(agent.rel), text, bool(result.truncated)))

    # THE BUDGET: the fixed parts are never cut (the floor, the checks, the
    # headings); a brief is kept room for (the longest one, at most
    # BRIEF_CHARS), so the shared head is the same for every helper; the
    # rules, the folder's guardrails and the lessons share what is left.
    floor = [f"- {g}" for g in C.DEFAULT_GUARDRAILS]
    closing = C.FLOOR_CLOSING if (rules or extra_guard) else ""
    longest_brief = max(
        (len(BRIEF_HEADING.format(rel=rel)) + 1 + len(t) + 1 + len(BRIEF_CUT.format(rel=rel))
         for _n, rel, t, _c in briefs),
        default=0,
    )
    fixed = (
        len(STANDARD_HEADER) + 1 + len(STANDARD_INTRO)
        + len(GUARDRAILS_HEADING) + 1 + len("\n".join(floor)) + 1 + len(closing)
        + len(checks) + longest_brief
        + len(CUT_LINE.format(parts=", ".join(PART_NAMES.values())))
        + len("- From this folder's harness files:\n") + 4 + len("- ")
        # the leading "\n\n" and the "\n\n" between up to seven parts, with room
        + 2 + 2 * 7 + 16
    )
    order = [("rules", rules), ("guardrails", extra_guard), ("lessons", lessons)]
    caps = _fair_caps([len(t) for _k, t in order], STANDARD_CAP - fixed)
    shortened: list[str] = []
    kept: dict[str, str] = {}
    for (key, text), cap in zip(order, caps):
        if text and cap is not None:
            text, cut = cap_text(text, cap)
            if cut:
                shortened.append(PART_NAMES[key])
        kept[key] = text

    parts = [STANDARD_HEADER + "\n" + STANDARD_INTRO]
    if kept["rules"]:
        parts.append(kept["rules"])
    guard_lines = list(floor)
    if kept["guardrails"]:
        guard_lines.append("- From this folder's harness files:\n" + kept["guardrails"])
    if closing:
        guard_lines.append(f"- {closing}")
    parts.append(GUARDRAILS_HEADING + "\n" + "\n".join(guard_lines))
    if kept["lessons"]:
        parts.append(kept["lessons"])
    if shortened:
        parts.append(CUT_LINE.format(parts=", ".join(shortened)))
    return HelperStandard(head="\n\n".join(parts), briefs=tuple(briefs), checks=checks)


def helper_names(parent: Any, definition: Any) -> list[str]:
    """A helper's names, best first, for matching a folder's brief: the team
    member it runs as, the agent the lead asked for, the ROLE it was routed
    by (and the role's noun), its type. Never raises."""
    out: list[str] = []
    try:
        out.append(str(getattr(parent, "member", "") or ""))
        call = getattr(parent, "call", None)
        if call is not None:
            out.append(str(getattr(call, "agent", "") or ""))
            role = _name(getattr(call, "role", "") or "")
            if not role:
                agent = str(getattr(call, "agent", "") or "")
                role = _name(agent) if agent.lower().startswith("role:") else ""
            if role:
                out += [role, ROLE_NOUNS.get(role, "")]
        kind = getattr(getattr(definition, "type", None), "value", "")
        out.append(str(kind or ""))
    except Exception:  # noqa: BLE001
        pass
    return [n for n in out if n]


__all__ = [
    "BRIEF_CHARS", "CHECK_CHECKERS", "CHECK_NEW_FILES", "CHECK_REQUEST", "CHECK_REVIEW", "CHECK_TESTS",
    "CHECKS_HEADING", "HelperStandard", "STANDARD_CAP", "STANDARD_HEADER", "build", "helper_names",
]
