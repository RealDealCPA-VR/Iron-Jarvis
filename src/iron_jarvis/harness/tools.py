"""The IJ harness's new tools (v1.364.0, Phase 1 of ``docs/IJ-HARNESS-PLAN.md``).

Each one is the Jarvis tool behind a capability Claude Code or Codex ships
(the mapping lives in :mod:`iron_jarvis.harness.parity`):

* ``multi_edit`` — several exact edits to ONE file, all or nothing, one undo
  entry (Claude Code's retired ``MultiEdit``; ``Edit`` per file today).
* ``apply_patch`` — Codex's V4A patch over many files, all or nothing, every
  path confined to the workspace, an undo entry per file it touched.
* ``glob`` — files by pattern, newest first, capped and reported (``Glob``).
* ``plan_update`` — the model's own step list (``TodoWrite`` / Codex
  ``update_plan``); writes nothing, the plan rides the result's ``data``.
* ``notebook_edit`` — replace / insert / delete one ``.ipynb`` cell
  (``NotebookEdit``), validated, undoable.
* ``mcp_list_resources`` / ``mcp_read_resource`` — the resources a connected
  app offers (``ListMcpResourcesTool`` / ``ReadMcpResourceTool``, Codex's
  ``list_mcp_resources`` / ``read_mcp_resource``).

Every file step runs off the event loop and is bounded; every write goes
through the undo journal (``capture_undo`` → ``UndoJournal``) and the ledger
(``registry.invoke``), and a write of a code file carries one syntax-check
line (:mod:`iron_jarvis.harness.verify`). None of them is on an agent roster
or auto-armed in chat: the IJ harness session arms them by name.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from ..tools.base import (
    Reversibility,
    RiskClass,
    Tool,
    ToolContext,
    ToolResult,
    safe_path,
    unwritable_workspace_error,
)
from ..tools.builtins import _bool_arg, _int_arg, unreadable_reason
from ..tools.undo import (
    INLINE_MAX_BYTES,
    PATCH_UNDO_KIND,
    RevertConflict,
    delete_preimage,
    make_file_descriptor,
    patch_entries,
    patch_entry_before,
    read_envelope,
    revert_workspace_file,
    save_preimage,
    sha256_bytes,
)
from .patch import Hunk, PatchError, apply_update, parse_patch
from .search import SearchError, glob_files, render_glob
from .textedit import (
    MAX_EDIT_BYTES,
    EditError,
    TextFile,
    TextFileError,
    apply_exact_edit,
    decode_text,
    encode_text,
    load_text,
)
from .verify import check_files, check_line, is_code_file, with_check

# PATCH_UNDO_KIND (the apply_patch journal kind) lives in tools/undo.py,
# shared with its readers (agents/outcome, the chat's changes view).

#: ``multi_edit`` takes at most this many edits in one call.
MAX_MULTI_EDITS = 100


def _list_arg(value: Any, name: str) -> list:
    """A list argument; a JSON string of one is accepted (models stringify)."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return parsed
    raise ValueError(f"`{name}` must be a list")


# =========================================================================== #
# multi_edit
# =========================================================================== #


def _multi_plan(path: Path, label: str, edits: list[dict[str, Any]]) -> tuple[TextFile, str, int]:
    """``(file, new text, replacements)`` — every edit applied in order to the
    text in memory, or :class:`EditError` naming the edit that failed.
    BLOCKING."""
    tf = load_text(path, label)
    text = tf.text
    total = 0
    for n, edit in enumerate(edits, 1):
        try:
            text, count = apply_exact_edit(
                text,
                str(edit.get("old", "")),
                str(edit.get("new", "")),
                replace_all=bool(edit.get("replace_all", False)),
                where=label,
            )
        except EditError as exc:
            raise EditError(
                f"edit {n} of {len(edits)}: {exc} Nothing was changed — no edit was applied."
            ) from None
        total += count
    return tf, text, total


def _parse_edits(args: dict[str, Any]) -> list[dict[str, Any]]:
    edits = _list_arg(args.get("edits"), "edits")
    if not edits:
        raise ValueError("`edits` is empty — give at least one {old, new}")
    if len(edits) > MAX_MULTI_EDITS:
        raise ValueError(f"`edits` has {len(edits)} entries — at most {MAX_MULTI_EDITS} per call")
    out: list[dict[str, Any]] = []
    for n, edit in enumerate(edits, 1):
        if not isinstance(edit, dict) or "old" not in edit or "new" not in edit:
            raise ValueError(f"edit {n} must be an object with `old` and `new`")
        replace_all = False
        if edit.get("replace_all") is not None:
            replace_all = _bool_arg(edit["replace_all"], f"edits[{n}].replace_all")
        out.append({"old": str(edit["old"]), "new": str(edit["new"]), "replace_all": replace_all})
    return out


class MultiEditTool(Tool):
    name = "multi_edit"
    description = (
        "Make several exact edits to ONE workspace text file in a single call. "
        "`edits` is an ordered list of {old, new, replace_all?}; each is applied "
        "to the result of the ones before it, with edit_file's rule (`old` must "
        "match exactly once unless replace_all). All or nothing: if any edit "
        "fails, the file is not changed and the reply names that edit. One undo."
    )
    reversibility = Reversibility.REVERSIBLE
    risk_class = RiskClass.PAGE_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["old", "new"],
                },
            },
        },
        "required": ["path", "edits"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        try:
            target = safe_path(ctx.workspace, args["path"])
            edits = _parse_edits(args)
        except Exception:  # noqa: BLE001 — a call that cannot run journals nothing
            return None

        def _capture() -> "dict[str, Any] | None":
            if not target.is_file():
                return None
            try:
                tf, new_text, _n = _multi_plan(target, str(args["path"]), edits)
            except (TextFileError, EditError):
                return None
            return make_file_descriptor(
                ctx.config.home,
                kind="file_restore",
                path=args["path"],
                mode="raw",
                prior_bytes=tf.raw,
                pre_sha256=sha256_bytes(tf.raw),
                post_sha256=sha256_bytes(tf.encode(new_text)),
            )

        return await asyncio.to_thread(_capture)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        label = str(args["path"])
        reason = await asyncio.to_thread(unreadable_reason, path, label)
        if reason is not None:
            return ToolResult(ok=False, error=reason)
        try:
            edits = _parse_edits(args)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))

        def _apply() -> int:
            tf, new_text, total = _multi_plan(path, label, edits)
            path.write_bytes(tf.encode(new_text))
            return total

        try:
            total = await asyncio.to_thread(_apply)
        except (TextFileError, EditError) as exc:
            return ToolResult(ok=False, error=str(exc))
        except PermissionError as exc:
            return ToolResult(ok=False, error=unwritable_workspace_error(exc, ctx.workspace))
        check = await check_line(path)
        data: dict[str, Any] = {
            "path": label,
            "abs_path": str(path),
            "edits": len(edits),
            "replacements": total,
        }
        if check:
            data["check"] = check
        n = len(edits)
        output = f"edited {label} ({n} edit{'s' if n != 1 else ''}, {total} replacement{'s' if total != 1 else ''})"
        return ToolResult(ok=True, output=with_check(output, check), data=data)


# =========================================================================== #
# apply_patch
# =========================================================================== #


@dataclass
class _FileState:
    """One path in the patch's in-memory view of the workspace."""

    target: Path
    rel: str  # workspace-relative, forward slashes
    before: bytes | None  # None = absent before the patch
    style: TextFile | None = None  # the file's encoding/newlines, when text
    text: str | None = None  # LF text now; None = absent now
    exists: bool = False

    def after_bytes(self) -> bytes | None:
        if not self.exists:
            return None
        if self.style is not None:
            return self.style.encode(self.text or "")
        return encode_text(self.text or "")


@dataclass
class PatchPlan:
    """What a patch does to the workspace, computed before any write."""

    states: dict[str, _FileState] = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)  # "A x", "M y", "D z"

    def changes(self) -> list[_FileState]:
        """The paths whose bytes really change (a no-op hunk is left out)."""
        return [s for s in self.states.values() if s.before != s.after_bytes()]


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _plan_patch(workspace: Path, hunks: list[Hunk]) -> PatchPlan:
    """Run every hunk against an in-memory view of the files. BLOCKING;
    :class:`PatchError` names the first hunk that does not apply — and
    nothing has been written."""
    root = workspace.resolve()
    plan = PatchPlan()

    def _state(raw: str, hunk: Hunk) -> _FileState:
        try:
            target = safe_path(workspace, raw)
        except PermissionError:
            raise PatchError(
                f"hunk {hunk.index} ({hunk.header()}): '{raw}' is outside the session's "
                "folder — a patch may only change files inside it"
            ) from None
        if target == root:
            raise PatchError(f"hunk {hunk.index} ({hunk.header()}): '{raw}' is the workspace folder itself")
        key = _key(target)
        if key in plan.states:
            return plan.states[key]
        try:
            rel = str(target.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = raw
        if target.is_dir():
            raise PatchError(f"hunk {hunk.index} ({hunk.header()}): '{raw}' is a folder, not a file")
        before: bytes | None = None
        if target.is_file():
            size = target.stat().st_size
            if size > MAX_EDIT_BYTES:
                raise PatchError(
                    f"hunk {hunk.index} ({hunk.header()}): '{raw}' is {size:,} bytes — over the "
                    f"{MAX_EDIT_BYTES:,}-byte limit for a patch"
                )
            try:
                before = target.read_bytes()
            except OSError as exc:
                raise PatchError(f"hunk {hunk.index} ({hunk.header()}): could not read '{raw}': {exc}") from None
        st = _FileState(target=target, rel=rel, before=before, exists=before is not None)
        plan.states[key] = st
        return st

    def _text_of(st: _FileState, hunk: Hunk) -> str:
        if st.text is not None:
            return st.text
        try:
            st.style = decode_text(st.before or b"", f"'{hunk.path}'")
        except TextFileError as exc:
            raise PatchError(f"hunk {hunk.index} ({hunk.header()}): {exc}") from None
        st.text = st.style.text
        return st.text

    for hunk in hunks:
        if hunk.kind == "add":
            st = _state(hunk.path, hunk)
            if st.exists and st.style is None and st.before is not None:
                # Overwriting a file keeps its encoding and line endings
                # (when it is text; a binary file is simply replaced).
                try:
                    st.style = decode_text(st.before, f"'{hunk.path}'")
                except TextFileError:
                    st.style = None
            st.text = hunk.contents
            st.exists = True
            plan.summary.append(f"A {hunk.path}")
        elif hunk.kind == "delete":
            st = _state(hunk.path, hunk)
            if not st.exists:
                raise PatchError(
                    f"hunk {hunk.index} ({hunk.header()}): '{hunk.path}' does not exist, so it cannot be deleted"
                )
            st.exists = False
            st.text = None
            plan.summary.append(f"D {hunk.path}")
        else:
            st = _state(hunk.path, hunk)
            if not st.exists:
                raise PatchError(
                    f"hunk {hunk.index} ({hunk.header()}): '{hunk.path}' does not exist — use "
                    "'*** Add File:' to create it"
                )
            new_text = apply_update(_text_of(st, hunk), hunk)
            if hunk.move_to:
                dest = _state(hunk.move_to, hunk)
                if dest is not st:
                    if dest.exists:
                        raise PatchError(
                            f"hunk {hunk.index} ({hunk.header()}): cannot move to '{hunk.move_to}' — "
                            "a file already exists there"
                        )
                    dest.style = st.style
                    dest.text = new_text
                    dest.exists = True
                    st.exists = False
                    st.text = None
                    plan.summary.append(f"M {hunk.move_to} (moved from {hunk.path})")
                    continue
            st.text = new_text
            plan.summary.append(f"M {hunk.path}")
    return plan


def _undo_envelope(home: Any, plan: PatchPlan) -> dict[str, Any]:
    """The ``files_patch`` journal descriptor: one entry per changed path
    with its before-image (inline when small, a blob ref otherwise) and the
    hash of what the patch leaves there (``None`` = absent)."""
    files: list[dict[str, Any]] = []
    created: list[str] = []
    changed: list[str] = []
    deleted: list[str] = []
    for st in plan.changes():
        after = st.after_bytes()
        entry: dict[str, Any] = {
            "path": st.rel,
            "existed": st.before is not None,
            "data": None,
            "ref": None,
            "post_sha256": sha256_bytes(after) if after is not None else None,
        }
        if st.before is not None:
            if len(st.before) <= INLINE_MAX_BYTES:
                entry["data"] = base64.b64encode(st.before).decode("ascii")
            else:
                entry["ref"] = save_preimage(home, new_id("undo"), st.before)
        files.append(entry)
        if st.before is None:
            created.append(st.rel)
        elif after is None:
            deleted.append(st.rel)
        else:
            changed.append(st.rel)
    meta = {
        "mode": "raw",
        "files": files,
        "created": created,
        "changed": changed,
        "deleted": deleted,
    }
    return {
        "kind": PATCH_UNDO_KIND,
        "reversible": True,
        "pre_ref": None,
        "pre_inline": json.dumps(meta),
        "pre_sha256": None,
        "post_sha256": None,
    }


def _write_plan(plan: PatchPlan) -> None:
    """Write every changed path: contents first, deletions last. A failure
    part way puts back what was already written, then re-raises.

    The file IN FLIGHT is put back too (v1.364.0 review): ``write_bytes``
    truncates when it opens, so a write that fails part way (a full disk, a
    scanner grabbing the file) left that file cut short while the reply said
    "what it had written was put back". It joins ``done`` BEFORE its write;
    putting back a file whose write never started rewrites the same bytes."""
    done: list[_FileState] = []
    changes = plan.changes()
    try:
        for st in [s for s in changes if s.exists]:
            st.target.parent.mkdir(parents=True, exist_ok=True)
            done.append(st)
            st.target.write_bytes(st.after_bytes() or b"")
        for st in [s for s in changes if not s.exists]:
            done.append(st)
            st.target.unlink()
    except BaseException:
        for st in reversed(done):
            try:
                if st.before is None:
                    st.target.unlink(missing_ok=True)
                else:
                    st.target.write_bytes(st.before)
            except OSError:
                pass
        raise


class ApplyPatchTool(Tool):
    """Codex's ``apply_patch`` (V4A format), all or nothing (v1.364.0)."""

    name = "apply_patch"
    description = (
        "Change files with a patch in Codex's apply_patch format. The patch "
        "starts with '*** Begin Patch' and ends with '*** End Patch'; between "
        "them, one hunk per file: '*** Add File: <path>' followed by '+' lines; "
        "'*** Delete File: <path>'; or '*** Update File: <path>' (optionally "
        "'*** Move to: <new path>') followed by chunks: an optional '@@ <a line "
        "near the change>' header, then lines starting with ' ' (unchanged "
        "context, 3 lines around each change), '-' (removed) or '+' (added). "
        "Paths are relative to the workspace. ALL OR NOTHING: every hunk is "
        "checked against the files first, and if any does not fit nothing is "
        "written and the reply names that hunk. Reversible."
    )
    reversibility = Reversibility.REVERSIBLE
    risk_class = RiskClass.PAGE_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "patch": {
                "type": "string",
                "description": "The whole patch, from '*** Begin Patch' to '*** End Patch'.",
            },
        },
        "required": ["patch"],
    }

    @staticmethod
    def _patch_text(args: dict[str, Any]) -> str:
        # Codex's freeform tool sends the patch as its input; a JSON caller
        # may name it `input` or `patch`.
        for key in ("patch", "input"):
            if isinstance(args.get(key), str):
                return args[key]
        return ""

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        def _capture() -> "dict[str, Any] | None":
            try:
                plan = _plan_patch(ctx.workspace, parse_patch(self._patch_text(args)))
            except (PatchError, OSError):
                return None
            if not plan.changes():
                return None
            return _undo_envelope(ctx.config.home, plan)

        try:
            return await asyncio.to_thread(_capture)
        except Exception:  # noqa: BLE001 — capture never blocks the tool
            return None

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if undo.get("kind") != PATCH_UNDO_KIND:
            return await revert_workspace_file(undo, ctx)
        home = ctx.config.home

        def _revert() -> ToolResult:
            files = patch_entries(read_envelope(undo))
            if not files:
                return ToolResult(ok=False, error="undo: no files recorded for this patch")
            targets: list[tuple[dict[str, Any], Path, bytes | None]] = []
            # 1) EVERY file must still be as the patch left it — checked for
            #    all of them before any is touched (all or nothing, like the
            #    patch itself).
            for entry in files:
                rel = str(entry.get("path") or "")
                try:
                    target = safe_path(ctx.workspace, rel)
                except Exception as exc:  # noqa: BLE001 — never write outside it
                    return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")
                want = entry.get("post_sha256")
                try:
                    now = sha256_bytes(target.read_bytes()) if target.is_file() else None
                except OSError:
                    now = None
                if now != want:
                    raise RevertConflict(
                        f"{rel} changed since the patch — refusing to undo it "
                        "(it would overwrite a newer change); nothing was undone"
                    )
                # Every before-image is loaded BEFORE any file is touched
                # (v1.364.0 review): a missing blob used to raise half way,
                # after earlier files were already put back.
                try:
                    before = patch_entry_before(home, entry)
                except (OSError, ValueError) as exc:
                    return ToolResult(
                        ok=False,
                        error=(
                            f"undo: the saved copy of {rel} could not be read ({exc}); "
                            "nothing was undone"
                        ),
                    )
                if before is None and entry.get("existed"):
                    return ToolResult(
                        ok=False,
                        error=f"undo: no saved copy of {rel} was kept; nothing was undone",
                    )
                targets.append((entry, target, before))
            # 2) Put every file back.
            restored = 0
            for entry, target, before in targets:
                try:
                    if before is None:
                        if target.exists():
                            target.unlink()
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(before)
                    restored += 1
                except OSError as exc:
                    return ToolResult(
                        ok=False,
                        error=f"undo: could not restore {entry.get('path')}: {exc} ({restored} restored before it)",
                    )
            for entry, _t, _b in targets:
                delete_preimage(home, entry.get("ref"))
            return ToolResult(ok=True, output=f"undo: put back {restored} file(s) the patch changed")

        return await asyncio.to_thread(_revert)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = self._patch_text(args)

        def _run() -> PatchPlan:
            plan = _plan_patch(ctx.workspace, parse_patch(text))
            _write_plan(plan)
            return plan

        try:
            plan = await asyncio.to_thread(_run)
        except PatchError as exc:
            return ToolResult(
                ok=False,
                error=f"apply_patch verification failed — nothing was changed. {exc}",
            )
        except PermissionError as exc:
            return ToolResult(ok=False, error=unwritable_workspace_error(exc, ctx.workspace))
        except OSError as exc:
            return ToolResult(
                ok=False,
                error=f"apply_patch could not write the files ({exc}); what it had written was put back",
            )
        changes = plan.changes()
        written = [s for s in changes if s.exists]
        check = await asyncio.to_thread(
            check_files, [(s.rel, s.target) for s in written if is_code_file(s.target)]
        ) if any(is_code_file(s.target) for s in written) else None
        output = "Success. Updated the following files:\n" + "\n".join(plan.summary)
        if not changes:
            output += "\n(the patch left every file as it was)"
        data: dict[str, Any] = {
            "files": [s.rel for s in changes],
            "created": [s.rel for s in changes if s.before is None],
            "changed": [s.rel for s in changes if s.before is not None and s.exists],
            "deleted": [s.rel for s in changes if not s.exists],
            "abs_paths": [str(s.target) for s in written],
        }
        if check:
            data["check"] = check
        created = [str(s.target) for s in changes if s.before is None]
        return ToolResult(
            ok=True,
            output=with_check(output, check),
            data=data,
            created_paths=created or None,
        )


# =========================================================================== #
# glob
# =========================================================================== #


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files by name pattern under a workspace folder (default: the "
        "workspace). `**` matches any number of folders ('**/*.py', "
        "'src/**/*.ts'); '*' stays inside one folder ('*.md' = only that "
        "folder). Newest first, at most 100 (the reply says how many more). "
        "Skips .git, node_modules, hidden folders and build output."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Folder to search in, relative to the workspace."},
        },
        "required": ["pattern"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        base = safe_path(ctx.workspace, args.get("path") or ".")

        def _run():
            if not base.is_dir():
                return None
            return glob_files(base, ctx.workspace.resolve(), str(args.get("pattern", "")))

        try:
            report = await asyncio.to_thread(_run)
        except SearchError as exc:
            return ToolResult(ok=False, error=str(exc))
        if report is None:
            return ToolResult(ok=False, error=f"no such folder: {args.get('path')}")
        return ToolResult(
            ok=True,
            output=render_glob(report),
            data={
                "count": report.total,
                "shown": len(report.paths),
                "truncated": bool(report.walk_truncated) or report.total > len(report.paths),
            },
        )


# =========================================================================== #
# plan_update
# =========================================================================== #

PLAN_STATUSES = ("pending", "in_progress", "completed")
MAX_PLAN_STEPS = 50
MAX_STEP_CHARS = 500
_MARK = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}


def parse_plan(args: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """``(steps, explanation)`` checked, or ValueError in words."""
    raw = args.get("plan")
    if raw is None:
        raw = args.get("todos")  # Claude Code's TodoWrite spelling
    steps_in = _list_arg(raw, "plan")
    if not steps_in:
        raise ValueError("`plan` is empty — send the whole list of steps")
    if len(steps_in) > MAX_PLAN_STEPS:
        raise ValueError(f"`plan` has {len(steps_in)} steps — at most {MAX_PLAN_STEPS}")
    steps: list[dict[str, str]] = []
    for n, item in enumerate(steps_in, 1):
        if not isinstance(item, dict):
            raise ValueError(f"step {n} must be an object with `step` and `status`")
        text = item.get("step", item.get("content"))
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"step {n} has no `step` text")
        status = str(item.get("status", "")).strip().lower()
        if status not in PLAN_STATUSES:
            raise ValueError(
                f"step {n} has status {item.get('status')!r} — use pending, in_progress or completed"
            )
        flat = " ".join(text.split())
        if len(flat) > MAX_STEP_CHARS:
            flat = flat[: MAX_STEP_CHARS - 1] + "…"
        steps.append({"step": flat, "status": status})
    working = sum(1 for s in steps if s["status"] == "in_progress")
    done = sum(1 for s in steps if s["status"] == "completed")
    if done < len(steps) and working != 1:
        raise ValueError(
            f"exactly one step must be in_progress while the plan is not finished "
            f"(this one has {working}) — mark the step you are working on in_progress"
        )
    if done == len(steps) and working:
        raise ValueError("every step is completed, so none can be in_progress")
    explanation = args.get("explanation")
    explanation = " ".join(str(explanation).split())[:1000] if isinstance(explanation, str) else ""
    return steps, explanation


class PlanUpdateTool(Tool):
    name = "plan_update"
    description = (
        "Keep your step list for this task. Send the WHOLE list every time: "
        "`plan` = [{step, status}] with status pending, in_progress or "
        "completed — exactly one in_progress until every step is completed — "
        "and an optional `explanation` of what changed. Writes nothing."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string"},
                        "status": {"type": "string", "enum": list(PLAN_STATUSES)},
                    },
                    "required": ["step", "status"],
                },
            },
            "explanation": {"type": "string"},
        },
        "required": ["plan"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            steps, explanation = parse_plan(args)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        done = sum(1 for s in steps if s["status"] == "completed")
        lines = [f"Plan updated ({done} of {len(steps)} done):"]
        lines += [f"{_MARK[s['status']]} {s['step']}" for s in steps]
        if explanation:
            lines.append(f"Why: {explanation}")
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"plan": steps, "explanation": explanation, "done": done, "total": len(steps)},
        )


# =========================================================================== #
# notebook_edit
# =========================================================================== #

NOTEBOOK_MODES = ("replace", "insert", "delete")


def _source_lines(text: str) -> list[str]:
    """nbformat stores a cell's source as lines that keep their newline."""
    return text.splitlines(keepends=True)


def _indent_of(text: str) -> int:
    """The JSON indent a notebook file was written with (Jupyter's is 1)."""
    for line in text.split("\n")[1:20]:
        stripped = line.lstrip(" ")
        if stripped.startswith('"'):
            n = len(line) - len(stripped)
            return n if 0 < n <= 8 else 1
    return 1


def _notebook_plan(path: Path, label: str, args: dict[str, Any]) -> tuple[TextFile, str, str]:
    """``(file, new text, what was done)`` or ValueError in words. BLOCKING."""
    tf = load_text(path, label)
    try:
        nb = json.loads(tf.text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not a valid notebook (JSON error at line {exc.lineno}: {exc.msg})") from None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise ValueError(f"{label} is not a Jupyter notebook (no `cells` list)")
    cells: list = nb["cells"]
    mode = str(args.get("edit_mode") or "replace").strip().lower()
    if mode not in NOTEBOOK_MODES:
        raise ValueError(f"edit_mode must be replace, insert or delete, got {mode!r}")
    cell_type = args.get("cell_type")
    if cell_type is not None:
        cell_type = str(cell_type).strip().lower()
        if cell_type not in ("code", "markdown", "raw"):
            raise ValueError(f"cell_type must be code, markdown or raw, got {cell_type!r}")
    index: int | None = None
    cell_id = args.get("cell_id")
    if isinstance(cell_id, str) and cell_id.strip():
        found = [i for i, c in enumerate(cells) if isinstance(c, dict) and c.get("id") == cell_id.strip()]
        if not found:
            raise ValueError(f"no cell with id {cell_id!r} in {label}")
        index = found[0]
        if mode == "insert":
            index += 1  # insert AFTER the named cell
    elif args.get("cell_index") is not None:
        index = _int_arg(args["cell_index"], "cell_index", minimum=0)
    else:
        raise ValueError("give `cell_index` (0-based) or `cell_id`")
    limit = len(cells) if mode == "insert" else len(cells) - 1
    if index > limit:
        raise ValueError(
            f"cell_index {index} is out of range — {label} has {len(cells)} cell(s)"
            + (" (insert takes 0 to the count)" if mode == "insert" else "")
        )
    new_source = args.get("new_source")
    if mode != "delete" and not isinstance(new_source, str):
        raise ValueError("`new_source` is required to replace or insert a cell")

    def _blank(kind: str) -> dict[str, Any]:
        cell: dict[str, Any] = {"cell_type": kind, "metadata": {}, "source": []}
        major = nb.get("nbformat") if isinstance(nb.get("nbformat"), int) else 4
        minor = nb.get("nbformat_minor") if isinstance(nb.get("nbformat_minor"), int) else 0
        if major >= 4 and (minor >= 5 or any(isinstance(c, dict) and "id" in c for c in cells)):
            cell["id"] = uuid.uuid4().hex[:8]
        if kind == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        return cell

    if mode == "delete":
        del cells[index]
        done = f"deleted cell {index}"
    elif mode == "insert":
        cell = _blank(cell_type or "code")
        cell["source"] = _source_lines(new_source)
        cells.insert(index, cell)
        done = f"inserted a {cell['cell_type']} cell at {index}"
    else:
        cell = cells[index]
        if not isinstance(cell, dict):
            raise ValueError(f"cell {index} of {label} is not a cell object")
        if cell_type and cell_type != cell.get("cell_type"):
            cell["cell_type"] = cell_type
            if cell_type == "code":
                cell.setdefault("execution_count", None)
                cell.setdefault("outputs", [])
            else:
                cell.pop("execution_count", None)
                cell.pop("outputs", None)
        cell["source"] = _source_lines(new_source)
        done = f"replaced cell {index}"
        if cell.get("cell_type") == "code" and (cell.get("outputs") or cell.get("execution_count") is not None):
            # Old outputs describe the old code: cleared, and said so.
            cell["outputs"] = []
            cell["execution_count"] = None
            done += " (its old outputs were cleared)"
    new_text = json.dumps(nb, indent=_indent_of(tf.text), ensure_ascii=False) + "\n"
    json.loads(new_text)  # the file written is always valid JSON
    return tf, new_text, done


class NotebookEditTool(Tool):
    name = "notebook_edit"
    description = (
        "Edit one cell of a Jupyter notebook (.ipynb) in the workspace: "
        "edit_mode replace (default), insert or delete; pick the cell by "
        "`cell_index` (0-based) or `cell_id` (insert with a cell_id goes AFTER "
        "that cell); `new_source` is the cell's whole new text; `cell_type` "
        "code / markdown (required to insert, optional to change a cell's "
        "type). Replacing a code cell clears its old outputs. Reversible."
    )
    reversibility = Reversibility.REVERSIBLE
    risk_class = RiskClass.PAGE_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "edit_mode": {"type": "string", "enum": list(NOTEBOOK_MODES)},
            "cell_index": {"type": "integer"},
            "cell_id": {"type": "string"},
            "new_source": {"type": "string"},
            "cell_type": {"type": "string", "enum": ["code", "markdown", "raw"]},
        },
        "required": ["path"],
    }

    @staticmethod
    def _path_arg(args: dict[str, Any]) -> str:
        return str(args.get("path") or args.get("notebook_path") or "")

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        label = self._path_arg(args)
        try:
            target = safe_path(ctx.workspace, label)
        except Exception:  # noqa: BLE001
            return None

        def _capture() -> "dict[str, Any] | None":
            if not target.is_file() or target.suffix.lower() != ".ipynb":
                return None
            try:
                tf, new_text, _done = _notebook_plan(target, label, args)
            except (TextFileError, ValueError):
                return None
            return make_file_descriptor(
                ctx.config.home,
                kind="file_restore",
                path=label,
                mode="raw",
                prior_bytes=tf.raw,
                pre_sha256=sha256_bytes(tf.raw),
                post_sha256=sha256_bytes(tf.encode(new_text)),
            )

        return await asyncio.to_thread(_capture)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        label = self._path_arg(args)
        if not label:
            return ToolResult(ok=False, error="`path` is required (the .ipynb file)")
        path = safe_path(ctx.workspace, label)
        reason = await asyncio.to_thread(unreadable_reason, path, label)
        if reason is not None:
            return ToolResult(ok=False, error=reason)
        if path.suffix.lower() != ".ipynb":
            return ToolResult(ok=False, error=f"{label} is not a .ipynb notebook — use edit_file for other files")

        def _apply() -> str:
            tf, new_text, done = _notebook_plan(path, label, args)
            path.write_bytes(tf.encode(new_text))
            return done

        try:
            done = await asyncio.to_thread(_apply)
        except (TextFileError, ValueError) as exc:
            return ToolResult(ok=False, error=str(exc))
        except PermissionError as exc:
            return ToolResult(ok=False, error=unwritable_workspace_error(exc, ctx.workspace))
        check = await check_line(path)
        data: dict[str, Any] = {"path": label, "abs_path": str(path), "done": done}
        if check:
            data["check"] = check
        return ToolResult(ok=True, output=with_check(f"{done} in {label}", check), data=data)


# =========================================================================== #
# MCP resources
# =========================================================================== #

#: One pack's list request, and one read, are bounded by these.
RESOURCE_LIST_TIMEOUT_S = 5.0
RESOURCE_READ_TIMEOUT_S = 15.0
#: Rows listed, and characters of one resource returned, at most.
MAX_RESOURCE_ROWS = 200
RESOURCE_CHARS = 20_000


def _live_clients() -> dict[str, Any]:
    from ..mcp import tools as _mcp_tools

    try:
        return dict(_mcp_tools.live_clients() or {})
    except Exception:  # noqa: BLE001 — no packs is an answer, never a crash
        return {}


def _resource_text(result: Any) -> str:
    parts: list[str] = []
    contents = result.get("contents") if isinstance(result, dict) else None
    for item in contents or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
        elif "blob" in item:
            mime = str(item.get("mimeType") or "application/octet-stream")
            parts.append(f"[binary {mime} content omitted]")
    return "\n\n".join(parts)


class McpListResourcesTool(Tool):
    name = "mcp_list_resources"
    description = (
        "List the resources (files, records, documents) your connected apps "
        "offer, each as `app: uri — name`. Optional `server` = one app's name. "
        "Read one with mcp_read_resource."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {"server": {"type": "string", "description": "Only this connected app."}},
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        clients = _live_clients()
        want = str(args.get("server") or "").strip()
        if want:
            if want not in clients:
                names = ", ".join(sorted(clients)) or "none"
                return ToolResult(ok=False, error=f"no connected app named {want!r} is running (running: {names})")
            clients = {want: clients[want]}
        if not clients:
            return ToolResult(ok=True, output="No connected apps are running.", data={"count": 0})

        async def _one(name: str, client: Any):
            try:
                rows = await asyncio.wait_for(client.list_resources(), RESOURCE_LIST_TIMEOUT_S)
                return name, rows, ""
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, TimeoutError):
                return name, None, f"{name} took too long to answer"
            except Exception as exc:  # noqa: BLE001 — one bad app is named, never fatal
                text = str(exc)
                if "-32601" in text or "method not found" in text.lower():
                    return name, [], ""
                return name, None, f"{name} could not be reached"

        results = await asyncio.gather(*(_one(n, clients[n]) for n in sorted(clients)))
        lines: list[str] = []
        failed: list[str] = []
        total = 0
        for name, rows, err in results:
            if rows is None:
                failed.append(err)
                continue
            for r in rows:
                if not isinstance(r, dict) or not isinstance(r.get("uri"), str):
                    continue
                total += 1
                if len(lines) >= MAX_RESOURCE_ROWS:
                    continue
                label = str(r.get("title") or r.get("name") or "")[:200]
                mime = str(r.get("mimeType") or "")[:100]
                line = f"{name}: {r['uri'][:2000]}"
                if label:
                    line += f" — {label}"
                if mime:
                    line += f" ({mime})"
                lines.append(line)
        out = "\n".join(lines) if lines else "The running apps offer no resources."
        if total > len(lines):
            out += f"\n\n[{total - len(lines):,} more not shown — pass `server` to list one app]"
        if failed:
            out += "\n\n[not listed: " + "; ".join(failed) + "]"
        return ToolResult(ok=True, output=out, data={"count": total, "shown": len(lines), "failed": len(failed)})


class McpReadResourceTool(Tool):
    name = "mcp_read_resource"
    description = (
        "Read one resource a connected app offers: `server` (the app's name) "
        "and `uri` (from mcp_list_resources). The text comes back as data from "
        "that app, cut to 20,000 characters."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    #: An app's resource is third-party content: fenced and scanned.
    returns_untrusted_content = True
    input_schema = {
        "type": "object",
        "properties": {"server": {"type": "string"}, "uri": {"type": "string"}},
        "required": ["server", "uri"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args.get("server") or "").strip()
        uri = str(args.get("uri") or "").strip()
        if not uri:
            return ToolResult(ok=False, error="`uri` is required")
        clients = _live_clients()
        client = clients.get(name)
        if client is None or not callable(getattr(client, "read_resource", None)):
            names = ", ".join(sorted(clients)) or "none"
            return ToolResult(ok=False, error=f"no connected app named {name!r} is running (running: {names})")
        try:
            result = await asyncio.wait_for(client.read_resource(uri), RESOURCE_READ_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            return ToolResult(ok=False, error=f"{name} took too long to send {uri}")
        except Exception as exc:  # noqa: BLE001 — named, never a traceback
            return ToolResult(ok=False, error=f"{name} could not send {uri}: {type(exc).__name__}")
        text = _resource_text(result)
        cut = len(text) > RESOURCE_CHARS
        if cut:
            text = text[:RESOURCE_CHARS] + (
                f"\n\n[cut: the first {RESOURCE_CHARS:,} of {len(text):,} characters were shown]"
            )
        return ToolResult(
            ok=True,
            output=text or "(the resource is empty)",
            data={"server": name, "uri": uri, "chars": len(text), "truncated": cut},
        )


def harness_tools() -> list[Tool]:
    """Every Phase 1 tool this module defines (the process tools live in
    :mod:`iron_jarvis.harness.process`)."""
    return [
        MultiEditTool(),
        ApplyPatchTool(),
        GlobTool(),
        PlanUpdateTool(),
        NotebookEditTool(),
        McpListResourcesTool(),
        McpReadResourceTool(),
    ]
