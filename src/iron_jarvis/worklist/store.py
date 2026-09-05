"""Worklist store — durable per-item job state (v1.174.0).

Pure-DB, offline-safe, and scoped to a JOB: a root session plus every subagent
delegated from it, and — critically — every LATER session that re-runs the same
job. The department walk is :func:`iron_jarvis.blackboard.store.resolve_board_id`
rather than a re-derivation of it, so the worklist and the blackboard can never
disagree about which team an agent belongs to.

Four properties carry the whole feature, and each one is a real failure mode:

1. **Adding is idempotent.** ``add`` never resets an item that already exists,
   and never queues a key that a completed item already PRODUCED. A resumed or
   re-run job therefore surveys the folder, adds nothing, and finishes — even
   though a rename job's second survey sees entirely different filenames.
2. **The board follows the JOB, not the session id.** This is what makes the
   previous property REACHABLE. ``rerun_session`` clones a session's inputs into
   a brand-new session id (and so does the user re-posting the same task), so a
   board keyed on the root session id alone starts empty every time and the
   whole ``produced``/``result_norm`` mechanism is dead code on every path the
   user actually takes. :meth:`WorklistStore.board_for_root` therefore derives
   the board from the job's IDENTITY — its project (or its own folder) plus the
   task text — and falls back to the session id only when there is no session
   row to read. Chat, which runs every turn under the literal session id
   ``"chat"``, keys on ``(session, workspace)`` exactly as
   :func:`iron_jarvis.repl.session.namespace_key` does; without that, every chat
   the user ever has would share ONE global board and a later conversation would
   be handed another client's file paths as "its own work".
3. **Claiming is a compare-and-swap — on BOTH paths.** ``claim`` flips
   ``pending -> doing`` in ONE ``UPDATE ... WHERE status = 'pending'`` that also
   stamps a per-call token, then reads back exactly the rows carrying that
   token. The stale-RECLAIM update is a CAS too, on the token the scan observed
   (``WHERE claim_token = <the one I saw>``): its first version predicated only
   on ``status = 'doing'``, which is true of every row it had just selected as
   doing, so two agents reclaiming at the same moment both "won" the same items
   — measured at 4 of 40 barrier-synchronised trials. On the acceptance folder
   that is two siblings renaming the same tax document.
4. **A dead claim is reclaimable.** An agent that dies mid-chunk leaves items in
   ``doing``, and nothing else would ever hand them out again — the resumed run
   would report "0 pending" over work that was never finished. Two ways back:
   a claim whose owning ``AgentRun`` has ENDED is re-offered immediately (there
   is no other agent to wait for), and any claim older than ``stale_seconds`` is
   re-offered as a backstop for a run that vanished without recording a state.
   The tool SAYS SO either way, because a silently re-run item is how the same
   file gets processed twice.

Everything here is synchronous SQLite (the module-level convention in
``core/db.py``). The tools call it through ``asyncio.to_thread`` — a bulk add
of hundreds of rows is real work, and the daemon is one event loop.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import Engine, update
from sqlmodel import select

from ..blackboard.store import resolve_board_id
from ..core.db import session_scope
from ..core.ids import new_id, utcnow
from ..core.models import AgentRun, Session as SessionRow
from .models import (
    DOING,
    DONE,
    FAILED,
    PENDING,
    STATUSES,
    WorklistItem,
    normalize_key,
)

#: Hard ceilings. A model that surveys a drive root instead of a folder must
#: not be able to write a million rows into the user's database, and a claim of
#: "give me everything" must not return a chunk no context window can hold.
MAX_ITEMS_PER_ADD = 500
MAX_BOARD_ITEMS = 5000
MAX_CLAIM = 25
DEFAULT_CLAIM = 5
#: How long a claim may sit in ``doing`` before another agent may take it over.
#: 15 minutes: comfortably longer than any single-file document read (one live
#: OCR of a scanned return took >180s) and far shorter than a stuck job's life.
DEFAULT_STALE_SECONDS = 900
#: Cap on the ``doing`` rows examined when looking for a stale claim. Claims are
#: chunks of at most ``MAX_CLAIM``, so this covers many concurrent subagents.
_STALE_SCAN_LIMIT = 200
#: Longest key we store. Paths are long on Windows; prose is not a key.
MAX_KEY_CHARS = 500
MAX_NOTE_CHARS = 1000
#: How much of a task's text takes part in the job identity. Long enough that
#: two different jobs cannot collide on a shared preamble, short enough that the
#: hash is cheap and a trailing edit does not fork the board.
_JOB_TASK_CHARS = 400
#: ``AgentRun.state`` values that mean the run is OVER. A claim held by one of
#: these is not "in progress with another agent" — there is no other agent — so
#: it is re-offered immediately instead of after the stale window.
_ENDED_RUN_STATES = frozenset({"completed", "failed", "cancelled"})


def _norm_folder(value: Any) -> str:
    """Case/link-normalized folder key — ``repl.session.namespace_key``'s rule."""
    try:
        return os.path.normcase(os.path.realpath(os.fspath(value)))
    except Exception:  # pragma: no cover - unresolvable path: key on the text
        return os.path.normcase(str(value))


def _job_board_id(scope: str, task: str) -> str:
    """A stable id for "this job", independent of which session is running it.

    Hashed rather than concatenated because the parts are a project id, an
    absolute Windows path and a sentence of user prose — a readable composite
    would blow past any sane column width and leak the task text into a value
    the dashboard prints.
    """
    digest = hashlib.sha256(f"{scope}\n{task}".encode("utf-8", "replace")).hexdigest()
    return f"job:{digest[:32]}"


def _as_utc(value: datetime | None) -> datetime | None:
    """Compare-safe UTC form of a stored timestamp.

    SQLite hands back NAIVE datetimes for a ``DateTime`` column while
    :func:`~iron_jarvis.core.ids.utcnow` produces AWARE ones. Subtracting one
    from the other raises, so staleness is computed here in Python — never as a
    SQL predicate — and a naive value is read as the UTC it was written as.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fingerprint_file(path: str | Path) -> tuple[str | None, int | None]:
    """``(sha256, size)`` of a file, or ``(None, None)`` when unreadable.

    Chunked, so a large PDF never lands in memory whole. BLOCKING — call it
    from a thread. Never raises: a missing result file makes an item *stale*,
    which the status report says out loud; it must not fail the bookkeeping
    call that was recording honest progress.
    """
    try:
        p = Path(path)
        size = p.stat().st_size
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest(), size
    except (OSError, ValueError):
        return None, None


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _chunks(values: Sequence[str], size: int = 400) -> Iterable[Sequence[str]]:
    """SQLite caps the number of bound parameters; ``IN`` lists are chunked."""
    for start in range(0, len(values), size):
        yield values[start : start + size]


class WorklistStore:
    """Durable, job-scoped worklist over :class:`WorklistItem`."""

    def __init__(self, engine: Engine, *, config: Any = None) -> None:
        self.engine = engine
        #: Optional :class:`~iron_jarvis.core.config.Config`. Used for ONE
        #: question — whether a session's ``workspace_path`` is a folder the
        #: user chose (stable across a re-run, so part of the job's identity) or
        #: a disposable managed workspace (a new path every run, so NOT part of
        #: it). Absent = the folder never counts, which is the safe direction:
        #: two jobs keyed on their task text alone at worst share a board they
        #: both belong to, while keying on a throwaway folder would silently
        #: give every re-run a fresh, empty list.
        self.config = config

    # -- scoping ----------------------------------------------------------- #

    def board_id_for(
        self, session_id: str, agent_run_id: str, workspace: Any = None
    ) -> str:
        """The board id for a RUNNING agent.

        Two steps: the blackboard's parent walk finds the DEPARTMENT (so a
        supervisor and its subagents share one list), then
        :meth:`board_for_root` turns that root session into the JOB's board (so
        a re-run of the same job shares it too).
        """
        root = resolve_board_id(self.engine, session_id, agent_run_id)
        return self.board_for_root(root, workspace)

    def root_session_for(self, session_id: str) -> str:
        """The board id for a session id alone — the HTTP/UI path, which has no
        ``agent_run_id`` to start from.

        A TeamTree link lands the user on a CHILD session's page, whose own id
        owns no items; walk the run parent chain upward (bounded by the
        delegation depth cap, and cycle-proof), then resolve the root session to
        its job board. Falls back to the session's own id, which is at worst an
        empty list — never another team's.
        """
        current = session_id
        seen = {current}
        with session_scope(self.engine) as db:
            for _hop in range(4):
                runs = list(db.exec(select(AgentRun).where(AgentRun.session_id == current)))
                parent_run_id = next((r.parent_id for r in runs if r.parent_id), None)
                if not parent_run_id:
                    break
                parent = db.get(AgentRun, parent_run_id)
                if parent is None or parent.session_id in seen:
                    break
                current = parent.session_id
                seen.add(current)
        return self.board_for_root(current)

    def board_for_root(self, root_session_id: str, workspace: Any = None) -> str:
        """The board that owns a root session's work — the JOB, not the session.

        ``rerun_session`` clones task/agent/model/project/folder into a FRESH
        session id, and a user re-posting the same task gets a fresh id too. A
        board keyed on the session id is therefore empty on every re-run, which
        makes "a re-run does no work twice" unreachable: run 2 surveys the
        already-renamed folder, sees 26 unknown files, and renames them again.

        The identity used instead is what actually distinguishes one job from
        another: WHERE it runs (its project, or its own user-chosen folder — a
        disposable managed workspace is deliberately excluded, being a different
        path every run) plus WHAT it was asked to do. Two different tasks in one
        project stay on separate boards; the same task re-run stays on one.

        Falls back to ``(session, workspace)`` when there is no session row —
        the chat lane, whose session id is the literal string ``"chat"`` for
        every turn the user ever takes. Keying chat on that id alone would put
        every conversation, in every project, on ONE permanent board: items from
        a bulk job armed for client A would be handed to a later chat about
        client B as its own work. This is the same collision
        ``repl.session.namespace_key`` exists to prevent, so it takes the same
        shape.
        """
        scope = ""
        task = ""
        with session_scope(self.engine) as db:
            row = db.get(SessionRow, root_session_id)
            if row is not None:
                task = " ".join((row.task or "").split()).casefold()[:_JOB_TASK_CHARS]
                if row.project_id:
                    scope = f"project:{row.project_id}"
                elif self._is_user_folder(row.workspace_path):
                    scope = f"dir:{_norm_folder(row.workspace_path)}"
        if task:
            return _job_board_id(scope, task)
        if workspace:
            return f"{root_session_id}@{_norm_folder(workspace)}"
        return root_session_id

    def _is_user_folder(self, workspace_path: str) -> bool:
        """True when a session runs DIRECTLY in a folder the user named.

        Reuses ``agents.runtime.is_direct_workspace`` — the same honest signal
        the orchestrator's re-run uses to decide where a re-run lands, so the
        board and the workspace can never disagree about which folder the job
        belongs to. Imported lazily: this module is imported by ``platform.py``
        before the agent layer is assembled.
        """
        if not workspace_path or self.config is None:
            return False
        try:
            from ..agents.runtime import is_direct_workspace

            return bool(is_direct_workspace(self.config, workspace_path))
        except Exception:  # pragma: no cover - scoping must never raise
            return False

    # -- writes ------------------------------------------------------------ #

    def add(
        self,
        board_id: str,
        entries: Sequence[tuple[str, str]],
        *,
        cap: int = MAX_BOARD_ITEMS,
    ) -> dict[str, Any]:
        """Queue ``(key, label)`` pairs, skipping anything already tracked.

        Returns a report naming every category, because "added 3 of 26" is the
        only answer that tells a resuming agent what it is looking at:

        ``added``     — genuinely new pending items.
        ``existing``  — the key is already an item (its status is UNCHANGED —
                        re-adding must never resurrect a finished unit of work).
        ``produced``  — the key is the RESULT of a completed item, i.e. this is
                        a renamed file the job itself created. Not queued.
        ``duplicate`` — the same key appeared twice in this one call.
        ``skipped_cap``/``skipped_invalid`` — refused, and reported, not silent.
        """
        seen: set[str] = set()
        wanted: list[tuple[str, str, str]] = []  # (norm, key, label)
        invalid = 0
        duplicate = 0
        for raw_key, raw_label in entries[:MAX_ITEMS_PER_ADD]:
            key = _clean(raw_key, MAX_KEY_CHARS)
            norm = normalize_key(key)
            if not norm:
                invalid += 1
                continue
            if norm in seen:
                duplicate += 1
                continue
            seen.add(norm)
            wanted.append((norm, key, _clean(raw_label, 200)))
        overflow = max(0, len(entries) - MAX_ITEMS_PER_ADD)

        added: list[str] = []
        existing: list[str] = []
        produced: list[str] = []
        skipped_cap = 0
        with session_scope(self.engine) as db:
            norms = [n for n, _k, _l in wanted]
            by_key: dict[str, WorklistItem] = {}
            by_result: dict[str, WorklistItem] = {}
            for part in _chunks(norms):
                for row in db.exec(
                    select(WorklistItem).where(
                        WorklistItem.board_id == board_id,
                        WorklistItem.key_norm.in_(part),  # type: ignore[attr-defined]
                    )
                ):
                    by_key[row.key_norm] = row
                for row in db.exec(
                    select(WorklistItem).where(
                        WorklistItem.board_id == board_id,
                        WorklistItem.result_norm.in_(part),  # type: ignore[attr-defined]
                    )
                ):
                    if row.status == DONE and row.result_norm:
                        by_result[row.result_norm] = row
            room = cap - self.count(board_id, db=db)
            for norm, key, label in wanted:
                if norm in by_key:
                    existing.append(key)
                    continue
                if norm in by_result:
                    produced.append(key)
                    continue
                if room <= 0:
                    skipped_cap += 1
                    continue
                db.add(
                    WorklistItem(
                        board_id=board_id,
                        key=key,
                        key_norm=norm,
                        label=label,
                        status=PENDING,
                    )
                )
                room -= 1
                added.append(key)
            if added:
                db.commit()
        report = self.summary(board_id)
        report.update(
            {
                "added": len(added),
                "added_keys": added,
                "existing": len(existing),
                "produced": len(produced),
                "produced_keys": produced,
                "duplicate": duplicate,
                "skipped_cap": skipped_cap + overflow,
                "skipped_invalid": invalid,
            }
        )
        return report

    def claim(
        self,
        board_id: str,
        agent_run_id: str,
        count: int = DEFAULT_CLAIM,
        *,
        stale_seconds: int = DEFAULT_STALE_SECONDS,
    ) -> tuple[list[WorklistItem], int]:
        """Atomically take up to ``count`` items; return ``(items, reclaimed)``.

        ``reclaimed`` counts items that were taken over from a claim older than
        ``stale_seconds`` — an agent that died mid-chunk. It is returned rather
        than hidden so the tool can say it: an item handed out twice, silently,
        is how a file gets processed twice.
        """
        try:
            want = int(count)
        except (TypeError, ValueError):
            want = DEFAULT_CLAIM
        want = max(1, min(want, MAX_CLAIM))
        # ORDERING IS DETERMINISTIC OR IT IS NOTHING (v1.177.0). This used to
        # order by (created_at, id). A survey adds a whole folder in ONE call,
        # so every row of that batch shares a timestamp to the microsecond, and
        # `id` is `new_id("wl")` — RANDOM. The tiebreaker therefore shuffled the
        # list: which files a chunk contained, and the order a resumed run saw
        # them in, changed between runs over identical inputs. It also made
        # `test_a_dead_claim_is_reclaimed_and_a_live_one_is_not` a coin flip that
        # finally came up tails on CI (green locally, red on the runner, same
        # commit). `key_norm` is unique per board, so ordering on it before `id`
        # is total: same-batch items now come back in a stable, explainable
        # order (by key) instead of an arbitrary one.
        token = new_id("clm")
        now = utcnow()
        reclaimed = 0
        with session_scope(self.engine) as db:
            pending = list(
                db.exec(
                    select(WorklistItem)
                    .where(
                        WorklistItem.board_id == board_id,
                        WorklistItem.status == PENDING,
                    )
                    .order_by(WorklistItem.created_at, WorklistItem.key_norm, WorklistItem.id)  # type: ignore[arg-type]
                    .limit(want)
                )
            )
            ids = [row.id for row in pending]
            if ids:
                # THE COMPARE-AND-SWAP. The `status == PENDING` predicate is what
                # makes a concurrent claim lose instead of double-booking, and
                # the token is what lets us read back only the rows we won.
                db.execute(
                    update(WorklistItem)
                    .where(
                        WorklistItem.id.in_(ids),  # type: ignore[attr-defined]
                        WorklistItem.status == PENDING,
                    )
                    .values(
                        status=DOING,
                        claimed_by=agent_run_id,
                        claimed_at=now,
                        claim_token=token,
                        updated_at=now,
                    )
                )
                db.commit()
            won = list(
                db.exec(
                    select(WorklistItem).where(
                        WorklistItem.board_id == board_id,
                        WorklistItem.claim_token == token,
                    )
                )
            )
            short = want - len(won)
            if short > 0:
                candidates = list(
                    db.exec(
                        select(WorklistItem)
                        .where(
                            WorklistItem.board_id == board_id,
                            WorklistItem.status == DOING,
                            WorklistItem.claim_token != token,
                        )
                        .order_by(WorklistItem.updated_at, WorklistItem.key_norm, WorklistItem.id)  # type: ignore[arg-type]
                        .limit(_STALE_SCAN_LIMIT)
                    )
                )
                cutoff = (
                    now - timedelta(seconds=max(1, int(stale_seconds)))
                    if stale_seconds > 0
                    else None
                )
                ended = self._ended_runs(db, {r.claimed_by for r in candidates if r.claimed_by})
                # (id, the token the SCAN saw) — that token is the compare half
                # of the compare-and-swap below, and carrying it out of the scan
                # is the whole fix. Two reasons a claim is takeable, and the
                # dead-run one does not wait for the clock: there is no other
                # agent to wait for.
                takeable: list[tuple[str, str]] = []
                for row in candidates:
                    stamp = _as_utc(row.claimed_at) or _as_utc(row.updated_at) or now
                    if row.claimed_by in ended or (cutoff is not None and stamp < cutoff):
                        takeable.append((row.id, row.claim_token or ""))
                takeable = takeable[:short]
                if takeable:
                    by_token: dict[str, list[str]] = {}
                    for row_id, old_token in takeable:
                        by_token.setdefault(old_token, []).append(row_id)
                    changed = 0
                    for old_token, ids_for_token in by_token.items():
                        # THE RECLAIM COMPARE-AND-SWAP. `status == DOING` alone
                        # is NOT a CAS — it is true of every row the scan just
                        # selected as doing, so a racing agent's UPDATE matched
                        # the same rows a moment after ours and both callers
                        # read back a full chunk. Predicating on the token the
                        # scan OBSERVED makes the loser match zero rows: the
                        # first writer replaced that token with its own.
                        result = db.execute(
                            update(WorklistItem)
                            .where(
                                WorklistItem.id.in_(ids_for_token),  # type: ignore[attr-defined]
                                WorklistItem.claim_token == old_token,
                                WorklistItem.status == DOING,
                            )
                            .values(
                                status=DOING,
                                claimed_by=agent_run_id,
                                claimed_at=now,
                                claim_token=token,
                                updated_at=now,
                            )
                        )
                        changed += int(result.rowcount or 0)
                    db.commit()
                    stale_ids = [row_id for row_id, _tok in takeable]
                    retaken = [
                        row
                        for part in _chunks(stale_ids)
                        for row in db.exec(
                            select(WorklistItem).where(
                                WorklistItem.board_id == board_id,
                                WorklistItem.claim_token == token,
                                WorklistItem.id.in_(part),  # type: ignore[attr-defined]
                            )
                        )
                    ]
                    # The read-back must equal what the UPDATEs actually changed.
                    # If it ever did not, some other writer's rows would be in
                    # this chunk — hand back NOTHING rather than a set we cannot
                    # prove is ours, because the failure mode is two agents
                    # renaming the same file.
                    if len(retaken) != changed:  # pragma: no cover - defensive
                        retaken = [r for r in retaken if r.claim_token == token]
                    reclaimed = len(retaken)
                    won.extend(retaken)
            # THE AUTHORITATIVE ORDER (v1.177.0). This sort runs AFTER the
            # read-back and after `retaken` is merged in, so it — not any SQL
            # ORDER BY — decides the sequence the agent works through. It sorted
            # by (created_at, id), and `id` is `new_id("wl")`: RANDOM. A survey
            # adds a whole folder in ONE call, so on a machine whose clock ties
            # those rows the tiebreaker was pure chance, and which files a chunk
            # held changed between runs over identical input. `key_norm` is
            # unique per board, so adding it makes the order total and
            # explainable (by key) instead of arbitrary.
            #
            # The first attempt at this fix put ORDER BY on the QUERIES and
            # missed this line entirely — green here, five different orders
            # across eight CI runs. If you add another ordering somewhere,
            # remember this sort is the last word.
            won.sort(key=lambda r: (r.created_at, r.key_norm, r.id))
            # Detach: the Session closes with this block, and callers read these
            # rows afterwards (a lazy refresh on a closed session raises).
            for row in won:
                db.expunge(row)
        return won, reclaimed

    @staticmethod
    def _ended_runs(db, run_ids: Iterable[str]) -> set[str]:
        """Of these ``agent_run_id``s, the ones whose run is OVER.

        A run that hit its step ceiling mid-chunk leaves items in ``doing`` with
        a ``claimed_by`` nobody will ever hear from again. Without this the only
        way back was the 15-minute stale window, during which ``worklist_next``
        told a resumed run "they are in progress with another agent — wait for
        them", which is advice about an agent that does not exist. An UNKNOWN
        run id (chat, a tool called outside a persisted run) is deliberately NOT
        treated as ended: we cannot see that it stopped, so the clock decides.
        """
        out: set[str] = set()
        for run_id in run_ids:
            run = db.get(AgentRun, run_id)
            if run is None:
                continue
            state = getattr(run.state, "value", run.state)
            if str(state) in _ENDED_RUN_STATES:
                out.add(run_id)
        return out

    def release_run(self, agent_run_id: str, *, board_id: str | None = None) -> int:
        """Hand every item ``agent_run_id`` still holds back to ``pending``.

        The counterpart to the dead-run reclaim above, for a caller that KNOWS a
        run has ended (an orchestrator finalize hook): returning the items now
        beats waiting for the next claim to notice. Same transition ``finish
        (status="pending")`` performs, in one statement.
        """
        if not agent_run_id:
            return 0
        with session_scope(self.engine) as db:
            stmt = update(WorklistItem).where(
                WorklistItem.claimed_by == agent_run_id,
                WorklistItem.status == DOING,
            )
            if board_id:
                stmt = stmt.where(WorklistItem.board_id == board_id)
            result = db.execute(
                stmt.values(
                    status=PENDING,
                    claimed_by="",
                    claimed_at=None,
                    claim_token="",
                    updated_at=utcnow(),
                )
            )
            db.commit()
            return int(result.rowcount or 0)

    def held_by(
        self, board_id: str, agent_run_id: str, *, limit: int = MAX_CLAIM
    ) -> list[WorklistItem]:
        """The ``doing`` rows on ``board_id`` that ``agent_run_id`` ITSELF holds.

        v1.227.0 (audit A3): ``worklist_next`` answered "still claimed … being
        worked on right now, do NOT redo them" about rows the CALLER held —
        session_2fd7 claimed 25 in one call, reported 7, and was then told four
        times in a row that its own 18 were somebody else's. This is the read
        that lets the tool tell a run "you already hold these" and hand them
        back. Bounded by ``limit`` (a claim is at most ``MAX_CLAIM`` rows, so
        the default covers everything one run can hold on one board).
        """
        if not agent_run_id:
            return []
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(WorklistItem)
                    .where(
                        WorklistItem.board_id == board_id,
                        WorklistItem.status == DOING,
                        WorklistItem.claimed_by == agent_run_id,
                    )
                    # Total already: key_norm is unique per board (the
                    # v1.177.0 rule), so no id tiebreaker is needed here.
                    .order_by(WorklistItem.created_at, WorklistItem.key_norm)  # type: ignore[arg-type]
                    .limit(max(1, int(limit)))
                )
            )
            for row in rows:
                db.expunge(row)
        return rows

    def reset_failed(self, board_id: str) -> int:
        """Flip every FAILED item on ``board_id`` back to ``pending`` with its
        claim cleared; returns how many. The "re-run the failed items" door
        (v1.227.0): a follow-up run then claims exactly those rows through the
        ordinary ``worklist_next`` path — nothing done is ever touched, and a
        failed item's note is kept so the next holder can read why it failed.
        """
        with session_scope(self.engine) as db:
            result = db.execute(
                update(WorklistItem)
                .where(
                    WorklistItem.board_id == board_id,
                    WorklistItem.status == FAILED,
                )
                .values(
                    status=PENDING,
                    claimed_by="",
                    claimed_at=None,
                    claim_token="",
                    updated_at=utcnow(),
                )
            )
            db.commit()
            return int(result.rowcount or 0)

    def finish(
        self,
        board_id: str,
        key: str,
        *,
        status: str = DONE,
        note: str = "",
        result_key: str = "",
        result_sha256: str | None = None,
        result_size: int | None = None,
    ) -> WorklistItem | None:
        """Record an item's outcome. ``None`` when the key is not on this board.

        ``status=pending`` RELEASES a claim (an agent handing work back), which
        is why ``pending`` is accepted here and not only the terminal states.
        """
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        norm = normalize_key(key)
        if not norm:
            return None
        result = _clean(result_key, MAX_KEY_CHARS)
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorklistItem).where(
                    WorklistItem.board_id == board_id,
                    WorklistItem.key_norm == norm,
                )
            ).first()
            if row is None:
                return None
            row.status = status
            if note:
                row.note = _clean(note, MAX_NOTE_CHARS)
            if result:
                row.result_key = result
                row.result_norm = normalize_key(result)
                row.result_sha256 = result_sha256
                row.result_size = result_size
            if status in (DONE, FAILED, PENDING):
                # The claim is over either way. Leaving a stale claimed_by on a
                # finished row would make the stale-reclaim scan consider it.
                row.claimed_by = ""
                row.claimed_at = None
                row.claim_token = ""
            row.updated_at = utcnow()
            db.add(row)
            db.commit()
            db.refresh(row)
            db.expunge(row)
        return row

    def clear(self, board_id: str) -> int:
        """Delete every item on a board; returns how many. (Used by tests and a
        deliberate restart — never called on a resume.)"""
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(select(WorklistItem).where(WorklistItem.board_id == board_id))
            )
            for row in rows:
                db.delete(row)
            if rows:
                db.commit()
        return len(rows)

    # -- reads ------------------------------------------------------------- #

    def count(self, board_id: str, *, db=None) -> int:
        if db is not None:
            return len(
                list(
                    db.exec(
                        select(WorklistItem.id).where(WorklistItem.board_id == board_id)
                    )
                )
            )
        with session_scope(self.engine) as own:
            return self.count(board_id, db=own)

    def items(
        self,
        board_id: str,
        *,
        statuses: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[WorklistItem]:
        wanted = list(statuses) if statuses else None
        with session_scope(self.engine) as db:
            stmt = select(WorklistItem).where(WorklistItem.board_id == board_id)
            if wanted:
                stmt = stmt.where(WorklistItem.status.in_(wanted))  # type: ignore[attr-defined]
            stmt = stmt.order_by(WorklistItem.created_at, WorklistItem.key_norm, WorklistItem.id).limit(  # type: ignore[arg-type]
                max(1, int(limit))
            )
            rows = list(db.exec(stmt))
            for row in rows:
                db.expunge(row)
        return rows

    def get(self, board_id: str, key: str) -> WorklistItem | None:
        norm = normalize_key(key)
        if not norm:
            return None
        with session_scope(self.engine) as db:
            row = db.exec(
                select(WorklistItem).where(
                    WorklistItem.board_id == board_id,
                    WorklistItem.key_norm == norm,
                )
            ).first()
            if row is not None:
                db.expunge(row)
            return row

    def summary(self, board_id: str) -> dict[str, Any]:
        """Counts per status + the derived totals the UI and the tools report.

        Counted from the ROWS, never from prose or a running tally: the whole
        point of the store is that "12 of 26 done" is checkable.
        """
        counts = dict.fromkeys(STATUSES, 0)
        with session_scope(self.engine) as db:
            for row in db.exec(
                select(WorklistItem.status).where(WorklistItem.board_id == board_id)
            ):
                status = row if isinstance(row, str) else row[0]
                if status in counts:
                    counts[status] += 1
        total = sum(counts.values())
        return {
            "board_id": board_id,
            "total": total,
            "counts": counts,
            "done": counts[DONE],
            "failed": counts[FAILED],
            "pending": counts[PENDING],
            "doing": counts[DOING],
            #: Everything still owed: pending + in-flight. The one number that
            #: answers "is this job finished?" — done+failed alone would call a
            #: job with 10 items mid-flight complete.
            "remaining": counts[PENDING] + counts[DOING],
            "complete": total > 0 and counts[PENDING] + counts[DOING] == 0,
        }

    def verify_results(self, board_id: str, *, limit: int = 200) -> dict[str, Any]:
        """Check finished items' result files against the disk, and SAY HOW MANY.

        Returns ``{checkable, checked, stale, clipped}``. ``checkable`` is
        counted in SQL over every row, ``checked`` is how many this bounded pass
        actually stat'ed. The two are separate on purpose: the caller reported
        "Verified: all 200 recorded result file(s) are present" over a 400-item
        board because the only number it had was the length of a capped list. A
        capped list that reads as complete is the silent-truncation lie this
        wave exists to end.

        BLOCKING (it stats files) and bounded — the caller runs it in a thread,
        and only when asked. This is UndoJournal's "the target changed since"
        check applied to finished work: a done item whose product vanished is
        reported STALE rather than counted as finished.
        """
        with session_scope(self.engine) as db:
            base = (
                WorklistItem.board_id == board_id,
                WorklistItem.status == DONE,
                WorklistItem.result_key != "",
            )
            checkable = len(list(db.exec(select(WorklistItem.id).where(*base))))
            rows = list(
                db.exec(
                    select(WorklistItem)
                    .where(*base)
                    .order_by(WorklistItem.created_at, WorklistItem.key_norm, WorklistItem.id)  # type: ignore[arg-type]
                    .limit(max(1, int(limit)))
                )
            )
            for row in rows:
                db.expunge(row)
        stale: list[WorklistItem] = []
        for row in rows:
            path = Path(row.result_key)
            try:
                exists = path.is_file()
                size = path.stat().st_size if exists else None
            except OSError:
                exists, size = False, None
            if not exists or (row.result_size is not None and size != row.result_size):
                stale.append(row)
        return {
            "checkable": checkable,
            "checked": len(rows),
            "stale": stale,
            "clipped": checkable > len(rows),
        }

    def stale_results(self, board_id: str, *, limit: int = 200) -> list[WorklistItem]:
        """The stale rows alone (see :meth:`verify_results`, which counts too)."""
        return self.verify_results(board_id, limit=limit)["stale"]


def item_view(row: WorklistItem) -> dict[str, Any]:
    """The wire/UI shape of an item (mirrors blackboard's ``_to_view``)."""
    return {
        "id": row.id,
        "key": row.key,
        "label": row.label,
        "status": row.status,
        "note": row.note,
        "claimed_by": row.claimed_by,
        "result_key": row.result_key,
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


__all__ = [
    "DEFAULT_CLAIM",
    "DEFAULT_STALE_SECONDS",
    "MAX_BOARD_ITEMS",
    "MAX_CLAIM",
    "MAX_ITEMS_PER_ADD",
    "WorklistStore",
    "fingerprint_file",
    "item_view",
    "DOING",
    "DONE",
    "FAILED",
    "PENDING",
]
