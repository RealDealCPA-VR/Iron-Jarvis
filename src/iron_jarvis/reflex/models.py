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
REFLEX_SOURCES = ("webhook", "comm", "email", "calendar", "slack")
#: What firing does.
REFLEX_ACTIONS = ("workflow", "remote_agent", "session")


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
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    last_fired_at: datetime | None = None
    fire_count: int = 0
