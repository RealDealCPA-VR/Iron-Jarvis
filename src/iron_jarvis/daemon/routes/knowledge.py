"""Knowledge routes: artifacts, file search, long-term memory sources.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import httpx

from fastapi import FastAPI, HTTPException
from pathlib import Path
from typing import Any

from .. import app as _app
from ..schemas import IngestDocumentBody, LTMAppend, LTMSourceBody
from ...core.fs_policy import fs_read_ok, is_protected_path


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/artifacts")
    def artifacts(session_id: str = "") -> dict[str, Any]:
        """All artifact names; or — with ?session_id= — the artifacts a specific
        session GENERATED (so a project task can show what it produced),
        newest first with media flags for the gallery/lightbox."""
        sid = session_id.strip()
        if not sid:
            return {"artifacts": d.platform.artifacts.list_names()}
        from sqlmodel import select

        from ...artifacts.models import ArtifactRecord
        from ...core.db import session_scope
        from ...creative.service import media_kind

        with session_scope(d.platform.engine) as db:
            rows = list(
                db.exec(
                    select(ArtifactRecord)
                    .where(ArtifactRecord.session_id == sid)
                    .order_by(ArtifactRecord.created_at.desc())  # type: ignore[attr-defined]
                )
            )
        seen: set[str] = set()
        items = []
        for r in rows:
            if r.name in seen:  # one card per artifact name (store versions it)
                continue
            seen.add(r.name)
            from pathlib import Path as _P

            items.append(
                {
                    "name": r.name,
                    "version": r.version,
                    "kind": r.kind,
                    "filename": _P(r.path).name,
                    "media": media_kind(r.path)
                    or ("image" if r.kind == "screenshot" else None),
                    "size": r.size,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "url": f"/creative/file/{r.name}",
                }
            )
        return {"artifacts": items, "session_id": sid}

    #: Never stringify an artifact bigger than this. The store holds generated
    #: MEDIA (mp4/png/mp3), and this endpoint used to hand every byte back as
    #: text: a 65 MB video became a 155 MB JSON body of U+FFFD replacement
    #: characters, which froze the browser laying it out. Text artifacts are
    #: kilobytes, so a 1 MB ceiling costs nothing real.
    _ARTIFACT_TEXT_MAX = 1_000_000

    @app.get("/artifacts/{name}")
    def artifact(name: str) -> dict[str, Any]:
        """One artifact's metadata, plus its content ONLY when it is genuinely
        text of sane size. ``content`` is null otherwise and ``content_note``
        says why — binary media is served properly by ``/creative/file/{name}``,
        and code lives in ``/code-artifacts``."""
        art = d.platform.artifacts.latest(name)
        if art is None:
            raise HTTPException(status_code=404, detail="no such artifact")
        content: str | None = None
        note = ""
        if art.size > _ARTIFACT_TEXT_MAX:
            note = (
                f"{art.size} bytes — too large to show as text; "
                f"fetch /creative/file/{name} instead"
            )
        else:
            raw = b""
            try:
                raw = d.platform.artifacts.read(name)
            except Exception:  # noqa: BLE001 — unreadable file, report honestly
                note = "artifact could not be read"
            if not note:
                # STRICT decode: unlike decode(..., "replace") this actually
                # RAISES on binary, which is the signal we want. "replace"
                # silently produced megabytes of garbage and made the old
                # except-branch below dead code.
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    note = f"binary artifact ({art.size} bytes) — not text"
        return {
            "name": art.name,
            "version": art.version,
            "size": art.size,
            "versions": d.platform.artifacts.versions(name),
            "content": content,
            "content_note": note,
        }

    @app.get("/filesearch/drives")
    def filesearch_drives() -> dict[str, Any]:
        from ...filesearch.service import list_drives

        return {"drives": list_drives()}

    @app.get("/filesearch")
    def filesearch(
        q: str, mode: str = "content", limit: int = 50, root: str | None = None
    ) -> dict[str, Any]:
        if root:
            ok, reason = fs_read_ok(root)
            if not ok:
                raise HTTPException(status_code=403, detail=reason)
        roots = [Path(root)] if root else None
        results = d.platform.filesearch.search(q, mode=mode, limit=limit, roots=roots)
        # Filter protected/out-of-allowlist hits (a default-root search can reach
        # them) — same as the agent file_search tool.
        results = [
            r
            for r in results
            if not is_protected_path(r.get("path", "")) and fs_read_ok(r.get("path", ""))[0]
        ]
        return {"results": results}

    @app.get("/ltm/search")
    def ltm_search(q: str, source: str | None = None, k: int = 5) -> dict[str, Any]:
        try:
            return {"results": d.platform.ltm.search(q, k=k, source=source)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/ltm/append")
    def ltm_append(body: LTMAppend) -> dict[str, Any]:
        try:
            src = body.source or d.platform.ltm.default_source()
            ref = d.platform.ltm.append(body.title, body.content, source=src)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ref": ref, "source": src}

    @app.post("/ltm/ingest-document")
    def ltm_ingest_document(body: IngestDocumentBody) -> dict[str, Any]:
        """Convert an uploaded document (PDF/office/HTML/text) to clean Markdown
        and store it DURABLY in long-term memory — so a PDF becomes a searchable
        knowledge-base note, not throwaway chat grounding. Structure-preserving
        for PDFs (markitdown); falls back to flattened text on any converter issue.
        """
        import base64
        import re
        import tempfile
        from pathlib import Path as _Path

        from ...documents import document_to_markdown

        approx_bytes = (len(body.content_b64) * 3) // 4
        if approx_bytes > _app._MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"document too large (~{approx_bytes // (1024 * 1024)} MB); "
                    f"limit is {_app._MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
                ),
            )
        safe_name = (
            re.sub(r"[^A-Za-z0-9._-]", "_", body.filename).strip("._") or "document"
        )
        try:
            data = base64.b64decode(body.content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")

        tmpdir = tempfile.mkdtemp(prefix="ij-ingest-")
        tmp = _Path(tmpdir) / safe_name
        try:
            tmp.write_bytes(data)
            markdown = document_to_markdown(tmp)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"could not convert document: {exc}"
            )
        finally:
            try:
                tmp.unlink(missing_ok=True)
                _Path(tmpdir).rmdir()
            except OSError:
                pass

        if not markdown.strip():
            raise HTTPException(
                status_code=422,
                detail="no extractable text in document (scanned image PDF?)",
            )
        title = body.title.strip() or _Path(safe_name).stem
        try:
            src = body.source or d.platform.ltm.default_source()
            ref = d.platform.ltm.append(title, markdown, source=src)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "ref": ref,
            "source": src,
            "title": title,
            "chars": len(markdown),
        }

    @app.get("/ltm/browse")
    def ltm_browse(source: str = "", limit: int = 30) -> dict[str, Any]:
        """Recent/enumerable items from ONE long-term source: an MCP brain's
        list tool, or a markdown vault's files. Search-only sources return an
        honest note instead of pretending to be empty."""
        ltm = d.platform.ltm
        name = (source or "").strip() or ltm.default_source()
        conn = ltm.get(name) if name else None
        if conn is None:
            raise HTTPException(status_code=404, detail=f"no such source: {name}")
        cap = max(1, min(int(limit or 30), 100))
        lister = getattr(conn, "list_items", None)
        if callable(lister):
            try:
                return {"source": name, "items": lister(limit=cap), "note": ""}
            except Exception as exc:  # noqa: BLE001 — honest per-source failure
                return {"source": name, "items": [], "note": str(exc)}
        files = getattr(conn, "_files", None)
        read = getattr(conn, "_read", None)
        if callable(files) and callable(read):
            items: list[dict[str, Any]] = []
            try:
                for p in list(files())[:cap]:
                    try:
                        text = read(p) or ""
                    except Exception:  # noqa: BLE001
                        continue
                    items.append(
                        {
                            "title": str(getattr(p, "stem", p)),
                            "snippet": text[:300],
                            "ref": str(p),
                            "source": name,
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                return {"source": name, "items": [], "note": str(exc)}
            return {"source": name, "items": items, "note": ""}
        return {
            "source": name,
            "items": [],
            "note": (
                f"{name} is search-only — it can't list its items. "
                "Recall search still reaches it."
            ),
        }

    @app.get("/ltm/sources")
    def ltm_sources() -> dict[str, Any]:
        """Registered memory sources + the LIVE bases.

        v1.172.0 ADDITIVE ``bases``: each active connector with whether it can
        actually be read right now. A base whose folder moved used to be
        indistinguishable from a base with no matches — the app answered from
        nothing and looked healthy doing it. ``sources`` and ``active`` are
        unchanged for existing callers.
        """
        from ...ltm.sources import CustomSourceStore

        bases: list[dict[str, Any]] = []
        for base_name in d.platform.ltm.sources():
            conn = d.platform.ltm.get(base_name)
            health = getattr(conn, "health", None)
            info: dict[str, Any] = {"name": base_name, "kind": getattr(conn, "name", "")}
            if callable(health):
                try:
                    info.update(health())
                except Exception as exc:  # noqa: BLE001 — a listing never 500s
                    info.update({"available": False, "detail": f"{type(exc).__name__}: {exc}"})
            else:
                # Remote kinds (Notion/cloud/http_rag) have no cheap local
                # probe; claim nothing rather than a comforting default.
                info.update({"available": None, "detail": ""})
            bases.append(info)
        return {
            "sources": [s.model_dump() for s in CustomSourceStore(d.platform.engine).list()],
            "active": d.platform.ltm.sources(),
            "bases": bases,
        }

    @app.post("/ltm/sources")
    def add_ltm_source(body: LTMSourceBody) -> dict[str, Any]:
        import re

        from ...ltm.sources import CustomSourceStore, connector_from_record

        import json

        store = CustomSourceStore(d.platform.engine)
        _slug = re.sub(r"[^a-zA-Z0-9_]+", "_", body.name.strip().lower())
        # A NEW secret (SSH password / http_rag bearer) is stored in the ENCRYPTED
        # vault (never in the DB); only its secret NAME is persisted on the record.
        token_secret = body.token_secret
        if body.kind == "ssh" and body.password.strip():
            token_secret = f"ltm_{_slug}_ssh"
            d.platform.secrets.set(token_secret, body.password.strip(), kind="token")
        elif body.kind == "http_rag" and body.token.strip():
            token_secret = f"ltm_{_slug}_http_rag"
            d.platform.secrets.set(token_secret, body.token.strip(), kind="token")
        elif body.kind == "notion" and body.token.strip():
            # Notion gets the same one-step setup as ssh/http_rag: paste the
            # integration token inline, it lands in the vault, only the secret
            # NAME persists. (Previously Notion alone required pre-creating a
            # secret on the Secrets page and referencing it by name.)
            token_secret = f"ltm_{_slug}_notion"
            d.platform.secrets.set(token_secret, body.token.strip(), kind="token")
        elif body.kind == "mcp" and body.token.strip():
            # An MCP brain's bearer token (pasted or parsed out of a Claude-
            # Desktop-style config) — vaulted, never on the record/config.toml.
            token_secret = f"ltm_{_slug}_mcp"
            d.platform.secrets.set(token_secret, body.token.strip(), kind="token")
        try:
            rec = store.add(
                body.name,
                body.kind,
                path=body.path,
                database_id=body.database_id,
                token_secret=token_secret,
                host=body.host,
                port=body.port,
                username=body.username,
                key_path=body.key_path,
                endpoint_url=body.endpoint_url,
                config_json=json.dumps(body.config) if body.config else "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        try:  # register it live so it's searchable without a restart
            conn = connector_from_record(
                rec,
                secret_resolver=d.platform.secrets.get,
                http_factory=lambda: httpx.Client(timeout=30),
                credential_resolver=d.platform.connections.credential,
                # The SHARED embedder (same as the boot-time sources) — falling
                # back to memory's mock only when the platform predates the field.
                embedder=(
                    getattr(d.platform, "embedder", None)
                    or getattr(getattr(d.platform, "memory", None), "embedder", None)
                ),
            )
            d.platform.ltm.register(conn)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400, detail=f"source saved but not loadable: {exc}"
            )
        return {"name": rec.name, "kind": rec.kind}

    @app.delete("/ltm/sources/{name}")
    def remove_ltm_source(name: str) -> dict[str, Any]:
        from ...ltm.sources import CustomSourceStore

        return {"removed": CustomSourceStore(d.platform.engine).remove(name)}
