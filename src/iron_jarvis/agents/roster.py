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
  (sessions / avg_score / success_rate / trend) by builtin agent type.

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

    def line(self) -> str:
        """One honest line, e.g. ``researcher — digger (87% over 23 runs)``.

        Stats ALWAYS carry the run count — never a bare percentage. No data
        renders ``(no runs yet)``; an unhealthy remote renders ``(offline)``.
        """
        head = self.name
        if self.description:
            head = f"{self.name} — {self.description}"
        return f"{_one_line(head)} {self._suffix()}"

    def _suffix(self) -> str:
        if not self.healthy:
            return "(offline)"
        stats = self.stats or {}
        try:
            sessions = int(stats.get("sessions") or 0)
        except (TypeError, ValueError):
            sessions = 0
        if sessions <= 0:
            return "(no runs yet)"
        runs = f"{sessions} run" + ("s" if sessions != 1 else "")
        rate = stats.get("success_rate")
        if isinstance(rate, (int, float)):
            return f"({round(float(rate) * 100)}% over {runs})"
        return f"({runs} so far)"

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready view (extension beyond the pinned API; includes line)."""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "delegable": self.delegable,
            "healthy": self.healthy,
            "stats": self.stats,
            "line": self.line(),
        }


# --- source readers (each fails soft to "contributes nothing") --------------


def _stats_by_type(platform) -> dict[str, dict]:
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
            name = str(view.get("agent_type") or "")
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


def _builtin_entries(stats_by_type: dict[str, dict]) -> list[RosterEntry]:
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
                stats=stats_by_type.get(name),
            )
        )
    return entries


def _dynamic_entries(platform) -> list[RosterEntry]:
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
            entries.append(
                RosterEntry(
                    name=f"custom:{name}",
                    kind="dynamic",
                    description=description,
                    # Verified spawn path — see the module docstring
                    # (SpawnAgentTool / POST /agents/{name}/spawn).
                    delegable=not coordinator,
                    healthy=True,
                    stats=None,  # outcomes accrue to the BASE type — honest None
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


def _remote_entries(platform) -> list[RosterEntry]:
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
            entries.append(
                RosterEntry(
                    name=f"remote:{name}",
                    kind="remote",
                    description=f"remote agent ({kind})",
                    # Headless ask path verified — RemoteAgentRegistry.run,
                    # used by reflex + delegate_remote (module docstring).
                    delegable=True,
                    healthy=healthy,
                    stats=None,  # no per-remote outcome tracking exists — honest
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
        entries = _builtin_entries(_stats_by_type(platform))
        entries.extend(_dynamic_entries(platform))
        entries.extend(_remote_entries(platform))
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
    the model knows they exist but won't pick them. Empty roster → ``""``."""
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
