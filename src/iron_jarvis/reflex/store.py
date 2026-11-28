"""Durable CRUD + matching for :class:`ReflexRule` rows.

Pure persistence — no execution, no orchestrator. The :class:`ReflexRouter`
owns *running* a matched rule; this store owns *finding* and *managing* them, so
it is cheap to build (just an engine) and safe to expose on the platform.
"""

from __future__ import annotations

import json
import re
import threading
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from .models import MAX_FIRES_PER_HOUR, RUN_ENDED_DEFAULT_OUTCOME, ReflexRule

log = get_logger("reflex.store")

#: Leap 05-a: what must go WITH a removed rule, per database (the platform
#: registers it: a file rule's watcher is removed and the rule's standing OKs
#: are revoked). Keyed weakly by the engine (the ``WorkflowStore.on_removed``
#: pattern): a ``ReflexStore`` is made wherever one is needed.
_ON_REMOVED: "weakref.WeakKeyDictionary[Engine, Callable[[str], None]]" = weakref.WeakKeyDictionary()

#: Leap 05-a (review): a rule's counters (the hourly count, the fire count)
#: are read and written in ONE step for every store in this process, so
#: starts that arrive together (each on its own worker thread) cannot all
#: read "11 so far" and all go, nor lose each other's count. Held only for
#: one small row read and write; never taken twice.
_GATE_LOCK = threading.Lock()


def on_removed(engine: Engine, callback: "Callable[[str], None]") -> None:
    """Register what runs after a rule is removed from ``engine`` (its id)."""
    _ON_REMOVED[engine] = callback


#: Leap 05-c: what runs after an edit DROPPED a rule's kept Limits (the
#: platform registers it: the OKs Keep it made are revoked), per database.
_ON_LIMITS_DROPPED: "weakref.WeakKeyDictionary[Engine, Callable[[str, list[str]], None]]" = (
    weakref.WeakKeyDictionary()
)


def on_limits_dropped(engine: Engine, callback: "Callable[[str, list[str]], None]") -> None:
    """Register what runs after an edit dropped a rule's Limits (its id and
    the ids of the OKs Keep it made)."""
    _ON_LIMITS_DROPPED[engine] = callback


def pause_words(limit: int) -> str:
    """Leap 05-a (D-05.3): the words a rule that paused itself carries."""
    n = int(limit or MAX_FIRES_PER_HOUR)
    return f"Paused: it started {n} times in an hour."


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _recent(raw: str, now: datetime) -> list[datetime]:
    """The starts of the last hour, from ``recent_starts_json``."""
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        items = []
    out: list[datetime] = []
    for it in items if isinstance(items, list) else []:
        try:
            when = _aware(datetime.fromisoformat(str(it)))
        except (TypeError, ValueError):
            continue
        # A start stamped AFTER ``now`` still counts (v1.576.1). Each caller
        # takes its ``now`` before it waits for the gate's lock, so two signals
        # of one burst can pass the gate in the other order than their stamps
        # (and a clock can step back): dropping such a start forgot a start
        # that was already counted, and a 13th got through a limit of 12.
        if when is not None and now - when < timedelta(hours=1):
            out.append(when)
    return out


class ReflexStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self, source: str | None = None) -> list[ReflexRule]:
        with session_scope(self.engine) as db:
            stmt = select(ReflexRule)
            if source is not None:
                stmt = stmt.where(ReflexRule.source == source)
            stmt = stmt.order_by(ReflexRule.created_at.desc())  # type: ignore[attr-defined]
            return list(db.exec(stmt))

    def get(self, rule_id: str) -> ReflexRule | None:
        with session_scope(self.engine) as db:
            return db.get(ReflexRule, rule_id)

    def add(
        self,
        *,
        name: str,
        source: str,
        match: str,
        action: str,
        target: str = "",
        task_template: str = "",
        enabled: bool = True,
        project_id: str | None = None,
        start: dict | None = None,
        agent_name: str = "",
        workspace_root: str = "",
        chat_thread_id: str = "",
        max_fires_per_hour: int | None = None,
    ) -> ReflexRule:
        """Leap 05-a: ``start`` (a file rule's folder, a download rule's site),
        ``agent_name``, ``workspace_root``, ``chat_thread_id`` and
        ``max_fires_per_hour`` (12 when not given, D-05.3)."""
        rule = ReflexRule(
            name=name.strip() or match.strip() or "reflex",
            source=source,
            match=match.strip(),
            action=action,
            target=target.strip(),
            task_template=task_template,
            enabled=enabled,
            # Context spine: empty/whitespace normalizes to None (ungrounded)
            # so "" never masquerades as a project id downstream.
            project_id=(project_id or "").strip() or None,
            start_json=json.dumps(start, default=str) if start else "",
            agent_name=str(agent_name or "").strip(),
            workspace_root=str(workspace_root or "").strip(),
            chat_thread_id=str(chat_thread_id or "").strip(),
            max_fires_per_hour=_limit(max_fires_per_hour),
        )
        with session_scope(self.engine) as db:
            db.add(rule)
            db.commit()
            db.refresh(rule)
        return rule

    def remove(self, rule_id: str) -> bool:
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
        # Leap 05-a: what goes with it (its watcher, its standing OKs).
        hook = _ON_REMOVED.get(self.engine)
        if hook is not None:
            try:
                hook(rule_id)
            except Exception:  # noqa: BLE001 — the rule is gone either way
                log.warning("could not tidy up after the rule %s", rule_id, exc_info=True)
        return True

    def set_project(self, rule_id: str, project_id: str | None) -> ReflexRule | None:
        """Re-ground (or un-ground) a rule. ``None``/empty clears the project.

        The CALLER decides whether an omitted field means "unchanged" — this
        method always assigns (same shape as :meth:`set_enabled`)."""
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return None
            row.project_id = (project_id or "").strip() or None
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    #: The fields :meth:`update` may change (v1.449.0, Wave D item 34). The
    #: id, the counters and the last fire's record are never a caller's.
    EDITABLE = (
        "name", "source", "match", "action", "target", "task_template", "project_id",
        # Leap 05-a. ``chat_thread_id`` is changed only through
        # ``chat_delivery.set_report_target`` (idea 39's one writer).
        "start_json", "agent_name", "workspace_root", "max_fires_per_hour",
    )

    def update(self, rule_id: str, **fields: Any) -> ReflexRule | None:
        """PARTIAL edit (v1.449.0, Wave D item 34): only the keys given change;
        an omitted field keeps its stored value (the PATCH /schedules rule).
        ``name`` / ``match`` / ``target`` are stripped; ``project_id`` ""
        clears the grounding. The CALLER validates the merged record (the
        route runs the add-time rules over it). None when the rule is gone;
        a key outside :data:`EDITABLE` is a ``ValueError`` — a counter or the
        last fire's record must never be written by an edit."""
        bad = [k for k in fields if k not in self.EDITABLE]
        if bad:
            raise ValueError(f"not an editable field: {', '.join(sorted(bad))}")
        from .limits import LIMITS_FIELDS, decoded as _kept_limits

        dropped: list[str] | None = None
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return None
            before = {k: _limits_value(row, k) for k in LIMITS_FIELDS}
            for key, value in fields.items():
                if key == "project_id":
                    row.project_id = (value or "").strip() or None
                elif key == "max_fires_per_hour":
                    row.max_fires_per_hour = _limit(value)
                elif key == "start_json":
                    row.start_json = value if isinstance(value, str) else (
                        json.dumps(value, default=str) if value else ""
                    )
                elif key in ("name", "match", "target", "agent_name", "workspace_root"):
                    setattr(row, key, str(value or "").strip())
                else:
                    setattr(row, key, str(value or ""))
            if not row.name:
                row.name = row.match or "reflex"
            # Leap 05-c: the Limits were approved for what the rule did and
            # where it worked; an edit of either drops them (rehearse it
            # again to keep new ones). Its name and hourly limit keep them.
            kept = _kept_limits(row)
            if kept and any(before[k] != _limits_value(row, k) for k in LIMITS_FIELDS):
                dropped = list(kept.get("grants") or [])
                row.limits_json = ""
            db.add(row)
            db.commit()
            db.refresh(row)
        if dropped is not None:
            hook = _ON_LIMITS_DROPPED.get(self.engine)
            if hook is not None:
                try:
                    hook(rule_id, dropped)
                except Exception:  # noqa: BLE001 — the Limits are gone either way
                    log.warning("could not revoke the kept OKs of the rule %s", rule_id, exc_info=True)
        return row

    def set_enabled(self, rule_id: str, enabled: bool) -> ReflexRule | None:
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return None
            row.enabled = enabled
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    # -- the hourly limit (leap 05-a, D-05.3) ------------------------------
    def start_gate(
        self, rule_id: str, *, now: datetime | None = None, reserve: bool = False
    ) -> dict[str, Any]:
        """May the rule start now? ``{"ok": True}``, or ``{"ok": False,
        "paused": True, "tripped": bool, "words": ...}`` when it is paused:
        ``tripped`` when THIS signal is the one that found it at its hourly
        limit (the row is paused now, ``paused_at``/``paused_reason``). An
        unknown id (the manual ``start`` path's unpersisted rule) may start.

        ``reserve`` (review): an ``ok`` answer also COUNTS the start in the
        same locked read-and-write, and carries its ``slot`` — so signals
        that arrive together (a burst of webhooks, several downloads) can
        never all pass the check before any of them is counted. A start that
        then fails gives its slot back (:meth:`release_start`). BLOCKING."""
        when = _aware(now) or utcnow()
        with _GATE_LOCK, session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return {"ok": True}
            limit = _limit(row.max_fires_per_hour)
            if row.paused_at is not None:
                return {"ok": False, "paused": True, "tripped": False,
                        "words": row.paused_reason or pause_words(limit)}
            kept = _recent(row.recent_starts_json, when)
            if len(kept) < limit:
                if not reserve:
                    return {"ok": True}
                kept.append(when)
                row.recent_starts_json = json.dumps([k.isoformat() for k in kept[-200:]])
                db.add(row)
                db.commit()
                return {"ok": True, "slot": when.isoformat()}
            row.paused_at = when
            row.paused_reason = pause_words(limit)
            db.add(row)
            db.commit()
            return {"ok": False, "paused": True, "tripped": True, "words": row.paused_reason}

    def release_start(self, rule_id: str, slot: str) -> None:
        """Give back a start :meth:`start_gate` reserved (``slot``) that did
        not happen: a failed start is not counted. BLOCKING."""
        if not slot:
            return
        with _GATE_LOCK, session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return
            try:
                items = json.loads(row.recent_starts_json or "[]")
            except (TypeError, ValueError):
                return
            if not isinstance(items, list) or slot not in items:
                return
            items.remove(slot)
            row.recent_starts_json = json.dumps(items)
            db.add(row)
            db.commit()

    def note_start(self, rule_id: str, *, now: datetime | None = None) -> None:
        """Count one START toward the hourly limit (a failed start is not
        one). BLOCKING."""
        when = _aware(now) or utcnow()
        with _GATE_LOCK, session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return
            kept = _recent(row.recent_starts_json, when)
            kept.append(when)
            row.recent_starts_json = json.dumps([k.isoformat() for k in kept[-200:]])
            db.add(row)
            db.commit()

    def note_told(self, rule_id: str, *, now: datetime | None = None) -> bool:
        """Mark the pause as SAID now, unless it was already said today (the
        cap: once a day per rule, local day). True when it may be said.
        BLOCKING."""
        when = _aware(now) or utcnow()
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return False
            told = _aware(row.pause_told_at)
            if told is not None and told.astimezone().date() == when.astimezone().date():
                return False
            row.pause_told_at = when
            db.add(row)
            db.commit()
            return True

    def resume(self, rule_id: str) -> ReflexRule | None:
        """Resume a paused rule (the Resume press): the pause and the last
        hour's count are cleared, and the rule is on. None when absent."""
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return None
            row.paused_at = None
            row.paused_reason = None
            row.recent_starts_json = ""
            row.enabled = True
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def keep_paused(self, rule_id: str) -> ReflexRule | None:
        """Keep it paused (the notice's stop answer): the rule is switched
        OFF, so it starts nothing and says nothing until it is turned on
        again. None when absent."""
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return None
            row.paused_at = None
            row.paused_reason = None
            row.recent_starts_json = ""
            row.enabled = False
            db.add(row)
            db.commit()
            db.refresh(row)
        return row

    def clear_stop(self, rule_id: str) -> None:
        """Leap 05-c: the row speaks of the LAST run, so a new start takes
        an earlier run's "Needs you" (it stopped at its Limits) away. Called
        BEFORE the run is launched, so the new run's own stop is never wiped.
        BLOCKING."""
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None or not row.limits_stop_json:
                return
            row.limits_stop_json = ""
            db.add(row)
            db.commit()

    def mark_fired(self, rule_id: str) -> None:
        with _GATE_LOCK, session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return
            row.fire_count = (row.fire_count or 0) + 1
            row.last_fired_at = utcnow()
            db.add(row)
            db.commit()

    def mark_result(
        self, rule_id: str, *, ok: bool, detail: str = "", href: str = ""
    ) -> None:
        """Record what the last matched signal did (v1.231.0, audit AE6).

        A successful start counts as a fire (``fire_count``/``last_fired_at``,
        as :meth:`mark_fired`) and clears ``last_error``; a failed start writes
        the reason to ``last_error`` and touches nothing else — a fire that did
        not happen is not counted as one. Unknown ids (the manual ``start``
        path's unpersisted rule) are a no-op. Leap 05-a (review): under the
        rule's counter lock, so starts that finish together are each
        counted."""
        with _GATE_LOCK, session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return
            if ok:
                row.fire_count = (row.fire_count or 0) + 1
                row.last_fired_at = utcnow()
                row.last_result = (detail or "")[:500] or None
                # Readiness 0j.5 item 15: where that start can be seen.
                row.last_href = (href or "")[:300] or None
                row.last_error = None
            else:
                row.last_error = (detail or "failed")[:500]
            db.add(row)
            db.commit()

    # -- matching ----------------------------------------------------------
    def matching_webhook(self, slug: str) -> list[ReflexRule]:
        """Enabled webhook rules whose ``match`` equals ``slug`` exactly."""
        return [
            r
            for r in self.list(source="webhook")
            if r.enabled and r.match == slug
        ]

    def matching(self, source: str, text: str) -> list[ReflexRule]:
        """Enabled rules of ``source`` whose keyword appears as a whole word in
        ``text`` (an empty keyword matches every signal). Case-insensitive.

        The generic keyword matcher for every text-carrying source
        (comm/email/calendar/slack). Webhooks use exact-slug matching instead
        (:meth:`matching_webhook`)."""
        low = (text or "").lower()
        out: list[ReflexRule] = []
        for r in self.list(source=source):
            if not r.enabled:
                continue
            kw = r.match.strip().lower()
            if not kw:
                out.append(r)
            elif re.search(rf"\b{re.escape(kw)}\b", low):
                out.append(r)
        return out

    def matching_download(self, name: str, site: str) -> list[ReflexRule]:
        """Leap 05-a: enabled ``download`` rules for a finished download:
        the keyword (``match``) is looked for in the file's NAME, and a rule
        that names a site (``start_json.match_site``) hears only downloads
        from that site (:func:`site_matches`)."""
        out: list[ReflexRule] = []
        for r in self.matching("download", name):
            want = str(r.decoded_start().get("match_site") or "").strip()
            if not want or site_matches(site, want):
                out.append(r)
        return out

    def matching_run_ended(self, ending: dict[str, Any]) -> list[ReflexRule]:
        """Leap 05-b: enabled ``run_ended`` rules that listen for ``ending``
        (a ``reflex/router.run_ended_context``): its kind (``what``; a
        ``rule`` rule hears any run a rule started, named by that rule), its
        name (``name``, "" = any of that kind; one line, case ignored) and
        how it ended (``outcome``; ``any`` = any way it fell short). Review 2:
        a schedule's fire also names what it ran (``ended_workflow``, the
        custom agent ``ended_agent``), so a rule for that workflow or agent
        hears the fire too, from its ONE signal. The loop guards are the
        router's (``on_run_ended``)."""
        kind = str(ending.get("ended_kind") or "")
        name = _flat(ending.get("ended_name"))
        by_rule = str(ending.get("by_rule_id") or "")
        by_name = _flat(ending.get("by_rule_name"))
        verdict = str(ending.get("ended_verdict") or "")
        # What the ended task ran, by kind: its own kind and name first.
        ran = {kind: name} if kind in ("workflow", "agent") else {}
        if kind == "schedule":
            for key, of in (("workflow", "ended_workflow"), ("agent", "ended_agent")):
                if _flat(ending.get(of)):
                    ran[key] = _flat(ending.get(of))
        out: list[ReflexRule] = []
        for r in self.list(source="run_ended"):
            if not r.enabled:
                continue
            start = r.decoded_start()
            if not outcome_matches(str(start.get("outcome") or RUN_ENDED_DEFAULT_OUTCOME), verdict):
                continue
            what = str(start.get("what") or "any")
            want = _flat(start.get("name"))
            if what == "rule":
                if not by_rule or (want and want != by_name):
                    continue
            elif what == "any":
                if want and want not in {name, by_name, *ran.values()}:
                    continue
            elif what in ran:
                if want and want != ran[what]:
                    continue
            elif what != kind or (want and want != name):
                continue
            out.append(r)
        return out

    def amend_result(self, rule_id: str, said: str, now_says: str) -> None:
        """Review 2 (05-b): change what the row's last start says, after the
        fact, without counting a fire: ``said`` (a part of ``last_result``)
        becomes ``now_says``, or ``now_says`` is added when ``said`` is not
        there. Unknown ids are a no-op."""
        with _GATE_LOCK, session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return
            last = str(row.last_result or "")
            if said and said in last:
                text = last.replace(said, now_says)
            else:
                text = " ".join(x for x in (last, now_says) if x)
            row.last_result = text[:500] or None
            db.add(row)
            db.commit()

    def matching_comm(self, text: str) -> list[ReflexRule]:
        """Enabled comm rules matching ``text`` (thin alias over :meth:`matching`,
        kept for the existing callers)."""
        return self.matching("comm", text)


#: Leap 05-b: the verdicts ``any`` covers (every way a task falls short).
SHORT_VERDICTS = ("failed", "part_failed", "needs_you", "did_nothing")


def outcome_matches(want: str, verdict: str) -> bool:
    """Does a run that ended with ``verdict`` meet a rule's ``outcome``?
    ``any`` is any way it fell short; a task that was done never does."""
    if verdict not in SHORT_VERDICTS:
        return False
    return want == "any" or want == verdict


def _flat(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _limits_value(row: ReflexRule, key: str) -> Any:
    """One field as leap 05-c compares it: ``start_json`` by what it says (a
    re-written dict in another key order is the same start), the rest by
    their stripped text (None and "" are the same)."""
    value = getattr(row, key, None)
    if key == "start_json":
        try:
            data = json.loads(value or "{}")
        except (TypeError, ValueError):
            return str(value or "")
        return json.dumps(data, sort_keys=True, default=str) if data else ""
    return " ".join(str(value or "").split())


def _limit(value: Any) -> int:
    """The hourly limit as a whole number, 1..120 (12 when unset or junk)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return MAX_FIRES_PER_HOUR
    if n < 1:
        return MAX_FIRES_PER_HOUR
    return min(n, 120)


def site_matches(site: str, want: str) -> bool:
    """Does a download's site (``scheme://host/path``, never a query) match
    what a rule names: a host ("irs.gov" hears "www.irs.gov" too) or a host
    and the start of a path ("example.com/reports")? Case-insensitive."""
    from urllib.parse import urlsplit

    want = (want or "").strip().lower()
    if not want:
        return True
    if "://" in want:
        want = want.split("://", 1)[1]
    try:
        parts = urlsplit(str(site or ""))
    except Exception:  # noqa: BLE001
        return False
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    w_host, _, w_path = want.partition("/")
    if not host or not w_host:
        return False
    if host != w_host and not host.endswith("." + w_host):
        return False
    return not w_path or path.lower().lstrip("/").startswith(w_path.lower())
