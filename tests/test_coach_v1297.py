"""v1.297.0 — the REFLECTION COACH. Offline, deterministic.

What is guarded, each with its silent failure mode:
  - ``run_signals`` reads every fact off the LEDGER (outcome, score, failed
    tools with the first error line, denials counted once, asks the clock
    answered, steps vs the cap, thumbs-down, repeated reads, a later
    /continue) — never from prose;
  - ``recent_runs`` takes the LARGER of "last N" and "last H hours", capped
    at 25, newest first, this agent only;
  - every taxonomy category is reachable from a hand-built signal list, and
    the ≥ 2-evidence floor holds (tool-misuse / human-correction need one);
  - ``propose`` is honest about "not now": a builtin, < 3 runs, no clusters,
    and — the honest-mock rule — NO record is minted with the offline mock;
  - with a fake router: a pending record whose rationale cites a category
    and a session id; the same evidence is not minted twice; a declined
    signature is suppressed; a draft that grows past +20% or drops a
    never/always line is refused;
  - ``accept`` writes the prompt through the registry (GET /agents shows it)
    with ``prompt_reason="coach"`` when the registry takes it; instructions
    that moved since mint → stale + 409;
  - the routes over the REAL app factory, in plain sentences.
"""

from __future__ import annotations

import functools
import inspect
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.coach import CoachEngine, CoachProposalRecord, StaleProposal
from iron_jarvis.coach import engine as coach_engine
from iron_jarvis.coach.signals import CONTINUATION_MARK, recent_runs, run_signals
from iron_jarvis.coach.taxonomy import CATEGORIES, clusters
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import (
    AgentRun,
    AgentType,
    EventRecord,
    PermissionMode,
    Session,
    SessionStatus,
    ToolInvocation,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import coach as coach_routes
from iron_jarvis.improvement.models import OutcomeRecord
from iron_jarvis.learning.models import FeedbackRecord
from iron_jarvis.platform import build_platform

NAME = "scout"
ROSTER = "custom:scout"
TOOLS = ["read_file", "write_file", "list_files"]
#: ~400 chars: the +20% growth cap leaves room for ONE added sentence (a
#: 200-char prompt would not — see the engine's MAX_GROWTH note).
PROMPT = (
    "You are Scout, a focused research helper for the firm.\n"
    "Never send client data to anyone outside the firm.\n"
    "Always cite the file you read a number from.\n"
    "Keep answers short and name the file you changed.\n"
    "When a document is a partnership return, list every partner you find "
    "with the share shown on the schedule, in the order the schedule lists "
    "them, and say plainly when a page was unreadable instead of guessing."
)
GOOD_AFTER = PROMPT + "\nCheck the file exists before you write to it."


# --------------------------------------------------------------------------- #
# fixtures + seeding
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.agents_registry.register(NAME, PROMPT, TOOLS, description="a scout")
    return p


class FakeRouter:
    """The router's ``complete`` shape (``route.response.text``), scripted."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools, **kw):
        self.calls.append({"system": system, "prompt": messages[-1].content})
        return SimpleNamespace(
            response=SimpleNamespace(text=self.text), provider="fake", model="fake"
        )


def _arm(platform, monkeypatch, text: str) -> FakeRouter:
    """A REAL-looking provider (the guard says yes) + a scripted router."""
    monkeypatch.setattr(CoachEngine, "_real_provider_available", lambda self: True)
    router = FakeRouter(text)
    platform.router = router
    return router


def _seed_run(
    platform,
    *,
    agent: str = ROSTER,
    status: SessionStatus = SessionStatus.COMPLETED,
    outcome: str | None = "completed",
    steps: int = 3,
    max_steps: int | None = None,
    tools: list[tuple] = (),
    score: float | None = None,
    success: bool = True,
    down: int = 0,
    unanswered: int = 0,
    denied_events: int = 0,
    task: str = "read the K-1 and list the partners",
    summary: str = "done",
    created_at=None,
    continued: bool = False,
) -> str:
    """One run in the ledger. ``tools`` = (name, ok, output, args, verdict)."""
    sess = Session(
        task=task,
        agent_type=AgentType.BUILDER,
        agent_name=agent,
        status=status,
        max_steps=max_steps,
        outcome=outcome,
        summary=summary,
    )
    if created_at is not None:
        sess.created_at = created_at
        sess.finished_at = created_at + timedelta(seconds=30)
    else:
        sess.finished_at = sess.created_at + timedelta(seconds=30)
    sid = sess.id
    run = AgentRun(session_id=sid, steps=steps, agent_type=AgentType.BUILDER)
    with session_scope(platform.engine) as db:
        db.add(sess)
        db.add(run)
        for spec in tools:
            name, ok, output, args, verdict = (tuple(spec) + (None,) * 5)[:5]
            db.add(
                ToolInvocation(
                    session_id=sid,
                    agent_run_id=run.id,
                    tool=name,
                    ok=bool(ok),
                    output=output or "",
                    args_json=json.dumps(args or {}),
                    verdict=verdict or PermissionMode.ALLOW,
                    reversibility="reversible",
                )
            )
        if score is not None:
            db.add(
                OutcomeRecord(
                    session_id=sid, agent_type="builder", agent_name=agent,
                    score=score, success=success,
                )
            )
        for _ in range(down):
            db.add(FeedbackRecord(session_id=sid, rating="down"))
        for i in range(unanswered):
            db.add(
                EventRecord(
                    id=f"evt_{sid}_{i}", type=EventType.APPROVAL_RESOLVED,
                    session_id=sid, payload_json=json.dumps({"decision": "timeout"}),
                )
            )
        for i in range(denied_events):
            db.add(
                EventRecord(
                    id=f"evt_den_{sid}_{i}", type=EventType.TOOL_DENIED,
                    session_id=sid, payload_json=json.dumps({"tool": "shell"}),
                )
            )
        if continued:
            follow = Session(
                task=f"go on\n\n{CONTINUATION_MARK}{task!r}. Prior result: done]",
                agent_type=AgentType.BUILDER,
            )
            follow.created_at = sess.created_at + timedelta(minutes=5)
            db.add(follow)
        db.commit()
    return sid


def _bad_runs(platform, n: int = 3) -> list[str]:
    """``n`` runs that each fail write_file twice (avoidable-rework)."""
    return [
        _seed_run(
            platform,
            tools=[
                ("write_file", False, "permission denied: read-only\nmore", {"path": "a"}),
                ("write_file", False, "permission denied: read-only", {"path": "a"}),
            ],
        )
        for _ in range(n)
    ]


def _count(engine) -> int:
    with session_scope(engine) as db:
        return len(list(db.exec(select(CoachProposalRecord))))


# --------------------------------------------------------------------------- #
# 1. signals off the ledger
# --------------------------------------------------------------------------- #


def test_run_signals_reads_every_fact_from_the_ledger(platform):
    sid = _seed_run(
        platform,
        status=SessionStatus.COMPLETED,
        outcome=None,  # not stored -> derived by session_result
        steps=9,
        max_steps=10,
        tools=[
            ("read_file", True, "ok", {"path": "k1.pdf"}),
            ("read_file", True, "ok", {"path": "k1.pdf"}),
            ("read_file", True, "ok", {"path": "k1.pdf"}),
            ("write_file", False, "missing required argument: content\n  at line 2", {"path": "out"}),
            ("write_file", False, "disk full", {"path": "out"}),
            ("shell", False, "permission denied", {"cmd": "rm"}, PermissionMode.DENY),
        ],
        score=0.4,
        success=False,
        down=1,
        unanswered=2,
        denied_events=1,
        summary="s" * 500,
        continued=True,
    )
    with session_scope(platform.engine) as db:
        sess = db.get(Session, sid)
        db.expunge(sess)
    sig = run_signals(platform, sess)
    assert sig["session_id"] == sid and sig["agent"] == ROSTER
    assert sig["status"] == "completed"
    assert sig["outcome"] == "needs_you"  # derived: asks timed out
    assert sig["score"] == 0.4 and sig["success"] is False
    assert sig["tools_used"] == ["read_file", "write_file", "shell"]
    failed = {f["tool"]: f for f in sig["tools_failed"]}
    assert failed["write_file"]["count"] == 2
    assert failed["write_file"]["first_error"] == "missing required argument: content"
    assert len(failed["write_file"]["first_error"]) <= 120
    assert failed["shell"]["count"] == 1
    assert sig["denials"] == 2  # one DENY row + one event without an invocation id
    assert sig["unanswered_asks"] == 2
    assert sig["steps"] == 9 and sig["max_steps"] == 10
    assert sig["down_feedback"] == 1
    assert sig["duration_s"] is not None
    assert len(sig["summary"]) == 300
    assert sig["read_repeats"] == 3
    assert sig["continued"] is True


def test_evidence_quotes_never_carry_a_secret(platform):
    """REVIEW v1.297.0: a failed tool's first output line becomes an evidence
    QUOTE that rides the proposal row, the coach.proposal event and the model
    prompt. A failed shell/web call echoes its own arguments into its error,
    so the line is masked (``detections.redact.mask``) before it is quoted;
    the session summary on the signal is masked the same way."""
    token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    sid = _seed_run(
        platform,
        status=SessionStatus.FAILED,
        outcome="failed",
        summary=f"curl failed with Authorization: Bearer {token}",
        tools=[
            ("web_fetch", False, f"401 for https://user:hunter2@x.test/?k=1 Authorization: Bearer {token}", {}),
            ("web_fetch", False, f"401 again Bearer {token}", {}),
        ],
    )
    with session_scope(platform.engine) as db:
        sess = db.get(Session, sid)
        db.expunge(sess)
    sig = run_signals(platform, sess)
    first = sig["tools_failed"][0]["first_error"]
    assert token not in first and "hunter2" not in first and "***" in first
    assert token not in sig["summary"] and "***" in sig["summary"]
    # The cluster's quote is built from that line and must be clean too
    # (avoidable-rework needs two runs of evidence: the same signal twice).
    quotes = [e["quote"] for c in clusters([sig, sig]) for e in c.evidence]
    assert quotes and all(token not in q and "hunter2" not in q for q in quotes)


def test_run_signals_default_cap_and_no_rows(platform):
    sid = _seed_run(platform, tools=[], max_steps=None)
    with session_scope(platform.engine) as db:
        sess = db.get(Session, sid)
        db.expunge(sess)
    sig = run_signals(platform, sess)
    assert sig["max_steps"] == platform.config.max_agent_steps
    assert sig["score"] is None and sig["denials"] == 0 and sig["continued"] is False
    assert sig["tools_failed"] == [] and sig["read_repeats"] == 0


def test_recent_runs_takes_the_larger_window_capped_newest_first(platform):
    now = utcnow()
    for i in range(12):  # old: outside 72h
        _seed_run(platform, created_at=now - timedelta(hours=100 + i))
    _seed_run(platform, agent="custom:other", created_at=now)
    runs = recent_runs(platform, ROSTER, limit=10, since_hours=72)
    assert len(runs) == 10  # max(10 by count, 0 in the window)
    assert all(r.agent_name == ROSTER for r in runs)
    assert [r.created_at for r in runs] == sorted((r.created_at for r in runs), reverse=True)
    for i in range(30):  # 30 inside the window -> the cap
        _seed_run(platform, created_at=now - timedelta(minutes=i))
    runs = recent_runs(platform, ROSTER, limit=10, since_hours=72)
    assert len(runs) == 25
    assert recent_runs(platform, "custom:nobody") == []


# --------------------------------------------------------------------------- #
# 2. taxonomy: every category reachable, the evidence floor
# --------------------------------------------------------------------------- #


def _sig(sid: str, **over) -> dict:
    base = {
        "session_id": sid, "status": "completed", "outcome": "completed", "score": 0.9,
        "tools_used": ["read_file"], "tools_failed": [], "denials": 0,
        "unanswered_asks": 0, "steps": 3, "max_steps": 12, "down_feedback": 0,
        "read_repeats": 1, "continued": False, "summary": "",
    }
    base.update(over)
    return base


def _only(found, category):
    return [c for c in found if c.category == category]


def test_taxonomy_verifier_miss():
    found = clusters([
        _sig("a", outcome="completed", down_feedback=1),
        _sig("b", outcome="completed_with_failures", tools_failed=[{"tool": "write_file", "count": 1, "first_error": "x"}]),
        _sig("c", outcome=None, score=0.95, down_feedback=1),
    ])
    (c,) = _only(found, "verifier-miss")
    assert c.count == 3 and [e["session_id"] for e in c.evidence] == ["a", "b", "c"]
    assert "rated it down" in c.evidence[0]["quote"]
    assert "write_file" in c.evidence[1]["quote"]


def test_taxonomy_avoidable_rework():
    f = [{"tool": "write_file", "count": 2, "first_error": "disk full"}]
    found = clusters([_sig("a", tools_failed=f), _sig("b", tools_failed=f)])
    (c,) = _only(found, "avoidable-rework")
    assert c.count == 2 and "write_file failed 2 times: disk full" == c.evidence[0]["quote"]
    assert not _only(clusters([_sig("a", tools_failed=[{"tool": "x", "count": 1, "first_error": ""}])]), "avoidable-rework")


def test_taxonomy_tool_misuse_needs_one_run():
    (c,) = _only(clusters([_sig("a", denials=2)]), "tool-misuse")
    assert c.count == 1 and "denied" in c.evidence[0]["quote"]
    for marker in ("missing required arg", "Unknown tool 'x'", "tool is not armed"):
        f = [{"tool": "t", "count": 1, "first_error": marker}]
        assert _only(clusters([_sig("a", tools_failed=f)]), "tool-misuse")
    assert not _only(clusters([_sig("a", tools_failed=[{"tool": "t", "count": 1, "first_error": "disk full"}])]), "tool-misuse")


def test_taxonomy_late_escalation():
    found = clusters([
        _sig("a", outcome="needs_you", steps=8, max_steps=12),
        _sig("b", outcome="needs_you", steps=12, max_steps=12),
        _sig("c", outcome="needs_you", steps=2, max_steps=12),  # early: fine
    ])
    (c,) = _only(found, "late-escalation")
    assert c.count == 2 and "step 8 of 12" in c.evidence[0]["quote"]


def test_taxonomy_scope_creep():
    found = clusters([
        _sig("a", steps=12, max_steps=12, status="completed", outcome="completed_with_failures"),
        _sig("b", steps=12, max_steps=12, status="failed", outcome=None),
        _sig("c", steps=12, max_steps=12, status="completed", outcome="completed"),  # finished
    ])
    (c,) = _only(found, "scope-creep")
    assert c.count == 2 and {e["session_id"] for e in c.evidence} == {"a", "b"}


def test_taxonomy_instruction_miss():
    found = clusters([
        _sig("a", unanswered_asks=1),
        _sig("b", status="failed", outcome=None, tools_used=[], summary="could not start"),
        _sig("c", status="failed", outcome=None, tools_used=["shell"]),  # it tried
    ])
    (c,) = _only(found, "instruction-miss")
    assert c.count == 2
    assert "unanswered" in c.evidence[0]["quote"] and "could not start" in c.evidence[1]["quote"]


def test_taxonomy_stale_context():
    found = clusters([_sig("a", read_repeats=3), _sig("b", read_repeats=5), _sig("c", read_repeats=2)])
    (c,) = _only(found, "stale-context")
    assert c.count == 2 and "3 times" in c.evidence[0]["quote"]


def test_taxonomy_human_correction_needs_one_run():
    (c,) = _only(clusters([_sig("a", continued=True)]), "human-correction")
    assert c.count == 1 and "continued" in c.evidence[0]["quote"]


def test_taxonomy_evidence_floor_order_and_determinism():
    sigs = [_sig("a", down_feedback=1)]  # one verifier-miss: below the floor
    assert clusters(sigs) == []
    sigs = [_sig("a", down_feedback=1, denials=1), _sig("b", down_feedback=1, read_repeats=4), _sig("c", read_repeats=3)]
    found = clusters(sigs)
    assert [c.category for c in found] == ["verifier-miss", "tool-misuse", "stale-context"]
    assert [c.weight for c in found] == [2.6, 1.5, 1.2]
    assert [c.to_dict() for c in clusters(sigs)] == [c.to_dict() for c in found]
    assert set(CATEGORIES) == {
        "verifier-miss", "avoidable-rework", "tool-misuse", "late-escalation",
        "scope-creep", "instruction-miss", "stale-context", "human-correction",
    }
    assert clusters([]) == [] and clusters([None, "junk"]) == []


# --------------------------------------------------------------------------- #
# 3. the engine: report, the honest refusals, the real path
# --------------------------------------------------------------------------- #


def test_report_shape(platform):
    ids = _bad_runs(platform, 2)
    rep = platform.coach.report(NAME)
    assert rep["agent"] == ROSTER
    assert [r["session_id"] for r in rep["runs"]] == list(reversed(ids))
    assert rep["clusters"][0]["category"] == "avoidable-rework"
    assert rep["clusters"][0]["count"] == 2
    assert set(rep["clusters"][0]) == {"category", "count", "evidence", "weight"}
    assert rep["categories"] == CATEGORIES
    assert platform.coach.report("custom:nobody")["runs"] == []


async def test_propose_refuses_fewer_than_three_runs(platform, monkeypatch):
    _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(platform, 2)
    assert await platform.coach.propose(NAME) is None
    assert "fewer than 3 runs" in platform.coach.last_reason
    assert _count(platform.engine) == 0


async def test_propose_refuses_when_nothing_clusters(platform, monkeypatch):
    _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    for _ in range(3):
        _seed_run(platform, tools=[("read_file", True, "ok", {"path": "a"})])
    assert await platform.coach.propose(NAME) is None
    assert platform.coach.last_reason == coach_engine.REASON_NO_CLUSTERS
    assert _count(platform.engine) == 0


async def test_propose_refuses_a_builtin_even_with_runs(platform, monkeypatch):
    router = _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    for _ in range(3):
        _seed_run(platform, agent="builder", tools=[("write_file", False, "x", {}), ("write_file", False, "x", {})])
    assert await platform.coach.propose("builder") is None
    assert platform.coach.last_reason == coach_engine.REASON_BUILTIN
    assert router.calls == [] and _count(platform.engine) == 0
    assert await platform.coach.propose("custom:ghost") is None
    assert platform.coach.last_reason == coach_engine.REASON_UNKNOWN


async def test_honest_mock_mints_nothing(platform):
    """The honest-mock pin: the offline mock is the default provider here and
    no CLI is detected (conftest isolates detection) — NO record, no call."""
    from iron_jarvis.providers.adapters.mock import MockLLMAdapter

    assert isinstance(platform.providers.get(platform.config.default_provider), MockLLMAdapter)
    assert platform.coach._real_provider_available() is False
    _bad_runs(platform, 3)
    calls = []
    real_complete = platform.router.complete

    async def spy(**kw):
        calls.append(kw)
        return await real_complete(**kw)

    platform.router.complete = spy
    assert await platform.coach.propose(NAME) is None
    assert platform.coach.last_reason == coach_engine.REASON_NO_MODEL
    assert calls == [] and _count(platform.engine) == 0


async def test_propose_mints_a_pending_record_and_publishes(platform, monkeypatch):
    router = _arm(platform, monkeypatch, f"Here you go:\n```\n{GOOD_AFTER}\n```\nthanks")
    ids = _bad_runs(platform, 3)
    rec = await platform.coach.propose(NAME)
    assert rec is not None and rec.id.startswith("coach_")
    assert rec.status == "pending" and rec.kind == "instructions"
    assert rec.agent == ROSTER and rec.target == NAME
    assert rec.before == PROMPT and rec.after == GOOD_AFTER
    assert "avoidable-rework" in rec.rationale and ids[0] in rec.rationale
    assert len(rec.rationale) <= 600
    evidence = json.loads(rec.evidence_json)
    assert evidence[0]["category"] == "avoidable-rework" and evidence[0]["count"] == 3
    assert len(rec.signature) == 64
    assert platform.coach.last_reason == ""
    # the one model call carried the instructions and the quotes
    assert len(router.calls) == 1
    assert PROMPT in router.calls[0]["prompt"] and "avoidable-rework" in router.calls[0]["prompt"]
    assert "write_file failed 2 times" in router.calls[0]["prompt"]
    assert "SMALLEST edit" in router.calls[0]["system"]
    ev = [e for e in platform.event_bus.history if e.type == EventType.COACH_PROPOSAL]
    assert len(ev) == 1 and ev[0].payload["id"] == rec.id
    assert ev[0].payload["categories"] == ["avoidable-rework"]
    assert ev[0].payload["agent"] == ROSTER and ev[0].payload["rationale"] == rec.rationale
    assert platform.coach.get(rec.id).id == rec.id
    assert [p.id for p in platform.coach.list(agent=NAME, status="pending")] == [rec.id]


async def test_propose_dedupes_a_pending_signature(platform, monkeypatch):
    router = _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(platform, 3)
    first = await platform.coach.propose(NAME)
    assert first is not None
    assert await platform.coach.propose(NAME) is None
    assert platform.coach.last_reason == coach_engine.REASON_PENDING
    assert len(router.calls) == 1 and _count(platform.engine) == 1


async def test_declined_signature_is_suppressed_for_14_days(platform, monkeypatch):
    _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(platform, 3)
    first = await platform.coach.propose(NAME)
    declined = platform.coach.decline(first.id)
    assert declined.status == "declined" and declined.decided_at is not None
    assert await platform.coach.propose(NAME) is None
    assert "declined" in platform.coach.last_reason and "14 days" in platform.coach.last_reason
    # …and an OLD decline no longer suppresses.
    with session_scope(platform.engine) as db:
        row = db.get(CoachProposalRecord, first.id)
        row.decided_at = utcnow() - timedelta(days=15)
        db.add(row)
        db.commit()
    assert (await platform.coach.propose(NAME)) is not None


@pytest.mark.parametrize(
    "after, word",
    [
        (PROMPT + "\n" + "Check the file exists first. " * 12, "+20%"),
        (PROMPT.replace("Never send client data to anyone outside the firm.\n", "") + "\nBe careful.", "safety line"),
        (PROMPT, "identical"),
        ("", "empty"),
    ],
)
async def test_propose_refuses_a_draft_that_is_not_small(platform, monkeypatch, after, word):
    _arm(platform, monkeypatch, f"```\n{after}\n```" if after else "```\n```")
    _bad_runs(platform, 3)
    assert await platform.coach.propose(NAME) is None
    assert platform.coach.last_reason.startswith(coach_engine.REASON_NOTHING_SMALL)
    assert word in platform.coach.last_reason
    assert _count(platform.engine) == 0


def test_validate_revision_and_fence_parsing():
    assert coach_engine.validate_revision(PROMPT, GOOD_AFTER) is None
    assert coach_engine.validate_revision(PROMPT, PROMPT + "\n" + "x" * 200)
    # The bound is max(+20%, +160 chars): a short prompt can take one sentence.
    short = "You are Scout. Never share client data. " + "Be brief. " * 16
    assert len(short) == 200
    # (appended on a NEW line — the never/always line itself must stay intact)
    assert coach_engine.validate_revision(short, short + "\n" + "y" * 149) is None  # +150
    assert "grows past" in coach_engine.validate_revision(short, short + "\n" + "y" * 169)  # +170
    long = "Always cite the file. " + "Keep going. " * 164
    assert len(long) == 1990
    assert coach_engine.validate_revision(long, long + "\n" + "y" * 389) is None  # under +20%
    assert "grows past" in coach_engine.validate_revision(long, long + "\n" + "y" * 499)  # +500
    assert coach_engine.validate_revision(PROMPT, PROMPT.replace("Always", "Usually"))
    assert coach_engine.extract_fenced("```text\nA\nB\n```") == "A\nB"
    assert coach_engine.extract_fenced("no fence here") == "no fence here"
    assert coach_engine.proposal_signature("a", ["y", "x"], ["s2", "s1"]) == coach_engine.proposal_signature("a", ["x", "y", "x"], ["s1", "s2"])
    assert coach_engine.proposal_signature("a", ["x"], ["s1"]) != coach_engine.proposal_signature("b", ["x"], ["s1"])


async def test_accept_writes_the_prompt_through_the_registry(platform, monkeypatch):
    _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(platform, 3)
    rec = await platform.coach.propose(NAME)
    registry = platform.agents_registry
    seen: list[dict] = []
    real_register = registry.register

    @functools.wraps(real_register)  # keep the signature the engine inspects
    def spy(name, prompt, tools, *a, **kw):
        seen.append({"name": name, "prompt": prompt, "tools": tools, **kw})
        return real_register(name, prompt, tools, *a, **kw)

    monkeypatch.setattr(registry, "register", spy)
    done = platform.coach.accept(rec.id)
    assert done.status == "accepted" and done.decided_at is not None
    assert registry.get(NAME).system_prompt == GOOD_AFTER
    assert seen[0]["name"] == NAME and seen[0]["prompt"] == GOOD_AFTER
    assert seen[0]["tools"] == TOOLS and seen[0]["description"] == "a scout"
    takes_reason = "prompt_reason" in inspect.signature(real_register).parameters
    assert seen[0].get("prompt_reason") == ("coach" if takes_reason else None)
    with pytest.raises(ValueError, match="already accepted"):
        platform.coach.accept(rec.id)
    with pytest.raises(KeyError):
        platform.coach.decline("coach_nope")


async def test_accept_on_changed_instructions_goes_stale(platform, monkeypatch):
    _arm(platform, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(platform, 3)
    rec = await platform.coach.propose(NAME)
    platform.agents_registry.register(NAME, PROMPT + "\nEdited by hand.", TOOLS)
    with pytest.raises(StaleProposal, match="ask the coach again"):
        platform.coach.accept(rec.id)
    assert platform.coach.get(rec.id).status == "stale"
    assert platform.agents_registry.get(NAME).system_prompt.endswith("Edited by hand.")


# --------------------------------------------------------------------------- #
# 4. the routes over the REAL app factory
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path))) as c:
        app = c.app
        # app.py's registration line is applied by the wave lead; until it is
        # there, register the module the way it would be (same register(app, d)).
        if not any(getattr(r, "path", "") == "/coach/proposals" for r in app.routes):
            coach_routes.register(app, SimpleNamespace(platform=app.state.platform))
        r = c.post("/agents", json={"name": NAME, "system_prompt": PROMPT, "tools": TOOLS})
        assert r.status_code == 200, r.text
        yield c


def test_routes_report_and_refusals(client):
    p = client.app.state.platform
    assert p.coach is not None
    _bad_runs(p, 2)
    r = client.get(f"/agents/{NAME}/coach")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"report", "proposals", "last_reason"}
    assert body["report"]["agent"] == ROSTER and len(body["report"]["runs"]) == 2
    assert body["report"]["clusters"][0]["category"] == "avoidable-rework"
    assert body["proposals"] == []
    assert client.get(f"/agents/custom:{NAME}/coach").status_code == 200
    r = client.get("/agents/ghost/coach")
    assert r.status_code == 404 and "no agent named 'ghost'" in r.json()["detail"]
    # propose under the mock: an honest null, never a 500
    _bad_runs(p, 1)
    r = client.post(f"/agents/{NAME}/coach")
    assert r.status_code == 200, r.text
    assert r.json() == {"proposal": None, "reason": coach_engine.REASON_NO_MODEL}
    assert client.get("/coach/proposals").json() == {"proposals": []}
    r = client.get("/coach/proposals", params={"status": "bogus"})
    assert r.status_code == 422 and "status must be one of" in r.json()["detail"]
    r = client.post("/coach/proposals/coach_nope/accept")
    assert r.status_code == 404 and "no coach proposal" in r.json()["detail"]


def test_routes_propose_accept_and_get_agents_shows_it(client, monkeypatch):
    p = client.app.state.platform
    _arm(p, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(p, 3)
    r = client.post(f"/agents/{NAME}/coach")
    assert r.status_code == 200, r.text
    prop = r.json()["proposal"]
    assert prop and prop["status"] == "pending" and prop["categories"] == ["avoidable-rework"]
    assert prop["before"] == PROMPT and prop["after"] == GOOD_AFTER
    assert r.json()["reason"] == ""
    assert client.get(f"/agents/{NAME}/coach").json()["proposals"][0]["id"] == prop["id"]
    assert client.get("/coach/proposals", params={"agent": NAME, "status": "pending"}).json()["proposals"][0]["id"] == prop["id"]
    r = client.post(f"/coach/proposals/{prop['id']}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["proposal"]["status"] == "accepted"
    rows = client.get("/agents").json()["dynamic"]
    assert next(a for a in rows if a["name"] == NAME)["system_prompt"] == GOOD_AFTER
    r = client.post(f"/coach/proposals/{prop['id']}/decline")
    assert r.status_code == 409 and "already accepted" in r.json()["detail"]


def test_routes_accept_stale_is_a_409_sentence(client, monkeypatch):
    p = client.app.state.platform
    _arm(p, monkeypatch, f"```\n{GOOD_AFTER}\n```")
    _bad_runs(p, 3)
    prop = client.post(f"/agents/{NAME}/coach").json()["proposal"]
    r = client.patch(f"/agents/{NAME}", json={"system_prompt": PROMPT + "\nHand edit."})
    assert r.status_code == 200, r.text
    r = client.post(f"/coach/proposals/{prop['id']}/accept")
    assert r.status_code == 409
    assert r.json()["detail"] == "the instructions changed since this was written — ask the coach again"
    assert client.get("/coach/proposals", params={"status": "stale"}).json()["proposals"][0]["id"] == prop["id"]
    r = client.post(f"/coach/proposals/{prop['id']}/decline")
    assert r.status_code == 409 and "already stale" in r.json()["detail"]


async def test_accepted_signature_is_suppressed_like_a_declined_one(platform, monkeypatch):
    """Review item 10: acting on a proposal must not make the same evidence
    pop straight back up — the coach waits for new runs."""
    _arm(platform, monkeypatch, "```" + chr(10) + GOOD_AFTER + chr(10) + "```")
    _bad_runs(platform, 3)
    first = await platform.coach.propose(NAME)
    assert first is not None
    platform.coach.accept(first.id)
    assert await platform.coach.propose(NAME) is None
    assert "already accepted" in platform.coach.last_reason


async def test_last_reason_is_per_agent(platform, monkeypatch):
    """Review item 9: agent B's reason must not read as agent A's."""
    _arm(platform, monkeypatch, "```" + chr(10) + GOOD_AFTER + chr(10) + "```")
    _bad_runs(platform, 3)
    assert (await platform.coach.propose(NAME)) is not None
    assert platform.coach.last_reason_for(NAME) == ""
    assert await platform.coach.propose("builder") is None
    assert platform.coach.last_reason_for("builder")
    # A's own answer is untouched by B's refusal.
    assert platform.coach.last_reason_for(NAME) == ""

