"""The reflection coach's engine (v1.297.0).

``report`` is pure: ledger signals for the agent's recent runs plus the
deterministic clusters. ``propose`` adds ONE router completion on top of that
report and mints a reviewable :class:`CoachProposalRecord` — or returns
``None`` with a plain ``reason`` (also kept on ``last_reason``) whenever the
honest answer is "not now": a builtin, fewer than three runs, no clusters, no
real model (the honest-mock rule — mock prose would be written into the
agent's own instructions and read back as authoritative on every later run),
or a signature that is already pending / was declined within 14 days.

The coach is a PLATFORM SERVICE. It never runs as the agent whose
instructions it reads, and it never coaches itself: there is no "self" here —
the target is always another record (``custom:<slug>``), nothing is written
until a person accepts, and ``accept`` goes through the ordinary registry
door so the revision is the same kind of edit the Agents page makes.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
from datetime import timedelta
from typing import Any

from sqlmodel import select

from ..core.db import session_scope
from ..core.events import EventType
from ..core.ids import utcnow
from .models import (
    ACCEPTED,
    DECLINE_SUPPRESS_DAYS,
    DECLINED,
    KIND_INSTRUCTIONS,
    PENDING,
    RATIONALE_CHARS,
    STALE,
    CoachProposalRecord,
)
from .signals import as_naive_utc, recent_runs, run_signals
from .taxonomy import CATEGORIES, Cluster, clusters

log = logging.getLogger("iron_jarvis.coach")

#: Fewer runs than this and there is no pattern to coach on.
MIN_RUNS = 3
#: The proposed text may grow by at most this fraction of ``before``.
MAX_GROWTH = 0.20
#: …or this many chars, whichever is larger — a 200-char instruction could
#: not take one added sentence under +20% alone.
MIN_GROWTH_CHARS = 160
#: Lines of the current instructions carrying one of these words are SAFETY
#: lines; a proposal that drops any of them is refused.
SAFETY_WORDS = ("never", "always")
#: Evidence quotes handed to the model per cluster.
QUOTES_PER_CLUSTER = 3
#: The model prompt's cap on the current instructions (chars).
PROMPT_INSTRUCTIONS_CHARS = 6000

REASON_BUILTIN = "the coach only coaches custom agents — a builtin's instructions are the app's"
REASON_UNKNOWN = "no such agent"
REASON_FEW_RUNS = "fewer than {n} runs to learn from"
REASON_NO_CLUSTERS = "nothing kept going wrong across the recent runs"
REASON_NO_MODEL = (
    "no real model is connected — the coach never writes instructions with the "
    "offline mock"
)
REASON_PENDING = "already pending — a proposal for the same evidence is waiting for you"
REASON_DECLINED = "you declined a proposal for the same evidence recently — suppressed for {days} days"
REASON_ACCEPTED = "you already accepted a change for the same evidence — the coach waits {days} days for new runs"
REASON_NOTHING_SMALL = "the coach had nothing small enough to say"
REASON_MODEL_FAILED = "the model call failed"
STALE_SENTENCE = "the instructions changed since this was written — ask the coach again"


class StaleProposal(Exception):
    """Raised by ``accept`` when the agent's instructions moved since mint; the
    row is already marked ``stale`` when this surfaces."""


def _safety_lines(text: str) -> list[str]:
    out = []
    for line in str(text or "").splitlines():
        low = line.lower()
        if any(w in low for w in SAFETY_WORDS) and line.strip():
            out.append(line.strip())
    return out


def validate_revision(before: str, after: str) -> str | None:
    """Why ``after`` is NOT an acceptable small edit of ``before`` (``None`` =
    acceptable): empty, grew past +20%, dropped a never/always line, or did
    not change anything."""
    before = str(before or "")
    after = str(after or "")
    if not after.strip():
        return "the proposed text is empty"
    if after.strip() == before.strip():
        return "the proposed text is identical to the current instructions"
    limit = max(int(len(before) * (1.0 + MAX_GROWTH)), len(before) + MIN_GROWTH_CHARS)
    if len(after) > limit:
        return (
            f"the proposed text grows past +{int(MAX_GROWTH * 100)}% "
            f"(or +{MIN_GROWTH_CHARS} chars) ({len(after)} > {limit} chars)"
        )
    kept = {line.strip() for line in after.splitlines()}
    for line in _safety_lines(before):
        if line not in kept:
            return f"the proposed text drops a safety line: {line[:80]!r}"
    return None


def extract_fenced(text: str) -> str:
    """The body of the first ``` fence in ``text``; the whole text when there
    is no fence (a model that forgot the fence still gets validated)."""
    text = str(text or "")
    start = text.find("```")
    if start == -1:
        return text.strip()
    nl = text.find("\n", start)
    if nl == -1:
        return ""
    end = text.find("```", nl + 1)
    body = text[nl + 1 : end] if end != -1 else text[nl + 1 :]
    return body.strip("\n").rstrip()


def proposal_signature(agent: str, categories: list[str], session_ids: list[str]) -> str:
    raw = "|".join(
        [str(agent or ""), ",".join(sorted(set(categories))), ",".join(sorted(set(session_ids)))]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def proposal_view(p: CoachProposalRecord) -> dict[str, Any]:
    try:
        evidence = json.loads(p.evidence_json or "[]")
    except (TypeError, ValueError):
        evidence = []
    return {
        "id": p.id,
        "agent": p.agent,
        "kind": p.kind,
        "target": p.target,
        "before": p.before,
        "after": p.after,
        "rationale": p.rationale,
        "evidence": evidence,
        "categories": [c.get("category") for c in evidence if isinstance(c, dict)],
        "signature": p.signature,
        "status": p.status,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
    }


class CoachEngine:
    """See the module docstring. One per platform (``platform.coach``)."""

    def __init__(self, platform) -> None:
        self.p = platform
        self.last_reason: str = ""
        #: Per-agent copy of ``last_reason`` (review item 9): two agents' reasons
        #: must not read as each other's on ``GET /agents/{name}/coach``.
        self.last_reasons: dict[str, str] = {}

    # -- the target --------------------------------------------------------

    @staticmethod
    def roster_name(raw: str) -> str:
        """``scout`` → ``custom:scout``; a roster name or a builtin type
        (``builder``) passes through unchanged."""
        from ..core.models import AgentType

        name = (raw or "").strip()
        if name.startswith("custom:") or name.startswith("remote:"):
            return name
        if name in {t.value for t in AgentType}:
            return name
        return f"custom:{name}" if name else ""

    def _slug(self, roster: str) -> str | None:
        """The registry slug for a ``custom:<slug>`` roster name whose record
        exists; ``None`` for a builtin, a remote, or an unknown name."""
        if not roster.startswith("custom:"):
            return None
        slug = roster[len("custom:"):]
        registry = getattr(self.p, "agents_registry", None)
        if not slug or registry is None:
            return None
        try:
            return slug if registry.get(slug) is not None else None
        except Exception:  # noqa: BLE001 — a broken registry refuses, never guesses
            return None

    def _current_instructions(self, slug: str) -> str | None:
        rec = self.p.agents_registry.get(slug)
        return None if rec is None else str(rec.system_prompt or "")

    # -- the pure report -----------------------------------------------------

    def report(self, roster_name: str) -> dict[str, Any]:
        """Ledger signals for the agent's recent runs + their clusters. No
        model, no side effects. Works for any roster name that has runs."""
        roster = self.roster_name(roster_name)
        runs = recent_runs(self.p, roster)
        signals = [run_signals(self.p, s) for s in runs]
        found = clusters(signals)
        return {
            "agent": roster,
            "runs": signals,
            "clusters": [c.to_dict() for c in found],
            "categories": dict(CATEGORIES),
        }

    # -- the honest-mock guard ----------------------------------------------

    def _real_provider_available(self) -> bool:
        """True only when a REAL adapter can answer — the default provider
        when it is not the offline mock, else any available failover
        candidate (the same walk ``app._skill_distill_complete`` makes). Mock
        is never a real provider here."""
        from ..providers.adapters.mock import MockLLMAdapter

        providers = getattr(self.p, "providers", None)
        if providers is None:
            return False
        cfg = self.p.config
        try:
            adapter = providers.get(cfg.default_provider, cfg.default_model)
        except Exception:  # noqa: BLE001 — fall through to the failover walk
            adapter = None
        if adapter is not None and not isinstance(adapter, MockLLMAdapter):
            return True
        try:
            from ..providers.router import _FAILOVER_ORDER
        except Exception:  # noqa: BLE001
            return False
        for name in _FAILOVER_ORDER:
            if name == "mock":
                continue
            try:
                if not providers.available(name):
                    continue
                candidate = providers.get(name)
            except Exception:  # noqa: BLE001 — try the next one
                continue
            if candidate is not None and not isinstance(candidate, MockLLMAdapter):
                return True
        return False

    # -- dedupe --------------------------------------------------------------

    def _suppressed(self, signature: str) -> str | None:
        with session_scope(self.p.engine) as db:
            rows = list(
                db.exec(
                    select(CoachProposalRecord).where(
                        CoachProposalRecord.signature == signature
                    )
                )
            )
        cutoff = as_naive_utc(utcnow() - timedelta(days=DECLINE_SUPPRESS_DAYS))
        for row in rows:
            if row.status == PENDING:
                return REASON_PENDING
        for row in rows:
            decided = as_naive_utc(row.decided_at)
            if row.status == DECLINED and decided and decided >= cutoff:
                return REASON_DECLINED.format(days=DECLINE_SUPPRESS_DAYS)
            # An ACCEPTED one too (review item 10): the same evidence would
            # otherwise be re-proposed the moment it was acted on, before a
            # single new run could show whether the change helped.
            if row.status == ACCEPTED and decided and decided >= cutoff:
                return REASON_ACCEPTED.format(days=DECLINE_SUPPRESS_DAYS)
        return None

    # -- the one model call ---------------------------------------------------

    def _prompt(self, instructions: str, found: list[Cluster]) -> tuple[str, str]:
        system = (
            "You are the reflection coach for one custom agent inside Iron Jarvis. "
            "You are given the agent's CURRENT instructions and the patterns its "
            "recent runs showed (fixed categories, with quotes from the ledger). "
            "Propose the SMALLEST edit to the instructions that addresses the "
            "heaviest pattern: add or adjust at most 3 sentences. Never remove or "
            "weaken the agent's identity, role or any line that says never/always. "
            "Do not rewrite the voice. Output ONLY the full new instructions text "
            "inside one ``` fence — no commentary before or after it."
        )
        lines = ["CURRENT INSTRUCTIONS:", "```", instructions[:PROMPT_INSTRUCTIONS_CHARS], "```", "", "PATTERNS (heaviest first):"]
        for c in found:
            lines.append(f"- {c.category} ×{c.count}: {CATEGORIES.get(c.category, '')}")
            for ev in c.evidence[:QUOTES_PER_CLUSTER]:
                lines.append(f"    {ev.get('session_id')}: {ev.get('quote')}")
        lines.append("")
        lines.append("Reply with ONLY the full new instructions inside a ``` fence.")
        return system, "\n".join(lines)

    async def _complete(self, system: str, prompt: str) -> str | None:
        """ONE router completion, the way ``ImprovementEngine._router_reflect``
        makes it. ``None`` on any failure — never an exception to the caller."""
        from ..providers.adapters.base import LLMMessage

        router = getattr(self.p, "router", None)
        if router is None:
            return None
        try:
            route = await router.complete(
                system=system,
                messages=[LLMMessage(role="user", content=prompt)],
                tools=[],
            )
        except Exception:  # noqa: BLE001 — a model error is a reason, not a crash
            log.exception("coach model call failed")
            return None
        response = getattr(route, "response", route)
        return str(getattr(response, "text", "") or "")

    @staticmethod
    def _rationale(found: list[Cluster], run_count: int) -> str:
        parts = []
        for c in found:
            ids = ", ".join(dict.fromkeys(e.get("session_id", "") for e in c.evidence))
            parts.append(f"{c.category} ×{c.count} ({ids})")
        text = f"Across {run_count} recent runs: " + "; ".join(parts) + "."
        return text[:RATIONALE_CHARS]

    def last_reason_for(self, roster_name: str) -> str:
        """The reason the last ``propose`` for THIS agent minted nothing ("" when
        it minted, or never ran)."""
        return self.last_reasons.get(self.roster_name(roster_name), "")

    async def propose(self, roster_name: str) -> CoachProposalRecord | None:
        """Mint ONE pending proposal for a custom agent, or ``None`` with the
        reason on ``last_reason`` / ``last_reason_for(name)``. The ledger reads
        run off-loop."""
        roster = self.roster_name(roster_name)
        try:
            return await self._propose(roster)
        finally:
            self.last_reasons[roster] = self.last_reason

    async def _propose(self, roster: str) -> CoachProposalRecord | None:
        slug = self._slug(roster)
        if slug is None:
            self.last_reason = REASON_BUILTIN if not roster.startswith("custom:") else REASON_UNKNOWN
            return None
        report = await asyncio.to_thread(self.report, roster)
        runs = report["runs"]
        if len(runs) < MIN_RUNS:
            self.last_reason = REASON_FEW_RUNS.format(n=MIN_RUNS)
            return None
        found = clusters(runs)
        if not found:
            self.last_reason = REASON_NO_CLUSTERS
            return None
        if not self._real_provider_available():
            self.last_reason = REASON_NO_MODEL
            return None
        session_ids = [e["session_id"] for c in found for e in c.evidence]
        signature = proposal_signature(roster, [c.category for c in found], session_ids)
        suppressed = await asyncio.to_thread(self._suppressed, signature)
        if suppressed:
            self.last_reason = suppressed
            return None
        before = self._current_instructions(slug)
        if before is None:
            self.last_reason = REASON_UNKNOWN
            return None
        system, prompt = self._prompt(before, found)
        raw = await self._complete(system, prompt)
        if raw is None:
            self.last_reason = REASON_MODEL_FAILED
            return None
        after = extract_fenced(raw)
        problem = validate_revision(before, after)
        if problem:
            log.info("coach refused its own draft for %s: %s", roster, problem)
            self.last_reason = f"{REASON_NOTHING_SMALL} ({problem})"
            return None
        record = CoachProposalRecord(
            agent=roster,
            kind=KIND_INSTRUCTIONS,
            target=slug,
            before=before,
            after=after,
            rationale=self._rationale(found, len(runs)),
            evidence_json=json.dumps([c.to_dict() for c in found]),
            signature=signature,
            status=PENDING,
        )
        with session_scope(self.p.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
            db.expunge(record)
        self.last_reason = ""
        await self._publish(record, [c.category for c in found])
        return record

    async def _publish(self, record: CoachProposalRecord, categories: list[str]) -> None:
        bus = getattr(self.p, "event_bus", None)
        if bus is None:
            return
        try:
            await bus.publish(
                EventType.COACH_PROPOSAL,
                {
                    "id": record.id,
                    "agent": record.agent,
                    "categories": categories,
                    "rationale": record.rationale,
                },
            )
        except Exception:  # noqa: BLE001 — a bus hiccup never loses the row
            log.debug("coach.proposal publish failed", exc_info=True)

    # -- decisions -------------------------------------------------------------

    def get(self, proposal_id: str) -> CoachProposalRecord | None:
        with session_scope(self.p.engine) as db:
            row = db.get(CoachProposalRecord, proposal_id)
            if row is not None:
                db.expunge(row)
            return row

    def list(
        self, agent: str | None = None, status: str | None = None
    ) -> list[CoachProposalRecord]:
        stmt = select(CoachProposalRecord)
        if agent:
            stmt = stmt.where(CoachProposalRecord.agent == self.roster_name(agent))
        if status:
            stmt = stmt.where(CoachProposalRecord.status == status)
        stmt = stmt.order_by(CoachProposalRecord.created_at.desc())  # type: ignore[attr-defined]
        with session_scope(self.p.engine) as db:
            rows = list(db.exec(stmt))
            for row in rows:
                db.expunge(row)
            return rows

    def _decide(self, proposal_id: str, status: str) -> CoachProposalRecord:
        with session_scope(self.p.engine) as db:
            row = db.get(CoachProposalRecord, proposal_id)
            if row is None:
                raise KeyError(f"no such proposal {proposal_id!r}")
            if row.status != PENDING:
                raise ValueError(f"this proposal was already {row.status}")
            row.status = status
            row.decided_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
            return row

    def decline(self, proposal_id: str) -> CoachProposalRecord:
        return self._decide(proposal_id, DECLINED)

    def accept(self, proposal_id: str) -> CoachProposalRecord:
        """Write ``after`` as the agent's instructions through the registry
        door (``prompt_reason="coach"`` when the registry takes it). A record
        whose instructions moved since mint is marked ``stale`` and
        :class:`StaleProposal` carries the 409 sentence."""
        row = self.get(proposal_id)
        if row is None:
            raise KeyError(f"no such proposal {proposal_id!r}")
        if row.status != PENDING:
            raise ValueError(f"this proposal was already {row.status}")
        registry = self.p.agents_registry
        rec = registry.get(row.target)
        if rec is None or str(rec.system_prompt or "") != row.before:
            self._decide(proposal_id, STALE)
            raise StaleProposal(
                STALE_SENTENCE if rec is not None else "the agent no longer exists — nothing to write"
            )
        try:
            tools = json.loads(rec.tools_json or "[]")
        except (TypeError, ValueError):
            tools = []
        kwargs: dict[str, Any] = {
            "base_type": rec.base_type,
            "description": rec.description,
            "provider": rec.provider,
            "model": rec.model,
        }
        try:
            if "prompt_reason" in inspect.signature(registry.register).parameters:
                kwargs["prompt_reason"] = "coach"
        except (TypeError, ValueError):
            pass
        registry.register(row.target, row.after, list(tools), **kwargs)
        return self._decide(proposal_id, ACCEPTED)
