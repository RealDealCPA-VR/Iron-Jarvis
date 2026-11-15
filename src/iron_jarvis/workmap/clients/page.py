"""What the two client pages read (v1.499.0, builder A).

Plain, BLOCKING reads (the routes are plain ``def``). They WRITE NOTHING and
never mint a suggestion. Every claim names the record it came from: a
decision names the recap and the work it came from, a person the titles or
file names they were seen in, a folder how it became the client's. Nothing
here is told to a model, and nothing is read from disk.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, or_
from sqlmodel import select

from ...core.db import session_scope
from ...core.ids import utcnow
from .. import keys
from ..index import _iso, _row, covered, link_for, public
from ..models import (
    CLIENT_CONFIRMED,
    CLIENT_DECLINED,
    CLIENT_PROPOSED,
    WorkClient,
    WorkClientFolder,
    WorkEntry,
    WorkForgotten,
)
from . import WORK_FOLDER_WORDS, ClientNotFound, ClientsUnreadable, names, store

#: What the page shows, at most.
FOLDERS_SHOWN = 8
ENTRIES_SHOWN = 100
FILES_SHOWN = 100
DECISIONS_SHOWN = 40
PEOPLE_SHOWN = 20
NAMED_ELSEWHERE_SHOWN = 10
#: The client's work read for files, decisions and people, at most.
WORK_READ_MAX = 2000
#: Newest rows looked at for "Named in other work".
ELSEWHERE_LOOK = 3000
SEEN_IN_MAX = 3
EVIDENCE_LINES = 4

DECIDED = "Decided: "
OPEN = "Open: "

#: Words that start a title ("Prepare Smith Dental payroll"), never a name.
_COMMON = frozenset(
    """
    prepare prepared send sent call called email emailed update updated fix fixed check
    checked draft drafted write wrote add added open close closed review reviewed file
    filed print printed scan scanned sign signed pay paid bill billed set setup move moved
    copy copied merge merged split rename renamed delete deleted find found read summarize
    summary list total totals reconcile reconciled post posted import imported export
    exported convert converted dear hello thanks regards
    """.split()
)
_WORD_RX = re.compile(r"[A-Za-z][A-Za-z'’]*")
_JOIN_RX = re.compile(r"[ \t_\-]+")
_EMAIL_RX = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def _engine(platform: Any) -> Any:
    engine = getattr(platform, "engine", None)
    if engine is None:
        raise ClientsUnreadable()
    return engine


def _naive(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def ago(when: Any, now: datetime | None = None) -> str:
    """Plain words for how long ago: "within the hour", "5 hours ago",
    "yesterday", "3 days ago"."""
    then = _naive(when)
    if then is None:
        return "a while ago"
    current = _naive(now) if now is not None else utcnow().replace(tzinfo=None)
    secs = max(0.0, ((current or then) - then).total_seconds())
    if secs < 3600:
        return "within the hour"
    if secs < 86400:
        hours = int(secs // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = int(secs // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


def _pieces(n: int) -> str:
    return "1 piece of work" if int(n) == 1 else f"{int(n)} pieces of work"


def _folders_word(n: int) -> str:
    return "1 folder" if int(n) == 1 else f"{int(n)} folders"


def label(folder: Any) -> str:
    """A folder as ``parent › name`` (never a full path)."""
    parts = [p for p in re.split(r"[\\/]+", str(folder or "").strip()) if p and not p.endswith(":")]
    return " › ".join(parts[-2:]) if parts else ""


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def evidence_lines(row: Any, now: datetime | None = None) -> list[str]:
    """Why Iron Jarvis asked, by folder NAMES: "Clients › Hartleys: 6 pieces
    of work, last 3 days ago" (at most 4)."""
    out = []
    for h in store.homes_of(row)[:EVIDENCE_LINES]:
        line = f"{label(h.get('folder'))}: {_pieces(int(h.get('work') or 0))}"
        if h.get("last_at"):
            line += f", last {ago(h.get('last_at'), now)}"
        out.append(line)
    return out


def _about(row: Any) -> str:
    ev = store.evidence_of(row)
    folders = int(ev.get("folders") or len(store.homes_of(row)) or 0)
    return f"{_folders_word(folders)}, {_pieces(int(ev.get('work') or 0))}"


def _work_groups(db: Any) -> list[tuple[str, int, Any]]:
    W = WorkEntry
    return [(str(k), int(n or 0), last) for k, n, last in db.exec(
        select(W.folder_key, func.count(), func.max(W.updated_at)).group_by(W.folder_key)
    )]


def clients_list(platform: Any) -> dict[str, Any]:
    """``GET /clients``::

        {clients:[{id,name,how,folders:[{folder,name}],work_count,last_at,
                   preferences_count}],
         waiting:[{id,name,rule,about,evidence:[str],folders,work,pre_ticked,
                   created_at}],
         declined:[{id,name,decided_at,was_client}], total, noticing}

    ``pre_ticked`` is true only for a name found under a Clients-type folder.
    Evidence names folders as ``parent › name``, never a full path.
    ``noticing`` is ``config.notice_clients``. Raises ``ClientsUnreadable``
    (the route answers 503, never an empty list)."""
    engine = _engine(platform)
    config = getattr(platform, "config", None)
    try:
        with session_scope(engine) as db:
            rows = list(db.exec(select(WorkClient)))
            folder_rows = list(db.exec(select(WorkClientFolder)))
            groups = _work_groups(db)
        cmap = store.load_map(engine)
        counts = {r.id: len(store.preferences(engine, r.id)) for r in rows if r.status == CLIENT_CONFIRMED}
    except ClientsUnreadable:
        raise
    except Exception as exc:  # noqa: BLE001 — said, never an empty list
        raise ClientsUnreadable() from exc
    work: dict[str, list[Any]] = {}
    for key, n, last in groups:
        root = cmap.client_of(key)
        if root is None:
            continue
        got = work.setdefault(root.client_id, [0, None])
        got[0] += n
        if last is not None and (got[1] is None or _naive(last) > _naive(got[1])):
            got[1] = last
    by_client: dict[str, list[Any]] = {}
    for f in sorted(folder_rows, key=lambda f: (_iso(f.created_at) or "", f.folder_key)):
        by_client.setdefault(f.client_id, []).append(f)
    now = utcnow()
    clients, waiting, declined = [], [], []
    for r in rows:
        if r.status == CLIENT_CONFIRMED:
            n, last = work.get(r.id, [0, None])
            clients.append({
                "id": r.id, "name": r.name, "how": r.how,
                "folders": [{"folder": f.folder or f.folder_key, "name": keys.folder_name(f.folder or f.folder_key)}
                            for f in by_client.get(r.id, [])],
                "work_count": int(n), "last_at": _iso(last),
                "preferences_count": int(counts.get(r.id, 0)),
            })
        elif r.status == CLIENT_PROPOSED:
            ev = store.evidence_of(r)
            rule = str(ev.get("rule") or "")
            waiting.append({
                "id": r.id, "name": r.name, "rule": rule, "about": _about(r),
                "evidence": evidence_lines(r, now),
                "folders": int(ev.get("folders") or len(store.homes_of(r)) or 0),
                "work": int(ev.get("work") or 0),
                "pre_ticked": rule == "under_clients",
                "created_at": _iso(r.created_at),
                "_strength": int(r.strength or 0),
            })
        elif r.status == CLIENT_DECLINED:
            declined.append({"id": r.id, "name": r.name, "decided_at": _iso(r.decided_at),
                             "was_client": r.confirmed_at is not None})
    clients.sort(key=lambda c: (c["name"].casefold(), c["id"]))
    waiting.sort(key=lambda w: (-w["_strength"], w["created_at"] or "", w["id"]))
    for w in waiting:
        w.pop("_strength", None)
    declined.sort(key=lambda d: (d["decided_at"] or ""), reverse=True)
    return {
        "clients": clients,
        "waiting": waiting,
        "declined": declined,
        "total": len(clients),
        "noticing": bool(getattr(config, "notice_clients", True)),
    }


def _inside_any(column: Any, folder_keys: Iterable[str]) -> Any:
    conds = []
    for k in folder_keys:
        prefix = k if k.endswith("/") else k + "/"
        conds += [
            column == k,
            column.startswith(prefix, autoescape=True),
            # A coding session's own copies of a repository belong to it.
            column.startswith(k + keys.WORKTREES_SUFFIX + "/", autoescape=True),
        ]
    return or_(*conds)


def _in_roots(key: str, roots: Iterable[str]) -> bool:
    k = keys.folder_key(keys.home_folder(key))
    return any(keys.inside(k, r) for r in roots)


def _banned_words(client_name: str, aliases: Iterable[str]) -> set[str]:
    own = set()
    for text in (client_name, *aliases):
        for t in names.name_key(text).split() + names.signature(text).split():
            own.add(t)
    return own


def _plain(token: str) -> str:
    return token.lower().replace("'", "").replace("’", "")


def _skip_word(token: str, own: set[str]) -> bool:
    low = _plain(token)
    return (
        len(low) < 2
        or names.is_period(low)
        or low in WORK_FOLDER_WORDS
        or low in names.COMPANY_SUFFIXES
        or low in keys.STOP_WORDS
        or low in keys.GENERIC_FOLDER_WORDS
        or low in _COMMON
        or low in own
        or low.rstrip("s") in own
    )


#: A run of more capitalised words than this is a title written in Title
#: Case ("Prepare The Annual Board Minutes Pack"), never a name.
TITLE_CASE_RUN = 4


def people_in(text: str, own: set[str]) -> list[str]:
    """Runs of 2–3 consecutive capitalised words ("Mary Smith") that are not
    months, weekdays, work words, company suffixes, common verbs or the
    client's own name. A run of capitalised words longer than
    :data:`TITLE_CASE_RUN` is a title written in Title Case, not a name.
    Linear."""
    found: list[str] = []
    body = str(text or "")[:2000]
    runs: list[list[str]] = []
    run: list[str] = []
    last_end = -1
    for m in _WORD_RX.finditer(body):
        tok = m.group(0)
        gap = body[last_end:m.start()] if last_end >= 0 else ""
        if run and not _JOIN_RX.fullmatch(gap or " "):
            runs.append(run)
            run = []
        if tok[:1].isupper() and any(ch.islower() for ch in tok[1:]):
            run.append(tok)
        elif run:
            runs.append(run)
            run = []
        last_end = m.end()
    if run:
        runs.append(run)
    for capitals in runs:
        if len(capitals) > TITLE_CASE_RUN:
            continue
        part: list[str] = []
        for tok in [*capitals, ""]:
            if tok and not _skip_word(tok, own):
                part.append(tok)
                continue
            if 2 <= len(part) <= 3:
                found.append(" ".join(part))
            part = []
    return found


def _stem(path: str) -> str:
    name = keys.file_name(path)
    stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
    return " ".join(re.split(r"[_\-.\s]+", stem))


def _people(rows: list[dict[str, Any]], recaps: dict[str, dict[str, Any]], own: set[str]) -> list[dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}

    def seen(name: str, how: str, row: dict[str, Any]) -> None:
        key = name.casefold()
        p = people.setdefault(key, {"name": name, "how": how, "seen_in": [], "_n": 0, "_ids": set()})
        p["_n"] += 1
        if row["id"] not in p["_ids"] and len(p["seen_in"]) < SEEN_IN_MAX:
            p["_ids"].add(row["id"])
            p["seen_in"].append({"entry_id": row["id"], "title": row["title"],
                                 "link": link_for(row["kind"], row["ref"])})

    for row in rows:
        for who in people_in(row["title"], own):
            seen(who, "title", row)
        for f in row["files"]:
            for who in people_in(_stem(f["path"]), own):
                seen(who, "file", row)
        texts = [row["title"]]
        recap = recaps.get(row["id"])
        if recap:
            texts += list(recap.get("lines") or [])
        for text in texts:
            for mail in _EMAIL_RX.findall(str(text or "")):
                seen(mail, "email", row)
    ranked = sorted(people.values(), key=lambda p: (-p["_n"], p["name"].casefold()))
    return [{"name": p["name"], "how": p["how"], "seen_in": p["seen_in"]} for p in ranked[:PEOPLE_SHOWN]]


def _decisions(rows: list[dict[str, Any]], recaps: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    from .. import recap as _recap

    out = []
    for row in rows:
        recap = recaps.get(row["id"])
        if not recap:
            continue
        current = _recap.current_for(recap, row.get("updated"))
        for line in recap.get("lines") or []:
            text = str(line or "").strip()
            for lead, lab in ((DECIDED, "decided"), (OPEN, "open")):
                if text.startswith(lead) and text[len(lead):].strip():
                    out.append({
                        "text": text[len(lead):].strip(), "label": lab, "entry_id": row["id"],
                        "title": row["title"], "link": link_for(row["kind"], row["ref"]),
                        "written_at": _iso(recap.get("created_at")), "current": bool(current),
                    })
        if len(out) >= DECISIONS_SHOWN:
            break
    return out[:DECISIONS_SHOWN]


def _files(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: set[str] = set()
    out = []
    for row in rows:
        for f in row["files"]:
            k = keys.folder_key(f["path"])
            if not k or k in seen:
                continue
            seen.add(k)
            if len(out) < FILES_SHOWN:
                out.append({
                    "name": keys.file_name(f["path"]), "path": f["path"], "how": f["how"],
                    "folder_name": keys.folder_name(row["folder"]), "entry_id": row["id"],
                    "title": row["title"], "link": link_for(row["kind"], row["ref"]),
                    "at": _iso(row["updated"]),
                })
    return out, len(seen)


def _why(row: Any, now: datetime) -> list[str]:
    ev = store.evidence_of(row)
    homes = store.homes_of(row)
    if str(ev.get("rule") or "") == "added" or row.how == "added":
        if homes:
            return [f"You added it with the folder {label(homes[0].get('folder'))}."]
        return ["You added it."]
    if not homes:
        return []
    labels = [label(h.get("folder")) for h in homes[:EVIDENCE_LINES]]
    first = f"Seen in {_join(labels)}, {_pieces(int(ev.get('work') or 0))}."
    return [first, *evidence_lines(row, now)]


def _aliases(row: Any) -> list[str]:
    shown: list[str] = []
    for s in store.evidence_of(row).get("spellings") or []:
        d = names.display_name(str(s)) if isinstance(s, str) else ""
        if d and d != row.name and d not in shown:
            shown.append(d)
    covered_sigs = {names.signature(x) for x in [row.name, *shown]}
    for sig in store.aliases_of(row):
        if sig not in covered_sigs:
            shown.append(sig)
    return shown


def client_page(platform: Any, client_id: str) -> dict[str, Any]:
    """``GET /clients/{id}``::

        {client:{id,name,status,how,aliases,created_at,confirmed_at,why:[str]},
         folders:[{folder,name,how,count,last_at,forgotten}],
         entries:[public row], entries_total,
         files:[{name,path,how,folder_name,entry_id,title,link,at}], files_total,
         decisions:[{text,label:"decided"|"open",entry_id,title,link,written_at,
                     current}],
         people:[{name,how:"title"|"file"|"email",seen_in:[{entry_id,title,link}]}],
         preferences:[{id,text,origin,local_only,evidence,created_at}],
         named_elsewhere:[{entry_id,title,link,folder_name}],
         projects:[{id,name,root}], told: str}

    Work: the work in its folders and under them (≤ 8 folders, a coding
    session's own copies counted), newest first, 100 shown. Files: the
    union of that work's ``files_json`` (≤ 100). Decisions: the ``Decided:``
    / ``Open:`` lines of kept recaps of that work (≤ 40, a stale one
    marked). People: computed at read time from the owner's own titles and
    file stems plus e-mail addresses in checked recap lines and titles
    (≤ 20; never stored, never told). Named elsewhere: ≤ 10 titles outside
    its folders naming it. Projects: active Projects whose root is inside
    one of its folders. ``told``: ``block.client_block(platform,
    folder=<its first folder>, local=False)``. A forgotten folder shows no
    work. Raises ``ClientNotFound`` (404) and ``ClientsUnreadable`` (503)."""
    from ...core.models import Project
    from .. import recap as _recap
    from . import CLIENT_ID_RE, block

    engine = _engine(platform)
    cid = str(client_id or "")
    if not CLIENT_ID_RE.match(cid):
        raise ClientNotFound()
    now = utcnow()
    W = WorkEntry
    try:
        with session_scope(engine) as db:
            row = db.get(WorkClient, cid)
            if row is None:
                raise ClientNotFound()
            frows = list(db.exec(
                select(WorkClientFolder).where(WorkClientFolder.client_id == cid)
                .order_by(WorkClientFolder.created_at, WorkClientFolder.folder_key)
            ))
            gone = [str(k) for k in db.exec(select(WorkForgotten.folder_key)) if k]
            all_keys = [str(f.folder_key) for f in frows]
            live = [f for f in frows if not any(covered(str(f.folder_key), g) for g in gone)]
            live_keys = [str(f.folder_key) for f in live[:FOLDERS_SHOWN]]
            folders = []
            for f in frows:
                is_gone = str(f.folder_key) not in [str(x.folder_key) for x in live]
                n, last = 0, None
                if not is_gone:
                    n, last = db.exec(
                        select(func.count(), func.max(W.updated_at)).select_from(W)
                        .where(_inside_any(W.folder_key, [str(f.folder_key)]))
                    ).one()
                folders.append({
                    "folder": f.folder or f.folder_key, "name": keys.folder_name(f.folder or f.folder_key),
                    "how": f.how, "count": int(n or 0), "last_at": _iso(last), "forgotten": is_gone,
                })
            rows: list[dict[str, Any]] = []
            total = 0
            if live_keys:
                where = _inside_any(W.folder_key, live_keys)
                total = int(db.exec(select(func.count()).select_from(W).where(where)).one() or 0)
                rows = [_row(e) for e in db.exec(
                    select(W).where(where).order_by(W.updated_at.desc(), W.id).limit(WORK_READ_MAX)  # type: ignore[attr-defined]
                )]
            recaps = _recap.recaps_for(db, [r["id"] for r in rows]) if rows else {}
            entries = [public(r, recaps.get(r["id"])) for r in rows[:ENTRIES_SHOWN]]
            files, files_total = _files(rows)
            decisions = _decisions(rows, recaps)
            own = _banned_words(row.name, store.aliases_of(row))
            people = _people(rows, recaps, own)
            # Named in other work: titles OUTSIDE its folders that name it.
            table = {row.id: (row.name, row.signature, store.aliases_of(row))}
            elsewhere = []
            for e in db.exec(select(W).order_by(W.updated_at.desc(), W.id).limit(ELSEWHERE_LOOK)):  # type: ignore[attr-defined]
                if len(elsewhere) >= NAMED_ELSEWHERE_SHOWN:
                    break
                if all_keys and _in_roots(e.folder_key or e.folder, all_keys):
                    continue
                r = _row(e)
                stems = " ".join(_stem(f["path"]) for f in r["files"])
                if row.id in names.named_in(f"{r['title']} {stems}", table):
                    elsewhere.append({"entry_id": r["id"], "title": r["title"],
                                      "link": link_for(r["kind"], r["ref"]),
                                      "folder_name": keys.folder_name(r["folder"])})
            projects = []
            if all_keys:
                for p in db.exec(select(Project).where(or_(Project.status == "active", Project.status.is_(None)))):  # type: ignore[union-attr]
                    root = str(p.root or "").strip()
                    if root and _in_roots(root, all_keys):
                        projects.append({"id": p.id, "name": p.name, "root": root})
            client = {
                "id": row.id, "name": row.name, "status": row.status, "how": row.how,
                "aliases": _aliases(row), "created_at": _iso(row.created_at),
                "confirmed_at": _iso(row.confirmed_at), "why": _why(row, now),
            }
            first = live[0].folder if live else (frows[0].folder if frows else "")
        prefs = [
            {"id": p["id"], "text": p["text"], "origin": p["origin"], "local_only": p["local_only"],
             "evidence": p["evidence"], "created_at": p["created_at"]}
            for p in store.preferences(engine, cid)
        ]
    except (ClientNotFound, ClientsUnreadable):
        raise
    except Exception as exc:  # noqa: BLE001 — said, never an empty page
        raise ClientsUnreadable() from exc
    told = block.client_block(platform, folder=first, local=False) if row.status == CLIENT_CONFIRMED and first else ""
    return {
        "client": client,
        "folders": folders,
        "entries": entries,
        "entries_total": total,
        "files": files,
        "files_total": files_total,
        "decisions": decisions,
        "people": people,
        "preferences": prefs,
        "named_elsewhere": elsewhere,
        "projects": projects,
        "told": told,
    }


def client_of_folder(platform: Any, folder: str) -> dict[str, Any]:
    """``GET /clients/of?folder=``: ``{client: {id, name} | null}``. Raises
    ``ClientsUnreadable``."""
    root = store.load_map(_engine(platform)).client_of(folder)
    return {"client": {"id": root.client_id, "name": root.name} if root is not None else None}


__all__ = ["ago", "client_of_folder", "client_page", "clients_list", "evidence_lines", "label", "people_in"]
