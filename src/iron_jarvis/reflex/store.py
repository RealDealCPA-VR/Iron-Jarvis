"""Durable CRUD + matching for :class:`ReflexRule` rows.

Pure persistence — no execution, no orchestrator. The :class:`ReflexRouter`
owns *running* a matched rule; this store owns *finding* and *managing* them, so
it is cheap to build (just an engine) and safe to expose on the platform.
"""

from __future__ import annotations

import re

from sqlalchemy import Engine
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from .models import ReflexRule


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
    ) -> ReflexRule:
        rule = ReflexRule(
            name=name.strip() or match.strip() or "reflex",
            source=source,
            match=match.strip(),
            action=action,
            target=target.strip(),
            task_template=task_template,
            enabled=enabled,
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
        return True

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

    def mark_fired(self, rule_id: str) -> None:
        with session_scope(self.engine) as db:
            row = db.get(ReflexRule, rule_id)
            if row is None:
                return
            row.fire_count = (row.fire_count or 0) + 1
            row.last_fired_at = utcnow()
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

    def matching_comm(self, text: str) -> list[ReflexRule]:
        """Enabled comm rules matching ``text`` (thin alias over :meth:`matching`,
        kept for the existing callers)."""
        return self.matching("comm", text)
