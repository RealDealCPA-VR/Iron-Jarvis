"""SentinelService — durable registry of always-on watchers (mirrors Scheduler).

This is the persistent registry that makes Sentinels real: enabled watchers
survive restarts and rehydrate their last-seen state, so a daemon tick keeps
diffing from where it left off (it never re-fires for changes already observed).

Like :class:`~iron_jarvis.scheduling.service.Scheduler`, it owns the shared
``engine`` and exposes ``add``/``list``/``get``/``remove``/``set_enabled``/
``load`` over :class:`SentinelRecord`. Two extra methods drive the watch loop:

  * :meth:`check` — scan ONE sentinel (via an injectable scanner), durably update
    its seen state + ``last_checked_at``, and return the changed files.
  * :meth:`poll_once` — check every enabled sentinel and, for any with changes,
    mint exactly ONE SUGGEST-ONLY proposal into the Motivation Layer backlog
    (``platform.intent.add_backlog``, ``source="sentinel"``). It NEVER spawns a
    session — execution still flows through the autonomy dial + budget + approval.

The very FIRST observation of a sentinel (``last_checked_at is None``) is treated
as a BASELINE: it records the current snapshot and fires nothing. This is the
conservative, anti-spam choice — pre-existing files (and everything present when a
brand-new watcher is created) count as "already seen", so enabling a Sentinel
never floods the backlog on the first tick.
"""

from __future__ import annotations

import json
from pathlib import Path
import os
import threading

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.fs_policy import fs_path_allowed, is_protected_path
from ..core.ids import utcnow
from ..core.logging import get_logger
from .models import KINDS, SentinelRecord
from .watcher import Scanner, default_scanner, diff_state

log = get_logger("sentinels")


#: AN review (v1.449.0): a watch path on a network location, refused by text.
NETWORK_WATCH = (
    "That path is on a network location, so a watcher cannot watch it. "
    "Pick a folder on this PC."
)


class SentinelService:
    """Persistent registry of always-on, suggest-only watchers."""

    def __init__(self, engine: Engine, *, scanner: Scanner | None = None) -> None:
        self.engine = engine
        # Injectable for deterministic offline tests; defaults to a real stat scan.
        self.scanner: Scanner = scanner or default_scanner
        # Serialize check() so the background tick + a manual /sentinels/poll
        # (both run on threads) can't double-read last_state for one sentinel.
        self._lock = threading.Lock()
        # Idea 39 (39-a): the bus a watcher's chat report announces itself on
        # (``chat.thread_updated``); the platform sets it. None = the report
        # still lands, the open page sees it on its next read.
        self.event_bus = None
        # The clock the hourly pacing reads (D-39.3); a test injects one.
        self.clock = utcnow

    # --- persistence helpers ---------------------------------------------

    def _fetch(self, db, name: str) -> SentinelRecord | None:
        return db.exec(
            select(SentinelRecord).where(SentinelRecord.name == name)
        ).first()

    def get(self, name: str) -> SentinelRecord | None:
        """Return the persisted sentinel named ``name`` (or None)."""
        with session_scope(self.engine) as db:
            return self._fetch(db, name)

    def list(self) -> list[SentinelRecord]:
        """Return all persisted sentinels, oldest first."""
        with session_scope(self.engine) as db:
            return list(
                db.exec(select(SentinelRecord).order_by(SentinelRecord.created_at))
            )

    def load(self) -> list[SentinelRecord]:
        """Rehydrate the registry on boot (no-op read; state lives in the DB).

        Mirrors ``Scheduler.start`` / ``DynamicAgentRegistry.load`` as the boot
        hook. Nothing in-memory needs rebuilding (the seen state is persisted on
        each :class:`SentinelRecord`), so this simply returns the current rows;
        the daemon loop then resumes diffing from each sentinel's last_state.
        """
        return self.list()

    # --- mutation ---------------------------------------------------------

    def add(
        self,
        name: str,
        *,
        path: str | None = None,
        glob: str | None = None,
        task: str = "",
        kind: str = "file",
        agent_type: str = "builder",
        risk: str = "low",
        enabled: bool = True,
        config: dict | None = None,
        chat_thread_id: str = "",
    ) -> SentinelRecord:
        """Persist a new watcher. Raises ``ValueError`` on a bad kind / dup name.

        ``chat_thread_id`` (idea 39, 39-a): the chat it was set up from; what it
        notices is reported there, at most once an hour (D-39.3).

        For ``kind="file"`` supply ``path`` (a file, directory, or glob) and an
        optional ``glob`` pattern relative to it. Adding a sentinel NEVER scans or
        fires — the first :meth:`check` establishes the baseline.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown sentinel kind {kind!r}; expected one of {KINDS}")
        name = (name or "").strip()
        if not name:
            raise ValueError("sentinel name is required")
        if self.get(name) is not None:
            raise ValueError(f"sentinel {name!r} already exists")

        cfg = dict(config or {})
        if path is not None:
            cfg["path"] = path
        if glob is not None:
            cfg["glob"] = glob
        self._check_config(kind, cfg)

        rec = SentinelRecord(
            name=name,
            kind=kind,
            config_json=json.dumps(cfg, default=str),
            task=task or "",
            agent_type=agent_type or "builder",
            risk=risk if risk in ("low", "med", "high") else "low",
            enabled=enabled,
            chat_thread_id=str(chat_thread_id or "").strip(),
        )
        with session_scope(self.engine) as db:
            db.add(rec)
            db.commit()
            db.refresh(rec)
        return rec

    def _check_config(self, kind: str, cfg: dict) -> None:
        """The add-time watch-spec rules, shared by :meth:`add` and
        :meth:`update` (v1.449.0, Wave D item 34) so an edit can never land a
        spec the add would have refused. ``ValueError`` in words."""
        if kind == "file" and not cfg.get("path"):
            raise ValueError("file sentinel requires a 'path'")
        if kind == "file":
            # Reject a protected/out-of-allowlist path up front (same fs_policy
            # every reader honors) and validate the glob so a bad pattern is a
            # clear error, not a silently-inert watcher.
            wpath = str(cfg.get("path") or "")
            # AN review (v1.449.0): a network or device spelling is refused by
            # its TEXT before the policy check or the scanner looks at it —
            # either one stats the path, and a stat of ``\\host\share``
            # is an SMB connection (the v1.379.0 lesson; ``sentinel_add`` is a
            # chat tool, so the path can be model-written).
            from ..harness.sessions import _network_spelling

            if _network_spelling(os.path.expanduser(wpath)):
                raise ValueError(NETWORK_WATCH)
            probe = os.path.expanduser(wpath)
            for ch in "*?[":
                cut = probe.find(ch)
                if cut != -1:
                    probe = probe[:cut]
            probe = probe or os.path.expanduser(wpath)
            if probe and (is_protected_path(probe) or not fs_path_allowed(probe)):
                raise ValueError(
                    f"watch path {wpath!r} is protected or outside the allowed roots"
                )
            try:
                self.scanner(wpath, cfg.get("glob"))
            except FileNotFoundError:
                # v1.231.0 (AE7): a root that is not there YET (unplugged
                # drive, folder created later) is a legal watch — the row
                # says "root unreachable" until it appears, then baselines.
                pass
            except Exception as exc:  # noqa: BLE001 — surface a bad glob/path now
                raise ValueError(f"invalid watch path/glob: {exc}") from exc

    #: What :meth:`update` may change. The name is the row's key (the page
    #: and the chat tools refer to a watcher by name), ``kind`` would change
    #: what the config means, and the seen state is the watcher's own.
    EDITABLE = ("path", "glob", "task", "agent_type", "risk", "enabled")

    def update(self, name: str, **fields) -> SentinelRecord | None:
        """PARTIAL edit (v1.449.0, Wave D item 34): only the keys given change,
        an omitted one keeps its stored value; ``glob`` "" / None removes the
        pattern. A changed watch spec passes :meth:`_check_config` BEFORE the
        row is written and RESETS the seen state (the next check records a
        new baseline for the new spec — never a flood of "new" files from a
        folder it never watched). None when absent; a key outside
        :data:`EDITABLE` is a ``ValueError``."""
        bad = [k for k in fields if k not in self.EDITABLE]
        if bad:
            raise ValueError(f"not an editable field: {', '.join(sorted(bad))}")
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return None
            cfg = rec.decoded_config()
            spec_changed = False
            if "path" in fields:
                new_path = str(fields["path"] or "").strip()
                if new_path != str(cfg.get("path") or ""):
                    cfg["path"] = new_path
                    spec_changed = True
            if "glob" in fields:
                new_glob = str(fields["glob"] or "").strip()
                if new_glob != str(cfg.get("glob") or ""):
                    if new_glob:
                        cfg["glob"] = new_glob
                    else:
                        cfg.pop("glob", None)
                    spec_changed = True
            if spec_changed:
                self._check_config(rec.kind, cfg)
                rec.config_json = json.dumps(cfg, default=str)
                rec.last_state_json = "{}"
                rec.last_checked_at = None
                rec.last_error = None
            if "task" in fields:
                rec.task = str(fields["task"] or "")
            if "agent_type" in fields and str(fields["agent_type"] or "").strip():
                rec.agent_type = str(fields["agent_type"]).strip()
            if "risk" in fields:
                risk = str(fields["risk"] or "")
                rec.risk = risk if risk in ("low", "med", "high") else rec.risk
            if "enabled" in fields and fields["enabled"] is not None:
                rec.enabled = bool(fields["enabled"])
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec

    def remove(self, name: str) -> bool:
        """Delete a sentinel. Returns False if absent."""
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return False
            db.delete(rec)
            db.commit()
        return True

    def set_enabled(self, name: str, enabled: bool) -> SentinelRecord | None:
        """Toggle a sentinel's ``enabled`` flag. None if absent."""
        with session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return None
            rec.enabled = enabled
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec

    # --- watching ---------------------------------------------------------

    def check(
        self, name: str, *, scanner: Scanner | None = None
    ) -> list[dict]:
        """Scan ONE sentinel, persist its new seen-state, return changed files.

        The first observation (``last_checked_at is None``) records the current
        snapshot as the BASELINE and returns ``[]`` (nothing fires). Subsequent
        checks diff against the persisted state. Always durable, never raises on a
        single bad scan (returns ``[]`` and still stamps ``last_checked_at``).
        """
        scan = scanner or self.scanner
        with self._lock, session_scope(self.engine) as db:
            rec = self._fetch(db, name)
            if rec is None:
                return []
            cfg = rec.decoded_config()
            try:
                current = scan(cfg.get("path", ""), cfg.get("glob"))
            except Exception as exc:  # noqa: BLE001 — a bad scan must never break the tick
                # v1.231.0 (audit AE7): the OLD baseline is kept untouched, and
                # the row says why on ``last_error`` — "root unreachable since
                # <t>" for a vanished folder (the "since" is the FIRST failure,
                # kept across ticks), else the scan error. When the drive is
                # back the diff runs against the memory it had, so a replug
                # proposes nothing for untouched files.
                if isinstance(exc, FileNotFoundError):
                    log.warning("sentinel %s: %s", name, exc)
                    if not (rec.last_error or "").startswith("root unreachable since "):
                        rec.last_error = f"root unreachable since {utcnow().isoformat()}"
                else:
                    log.exception("sentinel %s scan failed", name)
                    rec.last_error = f"scan failed: {type(exc).__name__}: {exc}"[:500]
                # Don't CONSUME the baseline on a transient first-scan failure: if
                # last_checked_at is still None, leave it None so the next tick
                # retries the baseline (else previous={} next time would flood the
                # backlog with every pre-existing file as "new").
                if rec.last_checked_at is not None:
                    rec.last_checked_at = utcnow()
                db.add(rec)
                db.commit()
                return []

            first = rec.last_checked_at is None
            previous = rec.decoded_state().get("seen", {})
            changed = [] if first else diff_state(previous, current)

            rec.last_state_json = json.dumps({"seen": current}, default=str)
            rec.last_checked_at = utcnow()
            rec.last_error = None
            db.add(rec)
            db.commit()
        return changed

    def poll_once(self, intent, *, scanner: Scanner | None = None) -> list:
        """Check every enabled sentinel; mint one suggest-only proposal per match.

        Returns the list of created :class:`ProposalRecord`s (possibly empty).
        Each fired sentinel mints exactly ONE backlog proposal via
        ``intent.add_backlog`` (``source="sentinel"``) — it NEVER spawns a
        session. A single bad sentinel never aborts the sweep.
        """
        created: list = []
        for rec in self.list():
            if not rec.enabled:
                continue
            try:
                changed = self.check(rec.name, scanner=scanner)
            except Exception:  # noqa: BLE001 — keep sweeping the rest
                log.exception("sentinel %s check failed", rec.name)
                continue
            if not changed:
                # Idea 39 (D-39.3): changes held back by the hour go out once
                # the hour is up, even when nothing new came.
                self.report_to_chat(rec, [])
                continue
            proposal = self._propose(intent, rec, changed)
            if proposal is not None:
                created.append(proposal)
            self.report_to_chat(rec, changed, suggested=proposal is not None)
        return created

    #: Idea 39 (39-a): the most paths a watcher keeps waiting for its next report.
    REPORT_PENDING_MAX = 200

    def report_to_chat(self, rec: SentinelRecord, changed: list[dict], *, suggested: bool = False) -> dict:
        """Idea 39 (39-a): a watcher set up from a chat says what it noticed
        there: "3 new or changed files in Intake: a.pdf, b.pdf, c.pdf." with
        the suggestion it left. At most ONE report an hour (D-39.3): changes
        noticed sooner wait on the row (``report_pending_json``) and join the
        next report. ``suggested`` (review): this poll left a suggestion that
        waits for approval; only then does the report say so (a held-back
        report, or a suggestion that could not be made, says nothing of one).
        BLOCKING (the poll runs on a worker thread). Returns ``{"reported":
        bool, "waiting": int}``; never raises."""
        from ..agents import chat_delivery as _cd

        out = {"reported": False, "waiting": 0}
        chat = _cd.clean_chat_thread_id(getattr(rec, "chat_thread_id", "") or "")
        if not chat:
            return out
        try:
            now = self.clock()
            with self._lock, session_scope(self.engine) as db:
                row = db.get(SentinelRecord, rec.id)
                if row is None:
                    return out
                try:
                    pending = json.loads(row.report_pending_json or "[]")
                except (TypeError, ValueError):
                    pending = []
                pending = [str(p) for p in (pending if isinstance(pending, list) else []) if p]
                for c in changed or []:
                    path = str((c or {}).get("path") or "")
                    if path and path not in pending:
                        pending.append(path)
                pending = pending[-self.REPORT_PENDING_MAX:]
                last = row.report_at
                if last is not None and getattr(last, "tzinfo", None) is None and getattr(now, "tzinfo", None):
                    last = last.replace(tzinfo=now.tzinfo)
                due = bool(pending) and (
                    last is None or (now - last).total_seconds() >= _cd.WATCHER_REPORT_GAP_S
                )
                if not due:
                    row.report_pending_json = json.dumps(pending)
                    db.add(row)
                    db.commit()
                    out["waiting"] = len(pending)
                    return out
                row.report_pending_json = ""
                row.report_at = now
                db.add(row)
                db.commit()
            names = [Path(p).name or p for p in pending]
            shown = ", ".join(names[:10]) + (f" and {len(names) - 10} more" if len(names) > 10 else "")
            n = len(names)
            words = f"{n} new or changed file{'' if n == 1 else 's'} in {rec.name}: {shown}."
            if suggested:
                suggestion = " ".join(str(rec.task or "a look at what changed").split()).rstrip(".")
                words += f" Suggested: {suggestion}. It waits for your approval on the Autonomy page."
            report = {
                "kind": "watcher", "name": rec.name, "id": rec.id, "href": "/autonomy",
                "outcome": "Noticed", "words": words,
                "key": f"watcher:{rec.id}:{now.isoformat()}",
            }
            platform = type("_P", (), {"engine": self.engine, "event_bus": self.event_bus})()
            _cd.report_to_chat(platform, chat, report)
            out["reported"] = True
            return out
        except Exception:  # noqa: BLE001 — a report never breaks the sweep
            log.warning("watcher %s could not report to its chat", getattr(rec, "name", "?"), exc_info=True)
            return out

    @staticmethod
    def _propose(intent, rec: SentinelRecord, changed: list[dict]):
        """Mint the SUGGEST-ONLY backlog proposal for a fired sentinel."""
        if intent is None:
            return None
        sample = ", ".join(c["path"] for c in changed[:5])
        more = "" if len(changed) <= 5 else f" (+{len(changed) - 5} more)"
        # STABLE title (no count) so every change for this sentinel folds into ONE
        # pending proposal (add_backlog refreshes its rationale) instead of either
        # spamming a new proposal per count or silently swallowing changes.
        title = f"Watcher '{rec.name}' noticed file changes"
        task = rec.task or (
            f"The '{rec.name}' watcher noticed file changes. Review what changed "
            "and summarise whether any follow-up is needed; take no action yet."
        )
        rationale = f"Changed: {sample}{more}."
        try:
            return intent.add_backlog(
                title=title,
                task=task,
                risk=rec.risk,
                source="sentinel",
                agent_type=rec.agent_type,
                rationale=rationale,
                # v1.446.0 (ease-of-use review item 21): the watcher's name
                # rides the ``autonomy.proposed`` a NEW suggestion publishes,
                # so the bell says "Your watcher X noticed changes · Review".
                producer=rec.name,
            )
        except Exception:  # noqa: BLE001 — a mint failure must not break the tick
            log.exception("sentinel %s could not mint a proposal", rec.name)
            return None
