"""The client record (v1.499.0, builder A): THE resolver, the ONLY reader of
client preferences, the question under a reply, and every change.

STUB until builder A lands: the reads answer "no clients" (an empty
:class:`ClientMap`, no preferences, no question) so every caller keeps
working; every change raises ``NotImplementedError``. The docstrings are the
rule to build.

Folder rules (enforced here, one plain sentence each, raised as
``ClientRefused``). Paths are compared as TEXT only (``keys.folder_key``,
``keys.inside``, ``keys.home_folder``); nothing touches the disk. A folder is
refused when it is: a drive, share or relative top (``index._FORGETTABLE``);
only Clients-type, generic or container words ("Downloads holds work for many
people. Pick a folder that is only Hartleys'."); a folder that holds another
client's folder ("C:\\Clients holds Smith's folder. Pick Hartleys' own
folder."); a folder inside another client's folder; a folder that already
belongs to another confirmed client. No nesting, either way: a folder
resolves to at most one client.

A confirmed client that becomes Not a client keeps its folder rows and
preferences, which claim and reach nothing; another client may take its
folders over; Make it a client again restores the folders still free. A
FORGOTTEN folder (``workforgotten``) stays its client's for the wall and for
preferences; only its work is not shown.

Every change bumps the process-local write version (so :func:`load_map`
re-reads), publishes its event with IDS ONLY (``client.confirmed {id}``,
``client.declined {id}``, ``client.changed {id}``), and after a confirm,
decline or confirm-many starts a noticing pass so the next batch is ready.
Two answers at once: the second raises ``ClientConflict``. Undo (builder C's
cards) restores exactly what one change did through these functions; a step
not listed here is agreed with builder A, never written as SQL elsewhere.

Only this file and ``workmap/models.py`` may name the client-preference
table's class or table (a grep pin).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import ClientRoot, Waiting

#: A loaded map is re-read when the write version moved, or when it is older
#: than this (another process may have written).
MAP_MAX_AGE_S = 30.0


@dataclass(frozen=True, eq=False)
class ClientMap:
    """Every confirmed folder and waiting home, loaded once and cached per
    engine (:func:`load_map`). Text only: no method touches the disk or the
    database. An EMPTY map answers "nothing" to every question."""

    #: The process-local write version it was loaded at.
    version: int = 0
    #: Every claiming folder of every CONFIRMED client (forgotten ones too).
    confirmed: tuple[ClientRoot, ...] = ()
    #: Every proposed (waiting) name.
    waiting: tuple[Waiting, ...] = ()
    #: CONFIRMED clients only: ``id -> (name, signature, aliases)`` (the
    #: table ``names.named_in`` reads for ``TurnWall.named``).
    names: Mapping[str, tuple[str, str, tuple[str, ...]]] = field(default_factory=dict)
    #: WAITING names, the same shape (``remember_preference``'s guard refuses
    #: text naming a confirmed OR waiting client; the wall never uses these
    #: for ``named``).
    waiting_names: Mapping[str, tuple[str, str, tuple[str, ...]]] = field(default_factory=dict)

    def client_of(self, folder: object) -> ClientRoot | None:
        """The :class:`ClientRoot` whose folder ``folder`` is, or is inside
        (after ``keys.home_folder`` + ``keys.folder_key``). None for "",
        for a folder no confirmed client claims, and on an empty map."""
        return None

    def waiting_of(self, folder: object) -> Waiting | None:
        """The waiting name whose home ``folder`` is, or is inside (and is
        not a confirmed client's). None on an empty map."""
        return None

    def provisional_of(self, folder: object) -> str | None:
        """``"folder:<key>"`` for the first name segment after a Clients-type
        segment of ``folder`` (``C:\\Clients\\Hartleys\\2026`` →
        ``folder:c:/clients/hartleys``): walled before the owner answers.
        None when the path has no Clients-type segment with a name under it.
        """
        return None

    def spans(self, folder: object) -> bool:
        """True when ``folder`` is itself Clients-type, or strictly holds a
        confirmed folder, a waiting home or a Clients-type folder (a parent
        folder: ``C:\\Clients``, ``Documents`` above Clients). False on an
        empty map for a folder with no Clients-type segment."""
        return False

    def roots_of(self, client_id: str) -> tuple[ClientRoot, ...]:
        """Every claiming folder of one confirmed client, as recorded
        (``()`` for an unknown or not-confirmed id)."""
        return ()


#: The map with nothing on record.
EMPTY_MAP = ClientMap()


def load_map(engine: Any) -> ClientMap:
    """THE resolver: every confirmed client's claiming folders and every
    waiting name, cached per engine on the process-local write version and
    re-read when older than :data:`MAP_MAX_AGE_S`. BLOCKING (two small
    reads). Raises ``ClientsUnreadable`` when the tables cannot be read
    (callers fail CLOSED, never "no clients")."""
    return EMPTY_MAP


def preferences(engine: Any, client_id: str) -> list[dict[str, Any]]:
    """The ONLY reader of client preferences: ``[{id, text, local_only,
    thread_id}]`` for one client, newest first, CONFIRMED rows only (the
    status is in the SQL). ``thread_id`` is the evidence's source chat (""
    for a typed one), so the block can ask whether it became Local only.
    Raises ``ClientsUnreadable``."""
    return []


def question_for_turn(platform: Any, *, folder: str, thread_id: str) -> dict[str, Any] | None:
    """The one question under a reply: ``{id, name, folders, work}`` or None.

    BLOCKING; the lanes call it after a finished turn, beside
    ``suggest_for_turn``, through ``asyncio.to_thread`` under
    ``wait_for(1.0)``, and only when ``suggest_preferences`` is true
    (dashboard turns) and the reply carries no preference suggestion. Here:
    ``notice_clients`` is on and the turn's ``folder`` is inside a home of a
    proposed row whose ``asked_at`` is NULL; the strongest such row is
    picked and ``asked_at`` set under the notice lock (a question ignored
    under its reply is never put under another). Never raises (None)."""
    return None


# --------------------------------------------------------------------------- #
# Changes. Each returns a view dict; each raises ClientNotFound (404),
# ClientConflict (409), ClientRefused (400) or ClientsUnreadable (503).
# --------------------------------------------------------------------------- #
def confirm(platform: Any, client_id: str, *, name: str | None = None) -> dict[str, Any]:
    """``proposed → confirmed`` (``POST /clients/{id}/confirm``; the page may
    send ``name``). Sets ``confirmed_at`` and ``decided_at``; each home
    becomes a ``found`` folder row (a home another client owns, or nested
    with one, is skipped and named in ``note``); other spellings become
    aliases; proposed rows now inside its folders are withdrawn. Publishes
    ``client.confirmed {id}``. Returns ``{ok, client: {id, name}, note}``."""
    raise NotImplementedError("clients: confirm is built by builder A")


def decline(platform: Any, client_id: str) -> dict[str, Any]:
    """``proposed`` or ``confirmed`` → ``declined`` (Not a client). Folders
    and preferences are kept but claim and reach nothing; the signature and
    aliases are never asked about again. Publishes ``client.declined
    {id}``. Returns ``{ok, client: {id, name}, note}``."""
    raise NotImplementedError("clients: decline is built by builder A")


def confirm_many(platform: Any, ids: Sequence[str]) -> dict[str, Any]:
    """The bulk review (``POST /clients/confirm-many``, ≤ 30 ids): each row
    re-read and confirmed only if still proposed. Returns ``{confirmed:
    [{id, name}], skipped: [{id, why}]}``."""
    raise NotImplementedError("clients: confirm_many is built by builder A")


def ask_again(platform: Any, client_id: str) -> dict[str, Any]:
    """A declined row that was NEVER confirmed goes back to ``proposed`` with
    ``asked_at`` NULL, even over ``MAX_WAITING``. A once-confirmed one is
    refused (that is :func:`restore`)."""
    raise NotImplementedError("clients: ask_again is built by builder A")


def restore(platform: Any, client_id: str) -> dict[str, Any]:
    """Make it a client again: a declined, once-confirmed client becomes
    ``confirmed``, its folders restored where they are still free (the rest
    named in ``note``). Publishes ``client.changed {id}``."""
    raise NotImplementedError("clients: restore is built by builder A")


def create(platform: Any, *, folder: str = "", name: str = "") -> dict[str, Any]:
    """An owner-made client (``POST /clients {folder, name?}``, "Make this a
    client", chat's ``client_answer``): ``how = added``, confirmed at once.
    With no name, the folder's own name (``names.display_name``). Reuses a
    proposed or declined row with the same signature (no twin; the owner's
    explicit action overrides an earlier No). Folder rules apply. A client
    with no folder reaches no chat until it has one. Publishes
    ``client.confirmed {id}``. Returns ``{ok, client: {id, name}, note}``."""
    raise NotImplementedError("clients: create is built by builder A")


def rename(platform: Any, client_id: str, name: str) -> dict[str, Any]:
    """``PATCH /clients/{id} {name}``: 1–80 chars, promptguard-scanned; the
    old signature becomes an alias. Refused when another row already has the
    new signature. Publishes ``client.changed {id}``."""
    raise NotImplementedError("clients: rename is built by builder A")


def add_folder(platform: Any, client_id: str, folder: str) -> dict[str, Any]:
    """``POST /clients/{id}/folders {folder}``: an ``added`` folder row,
    under the folder rules (a free row of a Not-a-client client may be taken
    over). Publishes ``client.changed {id}``."""
    raise NotImplementedError("clients: add_folder is built by builder A")


def remove_folder(platform: Any, client_id: str, folder: str) -> dict[str, Any]:
    """``POST /clients/{id}/folders/remove {folder}``: the folder row is
    deleted (its work stays in the record). Publishes ``client.changed
    {id}``."""
    raise NotImplementedError("clients: remove_folder is built by builder A")


def set_aliases(platform: Any, client_id: str, aliases: Sequence[str]) -> dict[str, Any]:
    """The client's other spellings, at most 5, stored as signatures; one
    that another row already uses is refused. Publishes ``client.changed
    {id}``."""
    raise NotImplementedError("clients: set_aliases is built by builder A")


def keep_preference(
    platform: Any,
    client_id: str,
    text: str,
    *,
    origin: str,
    local_only: bool = False,
    evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Keep one preference for a CONFIRMED client, with the owner's yes
    (``origin`` said: a chat card; typed: the page). ``text`` through
    ``learning.preferences.clean_text`` (refused with its sentence when
    flagged); ``evidence`` quotes the OWNER's own message (≤ 160), never the
    model's argument. Publishes ``client.changed {id}``. Returns ``{ok,
    preference: {id, text, origin, local_only, evidence, created_at}}``."""
    raise NotImplementedError("clients: keep_preference is built by builder A")


def change_preference(platform: Any, client_id: str, pref_id: str, text: str) -> dict[str, Any]:
    """``PATCH /clients/{id}/preferences/{pid}``: new text, same checks as
    :func:`keep_preference`. Publishes ``client.changed {id}``."""
    raise NotImplementedError("clients: change_preference is built by builder A")


def forget_preference(platform: Any, client_id: str, pref_id: str) -> dict[str, Any]:
    """``DELETE /clients/{id}/preferences/{pid}``: the row is deleted.
    Publishes ``client.changed {id}``. Returns the forgotten row's view, so
    an Undo can keep it again exactly."""
    raise NotImplementedError("clients: forget_preference is built by builder A")


__all__ = [
    "EMPTY_MAP",
    "MAP_MAX_AGE_S",
    "ClientMap",
    "add_folder",
    "ask_again",
    "change_preference",
    "confirm",
    "confirm_many",
    "create",
    "decline",
    "forget_preference",
    "keep_preference",
    "load_map",
    "preferences",
    "question_for_turn",
    "remove_folder",
    "rename",
    "restore",
    "set_aliases",
]
