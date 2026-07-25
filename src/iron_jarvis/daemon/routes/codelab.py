"""Code Lab routes (v1.95.0) — browse and RE-RUN what agents built.

``run_code`` scripts used to die with the session workspace. These endpoints
expose the durable :class:`~iron_jarvis.codelab.store.CodeArtifactStore` so the
Artifacts page can list saved scripts, read their source, run them again, and
delete them.

Re-run executes real code, so it is deliberately EXPLICIT: nothing here runs on
its own, on a schedule, or as a side effect of listing. A run happens only when
the user asks for one — the same consent model as the terminals surface, on a
daemon that is already loopback-bound and token-guarded.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from ..schemas import CodeArtifactRun, CodeArtifactSave


def _as_dict(rec, *, source: bool = False) -> dict[str, Any]:
    """List/detail shape. ``source`` is omitted from list rows — 118 scripts
    with their bodies inline is the payload mistake the media artifacts page
    made, and there is no reason to repeat it."""
    out = {
        "id": rec.id,
        "name": rec.name,
        "language": rec.language,
        "description": rec.description,
        "origin": rec.origin,
        "session_id": rec.session_id,
        "project_id": rec.project_id,
        "run_count": rec.run_count,
        "last_exit_code": rec.last_exit_code,
        "last_run_at": rec.last_run_at.isoformat() if rec.last_run_at else None,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
        "size": len(rec.source or ""),
    }
    if source:
        out["source"] = rec.source
        out["last_output"] = rec.last_output
    return out


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    @app.get("/code-artifacts")
    def list_code_artifacts(project_id: str = "") -> dict[str, Any]:
        """Saved scripts, most recently run first. Source bodies are NOT
        included — fetch one to read it."""
        rows = d.platform.code_artifacts.list(project_id.strip() or None)
        return {"artifacts": [_as_dict(r) for r in rows], "count": len(rows)}

    @app.get("/code-artifacts/{artifact_id}")
    def get_code_artifact(artifact_id: str) -> dict[str, Any]:
        """One saved script including its source and last output."""
        rec = d.platform.code_artifacts.get(artifact_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such code artifact")
        return _as_dict(rec, source=True)

    @app.post("/code-artifacts")
    def save_code_artifact(body: CodeArtifactSave) -> dict[str, Any]:
        """Save a script by hand (origin "manual") — the user writing their own
        mini-app, or keeping one an agent produced elsewhere."""
        if not (body.source or "").strip():
            raise HTTPException(status_code=400, detail="source is required")
        rec = d.platform.code_artifacts.save(
            body.name,
            body.language,
            body.source,
            description=body.description or "",
            origin="manual",
            project_id=(body.project_id or None),
            count_run=False,  # saving is not running — don't claim it ran
        )
        return _as_dict(rec, source=True)

    @app.post("/code-artifacts/{artifact_id}/run")
    async def run_code_artifact(artifact_id: str, body: CodeArtifactRun | None = None) -> dict[str, Any]:
        """RE-RUN a saved script and record the outcome.

        Runs in the artifact's own durable folder (``<home>/codelab/<id>``), NOT
        the long-deleted session workspace, so files it writes persist between
        runs. Returns the real exit code and output — a failed script reports
        its failure rather than being dressed up as success.
        """
        store = d.platform.code_artifacts
        rec = store.get(artifact_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such code artifact")

        from ...tools.runcode import ScriptRunFailed, execute_script

        cwd = d.platform.config.codelab_dir / rec.id
        timeout = (body.timeout_s if body and body.timeout_s else 60)
        try:
            rc, output = await execute_script(
                rec.language, rec.source, cwd, timeout_s=timeout
            )
        except ScriptRunFailed as exc:
            # Could not even start (no interpreter, timeout, missing shell).
            # 400, and recorded so the page shows WHY the last run failed.
            store.record_run(artifact_id, -1, f"[not run] {exc}")
            raise HTTPException(status_code=400, detail=str(exc))
        updated = store.record_run(artifact_id, rc, output)
        return {
            "ok": rc == 0,
            "exit_code": rc,
            "output": output,
            "cwd": str(cwd),
            "artifact": _as_dict(updated or rec, source=True),
        }

    @app.delete("/code-artifacts/{artifact_id}")
    def delete_code_artifact(artifact_id: str) -> dict[str, Any]:
        """Forget a saved script. Its codelab working folder is left alone —
        files it produced are the user's, not ours to delete."""
        if not d.platform.code_artifacts.delete(artifact_id):
            raise HTTPException(status_code=404, detail="no such code artifact")
        return {"deleted": artifact_id}
