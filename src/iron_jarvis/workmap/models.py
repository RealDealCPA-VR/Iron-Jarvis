"""The work record's tables (v1.491.0, vision plan leap 3; recaps and forgotten
folders v1.492.0; clients, their folders and their preferences v1.499.0).

One row per PIECE OF WORK that has a folder: a chat, a coding session in
Chat › Code, a coding session in a real tool (Build › Code), an agent run.
Every column is copied from what Iron Jarvis already stores (the thread, the
session row, the undo journal); nothing here is model-written and no file on
disk is read to fill it. The row is a DERIVED index: deleting it loses
nothing, and :mod:`iron_jarvis.workmap.index` rebuilds it from its sources.

Registered in ``core.db._LATE_MODEL_MODULES`` (a lazily imported table is
otherwise created on fresh test databases and on no real install).
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ..core.ids import utcnow

#: The kinds of work a row can be.
KIND_CHAT = "chat"
KIND_CODE = "code"            # Chat › Code (a Build thread)
KIND_TOOL_CODE = "tool_code"  # Build › Code (a real coding tool)
KIND_RUN = "run"              # an agent run
#: v1.518.0: a chat the owner had in another coding tool (Claude Code,
#: Codex), read from that tool's own files (``workmap/outside.py``). Its
#: ``ref`` is ``<tool>:<the tool's chat id>``.
KIND_OUTSIDE = "outside"
KINDS = (KIND_CHAT, KIND_CODE, KIND_TOOL_CODE, KIND_RUN, KIND_OUTSIDE)


class WorkEntry(SQLModel, table=True):
    """One piece of work, where it happened and what it left."""

    __tablename__ = "workentry"

    #: ``<kind>:<ref>``.
    id: str = Field(primary_key=True)
    kind: str = Field(default="", index=True)
    #: The source row's id (thread id, hosted session id, session id).
    ref: str = ""
    title: str = ""
    #: The folder as the user would read it.
    folder: str = ""
    #: The folder normalised for matching (``keys.folder_key``).
    folder_key: str = Field(default="", index=True)
    project_id: str | None = Field(default=None, index=True)
    #: Build › Code: the tool's name ("Claude Code"). "" otherwise.
    tool: str = ""
    #: An agent run's origin ("schedule:nightly"). "" otherwise.
    origin: str = ""
    #: completed | failed | cancelled | … ("" = not known / still open).
    outcome: str = ""
    #: The work was switched to Local only: its words stay off any model
    #: that leaves this network.
    local_only: bool = False
    #: The work may have run on a model on this network with no Local only
    #: switch to say so (Build › Code on Pi / OpenCode): kept home the same
    #: way (the v1.414.0 rule).
    keep_home: bool = False
    #: The user archived it: still on the Memory page, never told to a chat.
    archived: bool = False
    #: ``[{"path": <absolute path>, "how": "made" | "changed" | "wrote"}]``,
    #: newest first, at most ``collect.FILES_MAX``.
    files_json: str = "[]"
    files_total: int = 0
    #: A run's own summary line, cut. "" for the other kinds.
    note: str = ""
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    #: What the source row looked like when this row was built; a refresh
    #: rebuilds the row only when it differs.
    stamp: str = ""


#: A recap that was kept / a try that kept nothing (not tried again until
#: the conversation changes).
RECAP_KEPT = "kept"
RECAP_NONE = "none"


class WorkRecap(SQLModel, table=True):
    """A short recap of one finished conversation (v1.492.0, Phase 2): what
    was done, what was decided, what is still open. MODEL-WRITTEN and then
    checked against the conversation's own record (``workmap/recap``); every
    line that names a file, quotes words or states a figure the record does
    not hold is removed before it is kept.

    Its own table, never columns of :class:`WorkEntry`: that row is rebuilt
    whole from its sources (``db.merge``), which would wipe a recap. Deleting
    a recap loses nothing but the model call that wrote it.
    """

    __tablename__ = "workrecap"

    #: The work entry's id (``<kind>:<ref>``).
    id: str = Field(primary_key=True)
    kind: str = ""
    ref: str = ""
    #: :data:`RECAP_KEPT` | :data:`RECAP_NONE`.
    state: str = RECAP_NONE
    #: The kept lines ("Done: …", "Decided: …", "Open: …"), one per line.
    text: str = ""
    #: Why nothing was kept ("" for a kept recap). Plain words.
    reason: str = ""
    #: A hash of the conversation's lines the recap was written from.
    covers: str = ""
    #: The conversation's ``updated_at`` when it was written (ISO, naive
    #: UTC). A recap is told to a chat only while the conversation still
    #: carries this stamp.
    source_updated: str = Field(default="", index=True)
    #: Lines the check removed.
    dropped: int = 0
    provider: str = ""
    model: str = ""
    #: A model was asked (false for a try that stopped before any call).
    asked: bool = False
    #: Calls in a row that got no answer (a failed try is made once more,
    #: later, and then left until the conversation changes).
    tries: int = 0
    created_at: datetime = Field(default_factory=utcnow, index=True)
    #: Next leaps 04-b: this recap's Open and Done lines were carried into
    #: the open items (``workmap/open_items.sync``, in the same write that
    #: keeps a recap). False (or NULL on a row kept before 04-b) = not yet:
    #: ``open_items.backfill`` carries it once.
    open_synced: bool = False


# --------------------------------------------------------------------------- #
# Open items (next leaps 04-b; build-out plan R2.13, idea 04). The contract is
# the docstring of ``workmap/open_items.py``.
# --------------------------------------------------------------------------- #
#: An item still to do.
OPEN_ITEM_OPEN = "open"
#: The owner (or a chat the owner asked) said it is done.
OPEN_ITEM_DONE = "done"
#: The owner said it is no longer needed.
OPEN_ITEM_DROPPED = "dropped"
#: Its own conversation's rewritten recap no longer lists it.
OPEN_ITEM_SETTLED = "settled"
OPEN_ITEM_STATUSES = (OPEN_ITEM_OPEN, OPEN_ITEM_DONE, OPEN_ITEM_DROPPED, OPEN_ITEM_SETTLED)
#: Who closed it (``WorkOpenItem.closed_how``).
CLOSED_BY_YOU = "you"
CLOSED_BY_CHAT = "chat"
CLOSED_BY_CONVERSATION = "conversation"
#: Closed with another conversation's copy of the same words: this one was
#: already going when the owner closed them there (``closed_ref`` = the
#: owner's item). Never an answer of the owner's own.
CLOSED_WITH_COPY = "copy"


class WorkOpenItem(SQLModel, table=True):
    """One open item in a folder (next leaps 04-b): an ``Open:`` line of a
    kept recap, carried forward until the owner or its own conversation
    closes it. Derived from CHECKED recap lines only (model-written, then
    checked against the record): DATA, scanned again whenever it is told.

    Its own table, never columns of :class:`WorkEntry` (rebuilt whole from
    its sources) or :class:`WorkRecap` (replaced whole by each recap): a
    status the owner set must outlive both. Removed with its piece of work
    (a deleted source, a forgotten folder)."""

    __tablename__ = "workopenitem"

    #: ``core.ids.new_id("open")``: ``open_<12 hex>``.
    id: str = Field(primary_key=True)
    #: The work entry whose recap listed it (``<kind>:<ref>``).
    entry_id: str = Field(default="", index=True)
    #: ``keys.folder_key(keys.home_folder(folder))``: a coding session's own
    #: copy counts as its repository, as on the pick-up card.
    folder_key: str = Field(default="", index=True)
    #: The folder as the owner would read it.
    folder: str = ""
    #: The line's words (without "Open:"), at most ``recap.RECAP_LINE_CHARS``.
    text: str = ""
    #: ``open_items.text_key(text)``: how a reworded line is matched.
    text_key: str = ""
    #: :data:`OPEN_ITEM_STATUSES`.
    status: str = Field(default=OPEN_ITEM_OPEN, index=True)
    opened_at: datetime = Field(default_factory=utcnow, index=True)
    closed_at: datetime | None = None
    #: :data:`CLOSED_BY_YOU` | :data:`CLOSED_BY_CHAT` |
    #: :data:`CLOSED_BY_CONVERSATION` | :data:`CLOSED_WITH_COPY` ("" while
    #: open).
    closed_how: str = ""
    #: What closed it: the chat's thread id, the work entry whose rewritten
    #: recap settled it, or the owner's item it was closed with.
    closed_ref: str = ""
    #: A LATER piece of work in the same folder whose Done line seems to
    #: settle this item (``open_items.may_be_done``). It only asks "Mark it
    #: done?" (D-04.3); it never closes anything.
    maybe_done_entry: str = ""
    #: The piece of work the owner answered "Not yet" for: it never asks
    #: about that one again.
    maybe_declined: str = ""
    #: The owner reopened it: from then on only the owner (or a chat the
    #: owner asks) closes it; a rewritten recap no longer settles it.
    owner_kept: bool = False
    #: The work was Local only (or kept home) when the item was written. The
    #: piece of work's own state, asked again, decides what a turn is told.
    local_only: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class WorkForgotten(SQLModel, table=True):
    """A folder the user told Iron Jarvis to forget (v1.492.0): no work in it
    or under it is kept in the record, told to a chat, or recapped, until the
    user says to remember it again. The chats and files themselves are not
    touched."""

    __tablename__ = "workforgotten"

    #: ``keys.folder_key`` of the folder.
    folder_key: str = Field(primary_key=True)
    #: The folder as the user would read it.
    folder: str = ""
    created_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Clients (v1.499.0, vision plan leap 3, Phase 3; the contract is the
# docstring of ``workmap/clients/__init__.py``).
# --------------------------------------------------------------------------- #
#: A client's status. Only a CONFIRMED client is a client; a proposed row is
#: a suggestion that reaches no prompt; a declined row is kept so its name is
#: never asked about again.
CLIENT_PROPOSED = "proposed"
CLIENT_CONFIRMED = "confirmed"
CLIENT_DECLINED = "declined"
CLIENT_STATUSES = (CLIENT_PROPOSED, CLIENT_CONFIRMED, CLIENT_DECLINED)
#: How a client came to be on record.
CLIENT_NOTICED = "noticed"   # Iron Jarvis saw the name in folder names
CLIENT_ADDED = "added"       # the owner made it (a page or a chat)
#: How a folder came to be a client's.
CLIENT_FOLDER_FOUND = "found"   # a home the name was noticed in, kept at the Yes
CLIENT_FOLDER_ADDED = "added"   # the owner gave it
#: Where a client preference came from.
CLIENT_PREF_SAID = "said"     # an approval card in chat
CLIENT_PREF_TYPED = "typed"   # typed on the client's page
#: The only status a client preference is ever read in.
CLIENT_PREF_CONFIRMED = "confirmed"


class WorkClient(SQLModel, table=True):
    """A person or business the owner works for, known by its FOLDERS and
    confirmed by the owner (v1.499.0). Noticed from the work record's own
    folder names with no model call, or added by the owner. Not a Project
    (a Project links to it by folder) and not a ``LessonRecord``.

    The row of a declined name is KEPT: its ``signature`` and aliases are
    what stop Iron Jarvis asking about that name again, in any spelling.
    """

    __tablename__ = "workclient"

    #: ``core.ids.new_id("client")``: ``client_<12 hex>``.
    id: str = Field(primary_key=True)
    #: The display spelling, 1–80 chars, promptguard-scanned on write.
    name: str = ""
    #: ``clients.names.name_key(name)``.
    name_key: str = Field(default="", index=True)
    #: ``clients.names.signature(name)``: the never-ask-twice key.
    signature: str = Field(index=True, unique=True)
    #: ``[<signature>, …]``: at most 5 more spellings (seen at the Yes, earlier
    #: names after a rename, aliases the owner adds).
    aliases_json: str = "[]"
    #: :data:`CLIENT_PROPOSED` | :data:`CLIENT_CONFIRMED` | :data:`CLIENT_DECLINED`.
    status: str = Field(default=CLIENT_PROPOSED, index=True)
    #: :data:`CLIENT_NOTICED` | :data:`CLIENT_ADDED`.
    how: str = CLIENT_NOTICED
    #: ``{"rule": "under_clients" | "several_folders" | "added", "homes":
    #: [{"folder", "key", "work", "last_at", "local"}] (≤ 8), "work",
    #: "folders"}``. ``local`` is true when every piece of work in that home
    #: is Local only or kept home.
    evidence_json: str = "{}"
    #: The rank score (``detect``): stronger names are asked about first.
    strength: int = 0
    #: When the question rode under a reply (it rides under ONE reply only).
    asked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    #: The owner's answer (Yes or Not a client).
    decided_at: datetime | None = None
    #: The first Yes (kept through a later Not a client).
    confirmed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class WorkClientFolder(SQLModel, table=True):
    """A folder that belongs to a client (v1.499.0). The folder key IS the
    primary key, so a folder belongs to one client. The row of a client that
    became Not a client is kept and claims nothing; another client may take
    it over."""

    __tablename__ = "workclientfolder"

    #: ``keys.folder_key(keys.home_folder(folder))``.
    folder_key: str = Field(primary_key=True)
    client_id: str = Field(default="", index=True)
    #: The folder as the owner spelled it.
    folder: str = ""
    #: :data:`CLIENT_FOLDER_FOUND` | :data:`CLIENT_FOLDER_ADDED`.
    how: str = CLIENT_FOLDER_FOUND
    created_at: datetime = Field(default_factory=utcnow)


class WorkClientPreference(SQLModel, table=True):
    """Something the owner keeps for ONE client ("Uses cash basis",
    v1.499.0). Kept only with the owner's yes. Told only to a turn whose
    folder is inside that client's folders, through
    ``clients.block.client_block``; never to the global preference block,
    memory recall, the Build share file or the Kept list. Its ONLY reader is
    ``clients.store.preferences`` (a grep pin refuses this class's name
    anywhere in ``src/`` outside this file and ``workmap/clients/store.py``).
    """

    __tablename__ = "workclientpreference"

    #: ``core.ids.new_id("cpref")``: ``cpref_<12 hex>``.
    id: str = Field(primary_key=True)
    client_id: str = Field(default="", index=True)
    #: 1–280 chars, through ``learning.preferences.clean_text`` (promptguard
    #: at write; scanned again when it is told).
    text: str = ""
    #: Only :data:`CLIENT_PREF_CONFIRMED` is ever read or written this
    #: release; the column exists so a test can insert another status and
    #: prove it reaches nothing.
    status: str = CLIENT_PREF_CONFIRMED
    #: :data:`CLIENT_PREF_SAID` | :data:`CLIENT_PREF_TYPED`.
    origin: str = CLIENT_PREF_SAID
    #: Kept in a Local only turn: told only to a turn that stays on this
    #: network.
    local_only: bool = False
    #: ``[{"quote" (≤ 160, the OWNER's own words, never the model's
    #: argument), "thread_id", "where" (a folder NAME), "at", "link"}]``.
    evidence_json: str = "[]"
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class ReportedChat(SQLModel, table=True):
    """A chat ANOTHER APP reported through Iron Jarvis's connector (v1.528.0):
    Claude desktop's Chat tab, or Codex. Written ONLY by the ``chat_record``
    tool over a connected app's own key (``mcpserver/apps.py``); the app is
    the key's, never an argument the model gave. Model-written (the other
    app's model summarised its own chat), so every text is DATA: it reaches a
    prompt only through the work record's lines, which promptguard scans.

    Its own table, never columns of :class:`WorkEntry` (that row is rebuilt
    whole from its sources). A Claude Chat report is its own piece of work
    (``workmap/outside.py``, tool ``claude-chat``); a Codex report only adds a
    summary to the Codex chat it matches, because Codex's chats are already
    read from its own files.
    """

    __tablename__ = "reportedchat"

    #: ``core.ids.new_id("rchat")``.
    id: str = Field(primary_key=True)
    #: The connected app: ``claude-desktop`` | ``codex``.
    app: str = Field(default="", index=True)
    #: What makes a second report of the same chat update this row: the
    #: app's own chat id when it gave one, else the title's key and the day.
    chat_key: str = Field(default="", index=True)
    title: str = ""
    #: A few lines, plain text, at most ``reported.SUMMARY_CHARS``.
    summary: str = ""
    #: ``[<absolute folder>, …]`` (≤ 5), the first is the chat's folder.
    folders_json: str = "[]"
    #: ``[<absolute path>, …]`` (≤ 20): files the chat made or changed.
    files_json: str = "[]"
    #: A link back to the chat in its own app ("" when none was given).
    link: str = ""
    #: Times the app reported this chat (a long chat may report twice).
    reports: int = 1
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


__all__ = [
    "ReportedChat",
    "CLIENT_ADDED", "CLIENT_CONFIRMED", "CLIENT_DECLINED", "CLIENT_FOLDER_ADDED",
    "CLIENT_FOLDER_FOUND", "CLIENT_NOTICED", "CLIENT_PREF_CONFIRMED", "CLIENT_PREF_SAID",
    "CLIENT_PREF_TYPED", "CLIENT_PROPOSED", "CLIENT_STATUSES", "KINDS", "KIND_CHAT",
    "KIND_CODE", "KIND_RUN", "KIND_TOOL_CODE", "RECAP_KEPT", "RECAP_NONE", "WorkClient",
    "WorkClientFolder", "WorkClientPreference", "WorkEntry", "WorkForgotten", "WorkRecap",
    "CLOSED_BY_CHAT", "CLOSED_BY_CONVERSATION", "CLOSED_BY_YOU", "CLOSED_WITH_COPY", "OPEN_ITEM_DONE",
    "OPEN_ITEM_DROPPED",
    "OPEN_ITEM_OPEN", "OPEN_ITEM_SETTLED", "OPEN_ITEM_STATUSES", "WorkOpenItem",
]
