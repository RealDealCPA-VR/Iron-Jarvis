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
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..core.logging import get_logger

log = get_logger("triggers.starts")

#: Every kind of start (the plan's list). ``phone`` is stored on a rule as
#: ``comm`` (its wire value, :data:`WIRE_SOURCE`). ``schedule`` starts come
#: from the scheduler's clock and ``run_ended`` from a run's end (05-b); the
#: hub carries them for waiters and the one list of starts.
START_KINDS: tuple[str, ...] = (
    "schedule", "webhook", "email", "calendar", "phone", "slack", "file", "download", "run_ended",
)

#: A start kind's value on a rule's ``source`` column, where they differ.
WIRE_SOURCE: dict[str, str] = {"phone": "comm"}

#: How long the watcher's thread waits for the hub to start a file rule.
FILE_FIRE_TIMEOUT_S = 120.0

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
        if kind in ("schedule", "run_ended"):
            return []  # no rule hears these yet (05-b adds run_ended)
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


def ensure_file_watcher(platform: Any, rule: Any) -> Any:
    """Make (or keep) the ``rule``-mode watcher of a FILE rule: the folder
    and pattern of ``start_json``. Adding a watcher never scans or fires;
    its first check is the baseline (files already there are left alone).
    Returns the watcher, or None for a rule that is not a file rule. Raises
    ``ValueError`` in words for a folder the watcher service refuses (a
    network location, a protected path). BLOCKING."""
    if str(getattr(rule, "source", "") or "") != "file":
        return None
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None:
        raise ValueError("Folder watching is not available in this Iron Jarvis.")
    existing = watcher_for_rule(platform, rule.id)
    if existing is not None:
        return existing
    start = rule.decoded_start()
    path = str(start.get("path") or "").strip()
    if not path:
        raise ValueError("A file rule needs the folder to watch.")
    name = watcher_name_for(rule)
    if sentinels.get(name) is not None:
        name = f"{name} ({rule.id[-6:]})"
    return sentinels.add(
        name,
        path=path,
        glob=str(start.get("glob") or "").strip() or None,
        task="",
        mode="rule",
        rule_id=rule.id,
    )


def watcher_for_rule(platform: Any, rule_id: str) -> Any:
    """The rule-mode watcher that starts ``rule_id``, or None. BLOCKING."""
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None or not rule_id:
        return None
    for rec in sentinels.list():
        if getattr(rec, "mode", "") == "rule" and getattr(rec, "rule_id", "") == rule_id:
            return rec
    return None


def remove_file_watcher(platform: Any, rule_id: str) -> int:
    """Remove every watcher that starts ``rule_id`` (the rule is gone).
    BLOCKING; never raises."""
    sentinels = getattr(platform, "sentinels", None)
    if sentinels is None:
        return 0
    return sentinels.remove_for_rule(rule_id)


def file_rule_fire(get_hub: Callable[[], Any], get_loop: Callable[[], Any]):
    """The callback a rule-mode watcher hands its settled files to
    (``SentinelService.rule_fire``). It runs on the WATCHER'S thread: each
    start is sent to the daemon's loop and waited for (bounded), so a file
    is marked seen only once its start was decided. Returns one bool per
    file: True = decided (started, refused, or its rule is off or gone),
    False = it waits for the next scan (the rule is paused, or the start
    could not be reached)."""

    def _fire(watcher: Any, items: list[dict]) -> list[bool]:
        loop, hub = get_loop(), get_hub()
        if loop is None or hub is None or not items:
            return [False] * len(items)
        rule_id = str(getattr(watcher, "rule_id", "") or "")
        per_file = True
        try:
            store = getattr(getattr(hub, "router", None), "store", None)
            rule = store.get(rule_id) if store is not None and rule_id else None
            if rule is not None:
                per_file = rule.decoded_start().get("per_file", True) is not False
        except Exception:  # noqa: BLE001 — read nothing: one start per file
            per_file = True
        from ..reflex.router import file_context

        groups = [[it] for it in items] if per_file else [list(items)]
        out: list[bool] = []
        for group in groups:
            ctx = file_context([str(it.get("path") or "") for it in group], rule_id=rule_id)
            try:
                fut = asyncio.run_coroutine_threadsafe(hub.signal("file", ctx), loop)
                fired = fut.result(timeout=FILE_FIRE_TIMEOUT_S)
                decided = not any(isinstance(f, dict) and f.get("held") for f in (fired or []))
            except Exception:  # noqa: BLE001 — not reached: the files wait
                log.warning("a file rule could not be started", exc_info=True)
                decided = False
            out.extend([decided] * len(group))
        return out

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


# --- the card: When, Do, Where it works, Limits ------------------------------


def _q(text: Any, cap: int = 80) -> str:
    words = " ".join(str(text or "").split())
    return words if len(words) <= cap else words[: cap - 1].rstrip() + "…"


def _folder_name(path: str) -> str:
    p = str(path or "").rstrip("\\/")
    return Path(p).name or p


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
    if careful is None:
        from ..reflex.router import OUTSIDE_SOURCES

        careful = source in OUTSIDE_SOURCES
    if careful:
        limits += (
            " It starts carefully: the file came from a website."
            if source == "download"
            else " It starts carefully, because what starts it comes from outside this PC."
        )
    return {"when_words": when, "does_words": does, "where_words": where, "limits_words": limits}


def describe_for(platform: Any, rule: Any) -> dict[str, str]:
    """:func:`describe` with this PC's settings (the watcher's pace, whether
    outside signals start carefully, whether folder watching may run)."""
    cfg = getattr(platform, "config", None)
    try:
        tick = int(getattr(cfg, "sentinels_tick_seconds", 300) or 300)
    except (TypeError, ValueError):
        tick = 300
    from ..reflex.router import OUTSIDE_SOURCES

    source = str(getattr(rule, "source", "") or "")
    full = str(getattr(cfg, "comm_trust", "low") or "low").strip().lower() == "full"
    careful = source in OUTSIDE_SOURCES and not full
    watching = os.environ.get("IRONJARVIS_SENTINELS", "on").strip().lower() not in {"0", "false", "no", "off"}
    return describe(rule, tick_seconds=tick, careful=careful, watching=watching)
