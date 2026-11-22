"""Document routes: uploads, living documents, read/write, enhance.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from sqlmodel import select
from pathlib import Path
from typing import Any

from ...core.failures import plain_failure
from .. import app as _app
from ..schemas import (
    BatchFolderBody,
    DocEnhanceBody,
    DocumentOpenBody,
    ExcelGotoBody,
    DocWriteBody,
    FolderCheckBody,
    LiveDocCreate,
    RedactApplyBody,
    RedactScanBody,
    SaveCopyBody,
    UploadBody,
    WorkfolderBody,
)
from ...core.db import session_scope
from ...core.fs_policy import fs_read_ok

#: Suffixes the preview panel renders as pixels (v1.166.0). The preview
#: endpoint returns a POINTER only — the client loads the bytes via
#: /documents/file, which serves these inline so an <img>/iframe can show them.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
#: What may render inline in the browser; everything else downloads
#: (xlsx/docx can't render in an iframe anyway).
_INLINE_SUFFIXES = {".pdf", *_IMAGE_SUFFIXES}

#: Suffix → the app a native open will land in (user-facing button label).
_APP_LABEL = {
    ".docx": "Word", ".doc": "Word",
    ".xlsx": "Excel", ".xlsm": "Excel", ".csv": "Excel",
    ".pptx": "PowerPoint",
    ".pdf": "your PDF viewer",
    ".html": "your browser",
}


#: v1.502.0 (north-star 2.5): the ONLY file types "Open in its app" hands to
#: the OS — documents, pictures, audio and video. An ALLOW list, never a deny
#: list: for a ``.bat``, ``.hta``, ``.exe``, ``.lnk``, ``.js``, ``.ps1`` (and
#: every type nobody listed) the OS's "open" is RUN, and the path can come
#: from a model's reply or a file someone sent. A folder still opens.
OPENABLE_SUFFIXES = frozenset({
    # documents
    ".pdf", ".doc", ".docx", ".rtf", ".odt", ".txt", ".md", ".markdown", ".log",
    ".xls", ".xlsx", ".xlsm", ".csv", ".tsv", ".ods",
    ".ppt", ".pptx", ".odp", ".html", ".htm", ".json", ".xml", ".eml", ".msg",
    # pictures
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".svg",
    # audio and video
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv",
})


def not_openable_words(name: str) -> str:
    """The refusal for a file "Open in its app" will not start (v1.502.0)."""
    return (
        f"Iron Jarvis opens documents, pictures, audio and video only. "
        f"“{name}” could run a program, so it was not opened: open its "
        f"folder instead and decide there."
    )


def _folder_app() -> str:
    """What opens a FOLDER on this OS (v1.351.0, G11), in the answer's words."""
    import sys

    if sys.platform == "win32":
        return "File Explorer"
    if sys.platform == "darwin":
        return "Finder"
    return "your file manager"


def _gated_source(raw: str, base: Path | None = None) -> Path:
    """Resolve a caller-supplied SOURCE path first, then gate the RESOLVED path.

    The order is the whole point. ``fs_read_ok``/``is_protected_path`` resolve
    what they are handed, but ``Path.resolve()`` does NOT expand ``~`` and
    cannot know about a join that happens afterwards — so gating the RAW
    request string and then opening the ``expanduser()``-ed / ``base``-joined
    path checks one file and reads another. Both forms were live bypasses:
    ``~/.ironjarvis/ironjarvis.db`` gated as a literal ``~`` folder under the
    cwd, and a relative ``../secrets/<file>`` gated against the cwd, while the
    read landed inside a registered protected root (the Fernet key material,
    the SQLite DB holding inline undo pre-images of the user's real files).

    *base* is the directory a RELATIVE path is joined to; ``None`` means the
    caller must pass an absolute path, as the sibling creative.py file routes
    and ``_preview_path`` above already require.
    """
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="a path is required")
    src = Path(text).expanduser()
    if not src.is_absolute():
        if base is None:
            raise HTTPException(status_code=400, detail="an absolute path is required")
        src = base / src
    src = src.resolve()
    ok, reason = fs_read_ok(str(src))
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    return src


def _open_native(path: str) -> None:
    """Launch *path* with the OS-associated application (Word/Excel/…).
    Module-level so tests monkeypatch it instead of really launching apps."""
    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 — explicit, user-initiated open
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _batch_egress_check(platform: Any) -> None:
    """Refuse a LOCAL-ONLY chat's batch BEFORE any document is read when the
    model it would run on leaves this network (v1.332.0): the "extract" and
    "synthesize" roles when mapped, else the default route. Auto is left to
    the router, which picks only on this network under the scope. Raises
    :class:`~iron_jarvis.providers.egress.LocalOnlyRefused` (the app's 409)."""
    from ...providers import egress as _egress
    from ...providers.roles import resolve_role

    cfg = getattr(platform, "config", None)
    mgr = getattr(platform, "providers", None)
    default = str(getattr(cfg, "default_provider", "") or "")
    for role in ("extract", "synthesize"):
        llm = resolve_role(cfg, mgr, role, fallback_provider=None, fallback_model=None)
        prov = (llm.provider if getattr(llm, "applied", False) and llm.provider else default) or ""
        if prov.strip().lower() == "auto":
            continue
        model = str((llm.model if getattr(llm, "applied", False) else None)
                    or getattr(cfg, "default_model", "") or "")
        if not _egress.stays_on_this_network(prov, mgr, cfg, model):
            raise _egress.LocalOnlyRefused(prov, model, manager=mgr)


def _sentence(text: str) -> str:
    """``root_problem``'s words as a sentence for the chip (a capital first)."""
    t = (text or "").strip()
    return t[:1].upper() + t[1:] if t else t


def _unique_upload(uploads: Path, name: str, data: bytes) -> Path:
    """Store *data* under *name* in *uploads* without replacing another file
    (v1.499.3). The rule lives in ``documents/workfolder.store_upload`` since
    10-a, shared with a file from the phone; no free name is a 409 here."""
    from ...documents.workfolder import store_upload

    try:
        return store_upload(uploads, name, data)
    except OSError as exc:
        if "too many uploads named" not in str(exc):
            raise
        raise HTTPException(
            status_code=409,
            detail=f"too many uploads named {name}; rename the file and try again",
        )


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.post("/documents/upload")
    def documents_upload(body: UploadBody) -> dict[str, Any]:
        """Accept a base64 file and store it under <home>/uploads (no multipart dep)."""
        import base64

        # Cap the decoded size so a giant upload can't OOM-kill the whole daemon
        # (which would take down every session/terminal with it). 4/3 accounts for
        # base64 expansion; reject BEFORE decoding so we never buffer the bytes.
        approx_bytes = (len(body.content_b64) * 3) // 4
        if approx_bytes > _app._MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large (~{approx_bytes // (1024 * 1024)} MB); "
                    f"limit is {_app._MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                ),
            )
        from ...documents.workfolder import upload_name

        name = upload_name(body.filename)
        uploads = d.platform.config.home / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        try:
            data = base64.b64decode(body.content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=plain_failure("That file could not be read. Choose another file.", exc))
        target = _unique_upload(uploads, name, data)
        return {"path": str(target), "name": target.name, "bytes": len(data)}

    @app.post("/documents/workfolder")
    def documents_workfolder(body: WorkfolderBody) -> dict[str, Any]:
        """A no-project conversation's own visible folder (v1.244.0).

        ``documents/workfolder.py`` records the report this answers. Three
        intents, one route:

        * ``into`` — a folder this route made earlier for the same
          conversation: later attachments are copied in beside the first. It
          must sit under ``config.chat_files_dir``; anything else is a 400,
          because a route that copied uploads into any folder the caller named
          would be a file-copy primitive nobody asked for.
        * ``prefer`` — the folder the chat is already pointed at. Kept as it
          is when the app can work in it (``root_problem``, the ONE definition
          every "work in the user's own folder" door uses), with nothing
          copied: the user chose that folder, and filling it with duplicates
          is not ours to decide.
        * otherwise a NEW dated folder under ``chat_files_dir`` with the
          uploads copied in — and, when ``prefer`` was refused, a ``note``
          naming the refused folder, why, and where the work goes instead, so
          a sticky pick such as ``C:\\Users`` is never replaced silently.

        Sync on purpose: every step stats, probes or copies, so FastAPI runs
        it on the threadpool, off the event loop (v1.153.1).
        """
        from ...core.fs_policy import root_problem
        from ...documents.workfolder import copy_uploads_into, make_chat_workfolder

        cfg = d.platform.config
        uploads = cfg.home / "uploads"
        root = cfg.chat_files_dir

        into = (body.into or "").strip()
        if into:
            target = Path(into)
            try:
                target.resolve().relative_to(root.resolve())
                ours = target.is_dir()
            except (ValueError, OSError):
                ours = False
            if not ours:
                raise HTTPException(
                    status_code=400,
                    detail=f"not a conversation folder under {root}: {into}",
                )
            copied, skipped = copy_uploads_into(target, body.files, uploads)
            return {
                "path": str(target),
                "created": False,
                "files": copied,
                "skipped": skipped,
                "note": "",
            }

        prefer = (body.prefer or "").strip()
        refused = ""
        if prefer:
            try:
                refused = root_problem(prefer) or ""
            except Exception as exc:  # noqa: BLE001 — an unreadable pick is not usable
                refused = f"{type(exc).__name__}: {exc}"
            if not refused:
                return {"path": prefer, "created": False, "files": [], "skipped": [], "note": ""}

        title = body.title or (Path(body.files[0]).name if body.files else "")
        try:
            made = make_chat_workfolder(root, title, body.files, uploads)
        except OSError as exc:
            raise HTTPException(
                status_code=409,
                detail=plain_failure(f"A working folder could not be made in {root}.", exc),
            )
        problem = root_problem(made["path"])
        if problem:
            raise HTTPException(status_code=409, detail=problem)
        made["note"] = (
            f"This chat was pointed at {prefer}, but {refused}. "
            f"Its files go in {made['path']} instead."
            if prefer
            else ""
        )
        return made

    @app.post("/documents/folder-check")
    async def documents_folder_check(body: FolderCheckBody) -> dict[str, Any]:
        """May the chat work in the folder the user just picked? (v1.443.0)

        The chat's folder chip asks right after a pick, so a folder the chat
        cannot use is SAID under the chip instead of failing later: the answer
        is ``fs_policy.root_problem`` — the ONE definition the chat lanes'
        ``usable_workspace_root`` and every other "work in the user's own
        folder" door use — in a sentence, or ``None``. The pick stands either
        way (the turn grounds itself honestly); this only tells the user.

        A network spelling (``\\\\host\\share``, ``//host/share``, a device
        path) is NOT looked at: judged on its TEXT first, because a stat of a
        share is a network connection and can stall for as long as the
        network takes (the v1.379.0 lesson). ``checked`` is then false and
        nothing is said; a message still runs the lane's own check. Every
        other check is BLOCKING (a writability probe creates a file), so it
        runs through ``asyncio.to_thread``, never on the event loop.
        """
        from ...core.fs_policy import root_problem

        raw = (body.path or "").strip()
        spelled = raw.replace("/", "\\")
        if spelled.startswith("\\\\"):
            return {"problem": None, "checked": False}
        try:
            problem = await asyncio.to_thread(root_problem, raw)
        except Exception as exc:  # noqa: BLE001 — an unreadable pick is not usable
            problem = f"that folder could not be read: {type(exc).__name__}"
        return {"problem": _sentence(problem) if problem else None, "checked": True}

    # ------------------------------------------------------------------ #
    # A FOLDER OF DOCUMENTS BECOMES ONE SUMMARY SHEET (v1.251.0, C-04).
    #
    # `batch_documents` has existed since v1.133.0 and is deliberately NOT
    # auto-armable: `tools/autoselect` keeps it out on COST grounds (about one
    # model call per document, default 25, cap 100) and says so — it "remains
    # one click away in the '+' menu, which is the interactive consent its cost
    # profile deserves". These two routes are that click, made visible: the
    # PREVIEW counts what the batch would really process so the card can state
    # the number and the spend BEFORE anything is charged, and the RUN is the
    # user pressing the button.
    #
    # THE PREVIEW MUST AGREE WITH THE RUN. Both answers come from the SAME
    # `sweep` + `ocr_settings` pair the pipeline itself uses — a second counter
    # would drift and the card would quote a number the batch does not honour.
    # ------------------------------------------------------------------ #
    def _batch_folder(raw: str) -> Path:
        p = Path((raw or "").strip())
        if not raw or not p.is_absolute():
            raise HTTPException(status_code=400, detail="an absolute folder is required")
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=reason or "folder not allowed")
        if not p.is_dir():
            raise HTTPException(status_code=404, detail=f"not a folder: {p}")
        return p

    @app.post("/documents/batch/preview")
    def documents_batch_preview(body: BatchFolderBody) -> dict[str, Any]:
        """What a batch over this folder WOULD do — and cost. Nothing runs.

        Answers ``{folder, files, skipped, count, estimate_calls, cap,
        truncated}``. ``skipped`` carries ``sweep``'s own reason per entry
        (subfolder, unsupported type, read denied, over the cap), because a
        count that silently omits files reads as complete — the truncation lie
        this repo bans.

        ``estimate_calls`` is documents + 1 synthesis pass: the honest shape of
        the spend, not a price. Sync (it stats a folder) so FastAPI keeps it off
        the event loop (v1.153.1).
        """
        from ...documents.batch import sweep
        from ...documents.ocr import ocr_settings

        folder = _batch_folder(body.folder)
        cap = max(1, min(int(body.max_files or 25), 100))
        ocr_on, _pages = ocr_settings(d.platform.config)
        try:
            files, skipped = sweep(folder, cap, include_images=ocr_on)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=plain_failure(f"That folder could not be read: {folder}", exc))
        return {
            "folder": str(folder),
            "files": [{"name": p.name, "path": str(p)} for p in files],
            "skipped": skipped,
            "count": len(files),
            # One call per document plus the synthesis pass that writes the
            # sheet. Named as an ESTIMATE: a cached re-run costs less, and a
            # document that fails still spent its call.
            "estimate_calls": len(files) + (1 if files else 0),
            "cap": cap,
            "truncated": any(
                "over the max_files limit" in (s.get("reason") or "") for s in skipped
            ),
        }

    @app.post("/documents/batch")
    async def documents_batch_run(body: BatchFolderBody) -> dict[str, Any]:
        """RUN the batch — the user pressed the button, so this spends money.

        Invoked through ``registry.invoke`` with the real permission engine, so
        this route is not a permission bypass (the v1.121.0 rule a workflow tool
        step follows too). ``batch_documents`` is allow-tier, so no
        ``session_allow`` grant is passed or needed: a route that self-granted
        would be widening consent, not honouring it.

        Deliverables land in ``workspace_dir`` — the conversation's own folder
        (v1.244.0) — so the sheet appears beside the documents it summarises.
        ``created_paths`` comes back absolute, which is what puts it on the
        chat's Files rail.
        """
        from ...providers import egress as _egress
        from ...tools.base import ToolContext

        folder = _batch_folder(body.folder)
        # LOCAL ONLY (v1.332.0): a local-only chat's batch reads the folder on
        # models on this network only. Refused up front (409, the local-only
        # shape) when the model it would use leaves this network, and run
        # under the scope so the router refuses any call the check missed.
        local_only = await _egress.effective_local_only(
            d.platform.engine, body.local_only, body.thread_id
        )
        if local_only:
            _batch_egress_check(d.platform)
        tool = d.platform.registry.get("batch_documents")
        if tool is None:
            raise HTTPException(status_code=501, detail="batch_documents is not registered")
        ws = (body.workspace_dir or "").strip()
        workspace = Path(ws) if ws else folder
        if ws:
            ok, reason = fs_read_ok(ws)
            if not ok:
                raise HTTPException(status_code=403, detail=reason or "workspace not allowed")
        ctx = ToolContext(
            workspace=workspace,
            # The progress events carry this, so the page can tell THIS
            # conversation's batch from any other running at the same time.
            session_id=(body.session_id or "chat").strip() or "chat",
            agent_run_id="chat",
            config=d.platform.config,
            event_bus=d.platform.event_bus,
            engine=d.platform.engine,
        )
        with _egress.local_only_scope(local_only):
            result = await d.platform.registry.invoke(
                "batch_documents",
                {
                    "folder": str(folder),
                    "instructions": body.instructions or "",
                    "output": body.output or "both",
                    "max_files": max(1, min(int(body.max_files or 25), 100)),
                },
                ctx,
                d.platform.permissions,
                None,
            )
        if not result.ok:
            raise HTTPException(status_code=409, detail=result.error or "batch failed")
        return {
            "output": result.output,
            "report": result.data or {},
            "created_paths": result.created_paths or [],
        }

    # ------------------------------------------------------------------ #
    # Preview + native open (v1.89.0) — the chat's embedded document panel.
    # Any policy-allowed ABSOLUTE path works: local disk, a network share,
    # or a tailnet folder (the daemon reads it like any other file).
    # ------------------------------------------------------------------ #
    def _allowed_path(raw: str) -> Path:
        """An ABSOLUTE path the file policy lets the app read, not inside a
        protected root (400 / 403 otherwise). Says nothing about whether it
        exists or what it is: the callers decide that."""
        from ...core.fs_policy import is_protected_path

        p = Path((raw or "").strip())
        if not raw or not p.is_absolute():
            raise HTTPException(status_code=400, detail="an absolute path is required")
        ok, reason = fs_read_ok(str(p))
        if not ok or is_protected_path(str(p)):
            raise HTTPException(status_code=403, detail=reason or "path not allowed")
        return p

    def _preview_path(raw: str) -> Path:
        p = _allowed_path(raw)
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"no such file: {p}")
        return p

    @app.get("/documents/preview")
    def document_preview(path: str, sheet: str = "", start: int = 1) -> dict[str, Any]:
        """Structured preview of ONE document: spreadsheets as sheet tabs +
        rows (engine-read, capped), PDFs as an embed pointer (the client
        iframes /documents/file), everything else as extracted text.

        ``start`` (v1.549.0, idea 30-a): a sheet's rows from this row on, so
        a figure's cell past the first 80 rows can be shown lit (the answer
        says ``first_row``)."""
        p = _preview_path(path)
        suffix = p.suffix.lower()
        base: dict[str, Any] = {"name": p.name, "path": str(p), "suffix": suffix}
        first = max(1, int(start or 1))
        if suffix in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook

            try:
                wb = load_workbook(str(p), data_only=True, read_only=True)
                names = list(wb.sheetnames)
                ws = wb[sheet] if sheet and sheet in names else wb.active
                rows: list[list[str]] = []
                truncated = first > 1
                for i, row in enumerate(ws.iter_rows(values_only=True, min_row=first)):
                    if i >= 80:
                        truncated = True
                        break
                    # Column and cell clips are TRUNCATION too (v1.167.0): a
                    # 40-column ledger shown as a complete-looking 30-column
                    # table — and the Changes diff then reporting "no changes"
                    # for an edit in column AK — is the silent-truncation class.
                    if len(row) > 30:
                        truncated = True
                    if any(
                        v is not None and len(str(v)) > 80 for v in row[:30]
                    ):
                        truncated = True
                    rows.append(["" if v is None else str(v)[:80] for v in row[:30]])
                title = ws.title
                # Truncation honesty (v1.166.0): the REAL extent, so the client
                # can say "first 80 of N rows" instead of implying completeness.
                total_rows = ws.max_row
                total_cols = ws.max_column
                wb.close()
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=422, detail=plain_failure("That workbook could not be read.", exc)
                )
            out = {**base, "kind": "sheet", "sheets": names, "sheet": title,
                   "rows": rows, "truncated": truncated, "first_row": first}
            if total_rows is not None:  # read-only mode may lack dimensions
                out["total_rows"] = int(total_rows)
            if total_cols is not None:
                out["total_cols"] = int(total_cols)
            return out
        if suffix == ".pdf":
            return {**base, "kind": "pdf"}
        if suffix in _IMAGE_SUFFIXES:
            # A pointer only — no bytes in the JSON. The client loads the
            # pixels via /documents/file (served inline for image suffixes).
            return {**base, "kind": "image"}
        if suffix in (".csv", ".tsv"):
            import csv as _csv

            delim = "\t" if suffix == ".tsv" else ","
            rows = []
            truncated = first > 1
            total_rows = 0
            total_cols = 0
            try:
                with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                    for row in _csv.reader(fh, delimiter=delim):
                        total_rows += 1  # real count — full iteration is cheap
                        total_cols = max(total_cols, len(row))
                        if total_rows < first:
                            continue
                        if len(rows) >= 80:
                            truncated = True
                            continue
                        if len(row) > 30:
                            truncated = True
                        if any(len(str(v)) > 80 for v in row[:30]):
                            truncated = True  # cell clip is truncation too
                        rows.append([str(v)[:80] for v in row[:30]])
            except (OSError, _csv.Error) as exc:
                # _csv.Error covers parser failures such as a field over the
                # process-wide csv.field_size_limit (~128KB) — a realistic CSV
                # export with a blob cell. Do NOT raise the limit: it is
                # process-global state shared with every other csv consumer.
                raise HTTPException(status_code=422, detail=plain_failure("That file could not be read.", exc))
            return {**base, "kind": "sheet", "sheets": ["CSV"], "sheet": "CSV",
                    "rows": rows, "truncated": truncated, "first_row": first,
                    "total_rows": total_rows, "total_cols": total_cols}
        if suffix == ".docx":
            # WORD-FAITHFUL preview: semantic docx→HTML (headings, bold/italic,
            # lists, real tables) rendered by the client on a white page in a
            # SANDBOXED frame — extracted text loses exactly the formatting the
            # user is reviewing. Degrades to structured markdown, then text.
            try:
                import mammoth

                with p.open("rb") as fh:
                    html = mammoth.convert_to_html(fh).value or ""
                if html.strip():
                    return {**base, "kind": "html", "html": html[:400_000],
                            "truncated": len(html) > 400_000,
                            "total_chars": len(html)}
            except Exception:  # noqa: BLE001 — fall through to markdown/text
                pass
            try:
                from ...documents.pdf_markdown import document_to_markdown

                md = document_to_markdown(p)
                if md.strip():
                    return {**base, "kind": "markdown", "content": md[:40_000],
                            "truncated": len(md) > 40_000,
                            "total_chars": len(md)}
            except Exception:  # noqa: BLE001
                pass
        from ...documents.readers import extract_text

        try:
            text = extract_text(p)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=plain_failure("That file could not be read.", exc))
        kind = "markdown" if suffix in (".md", ".markdown") else "text"
        return {**base, "kind": kind, "content": text[:20_000],
                "truncated": len(text) > 20_000, "total_chars": len(text)}

    @app.get("/documents/file")
    def document_file(path: str, download: bool = False):
        """Raw file bytes (auth rides the header or ?token= like other embeds)
        — powers the preview panel's PDF iframe and image tags. PDFs and
        images serve INLINE so the browser renders them; ``?download=1``
        forces a save-as, and everything else is attachment always (an xlsx
        can't render in an iframe anyway)."""
        import mimetypes

        from fastapi.responses import FileResponse

        p = _preview_path(path)
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            media = "application/pdf"
        elif suffix in _IMAGE_SUFFIXES:
            guessed, _enc = mimetypes.guess_type(p.name)
            media = guessed or "application/octet-stream"
        else:
            media = "application/octet-stream"
        disposition = (
            "inline" if not download and suffix in _INLINE_SUFFIXES
            else "attachment"
        )
        return FileResponse(str(p), media_type=media, filename=p.name,
                            content_disposition_type=disposition)

    @app.get("/documents/page-image")
    def document_page_image(path: str = "", page: int = 0, find: str = "", fig: str = "",
                            mark: str = ""):
        """v1.549.0 (idea 30-a): page ``page`` of a PDF as a picture, with the
        line ``find`` (a figure's recorded line) boxed and ``fig`` lit. When
        the line is not on the page, the page comes back with the line
        printed above it and words saying so (``X-Line-Found: 0``). The read
        gate first; ``?token=`` like ``/documents/file`` (an <img> sends no
        header). Sync: FastAPI runs it on the threadpool.

        ``mark`` (what the page uses): a figure's record id instead of the
        path, page, line and figure, so no line of a client's file rides a
        web address (or the access log)."""
        import re as _re

        from fastapi.responses import Response

        from ...documents import source_store as _marks
        from ...documents.ocr import is_pdf_file
        from ...documents.page_image import NoSuchPage, render_page

        if mark:
            if not _re.fullmatch(r"src_[0-9a-f]{8,32}", mark):
                raise HTTPException(status_code=400, detail="That is not a figure link.")
            found = _marks.get_marks(d.platform.engine, [mark])
            if not found:
                raise HTTPException(status_code=404, detail="No record of where this came from.")
            rec = found[0]
            path, find, fig = rec["path"], rec["context"], rec["shown"]
            try:
                page = int((rec.get("locator") or {}).get("page") or 0)
            except (TypeError, ValueError):
                page = 0
            if page < 1:
                raise HTTPException(status_code=422, detail="That figure was not read from a PDF page.")
        p = _preview_path(path)
        if not is_pdf_file(p):
            raise HTTPException(status_code=415, detail=f"{p.name} is not a PDF.")
        try:
            png, found = render_page(p, int(page), find=(find or "")[:600], fig=(fig or "")[:60])
        except NoSuchPage:
            raise HTTPException(status_code=404, detail=f"{p.name} has no page {page}.")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=plain_failure("That page could not be drawn.", exc))
        return Response(
            content=png, media_type="image/png",
            headers={"X-Line-Found": "1" if found else "0", "Cache-Control": "no-store"},
        )

    @app.get("/sources/{ids}")
    def figure_sources(ids: str) -> dict[str, Any]:
        """v1.549.0 (idea 30-a): where the figures a reply links were read —
        ``ids`` is a link's ``<id>[,<id>…]``. Each mark says its file, its
        place, the figure as the file writes it and the line it sat on
        (secret-masked), and whether the file is still there and unchanged
        (``present`` / ``changed``, only for a file the read policy allows).
        Sync: FastAPI runs it on the threadpool."""
        import re as _re

        from ...documents import source_store as _marks
        from ...documents.sources import file_hash

        wanted = [i.strip() for i in (ids or "").split(",") if i.strip()]
        if not wanted or len(wanted) > _marks.MAX_LOOKUP or any(
            not _re.fullmatch(r"src_[0-9a-f]{8,32}", i) for i in wanted
        ):
            raise HTTPException(status_code=400, detail="Those are not figure links.")
        marks = _marks.get_marks(d.platform.engine, wanted)
        if not marks:
            raise HTTPException(status_code=404, detail="No record of where this came from.")
        hashes: dict[str, str] = {}
        for m in marks:
            m["name"] = Path(m["path"]).name
            from ...core.fs_policy import is_protected_path

            ok, _why = fs_read_ok(m["path"]) if m["path"] else (False, "")
            ok = bool(ok) and not is_protected_path(m["path"])
            if not ok:
                m["context"] = ""  # the read policy says no now: not even the line
            present = bool(ok) and Path(m["path"]).is_file()
            m["present"] = present
            m["changed"] = None
            if present and m.get("file_sha256"):
                if m["path"] not in hashes:
                    hashes[m["path"]] = file_hash(m["path"])
                now = hashes[m["path"]]
                m["changed"] = bool(now) and now != m["file_sha256"]
            for private in ("file_sha256", "thread_id", "turn_id"):
                m.pop(private, None)
        return {"marks": marks}

    @app.post("/documents/open")
    def document_open(body: DocumentOpenBody) -> dict[str, Any]:
        """Open a document with its OS-associated app (Word/Excel/…) — an
        explicit, user-initiated action from the preview panel's button.

        v1.351.0 (G11): a FOLDER opens too, in the OS file manager. The chat's
        work-folder chip ("Open") posts its folder here, and the file-only
        check answered it 404 "no such file" — the one button that shows a
        no-project chat's saved work never worked. The same policy gates
        apply first (absolute, readable, not protected); then a folder opens
        natively and a file goes on through the file check as before.
        Sync on purpose: FastAPI runs it on the threadpool, off the loop."""
        folder = _allowed_path(body.path)
        if folder.is_dir():
            try:
                _open_native(str(folder))
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=422, detail=plain_failure("That folder could not be opened.", exc))
            return {"ok": True, "app": _folder_app(), "folder": True}
        p = _preview_path(body.path)
        # v1.502.0 (north-star 2.5): a file opens only when its type is a
        # document, picture or media type (``OPENABLE_SUFFIXES``); anything
        # else would be RUN by the OS's open, so it is refused in words.
        if p.suffix.lower() not in OPENABLE_SUFFIXES:
            raise HTTPException(status_code=415, detail=not_openable_words(p.name))
        try:
            _open_native(str(p))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=plain_failure("That file could not be opened.", exc))
        return {"ok": True, "app": _APP_LABEL.get(p.suffix.lower(), "the default app")}

    @app.get("/excel/live/now")
    async def excel_live_now() -> dict[str, Any]:
        """What is in front in Excel right now (v1.468.0): the workbook, the
        sheet and the selected range. NAMES ONLY, never a cell's content, and
        nothing here reaches a model: the chat's "Working with Excel" line
        shows it to the user, and only a chat the user switched on sends the
        three names with a message. Excel not running answers at once with
        no child started."""
        from ...documents import excel_live as _xl

        return await _xl.now()

    @app.post("/excel/live/goto")
    async def excel_live_goto(body: ExcelGotoBody) -> dict[str, Any]:
        """Show one cell of a workbook that is OPEN in Excel (v1.467.0): the
        press behind a cell link in a reply. It selects the cell and brings
        Excel forward. It reads nothing and changes nothing in the workbook,
        and it never opens a file: a workbook that is not open is refused in
        words. The work is done by the short-lived Excel child, so a busy
        Excel can never hold the daemon."""
        from ...documents import excel_live as _xl

        target = _xl.clean_cell_target(body.workbook, body.sheet, body.cell)
        if target is None:
            raise HTTPException(status_code=400, detail="That is not a cell Iron Jarvis can show in Excel.")
        out = await _xl.goto(*target)
        if not out.get("ok"):
            raise HTTPException(status_code=409, detail=str(out.get("error") or _xl.NO_ANSWER))
        return {"ok": True, "workbook": out.get("workbook"), "sheet": out.get("sheet"), "address": out.get("address")}

    @app.get("/documents/live")
    def list_livedocs() -> dict[str, Any]:
        from ...core.models import LiveDocRecord

        with session_scope(d.platform.engine) as db:
            rows = list(db.exec(select(LiveDocRecord)))
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return {"docs": [r.model_dump() for r in rows]}

    @app.post("/documents/live")
    async def create_livedoc(body: LiveDocCreate) -> dict[str, Any]:
        from ...core.models import LiveDocRecord

        name = (body.name or "").strip()
        if not name or not (body.prompt or "").strip():
            raise HTTPException(status_code=400, detail="name and prompt are required")
        if body.format not in ("md", "html", "docx", "pdf"):
            raise HTTPException(status_code=400, detail="format must be md|html|docx|pdf")
        rec = LiveDocRecord(name=name, prompt=body.prompt.strip(), format=body.format,
                            provider=body.provider, model=body.model)

        def _insert() -> None:  # v1.226.0: SQLite write off the loop
            with session_scope(d.platform.engine) as db:
                db.add(rec)
                db.commit()
                db.refresh(rec)

        await asyncio.to_thread(_insert)
        # Optional auto-refresh: an event-kind schedule the lifespan handler
        # listens for. Manual-only docs simply skip this.
        if body.cron or body.interval_seconds:
            sched_name = f"livedoc_{rec.id}"
            try:
                d.platform.scheduler.add_task(
                    sched_name, body.cron,
                    interval_seconds=body.interval_seconds,
                    kind="event",
                    payload={"type": "livedoc.regenerate", "livedoc_id": rec.id},
                )

                def _link_schedule() -> None:  # v1.226.0: SQLite write off the loop
                    with session_scope(d.platform.engine) as db:
                        row = db.get(LiveDocRecord, rec.id)
                        row.schedule_name = sched_name
                        db.add(row)
                        db.commit()

                await asyncio.to_thread(_link_schedule)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=plain_failure("That schedule is not one Iron Jarvis can read.", exc))
        # First generation now, so the doc exists immediately.
        result = await d._regenerate_livedoc(rec.id)
        return {**result, "name": name, "schedule": bool(body.cron or body.interval_seconds)}

    @app.post("/documents/live/{doc_id}/regenerate")
    async def regenerate_livedoc_ep(doc_id: str) -> dict[str, Any]:
        return await d._regenerate_livedoc(doc_id)

    @app.delete("/documents/live/{doc_id}")
    def delete_livedoc(doc_id: str) -> dict[str, Any]:
        """Remove the living doc + its schedule from the APP. The generated
        file stays on disk (never delete the user's files)."""
        from ...core.models import LiveDocRecord

        with session_scope(d.platform.engine) as db:
            row = db.get(LiveDocRecord, doc_id)
            if row is None:
                raise HTTPException(status_code=404, detail="no such living document")
            sched = row.schedule_name
            db.delete(row)
            db.commit()
        if sched:
            try:
                d.platform.scheduler.remove(sched)
            except Exception:  # noqa: BLE001 — schedule may already be gone
                pass
        return {"deleted": doc_id, "files_touched": 0}

    @app.post("/documents/enhance")
    async def enhance_document(body: DocEnhanceBody) -> dict[str, Any]:
        """Suggest a better filename + polished content BEFORE creating —
        returned for review; nothing is written until the user confirms."""
        import json as _json

        from ...providers.adapters.base import LLMMessage

        if not (body.content or "").strip() and not (body.filename or "").strip():
            raise HTTPException(status_code=400, detail="nothing to enhance")
        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=plain_failure("That model could not be used. Choose another model in Settings.", exc))
        system = (
            "You polish document drafts. Respond with ONLY JSON: "
            '{"filename": "improved-name.ext (keep/choose a sensible extension)", '
            '"content": "the improved document content (markdown allowed)", '
            '"notes": "1-3 short bullets on what you changed and why"}. '
            "Improve clarity/structure/professional tone; NEVER invent facts or "
            "figures; keep the user's meaning."
        )
        user = f"Filename: {body.filename or '(none)'}\n\nContent:\n{(body.content or '')[:10000]}"
        resp, _p, _m = await d._one_shot_complete(
            provider, adapter, system=system,
            messages=[LLMMessage(role="user", content=user)],
        )
        text = resp.text or ""
        start, depth, obj = text.find("{"), 0, ""
        if start >= 0:
            for i in range(start, len(text)):
                depth += (text[i] == "{") - (text[i] == "}")
                if depth == 0:
                    obj = text[start:i + 1]
                    break
        try:
            out = _json.loads(obj)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="no valid suggestion — try again")
        return {
            "filename": str(out.get("filename") or body.filename),
            "content": str(out.get("content") or body.content),
            "notes": str(out.get("notes") or ""),
        }

    @app.get("/documents/read")
    async def documents_read(path: str) -> dict[str, Any]:
        import asyncio as _asyncio

        from ...documents import extract_text
        from ...documents.ocr import looks_scanned_pdf, ocr_pdf

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        try:
            text = await _asyncio.to_thread(extract_text, path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=plain_failure("That file could not be read.", exc))
        # SCANNED-PDF fallback: an image-only PDF (death certificate, signed
        # form) has no text layer — recover it via vision OCR instead of
        # returning empty silence. The note always says how the text came to
        # be (or exactly why it couldn't).
        note = ""
        p = Path(path)
        if looks_scanned_pdf(p, text):
            try:
                ocr_text, note = await ocr_pdf(p, d.platform.router)
                if ocr_text:
                    text = ocr_text
            except Exception as exc:  # noqa: BLE001 — OCR failure ≠ read failure
                note = plain_failure(
                    "This looks like a scanned PDF, and its text could not be read from the page images.", exc
                )
        return {"path": path, "text": text[:20000], "note": note}

    #: Suffixes "Compare with…" can put side by side (C-07) — what the
    #: comparison engine and the preview's text diff both understand.
    _COMPARABLE = {
        ".docx", ".doc", ".pdf", ".xlsx", ".xlsm", ".xls", ".csv",
        ".txt", ".md", ".rtf", ".pptx",
    }

    @app.get("/documents/siblings")
    async def documents_siblings(path: str) -> dict[str, Any]:
        """Other documents sitting in the same folder as ``path``.

        This is what the preview's "Compare with…" offers. A conversation's
        files live together (v1.244.0 gives a plain chat its own dated folder),
        so the folder IS the conversation's file list — which keeps the feature
        reachable without any page having to hand it one. Read-gated by the same
        policy as every read, capped, and off the event loop (the v1.153.1 rule:
        a folder scan on the loop is the "Daemon offline" shape)."""
        import asyncio as _asyncio
        import os as _os

        ok, reason = fs_read_ok(path)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)

        def _scan() -> tuple[list[dict[str, Any]], bool]:
            here = Path(path)
            parent = here.parent
            if not parent.is_dir():
                return [], False
            found: list[dict[str, Any]] = []
            seen = 0
            truncated = False
            try:
                with _os.scandir(parent) as it:
                    for entry in it:
                        seen += 1
                        if seen > 2000:
                            truncated = True
                            break
                        if not entry.is_file():
                            continue
                        p = Path(entry.path)
                        if p.suffix.lower() not in _COMPARABLE:
                            continue
                        if p.resolve() == here.resolve():
                            continue
                        allowed, _why = fs_read_ok(entry.path)
                        if not allowed:
                            continue
                        found.append({"path": str(p), "name": p.name})
            except OSError:
                return [], False
            found.sort(key=lambda f: f["name"].casefold())
            if len(found) > 50:
                found, truncated = found[:50], True
            return found, truncated

        files, truncated = await _asyncio.to_thread(_scan)
        return {"files": files, "truncated": truncated}

    # ------------------------------------------------------- save a copy to… ---
    # REPORTED: "the agent should give me options with buttons as to where to
    # store the file."
    #
    # Chat runs its tools inside a confined workspace (the uploads scratch dir,
    # or the grounded project folder), so anything it produces lands THERE by
    # construction — correct for confinement, useless as a place to find a
    # finished document. These two routes let the preview panel offer real
    # destinations instead of leaving the user to hunt for the output.

    def _places() -> list[dict[str, str]]:
        home = Path.home()
        out: list[dict[str, str]] = []
        for key, label, p in (
            ("desktop", "Desktop", home / "Desktop"),
            ("documents", "Documents", home / "Documents"),
            ("downloads", "Downloads", home / "Downloads"),
        ):
            if p.is_dir():
                out.append({"key": key, "label": label, "path": str(p)})
        return out

    @app.get("/documents/places")
    def documents_places() -> dict[str, Any]:
        """Common save destinations that actually exist on this machine."""
        return {"places": _places()}

    @app.post("/documents/save-copy")
    def documents_save_copy(body: SaveCopyBody) -> dict[str, Any]:
        import shutil

        from ...core.fs_policy import is_protected_path

        # Resolve BEFORE gating — see _gated_source. The old order checked the
        # raw string and copied the expanduser()'d file.
        src = _gated_source(body.source)
        if not src.is_file():
            raise HTTPException(status_code=404, detail=f"not a file: {body.source}")
        dest_dir = Path(body.dest_dir).expanduser()
        if not dest_dir.is_absolute():
            raise HTTPException(status_code=400, detail="dest_dir must be absolute")
        if is_protected_path(dest_dir):
            raise HTTPException(status_code=403, detail="destination is a protected path")
        if not dest_dir.is_dir():
            raise HTTPException(status_code=404, detail=f"no such folder: {body.dest_dir}")
        # The NAME is a file name, never a path (v1.499.3): "..\\..\\x" was
        # joined unchecked and escaped the folder just checked above.
        name = body.name.strip() or src.name
        if (
            any(sep in name for sep in ("/", "\\", ":", "\x00"))
            or name in (".", "..")
            or Path(name).is_absolute()
            or Path(name).name != name
        ):
            raise HTTPException(
                status_code=400,
                detail="the name must be a file name only, without a folder or drive",
            )
        target = dest_dir / name
        try:
            inside = target.resolve().parent == dest_dir.resolve()
        except OSError:
            inside = False
        if not inside:
            raise HTTPException(
                status_code=400, detail="the copy must go in the folder that was picked"
            )
        if target.resolve() == src.resolve():
            raise HTTPException(status_code=400, detail="that is where the file already is")
        if target.exists() and not body.overwrite:
            raise HTTPException(
                status_code=409, detail=f"{target.name} is already in that folder"
            )
        try:
            shutil.copy2(src, target)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=plain_failure("The copy could not be saved.", exc))
        return {"path": str(target), "name": target.name, "folder": str(dest_dir)}

    # ---------------------------------------------------------- PII redaction ---
    # REPORTED: the redaction tool "didn't show me which items it recognized as
    # PII for my approval" and "routed to a folder without asking me where to
    # put this".
    #
    # Both were true, and both came from the same place: redaction was only ever
    # reachable through an AGENT. The confirm-first contract lived in a tool
    # description and a skill playbook — advice a model may or may not follow —
    # and the pii-redaction skill in fact jumped straight to redact_pii, so the
    # approval list was never shown and the destination defaulted silently.
    #
    # These two routes make the flow a real, deterministic UI: scan returns the
    # candidates, apply redacts EXACTLY the confirmed values to a destination the
    # user picked. No model in the loop, so the step cannot be skipped.

    def _redact_source(path: str) -> Path:
        # Resolve (expanduser + documents-join) BEFORE gating — see
        # _gated_source. Checking the raw string let '~/…' and '../secrets/…'
        # name a protected store the gate never saw.
        src = _gated_source(path, d.platform.config.home / "documents")
        if not src.is_file():
            raise HTTPException(status_code=404, detail=f"not a file: {path}")
        return src

    @app.post("/documents/redact/scan")
    async def documents_redact_scan(body: RedactScanBody) -> dict[str, Any]:
        import asyncio as _asyncio

        from ...documents.redact import ALL_CATEGORIES, scan_document

        src = _redact_source(body.path)
        cats = [str(c).strip().lower() for c in body.categories if str(c).strip()]
        unknown = [c for c in cats if c not in ALL_CATEGORIES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"unknown categories: {', '.join(unknown)} — "
                       f"valid: {', '.join(sorted(ALL_CATEGORIES))}",
            )
        try:
            findings = await _asyncio.to_thread(
                scan_document,
                src,
                extra_terms=[t for t in body.extra_terms if t.strip()],
                categories=set(cats) or None,
            )
        except Exception as exc:  # noqa: BLE001 — a bad file must not 500
            raise HTTPException(status_code=400, detail=plain_failure("That file could not be checked for what to redact.", exc))
        # The default destination is offered UP FRONT so the user can see and
        # change where the output will land before approving anything.
        default_out = src.with_name(f"{src.stem}.redacted{src.suffix}")
        return {
            "source": str(src),
            "name": src.name,
            "findings": findings,
            "default_output_path": str(default_out),
            "suffix": src.suffix.lower(),
        }

    @app.post("/documents/redact/apply")
    async def documents_redact_apply(body: RedactApplyBody) -> dict[str, Any]:
        import asyncio as _asyncio

        from ...core.fs_policy import is_protected_path
        from ...documents.redact import STYLES, redact_file

        src = _redact_source(body.path)
        style = (body.style or "black").strip().lower()
        if style not in STYLES:
            raise HTTPException(
                status_code=400, detail=f"unknown style {style!r} — use {', '.join(STYLES)}"
            )
        terms = [t for t in body.terms if t and t.strip()]
        if not terms:
            # An empty list would fall through to auto-detection in the engine
            # and redact things the user never ticked. Refuse instead.
            raise HTTPException(
                status_code=400,
                detail="no items confirmed — tick at least one finding to redact",
            )
        target = (
            Path(body.output_path).expanduser()
            if body.output_path.strip()
            else src.with_name(f"{src.stem}.redacted{src.suffix}")
        )
        if not target.is_absolute():
            raise HTTPException(status_code=400, detail="output_path must be absolute")
        if is_protected_path(target):
            raise HTTPException(status_code=403, detail="destination is a protected path")
        if target.resolve() == src.resolve():
            raise HTTPException(
                status_code=400,
                detail="destination must differ from the source — the original is never overwritten",
            )
        if target.exists() and not body.overwrite:
            raise HTTPException(
                status_code=409, detail=f"{target.name} already exists at that location"
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            counts, note = await _asyncio.to_thread(
                redact_file, src, target, style=style, only_terms=terms
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=plain_failure("That file could not be redacted. The original is unchanged.", exc))
        return {
            "path": str(target),
            "name": target.name,
            "source": str(src),
            "style": style,
            "counts": counts,
            "total": sum(counts.values()),
            "note": note,
        }

    @app.post("/documents/write")
    def documents_write(body: DocWriteBody) -> dict[str, Any]:
        from ...documents import write_document

        base = (d.platform.config.home / "documents").resolve()
        target = (base / body.path).resolve()
        if target != base and not target.is_relative_to(base):
            raise HTTPException(status_code=400, detail="path escapes documents dir")
        out = write_document(target, body.content, kind=body.kind)
        return {
            "path": str(out.relative_to(base)).replace("\\", "/"),
            "bytes": out.stat().st_size,
        }

    # v1.560.0 (idea 31-a): a sorting plan's card — its own module.
    from . import stack as _stack_routes

    _stack_routes.register(app, d)
