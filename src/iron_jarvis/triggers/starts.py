"""The start hub: the ONE place every kind of start goes through (leap 05-a).

Idea 05 ("say 'when this happens, do that' once"). Every signal that can start
a rule — a webhook, an email, a calendar event, a phone message, a Slack
message, a new file in a watched folder, a finished download — reaches the
rules through :meth:`StartHub.signal`. The hub calls the reflex router
EXACTLY as each caller did before it existed (same method, same arguments),
then any waiters (later phases: a goal that wakes on a signal, 08-c; a
"wait until", 09-c). A waiter reads a signal; it never starts or stops one.

Callers reach the hub through :func:`hub_for` (the router owns its hub, so a
test that swaps the router swaps the hub with it).

Also here, composed by code (never by a model): :func:`describe`, the four
lines of a rule's card (When, Do, Where it works, Limits), and the two
starts a folder or a download makes:

* a FILE rule's folder is watched by the existing watcher service, never a
  second scanner (``sentinels/service.SentinelService``, a ``rule``-mode
  watcher); :func:`file_rule_fire` is the callback it hands settled files to,
  which calls the hub on the daemon's loop;
* a DOWNLOAD rule hears ``browser.download_completed`` through
  :func:`download_handler`, with only the verified local path, the file's
  name and the site it came from.

What a signal says is data, never instructions: the router fences the
outside words (``reflex/router._FENCED_KEYS``) and outside sources start
their work at low trust (``reflex/router.OUTSIDE_SOURCES``).

Leap 05-b: another task that ended short starts a ``run_ended`` rule (ONE
signal per run: :func:`run_ended_handler` for a workflow's or an agent's run,
:func:`schedule_ended_signal` for a schedule's fire; the loop guards are the
router's ``on_run_ended``), and :func:`list_starts` is the one list of every
way work starts by itself (``GET /starts``, chat's ``start_list``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..core.logging import get_logger

log = get_logger("triggers.starts")

#: Every kind of start (the plan's list). ``phone`` is stored on a rule as
#: ``comm`` (its wire value, :data:`WIRE_SOURCE`). ``schedule`` starts come
#: from the scheduler's clock (the hub carries them for waiters and the one
#: list of starts); ``run_ended`` from another task that ended short (05-b:
#: :func:`run_ended_handler` and :func:`schedule_ended_signal`).
START_KINDS: tuple[str, ...] = (
    "schedule", "webhook", "email", "calendar", "phone", "slack", "file", "download", "run_ended",
)

#: A start kind's value on a rule's ``source`` column, where they differ.
WIRE_SOURCE: dict[str, str] = {"phone": "comm"}

#: How long the watcher's thread waits for the hub to start a file rule.
FILE_FIRE_TIMEOUT_S = 120.0
#: A start that outlasted that wait is remembered (so its file is never
#: started twice) until the scan after it ends; one whose file was never
#: handed again is forgotten this long after it ended.
UNDER_WAY_KEEP_S = 24 * 3600.0

Waiter = Callable[[str, dict], "Awaitable[None] | None"]


def wire_source(kind: str) -> str:
    """The rule ``source`` a start kind is stored under."""
    return WIRE_SOURCE.get(kind, kind)


def kind_of(source: str) -> str:
    """The start kind a rule ``source`` is (``comm`` is ``phone``)."""
    for kind, wire in WIRE_SOURCE.items():
        if wire == source:
            return kind
    return source


class StartHub:
    """One entry for every kind of start. Cheap; owned by its router."""

    def __init__(self, router: Any) -> None:
        self.router = router
        self._waiters: list[tuple[str, Waiter]] = []

    def add_waiter(self, kind: str, fn: Waiter) -> Callable[[], None]:
        """Call ``fn(kind, context)`` after every signal of ``kind`` ("*" =
        every kind). Returns the function that removes it."""
        entry = (str(kind or "*"), fn)
        self._waiters.append(entry)

        def _remove() -> None:
            try:
                self._waiters.remove(entry)
            except ValueError:
                pass

        return _remove

    async def signal(
        self, kind: str, context: dict | None = None, *, fields: dict | None = None
    ) -> list[dict[str, Any]]:
        """Start whatever ``kind`` starts; returns what the router answered
        (one result per rule it fired, as before).

        Two shapes, so every caller reaches the router EXACTLY as it did:
        ``fields`` are a kind's own fields for the router's own entry point
        (``on_webhook(slug, body)``, ``on_calendar(title=, start=,
        description=)``, ``on_slack(text=, channel=, sender=)``,
        ``on_email(sender=, subject=, body=)``, ``on_comm(text)``);
        ``context`` is an already-built signal context, read by
        ``on_signal(source, context)`` (a channel's message), ``on_file`` or
        ``on_download``. A router failure propagates to the caller, as it did
        (each caller keeps its own guard)."""
        kind = kind_of(str(kind or ""))
        fired = await self._route(kind, context, fields)
        if self._waiters:
            await self._tell_waiters(kind, self._context_for(kind, context, fields))
        return fired

    async def _route(self, kind: str, context: dict | None, fields: dict | None) -> list[dict[str, Any]]:
        r = self.router
        if r is None:
            return []
        f = dict(fields or {})
        if kind == "webhook":
            src = f or dict(context or {})
            return await r.on_webhook(str(src.get("slug") or ""), src.get("body"))
        if kind == "file":
            return await r.on_file(dict(context or {}))
        if kind == "download":
            return await r.on_download(dict(context or {}))
        if fields is not None:
            if kind == "calendar":
                return await r.on_calendar(
                    title=f.get("title", ""), start=f.get("start", ""), description=f.get("description", ""),
                )
            if kind == "slack":
                return await r.on_slack(text=f.get("text", ""), channel=f.get("channel", ""), sender=f.get("sender", ""))
            if kind == "email":
                return await r.on_email(sender=f.get("sender", ""), subject=f.get("subject", ""), body=f.get("body", ""))
            if kind == "phone":
                return await r.on_comm(str(f.get("text") or ""))
        if kind == "run_ended":
            # Leap 05-b: another task ended short (``run_ended_context``).
            return await r.on_run_ended(dict(context or {}))
        if kind == "schedule":
            return []  # a schedule starts at a time, through the scheduler: no rule hears it
        return await r.on_signal(wire_source(kind), dict(context or {}))

    @staticmethod
    def _context_for(kind: str, context: dict | None, fields: dict | None) -> dict:
        """The signal's context as the router reads it (for waiters)."""
        if fields is None:
            return dict(context or {})
        from ..reflex import router as _r

        f = dict(fields)
        try:
            if kind == "calendar":
                return _r.calendar_context(title=f.get("title", ""), start=f.get("start", ""),
                                           description=f.get("description", ""))
            if kind == "slack":
                return _r.slack_context(text=f.get("text", ""), channel=f.get("channel", ""),
                                        sender=f.get("sender", ""))
            if kind == "email":
                return _r.email_context(sender=f.get("sender", ""), subject=f.get("subject", ""),
                                        body=f.get("body", ""))
            if kind == "phone":
                return _r.comm_context(str(f.get("text") or ""))
        except Exception:  # noqa: BLE001
            pass
        return f

    async def _tell_waiters(self, kind: str, context: dict) -> None:
        for want, fn in list(self._waiters):
            if want not in ("*", kind):
                continue
            try:
                out = fn(kind, dict(context))
                if asyncio.iscoroutine(out):
                    await out
            except Exception:  # noqa: BLE001 — a waiter never breaks a start
                log.warning("a waiter on %s signals failed", kind, exc_info=True)


def hub_for(router: Any) -> "StartHub | None":
    """The router's hub (made and kept on it the first time). None for no
    router."""
    if router is None:
        return None
    hub = getattr(router, "start_hub", None)
    if isinstance(hub, StartHub) and hub.router is router:
        return hub
    hub = StartHub(router)
    try:
        router.start_hub = hub
    except Exception:  # noqa: BLE001 — a router that keeps nothing: a fresh hub each time
        pass
    return hub


# --- a file rule: its watcher ------------------------------------------------


def watcher_name_for(rule: Any) -> str:
    """The name of a file rule's own watcher ("Rule: Sort intake")."""
    name = " ".join(str(getattr(rule, "name", "") or "rule").split())[:80]
    return f"Rule: {name}"


def take_baseline(platform: Any, watcher: Any) -> None:
    """Review (05-a): a file rule's watcher records what is in its folder
    NOW — the moment the rule is made, its folder changes or it comes back
    on Undo — so the files already there are left alone and a file dropped
    right after (before the watch loop's first scan) starts the rule. Never
    fires; never raises. BLOCKING (it lists the folder)."""
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None or watcher is None or getattr(watcher, "last_checked_at", None) is not None:
        return
    try:
        sentinels.check_rule(watcher.name)
    except Exception:  # noqa: BLE001 — the loop's first scan takes it instead
        log.warning("the folder of rule watcher %s could not be read yet", getattr(watcher, "name", ""),
                    exc_info=True)


def ensure_file_watcher(platform: Any, rule: Any) -> Any:
    """Make (or keep) the ``rule``-mode watcher of a FILE rule: the folder
    and pattern of ``start_json``. Adding a watcher never fires; it records
    what is in the folder now (:func:`take_baseline`: files already there
    are left alone, a file dropped after it starts the rule). Returns the
    watcher, or None for a rule that is not a file rule. Raises
    ``ValueError`` in words for a folder the watcher service refuses (a
    network location, a protected path). BLOCKING."""
    if str(getattr(rule, "source", "") or "") != "file":
        return None
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None:
        raise ValueError("Folder watching is not available in this Iron Jarvis.")
    existing = watcher_for_rule(platform, rule.id)
    if existing is not None:
        take_baseline(platform, existing)
        return existing
    start = rule.decoded_start()
    path = str(start.get("path") or "").strip()
    if not path:
        raise ValueError("A file rule needs the folder to watch.")
    name = watcher_name_for(rule)
    if sentinels.get(name) is not None:
        name = f"{name} ({rule.id[-6:]})"
    made = sentinels.add(
        name,
        path=path,
        glob=str(start.get("glob") or "").strip() or None,
        task="",
        mode="rule",
        rule_id=rule.id,
    )
    take_baseline(platform, made)
    return sentinels.get(made.name) or made


def watcher_for_rule(platform: Any, rule_id: str) -> Any:
    """The rule-mode watcher that starts ``rule_id``, or None. BLOCKING."""
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None or not rule_id:
        return None
    for rec in sentinels.list():
        if getattr(rec, "mode", "") == "rule" and getattr(rec, "rule_id", "") == rule_id:
            return rec
    return None


def rule_owns_words(platform: Any, watcher: Any) -> str:
    """Review (05-a): "" for a watcher the user manages on Watchers; for the
    watcher a file rule keeps of its folder (while that rule exists), the
    refusal in words — it is changed and removed WITH its rule, never on its
    own (a lone change would leave the rule watching nothing, and its Undo
    would bring it back as a plain suggestion watcher). BLOCKING."""
    if watcher is None or getattr(watcher, "mode", "") != "rule":
        return ""
    rid = str(getattr(watcher, "rule_id", "") or "")
    try:
        reflex = getattr(platform, "reflex", None)
        rule = reflex.get(rid) if reflex is not None and rid else None
    except Exception:  # noqa: BLE001 — unreadable: refuse, the rule may be there
        rule = None
        return (f"The watcher “{watcher.name}” belongs to a rule: it is changed or removed with that "
                "rule, on the Rules page.")
    if rule is None:
        return ""  # its rule is gone: nothing depends on it
    return (f"The watcher “{watcher.name}” belongs to the rule “{rule.name}”: it is changed or removed "
            "with that rule, on the Rules page.")


def remove_file_watcher(platform: Any, rule_id: str) -> int:
    """Remove every watcher that starts ``rule_id`` (the rule is gone).
    BLOCKING; never raises."""
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None:
        return 0
    return sentinels.remove_for_rule(rule_id)


#: How far back a rule's own runs are read to tell the files they wrote.
OWN_RUNS_DAYS = 2
#: At most this many of a rule's recent runs are read for it.
OWN_RUNS_MAX = 200


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path or "")))
    except (TypeError, ValueError):
        return ""


def _written_files(kind: str, meta: Any, post: Any) -> list[tuple[str, str, Any]]:
    """Review (05-a): every file ONE Undo journal row says its tool wrote,
    as ``(path, mode, hash of what it left)`` (``path`` as the row has it:
    relative to the run's folder, or absolute). Read by the row's shape, so a
    tool that writes many files is read too: an ``apply_patch`` row's
    ``files``; a single file's ``path``; a ``paths`` list with its
    ``sha256`` map (``stack_apply``'s ``files_delete``); a ``dir`` with the
    ``outputs`` written in it and their ``hashes`` (``pdf_split``'s
    ``pdf_split_delete``). Never raises."""
    from ..tools import undo as _undo

    if not isinstance(meta, dict):
        return []
    if kind == _undo.PATCH_UNDO_KIND:
        return [(str(e.get("path")), "raw", e.get("post_sha256")) for e in _undo.patch_entries(meta)]
    out: list[tuple[str, str, Any]] = []
    mode = str(meta.get("mode") or "raw")
    if meta.get("path"):
        out.append((str(meta["path"]), mode, post))
    paths = meta.get("paths")
    if isinstance(paths, list):
        sums = meta.get("sha256") if isinstance(meta.get("sha256"), dict) else {}
        out += [(p, mode, sums.get(p)) for p in paths if isinstance(p, str) and p]
    outputs = meta.get("outputs")
    if isinstance(outputs, list):
        folder = str(meta.get("dir") or ".")
        sums = meta.get("hashes") if isinstance(meta.get("hashes"), dict) else {}
        out += [(str(Path(folder) / n), "raw", sums.get(n)) for n in outputs if isinstance(n, str) and n]
    return out


def own_output(engine: Any, rule: Any, paths: list[str]) -> set[str]:
    """Review (05-a): which of ``paths`` (new files in a file rule's folder)
    were written by the rule's OWN runs — a summary its work saved next to
    the file it read must never start the rule again, or the rule would keep
    starting itself (at the watcher's pace, under its hourly limit, never
    pausing). Read from the Undo journal of the rule's recent runs (each
    file a run's tools wrote, with the hash of what they left): a file counts
    only when its path AND its bytes are what the run wrote, so a file the
    user drops is never taken for one. Returns the normalized paths. Never
    raises (``set()``). BLOCKING."""
    rid = str(getattr(rule, "id", "") or "")
    if engine is None or not rid or not paths:
        return set()
    wanted = {_norm(p): p for p in paths if p}
    try:
        from datetime import timedelta

        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.ids import utcnow
        from ..core.models import Session, UndoJournal
        from ..tools import undo as _undo

        since = utcnow() - timedelta(days=OWN_RUNS_DAYS)
        origin = f"reflex:{getattr(rule, 'name', '')}"
        with session_scope(engine) as db:
            rows = db.exec(
                select(Session.id, Session.workspace_path, Session.origin, Session.options_json)
                .where(Session.origin.like("reflex:%"))  # type: ignore[union-attr]
                .where(Session.created_at >= since)
                .order_by(Session.created_at.desc())  # type: ignore[attr-defined]
                .limit(OWN_RUNS_MAX)
            ).all()
            runs: dict[str, str] = {}
            for sid, workspace, run_origin, options in rows:
                try:
                    opts = json.loads(options or "{}")
                except (TypeError, ValueError):
                    opts = {}
                if (isinstance(opts, dict) and opts.get("rule_id") == rid) or run_origin == origin:
                    runs[sid] = str(workspace or "")
            if not runs:
                return set()
            entries = db.exec(select(UndoJournal).where(UndoJournal.session_id.in_(list(runs)))).all()  # type: ignore[attr-defined]
            written: list[tuple[str, str, Any]] = []  # (normalized path, mode, post hash)
            for j in entries:
                meta = _undo.read_envelope({"pre_inline": j.pre_inline})
                # The folder the tool wrote against, as the registry stamped
                # it on the row; else the run's own folder.
                base = str((meta.get("workspace") if isinstance(meta, dict) else "") or runs.get(j.session_id, ""))
                for rel, mode, post in _written_files(j.kind, meta, getattr(j, "post_sha256", None)):
                    if not rel:
                        continue
                    full = Path(str(rel))
                    if not full.is_absolute() and base:
                        full = Path(base) / full
                    written.append((_norm(str(full)), str(mode), post))
        out: set[str] = set()
        for key, mode, post in written:
            if key not in wanted or key in out:
                continue
            if post is None or _undo.sha256_target(wanted[key], mode) == post:
                out.add(key)
        return out
    except Exception:  # noqa: BLE001 — unreadable: every file is the user's
        log.warning("the files rule %s wrote could not be read", rid, exc_info=True)
        return set()


def file_rule_fire(
    get_hub: Callable[[], Any],
    get_loop: Callable[[], Any],
    mark_seen: Callable[[str, list[dict]], None] | None = None,
):
    """The callback a rule-mode watcher hands its settled files to
    (``SentinelService.rule_fire``). It runs on the WATCHER'S thread: each
    start is sent to the daemon's loop and waited for (bounded), so a file
    is marked seen only once its start was decided. Returns one bool per
    file: True = decided (started, refused, or its rule is off or gone),
    False = it waits for the next scan (the rule is paused, or the start
    could not be reached).

    Review (05-a): a start that outlasts the wait (a stalled loop, a locked
    database) is still under way on the loop, so its file is not seen yet
    and the next scan hands it again. It is NOT started again while that
    start runs (it waits); the first scan after it ends takes its answer —
    decided, or (it ended held, or failed) started again. Kept in memory
    on the callback as ``under_way``: ``{(rule id, file): (start, when,
    (time, size))}``. ``mark_seen`` (``SentinelService.mark_rule_seen``):
    such a start that ENDS decided marks its files seen right then, on a
    thread of its own (never on the loop), so a restart before the next
    scan does not start them again. An ended start answers only for the
    same file (the same time and size): a new file of the same name later
    starts the rule as any new file does."""
    import concurrent.futures
    import threading
    import time

    under_way: dict[tuple[str, str], tuple[Any, float, tuple[Any, Any]]] = {}
    lock = threading.Lock()

    def _stat(it: dict) -> tuple[Any, Any]:
        return (it.get("mtime"), it.get("size"))

    def _seen_when_it_ends(fut: Any, watcher_name: str, group_items: list[dict]) -> None:
        """A start that outlasted the wait: once it ends decided, its files
        are marked seen (off the loop: the done callback runs there)."""
        if mark_seen is None or not watcher_name:
            return

        def _mark() -> None:
            try:
                mark_seen(watcher_name, group_items)
            except Exception:  # noqa: BLE001 — not marked: the next scan takes its answer
                log.warning("the files of a late start could not be marked seen", exc_info=True)

        def _ended(f: Any) -> None:
            try:
                if _ended_decided(f):
                    threading.Thread(target=_mark, name="rule-start-seen", daemon=True).start()
            except Exception:  # noqa: BLE001 — never breaks the loop
                log.warning("a late start's end could not be read", exc_info=True)

        fut.add_done_callback(_ended)

    def _decided(fired: Any) -> bool:
        return not any(isinstance(f, dict) and f.get("held") for f in (fired or []))

    def _ended_decided(fut: Any) -> bool:
        """A start that ended: True = decided; False = start it again (it
        ended held, failed or was cancelled)."""
        try:
            return _decided(fut.result(timeout=0))
        except Exception:  # noqa: BLE001 — it never started (or was cancelled): again
            return False

    def _fire(watcher: Any, items: list[dict]) -> list[bool]:
        loop, hub = get_loop(), get_hub()
        if loop is None or hub is None or not items:
            return [False] * len(items)
        rule_id = str(getattr(watcher, "rule_id", "") or "")
        per_file = True
        rule = None
        store = None
        try:
            store = getattr(getattr(hub, "router", None), "store", None)
            rule = store.get(rule_id) if store is not None and rule_id else None
            if rule is not None:
                per_file = rule.decoded_start().get("per_file", True) is not False
        except Exception:  # noqa: BLE001 — read nothing: one start per file
            per_file = True
        from ..reflex.router import file_context

        # Review (05-a): a file the rule's own work wrote is decided (seen)
        # and starts nothing — the rule never starts itself on its results.
        own = own_output(getattr(store, "engine", None), rule,
                         [str(it.get("path") or "") for it in items]) if rule is not None else set()
        answers: dict[int, bool] = {}
        todo: list[tuple[int, dict]] = []
        now = time.monotonic()
        with lock:
            for key, (f, t, _st) in list(under_way.items()):
                if f.done() and now - t > UNDER_WAY_KEEP_S:
                    under_way.pop(key, None)
        for i, it in enumerate(items):
            norm = _norm(str(it.get("path") or ""))
            if own and norm in own:
                answers[i] = True
                log.info("rule %s: %s was written by its own work, so it starts nothing",
                         rule_id, Path(str(it.get("path") or "")).name)
                continue
            with lock:
                prior = under_way.get((rule_id, norm))
                ended = prior is not None and prior[0].done()
                if ended:
                    under_way.pop((rule_id, norm), None)
            if prior is not None and not ended:
                answers[i] = False  # its start is still under way: never twice
                continue
            if prior is not None and prior[2] == _stat(it) and _ended_decided(prior[0]):
                answers[i] = True  # it started meanwhile: decided now
                continue
            todo.append((i, it))
        groups = [[x] for x in todo] if per_file else ([todo] if todo else [])
        for group in groups:
            ctx = file_context([str(it.get("path") or "") for _i, it in group], rule_id=rule_id)
            fut = None
            try:
                fut = asyncio.run_coroutine_threadsafe(hub.signal("file", ctx), loop)
                decided = _decided(fut.result(timeout=FILE_FIRE_TIMEOUT_S))
            except concurrent.futures.TimeoutError:
                # Still under way on the loop: its files wait, and the scan
                # after it ends takes its answer (never a second start).
                decided = False
                if fut is not None:
                    with lock:
                        for _i, it in group:
                            under_way[(rule_id, _norm(str(it.get("path") or "")))] = (
                                fut, time.monotonic(), _stat(it),
                            )
                    _seen_when_it_ends(fut, str(getattr(watcher, "name", "") or ""), [it for _i, it in group])
                log.warning("rule %s: a start took longer than %d s; its file waits for it",
                            rule_id, int(FILE_FIRE_TIMEOUT_S))
            except Exception:  # noqa: BLE001 — not reached: the files wait
                log.warning("a file rule could not be started", exc_info=True)
                decided = False
            for i, _it in group:
                answers[i] = decided
        return [answers.get(i, False) for i in range(len(items))]

    _fire.under_way = under_way  # type: ignore[attr-defined]
    _fire.mark_seen = mark_seen  # type: ignore[attr-defined]
    return _fire


# --- a download rule ----------------------------------------------------------


def download_handler(get_hub: Callable[[], Any], get_loop: Callable[[], Any]):
    """An event-bus handler: a VERIFIED finished download
    (``browser.download_completed`` with a ``local_path``) starts the
    download rules that hear it, on the daemon's loop (sent, not waited
    for: the bus thread never blocks on a start). Only the verified local
    path, the file's name and the site (scheme, host and path, never the
    query) are passed. Never raises."""
    import threading

    from ..browser.downloads import EVENT_TYPE, site_of

    # One start per download: a repeat of the same report (the same id and
    # file) starts nothing again. Bounded, in memory.
    seen: list[tuple[str, str]] = []
    lock = threading.Lock()

    def _on_event(event: Any) -> None:
        try:
            if getattr(event, "type", "") != EVENT_TYPE:
                return
            payload = dict(getattr(event, "payload", None) or {})
            local = str(payload.get("local_path") or "").strip()
            if not local:
                return  # not verified: nothing is started from a claim
            hub, loop = get_hub(), get_loop()
            if hub is None or loop is None:
                return
            key = (str(payload.get("download_id") or ""), os.path.normcase(local))
            with lock:
                if key in seen:
                    return
                seen.append(key)
                del seen[:-200]
            from ..reflex.router import download_context

            site = site_of(payload.get("source_url")) or site_of(payload.get("final_url"))
            ctx = download_context(local_path=local, site=site)
            asyncio.run_coroutine_threadsafe(hub.signal("download", ctx), loop)
        except Exception:  # noqa: BLE001 — a bus handler runs beside every other one
            log.warning("a finished download could not start its rules", exc_info=True)

    return _on_event


# --- "when another task fails" (leap 05-b) ----------------------------------
#
# ONE signal per run that ended short. A schedule's fire speaks for every run
# it ran (its recorded outcome, :func:`schedule_ended_signal`): the bus
# handler stays quiet inside a schedule fire. A workflow's run speaks for its
# steps (a step's own session is never a second signal). A rule's run and an
# agent run speak for themselves. The verdict and the one-line reason come
# from the record (``agents/outcome.run_ending``, a workflow's status, a
# schedule's row), never from the model's closing words; what the run SAID is
# carried as ``detail`` and fenced in the rule's task.


def rule_started_by(rule: Any) -> str:
    """What a run a rule started carries as ``WorkflowRunRecord.started_by``:
    ``rule:<source>:<id>`` ("" for the phone's ``/run``, a ``manual:`` rule
    that is not stored)."""
    if str(getattr(rule, "name", "") or "").startswith("manual:"):
        return ""
    return f"rule:{getattr(rule, 'source', '') or ''}:{getattr(rule, 'id', '') or ''}"


def parse_started_by(value: Any) -> tuple[str, str]:
    """``(source, rule id)`` from a ``started_by`` :func:`rule_started_by`
    wrote; ``("", "")`` for anything else (a person's run, a schedule's)."""
    parts = str(value or "").split(":", 2)
    if len(parts) == 3 and parts[0] == "rule" and parts[2]:
        return parts[1], parts[2]
    return "", ""


#: ``agents/outcome.run_ending``'s badge word -> a run's verdict.
_VERDICT_OF_WORD = {
    "Failed": "failed",
    "Part failed": "part_failed",
    "Needs you": "needs_you",
    "Did nothing": "did_nothing",
}
#: A schedule's recorded status (``scheduling/run_outcome``) -> a verdict. A
#: fire that was done, skipped, missed or stopped starts nothing.
_VERDICT_OF_SCHEDULE = {
    "error": "failed",
    "partial": "part_failed",
    "needs_you": "needs_you",
    "did_nothing": "did_nothing",
}
#: A workflow run's ending status -> a verdict.
_VERDICT_OF_WORKFLOW = {
    "failed": "failed",
    "completed_with_failures": "part_failed",
}


def _reason_for(verdict: str) -> str:
    """The ledger's one sentence for a verdict (the Schedules row's words,
    ``scheduling/run_outcome``)."""
    try:
        from ..scheduling.run_outcome import DID_NOTHING_LEAD, NEEDS_YOU_LEAD, PARTIAL_LEAD
    except Exception:  # noqa: BLE001
        DID_NOTHING_LEAD = NEEDS_YOU_LEAD = PARTIAL_LEAD = ""
    return {
        "failed": "The run did not finish.",
        "part_failed": PARTIAL_LEAD,
        "needs_you": NEEDS_YOU_LEAD,
        "did_nothing": DID_NOTHING_LEAD,
    }.get(verdict, "")


def _rule_name(engine: Any, rule_id: str) -> tuple[str, str]:
    """``(name, source)`` of a stored rule, ``("", "")`` when it is gone."""
    if not rule_id or engine is None:
        return "", ""
    try:
        from ..reflex.store import ReflexStore

        rule = ReflexStore(engine).get(rule_id)
    except Exception:  # noqa: BLE001
        return "", ""
    if rule is None:
        return "", ""
    return str(rule.name or ""), str(rule.source or "")


def ending_of_session(engine: Any, session_id: str, payload: dict | None = None) -> dict | None:
    """The ``run_ended`` context of a finished agent run, or None when it
    starts nothing: it was done, or stopped; it is a REHEARSAL; it is a
    workflow's step (the workflow speaks); it is chat's shared turn row.
    BLOCKING (one row read, plus the rule that started it); never raises."""
    sid = str(session_id or "").strip()
    data = dict(payload or {})
    if not sid or sid == "chat" or data.get("rehearsal"):
        return None
    try:
        from ..agents.outcome import run_ending
        from ..core.db import session_scope
        from ..core.models import Session
        from ..scheduling.knobs import session_options
        from ..tools.rehearsal import rehearsal_of

        with session_scope(engine) as db:
            row = db.get(Session, sid)
            if row is None:
                return None
            if rehearsal_of(row):
                return None
            ending = run_ending(row)
            verdict = _VERDICT_OF_WORD.get(ending.words, "")
            if ending.ok or not verdict:
                return None  # done, or stopped by a person or Emergency stop
            origin = str(row.origin or "")
            door, _, rest = origin.partition(":")
            opts = session_options(row) or {}
            summary = str(row.summary or "")
            trust = str(row.trust or "")
            agent = str(row.agent_name or "")
        if door == "workflow":
            return None  # a workflow's step: its workflow's ending speaks for it
        # A helper another run handed work to (``delegate``, ``spawn_agent``,
        # a mission's teammate): its root run's ending speaks for it, as a
        # step's workflow does. Children never inherit run options, so a
        # helper of a run_ended rule's run would otherwise read as a fresh
        # agent run and could start that rule again.
        from ..agents.team import mission_root

        root = mission_root(engine, sid)
        if root is not None and str(getattr(root, "id", "") or "") != sid:
            return None
        from ..reflex.router import RULE_OPTION, RULE_SOURCE_OPTION, run_ended_context

        rid = str(opts.get(RULE_OPTION) or "") if isinstance(opts, dict) else ""
        rule_name, rule_source = _rule_name(engine, rid)
        rule_source = str((opts.get(RULE_SOURCE_OPTION) if isinstance(opts, dict) else "") or rule_source)
        if door == "reflex" or rid:
            name = rule_name or (rest if door == "reflex" else "")
            return run_ended_context(
                kind="rule", name=name, verdict=verdict, reason=ending.reason, detail=summary,
                href=f"/sessions/{sid}", run_id=sid, by_rule_id=rid, by_rule_name=name,
                by_rule_source=rule_source, careful=trust == "low",
            )
        if door == "schedule":
            # Outside a schedule's fire (a person's rerun keeps the origin):
            # the run speaks for its schedule.
            return run_ended_context(
                kind="schedule", name=rest, verdict=verdict, reason=ending.reason, detail=summary,
                href=f"/sessions/{sid}", run_id=sid, careful=trust == "low",
            )
        name = agent.split(":", 1)[1] if agent.startswith("custom:") else ""
        return run_ended_context(
            kind="agent", name=name, verdict=verdict, reason=ending.reason, detail=summary,
            href=f"/sessions/{sid}", run_id=sid, careful=trust == "low",
        )
    except Exception:  # noqa: BLE001 — a reading never breaks a run's end
        log.warning("the end of run %s could not be read for the rules", sid, exc_info=True)
        return None


def ending_of_workflow(engine: Any, payload: dict | None) -> dict | None:
    """The ``run_ended`` context of a finished WORKFLOW run
    (``workflow.completed``), or None when it was done or stopped. Its reason
    names the step that stopped it, or the steps it went on past, by name
    (code, from the run's own record); its ``detail`` is that step's words.
    BLOCKING (one row read); never raises."""
    data = dict(payload or {})
    status = str(data.get("status") or "")
    verdict = _VERDICT_OF_WORKFLOW.get(status, "")
    if not verdict:
        return None
    name = str(data.get("workflow") or "")
    run_id = str(data.get("run_id") or "")
    try:
        started_by, outputs = "", {}
        if run_id and engine is not None:
            from ..core.db import session_scope
            from ..workflows.models import WorkflowRunRecord

            with session_scope(engine) as db:
                rec = db.get(WorkflowRunRecord, run_id)
                if rec is not None:
                    started_by = str(rec.started_by or "")
                    try:
                        outputs = json.loads(rec.outputs_json or "{}")
                    except (TypeError, ValueError):
                        outputs = {}
        outputs = outputs if isinstance(outputs, dict) else {}
        failed = [
            (str(step), out) for step, out in outputs.items()
            if isinstance(out, dict) and out.get("status") == "failed" and step != "Trigger"
        ]
        steps = [" ".join(s.split())[:60] for s, _o in failed][:5]
        if verdict == "failed":
            reason = (f"It stopped at its step “{steps[-1]}”." if steps
                      else " ".join(str(data.get("reason") or "").split())[:200] or "The run did not finish.")
        else:
            reason = (
                "Some of its steps failed: " + ", ".join(f"“{s}”" for s in steps) + "."
                if steps else _reason_for("part_failed")
            )
        detail = "\n".join(str(o.get("summary") or "")[:600] for _s, o in failed[-2:] if o.get("summary"))
        source, rid = parse_started_by(started_by)
        rule_name, _src = _rule_name(engine, rid)
        from ..reflex.router import OUTSIDE_SOURCES, run_ended_context

        trigger = outputs.get("Trigger") if isinstance(outputs.get("Trigger"), dict) else {}
        careful = source in OUTSIDE_SOURCES or str((trigger or {}).get("trust") or "") == "low"
        return run_ended_context(
            kind="workflow", name=name, verdict=verdict, reason=reason, detail=detail,
            href="/workflows", run_id=run_id, by_rule_id=rid, by_rule_name=rule_name,
            by_rule_source=source, careful=careful,
        )
    except Exception:  # noqa: BLE001 — a reading never breaks a run's end
        log.warning("the end of workflow run %s could not be read for the rules", run_id, exc_info=True)
        return None


def ending_of_schedule(task: Any, status: str, detail: str = "", session_id: str = "") -> dict | None:
    """The ``run_ended`` context of ONE schedule fire, from what its row now
    records (``platform._record_outcome``), or None when the fire was done,
    skipped, missed or stopped. Pure."""
    verdict = _VERDICT_OF_SCHEDULE.get(str(status or ""), "")
    if not verdict or task is None:
        return None
    from ..reflex.router import run_ended_context

    sid = str(session_id or "")
    return run_ended_context(
        kind="schedule", name=str(getattr(task, "name", "") or ""), verdict=verdict,
        reason=_reason_for(verdict), detail=str(detail or ""),
        href=f"/sessions/{sid}" if sid else "/schedules", run_id=sid or str(getattr(task, "id", "") or ""),
    )


def _someone_listens(hub: Any) -> bool:
    """Is there an enabled ``run_ended`` rule, or a waiter on these signals?
    Read before anything else, so a PC with no such rule does no work at a
    run's end (one indexed read). Never raises (a fault reads True)."""
    if any(want in ("*", "run_ended") for want, _fn in list(getattr(hub, "_waiters", []) or [])):
        return True
    store = getattr(getattr(hub, "router", None), "store", None)
    if store is None:
        return False
    try:
        return any(r.enabled for r in store.list(source="run_ended"))
    except Exception:  # noqa: BLE001
        return True


def _signal_hub(hub: Any, loop: Any, ctx: dict) -> Any:
    """Send one ``run_ended`` signal to the daemon's loop (never waited
    for). Returns the future (tests wait on it). Sent from a FRESH context:
    the caller's (a schedule's fire, a bus handler's copy of a run's) would
    ride into the rule's run, which is not part of that fire (its end would
    read as inside it)."""
    import contextvars

    return contextvars.Context().run(asyncio.run_coroutine_threadsafe, hub.signal("run_ended", ctx), loop)


def run_ended_handler(get_hub: Callable[[], Any], get_loop: Callable[[], Any]):
    """An event-bus handler (a sync one: the bus runs it off the loop):
    ``session.completed`` and ``workflow.completed`` that ended SHORT send one
    ``run_ended`` signal to the hub on the daemon's loop (sent, not waited
    for). Quiet inside a schedule's fire (the schedule's recorded outcome is
    that fire's one signal) and when no rule or waiter listens. One signal
    per run (a repeat of the same end starts nothing again). Never raises."""
    import threading

    from ..core.events import EventType

    seen: list[tuple[str, str]] = []
    lock = threading.Lock()

    def _on_event(event: Any) -> None:
        try:
            etype = getattr(event, "type", "")
            if etype not in (EventType.SESSION_COMPLETED, EventType.WORKFLOW_COMPLETED):
                return
            from ..comm.notifier import in_schedule_fire

            if in_schedule_fire():
                return
            hub, loop = get_hub(), get_loop()
            if hub is None or loop is None or not _someone_listens(hub):
                return
            engine = getattr(getattr(getattr(hub, "router", None), "store", None), "engine", None)
            payload = dict(getattr(event, "payload", None) or {})
            if etype == EventType.SESSION_COMPLETED:
                ctx = ending_of_session(engine, str(getattr(event, "session_id", "") or ""), payload)
            else:
                ctx = ending_of_workflow(engine, payload)
            if not ctx:
                return
            key = (str(ctx.get("ended_kind") or ""), str(ctx.get("run_id") or ""))
            with lock:
                if key[1] and key in seen:
                    return
                seen.append(key)
                del seen[:-500]
            _signal_hub(hub, loop, ctx)
        except Exception:  # noqa: BLE001 — a bus handler runs beside every other one
            log.warning("a run's end could not reach the rules", exc_info=True)

    return _on_event


def schedule_ended_signal(get_hub: Callable[[], Any], get_loop: Callable[[], Any]):
    """The hook a schedule's fire calls once its row records how it went
    (``platform._run_scheduled``): a fire that ended short sends ONE
    ``run_ended`` signal to the hub on the daemon's loop (sent, not waited
    for; a schedule fires on the scheduler's own thread). Returns the future,
    or None. BLOCKING (one indexed read); never raises."""

    def _ended(task: Any, status: str, detail: str = "", session_id: str = "") -> Any:
        try:
            ctx = ending_of_schedule(task, status, detail, session_id)
            if not ctx:
                return None
            hub, loop = get_hub(), get_loop()
            if hub is None or loop is None or not _someone_listens(hub):
                return None
            return _signal_hub(hub, loop, ctx)
        except Exception:  # noqa: BLE001 — the row is the record either way
            log.warning("a schedule's end could not reach the rules", exc_info=True)
            return None

    return _ended


# --- every start in one place (leap 05-b) -------------------------------------

#: A schedule's recorded status, in the words every badge says.
_SCHEDULE_LAST = {
    "ok": "Done",
    "error": "Failed",
    "needs_you": "Needs you",
    "partial": "Part failed",
    "did_nothing": "Did nothing",
    "skipped": "Skipped",
    "missed": "Missed",
    "cancelled": "Stopped",
}


def _schedule_last_words(rec: Any) -> str:
    status = str(getattr(rec, "last_status", "") or "")
    if not status:
        return "Not run yet."
    word = _SCHEDULE_LAST.get(status, status.replace("_", " ").capitalize())
    detail = _q(getattr(rec, "last_detail", "") or "", 160)
    return f"Last run: {word}" + (f". {detail}" if detail else ".")


def _rule_last_words(rule: Any) -> str:
    if getattr(rule, "paused_at", None):
        return _q(getattr(rule, "paused_reason", "") or "Paused.", 160)
    err = _q(getattr(rule, "last_error", "") or "", 160)
    if err:
        return f"Last time it could not start: {err}"
    done = _q(getattr(rule, "last_result", "") or "", 160)
    if done:
        return f"Last time: {done}"
    return "Has not run yet."


def _schedule_workflow(rec: Any) -> str:
    if str(getattr(rec, "kind", "") or "") != "workflow":
        return ""
    try:
        payload = rec.decoded_payload()
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(str(payload.get("workflow") or payload.get("name") or "").split())


def list_starts(platform: Any, *, workflow: str | None = None) -> list[dict[str, Any]]:
    """Leap 05-b: every way work starts on its own here, ONE list built from
    the stored objects themselves (no table of its own): each schedule
    (``ScheduledTaskRecord``, it starts at a time) and each rule
    (``ReflexRule``). Rows: ``{id, kind, name, when_words, does_words,
    enabled, last_words, href, store}``; ``id`` loads from ``store``'s own
    table. ``workflow``: only what runs that saved workflow. BLOCKING."""
    want = " ".join(str(workflow or "").split()).casefold()
    rows: list[dict[str, Any]] = []
    scheduler = getattr(platform, "scheduler", None)
    if scheduler is not None:
        from ..scheduling.tools import _does_words, when_words

        try:
            schedules = list(scheduler.list())
        except Exception:  # noqa: BLE001 — unreadable: the rules still list
            log.warning("the schedules could not be read for the list of starts", exc_info=True)
            schedules = []
        for rec in schedules:
            if want and _schedule_workflow(rec).casefold() != want:
                continue
            does = _does_words(rec)
            # The time in words; the stored cron line stays on Schedules.
            when = re.sub(r"\s*\(cron [^)]*\)$", "", when_words(rec)).strip()
            rows.append({
                "id": rec.id,
                "kind": "schedule",
                "name": rec.name,
                "when_words": (when[:1].upper() + when[1:] + ".") if when else "",
                "does_words": (does[:1].upper() + does[1:] + ("" if does.endswith(".") else ".")) if does else "",
                "enabled": bool(rec.enabled),
                "last_words": _schedule_last_words(rec),
                "href": "/schedules",
                "store": "schedule",
            })
    reflex = getattr(platform, "reflex", None)
    if reflex is not None:
        try:
            rules = list(reflex.list())
        except Exception:  # noqa: BLE001
            log.warning("the rules could not be read for the list of starts", exc_info=True)
            rules = []
        for rule in rules:
            if want and not (rule.action == "workflow" and " ".join(str(rule.target or "").split()).casefold() == want):
                continue
            words = describe_for(platform, rule)
            rows.append({
                "id": rule.id,
                "kind": kind_of(str(rule.source or "")),
                "name": rule.name,
                "when_words": words.get("when_words", ""),
                "does_words": words.get("does_words", ""),
                "enabled": bool(rule.enabled) and getattr(rule, "paused_at", None) is None,
                "last_words": _rule_last_words(rule),
                "href": f"/reflex?rule={rule.id}",
                "store": "rule",
            })
    return rows


# --- the card: When, Do, Where it works, Limits ------------------------------


def _q(text: Any, cap: int = 80) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= cap else words[: cap - 1].rstrip() + "…"


def _folder_name(path: str) -> str:
    p = str(path or "").rstrip("\\/")
    return Path(p).name or p


#: Leap 05-b: how the task must have ended, in words (``start_json.outcome``).
_OUTCOME_VERB = {
    "failed": "fails",
    "part_failed": "finishes with part of it failed",
    "needs_you": "ends needing you",
    "any": "fails or falls short (part failed, needs you, or did nothing)",
}
#: Which task, when no name is given (``start_json.what``).
_ANY_OF_KIND = {
    "schedule": "any of your schedules",
    "workflow": "any workflow",
    "rule": "a run of any rule",
    "agent": "any agent run",
    "any": "any other task",
}


def run_ended_when_words(start: dict) -> str:
    """The When line of a ``run_ended`` rule: "When the schedule “Nightly
    report” fails." Pure."""
    from ..reflex.models import RUN_ENDED_DEFAULT_OUTCOME
    from ..reflex.router import ended_what_words

    what = str((start or {}).get("what") or "any")
    name = str((start or {}).get("name") or "").strip()
    outcome = str((start or {}).get("outcome") or RUN_ENDED_DEFAULT_OUTCOME)
    if what == "any":
        which = f"the task “{_q(name, 60)}”" if name else _ANY_OF_KIND["any"]
    elif what == "rule":
        which = f"a run of the rule “{_q(name, 60)}”" if name else _ANY_OF_KIND["rule"]
    else:
        which = ended_what_words(what, name) if name else _ANY_OF_KIND.get(what, "another task")
    return f"When {which} {_OUTCOME_VERB.get(outcome, 'fails')}."


def describe(rule: Any, *, tick_seconds: int = 300, careful: bool | None = None,
             watching: bool = True) -> dict[str, str]:
    """The four lines of a rule's card and row, composed by code:
    ``when_words``, ``does_words``, ``where_words``, ``limits_words``.
    ``careful``: does its work start in the careful mode (an outside signal,
    a download; the router's ``_signal_trust``)? ``watching``: for a file
    rule, is folder watching able to run (``IRONJARVIS_SENTINELS`` is not
    off)? Pure."""
    source = str(getattr(rule, "source", "") or "")
    match = _q(getattr(rule, "match", ""), 60)
    start = rule.decoded_start() if hasattr(rule, "decoded_start") else {}
    if source == "file":
        folder = str(start.get("path") or "")
        glob = _q(start.get("glob") or "", 40)
        every = max(1, int(round(max(15, int(tick_seconds or 300)) / 60)))
        when = f"When a new file lands in {_folder_name(folder) or 'the folder'}"
        if glob:
            when += f" (matching {glob})"
        when += f". It looks every {every} minute{'' if every == 1 else 's'}; files already there are left alone."
        if not watching:
            when += " Folder watching is turned off on this PC, so it looks at nothing until it is on again."
    elif source == "download":
        site = _q(start.get("match_site") or "", 60)
        when = "When a download finishes in your browser"
        if site:
            when += f" from {site}"
        if match:
            when += f" and its name has “{match}”"
        when += "."
    elif source == "run_ended":
        when = run_ended_when_words(start)
    elif source == "webhook":
        when = f"When the webhook “{match or '?'}” is called."
    elif source == "email":
        when = f"When an email mentions “{match}”." if match else "When any email arrives."
    elif source == "calendar":
        when = f"When an event titled “{match}” is near." if match else "When any event is near."
    elif source == "slack":
        when = f"When a Slack message mentions “{match}”." if match else "When any Slack message arrives."
    else:
        when = f"When a message from your phone mentions “{match}”." if match else "When any message arrives from your phone."

    action = str(getattr(rule, "action", "") or "")
    target = _q(getattr(rule, "target", ""), 60)
    task = _q(getattr(rule, "task_template", ""), 160)
    agent = _q(getattr(rule, "agent_name", ""), 60)
    if action == "workflow":
        does = f"Runs the workflow “{target}”."
    elif action == "remote_agent":
        does = f"Hands it to the remote agent “{target}”" + (f": {task}" if task else ".")
    else:
        who = f"Your agent “{agent}”" if agent else "Jarvis"
        does = f"{who} does: {task}" if task else f"{who} decides what to do with it."
    if not does.endswith((".", "?", "!", "…")):
        does += "."

    root = str(getattr(rule, "workspace_root", "") or "").strip()
    if root:
        where = f"In {root}."
    elif getattr(rule, "project_id", None):
        where = "In its project's folder."
    else:
        where = "In a work folder of its own."

    limit = int(getattr(rule, "max_fires_per_hour", 0) or 12)
    limits = f"Starts at most {limit} times an hour, then pauses and tells you. Anything risky asks you first."
    if source == "run_ended":
        # Leap 05-b: the loop guard, said where the rule is read.
        limits += " The work it does never starts it, or a rule like it, again."
    if careful is None:
        from ..reflex.router import OUTSIDE_SOURCES, watches_downloads

        careful = source in OUTSIDE_SOURCES or watches_downloads(rule)
    if careful:
        if source == "download":
            limits += " It starts carefully: the file came from a website."
        elif source == "file":
            limits += " It starts carefully: files in a Downloads folder come from websites."
        else:
            limits += " It starts carefully, because what starts it comes from outside this PC."
    return {"when_words": when, "does_words": does, "where_words": where, "limits_words": limits}


def describe_for(platform: Any, rule: Any) -> dict[str, str]:
    """:func:`describe` with this PC's settings (the watcher's pace, whether
    outside signals start carefully, whether folder watching may run)."""
    cfg = getattr(platform, "config", None)
    try:
        tick = int(getattr(cfg, "sentinels_tick_seconds", 300) or 300)
    except (TypeError, ValueError):
        tick = 300
    from ..reflex.router import OUTSIDE_SOURCES, watches_downloads

    source = str(getattr(rule, "source", "") or "")
    full = str(getattr(cfg, "comm_trust", "low") or "low").strip().lower() == "full"
    careful = (source in OUTSIDE_SOURCES or watches_downloads(rule)) and not full
    watching = os.environ.get("IRONJARVIS_SENTINELS", "on").strip().lower() not in {"0", "false", "no", "off"}
    return describe(rule, tick_seconds=tick, careful=careful, watching=watching)
