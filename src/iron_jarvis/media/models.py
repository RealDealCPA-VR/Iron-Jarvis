"""The media job row (v1.346.0, contract item 2) — one generation that
finishes in the background and comes home to the chat it was asked in.

A row is written when :class:`~iron_jarvis.media.jobs.MediaJobs` takes a
started generation off a tool's hands, moves ``queued`` → ``running`` →
``succeeded`` | ``failed``, and keeps the delivered file's path and the
gallery artifact name so the chat line, the bell and ``GET /media/jobs`` all
read the same record. ``provider_job_id`` is the SERVICE's id (Pixio's
``contentId``): the boot reconcile re-polls by it and never starts a second
generation.

Importing this module registers the table on the shared SQLModel metadata
BEFORE ``init_db`` runs (``core.db._LATE_MODEL_MODULES`` imports it — the
v1.151.2 lesson: a lazily-created table lands on fresh test DBs and on no
real install). Every column is a plain str/datetime: no enum column, so a
status this process does not know can never poison a read on a newer DB.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
STATUSES = (QUEUED, RUNNING, SUCCEEDED, FAILED)
#: Rows the boot reconcile picks up: the process died while these were live.
LIVE_STATUSES = (QUEUED, RUNNING)


class MediaJobRecord(SQLModel, table=True):
    __tablename__ = "mediajob"

    id: str = Field(default_factory=lambda: new_id("mj"), primary_key=True)
    #: The provider id (``pixio`` / ``openai_images`` / ``fake``).
    provider: str = Field(default="", index=True)
    #: image | video | audio | music
    kind: str = ""
    model: str = ""
    prompt: str = ""
    #: JSON object: the params the generation was started with (never a key).
    params_json: str = "{}"
    status: str = Field(default=QUEUED, index=True)
    #: The saved chat the result comes home to ("" = an agent run: nothing
    #: is appended, only the events go out).
    thread_id: str = Field(default="", index=True)
    session_id: str = ""
    project_id: str = ""
    #: The SERVICE's job id — what the reconcile re-polls by.
    provider_job_id: str = ""
    #: The delivered file (absolute) and its Creative gallery name.
    output_path: str = ""
    artifact: str = ""
    #: Plain words when it failed (never a key).
    error: str = ""
    #: What it cost, in words ("free (Maker: 4 left today)", "12 credits").
    cost_note: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: datetime | None = None
