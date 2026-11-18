"""Clients (v1.499.0, vision plan leap 3, Phase 3): THE CONTRACT.

Five builders code against this docstring at the same time. It is the
agreement; a change to it is agreed with every builder first.

What a client is
================
A person or business the owner works for, KNOWN BY ITS FOLDERS AND CONFIRMED
BY THE OWNER. Only a confirmed client is a client. A waiting name is a
suggestion: it reaches no prompt and is never sent to the phone. A client is
not a Project (a Project inside a client's folders is LINKED, never created)
and it is not a ``LessonRecord``.

The tables (``workmap/models.py``; ``workmap.models`` is already a late model
module in ``core/db.py``, so ``init_db`` creates them):

* ``workclient`` (``WorkClient``): ``id`` ``client_<12 hex>``; ``name``;
  ``name_key`` (indexed); ``signature`` (UNIQUE, the never-ask-twice key);
  ``aliases_json`` (≤ 5 more signatures); ``status`` (indexed) proposed |
  confirmed | declined; ``how`` noticed | added; ``evidence_json``
  ``{rule, homes: [{folder, key, work, last_at, local}] (≤ 8), work,
  folders}``; ``strength``; ``asked_at``; ``created_at`` (indexed),
  ``decided_at``, ``confirmed_at`` (the first Yes), ``updated_at``.
* ``workclientfolder`` (``WorkClientFolder``): ``folder_key`` PK
  (``keys.folder_key(keys.home_folder(folder))``, so a folder belongs to ONE
  client); ``client_id`` (indexed); ``folder`` as spelled; ``how`` found |
  added; ``created_at``.
* the client-preference table (its class and table name are written ONLY in
  ``workmap/models.py`` and ``store.py``: a grep pin refuses them anywhere
  else in ``src/``): ``id`` ``cpref_<12 hex>``; ``client_id`` (indexed);
  ``text`` 1–280 chars through ``learning.preferences.clean_text``;
  ``status`` (only ``confirmed`` is ever read); ``origin`` said | typed;
  ``local_only``; ``evidence_json`` ``[{quote ≤ 160 (the OWNER's words),
  thread_id, where (a folder NAME), at, link}]``; ``created_at`` (indexed),
  ``updated_at``.

The status / how / origin words are the ``CLIENT_*`` constants in
``workmap/models.py``.

Who owns what (one file per builder)
====================================
This ``__init__`` is the contract ONLY: the exceptions, the frozen value
types, the constants. It imports none of its modules, so any module (the
lanes, ``workmap/block.py``, ``memory/fabric.py``) may import it without a
cycle. Import the module you need::

    from iron_jarvis.workmap.clients import block, detect, names, page, store, wall

* ``names``  (A) PURE TEXT: ``path_parts``, ``name_key``, ``signature``,
  ``is_name_segment``, ``is_client_parent``, ``display_name``,
  ``possessive``, ``named_in``. Nothing touches the disk.
* ``detect`` (A) noticing names with NO model call: ``candidates`` (pure),
  ``notice`` (mints / refreshes / withdraws proposed rows, ≤
  :data:`MAX_WAITING`), ``clients_loop`` (``bg_tasks['work_clients']``,
  injected clock and sleep).
* ``store``  (A) the record: ``ClientMap`` + ``load_map`` (THE resolver,
  cached per engine), ``preferences`` (the ONLY reader of the
  client-preference table, confirmed rows only), ``question_for_turn``, and
  every mutation.
* ``page``   (A) what the two pages read: ``clients_list`` (``GET
  /clients``), ``client_page`` (``GET /clients/{id}``). Writes nothing.
* ``block``  (A) ``client_block``: what a turn in a confirmed client's
  folders is told (the stable head). Never raises.
* ``wall``   (B) the ONE rule for what each turn may be told: ``turn_wall``,
  ``may_tell``, the ContextVar ``CURRENT`` with ``current`` /
  ``turn_scope`` / ``scoped_frames`` / ``owner_screen``,
  ``thread_owners``, ``session_owners``.
* Routes (A): ``daemon/routes/clients.py``. The lanes, ``workmap/block.py``,
  memory recall and history search (B). Chat tools, the inbox kind, the
  ``remember_preference`` guards and the setting (C). The dashboard (D).

``config.notice_clients`` (default True, "Notice clients in your folders")
switches noticing and the question under a reply; read it with
``getattr(config, "notice_clients", True)``.

The value types (below)
=======================
* :class:`ClientRoot` ``(client_id, name, key, folder)``: one folder of a
  CONFIRMED client (``key`` is the folder key, ``folder`` as spelled).
* :class:`Waiting` ``(client_id, name, homes)``: a proposed name; ``homes``
  are folder keys.
* :class:`TurnWall` ``(owner, client, spans, named, ok, folder_key)``: what a
  turn may be told. ``owner`` is ``"client:<id>"`` | ``"waiting:<id>"`` |
  ``"folder:<key>"`` | None; ``client`` is set only for a confirmed owner;
  ``spans`` = ``ClientMap.spans(folder)``; ``named`` = ids of the CONFIRMED
  clients named in the OWNER's own words (never the model's); ``ok`` is False
  when the client tables could not be read (fail closed: the work block
  keeps to this exact folder, memory recall drops every chat passage).
  :data:`NO_CLIENT` is the turn with no client whose map read fine (and what
  ``wall.current()`` answers when nothing set it).

The wall (``wall.may_tell``), in this order
===========================================
Owner of a folder (text only, after ``home_folder`` + ``folder_key``):
``client:<id>`` when inside a confirmed folder; else ``waiting:<id>`` inside a
waiting home; else ``folder:<key>`` for the first name segment after a
Clients-type segment; else None. Owner of a row or passage: its folder's
owner; when that is None, NAMED ATTRIBUTION: the confirmed clients its title,
file stems or snippet name (``names.named_in``); for an ``outside`` turn
also the WAITING names they (or the folder's segments) name.

1. ``not turn.ok``: only rows in exactly the turn's folder.
2. Row owner None: True (the Phase 1 and 2 rules hold).
2b. ``turn.outside`` (another app asks, ``wall.outside_and_map``): False for
   a row with an owner of any kind (confirmed, waiting, provisional, or
   proved by its files or a named segment).
3. ``turn.owner`` set: True only if the row owner is exactly ``turn.owner``
   (a named set must equal ``{turn.owner}``).
4. Row owner confirmed: True only if it is in ``turn.named`` (pointers only,
   never facts). A row with several owners needs EVERY confirmed one named,
   whatever its other owners are; those then go on to rule 5.
5. ``turn.spans``: False.
6. Otherwise True.

Facts (the client block, client preferences, the "other folders" part) go
ONLY to a turn whose ``client`` is that confirmed client. Recaps stay
exact-folder. Waiting names never enter a prompt.

Routes (``daemon/routes/clients.py``; all plain ``def``, outside
``/memory/``; 503 when a read fails; GETs write nothing and never mint)
=====================================================================
* ``GET /clients`` →
  ``{clients:[{id,name,how,folders:[{folder,name}],work_count,last_at,
  preferences_count}], waiting:[{id,name,rule,about,evidence:[str],folders,
  work,pre_ticked,created_at}], declined:[{id,name,decided_at,was_client}],
  total, noticing}``
* ``GET /clients/{id}`` →
  ``{client:{id,name,status,how,aliases,created_at,confirmed_at,why:[str]},
  folders:[{folder,name,how,count,last_at,forgotten}], entries:[public row],
  entries_total, files:[{name,path,how,folder_name,entry_id,title,link,at}],
  files_total, decisions:[{text,label:"decided"|"open",entry_id,title,link,
  written_at,current}], people:[{name,how:"title"|"file"|"email",
  seen_in:[{entry_id,title,link}]}], preferences:[{id,text,origin,local_only,
  evidence,created_at}], named_elsewhere:[{entry_id,title,link,folder_name}],
  projects:[{id,name,root}], told: str}``
* ``GET /clients/of?folder=`` → ``{client: {id,name} | null}``
* ``POST /clients {folder, name?}`` (an owner-made client; reuses a matching
  proposed or declined row, never a twin)
* ``PATCH /clients/{id} {name}``
* ``POST /clients/{id}/confirm`` (the page may send ``{name}``) →
  ``{ok, client:{id,name}, note}``; ``/decline``; ``/ask-again``;
  ``/restore``
* ``POST /clients/confirm-many {ids ≤ 30}`` →
  ``{confirmed:[…], skipped:[{id, why}]}``
* ``POST /clients/{id}/folders {folder}``;
  ``POST /clients/{id}/folders/remove {folder}``
* ``POST /clients/{id}/preferences {text}`` (origin typed);
  ``PATCH|DELETE /clients/{id}/preferences/{pid}``
* ``POST /clients/notice`` ("Look again now")

Errors map from the exceptions below: :class:`ClientNotFound` 404 "That client
is not on record."; :class:`ClientConflict` 409 "Hartleys was already
answered."; :class:`ClientRefused` 400 with the refusal sentence;
:class:`ClientsUnreadable` 503 "Your clients could not be read. Nothing was
changed." Each carries ``.sentence`` (and ``.status``).

Wire keys and events
====================
* The POST ``/chat`` response and the ``/chat/stream`` done frame gain
  ``client_question: {id, name, folders, work} | null``, ALWAYS present (lock
  step, MIRROR NOTE). The dashboard stores it with the message, like
  ``suggestion``.
* ``GET /work-record/folders`` rows gain ``client: {id, name} | null``
  (computed by the server; the dashboard never re-derives it).
* Events (``core/events.EventType``), IDS ONLY, never a name (events can
  reach the phone channel): ``client.suggested {ids}``,
  ``client.confirmed {id}``, ``client.declined {id}``, ``client.changed
  {id}`` (renamed, a folder or a preference changed, restored).
  ``IMPROVEMENT_EVENT_TYPES`` gains the first three.
* The inbox kind ``client`` ("Clients Iron Jarvis noticed", right after
  "Preferences Iron Jarvis noticed"); its buttons post to
  ``/clients/{id}/confirm`` and ``/clients/{id}/decline`` (one press each);
  its home is ``/clients#waiting``.
* A door's href is built only from an id matching :data:`CLIENT_ID_RE`;
  anything else falls back to ``/clients``.

The words
=========
On screen (``dashboard/lib/clients.ts``): Clients · Your clients · Is <name>
a client? · Yes, a client · Not a client · No · These are clients · Ask again
· Make it a client again · Look again now · What <possessive> wants · Keep
for <name> · What a chat in <possessive> folders is told · Decided · Still
open · People named · Named in other work. ``possessive`` follows one table,
pinned on both sides: "Hartley's" stays "Hartley's", "Hartleys" becomes
"Hartleys'", "Smith" becomes "Smith's".

The question, everywhere: "Is Hartleys a client?". Under a reply: "Iron Jarvis
has seen Hartleys in 2 folders and 9 pieces of work. Is Hartleys a client?";
after Yes "Hartleys is a client now."; after No "Iron Jarvis won't ask about
Hartleys again." Folder refusals: "Downloads holds work for many people. Pick
a folder that is only Hartleys'." / "C:\\Clients holds Smith's folder. Pick
Hartleys' own folder." VOCABULARY row **client**: "a person or business the
owner works for, known by its folders and confirmed by the owner" (never
"customer" or "account" on screen). No engine words on screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Numbers
# --------------------------------------------------------------------------- #
#: At most this many names wait for an answer at once (= the inbox's
#: ``PER_KIND``, so every waiting one is listed). "Ask again" may go over it.
MAX_WAITING = 30
#: The client block's whole size, in characters (12 preference lines and 4
#: other folders at most inside it).
CLIENT_BLOCK_CHARS = 1600
#: The client hop's bound in ``_gather_grounding`` (a REQUIRED hop): past it
#: the block is "" and the receipt names ``client``, never dropped silently.
CLIENT_HOP_MAX_S = 5

#: A client's id: ``core.ids.new_id("client")``. A door's href is built only
#: from an id this matches (a name never does).
CLIENT_ID_RE = re.compile(r"^client_[0-9a-f]{12}$")
#: A client preference's id: ``core.ids.new_id("cpref")``.
CLIENT_PREF_ID_RE = re.compile(r"^cpref_[0-9a-f]{12}$")

# --------------------------------------------------------------------------- #
# Words
# --------------------------------------------------------------------------- #
#: A folder whose name is one of these (lower case, apostrophes dropped,
#: ``& - _ .`` as spaces, single spaces) holds clients' folders: the first
#: name segment under it is one client (owner decision 9; the owner's own
#: additions are not part of this contract).
CLIENT_PARENT_PHRASES = frozenset(
    {
        "clients", "client", "customers", "customer", "client files",
        "client folders", "customer files", "tax clients",
    }
)

#: Words that say what KIND of work a folder holds, never whose
#: (``names.name_key`` drops them, beside ``keys.GENERIC_FOLDER_WORDS`` and
#: ``keys.STOP_WORDS``). The three designs' lists, with singular forms.
WORK_FOLDER_WORDS = frozenset(
    """
    tax taxes return returns payroll bookkeeping book books audit audits workpaper workpapers
    statement statements bank banking receipt receipts invoice invoices scan scans
    reconciliation reconciliations recon ledger budget reports report quarterly monthly
    annual federal state irs notice notices letter letters correspondence engagement
    engagements extension amended estimate estimates sale sales filing filings form forms
    schedule schedules draft drafts final review notes note meeting meetings old new copy
    done inbox template templates prior current file files doc docs work accounting closing
    w2 w9 1099 k1 1040 1065 1120 1120s 990 1041
    """.split()
)


# --------------------------------------------------------------------------- #
# Errors (each carries a plain sentence the route answers with)
# --------------------------------------------------------------------------- #
class ClientError(Exception):
    """A client operation that did not happen. ``sentence`` is what the
    owner reads; ``status`` the HTTP status a route answers with."""

    status = 400
    default = "That could not be done."

    def __init__(self, sentence: str | None = None) -> None:
        self.sentence = " ".join(str(sentence or self.default).split())
        super().__init__(self.sentence)


class ClientsUnreadable(ClientError):
    """The client tables could not be read (503). A reader that gets it
    fails CLOSED (``TurnWall.ok`` False), never "no clients"."""

    status = 503
    default = "Your clients could not be read. Nothing was changed."


class ClientNotFound(ClientError):
    """No client with that id (404)."""

    status = 404
    default = "That client is not on record."


class ClientConflict(ClientError):
    """The record changed under the request: already answered, answered twice
    at once, changed since the card (409). The sentence names the client
    ("Hartleys was already answered.")."""

    status = 409
    default = "That client was already answered."


class ClientRefused(ClientError):
    """A folder rule or a text check refused the request (400); the sentence
    says which, in one plain sentence."""

    status = 400
    default = "That was not done."


# --------------------------------------------------------------------------- #
# Value types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ClientRoot:
    """One folder of a CONFIRMED client."""

    client_id: str
    name: str
    #: ``keys.folder_key(keys.home_folder(folder))``.
    key: str
    #: As the owner spelled it.
    folder: str


@dataclass(frozen=True)
class Waiting:
    """A name Iron Jarvis noticed that waits for the owner's answer."""

    client_id: str
    name: str
    #: The homes' folder keys (outermost kept).
    homes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnWall:
    """What one turn may be told (``wall.turn_wall``)."""

    #: ``"client:<id>"`` | ``"waiting:<id>"`` | ``"folder:<key>"`` | None.
    owner: str | None = None
    #: The confirmed client whose folder the turn is in (else None).
    client: ClientRoot | None = None
    #: The turn's folder is Clients-type, or holds a confirmed folder, a
    #: waiting home or a Clients-type folder.
    spans: bool = False
    #: Ids of the confirmed clients the OWNER's own words name.
    named: frozenset[str] = field(default_factory=frozenset)
    #: False when the client tables could not be read (fail closed).
    ok: bool = True
    #: The turn's folder key ("" for a turn with no folder).
    folder_key: str = ""
    #: The asker is ANOTHER APP over the connector, not the owner
    #: (``wall.outside_and_map``): told nothing that has an owner (rule 2b).
    outside: bool = False


#: No client, and the client map read fine: what ``wall.current()`` answers
#: when nothing set the wall, and what a turn with no folder gets.
NO_CLIENT = TurnWall()


__all__ = [
    "CLIENT_BLOCK_CHARS",
    "CLIENT_HOP_MAX_S",
    "CLIENT_ID_RE",
    "CLIENT_PARENT_PHRASES",
    "CLIENT_PREF_ID_RE",
    "MAX_WAITING",
    "NO_CLIENT",
    "WORK_FOLDER_WORDS",
    "ClientConflict",
    "ClientError",
    "ClientNotFound",
    "ClientRefused",
    "ClientRoot",
    "ClientsUnreadable",
    "TurnWall",
    "Waiting",
]
