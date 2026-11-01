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

THE HELPER'S OWN WINDOW (v1.421.0 review): the section rides the helper's
SYSTEM prompt, which the agent runtime's transcript planner
(``context/agent_window.plan_agent_transcript``) cannot shrink — on a small
window the planner clips the helper's TASK instead (the review measured a
team worker on a model known to read 4,096 tokens at once receive a
1,297-character piece as 400 characters). So when the HELPER's model has a
KNOWN window (``daemon/chat_turn._context_window``, the one ladder, asked
through ``window_of`` with the model the helper really runs on), the section
is composed to :func:`window_cap` of it — the same parts in the same order,
a smaller share each, a part with too little room LEFT OUT, and what was
shortened or left out SAID with the window it was cut for
(:data:`WINDOW_CUT_LINE`). An unknown window, or one of at least
:data:`STANDARD_WINDOW_FULL` tokens, keeps the full section byte for byte; the
floor and the checks are never cut. Helpers on the same model still get one
byte-identical section.

A helper outside a Build session, or with no folder, gets no section at all —
its prompt is exactly what it was.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
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

#: v1.421.0 review — THE HELPER'S OWN WINDOW. The share of a helper model's
#: KNOWN context window (in the planner's own conversion,
#: ``context/budget.CHARS_PER_TOKEN``) the whole section may take: the
#: 16,000-character ceiling is reached exactly at the planner's own assumed
#: window (``context/budget.DEFAULT_WINDOW``, 32,000 tokens), so a model known
#: to read that much and a model whose window is unknown get the same section.
STANDARD_WINDOW_SHARE = 0.14
#: The window (tokens) from which the full section fits its share.
STANDARD_WINDOW_FULL = 32_000
#: A part given less room than this on a small window is LEFT OUT (and said
#: to be) rather than cut to a stub that is mostly the trim marker.
MIN_PART_CHARS = 200
#: The cut line of a section composed for a helper's small window.
WINDOW_CUT_LINE = (
    "(To fit this helper's model, which reads about {window:,} tokens at once: {what}. "
    "The whole text is in the folder.)"
)

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


def window_cap(window: "int | None") -> "int | None":
    """The section's ceiling (characters) for a helper whose model reads
    ``window`` tokens at once (v1.421.0 review): None — the full
    :data:`STANDARD_CAP` section — for an unknown window or one of at least
    :data:`STANDARD_WINDOW_FULL` tokens; else :data:`STANDARD_WINDOW_SHARE`
    of it."""
    try:
        n = int(window or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0 or n >= STANDARD_WINDOW_FULL:
        return None
    from ..context.budget import CHARS_PER_TOKEN

    return min(STANDARD_CAP, int(n * STANDARD_WINDOW_SHARE * CHARS_PER_TOKEN))


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
    #: v1.421.0 review: the scanned parts the head was composed from —
    #: ``(rules, folder guardrails, lessons)`` — so a helper on a small window
    #: gets the same section composed to its own share (:func:`window_cap`).
    #: () (a hand-made standard, the floor-only fallback): the head as it is.
    parts: tuple[str, ...] = field(default=(), compare=False, repr=False)
    #: v1.421.0 review: ``(provider, model) -> window tokens | None`` — the
    #: ONE window ladder (``daemon/chat_turn._context_window``), bound to the
    #: platform by the turn that built this standard. None: every helper gets
    #: the full section.
    window_of: Any = field(default=None, compare=False, repr=False)

    def brief_for(
        self, names: Iterable[Any], briefs: "tuple[tuple[str, str, str, bool], ...] | None" = None,
    ) -> "tuple[str, str, str, bool] | None":
        """The first brief whose name is one of ``names`` (in that order)."""
        have = {b[0]: b for b in (self.briefs if briefs is None else briefs)}
        for raw in names:
            key = _name(raw)
            if key and key in have:
                return have[key]
        return None

    def window_for(self, provider: str, model: str) -> "int | None":
        """The helper model's known window (tokens), or None — unknown, no
        ladder, or the ladder failed: the full section, never a guess."""
        if self.window_of is None or not (provider or model):
            return None
        try:
            n = self.window_of(str(provider or ""), str(model or ""))
            return int(n) if n else None
        except Exception:  # noqa: BLE001 — unknown is the full section
            return None

    def for_helper(self, names: Iterable[Any] = (), *, provider: str = "", model: str = "") -> str:
        """The section a helper's system prompt ends with (``"\\n\\n"`` +
        the parts in order). ``names``: the helper's names, best first.
        ``provider`` / ``model`` (v1.421.0 review): what the helper REALLY
        runs on — a small known window gets the section composed to its share
        (:func:`window_cap`), what was cut said; omitted or unknown: the full
        section, byte for byte."""
        head, briefs = self.head, self.briefs
        window = self.window_for(provider, model)
        cap = window_cap(window)
        if cap is not None and len(self.parts) == 3:
            rules, extra_guard, lessons = self.parts
            head, briefs = _compose(
                rules=rules, extra_guard=extra_guard, lessons=lessons, checks=self.checks,
                briefs=self.briefs, cap=cap, window=window,
            )
        parts = [head]
        brief = self.brief_for(names, briefs)
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
    window_of: Any = None,
) -> HelperStandard:
    """The standard (see the module docstring). ``rules_text`` is the lead's
    own rendered folder-rules section; ``layer`` the turn's validated harness
    layer (``components.ProjectLayer``; a layer that is not used contributes
    nothing); ``library`` its accepted lessons; ``finish`` its finish switch;
    ``checkers`` the project checkers it found (``harness/checkers``);
    ``reviewer`` True when a reviewer will read this reply's change.
    ``window_of`` (v1.421.0 review): ``(provider, model) -> tokens | None``,
    the one window ladder bound to the platform — a helper whose model has a
    small known window gets the section composed to its share
    (:meth:`HelperStandard.for_helper`); None: every helper the full section.
    BLOCKING (the brief scans): callers hop it off the loop. Never raises — a
    fault gives the floor and the checks alone."""
    from . import components as C

    try:
        return _build(
            rules_text=rules_text, layer=layer, library=library, finish=finish,
            checkers=checkers, reviewer=reviewer, low_trust=low_trust,
            event_bus=event_bus, session_id=session_id, window_of=window_of,
        )
    except Exception:  # noqa: BLE001 — a helper's prompt never breaks a turn
        log.warning("the helpers' standard could not be built; the floor only", exc_info=True)
        head = "\n\n".join([
            STANDARD_HEADER + "\n" + STANDARD_INTRO,
            GUARDRAILS_HEADING + "\n" + "\n".join(f"- {g}" for g in C.DEFAULT_GUARDRAILS),
        ])
        return HelperStandard(head=head)


def _build(*, rules_text, layer, library, finish, checkers, reviewer, low_trust, event_bus, session_id,
           window_of=None):
    from ..core.promptguard import publish_blocked, scan_context
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

    head, _briefs = _compose(
        rules=rules, extra_guard=extra_guard, lessons=lessons, checks=checks, briefs=tuple(briefs),
        cap=STANDARD_CAP,
    )
    return HelperStandard(
        head=head, briefs=tuple(briefs), checks=checks, parts=(rules, extra_guard, lessons),
        window_of=window_of,
    )


def _window_cut_line(window: int, shortened: list[str], left_out: list[str]) -> str:
    """The cut line of a section composed for a helper's small window."""
    bits = []
    if shortened:
        bits.append("shortened " + ", ".join(shortened))
    if left_out:
        bits.append("left out " + ", ".join(left_out))
    return WINDOW_CUT_LINE.format(window=int(window), what="; ".join(bits))


def _compose(
    *, rules: str, extra_guard: str, lessons: str, checks: str,
    briefs: "tuple[tuple[str, str, str, bool], ...]", cap: int, window: "int | None" = None,
) -> "tuple[str, tuple[tuple[str, str, str, bool], ...]]":
    """``(head, briefs)`` — the shared head composed to ``cap`` characters and
    the briefs it reserved room for. ``window`` None: the full section
    (:data:`STANDARD_CAP`, briefs at :data:`BRIEF_CHARS`, :data:`CUT_LINE`);
    a helper's small window (v1.421.0 review): a brief gets at most a quarter
    of ``cap``, a part with less than :data:`MIN_PART_CHARS` of room is LEFT
    OUT, and :data:`WINDOW_CUT_LINE` says both. The floor, the checks and the
    headings are never cut — on a window so small that they alone pass
    ``cap``, the section is just them and the cut line."""
    from ..core.promptguard import cap_text
    from . import components as C

    if window is not None:
        # v1.421.0 review: a brief's own share on a small window (a quarter
        # of the section — 3,000 of 16,000 at full size, unchanged there).
        brief_cap = min(BRIEF_CHARS, cap // 4)
        cut_briefs = []
        for name, rel, text, was_cut in briefs:
            if len(text) > brief_cap:
                text, _ = cap_text(text, brief_cap)
                was_cut = True
            cut_briefs.append((name, rel, text, was_cut))
        briefs = tuple(cut_briefs)

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
    names = list(PART_NAMES.values())
    cut_room = (
        len(CUT_LINE.format(parts=", ".join(names))) if window is None
        # an upper bound: every name both shortened and left out
        else len(_window_cut_line(window, names, names))
    )
    fixed = (
        len(STANDARD_HEADER) + 1 + len(STANDARD_INTRO)
        + len(GUARDRAILS_HEADING) + 1 + len("\n".join(floor)) + 1 + len(closing)
        + len(checks) + longest_brief
        + cut_room
        + len("- From this folder's harness files:\n") + 4 + len("- ")
        # the leading "\n\n" and the "\n\n" between up to seven parts, with room
        + 2 + 2 * 7 + 16
    )
    order = [("rules", rules), ("guardrails", extra_guard), ("lessons", lessons)]
    room = cap - fixed
    # v1.421.0 review: no room at all is "every part left out" — the
    # folder-rules reader's fair share answers "no cap" for a budget at or
    # below zero, which here would keep every part WHOLE.
    caps = [0] * len(order) if room <= 0 else _fair_caps([len(t) for _k, t in order], room)
    shortened: list[str] = []
    left_out: list[str] = []
    kept: dict[str, str] = {}
    for (key, text), part_cap in zip(order, caps):
        if text and part_cap is not None:
            if part_cap < MIN_PART_CHARS:
                left_out.append(PART_NAMES[key])
                text = ""
            else:
                text, cut = cap_text(text, part_cap)
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
    if window is not None and (shortened or left_out):
        parts.append(_window_cut_line(window, shortened, left_out))
    elif shortened or left_out:
        parts.append(CUT_LINE.format(parts=", ".join(shortened + left_out)))
    return "\n\n".join(parts), briefs


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
    "CHECKS_HEADING", "HelperStandard", "MIN_PART_CHARS", "STANDARD_CAP", "STANDARD_HEADER",
    "STANDARD_WINDOW_FULL", "STANDARD_WINDOW_SHARE", "WINDOW_CUT_LINE", "build", "helper_names", "window_cap",
]
