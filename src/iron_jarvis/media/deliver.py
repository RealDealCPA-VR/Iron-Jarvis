"""Landing finished media (v1.346.0) — the delivery path LIFTED out of
``tools/pixio.py``'s ``_deliver`` so every provider lands files the same way.

Two copies of a generation: a WORKSPACE file (``media/<provider>/<job>.<ext>``
by default — the pixio tools keep their documented ``pixio/<id>`` folder via
``rel_dir``) that downstream tools and the chat's files rail reach by absolute
path, and a DURABLE copy in the Creative gallery through the artifact sink
(``ArtifactStore.save`` in production: fires ``artifact.generated``, so the
gallery updates live; tagged with the project when the producing context
carries one). The workspace copy is disposable; without the gallery copy a
creation would vanish with the session.

SYNC on purpose: the callers are async and run it through ``asyncio.to_thread``
(a 50 MB video write must not sit on the event loop).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .pixio_http import safe_name

#: (artifact_name, blob, filename, kind, session_id, *, project_id=...) — the
#: durable gallery sink. ``project_id`` rides as a KEYWORD, and only when the
#: producing context carries one, so a five-arg sink double keeps working.
ArtifactSink = Callable[..., Any]


def deliver(
    data: bytes,
    *,
    kind: str,
    provider: str,
    job_id: str,
    ext: str,
    workspace: Path,
    session_id: str | None,
    project_id: str | None = None,
    artifact_sink: ArtifactSink | None = None,
    rel_dir: str | None = None,
) -> dict[str, Any]:
    """Write ``data`` under the workspace and copy it to the gallery.

    Returns ``{"abs_path", "rel", "artifact", "created"}``: ``abs_path`` is
    the RESOLVED absolute path (chat drops relative ``created_paths`` by
    design, and the workspace itself may be relative); ``rel`` the workspace-
    relative path; ``artifact`` the gallery name ("" when no sink or the sink
    failed — the gallery is a bonus, never a reason to lose the file);
    ``created`` whether THIS call brought the file into existence (a
    re-delivery over an existing file must not be journaled as a creation).
    """
    from ..creative.service import media_kind

    folder = rel_dir if rel_dir is not None else f"media/{safe_name(provider or 'media')}"
    suffix = ext if ext.startswith(".") else (f".{ext}" if ext else ".bin")
    rel = f"{folder}/{safe_name(job_id)}{suffix}".replace("//", "/")
    dest = (Path(workspace) / rel).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    existed = dest.exists()
    dest.write_bytes(data)
    artifact = ""
    if artifact_sink is not None:
        try:
            artifact = f"creative-{safe_name(job_id)}"
            sink_kwargs: dict[str, Any] = {}
            if project_id:
                sink_kwargs["project_id"] = project_id
            artifact_sink(
                artifact,
                data,
                dest.name,
                media_kind(dest.name) or kind or "file",
                session_id,
                **sink_kwargs,
            )
        except Exception:  # noqa: BLE001 — the gallery is a bonus, never break delivery
            artifact = ""
    return {"abs_path": str(dest), "rel": rel, "artifact": artifact, "created": not existed}
