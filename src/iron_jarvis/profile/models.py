"""The user profile record (v1.144.0) — ONE row, the identity spine.

Every surface that talks to the user (chat, the streamed chat, phone/Telegram,
agent sessions, the round table) renders this same record into its system
prompt, so switching model — 14B local to a cloud frontier model — cannot
change how Iron Jarvis writes.

Deliberately ONE ROW (``id="me"``): this app is a single-user local OS, and a
multi-row table would immediately raise "which profile is active?" at every
seam. A future multi-user story adds a scope column; nothing here forecloses it.

The fields are split into three groups, which is exactly how
:mod:`iron_jarvis.profile.block` renders them:

* **who** — ``about`` (free text the user writes about themselves),
* **how to answer** — the preference fields (tone / formatting / reading level
  / length / language / accessibility) plus ``formatting_rules`` free text,
* **voice** — ``voice_card``, a short description of how the USER writes, which
  v1.145.0's "Train Jarvis on me" derives from their own writing samples. The
  column lands here so the renderer has one stable shape from the start; until
  that wave it is simply empty.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: The single profile row's primary key. Not user-visible.
PROFILE_ID = "me"


class UserProfileRecord(SQLModel, table=True):
    """The user's persistent profile — one row, id :data:`PROFILE_ID`."""

    id: str = Field(default=PROFILE_ID, primary_key=True)

    #: Master switch. OFF renders NOTHING at any seam (the escape hatch for
    #: "this profile is making answers worse" that doesn't require deleting it).
    enabled: bool = True

    # --- who ---------------------------------------------------------------
    #: Free text: role, expertise, what they work on, how to address them.
    about: str = ""

    # --- how to answer -----------------------------------------------------
    #: Preset key from ``presets.TONES`` or free text used verbatim.
    tone: str = ""
    #: Preset key from ``presets.WRITING_STYLES`` or free text used verbatim.
    writing_style: str = ""
    #: Preset key from ``presets.FORMATTING``.
    formatting: str = ""
    #: Free text: the user's own formatting rules, always appended verbatim.
    #: This is what makes the accessibility presets CUSTOMIZABLE — applying a
    #: preset fills this field, and the user edits it afterwards.
    formatting_rules: str = ""
    #: Preset key from ``presets.READING_LEVELS``.
    reading_level: str = ""
    #: Preset key from ``presets.RESPONSE_LENGTHS``.
    response_length: str = ""
    #: Preset key from ``presets.ACCESSIBILITY`` ("" = none).
    accessibility: str = ""

    # --- language ----------------------------------------------------------
    #: ISO-639-1 code from ``language.LANGUAGES``; "" = no constraint (the
    #: model answers in whatever language the user wrote in, today's behaviour).
    language: str = ""
    #: When a language IS set, also CHECK the reply and ask once for a rewrite
    #: on script-level leakage. Instruction-only when False.
    enforce_language: bool = True

    # --- voice (populated by v1.145.0 "Train Jarvis on me") ------------------
    #: A short description of how the USER writes, imitated for tone/rhythm.
    voice_card: str = ""
    #: Provenance: where the voice card came from (e.g. "3 writing samples").
    voice_source: str = ""

    updated_at: datetime = Field(default_factory=utcnow)


class WritingSampleRecord(SQLModel, table=True):
    """One piece of the user's own writing, kept so the voice card can be
    RE-derived (v1.145.0).

    Samples are kept rather than consumed for a reason: a derived voice card is
    a summary, and a summary you cannot re-run is a summary you cannot correct.
    Adding a fourth sample and pressing Derive again must produce a better card,
    not a card built on three samples plus a paraphrase of the fourth.

    They are NOT knowledge: a sample is evidence about STYLE, never grounding
    for answers, so it deliberately does not join the memory fabric or the
    history index. Anything the user wants Iron Jarvis to KNOW goes through the
    existing doorways (``POST /ltm/ingest-document``, the memory importers,
    project knowledge) — the /train wizard points at exactly those.
    """

    id: str = Field(default_factory=lambda: new_id("sample"), primary_key=True)
    #: What this is, for the user's own list ("blog post", "client email").
    label: str = ""
    #: The writing itself.
    text: str = ""
    #: How it arrived: "pasted" | "document:<filename>".
    origin: str = "pasted"
    created_at: datetime = Field(default_factory=utcnow)
