"""Page-level PDF tools (v1.138.0): ``pdf_arrange`` + ``pdf_split``.

Pages in, pages out — these tools rearrange/merge/rotate/crop/split the PAGES
of real PDFs. They never extract text (that is ``read_document`` /
``extract_pdf``) and never create documents from content (that is
``write_document``): complementary by construction.

Safety model (same discipline as ``documents/tools.py``):

* inputs are READ from anywhere, gated by ``fs_read_ok`` (absolute or
  workspace-relative via the ``_resolve_read_path`` pattern);
* outputs are WRITTEN ONLY inside the session workspace (``safe_path``),
  never over an input, never in-place;
* every written file is TX-01 undoable (``Reversibility.REVERSIBLE``);
* page counts in results are ENGINE-COMPUTED — the engine re-opens each
  written file and reports what is really there, never a claim.

The page math lives in ``pdf_pages`` (pure functions on pypdf, no Tool
imports); its errors are specific and honest and are passed through verbatim.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from ..tools.undo import (
    RevertConflict,
    make_file_descriptor,
    read_envelope,
    revert_workspace_file,
    sha256_bytes,
)
from .tools import _resolve_read_path

#: Page-spec grammar, shared by both tool descriptions (mirrors the engine's
#: documented grammar in ``pdf_pages``).
_SPEC_HELP = (
    "comma-separated tokens: N | N-M (backwards = reversed) | N-end | end | "
    "all | blank (insert a blank page), each with an optional @90/@180/@270 "
    "rotation suffix (e.g. '2-5@90'). 1-based, inclusive."
)


def _entry(item: Any) -> dict[str, Any]:
    """Normalize an engine per-file report entry to ``{path, pages}``.

    The engine reports dataclasses or plain dicts; either way the values are
    its own re-opened page counts — this only reshapes, never computes."""
    if isinstance(item, dict):
        path = item.get("path")
        pages = item.get("pages", item.get("page_count"))
    else:
        path = getattr(item, "path")
        pages = getattr(item, "pages", None)
        if pages is None:
            pages = getattr(item, "page_count")
    return {"path": str(path), "pages": int(pages)}


def _report_field(report: Any, name: str, *fallbacks: str) -> Any:
    """Read ``name`` off an engine report (dataclass or dict)."""
    for key in (name, *fallbacks):
        if isinstance(report, dict):
            if key in report:
                return report[key]
        elif hasattr(report, key):
            return getattr(report, key)
    raise KeyError(f"engine report is missing {name!r}")


def _workspace_rel(path: Path, ctx: ToolContext) -> str:
    return str(path.resolve().relative_to(Path(ctx.workspace).resolve())).replace(
        "\\", "/"
    )


class PdfArrangeTool(Tool):
    name = "pdf_arrange"
    reversibility = Reversibility.REVERSIBLE  # TX-01: only the NEW output file
    description = (
        "Rearrange, merge, rotate, delete, duplicate, reverse, or crop the "
        "PAGES of real PDFs into a NEW PDF in the session workspace — the "
        "originals are NEVER modified. Each input takes a page spec "
        f"({_SPEC_HELP}); pages not selected are dropped, repeated pages are "
        "duplicated, and multiple inputs merge in order. Optional percent "
        "crop margins, output metadata, and output encryption. The result "
        "reports page counts the engine VERIFIED by re-opening the written "
        "file. This tool never extracts text — use read_document/extract_pdf "
        "for that."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "inputs": {
                "type": "array",
                "description": "Source PDFs in merge order (originals untouched).",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Source PDF (absolute, or workspace-relative)."
                            ),
                        },
                        "pages": {
                            "type": "string",
                            "description": (
                                f"Page spec (default 'all') — {_SPEC_HELP}"
                            ),
                        },
                        "password": {
                            "type": "string",
                            "description": "Password if this source is encrypted.",
                        },
                    },
                    "required": ["path"],
                },
            },
            "output": {
                "type": "string",
                "description": (
                    "Workspace-relative path of the NEW .pdf to create."
                ),
            },
            "crop": {
                "type": "object",
                "description": (
                    "Optional PERCENT margins cropped off every output page."
                ),
                "properties": {
                    "top": {"type": "number"},
                    "right": {"type": "number"},
                    "bottom": {"type": "number"},
                    "left": {"type": "number"},
                },
            },
            "metadata": {
                "type": "object",
                "description": "Optional output metadata.",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "subject": {"type": "string"},
                },
            },
            "encrypt_password": {
                "type": "string",
                "description": (
                    "Optional user password to encrypt the OUTPUT with."
                ),
            },
        },
        "required": ["inputs", "output"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Same inverse as ``write_document``: restore prior bytes when the
        output path already exists, else delete the created file."""
        try:
            target = safe_path(ctx.workspace, str(args.get("output") or ""))
        except Exception:
            return None
        if target.is_file():
            try:
                prior = target.read_bytes()
            except OSError:
                return None
            return make_file_descriptor(
                ctx.config.home,
                kind="file_restore",
                path=str(args.get("output") or ""),
                mode="raw",
                prior_bytes=prior,
                pre_sha256=sha256_bytes(prior),
            )
        return make_file_descriptor(
            ctx.config.home,
            kind="file_delete",
            path=str(args.get("output") or ""),
            mode="raw",
        )

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from . import pdf_pages as _pages  # lazy: pure engine, heavy import

        raw_inputs = args.get("inputs")
        if not isinstance(raw_inputs, list) or not raw_inputs:
            return ToolResult(
                ok=False,
                error="inputs must be a non-empty list of {path, pages?, password?}",
            )
        sources: list[tuple[Path, dict[str, Any]]] = []
        for item in raw_inputs:
            if not isinstance(item, dict) or not str(item.get("path") or "").strip():
                return ToolResult(ok=False, error="each input needs a path")
            src = _resolve_read_path(str(item["path"]), ctx)
            allowed, reason = fs_read_ok(str(src))
            if not allowed:
                return ToolResult(ok=False, error=f"read denied: {reason}")
            if not src.is_file():
                return ToolResult(ok=False, error=f"not a file: {item['path']}")
            sources.append((src, item))
        try:
            target = safe_path(ctx.workspace, str(args.get("output") or ""))
        except Exception as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        if target.suffix.lower() != ".pdf":
            return ToolResult(
                ok=False, error=f"output must end in .pdf: {args.get('output')!r}"
            )
        for src, _ in sources:
            if target == src.resolve():
                return ToolResult(
                    ok=False,
                    error=(
                        "output must differ from every input — the originals "
                        "are never overwritten"
                    ),
                )
        engine_inputs = [
            _pages.ArrangeInput(
                path=str(src),
                pages_spec=str(item.get("pages") or "all"),
                password=(str(item["password"]) if item.get("password") else None),
            )
            for src, item in sources
        ]
        crop = args.get("crop") if isinstance(args.get("crop"), dict) else None
        metadata = (
            args.get("metadata") if isinstance(args.get("metadata"), dict) else None
        )
        encrypt = str(args.get("encrypt_password") or "") or None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            report = await asyncio.to_thread(  # CPU-bound page work off the loop
                _pages.arrange,
                engine_inputs,
                target,
                crop=crop,
                encrypt_password=encrypt,
                metadata=metadata,
            )
            # Engine-computed honesty: every count below comes from the
            # engine's report, which re-opened the written file.
            pages = int(_report_field(report, "pages", "page_count"))
            in_entries = [_entry(e) for e in _report_field(report, "inputs")]
        except Exception as exc:  # engine errors are specific — pass them through
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        rel = _workspace_rel(target, ctx)
        return ToolResult(
            ok=True,
            output=(
                f"wrote {rel}: {pages} page(s), verified by re-opening, from "
                f"{len(in_entries)} input PDF(s) — originals untouched"
            ),
            data={"path": rel, "pages": pages, "inputs": in_entries},
            # v1.166.0: safe again — registry._record skips post-hoc journaling
            # when capture_undo already owns this invocation's UndoJournal slot.
            created_paths=[str(target.resolve())],
        )


class PdfSplitTool(Tool):
    name = "pdf_split"
    reversibility = Reversibility.REVERSIBLE  # TX-01: only the NEW output files
    description = (
        "Split a real PDF into multiple NEW PDFs in the session workspace — "
        "the original is NEVER modified. Pick exactly ONE mode: `ranges` "
        "(e.g. ['1-3','4-end']), `every` N pages, or `per_page` (one file "
        "per page). Outputs are named <stem>-part01.pdf... and never clobber "
        "existing files. The result reports each output's page count as "
        "VERIFIED by the engine re-opening the written file."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Source PDF (absolute, or workspace-relative).",
            },
            "password": {
                "type": "string",
                "description": "Password if the source is encrypted.",
            },
            "ranges": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Mode 1: one output per range, e.g. ['1-3', '4-end']."
                ),
            },
            "every": {
                "type": "number",
                "description": "Mode 2: a new output every N pages.",
            },
            "per_page": {
                "type": "boolean",
                "description": "Mode 3: one output per page.",
            },
            "out_dir": {
                "type": "string",
                "description": (
                    "Workspace-relative folder for the outputs (default: the "
                    "workspace root)."
                ),
            },
        },
        "required": ["path"],
    }

    def __init__(self) -> None:
        # capture_undo → execute handoff: the registry calls both with the SAME
        # args object, so the descriptor is keyed by id(args) and enriched in
        # execute with the run's ACTUAL output names + hashes (the registry
        # journals the descriptor only AFTER a successful execute, so the
        # enrichment always lands in the journal).
        self._pending: dict[int, dict[str, Any]] = {}

    def _out_dir(self, args: dict[str, Any], ctx: ToolContext) -> Path:
        return safe_path(ctx.workspace, str(args.get("out_dir") or "").strip() or ".")

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """The only side effects are NEW ``<stem>-part*.pdf`` files in
        ``out_dir`` (the engine never clobbers) — snapshot which such files
        already exist, then let ``execute`` record the exact files it wrote
        (plus their hashes) so ``revert`` removes exactly this run's outputs
        and refuses to delete one the user has since edited."""
        try:
            src = _resolve_read_path(str(args.get("path") or ""), ctx)
            out_dir = self._out_dir(args, ctx)
            rel = _workspace_rel(out_dir, ctx) or "."
        except Exception:
            return None
        prefix = f"{src.stem}-part"
        existing = (
            sorted(
                p.name
                for p in out_dir.iterdir()
                if p.name.startswith(prefix) and p.suffix.lower() == ".pdf"
            )
            if out_dir.is_dir()
            else []
        )
        desc = {
            "kind": "pdf_split_delete",
            "reversible": True,
            "pre_ref": None,
            "pre_inline": json.dumps(
                {
                    "dir": rel,
                    "prefix": prefix,
                    "existing": existing,
                    "dir_existed": out_dir.is_dir(),
                }
            ),
            "pre_sha256": None,
            "post_sha256": None,
        }
        self._pending[id(args)] = desc
        return desc

    @staticmethod
    def _record_outputs(
        desc: "dict[str, Any] | None", outputs: "list[dict[str, Any]]"
    ) -> None:
        """Enrich the captured descriptor with the files THIS run actually wrote.

        Without this, revert's only knowledge is the pre-run snapshot diff —
        which over-deletes when a LATER split into the same dir ran before the
        undo (its outputs also match the prefix but are absent from OUR
        snapshot). The name list scopes deletion to exactly this run; the
        hashes arm the same anti-clobber refusal ``write_document`` gets from
        ``guard_unchanged``. Best-effort: a hash failure just leaves that file
        unguarded, never blocks the tool."""
        if desc is None:
            return
        names: list[str] = []
        hashes: dict[str, str] = {}
        for o in outputs:
            p = Path(o["path"])
            names.append(p.name)
            try:
                hashes[p.name] = sha256_bytes(p.read_bytes())
            except OSError:
                pass
        meta = read_envelope(desc)
        meta["outputs"] = names
        meta["hashes"] = hashes
        desc["pre_inline"] = json.dumps(meta)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        meta = read_envelope(undo)
        prefix = str(meta.get("prefix") or "")
        if undo.get("kind") != "pdf_split_delete" or not prefix:
            return ToolResult(
                ok=False, error=f"undo: unknown file undo kind {undo.get('kind')!r}"
            )
        try:
            out_dir = safe_path(ctx.workspace, str(meta.get("dir") or "."))
        except Exception as exc:  # path escaped the workspace — never touch it
            return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")
        existing = set(meta.get("existing") or [])
        # The run's ACTUAL outputs (recorded by execute). Scoping deletion to
        # this set is what makes the undo per-run: a snapshot-diff alone also
        # matches the outputs of a LATER split into the same dir. Hashes arm
        # the anti-clobber refusal (same discipline as guard_unchanged).
        recorded = meta.get("outputs")
        allowed = set(recorded) if isinstance(recorded, list) else None
        hashes = meta.get("hashes") if isinstance(meta.get("hashes"), dict) else {}
        removed = 0
        if out_dir.is_dir():
            targets: list[Path] = []
            for p in sorted(out_dir.iterdir()):
                if (
                    not p.name.startswith(prefix)
                    or p.suffix.lower() != ".pdf"
                    or p.name in existing
                    or (allowed is not None and p.name not in allowed)
                ):
                    continue
                targets.append(p)
            # Refuse BEFORE deleting anything: an all-or-nothing check so a
            # user-edited part never gets destroyed and no partial undo runs.
            for p in targets:
                want = hashes.get(p.name)
                if not want:
                    continue
                try:
                    have = sha256_bytes(p.read_bytes())
                except OSError:
                    continue
                if have != want:
                    raise RevertConflict(
                        f"{p.name} changed since the split — refusing to undo "
                        "(it would delete a newer change)"
                    )
            for p in targets:
                try:
                    p.unlink()
                    removed += 1
                except OSError as exc:
                    return ToolResult(
                        ok=False, error=f"undo: could not remove {p.name}: {exc}"
                    )
            if not meta.get("dir_existed") and not any(out_dir.iterdir()):
                try:
                    out_dir.rmdir()  # we created it — leave no empty husk
                except OSError:
                    pass
        return ToolResult(ok=True, output=f"undo: removed {removed} split output(s)")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from . import pdf_pages as _pages  # lazy: pure engine, heavy import

        # Claim the descriptor captured for THIS args object up front so a
        # failed run never leaves a stale pending entry behind.
        undo_desc = self._pending.pop(id(args), None)
        src = _resolve_read_path(str(args.get("path") or ""), ctx)
        allowed, reason = fs_read_ok(str(src))
        if not allowed:
            return ToolResult(ok=False, error=f"read denied: {reason}")
        if not src.is_file():
            return ToolResult(ok=False, error=f"not a file: {args.get('path')}")
        modes: dict[str, Any] = {}
        if args.get("ranges"):
            modes["ranges"] = [str(r) for r in args["ranges"]]
        if args.get("every"):
            modes["every"] = int(args["every"])
        if args.get("per_page"):
            modes["per_page"] = True
        if len(modes) != 1:
            return ToolResult(
                ok=False,
                error=(
                    "pick exactly one split mode: ranges, every, or per_page "
                    f"(got {sorted(modes) or 'none'})"
                ),
            )
        try:
            out_dir = self._out_dir(args, ctx)
        except Exception as exc:
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        password = str(args["password"]) if args.get("password") else None
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            report = await asyncio.to_thread(  # CPU-bound page work off the loop
                _pages.split, src, out_dir, mode=modes, password=password
            )
            # Engine-computed honesty: per-output counts come from the engine
            # re-opening each written file.
            outputs = [_entry(e) for e in _report_field(report, "outputs")]
        except Exception as exc:  # engine errors are specific — pass them through
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        # Record the run's actual outputs (+ hashes) into the undo descriptor —
        # the registry journals it AFTER this return, so the envelope revert
        # reads always carries the per-run scope.
        self._record_outputs(undo_desc, outputs)
        shown = [
            {"path": _workspace_rel(Path(o["path"]), ctx), "pages": o["pages"]}
            for o in outputs
        ]
        lines = [
            f"split {src.name} into {len(shown)} file(s) "
            "(page counts verified by re-opening; original untouched):"
        ] + [f"- {o['path']} — {o['pages']} page(s)" for o in shown]
        return ToolResult(
            ok=True,
            output="\n".join(lines),
            data={"outputs": shown},
            # v1.166.0: safe again — registry._record skips post-hoc journaling
            # under a capture_undo, and collapses multi-path creations into one
            # `files_delete` envelope row when there is no capture.
            created_paths=[str(Path(o["path"]).resolve()) for o in outputs],
        )


def pdf_page_tools() -> list[Tool]:
    """The page-level PDF tools (engine: ``pdf_pages``)."""
    return [PdfArrangeTool(), PdfSplitTool()]
