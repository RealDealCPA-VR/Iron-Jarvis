"""Skill Learning Engine — skills that teach themselves into existence.

``skill_create`` (v1.90.0) lets an agent SAVE a proven approach, but only when
someone thinks to ask. This engine closes the loop autonomously, in two lanes:

* **create** — a finished session that solved a real multi-step task WITHOUT a
  skill becomes a :class:`~.learning_models.SkillCandidateRecord`; a distill
  sweep turns it into a draft SKILL.md (agentskills.io format) for review.
* **refine** — every skill use (derived post-session from successful
  ``skill_load`` invocations) accumulates outcome stats; when a skill's session
  fails or scores low, a refinement candidate is minted and distilled into a
  full replacement body (with the previous body kept for the diff).

Both lanes are SUGGEST-ONLY: nothing lands in the skills directory until
:meth:`SkillLearningEngine.approve` runs — unless the user flips the explicit
``skill_learning_auto_approve`` setting. The split mirrors the codebase's
learning pattern: :meth:`observe_session` is cheap, deterministic, pure-DB and
NEVER raises (it runs on every session completion); :meth:`distill_candidates`
is model-backed and only ever runs through a REAL provider the daemon supplies
as an injected ``complete`` callable (a fabricated skill from a mock would be
worse than none — crystallize's honest-mock rule).

Scope note (v1): chat NON-agent turns are out of scope. They have no post-turn
learning hook and file their ToolInvocations under the literal session id
``"chat"``, so there is no scored session to observe. Agent-mode chat funnels
skill use through the ``skill_load`` tool inside a real session and IS covered.
"""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from sqlmodel import select

from ..core.db import session_scope
from ..core.ids import utcnow
from ..core.logging import get_logger
from ..core.models import Session, ToolInvocation
from ..improvement.models import OutcomeRecord
from .framework import _tokens
from .loader import SKILL_FILE, _parse_frontmatter, save_skill, slugify
from .learning_models import (
    SkillCandidateRecord,
    SkillProposalRecord,
    SkillStatRecord,
    SkillUseRecord,
)

log = get_logger("skill_learning")

#: The daemon-supplied model call: ``async (system, prompt) -> reply text``.
Complete = Callable[[str, str], Awaitable[str]]

#: A session at or below this score counts as low (parity with the
#: ImprovementEngine's ``_LOW_SCORE``) and sends its skills to the refine lane.
_LOW_SCORE = 0.6
#: Create-lane bar: a task is only skill-worthy past this much tool work.
_MIN_TOOL_CALLS = 3
_MIN_DISTINCT_TOOLS = 2
#: Unusable distillation replies before a candidate is dismissed for good.
_MAX_ATTEMPTS = 3
#: Hardening caps on model output (the body is MODEL OUTPUT, possibly steered
#: by untrusted session content — mirror chat's ``_sanitize_draft`` spirit).
_MAX_NAME = 80
_MAX_DESCRIPTION = 300
_MIN_BODY = 30
_MAX_BODY = 20_000
#: A reply longer than this is truncated BEFORE any parsing — a legitimate
#: SKILL.md is well under this, and it bounds the YAML the parser ever sees
#: (a frontmatter bomb gets its closing ``---`` truncated away and is rejected).
_MAX_REPLY = 200_000
#: Context caps for distillation prompts.
_MAX_CONTEXT_TOOLS = 30
_SNIPPET = 200
#: Create-lane dedup vs existing skills: a registry hit whose name+description
#: shares at least ``max(2, ceil(0.6 * task tokens))`` tokens with the task is
#: treated as "a skill for this already exists".
_DEDUP_MIN_OVERLAP = 2
_DEDUP_FRACTION = 0.6
#: Refine proposals only ever target skills whose registry source is one of
#: these. "user" skills live in the writable root save_skill targets. "builtin"
#: is included because the user root SHADOWS builtin on repopulate (proven by
#: test_user_root_shadows_builtin_on_repopulate — ``repopulate`` discovers
#: builtin first, then user, and ``discover`` is last-wins on name collision),
#: so refining a shipped skill is copy-on-write, never an in-place rewrite.
#: Never claude/codex/custom — those roots belong to other tools.
_REFINE_SOURCES = ("user", "builtin")

_CREATE_SYSTEM = (
    "You write reusable agent skills in the agentskills.io SKILL.md format. "
    "Reply with ONLY the full SKILL.md file content: YAML frontmatter between "
    "--- lines carrying `name` (short, kebab-case) and `description` (one line "
    "saying WHEN to use the skill), then a markdown body with concrete, "
    "numbered steps naming the exact tools used. GENERALIZE one-off specifics "
    "(a particular file name, date, client) into role words so the skill works "
    "next time too. Base the steps ONLY on what the session actually did; "
    "never invent work that didn't happen. No prose or code fences around the "
    "file."
)

_REFINE_SYSTEM = (
    "You improve an existing agent skill in the agentskills.io SKILL.md "
    "format. You are given the current SKILL.md and evidence from sessions "
    "where the skill underperformed. Reply with ONLY the full REPLACEMENT "
    "SKILL.md file content (keep the same `name` in the frontmatter): sharpen "
    "the steps, add the missing guidance the evidence points at, and keep "
    "everything that already works. No prose or code fences around the file."
)


def _signature(task: str) -> str:
    """Normalized create-lane dedup key for a task."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (task or "").lower()).strip()
    return f"create::{normalized[:200]}"


def _compose_skill_md(name: str, description: str, body: str) -> str:
    """Canonical SKILL.md text (same shape ``save_skill`` writes)."""
    front = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{front}\n---\n\n{body}\n"


class SkillLearningEngine:
    """Observes finished sessions, distils skill drafts, and manages review."""

    def __init__(self, platform, *, on_proposal=None) -> None:
        self.p = platform
        self.engine = platform.engine
        #: Optional best-effort callback the daemon sets to publish the
        #: ``skill.proposal_created`` event: called with the freshly minted
        #: :class:`SkillProposalRecord` (status "approved" when auto-approve
        #: kicked in, "pending" otherwise). Errors are logged, never raised.
        self.on_proposal = on_proposal
        # Serializes the use/stat read-modify-write and candidate gate+insert so
        # a scheduled-workflow session finalizing on the APScheduler thread
        # can't race a main-loop completion (same reason as ImprovementEngine).
        self._lock = threading.Lock()
        # Distill debounce: the SESSION_COMPLETED handler and the manual
        # "Distill now" route may overlap; a second sweep no-ops honestly.
        self._distilling = False

    # -- defensive platform reads (other pairs own config/platform wiring) ---

    def _registry(self):
        return getattr(self.p, "skills", None)

    def _config(self):
        return getattr(self.p, "config", None)

    def _enabled(self) -> bool:
        return bool(getattr(self._config(), "skill_learning_enabled", True))

    # -- observation: pure-DB, deterministic, runs on EVERY completion --------

    def observe_session(self, session) -> None:
        """Record skill uses + stats and mint candidates. NEVER raises.

        ``session`` is a :class:`Session` row or its id. Reads the session, its
        ToolInvocation rows, and the ImprovementEngine's already-written
        OutcomeRecord (never re-scores). Idempotent: a re-observed session
        (retry/resume) writes nothing twice.
        """
        try:
            self._observe(session)
        except Exception:  # noqa: BLE001 - the completion hook must never break a run
            sid = getattr(session, "id", session)
            log.exception("skill-learning observe failed for session %s", sid)

    def _observe(self, session) -> None:
        sid = session if isinstance(session, str) else str(getattr(session, "id", "") or "")
        if not sid:
            return
        with session_scope(self.engine) as db:
            sess = db.get(Session, sid)
            if sess is None:
                return
            task = (sess.task or "").strip()
            status = getattr(sess.status, "value", str(sess.status))
            invocations = list(
                db.exec(
                    select(ToolInvocation).where(ToolInvocation.session_id == sid)
                )
            )
            seen = (
                db.exec(
                    select(SkillUseRecord).where(SkillUseRecord.session_id == sid)
                ).first()
                is not None
                or db.exec(
                    select(SkillCandidateRecord).where(
                        SkillCandidateRecord.session_id == sid
                    )
                ).first()
                is not None
            )
        if seen:
            return  # already observed — a rerun must not double-count stats

        used = self._skill_uses(invocations)
        if used:
            score, success = self._session_outcome(sid, status)
            self._record_uses(sid, task, used, score, success)
            return  # skill-assisted runs feed the refine lane, never create
        self._maybe_create_candidate(sid, task, status, invocations)

    def _skill_uses(self, invocations: list[ToolInvocation]) -> list[str]:
        """Registry-resolved skill names loaded in the session (deduped order).

        Derived from SUCCESSFUL ``skill_load`` rows: the args schema is
        ``{"name": ...}``; malformed/redacted JSON and loads of unknown skills
        are skipped (a failed load attempt is not skill assistance).
        """
        registry = self._registry()
        seen: dict[str, None] = {}
        for inv in invocations:
            if inv.tool != "skill_load" or not inv.ok:
                continue
            try:
                args = json.loads(inv.args_json or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(args, dict):
                continue
            name = str(args.get("name") or "").strip()
            if not name:
                continue
            if registry is None or registry.get(name) is None:
                continue
            seen.setdefault(name, None)
        return list(seen)

    def _session_outcome(self, sid: str, status: str) -> tuple[float, bool]:
        """The session's ALREADY-RECORDED outcome (never re-scored). Falls back
        to the session status when the ImprovementEngine wrote no row."""
        with session_scope(self.engine) as db:
            outcome = db.exec(
                select(OutcomeRecord).where(OutcomeRecord.session_id == sid)
            ).first()
        if outcome is not None:
            return float(outcome.score), bool(outcome.success)
        ok = status == "completed"
        return (1.0 if ok else 0.0), ok

    def _record_uses(
        self, sid: str, task: str, names: list[str], score: float, success: bool
    ) -> None:
        now = utcnow()
        with self._lock, session_scope(self.engine) as db:
            for name in names:
                db.add(SkillUseRecord(session_id=sid, skill_name=name))
                s = db.get(SkillStatRecord, name) or SkillStatRecord(skill_name=name)
                s.use_count += 1
                s.score_sum += score
                s.success_count += int(success)
                s.last_used_at = now
                db.add(s)
            db.commit()
        # Use/stat tracking is passive telemetry and always on; only candidate
        # minting (the learning behaviour the toggle promises) is gated.
        if not self._enabled():
            return
        if success and score > _LOW_SCORE:
            return
        for name in names:
            self._maybe_refine_candidate(sid, task, name)

    def _maybe_refine_candidate(self, sid: str, task: str, name: str) -> None:
        registry = self._registry()
        skill = registry.get(name) if registry is not None else None
        if skill is None or getattr(skill, "source", "") not in _REFINE_SOURCES:
            return  # never rewrite claude/codex/custom roots — not ours
        with self._lock, session_scope(self.engine) as db:
            cand = db.exec(
                select(SkillCandidateRecord).where(
                    SkillCandidateRecord.kind == "refine",
                    SkillCandidateRecord.skill_name == name,
                    SkillCandidateRecord.status == "pending",
                )
            ).first()
            if cand is not None:
                return
            prop = db.exec(
                select(SkillProposalRecord).where(
                    SkillProposalRecord.kind == "refine",
                    SkillProposalRecord.skill_name == name,
                    SkillProposalRecord.status == "pending",
                )
            ).first()
            if prop is not None:
                return  # an improvement is already awaiting review
            db.add(
                SkillCandidateRecord(
                    session_id=sid,
                    task=task,
                    kind="refine",
                    skill_name=name,
                    signature=f"refine::{name}",
                )
            )
            db.commit()

    def _maybe_create_candidate(
        self, sid: str, task: str, status: str, invocations: list[ToolInvocation]
    ) -> None:
        if not self._enabled():
            return
        if status != "completed" or not task:
            return
        if len(invocations) < _MIN_TOOL_CALLS:
            return
        if len({i.tool for i in invocations if i.tool}) < _MIN_DISTINCT_TOOLS:
            return
        if self._matches_existing_skill(task):
            return
        sig = _signature(task)
        with self._lock, session_scope(self.engine) as db:
            cand = db.exec(
                select(SkillCandidateRecord).where(
                    SkillCandidateRecord.signature == sig,
                    SkillCandidateRecord.status == "pending",
                )
            ).first()
            if cand is not None:
                return
            props = list(
                db.exec(
                    select(SkillProposalRecord).where(
                        SkillProposalRecord.signature == sig
                    )
                )
            )
            # A pending proposal is a dup; a REJECTED one is the user saying
            # "not this" — suppress re-proposing the same procedure.
            if any(p.status in ("pending", "rejected") for p in props):
                return
            db.add(
                SkillCandidateRecord(
                    session_id=sid, task=task, kind="create", signature=sig
                )
            )
            db.commit()

    def _matches_existing_skill(self, task: str) -> bool:
        """True when a registry skill already covers this task (token overlap
        of the task vs the hit's name+description clears the dedup bar)."""
        registry = self._registry()
        if registry is None:
            return False
        terms = set(_tokens(task))
        if not terms:
            return False
        try:
            hits = registry.search(task, 5)
        except Exception:  # noqa: BLE001 - a search hiccup must not block observation
            return False
        needed = max(_DEDUP_MIN_OVERLAP, math.ceil(len(terms) * _DEDUP_FRACTION))
        for skill in hits:
            hay = set(_tokens(f"{skill.name} {skill.description}"))
            if len(terms & hay) >= needed:
                return True
        return False

    # -- distillation: model-backed, real provider only ----------------------

    async def distill_candidates(self, complete: Complete, *, limit: int = 3) -> dict:
        """Turn pending candidates into reviewable proposals (oldest first).

        ``complete`` is an async ``(system, prompt) -> reply`` the DAEMON
        supplies, wired to a REAL provider — never mock (a fabricated skill
        draft would poison future runs; the wiring returns silently under
        mock instead of calling this). An unusable reply leaves the candidate
        pending for the next sweep, bounded at ``_MAX_ATTEMPTS`` tries before
        it is dismissed. Overlapping sweeps no-op honestly.
        """
        if self._distilling:
            return {
                "reviewed": 0,
                "proposals": [],
                "dismissed": 0,
                "note": "a distill sweep is already running",
            }
        self._distilling = True
        try:
            return await self._distill(complete, limit=limit)
        finally:
            self._distilling = False

    async def _distill(self, complete: Complete, *, limit: int) -> dict:
        if not self._enabled():
            return {
                "reviewed": 0,
                "proposals": [],
                "dismissed": 0,
                "note": "skill learning is disabled",
            }
        with session_scope(self.engine) as db:
            candidates = list(
                db.exec(
                    select(SkillCandidateRecord)
                    .where(SkillCandidateRecord.status == "pending")
                    .order_by(SkillCandidateRecord.created_at.asc())
                    .limit(max(1, int(limit)))
                )
            )
        proposal_ids: list[str] = []
        dismissed = 0
        for cand in candidates:
            if cand.kind == "refine":
                outcome = await self._distill_refine(cand, complete)
            else:
                outcome = await self._distill_create(cand, complete)
            if isinstance(outcome, SkillProposalRecord):
                self._set_candidate_status(cand.id, "distilled")
                outcome = self._maybe_auto_approve(outcome)
                proposal_ids.append(outcome.id)
                self._notify(outcome)
            elif outcome == "dismiss":  # unfixable (e.g. the skill vanished)
                self._set_candidate_status(cand.id, "dismissed")
                dismissed += 1
            else:  # unusable reply — bounded retry
                dismissed += self._bump_attempts(cand.id)
        return {
            "reviewed": len(candidates),
            "proposals": proposal_ids,
            "dismissed": dismissed,
        }

    async def _distill_create(
        self, cand: SkillCandidateRecord, complete: Complete
    ) -> "SkillProposalRecord | str | None":
        task, summary, tool_lines = self._session_context(cand.session_id, cand.task)
        prompt = (
            "A finished agent session solved this task successfully. Capture "
            "the repeatable procedure as a skill.\n\n"
            f"Task: {task}\n"
            f"Outcome summary: {summary or '(none recorded)'}\n"
            f"Tool history:\n{tool_lines or '(no tool detail available)'}\n\n"
            "Write the full SKILL.md now."
        )
        reply = await complete(_CREATE_SYSTEM, prompt)
        name, description, body = self._parse_skill_md(reply)
        if not name:
            return None
        if self._slug_taken(slugify(name)):
            return None  # must be unique vs registry AND other pending drafts
        return self._mint_proposal(
            kind="create",
            skill_name=name,
            description=description,
            body_md=_compose_skill_md(name, description, body),
            prev_body_md="",
            source_session_ids=[cand.session_id],
            signature=cand.signature,
        )

    async def _distill_refine(
        self, cand: SkillCandidateRecord, complete: Complete
    ) -> "SkillProposalRecord | str | None":
        registry = self._registry()
        skill = registry.get(cand.skill_name) if registry is not None else None
        if skill is None:
            return "dismiss"  # the skill is gone — nothing left to refine
        prev_body = self._read_skill_md(skill)
        evidence = self._refine_evidence(cand)
        prompt = (
            f"Current SKILL.md for '{skill.name}':\n\n{prev_body[:_MAX_BODY]}\n\n"
            f"Evidence of underperformance:\n{evidence}\n\n"
            "Write the full replacement SKILL.md now (same name)."
        )
        reply = await complete(_REFINE_SYSTEM, prompt)
        _name, description, body = self._parse_skill_md(reply)
        if not body:
            return None
        # Refine always targets the ORIGINAL skill, whatever the model wrote.
        name = skill.name
        description = description or skill.description
        return self._mint_proposal(
            kind="refine",
            skill_name=name,
            description=description,
            body_md=_compose_skill_md(name, description, body),
            prev_body_md=prev_body,
            source_session_ids=[cand.session_id],
            signature=f"refine::{name}",
        )

    def _session_context(self, sid: str, task: str) -> tuple[str, str, str]:
        """(task, summary, capped tool-history lines) for a create prompt.

        Prefers the orchestrator's ``transcript()`` (the evidence source the
        review path uses too); falls back to a direct ToolInvocation read when
        no orchestrator is attached (bare-platform installs)."""
        summary = ""
        with session_scope(self.engine) as db:
            sess = db.get(Session, sid)
            if sess is not None:
                summary = (sess.summary or "").strip()
                task = task or (sess.task or "").strip()
        tools: list[dict] = []
        orch = getattr(self.p, "orchestrator", None)
        if orch is not None:
            try:
                tools = list(orch.transcript(sid).get("tools") or [])
            except Exception:  # noqa: BLE001 - fall back to the direct read
                tools = []
        if not tools:
            with session_scope(self.engine) as db:
                rows = list(
                    db.exec(
                        select(ToolInvocation).where(
                            ToolInvocation.session_id == sid
                        )
                    )
                )
            tools = [r.model_dump() for r in rows]
        lines = []
        for t in tools[:_MAX_CONTEXT_TOOLS]:
            tool = str(t.get("tool") or "")
            status = "ok" if t.get("ok") else "FAILED"
            args = str(t.get("args_json") or "")[:_SNIPPET]
            output = str(t.get("output") or "").replace("\n", " ")[:_SNIPPET]
            lines.append(f"- {tool} ({status}) args={args} -> {output}")
        return task, summary[:2000], "\n".join(lines)

    def _refine_evidence(self, cand: SkillCandidateRecord) -> str:
        parts: list[str] = []
        with session_scope(self.engine) as db:
            sess = db.get(Session, cand.session_id)
            outcome = db.exec(
                select(OutcomeRecord).where(
                    OutcomeRecord.session_id == cand.session_id
                )
            ).first()
            stat = db.get(SkillStatRecord, cand.skill_name)
        if sess is not None:
            parts.append(f"- Task: {(sess.task or '').strip()[:400]}")
            if (sess.summary or "").strip():
                parts.append(f"- Outcome: {sess.summary.strip()[:400]}")
            parts.append(
                f"- Session status: {getattr(sess.status, 'value', sess.status)}"
            )
        if outcome is not None:
            parts.append(
                f"- Score: {outcome.score} (success={bool(outcome.success)})"
            )
        if stat is not None and stat.use_count:
            parts.append(
                f"- Lifetime: {stat.use_count} uses, "
                f"{round(stat.success_count / stat.use_count, 2)} success rate, "
                f"{round(stat.score_sum / stat.use_count, 2)} avg score"
            )
        return "\n".join(parts) or "- (no session evidence available)"

    def _parse_skill_md(self, reply: str) -> tuple[str, str, str]:
        """Harden a model reply into (name, description, body) or empties.

        Tolerates a surrounding code fence and prose before the frontmatter;
        requires parseable frontmatter with a non-empty name and a non-trivial
        body, and caps every field (the reply is model output, possibly
        steered by untrusted session content)."""
        text = (reply or "").strip()[:_MAX_REPLY]
        # Strip a code fence ONLY when it wraps the whole file: greedy to the
        # LAST closing fence (so ``` blocks INSIDE the body survive) and gated
        # on the fenced content starting with frontmatter (so a body that ENDS
        # with a code block is never mistaken for a wrapper and swallowed).
        fence = re.search(
            r"```(?:markdown|md|yaml)?[ \t]*\n(.*)\n?```\s*\Z", text, re.DOTALL
        )
        if fence and fence.group(1).lstrip().startswith("---"):
            text = fence.group(1).strip()
        # The frontmatter opens at a `---` LINE (prose like "sure --- here" or
        # an inline dash run must not be mistaken for the delimiter).
        start = re.search(r"^---[ \t]*$", text, re.MULTILINE)
        if start is None:
            return "", "", ""
        text = text[start.start() :]
        try:
            meta, body = _parse_frontmatter(text)
        except Exception:  # noqa: BLE001 - malformed YAML from the model
            return "", "", ""
        name = str(meta.get("name") or "").strip()[:_MAX_NAME]
        description = str(meta.get("description") or "").strip()[:_MAX_DESCRIPTION]
        body = (body or "").strip()[:_MAX_BODY]
        if not name or len(body) < _MIN_BODY:
            return "", "", ""
        return name, description, body

    def _slug_taken(self, slug: str) -> bool:
        taken: set[str] = set()
        registry = self._registry()
        if registry is not None:
            try:
                taken = {slugify(s.name) for s in registry.list()}
            except Exception:  # noqa: BLE001 - degrade to DB-only dedup
                pass
        with session_scope(self.engine) as db:
            pending = list(
                db.exec(
                    select(SkillProposalRecord).where(
                        SkillProposalRecord.status == "pending"
                    )
                )
            )
        taken |= {slugify(p.skill_name) for p in pending if p.skill_name}
        return slug in taken

    def _mint_proposal(
        self,
        *,
        kind: str,
        skill_name: str,
        description: str,
        body_md: str,
        prev_body_md: str,
        source_session_ids: list[str],
        signature: str,
    ) -> SkillProposalRecord:
        record = SkillProposalRecord(
            kind=kind,
            skill_name=skill_name,
            description=description,
            body_md=body_md,
            prev_body_md=prev_body_md,
            source_session_ids=json.dumps(source_session_ids),
            signature=signature,
        )
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
        return record

    def _set_candidate_status(self, cand_id: str, status: str) -> None:
        with session_scope(self.engine) as db:
            row = db.get(SkillCandidateRecord, cand_id)
            if row is not None:
                row.status = status
                db.add(row)
                db.commit()

    def _bump_attempts(self, cand_id: str) -> int:
        """Count an unusable reply; dismiss at the bound. Returns 1 iff dismissed."""
        with session_scope(self.engine) as db:
            row = db.get(SkillCandidateRecord, cand_id)
            if row is None:
                return 0
            row.attempts = int(row.attempts or 0) + 1
            if row.attempts >= _MAX_ATTEMPTS:
                row.status = "dismissed"
            db.add(row)
            db.commit()
            return 1 if row.status == "dismissed" else 0

    def _maybe_auto_approve(self, proposal: SkillProposalRecord) -> SkillProposalRecord:
        if not bool(getattr(self._config(), "skill_learning_auto_approve", False)):
            return proposal
        try:
            return self.approve(proposal.id)
        except Exception:  # noqa: BLE001 - a failed auto-approve leaves it reviewable
            log.exception("auto-approve failed for proposal %s", proposal.id)
            return proposal

    def _notify(self, proposal: SkillProposalRecord) -> None:
        cb = self.on_proposal
        if cb is None:
            return
        try:
            cb(proposal)
        except Exception:  # noqa: BLE001 - event publishing is best-effort
            log.exception("on_proposal callback failed for %s", proposal.id)

    # -- proposal lifecycle ---------------------------------------------------

    def approve(self, proposal_id: str, *, body_md: str | None = None) -> SkillProposalRecord:
        """Write the proposal's skill to disk and mark it approved.

        An edited ``body_md`` wins over the stored draft. CREATE never
        clobbers: an existing slug (registry or disk) gets a ``-2``/``-3``
        suffix. REFINE intentionally overwrites the same user slug (that IS
        the update path — ``save_skill`` writes in place), falling back to
        create semantics if the skill vanished. The registry is repopulated in
        place so the result is searchable immediately. Raises ``ValueError``
        for an unknown or already-decided proposal.
        """
        with session_scope(self.engine) as db:
            row = db.get(SkillProposalRecord, proposal_id)
            if row is None:
                raise ValueError(f"no such proposal: {proposal_id}")
            if row.status != "pending":
                raise ValueError(f"proposal already {row.status}")
            kind = row.kind
            stored_body = row.body_md
            prop_name = row.skill_name
            prop_desc = row.description

        body = (body_md or "").strip() or stored_body
        name, description, instructions = self._split_body(body, prop_name, prop_desc)
        config = self._config()
        if config is None:
            raise ValueError("no config available to locate the skills directory")
        skills_root = Path(config.home) / "skills"

        registry = self._registry()
        if kind == "refine" and registry is not None and registry.get(prop_name) is not None:
            name = prop_name  # overwrite the same slug — the update path
        else:  # create, or a refine whose target vanished -> create semantics
            name = self._unclobbered_name(name, skills_root)
        save_skill(skills_root, name, description, instructions)

        if registry is not None:
            try:
                registry.repopulate(
                    config.home, getattr(config, "extra_skill_paths", None)
                )
            except Exception:  # noqa: BLE001 - the file is saved; next boot rescans
                log.exception("registry repopulate failed after approving %s", proposal_id)

        with session_scope(self.engine) as db:
            row = db.get(SkillProposalRecord, proposal_id)
            row.status = "approved"
            row.decided_at = utcnow()
            row.skill_name = name  # the name that actually landed (may be -2)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def reject(self, proposal_id: str) -> SkillProposalRecord:
        """Mark a pending proposal rejected; its signature suppresses
        re-proposing the same procedure in the create-lane gate. Raises
        ``ValueError`` for an unknown or already-decided proposal."""
        with session_scope(self.engine) as db:
            row = db.get(SkillProposalRecord, proposal_id)
            if row is None:
                raise ValueError(f"no such proposal: {proposal_id}")
            if row.status != "pending":
                raise ValueError(f"proposal already {row.status}")
            row.status = "rejected"
            row.decided_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def proposals(self, status: str | None = None) -> list[SkillProposalRecord]:
        """Proposals for the review UI: pending first, newest first within."""
        with session_scope(self.engine) as db:
            query = select(SkillProposalRecord)
            if status is not None:
                query = query.where(SkillProposalRecord.status == status)
            rows = list(db.exec(query))
        rows.sort(
            key=lambda r: (r.status != "pending", -(r.created_at.timestamp()))
        )
        return rows

    def _split_body(
        self, body: str, fallback_name: str, fallback_desc: str
    ) -> tuple[str, str, str]:
        """(name, description, instructions) from a (possibly edited) SKILL.md."""
        try:
            meta, instructions = _parse_frontmatter(body)
        except Exception:  # noqa: BLE001 - treat an unparseable edit as pure body
            meta, instructions = {}, body
        name = str(meta.get("name") or "").strip()[:_MAX_NAME] or fallback_name
        description = (
            str(meta.get("description") or "").strip()[:_MAX_DESCRIPTION]
            or fallback_desc
        )
        instructions = (instructions or "").strip()
        if not instructions:
            raise ValueError("proposal body has no instructions")
        return name, description, instructions

    def _unclobbered_name(self, name: str, skills_root: Path) -> str:
        """Suffix ``-2``/``-3`` until the slug is free in BOTH the registry and
        on disk (a registry-only check would still shadow-or-clobber a dir the
        registry hasn't rescanned yet)."""
        taken: set[str] = set()
        registry = self._registry()
        if registry is not None:
            try:
                taken = {slugify(s.name) for s in registry.list()}
            except Exception:  # noqa: BLE001 - fall through to the disk check
                pass
        candidate = name
        i = 2
        while slugify(candidate) in taken or (skills_root / slugify(candidate)).exists():
            candidate = f"{name}-{i}"
            i += 1
            if i > 50:  # defensive bound — 50 collisions means something is wrong
                break
        return candidate

    # -- read side ------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Per-skill outcome views + pending counts (never raises)."""
        try:
            return self._stats()
        except Exception:  # noqa: BLE001
            log.exception("skill-learning stats read failed")
            return {"skills": [], "pending_proposals": 0, "pending_candidates": 0}

    def _stats(self) -> dict[str, Any]:
        with session_scope(self.engine) as db:
            stat_rows = list(db.exec(select(SkillStatRecord)))
            pending_proposals = len(
                list(
                    db.exec(
                        select(SkillProposalRecord.id).where(
                            SkillProposalRecord.status == "pending"
                        )
                    )
                )
            )
            pending_candidates = len(
                list(
                    db.exec(
                        select(SkillCandidateRecord.id).where(
                            SkillCandidateRecord.status == "pending"
                        )
                    )
                )
            )
        views = []
        for s in stat_rows:
            n = s.use_count
            views.append(
                {
                    "skill_name": s.skill_name,
                    "use_count": n,
                    "avg_score": round(s.score_sum / n, 4) if n else None,
                    "success_rate": round(s.success_count / n, 4) if n else None,
                    "last_used_at": s.last_used_at,
                }
            )
        views.sort(key=lambda v: (-v["use_count"], v["skill_name"]))
        return {
            "skills": views,
            "pending_proposals": pending_proposals,
            "pending_candidates": pending_candidates,
        }

    def _read_skill_md(self, skill) -> str:
        """The skill's current on-disk SKILL.md (reconstructed if unreadable)."""
        try:
            return (Path(skill.dir) / SKILL_FILE).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A hand-mangled or oddly encoded file must not sink the whole
            # distill sweep — reconstruct from the registry's parsed copy.
            return _compose_skill_md(
                skill.name, skill.description, skill.instructions
            )
