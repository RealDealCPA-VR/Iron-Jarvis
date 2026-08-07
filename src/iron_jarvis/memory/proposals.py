"""Memory housekeeping proposals (v1.143.0) — the steward's SUGGEST-ONLY lane.

The memory steward (``memory/steward.py``) may ADD memory freely: an
``ltm_append`` is append-only and already undoable, so a wrong note costs the
user one click. Everything else it notices — a duplicate, a note that went
stale, two notes that contradict each other, three notes that want to be one —
is a REVISION, and a revision **deletes or rewrites something the user wrote**.
So revisions never happen on their own. They land here as reviewable
:class:`MemoryProposalRecord` rows and wait for a click.

This is the v1.135.0 skill-proposal shape applied to memory, deliberately and
almost line-for-line:

* mint → ``pending`` → ``approved`` / ``dismissed`` (no other states);
* a DISMISSED proposal's :attr:`~MemoryProposalRecord.signature` suppresses the
  same suggestion from being raised again — "not this" sticks, so the steward
  can't nag;
* every read path never raises (a review card must never be the reason the
  Memory page 500s);
* approve does the real thing through the paths the app already owns, and is
  HONEST when it cannot.

What "approve" actually does
----------------------------
One mechanism serves all four kinds — only the words differ. The proposal's
``payload_json`` says what applying would write::

    {"survivor_ref": "<note ref>",     # the note that survives (optional)
     "text": "<full replacement text>", # its new content (optional)
     "remove_refs": ["<note ref>", …]}  # notes to remove (optional)

* **duplicate** — keep one note, remove the copies (``remove_refs``).
* **merge** — write the merged text to the survivor, remove the others.
* **contradiction** — rewrite the survivor with the reconciled text.
* **stale** — rewrite the note with corrected text, or remove it outright.

Which stores can be applied — and undone
----------------------------------------
Rewriting/removing a note means touching a FILE. That is only possible for a
markdown-backed memory base (the built-in one, an Obsidian vault, any custom
markdown folder — every connector that exposes ``.dir``). Notion, an MCP brain,
an HTTP-RAG endpoint, a cloud base: their notes live somewhere this daemon
cannot rewrite, so :meth:`MemoryProposalStore.approve` returns an honest error
result instead of pretending, and the record stays ``pending``.

Where the file CAN be touched, every touch is journaled into the same TX-01
undo ledger every reversible tool uses (:class:`~iron_jarvis.core.models.
UndoJournal` + a ``ltm_append`` :class:`~iron_jarvis.core.models.ToolInvocation`),
using exactly the ``memory_restore`` / ``memory_delete_file`` descriptors
``LTMAppendTool.revert`` already knows how to reverse. So an approved cleanup
shows up on the Time-travel list and one click puts every note back. Nothing
new was invented for undo; the existing lane was reused.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Field, SQLModel, select

from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from ..core.logging import get_logger
from ..core.models import PermissionMode, ToolInvocation, UndoJournal
from ..ltm.base import slugify
from ..tools.base import Reversibility
from ..tools.undo import make_file_descriptor, sha256_bytes

log = get_logger("memory_proposals")

#: The housekeeping kinds the steward may propose. Every one is a REVISION —
#: that is the whole reason this table exists. Additions never come here.
KINDS: tuple[str, ...] = ("duplicate", "stale", "contradiction", "merge")

#: The synthetic session id approvals are filed under in the tool ledger. Same
#: trick chat's non-agent turns use (they file under the literal id "chat"):
#: the work is real and auditable, it just has no agent session behind it.
APPLY_SESSION_ID = "memory-review"

#: Hardening caps. Proposal fields are MODEL OUTPUT steered by conversation
#: text the user did not write, so everything that lands in the DB is bounded.
_MAX_REFS = 20
_MAX_REF = 500
_MAX_RATIONALE = 2000
_MAX_ACTION = 400
_MAX_TEXT = 200_000
_MAX_SIGNATURE = 500

#: Engines whose table has already been ensured (the DDL is idempotent, this
#: just keeps a per-request store construction from re-running it).
_ENSURED: set[int] = set()


class MemoryProposalRecord(SQLModel, table=True):
    """One reviewable housekeeping suggestion (suggest-only, never auto-applied).

    A plain new SQLModel table — nothing for the additive-column reconciler.
    """

    id: str = Field(default_factory=lambda: new_id("mprop"), primary_key=True)
    #: duplicate | stale | contradiction | merge (see :data:`KINDS`).
    kind: str = Field(default="duplicate", index=True)
    #: The memory BASE the affected notes live in (an LTM connector name).
    base: str = Field(default="", index=True)
    #: JSON list[str] of the affected note refs (paths) or titles — what the
    #: review card shows as "the notes this touches".
    refs: str = "[]"
    #: Why the steward thinks this — shown verbatim to the user.
    rationale: str = ""
    #: One human-readable line: what approving would DO.
    suggested_action: str = ""
    #: What applying would write: ``{"survivor_ref", "text", "remove_refs"}``.
    payload_json: str = "{}"
    #: Stable dedup key (kind + base + sorted refs). A DISMISSED signature is
    #: suppressed, so the same suggestion is never raised twice.
    signature: str = Field(default="", index=True)
    status: str = "pending"  # pending | approved | dismissed
    #: Set by :meth:`MemoryProposalStore.approve` — exactly what it did (the
    #: notes it changed, the undo action ids, whether undo is available).
    applied_json: str = "{}"
    #: The steward run that raised this (empty when filed by hand/a test).
    run_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None

    def decoded_refs(self) -> list[str]:
        """``refs`` as a list (never raises — a mangled row reads as empty)."""
        try:
            parsed = json.loads(self.refs or "[]")
        except (TypeError, ValueError):
            return []
        return [str(r) for r in parsed] if isinstance(parsed, list) else []

    def decoded_payload(self) -> dict[str, Any]:
        """``payload_json`` as a dict (never raises)."""
        try:
            parsed = json.loads(self.payload_json or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def decoded_applied(self) -> dict[str, Any]:
        """``applied_json`` as a dict (never raises)."""
        try:
            parsed = json.loads(self.applied_json or "{}")
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}


@dataclass
class ApplyResult:
    """The honest outcome of applying one proposal.

    ``ok=False`` is a NORMAL, expected answer (a Notion base can't be rewritten
    from here) — the caller reports it, the record stays ``pending``.
    """

    ok: bool
    detail: str = ""
    error: str = ""
    #: Human-readable descriptions of each note that ACTUALLY changed. A note
    #: that was already gone is NOT listed here — see :attr:`skipped`.
    changed: list[str] = field(default_factory=list)
    #: Notes the suggestion named that needed no work (already removed). Kept
    #: apart from :attr:`changed` so "nothing actually happened" can never be
    #: reported to the user as "Memory updated".
    skipped: list[str] = field(default_factory=list)
    #: ToolInvocation ids — each one is undoable via ``POST /undo/{id}``.
    undo_ids: list[str] = field(default_factory=list)
    undoable: bool = False
    #: True when the apply FAILED PART-WAY: some notes were already changed and
    #: the record is still ``pending``. The user has to be told, or they read a
    #: plain error and believe their notes are untouched.
    partial: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "error": self.error,
            "changed": list(self.changed),
            "skipped": list(self.skipped),
            "undo_ids": list(self.undo_ids),
            "undoable": self.undoable,
            "partial": self.partial,
        }


def _norm_ref(ref: str) -> str:
    """One note reference reduced to the form two spellings of the SAME note share.

    Suppression is the anti-nag contract, and the thing it defends against is a
    MODEL: the steward re-derives its suggestions from scratch every run and has
    no obligation to spell a note the way it did last week. MEASURED against the
    first implementation (case/order/separator only): a suggestion the user had
    dismissed came straight back when the next run wrote ``alpha.md`` instead of
    ``alpha``, ``./alpha`` instead of ``alpha``, or a trailing slash — three of
    the six variants tried. "Not this" has to stick harder than that.

    So the decorations that CANNOT change which file is meant are removed:
    separator, case, surrounding whitespace, ``./`` prefixes, trailing slashes,
    and the ``.md`` extension (``_candidate_path`` treats ``alpha`` and
    ``alpha.md`` as the same note by construction).

    What is deliberately NOT removed is the FOLDER: ``work/alpha`` and
    ``home/alpha`` are two different notes in a vault with subfolders, and
    collapsing them would silently swallow a real second suggestion — an
    under-suppression is one extra card, an over-suppression is a suggestion the
    user never sees. An absolute path still signs differently from a bare title
    for the same reason (nothing here can resolve one against the other without
    the base folder, which a pure function does not have).
    """
    value = str(ref or "").strip().replace("\\", "/").lower()
    while value.startswith("./"):
        value = value[2:]
    value = value.rstrip("/")
    if value.endswith(".md"):
        value = value[:-3]
    return value.strip()


def signature_for(kind: str, base: str, refs: list[str]) -> str:
    """Stable dedup key for one suggestion.

    Keyed on kind + base + the SET of affected notes (order-independent, and
    spelling-independent — see :func:`_norm_ref`), so the same "these two notes
    are duplicates" is one signature however the steward happens to list them.
    """
    norm = sorted({n for n in (_norm_ref(r) for r in refs) if n})
    joined = "|".join(norm)
    signature = f"{kind}::{base}::{joined}"
    if len(signature) <= _MAX_SIGNATURE:
        return signature
    # A merge over a dozen notes can outrun the column: 20 refs × 500 chars is
    # allowed by the caps. TRUNCATING it would make two long suggestions that
    # happen to share a prefix sign identically, and an over-suppression is a
    # suggestion the user never sees — the one failure mode worse than a
    # duplicate card. So a long list is HASHED instead: still stable, still
    # order-independent, and collision-free in practice.
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]
    return f"{kind}::{base}::#{digest}"[:_MAX_SIGNATURE]


def _ensure_table(engine) -> None:
    """Create ``memoryproposalrecord`` if missing.

    This module is imported by the route layer, which ``create_app`` imports
    AFTER ``init_db`` has already run ``create_all`` — so the table would not
    exist on a daemon that never imported us at boot. Idempotent, best-effort,
    and never raises: a review card must not be able to brick anything.
    """
    key = id(engine)
    if key in _ENSURED:
        return
    try:
        MemoryProposalRecord.__table__.create(engine, checkfirst=True)
    except Exception:  # noqa: BLE001 — a DDL hiccup must never break a request
        log.exception("could not ensure the memory-proposal table")
    _ENSURED.add(key)


def _normalize_text(text: str) -> str:
    r"""Logical note text: ``\r\n`` collapsed to ``\n``.

    Both the hash we journal and the bytes we write have to agree, and Python's
    universal-newline translation rewrites ``\n`` on the way to disk (and back
    on the way in). Hashing the logical ``\n`` form is exactly what
    ``LTMAppendTool.capture_undo`` does — keep it identical or an undo would
    read as drift on Windows.
    """
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


class MemoryProposalStore:
    """Persist / list / decide memory housekeeping proposals.

    ``ltm`` (a :class:`~iron_jarvis.ltm.manager.LongTermMemory`) and ``home``
    (the state dir, for undo pre-images) are only needed to APPLY a proposal;
    the read side works without them, so a bare ``MemoryProposalStore(engine)``
    is a perfectly good listing store for tests and for the route's degraded
    path.
    """

    def __init__(self, engine, ltm=None, home: str | Path | None = None) -> None:
        self.engine = engine
        self.ltm = ltm
        self.home = home
        _ensure_table(engine)

    # -- write side -----------------------------------------------------------

    def create(
        self,
        *,
        kind: str,
        base: str,
        refs: list[str],
        rationale: str = "",
        suggested_action: str = "",
        payload: dict[str, Any] | None = None,
        run_id: str = "",
    ) -> MemoryProposalRecord | None:
        """File one suggestion. Returns ``None`` when it is SUPPRESSED.

        Suppression is the whole anti-nag contract: a signature that is already
        pending (a duplicate suggestion) or was DISMISSED (the user said "not
        this") never comes back. Raises ``ValueError`` only for input the caller
        got wrong (unknown kind / no refs) — a DB hiccup logs and returns None.
        """
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"unknown proposal kind {kind!r}; expected one of {KINDS}")
        base = str(base or "").strip()
        clean_refs = [str(r).strip()[:_MAX_REF] for r in (refs or []) if str(r).strip()]
        clean_refs = clean_refs[:_MAX_REFS]
        if not clean_refs:
            raise ValueError("a memory proposal must name at least one note")
        sig = signature_for(kind, base, clean_refs)
        record = MemoryProposalRecord(
            kind=kind,
            base=base,
            refs=json.dumps(clean_refs),
            rationale=str(rationale or "").strip()[:_MAX_RATIONALE],
            suggested_action=str(suggested_action or "").strip()[:_MAX_ACTION],
            payload_json=json.dumps(_clean_payload(payload)),
            signature=sig,
            run_id=str(run_id or "")[:120],
        )
        try:
            with session_scope(self.engine) as db:
                clash = db.exec(
                    select(MemoryProposalRecord).where(
                        MemoryProposalRecord.signature == sig,
                        # "approved" is deliberately NOT suppressed: the notes
                        # it touched are gone, so the same signature can only
                        # recur if the situation genuinely came back.
                        MemoryProposalRecord.status.in_(("pending", "dismissed")),
                    )
                ).first()
                if clash is not None:
                    return None
                db.add(record)
                db.commit()
                db.refresh(record)
                return record
        except Exception:  # noqa: BLE001 — filing a suggestion must never break a run
            log.exception("could not file memory proposal (%s / %s)", kind, base)
            return None

    def approve(self, proposal_id: str) -> tuple[MemoryProposalRecord, ApplyResult]:
        """Apply a pending proposal and mark it approved.

        Returns ``(record, result)``. When ``result.ok`` is False NOTHING was
        changed and the record stays ``pending`` — an unsupported base or a bad
        payload is an honest refusal, not a silent "approved". Raises
        ``ValueError`` for an unknown (404) or already-decided (409) proposal,
        mirroring ``SkillLearningEngine.approve``.
        """
        with session_scope(self.engine) as db:
            row = db.get(MemoryProposalRecord, proposal_id)
            if row is None:
                raise ValueError(f"no such proposal: {proposal_id}")
            if row.status != "pending":
                raise ValueError(f"proposal already {row.status}")
            kind = row.kind
            base = row.base
            payload = row.decoded_payload()
            refs = row.decoded_refs()

        result = self._apply(kind=kind, base=base, payload=payload, refs=refs)
        if not result.ok:
            # Leave it PENDING: the user can connect/fix the base and retry.
            # But if the apply got part-way (a rewrite landed, one of three
            # deletions succeeded), record WHAT happened on the row anyway —
            # otherwise the only trace is two orphan entries on the Time-travel
            # list and a card that still reads "nothing has changed yet".
            if result.changed:
                self._record_partial(proposal_id, result)
            return self.get(proposal_id) or row, result

        with session_scope(self.engine) as db:
            row = db.get(MemoryProposalRecord, proposal_id)
            if row is None:  # deleted underneath us — the effect still happened
                raise ValueError(f"no such proposal: {proposal_id}")
            row.status = "approved"
            row.decided_at = utcnow()
            row.applied_json = json.dumps(
                {**result.to_dict(), "at": utcnow().isoformat()}
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row, result

    def _record_partial(self, proposal_id: str, result: ApplyResult) -> None:
        """Stamp a half-applied attempt onto a record that stays ``pending``.

        Best-effort by construction: the CHANGES are already on disk, so a
        bookkeeping failure here must not turn into an exception that hides
        them. Status is deliberately untouched — the suggestion is not done.
        """
        try:
            with session_scope(self.engine) as db:
                row = db.get(MemoryProposalRecord, proposal_id)
                if row is None or row.status != "pending":
                    return
                row.applied_json = json.dumps(
                    {**result.to_dict(), "partial": True, "at": utcnow().isoformat()}
                )
                db.add(row)
                db.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not record a partial memory-review apply")

    def dismiss(self, proposal_id: str) -> MemoryProposalRecord:
        """Mark a pending proposal dismissed; its signature is then suppressed
        for good. Raises ``ValueError`` for unknown / already-decided."""
        with session_scope(self.engine) as db:
            row = db.get(MemoryProposalRecord, proposal_id)
            if row is None:
                raise ValueError(f"no such proposal: {proposal_id}")
            if row.status != "pending":
                raise ValueError(f"proposal already {row.status}")
            row.status = "dismissed"
            row.decided_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    # -- read side (never raises) ---------------------------------------------

    def list(self, status: str | None = None) -> list[MemoryProposalRecord]:
        """Proposals for the review card: pending first, newest first within."""
        try:
            with session_scope(self.engine) as db:
                query = select(MemoryProposalRecord)
                if status is not None:
                    query = query.where(MemoryProposalRecord.status == status)
                rows = list(db.exec(query))
        except Exception:  # noqa: BLE001
            log.exception("memory-proposal list failed")
            return []
        rows.sort(
            key=lambda r: (
                r.status != "pending",
                -(r.created_at.timestamp() if r.created_at else 0.0),
            )
        )
        return rows

    def get(self, proposal_id: str) -> MemoryProposalRecord | None:
        try:
            with session_scope(self.engine) as db:
                return db.get(MemoryProposalRecord, proposal_id)
        except Exception:  # noqa: BLE001
            log.exception("memory-proposal get failed")
            return None

    def suppressed(self, signature: str) -> bool:
        """True when this signature would be refused by :meth:`create`."""
        try:
            with session_scope(self.engine) as db:
                return (
                    db.exec(
                        select(MemoryProposalRecord).where(
                            MemoryProposalRecord.signature == signature,
                            MemoryProposalRecord.status.in_(("pending", "dismissed")),
                        )
                    ).first()
                    is not None
                )
        except Exception:  # noqa: BLE001
            log.exception("memory-proposal suppression check failed")
            return False

    def stats(self) -> dict[str, Any]:
        """Counts for the status line (never raises)."""
        empty = {
            "pending": 0,
            "approved": 0,
            "dismissed": 0,
            "total": 0,
            "by_kind": {},
        }
        try:
            with session_scope(self.engine) as db:
                rows = list(db.exec(select(MemoryProposalRecord)))
        except Exception:  # noqa: BLE001
            log.exception("memory-proposal stats failed")
            return empty
        out = dict(empty)
        by_kind: dict[str, int] = {}
        for r in rows:
            out["total"] += 1
            if r.status in out:
                out[r.status] += 1
            if r.status == "pending":
                by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
        out["by_kind"] = by_kind
        return out

    # -- base capability (what the UI must be honest about) -------------------

    def describe_base(self, base: str) -> dict[str, Any]:
        """Can this proposal be applied here, and would it be undoable?

        A markdown-backed base (anything exposing ``.dir``) can be rewritten in
        place and every change is journaled, so undo works. Everything else
        (Notion, an MCP brain, HTTP-RAG, cloud) is read/append-only from here.
        """
        conn = None
        if self.ltm is not None:
            try:
                conn = self.ltm.get(base) if base else None
            except Exception:  # noqa: BLE001
                conn = None
        if conn is None:
            return {
                "can_apply": False,
                "undoable": False,
                "note": (
                    f"The memory base “{base}” isn’t connected right now, so this "
                    "can’t be applied. Reconnect it on the Memory page."
                    if base
                    else "This suggestion doesn’t name a memory base, so it can’t "
                    "be applied."
                ),
            }
        if getattr(conn, "dir", None) is None:
            return {
                "can_apply": False,
                "undoable": False,
                "note": (
                    f"“{base}” keeps its notes outside this computer, so Iron "
                    "Jarvis can’t rewrite or remove them from here. Make the "
                    "change in that base directly."
                ),
            }
        return {
            "can_apply": True,
            "undoable": True,
            "note": "Applying this is undoable — it lands on the Time travel list.",
        }

    # -- the apply mechanism --------------------------------------------------

    def _apply(
        self,
        *,
        kind: str,
        base: str,
        payload: dict[str, Any],
        refs: list[str],
    ) -> ApplyResult:
        """Do the rewrite/removal for one proposal. NEVER raises."""
        try:
            return self._apply_inner(kind=kind, base=base, payload=payload, refs=refs)
        except Exception as exc:  # noqa: BLE001 — an honest error beats a 500
            log.exception("applying a memory proposal failed (%s / %s)", kind, base)
            return ApplyResult(
                ok=False, error=f"could not apply: {type(exc).__name__}: {exc}"
            )

    def _apply_inner(
        self,
        *,
        kind: str,
        base: str,
        payload: dict[str, Any],
        refs: list[str],
    ) -> ApplyResult:
        capability = self.describe_base(base)
        if not capability["can_apply"]:
            return ApplyResult(ok=False, error=capability["note"])
        conn = self.ltm.get(base)
        directory = Path(getattr(conn, "dir")).resolve()

        text = _normalize_text(str(payload.get("text") or ""))[:_MAX_TEXT]
        survivor_ref = str(payload.get("survivor_ref") or "").strip()
        if text and not survivor_ref:
            survivor_ref = refs[0] if refs else ""
        removes = [
            str(r).strip()
            for r in (payload.get("remove_refs") or [])
            if str(r).strip()
        ][:_MAX_REFS]

        if not text and not removes:
            return ApplyResult(
                ok=False,
                error=(
                    "this suggestion doesn’t say what to write or remove, so "
                    "there is nothing to apply"
                ),
            )
        if text and not survivor_ref:
            return ApplyResult(
                ok=False,
                error="this suggestion has replacement text but names no note to write it to",
            )

        changed: list[str] = []
        skipped: list[str] = []
        undo_ids: list[str] = []

        if text:
            target = _resolve_target(directory, survivor_ref)
            prior = _read_note(target)
            # ONE body for both the write and the hash — journaling anything
            # other than the exact bytes on disk would read as drift on undo.
            body = _note_body(text)
            new_bytes = body.encode("utf-8")
            _write_note(target, body)
            action_id = self._journal(
                kind="memory_restore" if prior is not None else "memory_delete_file",
                path=target,
                prior_bytes=prior,
                post_sha256=sha256_bytes(new_bytes),
                output=(
                    f"memory review ({kind}): "
                    f"{'rewrote' if prior is not None else 'created'} note "
                    f"“{target.stem}” in memory base {base}"
                ),
            )
            changed.append(
                f"{'Rewrote' if prior is not None else 'Created'} “{target.stem}”"
            )
            if action_id:
                undo_ids.append(action_id)

        for ref in removes:
            victim = _resolve_existing(directory, ref)
            if victim is None:
                # NOT a change. Counting "already gone" as work is how an
                # approve that did literally nothing came back ok=True and the
                # card said "Memory updated" — measured, and exactly the kind of
                # comfortable lie this feature cannot afford.
                skipped.append(f"“{Path(ref).stem or ref}” was already gone")
                continue
            if text and victim == _resolve_target(directory, survivor_ref):
                # Never remove the note we just wrote the survivor text into.
                continue
            prior = _read_note(victim)
            try:
                victim.unlink()
            except OSError as exc:
                # PART of this suggestion is already on disk. Say so in the
                # error itself — the caller leaves the record pending, and a
                # bare "could not remove X" would let the user believe their
                # notes were untouched while a rewrite and a deletion stood.
                detail = f"could not remove “{victim.stem}”: {exc}"
                if changed:
                    detail += (
                        ". Part of this was already done: "
                        + "; ".join(changed)
                        + " — undo those from Time travel if you don't want them."
                    )
                return ApplyResult(
                    ok=False,
                    error=detail,
                    detail="; ".join(changed),
                    changed=changed,
                    skipped=skipped,
                    undo_ids=undo_ids,
                    undoable=bool(undo_ids),
                    partial=bool(changed),
                )
            action_id = self._journal(
                kind="memory_restore",
                path=victim,
                prior_bytes=prior,
                # The file is GONE, so there is no post-state to hash; the
                # revert's drift guard skips when post_sha256 is None and
                # simply writes the note back.
                post_sha256=None,
                output=(
                    f"memory review ({kind}): removed note “{victim.stem}” "
                    f"from memory base {base}"
                ),
            )
            changed.append(f"Removed “{victim.stem}”")
            if action_id:
                undo_ids.append(action_id)

        if not changed:
            return ApplyResult(
                ok=False,
                error=(
                    "nothing was found to change — "
                    + ("; ".join(skipped) if skipped else "the notes may already be gone")
                ),
                skipped=skipped,
            )
        return ApplyResult(
            ok=True,
            detail="; ".join(changed + skipped),
            changed=changed,
            skipped=skipped,
            undo_ids=undo_ids,
            undoable=bool(undo_ids),
        )

    def _journal(
        self,
        *,
        kind: str,
        path: Path,
        prior_bytes: bytes | None,
        post_sha256: str | None,
        output: str,
    ) -> str:
        """Write the TX-01 inverse for one note mutation; return its action id.

        The pair (``ToolInvocation`` + ``UndoJournal``) is exactly what
        ``POST /undo/{id}`` consumes, and the descriptor kinds are the two
        ``LTMAppendTool.revert`` already implements — so undo needs no new code
        anywhere. Best-effort: if the ledger write fails the CHANGE still stands
        (it is on disk), the user just doesn't get a one-click undo, and the
        caller reports ``undoable`` honestly.
        """
        if self.home is None:
            return ""
        action_id = new_id("tool")
        try:
            descriptor = make_file_descriptor(
                self.home,
                kind=kind,
                path=str(path),
                mode="text",
                prior_bytes=prior_bytes,
                pre_sha256=sha256_bytes(prior_bytes) if prior_bytes is not None else None,
                post_sha256=post_sha256,
            )
            with session_scope(self.engine) as db:
                db.add(
                    ToolInvocation(
                        id=action_id,
                        session_id=APPLY_SESSION_ID,
                        agent_run_id="",
                        tool="ltm_append",
                        args_json="{}",
                        verdict=PermissionMode.ALLOW,
                        ok=True,
                        output=output[:4000],
                        reversibility=Reversibility.REVERSIBLE.value,
                    )
                )
                db.add(
                    UndoJournal(
                        action_id=action_id,
                        session_id=APPLY_SESSION_ID,
                        agent_run_id="",
                        tool="ltm_append",
                        kind=descriptor["kind"],
                        reversible=True,
                        pre_ref=descriptor["pre_ref"],
                        pre_inline=descriptor["pre_inline"],
                        pre_sha256=descriptor["pre_sha256"],
                        post_sha256=descriptor["post_sha256"],
                    )
                )
                db.commit()
            return action_id
        except Exception:  # noqa: BLE001 — no undo entry beats no cleanup
            log.exception("could not journal the memory-review undo for %s", path)
            return ""


# --- note-file helpers -------------------------------------------------------


def _clean_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only the three keys apply understands, capped."""
    src = payload if isinstance(payload, dict) else {}
    out: dict[str, Any] = {}
    survivor = str(src.get("survivor_ref") or "").strip()
    if survivor:
        out["survivor_ref"] = survivor[:_MAX_REF]
    text = str(src.get("text") or "")
    if text.strip():
        out["text"] = _normalize_text(text)[:_MAX_TEXT]
    removes = [
        str(r).strip()[:_MAX_REF]
        for r in (src.get("remove_refs") or [])
        if str(r).strip()
    ]
    if removes:
        out["remove_refs"] = removes[:_MAX_REFS]
    return out


def _contained(directory: Path, candidate: Path) -> Path:
    """Refuse any path outside the base's folder.

    Proposal payloads are model output steered by text the user did not write.
    Without this, ``remove_refs: ["../../.ssh/id_rsa"]`` would be a delete
    primitive. Every resolved note path goes through here.
    """
    resolved = candidate.resolve()
    try:
        resolved.relative_to(directory)
    except ValueError:
        raise ValueError(f"“{candidate}” is outside this memory base")
    return resolved


def _candidate_path(directory: Path, ref: str) -> Path:
    """Interpret a ref (absolute path, relative filename, or bare title)."""
    raw = (ref or "").strip()
    if not raw:
        raise ValueError("empty note reference")
    p = Path(raw)
    if not p.is_absolute():
        name = raw if raw.lower().endswith(".md") else f"{slugify(raw)}.md"
        p = directory / name
    return _contained(directory, p)


def _resolve_existing(directory: Path, ref: str) -> Path | None:
    """The EXISTING note a ref points at, or None.

    Falls back to a recursive stem match so a vault that files notes in
    subfolders still resolves a bare title.
    """
    direct = _candidate_path(directory, ref)
    if direct.is_file():
        return direct
    stem = direct.stem.lower()
    try:
        for candidate in sorted(directory.glob("**/*.md")):
            if candidate.is_file() and candidate.stem.lower() == stem:
                return _contained(directory, candidate)
    except OSError:
        return None
    return None


def _resolve_target(directory: Path, ref: str) -> Path:
    """Where the survivor text should be written (existing note wins)."""
    return _resolve_existing(directory, ref) or _candidate_path(directory, ref)


def _read_note(path: Path) -> bytes | None:
    """Logical UTF-8 bytes of an existing note, or None when it doesn't exist.

    Identical to ``LTMAppendTool.capture_undo``'s pre-image: decoded text
    re-encoded, so the hash is newline-representation invariant.
    """
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").encode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _note_body(text: str) -> str:
    r"""The exact logical content a note will hold: ``\n`` newlines, one
    trailing newline (what ``MarkdownDirConnector.append`` leaves behind)."""
    body = _normalize_text(text)
    return body if body.endswith("\n") else body + "\n"


def _write_note(path: Path, body: str) -> None:
    """Atomically replace a note's whole content with ``body`` VERBATIM.

    Same stage-then-``os.replace`` discipline ``MarkdownDirConnector.append``
    uses — a crash mid-write must not lose the note we are consolidating INTO.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
