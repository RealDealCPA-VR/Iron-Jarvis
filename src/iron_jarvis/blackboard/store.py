"""Blackboard store (Departments substrate).

Pure-DB, offline-safe shared space scoped to a *department*. A department is a
root session plus all of its sibling sub-agents; they share ONE board so they
can post findings and address each other instead of only summarizing upward.

The board id is the ROOT session id: :func:`resolve_board_id` walks the
``AgentRun`` ``parent_id`` chain up from the calling agent to the root run and
returns that root run's session id. A supervisor (a root run) and every
descendant sub-agent therefore resolve to the same board, while an unrelated
task — with its own root — resolves to a different board (scoping/isolation).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, and_, or_
from sqlmodel import Session, select

from ..core.db import session_scope
from ..core.models import AgentRun
from .models import BlackboardKind, BlackboardRecord

#: Bounds for the department walk that builds the roster. A board is one team,
#: not a drive scan — but a runaway delegation tree must never turn "who can I
#: talk to?" into an unbounded query on the event loop's thread pool.
_MAX_ROSTER_RUNS = 200
_MAX_ROSTER_DEPTH = 6

#: Board ids that never have a REAL department behind them. ``daemon/chat_turn``
#: persists one ``AgentRun(session_id="chat", parent_id=None)`` per chat turn as
#: a USAGE-LEDGER row — accounting, not a teammate. Seeding the roster from them
#: would fill the permanent global "chat" board with hundreds of identically
#: named phantom members (making every name permanently ambiguous) and cost one
#: nested department walk each. On this board the roster degrades to
#: posters-only, exactly as it behaved before this release.
_LEDGER_BOARDS = frozenset({"chat"})

#: Runs that have STOPPED. Everything else (created/initializing/running/
#: waiting/paused/delegating/reviewing, and an unknown state) is live.
_DONE_STATES = frozenset({"completed", "cancelled", "failed"})


def run_identity(run: AgentRun | None) -> str:
    """The roster-style NAME of a run — "builder", "researcher", ....

    HONEST LIMIT: ``AgentRun`` persists only the base ``agent_type``, so a
    dynamic agent spawned as ``custom:<slug>`` (and a remote) currently reports
    its base type here. The moment the spawn path records the canonical roster
    name on the run row, this function is the ONE place that has to change —
    everything else already speaks names.
    """
    if run is None:
        return ""
    return str(getattr(getattr(run, "agent_type", None), "value", "") or "agent")


def _state_of(run: AgentRun | None) -> str:
    """The run's lifecycle state as a plain string ("running"/"completed"/...),
    so the roster can say who is BUSY without a second query. ``""`` when
    unknown."""
    return str(getattr(getattr(run, "state", None), "value", "") or "")


def resolve_board_id(engine: Engine, session_id: str, agent_run_id: str) -> str:
    """Return the department board id for a running agent.

    Walks the ``AgentRun`` parent chain to the root and returns the root run's
    session id. Falls back to ``session_id`` when the run can't be resolved (e.g.
    a tool invoked outside a persisted run), which keeps each call scoped to at
    least its own session — never a shared/global board.
    """
    seen: set[str] = set()
    with session_scope(engine) as db:
        run = db.get(AgentRun, agent_run_id)
        if run is None:
            return session_id
        while run.parent_id and run.parent_id not in seen:
            seen.add(run.id)
            parent = db.get(AgentRun, run.parent_id)
            if parent is None:
                break
            run = parent
        return run.session_id


class BlackboardStore:
    """Post/read/list over the department-scoped :class:`BlackboardRecord` table."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def board_id_for(self, session_id: str, agent_run_id: str) -> str:
        return resolve_board_id(self.engine, session_id, agent_run_id)

    def name_for(self, agent_run_id: str) -> str:
        """The addressable NAME of one run — ``""`` when there is no run row
        (chat, a deleted run, a tool called outside a persisted run). Never
        raises: an identity lookup must not be able to fail a post."""
        if not agent_run_id:
            return ""
        try:
            with session_scope(self.engine) as db:
                return run_identity(db.get(AgentRun, agent_run_id))
        except Exception:  # noqa: BLE001 — no identity is not an error
            return ""

    def post(
        self,
        board_id: str,
        author: str,
        text: str,
        *,
        kind: BlackboardKind = BlackboardKind.NOTE,
        to_agent: str | None = None,
        author_name: str = "",
        to_name: str | None = None,
    ) -> BlackboardRecord:
        record = BlackboardRecord(
            board_id=board_id,
            author=author,
            kind=kind,
            to_agent=to_agent,
            author_name=author_name,
            to_name=to_name,
            text=text,
        )
        # Fresh session per call (no shared Session across concurrent coroutines);
        # add+commit has no await between them, so the write is atomic w.r.t. the
        # cooperative scheduler.
        with session_scope(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
        return record

    def list(
        self,
        board_id: str,
        *,
        since: datetime | None = None,
        to_agent: str | None = None,
        to_name: str | None = None,
    ) -> list[BlackboardRecord]:
        """Board entries, oldest first.

        ``to_agent``/``to_name`` together answer "addressed to ME": a row
        matches on the run id, OR on the name when the row carries NO run id.
        The emptiness guard is load-bearing — two sibling ``builder`` runs share
        a name, so a bare name match would hand one sibling's directed message
        to the other.
        """
        with session_scope(self.engine) as db:
            stmt = select(BlackboardRecord).where(BlackboardRecord.board_id == board_id)
            if since is not None:
                stmt = stmt.where(BlackboardRecord.created_at > since)
            by_name = and_(
                BlackboardRecord.to_name == to_name,
                or_(
                    BlackboardRecord.to_agent.is_(None),  # type: ignore[union-attr]
                    BlackboardRecord.to_agent == "",
                ),
            )
            if to_agent is not None and to_name:
                stmt = stmt.where(or_(BlackboardRecord.to_agent == to_agent, by_name))
            elif to_agent is not None:
                stmt = stmt.where(BlackboardRecord.to_agent == to_agent)
            elif to_name:
                stmt = stmt.where(by_name)
            # Deterministic chronological order; id is a stable tiebreak.
            stmt = stmt.order_by(BlackboardRecord.created_at, BlackboardRecord.id)
            return list(db.exec(stmt))

    def _seed_runs(self, board_id: str) -> list[str]:
        """Run ids in the board's own session that really belong to this board.

        Membership is decided by :func:`resolve_board_id` — the ONE department
        walk, shared with the worklist — never by a re-derivation of it.

        ``_LEDGER_BOARDS`` is skipped: those sessions hold accounting rows, not
        agents, and treating them as members is worse than having no roster.
        """
        if board_id in _LEDGER_BOARDS:
            return []
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(AgentRun)
                    .where(AgentRun.session_id == board_id)
                    .order_by(AgentRun.created_at, AgentRun.id)  # type: ignore[arg-type]
                    .limit(_MAX_ROSTER_RUNS)
                )
            )
            candidates = [(r.id, r.session_id) for r in rows]
        # Outside the scope above: resolve_board_id opens its own session.
        return [
            run_id
            for run_id, session_id in candidates
            if resolve_board_id(self.engine, session_id, run_id) == board_id
        ]

    def _department_runs(self, db: Session, board_id: str) -> dict[str, dict]:
        """Every run on this board — the seeds plus their whole delegation
        subtree — in breadth-first (root-first) order. Bounded on both depth and
        count; a run is recorded once."""
        members: dict[str, dict] = {}
        frontier = [
            run
            for run in (db.get(AgentRun, rid) for rid in self._seed_runs(board_id))
            if run is not None
        ]
        depth = 0
        while frontier and depth <= _MAX_ROSTER_DEPTH:
            if len(members) >= _MAX_ROSTER_RUNS:
                break
            added: list[str] = []
            for run in frontier:
                if run.id in members or len(members) >= _MAX_ROSTER_RUNS:
                    continue
                members[run.id] = {
                    "agent_run_id": run.id,
                    "handle": run_identity(run),
                    "state": _state_of(run),
                    "posts": 0,
                }
                added.append(run.id)
            if not added:
                break
            frontier = list(
                db.exec(
                    select(AgentRun)
                    .where(AgentRun.parent_id.in_(added))  # type: ignore[union-attr]
                    .order_by(AgentRun.created_at, AgentRun.id)  # type: ignore[arg-type]
                    .limit(_MAX_ROSTER_RUNS)
                )
            )
            depth += 1
        return members

    def roster(self, board_id: str) -> list[dict]:
        """The team roster — every agent ADDRESSABLE on this board.

        Derived from the ``AgentRun`` rows that belong to the department (the
        root session's runs plus their delegation subtree), NOT from who has
        posted. That was the defect: a teammate who had never spoken was
        invisible here, so it could be named in a summary and addressed by
        nobody. Authors with no run row (the ``"chat"`` seam, a deleted run) are
        merged in afterwards so the board never shows a message from an agent
        the roster denies exists.

        Bounded and NEVER raises — discovery degrading to an empty list is
        survivable, discovery taking the tool call down with it is not.
        """
        try:
            with session_scope(self.engine) as db:
                members = self._department_runs(db, board_id)
                rows = list(
                    db.exec(
                        select(BlackboardRecord).where(
                            BlackboardRecord.board_id == board_id
                        )
                    )
                )
                counts: dict[str, int] = {}
                for r in rows:
                    counts[r.author] = counts.get(r.author, 0) + 1
                for run_id, entry in members.items():
                    entry["posts"] = counts.pop(run_id, 0)
                extra: list[dict] = []
                for run_id, posts in sorted(counts.items()):
                    if not run_id:
                        continue
                    run = db.get(AgentRun, run_id)
                    extra.append(
                        {
                            "agent_run_id": run_id,
                            "handle": run_identity(run) or "agent",
                            "state": _state_of(run),
                            "posts": posts,
                        }
                    )
                return list(members.values()) + extra
        except Exception:  # noqa: BLE001 — discovery must not break a tool call
            return []

    def resolve_addressee(
        self,
        board_id: str,
        query: str,
        me: str = "",
        roster: list[dict] | None = None,
    ) -> tuple[str, str, list[dict]]:
        """Resolve a recipient the model typed to ``(run_id, name, candidates)``.

        Order: exact run id → exact name on this board (case-insensitive) →
        unresolved. Ambiguity is NEVER broken silently: two LIVE ``builder``
        children return ``("", "", [both rows])`` so the caller can refuse and
        name the distinct run ids. Unresolved returns ``("", "", [])``.

        Two rules keep a name USABLE rather than technically-correct:

        * ``me`` (the caller's run id) is not a candidate for its own name. A
          builder addressing "builder" on a board with one other builder means
          the OTHER one — reporting that as ambiguous, and handing the model its
          own run id as an addressable teammate, produces exactly the
          self-addressed unreadable row this unit exists to eliminate.
        * LIVE runs win over finished ones. ``delegate`` BLOCKS, so every
          delegated child is ``completed`` by the time the parent resumes;
          counting the graveyard would make the second delegation of any agent
          type kill name-addressing for that type for the rest of the session.
          Only when NO live run carries the name do finished ones answer for it
          (a completed teammate's board is still worth writing to) — and two
          finished namesakes are still ambiguous.

        ``roster`` may be supplied by a caller that already fetched it, so a
        refusal path does not walk the department twice.
        """
        wanted = (query or "").strip()
        if not wanted:
            return "", "", []
        entries = self.roster(board_id) if roster is None else roster
        for entry in entries:
            if entry.get("agent_run_id") == wanted:
                return wanted, str(entry.get("handle") or ""), []
        matches = [
            e
            for e in entries
            if str(e.get("handle") or "").casefold() == wanted.casefold()
            and e.get("agent_run_id") != me
        ]
        live = [e for e in matches if str(e.get("state") or "") not in _DONE_STATES]
        if live:
            matches = live
        if len(matches) == 1:
            return (
                str(matches[0].get("agent_run_id") or ""),
                str(matches[0].get("handle") or ""),
                [],
            )
        if len(matches) > 1:
            return "", "", matches
        return "", "", []
