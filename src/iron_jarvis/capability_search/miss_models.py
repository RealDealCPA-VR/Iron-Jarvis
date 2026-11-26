"""The tables of things the owner asked for that Iron Jarvis had no feature
for (next leaps 20-a): ONE row per miss, ONE row per group of misses that ask
for the same thing.

A LATE model (``core.db._LATE_MODEL_MODULES``), so a fresh install and an
upgraded one both get the tables on boot. Written only by
``capability_search/misses.py``.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import new_id, utcnow

#: What a miss was: the lookup answered nothing, its first line was "Not
#: offered", or the request was named by a word no entry of the list knows.
KIND_NOTHING, KIND_NOT_OFFERED, KIND_UNKNOWN = "nothing", "not_offered", "unknown"
KINDS = (KIND_NOTHING, KIND_NOT_OFFERED, KIND_UNKNOWN)

#: A group's states: waiting for an answer, "Not needed", "Keep as a note for
#: the next version", and (20-b) an extension was found for it.
OPEN, DECLINED, NOTED, FOUND = "open", "declined", "noted", "found"
STATUSES = (OPEN, DECLINED, NOTED, FOUND)


class CapabilityMiss(SQLModel, table=True):
    """One time the owner asked for something Iron Jarvis had no feature for.

    * ``words``: the owner's own message, secrets masked
      (``failure_words.mask_failure_text``), at most 300 characters. Kept on
      this PC only and removed after 90 days (D-20.1); shown only on
      Improvements.
    * ``query``: the lookup's own words for the request, masked the same way.
    * ``kind``: :data:`KINDS`; ``entry_id``: the "Not offered" line's id.
    * ``unknown``: the words of the request (the query's first part) that no
      entry of the list knows, space-separated: how two asks for the same
      missing thing are told apart from two that only share a verb.
    * ``thread_id`` (the saved chat, or the run's session), ``surface``
      (chat, run, build, pane, sidebar), ``local_only``.
    """

    __tablename__ = "capabilitymiss"

    id: str = Field(default_factory=lambda: new_id("miss"), primary_key=True)
    words: str = ""
    query: str = ""
    kind: str = KIND_NOTHING
    entry_id: str = ""
    unknown: str = ""
    thread_id: str = ""
    surface: str = ""
    local_only: bool = False
    group_id: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class MissGroup(SQLModel, table=True):
    """Misses that ask for the same thing.

    * ``key``: the sorted search words of the group's most common query (the
      capability index's own ``tokens``); ``unknown``: that query's words no
      entry knows. A group whose rows were all removed is still found by them.
    * ``title``: the most common query's first part ("send a fax").
    * ``title_local_only``: that title was written only in Local only chats
      (no ask outside one used the same words), so chat names it only to a
      turn that itself stays on this network (``inbox/tools``).
    * ``count``: every miss the group ever had (kept when old rows go).
    * ``status``: :data:`STATUSES`; ``decided_at``: when the owner answered.
    * ``extensions_json``: 20-b's extensions that can do it (``[]`` here).
    """

    __tablename__ = "missgroup"

    id: str = Field(default_factory=lambda: new_id("missgroup"), primary_key=True)
    key: str = ""
    unknown: str = ""
    title: str = ""
    title_local_only: bool = False
    count: int = 0
    first_at: datetime = Field(default_factory=utcnow)
    last_at: datetime = Field(default_factory=utcnow, index=True)
    status: str = Field(default=OPEN, index=True)
    decided_at: datetime | None = None
    extensions_json: str = "[]"
