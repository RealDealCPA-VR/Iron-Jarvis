"""Reflex rules — the durable bindings that make Iron Jarvis *ambient*.

A :class:`ReflexRule` binds an inbound SIGNAL (an external webhook firing, or a
keyword in an inbound comm message) to an ACTION (run a saved workflow, delegate
to a remote agent, or start a supervised session). The :class:`ReflexRouter`
consults these on every signal; the rules persist so the reflexes survive a
restart. Everything is off by default — a rule only exists because the user
created it — and every action still runs through the normal permission engine.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: Where the signal comes from. CX-05 ("inbound everything") extends the loop
#: beyond webhooks/comm so the WORLD can start work: an inbound email, a calendar
#: event coming due, or a Slack message can each fire a rule. Every source still
#: runs the SAME gated action path — a remote signal gets no more power than a
#: local one.
REFLEX_SOURCES = ("webhook", "comm", "email", "calendar", "slack", "file", "download", "run_ended")
#: What firing does.
REFLEX_ACTIONS = ("workflow", "remote_agent", "session")

#: Leap 05-a (D-05.3): how many times one rule may start in an hour before it
#: pauses itself and says so (once a day per rule).
MAX_FIRES_PER_HOUR = 12

#: Leap 05-b: what kind of task a ``run_ended`` rule listens to
#: (``start_json.what``): one of your schedules, a workflow, a rule's run, an
#: agent run, or any of them.
RUN_ENDED_WHATS = ("schedule", "workflow", "rule", "agent", "any")
#: How that task must have ended (``start_json.outcome``). ``any`` is any way
#: it fell short: failed, part failed, needs you, or did nothing. A task that
#: was done, or that a person stopped, starts nothing.
RUN_ENDED_OUTCOMES = ("failed", "part_failed", "needs_you", "any")
#: Left out, a rule listens for a task that FAILED.
RUN_ENDED_DEFAULT_OUTCOME = "failed"


class ReflexRule(SQLModel, table=True):
    """One durable signal→action binding (the Reflex Loop's unit of automation)."""

    id: str = Field(default_factory=lambda: new_id("reflex"), primary_key=True)
    name: str = ""
    #: "webhook" | "comm".
    source: str = Field(default="webhook", index=True)
    #: What the signal must contain to fire. For webhook: the exact inbound
    #: webhook slug. For every text-carrying source (comm/email/calendar/slack):
    #: a keyword matched case-insensitively as a whole word in the signal text
    #: (email = subject+body, calendar = title+description, slack = message text);
    #: an EMPTY keyword matches every signal of that source.
    match: str = ""
    #: "workflow" | "remote_agent" | "session".
    action: str = "workflow"
    #: The workflow name / remote-agent name. Unused for a bare "session" action.
    target: str = ""
    #: Task text for session/remote_agent actions. Supports {body}/{text}/{slug}
    #: placeholders filled from the triggering signal. Empty → a sensible default.
    task_template: str = ""
    #: Context spine: the project this rule's work is grounded in (v1.200.0).
    #: A "client emailed the missing 1099" reflex should run with that client's
    #: brief/instructions/knowledge, not with zero context. None = ungrounded
    #: (the pre-v1.200.0 behavior, and still the default). Additive column —
    #: existing DBs self-heal via ``core.db._reconcile_additive_columns``.
    project_id: str | None = Field(default=None, index=True)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    last_fired_at: datetime | None = None
    fire_count: int = 0
    #: v1.231.0 (audit AE6): what the LAST fire did, on the row the user reads.
    #: ``last_error`` is the reason the most recent matched signal could not
    #: start the action ("no saved workflow 'x'"); ``last_result`` is the
    #: kind/id of the last successful start. A fire that failed used to leave
    #: no trace on the rule at all. Additive columns — existing DBs self-heal
    #: via ``core.db._reconcile_additive_columns`` (same as ``project_id``).
    last_error: str | None = None
    last_result: str | None = None
    #: Readiness 0j.5 item 15: WHERE the last successful start can be seen
    #: (``/sessions/<id>``, ``/workflows``, the team page), beside
    #: ``last_result``, which now holds the start in plain words ("Started
    #: an agent run."). None on a row older than this, and on one that never
    #: started anything. Additive column (auto-reconciled).
    last_href: str | None = None
    #: Leap 06-a: when the user pressed Keep it on a rehearsal of this rule
    #: (None = never). Additive column (auto-reconciled).
    rehearsed_at: datetime | None = None
    #: Leap 05-a: what the start needs beyond ``match``, by source (JSON):
    #: ``{"path", "glob", "per_file"}`` for ``file`` (the folder it watches;
    #: its watcher is a ``rule``-mode SentinelRecord naming this rule) and
    #: ``{"match_site"}`` for ``download``. Leap 05-b: ``{"what", "name",
    #: "outcome"}`` for ``run_ended`` (another task ended short: which kind,
    #: its name or "" for any of that kind, and how it ended). "" for every
    #: other source.
    start_json: str = ""
    #: The custom agent (its roster name) the work is done by; "" keeps the
    #: supervisor, as before. Resolved at fire time the way a schedule's is
    #: (``agents/resolve.resolve_for_unattended``).
    agent_name: str = ""
    #: The folder the work runs IN ("" = the run's own folder). A file rule
    #: made from chat works in the folder it watches unless told otherwise.
    workspace_root: str = ""
    #: Idea 39: the saved chat this rule was made in; its runs report there
    #: (``agents/chat_delivery``). "" = it reports nowhere.
    chat_thread_id: str = Field(default="", index=True)
    #: D-05.3: starts allowed in one hour before the rule pauses itself.
    max_fires_per_hour: int = MAX_FIRES_PER_HOUR
    #: When (UTC, ISO) each start of the last hour happened (JSON list).
    recent_starts_json: str = ""
    #: Set when the rule paused itself (the hourly limit); cleared by Resume.
    #: A paused rule starts nothing; a file rule's files wait for it.
    paused_at: datetime | None = None
    paused_reason: str | None = None
    #: When the pause was last SAID (bell): at most once a day per rule.
    pause_told_at: datetime | None = None

    def decoded_start(self) -> dict:
        """``start_json`` as a dict (junk reads ``{}``)."""
        import json

        try:
            data = json.loads(self.start_json or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
