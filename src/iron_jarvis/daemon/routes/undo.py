"""TX-01 time-travel routes: list undoable actions and reverse one.

``GET /undo``           — recent reversible, not-yet-undone actions (newest first),
                          joining the UndoJournal inverse to its ToolInvocation.
``POST /undo/{id}``     — replay a captured inverse through the SAME tool +
                          PermissionEngine + fs policy as the forward mutation,
                          then mark the action undone AND write the undo itself
                          into the ledger (a new ToolInvocation with ``undo_of``)
                          + publish ``action.reverted``.

Moved into routes/ like the other domains; closure-local state is reached
through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from pathlib import Path
from typing import Any

from sqlmodel import select

from ...core.failures import plain_failure
from ...core.config import restore_config_values
from ...core.db import session_scope
from ...core.events import EventType
from ...core.ids import new_id, utcnow
from ...core.models import PermissionMode, Session, ToolInvocation, UndoJournal
from ...tools.base import Reversibility, ToolContext
from ...tools.undo import RevertConflict

logger = logging.getLogger("iron_jarvis.undo")


# THE UNDO PRESS IS THE CONSENT (third fix plan, PG4, decision K4). A revert
# meets the permission gate of the tool that made the change, and the gate
# was asked with nobody there to answer — so a tool set to ask (every rename,
# move or copy of a download on a default install; every file edit for a
# person who set edits to ask) could never be undone. When a PERSON pressed
# Undo (this route, a task card's Revert, a coding session's Rewind) the gate
# is handed the consent for THAT tool's permission key for THAT one revert
# (``session_allow``): a tool set to never stays refused, and the folder
# policy and every other check the revert passes through are unchanged. An
# undo a MODEL asks for (``change_undo``, ``config_undo``) passes a plain id
# and gets no consent: its gate is what it was.


class PressedUndo(str):
    """An action id a PERSON pressed Undo, Revert or Rewind on — the only way
    besides ``pressed=True`` that :func:`perform_undo` hands the gate the
    consent. A plain ``str`` never carries it, so a caller that does not say
    a person pressed is refused as before (fail closed). ``harness/rewind.plan``
    mints these for the actions of the Rewind the person asked for."""

    __slots__ = ()


def _pressed(action_id: Any, pressed: bool) -> bool:
    return bool(pressed) or isinstance(action_id, PressedUndo)


#: The 422 for a change whose tool is gone (was "tool '<name>' is no longer
#: registered — cannot undo").
TOOL_GONE = "This change cannot be undone: the tool that made it is no longer part of Iron Jarvis."


def _doing_words(tool_name: str) -> str:
    """What the tool does, in the approval card's own words ("save a file")."""
    try:
        from ...agents.mission import card_words

        return card_words(tool_name)
    except Exception:  # noqa: BLE001 — words only
        return "use the tool that made it"


def revert_refusal(permissions: Any, tool: Any, *, pressed: bool) -> str:
    """Why the gate refuses to put back a change *tool* made — ``""`` when it
    lets the revert run. ``pressed``: a person pressed Undo, so an ASK is
    answered by that press (only for this tool's own permission key; a
    never is never lifted). The reason is a plain clause that follows
    "This change was not undone: " / a rewind's file name. Never raises."""
    key = tool.perm_key()
    try:
        decision = permissions.authorize(key, {}, session_allow=frozenset({key}) if pressed else None)
    except Exception:  # noqa: BLE001 — a gate fault refuses, in words
        logger.warning("undo: the permission check for %s failed", key, exc_info=True)
        return "Iron Jarvis could not check whether it may put it back"
    if decision.allowed:
        return ""
    words = _doing_words(getattr(tool, "name", "") or key)
    if decision.mode is PermissionMode.DENY:
        return f"putting it back means letting Iron Jarvis {words}, and your settings never allow that"
    return (
        f"putting it back means letting Iron Jarvis {words}, which asks you first, "
        "so only you can undo it, with its Undo in the app"
    )


# NOTHING HERE TOUCHES THE DATABASE ON THE EVENT LOOP (v1.311.0, finding
# undo-revert-db-writes-on-loop). Both routes are `async def` — `tool.revert`
# is a coroutine that offloads its own file I/O — and every SQLite
# transaction they opened ran ON the loop: a revert of N actions was ~2N
# transactions there, and with BUSY_TIMEOUT_MS = 30 s one write lock held
# elsewhere (a backup, a VACUUM) froze every chat stream, pane and poll —
# the v1.153.1 shape. The lookup, the finalize write, the candidate query and
# the config restore are sync helpers run through `asyncio.to_thread`; an
# HTTPException raised inside one propagates through the await unchanged.


def _lookup(d, action_id: str) -> dict[str, Any]:
    """Step 1: the action + its captured inverse, snapshotted into plain
    values before the session closes (SQLModel attrs expire after commit)."""
    with session_scope(d.platform.engine) as db:
        inv = db.get(ToolInvocation, action_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="unknown action")
        if inv.undone_at is not None:
            raise HTTPException(status_code=409, detail="action already undone")
        journal = db.get(UndoJournal, action_id)
        session = db.get(Session, inv.session_id)
        out: dict[str, Any] = {
            "tool_name": inv.tool,
            "session_id": inv.session_id,
            "agent_run_id": inv.agent_run_id,
            "reversibility": (inv.reversibility or "").lower(),
            "workspace_path": session.workspace_path if session is not None else "",
            "has_journal": journal is not None,
            "journal_kind": "",
            "journal_reversible": False,
            "desc": {},
        }
        if journal is not None:
            out["journal_kind"] = journal.kind
            out["desc"] = {
                "kind": journal.kind,
                "reversible": bool(journal.reversible),
                "pre_ref": journal.pre_ref,
                "pre_inline": journal.pre_inline,
                "pre_sha256": journal.pre_sha256,
                "post_sha256": journal.post_sha256,
            }
            out["journal_reversible"] = bool(journal.reversible)
        return out


def _restore_settings(d, desc: dict[str, Any]) -> str:
    """Step 3: reverse a settings change — through THE one settings writer
    (calm UI redesign S2), so an undo runs the SAME live side effects a
    save does (endpoints re-pointed, every loop re-armed — the old copy
    here re-armed only autonomy/sentinels, so undoing a calendar, fleet or
    browser change waited for a restart). Off the loop (it may start or
    stop a process, write files)."""
    from .settings import config_writer

    try:
        pre = json.loads(desc.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        pre = {}
    prior = pre.get("prior", {}) if isinstance(pre, dict) else {}
    device_id = str(pre.get("device_id") or "") if isinstance(pre, dict) else ""
    change = config_writer(d).restore(
        prior, device_id=device_id, restore_fn=restore_config_values
    )
    words = f"undo: restored settings {', '.join(change.updated) or '(none)'}"
    skipped = getattr(change, "skipped", None) or {}
    if skipped:
        # v1.449.0 (readiness Wave C, item 24 review): a key the writer could
        # not put back is SAID with its reason, never left as a quiet "(none)".
        words += "; not restored: " + "; ".join(f"{k} ({why})" for k, why in skipped.items())
    return words


def _finalize(
    d,
    action_id: str,
    undo_inv_id: str,
    session_id: str,
    agent_run_id: str,
    tool_name: str,
    result_output: str,
) -> None:
    """Step 5's write transaction: mark the action undone, consume the
    journal, and record the undo AS a first-class ledger entry."""
    with session_scope(d.platform.engine) as db:
        inv = db.get(ToolInvocation, action_id)
        if inv is None:  # deleted underneath us (race) — nothing to finalize
            raise HTTPException(status_code=404, detail="unknown action")
        if inv.undone_at is not None:  # a concurrent undo won
            raise HTTPException(status_code=409, detail="action already undone")
        inv.undone_at = utcnow()
        db.add(inv)
        j = db.get(UndoJournal, action_id)
        if j is not None:
            j.applied_at = utcnow()
            db.add(j)
        db.add(
            ToolInvocation(
                id=undo_inv_id,
                session_id=session_id,
                agent_run_id=agent_run_id,
                tool=tool_name,
                args_json="{}",
                verdict=PermissionMode.ALLOW,
                ok=True,
                output=(result_output or "")[:4000],
                # the undo action itself is not further reversible
                reversibility=Reversibility.IRREVERSIBLE.value,
                undo_of=action_id,
            )
        )
        db.commit()


def _revert_candidates(d, session_id: str) -> list[str]:
    """``revert_session``'s candidate query (newest first)."""
    with session_scope(d.platform.engine) as db:
        if db.get(Session, session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        rows = db.exec(
            select(UndoJournal, ToolInvocation)
            .where(UndoJournal.action_id == ToolInvocation.id)
            .where(ToolInvocation.session_id == session_id)
            .where(UndoJournal.reversible == True)  # noqa: E712
            .where(ToolInvocation.undone_at == None)  # noqa: E711
            .order_by(ToolInvocation.created_at.desc())  # type: ignore[attr-defined]
        ).all()
        return [
            inv.id
            for journal, inv in rows
            if (inv.reversibility or "").lower() != Reversibility.IRREVERSIBLE.value
        ]


async def perform_undo(d, action_id: str, *, pressed: bool = False) -> dict[str, Any]:
    """Reverse one recorded action — THE body of ``POST /undo/{action_id}``
    (calm UI redesign: chat's ``config_undo`` runs this same code, so "undo
    that" in chat and the Undo button cannot drift apart). Raises
    ``HTTPException`` with the route's exact status and words.

    ``pressed`` (or a :class:`PressedUndo` id): a PERSON pressed Undo, and
    that press is the consent a tool set to ask needs (PG4, K4). Default
    False: a model's undo is gated as before."""
    consent = _pressed(action_id, pressed)
    action_id = str(action_id)
    platform = d.platform
    engine = platform.engine

    # 1) Look up the action + its captured inverse (off the loop).
    found = await asyncio.to_thread(_lookup, d, action_id)
    tool_name = found["tool_name"]
    session_id = found["session_id"]
    agent_run_id = found["agent_run_id"]
    reversibility = found["reversibility"]
    workspace_path = found["workspace_path"]
    desc: dict[str, Any] = found["desc"]
    journal_kind = found["journal_kind"]
    journal_reversible = found["journal_reversible"]
    journal = desc if found["has_journal"] else None

    # 2) Refuse honestly when there is nothing safe to reverse.
    if journal is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "no inverse was captured for this action — it cannot be undone "
                "(the action predates undo capture, or the capture failed)"
            ),
        )
    if not journal_reversible:
        raise HTTPException(
            status_code=422,
            detail=(
                "this action was recorded as non-reversible "
                "(its effect has no safe inverse) and cannot be undone"
            ),
        )
    if reversibility == Reversibility.IRREVERSIBLE.value:
        raise HTTPException(
            status_code=422,
            detail=(
                "this tool's effect leaves the machine (external send / spend) "
                "and cannot be undone"
            ),
        )

    # 3) Settings changes are reversed against the live config, not a tool
    # (a config.toml rewrite + an endpoint re-point: off the loop).
    if journal_kind == "setting_restore":
        result_output = await asyncio.to_thread(_restore_settings, d, desc)
    elif journal_kind == "secret_restore":
        # Calm UI redesign S4: a credential set from a chat card — put the
        # backed-up value back (or remove a new one). Names only, never a
        # value, in the result.
        from ...settings.credentials import CredentialError, CredentialStore

        try:
            _sec = json.loads(desc.get("pre_inline") or "{}")
        except (TypeError, ValueError):
            _sec = {}
        try:
            result_output = await asyncio.to_thread(
                CredentialStore(d).restore, _sec if isinstance(_sec, dict) else {}
            )
        except CredentialError as exc:
            # v1.489.0: an Undo that would write over a newer value refuses
            # in words (the Wave 3 Undo rule), never a 500.
            raise HTTPException(status_code=409, detail=str(exc))
    elif journal_kind == "record_restore":
        # Calm UI redesign S5: a schedule / workflow / channel / app
        # changed from chat — put the snapshot back (a created record is
        # removed, a removed one recreated). No tool permission is asked:
        # an Undo restores what the user had.
        from ...settings import records as _records

        try:
            _rec = json.loads(desc.get("pre_inline") or "{}")
            if isinstance(_rec, dict):
                # Next leaps 41-a (D-41.2): whether a PERSON pressed Undo, said
                # here and only here (any value the journal holds is replaced):
                # an Undo that would turn a paired computer's switch back on
                # needs that press (settings/records_peers.restore_kind).
                _rec["pressed"] = bool(consent)
            result_output = await asyncio.to_thread(
                _records.restore, platform, _rec if isinstance(_rec, dict) else {}
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=plain_failure("That change could not be undone.", exc))
    elif journal_kind == "excel_live_restore":
        # v1.466.0: a change made in a workbook open in Excel. Like a record
        # restore, no tool permission is asked: an Undo puts back what the
        # user had, and it leaves alone any cell changed again since.
        from ...documents import excel_live as _live

        _words, _ok = await _live.restore(desc)
        if not _ok:
            raise HTTPException(status_code=409, detail=_words)
        result_output = _words
    else:
        # 4) Tool-backed revert: same tool, same PermissionEngine + fs policy.
        # A person's press answers the tool's ask (PG4, K4); a never stays.
        tool = platform.registry.get(tool_name)
        if tool is None:
            raise HTTPException(status_code=422, detail=TOOL_GONE)
        why = revert_refusal(platform.permissions, tool, pressed=consent)
        if why:
            raise HTTPException(
                status_code=403, detail=f"This change was not undone: {why}."
            )
        # The workspace the envelope's relative path was captured against
        # (v1.166.3) beats any reconstruction: chat runs as session id
        # "chat" with NO Session row (the old fallback guessed
        # workspaces_dir/chat and every chat undo 409'd or targeted the
        # wrong tree), and chat's workspace also varies per turn (uploads
        # vs. the grounded project). Rows from before the stamp fall back
        # to the historical reconstruction unchanged.
        env_workspace: str | None = None
        try:
            _env = json.loads(desc.get("pre_inline") or "{}")
            if isinstance(_env, dict):
                raw_ws = _env.get("workspace")
                if isinstance(raw_ws, str) and raw_ws and Path(raw_ws).is_absolute():
                    env_workspace = raw_ws
        except (TypeError, ValueError):
            pass
        workspace = (
            Path(env_workspace)
            if env_workspace
            else Path(workspace_path)
            if workspace_path
            else platform.config.workspaces_dir / session_id
        )
        ctx = ToolContext(
            workspace=workspace,
            session_id=session_id,
            agent_run_id=agent_run_id,
            config=platform.config,
            event_bus=platform.event_bus,
            engine=engine,
        )
        try:
            result = await tool.revert(desc, ctx)
        except RevertConflict as exc:
            # The target changed since the action — refuse rather than clobber.
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — a bad revert must not 500
            raise HTTPException(
                status_code=409, detail=plain_failure("That change could not be undone.", exc)
            )
        if not result.ok:
            raise HTTPException(
                status_code=409, detail=result.error or "undo failed"
            )
        result_output = result.output

    # 5) Mark the action undone, consume the journal, and record the undo AS a
    # first-class ledger entry (undo_of=<original>). Re-fetch fresh rows.
    # NOTE: step 4 already mutated the target on disk. If this bookkeeping
    # transaction fails, that effect is NOT rolled back — so surface the failure
    # loudly (below) rather than let a reverted-but-unrecorded state pass silently.
    undo_inv_id = new_id("tool")
    try:
        await asyncio.to_thread(
            _finalize,
            d,
            action_id,
            undo_inv_id,
            session_id,
            agent_run_id,
            tool_name,
            result_output,
        )
    except HTTPException:
        raise  # the intended race conditions (404/409) — not an inconsistency
    except Exception as exc:  # noqa: BLE001 — finalize failed AFTER the revert ran
        logger.error(
            "undo %s: effect reverted but ledger finalize failed: %s",
            action_id, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "the action was reverted on disk but the audit ledger could not "
                "be updated (Iron Jarvis kept the details in its log); the timeline may still show "
                "it as undoable"
            ),
        )

    await platform.event_bus.publish(
        EventType.ACTION_REVERTED,
        {
            "action_id": action_id,
            "undo_invocation_id": undo_inv_id,
            "tool": tool_name,
            "kind": journal_kind,
        },
        session_id=session_id,
    )
    # v1.507.0 (north-star plan 7.1): undoing a change a CHAT reply made is
    # the owner saying it was wrong — it leaves ONE suggestion in
    # Improvements, with that reply's chat as evidence (only when the saved
    # chat names the change for certain). Off the loop; never fails the undo.
    if session_id == "chat":
        from ...learning import went_wrong

        row = await asyncio.to_thread(went_wrong.suggest_from_undo, engine, action_id)
        await went_wrong.publish_suggested(platform.event_bus, row)
    return {
        "undone": action_id,
        "undo_invocation_id": undo_inv_id,
        "tool": tool_name,
        "output": result_output,
    }


def _newest_first(d, action_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Next leaps 40-a: ``(changes newest first, ids that are no change)``.
    Each change carries its name (``records_history.change_row``) so a skip
    can say which one it was. Blocking."""
    from ...settings import records_history as H

    found: list[tuple[Any, dict[str, Any]]] = []
    missing: list[dict[str, Any]] = []
    with session_scope(d.platform.engine) as db:
        rows = {
            inv.id: inv
            for inv in db.exec(select(ToolInvocation).where(ToolInvocation.id.in_(action_ids)))  # type: ignore[attr-defined]
        }
        for aid in action_ids:
            inv = rows.get(aid)
            if inv is None or inv.undo_of is not None:
                missing.append({"id": aid, "target": "", "what": "", "kind": "", "reason": "there is no such change"})
                continue
            row = H.change_row(inv, db.get(UndoJournal, aid))
            found.append((inv.created_at, {"id": aid, "target": row["target"], "what": row["what"], "kind": row["kind"]}))
    # NEWEST FIRST, the only order that composes (the run revert's rule):
    # three edits to one file replay backwards to the original.
    found.sort(key=lambda pair: (pair[0] is not None, pair[0], pair[1]["id"]), reverse=True)
    return [row for _at, row in found], missing


async def undo_many(d, action_ids: Any, *, pressed: bool) -> dict[str, Any]:
    """Next leaps 40-a: put several changes back — THE Undo
    (:func:`perform_undo`) once per change, NEWEST FIRST whatever order they
    were picked in, exactly as ``POST /sessions/{id}/revert`` does. A change
    that cannot be put back (changed since, already undone, no such change)
    is skipped BY NAME with why, and the rest still revert: never
    all-or-nothing. ``pressed``: a person pressed "Undo these" (the page),
    the consent a tool set to ask needs (PG4); chat's ``change_undo`` passes
    False, as its single Undo does. ``{reverted, skipped, considered,
    words}``."""
    from ...settings import records_day as RD

    ids = list(dict.fromkeys(str(a).strip() for a in (action_ids or []) if str(a or "").strip()))
    known, skipped = await asyncio.to_thread(_newest_first, d, ids)
    reverted: list[dict[str, Any]] = []
    for row in known:
        aid = row["id"]
        try:
            out = await perform_undo(d, PressedUndo(aid) if pressed else aid, pressed=pressed)
            reverted.append({**row, "undo_invocation_id": out.get("undo_invocation_id")})
        except HTTPException as exc:
            skipped.append({**row, "reason": RD.plain_skip(exc.detail)})
        except Exception as exc:  # noqa: BLE001 — one bad change, not the batch
            logger.warning("undo many: change %s failed", aid, exc_info=True)
            skipped.append({**row, "reason": plain_failure("That change could not be undone.", exc)})
    return {
        "reverted": reverted,
        "skipped": skipped,
        "considered": len(ids),
        "words": RD.put_back_words(reverted, skipped),
    }


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    # ---- Your day (next leaps 40-a, R2.18) --------------------------------
    # The Activity page's "Your day" tab. Every read and every Undo is off
    # the loop; before and after is for the person's own screen (D-40.2).

    @app.get("/changes/day")
    async def changes_day(date: str = "") -> dict[str, Any]:
        """One LOCAL day's changes, grouped by where they came from, with
        what cannot be undone listed apart (``records_day.day_changes``)."""
        from ...settings import records_day as RD

        day = RD.parse_day(date) if str(date or "").strip() else RD.today_local()
        if day is None:
            raise HTTPException(status_code=422, detail="date is a day as YYYY-MM-DD")
        return await asyncio.to_thread(RD.day_changes, d.platform.engine, day, home=d.platform.config.home)

    @app.get("/changes/{action_id}/before-after")
    async def change_before_after(action_id: str) -> dict[str, Any]:
        """One change's before and after, read on this PC (D-40.2)."""
        from ...settings import records_day as RD
        from .settings import config_writer

        writer = config_writer(d)
        out = await asyncio.to_thread(RD.before_after, d.platform, action_id, current_setting=writer.current)
        if out is None:
            raise HTTPException(status_code=404, detail="unknown change")
        return out

    @app.post("/changes/undo")
    async def changes_undo(request: Request) -> dict[str, Any]:
        """"Undo these": the changes ticked on the page, newest first, after
        the one confirmation that listed them (D-40.1). The press is the
        consent (PG4)."""
        from ...settings import records_day as RD

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="send {action_ids: [...]}")
        ids = body.get("action_ids") if isinstance(body, dict) else None
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) and x.strip() for x in ids):
            raise HTTPException(status_code=422, detail="send {action_ids: [...]}, the changes to undo")
        if len(ids) > RD.PAGE_MAX:
            raise HTTPException(
                status_code=422, detail=f"At most {RD.PAGE_MAX} changes can be undone at once; this asked for {len(ids)}."
            )
        return await undo_many(d, ids, pressed=True)

    @app.get("/undo")
    def list_undoable(
        limit: int = 100, session_id: str | None = None
    ) -> dict[str, Any]:
        """Recent reversible actions still eligible for undo (newest first).

        v1.168.0, ADDITIVE: ``session_id`` narrows the list to one session's
        actions (chat's file writes all run as session id ``"chat"``), and each
        row also carries ``path`` (the journal envelope's workspace-relative
        target) and ``workspace`` (the v1.166.3 capture-time stamp). Both are
        ``null`` when the envelope has no single target — a pathless kind
        (``setting_restore``), a multi-file ``files_delete`` envelope, or a
        pre-stamp row — so a client can only ever join a row to a file it can
        actually name, never to a guess. ``path`` is also ``null`` when the
        envelope journaled an ABSOLUTE target (``memory_*`` kinds record the
        LTM store's absolute file path — see ltm/tools.py and
        memory/proposals.py): the field's contract is workspace-relative, and
        ``workspace + "/" + <absolute>`` joins to a path that names no real
        file, so reporting it verbatim would hand a future consumer a value
        that only ever mis-joins.
        """
        engine = d.platform.engine
        limit = max(1, min(int(limit or 100), 500))
        items: list[dict[str, Any]] = []
        with session_scope(engine) as db:
            stmt = (
                select(UndoJournal, ToolInvocation)
                .where(UndoJournal.action_id == ToolInvocation.id)
                .where(UndoJournal.reversible == True)  # noqa: E712
                .where(ToolInvocation.undone_at == None)  # noqa: E711
            )
            if session_id:
                stmt = stmt.where(ToolInvocation.session_id == session_id)
            rows = db.exec(
                stmt.order_by(ToolInvocation.created_at.desc()).limit(limit)
            ).all()
            for journal, inv in rows:
                rev = (inv.reversibility or "").lower()
                # The envelope in pre_inline is the ONLY place the target path
                # and capture-time workspace live (the journal's column set is
                # fixed). Best-effort parse: an unreadable envelope reports
                # null rather than failing the whole listing.
                env_path: str | None = None
                env_workspace: str | None = None
                try:
                    meta = json.loads(journal.pre_inline or "{}")
                except (TypeError, ValueError):
                    meta = None
                if isinstance(meta, dict):
                    raw_path = meta.get("path")
                    if (
                        isinstance(raw_path, str)
                        and raw_path
                        # Honest-null: the field means workspace-RELATIVE (see
                        # docstring); memory_* envelopes journal absolute LTM
                        # store paths, which would only ever mis-join.
                        and not Path(raw_path).is_absolute()
                    ):
                        env_path = raw_path
                    raw_ws = meta.get("workspace")
                    if isinstance(raw_ws, str) and raw_ws:
                        env_workspace = raw_ws
                items.append(
                    {
                        "action_id": inv.id,
                        "session_id": inv.session_id,
                        "tool": inv.tool,
                        "kind": journal.kind,
                        "reversible": bool(journal.reversible),
                        "reversibility": inv.reversibility,
                        # eligible to undo right now: recorded reversible AND the
                        # tool didn't declare itself irreversible AND not yet undone
                        # (the query already excludes undone rows).
                        "undoable": bool(journal.reversible)
                        and rev != Reversibility.IRREVERSIBLE.value,
                        "output": (inv.output or "")[:200],
                        "created_at": inv.created_at.isoformat()
                        if inv.created_at
                        else None,
                        # v1.168.0 additive fields (see docstring).
                        "path": env_path,
                        "workspace": env_workspace,
                    }
                )
        return {"actions": items}

    @app.post("/undo/{action_id}")
    async def undo_action(action_id: str) -> dict[str, Any]:
        # The dashboard's Undo: a person pressed it (PG4, K4).
        return await perform_undo(d, action_id, pressed=True)

    @app.post("/sessions/{session_id}/revert")
    async def revert_session(session_id: str) -> dict[str, Any]:
        """Undo everything THIS session did that still has a usable inverse
        (v1.149.0) — the "Revert" on a failed task's card.

        NEWEST FIRST, which is the only order that composes: three edits to one
        file replay backwards to the original, where oldest-first would restore
        the first pre-image and then immediately re-apply the second edit's.

        It calls ``undo_action`` per action rather than reimplementing the
        revert. That handler owns the delicate parts — the since-changed hash
        guard, the permission + fs-policy replay, marking the journal consumed,
        and writing the undo INTO the ledger as its own auditable row — and a
        second copy of that logic is the kind of thing that drifts and then
        silently clobbers newer work.

        Partial results are the norm and are reported honestly: an action whose
        target changed since is REFUSED (409) and listed in ``skipped`` with the
        reason, while the rest still revert. Never all-or-nothing — a file that
        cannot be safely restored must not block restoring the four that can.
        """
        candidates = await asyncio.to_thread(_revert_candidates, d, session_id)

        reverted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for action_id in candidates:
            try:
                out = await undo_action(action_id)
                reverted.append({"action_id": action_id, "tool": out.get("tool", "")})
            except HTTPException as exc:
                skipped.append({"action_id": action_id, "reason": str(exc.detail)})
            except Exception as exc:  # noqa: BLE001 — one bad action, not the batch
                logger.warning(
                    "revert %s: action %s failed", session_id, action_id, exc_info=True
                )
                skipped.append({"action_id": action_id, "reason": plain_failure("That change could not be undone.", exc)})
        return {
            "session_id": session_id,
            "reverted": reverted,
            "skipped": skipped,
            "considered": len(candidates),
        }
