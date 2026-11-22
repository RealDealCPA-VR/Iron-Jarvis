"""The two tools of "Sort this stack" (idea 31, phase 31-a).

* ``stack_plan`` (allow, READONLY; its result carries page text, so it is
  fenced as data): reads every page of a scanned PDF ON THIS PC, plans where
  each document starts and ends with ONE model call, runs the safety step
  (``documents/stack``) and keeps the plan. Its result carries the plan CARD
  (``data["stack_plan"]``), which both chat lanes put under the reply through
  ``chat_turn.config_cards_from_result``. It writes no file of the user's.
* ``stack_apply`` (ask, REVERSIBLE): writes the plan, as the user left it on
  the card, into folders inside the chat's folder. The card's Confirm runs it
  through the registry (``POST /documents/stack/{id}/apply``: the press is the
  answer to its ask). It refuses a plan made in the same chat turn (the user
  has not seen it yet). Its Undo is ONE ``files_delete`` envelope over every
  file it wrote, each checked against the hash it had when written, and it
  removes the folders it made when they are empty again. The scan itself is
  never touched (D-31.1).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult, safe_path
from ..tools.undo import read_envelope, revert_workspace_file, sha256_target
from . import stack as S
from .tools import _resolve_read_path


def _turn_of(ctx: ToolContext) -> str:
    turn = getattr(ctx, "source_turn", None)
    return str(getattr(turn, "turn_id", "") or getattr(ctx, "turn_id", "") or "")


class StackPlanTool(Tool):
    name = "stack_plan"
    reversibility = Reversibility.READONLY  # keeps a plan in Iron Jarvis's home; no file of the user's
    risk_class = RiskClass.READ
    returns_untrusted_content = True  # the names come from the pages' own words
    description = (
        "Plan how to sort ONE scanned PDF that holds several documents (a pile of "
        "statements, bills and letters scanned together) into separate named "
        "documents: where each starts and ends, a name like '2026-03 Bank "
        "statement.pdf', and a folder. Every page is read on this PC first. The "
        "plan is shown to the user on a card where they can move pages, rename, "
        "pick folders and confirm; NOTHING is written by this tool, and the scan "
        "is never changed. Use it when the user asks to sort, split or separate a "
        "scan into its documents."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The scanned PDF (absolute, or in the chat's folder)."},
        },
        "required": ["path"],
    }

    def __init__(self, router_resolver: "Any | None" = None) -> None:
        self._router_resolver = router_resolver

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args.get("path") or "").strip()
        if not raw:
            return ToolResult(ok=False, error="Say which PDF to sort.")

        def _gate() -> "tuple[Path, tuple[bool, str], bool]":
            p = _resolve_read_path(raw, ctx)
            return p, fs_read_ok(p), p.is_file()

        path, (allowed, reason), exists = await asyncio.to_thread(_gate)
        if not allowed:
            return ToolResult(ok=False, error=reason)
        if not exists:
            return ToolResult(ok=False, error=f"There is no file at {path}.")
        if path.suffix.lower() != ".pdf":
            return ToolResult(ok=False, error=f"{path.name} is not a PDF. Sorting a scan works on a PDF.")
        router = self._router_resolver() if callable(self._router_resolver) else None
        if router is None:
            return ToolResult(ok=False, error="No model is set up to plan the documents.")
        try:
            pages = await asyncio.to_thread(S.read_pages, path, ctx.config)
            sha = await asyncio.to_thread(S.file_sha256, path)
            readable = [pg for pg in pages if pg.how in (S.HOW_TEXT, S.HOW_THIS_PC)]
            if readable:
                docs = await S.plan_pages_async(pages, source_name=path.name, router=router, config=ctx.config)
            else:
                # Nothing this PC could read: no model is asked about pages it
                # was never shown. Every page waits in "Not sure", and the card
                # offers to read them.
                docs = S.safe_plan([], pages)
        except S.StackError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — a scan that breaks the reader is said, never a crash
            return ToolResult(ok=False, error=f"{path.name} could not be read: {type(exc).__name__}: {exc}")
        plan = S.new_plan(
            source=path.resolve(), sha256=sha, pages=pages, documents=docs,
            workspace=Path(ctx.workspace).resolve(), thread_id=getattr(ctx, "thread_id", ""),
            turn_id=_turn_of(ctx),
        )
        await asyncio.to_thread(S.save_plan, ctx.config.home, plan)
        unread = [pg.number for pg in pages if pg.how == S.HOW_UNREAD]
        offer = S.vision_choice(router, ctx.config) if unread else None
        card = S.card_of(plan, offer=offer)
        here = card["read_here"]
        lines = [
            f"Plan made for {path.name}: {len(docs)} document(s) from {len(pages)} page(s)"
            + (f"; {here} page(s) read on this PC by Windows' OCR" if here else "")
            + (f"; {len(unread)} page(s) could not be read on this PC" if unread else "")
            + ".",
            "The plan is shown to the user on a card under your reply. There they can move a page to "
            "another document, rename a document, pick its folder, and press Confirm. Nothing is written "
            "until they press Confirm, and the scan itself is never changed. Do not write the documents "
            "yourself; tell the user the plan is on the card.",
            S.outline_words(plan),
        ]
        if unread:
            lines.append(
                (f"The card offers to read the {len(unread)} unread page(s) with {offer['name']}; nothing has "
                 "been sent to it.") if offer else
                "No connected model can read pictures, so those pages stay in \"Not sure\" unless the user "
                "moves them."
            )
        return ToolResult(ok=True, output="\n".join(lines), data={"stack_plan": card, "plan_id": plan["id"]})


class StackApplyTool(Tool):
    name = "stack_apply"
    reversibility = Reversibility.REVERSIBLE
    risk_class = RiskClass.PAGE_ACTION
    description = (
        "Write a sorting plan made by stack_plan: each document becomes its own PDF in "
        "its folder inside the chat's folder. The scan is never changed. Only after the "
        "user confirmed the plan on its card; one Undo removes every file it wrote."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "The plan's id, from stack_plan."},
            "documents": {
                "type": "array",
                "description": "The documents as the user left them (optional; the plan's own when left out).",
                "items": {
                    "type": "object",
                    "properties": {
                        "pages": {"type": "array", "items": {"type": "integer"}},
                        "name": {"type": "string"},
                        "folder": {"type": "string"},
                    },
                    "required": ["pages"],
                },
            },
        },
        "required": ["plan_id"],
    }

    def __init__(self) -> None:
        # capture_undo → execute hand-off (the registry calls both with the
        # SAME args object, as pdf_split's does): execute records the files it
        # really wrote into the descriptor the registry journals after it.
        self._pending: dict[int, dict[str, Any]] = {}

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> "dict[str, Any] | None":
        desc = {
            "kind": "files_delete",
            "reversible": True,
            "pre_ref": None,
            "pre_inline": json.dumps({
                "paths": [], "mode": "raw", "sha256": {}, "dirs": [],
                "plan_id": str(args.get("plan_id") or ""),
            }),
            "pre_sha256": None,
            "post_sha256": None,
        }
        self._pending[id(args)] = desc
        return desc

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        desc = self._pending.pop(id(args), None)
        plan_id = str(args.get("plan_id") or "").strip()
        try:
            plan = await asyncio.to_thread(S.load_plan, ctx.config.home, plan_id)
        except S.StackError as exc:
            return ToolResult(ok=False, error=str(exc))
        made_in = str(plan.get("turn_id") or "")
        if made_in and made_in == _turn_of(ctx):
            return ToolResult(ok=False, error=(
                "The plan was made in this reply, so the user has not seen it yet. It is on a card "
                "under the reply, where they confirm it. Nothing was written."
            ))
        ws = Path(ctx.workspace).resolve()
        if Path(str(plan.get("workspace") or "")).resolve() != ws:
            return ToolResult(ok=False, error=(
                f"This plan belongs to another folder ({plan.get('workspace')}). Nothing was written."
            ))
        if plan.get("applied"):
            return ToolResult(ok=False, error=(
                "This plan was already written. Undo on its card takes those documents back first."
            ))
        try:
            docs = S.edited_documents(plan, args.get("documents"))
            written = await asyncio.to_thread(S.write_plan, plan, docs, ws)
        except S.StackError as exc:
            return ToolResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — every file written was removed by write_plan
            return ToolResult(ok=False, error=f"The documents could not be written ({type(exc).__name__}: {exc}). "
                                              "Nothing was kept.")

        def _record() -> None:
            rels = [Path(f["path"]).relative_to(ws).as_posix() for f in written.files]
            meta = read_envelope(desc) if desc else {}
            meta["paths"] = rels
            meta["mode"] = "raw"
            meta["sha256"] = {r: sha256_target(ws / r, "raw") for r in rels}
            meta["dirs"] = [Path(d).resolve().relative_to(ws).as_posix() for d in written.dirs]
            meta["plan_id"] = plan_id
            if desc is not None:
                desc["pre_inline"] = json.dumps(meta)
            plan["documents"] = docs
            plan["applied"] = {"files": [f["path"] for f in written.files]}
            S.save_plan(ctx.config.home, plan)
            S.keep_facts(ctx.engine, written, plan, thread_id=getattr(ctx, "thread_id", "") or plan.get("thread_id", ""),
                         turn_id=_turn_of(ctx))

        await asyncio.to_thread(_record)
        total = int(plan.get("page_count") or 0)
        lines = [
            f"Wrote {len(written.files)} document(s) from {plan.get('source_name')} ({total} page(s); each "
            "file re-opened and its pages counted, and they add up to the scan's; the scan is unchanged at "
            f"{plan.get('source')}):"
        ] + [f"- {f['path']} ({S.span_words(f['source_pages'])} of the scan, {f['pages']} page(s))"
             for f in written.files]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"stack_applied": {"plan_id": plan_id, "source": plan.get("source"),
                                    "files": [{k: f[k] for k in ("path", "pages", "name", "folder")}
                                              for f in written.files]}},
            created_paths=[f["path"] for f in written.files],
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Remove every file the plan wrote (each only while it still has the
        bytes it was written with: one changed since refuses the whole Undo),
        then the folders it made, when they are empty. The scan stays."""
        result = await revert_workspace_file(undo, ctx)
        if not result.ok:
            return result
        meta = read_envelope(undo)

        def _tidy() -> int:
            removed = 0
            dirs = [str(d) for d in meta.get("dirs") or [] if isinstance(d, str)]
            for rel in sorted(dirs, key=len, reverse=True):
                try:
                    target = safe_path(Path(ctx.workspace), rel)
                    if target.is_dir() and not any(target.iterdir()):
                        target.rmdir()
                        removed += 1
                except Exception:  # noqa: BLE001 — a folder that stays is said by the count
                    continue
            try:
                from .source_store import drop_kept_paths

                ws = Path(ctx.workspace).resolve()
                drop_kept_paths(ctx.engine, [str((ws / str(p)).resolve()) for p in meta.get("paths") or []])
            except Exception:  # noqa: BLE001
                pass
            plan_id = str(meta.get("plan_id") or "")
            if S.valid_plan_id(plan_id):
                try:
                    plan = S.load_plan(ctx.config.home, plan_id)
                    plan["applied"] = None
                    S.save_plan(ctx.config.home, plan)
                except Exception:  # noqa: BLE001 — the plan may be gone; the files are what matter
                    pass
            return removed

        folders = await asyncio.to_thread(_tidy)
        note = result.output + (f"; removed {folders} folder(s) it had made" if folders else "")
        return ToolResult(ok=True, output=note + "; the scan was left as it was")


def stack_tools(router_resolver: "Any | None" = None) -> "list[Tool]":
    return [StackPlanTool(router_resolver), StackApplyTool()]


__all__ = ["StackApplyTool", "StackPlanTool", "stack_tools"]
