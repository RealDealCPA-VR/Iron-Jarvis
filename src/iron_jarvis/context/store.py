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

import hashlib
import json
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
    #: JSON list of the CLAIMS the check removed (v1.169.0, ADDITIVE — reads
    #: NULL/"" on rows from before the column and on lanes that never pass
    #: them). The COUNT above was always persisted; the claims themselves were
    #: previously readable exactly once, in the creating response — and this
    #: text is injected into every later turn's system prompt, so what was
    #: REMOVED from it deserves to stay inspectable too.
    stripped_claims_json: str = ""
    #: "manual" (the user chose it) | "auto" (the ceiling forced it).
    trigger: str = "auto"
    provider: str = ""
    model: str = ""
    #: The agent session this came from, when it came from a run ("" for chat).
    session_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    def claims(self) -> list[str]:
        """The stripped claims, decoded. ``[]`` when none were recorded —
        callers must distinguish that from ``stripped == 0`` (see the routes:
        a count with no text is reported honestly as "not recorded")."""
        try:
            out = json.loads(self.stripped_claims_json or "[]")
        except Exception:  # noqa: BLE001 — a corrupt cell is "not recorded"
            return []
        return [c for c in out if isinstance(c, str)] if isinstance(out, list) else []


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
        stripped_claims: list[str] | None = None,
        trigger: str = "auto",
        provider: str = "",
        model: str = "",
        session_id: str = "",
    ) -> CompactionRecord | None:
        if not key or not summary.strip():
            return None
        # ADDITIVE kwarg (v1.169.0): callers that never pass claims are
        # untouched and their rows read back ``claims() == []``. Persisted
        # FAITHFULLY — no truncation here. The store must never silently
        # shorten the list it was handed: the creating response returns the
        # caller's list verbatim, and a cap here would make the inspect route
        # forever disagree with it (same compaction, two different
        # removed-claims lists depending on which endpoint you ask). Any
        # bounding is the producer's call (``compaction.compact_messages``
        # caps its own output) — and when a producer DOES cap, ``stripped``
        # exceeding ``len(claims)`` is the signal the UI renders honestly.
        claims = [c for c in (stripped_claims or []) if isinstance(c, str)]
        rec = CompactionRecord(
            id=key,
            summary=summary,
            covers=int(covers),
            stripped=int(stripped),
            stripped_claims_json=json.dumps(claims) if claims else "",
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

    def standing(self, messages: list) -> CompactionRecord | None:
        """The summary STANDING over this exact conversation, if one is stored
        (v1.169.0 — the compaction-inspect read path).

        Probes EVERY prefix of *messages* under the chat keying the live turn
        uses — :func:`~.compaction.prefix_key` over ``f"{role}\\x1e{content}"``
        pairs, exactly as ``chat_turn._apply_compaction`` and the
        ``POST /chat/compact`` route compute it — and returns the LONGEST
        stored record. A single exact-key ``get`` would not do: the live turn
        keys on the message list it was HANDED, which grows by a message or two
        every turn, so a summary created three turns ago covers a strict PREFIX
        of today's thread and its creating key alone would never be found
        again. Content addressing is what makes the probe sound — a hash hit
        for a prefix PROVES the covered messages are byte-identical to what the
        summary was verified against. Longest wins because a re-compaction
        absorbs the prior summary as material (see ``compaction.build_prompt``),
        so the longer record supersedes the shorter one it swallowed.

        The incremental hash below is ``prefix_key`` unrolled — one ``\\x1f`` +
        text update per message, a digest snapshot per prefix — so the whole
        scan is O(total content) instead of O(n²). Byte-for-byte parity with
        ``prefix_key`` itself is pinned by
        ``tests/test_compaction_inspect_v1169.py``; if that function ever
        changes shape, the pin turns red before this silently returns misses.

        Accepts dict messages (a stored thread's ``messages_json``) or
        attribute-style ones (``ChatMessageBody``), read the same defaulted way
        the live turn reads them. Never raises — same contract as :meth:`get`.
        """
        keys: dict[str, int] = {}  # prefix key -> prefix length (messages)
        h = hashlib.sha256()
        try:
            for i, m in enumerate(list(messages or [])):
                if isinstance(m, dict):
                    role = m.get("role") or "user"
                    text = m.get("content") or ""
                else:
                    role = getattr(m, "role", "user") or "user"
                    text = getattr(m, "content", "") or ""
                h.update(b"\x1f")
                h.update(f"{role}\x1e{text}".encode("utf-8", "replace"))
                keys[h.copy().hexdigest()] = i + 1
        except Exception:  # noqa: BLE001 — degrade to "no compaction yet"
            return None
        if not keys:
            return None
        try:
            with Session(self.engine) as db:
                rows = list(
                    db.exec(
                        select(CompactionRecord).where(
                            CompactionRecord.id.in_(list(keys))  # type: ignore[attr-defined]
                        )
                    )
                )
        except Exception:  # noqa: BLE001 — degrade to "no compaction yet"
            return None
        rows = [r for r in rows if r.summary.strip()]
        if not rows:
            return None
        return max(rows, key=lambda r: keys.get(r.id, 0))

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
