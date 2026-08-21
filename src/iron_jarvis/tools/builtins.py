"""Built-in tools (§18). Workspace-scoped subset for the Phase 0–3 slice.

read_file / write_file / edit_file / list_files / grep operate strictly inside
the session workspace (§17 filesystem=workspace_only). shell is included but
defaults to permission ``ask`` and real isolation lands with the Sandbox Manager
(§16, Phase 4).
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from .base import Reversibility, Tool, ToolContext, ToolResult, safe_path
from .undo import (
    make_file_descriptor,
    read_envelope,
    revert_workspace_file,
    sha256_bytes,
)


def _text_sha(content: str) -> str:
    """Newline-invariant hash of text content — matches ``sha256_target(mode=text)``."""
    return sha256_bytes(content.encode("utf-8"))


# --------------------------------------------------------------------------- #
# Bounded workspace walking (v1.153.1)
#
# `list_files` and `grep` each did `base.rglob("*")` — an UNBOUNDED recursive
# walk — INLINE on the daemon's single event loop. Pointed at a large folder,
# one call wedged the entire daemon: it kept listening on 8787 but answered
# nothing, so every request hung, the dashboard reported "Daemon offline", retry
# hung identically, and no threads would load. Observed live at 84% CPU with the
# MainThread parked in `pathlib.is_file` under `ListFilesTool.execute`.
#
# ShellTool three definitions below already carried the rule in a comment ("the
# tool runs on the daemon's single event loop — inline it would freeze ALL
# requests"); these two were simply never brought in line with it.
#
# Two independent defects, so two independent fixes: the walk is now BOUNDED
# (caps + a deadline + pruned heavy directories), and it is OFFLOADED to a
# thread so even a pathological tree can only ever slow down its own request.
# --------------------------------------------------------------------------- #

#: Directories never worth walking for a listing or a text search, and the usual
#: reason a workspace walk explodes.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".next", ".turbo", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".gradle", "target", ".idea", ".vscode", "site-packages",
})

#: Hard ceilings. Every one of them is REPORTED when hit — a listing silently
#: truncated is worse than a slow one, because the model treats what it got as
#: the whole picture and confidently concludes a file does not exist.
_MAX_WALK_ENTRIES = 5000
_MAX_GREP_FILES = 2000
_MAX_GREP_HITS = 500
_MAX_GREP_FILE_BYTES = 2_000_000
_WALK_DEADLINE_S = 10.0


#: Bound ONCE on first use to ``documents/readers._decode_bytes`` — this project's
#: single text decoder (utf-8-sig → strict cp1252 → charset-normalizer → latin-1),
#: written so "cp1252/latin-1 office exports survive instead of turning into
#: replacement characters". `grep` hard-coded ``encoding="utf-8"`` and swallowed
#: the UnicodeDecodeError, so an Excel/QuickBooks/Lacerte CSV was invisible to
#: search with no signal. LAZY AND CACHED because importing it pulls in the whole
#: ``iron_jarvis.documents`` package and this runs once per file across up to
#: ``_MAX_GREP_FILES`` files. Same helper as ``filesearch/service._decode_text``;
#: the two live apart because ``tools/`` must not depend on ``filesearch/``.
_DECODER: Any = None


def _decode_text(data: bytes) -> str:
    """Decode arbitrary text bytes with the project's shared decoder."""
    global _DECODER
    if _DECODER is None:
        from ..documents.readers import _decode_bytes

        _DECODER = _decode_bytes
    return _DECODER(data)


def _is_under(path: Path, root: Path) -> bool:
    """True when *path* really sits under *root*.

    ``relative_to`` raises for anything outside, and a walk that followed a
    junction could hand us such a path — a raised ValueError inside the listing
    comprehension would surface as a tool crash rather than a skipped entry.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _walk_files(base: Path, *, limit: int, deadline_s: float = _WALK_DEADLINE_S):
    """Yield files under *base*, pruned and bounded.

    Returns ``(paths, truncated_reason)``. Uses ``os.walk`` rather than
    ``rglob`` because only ``os.walk`` can PRUNE — dropping ``node_modules``
    after descending into it is the expensive half of the problem.
    """
    import os
    import time

    started = time.monotonic()
    out: list[Path] = []
    truncated = ""
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [dd for dd in dirs if dd not in _SKIP_DIRS and not dd.startswith(".")]
        for fn in files:
            out.append(Path(root) / fn)
            if len(out) >= limit:
                return out, f"stopped at {limit} files"
        # `>=`, not `>`: on Windows time.monotonic() has ~15ms granularity, so a
        # small tree can finish inside a single tick with elapsed == 0.0 exactly.
        # With `>` a deadline of 0 then meant "no deadline", which is the
        # opposite of what it says. At or past the deadline, stop.
        if time.monotonic() - started >= deadline_s:
            truncated = f"stopped after {deadline_s:.0f}s"
            break
    return out, truncated


#: Office formats that are not plain text. ``read_file`` DELEGATES these to the
#: document extractor rather than failing: the user's expectation is simply
#: "the app reads my documents", and making that depend on the model picking
#: the right tool is a trap it already fell into — a live report had the
#: assistant announce that .docx files were "blocked by the filter" (no such
#: filter exists; the documents extract perfectly) after read_file handed it a
#: bare UnicodeDecodeError. Doing the right thing beats explaining the wrong one.
_DOC_EXTENSIONS = {
    ".docx": "Word", ".doc": "Word", ".pdf": "PDF", ".xlsx": "Excel",
    ".xls": "Excel", ".pptx": "PowerPoint", ".ppt": "PowerPoint",
    ".odt": "OpenDocument", ".rtf": "Rich Text",
}


def _document_body(text: str) -> str:
    """The document's OWN text, with the READER's metadata stripped.

    ``extract_text`` can prepend a ``[NOTE: ...]`` line (an extension that lies
    about its contents) and returns a bracketed sentinel sentence for a file with
    no extractable text. Both are the reader talking ABOUT the file rather than
    text FROM it, so counting them as content is what made an unreadable scan
    look like a successfully read document.
    """
    lines = [
        ln
        for ln in (text or "").splitlines()
        if not ln.startswith("[NOTE:") and not ln.startswith("[no extractable text")
    ]
    body = "\n".join(lines)
    try:
        from ..documents.readers import SCANNED_PDF_SENTINEL

        body = body.replace(SCANNED_PDF_SENTINEL, "")
    except Exception:  # noqa: BLE001 — the prefix filter above already covers it
        pass
    return body.strip()


def _empty_extraction_note(path: Path, text: str) -> str:
    """What to SAY when a document extracted to nothing at all.

    Two different facts, and conflating them would be its own dishonesty: a scan
    holds text we simply cannot reach from here, an empty file holds none. Runs
    ``needs_ocr`` (which parses the PDF), so callers hand it to a thread.
    """
    try:
        from ..documents.ocr import needs_ocr

        scanned = needs_ocr(path, text)
    except Exception:  # noqa: BLE001 — an undecidable file is reported plainly
        scanned = False
    if scanned:
        return (
            "NOTHING WAS READ from this file: its text lives in the page image "
            "(a scan or photo) and no vision model is available on this path, so "
            "it was NOT transcribed. Do not describe or rename it from this "
            "output — read it with read_document, or say plainly that it could "
            "not be read"
        )
    return (
        "NOTHING WAS READ from this file — the extractor returned no text at "
        "all (it may be empty, image-only, or password-protected). Do not treat "
        "the absence of text as a description of its contents"
    )


async def _extract_document(
    path: Path, kind: str, ctx: ToolContext | None = None, resolver: Any = None
) -> ToolResult:
    """Serve a document through the extractor, LABELLED as extracted text.

    The label is not decoration. This is a lossy, read-only view of the file —
    round-tripping it through ``write_file`` would replace a real .docx with
    plain text and destroy the document, so the reply says so explicitly.

    OFF THE EVENT LOOP (v1.174.0). ``extract_text`` parses a whole PDF/DOCX
    synchronously, and this ran INLINE on the daemon's single loop — the exact
    shape of the v1.153.1 outage, and about to get far worse now that an
    unreadable scan can reach a per-page vision call. Every other document tool
    already offloads; this one was simply never brought in line.

    It also routes through the SHARED OCR reach point, so a scanned PDF opened
    with ``read_file`` gets the same treatment (or, with no vision model wired,
    the same honest note) it gets from ``read_document`` — which one of two
    interchangeable tools the model happened to name must not decide whether
    the app can read the user's file at all.
    """
    from ..documents import extract_text

    try:
        text = await asyncio.to_thread(extract_text, str(path))
    except ValueError as exc:  # legacy/protected/oversized — a real, nameable no
        return ToolResult(
            ok=False, error=f"cannot read {path.name}: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 — surface the cause, never a guess
        return ToolResult(
            ok=False, error=f"cannot read {path.name}: {type(exc).__name__}: {exc}"
        )
    ocr_note = ""
    if ctx is not None:
        try:
            from ..documents.tools import _with_ocr

            text, ocr_note = await _with_ocr(path, text, resolver, ctx)
        except Exception:  # noqa: BLE001 — recovery is a bonus, never a gate
            ocr_note = ""
    if not ocr_note and not _document_body(text):
        # SILENCE IS NOT AN ANSWER (v1.174.0 review). With no resolver wired,
        # `ocr_if_unreadable` returns immediately and says nothing — so a scanned
        # PDF opened with `read_file` came back ok=True carrying ONLY the
        # "[extracted text from a PDF document ...]" header. A header over an
        # empty body reads as "this document is blank", and the model then
        # renames/summarises a file nobody read. Say what happened instead.
        ocr_note = await asyncio.to_thread(_empty_extraction_note, path, text)
    note = (
        f"[extracted text from a {kind} document — read-only view; to change it "
        f"use write_document, never write_file]\n\n"
    )
    if ocr_note:
        note = f"[{ocr_note}]\n" + note
    return ToolResult(
        ok=True,
        output=note + text,
        data={
            "bytes": len(text),
            "extracted": True,
            "format": kind,
            **({"note": ocr_note} if ocr_note else {}),
        },
    )


#: How many children a "that's a directory" error names before it truncates.
#: Enough to be actionable, few enough that a 5,000-entry folder cannot flood
#: the transcript the budget is trying to protect.
_DIR_HINT_ENTRIES = 12


def unreadable_reason(target: Path, raw: str) -> "str | None":
    """WHY *target* cannot be read as a file, or ``None`` when it can.

    THE MEASURED FAILURE (v1.177.0). Every reader answered
    ``f"no such file: {path}"`` whenever ``is_file()`` was False — so a
    DIRECTORY THAT EXISTS, a path outside the workspace, and a genuinely absent
    file all produced the same sentence, and the only one of the three the
    sentence is true about is the last. On a real job (rename 26 tax documents)
    the agent called ``read_file`` on the folder, was told "no such file", and
    reasonably concluded it had the path wrong: it then tried the same folder
    five different ways, fell back to ``shell``, and burned 24 of the run's 68
    tool calls before reading anything. The step budget stopped it, and the run
    reported "budget" as the cause — a symptom two layers above a tool that lied
    about what it was looking at.

    This is the v1.174.0 "FAILED TOOLS TELL THE TRUTH" rule applied to the
    readers' OWN errors: a failure has to say which of those three happened, and
    for a directory it names what is inside so the next call can be the right
    one instead of another guess.
    """
    try:
        if target.is_file():
            return None
        if target.is_dir():
            try:
                names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
            except OSError:
                names = []
            shown = ", ".join(names[:_DIR_HINT_ENTRIES])
            more = f" (+{len(names) - _DIR_HINT_ENTRIES} more)" if len(names) > _DIR_HINT_ENTRIES else ""
            inside = f" It contains: {shown}{more}." if names else " It is empty."
            return (
                f"'{raw}' is a DIRECTORY, not a file — this tool reads one file "
                f"at a time.{inside} Use list_files to enumerate it, then read a "
                "file inside it by name."
            )
        if target.exists():
            return (
                f"'{raw}' exists but is not a regular file (it may be a device, "
                "socket, or broken link), so there is nothing to read."
            )
    except OSError as exc:  # an unstattable path is reported, never swallowed
        return f"'{raw}' could not be inspected: {exc}"
    return f"no such file: {raw}"


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a file from the session workspace: UTF-8 text (code, .md, .txt, "
        ".json) directly, and Word/PDF/Excel/PowerPoint/RTF by extracting their "
        "text automatically. read_document does the same with page/sheet "
        "selection for large documents."
    )
    reversibility = Reversibility.READONLY  # a read has no side effect to undo
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(self, router_resolver: "Any | None" = None) -> None:
        #: () -> the platform's ModelRouter, when the caller wired one. Same
        #: shape as ReadDocumentTool/ExtractPDFTool: it powers the scanned-file
        #: OCR fallback. Optional so ``default_registry()`` and every bare
        #: construction in the tests still work unchanged — with no resolver the
        #: unreadable-scan path degrades to the honest note, never to silence.
        self._router_resolver = router_resolver

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        # Stat + (for a directory) one listing — off the event loop like every
        # other filesystem step here (v1.153.1).
        reason = await asyncio.to_thread(unreadable_reason, path, str(args["path"]))
        if reason is not None:
            return ToolResult(ok=False, error=reason)
        # A known office format goes straight to the extractor — the extension
        # is certain knowledge, so there is nothing to try and fail at first.
        kind = _DOC_EXTENSIONS.get(path.suffix.lower())
        if kind:
            return await _extract_document(path, kind, ctx, self._router_resolver)
        try:
            # Offloaded for the same reason the walk below is: a large file on a
            # slow or unhydrated path blocks every other request in the daemon.
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            # Not text and not a known document extension: let the extractor
            # sniff it (it handles unknown-suffix files and images) rather than
            # returning a decode traceback nothing can act on.
            return await _extract_document(path, "binary", ctx, self._router_resolver)
        except OSError as exc:  # permissions, a vanished file, a locked handle
            return ToolResult(ok=False, error=f"could not read {path.name}: {exc}")
        return ToolResult(ok=True, output=text, data={"bytes": len(text)})


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 text file in the session workspace."
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes are captured
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the inverse of the write: prior bytes when overwriting an
        existing file (``file_restore``), or a delete of the path we are about to
        CREATE (``file_delete``). ``post_sha256`` is the newline-invariant hash of
        the content we will write, so a later external edit is detected on undo."""
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None

        # OFF THE EVENT LOOP (v1.153.1's rule, applied here at last). This hook
        # is awaited by ``registry.invoke`` BEFORE ``execute`` runs, so its
        # stat + full read of the prior file blocks every other request in the
        # daemon exactly like ``read_file`` used to — and then ``execute``
        # blocks on the same file a second time.
        def _capture() -> "dict[str, Any] | None":
            post = _text_sha(args["content"])
            if target.is_file():
                try:
                    prior = target.read_text(encoding="utf-8").encode("utf-8")
                    mode = "text"
                except (UnicodeDecodeError, OSError):
                    prior = target.read_bytes()
                    mode, post_ = "raw", None  # can't predict text bytes for binary
                else:
                    post_ = post
                return make_file_descriptor(
                    ctx.config.home,
                    kind="file_restore",
                    path=args["path"],
                    mode=mode,
                    prior_bytes=prior,
                    pre_sha256=sha256_bytes(prior),
                    post_sha256=post_,
                )
            return make_file_descriptor(
                ctx.config.home,
                kind="file_delete",
                path=args["path"],
                mode="text",
                post_sha256=post,
            )

        return await asyncio.to_thread(_capture)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        content = args["content"]

        # mkdir + write are BOTH filesystem calls, and a write onto an
        # unhydrated OneDrive path or a dead network share stalls for as long
        # as the OS takes — on the loop that presents as "Daemon offline"
        # (v1.153.1), not as a slow write.
        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)
        return ToolResult(
            ok=True,
            output=f"wrote {len(content)} bytes to {args['path']}",
            data={"path": args["path"], "bytes": len(content)},
            # v1.166.0: safe again — registry._record skips post-hoc journaling
            # when capture_undo already owns this invocation's UndoJournal slot,
            # so created_paths only feeds chat's documents/ArtifactsRail merge.
            created_paths=[str(path)],
        )


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace the first occurrence of `old` with `new` in a workspace file."
    reversibility = Reversibility.REVERSIBLE  # TX-01: prior bytes are captured
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string"},
            "new": {"type": "string"},
        },
        "required": ["path", "old", "new"],
    }

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """Snapshot the pre-edit text. ``post_sha256`` is the hash of the exact
        text ``execute`` will produce (first-occurrence replace), so a concurrent
        edit is caught on undo. No-op when the edit won't apply (file missing / old
        text absent) — nothing will change, so there is nothing to undo."""
        try:
            target = safe_path(ctx.workspace, args["path"])
        except Exception:
            return None

        # Offloaded for the same reason WriteFileTool's hook is: the pre-image
        # read happens on the loop, before execute, on the very file execute is
        # about to read again.
        def _capture() -> "dict[str, Any] | None":
            if not target.is_file():
                return None
            try:
                text = target.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                return None
            if args["old"] not in text:
                return None
            new_text = text.replace(args["old"], args["new"], 1)
            return make_file_descriptor(
                ctx.config.home,
                kind="file_restore",
                path=args["path"],
                mode="text",
                prior_bytes=text.encode("utf-8"),
                pre_sha256=_text_sha(text),
                post_sha256=_text_sha(new_text),
            )

        return await asyncio.to_thread(_capture)

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        path = safe_path(ctx.workspace, args["path"])
        # Stat walk, read and write ALL go to a worker thread — read_file was
        # brought in line in v1.153.1 and edit_file, which does both halves,
        # was not. On a stalled path this froze the whole daemon.
        reason = await asyncio.to_thread(unreadable_reason, path, str(args["path"]))
        if reason is not None:
            return ToolResult(ok=False, error=reason)
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        if args["old"] not in text:
            return ToolResult(ok=False, error="`old` text not found")
        await asyncio.to_thread(
            path.write_text, text.replace(args["old"], args["new"], 1), encoding="utf-8"
        )
        return ToolResult(ok=True, output=f"edited {args['path']}")


def _case_only_rename(src: Path, dst: Path) -> bool:
    """True when ``dst`` is ``src`` respelled with different CAPITALIZATION.

    On Windows the filesystem folds case, so equal ``os.path.normcase`` strings
    mean both names denote the SAME on-disk entry: the caller is fixing
    capitalization ("2025_w2.pdf" -> "2025_W2.pdf"), which is a legitimate
    rename that the plain ``dst.exists()`` clobber guard would refuse. On a
    case-SENSITIVE filesystem ``normcase`` is the identity, so this is never
    taken and such a rename stays an ordinary one. Pure string work — no I/O,
    so it is safe to call on the event loop.
    """
    return (
        str(src) != str(dst)
        and os.path.normcase(str(src)) == os.path.normcase(str(dst))
    )


def _denotes_a_different_file(src: Path, dst: Path) -> bool:
    """True when ``dst`` is a SEPARATE on-disk file from ``src``.

    ``_case_only_rename`` is a string test, and ``os.path.normcase`` folds case
    on Windows REGARDLESS of whether the directory actually is case-insensitive
    — per-directory case sensitivity on NTFS, and a mounted Samba/NAS share with
    ``case sensitive = yes``, both make ``foo.pdf`` and ``FOO.pdf`` two distinct
    files while the string test still says "same entry". Taking the case-only
    branch there would rename straight over the other file with no
    ``overwrite=true``, so the string test is CONFIRMED against the filesystem
    before the clobber refusal is allowed to stand down.

    ``OSError`` answers "different" on purpose: not knowing must land on the
    refusal, never on the silent overwrite (permissions FAIL CLOSED). Does real
    I/O — call it from a worker thread.
    """
    try:
        if not dst.exists():
            return False
        return not os.path.samefile(str(src), str(dst))
    except OSError:
        return True


def _with_requested_case(resolved: Path, wanted: str) -> Path:
    """Put the SPELLING the caller asked for back onto a resolved path.

    ``safe_path`` calls ``.resolve()``, and on Windows that answers with the
    ON-DISK spelling whenever a case variant of the name already exists — so
    "2025_W2.pdf" comes back as "2025_w2.pdf" and the capitalization the caller
    asked for is destroyed before anyone looks at it. Only a PURE case
    difference is restored (same parent, same letters), so anything else
    ``resolve()`` decided (symlinks, "..") and the containment ``safe_path``
    just proved are both left exactly as they are. One predicate, because the
    forward rename and its undo must agree about what was asked for.
    """
    if wanted and resolved.name != wanted and resolved.name.lower() == wanted.lower():
        return resolved.with_name(wanted)
    return resolved


def _rename_via_temp(src: Path, dst: Path) -> None:
    """Do a capitalization-only rename in TWO steps, through a unique temporary
    name, which is what a case-folding filesystem requires: renaming straight
    onto a name the OS considers identical to the source is not portable (it can
    report success and leave the old spelling on disk). After step one no entry
    with ``dst``'s name exists, so step two writes the exact spelling asked for.
    A failing second step rolls the first one back, so a failure never leaves
    the user's file parked under the temporary name.
    """
    tmp = dst.with_name(f"{dst.name}.ij-case-{uuid4().hex[:8]}")
    src.replace(tmp)
    try:
        tmp.replace(dst)
    except OSError:
        try:
            tmp.replace(src)
        except OSError:  # nothing more we can do; report the real failure
            pass
        raise


class RenameFileTool(Tool):
    """Rename or move ONE file inside the workspace (v1.177.2).

    THE CAPABILITY THAT WAS NOT THERE. "Rename all files in this folder to a
    name that is more appropriate given the content" is this app's own
    acceptance job, run four times, and the roster held read_file, write_file,
    edit_file, list_files, grep — and nothing that can rename a file. So the
    agent did what a person would: it shelled out. The measured run spent 25
    ``shell`` calls, several of them writing PyMuPDF scripts to re-extract PDFs
    ``read_file`` had ALREADY read successfully, and renamed nothing.

    Every wave before this one built scaffolding around the hole — honest
    directory errors, OCR, a durable worklist, a deterministic plan — and all of
    it was necessary, and none of it could put a new name on a file. This is the
    same lesson v1.174.0 wrote down and did not finish applying: the agent was
    not confused, it was compensating for a missing capability.

    Doing it as a TOOL rather than a shell command buys the three things shell
    cannot: the workspace confinement every other file tool obeys, an entry in
    the undo journal (a rename is trivially reversible — rename back), and a
    refusal to clobber. `move` is the same operation, so this serves both.
    """

    name = "rename_file"
    description = (
        "Rename or move ONE file inside the workspace. Give `path` (the file "
        "now) and `new_path` (what it should be called, or where it should go — "
        "a bare filename keeps it in the same folder). Refuses to overwrite an "
        "existing file unless overwrite=true. Reversible."
    )
    reversibility = Reversibility.REVERSIBLE
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "new_path": {"type": "string"},
            "overwrite": {"type": "boolean"},
        },
        "required": ["path", "new_path"],
    }

    @staticmethod
    def _targets(args: dict[str, Any], ctx: ToolContext) -> "tuple[Path, Path]":
        src = safe_path(ctx.workspace, args["path"])
        raw_new = str(args["new_path"]).strip()
        # A BARE NAME KEEPS THE FOLDER. Resolving "2025_W2.pdf" against the
        # workspace root would silently move a file out of the subfolder it
        # lives in — on a rename job over a nested folder that is data loss the
        # user never asked for and would not notice until the folder was empty.
        if not Path(raw_new).parent.parts:
            dst = safe_path(ctx.workspace, str(Path(args["path"]).parent / raw_new))
        else:
            dst = safe_path(ctx.workspace, raw_new)
        # THE REQUESTED CASING SURVIVES RESOLUTION (see _with_requested_case) —
        # without this the capitalization fix the user asked for is gone before
        # the tool ever looks at it.
        dst = _with_requested_case(dst, Path(raw_new).name)
        return src, dst

    async def capture_undo(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> "dict[str, Any] | None":
        """The inverse of a rename is a rename back — no pre-image needed.

        The destination path is what the revert must move FROM, so it is stored
        as the descriptor's ``path`` and the ORIGINAL rides in ``mode``'s slot
        via a dedicated envelope field.
        """
        try:
            src, dst = self._targets(args, ctx)
        except Exception:  # noqa: BLE001 — an unresolvable pair is not journaled
            return None
        if not src.is_file():
            return None
        descriptor = make_file_descriptor(
            ctx.config.home,
            kind="file_rename",
            path=str(dst),
            mode="rename",
            pre_sha256=None,
            post_sha256=None,
        )
        # Carry the original path alongside; read_envelope hands it back.
        import json as _json

        meta = _json.loads(descriptor["pre_inline"])
        meta["rename_from"] = str(src)
        descriptor["pre_inline"] = _json.dumps(meta)
        return descriptor

    async def revert(self, undo: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """A CASE-ONLY RENAME MUST UNDO LIKE ANY OTHER (v1.192.0).

        This tool declares ``Reversibility.REVERSIBLE`` and the undo-journal
        entry is one of the three reasons it exists as a tool rather than a
        shell call, so the new capability may NOT quietly exempt itself from
        that contract on a folder of client documents. The shared
        ``file_rename`` branch cannot serve it: it does
        ``back = safe_path(ctx.workspace, origin)``, and on Windows that
        resolves the OLD spelling straight back to the NEW on-disk one — so
        ``back.exists()`` is True and the revert refuses with "…already exists
        again", naming the NEW spelling as the obstacle to restoring the old
        one. That is not a cautious refusal, it is a refusal that describes the
        wrong file.

        So the case-only pair is handled HERE, in the same terms the forward
        rename used, and everything else falls through unchanged.
        """
        if undo.get("kind") == "file_rename":
            meta = read_envelope(undo)
            rel, origin = meta.get("path"), meta.get("rename_from")
            if rel and origin:
                try:
                    target = safe_path(ctx.workspace, str(rel))
                    back = safe_path(ctx.workspace, str(origin))
                except Exception as exc:  # noqa: BLE001 — never write outside it
                    return ToolResult(ok=False, error=f"undo: unsafe path: {exc}")
                # Same restoration _targets does. Without it ``back`` comes back
                # spelled like ``target`` and the case-only branch below could
                # never be taken.
                back = _with_requested_case(back, Path(str(origin)).name)
                # CONFIRMED against the filesystem, exactly as execute does it:
                # this branch deliberately skips the ``back.exists()`` clobber
                # guard, so on a case-SENSITIVE directory an unconfirmed match
                # would destroy a genuinely different file DURING AN UNDO. When
                # the two are not one entry, fall through to
                # ``revert_workspace_file`` and let it refuse.
                if _case_only_rename(target, back) and not await asyncio.to_thread(
                    _denotes_a_different_file, target, back
                ):
                    # The "no longer there" check still applies; the clobber
                    # guard does not — it would be answering about the very
                    # file being renamed.
                    if not await asyncio.to_thread(target.exists):
                        return ToolResult(
                            ok=False,
                            error=(
                                f"undo: {rel} is no longer there "
                                "(renamed or removed since)"
                            ),
                        )
                    try:
                        await asyncio.to_thread(_rename_via_temp, target, back)
                    except OSError as exc:
                        return ToolResult(
                            ok=False, error=f"undo: could not rename back: {exc}"
                        )
                    return ToolResult(
                        ok=True, output=f"undo: renamed {rel} back to {origin}"
                    )
        return await revert_workspace_file(undo, ctx)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            src, dst = self._targets(args, ctx)
        except PermissionError as exc:
            return ToolResult(ok=False, error=str(exc))
        reason = await asyncio.to_thread(unreadable_reason, src, str(args["path"]))
        if reason is not None:
            return ToolResult(ok=False, error=reason)
        # Compared as STRINGS: on Windows ``Path`` equality folds case, so
        # ``src == dst`` was True for a case variant and a capitalization fix
        # was rejected as "the same name" — which it is not.
        if str(src) == str(dst):
            return ToolResult(
                ok=False, error=f"the new name is the same as the old one: {src.name}"
            )
        # A CASE-ONLY RENAME IS A REAL RENAME. Its destination is by definition
        # the source itself, so neither the folder check nor the clobber refusal
        # applies (both would answer about the file being renamed), and the
        # rename has to go through a temporary name. But the name test alone may
        # NOT disarm the clobber refusal — ``normcase`` folds case even where the
        # directory does not (see ``_denotes_a_different_file``), so the pair is
        # confirmed to be ONE on-disk entry first. When it is not, this stays an
        # ordinary rename and the checks below give the normal refusal.
        case_only = _case_only_rename(src, dst) and not await asyncio.to_thread(
            _denotes_a_different_file, src, dst
        )
        if not case_only:
            if not await asyncio.to_thread(lambda: dst.parent.is_dir()):
                return ToolResult(
                    ok=False,
                    error=f"the destination folder does not exist: {dst.parent}",
                )
            if await asyncio.to_thread(dst.exists) and not args.get("overwrite"):
                # NEVER silently. Two documents whose contents suggest the same
                # name is the NORMAL case on a tax folder (two 1099-NECs from
                # one payer), and a clobber there destroys a client's file.
                return ToolResult(
                    ok=False,
                    error=(
                        f"'{dst.name}' already exists in that folder — refusing to "
                        "overwrite it. Choose a different name (add a distinguishing "
                        "detail), or pass overwrite=true if replacing it is intended."
                    ),
                )
        try:
            if case_only:
                await asyncio.to_thread(_rename_via_temp, src, dst)
            else:
                await asyncio.to_thread(src.replace, dst)
        except OSError as exc:
            return ToolResult(ok=False, error=f"could not rename: {exc}")
        # ABSOLUTE paths (the v1.153.2 rule): a workspace-relative answer is a
        # bare filename whenever the file sits in the workspace root, and the
        # user then looks for it next to the original.
        return ToolResult(
            ok=True,
            output=f"renamed:\n  from: {src}\n  to:   {dst}",
            data={"from": str(src), "to": str(dst), "abs_path": str(dst)},
        )


class ListFilesTool(Tool):
    name = "list_files"
    description = "List files under a workspace directory (default: workspace root)."
    reversibility = Reversibility.READONLY  # a listing has no side effect
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        base = safe_path(ctx.workspace, args.get("path", "."))
        if not base.exists():
            return ToolResult(ok=False, error="no such directory")

        def _list():
            root = ctx.workspace.resolve()
            paths, truncated = _walk_files(base, limit=_MAX_WALK_ENTRIES)
            names = sorted(
                str(p.relative_to(root)).replace("\\", "/")
                for p in paths
                if _is_under(p, root)
            )
            return names, truncated

        # Offloaded for the same reason ShellTool is: this runs on the daemon's
        # single event loop, and inline it freezes every other request.
        entries, truncated = await asyncio.to_thread(_list)
        out = "\n".join(entries)
        if truncated:
            # Said OUT LOUD. A silently-truncated listing reads as complete, and
            # the model then reports that a file is not there.
            out += (
                f"\n\n[listing truncated — {truncated}. This is NOT the whole "
                f"directory; narrow `path` to see the rest.]"
            )
        return ToolResult(
            ok=True,
            output=out,
            data={"count": len(entries), "truncated": bool(truncated)},
        )


class GrepTool(Tool):
    name = "grep"
    description = "Regex-search workspace files; returns matching path:line entries."
    reversibility = Reversibility.READONLY  # a search has no side effect
    input_schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        base = safe_path(ctx.workspace, args.get("path", "."))
        try:
            rx = re.compile(args["pattern"])
        except re.error as exc:
            return ToolResult(ok=False, error=f"bad regex: {exc}")
        def _search():
            import time

            root = ctx.workspace.resolve()
            started = time.monotonic()
            hits: list[str] = []
            # Files we opened and could NOT turn into text. Counted, not dropped:
            # the truncation note below already proves this tool reports what it
            # could not cover, and a skipped file is the same kind of hole.
            unreadable = 0
            if base.is_file():
                files, truncated = [base], ""
            else:
                files, truncated = _walk_files(base, limit=_MAX_GREP_FILES)
            for fp in files:
                if time.monotonic() - started > _WALK_DEADLINE_S:
                    truncated = truncated or f"stopped after {_WALK_DEADLINE_S:.0f}s"
                    break
                try:
                    # Skip anything too big to be worth scanning line-by-line;
                    # reading a 300MB log into memory on this path is what turns
                    # a slow search into an unresponsive app.
                    if fp.stat().st_size > _MAX_GREP_FILE_BYTES:
                        continue
                    raw = fp.read_bytes()
                except (OSError, ValueError):
                    unreadable += 1
                    continue
                # The decoder below is TOTAL (latin-1 maps all 256 bytes), so it
                # can no longer be the thing that keeps binaries out — the
                # UnicodeDecodeError used to do that by accident. Sniff for NUL
                # explicitly instead, exactly as `filesearch/service._read_text`
                # does; without it every .png and .exe in the tree would be
                # scanned as latin-1 mojibake and could produce junk matches.
                if b"\x00" in raw:
                    continue
                try:
                    text = _decode_text(raw)
                except Exception:  # noqa: BLE001 — the decoder is total; this
                    # only fires if the documents package fails to import, which
                    # must degrade to a REPORTED skip, not a silent miss.
                    unreadable += 1
                    continue
                try:
                    for i, line in enumerate(text.splitlines(), 1):
                        if rx.search(line):
                            rel = str(fp.relative_to(root)).replace("\\", "/")
                            hits.append(f"{rel}:{i}: {line.strip()}")
                            if len(hits) >= _MAX_GREP_HITS:
                                return (
                                    hits,
                                    f"stopped at {_MAX_GREP_HITS} matches",
                                    unreadable,
                                )
                except ValueError:  # a path outside root -> relative_to
                    continue
            return hits, truncated, unreadable

        # Offloaded: unlike the old inline version, a pathological tree can now
        # only ever slow down THIS request instead of the whole daemon.
        hits, truncated, unreadable = await asyncio.to_thread(_search)
        out = "\n".join(hits)
        if truncated:
            out += f"\n\n[search truncated — {truncated}. Narrow `path` or the pattern.]"
        if unreadable:
            out += (
                f"\n\n[{unreadable} file(s) skipped — unreadable encoding or "
                f"denied access. This search did NOT cover them.]"
            )
        return ToolResult(
            ok=True,
            output=out,
            data={
                "matches": len(hits),
                "truncated": bool(truncated),
                "skipped_unreadable": unreadable,
            },
        )


class ShellTool(Tool):
    """The PLACEHOLDER shell — superseded in the running app.

    ``platform.py`` registers ``SandboxedShellTool`` under the same name and
    overwrites this one, so in the packaged daemon nothing here executes: the
    real command path is ``sandbox/shell_tool.py`` over ``sandbox/native.py``
    (which already carries partial output into ``SandboxResult.output``, so
    contract 1 composes a timeout's diagnostic there too). This class still runs
    in ``default_registry()`` and in every test that builds a bare registry —
    which is the only reason the timeout below is worth fixing here rather than
    deleting. Do not read a fix in this file as a fix in production.
    """

    name = "shell"
    description = "Run a shell command in the workspace. (Sandboxing arrives in Phase 4.)"
    permission_key = "shell"  # defaults to 'ask' — fail-closed in headless mode
    input_schema = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        try:
            # Offload to a thread: subprocess.run blocks its OS thread for up to 60s,
            # and the tool runs on the daemon's single event loop — inline it would
            # freeze ALL requests, WS event delivery, and every other session.
            proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    args["command"],
                    shell=True,
                    cwd=ctx.workspace,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            )
        except subprocess.TimeoutExpired as exc:
            # Hand back whatever the command DID print before it hung. A bare
            # "command timed out" is the same dead end as a bare "exit 1": the
            # partial output is usually the only clue about which stage stalled.
            # (`.stdout`/`.stderr` are bytes or str depending on the platform's
            # buffering, so decode defensively.)
            def _text(raw: Any) -> str:
                if isinstance(raw, bytes):
                    return raw.decode("utf-8", "replace")
                return str(raw or "")

            partial = (
                _text(getattr(exc, "stdout", ""))
                + _text(getattr(exc, "stderr", ""))
            ).strip()
            return ToolResult(
                ok=False,
                output=partial,
                error="command timed out after 60s",
                data={"returncode": None, "timed_out": True},
            )
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        return ToolResult(
            ok=proc.returncode == 0,
            output=out.strip(),
            data={"returncode": proc.returncode},
            error=None if proc.returncode == 0 else f"exit {proc.returncode}",
        )


def default_registry():
    """Build a registry populated with the built-in tools."""
    from .registry import ToolRegistry

    registry = ToolRegistry()
    for tool_cls in (
        ReadFileTool,
        WriteFileTool,
        EditFileTool,
        RenameFileTool,
        ListFilesTool,
        GrepTool,
        ShellTool,
    ):
        registry.register(tool_cls())
    return registry
