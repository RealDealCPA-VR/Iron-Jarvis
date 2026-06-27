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


class SentinelService:
    """Persistent registry of always-on, suggest-only watchers."""

    def __init__(self, engine: Engine, *, scanner: Scanner | None = None) -> None:
        self.engine = engine
        # Injectable for deterministic offline tests; defaults to a real stat scan.
        self.scanner: Scanner = scanner or default_scanner
        # Serialize check() so the background tick + a manual /sentinels/poll
        # (both run on threads) can't double-read last_state for one sentinel.
        self._lock = threading.Lock()

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
    ) -> SentinelRecord:
        """Persist a new watcher. Raises ``ValueError`` on a bad kind / dup name.

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
        if kind == "file" and not cfg.get("path"):
            raise ValueError("file sentinel requires a 'path'")
        if kind == "file":
            # Reject a protected/out-of-allowlist path up front (same fs_policy
            # every reader honors) and validate the glob so a bad pattern is a
            # clear error, not a silently-inert watcher.
            wpath = str(cfg.get("path") or "")
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
            except Exception as exc:  # noqa: BLE001 — surface a bad glob/path now
                raise ValueError(f"invalid watch path/glob: {exc}") from exc

        rec = SentinelRecord(
            name=name,
            kind=kind,
            config_json=json.dumps(cfg, default=str),
            task=task or "",
            agent_type=agent_type or "builder",
            risk=risk if risk in ("low", "med", "high") else "low",
            enabled=enabled,
        )
        with session_scope(self.engine) as db:
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
            except Exception:  # noqa: BLE001 — a bad scan must never break the tick
                log.exception("sentinel %s scan failed", name)
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
                continue
            proposal = self._propose(intent, rec, changed)
            if proposal is not None:
                created.append(proposal)
        return created

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
        title = f"Sentinel '{rec.name}' noticed file changes"
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
            )
        except Exception:  # noqa: BLE001 — a mint failure must not break the tick
            log.exception("sentinel %s could not mint a proposal", rec.name)
            return None
