"""Noticing names that look like clients (v1.499.0, builder A).

NO MODEL CALL, no disk read, no file content: a pure pass over the work
record's own folder paths (every segment, not only the last). The owner's
own folder names are the strongest signal and are already local; the
owner's Yes is the judgement.

Input (``notice``, in ``asyncio.to_thread`` after
``work_map(...).refresh(budget=60)``; two bounded reads):

* Read 1, the folders: ``SELECT folder_key, MIN(folder), COUNT(*),
  MAX(updated_at), SUM(local_only OR keep_home), SUM(kind IN ('code',
  'tool_code')) FROM workentry WHERE archived = 0 GROUP BY folder_key LIMIT
  5000``. Each row is re-keyed with ``keys.home_folder`` (a worktree copy
  counts as its repository); ``index.covered`` is checked again.
* Read 2, the file names: ``files_json`` of the newest 3000 non-archived
  rows, for file-extension counts per folder.

Candidacy, per folder row: with a Clients-type segment, the ONLY candidate is
the first name segment after the LAST Clients-type segment (period segments
skipped), ``under_clients``; deeper segments are that client's subfolders.
Otherwise every name segment is a candidate. A candidate's home is the path
prefix through its segment, as spelled. Grouped by ``names.signature``:
homes (outermost kept), work folders, work, last_at, under_clients,
capitalised, all_code, code_share (``py ts tsx js jsx go rs java cs cpp c h
rb php toml lock yml yaml sh ps1``), local per home.

Qualifies: ``(under_clients AND work >= 2) OR (capitalised AND work_folders >=
2 AND work >= 3)``. Refused, in this order: a. code (not under_clients AND
(all_code OR (>= 5 named files AND code_share >= 0.6))); b. a shared subfolder
word (homes directly under >= 2 different parents that are other candidates'
name segments; period parents do not count); c. a container (its homes hold
>= 2 other qualifying candidates' homes); d. everywhere (>= 5 folders and its
work folders > 60% of them); e. nested (every home inside another surviving
candidate's home; an outer name the owner already answered never counts, so
declining "Accounting" does not silence the client inside it); f. taken (a
home inside, or holding, a confirmed client's
folder); g. known (a row in any status has this signature or alias; a
proposed row is refreshed instead); h. forgotten (every home covered by
``workforgotten``); i. off (``config.notice_clients`` False).

Strength = ``(under_clients ? 5 : 0) + 2·len(homes) + min(work, 10)``, then
``last_at``, newest first.

A pass MINTS proposed rows (at most ``MAX_WAITING`` waiting at once),
REFRESHES the evidence of rows still proposed, and WITHDRAWS (deletes)
proposed rows that no longer qualify. It withdraws only a row it can vouch
it made itself and that was never answered (a noticed row whose evidence
names a noticing rule and with no ``decided_at``): a row put back by "Ask
again" or written by hand is never deleted. GET routes and the inbox never
run a pass.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping

from sqlalchemy import case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ...core.db import session_scope
from ...core.ids import new_id, utcnow
from .. import keys
from ..index import covered, work_map
from ..models import (
    CLIENT_CONFIRMED,
    CLIENT_DECLINED,
    CLIENT_NOTICED,
    CLIENT_PROPOSED,
    KIND_CODE,
    KIND_TOOL_CODE,
    WorkClient,
    WorkClientFolder,
    WorkEntry,
    WorkForgotten,
)
from . import MAX_WAITING, ClientsUnreadable, names, store

log = logging.getLogger(__name__)

#: Noticing passes are throttled to one per this many seconds unless forced.
NOTICE_THROTTLE_S = 60.0
#: The background pass: first 120 s after start, then every 30 minutes.
FIRST_PASS_S = 120.0
EVERY_S = 30 * 60.0
#: The two bounded reads.
FOLDER_ROWS_MAX = 5000
FILE_ROWS_MAX = 3000
#: Homes kept in one name's evidence.
HOMES_MAX = 8
#: The rules a noticed row's evidence names (anything else was not minted
#: by a pass, so a pass never withdraws it).
RULE_UNDER_CLIENTS = "under_clients"
RULE_SEVERAL_FOLDERS = "several_folders"
NOTICED_RULES = (RULE_UNDER_CLIENTS, RULE_SEVERAL_FOLDERS)
#: The work record's refresh budget before a pass reads it.
REFRESH_BUDGET = 60

_LAST_ATTR = "_ironjarvis_clients_noticed_at"
_LAST: dict[int, float] = {}


@dataclass(frozen=True)
class CandidateHome:
    """One home of a candidate name: an ``evidence_json`` ``homes`` entry."""

    #: The path prefix through the name's segment, as spelled.
    folder: str
    #: Its folder key.
    key: str
    #: Pieces of work in it (and under it).
    work: int = 0
    last_at: datetime | None = None
    #: Every piece of work in it is Local only or kept home.
    local: bool = False


@dataclass(frozen=True)
class Candidate:
    """A name that qualifies and survived every refusal."""

    #: ``names.signature`` of the name: the row's UNIQUE key.
    signature: str
    #: ``names.display_name`` of its spellings.
    name: str
    #: ``"under_clients"`` | ``"several_folders"``.
    rule: str
    #: At most 8, strongest first.
    homes: tuple[CandidateHome, ...] = ()
    #: Pieces of work in its homes.
    work: int = 0
    #: Distinct work folders inside its homes.
    folders: int = 0
    last_at: datetime | None = None
    under_clients: bool = False
    #: The rank score.
    strength: int = 0
    #: Every spelling seen (the other spellings become aliases at the Yes).
    spellings: tuple[str, ...] = ()


def _naive(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


def _strict_inside(key: str, base: str) -> bool:
    return key != base and keys.inside(key, base)


def _parent(key: str) -> str:
    return key.rsplit("/", 1)[0] if "/" in key.strip("/") else ""


def _extension(path: Any) -> str:
    name = keys.file_name(path)
    return name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""


def candidates(
    rows: Iterable[tuple[str, str, int, "datetime | None", int, int]],
    files: Iterable[tuple[str, str]],
    *,
    known: Mapping[str, str],
    confirmed_keys: Iterable[str],
    forgotten: Iterable[str],
) -> list[Candidate]:
    """PURE: the names that qualify and survive refusals a–h, strongest
    first.

    ``rows``: Read 1, ``(folder_key, folder, work, last_at, local_count,
    code_count)``. ``files``: Read 2, ``(folder_key, files_json)``.
    ``known``: every signature and alias on record → its row's status (a
    confirmed or declined one refuses the name; a proposed one is returned so
    :func:`notice` refreshes that row). ``confirmed_keys``: the folder keys a
    confirmed client claims. ``forgotten``: the ``workforgotten`` keys."""
    gone = [str(k) for k in forgotten if k]
    taken = [str(k) for k in confirmed_keys if k]

    # The folders, re-keyed to their home folder (a worktree copy is its
    # repository), forgotten ones left out.
    folders: dict[str, dict[str, Any]] = {}
    rekey: dict[str, str] = {}
    for fk, shown, work, last_at, local_count, code_count in rows:
        home = keys.home_folder(str(shown or "").strip())
        key = keys.folder_key(home)
        if not key or any(covered(key, f) for f in gone):
            continue
        rekey[str(fk or "")] = key
        d = folders.setdefault(key, {"folder": home, "work": 0, "last_at": None, "local": 0, "code": 0})
        d["work"] += int(work or 0)
        d["local"] += int(local_count or 0)
        d["code"] += int(code_count or 0)
        d["last_at"] = _later(d["last_at"], _naive(last_at))

    named_files: Counter[str] = Counter()
    code_files: Counter[str] = Counter()
    for fk, files_json in files:
        key = rekey.get(str(fk or "")) or keys.folder_key(keys.home_folder(str(fk or "")))
        if key not in folders:
            continue
        try:
            data = json.loads(files_json or "[]")
        except (TypeError, ValueError):
            continue
        for f in data if isinstance(data, list) else []:
            ext = _extension(f.get("path") if isinstance(f, dict) else "")
            if not ext:
                continue
            named_files[key] += 1
            if ext in names.CODE_EXTENSIONS:
                code_files[key] += 1

    # Every candidate occurrence, grouped by signature.
    groups: dict[str, dict[str, Any]] = {}
    for key, d in folders.items():
        child = names.client_child(d["folder"])
        if child is not None:
            occurrences = [(child[0], child[1], True)]
        elif any(names.is_client_parent(s) for s in names.path_parts(d["folder"])):
            occurrences = []  # C:\Clients itself, or a non-name under it
        else:
            occurrences = [(seg, prefix, False) for seg, prefix in names.segments(d["folder"])
                           if names.is_name_segment(seg)]
        for seg, prefix, under in occurrences:
            sig = names.signature(seg)
            if not sig:
                continue
            g = groups.setdefault(sig, {"spellings": [], "homes": {}, "rows": set(), "under": False})
            g["homes"].setdefault(keys.folder_key(prefix), prefix)
            g["rows"].add(key)
            g["spellings"].append(seg)
            g["under"] = g["under"] or under

    # Measure each group.
    for g in groups.values():
        hks = list(g["homes"])
        outer = [h for h in hks if not any(_strict_inside(h, o) for o in hks)]
        g["home_keys"] = outer
        g["work"] = sum(folders[r]["work"] for r in g["rows"])
        g["folders"] = len(g["rows"])
        g["last_at"] = None
        for r in g["rows"]:
            g["last_at"] = _later(g["last_at"], folders[r]["last_at"])
        g["cap"] = any(s[:1].isupper() for s in g["spellings"])
        code = sum(folders[r]["code"] for r in g["rows"])
        g["all_code"] = g["work"] > 0 and code >= g["work"]
        nfiles = sum(named_files[r] for r in g["rows"])
        ncode = sum(code_files[r] for r in g["rows"])
        g["code_share_hit"] = nfiles >= 5 and (ncode / nfiles) >= 0.6

    def qualifies(g: dict[str, Any]) -> bool:
        return (g["under"] and g["work"] >= 2) or (g["cap"] and g["folders"] >= 2 and g["work"] >= 3)

    alive = {sig: g for sig, g in groups.items() if qualifies(g)}
    # a. code
    alive = {s: g for s, g in alive.items() if g["under"] or not (g["all_code"] or g["code_share_hit"])}
    # b. a shared subfolder word ("Board Minutes" under Hartleys AND Smith)
    owner_of_home: dict[str, str] = {}
    for sig, g in groups.items():
        for hk in g["homes"]:
            owner_of_home.setdefault(hk, sig)
    shared = set()
    for sig, g in alive.items():
        parents = {_parent(hk) for hk in g["home_keys"]}
        hits = {p for p in parents if p and owner_of_home.get(p) not in (None, sig)}
        if len(hits) >= 2:
            shared.add(sig)
    alive = {s: g for s, g in alive.items() if s not in shared}
    # c. a container (C:\Work holding Hartleys and Smith)
    containers = set()
    for sig, g in alive.items():
        inner = {
            other for other, og in alive.items() if other != sig
            and any(_strict_inside(ohk, hk) for ohk in og["home_keys"] for hk in g["home_keys"])
        }
        if len(inner) >= 2:
            containers.add(sig)
    alive = {s: g for s, g in alive.items() if s not in containers}
    # d. everywhere
    total = len(folders)
    if total >= 5:
        alive = {s: g for s, g in alive.items() if g["folders"] <= 0.6 * total}
    # e. nested: the outer one is the client. An outer name the owner already
    # answered is not a candidate any more, so it never hides the one inside
    # ("Accounting" declined must not silence Hartleys under it).
    answered = {s for s in alive if known.get(s) in (CLIENT_CONFIRMED, CLIENT_DECLINED)}
    outer = {s: g for s, g in alive.items() if s not in answered}
    nested = set()
    for sig, g in alive.items():
        if g["home_keys"] and all(
            any(_strict_inside(hk, ohk) for other, og in outer.items() if other != sig for ohk in og["home_keys"])
            for hk in g["home_keys"]
        ):
            nested.add(sig)
    alive = {s: g for s, g in alive.items() if s not in nested}

    out: list[Candidate] = []
    for sig, g in alive.items():
        # f. taken: a home inside, or holding, a confirmed client's folder
        if any(keys.inside(hk, t) or keys.inside(t, hk) for hk in g["home_keys"] for t in taken):
            continue
        # g. known: a Yes or a No is final for every spelling
        if known.get(sig) in (CLIENT_CONFIRMED, CLIENT_DECLINED):
            continue
        # h. forgotten
        if gone and all(any(covered(hk, f) for f in gone) for hk in g["home_keys"]):
            continue
        name = names.display_name(g["spellings"])
        if not name:
            continue
        homes = []
        for hk in g["home_keys"]:
            inside = [r for r in g["rows"] if keys.inside(r, hk)]
            work = sum(folders[r]["work"] for r in inside)
            local = work > 0 and sum(folders[r]["local"] for r in inside) >= work
            last = None
            for r in inside:
                last = _later(last, folders[r]["last_at"])
            homes.append(CandidateHome(folder=g["homes"][hk], key=hk, work=work, last_at=last, local=local))
        homes.sort(key=lambda h: (h.work, h.last_at or datetime.min), reverse=True)
        under = bool(g["under"] and g["work"] >= 2)
        out.append(Candidate(
            signature=sig,
            name=name,
            rule=RULE_UNDER_CLIENTS if under else RULE_SEVERAL_FOLDERS,
            homes=tuple(homes[:HOMES_MAX]),
            work=int(g["work"]),
            folders=int(g["folders"]),
            last_at=g["last_at"],
            under_clients=bool(g["under"]),
            strength=(5 if g["under"] else 0) + 2 * len(g["home_keys"]) + min(int(g["work"]), 10),
            spellings=tuple(dict.fromkeys(g["spellings"])),
        ))
    out.sort(key=lambda c: (c.strength, c.last_at or datetime.min, c.signature), reverse=True)
    return out


def evidence_of(c: Candidate) -> dict[str, Any]:
    """The ``evidence_json`` of a noticed row (``spellings`` is extra: the
    page's "Also written")."""
    return {
        "rule": c.rule,
        "homes": [
            {
                "folder": h.folder, "key": h.key, "work": h.work,
                "last_at": h.last_at.replace(tzinfo=timezone.utc).isoformat() if h.last_at else None,
                "local": h.local,
            }
            for h in c.homes
        ],
        "work": c.work,
        "folders": c.folders,
        "spellings": list(c.spellings[:5]),
    }


def _read(engine: Any) -> dict[str, Any]:
    W = WorkEntry
    local = func.sum(case((or_(W.local_only == True, W.keep_home == True), 1), else_=0))  # noqa: E712
    code = func.sum(case((W.kind.in_([KIND_CODE, KIND_TOOL_CODE]), 1), else_=0))  # type: ignore[attr-defined]
    last = func.max(W.updated_at)
    with session_scope(engine) as db:
        rows = list(db.exec(
            select(W.folder_key, func.min(W.folder), func.count(), last, local, code)
            .where(W.archived == False)  # noqa: E712
            .group_by(W.folder_key)
            .order_by(last.desc(), W.folder_key)
            .limit(FOLDER_ROWS_MAX)
        ))
        files = list(db.exec(
            select(W.folder_key, W.files_json)
            .where(W.archived == False)  # noqa: E712
            .order_by(W.updated_at.desc(), W.id)  # type: ignore[attr-defined]
            .limit(FILE_ROWS_MAX)
        ))
        clients = list(db.exec(select(WorkClient)))
        folder_rows = list(db.exec(select(WorkClientFolder.folder_key, WorkClientFolder.client_id)))
        forgotten = [str(k) for k in db.exec(select(WorkForgotten.folder_key)) if k]
    rank = {CLIENT_CONFIRMED: 3, CLIENT_DECLINED: 2, CLIENT_PROPOSED: 1}
    known: dict[str, str] = {}
    for c in clients:
        for sig in (c.signature, *store.aliases_of(c)):
            if sig and rank.get(c.status, 0) > rank.get(known.get(sig, ""), 0):
                known[sig] = c.status
    confirmed = {c.id for c in clients if c.status == CLIENT_CONFIRMED}
    return {
        "rows": rows,
        "files": files,
        "known": known,
        "confirmed_keys": [str(k) for k, cid in folder_rows if cid in confirmed],
        "forgotten": forgotten,
    }


def _rule_of(row: WorkClient) -> str:
    try:
        ev = json.loads(row.evidence_json or "{}")
    except (TypeError, ValueError):
        return ""
    return str(ev.get("rule") or "") if isinstance(ev, dict) else ""


def _safe_name(name: str) -> bool:
    from ...core.promptguard import scan_context

    return bool(name) and not scan_context(name, source="client name", cap=None).blocked


def _write(engine: Any, cands: list[Candidate]) -> tuple[list[str], bool]:
    """Refresh, withdraw, then mint. ``(minted ids, anything changed)``."""
    now = utcnow()
    changed = False
    with session_scope(engine) as db:
        proposed = list(db.exec(select(WorkClient).where(WorkClient.status == CLIENT_PROPOSED)))
        by_sig: dict[str, WorkClient] = {}
        for row in proposed:
            for sig in (row.signature, *store.aliases_of(row)):
                if sig:
                    by_sig.setdefault(sig, row)
        kept: set[str] = set()
        for c in cands:
            row = by_sig.get(c.signature)
            if row is None or row.id in kept:
                continue
            kept.add(row.id)
            evidence = json.dumps(evidence_of(c), ensure_ascii=True)
            if row.evidence_json != evidence or row.strength != c.strength:
                row.evidence_json = evidence
                row.strength = c.strength
                row.updated_at = now
                db.add(row)
                changed = True
        withdrawn = 0
        for row in proposed:
            if row.id in kept or row.decided_at is not None or row.how != CLIENT_NOTICED:
                continue
            if _rule_of(row) not in NOTICED_RULES:
                continue  # not minted by a pass: never withdrawn by one
            db.delete(row)
            withdrawn += 1
            changed = True
        db.commit()
        waiting = len(proposed) - withdrawn
    minted: list[str] = []
    for c in cands:
        if waiting >= MAX_WAITING:
            break
        if c.signature in by_sig or not _safe_name(c.name):
            continue
        rid = new_id("client")
        try:
            with session_scope(engine) as db:
                db.add(WorkClient(
                    id=rid, name=c.name[:80], name_key=names.name_key(c.name),
                    signature=c.signature, status=CLIENT_PROPOSED, how=CLIENT_NOTICED,
                    evidence_json=json.dumps(evidence_of(c), ensure_ascii=True), strength=c.strength,
                ))
                db.commit()
        except IntegrityError:
            continue  # another process minted this name a moment ago
        minted.append(rid)
        waiting += 1
        changed = True
    return minted, changed


def _last_pass(engine: Any) -> float | None:
    got = getattr(engine, _LAST_ATTR, None)
    return got if isinstance(got, float) else _LAST.get(id(engine))


def _note_pass(engine: Any, when: float) -> None:
    try:
        setattr(engine, _LAST_ATTR, when)
    except Exception:  # noqa: BLE001 — a slotted / mock engine
        _LAST[id(engine)] = when


def enabled(platform: Any) -> bool:
    return bool(getattr(getattr(platform, "config", None), "notice_clients", True))


def notice(platform: Any, *, force: bool = False) -> int:
    """One noticing pass (BLOCKING: call it through ``asyncio.to_thread``).

    Under a module lock plus the UNIQUE signature index: mints up to
    ``MAX_WAITING`` proposed rows, strongest first, and publishes
    ``client.suggested {ids}``; refreshes evidence and strength on rows still
    proposed; withdraws (deletes) proposed rows that no longer qualify (never
    answered, so nothing is lost). Throttled to one pass per
    :data:`NOTICE_THROTTLE_S` unless ``force``. Off when
    ``config.notice_clients`` is False. Returns how many rows it minted.
    Raises ``ClientsUnreadable`` when the record cannot be read (``POST
    /clients/notice`` answers 503; the loop and the after-answer pass
    swallow it)."""
    if not enabled(platform):
        return 0
    engine = getattr(platform, "engine", None)
    if engine is None:
        raise ClientsUnreadable()
    with store.NOTICE_LOCK:
        now = time.monotonic()
        last = _last_pass(engine)
        if not force and last is not None and now - last < NOTICE_THROTTLE_S:
            return 0
        _note_pass(engine, now)
    # The record is brought up to date OUTSIDE the lock (it has its own), so
    # the question under a reply never waits for a refresh.
    wm = work_map(engine, getattr(getattr(platform, "config", None), "home", None))
    if wm is not None:
        wm.refresh(budget=REFRESH_BUDGET)  # never raises
    with store.NOTICE_LOCK:
        try:
            got = _read(engine)
            cands = candidates(got["rows"], got["files"], known=got["known"],
                               confirmed_keys=got["confirmed_keys"], forgotten=got["forgotten"])
            minted, changed = _write(engine, cands)
        except ClientsUnreadable:
            raise
        except Exception as exc:  # noqa: BLE001 — said as unreadable, never as "none"
            log.debug("clients: a noticing pass could not read or write", exc_info=True)
            raise ClientsUnreadable() from exc
        if changed:
            store.bump_version()
    if minted:
        from ...core.events import EventType

        store.publish(platform, EventType.CLIENT_SUGGESTED, {"ids": list(minted)})
    return len(minted)


async def clients_loop(
    platform: Any,
    *,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[Any]],
    on_tick: "Callable[[bool, BaseException | None], Any] | None" = None,
    on_off: "Callable[[], Any] | None" = None,
) -> None:
    """``bg_tasks['work_clients']``: the first pass :data:`FIRST_PASS_S`
    after start, then every :data:`EVERY_S`, each through
    ``asyncio.to_thread(notice, platform)``. Time comes only from ``clock``
    and ``sleep`` (pinned without a real sleep). Never raises (a cancel ends
    it). Each pass reports ITS OWN outcome through ``on_tick(ok, exc)``;
    switched off calls ``on_off`` (off is neither failing nor healthy)."""
    try:
        store.remember_loop(asyncio.get_running_loop())
    except Exception:  # noqa: BLE001 — events then publish from the thread
        pass
    due = clock() + FIRST_PASS_S
    while True:
        try:
            await sleep(max(0.0, due - clock()))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a broken sleep must not spin
            log.warning("clients: the noticing loop's sleep failed; it stops", exc_info=True)
            return
        if clock() < due:
            continue  # woke early (Windows timers): wait out the rest
        due = clock() + EVERY_S
        try:
            if not enabled(platform):
                if on_off is not None:
                    on_off()
                continue
            await asyncio.to_thread(notice, platform)
            if on_tick is not None:
                on_tick(True, None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed pass is a row, not a crash
            log.debug("clients: a noticing pass failed", exc_info=True)
            try:
                if on_tick is not None:
                    on_tick(False, exc)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "EVERY_S",
    "FIRST_PASS_S",
    "NOTICED_RULES",
    "NOTICE_THROTTLE_S",
    "Candidate",
    "CandidateHome",
    "candidates",
    "clients_loop",
    "evidence_of",
    "notice",
]
