"""`apply_changes` — many file changes, ONE approval, each one undoable
(v1.473.0).

THE PROBLEM. "Rename these forty statements" was forty separate calls, every
argument typed by the model. A tool script could not help: a script only
reads.

THE PATH NOW. A script works out the list and notes each change with
``tools.plan("rename_file", path=..., new_path=...)`` (nothing runs; the list
is kept as ``changes``). The model then calls this tool once::

    apply_changes(_from_script={"changes": "changes"})

and the user sees ONE card that lists every change. On Allow, each change runs
through ``ToolRegistry.invoke`` like a call the model made itself: its own
ledger row, its own Undo entry, its own events.

WHAT A BATCH MAY HOLD. Only file changes that can be undone
(:data:`BATCH_TOOLS`). Nothing that sends, deletes for good, changes a
setting or reaches outside this PC: those keep their own card, one call at a
time.

CHECKED BEFORE ANYTHING RUNS. Every change must name a batch tool that is one
of this turn's tools, is not set to never run, and does not itself need an
approval of its own (a guarded file such as ``.git/config``). One change that
fails that check refuses the WHOLE batch with nothing changed, so "nothing
was changed" is always true of a refusal.

The batch's approval covers exactly the changes it listed: the grant for the
step tools is made for this one call and passed to ``invoke`` as
``session_allow``, never recorded anywhere.

ONE BATCH, ONE APPROVAL (v1.473.0 review). The card is asked ONCE, EVERY TIME
(:meth:`ApplyChangesTool.spend_check`: Allow once and Deny only). With an
ordinary card, "Allow for this chat" on a batch of one rename covered every
LATER batch, whatever it held: forty overwrites nobody saw. Only "Don't ask"
(the user's own posture) runs a batch without its card.

GUARDED FILES NEVER RIDE A BATCH. A change whose path (the file written, or
either end of a rename) is one of Git's, Iron Jarvis's or a coding agent's own
files (``core/auto_approve.sensitive_kind``: ``.git/**``, ``.ironjarvis/**``,
``.claude/**``, an AGENTS.md…) refuses the whole batch, in every posture:
made directly, that change gets its own once-only safety card.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..core import progress
from .base import Reversibility, RiskClass, SpendCheck, Tool, ToolContext, ToolResult

log = logging.getLogger("iron_jarvis.tools.apply_changes")

NAME = "apply_changes"

#: The most changes one batch may hold.
MAX_CHANGES = 200

#: Failures in a row after which the rest is not attempted (a wrong folder
#: makes every change fail the same way).
STOP_AFTER_FAILURES = 5

#: The file changes a batch may hold. Each is REVERSIBLE (it writes an Undo
#: entry) and works only on files under the app's own file rules.
BATCH_TOOLS: frozenset[str] = frozenset({
    "rename_file", "write_document", "write_file", "edit_file", "multi_edit",
    "docx_edit", "pdf_form_fill", "pdf_split", "pdf_arrange",
    "excel_edit", "excel_apply_spec", "redact_pii",
})

#: Why the batch card asks every time (``core/auto_approve.ONCE_REASON_WHY``).
BATCH_REASON = "batch"

#: The arguments of a batch tool that name a file it writes, moves or makes.
_PATH_KEYS: tuple[str, ...] = (
    "path", "new_path", "file_path", "output", "output_path", "out", "save_as",
    "dest", "destination", "target",
)

_LINE_CHARS = 160
#: Lines of the batch shown on a card and kept on the ledger row.
MAX_SHOWN = 200

_VERBS: dict[str, str] = {
    "rename_file": "Rename",
    "write_document": "Write",
    "write_file": "Write",
    "edit_file": "Edit",
    "multi_edit": "Edit",
    "docx_edit": "Edit",
    "pdf_form_fill": "Fill the form in",
    "pdf_split": "Split",
    "pdf_arrange": "Rearrange the pages of",
    "excel_edit": "Edit the workbook",
    "excel_apply_spec": "Edit the workbook",
    "redact_pii": "Redact",
}


def _one_line(text: Any, limit: int = _LINE_CHARS) -> str:
    # Bounded BEFORE it is masked (v1.473.0 review): this runs on the event
    # loop for every line of a card, and a plan whose 200 paths were 39,000
    # characters each took 0.9 s there (the mask read every character).
    text = str(text)[: max(2_000, limit * 8)]
    try:
        from ..detections.redact import mask

        text = mask(text)
    except Exception:  # noqa: BLE001 — wording only
        text = str(text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe_change(change: Any) -> str:
    """One change in plain words, for the card and the result:
    ``Rename a.pdf to 2026-a.pdf``. Reads only the path-like arguments;
    never the content being written."""
    if not isinstance(change, dict):
        return "(not a change)"
    tool = str(change.get("tool") or "")
    args = change.get("args") if isinstance(change.get("args"), dict) else {}
    verb = _VERBS.get(tool, tool or "Change")
    path = args.get("path") or args.get("file_path") or ""
    line = f"{verb} {path}".strip()
    if tool == "rename_file" and args.get("new_path"):
        line += f" to {args.get('new_path')}"
    elif args.get("output"):
        line += f" (saved as {args.get('output')})"
    return _one_line(line)


def guarded_target(change: Any, workspace: Any) -> str:
    """The first file of this change that steers Git, Iron Jarvis or a coding
    agent, as ``<path>: <why>``; "" for none. BLOCKING (the path is resolved):
    call off the event loop. Raises when a path cannot be read (the caller
    fails closed)."""
    from ..core import auto_approve as _auto

    args = change.get("args") if isinstance(change, dict) else None
    if not isinstance(args, dict) or not workspace:
        return ""
    folder = Path(workspace)
    for key in _PATH_KEYS:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        kind = _auto.sensitive_kind(value, folder)
        if kind:
            shown = _one_line(value.strip().replace("\\", "/"), 120)
            return f"{shown}: {_auto.GUARD_WHY.get(kind, _auto.GUARD_WHY['unknown'])}"
    return ""


def shown_changes(changes: Any) -> dict[str, Any]:
    """What a card and a ledger row show for a list of changes: one line
    each (the first :data:`MAX_SHOWN`), and the count."""
    rows = changes if isinstance(changes, list) else []
    return {
        "changes": [describe_change(c) for c in rows[:MAX_SHOWN]],
        "total": len(rows),
    }


def plan_problem(changes: Any, allowed: Any, registry: Any) -> str:
    """Why this list cannot be a batch, or "". The checks that need no
    context: its shape, and each change's tool. Used when a script hands its
    plan over (so the script hears at once) and again when the batch runs."""
    if not isinstance(changes, list) or not changes:
        return (
            'a batch needs a list of changes: [{"tool": "rename_file", "args": '
            '{"path": ..., "new_path": ...}}, ...]'
        )
    if len(changes) > MAX_CHANGES:
        return (
            f"that is {len(changes)} changes; one batch holds at most {MAX_CHANGES}. "
            "Split it into smaller batches"
        )
    allowed = set(allowed or ())
    for at, change in enumerate(changes, start=1):
        if not isinstance(change, dict) or not isinstance(change.get("args", {}), dict):
            return f'change {at} is not {{"tool": ..., "args": {{...}}}}'
        tool = str(change.get("tool") or "").strip()
        if not tool:
            return f"change {at} names no tool"
        if tool not in BATCH_TOOLS:
            return (
                f"change {at} uses `{tool}`, which a batch cannot hold. A batch "
                "holds file changes that can be undone: "
                + ", ".join(sorted(BATCH_TOOLS))
                + f". Call `{tool}` directly"
            )
        if tool not in allowed:
            return (
                f"change {at} uses `{tool}`, which is not one of this turn's "
                "tools, so the batch cannot run it"
            )
        if registry is not None and registry.get(tool) is None:
            return f"change {at} uses `{tool}`, which does not exist"
        if "_from_script" in (change.get("args") or {}):
            return f"change {at} carries `_from_script`; give the change its values"
        if tool == "rename_file" and (change.get("args") or {}).get("overwrite"):
            # v1.473.0 review: the card line would read "Rename a to b" and
            # say nothing of the file at b that is replaced, and that file is
            # not something this change's Undo brings back.
            return (
                f"change {at} would replace a file that is already there "
                "(overwrite). A batch never replaces a file by renaming: pick "
                "a name that is free, or make that one change directly with "
                "`rename_file`"
            )
    return ""


class ApplyChangesTool(Tool):
    name = NAME
    permission_key = NAME
    # Every change it makes writes its own Undo entry.
    reversibility = Reversibility.REVERSIBLE
    risk_class = RiskClass.PAGE_ACTION
    # ITS RESULT IS DATA (v1.473.0 review). It repeats each change's paths,
    # and a script may have taken those from a document ("rename each file
    # to the name its first line gives"). A script's own printed output is
    # fenced and scanned; without this the same text came back through the
    # batch as plain, trusted tool output.
    returns_untrusted_content = True
    description = (
        "Make MANY file changes after ONE approval: renames, writes and edits "
        "the user sees as one list. Give `changes` as a list of {\"tool\", "
        "\"args\"}, or better, let a tool script work the list out with "
        "tools.plan(...) and pass _from_script={\"changes\": \"changes\"} so "
        "nothing is retyped. A batch holds only file changes that can be "
        "undone (" + ", ".join(sorted(BATCH_TOOLS)) + "); each one is made "
        "exactly as if you had called its tool yourself and can be undone on "
        "its own. If one change is not allowed, nothing is changed."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "description": (
                    'The changes, in order: [{"tool": "rename_file", "args": '
                    '{"path": "a.pdf", "new_path": "2026-a.pdf"}}, ...]'
                ),
                "items": {"type": "object"},
            },
        },
        "required": ["changes"],
    }

    def __init__(self, tool_registry: Any) -> None:
        self._registry = tool_registry

    def redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        # The card and the ledger row hold one line per change, never the
        # content being written (a batch of forty workbooks is not a row).
        if not isinstance(args, dict):
            return args
        shown = {k: v for k, v in args.items() if k != "changes"}
        if isinstance(args.get("changes"), list):
            shown.update(shown_changes(args["changes"]))
        return shown

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> "SpendCheck | None":
        """ASKED ONCE, EVERY TIME: Allow once and Deny only, no grant of any
        width covers the next batch (it holds different changes). "Don't
        ask" drops it, as it drops Ask first's own guard."""
        return SpendCheck(ask=True, once_only=True, reason=BATCH_REASON, dont_ask_skips=True)

    async def _step_problem(
        self, at: int, change: dict[str, Any], scope: Any, ctx: ToolContext
    ) -> str:
        """Why change *at* stops the batch, or "". Fail-closed."""
        name = str(change.get("tool") or "")
        args = change.get("args") or {}
        try:
            tool = self._registry.get(name)
            if tool is None:
                return f"change {at} uses `{name}`, which does not exist"
            if getattr(tool, "quarantined", False):
                return f"change {at} uses `{name}`, which is held for the user's first look"
            if getattr(tool, "reversibility", None) != Reversibility.REVERSIBLE:
                return f"change {at} uses `{name}`, which cannot be undone"
            perms = getattr(scope, "perms", None)
            mode = getattr(
                perms.mode_for(tool.perm_key(), getattr(scope, "agent_overrides", None)),
                "value", "ask",
            )
            if mode == "deny":
                return f"change {at} uses `{name}`, which is set to never run here"
            check = await tool.spend_check(args, ctx)
            if check is not None and getattr(check, "ask", False):
                return (
                    f"change {at} ({describe_change(change)}) needs an approval "
                    f"of its own. Make that one change directly with `{name}`"
                )
            # Ask first's guard for Git's and Iron Jarvis's own files lives
            # OUTSIDE the tools (`tools/base.spend_checks_for`), so the step
            # tool's own check above never sees it: read the paths here.
            guarded = await asyncio.to_thread(
                guarded_target, change, getattr(ctx, "workspace", None)
            )
            if guarded:
                return (
                    f"change {at} touches a guarded file ({guarded}), so it "
                    f"needs an approval of its own. Make that one change "
                    f"directly with `{name}`"
                )
        except Exception as exc:  # noqa: BLE001 — cannot tell: do not run it
            log.debug("apply_changes check failed for %s", name, exc_info=True)
            return f"change {at} could not be checked ({type(exc).__name__})"
        return ""

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .registry import current_call_scope

        scope = current_call_scope()
        allowed = getattr(scope, "allowed_names", None)
        if scope is None or not allowed:
            return ToolResult(
                ok=False,
                error="NOTHING WAS CHANGED. A batch can only run inside a chat turn or a run.",
            )
        changes = args.get("changes")
        problem = plan_problem(changes, allowed, self._registry)
        if not problem:
            for at, change in enumerate(changes, start=1):
                problem = await self._step_problem(at, change, scope, ctx)
                if problem:
                    break
        if problem:
            return ToolResult(ok=False, error=f"NOTHING WAS CHANGED. {problem}.")

        # This one approval covers exactly the tools the listed changes use.
        grant = set(scope.session_allow or ())
        for change in changes:
            tool = self._registry.get(str(change.get("tool")))
            grant.add(tool.name)
            grant.add(tool.perm_key())

        done: list[str] = []
        failed: list[str] = []
        paths: list[str] = []
        streak = 0
        attempted = 0
        for change in changes:
            attempted += 1
            name = str(change.get("tool"))
            progress.touch()
            result = await self._registry.invoke(
                name,
                dict(change.get("args") or {}),
                ctx,
                scope.perms,
                agent_overrides=scope.agent_overrides,
                session_allow=grant,
                allowed_names=set(allowed),
                deadline_s=scope.deadline_s,
                grants=scope.grants,
                scopes=scope.scopes,
            )
            line = describe_change(change)
            if result.ok:
                streak = 0
                done.append(line)
                for path in self._paths_of(result):
                    if path not in paths:
                        paths.append(path)
            else:
                streak += 1
                failed.append(f"{line}: {_one_line(result.error or 'failed', 240)}")
                if streak >= STOP_AFTER_FAILURES:
                    break
        left = len(changes) - attempted
        total = len(changes)
        head = (
            f"Made all {total} changes."
            if not failed and not left
            else f"Made {len(done)} of {total} changes."
        )
        parts = [head]
        if done:
            shown = done[:20]
            parts.append("\n".join(f"- {line}" for line in shown))
            if len(done) > len(shown):
                parts.append(f"… and {len(done) - len(shown)} more.")
        if failed:
            parts.append(
                f"NOT DONE ({len(failed)}):\n" + "\n".join(f"- {line}" for line in failed[:20])
            )
        if left:
            parts.append(
                f"STOPPED after {STOP_AFTER_FAILURES} failures in a row: "
                f"{left} change{'s' if left != 1 else ''} not attempted."
            )
        if done:
            parts.append("Each change can be undone on its own.")
        output = "\n".join(parts)
        data = {
            "applied": len(done),
            "failed": len(failed),
            "not_attempted": left,
            "total": total,
        }
        if not done:
            return ToolResult(
                ok=False, output=output, error="NOTHING WAS CHANGED. " + output, data=data
            )
        return ToolResult(ok=True, output=output, data=data, created_paths=paths or None)

    @staticmethod
    def _paths_of(result: ToolResult) -> list[str]:
        """The absolute paths one change says it wrote."""
        out: list[str] = []
        data = result.data if isinstance(result.data, dict) else {}
        for key in ("abs_path", "abs_output"):
            value = data.get(key)
            if isinstance(value, str) and value:
                out.append(value)
        for value in result.created_paths or []:
            if isinstance(value, str) and value:
                out.append(value)
        return out
