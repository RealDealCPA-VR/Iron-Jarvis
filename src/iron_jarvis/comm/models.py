"""Inbound-comm persistence model (two-way comm, made durable).

``InboundOffsetRecord`` is the durable last-seen polling offset per channel
*registration* (e.g. the Telegram ``getUpdates`` offset). Persisting it means a
daemon restart resumes from where it left off and never reprocesses an already
handled message.

Importing this module before ``init_db`` registers the table on
``SQLModel.metadata`` so it auto-creates with the rest of the schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class InboundOffsetRecord(SQLModel, table=True):
    """The durable inbound poll offset for one channel registration."""

    #: the channel's registration name in the notifier (e.g. ``"tg"``).
    channel: str = Field(primary_key=True)
    offset: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class PendingPromptRecord(SQLModel, table=True):
    """One question waiting for a REMOTE identity's answer (v1.137.0).

    Registered when a machine-side gate needs the human — today ``kind ==
    "workflow_ask"`` (a workflow run parked at an ask step); the shape leaves
    room for ``"review"`` (approve/reject) later. ``ref_id`` is the gated
    thing's id (the workflow run id). ``options_json`` is a JSON list of
    choice strings — a numbered reply ("1") picks the matching option; it is
    ``"[]"`` when the gate is free-text (workflow ask steps carry no options
    today, so answering those takes ``/answer <text>`` — or a bare pure
    integer, which resolves as the literal answer).

    ``status``: ``open`` (answerable) | ``answered`` (this identity resolved
    it) | ``expired`` (the run un-parked by other means before any reply) |
    ``superseded`` (a newer prompt replaced it, or the answer lost the
    first-answer-wins claim race). Writes go through
    :class:`~iron_jarvis.comm.prompts.PendingPromptStore` (single lock, same
    discipline as :class:`CommIdentityRecord`).
    """

    id: str = Field(default_factory=lambda: new_id("pp"), primary_key=True)
    kind: str = "workflow_ask"
    ref_id: str = Field(index=True)  # e.g. the WorkflowRunRecord id
    question: str = ""  # capped by the store (see _QUESTION_CAP)
    options_json: str = "[]"
    channel: str = Field(index=True)  # notifier registration name
    sender_id: str = Field(index=True)  # channel-native sender id, as str
    thread_id: str = ""  # the comm thread the prompt line landed on
    status: str = Field(default="open", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None


class CommIdentityRecord(SQLModel, table=True):
    """One remote sender's binding to a chat thread (messaging surfaces).

    ``(channel, sender_id)`` → the daemon-owned ``ChatThreadRecord`` that
    sender talks in, so a phone conversation has continuity instead of
    spawning amnesiac one-shots. There is AT MOST ONE row per
    ``(channel, sender_id)``: SQLite gains no unique constraint via the
    additive reconciler (SQLModel ``create_all`` only shapes NEW tables), so
    uniqueness is enforced by lookup discipline — every create/retire goes
    through :class:`~iron_jarvis.comm.threads.CommThreadStore`'s single
    ``threading.Lock``, which serializes the find-or-create. Do not insert
    rows around the store.

    ``/new`` semantics: retiring DELETES this row (the thread survives,
    unbound) so the sender's next message mints a fresh thread.
    """

    id: str = Field(default_factory=lambda: new_id("cid"), primary_key=True)
    channel: str = Field(index=True)  # notifier registration name, e.g. "telegram"
    sender_id: str = Field(index=True)  # channel-native sender id, stored as str
    thread_id: str = ""  # the bound ChatThreadRecord id
    display_name: str = ""  # human label ("Val"); falls back to sender_id
    created_at: datetime = Field(default_factory=utcnow)
