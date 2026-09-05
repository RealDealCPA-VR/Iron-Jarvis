"""Worklist tools — the four calls that let an agent FINISH a bulk job.

``worklist_add``    — survey once, queue every unit of work (idempotent).
``worklist_next``   — CLAIM a chunk, so two subagents never take the same item.
``worklist_done``   — record an outcome (and what the item turned into).
``worklist_status`` — the counts, derived from rows, never from prose.

WHY A TOOL AND NOT A NOTE ON THE BLACKBOARD. The blackboard is prose between
teammates; nothing stops two children reading the same note and doing the same
file, and nothing survives the run's step ceiling in a form the NEXT run can
act on. These four calls put the job's state in a table with a compare-and-swap
claim, which is the difference between "I told my teammate I'd take those" and
"those are mine".

SCOPE + AUTHOR come from the running :class:`~iron_jarvis.tools.base.
ToolContext`, exactly as the blackboard tools do: the board id from the agent's
ROOT session (so a supervisor and its subagents share ONE list) and the claim
holder from ``agent_run_id``.

NOT MARKED ``returns_untrusted_content``, deliberately. What comes back is item
KEYS (paths the agent already saw from its own ``list_files``, which is not
fenced either) and notes the agents themselves wrote — never document content.
Fencing here would also mean the injection scanner can WITHHOLD a whole chunk,
and an agent that silently receives "[content withheld]" instead of its five
files fails in exactly the invisible way this wave exists to end. Document text
stays where it is scanned: the document tools.

Every store call goes through ``asyncio.to_thread`` — a 500-row bulk add is
real work and the daemon is one event loop (v1.153.1).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from .models import DOING, DONE, FAILED, PENDING, WorklistItem
from .store import (
    DEFAULT_CLAIM,
    DEFAULT_STALE_SECONDS,
    MAX_CLAIM,
    MAX_ITEMS_PER_ADD,
    WorklistStore,
    fingerprint_file,
    item_view,
)

#: How many keys a single report may name before it says "+N more" instead.
#: A 5,000-item worklist must not be able to spend the whole context window
#: describing itself — the counts are the answer, the sample is the flavour.
_LIST_SAMPLE = 25

#: Rows one status report READS. Separate from ``_LIST_SAMPLE`` (what it prints)
#: because the remainder is computed from the SUMMARY, never from this cap.
_STATUS_ROWS = 200

#: Statuses ``worklist_done`` accepts from a model, and what each means.
_DONE_STATUSES = {
    DONE: "finished",
    FAILED: "could not be finished",
    PENDING: "handed back (claim released)",
}


def _entries(args: dict[str, Any]) -> list[tuple[str, str]]:
    """Normalize the several honest shapes a model will send.

    Accepts ``items`` as a list of strings, a list of ``{key,label}`` objects, a
    newline/`;`-separated string, or a single ``key``. A model that gets the
    shape slightly wrong should still queue its work — the alternative is a
    bulk job that dies on a schema quibble, which is the failure mode being
    fixed, not a new kind of rigor.
    """
    raw = args.get("items")
    if raw is None:
        raw = args.get("keys")
    if raw is None and args.get("key"):
        raw = [args.get("key")]
    if isinstance(raw, str):
        parts = [p for chunk in raw.split("\n") for p in chunk.split(";")]
        raw = [p for p in (s.strip() for s in parts) if p]
    if not isinstance(raw, list):
        return []
    out: list[tuple[str, str]] = []
    for entry in raw:
        if isinstance(entry, dict):
            key = entry.get("key") or entry.get("path") or entry.get("name") or ""
            label = entry.get("label") or entry.get("title") or entry.get("note") or ""
            out.append((str(key), str(label)))
        elif entry is not None:
            out.append((str(entry), ""))
    return out


def _gated_fingerprint(path: str) -> tuple[str | None, int | None, str]:
    """``(sha256, size, refusal)`` for a model-supplied result path. BLOCKING.

    EVERY file tool in this app routes through ``core/fs_policy`` — including
    this one. Fingerprinting an absolute path the model chose is a file READ,
    and answering "Result recorded" for a readable path versus "could not be
    read" for an unreadable one is an existence-and-size oracle for any path on
    the disk, protected roots included. The v1.160.0 lesson was exactly this
    shape: a SECOND path around fs_policy is how the app's own Fernet key
    became reachable while ``read_file`` refused it.
    """
    allowed, reason = fs_read_ok(path)
    if not allowed:
        return None, None, reason
    # Best-effort from here: a missing result makes the item STALE in the status
    # report, it must never fail the honest bookkeeping call recording progress.
    sha, size = fingerprint_file(path)
    return sha, size, ""


def reason_note(reason: str) -> str:
    """The one sentence a refused result path gets. Says WHY, names no file
    fact — whether it exists, and how big it is, are exactly what the refusal
    is protecting."""
    return (
        f"the result path is outside the allowed roots ({reason}), so it was "
        "not opened. The item is still marked done."
    )


def _tally(summary: dict[str, Any]) -> str:
    """The one line every worklist tool ends with: where the job stands."""
    return (
        f"Worklist: {summary['done']} of {summary['total']} done"
        f" · {summary['pending']} pending"
        f" · {summary['doing']} in progress"
        f" · {summary['failed']} failed"
    )


def _sample(keys: list[str], total: int | None = None) -> str:
    """A bounded list plus an HONEST remainder.

    ``total`` is the number of items actually in that state, from the summary —
    which is counted over every row. Subtracting from ``len(keys)`` instead
    under-reports the moment the row list itself is capped: a board with 250
    pending items said "… and 175 more" when 225 remained. The panel already
    derives its "+N more" from the summary counts; the two surfaces must not
    disagree about the same board.
    """
    shown = keys[:_LIST_SAMPLE]
    text = "\n".join(f"  - {k}" for k in shown)
    hidden = max(0, (len(keys) if total is None else int(total)) - len(shown))
    if hidden:
        text += f"\n  … and {hidden} more"
    return text


class _WorklistTool(Tool):
    """Shared plumbing: the store, and the department board id for this call."""

    #: Bookkeeping only. Nothing outside the worklist table changes, so there is
    #: no file/state pre-image to capture and nothing an undo could restore.
    reversibility = Reversibility.IRREVERSIBLE

    def __init__(self, store: WorklistStore) -> None:
        self.store = store

    async def _board(self, ctx: ToolContext) -> str:
        # The department walk + the job identity are both SQLite reads — reads
        # on the loop thread all the same. Off they go.
        #
        # ctx.workspace is passed because it is the ONLY thing separating one
        # chat's worklist from another's: chat runs every turn as session id
        # "chat" with no AgentRun row, so without the folder every conversation
        # in every project would share one permanent global board.
        return await asyncio.to_thread(
            self.store.board_id_for, ctx.session_id, ctx.agent_run_id, ctx.workspace
        )


class WorklistAddTool(_WorklistTool):
    name = "worklist_add"
    description = (
        "Queue the units of work for a bulk job (one per file/record) on your "
        "team's durable worklist, so the job survives a step limit and a re-run "
        "does not redo finished work. Survey ONCE, then add every item in a "
        "single call. Args: items (a list of keys — use the full file path — or "
        "a list of {key, label} objects). Adding is idempotent: an item that is "
        "already tracked keeps its status, and a key that a finished item "
        f"already PRODUCED (e.g. a file you renamed) is not re-queued. Up to "
        f"{MAX_ITEMS_PER_ADD} items per call."
    )
    input_schema = {
        "type": "object",
        "properties": {
            # No inner item type on purpose: BOTH a list of key strings and a
            # list of {key, label} objects are accepted (see `_entries`), and a
            # schema that admitted only one of them would make the other look
            # like a model error when it is a shape we deliberately handle.
            "items": {
                "type": "array",
                "description": "Item keys (full paths), or {key, label} objects.",
            }
        },
        "required": ["items"],
    }
    permission_key = "worklist_add"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        entries = _entries(args)
        if not entries:
            return ToolResult(
                ok=False,
                error=(
                    "`items` is required — a list of keys (full file paths) or "
                    "{key, label} objects."
                ),
            )
        board_id = await self._board(ctx)
        report = await asyncio.to_thread(self.store.add, board_id, entries)
        lines = [
            f"Added {report['added']} new item(s) to the worklist."
        ]
        if report["existing"]:
            lines.append(
                f"{report['existing']} were already tracked (status unchanged)."
            )
        if report["produced"]:
            lines.append(
                f"{report['produced']} are files a FINISHED item already produced "
                "— not re-queued:\n" + _sample(report["produced_keys"])
            )
        if report["duplicate"]:
            lines.append(f"{report['duplicate']} duplicate key(s) in this call.")
        if report["skipped_cap"]:
            lines.append(
                f"{report['skipped_cap']} item(s) were REFUSED — the worklist cap "
                "was reached. Split the job or narrow the survey."
            )
        if report["skipped_invalid"]:
            lines.append(f"{report['skipped_invalid']} empty key(s) ignored.")
        lines.append(_tally(report))
        if report["remaining"]:
            lines.append("Next: call `worklist_next` to claim a chunk and start.")
        return ToolResult(ok=True, output="\n".join(lines), data=report)


class WorklistNextTool(_WorklistTool):
    name = "worklist_next"
    description = (
        "CLAIM the next chunk of pending work from your team's worklist and "
        "return it. The items become yours (status 'doing') so a sibling agent "
        "cannot take the same ones. Args: count (how many, default "
        f"{DEFAULT_CLAIM}, max {MAX_CLAIM}). Call `worklist_done` for each key "
        "when you finish it. An empty result means the job is finished — stop "
        "and summarize; do not invent more work."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": f"How many items to claim (1-{MAX_CLAIM}).",
            }
        },
    }
    permission_key = "worklist_next"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        board_id = await self._board(ctx)
        items, reclaimed = await asyncio.to_thread(
            self.store.claim,
            board_id,
            ctx.agent_run_id,
            args.get("count", DEFAULT_CLAIM),
        )
        summary = await asyncio.to_thread(self.store.summary, board_id)
        if not items:
            # THE CALLER'S OWN CLAIM IS NOT "ANOTHER RUN" (v1.227.0, audit A3).
            # A run that claimed 25, reported 7 and asked for more was told the
            # other 18 were "being worked on right now … do NOT redo them" —
            # by itself. Rows this run still holds are handed back to it here,
            # by name; only rows held by OTHER run ids get the wait-or-release
            # wording below.
            mine = await asyncio.to_thread(self.store.held_by, board_id, ctx.agent_run_id)
            others = max(0, int(summary["doing"]) - len(mine))
            if summary["total"] == 0:
                text = (
                    "The worklist is EMPTY — nothing has been queued yet. Survey "
                    "the job and call `worklist_add` first."
                )
            elif mine:
                lines = [
                    f"No unclaimed items. You already hold {len(mine)} of these — "
                    "here they are again; finish and report each one with "
                    "`worklist_done` (or hand one back with status='pending'):"
                ]
                for row in mine:
                    label = f" — {row.label}" if row.label else ""
                    lines.append(f"  - {row.key}{label}")
                if others:
                    lines.append(
                        f"{others} more are held by another run — do NOT redo "
                        "those; they are re-offered automatically if that run ends."
                    )
                lines.append(_tally(summary))
                text = "\n".join(lines)
            elif summary["doing"]:
                # HONEST, and actionable. The old wording ("in progress with
                # another agent — wait for them") is advice about an agent that
                # may not exist: a run that hit its step ceiling mid-chunk left
                # these rows claimed by a run that has ENDED. A claim from an
                # ended run is now re-offered on the spot — so anything still
                # here is either genuinely live or held by a claimant we cannot
                # see the state of, and the way out is named rather than implied.
                minutes = max(1, DEFAULT_STALE_SECONDS // 60)
                text = (
                    f"No unclaimed items: {summary['doing']} are still claimed by "
                    "another run. A claim held by a run that has ENDED is handed "
                    "back automatically, so these are either being worked on "
                    f"right now or will be re-offered about {minutes} minutes "
                    "after they were claimed. Do NOT redo them; the holder can "
                    "release one immediately with `worklist_done` status='pending'.\n"
                    + _tally(summary)
                )
            else:
                text = (
                    "No pending items — every unit of work is finished. Stop and "
                    "summarize; do not start new work.\n" + _tally(summary)
                )
            return ToolResult(
                ok=True,
                output=text,
                data={
                    "claimed": [],
                    "reclaimed": 0,
                    "summary": summary,
                    #: Rows THIS run still holds (re-offered above), and how
                    #: many are held by other runs. ``claimed`` stays empty:
                    #: nothing changed hands.
                    "held_by_me": len(mine),
                    "held": [item_view(r) for r in mine],
                    "held_by_others": others,
                },
            )
        lines = [f"Claimed {len(items)} item(s). They are yours until you report them:"]
        for row in items:
            label = f" — {row.label}" if row.label else ""
            lines.append(f"  - {row.key}{label}")
        if reclaimed:
            lines.append(
                f"NOTE: {reclaimed} of these were RECLAIMED from an earlier claim "
                "that never reported back — check whether the work was already "
                "partly done before redoing it."
            )
        lines.append(
            "Report each one with `worklist_done` (key, status, and result_path "
            "when you created or renamed a file)."
        )
        lines.append(_tally(summary))
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={
                "claimed": [item_view(r) for r in items],
                "reclaimed": reclaimed,
                "summary": summary,
            },
        )


class WorklistDoneTool(_WorklistTool):
    name = "worklist_done"
    description = (
        "Report the outcome of ONE worklist item. Args: key (exactly as it was "
        "handed to you), status ('done' — finished, 'failed' — could not be "
        "done, or 'pending' — hand it back), note (one line: what you did, or "
        "why it failed), and result_path (the file this item produced, e.g. the "
        "NEW path after a rename — this is what stops a re-run from redoing it)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "status": {"type": "string", "enum": [DONE, FAILED, PENDING]},
            "note": {"type": "string"},
            "result_path": {"type": "string"},
        },
        "required": ["key"],
    }
    permission_key = "worklist_done"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        key = str(args.get("key") or "").strip()
        if not key:
            return ToolResult(ok=False, error="`key` is required")
        status = str(args.get("status") or DONE).strip().lower()
        if status == "complete" or status == "completed" or status == "ok":
            status = DONE
        if status == "error":
            status = FAILED
        if status not in _DONE_STATUSES:
            return ToolResult(
                ok=False,
                error=(
                    f"unknown status {status!r} — use one of "
                    + ", ".join(sorted(_DONE_STATUSES))
                ),
            )
        result_path = str(args.get("result_path") or args.get("result") or "").strip()
        sha, size, refused = (None, None, "")
        if result_path and status == DONE:
            # ONE hop off the loop for BOTH the policy check (it resolves the
            # path, which touches the filesystem) and the hash. v1.153.1: the
            # daemon is one event loop and a sync filesystem touch inside a tool
            # reads to the user as "Daemon offline".
            sha, size, refused = await asyncio.to_thread(_gated_fingerprint, result_path)
        board_id = await self._board(ctx)
        row = await asyncio.to_thread(
            self.store.finish,
            board_id,
            key,
            status=status,
            note=str(args.get("note") or ""),
            result_key=result_path,
            result_sha256=sha,
            result_size=size,
        )
        summary = await asyncio.to_thread(self.store.summary, board_id)
        if row is None:
            return ToolResult(
                ok=False,
                error=(
                    f"'{key}' is not on this team's worklist — nothing was "
                    "recorded. Use the key exactly as `worklist_next` gave it, "
                    "or `worklist_add` it first."
                ),
                data={"summary": summary},
            )
        lines = [f"{row.key}: {_DONE_STATUSES[status]}."]
        if result_path and status == DONE:
            if refused:
                # No existence claim either way — the path was never opened.
                lines.append(
                    f"Result recorded WITHOUT a fingerprint: {reason_note(refused)}"
                )
            elif sha is None:
                lines.append(
                    f"WARNING: '{result_path}' could not be read, so the result "
                    "was recorded WITHOUT a fingerprint — check the path is right."
                )
            else:
                lines.append(f"Result recorded: {row.result_key}")
        lines.append(_tally(summary))
        if summary["complete"]:
            lines.append(
                "Every item is now reported. Stop claiming work and write the "
                "final summary."
            )
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"item": item_view(row), "summary": summary},
        )


class WorklistStatusTool(_WorklistTool):
    name = "worklist_status"
    description = (
        "Where the job stands: how many items are done, pending, in progress or "
        "failed, with the pending and failed keys. Counted from the durable "
        "record, not from the conversation. Args: verify (true also checks that "
        "each finished item's result file is still on disk)."
    )
    input_schema = {
        "type": "object",
        "properties": {"verify": {"type": "boolean"}},
    }
    permission_key = "worklist_status"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        board_id = await self._board(ctx)
        summary = await asyncio.to_thread(self.store.summary, board_id)
        if summary["total"] == 0:
            return ToolResult(
                ok=True,
                output=(
                    "The worklist is EMPTY — nothing has been queued for this "
                    "job yet."
                ),
                data={"summary": summary, "pending": [], "failed": []},
            )
        pending = await asyncio.to_thread(
            self.store.items, board_id, statuses=[PENDING, DOING], limit=_STATUS_ROWS
        )
        failed = await asyncio.to_thread(
            self.store.items, board_id, statuses=[FAILED], limit=_STATUS_ROWS
        )
        lines = [_tally(summary)]
        if pending:
            lines.append("Still to do:")
            # The remainder comes from the SUMMARY (every row), not from this
            # capped read — see `_sample`.
            lines.append(
                _sample(
                    [
                        f"{r.key}{' (in progress)' if r.status == DOING else ''}"
                        for r in pending
                    ],
                    summary["remaining"],
                )
            )
        if failed:
            lines.append("Failed:")
            lines.append(
                _sample(
                    [f"{r.key} — {r.note or 'no reason recorded'}" for r in failed],
                    summary["failed"],
                )
            )
        clipped = (
            len(pending) >= _STATUS_ROWS and summary["remaining"] > len(pending)
        ) or (len(failed) >= _STATUS_ROWS and summary["failed"] > len(failed))
        if clipped:
            lines.append(
                f"(This report read the first {_STATUS_ROWS} row(s) of each list; "
                "the counts above cover every item.)"
            )
        stale: list[WorklistItem] = []
        verify: dict[str, Any] = {}
        if args.get("verify"):
            verify = await asyncio.to_thread(
                self.store.verify_results, board_id, limit=_STATUS_ROWS
            )
            stale = verify["stale"]
            checkable, checked = verify["checkable"], verify["checked"]
            # "all N are present" over a CAPPED list is the silent-truncation
            # lie: `checked` is what was actually stat'ed, `checkable` is how
            # many recorded a result at all. Say both whenever they differ —
            # "Verified: all 200 recorded result file(s) are present" over a
            # 400-item board is a clean bill of health for work nobody looked at.
            scope = (
                f"the first {checked} of {checkable}"
                if verify["clipped"]
                else f"all {checked}"
            )
            if stale:
                lines.append(
                    f"STALE: {len(stale)} of {scope} checked recorded result "
                    "file(s) are gone or changed — the work may need redoing:"
                )
                lines.append(
                    _sample([f"{r.key} -> {r.result_key}" for r in stale], len(stale))
                )
            elif checked:
                lines.append(
                    f"Verified {scope} recorded result file(s): all present."
                )
            else:
                # Vacuous truth is a lie in a status report: nothing was checked.
                lines.append(
                    "Nothing to verify — no finished item recorded a result file."
                )
        if summary["complete"]:
            lines.append("Nothing is outstanding — the job is finished.")
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={
                "summary": summary,
                "pending": [item_view(r) for r in pending],
                "failed": [item_view(r) for r in failed],
                "stale": [item_view(r) for r in stale],
                #: True when these row lists are shorter than the counts — the
                #: consumer must not derive totals from `len(pending)`.
                "clipped": clipped,
                "checkable": verify.get("checkable", 0),
                "checked": verify.get("checked", 0),
            },
        )


#: The names an agent roster (``agents/types.py``) and the permission defaults
#: refer to. One list, so a roster can never drift from what is registered.
WORKLIST_TOOL_NAMES: tuple[str, ...] = (
    "worklist_add",
    "worklist_next",
    "worklist_done",
    "worklist_status",
)


def worklist_tools(store: WorklistStore) -> list[Tool]:
    """Build the worklist tool set bound to ``store``."""
    return [
        WorklistAddTool(store),
        WorklistNextTool(store),
        WorklistDoneTool(store),
        WorklistStatusTool(store),
    ]
