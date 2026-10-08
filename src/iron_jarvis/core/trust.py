"""Session-level TRUST posture (v1.298.0, wave 4a).

A run is either FULL trust (the default — the user started it, or it was
started by something the user configured) or LOW trust: it was started from
an inbound channel message (``comm/inbound.py``), it is the child of a
low-trust run (delegate / spawn_agent / assign_work), or it READ content the
injection scanner flagged mid-run (the untrusted-content fence in
``agents/runtime.py`` and both chat lanes).

Low trust changes exactly one thing: the tools that WRITE DURABLE STATE the
NEXT run reads — memory, preferences, agents, skills, schedules, workflows,
queued work, a live PTY — are kept away. The run may still read, search,
write files in its folder and answer; it is told to SAY what it would have
changed instead. ``shell`` under low trust must run in the isolating
sandbox (Docker); the native fallback is refused, not advised.

Everything here is pure: no I/O, no platform. The posture is applied by the
runtime (``AgentRuntime``) and the two chat lanes; the row columns live on
``core.models.Session`` (``trust``, ``trust_reason``, ``tainted_at``).
"""

from __future__ import annotations

from typing import Iterable, Mapping

TRUST_FULL = "full"
TRUST_LOW = "low"

#: Tools a LOW-trust run may never call: each one writes state that outlives
#: the run and is read by a LATER run or by the user as if it were theirs.
#: Every name here is a registered tool (``tests/test_trust_posture_v1298``
#: pins that against the real registry). ``self_dev`` was in the brief and
#: is NOT a tool (self-dev is a session flag, ``create_session(self_dev=)``),
#: so it is not listed. ``pane_send``/``pane_spawn`` already sit on the deny
#: floor for agent definitions; listing them here closes the per-session
#: grant path too (``low_trust_overrides`` writes a hard deny, which no
#: grant lifts).
LOW_TRUST_DENY: frozenset[str] = frozenset(
    {
        # Calm UI redesign S3/S4: a run started by an inbound message never
        # changes a setting or asks for a credential.
        "config_set",
        "config_change",
        "config_change_protected",
        "config_secret",
        # Redesign S5: nor a schedule, workflow, channel or app.
        "schedule_update",
        "schedule_delete",
        "workflow_update",
        "workflow_delete",
        "workflow_schedule",
        "channel_toggle",
        "channel_connect",
        "app_connect",
        # ...nor a webhook, sentinel, goal or reflex rule (each is unattended
        # work a later signal starts), nor "undo that" — an undo can put back
        # a trigger the user had removed. ``grant_revoke`` stays ALLOWED: it
        # only takes an always-allow away (every later call asks again), and
        # its Undo is the user's press on the card, never a low run's tool.
        "webhook_update",
        "webhook_delete",
        "sentinel_update",
        "sentinel_delete",
        "goal_update",
        "goal_delete",
        "reflex_create",
        "reflex_update",
        "reflex_delete",
        "config_undo",
        "create_agent",
        "remember_preference",
        "skill_create",
        "assign_work",
        "schedule_create",
        "workflow_create",
        "memory_write",
        "ltm_append",
        "notebook",
        "memory_propose",
        "capability_propose",
        "pane_send",
        "pane_spawn",
        # Reviewer additions (wave 4a): more state that outlives the run and
        # that a LATER run (or the user) reads as if it were theirs —
        # a dynamic tool, a secret, a webhook/sentinel/goal trigger, a queued
        # worklist item — and ``workflow_run``, whose steps start sessions
        # the workflow engine does not stamp low (a laundering path).
        "tool_create",
        "tool_delete",
        "secret_set",
        "webhook_add",
        "sentinel_add",
        "goal_add",
        "worklist_add",
        "workflow_run",
        # A low-trust run must not hand what it read to ANOTHER MACHINE: the
        # remote has no fence, no scanner and no trust posture of its own.
        "delegate_remote",
    }
)

#: The ONE sentence a run that starts low reads in its system prompt. A run
#: tainted MID-run gets nothing appended (prompt caching) — the refusal text
#: on its next kept-away call is its signal.
LOW_TRUST_PROMPT = (
    "This run is in low trust: it must not change memory, settings, agents or "
    "skills — say what you would have changed instead."
)

#: What the shell tool answers when low trust asks for the sandbox and only
#: the native runtime is reachable. A refusal, never an advisory.
LOW_TRUST_SHELL_REFUSAL = "low trust needs the sandbox; Docker is not reachable"


def normalize_trust(value: object) -> str:
    """``"low"`` → ``TRUST_LOW``; anything else (``""``, ``None``, ``"full"``,
    junk) → ``TRUST_FULL``. The one normaliser every door goes through."""
    return TRUST_LOW if str(value or "").strip().lower() == TRUST_LOW else TRUST_FULL


def low_trust_overrides(
    base: Mapping[str, str] | None,
    names: Iterable[str] = LOW_TRUST_DENY,
) -> dict[str, str]:
    """``base`` merged with ``{name: "deny"}`` for every kept-away name.

    Deny WINS: a base ``allow``/``ask`` on a listed name becomes ``deny``, a
    base ``deny`` on any name stays ``deny``, and nothing not listed changes —
    so this can only ever NARROW what a run may do, never widen it. ``names``
    lets the caller add the registry's permission KEYS beside the tool names
    (the engine authorises on ``perm_key()``).
    """
    merged: dict[str, str] = {str(k): str(v) for k, v in dict(base or {}).items()}
    for name in names:
        if name:
            merged[str(name)] = "deny"
    return merged


def taint_reason(tool: str, category: str | None) -> str:
    """One sentence for ``Session.trust_reason`` when a run's own reading
    lowered it: ``read content flagged as <category> from <tool>``."""
    cat = " ".join(str(category or "suspicious content").replace("_", " ").split())
    return f"read content flagged as {cat} from {tool or 'a tool'}"


def effective_trust(session_trust: object, tainted: bool) -> str:
    """The posture a call runs under NOW: the row's trust, lowered to
    ``TRUST_LOW`` once this run has been tainted."""
    if tainted:
        return TRUST_LOW
    return normalize_trust(session_trust)


def low_trust_refusal(tool: str, tainted: bool) -> str:
    """The sentence a kept-away call is refused with. ``tainted`` names the
    cause the model can act on (it READ something), the other wording is for
    a run that was low from its door."""
    if tainted:
        return (
            f"this run is in low trust after reading flagged content — "
            f"{tool} is kept away"
        )
    return f"this run is in low trust — {tool} is kept away"


def kept_away(armed: Iterable[str]) -> list[str]:
    """The names in ``armed`` (order kept) that low trust keeps away."""
    return [str(t) for t in armed if str(t) in LOW_TRUST_DENY]


def low_trust_note(count: int) -> str:
    """The one ledger-visible note a chat lane records when it dropped
    kept-away tools from its armed set."""
    return f"low trust: {int(count)} tool{'s' if count != 1 else ''} kept away"
