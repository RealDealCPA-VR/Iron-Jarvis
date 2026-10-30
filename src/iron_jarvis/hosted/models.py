"""The Code view's session row (v1.409.0) — a late model
(``core.db._LATE_MODEL_MODULES``).

One row per Code-view session: which harness, which folder, which account
(and the HOME its CLI runs from, so a resume reaches the same saved
conversation), the model / effort / permission mode the user chose and the
harness's OWN session id (``claude --resume <id>`` / ``codex resume <id>``
opens the same conversation in a terminal). What the harness did lives in
``<home>/hosted/<id>/events.jsonl`` (:mod:`.events`), never in this row.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow


class HostedSessionRecord(SQLModel, table=True):
    """One Code-view session."""

    __tablename__ = "hostedsessionrecord"

    id: str = Field(default_factory=lambda: new_id("app"), primary_key=True)
    #: ``claude-code`` | ``codex``.
    harness: str = Field(default="", index=True)
    #: The folder the harness runs in (validated by ``fs_policy.root_problem``).
    #: "" until the first message when the session started with NO folder.
    folder: str = Field(default="", index=True)
    #: v1.412.0: the session started with no folder chosen: its first message
    #: makes it a folder of its own under the chat files folder (the v1.379.0
    #: rule) and ``folder`` names it from then on. NULL on older rows = False.
    no_folder: bool | None = Field(default=False)
    title: str = ""
    #: Iron-Proxy profile id the session runs on; "" = this PC's own login.
    account: str = ""
    #: The account as a Build pane records it (``pane_accounts`` record:
    #: id, title, source, home, unset) — JSON. The HOME is what makes a later
    #: process resume the SAME conversation; never shown in a row.
    account_json: str = ""
    model: str = ""
    effort: str = ""
    #: ``ask`` | ``edits`` | ``plan`` | ``full`` (see ``hosted.claude.MODES``
    #: / ``hosted.codex.MODES``).
    mode: str = "ask"
    #: The harness's own session / thread id, once it said it.
    harness_session_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    archived_at: datetime | None = None
    #: The last failure in plain words ("" = none).
    last_error: str = ""
    #: The Build terminal "Open in terminal" started on this conversation;
    #: the row takes no message while that terminal's CLI still runs.
    terminal_id: str = ""
