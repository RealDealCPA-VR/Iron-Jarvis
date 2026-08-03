"""SkillLearningEngine tests — fully offline (DB rows + injected fake models).

Proves the suggest-only skill loop closes safely:
  * deterministic candidate gating on every completion (create + refine lanes,
    dedup vs existing skills / pending work / rejected proposals);
  * skill-use derivation from ``skill_load`` invocations (malformed args and
    failed loads never count) + rolling stat math;
  * model-backed distillation with injected fakes (valid reply -> proposal;
    garbage -> bounded retry then dismissed; name conflicts unusable);
  * approve writes via save_skill with create-never-clobbers / refine-overwrites
    semantics and a visible registry repopulate; reject suppresses re-proposal;
  * observe_session / stats never raise, even with poisoned dependencies.

External skill roots (~/.claude, ~/.codex) are stubbed out so the registry is
builtin + user only — hermetic on a dev box full of real Claude skills.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

import iron_jarvis.skills.learning_models  # noqa: F401  (register tables before init_db)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus, ToolInvocation
from iron_jarvis.improvement.models import OutcomeRecord
from iron_jarvis.platform import build_platform
from iron_jarvis.skills import framework
from iron_jarvis.skills.learning import SkillLearningEngine
from iron_jarvis.skills.learning_models import (
    SkillCandidateRecord,
    SkillProposalRecord,
    SkillStatRecord,
    SkillUseRecord,
)
from iron_jarvis.skills.loader import save_skill


@pytest.fixture
def platform(tmp_path, monkeypatch):
    # Hermetic registry: builtin + user roots only. On a dev box the real
    # ~/.claude and ~/.codex hold dozens of skills, which would make dedup and
    # slug-uniqueness assertions depend on host state.
    monkeypatch.setattr(framework, "external_skill_roots", lambda: [])
    monkeypatch.setattr(framework, "marketplace_catalog_dirs", lambda home=None: [])
    return build_platform(str(tmp_path))


@pytest.fixture
def learning(platform):
    return SkillLearningEngine(platform)


#: A tool history that clears the create-lane bar (3 calls, 3 distinct tools).
QUALIFYING_TOOLS = (
    ("read_file", True, "{}"),
    ("run_code", True, "{}"),
    ("write_file", True, "{}"),
)

#: Deliberately shares <60% of its tokens with every builtin skill description.
TASK = "reconcile q3 vendor ledger balances against bank statements"


def _count(engine, model) -> int:
    with session_scope(engine) as db:
        return len(list(db.exec(select(model))))


def _rows(engine, model) -> list:
    with session_scope(engine) as db:
        return list(db.exec(select(model)))


def _seed_session(
    platform,
    *,
    task: str = TASK,
    status: SessionStatus = SessionStatus.COMPLETED,
    tools=QUALIFYING_TOOLS,
    score: float | None = None,
    success: bool = False,
) -> str:
    """A finished Session + its ToolInvocation rows (+ optional OutcomeRecord,
    mirroring what the ImprovementEngine writes before observe runs)."""
    sess = Session(task=task, status=status)
    sid = sess.id  # capture before the scope closes (avoids detached refresh)
    with session_scope(platform.engine) as db:
        db.add(sess)
        for tool, ok, args in tools:
            db.add(
                ToolInvocation(
                    session_id=sid, agent_run_id="r", tool=tool, ok=ok, args_json=args
                )
            )
        if score is not None:
            db.add(OutcomeRecord(session_id=sid, score=score, success=success))
        db.commit()
    return sid


def _add_user_skill(
    platform,
    name: str = "invoice-chaser",
    description: str = "Chase overdue invoices with a polite nudge sequence.",
    instructions: str = "1. Find overdue invoices.\n2. Draft a polite nudge.",
) -> str:
    save_skill(platform.config.home / "skills", name, description, instructions)
    platform.skills.repopulate(platform.config.home, None)
    return name


def _load_args(name: str) -> str:
    return json.dumps({"name": name})


def _skill_md_reply(
    name: str = "vendor-ledger-reconciliation",
    description: str = "Use when reconciling vendor ledgers against bank statements.",
    body: str = (
        "# Steps\n\n"
        "1. Pull the vendor ledger with read_file.\n"
        "2. Compare balances with run_code.\n"
        "3. Write the reconciliation summary with write_file."
    ),
) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"


def _completer(*replies: str):
    """An injected fake ``complete`` (never a real model): returns ``replies``
    in order, repeating the last one; records every (system, prompt) call."""
    calls: list[tuple[str, str]] = []

    async def complete(system: str, prompt: str) -> str:
        calls.append((system, prompt))
        idx = min(len(calls) - 1, len(replies) - 1)
        return replies[idx]

    complete.calls = calls
    return complete


class _CfgOverride:
    """Wrap the real config with extra attributes the engine reads via getattr
    (the real fields are Pair B's config.py edit — out of this partition)."""

    def __init__(self, base, **over):
        self._base = base
        self._over = over

    def __getattr__(self, name):
        if name in ("_base", "_over"):
            raise AttributeError(name)
        if name in self._over:
            return self._over[name]
        return getattr(self._base, name)


# -- create-lane gating -------------------------------------------------------


def test_qualifying_session_mints_create_candidate(platform, learning):
    sid = _seed_session(platform)
    learning.observe_session(sid)
    cands = _rows(platform.engine, SkillCandidateRecord)
    assert len(cands) == 1
    c = cands[0]
    assert c.kind == "create" and c.status == "pending"
    assert c.session_id == sid and c.task == TASK
    assert c.signature.startswith("create::")


def test_failed_session_mints_no_create_candidate(platform, learning):
    sid = _seed_session(platform, status=SessionStatus.FAILED)
    learning.observe_session(sid)
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_too_few_tool_calls_no_candidate(platform, learning):
    sid = _seed_session(platform, tools=QUALIFYING_TOOLS[:2])
    learning.observe_session(sid)
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_too_few_distinct_tools_no_candidate(platform, learning):
    sid = _seed_session(platform, tools=(("shell", True, "{}"),) * 3)
    learning.observe_session(sid)
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_empty_task_no_candidate(platform, learning):
    sid = _seed_session(platform, task="   ")
    learning.observe_session(sid)
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_existing_skill_dedups_create_lane(platform, learning):
    # The builtin "research" skill's name+description covers this task text.
    sid = _seed_session(platform, task="research investigation sources")
    learning.observe_session(sid)
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_pending_candidate_dedups_same_task(platform, learning):
    learning.observe_session(_seed_session(platform))
    learning.observe_session(_seed_session(platform))  # same TASK, new session
    assert _count(platform.engine, SkillCandidateRecord) == 1


def test_pending_and_rejected_proposals_suppress_create(platform, learning):
    from iron_jarvis.skills.learning import _signature

    for status in ("pending", "rejected"):
        with session_scope(platform.engine) as db:
            db.add(
                SkillProposalRecord(
                    kind="create",
                    skill_name=f"x-{status}",
                    body_md="b",
                    signature=_signature(TASK),
                    status=status,
                )
            )
            db.commit()
        learning.observe_session(_seed_session(platform))
        assert _count(platform.engine, SkillCandidateRecord) == 0
        with session_scope(platform.engine) as db:  # reset for the next status
            for p in db.exec(select(SkillProposalRecord)):
                db.delete(p)
            db.commit()


def test_disabled_flag_blocks_candidates_but_not_use_stats(platform, learning):
    platform.config = _CfgOverride(platform.config, skill_learning_enabled=False)
    learning.observe_session(_seed_session(platform))
    assert _count(platform.engine, SkillCandidateRecord) == 0

    name = _add_user_skill(platform)
    sid = _seed_session(
        platform,
        tools=(("skill_load", True, _load_args(name)),),
        score=0.2,
        success=False,
    )
    learning.observe_session(sid)
    # Usage telemetry stays on; the learning behaviour (candidates) is gated.
    assert _count(platform.engine, SkillUseRecord) == 1
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_observe_is_idempotent(platform, learning):
    name = _add_user_skill(platform)
    sid = _seed_session(
        platform,
        tools=(("skill_load", True, _load_args(name)),),
        score=0.2,
        success=False,
    )
    learning.observe_session(sid)
    learning.observe_session(sid)  # a rerun/resume must not double-count
    assert _count(platform.engine, SkillUseRecord) == 1
    with session_scope(platform.engine) as db:
        stat = db.get(SkillStatRecord, name)
    assert stat.use_count == 1

    sid2 = _seed_session(platform)
    learning.observe_session(sid2)
    learning.observe_session(sid2)
    assert _count(platform.engine, SkillCandidateRecord) == 2  # 1 refine + 1 create


def test_observe_unknown_session_is_a_noop(platform, learning):
    learning.observe_session("session_nope")
    assert _count(platform.engine, SkillCandidateRecord) == 0
    assert _count(platform.engine, SkillUseRecord) == 0


# -- skill-use derivation + stat math ----------------------------------------


def test_use_derivation_guards_malformed_and_failed_loads(platform, learning):
    sid = _seed_session(
        platform,
        tools=(
            ("skill_load", True, _load_args("research")),  # counts (builtin)
            ("skill_load", True, "{not json"),  # malformed args -> skipped
            ("skill_load", False, _load_args("research")),  # failed -> skipped
            ("skill_load", True, _load_args("no-such-skill")),  # unresolved
            ("skill_load", True, json.dumps(["name"])),  # non-dict args
            ("read_file", True, "{}"),
            ("write_file", True, "{}"),
        ),
        score=0.9,
        success=True,
    )
    learning.observe_session(sid)
    uses = _rows(platform.engine, SkillUseRecord)
    assert [u.skill_name for u in uses] == ["research"]
    # A skill was used, so the (otherwise qualifying) create lane is skipped.
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_stat_math_and_stats_view(platform, learning):
    name = _add_user_skill(platform)
    use = (("skill_load", True, _load_args(name)),)
    learning.observe_session(
        _seed_session(platform, tools=use, score=1.0, success=True)
    )
    learning.observe_session(
        _seed_session(platform, tools=use, score=0.4, success=False)
    )
    with session_scope(platform.engine) as db:
        stat = db.get(SkillStatRecord, name)
    assert stat.use_count == 2
    assert stat.score_sum == pytest.approx(1.4)
    assert stat.success_count == 1
    assert stat.last_used_at is not None

    view = next(s for s in learning.stats()["skills"] if s["skill_name"] == name)
    assert view["use_count"] == 2
    assert view["avg_score"] == pytest.approx(0.7)
    assert view["success_rate"] == pytest.approx(0.5)
    assert view["last_used_at"] is not None


# -- refine-lane gating -------------------------------------------------------


def test_low_score_use_mints_refine_candidate_once(platform, learning):
    name = _add_user_skill(platform)
    use = (("skill_load", True, _load_args(name)),)
    learning.observe_session(
        _seed_session(platform, tools=use, score=0.3, success=False)
    )
    cands = _rows(platform.engine, SkillCandidateRecord)
    assert len(cands) == 1
    assert cands[0].kind == "refine" and cands[0].skill_name == name
    # A second low scorer must not stack a duplicate pending candidate.
    learning.observe_session(
        _seed_session(platform, tools=use, score=0.2, success=False)
    )
    assert _count(platform.engine, SkillCandidateRecord) == 1


def test_high_score_use_mints_no_refine_candidate(platform, learning):
    name = _add_user_skill(platform)
    sid = _seed_session(
        platform,
        tools=(("skill_load", True, _load_args(name)),),
        score=0.95,
        success=True,
    )
    learning.observe_session(sid)
    assert _count(platform.engine, SkillUseRecord) == 1
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_failed_session_without_outcome_row_uses_status_fallback(platform, learning):
    # No OutcomeRecord at all: the engine must NOT re-score — it degrades to
    # the session status, and a FAILED status still feeds the refine lane.
    name = _add_user_skill(platform)
    sid = _seed_session(
        platform,
        status=SessionStatus.FAILED,
        tools=(("skill_load", True, _load_args(name)),),
    )
    learning.observe_session(sid)
    cands = _rows(platform.engine, SkillCandidateRecord)
    assert len(cands) == 1 and cands[0].kind == "refine"


def test_refine_never_targets_external_source_skills(platform, learning):
    # A skill pulled in from the Claude root: used + low-scoring, but its
    # source is not ours to rewrite -> stats yes, refine candidate no.
    from iron_jarvis.skills.loader import Skill

    platform.skills._skills["claude-thing"] = Skill(
        name="claude-thing",
        description="external",
        instructions="body",
        dir=platform.config.home,
        source="claude",
    )
    sid = _seed_session(
        platform,
        tools=(("skill_load", True, _load_args("claude-thing")),),
        score=0.1,
        success=False,
    )
    learning.observe_session(sid)
    assert _count(platform.engine, SkillUseRecord) == 1
    assert _count(platform.engine, SkillCandidateRecord) == 0


def test_user_root_shadows_builtin_on_repopulate(platform):
    # THE eligibility proof for including "builtin" in _REFINE_SOURCES:
    # repopulate discovers builtin first, then the user root, and discover()
    # is last-wins on a name collision — so a user-root copy SHADOWS the
    # shipped builtin (copy-on-write refinement, never an in-place rewrite).
    assert platform.skills.get("research").source == "builtin"
    save_skill(
        platform.config.home / "skills",
        "research",
        "my sharper research method",
        "1. My replacement steps for the research skill body.",
    )
    platform.skills.repopulate(platform.config.home, None)
    shadowed = platform.skills.get("research")
    assert shadowed.source == "user"
    assert "My replacement steps" in shadowed.instructions


def test_low_score_builtin_use_is_refine_eligible(platform, learning):
    # Allowed BECAUSE the shadowing test above proves user-over-builtin wins.
    sid = _seed_session(
        platform,
        tools=(("skill_load", True, _load_args("research")),),
        score=0.2,
        success=False,
    )
    learning.observe_session(sid)
    cands = _rows(platform.engine, SkillCandidateRecord)
    assert len(cands) == 1
    assert cands[0].kind == "refine" and cands[0].skill_name == "research"


# -- never-raise guards -------------------------------------------------------


def test_observe_never_raises_with_poisoned_db(platform, learning):
    sid = _seed_session(platform)
    learning.engine = object()  # any DB touch now explodes internally
    learning.observe_session(sid)  # must swallow + log, never raise


def test_observe_never_raises_with_poisoned_registry(platform, learning):
    class _Boom:
        def get(self, name):
            raise RuntimeError("boom")

        def search(self, query, k=5):
            raise RuntimeError("boom")

        def list(self):
            raise RuntimeError("boom")

    sid = _seed_session(
        platform, tools=(("skill_load", True, _load_args("research")),)
    )
    platform.skills = _Boom()
    learning.observe_session(sid)  # must not raise


def test_stats_never_raises_with_poisoned_db(platform, learning):
    learning.engine = object()
    out = learning.stats()
    assert out == {"skills": [], "pending_proposals": 0, "pending_candidates": 0}


# -- distillation -------------------------------------------------------------


async def test_distill_create_happy_path_mints_proposal(platform, learning):
    seen: list[SkillProposalRecord] = []
    learning.on_proposal = seen.append
    learning.observe_session(_seed_session(platform))

    complete = _completer(_skill_md_reply())
    out = await learning.distill_candidates(complete)

    assert out["reviewed"] == 1 and out["dismissed"] == 0
    props = _rows(platform.engine, SkillProposalRecord)
    assert len(props) == 1
    p = props[0]
    assert out["proposals"] == [p.id]
    assert p.kind == "create" and p.status == "pending"
    assert p.skill_name == "vendor-ledger-reconciliation"
    assert p.body_md.startswith("---\n") and "read_file" in p.body_md
    assert json.loads(p.source_session_ids)
    # Candidate consumed; callback fired with the minted record.
    cand = _rows(platform.engine, SkillCandidateRecord)[0]
    assert cand.status == "distilled"
    assert [x.id for x in seen] == [p.id] and seen[0].status == "pending"
    # The prompt carried real session evidence (task + tool history).
    system, prompt = complete.calls[0]
    assert "SKILL.md" in system and TASK in prompt and "read_file" in prompt


async def test_distill_garbage_reply_retries_then_dismisses(platform, learning):
    learning.observe_session(_seed_session(platform))
    complete = _completer("cannot help with that")

    for expected_attempts in (1, 2):
        out = await learning.distill_candidates(complete)
        assert out["proposals"] == [] and out["dismissed"] == 0
        cand = _rows(platform.engine, SkillCandidateRecord)[0]
        assert cand.status == "pending" and cand.attempts == expected_attempts

    out = await learning.distill_candidates(complete)
    assert out["dismissed"] == 1
    cand = _rows(platform.engine, SkillCandidateRecord)[0]
    assert cand.status == "dismissed" and cand.attempts == 3
    assert _count(platform.engine, SkillProposalRecord) == 0

    # A dismissed candidate never re-enters a sweep.
    out = await learning.distill_candidates(complete)
    assert out["reviewed"] == 0


async def test_distill_name_conflict_with_registry_is_unusable(platform, learning):
    learning.observe_session(_seed_session(platform))
    complete = _completer(_skill_md_reply(name="research"))  # builtin exists
    out = await learning.distill_candidates(complete)
    assert out["proposals"] == []
    cand = _rows(platform.engine, SkillCandidateRecord)[0]
    assert cand.status == "pending" and cand.attempts == 1


async def test_distill_name_conflict_with_pending_proposal(platform, learning):
    learning.observe_session(_seed_session(platform))
    learning.observe_session(
        _seed_session(platform, task="draft the monthly newsletter from blog posts")
    )
    # The model proposes the SAME name for both candidates.
    out = await learning.distill_candidates(_completer(_skill_md_reply()))
    assert out["reviewed"] == 2 and len(out["proposals"]) == 1
    cands = sorted(_rows(platform.engine, SkillCandidateRecord), key=lambda c: c.status)
    assert {c.status for c in cands} == {"distilled", "pending"}
    pending = next(c for c in cands if c.status == "pending")
    assert pending.attempts == 1


async def test_distill_refine_builds_replacement_and_keeps_name(platform, learning):
    name = _add_user_skill(platform)
    sid = _seed_session(
        platform,
        tools=(("skill_load", True, _load_args(name)),),
        score=0.2,
        success=False,
    )
    learning.observe_session(sid)
    reply = _skill_md_reply(
        name="totally-renamed",  # the model drifting the name must not win
        description="Improved chasing method.",
        body="# Improved\n\n1. Better step one with more detail.\n2. Step two.",
    )
    complete = _completer(reply)
    out = await learning.distill_candidates(complete)

    assert len(out["proposals"]) == 1
    p = _rows(platform.engine, SkillProposalRecord)[0]
    assert p.kind == "refine"
    assert p.skill_name == name  # original name preserved
    assert f"name: {name}" in p.body_md and "Better step one" in p.body_md
    assert "Find overdue invoices" in p.prev_body_md  # the diff base
    # The refine prompt carried the current body + failure evidence.
    _system, prompt = complete.calls[0]
    assert "Find overdue invoices" in prompt and "Score: 0.2" in prompt


async def test_distill_refine_vanished_skill_dismisses_without_model_call(
    platform, learning
):
    with session_scope(platform.engine) as db:
        db.add(
            SkillCandidateRecord(
                session_id="session_x",
                task="t",
                kind="refine",
                skill_name="ghost-skill",
                signature="refine::ghost-skill",
            )
        )
        db.commit()
    complete = _completer(_skill_md_reply())
    out = await learning.distill_candidates(complete)
    assert out["dismissed"] == 1 and out["proposals"] == []
    assert complete.calls == []  # no model call for an unfixable candidate
    cand = _rows(platform.engine, SkillCandidateRecord)[0]
    assert cand.status == "dismissed"


async def test_distill_disabled_is_a_noop(platform, learning):
    learning.observe_session(_seed_session(platform))
    platform.config = _CfgOverride(platform.config, skill_learning_enabled=False)
    complete = _completer(_skill_md_reply())
    out = await learning.distill_candidates(complete)
    assert out["reviewed"] == 0 and complete.calls == []


async def test_distill_debounces_overlapping_sweeps(platform, learning):
    learning.observe_session(_seed_session(platform))
    learning._distilling = True  # a sweep is "already running"
    complete = _completer(_skill_md_reply())
    out = await learning.distill_candidates(complete)
    assert out["reviewed"] == 0 and complete.calls == []
    assert "already running" in out["note"]


async def test_distill_auto_approve_writes_skill_immediately(platform, learning):
    seen: list[SkillProposalRecord] = []
    learning.on_proposal = seen.append
    platform.config = _CfgOverride(platform.config, skill_learning_auto_approve=True)
    learning.observe_session(_seed_session(platform))

    out = await learning.distill_candidates(_completer(_skill_md_reply()))

    assert len(out["proposals"]) == 1
    p = _rows(platform.engine, SkillProposalRecord)[0]
    assert p.status == "approved" and p.decided_at is not None
    skill_dir = platform.config.home / "skills" / "vendor-ledger-reconciliation"
    assert (skill_dir / "SKILL.md").is_file()
    assert platform.skills.get("vendor-ledger-reconciliation") is not None
    # The callback sees the APPROVED record (the daemon tags the event auto=true).
    assert seen and seen[0].status == "approved"


async def test_on_proposal_callback_errors_are_swallowed(platform, learning):
    def _boom(_proposal):
        raise RuntimeError("notify failed")

    learning.on_proposal = _boom
    learning.observe_session(_seed_session(platform))
    out = await learning.distill_candidates(_completer(_skill_md_reply()))
    assert len(out["proposals"]) == 1  # the mint survived the bad callback
    assert _rows(platform.engine, SkillProposalRecord)[0].status == "pending"


# -- approve / reject ---------------------------------------------------------


def _seed_proposal(platform, **over) -> str:
    fields = {
        "kind": "create",
        "skill_name": "weekly-report",
        "description": "d",
        "body_md": _skill_md_reply(
            name="weekly-report",
            description="Use to compile the weekly report.",
            body="# Steps\n\n1. Gather the inputs.\n2. Compile and send the report.",
        ),
        "signature": "create::weekly report",
    }
    fields.update(over)
    record = SkillProposalRecord(**fields)
    pid = record.id
    with session_scope(platform.engine) as db:
        db.add(record)
        db.commit()
    return pid


def test_approve_create_suffixes_instead_of_clobbering(platform, learning):
    _add_user_skill(
        platform, name="weekly-report", instructions="ORIGINAL body to protect."
    )
    pid = _seed_proposal(platform)
    out = learning.approve(pid)

    assert out.status == "approved" and out.decided_at is not None
    assert out.skill_name == "weekly-report-2"  # never clobbers the existing slug
    root = platform.config.home / "skills"
    assert "ORIGINAL body" in (root / "weekly-report" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Compile and send" in (root / "weekly-report-2" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    # Repopulated in place: both are live in the registry immediately.
    assert platform.skills.get("weekly-report") is not None
    assert platform.skills.get("weekly-report-2") is not None


def test_approve_refine_overwrites_same_slug(platform, learning):
    name = _add_user_skill(platform)
    pid = _seed_proposal(
        platform,
        kind="refine",
        skill_name=name,
        body_md=_skill_md_reply(
            name=name,
            description="Sharper chasing method.",
            body="# Improved\n\n1. NEW improved chasing steps live here now.",
        ),
        signature=f"refine::{name}",
    )
    out = learning.approve(pid)

    assert out.status == "approved" and out.skill_name == name
    root = platform.config.home / "skills"
    assert "NEW improved" in (root / name / "SKILL.md").read_text(encoding="utf-8")
    assert not (root / f"{name}-2").exists()  # overwrote, did not fork
    assert "NEW improved" in platform.skills.get(name).instructions


def test_approve_refine_vanished_skill_falls_back_to_create(platform, learning):
    pid = _seed_proposal(
        platform,
        kind="refine",
        skill_name="ghost-skill",
        body_md=_skill_md_reply(
            name="ghost-skill",
            description="Was deleted between distill and approve.",
            body="# Steps\n\n1. Recreate the vanished procedure from scratch.",
        ),
        signature="refine::ghost-skill",
    )
    out = learning.approve(pid)
    assert out.status == "approved" and out.skill_name == "ghost-skill"
    root = platform.config.home / "skills"
    assert (root / "ghost-skill" / "SKILL.md").is_file()
    assert platform.skills.get("ghost-skill") is not None


def test_approve_with_edited_body_wins(platform, learning):
    pid = _seed_proposal(platform)
    edited = _skill_md_reply(
        name="weekly-report",
        description="Edited before approval.",
        body="# Steps\n\n1. The USER-EDITED procedure is what must land.",
    )
    out = learning.approve(pid, body_md=edited)
    assert out.status == "approved"
    text = (
        platform.config.home / "skills" / out.skill_name / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "USER-EDITED" in text


def test_approve_unknown_or_decided_raises(platform, learning):
    with pytest.raises(ValueError):
        learning.approve("skp_nope")
    pid = _seed_proposal(platform)
    learning.approve(pid)
    with pytest.raises(ValueError):
        learning.approve(pid)
    with pytest.raises(ValueError):
        learning.reject(pid)


async def test_reject_suppresses_the_same_procedure(platform, learning):
    learning.observe_session(_seed_session(platform))
    out = await learning.distill_candidates(_completer(_skill_md_reply()))
    pid = out["proposals"][0]

    rejected = learning.reject(pid)
    assert rejected.status == "rejected" and rejected.decided_at is not None
    # Nothing landed on disk.
    assert not (
        platform.config.home / "skills" / "vendor-ledger-reconciliation"
    ).exists()

    # The same procedure finishing again must NOT be re-proposed.
    learning.observe_session(_seed_session(platform))
    pending = [
        c
        for c in _rows(platform.engine, SkillCandidateRecord)
        if c.status == "pending"
    ]
    assert pending == []


def test_proposals_listing_orders_pending_first(platform, learning):
    a = _seed_proposal(platform, skill_name="a-skill", signature="create::a")
    learning.reject(a)
    b = _seed_proposal(platform, skill_name="b-skill", signature="create::b")
    rows = learning.proposals()
    assert [r.id for r in rows] == [b, a]
    assert [r.id for r in learning.proposals(status="rejected")] == [a]


# -- reply-parsing hardening (reviewer regressions) ---------------------------


async def test_distill_body_with_inner_code_fence_still_parses(platform, learning):
    # REGRESSION: the old fence-stripper grabbed the FIRST ``` block anywhere,
    # so a valid unfenced reply whose BODY contained a fenced template was
    # gutted (frontmatter lost -> "unusable" -> eventually dismissed).
    learning.observe_session(_seed_session(platform))
    reply = (
        "---\nname: chase-invoices\n"
        "description: Use when chasing overdue invoices.\n---\n\n"
        "# Steps\n\n1. Use this email template:\n\n"
        "```\nDear {client},\nPlease pay invoice {number}.\n```\n\n2. Send it.\n"
    )
    out = await learning.distill_candidates(_completer(reply))
    assert len(out["proposals"]) == 1
    p = _rows(platform.engine, SkillProposalRecord)[0]
    assert p.skill_name == "chase-invoices"
    assert "Dear {client}," in p.body_md  # the inner fence's content survived


def test_parse_skill_md_hardening(learning):
    # Prose with an inline dash-run before the frontmatter (the delimiter is a
    # `---` LINE, not any substring).
    body = "# Steps\n\n1. Do the thing carefully and completely now."
    name, _d, parsed = learning._parse_skill_md(
        f"Sure --- here is the file:\n\n---\nname: y-skill\ndescription: d\n---\n\n{body}\n"
    )
    assert name == "y-skill" and parsed == body

    # A fence-wrapped reply whose body ALSO contains an inner fence: the
    # wrapper is stripped, the inner block survives intact.
    inner = f"{body}\n\n```yaml\nkey: value\n```"
    name, _d, parsed = learning._parse_skill_md(
        f"```markdown\n---\nname: z-skill\ndescription: d\n---\n\n{inner}\n```"
    )
    assert name == "z-skill" and "key: value" in parsed and parsed.count("```") == 2

    # An UNfenced reply whose body ENDS with a code block must not be mistaken
    # for a wrapper (the old regex would have swallowed everything before it).
    ends_fenced = f"---\nname: w-skill\ndescription: d\n---\n\n{body}\n\n```\npy run.py\n```"
    name, _d, parsed = learning._parse_skill_md(ends_fenced)
    assert name == "w-skill" and "py run.py" in parsed

    # `---` lines INSIDE the body stay in the body.
    name, _d, parsed = learning._parse_skill_md(
        f"---\nname: v-skill\ndescription: d\n---\n\n{body}\n\n---\n\nMore steps here."
    )
    assert name == "v-skill" and "---" in parsed and "More steps" in parsed

    # Degenerate model output is rejected as unusable, never raises.
    assert learning._parse_skill_md("no frontmatter at all") == ("", "", "")
    assert learning._parse_skill_md("---\n- a\n- b\n---\n\n" + body) == ("", "", "")  # non-dict
    assert learning._parse_skill_md("---\nname: n\ndescription: d\n---\n\nx") == ("", "", "")  # 1-char body
    bomb = "---\na: " + "x" * 250_000 + "\n---\n\n" + body  # frontmatter bomb
    assert learning._parse_skill_md(bomb) == ("", "", "")


async def test_distill_complete_exception_releases_debounce(platform, learning):
    # A provider blow-up mid-sweep must release the debounce flag (the finally)
    # and leave the candidate pending WITHOUT burning a retry attempt (a
    # transport failure is not an unusable reply).
    learning.observe_session(_seed_session(platform))

    async def boom(system: str, prompt: str) -> str:
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        await learning.distill_candidates(boom)
    assert learning._distilling is False
    cand = _rows(platform.engine, SkillCandidateRecord)[0]
    assert cand.status == "pending" and cand.attempts == 0

    # The next sweep is not debounced and completes normally.
    out = await learning.distill_candidates(_completer(_skill_md_reply()))
    assert len(out["proposals"]) == 1


def test_approve_traversal_name_stays_inside_skills_root(platform, learning):
    # A hostile/degenerate skill name must never write outside <home>/skills.
    pid = _seed_proposal(
        platform,
        skill_name="../../evil",
        body_md=_skill_md_reply(
            name="../../evil",
            description="d",
            body="# Steps\n\n1. This must land inside the skills root only.",
        ),
        signature="create::evil",
    )
    learning.approve(pid)
    home = platform.config.home
    assert (home / "skills" / "evil" / "SKILL.md").is_file()
    assert not (home / "evil").exists()
    assert not (home.parent / "evil").exists()
