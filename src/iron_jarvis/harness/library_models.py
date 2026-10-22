"""The experience library's row (v1.370.0, IJ harness plan Phase 7).

One row per thing the library holds for a PROJECT FOLDER (keyed by
``harness.sessions.folder_key``): a ``lesson`` (one evidence-supported
sentence or few lines a reflector wrote after a Build session's turn did real
work) or a ``layer_edit`` (a proposed change to one of the folder's harness
files, ``<folder>/.ironjarvis/harness/<file>``).

NOTHING IS ADOPTED WITHOUT THE USER (Harness² principle 9, the v1.297.0 coach
rule): every row is minted ``proposed``. An ACCEPTED lesson rides the next
sessions in that folder (``harness/library.library_lines``); an accepted
layer edit is WRITTEN into the folder's file at that moment, and only then.
A declined row suppresses the same signature for 14 days; "forget" deletes an
accepted lesson.

Lessons live HERE, in the app's own database — never in the user's folder
unless the user accepts an edit that puts something there.

Importing this module registers the table on the shared metadata;
``core.db._LATE_MODEL_MODULES`` imports it before ``init_db`` (the v1.151.2
lesson). Every column is a plain str / datetime: a status this process does
not know can never poison a read on a newer database.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

PROPOSED = "proposed"
ACCEPTED = "accepted"
DECLINED = "declined"
STALE = "stale"
STATUSES = (PROPOSED, ACCEPTED, DECLINED, STALE)

KIND_LESSON = "lesson"
KIND_LAYER_EDIT = "layer_edit"
KINDS = (KIND_LESSON, KIND_LAYER_EDIT)

#: How long a declined signature stays suppressed (the coach's 14 days).
DECLINE_SUPPRESS_DAYS = 14


class HarnessLibraryRecord(SQLModel, table=True):
    __tablename__ = "harnesslibrary"

    id: str = Field(default_factory=lambda: new_id("lib"), primary_key=True)
    #: ``sessions.folder_key`` of the project folder (case-folded, "/").
    folder_key: str = Field(default="", index=True)
    #: The folder as the session spelled it (for the screen; never in an event).
    folder: str = ""
    #: ``lesson`` | ``layer_edit``.
    kind: str = KIND_LESSON
    status: str = Field(default=PROPOSED, index=True)
    #: The lesson (kind lesson), or the one-line reason for the edit.
    text: str = ""
    #: ``layer_edit``: the harness file it changes (``memory.md``, …).
    file: str = ""
    #: ``layer_edit``: the file's text when proposed ("" = no file yet) — an
    #: Accept refuses honestly when the file moved since (``stale``).
    before: str = ""
    #: ``layer_edit``: the proposed full text of the file.
    after: str = ""
    #: JSON: {"paths": [...], "quotes": [...], "stripped": [...]} — the claims
    #: the ledger check verified and the ones it removed.
    evidence_json: str = "{}"
    #: sha256(folder_key | kind | file | normalised text) — the dedupe key.
    signature: str = Field(default="", index=True)
    #: The Build session and turn it came from.
    thread_id: str = Field(default="", index=True)
    turn_id: str = ""
    #: The model that wrote it.
    provider: str = ""
    model: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
