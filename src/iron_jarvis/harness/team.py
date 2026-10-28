"""THE CODING-SESSION TEAM (v1.403.0).

The user's decision (2026-10-28): "Lead plans and checks but never edits
files, Workers do the code, Reviewer checks every change. If no worker can
take a piece, the lead may do it and must say so."

WHY IT IS ENFORCED, NOT ASKED. A planner TOLD to hand work to its team handed
out nothing in 108 benchmark runs (v1.400.0's team-first words reached it and
changed nothing), and the user's "Try on several models" left each model doing
the whole task alone, unsure of its duties. So a session WITH A TEAM takes the
lead's hands away: the daemon does not arm a single tool that changes a file
or runs a program for it (:func:`lead_tools`), and what is armed is what the
model is shown (the parity rule) — the registry's ``allowed_names`` gate
refuses anything else "not armed".

THE ROLES (the contract's words: "Team lead", "Workers", "Reviewer"):

* the LEAD is the coding session's own model (its provider, model and
  account pin). It talks with the user, reads the project, writes the plan
  and what "done" means, splits the work into pieces and hands each one to a
  worker, checks each worker's report, answers the reviewer, and reports to
  the user;
* the WORKERS (1 or 2 model-team members) each take one piece at a time on
  their OWN model and account (``helpers.for_member``), in the session's
  folder and branch, under its posture, Local only and trust — with the
  coding tools the lead does not hold, and a short set of WORKER RULES in
  their system prompt (:func:`worker_rules`);
* the REVIEWER (0 or 1 member) checks every change before the reply ends
  (the v1.401.0 finish review — read-only), and ONCE more after the lead's
  workers fixed what it found (``harness/finish``: at most two reviews per
  reply).

THE ONE EXCEPTION. When a piece for a worker role (code / test / read) is
refused because no worker of the session can take it now — the team router's
own refusal (every worker at its account floor, its pay limit, unavailable,
Local only) — the lead's coding tools are UNLOCKED for the rest of that reply
(:meth:`TeamTurn.no_worker` → the lane's ``chat_turn.apply_team_unlock`` at
the next round), the refusal says so in one note, and the turn record lists it
(``lead_did``). A worker whose run FAILED does not unlock anything: the lead
hands the piece again, or to the other worker.

A session with no workers is a SOLO session — byte-identical to v1.402.0
(nothing here is read: :func:`current` is None).

THE DATA. ``setup.team`` (:data:`SETUP_KEY`) ``{"workers": [names], "reviewer":
name | ""}`` (:func:`clean_record` — the setup whitelist; :func:`validate` —
the routes' checks), and a per-folder default in
``BuildFolderRecord.team_json`` (:func:`folder_team`). A turn reads it through
:data:`SESSION_TEAM` (armed by ``routes/build.start_build_turn`` like the pin
and the hold — and by the benchmark's team runner), never from a client's
body: no HTTP field can make a chat turn a team turn.

Every reader here is cheap and never raises (a fact it cannot read is absent);
the BLOCKING ones say so and are called off the loop.
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: The Build session setup key (``routes/chat._clean_setup`` whitelists it).
SETUP_KEY = "team"
#: At most this many workers (the user's decision: one or two).
MAX_WORKERS = 2
#: A member name as stored (the agents registry's own names).
MAX_NAME_CHARS = 60
#: The role a worker must hold, and the roles a worker takes pieces of.
WORKER_ROLE = "code"
WORKER_ROLES: tuple[str, ...] = ("code", "test", "read")
#: The role a reviewer must hold.
REVIEWER_ROLE = "review"
#: The roles ``POST /team/members/from-model`` gives a member, by its role on
#: the session's team.
FROM_MODEL_ROLES: dict[str, tuple[str, ...]] = {
    "worker": ("code", "test", "read"),
    "reviewer": ("review",),
}
#: The words every refusal names the team by (the router's ``team_words``).
TEAM_WORDS = "this session's team"
#: How many "the lead did it itself" entries one reply keeps.
MAX_LEAD_DID = 10
#: One reason's length on the record (the helper row's own bound).
REASON_CHARS = 300

#: The member states a session view says (the contract's vocabulary).
STATE_READY, STATE_BUSY, STATE_LIMIT, STATE_UNAVAILABLE, STATE_UNKNOWN = (
    "ready", "busy", "limit", "unavailable", "unknown",
)


# --------------------------------------------------------------------------- #
# the stored record
# --------------------------------------------------------------------------- #


def _name(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:MAX_NAME_CHARS] if isinstance(raw, str) else ""


def clean_record(raw: Any) -> "dict[str, Any] | None":
    """``setup.team`` as STORED (the whitelist — the routes validate before
    they write it): ``{"workers": [1..2 names], "reviewer": name | ""}``.
    Strings only, trimmed, a name once; a reviewer who is also a worker is
    dropped; None when no worker remains (a solo session stores nothing)."""
    if not isinstance(raw, dict):
        return None
    workers: list[str] = []
    seen: set[str] = set()
    items = raw.get("workers")
    for item in items if isinstance(items, list) else []:
        name = _name(item)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            workers.append(name)
    workers = workers[:MAX_WORKERS]
    if not workers:
        return None
    reviewer = _name(raw.get("reviewer"))
    if reviewer.casefold() in seen:
        reviewer = ""
    return {"workers": workers, "reviewer": reviewer}


class TeamRefused(ValueError):
    """A team (or a member made from a model) refused in one plain sentence."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _registry(platform: Any) -> Any:
    return getattr(platform, "agents_registry", None)


def _record(platform: Any, name: str) -> Any:
    registry = _registry(platform)
    if registry is None or not name:
        return None
    try:
        return registry.get(name)
    except Exception:  # noqa: BLE001 — unreadable: not found
        return None


def validate(platform: Any, raw: Any) -> "dict[str, Any] | None":
    """The team a route may store, or :class:`TeamRefused` in one sentence:
    an unknown member, more than two workers, a worker without the Writing
    code role, a reviewer without the Reviewing role, a member named twice,
    or the same member as a worker and the reviewer. None (or no workers) is
    a SOLO session — None. BLOCKING (registry reads)."""
    from ..agents import model_team as MT

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TeamRefused("A team is a list of workers and, optionally, a reviewer.")
    items = raw.get("workers")
    if items is None:
        items = []
    if not isinstance(items, list) or not all(isinstance(w, str) for w in items):
        raise TeamRefused("A team's workers are a list of member names.")
    reviewer_raw = raw.get("reviewer")
    if reviewer_raw is not None and not isinstance(reviewer_raw, str):
        raise TeamRefused("A team's reviewer is one member's name.")
    workers = [_name(w) for w in items if _name(w)]
    if not workers:
        return None
    if len(workers) > MAX_WORKERS:
        raise TeamRefused(f"A session has at most {MAX_WORKERS} workers.")
    seen: set[str] = set()
    for name in workers:
        if name.casefold() in seen:
            raise TeamRefused(f"{name} is listed twice.")
        seen.add(name.casefold())
        rec = _record(platform, name)
        if rec is None or not MT.is_member(rec):
            raise TeamRefused(f"There is no member of your model team named {name}.")
        if WORKER_ROLE not in MT.roles_of(rec):
            raise TeamRefused(f"{name} cannot be a worker: it does not hold the Writing code role.")
    reviewer = _name(reviewer_raw)
    if reviewer:
        if reviewer.casefold() in seen:
            raise TeamRefused(f"{reviewer} cannot be both a worker and the reviewer.")
        rec = _record(platform, reviewer)
        if rec is None or not MT.is_member(rec):
            raise TeamRefused(f"There is no member of your model team named {reviewer}.")
        if REVIEWER_ROLE not in MT.roles_of(rec):
            raise TeamRefused(f"{reviewer} cannot be the reviewer: it does not hold the Reviewing a change role.")
    return {"workers": workers, "reviewer": reviewer}


# --------------------------------------------------------------------------- #
# the team a turn runs with
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SessionTeam:
    """The team ONE turn of a coding session runs with (member names)."""

    workers: tuple[str, ...]
    reviewer: str = ""

    @classmethod
    def of(cls, record: "dict[str, Any] | None") -> "SessionTeam | None":
        clean = clean_record(record)
        if clean is None:
            return None
        return cls(workers=tuple(clean["workers"]), reviewer=str(clean.get("reviewer") or ""))

    def members(self) -> list[str]:
        return [*self.workers, *([self.reviewer] if self.reviewer else [])]

    def is_worker(self, name: Any) -> bool:
        n = str(name or "").strip()
        if n.lower().startswith("custom:"):
            n = n.split(":", 1)[1].strip()
        return any(n.casefold() == w.casefold() for w in self.workers)

    def is_reviewer(self, name: Any) -> bool:
        n = str(name or "").strip()
        if n.lower().startswith("custom:"):
            n = n.split(":", 1)[1].strip()
        return bool(self.reviewer) and n.casefold() == self.reviewer.casefold()


def from_setup(setup: Any) -> "SessionTeam | None":
    """The team a session's setup names (None: a solo session)."""
    if not isinstance(setup, dict):
        return None
    return SessionTeam.of(setup.get(SETUP_KEY))


#: The team of the coding-session turn in flight (None: solo — every other
#: turn). Armed by ``routes/build.start_build_turn`` (and the benchmark's team
#: runner) while the turn's driver task is created, like the account pin and
#: the model hold; read once by ``chat_turn._build_harness``.
SESSION_TEAM: ContextVar["SessionTeam | None"] = ContextVar("ij_session_team", default=None)


def set_team(team: "SessionTeam | None") -> Any:
    return SESSION_TEAM.set(team)


def reset_team(token: Any) -> None:
    try:
        SESSION_TEAM.reset(token)
    except (ValueError, LookupError):
        SESSION_TEAM.set(None)


def current() -> "SessionTeam | None":
    try:
        return SESSION_TEAM.get()
    except Exception:  # noqa: BLE001
        return None


def resolve(platform: Any, team: "SessionTeam | None") -> "SessionTeam | None":
    """``team`` as it can run NOW: workers that are still members holding the
    Writing code role, a reviewer that still holds Reviewing (else none). A
    team with no worker left is no team (the turn runs solo). BLOCKING
    (registry reads); never raises."""
    if team is None:
        return None
    try:
        from ..agents import model_team as MT

        workers = tuple(
            w for w in team.workers
            if (rec := _record(platform, w)) is not None and WORKER_ROLE in MT.roles_of(rec)
        )
        if not workers:
            return None
        reviewer = team.reviewer
        if reviewer:
            rec = _record(platform, reviewer)
            if rec is None or REVIEWER_ROLE not in MT.roles_of(rec):
                reviewer = ""
        return SessionTeam(workers=workers, reviewer=reviewer)
    except Exception:  # noqa: BLE001 — unreadable: solo, never a half lock
        log.debug("coding-session team could not be read", exc_info=True)
        return None


# --------------------------------------------------------------------------- #
# the lead's tools
# --------------------------------------------------------------------------- #

#: The helper doors the lead KEEPS (the registry counts ``delegate`` /
#: ``spawn_agent`` as mutating — a hand-off can write — but they are exactly
#: how the lead gets the work done).
LEAD_KEEPS: frozenset[str] = frozenset({"delegate", "spawn_agent", "list_agents"})
#: Beside the registry's mutating tools and the chat's file-changing ones:
#: what stops a program, acts on a page or types into a terminal.
_LEAD_ALSO_LOCKED: frozenset[str] = frozenset({
    "process_stop", "page_click", "page_type", "pane_send", "pane_spawn",
    "delegate_remote", "workflow_run",
})


def lead_locked() -> frozenset[str]:
    """Every tool a team's lead never holds: anything that changes a file or
    runs something (``tools.registry._MUTATING_TOOLS``, the chat's
    ``_FILE_CHANGING_TOOLS`` and browser acting tools), a program's stop, a
    page click or typing — minus the helper doors (:data:`LEAD_KEEPS`)."""
    from ..tools.registry import _MUTATING_TOOLS

    try:
        from ..daemon.chat_turn import _BROWSER_ACTING_TOOLS, _FILE_CHANGING_TOOLS
    except Exception:  # noqa: BLE001 — never a wider lead: the core set stands
        _BROWSER_ACTING_TOOLS = _FILE_CHANGING_TOOLS = frozenset()  # type: ignore[assignment]
    return frozenset(
        (set(_MUTATING_TOOLS) | set(_FILE_CHANGING_TOOLS) | set(_BROWSER_ACTING_TOOLS)
         | set(_LEAD_ALSO_LOCKED)) - set(LEAD_KEEPS)
    )


def _readonly_app_tool(registry: Any, name: str) -> bool:
    try:
        tool = registry.get(name) if registry is not None else None
        return str(getattr(getattr(tool, "reversibility", ""), "value", "")) == "readonly"
    except Exception:  # noqa: BLE001 — not known read-only: it may change things
        return False


def lead_tools(names: Iterable[str], registry: Any = None) -> list[str]:
    """THE ONE RULE for what a team's lead holds: ``names`` (the turn's
    tools, in order) minus every tool that changes a file or runs something
    (:func:`lead_locked`) and minus an app's tool its server did not declare
    read-only. It keeps reading, searching and listing, a picture, the web's
    reads, the plan, the user's question, the skills and the helper doors."""
    locked = lead_locked()
    out: list[str] = []
    for name in names:
        n = str(name)
        if n in locked:
            continue
        if n.startswith("mcp__") and not _readonly_app_tool(registry, n):
            continue
        out.append(n)
    return out


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #

TEAM_BRIEF_HEADER = "# Your team"
TEAM_BRIEF_LEAD = (
    "You are the TEAM LEAD of this coding session. You plan and check; your workers do the "
    "code; your reviewer checks every change."
)
TEAM_BRIEF_JOB = (
    "Your job, in order:\n"
    "1. Understand what the user wants and what counts as done. Ask the user only about what "
    "they want, never about how to do the work.\n"
    "2. Split the work into pieces. For each piece write the files it may touch, what done "
    "means, and the tests to run.\n"
    "3. Give pieces that touch the same files to ONE worker, or hand them out one after "
    "another.\n"
    "4. Start pieces that do not depend on each other together, in one message, each to its "
    "own worker.\n"
    "5. Check each worker's report against the code: read the files it changed.\n"
    "6. When the reviewer says changes are needed, hand the fixes to a worker."
)
TEAM_BRIEF_HANDS = (
    "You do not edit files or run commands yourself: those tools are not yours in this "
    "session. Hand a piece to a worker by name, or ask for its role (role:code, role:test or "
    "role:read) and Iron Jarvis picks the worker. Only when Iron Jarvis tells you that no "
    "worker can take a piece may you do that piece yourself; then say in your reply that you "
    "did it yourself, and why."
)
NO_REVIEWER_LINE = "No reviewer is on this team: read each change yourself before you end."
#: The note a refusal of a worker piece carries when it UNLOCKS the lead.
UNLOCK_NOTE = (
    "No worker could take this: {reason} You may do this piece yourself now; say in your "
    "reply that you did it yourself and why."
)
#: What every helper door refuses a name outside the session's team with.
OFF_TEAM = (
    "'{who}' is not on this session's team. Hand this piece to a worker ({workers}), or ask "
    "for role:code, role:test or role:read{reviewer}."
)
NO_REVIEWER_REFUSAL = (
    "This session's team has no reviewer. Read the change yourself, or add a reviewer to the "
    "session's team."
)

WORKER_RULES_HEADER = "# Your part in this coding session's team"
WORKER_RULES = (
    "You are a WORKER on this coding session's team; the team lead hands you one piece at a "
    "time.\n"
    "- Do only the piece you were given. Touch only the files the piece names; if it needs "
    "more, change them and say so in your report.\n"
    "- Run the tests the piece names, and say what they showed.\n"
    "- End with a report: the files you changed, the tests you ran with their results, and "
    "anything you left undone."
)


def worker_rules() -> str:
    """The WORKER RULES block a worker's system prompt ends with."""
    return "\n\n" + WORKER_RULES_HEADER + "\n" + WORKER_RULES


def _roster(name: str) -> str:
    return f"custom:{name}"


def _and_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def brief(platform: Any, team: SessionTeam) -> str:
    """The lead's ``# Your team`` section (BEFORE the planner; not in the
    fingerprint — like the model team's list it replaces in a team turn).
    Each member's line is the roster's own (``roster.roster_block`` — the ONE
    source of a member's model, place, cost and roles). BLOCKING (cached
    reads); "" never — a team always has its job words."""
    from ..agents.roster import roster_block

    wanted = [_roster(n) for n in team.members()]
    try:
        block = roster_block(platform, limit=len(wanted) + 2, only=wanted)
    except Exception:  # noqa: BLE001 — the names alone, when the roster cannot be read
        log.debug("team brief roster failed", exc_info=True)
        block = ""
    lines = [ln for ln in block.split("\n")[1:] if ln.startswith("- ")] if block else []
    keys = sorted(wanted, key=len, reverse=True)

    def _line_for(name: str) -> str:
        key = _roster(name)
        for ln in lines:
            rest = ln[2:]
            got = next((k for k in keys if rest == k or rest.startswith(k + " ")), None)
            if got == key:
                return ln
        return f"- {key}"

    parts = [TEAM_BRIEF_HEADER, TEAM_BRIEF_LEAD, "Workers (each takes one piece at a time):"]
    parts += [_line_for(w) for w in team.workers]
    if team.reviewer:
        parts += ["Reviewer (checks every change; Iron Jarvis hands it the change before you end):",
                  _line_for(team.reviewer)]
    else:
        parts.append(NO_REVIEWER_LINE)
    parts += [TEAM_BRIEF_JOB, TEAM_BRIEF_HANDS]
    return "\n".join(parts)


def tool_sentence(input_schema: Any, team: "SessionTeam | None") -> str:
    """The sentence a helper tool's description LEADS with in a team turn,
    naming the session's workers (and reviewer) in that tool's own argument
    words: ``agent_type`` / ``agent`` + ``role`` (Jarvis's ``delegate`` /
    ``spawn_agent``), ``subagent_type`` (Claude Code's ``Agent``) or
    ``agent_type`` with ``role:<id>`` (Codex's ``spawn_agent``). "" for a
    tool that takes none of them. Never raises."""
    if team is None:
        return ""
    try:
        props = (input_schema or {}).get("properties") or {}
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(props, dict):
        return ""
    workers = _and_list([_roster(w) for w in team.workers])
    if "role" in props:
        key = next((k for k in ("agent_type", "agent") if k in props), "")
        name_how = f"{key} {workers}" if key else workers
        how = f"set {name_how}, or set role to code, test or read"
        rev = f"; the reviewer is {_roster(team.reviewer)} (role review)" if team.reviewer else ""
    else:
        key = next((k for k in ("subagent_type", "agent_type") if k in props), "")
        if not key:
            return ""
        how = f"give {key} a worker's name ({workers}) or role:code, role:test or role:read"
        rev = f"; the reviewer is {_roster(team.reviewer)} (role:review)" if team.reviewer else ""
    return f"You lead this session's team: hand each piece to a worker ({how}){rev}."


def lead_reason(refusal: Any, reason: Any = "") -> str:
    """The router's own sentence of why no worker could take a piece, as the
    record and the note say it: its refusal without the advice aimed at a
    planner that may do the piece ("Do this piece yourself."), one line,
    bounded, ending in a full stop."""
    from ..agents.model_team import PIECE_TAIL

    text = " ".join(str(refusal or "").split()) or " ".join(str(reason or "").split())
    for tail in (" Do this piece yourself, or hand it out again later.", " Do this piece yourself.",
                 PIECE_TAIL, " Do this piece yourself"):
        if text.endswith(tail):
            text = text[: -len(tail)].rstrip()
    text = text[:REASON_CHARS].rstrip()
    if text and text[-1] not in ".!?":
        text += "."
    return text or "No worker could take this piece now."


@dataclass
class TeamTurn:
    """ONE reply of a coding session with a team (``HarnessTurn.team``)."""

    team: SessionTeam
    #: The full coding set (the unlocked plan's tools): what a worker works
    #: with, and what the lead gets when it is unlocked.
    worker_tools: tuple[str, ...] = ()
    lead_label: str = ""
    worker_labels: tuple[str, ...] = ()
    reviewer_label: str = ""
    pieces: int = 0
    lead_did: list = field(default_factory=list)
    unlocked: bool = False
    _unlock_pending: bool = False

    # -- the doors ----------------------------------------------------------- #

    def route_names(self, role: str) -> list[str]:
        """The roster names the router may hand a piece of ``role`` to: the
        workers for code / test / read, the reviewer for a review."""
        if role == REVIEWER_ROLE:
            return [_roster(self.team.reviewer)] if self.team.reviewer else []
        return [_roster(w) for w in self.team.workers]

    def off_team(self, who: Any) -> str:
        """The refusal of a helper outside the session's team, or ""."""
        name = str(who or "").strip()
        if self.team.is_worker(name) or self.team.is_reviewer(name):
            return ""
        rev = f"; the reviewer is {_roster(self.team.reviewer)}" if self.team.reviewer else ""
        return OFF_TEAM.format(who=name or "that helper",
                               workers=", ".join(_roster(w) for w in self.team.workers), reviewer=rev)

    def no_worker(self, role: str, refusal: Any, reason: Any = "") -> str:
        """A worker piece (code / test / read) the router could not place:
        the lead is UNLOCKED for the rest of the reply (the lane applies it at
        its next round), the record says why, and the answer the lead reads
        is the note (:data:`UNLOCK_NOTE`). "" for any other role (a review
        that no one can take unlocks nothing)."""
        if role not in WORKER_ROLES:
            return ""
        why = lead_reason(refusal, reason)
        if len(self.lead_did) < MAX_LEAD_DID:
            self.lead_did.append({"reason": why})
        self._unlock_pending = True
        log.info("coding-session team: no worker could take a %s piece; the lead may do it (%s)", role, why)
        return UNLOCK_NOTE.format(reason=why)

    def note_piece(self, member: Any) -> None:
        """A piece really started on a member of the team."""
        if self.team.is_worker(member):
            self.pieces += 1

    def take_unlock(self) -> bool:
        """True ONCE, when the lead is to be unlocked now (the lane's
        ``apply_team_unlock`` then arms the coding tools in place)."""
        if not self._unlock_pending or self.unlocked:
            self._unlock_pending = False
            return False
        self._unlock_pending = False
        self.unlocked = True
        return True

    # -- the record ---------------------------------------------------------- #

    def record(self) -> dict[str, Any]:
        """The turn record's ``team`` (the contract's shape)."""
        return {
            "lead": self.lead_label,
            "workers": list(self.worker_labels),
            "reviewer": self.reviewer_label,
            "pieces": int(self.pieces),
            "lead_did": [dict(e) for e in self.lead_did],
        }


def label_of(platform: Any, provider: str, model: str) -> str:
    """A model's label in the turn record (the model team's own words)."""
    from ..agents.model_team import model_words

    try:
        return str(model_words(platform, provider, model) or model or provider)
    except Exception:  # noqa: BLE001
        return str(model or provider)


def member_label(platform: Any, name: str) -> str:
    from ..agents import model_team as MT

    rec = _record(platform, name)
    if rec is None:
        return name
    provider = str(getattr(rec, "provider", "") or "").strip()
    if not provider:
        return name
    return label_of(platform, provider, MT.pinned_model(platform, rec))


def turn_for(platform: Any, team: SessionTeam, *, worker_tools: Iterable[str],
             lead_provider: str, lead_model: str) -> TeamTurn:
    """The :class:`TeamTurn` of one reply. BLOCKING (registry reads)."""
    return TeamTurn(
        team=team,
        worker_tools=tuple(dict.fromkeys(str(t) for t in worker_tools)),
        lead_label=label_of(platform, lead_provider, lead_model),
        worker_labels=tuple(member_label(platform, w) for w in team.workers),
        reviewer_label=member_label(platform, team.reviewer) if team.reviewer else "",
    )


#: The turn record's keys, in order (a reply line keeps exactly these).
RECORD_KEYS = ("lead", "workers", "reviewer", "pieces", "lead_did")


def saved_record(raw: Any) -> "dict[str, Any] | None":
    """The done frame's ``team`` as a Build reply line keeps it, else None."""
    if not isinstance(raw, dict):
        return None
    lead = raw.get("lead")
    workers = raw.get("workers")
    if not isinstance(lead, str) or not isinstance(workers, list):
        return None
    pieces = raw.get("pieces")
    did = []
    for e in raw.get("lead_did") or []:
        if isinstance(e, dict) and isinstance(e.get("reason"), str) and e["reason"].strip():
            did.append({"reason": " ".join(e["reason"].split())[:REASON_CHARS]})
    reviewer = raw.get("reviewer")
    return {
        "lead": " ".join(lead.split())[:120],
        "workers": [" ".join(str(w).split())[:120] for w in workers if isinstance(w, str)][:MAX_WORKERS],
        "reviewer": " ".join(reviewer.split())[:120] if isinstance(reviewer, str) else "",
        "pieces": int(pieces) if isinstance(pieces, int) and not isinstance(pieces, bool) and pieces >= 0 else 0,
        "lead_did": did[:MAX_LEAD_DID],
    }


# --------------------------------------------------------------------------- #
# a session's view (route 5) — CACHED reads only
# --------------------------------------------------------------------------- #


def member_state(platform: Any, record: Any) -> str:
    """``ready`` | ``busy`` | ``limit`` | ``unavailable`` | ``unknown`` for a
    member, from CACHED answers only (a CLI's sign-in verdict is never
    refreshed, Iron-Proxy's snapshot never re-read, the fleet's last pass):
    paused or not connected → unavailable; a subscription account at its
    limit, or at the router's no-new-work mark → limit; a local box with
    requests waiting → busy. Never raises."""
    if record is None:
        return STATE_UNAVAILABLE
    try:
        from ..agents import model_team as MT
        from . import build_accounts as BA

        if str(getattr(record, "paused_reason", "") or "").strip():
            return STATE_UNAVAILABLE
        provider = str(getattr(record, "provider", "") or "").strip()
        if not provider:
            return STATE_UNKNOWN
        ready = MT.provider_ready(platform, provider)
        if ready is False:
            return STATE_UNAVAILABLE
        model = MT.pinned_model(platform, record)
        cost = MT.cost_type(platform, provider, model)
        if cost == MT.COST_PLAN:
            usage = MT.usage_for(platform, provider, str(getattr(record, "account", "") or ""))
            if usage:
                if usage.get("status") == BA.NEEDS_SIGN_IN:
                    return STATE_UNAVAILABLE
                if usage.get("status") == BA.LIMIT:
                    return STATE_LIMIT
                pct = usage.get("percent")
                if isinstance(pct, (int, float)) and not isinstance(pct, bool) and pct >= BA.NEW_WORK_MAX_PERCENT:
                    return STATE_LIMIT
        elif cost == MT.COST_FREE and MT.local_box(platform, provider) == "busy":
            return STATE_BUSY
        return STATE_READY if ready else STATE_UNKNOWN
    except Exception:  # noqa: BLE001
        return STATE_UNKNOWN


def _member_view(platform: Any, name: str) -> dict[str, Any]:
    from ..agents import model_team as MT

    rec = _record(platform, name)
    provider = str(getattr(rec, "provider", "") or "").strip() if rec is not None else ""
    model = MT.pinned_model(platform, rec) if rec is not None else ""
    return {
        "name": name,
        "provider": provider,
        "model": model,
        "label": label_of(platform, provider, model) if provider else name,
        "state": member_state(platform, rec) if rec is not None else STATE_UNAVAILABLE,
    }


def session_view(platform: Any, setup: Any) -> "dict[str, Any] | None":
    """A session view's ``team`` (the contract's route 5) — ONLY when a team
    is set (None otherwise, so a solo session's view is as before). CACHED
    reads only: a sync route calls it (FastAPI's threadpool). Never raises."""
    team = from_setup(setup)
    if team is None:
        return None
    try:
        from ..daemon.chat_turn import _harness_model
        from ..providers.cli_auth import cached_verdicts, never_probe

        prov, mod = _harness_model(platform, str(setup.get("provider") or ""), str(setup.get("model") or ""))
        with cached_verdicts(), never_probe():
            return {
                "lead": {"provider": prov, "model": mod, "label": label_of(platform, prov, mod)},
                "workers": [_member_view(platform, w) for w in team.workers],
                "reviewer": _member_view(platform, team.reviewer) if team.reviewer else None,
            }
    except Exception:  # noqa: BLE001 — the names alone, never a broken view
        log.debug("team view failed", exc_info=True)
        return {
            "lead": {"provider": "", "model": "", "label": ""},
            "workers": [{"name": w, "provider": "", "model": "", "label": w, "state": STATE_UNKNOWN}
                        for w in team.workers],
            "reviewer": ({"name": team.reviewer, "provider": "", "model": "", "label": team.reviewer,
                          "state": STATE_UNKNOWN} if team.reviewer else None),
        }


# --------------------------------------------------------------------------- #
# the folder's default team (BuildFolderRecord.team_json)
# --------------------------------------------------------------------------- #


def folder_team(engine: Any, folder: str) -> "dict[str, Any] | None":
    """The team a NEW session in ``folder`` starts with (None: none saved).
    BLOCKING; never raises."""
    from .sessions import folder_key

    key = folder_key(folder)
    if not key or engine is None:
        return None
    try:
        from ..core.db import session_scope
        from ..core.models import BuildFolderRecord

        with session_scope(engine) as db:
            row = db.get(BuildFolderRecord, key)
            raw = str(getattr(row, "team_json", "") or "") if row is not None else ""
        return clean_record(json.loads(raw)) if raw else None
    except Exception:  # noqa: BLE001
        return None


def set_folder_team(engine: Any, folder: str, record: "dict[str, Any] | None") -> "dict[str, Any] | None":
    """Save (or with None, clear) ``folder``'s default team. The folder's
    display name is untouched. BLOCKING."""
    from ..core.db import CONVERSATION_WRITE_LOCK, session_scope
    from ..core.ids import utcnow
    from ..core.models import BuildFolderRecord
    from .sessions import folder_key

    key = folder_key(folder)
    if not key:
        raise TeamRefused("Say which folder the team is for.")
    clean = clean_record(record) if record is not None else None
    with CONVERSATION_WRITE_LOCK, session_scope(engine) as db:
        row = db.get(BuildFolderRecord, key)
        if row is None:
            if clean is None:
                return None
            row = BuildFolderRecord(key=key, path=str(folder), name="")
        row.team_json = json.dumps(clean) if clean is not None else ""
        row.updated_at = utcnow()
        db.add(row)
        db.commit()
    return clean


# --------------------------------------------------------------------------- #
# a member made from a model (POST /team/members/from-model)
# --------------------------------------------------------------------------- #

MEMBER_PROMPT = (
    "You are a member of the user's model team. You are handed one piece of a larger piece "
    "of work: do exactly that piece, in the folder you are given, and report back plainly "
    "what you did, what you found and anything you could not finish."
)


def _catalog_row(catalog: Iterable[dict[str, Any]], provider: str, model: str) -> "dict[str, Any] | None":
    rows = [r for r in catalog or [] if str(r.get("provider") or "") == provider]
    if not rows:
        return None
    if not model:
        return next((r for r in rows if r.get("available")), rows[0])
    for r in rows:
        if str(r.get("model") or "") == model:
            return r
        aliases = r.get("aliases")
        if isinstance(aliases, list) and model in aliases:
            return r
    return None


def member_from_model(platform: Any, catalog: list[dict[str, Any]], *, provider: str, model: str,
                      role: str, local_only: bool = False) -> dict[str, Any]:
    """Make — or REUSE — the model-team member that runs on ``provider`` /
    ``model``, for a session's ``role`` ("worker" | "reviewer"). A member
    already on that model keeps everything it has (its prompt, account,
    allowance, notes) and GAINS the role's roles; else a new member named
    after the model (unique, slugged). :class:`TeamRefused` (409) for the demo
    model, a model Iron Jarvis cannot use or reach now, and a cloud model in a
    Local only session. BLOCKING."""
    from ..agents import model_team as MT

    want = FROM_MODEL_ROLES.get(str(role or "").strip().lower())
    if want is None:
        raise TeamRefused("A member is made as a worker or as the reviewer.", status=400)
    provider = str(provider or "").strip()
    model = str(model or "").strip()
    if not provider:
        raise TeamRefused("Say which model the member runs on.", status=400)
    if provider.lower() == "mock":
        raise TeamRefused(
            "The demo model cannot be on a team: its replies are scripted. Connect a model first.",
            status=409,
        )
    row = _catalog_row(catalog, provider, model)
    shown = "/".join(p for p in (provider, model) if p)
    if row is None:
        raise TeamRefused(f"{shown} is not one of the models Iron Jarvis can use right now.", status=409)
    if not row.get("available"):
        raise TeamRefused(f"{shown} is not connected right now, so it cannot join the team.", status=409)
    model = str(row.get("model") or model)
    label = str(row.get("name") or "") or MT.model_words(platform, provider, model)
    if local_only and not row.get("on_this_network"):
        raise TeamRefused(
            f"{label} is not on this PC or your network, and this session is Local only — nothing "
            "leaves your network. Pick a model of your own.",
            status=409,
        )
    registry = _registry(platform)
    if registry is None:
        raise TeamRefused("Custom agents are not available right now.", status=503)
    existing = next(
        (r for r in MT.team_members(platform)
         if str(getattr(r, "provider", "") or "").strip() == provider
         and str(getattr(r, "model", "") or "").strip() == model),
        None,
    )
    if existing is not None:
        roles = MT.clean_roles([*MT.roles_of(existing), *want])
        if roles != MT.roles_of(existing):
            try:
                tools = json.loads(getattr(existing, "tools_json", "") or "[]")
            except (TypeError, ValueError):
                tools = []
            # Every other field AS IT IS (the register upsert writes the
            # positional ones; the keyword ones keep the stored value).
            registry.register(
                existing.name, str(existing.system_prompt or ""), list(tools) if isinstance(tools, list) else [],
                base_type=str(existing.base_type or "builder"), description=str(existing.description or ""),
                provider=str(existing.provider or ""), model=str(existing.model or ""), roles=roles,
            )
        return {"name": existing.name, "provider": provider, "model": model, "label": label,
                "roles": roles, "created": False}
    taken = MT._taken_names(platform)
    name = MT._unique(MT._slug(label) or MT._slug(model) or MT._slug(provider), taken)
    registry.register(
        name, MEMBER_PROMPT, [], base_type="builder",
        description=f"{label} on your coding sessions' team",
        provider=provider, model=model, roles=list(want),
    )
    return {"name": name, "provider": provider, "model": model, "label": label,
            "roles": MT.clean_roles(list(want)), "created": True}


__all__ = [
    "FROM_MODEL_ROLES", "LEAD_KEEPS", "MAX_WORKERS", "OFF_TEAM", "REVIEWER_ROLE", "SESSION_TEAM",
    "SETUP_KEY", "SessionTeam", "TEAM_BRIEF_HEADER", "TEAM_WORDS", "TeamRefused", "TeamTurn",
    "UNLOCK_NOTE", "WORKER_ROLE", "WORKER_ROLES", "WORKER_RULES",
    "brief", "clean_record", "current", "folder_team", "from_setup", "lead_locked", "lead_reason",
    "lead_tools", "member_from_model", "member_state", "reset_team", "resolve", "saved_record",
    "session_view", "set_folder_team", "set_team", "tool_sentence", "turn_for", "validate",
    "worker_rules",
]
