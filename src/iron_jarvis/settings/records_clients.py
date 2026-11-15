"""Your clients from chat (v1.499.0, vision plan leap 3 Phase 3, builder C:
"who are my clients?", "yes, Hartleys is a client", "Smith isn't a client",
"add D:\\Scans\\Hartleys to Hartleys", "Hartley's wants cash basis", "forget
that Hartleys uses cash basis").

A client is a person or business the owner works for, KNOWN BY ITS FOLDERS
AND CONFIRMED BY THE OWNER (``workmap/clients``, the contract). Six tools
reach it from chat, the way ``settings.records`` reaches schedules and goals:

* ``client_list`` (allow, read-only) — WALLED. In a client's folder only that
  client is listed (its folders by name, work and preference counts, its
  page), plus how many other clients are on the Clients page. In a chat with
  no client every confirmed client is listed by name and folder names, except
  that a cloud turn leaves out a client known only from Local only work ("1
  more client is kept on this computer"). Names that wait for an answer are a
  COUNT, unless the owner's own message names one.
* ``client_answer`` / ``client_update`` / ``client_preference_add`` /
  ``client_preference_change`` / ``client_preference_forget`` (ask) — each
  shows the approval card in words that name the client, and each change has
  an Undo that puts back exactly what that change did (through the record's
  own functions in ``workmap/clients/store``, never SQL here), refusing in a
  sentence when the record changed since.

THE OWNER'S WORDS DECIDE, never the model's argument: a name a tool acts on
must be named in the OWNER's own message (:data:`OWNER_WORDS`, set by both
chat lanes around the turn), unless it is the turn's own client. In a
client's folder, a change to ANOTHER client is refused ("This chat is in
Smith's folder. …"): what one client's chat is told never names another.
A preference keeps the owner's own message as its evidence (at most 160
characters), never the model's words. Every writer is denied at low trust
(``core/trust.LOW_TRUST_DENY``).

Arming (:func:`clients_tools_for`, called once by
``records.record_tools_for`` with the live client names): anchored or
bounded, linear patterns over the owner's message. "my client called this
morning", "the client portal is down", "Mrs. Price wants coffee" (a name that
is no client) and "what is a client?" arm no writer; a QUESTION keeps only
the reader.

The client record's own reads and changes are BLOCKING (SQLite); the tools
run them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterable, Iterator, Mapping

from ..tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult
from ..workmap import clients as C
from ..workmap import keys as K
from ..workmap.clients import names as N
from ..workmap.clients import page as PG
from ..workmap.clients import store as S
from ..workmap.clients import wall as W
from . import records as R

log = logging.getLogger("ironjarvis.settings")

#: The reader (allow; never journaled, carries no card).
CLIENT_READER = "client_list"
#: The writers: each asks on the card and has an Undo.
CLIENT_WRITERS: tuple[str, ...] = (
    "client_answer",
    "client_update",
    "client_preference_add",
    "client_preference_change",
    "client_preference_forget",
)
#: The tools that carry a card and an Undo, with their default permission
#: (``records.RECORD_TOOL_TIERS`` and ``core.config``'s table say the same).
CLIENT_TOOL_TIERS: dict[str, str] = {name: "ask" for name in CLIENT_WRITERS}
#: Every tool of this module and its tier.
ALL_CLIENT_TOOL_TIERS: dict[str, str] = {**CLIENT_TOOL_TIERS, CLIENT_READER: "allow"}

#: The record kind this module's Undo serves (``records.restore``).
KIND = "client"
KINDS: tuple[str, ...] = (KIND,)

CLIENTS_HREF = "/clients"
WAITING_HREF = "/clients#waiting"

#: How much of the owner's message is read (for the name rule, the evidence
#: quote and the arming). A request names its client near the start.
OWNER_WORDS_CHARS = 4000
SCAN_CHARS = 2000
#: The owner's own words a client preference keeps as its evidence.
QUOTE_CHARS = 160
#: How many clients ``client_list`` names at most.
LIST_LIMIT = 40

UNREADABLE = C.ClientsUnreadable.default
NO_OWNER_WORDS = (
    "Iron Jarvis changes your clients only from your own words in a chat, so nothing was changed."
)
NOT_READY = "Clients cannot be changed in this build yet, so nothing was changed."
CHANGES_NOTHING = "changes nothing by itself"

# --------------------------------------------------------------------------- #
# the owner's own words in this turn
# --------------------------------------------------------------------------- #
#: The OWNER's message of the turn in flight (both chat lanes set it around
#: the turn, next to ``wall.turn_scope`` / ``wall.scoped_frames``). Unset it
#: is "", and every writer then refuses (fail safe: a tool never acts on a
#: name only the model wrote).
OWNER_WORDS: contextvars.ContextVar[str] = contextvars.ContextVar("ij_client_owner_words", default="")


def owner_words() -> str:
    """The owner's own message in this context ("" when no lane set it).
    Never raises."""
    try:
        got = OWNER_WORDS.get()
    except Exception:  # noqa: BLE001 — never raises
        return ""
    return got if isinstance(got, str) else ""


def _words_of(text: Any) -> str:
    return str(text or "")[:OWNER_WORDS_CHARS]


@contextmanager
def owner_words_scope(text: Any) -> Iterator[str]:
    """Set :data:`OWNER_WORDS` for the body of the ``with`` (the POST lane;
    reset after, however it ends)."""
    words = _words_of(text)
    token = OWNER_WORDS.set(words)
    try:
        yield words
    finally:
        try:
            OWNER_WORDS.reset(token)
        except (ValueError, LookupError, RuntimeError):  # a token from another context
            OWNER_WORDS.set("")


async def scoped_owner_words(agen: AsyncIterator[Any], text: Any) -> AsyncIterator[Any]:
    """``agen`` iterated with :data:`OWNER_WORDS` set in the iterating task's
    context (the stream lane, beside ``wall.scoped_frames``). Closing this
    generator closes ``agen`` at once."""
    token = OWNER_WORDS.set(_words_of(text))
    try:
        async for frame in agen:
            yield frame
    finally:
        try:
            close = getattr(agen, "aclose", None)
            if close is not None:
                await close()
        finally:
            try:
                OWNER_WORDS.reset(token)
            except (ValueError, LookupError, RuntimeError):
                OWNER_WORDS.set("")


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #


def _flat(text: Any, n: int = 80) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _q(text: Any, n: int = 120) -> str:
    return "“" + _flat(text, n) + "”"


def possessive(name: Any) -> str:
    """The possessive by the ONE table (``names.possessive``): "Hartley's"
    stays "Hartley's", "Hartleys" becomes "Hartleys'", "Smith" becomes
    "Smith's". The same table here only when that function answers nothing."""
    n = _flat(name)
    if not n:
        return ""
    try:
        got = N.possessive(n)
    except Exception:  # noqa: BLE001
        got = ""
    if got:
        return got
    if n.endswith(("'s", "’s")):
        return n
    return n + "'" if n.endswith("s") else n + "'s"


def client_href(client_id: Any) -> str:
    """A client's page, built ONLY from an id that is a client's id
    (``client_<12 hex>``); anything else is the Clients page."""
    cid = str(client_id or "")
    return f"{CLIENTS_HREF}/{cid}" if C.CLIENT_ID_RE.fullmatch(cid) else CLIENTS_HREF


def _folder_name(folder: Any) -> str:
    from ..workmap import keys

    return keys.folder_name(folder) or ""


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


# --------------------------------------------------------------------------- #
# the record, read through its own functions
# --------------------------------------------------------------------------- #


def _engine(platform: Any) -> Any:
    return platform.engine


def _map(platform: Any) -> Any:
    """THE resolver (``store.load_map``). Raises ``ClientsUnreadable``."""
    return S.load_map(_engine(platform))


def _sig(name: Any) -> str:
    try:
        return str(N.signature(name) or "")
    except Exception:  # noqa: BLE001
        return ""


def _named(text: Any, table: Mapping[str, Any]) -> frozenset[str]:
    """The ids of ``table`` the owner's ``text`` names (``names.named_in``)."""
    if not table or not str(text or "").strip():
        return frozenset()
    try:
        return frozenset(N.named_in(str(text), table))
    except Exception:  # noqa: BLE001 — a matcher fault names nothing
        return frozenset()


def _one_name_table(name: str, aliases: Iterable[str] = ()) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    return {"_": (name, _sig(name), tuple(aliases))}


def _names_it(text: Any, name: str, aliases: Iterable[str] = ()) -> bool:
    """Does the owner's ``text`` name ``name`` (or one of its aliases)?"""
    return bool(name) and bool(_named(text, _one_name_table(name, aliases)))


class Target:
    """A name as the record knows it."""

    __slots__ = ("id", "name", "status", "was_client", "aliases")

    def __init__(self, id: str = "", name: str = "", status: str = "", was_client: bool = False,
                 aliases: tuple[str, ...] = ()) -> None:
        self.id, self.name, self.status, self.was_client, self.aliases = id, name, status, was_client, aliases

    @property
    def known(self) -> bool:
        return bool(self.id)


def _declined(platform: Any) -> list[dict[str, Any]]:
    try:
        rows = PG.clients_list(platform).get("declined") or []
    except C.ClientError:
        raise
    except Exception:  # noqa: BLE001
        raise C.ClientsUnreadable()
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def find(platform: Any, cmap: Any, ref: Any) -> Target:
    """The record's entry for ``ref`` (a name, an alias, or a client's id):
    a confirmed client, a waiting name, a name marked not a client, or an
    unknown name. BLOCKING. Raises ``ClientsUnreadable``."""
    raw = _flat(ref)
    if not raw:
        return Target()
    tables = ((getattr(cmap, "names", {}) or {}, "confirmed"),
              (getattr(cmap, "waiting_names", {}) or {}, "proposed"))
    if C.CLIENT_ID_RE.fullmatch(raw):
        for table, status in tables:
            if raw in table:
                name, _s, aliases = table[raw]
                return Target(raw, str(name), status, status == "confirmed", tuple(aliases))
        for row in _declined(platform):
            if row.get("id") == raw:
                return Target(raw, str(row.get("name") or ""), "declined", bool(row.get("was_client")))
        return Target()
    sig = _sig(raw)
    if sig:
        for table, status in tables:
            for cid, (name, s, aliases) in table.items():
                if sig == s or sig in tuple(aliases or ()):
                    return Target(str(cid), str(name), status, status == "confirmed", tuple(aliases or ()))
        for row in _declined(platform):
            aliases = tuple(str(a) for a in (row.get("aliases") or ()) if a)
            if _sig(row.get("name")) == sig or sig in aliases:
                return Target(str(row["id"]), str(row.get("name") or ""), "declined", bool(row.get("was_client")),
                              aliases)
    return Target(name=raw)


def snap_client(platform: Any, client_id: str) -> dict[str, Any] | None:
    """One client as it is now, for an Undo's "changed since" check: ``{id,
    status, name, aliases, folders, prefs: {id: text}}``, or None when it is
    not on record. BLOCKING; reads through the record's own functions."""
    if not client_id:
        return None
    cmap = _map(platform)
    names = getattr(cmap, "names", {}) or {}
    waiting = getattr(cmap, "waiting_names", {}) or {}
    status = ""
    name = ""
    aliases: tuple[str, ...] = ()
    if client_id in names:
        status = "confirmed"
        name, _s, aliases = names[client_id]
    elif client_id in waiting:
        status = "proposed"
        name, _s, aliases = waiting[client_id]
    else:
        for row in _declined(platform):
            if row.get("id") == client_id:
                status, name = "declined", str(row.get("name") or "")
                break
    if not status:
        return None
    try:
        roots = cmap.roots_of(client_id) if status == "confirmed" else ()
    except Exception:  # noqa: BLE001
        roots = ()
    if status == "declined":
        # A Not-a-client client's folder rows are kept, unused: a later
        # takeover of one is a change since (the independent review).
        roots = tuple(SimpleNamespace(key=str(r.get("key") or ""))
                      for r in S.folder_rows(_engine(platform), client_id) or [])
    prefs = {str(p.get("id")): str(p.get("text") or "") for p in S.preferences(_engine(platform), client_id) or []}
    return {
        "id": client_id,
        "status": status,
        "name": str(name),
        "aliases": sorted(str(a) for a in aliases or ()),
        "folders": sorted(str(getattr(r, "key", "") or "") for r in roots or ()),
        "prefs": prefs,
    }


def live_names(platform: Any) -> dict[str, tuple[str, str, tuple[str, ...]]]:
    """Every confirmed and waiting client's ``(name, signature, aliases)`` by
    id, for :func:`clients_tools_for` (the stream lane reads it off the loop
    when the message may be about a client). BLOCKING; never raises (an
    unreadable record names nothing)."""
    try:
        cmap = _map(platform)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    for table in (getattr(cmap, "names", {}) or {}, getattr(cmap, "waiting_names", {}) or {}):
        for cid, entry in table.items():
            try:
                name, sig, aliases = entry
            except (TypeError, ValueError):
                continue
            out[str(cid)] = (str(name), str(sig), tuple(aliases or ()))
    return out


# --------------------------------------------------------------------------- #
# the turn's wall and the owner's words: who a change may be about
# --------------------------------------------------------------------------- #


class Gate:
    """What one call may act on: the turn's wall, the map, the owner's words,
    and a refusal sentence ("" = go on)."""

    __slots__ = ("turn", "cmap", "words", "problem")

    def __init__(self, turn: Any, cmap: Any, words: str, problem: str = "") -> None:
        self.turn, self.cmap, self.words, self.problem = turn, cmap, words, problem

    @property
    def own_id(self) -> str:
        owner = str(getattr(self.turn, "owner", "") or "")
        if owner.startswith(("client:", "waiting:")):
            return owner.split(":", 1)[1]
        return ""

    @property
    def owned(self) -> bool:
        """The turn's folder belongs to ONE client: a confirmed client, a
        waiting name, or a folder under a Clients-type folder nobody has
        answered about yet (``folder:<key>``). Walled the same way."""
        return bool(str(getattr(self.turn, "owner", "") or ""))

    @property
    def provisional_sig(self) -> str:
        """For a ``folder:<key>`` owner, the signature of that folder's own
        name ("" otherwise): the one name that may be acted on from it."""
        owner = str(getattr(self.turn, "owner", "") or "")
        if not owner.startswith("folder:"):
            return ""
        return N.signature(K.folder_name(owner.split(":", 1)[1]))

    def is_own(self, target: Any) -> bool:
        """Is ``target`` the client this turn's folder belongs to?"""
        tid = str(getattr(target, "id", "") or "")
        own = self.own_id
        if own:
            return bool(tid) and tid == own
        psig = self.provisional_sig
        if not psig:
            return False
        sigs = {_sig(getattr(target, "name", "")), *(getattr(target, "aliases", ()) or ())}
        return psig in sigs

    @property
    def own_name(self) -> str:
        cid = self.own_id
        for table in (getattr(self.cmap, "names", {}) or {}, getattr(self.cmap, "waiting_names", {}) or {}):
            if cid in table:
                return str(table[cid][0])
        client = getattr(self.turn, "client", None)
        return str(getattr(client, "name", "") or "")

    @property
    def client(self) -> Any:
        return getattr(self.turn, "client", None)

    def named(self, *, waiting: bool = True) -> frozenset[str]:
        """The confirmed (and waiting) clients the owner's words name."""
        out = _named(self.words, getattr(self.cmap, "names", {}) or {})
        if waiting:
            out |= _named(self.words, getattr(self.cmap, "waiting_names", {}) or {})
        return out


def gate(platform: Any) -> Gate:
    """The turn's wall (``wall.current()``), the client map and the owner's
    words, with the first refusal that applies. BLOCKING; never raises."""
    turn = W.current()
    words = owner_words()
    if not getattr(turn, "ok", True):
        return Gate(turn, S.EMPTY_MAP, words, UNREADABLE)
    try:
        cmap = _map(platform)
    except C.ClientError as exc:
        return Gate(turn, S.EMPTY_MAP, words, exc.sentence)
    except Exception:  # noqa: BLE001 — fail closed
        return Gate(turn, S.EMPTY_MAP, words, UNREADABLE)
    if not words.strip():
        return Gate(turn, cmap, words, NO_OWNER_WORDS)
    return Gate(turn, cmap, words)


def _other_client_words(g: Gate, what: str) -> str:
    """The refusal in a client's folder for a change to ANOTHER client. It
    never names that other client's folders or work."""
    own = g.own_name
    where = (f"{possessive(own)} folder" if own
             else "a folder kept apart for one client" if g.owned else "another client's folder")
    if what == "preference":
        return (
            f"This chat is in {where}. A preference for another client is kept from that client's page "
            "or a chat in its folders."
        )
    return (
        f"This chat is in {where}, so it changes only {own or 'that client'}. Do this on the Clients page "
        f"({CLIENTS_HREF}) or in a chat in that client's own folders; nothing was changed."
    )


def _wall_problem(g: Gate, target: Target, *, what: str = "change") -> str:
    """In a client's folder (or a waiting name's), only THAT client may be
    acted on; anywhere else the owner's words decide."""
    if not g.owned or g.is_own(target):
        return ""
    return _other_client_words(g, what)


def _not_named_words(name: str) -> str:
    return (
        f"Iron Jarvis acts on a client only when your own message names it, and {_q(name, 60)} is not "
        "in it, so nothing was changed. Say the name yourself."
    )


# --------------------------------------------------------------------------- #
# the Undo: what a change did, and putting it back
# --------------------------------------------------------------------------- #

#: Ops whose Undo is the record's ``unconfirm`` (agreed with builder A).
_NEEDS_UNCONFIRM = frozenset({"confirm", "create", "reconfirm"})


def _can_unconfirm() -> bool:
    return callable(getattr(S, "unconfirm", None))


def reversible(ops: Iterable[Mapping[str, Any]]) -> bool:
    """Can every op of a plan be put back by the record's own functions?
    (No Undo is promised that the journal cannot perform.)"""
    return all(str(op.get("op")) not in _NEEDS_UNCONFIRM or _can_unconfirm() for op in ops)


def _client_id_of(view: Any, fallback: str = "") -> str:
    if isinstance(view, Mapping):
        client = view.get("client")
        if isinstance(client, Mapping) and client.get("id"):
            return str(client["id"])
        if view.get("id"):
            return str(view["id"])
    return fallback


#: The refusal kinds whose sentence names ANOTHER client (``store._refusal``).
_NAMES_ANOTHER = frozenset({"taken", "inside", "holds", "name_taken"})
OVERLAPS_ANOTHER = (
    "That overlaps a folder or name another client has on record, so nothing was changed. "
    f"The Clients page ({CLIENTS_HREF}) shows which."
)


def chat_sentence(exc: BaseException) -> str:
    """What a client tool says for a refused change. In a turn whose folder
    belongs to one client (the independent review), a folder or name rule
    that names ANOTHER client is told without that client's name or
    folders; the page's own routes keep the named sentence."""
    sentence = exc.sentence if isinstance(exc, C.ClientError) else NOT_READY
    if getattr(exc, "kind", "") in _NAMES_ANOTHER and getattr(W.current(), "owner", None):
        return OVERLAPS_ANOTHER
    return sentence


def run_ops(platform: Any, ops: list[dict[str, Any]], client_id: str) -> tuple[str, list[dict[str, Any]], str]:
    """Do each op through the record's own functions, in order. Returns
    ``(client id, the ops done with what each made, the refusal of the op
    that stopped it or "")``. An op that fails after another was done stops
    the rest, and the ones done stay done (their Undo is kept). BLOCKING.
    Raises the FIRST op's ``ClientError`` when nothing was done."""
    done: list[dict[str, Any]] = []
    cid = client_id
    for op in ops:
        kind = str(op.get("op"))
        oid = str(op.get("id") or cid)
        try:
            if kind == "confirm":
                cid = _client_id_of(S.confirm(platform, oid), oid)
            elif kind == "ask_again":
                S.ask_again(platform, oid)
            elif kind == "reconfirm":
                S.ask_again(platform, oid)
                cid = _client_id_of(S.confirm(platform, oid), oid)
            elif kind == "create":
                made = {"local_only": True} if _local_turn() else {}
                cid = _client_id_of(S.create(platform, folder=str(op.get("folder") or ""),
                                             name=str(op.get("name") or ""), **made))
            elif kind == "restore":
                cid = _client_id_of(S.restore(platform, oid), oid)
            elif kind == "decline":
                cid = _client_id_of(S.decline(platform, oid), oid)
            elif kind == "add_folder":
                S.add_folder(platform, oid, str(op.get("folder") or ""))
            elif kind == "remove_folder":
                got = S.remove_folder(platform, oid, str(op.get("folder") or ""))
                # What was removed, so its Undo puts it back AS IT WAS (a
                # found folder stays found: its Local only check holds).
                was = got.get("folder") if isinstance(got, Mapping) else None
                if isinstance(was, Mapping):
                    op = {**op, "how": str(was.get("how") or ""), "spelled": str(was.get("folder") or "")}
            elif kind == "rename":
                S.rename(platform, oid, str(op.get("name") or ""))
            elif kind == "aliases":
                S.set_aliases(platform, oid, list(op.get("aliases") or ()))
            elif kind == "keep_pref":
                got = S.keep_preference(
                    platform, oid, str(op.get("text") or ""), origin="said",
                    local_only=bool(op.get("local_only")), evidence=list(op.get("evidence") or ()),
                )
                pref = got.get("preference") if isinstance(got, Mapping) else None
                op = {**op, "pref_id": str((pref or {}).get("id") or "")}
            elif kind == "change_pref":
                S.change_preference(
                    platform, oid, str(op.get("pref_id") or ""), str(op.get("text") or ""),
                    **_pref_fields(op, ""),
                )
            elif kind == "forget_pref":
                got = S.forget_preference(platform, oid, str(op.get("pref_id") or ""))
                op = {**op, "view": dict(got.get("preference") or got) if isinstance(got, Mapping) else {}}
            else:
                raise C.ClientRefused("That change is not one Iron Jarvis knows.")
        except (C.ClientError, NotImplementedError) as exc:
            if not done:
                raise
            # Some of it WAS done: never "Nothing was changed" over it.
            why = chat_sentence(exc).replace(" Nothing was changed.", "").strip()
            return cid, done, why
        if kind in _MAKES_A_CLIENT:
            op = {**op, "no_folder": _has_no_folder(platform, cid or oid)}
        done.append({**op, "id": cid if kind in ("create",) else oid})
        if not cid:
            cid = oid
    return cid, done, ""


def _pref_fields(op: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    """The Local only flag, origin and evidence a ``change_pref`` op sets
    (``prefix`` "old_" for its Undo); none for an op written before them."""
    if prefix + "local_only" not in op:
        return {}
    return {
        "local_only": bool(op.get(prefix + "local_only")),
        "origin": str(op.get(prefix + "origin") or "said"),
        "evidence": list(op.get(prefix + "evidence") or ()),
    }


#: Ops that make a client (a Yes, again or after all).
_MAKES_A_CLIENT = frozenset({"confirm", "reconfirm", "create", "restore"})


def _has_no_folder(platform: Any, client_id: str) -> bool:
    """Was the client left with no folder (every home another client's), so
    no chat is told about it? (The independent review: the reply says so.)"""
    try:
        return not _map(platform).roots_of(client_id)
    except Exception:  # noqa: BLE001 — unsure: say nothing more
        return False


def _local_turn() -> bool:
    """Is the turn in flight Local only (a client made here is known only
    from Local only words)?"""
    try:
        from ..providers import egress

        return bool(egress.current_scope().on)
    except Exception:  # noqa: BLE001 — unsure: treat it as Local only (withheld)
        return True


def _note_of(answer: Any) -> str:
    return " ".join(str(answer.get("note") or "").split()) if isinstance(answer, Mapping) else ""


def _undo_one(platform: Any, op: Mapping[str, Any]) -> str:
    """Put back one op; returns what the record said it could NOT put back
    exactly ("" when it did)."""
    kind = str(op.get("op"))
    cid = str(op.get("id") or "")
    if kind == "confirm":
        S.unconfirm(platform, cid, back_to="proposed")
    elif kind == "reconfirm":
        S.unconfirm(platform, cid, back_to="declined")
    elif kind == "ask_again":
        S.decline(platform, cid)
    elif kind == "create":
        S.unconfirm(platform, cid, back_to=str(op.get("reused") or "gone"))
    elif kind == "restore":
        S.decline(platform, cid)
    elif kind == "decline":
        if op.get("was") == "confirmed":
            engine = _engine(platform)
            before = {str(r.get("key")) for r in S.folder_rows(engine, cid) or []}
            note = _note_of(S.restore(platform, cid))
            after = {str(r.get("key")) for r in S.folder_rows(engine, cid) or []}
            # A folder that now overlaps another client's was dropped: say so.
            return note if before - after else ""
        S.ask_again(platform, cid)
    elif kind == "add_folder":
        S.remove_folder(platform, cid, str(op.get("folder") or ""))
    elif kind == "remove_folder":
        how = str(op.get("how") or "")
        folder = str(op.get("spelled") or op.get("folder") or "")
        if how:
            S.add_folder(platform, cid, folder, how=how)
        else:
            S.add_folder(platform, cid, folder)
    elif kind == "rename":
        S.rename(platform, cid, str(op.get("old") or ""))
        S.set_aliases(platform, cid, list(op.get("old_aliases") or ()))
    elif kind == "aliases":
        S.set_aliases(platform, cid, list(op.get("prior") or ()))
    elif kind == "keep_pref":
        S.forget_preference(platform, cid, str(op.get("pref_id") or ""))
    elif kind == "change_pref":
        S.change_preference(platform, cid, str(op.get("pref_id") or ""), str(op.get("old") or ""),
                            **_pref_fields(op, "old_"))
    elif kind == "forget_pref":
        view = op.get("view") if isinstance(op.get("view"), Mapping) else {}
        S.keep_preference(
            platform, cid, str(view.get("text") or ""), origin=str(view.get("origin") or "said"),
            local_only=bool(view.get("local_only")), evidence=list(view.get("evidence") or ()),
        )
    else:
        raise R.RecordError("This change has nothing Undo can put back.")
    return ""


def _same(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    keys = ("status", "name", "aliases", "folders", "prefs")
    if a is None or b is None:
        return a is b
    return all(a.get(k) == b.get(k) for k in keys)


def restore_client(platform: Any, client_id: str, prior: Any, desc: Mapping[str, Any]) -> str:
    """``POST /undo`` for a client change from chat: what THAT change did,
    nothing else, in reverse order, through the record's own functions.
    Refuses in one sentence when the client was changed again since.
    BLOCKING."""
    meta = desc if isinstance(desc, Mapping) else {}
    ops = [op for op in (meta.get("ops") or []) if isinstance(op, Mapping)]
    if not ops:
        raise R.RecordError("This change has nothing Undo can put back.")
    name = _flat(meta.get("label_name") or "") or "That client"
    after = meta.get("after") if isinstance(meta.get("after"), Mapping) else None
    if after is None:
        raise R.RecordError(
            f"Iron Jarvis could not read how this change left {name}, so an Undo would be a guess; change "
            "it on its page instead."
        )
    try:
        current = snap_client(platform, client_id)
    except C.ClientError as exc:
        raise R.RecordError(exc.sentence) from exc
    if not _same(current, after):
        raise R.RecordError(
            f"{name} was changed again after this change, so Undo would undo that too; change it on "
            "its page instead."
        )
    notes: list[str] = []
    try:
        for op in reversed(ops):
            note = _undo_one(platform, op)
            if note:
                notes.append(note)
    except C.ClientError as exc:
        raise R.RecordError(exc.sentence) from exc
    except (NotImplementedError, AttributeError) as exc:
        raise R.RecordError("This change cannot be undone in this build; change it on the Clients page.") from exc
    if notes:
        # Never "as it was" over what the record could not put back.
        return f"undo: {name} is back, but not exactly as before this change: " + " ".join(notes)
    return f"undo: {name} is back as it was before this change"


def _did(op: Mapping[str, Any], name: str, *, no_folder: bool = False) -> str:
    kind = str(op.get("op"))
    poss = possessive(name)
    if kind in ("confirm", "reconfirm", "create", "restore"):
        if op.get("no_folder"):
            return f"{name} is a client now, but has no folder yet, so no chat is told about {name}"
        return f"{name} is a client now"
    if kind == "ask_again":
        return f"{name} waits for your answer again (you had said it was not a client)"
    if kind == "decline":
        if op.get("was") == "confirmed":
            return f"{name} is marked as not a client; {poss} folders and preferences are kept, unused"
        return f"{name} is marked as not a client, and Iron Jarvis won't ask about {name} again"
    if kind == "add_folder":
        return f"added the folder {op.get('folder')} to {name}"
    if kind == "remove_folder":
        return f"took the folder {op.get('folder')} out of {poss} folders (its work stays on record)"
    if kind == "rename":
        return f"renamed {op.get('old')} to {op.get('name')}"
    if kind == "aliases":
        extra = [a for a in (op.get("aliases") or []) if a not in (op.get("prior") or [])]
        return f"{name} is now also known as " + ", ".join(str(a) for a in extra)
    if kind == "keep_pref":
        if no_folder:
            return f"kept for {name} only: {_q(op.get('text'))}; no chat is told it until {name} has a folder"
        return f"kept for {name} only: {_q(op.get('text'))}; chats in {poss} folders are told it"
    if kind == "change_pref":
        return f"changed what is kept for {name}: {_q(op.get('old'))} now reads {_q(op.get('text'))}"
    if kind == "forget_pref":
        view = op.get("view") if isinstance(op.get("view"), Mapping) else {}
        return f"forgot what was kept for {name}: {_q(view.get('text'))}"
    return "done"


def did_words(name: str, client_id: str, done: list[dict[str, Any]]) -> str:
    """What the ops DONE did, in words, read from the ops themselves (never
    from the plan's promise: an op that did not run is not said)."""
    no_folder = any(op.get("no_folder") for op in done)
    parts = [_did(op, name, no_folder=no_folder) for op in done]
    text = "; ".join(p for p in parts if p)
    text = (text[:1].upper() + text[1:] + ".") if text else "Nothing was changed."
    if any(str(op.get("op")) in ("confirm", "reconfirm", "create", "restore") for op in done):
        text += f" Its page: {client_href(client_id)}"
    return text


def restore_kind(platform: Any, kind: str, name: str, prior: Any, desc: Mapping[str, Any]) -> str:
    """``records.restore`` for this module's kind. BLOCKING."""
    if kind != KIND:
        raise R.RecordError(f"unknown record kind {kind!r}")
    return restore_client(platform, name, prior, desc)


# --------------------------------------------------------------------------- #
# the Undo descriptor of THIS call (the records_self pattern)
# --------------------------------------------------------------------------- #

_DESC: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar("ij_client_record_desc", default=None)


def _stash(desc: "dict[str, Any] | None") -> "dict[str, Any] | None":
    _DESC.set(desc)
    return desc


def _note(**fields: Any) -> None:
    """Add ``fields`` to the descriptor this call captured (journaled only
    after a SUCCESSFUL execute). No-op with no capture."""
    desc = _DESC.get()
    _DESC.set(None)
    if not desc:
        return
    try:
        meta = json.loads(desc.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(meta, dict):
        return
    meta.update(fields)
    desc["pre_inline"] = json.dumps(meta, default=str)


# --------------------------------------------------------------------------- #
# plans: what one call will do, read from the record and the owner's words
# --------------------------------------------------------------------------- #


def _plan(**kw: Any) -> dict[str, Any]:
    out = {"ops": [], "problem": "", "words": "", "nothing": "", "client_id": "", "name": "", "title": "",
           "old": None, "new": None, "said": ""}
    out.update(kw)
    return out


def _yes_no(value: Any) -> str:
    v = str(value if value is not None else "").strip().lower()
    if v in ("yes", "y", "true", "client", "a client", "is a client", "confirm"):
        return "yes"
    if v in ("no", "n", "false", "not a client", "not", "decline"):
        return "no"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return ""


def plan_answer(platform: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """``client_answer {name, answer, folder?}``. BLOCKING."""
    g = gate(platform)
    if g.problem:
        return _plan(problem=g.problem)
    name = _flat(args.get("name"))
    folder = " ".join(str(args.get("folder") or "").split())
    answer = _yes_no(args.get("answer"))
    if not name:
        return _plan(problem="Say which name: a client is kept by its name.")
    if answer not in ("yes", "no"):
        return _plan(problem="Say whether it is a client: answer yes or no.")
    try:
        t = find(platform, g.cmap, name)
    except C.ClientError as exc:
        return _plan(problem=exc.sentence)
    shown = t.name or name
    if not (_names_it(g.words, shown, t.aliases) or _names_it(g.words, name)):
        return _plan(problem=_not_named_words(shown))
    wall = _wall_problem(g, t)
    if wall:
        return _plan(problem=wall)
    poss = possessive(shown)
    ops: list[dict[str, Any]] = []
    if answer == "yes":
        if t.status == "confirmed":
            if not folder:
                return _plan(nothing=f"{shown} is already a client; nothing was changed.", client_id=t.id, name=shown)
            ops.append({"op": "add_folder", "id": t.id, "folder": folder})
            words = f"Adds the folder {folder} to {shown}. Chats in it are told about {shown} only."
            title, old, new = "Client folder added", None, folder
        elif t.status == "proposed":
            ops.append({"op": "confirm", "id": t.id})
            if folder:
                ops.append({"op": "add_folder", "id": t.id, "folder": folder})
            words = (
                f"Makes {shown} a client: Iron Jarvis keeps {poss} folders, work and preferences together, "
                f"and tells chats about {shown} only in {poss} folders."
                + (f" It also adds the folder {folder}." if folder else "")
            )
            title, old, new = "Client confirmed", "Waiting for your answer", "A client"
        elif t.status == "declined" and t.was_client:
            ops.append({"op": "restore", "id": t.id})
            if folder:
                ops.append({"op": "add_folder", "id": t.id, "folder": folder})
            words = f"Makes {shown} a client again, with {poss} folders that are still free."
            title, old, new = "Client again", "Not a client", "A client"
        elif t.status == "declined":
            if folder:
                ops.append({"op": "create", "folder": folder, "name": shown, "reused": "declined"})
            else:
                # Two recorded steps (the independent review): if the Yes
                # cannot land after the No was undone, the reply says so and
                # its Undo puts the No back.
                ops.append({"op": "ask_again", "id": t.id})
                ops.append({"op": "confirm", "id": t.id})
            words = (
                f"Makes {shown} a client after all (you had said it was not one)"
                + (f", with the folder {folder}." if folder else ", with the folders it was seen in.")
                + f" Chats are told about {shown} only in {poss} folders."
            )
            title, old, new = "Client confirmed", "Not a client", "A client"
        else:
            if not folder:
                return _plan(problem=(
                    f"{shown} has no folder yet, so no chat is told about it. Tell me its folder."
                ))
            ops.append({"op": "create", "folder": folder, "name": shown, "reused": ""})
            words = (
                f"Makes {shown} a client with the folder {folder}. Chats are told about {shown} only in "
                f"{poss} folders."
            )
            title, old, new = "Client added", None, "A client"
    else:
        if t.status == "confirmed":
            ops.append({"op": "decline", "id": t.id, "was": "confirmed"})
            words = (
                f"Marks {shown} as not a client: chats in {poss} folders stop being told about {shown}. "
                f"{poss} folders and preferences are kept, unused, and {shown} can be made a client again."
            )
            title, old, new = "Not a client", "A client", "Not a client"
        elif t.status == "proposed":
            ops.append({"op": "decline", "id": t.id, "was": "proposed"})
            words = f"Marks {shown} as not a client. Iron Jarvis won't ask about {shown} again."
            title, old, new = "Not a client", "Waiting for your answer", "Not a client"
        elif t.status == "declined":
            return _plan(nothing=f"{shown} is already marked as not a client; nothing was changed.",
                         client_id=t.id, name=shown)
        else:
            return _plan(nothing=f"No client or noticed name called {shown} is on record, so nothing was changed.",
                         name=shown)
    return _plan(ops=ops, words=words, client_id=t.id, name=shown, title=title, old=old, new=new)


def _target_client(platform: Any, g: Gate, ref: str, *, what: str, waiting_ok: bool) -> tuple[Target, str]:
    """The client a change is for: the turn's own client by default; one the
    owner's words name otherwise; never ANOTHER client in a client's folder."""
    try:
        if ref:
            t = find(platform, g.cmap, ref)
            if not t.known or t.status == "declined" or (t.status == "proposed" and not waiting_ok):
                return t, (
                    f"{_flat(t.name or ref)} is not one of your clients, so nothing was changed."
                    + (" Say it is a client first." if t.status != "declined" else "")
                )
        else:
            own = g.own_id
            if own and own in (getattr(g.cmap, "names", {}) or {}):
                t = find(platform, g.cmap, own)
            else:
                named = sorted(g.named(waiting=waiting_ok))
                if len(named) != 1:
                    return Target(), (
                        "Say which client this is for, by name." if not named else
                        "Your message names more than one client; say which one this is for."
                    )
                t = find(platform, g.cmap, named[0])
    except C.ClientError as exc:
        return Target(), exc.sentence
    if g.owned:
        if not g.is_own(t):
            return t, _other_client_words(g, what)
        named = g.named()
        if named - {t.id} and t.id not in named:
            return t, _other_client_words(g, what)
        return t, ""
    if not _names_it(g.words, t.name, t.aliases):
        return t, _not_named_words(t.name)
    return t, ""


def _where(g: Gate) -> str:
    client = g.client
    return _folder_name(getattr(client, "folder", "")) if client is not None else ""


def _thread_link(thread_id: str) -> str:
    return f"/chat?thread={thread_id}" if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", thread_id or "") else ""


def _said_evidence(g: Gate, thread_id: str) -> list[dict[str, Any]]:
    """The owner's own words in this chat, as a preference's evidence."""
    return [{
        "quote": _flat(g.words, QUOTE_CHARS),
        "thread_id": thread_id if _thread_link(thread_id) else "",
        "where": _where(g),
        "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "link": _thread_link(thread_id),
    }]


def plan_preference_add(platform: Any, args: Mapping[str, Any], *, thread_id: str = "") -> dict[str, Any]:
    """``client_preference_add {text, client?}``. BLOCKING."""
    from ..learning.preferences import PreferenceInvalid, clean_text
    from ..providers import egress

    g = gate(platform)
    if g.problem:
        return _plan(problem=g.problem)
    try:
        text = clean_text(args.get("text"))
    except PreferenceInvalid as exc:
        return _plan(problem=str(exc))
    t, problem = _target_client(platform, g, _flat(args.get("client")), what="preference", waiting_ok=True)
    if problem:
        return _plan(problem=problem)
    poss = possessive(t.name)
    evidence = _said_evidence(g, thread_id)
    local = bool(egress.current_scope().on)
    keep = {"op": "keep_pref", "id": t.id, "text": text, "local_only": local, "evidence": evidence}
    if t.status == "proposed":
        ops = [{"op": "confirm", "id": t.id}, keep]
        words = f"Make {t.name} a client and keep for {t.name}: {_q(text)}"
        title = "Client confirmed and preference kept"
    else:
        ops = [keep]
        words = f"Keep for {t.name} only (told in {poss} folders): {_q(text)}"
        title = "Client preference kept"
    return _plan(ops=ops, words=words, client_id=t.id, name=t.name, title=title, old=None, new=text, said=text)


def _client_prefs(platform: Any, client_id: str) -> list[dict[str, Any]]:
    return [p for p in (S.preferences(_engine(platform), client_id) or []) if isinstance(p, Mapping)]


_REF_WORD = re.compile(r"[a-z0-9]+")
_REF_STOP = frozenset({"the", "a", "an", "that", "this", "about", "for", "to", "of", "my", "preference", "uses",
                       "use", "wants", "want", "is", "and", "or", "they", "it"})


def _ref_words(text: Any) -> set[str]:
    return {w for w in _REF_WORD.findall(str(text or "").lower()[:600]) if w not in _REF_STOP and len(w) > 1}


def _resolve_pref(platform: Any, client_id: str, name: str, ref_id: str, ref_text: str) -> tuple[dict[str, Any] | None, str]:
    prefs = _client_prefs(platform, client_id)
    if not prefs:
        return None, f"Nothing is kept for {name} yet."
    if ref_id:
        for p in prefs:
            if str(p.get("id")) == ref_id:
                return dict(p), ""
        return None, f"That is not one of the preferences kept for {name}."
    want = _ref_words(ref_text)
    for p in prefs:
        if " ".join(str(p.get("text") or "").split()).casefold() == " ".join(ref_text.split()).casefold():
            return dict(p), ""
    scored = sorted(((len(want & _ref_words(p.get("text"))), i) for i, p in enumerate(prefs)), reverse=True)
    if scored and scored[0][0]:
        best = scored[0][0]
        top = [prefs[i] for hit, i in scored if hit == best]
        if len(top) == 1:
            return dict(top[0]), ""
        return None, f"More than one preference kept for {name} matches; say which one."
    some = ", ".join(_q(p.get("text"), 50) for p in prefs[:3])
    return None, f"No preference kept for {name} matches. Kept for {name}: {some}."


def plan_preference_change(platform: Any, args: Mapping[str, Any], *, thread_id: str = "") -> dict[str, Any]:
    """``client_preference_change {id|text, new_text, client?}``. The new
    words are the owner's, said in THIS chat: the row becomes Local only when
    this turn is (never the other way), and its evidence is this message.
    BLOCKING."""
    from ..learning.preferences import PreferenceInvalid, clean_text
    from ..providers import egress

    g = gate(platform)
    if g.problem:
        return _plan(problem=g.problem)
    try:
        new = clean_text(args.get("new_text"))
    except PreferenceInvalid as exc:
        return _plan(problem=str(exc))
    t, problem = _target_client(platform, g, _flat(args.get("client")), what="preference", waiting_ok=False)
    if problem:
        return _plan(problem=problem)
    try:
        pref, problem = _resolve_pref(platform, t.id, t.name, _flat(args.get("id")), _flat(args.get("text"), 600))
    except C.ClientError as exc:
        return _plan(problem=exc.sentence)
    if pref is None:
        return _plan(problem=problem)
    old = str(pref.get("text") or "")
    if " ".join(old.split()) == new:
        return _plan(nothing=f"That preference for {t.name} already reads {_q(new)}; nothing was changed.",
                     client_id=t.id, name=t.name)
    was_local = bool(pref.get("local_only"))
    ops = [{
        "op": "change_pref", "id": t.id, "pref_id": str(pref.get("id")), "text": new, "old": old,
        "local_only": was_local or bool(egress.current_scope().on), "origin": "said",
        "evidence": _said_evidence(g, thread_id),
        "old_local_only": was_local, "old_origin": str(pref.get("origin") or "said"),
        "old_evidence": list(pref.get("evidence") or ()),
    }]
    words = f"Change what is kept for {t.name}: {_q(old)} becomes {_q(new)}"
    return _plan(ops=ops, words=words, client_id=t.id, name=t.name, title="Client preference changed", old=old, new=new)


def plan_preference_forget(platform: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """``client_preference_forget {id|text, client?}``. BLOCKING."""
    g = gate(platform)
    if g.problem:
        return _plan(problem=g.problem)
    t, problem = _target_client(platform, g, _flat(args.get("client")), what="preference", waiting_ok=False)
    if problem:
        return _plan(problem=problem)
    try:
        pref, problem = _resolve_pref(platform, t.id, t.name, _flat(args.get("id")), _flat(args.get("text"), 600))
    except C.ClientError as exc:
        return _plan(problem=exc.sentence)
    if pref is None:
        return _plan(problem=problem)
    old = str(pref.get("text") or "")
    ops = [{"op": "forget_pref", "id": t.id, "pref_id": str(pref.get("id"))}]
    words = f"Forget what is kept for {t.name}: {_q(old)}"
    return _plan(ops=ops, words=words, client_id=t.id, name=t.name, title="Client preference forgotten", old=old, new=None)


def plan_update(platform: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """``client_update {client, rename?, add_folder?, remove_folder?,
    aliases?}``. BLOCKING."""
    g = gate(platform)
    if g.problem:
        return _plan(problem=g.problem)
    t, problem = _target_client(platform, g, _flat(args.get("client")), what="change", waiting_ok=False)
    if problem:
        return _plan(problem=problem)
    ops: list[dict[str, Any]] = []
    said: list[str] = []
    new_name = _flat(args.get("rename"))
    if new_name and new_name != t.name:
        if not _names_it(g.words, new_name):
            return _plan(problem=_not_named_words(new_name))
        ops.append({"op": "rename", "id": t.id, "name": new_name, "old": t.name, "old_aliases": list(t.aliases)})
        said.append(f"renames {t.name} to {new_name}")
    add = " ".join(str(args.get("add_folder") or "").split())
    if add:
        ops.append({"op": "add_folder", "id": t.id, "folder": add})
        said.append(f"adds the folder {add}")
    remove = " ".join(str(args.get("remove_folder") or "").split())
    if remove:
        ops.append({"op": "remove_folder", "id": t.id, "folder": remove})
        said.append(f"takes the folder {remove} out of {possessive(t.name)} folders (its work stays on record)")
    raw_aliases = args.get("aliases")
    if isinstance(raw_aliases, str):
        raw_aliases = [raw_aliases]
    extra = [_flat(a) for a in (raw_aliases or []) if _flat(a)]
    if extra:
        for alias in extra:
            if not _names_it(g.words, alias):
                return _plan(problem=_not_named_words(alias))
        ops.append({"op": "aliases", "id": t.id, "aliases": [*t.aliases, *extra], "prior": list(t.aliases)})
        said.append("also knows it as " + ", ".join(extra))
    if not ops:
        return _plan(problem=f"Say what to change about {t.name}: its name, a folder, or another name it goes by.")
    words = "For " + t.name + ", Iron Jarvis " + "; ".join(said) + "."
    return _plan(ops=ops, words=words, client_id=t.id, name=t.name, title="Client updated",
                 old=t.name, new=new_name or t.name)


# --------------------------------------------------------------------------- #
# the reader
# --------------------------------------------------------------------------- #


def _local_only_known(platform: Any) -> set[str] | None:
    """The clients known ONLY from Local only work, so a cloud turn is not
    told their names. Asked AGAIN each time (the independent review; the
    house rule that the source is asked again, never a frozen copy):

    * made by the owner in a Local only chat (``made_local`` in its record);
    * noticed, with folders on record, none of which now holds work that is
      neither Local only nor kept home (``block.shared_work``);
    * noticed, with no folder on record, every home it was noticed in Local
      only (its evidence).

    None when it cannot be read (the caller then withholds every client from
    a cloud turn)."""
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..workmap.clients.block import shared_work
        from ..workmap.models import CLIENT_ADDED, CLIENT_NOTICED, WorkClient, WorkClientFolder

        engine = _engine(platform)
        out: set[str] = set()
        with session_scope(engine) as db:
            rows = list(db.exec(
                select(WorkClient.id, WorkClient.how, WorkClient.evidence_json).limit(5000)
            ))
            folder_keys: dict[str, list[str]] = {}
            for key, cid in db.exec(select(WorkClientFolder.folder_key, WorkClientFolder.client_id)):
                folder_keys.setdefault(str(cid), []).append(str(key))
        for cid, how, evidence in rows:
            try:
                ev = json.loads(evidence or "{}") or {}
                homes = ev.get("homes") or []
            except (TypeError, ValueError, AttributeError):
                ev, homes = {}, []
            if how == CLIENT_ADDED:
                if isinstance(ev, dict) and ev.get("made_local") is True:
                    out.add(str(cid))
                continue
            if how != CLIENT_NOTICED:
                continue
            mine = folder_keys.get(str(cid)) or []
            if mine:
                if not any(shared_work(engine, k) for k in mine[:20]):
                    out.add(str(cid))
            elif homes and all(isinstance(h, dict) and h.get("local") is True for h in homes):
                out.add(str(cid))
        return out
    except Exception:  # noqa: BLE001 — fail closed
        log.debug("client_list: Local only read failed", exc_info=True)
        return None


def _row_line(row: Mapping[str, Any]) -> str:
    folders = [str(f.get("name") or "") for f in (row.get("folders") or []) if isinstance(f, Mapping)]
    folders = [f for f in folders if f][:4]
    parts = [("folders " + ", ".join(folders)) if folders else "no folder yet"]
    parts.append(_plural(int(row.get("work_count") or 0), "piece of work", "pieces of work"))
    parts.append(_plural(int(row.get("preferences_count") or 0), "preference"))
    return f"{_flat(row.get('name'))}: " + "; ".join(parts) + f" (its page: {client_href(row.get('id'))})"


def list_words(platform: Any) -> tuple[str, dict[str, Any]]:
    """What ``client_list`` answers in THIS turn, walled. BLOCKING. Raises
    ``ClientError`` when the record cannot be read."""
    from ..providers import egress

    turn = W.current()
    if not getattr(turn, "ok", True):
        raise C.ClientsUnreadable()
    try:
        lst = PG.clients_list(platform)
        cmap = _map(platform)
    except C.ClientError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed
        raise C.ClientsUnreadable() from exc
    clients = [r for r in (lst.get("clients") or []) if isinstance(r, Mapping) and r.get("id")]
    waiting = [r for r in (lst.get("waiting") or []) if isinstance(r, Mapping) and r.get("id")]
    words = owner_words()
    g = Gate(turn, cmap, words)
    lines: list[str] = []
    shown: list[dict[str, Any]] = []
    own = g.own_id
    if g.owned:  # a client's, a waiting name's or a not-yet-answered Clients folder
        mine = [r for r in clients if own and str(r.get("id")) == own]
        others = len(clients) - len(mine)
        if mine:
            lines.append("This chat's folder belongs to a client. " + _row_line(mine[0]))
            shown.append({"id": own, "name": str(mine[0].get("name") or "")})
        else:
            lines.append("This chat's folder is kept apart for one client, so other clients are not listed here.")
        if others:
            lines.append(
                f"{_plural(others, 'other client')} {'is' if others == 1 else 'are'} on the Clients page "
                f"({CLIENTS_HREF}); a chat in one client's folder is not told about the others."
            )
        if waiting:
            lines.append(f"{_plural(len(waiting), 'name')} Iron Jarvis noticed in your folders "
                         f"{'waits' if len(waiting) == 1 else 'wait'} for your answer on the Clients page "
                         f"({WAITING_HREF}).")
        return "\n".join(lines), {"clients": shown, "waiting": len(waiting), "kept_here": 0}
    local_ok = egress.may_recall_local_only_chats()
    kept_here: set[str] = set()
    if not local_ok:
        known = _local_only_known(platform)
        kept_here = {str(r.get("id")) for r in clients} if known is None else known
    listed = [r for r in clients if str(r.get("id")) not in kept_here]
    withheld = len(clients) - len(listed)
    if listed:
        lines.append(f"Your clients ({len(listed)}):")
        for r in listed[:LIST_LIMIT]:
            lines.append("- " + _row_line(r))
            shown.append({"id": str(r.get("id")), "name": str(r.get("name") or "")})
        if len(listed) > LIST_LIMIT:
            lines.append(f"…and {len(listed) - LIST_LIMIT} more on the Clients page ({CLIENTS_HREF}).")
    elif not withheld:
        lines.append(
            "You have no clients yet. When a name keeps coming back in your folders Iron Jarvis asks about "
            "it, and any folder can be made a client on the Clients page."
        )
    if withheld:
        lines.append(
            f"{_plural(withheld, 'more client')} {'is' if withheld == 1 else 'are'} kept on this computer "
            "(known only from Local only work), so not named in this chat."
        )
    named_waiting = _named(words, getattr(cmap, "waiting_names", {}) or {})
    for r in waiting:
        if str(r.get("id")) in named_waiting:
            about = _flat(r.get("about"))
            lines.append(
                f"Iron Jarvis noticed {_flat(r.get('name'))} in your folders"
                + (f" ({about})" if about else "")
                + f" and waits for your answer: is {_flat(r.get('name'))} a client?"
            )
    rest = len([r for r in waiting if str(r.get("id")) not in named_waiting])
    if rest:
        lines.append(
            f"{_plural(rest, 'more name')} Iron Jarvis noticed in your folders "
            f"{'waits' if rest == 1 else 'wait'} for your answer on the Clients page ({WAITING_HREF})."
        )
    return "\n".join(lines), {"clients": shown, "waiting": len(waiting), "kept_here": withheld}


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #


class ClientListTool(Tool):
    name = CLIENT_READER
    description = (
        "List the user's clients (people and businesses they do work for, each known by its folders), "
        "as THIS chat may be told: in a client's folder only that client; elsewhere every client by "
        "name and folder names. Read-only. Names Iron Jarvis noticed that wait for the user's answer "
        "are a count unless the user named one."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            output, data = await asyncio.to_thread(list_words, self.platform)
        except C.ClientError as exc:
            return ToolResult(ok=False, error=exc.sentence)
        except Exception:  # noqa: BLE001
            log.warning("client_list failed", exc_info=True)
            return ToolResult(ok=False, error=UNREADABLE)
        return ToolResult(ok=True, output=output, data={**data, "client_id": "", "client": ""})


class _ClientTool(R._RecordTool):  # noqa: SLF001 — the records module's own base
    RECORD = KIND

    def plan(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        raise NotImplementedError

    async def _planned(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self.plan, dict(args or {}), ctx)
        except Exception:  # noqa: BLE001 — a plan that cannot be made changes nothing
            log.warning("%s: plan failed", self.name, exc_info=True)
            return _plan(problem=UNREADABLE)

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """The card names the client and says what the change does; a call
        that changes nothing (refused, or already so) is no card at all.
        "Don't ask" is the owner's own choice and is kept; an unattended door
        (a schedule, the phone) is refused in words, never asked there: a
        client's name never goes to the phone."""
        plan = await self._planned(args, ctx)
        if plan["problem"] or not plan["ops"]:
            return SpendCheck(ask=False, cost_note=CHANGES_NOTHING)
        return SpendCheck(ask=True, words=plan["words"], dont_ask_skips=True, attended_only=True)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        R._CHANGE_ID.set("")  # noqa: SLF001
        plan = await self._planned(args, ctx)
        if plan["problem"] or not plan["ops"] or not reversible(plan["ops"]):
            return None
        return _stash(await R.capture_create_undo(self.platform, KIND, "", pending=True))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        plan = await self._planned(args, ctx)
        if plan["problem"]:
            return ToolResult(ok=False, error=plan["problem"])
        if not plan["ops"]:
            # Nothing ran, so nothing is claimed: a reply saying it changed
            # the client gets the "not made" note (daemon/change_claims).
            return ToolResult(ok=False, error=plan["nothing"] or "Nothing was changed.")
        try:
            cid, done, stopped = await asyncio.to_thread(run_ops, self.platform, plan["ops"], plan["client_id"])
        except C.ClientError as exc:
            return ToolResult(ok=False, error=chat_sentence(exc))
        except NotImplementedError:
            return ToolResult(ok=False, error=NOT_READY)
        try:
            after = await asyncio.to_thread(snap_client, self.platform, cid)
        except Exception:  # noqa: BLE001
            after = None
        R.bind_created(cid)
        _note(ops=done, after=after, label_name=plan["name"])
        name = plan["name"]
        output = did_words(name, cid, done)
        if stopped:
            output += f" The rest was not done: {stopped}"
        data: dict[str, Any] = {"client_id": cid, "client": name}
        if R._CHANGE_ID.get():  # noqa: SLF001 — a card only with an Undo
            data["record_change"] = self._card(plan["title"], f"Client “{name}”", plan["old"], plan["new"],
                                               f"client.{cid}")
        return ToolResult(ok=True, output=output, data=data)


class ClientAnswerTool(_ClientTool):
    name = "client_answer"
    description = (
        "Say whether a name is one of the user's clients, in the user's own words (`name` exactly as the "
        "user wrote it; `answer` yes or no; `folder` = the client's folder when the user gives one). "
        "Answers a name Iron Jarvis noticed in the user's folders, marks a client as not a client, or "
        "makes a new client from its folder. The user approves on a card; the change has an Undo. Only a "
        "name in the user's own message is answered."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "answer": {"type": "string", "enum": ["yes", "no"]},
            "folder": {"type": "string"},
        },
        "required": ["name", "answer"],
    }

    def plan(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        return plan_answer(self.platform, args)


class ClientUpdateTool(_ClientTool):
    name = "client_update"
    description = (
        "Change one of the user's clients: rename it (`rename`), add or take out one of its folders "
        "(`add_folder` / `remove_folder`, a full folder path), or add other names it goes by (`aliases`). "
        "`client` = the client's name as the user wrote it; leave it out in a chat in that client's "
        "folder. The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "client": {"type": "string"},
            "rename": {"type": "string"},
            "add_folder": {"type": "string"},
            "remove_folder": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
        },
    }

    def plan(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        return plan_update(self.platform, args)


class ClientPreferenceAddTool(_ClientTool):
    name = "client_preference_add"
    description = (
        "Keep something the user wants for ONE client (`text` = one sentence in the user's words, at most "
        "280 characters: \"Uses cash basis.\"; `client` = the client's name as the user wrote it, left out "
        "in a chat in that client's folder). It is told only to chats, agent runs and terminal help in "
        "that client's folders, never to other clients' chats. Use this, not remember_preference, for a "
        "preference about one client. The user approves on a card; Undo removes it again."
    )
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "client": {"type": "string"}},
        "required": ["text"],
    }

    def plan(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        return plan_preference_add(self.platform, args, thread_id=str(getattr(ctx, "thread_id", "") or ""))


class ClientPreferenceChangeTool(_ClientTool):
    name = "client_preference_change"
    description = (
        "Change the words of something kept for one client (`id` = its id, or `text` = its words; "
        "`new_text` = the new sentence; `client` = the client's name as the user wrote it, left out in a "
        "chat in that client's folder). The user approves on a card that quotes both; Undo puts the old "
        "words back."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "text": {"type": "string"},
            "new_text": {"type": "string"},
            "client": {"type": "string"},
        },
        "required": ["new_text"],
    }

    def plan(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        return plan_preference_change(self.platform, args, thread_id=str(getattr(ctx, "thread_id", "") or ""))


class ClientPreferenceForgetTool(_ClientTool):
    name = "client_preference_forget"
    description = (
        "Forget something kept for one client (`id` = its id, or `text` = its words; `client` = the "
        "client's name as the user wrote it, left out in a chat in that client's folder). The user "
        "approves on a card that quotes it; Undo keeps it again."
    )
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "client": {"type": "string"}},
    }

    def plan(self, args: Mapping[str, Any], ctx: "ToolContext | None" = None) -> dict[str, Any]:
        return plan_preference_forget(self.platform, args)


def client_tools(platform: Any) -> list[Tool]:
    return [
        ClientListTool(platform),
        ClientAnswerTool(platform),
        ClientUpdateTool(platform),
        ClientPreferenceAddTool(platform),
        ClientPreferenceChangeTool(platform),
        ClientPreferenceForgetTool(platform),
    ]


# --------------------------------------------------------------------------- #
# arming: which of these a message plausibly asks for (linear patterns)
# --------------------------------------------------------------------------- #

#: Sentences, cut by plain splits (never a regex with a gap).
_SENTENCE_END = re.compile(r"[.!?;\n]+")
SENTENCE_CHARS = 400

#: "who are my clients", "show me my clients", "list our clients".
_LIST_RX = re.compile(
    r"\b(?:who\s+are|show(?:\s+me)?|list|see|what\s+are|which\s+are|tell\s+me|give\s+me)\s+"
    r"(?:all\s+)?(?:of\s+)?(?:my|our|the)\s+clients\b"
    r"|\bmy\s+client\s+list\b|\bhow\s+many\s+clients\b|\bclients\s+(?:do\s+)?(?:i|we)\s+have\b",
    re.IGNORECASE,
)
#: A name as the owner writes one: capitalised words (at most five).
_NAME = r"(?P<name>[A-Z][\w'’&-]*(?:\s+[A-Z&][\w'’&-]*){0,4})"
#: What makes "<Name> is a client" about something else ("a client portal").
_CLIENT_THING = (
    r"(?!\s+(?:e-?mails?|portals?|meetings?|calls?|files?|folders?|lists?|letters?|accounts?|names?|records?"
    r"|notes?|pages?|projects?|work\b|side|servers?|apps?|application|software|machines?|computers?|id\b"
    r"|numbers?|codes?|base|services?|support|requests?|questions?|issues?|programs?|tools?|versions?))"
)
_A_CLIENT = r"(?i:(?:not\s+|no\s+longer\s+)?(?:a|my|our)\s+(?:new\s+)?clients?|one\s+of\s+(?:my|our)\s+clients)\b"
_ANSWER_RXS = (
    re.compile(_NAME + r"\s+(?i:is|are)\s+" + _A_CLIENT + _CLIENT_THING),
    re.compile(_NAME + r"\s+(?i:isn['’]t|aren['’]t)\s+(?i:a|my|our)\s+clients?\b" + _CLIENT_THING),
    re.compile(
        r"\b(?i:make|mark|add|set\s+up|treat|count)\s+" + _NAME
        + r"\s+(?i:as\s+)?(?i:(?:not\s+)?(?:a|my|our)\s+(?:new\s+)?clients?)\b" + _CLIENT_THING
    ),
)
#: A capitalised word that starts a sentence but names nobody.
_NOT_A_NAME = frozenset(
    "this that it he she they there here which who what each every one someone everyone nobody the a an my "
    "our your his her their yes no ok okay so and but also now then is are was".split()
)
#: A preference said about a client ("Hartley's wants cash basis").
_PREF_SHAPE = re.compile(
    r"\b(?:wants?|prefers?|likes?|needs?|uses?|would\s+(?:like|rather)|always|never|asks?\s+(?:for|that)"
    r"|insists?\s+on|expects?|requires?|is\s+on\s+(?:cash|accrual))\b",
    re.IGNORECASE,
)
#: The turn's own client as the subject ("they want…", "for this client…").
_OWN_SUBJECT = re.compile(
    r"\A\s*(?:(?:remember|note|keep\s+in\s+mind)\s+(?:that\s+)?)?(?:they|their|he|she|the\s+client|this\s+client)\b"
    r"|\bfor\s+(?:this|the)\s+client\b",
    re.IGNORECASE,
)
_CHANGE_PREF = re.compile(
    r"\b(?:change|update|edit|reword|fix|correct|replace)\b[^\n]{0,60}?\bpreferences?\b"
    r"|\b(?:now|instead)\s+(?:wants?|prefers?|uses?)\b|\binstead\s+of\b",
    re.IGNORECASE,
)
_FORGET_PREF = re.compile(
    r"\b(?:forget|drop|remove|delete|stop\s+remembering|erase)\b"
    r"|\bno\s+longer\s+(?:wants?|uses?|prefers?|needs?|likes?)\b",
    re.IGNORECASE,
)
#: A folder path the owner wrote (a drive, a share, a home folder).
_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|\\\\[\w.-]|~[\\/])")
_FOLDER_VERB = re.compile(r"\b(?:add|put|link|attach|move|remove|take|drop|include)\b", re.IGNORECASE)
_FOLDER_WORD = re.compile(r"\bfolders?\b", re.IGNORECASE)
_RENAME = re.compile(r"\b(?:rename|re-name)\b", re.IGNORECASE)
_ALIAS = re.compile(
    r"\b(?:is\s+also\s+(?:called|known\s+as|written|spelled|spelt)|also\s+goes\s+by|goes\s+by|aka|also\s+called)\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [s.strip()[:SENTENCE_CHARS] for s in _SENTENCE_END.split(text) if s.strip()]


def _names_table(client_names: Any, in_client: Any) -> dict[str, Any]:
    """The names the stream lane read off the loop (:func:`live_names`, or a
    plain list of names), plus the turn's own client."""
    table: dict[str, Any] = {}
    if isinstance(client_names, Mapping):
        for k, v in client_names.items():
            if isinstance(v, (tuple, list)) and len(v) == 3:
                table[str(k)] = (str(v[0]), str(v[1]), tuple(v[2] or ()))
            elif isinstance(v, str):
                table[str(k)] = (v, _sig(v), ())
    else:
        for i, n in enumerate(client_names or ()):
            if isinstance(n, str) and n.strip():
                table[f"n{i}"] = (n, _sig(n), ())
    own = getattr(in_client, "name", in_client)
    if isinstance(own, str) and own.strip():
        table["_own"] = (own, _sig(own), ())
    return {k: v for k, v in table.items() if v[0] and v[1]}


def _answer_clause(s: str) -> bool:
    for rx in _ANSWER_RXS:
        for m in rx.finditer(s):
            first = m.group("name").split()[0].strip("'’").lower()
            if first not in _NOT_A_NAME:
                return True
    return False


def clients_tools_for(text: Any, *, client_names: Any = (), in_client: Any = "") -> list[str]:
    """The client tools a chat message plausibly asks for (v1.499.0) — read
    ONCE by ``records.record_tools_for``. ``client_names``: the live
    confirmed and waiting names (:func:`live_names`, read off the loop by the
    stream lane); ``in_client``: the turn's own confirmed client's name ("" =
    the chat is in no client's folder). A request to list clients arms the
    reader; "<Name> is (not) a client" / "make <Name> a client" the answer;
    a folder, a new name or another name it goes by for a KNOWN client the
    update; a preference said about a known client (or, in a client's
    folder, about "they" / "the client") the preference tools. A QUESTION
    keeps only the reader. Linear; never raises."""
    t = str(text or "")[:SCAN_CHARS]
    try:
        if not t.strip():
            return []
        out: list[str] = []
        sentences = _sentences(t)
        if any(_LIST_RX.search(s) for s in sentences):
            out.append(CLIENT_READER)
        if R.is_question(t):
            return out
        table = _names_table(client_names, in_client)
        own = bool(str(getattr(in_client, "name", in_client) or "").strip())
        writers: list[str] = []
        for s in sentences:
            if _answer_clause(s):
                writers.append("client_answer")
            named = bool(_named(s, table)) if table else False
            about_own = own and _OWN_SUBJECT.search(s) is not None
            if not (named or about_own):
                continue
            if named and (
                (_PATH.search(s) and _FOLDER_VERB.search(s))
                or (_FOLDER_WORD.search(s) and _FOLDER_VERB.search(s))
                or _RENAME.search(s)
                or _ALIAS.search(s)
            ):
                writers.append("client_update")
            forget = _FORGET_PREF.search(s) is not None
            change = _CHANGE_PREF.search(s) is not None
            if forget:
                writers.append("client_preference_forget")
            elif change:
                writers.append("client_preference_change")
            elif _PREF_SHAPE.search(s):
                writers.append("client_preference_add")
        for name in writers:
            if name not in out:
                out.append(name)
        return out
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        log.debug("client arming failed", exc_info=True)
        return []


__all__ = [
    "ALL_CLIENT_TOOL_TIERS",
    "CLIENT_READER",
    "CLIENT_TOOL_TIERS",
    "CLIENT_WRITERS",
    "KIND",
    "KINDS",
    "OWNER_WORDS",
    "client_href",
    "client_tools",
    "clients_tools_for",
    "list_words",
    "live_names",
    "owner_words",
    "owner_words_scope",
    "possessive",
    "restore_kind",
    "scoped_owner_words",
    "snap_client",
]
