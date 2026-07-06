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
    def artifacts() -> dict[str, Any]:
        return {"artifacts": d.platform.artifacts.list_names()}

    @app.get("/artifacts/{name}")
    def artifact(name: str) -> dict[str, Any]:
        art = d.platform.artifacts.latest(name)
        if art is None:
            raise HTTPException(status_code=404, detail="no such artifact")
        try:
            content = d.platform.artifacts.read(name).decode("utf-8", "replace")
        except Exception:
            content = None
        return {
            "name": art.name,
            "version": art.version,
            "size": art.size,
            "versions": d.platform.artifacts.versions(name),
            "content": content,
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

    @app.get("/ltm/sources")
    def ltm_sources() -> dict[str, Any]:
        from ...ltm.sources import CustomSourceStore

        return {
            "sources": [s.model_dump() for s in CustomSourceStore(d.platform.engine).list()],
            "active": d.platform.ltm.sources(),
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
