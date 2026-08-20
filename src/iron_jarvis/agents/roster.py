"""Capability roster — one honest view of every agent that can take work.

Pure, read-only composition over EXISTING data (no new tables, no scoring):

* built-in agent types (:mod:`.types` ``_DEFINITIONS``) with a one-line
  strength derived from each definition's role text;
* dynamic agents (``platform.agents_registry`` —
  :class:`~iron_jarvis.agents.dynamic.DynamicAgentRegistry`) as
  ``custom:<name>``;
* remote agents (:class:`~iron_jarvis.agents.remote.RemoteAgentRegistry`)
  as ``remote:<name>``;
* measured stats joined from ``platform.improvement.stats()["agents"]``
  (sessions / avg_score / success_rate / trend) BY ROSTER NAME (v1.193.0 — it
  was by builtin agent type, so every agent the user created reported
  ``stats=None`` forever and a supervisor could pick the right ROLE but never
  the right AGENT). ``AgentStatRecord`` is keyed by that same name and a
  builtin's name IS its type string, so builtins keep their existing history
  and nothing was migrated. The lookup is case-insensitive, but the WRITER
  should store ``entry.name`` verbatim (see the NAME CONTRACT below).
* LIVENESS (v1.193.0) — ``activity`` says whether that agent is working right
  now, read from the orchestrator's IN-MEMORY ``_running`` / ``_governed`` /
  ``_queued`` state and resolved to names through the session rows those ids
  point at (a bounded primary-key read, capped at ``_LIVENESS_MAX`` ids; no
  network, no scan). A missing orchestrator, a poisoned one, or a session the
  roster cannot name degrades to ``"unknown"`` — never to a guess, never to a
  raise. ``"unknown"`` and ``"idle"`` both render as NO marker: absence of a
  marker is not a claim that anyone is free.

WHO RAN: TWO SIGNALS, ONE PREDICATE (v1.193.0). A ``Session`` row's
``agent_type`` records only the builtin a run EXECUTED as, so nothing on it
tells ``custom:tax-reader`` apart from the ``builder`` it is based on — which
is why a user-created teammate used to read "(no runs yet)" forever while its
runs were credited to the base type. :func:`resolve_roster_name` resolves it in
this precedence order:

1. **The explicit column.** ``Session.agent_name`` carries the roster name
   verbatim and is stamped by every door that RESOLVED one — most importantly
   ``POST /agents/{name}/spawn``, the Agents page's Run button, which is THE
   most common way a user runs their own agent and which publishes no
   delegation event at all (before this column it was credited to the base
   type, i.e. the exact defect this section exists to remove) — plus
   ``delegate`` and ``spawn_agent``.
2. **The delegation ledger.** Both handoff doors publish ``delegation.started``
   with ``{child_session_id, agent}`` BEFORE the child runs
   (``delegate_tool.publish_delegation_started``) and ``platform.py`` persists
   every event to ``EventRecord``; :func:`ledger_roster_name` reads it back.
   This still covers rows written before the column existed, and any future
   door that publishes the event without stamping the row.
3. Failing both, the bare ``agent_type`` — the pre-v1.193.0 behaviour, and
   exactly right for a builtin (a builtin's roster name IS its type string).

The two doors disagree about the STRING (delegate hands out the prefixed
``custom:<slug>``, spawn the bare slug), so both fold through
:func:`canonical_roster_name` or one teammate's history splits across two keys.

CROSS-UNIT DEPENDENCY, DECLARED. Half of that ladder is owned elsewhere:
:func:`_delegation_names` reads ``EventType.DELEGATION_STARTED`` by identity and
the payload keys ``child_session_id`` and ``agent`` BY NAME. If the delegate
door renames either key, or stops publishing, nothing here fails loudly — every
read sits behind its own ``except`` and ``_delegation_names`` simply returns
``{}``. THAT IS A DEGRADATION RATHER THAN A CLIFF ONLY BECAUSE STEP 1 EXISTS:
rows stamped with ``agent_name`` keep their true attribution and only un-stamped
rows fall back to base-type attribution. Without the column the same rename
would have silently returned EVERY ``custom:*`` agent to "(no runs yet)" — the
original defect, restored invisibly. Any change to that payload therefore
belongs in the same change set as a change here.

LIVENESS BLIND SPOT, STATED PLAINLY. ``activity`` sees only what the
ORCHESTRATOR knows about: sessions it started under ``spawn_managed``, sessions
parked in ``_queued``, and the ``run_session`` lane that self-registers into
``_running``/``_governed``. ``delegate`` and ``spawn_agent`` call
``AgentRuntime.run`` DIRECTLY — their children never enter any of those sets, so
a delegated child is invisible to this signal for its entire life. That is the
uncomfortable part: the fan-out saturation a busy marker would most help a
supervisor avoid (one coordinator handing work to eight teammates at once) is
precisely the case it CANNOT see. The two available fixes are both worse than
the gap. Routing children through the governor deadlocks (the parent is blocked
awaiting the child while holding a slot — ``orchestrator.child_slot`` works
through the cycle in full), and registering them in ``_running`` for display
only hands ``cancel_session`` and the slot-free promotion hook handles that do
not belong to them. Reading ACTIVE ``Session`` rows instead was rejected too: it
is an unindexed scan on the event loop inside prompt composition, and a run
stranded ACTIVE by a crash would report its agent busy forever. So the limit
stands, documented, rather than papered over — absence of a "busy" marker was
never a claim that anyone is free, and for a delegated child it is not even
evidence.

DELEGABILITY (verified capability, never aspiration):

* **builtin** — delegable iff its definition does not itself carry the
  ``delegate`` tool (supervisor AND, since v1.166.0, planner — the
  generalized anti-fork-bomb rule; mirrors delegate_tool).
* **dynamic** — delegable unless its stored tool list carries ``delegate``
  or its base type is the supervisor (delegate_tool would refuse either —
  offering them would be aspiration, not capability). Otherwise a REAL spawn
  path exists and is already exercised in production. Call path (Pair S
  consumes this):
  ``SpawnAgentTool.execute`` (``agents/agent_tools.py``) does
  ``platform.agents_registry.definition(name)`` →
  ``Orchestrator(platform).create_session(task, definition.type, ...)`` →
  ``AgentRuntime(platform).run(child_session, definition, ...)``; the HTTP
  twin is ``POST /agents/{name}/spawn`` (``daemon/routes/agents.py``) which
  runs the identical ``registry.definition → orchestrator.create_session →
  AgentRuntime.run`` sequence. A dynamic agent therefore runs as a full
  session under its base ``AgentType`` with its own prompt + tool allowlist.

NAME CONTRACT (the exact transform Pair S must apply to spawn a roster
target): take ``resolve_target(...)``'s **returned entry's** ``name``, strip
everything up to and including the first ``:`` (``entry.name.partition(":")
[2]`` for dynamic/remote; builtins have no prefix and pass through as-is),
and hand that remainder VERBATIM to the spawn path. The remainder IS the
registry key: ``DynamicAgentRegistry.definition()`` / ``.get()`` and
``RemoteAgentRegistry.get()`` both look up the exact stored ``record.name``
— case-sensitively, un-slugified. Never strip the CALLER's raw query
instead: ``resolve_target`` matches case-insensitively, so the query's
casing may not equal the stored name, and only ``entry.name`` (which
preserves the record's original casing) is guaranteed to resolve.
* **remote** — delegable=True while enabled: the headless ask path is
  ``RemoteAgentRegistry.run(record, task, secret_resolver)``
  (``agents/remote.py``), already used without a human in the loop by the
  reflex router (``reflex/router.py::_run_remote``), by the
  ``delegate_remote`` tool, and by ``POST /agents/remote/{name}/run``.

HEALTH: there is NO persisted probe result for remotes — the only live probe
is the on-demand ``POST /agents/remote/{name}/test`` → ``RemoteAgentRegistry
.test`` (network). The roster NEVER performs network calls: ``healthy``
defaults to the stored ``enabled`` flag (a disabled remote cannot be run —
the run route 400s and ``delegate_remote`` refuses), and a health check is
injectable as a ``platform.remote_health`` callable ``(record) -> bool`` for
tests or a future cached probe. A raising health callable falls back to the
enabled flag rather than knocking agents offline. Remote records may likewise
be injected as ``platform.remote_agents`` (any object with ``.list()``);
absent that, the registry is built from ``platform.engine`` as the routes do.

Every reader is defensive: ``build_roster`` NEVER raises, even on a
half-built platform (``None`` attributes, poisoned registries) — a broken
source simply contributes nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .types import _DEFINITIONS

#: One-line strengths for the builtin types, distilled from each
#: definition's role prompt in :mod:`.types` (kept to ~8 words).
_BUILTIN_STRENGTHS: dict[str, str] = {
    "builder": "hands-on doer for files, shell, and documents",
    "planner": "breaks goals into plans, delegates and schedules work",
    "reviewer": "careful second look at correctness and risk",
    "supervisor": "coordinates specialist subagents on multi-part goals",
    "researcher": "gathers and synthesizes findings from files and web",
    "memory": "curates and tidies the platform's stored knowledge",
    "maintainer": "carefully edits and tests Iron Jarvis's own code",
    "automation": "wires up schedules, webhooks, workflows, and integrations",
}

#: Character cap for one rendered line inside :func:`roster_block` — keeps
#: the whole block ≤ ~1200 chars at the 14-entry limit.
_BLOCK_LINE_CHARS = 74

_BLOCK_HEADER = "# Who can take this work"

#: Hard cap on how many in-flight session ids the liveness read will resolve.
#: The orchestrator's live sets are small by construction (they are gated by
#: ``max_concurrent_sessions``), but a roster read sits inside prompt
#: composition on the event loop, so the DB work it can ever do is BOUNDED.
_LIVENESS_MAX = 32

#: ``RosterEntry.activity`` values. "unknown" is the honest default — the
#: roster never claims an agent is free, only ever that one is taken.
_BUSY, _QUEUED, _UNKNOWN = "busy", "queued", "unknown"

#: Casefolded builtin type strings, from the roster's OWN source of builtin
#: truth (:data:`~iron_jarvis.agents.types._DEFINITIONS`) rather than a second
#: hand-kept list. Used by :func:`canonical_roster_name` — a builtin name is
#: already a canonical roster name and must never be folded into ``custom:``.
_BUILTIN_NAMES: frozenset[str] = frozenset(t.value.casefold() for t in _DEFINITIONS)


def _one_line(text: Any) -> str:
    """Collapse any whitespace runs (incl. newlines/tabs) to single spaces.

    Descriptions come from user-authored records; a newline inside one would
    escape the roster block's bullet list and land in the prompt as a bare
    line — a formatting break AND a mild injection surface.
    """
    return " ".join(str(text or "").split())


@dataclass
class RosterEntry:
    name: str          # "builder" | "custom:<slug>" | "remote:<name>"
    kind: str          # "builtin" | "dynamic" | "remote"
    description: str   # one line
    delegable: bool    # a session CAN actually be spawned on it (verified)
    healthy: bool      # remotes: live status; builtin/dynamic: True
    stats: dict | None  # {"sessions", "avg_score", "success_rate", "trend"}
    #: Liveness (v1.193.0): "busy" | "queued" | "idle" | "unknown". Additive
    #: with a default so every existing construction keeps working.
    activity: str = _UNKNOWN

    def line(self) -> str:
        """One honest line, e.g. ``researcher — digger (87% over 23 runs)``.

        Stats ALWAYS carry the run count — never a bare percentage. No data
        renders ``(no runs yet)``; an unhealthy remote renders ``(offline)``;
        an agent that is working right now leads with ``(busy, …)``.
        """
        head = self.name
        if self.description:
            head = f"{self.name} — {self.description}"
        return f"{_one_line(head)} {self._suffix()}"

    def _suffix(self) -> str:
        if not self.healthy:
            return "(offline)"
        # Liveness leads, the track record follows, inside ONE pair of parens —
        # the honesty rule is unchanged: a percentage never appears without its
        # sample size, whoever is busy.
        live = f"{self.activity}, " if self.activity in (_BUSY, _QUEUED) else ""
        stats = self.stats or {}
        try:
            sessions = int(stats.get("sessions") or 0)
        except (TypeError, ValueError):
            sessions = 0
        if sessions <= 0:
            return f"({live}no runs yet)"
        runs = f"{sessions} run" + ("s" if sessions != 1 else "")
        rate = stats.get("success_rate")
        if isinstance(rate, (int, float)):
            return f"({live}{round(float(rate) * 100)}% over {runs})"
        return f"({live}{runs} so far)"

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready view (extension beyond the pinned API; includes line)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "delegable": self.delegable,
            "healthy": self.healthy,
            "stats": self.stats,
            "activity": self.activity,
            "line": self.line(),
        }


# --- source readers (each fails soft to "contributes nothing") --------------


def _stats_by_name(platform) -> dict[str, dict]:
    """Measured stats keyed by CASEFOLDED roster name (``_stat_for`` reads it).

    ``"name"`` is the v1.193.0 key; ``"agent_type"`` is the older one and still
    carries the same string for every builtin, so an improvement engine from
    before this wave joins exactly as it always did.
    """
    improvement = getattr(platform, "improvement", None)
    if improvement is None:
        return {}
    try:
        views = improvement.stats().get("agents") or []
    except Exception:  # noqa: BLE001 — a broken stats read costs stats, not the roster
        return {}
    joined: dict[str, dict] = {}
    for view in views:
        try:
            name = _norm(view.get("name") or view.get("agent_type") or "")
            if not name:
                continue
            joined[name] = {
                "sessions": int(view.get("sessions") or 0),
                "avg_score": view.get("avg_score"),
                "success_rate": view.get("success_rate"),
                "trend": view.get("trend"),
            }
        except Exception:  # noqa: BLE001 — skip one malformed view, keep the rest
            continue
    return joined


def _stat_for(stats: dict[str, dict], name: str) -> dict | None:
    """The measured record for one roster name, or an honest ``None``."""
    return stats.get(_norm(name))


def session_roster_name(session) -> str:
    """The roster name a ``Session`` row proves it ran as. NEVER raises.

    THE ONE PREDICATE for session → roster name: the roster reads it for
    liveness and :mod:`iron_jarvis.improvement.engine` writes stats with it, so
    a run shows up under the same key it is measured under. Two copies of this
    rule would drift the moment one side learned about a new namespace.

    A session row carries the builtin ``agent_type`` it EXECUTED as plus, since
    v1.193.0, the ``agent_name`` the creating door resolved. ``agent_name`` is
    still read defensively (``getattr``): the suite builds bare
    ``SimpleNamespace`` rows, and an EMPTY value is the normal state of every
    row written before the column landed and of every session created by a door
    that knows no roster name. Absent it the honest answer is the builtin type,
    which is exactly right for a builtin and a documented under-attribution for
    anything else — :func:`resolve_roster_name` is the caller that can do better
    by consulting the delegation ledger.
    """
    try:
        named = str(getattr(session, "agent_name", "") or "").strip()
        if named:
            return named
        agent_type = getattr(session, "agent_type", "")
        return str(getattr(agent_type, "value", agent_type) or "")
    except Exception:  # noqa: BLE001 — an unreadable row simply has no name
        return ""


def _dynamic_slugs(platform) -> set[str]:
    """Casefolded names of the dynamic agents that exist right now. NEVER raises."""
    registry = getattr(platform, "agents_registry", None)
    if registry is None:
        return set()
    try:
        return {
            _norm(getattr(record, "name", ""))
            for record in registry.list()
            if str(getattr(record, "name", "") or "").strip()
        }
    except Exception:  # noqa: BLE001 — a poisoned registry simply names nothing
        return set()


def canonical_roster_name(platform, raw: Any, *, slugs: set[str] | None = None) -> str:
    """Fold a HANDOFF TARGET string onto the roster's own name. NEVER raises.

    The two doors disagree by construction: ``delegate`` publishes the roster
    entry's prefixed name (``"custom:tax-reader"``), ``spawn_agent`` publishes
    the BARE slug it was called with (``"tax-reader"``). Left alone that splits
    one teammate's history across two keys, so both fold here — using the same
    dynamic-beats-builtin predicate ``SpawnAgentTool`` itself resolves with
    (``registry.definition(name)`` first, ``AgentType(name)`` second).

    A BUILTIN NAME IS NEVER FOLDED. Nothing reserves the builtin names —
    ``DynamicAgentRegistry.register`` and ``POST /agents`` both accept
    ``name="researcher"`` — and ``resolve_target`` matches builtins FIRST, so
    delegating to the builtin researcher publishes the bare ``"researcher"``.
    Folding that into ``custom:researcher`` because a same-named dynamic record
    happens to exist would credit the builtin's run to the custom agent and mark
    the wrong roster entry busy: a FALSE track record, which is worse than no
    track record and is the one thing this attribution must never produce. So a
    bare name that IS a builtin type value is returned unchanged.

    THE RESIDUAL AMBIGUITY IS NAMED, NOT HIDDEN: ``spawn_agent`` resolves
    dynamic-FIRST, so its bare ``"researcher"`` may well mean the shadowing
    custom agent, and this rule credits it to the builtin instead. The event
    payload carries no marker saying which door resolved it, and inventing one
    is a change in a file this module does not own (see the module docstring's
    cross-unit note). Under-crediting a shadowing custom agent is the safe side
    of that coin — and it is moot for any run whose ``Session.agent_name`` was
    stamped, which is every run through the doors as of v1.193.0.

    ``slugs`` is the pre-read dynamic-name set, so resolving a whole batch of
    ids costs ONE registry listing rather than one per id.
    """
    try:
        name = " ".join(str(raw or "").split())
        if not name:
            return ""
        low = name.casefold()
        if low.startswith("custom:") or low.startswith("remote:"):
            return name
        if low in _BUILTIN_NAMES:
            return name
        known = _dynamic_slugs(platform) if slugs is None else slugs
        if _norm(name) in known:
            return f"custom:{name}"
        return name
    except Exception:  # noqa: BLE001 — an unfoldable target is simply unknown
        return ""


def _delegation_names(platform, ids: list[str]) -> dict[str, str]:
    """child session id → the ROSTER NAME it was handed off as. NEVER raises.

    See the module docstring ("WHO RAN"): ``delegation.started`` is the only
    persisted place a dynamic teammate's own name survives a handoff, because
    the ``Session`` row keeps just the builtin type it executed as.

    BOUNDED BY CONSTRUCTION: ``EventRecord.type`` is indexed so the LIKE only
    ever runs over delegation rows, the id set is capped at ``_LIVENESS_MAX``,
    and the payload match is RE-CHECKED in Python — a substring hit inside some
    other field can therefore never mis-attribute a run.
    """
    engine = getattr(platform, "engine", None)
    wanted: list[str] = []
    for sid in ids or ():
        text = str(sid or "").strip()
        if text and text not in wanted:
            wanted.append(text)
    wanted = wanted[:_LIVENESS_MAX]
    if engine is None or not wanted:
        return {}
    try:
        from sqlalchemy import or_
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.events import EventType
        from ..core.models import EventRecord

        slugs = _dynamic_slugs(platform)
        out: dict[str, str] = {}
        with session_scope(engine) as db:
            rows = db.exec(
                select(EventRecord)
                .where(EventRecord.type == EventType.DELEGATION_STARTED)
                .where(
                    or_(
                        *[
                            EventRecord.payload_json.contains(sid)  # type: ignore[attr-defined]
                            for sid in wanted
                        ]
                    )
                )
                .order_by(EventRecord.created_at)  # type: ignore[arg-type]
            )
            for row in rows:
                try:
                    payload = json.loads(row.payload_json or "{}")
                    child = str(payload.get("child_session_id") or "").strip()
                    if child not in wanted:
                        continue
                    target = canonical_roster_name(
                        platform, payload.get("agent"), slugs=slugs
                    )
                    if target:
                        out[child] = target
                except Exception:  # noqa: BLE001 — one odd row, keep the rest
                    continue
        return out
    except Exception:  # noqa: BLE001 — no ledger, no names; the roster is unharmed
        return {}


def ledger_roster_name(platform, session_id: str) -> str:
    """The roster name ONE session was handed off as, or ``""``. NEVER raises.

    The public door onto :func:`_delegation_names` for a single id — used by
    :mod:`iron_jarvis.improvement.engine` so a run is MEASURED under the same
    key the roster DISPLAYS it under.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    return _delegation_names(platform, [sid]).get(sid, "")


def resolve_roster_name(platform, session) -> str:
    """THE roster name for one session row: explicit column → LEDGER → type.

    :func:`session_roster_name` answers what the ROW alone can prove; this
    answers what the PLATFORM can prove, which is the question both the stats
    writer and the liveness read actually have.
    """
    try:
        explicit = str(getattr(session, "agent_name", "") or "").strip()
        if explicit:
            return canonical_roster_name(platform, explicit)
        sid = str(getattr(session, "id", "") or "").strip()
        if sid:
            named = ledger_roster_name(platform, sid)
            if named:
                return named
    except Exception:  # noqa: BLE001 — fall through to what the row can prove
        pass
    return session_roster_name(session)


def _live_session_ids(platform) -> tuple[list[str], list[str]]:
    """``(busy_ids, queued_ids)`` from the orchestrator's in-memory state."""
    orch = getattr(platform, "orchestrator", None)
    if orch is None:
        return [], []
    busy: list[str] = []
    queued: list[str] = []
    try:
        # ``_running`` also holds non-session background work (workflow runs,
        # slack handlers); those ids simply resolve to no session row and drop
        # out. ``_governed`` is the session-only denominator — a governed id
        # holds a concurrency slot even in the instant it is not in _running.
        for sid in list(getattr(orch, "_running", {}) or {}):
            busy.append(str(sid))
        for sid in list(getattr(orch, "_governed", ()) or ()):
            busy.append(str(sid))
    except Exception:  # noqa: BLE001 — a poisoned orchestrator means "unknown"
        return [], []
    try:
        for entry in list(getattr(orch, "_queued", ()) or ()):
            try:
                queued.append(str(entry[0]))
            except Exception:  # noqa: BLE001 — skip one odd entry, keep the rest
                continue
    except Exception:  # noqa: BLE001
        queued = []
    return busy, queued


def _activity_by_name(platform) -> dict[str, str]:
    """Map casefolded roster name → ``"busy"``/``"queued"``. NEVER raises.

    Everything absent from this map is ``"unknown"``: the roster reports who is
    TAKEN, and never asserts that anyone is free.
    """
    try:
        busy, queued = _live_session_ids(platform)
        if not busy and not queued:
            return {}
        # BUSY FIRST, because the tail is what ``_LIVENESS_MAX`` drops: with the
        # queued ids leading, a backlog longer than the cap would evict every
        # actually-RUNNING id and report each working agent as not-busy while
        # marking queued ones. Ordering here is purely about what survives the
        # cap; key precedence is enforced by the two loops below.
        ids: list[str] = []
        for sid in busy + queued:
            if sid and sid not in ids:
                ids.append(sid)
        ids = ids[:_LIVENESS_MAX]
        names = _session_names(platform, ids)
        activity: dict[str, str] = {}
        for sid in queued:  # …and busy LAST: a running id must win the key
            name = names.get(sid)
            if name:
                activity.setdefault(_norm(name), _QUEUED)
        for sid in busy:
            name = names.get(sid)
            if name:
                activity[_norm(name)] = _BUSY
        return activity
    except Exception:  # noqa: BLE001 — liveness is a bonus, never a liability
        return {}


def _session_names(platform, ids: list[str]) -> dict[str, str]:
    """Resolve session ids to roster names with TWO bounded reads. NEVER raises.

    The primary-key read over ``Session`` says what each ROW can prove; the
    delegation ledger says who the run was handed TO. Without the second read a
    running ``custom:tax-reader`` marks the BUILTIN ``builder`` busy — a false
    statement about builder, and no signal at all about the teammate a
    supervisor is actually choosing between. An explicit ``Session.agent_name``
    outranks the ledger; an id with no session row is never named from the
    ledger alone (``_running`` also holds workflow/comm work).
    """
    engine = getattr(platform, "engine", None)
    if engine is None or not ids:
        return {}
    out: dict[str, str] = {}
    explicit: set[str] = set()
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import Session

        with session_scope(engine) as db:
            rows = db.exec(select(Session).where(Session.id.in_(ids)))  # type: ignore[attr-defined]
            for row in rows:
                name = session_roster_name(row)
                if name:
                    out[str(row.id)] = name
                if str(getattr(row, "agent_name", "") or "").strip():
                    explicit.add(str(row.id))
    except Exception:  # noqa: BLE001 — no DB, no names; the roster is unharmed
        return {}
    try:
        for sid, target in _delegation_names(platform, ids).items():
            if sid in out and sid not in explicit:
                out[sid] = target
    except Exception:  # noqa: BLE001 — the ledger is a bonus, never a liability
        pass
    return out


def _builtin_entries(
    stats_by_name: dict[str, dict], activity: dict[str, str]
) -> list[RosterEntry]:
    entries = []
    for agent_type, definition in _DEFINITIONS.items():
        name = agent_type.value
        entries.append(
            RosterEntry(
                name=name,
                kind="builtin",
                description=_BUILTIN_STRENGTHS.get(name, "general-purpose agent"),
                # Anti-fork-bomb, generalized (v1.166.0): an agent whose
                # definition carries `delegate` can itself delegate — the
                # supervisor AND the planner — so offering it as a delegation
                # target invites coordinator-to-coordinator fan-out (mirrors
                # delegate_tool's standing rule).
                delegable="delegate" not in (definition.tools or []),
                healthy=True,
                stats=_stat_for(stats_by_name, name),
                activity=activity.get(_norm(name), _UNKNOWN),
            )
        )
    return entries


def _dynamic_entries(
    platform, stats_by_name: dict[str, dict], activity: dict[str, str]
) -> list[RosterEntry]:
    registry = getattr(platform, "agents_registry", None)
    if registry is None:
        return []
    try:
        records = list(registry.list())
    except Exception:  # noqa: BLE001 — poisoned registry contributes nothing
        return []
    entries = []
    for record in records:
        try:
            name = str(getattr(record, "name", "") or "")
            if not name:
                continue
            base = _one_line(getattr(record, "base_type", "")) or "builder"
            description = _one_line(getattr(record, "description", ""))
            if not description:
                description = f"custom agent (base {base})"
            # Anti-fork-bomb, generalized (v1.166.0): a dynamic agent whose
            # stored tool list carries `delegate`, or whose base type is the
            # supervisor, would be REFUSED at delegation time — listing it as
            # delegable would be aspiration, not verified capability.
            # READ THE COMPOSED DEFINITION, NOT THE RAW ROW (v1.178.0). This
            # asked the STORED list, and since v1.178.0 an empty stored list
            # means "not specified" and INHERITS the base type's roster. A
            # dynamic agent with base_type="planner" and no stored tools
            # therefore holds `delegate` (types.py, since v1.166.0) while this
            # row still said delegable=True — so the roster advertised a
            # delegation `delegate_tool` would refuse, which is precisely the
            # aspiration-not-capability this block exists to prevent. Reachable
            # via the `create_agent` tool, which accepts base_type.
            definition = None
            try:
                definition = registry.definition(name)
            except Exception:  # noqa: BLE001 — one bad record must not kill the roster
                definition = None
            if definition is not None:
                tools = list(definition.tools)
            else:
                try:
                    tools = json.loads(getattr(record, "tools_json", "") or "[]")
                except (TypeError, ValueError):
                    tools = []
            coordinator = (
                isinstance(tools, list) and "delegate" in tools
            ) or base.casefold() == "supervisor"
            entry_name = f"custom:{name}"
            entries.append(
                RosterEntry(
                    name=entry_name,
                    kind="dynamic",
                    description=description,
                    # Verified spawn path — see the module docstring
                    # (SpawnAgentTool / POST /agents/{name}/spawn).
                    delegable=not coordinator,
                    healthy=True,
                    # v1.193.0: a teammate the USER created can finally earn a
                    # track record — outcomes are keyed by THIS name now, not by
                    # the base type it happens to execute as. Still None until
                    # it has actually run, which renders "(no runs yet)".
                    stats=_stat_for(stats_by_name, entry_name),
                    activity=activity.get(_norm(entry_name), _UNKNOWN),
                )
            )
        except Exception:  # noqa: BLE001 — skip one bad record, keep the rest
            continue
    return entries


def _remote_records(platform) -> list:
    registry = getattr(platform, "remote_agents", None)  # injectable seam
    if registry is None:
        engine = getattr(platform, "engine", None)
        if engine is None:
            return []
        try:
            from .remote import RemoteAgentRegistry

            registry = RemoteAgentRegistry(engine)
        except Exception:  # noqa: BLE001
            return []
    try:
        return list(registry.list())
    except Exception:  # noqa: BLE001 — poisoned registry contributes nothing
        return []


def _remote_entries(
    platform, stats_by_name: dict[str, dict], activity: dict[str, str]
) -> list[RosterEntry]:
    health = getattr(platform, "remote_health", None)  # injectable seam
    entries = []
    for record in _remote_records(platform):
        try:
            name = str(getattr(record, "name", "") or "")
            if not name:
                continue
            enabled = bool(getattr(record, "enabled", True))
            healthy = enabled
            if healthy and callable(health):
                try:
                    healthy = bool(health(record))
                except Exception:  # noqa: BLE001 — broken probe falls back to enabled
                    healthy = enabled
            kind = _one_line(getattr(record, "kind", "")) or "http-task"
            entry_name = f"remote:{name}"
            entries.append(
                RosterEntry(
                    name=entry_name,
                    kind="remote",
                    description=f"remote agent ({kind})",
                    # Headless ask path verified — RemoteAgentRegistry.run,
                    # used by reflex + delegate_remote (module docstring).
                    delegable=True,
                    healthy=healthy,
                    # v1.193.0: a remote ask opens no Session, so its history
                    # comes from ImprovementEngine.record_agent_outcome under
                    # this exact key. None until one is recorded.
                    stats=_stat_for(stats_by_name, entry_name),
                    activity=activity.get(_norm(entry_name), _UNKNOWN),
                )
            )
        except Exception:  # noqa: BLE001 — skip one bad record, keep the rest
            continue
    return entries


# --- the pinned API ---------------------------------------------------------


def build_roster(platform) -> list[RosterEntry]:
    """Compose every known agent into roster entries. NEVER raises; worst
    case (all sources broken) is the builtin list, or ``[]``."""
    try:
        stats = _stats_by_name(platform)
        activity = _activity_by_name(platform)
        entries = _builtin_entries(stats, activity)
        entries.extend(_dynamic_entries(platform, stats, activity))
        entries.extend(_remote_entries(platform, stats, activity))
        return entries
    except Exception:  # noqa: BLE001 — the roster must never take a caller down
        return []


def _clamp(text: str, limit: int = _BLOCK_LINE_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _block_line(entry: RosterEntry) -> str:
    """Budgeted line for the prompt block: the DESCRIPTION is clamped, the
    stats/offline suffix never is — a truncated "(87% over 2…" would render
    the bare percentage the honesty rules forbid."""
    suffix = entry._suffix()
    head = entry.name
    if entry.description:
        head = f"{entry.name} — {entry.description}"
    return f"{_clamp(_one_line(head), _BLOCK_LINE_CHARS - len(suffix) - 1)} {suffix}"


def roster_block(platform, *, limit: int = 14) -> str:
    """Compact prompt block. Only healthy + delegable entries are listed;
    unhealthy remotes collapse into one trailing ``offline: ...`` note so
    the model knows they exist but won't pick them. Empty roster → ``""``.

    A busy or queued agent is still LISTED (it can take the work, just not
    yet) and says so in its suffix — ``builder — … (busy, 87% over 23 runs)``
    — so a supervisor can choose someone else or wait instead of delegating
    blind into a saturated queue."""
    entries = build_roster(platform)
    main = [e for e in entries if e.delegable and e.healthy][: max(0, limit)]
    offline = [e.name for e in entries if e.kind == "remote" and not e.healthy]
    if not main and not offline:
        return ""
    lines = [_BLOCK_HEADER]
    lines.extend("- " + _block_line(e) for e in main)
    if offline:
        lines.append(_clamp("offline: " + ", ".join(offline)))
    return "\n".join(lines)


def delegable_names(platform) -> list[str]:
    """Names a delegation can actually target right now (healthy + delegable)."""
    return [e.name for e in build_roster(platform) if e.delegable and e.healthy]


def _norm(name: str) -> str:
    try:
        text = str(name or "").strip()
    except Exception:  # noqa: BLE001 — an unstringable target is just unknown
        return ""
    if ":" in text:
        head, _, tail = text.partition(":")
        text = f"{head.strip()}:{tail.strip()}"
    return text.casefold()


def resolve_target(
    platform, name, *, require_delegable: bool = True
) -> RosterEntry | None:
    """Resolve a (model- or user-supplied) target name to a roster entry.

    Case-insensitive, trims whitespace (including around a ``custom:`` /
    ``remote:`` colon), and accepts the bare slug for prefixed entries.
    Returns ``None`` for unknown, offline, or non-delegable targets — the
    caller keeps its default in that case.

    ``require_delegable=False`` (v1.166.0) is for CONVERSATION surfaces (the
    ``@mention`` panel): a coordinator like planner/supervisor cannot take
    delegated WORK (fork-bomb rule) but can absolutely be talked to — the
    mentionable catalog lists them, so the panel must resolve them too.
    """
    query = _norm(name)
    if not query:
        return None
    entries = build_roster(platform)
    found: RosterEntry | None = None
    for entry in entries:  # exact (prefixed or builtin) name first
        if _norm(entry.name) == query:
            found = entry
            break
    if found is None and ":" not in query:  # bare slug for custom:/remote:
        for entry in entries:
            _pre, sep, slug = entry.name.partition(":")
            if sep and slug.strip().casefold() == query:
                found = entry
                break
    if found is None or not found.healthy:
        return None
    if require_delegable and not found.delegable:
        return None
    return found
