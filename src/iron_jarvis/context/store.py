"""Where a compaction is remembered so it is paid for ONCE (v1.153.0).

Chat history is CLIENT-owned: the browser holds the thread and posts the whole
message list on every turn (``ChatBody.messages``). Without persistence the
daemon would have to re-summarize the same prefix on every single turn once a
conversation crossed the threshold — one extra model call per keystroke-cycle,
forever. So the summary is stored and reused.

CONTENT-ADDRESSED, NOT THREAD-KEYED. The row id is a hash of the exact messages
the summary covers (:func:`~.compaction.prefix_key`), which falls out of three
facts about this app: ``ChatBody`` carries no thread id at all; an unsaved chat
has no id to carry; and a forked or regenerated thread shares its parent's
prefix, so it inherits the parent's summary instead of paying for the identical
call again. Two conversations with byte-identical openings SHOULD share one
compaction — the summary is a pure function of what it covers.

The transcript itself is never touched. This table holds a derived, discardable
view; deleting every row costs nothing but a recomputation.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, Session, SQLModel, select

from ..core.ids import utcnow


class CompactionRecord(SQLModel, table=True):
    """One verified summary of one exact message prefix.

    A plain new SQLModel table — nothing here for the additive-column
    reconciler. It IS created lazily, so ``context.store`` is registered in
    ``core.db._LATE_MODEL_MODULES``: a table created only by
    ``__table__.create(checkfirst=True)`` is invisible to the reconciler, which
    is precisely how v1.151.2 shipped a column that existed on every fresh test
    database and on no real install.
    """

    #: sha256 of the covered messages — see :func:`compaction.prefix_key`.
    id: str = Field(primary_key=True)
    #: The rendered, VERIFIED summary (already headed).
    summary: str = ""
    #: How many leading messages this replaces.
    covers: int = 0
    #: Lines the ledger/transcript check removed from the model's draft.
    stripped: int = 0
    #: "manual" (the user chose it) | "auto" (the ceiling forced it).
    trigger: str = "auto"
    provider: str = ""
    model: str = ""
    #: The agent session this came from, when it came from a run ("" for chat).
    session_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class CompactionStore:
    """Read/write compactions. Never raises on the read path."""

    def __init__(self, engine) -> None:
        self.engine = engine
        try:
            CompactionRecord.__table__.create(engine, checkfirst=True)
        except Exception:  # noqa: BLE001 — a missing cache must not break boot
            pass

    def get(self, key: str) -> CompactionRecord | None:
        if not key:
            return None
        try:
            with Session(self.engine) as db:
                return db.get(CompactionRecord, key)
        except Exception:  # noqa: BLE001 — degrade to "no compaction yet"
            return None

    def put(
        self,
        key: str,
        *,
        summary: str,
        covers: int,
        stripped: int = 0,
        trigger: str = "auto",
        provider: str = "",
        model: str = "",
        session_id: str = "",
    ) -> CompactionRecord | None:
        if not key or not summary.strip():
            return None
        rec = CompactionRecord(
            id=key,
            summary=summary,
            covers=int(covers),
            stripped=int(stripped),
            trigger=trigger,
            provider=provider,
            model=model,
            session_id=session_id,
        )
        try:
            with Session(self.engine) as db:
                db.merge(rec)  # content-addressed: re-writing is a no-op update
                db.commit()
        except Exception:  # noqa: BLE001 — losing the cache costs a recompute
            return None
        return rec

    def recent(self, limit: int = 20) -> list[CompactionRecord]:
        try:
            with Session(self.engine) as db:
                return list(
                    db.exec(
                        select(CompactionRecord)
                        .order_by(CompactionRecord.created_at.desc())  # type: ignore[attr-defined]
                        .limit(limit)
                    )
                )
        except Exception:  # noqa: BLE001
            return []
