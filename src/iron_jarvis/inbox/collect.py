"""The Improvements inbox: ONE read over every suggestion that is waiting for
the user's answer (v1.495.0, vision plan leap 5, Phase 1).

Iron Jarvis already makes many kinds of suggestion, and each lives on its own
screen: a coach's change to an agent's instructions, a working habit for a
team member, a lesson or an instructions change from a coding session, a
skill draft, a request for a tool, a memory tidy-up, a preference it noticed,
something it wants to do by itself, and an offer to let a goal use a tool
without asking. Most go unseen. This module reads them all into one list.

THE RULES, each pinned in ``tests/test_improvements_inbox_v1495.py``:

* NOTHING NEW IS SUGGESTED AND NOTHING IS DECIDED HERE. The inbox only READS
  the stores that exist, and every item carries the address of ITS OWN
  accept and decline routes. The page presses those routes; this module has
  no decision code, so a rule a route enforces (a stale edit, a name already
  taken) is enforced here too.
* WHAT AN ACCEPT WILL DO IS READ FROM THE RECORD, never from a model's own
  sentence about its suggestion: a memory tidy-up says which notes are
  deleted or replaced (the memory review route's own view), and an accept
  the route would refuse right now (Emergency stop, a base that is not
  connected, a tool Iron Jarvis cannot add) is ``blocked`` with the reason
  BEFORE the press.
* A STORE THAT CANNOT BE READ IS NAMED (``unreadable``), never shown as
  "nothing waiting". Every kind is read with this module's own query, which
  RAISES when it fails (several stores' own ``list`` swallow a failure and
  answer an empty list). One kind failing never hides the others, and one
  row that cannot be shown never hides its kind.
* BOUNDED, AND IT SAYS SO: at most :data:`PER_KIND` rows are read for a kind
  (the group's ``count`` is a real count of all that wait), long texts are
  cut at :data:`DETAIL_CHARS` with ``cut`` set, and offers are looked for on
  at most :data:`GOALS_MAX` goals (past that the kind is named as
  incomplete).
* TWO PRESSES (``confirm``) for an accept that acts at once beyond saving
  what the item shows: it starts a task, creates a tool, or deletes or
  rewrites notes. A decline that is final and takes two presses on its own
  screen (rejecting something Iron Jarvis wants to do) takes two here.
* An offer to run a tool WITHOUT ASKING is listed but never decided here: it
  widens what runs unasked, so it is answered on its own screen.
* No model is called, no file on disk is opened (but Iron Jarvis's own
  state file for search by meaning, 16-a), nothing is written.
  BLOCKING (SQLite): the route is a sync ``def``.

NOT listed, on purpose: approvals of an action in flight (their own cards),
code changes waiting for review (work results, on the board and the bell),
and old skills the curator tidies by itself.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote

log = logging.getLogger(__name__)

#: Items listed for one kind (the group's count is still the real total).
PER_KIND = 30
#: A before / after / body text, at most.
DETAIL_CHARS = 6000
TITLE_CHARS = 200
WHY_CHARS = 600
LINE_CHARS = 240
EVIDENCE_LINES = 4
#: Goals with approval asks that are looked at for "without asking" offers.
GOALS_MAX = 60

KIND_COACH = "coach"
KIND_HABIT = "habit"
KIND_LESSON = "lesson"
KIND_SESSION_EDIT = "session_edit"
KIND_SKILL = "skill"
KIND_TOOL = "tool"
KIND_MEMORY = "memory"
KIND_PREFERENCE = "preference"
#: v1.499.0 (vision plan leap 3 Phase 3): a name noticed in the owner's folder
#: names that may be a client (``workmap/clients``).
KIND_CLIENT = "client"
#: Next leaps 16-a: search by meaning, offered once with what it costs here
#: (``memory/meaning``: its own file, read; the look that finds and measures
#: the model runs elsewhere, on its own pacing).
KIND_MEANING = "meaning_search"
KIND_WANTS = "wants"
KIND_UNASKED = "unasked"

#: The groups, in the order the page shows them: ``(kind, heading)``.
KINDS: tuple[tuple[str, str], ...] = (
    (KIND_PREFERENCE, "Preferences Iron Jarvis noticed"),
    (KIND_CLIENT, "Clients Iron Jarvis noticed"),
    (KIND_MEANING, "Search by meaning"),
    (KIND_SKILL, "Skill drafts"),
    (KIND_COACH, "Agent instructions"),
    (KIND_HABIT, "Working habits for a team member"),
    (KIND_LESSON, "Lessons from coding sessions"),
    (KIND_SESSION_EDIT, "Coding session instructions"),
    (KIND_MEMORY, "Memory tidy-up"),
    (KIND_TOOL, "Requests for a tool"),
    (KIND_WANTS, "Things Iron Jarvis wants to do"),
    (KIND_UNASKED, "Offers to stop asking"),
)
LABELS = dict(KINDS)

OPEN_LABEL = "Open where it lives"

STOPPED_BLOCK = (
    "Emergency stop is on, so nothing can be started. Turn it off in Settings, then approve this."
)
SELF_DEV_BLOCK = (
    "This one changes Iron Jarvis's own code, and Self-development is turned off in Settings."
)
HABIT_OFF_NOTE = (
    "Accepting saves this habit. Team notes are turned off, so no model is shown it until "
    "you turn them on."
)
MEMORY_PARTIAL = "An earlier try already changed some of these notes."


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #
def _one_line(text: Any, cap: int) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= cap else words[: cap - 1].rstrip() + "…"


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _loads(text: Any, default: Any) -> Any:
    try:
        got = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        return default
    return got if isinstance(got, type(default)) else default


def _cut(text: Any) -> tuple[str, bool]:
    raw = str(text or "")
    if len(raw) <= DETAIL_CHARS:
        return raw, False
    return raw[:DETAIL_CHARS], True


def detail_diff(before: Any, after: Any, label: str = "") -> dict[str, Any]:
    b, cut_b = _cut(before)
    a, cut_a = _cut(after)
    return {"mode": "diff", "before": b, "after": a, "label": label, "cut": cut_b or cut_a}


def detail_text(text: Any, label: str = "", *, cut: bool = False) -> dict[str, Any] | None:
    body, was_cut = _cut(text)
    if not body.strip():
        return None
    return {"mode": "text", "before": "", "after": body, "label": label, "cut": bool(cut or was_cut)}


def action(label: str, path: str, *, body: dict[str, Any] | None = None, note: str = "",
           confirm: bool = False, blocked: str = "") -> dict[str, Any]:
    """One decision as the page presses it: this kind's OWN route."""
    return {
        "label": label, "method": "POST", "path": path, "body": body if body is not None else {},
        "note": note, "confirm": bool(confirm), "blocked": blocked,
    }


def item(kind: str, ref: str, *, title: str, about: str = "", why: str = "",
         evidence: list[str] | None = None, created_at: Any = None,
         detail: dict[str, Any] | None = None, accept: dict[str, Any] | None = None,
         decline: dict[str, Any] | None = None, home: str = "",
         extra: dict[str, Any] | None = None) -> dict[str, Any]:
    lines = [_one_line(e, LINE_CHARS) for e in (evidence or []) if str(e or "").strip()]
    return {
        "id": f"{kind}:{ref}",
        "kind": kind,
        "ref": str(ref),
        "title": _one_line(title, TITLE_CHARS),
        "about": _one_line(about, 120),
        "why": _one_line(why, WHY_CHARS),
        "evidence": lines[:EVIDENCE_LINES],
        "created_at": _iso(created_at),
        "detail": detail,
        "accept": accept,
        "decline": decline,
        "home": {"href": home, "label": OPEN_LABEL} if home else None,
        "extra": extra or {},
    }


def _seg(value: Any) -> str:
    return quote(str(value or ""), safe="")


def _agent_home(agent: str) -> str:
    return f"/agents?view=team&agent={_seg(agent)}" if agent else "/agents?view=team"


def _note_name(ref: Any) -> str:
    """A note's own name: its file name without the folders or ``.md``."""
    tail = str(ref or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or str(ref or "")
    return tail[:-3] if tail.lower().endswith(".md") else tail


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# --------------------------------------------------------------------------- #
# one builder per kind (pure: a row's view in, an item out)
# --------------------------------------------------------------------------- #
def coach_item(view: dict[str, Any], *, team_notes_on: bool = True) -> dict[str, Any]:
    """A coach row: a change to an agent's instructions, or a working habit."""
    from ..coach.taxonomy import CATEGORIES

    pid = str(view.get("id") or "")
    agent = str(view.get("agent") or "")
    name = str(view.get("target") or agent.split(":", 1)[-1])
    paths = {
        "accept": f"/coach/proposals/{_seg(pid)}/accept",
        "decline": f"/coach/proposals/{_seg(pid)}/decline",
    }
    if str(view.get("kind") or "") == "team_note":
        found = [
            f"{f.get('path')}:{f.get('line')}: {f.get('what')}" if f.get("line") else f"{f.get('path')}: {f.get('what')}"
            for f in view.get("evidence") or [] if isinstance(f, dict) and f.get("what")
        ]
        note = (
            f"Accepting adds this to the working habits {name} is given with each piece of team work."
            if team_notes_on else HABIT_OFF_NOTE
        )
        return item(
            KIND_HABIT, pid, title=str(view.get("after") or ""), about=name,
            why=str(view.get("rationale") or ""), evidence=found, created_at=view.get("created_at"),
            accept=action("Accept", paths["accept"], note=note),
            decline=action("Decline", paths["decline"]), home=_agent_home(agent),
        )
    seen = [CATEGORIES.get(str(c), "") for c in view.get("categories") or [] if c]
    return item(
        KIND_COACH, pid, title=f"Change the instructions of {name}", about=name,
        why=str(view.get("rationale") or ""),
        evidence=[f"What the coach saw: {s}" for s in seen if s],
        created_at=view.get("created_at"),
        detail=detail_diff(view.get("before"), view.get("after"), "The agent's instructions"),
        accept=action("Accept", paths["accept"],
                      note=f"Accepting replaces the instructions of {name} with this version. The version before is kept."),
        decline=action("Decline", paths["decline"]), home=_agent_home(agent),
    )


def library_item(view: dict[str, Any], folder_name: str) -> dict[str, Any]:
    """A coding session's lesson, or a change to its instructions files."""
    rid = str(view.get("id") or "")
    thread = str(view.get("thread_id") or "")
    home = f"/chat?view=code&session={_seg(thread)}" if thread else ""
    evidence = view.get("evidence") if isinstance(view.get("evidence"), dict) else {}
    quotes = [f"“{q}”" for q in evidence.get("quotes") or [] if isinstance(q, str)]
    files = [str(p) for p in evidence.get("paths") or [] if isinstance(p, str)]
    lines = quotes[:2] + ([f"Files: {', '.join(files[:4])}"] if files else [])
    accept_path = f"/build/library/{_seg(rid)}/accept"
    decline = action("Decline", f"/build/library/{_seg(rid)}/decline")
    where = folder_name or "that folder"
    if str(view.get("kind") or "") == "layer_edit":
        file = str(view.get("file") or "")
        return item(
            KIND_SESSION_EDIT, rid, title=f"Change {file} for coding sessions in {where}", about=where,
            why=str(view.get("text") or ""), evidence=lines, created_at=view.get("created_at"),
            detail=detail_diff(view.get("before"), view.get("after"), file),
            accept=action("Accept", accept_path,
                          note=f"Accepting writes this change to {file} in {where}. It is refused if that file changed since."),
            decline=decline, home=home,
        )
    text = str(view.get("text") or "")
    return item(
        KIND_LESSON, rid, title=text, about=where, evidence=lines, created_at=view.get("created_at"),
        detail=detail_text(text, "The lesson") if len(" ".join(text.split())) > TITLE_CHARS else None,
        accept=action("Accept", accept_path,
                      note=f"Accepting keeps this lesson for later coding sessions in {where}."),
        decline=decline, home=home,
    )


def skill_item(view: dict[str, Any]) -> dict[str, Any]:
    pid = str(view.get("id") or "")
    name = str(view.get("skill_name") or "a skill")
    refine = str(view.get("kind") or "") == "refine"
    sources = view.get("source_session_ids")
    runs = len(sources) if isinstance(sources, (list, tuple)) else 0
    detail = (
        detail_diff(view.get("prev_body_md"), view.get("body_md"), f"The skill {name}") if refine
        else detail_text(view.get("body_md"), f"The skill {name}")
    )
    return item(
        KIND_SKILL, pid, title=f"{'Update the skill' if refine else 'New skill'}: {name}", about=name,
        why=str(view.get("description") or ""),
        evidence=[f"Written from {_plural(runs, 'earlier run')}"] if runs else [],
        created_at=view.get("created_at"), detail=detail,
        accept=action("Approve", f"/skills/proposals/{_seg(pid)}/approve",
                      note=("Approving updates that skill with this version." if refine
                            else "Approving saves this as a skill agents and chat can use.")),
        decline=action("Reject", f"/skills/proposals/{_seg(pid)}/reject"), home="/skills",
    )


_TOOL_KIND_WORDS = {"tool": "A tool", "mcp": "An extension", "connection": "A connection"}


def tool_item(view: dict[str, Any]) -> dict[str, Any]:
    pid = str(view.get("id") or "")
    name = str(view.get("name") or "")
    what = _TOOL_KIND_WORDS.get(str(view.get("kind") or ""), "A tool")
    floor = str(view.get("blocked") or "")
    can = bool(view.get("can_apply", True)) and not floor
    why_not = (floor or str(view.get("kind_note") or "")) if not can else ""
    command = view.get("command")
    if isinstance(command, (list, tuple)):
        command = " ".join(str(c) for c in command)
    lines = []
    if view.get("scope"):
        lines.append(f"What it would be allowed to do: {view.get('scope')}")
    if view.get("task"):
        lines.append(f"Asked for while working on: {view.get('task')}")
    return item(
        KIND_TOOL, pid, title=f"{what}: {name}", about=name, why=str(view.get("rationale") or ""),
        evidence=lines, created_at=view.get("created_at"),
        detail=detail_text(command, "The command it would run"),
        accept=action("Approve & create", f"/capability/proposals/{_seg(pid)}/approve", confirm=True,
                      note="Approving creates it from the request as written. It still asks you each time it runs.",
                      blocked=("" if can else (why_not or "Iron Jarvis cannot add this one for you."))),
        decline=action("Reject", f"/capability/proposals/{_seg(pid)}/reject"), home="/tools",
    )


_MEMORY_KIND_WORDS = {
    "duplicate": "Two notes say the same thing",
    "stale": "A note looks out of date",
    "contradiction": "Two notes disagree",
    "merge": "Notes that could be one",
}


def memory_effect(view: dict[str, Any]) -> str:
    """What approving WILL do, read off the payload (the Python twin of the
    memory review card's ``effectOf``): never the model's own sentence."""
    removed = [_note_name(r) for r in view.get("remove_refs") or [] if r]
    bits: list[str] = []
    if view.get("rewrites"):
        survivor = _note_name(view.get("survivor_ref"))
        bits.append(f"replaces everything in “{survivor}”" if survivor else "replaces a note's contents")
    if removed:
        bits.append(f"deletes {_plural(len(removed), 'note')}: " + ", ".join(f"“{n}”" for n in removed))
    elif int(view.get("removes") or 0) > 0:
        bits.append(f"deletes {_plural(int(view.get('removes') or 0), 'note')}")
    return f"Approving {', and '.join(bits)}." if bits else ""


def memory_item(view: dict[str, Any]) -> dict[str, Any]:
    """A memory tidy-up, from the memory review route's own view."""
    pid = str(view.get("id") or "")
    base = str(view.get("base") or "")
    refs = [_note_name(r) for r in view.get("refs") or [] if r]
    payload = view.get("payload") if isinstance(view.get("payload"), dict) else {}
    says = str(view.get("suggested_action") or "")
    head = _MEMORY_KIND_WORDS.get(str(view.get("kind") or ""), "A memory tidy-up")
    can = bool(view.get("can_apply"))
    effect = memory_effect(view) or "Approving changes the notes named here."
    if view.get("undoable"):
        effect += " It can be undone from the Activity page."
    lines = [MEMORY_PARTIAL] if view.get("partial") else []
    if refs:
        lines.append(f"Notes: {', '.join(refs[:5])}{' and more' if len(refs) > 5 else ''}")
    if says:
        lines.append(f"The suggestion says: {says}")
    return item(
        KIND_MEMORY, pid, title=f"{head}{f' in {base}' if base else ''}", about=base,
        why=str(view.get("rationale") or ""), evidence=lines, created_at=view.get("created_at"),
        detail=detail_text(payload.get("text"), "The note as it would read",
                           cut=bool(payload.get("text_truncated"))),
        accept=action("Approve", f"/memory/review/{_seg(pid)}/approve", confirm=True, note=effect,
                      blocked=("" if can else (str(view.get("base_note") or "")
                                               or "This one cannot be applied from here."))),
        decline=action("Dismiss", f"/memory/review/{_seg(pid)}/dismiss"), home="/memory",
    )


_WHERE_WORDS = {
    "chat": "in a chat", "phone": "on your phone", "build": "in a coding session",
    "code": "in a coding session", "claude-code": "in Claude Code", "codex": "in Codex",
}


#: v1.501.0: evidence from a run is NOT the owner's words — what a run was
#: asked (a preference it guessed) or a task note (a condensed lesson).
_RUN_WHERE = "run"


#: v1.507.0 (north-star plan 7.1 / 7.3): what keeping a suggestion from what
#: went wrong in a chat, or from Reflect's low-scoring runs
#: (``learning.preferences.WALLED_SOURCES``), does: once kept, it is told only
#: where the client wall allows.
WALLED_NOTE = (
    "Keeping it tells later chats and agents to work this way. It is never told in another "
    "client's folders."
)


def preference_item(view: dict[str, Any]) -> dict[str, Any]:
    from ..learning.preferences import WALLED_SOURCES as _WALLED_SOURCES

    pid = str(view.get("id") or "")
    source = str(view.get("source") or "")
    condensed = source == "distilled"
    said = []
    for e in view.get("evidence") or []:
        if isinstance(e, dict) and e.get("quote"):
            if str(e.get("where") or "") == _RUN_WHERE:
                said.append(
                    f"From a note on an agent run: “{e.get('quote')}”" if condensed
                    else f"From an agent run that went badly, for the task: “{e.get('quote')}”"
                    if source == "reflected"
                    else f"Noticed during an agent run for the task: “{e.get('quote')}”"
                )
                continue
            where = _WHERE_WORDS.get(str(e.get("where") or ""), "in a chat")
            said.append(f"You said, {where}: “{e.get('quote')}”")
    note = WALLED_NOTE if source in _WALLED_SOURCES else "Keeping it tells every later chat and agent to work this way."
    if condensed:
        note += (" Once every lesson condensed with it is answered, the agent run notes it was "
                 "condensed from are removed.")
    return item(
        KIND_PREFERENCE, pid, title=str(view.get("text") or ""), evidence=said,
        created_at=view.get("created_at"),
        accept=action("Keep", f"/memory/preferences/{_seg(pid)}/keep", note=note),
        decline=action("Not this", f"/memory/preferences/{_seg(pid)}/decline"),
        home="/memory#prefs-suggested",
    )


#: v1.499.0: where a client's suggestion is answered in bulk.
CLIENTS_HOME = "/clients#waiting"


def _possessive(name: str) -> str:
    """The ONE possessive table (``workmap.clients.names.possessive``)."""
    from ..settings.records_clients import possessive

    return possessive(name) or name


def _segments(folder: Any) -> list[str]:
    """A folder's parts as spelled, without its drive, a share's server and
    share, or a ``Users\\<name>`` / ``home/<name>`` (text only)."""
    raw = str(folder or "").replace("\\", "/")
    unc = raw.startswith("//")
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    if unc:
        parts = parts[2:]
    elif parts and len(parts[0]) == 2 and parts[0][1] == ":":
        parts = parts[1:]
    if len(parts) >= 2 and parts[0].lower() in ("users", "home"):
        parts = parts[2:]
    return parts


def folder_label(folder: Any) -> str:
    """A home folder by NAME, never a full path: its parent and its own
    name, "Clients › Hartleys" (or just its name)."""
    parts = _segments(folder)
    return " › ".join(parts[-2:]) if parts else ""


def _ago(at: Any, now: datetime) -> str:
    """ "today", "yesterday", "3 days ago", "2 weeks ago", "5 months ago"."""
    when: datetime | None = None
    if isinstance(at, datetime):
        when = at
    elif isinstance(at, str) and at.strip():
        try:
            when = datetime.fromisoformat(at.strip().replace("Z", "+00:00"))
        except ValueError:
            when = None
    if when is None:
        return ""
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc).replace(tzinfo=None)
    days = max(0, (now - when).days)
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def client_view(row: Any) -> dict[str, Any]:
    """A waiting client row as the builder reads it (the record's own
    columns; nothing else)."""
    return {
        "id": str(getattr(row, "id", "") or ""),
        "name": str(getattr(row, "name", "") or ""),
        "evidence": _loads(getattr(row, "evidence_json", "{}"), {}),
        "created_at": getattr(row, "created_at", None),
    }


def client_item(view: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """A name Iron Jarvis noticed in the owner's folder names (v1.499.0):
    "Is Hartleys a client?". Its folders are named by NAME (parent › name),
    never by a full path. Both answers are ONE press (saving the answer is
    all either does)."""
    pid = str(view.get("id") or "")
    name = " ".join(str(view.get("name") or "").split())
    poss = _possessive(name)
    ev = view.get("evidence") if isinstance(view.get("evidence"), dict) else {}
    homes = [h for h in (ev.get("homes") or []) if isinstance(h, dict)]
    work = int(ev.get("work") or sum(int(h.get("work") or 0) for h in homes) or 0)
    folders = int(ev.get("folders") or len(homes) or 0)
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    lines: list[str] = []
    for h in homes:
        label = folder_label(h.get("folder"))
        if not label:
            continue
        line = f"{label}: {_plural(int(h.get('work') or 0), 'piece of work', 'pieces of work')}"
        age = _ago(h.get("last_at"), moment)
        if age:
            line += f", last {age}"
        lines.append(line)
    return item(
        KIND_CLIENT, pid, title=f"Is {name} a client?",
        about=f"{_plural(folders, 'folder')}, {_plural(work, 'piece of work', 'pieces of work')}",
        why=(
            f"Iron Jarvis saw this name in your folder names. Saying yes gives {name} a page and lets you "
            f"keep preferences for {name} only; chats in other clients' folders are never told about it. "
            "Nothing was sent anywhere to notice it. You can answer many at once on the Clients page."
        ),
        evidence=lines, created_at=view.get("created_at"),
        accept=action("Yes, a client", f"/clients/{_seg(pid)}/confirm",
                      note=f"Gives {name} a page and keeps its folders, work and preferences together. "
                           f"Chats are told about {name} only in {poss} folders."),
        decline=action("Not a client", f"/clients/{_seg(pid)}/decline",
                       note=f"Iron Jarvis won't ask about {name} again."),
        home=CLIENTS_HOME,
    )


MEANING_HOME = "/settings?focus=meaning_search"
MEANING_OTHER = "Use another model"


def meaning_item(view: dict[str, Any]) -> dict[str, Any]:
    """16-a: the ONE offer of search by meaning, while it was never answered
    and a search model on this PC or the owner's network answered its probe
    (``memory/meaning.view``). Turn it on presses the setting's own route
    with THAT model; Not now is final; Use another model opens the setting,
    where every model found is listed with what it costs here."""
    best = view.get("best") or {}
    model = str(best.get("model") or "")
    where = str(best.get("where_words") or "")
    others = [str(c.get("model") or "") for c in view.get("candidates") or []
              if c.get("choice") != best.get("choice") and c.get("works") is not False]
    lines = [str(best.get("cost_words") or "")]
    if others:
        lines.append(f"Also found: {', '.join(others[:4])}{' and more' if len(others) > 4 else ''}.")
    it = item(
        KIND_MEANING, "offer", title=str(view.get("words") or ""),
        about=f"{model} {where}".strip(),
        why=(
            f"{model} is already {where}. With it, Iron Jarvis finds notes, files and memories by what they "
            "are about, not only by their words, and the text it searches stays on this PC or your own network."
        ),
        evidence=lines, created_at=view.get("looked_at") or None,
        accept=action("Turn it on", "/memory/meaning/choose", body={"choice": str(best.get("choice") or "")},
                      note="It takes effect at once, with no restart. Settings can turn it off again, with Undo."),
        decline=action("Not now", "/memory/meaning/decline", note="Iron Jarvis won't offer it again."),
        home=MEANING_HOME,
    )
    it["home"] = {"href": MEANING_HOME, "label": MEANING_OTHER}
    return it


_RISK_WORDS = {"low": "Low risk", "med": "Medium risk", "high": "High risk"}
_SOURCE_WORDS = {
    "sentinel": "A watcher noticed a change",
    "event": "Something that happened in the app",
    "deliberation": "Its own thinking about one of your goals",
}


def wants_item(row: Any, *, stopped: bool = False, self_dev_on: bool = True) -> dict[str, Any]:
    """Something Iron Jarvis wants to do. ``stopped`` = Emergency stop is on
    (the approve route refuses then); a task for the maintainer agent needs
    Self-development on."""
    pid = str(getattr(row, "id", "") or "")
    act = _loads(getattr(row, "action_json", "{}"), {})
    lines = [_RISK_WORDS.get(str(getattr(row, "risk", "") or ""), "")]
    lines.append(_SOURCE_WORDS.get(str(getattr(row, "source", "") or ""), ""))
    task = str(act.get("task") or "")
    blocked = ""
    if stopped:
        blocked = STOPPED_BLOCK
    elif str(act.get("agent_type") or "") == "maintainer" and not self_dev_on:
        blocked = SELF_DEV_BLOCK
    return item(
        KIND_WANTS, pid, title=str(getattr(row, "title", "") or "Something it wants to do"),
        why=str(getattr(row, "rationale", "") or ""), evidence=[ln for ln in lines if ln],
        created_at=getattr(row, "created_at", None),
        detail=detail_text(task, "The task it would start"),
        accept=action("Approve and run", f"/proposals/{_seg(pid)}/approve", confirm=True,
                      note="Approving starts this task now, as an agent run.", blocked=blocked),
        decline=action("Reject", f"/proposals/{_seg(pid)}/reject", confirm=True,
                       note="Rejecting is final."),
        home="/autonomy",
    )


def unasked_item(goal: Any, tool: str, *, args_hash: str = "", label: str = "", count: int = 0) -> dict[str, Any]:
    gid = str(getattr(goal, "id", "") or "")
    name = _one_line(getattr(goal, "name", "") or getattr(goal, "contract_text", "") or "a goal", 80)
    what = label or tool
    ref = f"{gid}:{tool}:{args_hash}" if args_hash else f"{gid}:{tool}"
    return item(
        KIND_UNASKED, ref,
        title=(f"Let the goal “{name}” run exactly this without asking: {what}" if args_hash
               else f"Let the goal “{name}” use {what} without asking"),
        about=name,
        evidence=[f"It asked {count} times and you approved every time."] if count else
                 ["It asked several times and you approved every time."],
        home="/autonomy",
        extra={"goal_id": gid, "tool": tool, "args_hash": args_hash},
    )


# --------------------------------------------------------------------------- #
# the sources (BLOCKING; each answers {kind: (items, how many wait in all)})
# --------------------------------------------------------------------------- #
class Read(dict):
    """``{kind: (items, count)}``; ``partial`` names kinds that could only be
    read in part (they are listed, and named as possibly incomplete)."""

    partial: tuple[str, ...] = ()


Source = Callable[[Any], Read]


def _pending(engine: Any, model: Any, *where: Any) -> tuple[list[Any], int]:
    """The newest :data:`PER_KIND` rows that match, and how many match in
    all. RAISES when the table cannot be read (the caller names the kind)."""
    from sqlalchemy import func, text
    from sqlmodel import select

    from ..core.db import session_scope

    with session_scope(engine) as db:
        total = db.exec(select(func.count()).select_from(model).where(*where)).one()
        rows = list(db.exec(
            select(model).where(*where)
            .order_by(model.created_at.desc(), text("rowid DESC")).limit(PER_KIND)
        ))
    return rows, int(total or 0)


def _built(rows: list[Any], build: Callable[[Any], dict[str, Any]], what: str) -> list[dict[str, Any]]:
    """One item per row; a row that cannot be shown is skipped (and logged),
    so one odd row never hides its kind. The kind's count still includes it."""
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(build(row))
        except Exception:  # noqa: BLE001
            log.warning("improvements inbox: one %s row could not be shown", what, exc_info=True)
    return out


def _preferences(d: Any) -> Read:
    from ..learning import preferences as prefs
    from ..learning.models import LessonRecord

    rows, total = _pending(d.platform.engine, LessonRecord, LessonRecord.status == prefs.STATUS_PROPOSED)
    return Read({KIND_PREFERENCE: (_built(rows, lambda r: preference_item(prefs.pref_view(r)), "preference"), total)})


def _clients(d: Any) -> Read:
    """v1.499.0: the names noticed in the owner's folders that wait for an
    answer (``workclient`` rows still proposed)."""
    from ..workmap.models import CLIENT_PROPOSED, WorkClient

    rows, total = _pending(d.platform.engine, WorkClient, WorkClient.status == CLIENT_PROPOSED)
    return Read({KIND_CLIENT: (_built(rows, lambda r: client_item(client_view(r)), "client"), total)})


def _meaning(d: Any) -> Read:
    """16-a: the search-by-meaning offer, from its state file (Iron Jarvis's
    own; no model, no network: the look ran on the meaning loop's pacing)."""
    from ..memory import meaning

    view = meaning.view(d)
    if not view.get("offer"):
        return Read({KIND_MEANING: ([], 0)})
    return Read({KIND_MEANING: ([meaning_item(view)], 1)})


def _skills(d: Any) -> Read:
    from ..daemon.routes.skill_learning import _proposal_view
    from ..skills.learning_models import SkillProposalRecord

    if getattr(d.platform, "skill_learning", None) is None:
        return Read()
    rows, total = _pending(d.platform.engine, SkillProposalRecord, SkillProposalRecord.status == "pending")
    return Read({KIND_SKILL: (_built(rows, lambda r: skill_item(_proposal_view(r)), "skill"), total)})


def _coach(d: Any) -> Read:
    from ..coach.engine import proposal_view
    from ..coach.models import PENDING, CoachProposalRecord as R

    if getattr(d.platform, "coach", None) is None:
        return Read()
    on = bool(getattr(getattr(d.platform, "config", None), "team_notes", True))
    engine = d.platform.engine
    edits, n_edits = _pending(engine, R, R.status == PENDING, R.kind != "team_note")
    habits, n_habits = _pending(engine, R, R.status == PENDING, R.kind == "team_note")
    build = lambda r: coach_item(proposal_view(r), team_notes_on=on)  # noqa: E731
    return Read({
        KIND_COACH: (_built(edits, build, "coach"), n_edits),
        KIND_HABIT: (_built(habits, build, "habit"), n_habits),
    })


def _library(d: Any) -> Read:
    from ..harness import library as lib
    from ..harness.library_models import PROPOSED, HarnessLibraryRecord as R

    engine = d.platform.engine
    lessons, n_lessons = _pending(engine, R, R.status == PROPOSED, R.kind != "layer_edit")
    edits, n_edits = _pending(engine, R, R.status == PROPOSED, R.kind == "layer_edit")
    build = lambda r: library_item(lib.row_view(r), lib._folder_name(r.folder))  # noqa: E731
    return Read({
        KIND_LESSON: (_built(lessons, build, "lesson"), n_lessons),
        KIND_SESSION_EDIT: (_built(edits, build, "session edit"), n_edits),
    })


def _memory(d: Any) -> Read:
    from ..daemon.routes.memory_review import suggestion_view
    from ..memory.proposals import MemoryProposalRecord as R, MemoryProposalStore

    store = getattr(d, "memory_proposals", None)
    if store is None:
        store = MemoryProposalStore(
            d.platform.engine, ltm=getattr(d.platform, "ltm", None),
            home=getattr(getattr(d.platform, "config", None), "home", None),
        )
    rows, total = _pending(d.platform.engine, R, R.status == "pending")
    return Read({KIND_MEMORY: (_built(rows, lambda r: memory_item(suggestion_view(store, r)), "memory"), total)})


def _tools(d: Any) -> Read:
    from ..capability.models import PENDING, CapabilityProposalRecord as R
    from ..capability.store import CapabilityProposalStore, proposal_view

    store = getattr(d, "capability_proposals", None) or getattr(d.platform, "capabilities", None)
    if store is None:
        store = CapabilityProposalStore(d.platform.engine, platform=d.platform)
    rows, total = _pending(d.platform.engine, R, R.status == PENDING)
    return Read({KIND_TOOL: (_built(rows, lambda r: tool_item(proposal_view(r, store)), "tool"), total)})


def _wants(d: Any) -> Read:
    from ..motivation.models import ProposalRecord as R

    if getattr(d.platform, "intent", None) is None:
        return Read()
    config = getattr(d.platform, "config", None)
    stopped = bool(getattr(config, "autonomy_kill_switch", False))
    self_dev = bool(getattr(config, "self_dev_enabled", False))
    rows, total = _pending(d.platform.engine, R, R.status == "pending")
    build = lambda r: wants_item(r, stopped=stopped, self_dev_on=self_dev)  # noqa: E731
    return Read({KIND_WANTS: (_built(rows, build, "wants"), total)})


class _GrantsOnce:
    """The grants store, read once per goal (the offer rules ask three times)."""

    def __init__(self, grants: Any) -> None:
        self._grants = grants
        self._seen: dict[tuple[Any, ...], Any] = {}

    def list(self, *key: Any) -> Any:
        if key not in self._seen:
            self._seen[key] = self._grants.list(*key)
        return self._seen[key]


def _goals_with_asks(engine: Any) -> tuple[list[Any], bool]:
    """The goals that have ever asked for an approval (only those can have an
    offer), at most :data:`GOALS_MAX`, and whether there were more. RAISES
    when it cannot be read."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.events import EventType
    from ..core.models import EventRecord
    from ..core.models import Session as SessionRow
    from ..goals.engine import _goal_origin
    from ..goals.models import GoalContractRecord as G

    prefix = _goal_origin("")
    asked = select(EventRecord.session_id).where(EventRecord.type == EventType.APPROVAL_REQUESTED)
    with session_scope(engine) as db:
        origins = list(db.exec(
            select(SessionRow.origin).where(SessionRow.origin.like(f"{prefix}%"))  # type: ignore[union-attr]
            .where(SessionRow.id.in_(asked)).distinct().limit(GOALS_MAX + 1)  # type: ignore[attr-defined]
        ))
        ids = [str(o)[len(prefix):] for o in origins if o][:GOALS_MAX]
        goals = list(db.exec(
            select(G).where(G.id.in_(ids)).order_by(G.created_at.desc(), G.id)  # type: ignore[attr-defined]
        )) if ids else []
    return goals, len(origins) > GOALS_MAX


def _unasked(d: Any) -> Read:
    from ..daemon.routes import goals as G

    if getattr(d.platform, "goal_engine", None) is None:
        return Read()
    engine = d.platform.engine
    store = getattr(d.platform, "grants", None)
    goals, more = _goals_with_asks(engine)
    out: list[dict[str, Any]] = []
    for goal in goals:
        rows = G.ask_rows_strict(engine, goal.id)
        if not rows:
            continue
        grants = _GrantsOnce(store) if store is not None else None
        stats = G._stats_from_rows(rows)
        hash_stats = G._hash_stats_from_rows(rows)
        for offer in G.exact_offers(goal, hash_stats, grants):
            out.append(unasked_item(goal, str(offer.get("tool") or ""), args_hash=str(offer.get("args_hash") or ""),
                                    label=str(offer.get("label") or ""), count=int(offer.get("count") or 0)))
        for tool in G.grant_offers(goal, stats, hash_stats, grants):
            out.append(unasked_item(goal, str(tool), count=int((stats.get(tool) or {}).get("asked", 0))))
    got = Read({KIND_UNASKED: (out[:PER_KIND], len(out))})
    if more:
        got.partial = (KIND_UNASKED,)
    return got


#: ``(the kinds a source answers for, the source)``.
SOURCES: tuple[tuple[tuple[str, ...], Source], ...] = (
    ((KIND_PREFERENCE,), _preferences),
    ((KIND_CLIENT,), _clients),
    ((KIND_MEANING,), _meaning),
    ((KIND_SKILL,), _skills),
    ((KIND_COACH, KIND_HABIT), _coach),
    ((KIND_LESSON, KIND_SESSION_EDIT), _library),
    ((KIND_MEMORY,), _memory),
    ((KIND_TOOL,), _tools),
    ((KIND_WANTS,), _wants),
    ((KIND_UNASKED,), _unasked),
)


def night_reasons(d: Any, items: list[dict[str, Any]]) -> None:
    """17-a: a suggestion the NIGHT REVIEW made says why it was made, on its
    own item: ``extra.night = {night, why}``, read from the night's record
    (``improve/night_review.reasons``). A read only: the inbox still decides
    nothing, and a record that cannot be read leaves the items as they are."""
    try:
        from ..improve.night_review import reasons

        index = reasons(d.platform.engine)
    except Exception:  # noqa: BLE001 — the reasons are a nicety, never a lost list
        log.debug("improvements inbox: the night review's reasons could not be read", exc_info=True)
        return
    for it in items:
        got = index.get((str(it.get("kind") or ""), str(it.get("ref") or "")))
        if got:
            it["extra"] = {**(it.get("extra") or {}), "night": dict(got)}


def collect(d: Any) -> dict[str, Any]:
    """Everything that is waiting, in one answer. BLOCKING; never raises.

    ``{items, groups: [{kind, label, count, shown}], total, unreadable:
    [{kind, label}], results: [...]}``. ``count`` is how many are really
    waiting for that kind; ``shown`` how many of them are in ``items``.
    ``unreadable`` names every kind that could not be read, or only in part.
    ``results`` (18-a, :func:`change_results`): the results of changes the
    owner already accepted; they wait for nothing, so no count holds them."""
    got: dict[str, tuple[list[dict[str, Any]], int]] = {}
    unreadable: list[dict[str, str]] = []
    for kinds, source in SOURCES:
        try:
            read = source(d)
            got.update(read)
            unreadable += [{"kind": k, "label": LABELS[k]} for k in getattr(read, "partial", ())]
        except Exception:  # noqa: BLE001 — one store never hides the others
            log.warning("improvements inbox: %s could not be read", "/".join(kinds), exc_info=True)
            unreadable += [{"kind": k, "label": LABELS[k]} for k in kinds]
    items: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for kind, label in KINDS:
        rows, count = got.get(kind, ([], 0))
        if not count:
            continue
        items += rows
        groups.append({"kind": kind, "label": label, "count": int(count), "shown": len(rows)})
    night_reasons(d, items)
    return {
        "items": items,
        "groups": groups,
        "total": sum(g["count"] for g in groups),
        "unreadable": unreadable,
        "results": change_results(d, unreadable),
    }


#: 18-a: the kind under which the results of accepted changes are named
#: when they cannot be read (never "no results").
RESULTS_KIND = "results"


def change_results(d: Any, unreadable: list[dict[str, str]]) -> list[dict[str, Any]]:
    """18-a: the results of the changes the owner accepted (``improve/
    results.page``: newest first, at most 30, each with its words built from
    its numbers). A READ of that module's own table: the inbox still
    decides nothing. When the table cannot be read it is named in
    ``unreadable`` and the list is empty; a record the LAST count could not
    read (an agent's work, a skill's uses, a working habit's record) is
    named there too, by its kind (``results.unreadable_labels``), so a
    result held back by it is never passed off as merely "not measured
    yet"."""
    from ..improve import results as R

    engine = d.platform.engine
    try:
        rows = R.page(engine)
    except Exception:  # noqa: BLE001 — named, never shown as "no results"
        log.warning("improvements inbox: the results of accepted changes could not be read", exc_info=True)
        unreadable.append({"kind": RESULTS_KIND, "label": R.LABEL})
        return []
    try:
        labels = R.unreadable_labels(R.last_check(engine))
    except Exception:  # noqa: BLE001 — the last count's own record: named the same way
        log.warning("improvements inbox: the last count of the results could not be read", exc_info=True)
        labels = [R.LABEL]
    for label in labels:
        unreadable.append({"kind": RESULTS_KIND, "label": label})
    return rows


__all__ = [
    "DETAIL_CHARS", "GOALS_MAX", "KINDS", "LABELS", "OPEN_LABEL", "PER_KIND", "RESULTS_KIND", "SOURCES", "Read",
    "action", "change_results",
    "client_item", "client_view", "coach_item", "collect", "detail_diff", "detail_text", "folder_label", "item",
    "library_item", "memory_effect", "night_reasons",
    "memory_item", "preference_item", "skill_item", "tool_item", "unasked_item", "wants_item",
]
