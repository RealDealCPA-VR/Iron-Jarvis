"""Creative routes: the gallery, media file serving, Pixio publish/upload.

The gallery lists media ARTIFACTS (pixio generations save into the store via
the artifact sink; screenshots and uploads live there too). File serving
carries proper content-types so the dashboard can render <img>/<video>/<audio>
directly (the token middleware already accepts ?token= for exactly this).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from ..schemas import (
    CreativePublishBody,
    CreativeUploadBody,
    StudioSayBody,
    StudioStartBody,
)
from ...core.fs_policy import fs_read_ok
from ...creative.service import list_media, media_kind, mime_for

#: Per-CLI "run without permission prompts" flags for studio autopilot. Only
#: CLIs listed here get a flag — unknown ones launch plain (still driveable).
_AUTOPILOT_FLAGS = {
    "claude": "--dangerously-skip-permissions",
    "codex": "--full-auto",
}

#: Media-generation skills the studio brief points the CLI at when the user
#: picks "Auto" — the CLI (e.g. Claude Code) discovers these from
#: ~/.claude/skills on this machine and routes by description.
_AUTO_SKILL_HINT = (
    "Pick the best media-generation skill you have for what I describe "
    "(pixio-story for narrative video, seedance-storyboard for cinematic "
    "clips, pixio-song for music, pixio-skill for single images/audio/video)."
)

_MISSING_KEY = (
    "Pixio isn't connected — add your key on the Connections page (or a secret "
    "named 'pixio') to publish media."
)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _pixio_key() -> str | None:
        try:
            key = d.platform.secrets.get("pixio")
        except Exception:  # noqa: BLE001 — vault miss = not configured
            key = None
        return key or os.environ.get("PIXIO_API_KEY") or None

    @app.get("/creative/items")
    def creative_items(limit: int = 200) -> dict[str, Any]:
        """The gallery: every media artifact, newest first."""
        items = list_media(d.platform, limit=limit)
        return {"items": items, "count": len(items)}

    @app.get("/creative/file/{name}")
    def creative_file(name: str, version: int | None = None):
        """Serve one gallery item's bytes with its real content-type.
        <img src>/<video src> can't send an Authorization header — the token
        middleware accepts ?token= (same pattern as the /events WebSocket)."""
        path = d.platform.artifacts.version_path(name, version)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="no such media")
        if media_kind(path.name) is None:
            raise HTTPException(status_code=415, detail="not a media artifact")
        return FileResponse(path, media_type=mime_for(path.name))

    @app.get("/creative/file-by-path")
    def creative_file_by_path(path: str):
        """Serve a LOCAL media file (chat replies embed generated media by its
        absolute workspace path). Media extensions only + the fs policy guard —
        never a vault key or arbitrary file."""
        p = Path((path or "").strip())
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail="absolute path required")
        if media_kind(p.name) is None:
            raise HTTPException(status_code=415, detail="not a media file")
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=f"blocked: {reason}")
        if not p.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        return FileResponse(p, media_type=mime_for(p.name))

    @app.post("/creative/publish")
    async def creative_publish(body: CreativePublishBody) -> dict[str, Any]:
        """Publish media to Pixio's CDN → a clean, PERMANENT, PUBLIC url usable
        directly in generation params. Source: gallery name, local path, or a
        remote url to mirror. Honest 424 when Pixio isn't connected."""
        import asyncio

        from ...tools.pixio import pixio_publish

        key = _pixio_key()
        if not key:
            raise HTTPException(status_code=424, detail=_MISSING_KEY)
        sources = [bool(body.name.strip()), bool(body.path.strip()), bool(body.url.strip())]
        if sum(sources) != 1:
            raise HTTPException(
                status_code=400, detail="give exactly one of name, path, or url"
            )
        endpoint = "images" if body.endpoint == "images" else "media"
        try:
            if body.url.strip():
                url = await asyncio.to_thread(
                    pixio_publish, key, url=body.url.strip(), endpoint=endpoint
                )
                return {"url": url}
            if body.name.strip():
                p = d.platform.artifacts.version_path(body.name.strip(), body.version)
                if p is None or not p.is_file():
                    raise HTTPException(status_code=404, detail="no such media")
            else:
                p = Path(body.path.strip())
                if not p.is_absolute() or not p.is_file():
                    raise HTTPException(status_code=404, detail="no such file")
                ok, reason = fs_read_ok(str(p))
                if not ok:
                    raise HTTPException(status_code=403, detail=f"blocked: {reason}")
            if media_kind(p.name) is None:
                raise HTTPException(
                    status_code=415,
                    detail="only image/video/audio may be published to the public CDN",
                )
            url = await asyncio.to_thread(
                pixio_publish,
                key,
                blob=p.read_bytes(),
                filename=p.name,
                mime=mime_for(p.name),
                endpoint=endpoint,
            )
            return {"url": url}
        except RuntimeError as exc:  # honest Pixio-side failure
            raise HTTPException(status_code=424, detail=str(exc))

    # --- Creative Studio: drive an AI CLI from the Creative page -----------
    # The user's real creative workflow is a CLI (Claude Code + the pixio
    # skills). The studio opens a MANAGED terminal (it appears on the Build
    # page like any other), launches the chosen CLI in the chosen destination
    # folder, and relays chat-style messages into it.

    @app.post("/creative/studio/start")
    def studio_start(body: StudioStartBody) -> dict[str, Any]:
        from ...terminals.ai_clis import detect_ai_clis

        cwd = Path((body.cwd or "").strip())
        if not cwd.is_absolute() or not cwd.is_dir():
            raise HTTPException(status_code=400, detail="cwd must be an existing folder")
        cli = next((c for c in detect_ai_clis() if c["id"] == body.cli), None)
        if cli is None:
            raise HTTPException(status_code=404, detail=f"unknown CLI '{body.cli}'")
        if not cli.get("installed"):
            raise HTTPException(
                status_code=424,
                detail=f"{cli['label']} isn't installed on this machine ({cli['url']})",
            )
        command = str(cli["command"]).strip()
        flag = _AUTOPILOT_FLAGS.get(body.cli, "") if body.autopilot else ""
        if flag:
            command = f"{command} {flag}"
        try:
            session = d.platform.terminals.create(cwd=str(cwd))
        except RuntimeError as exc:  # session cap reached
            raise HTTPException(status_code=429, detail=str(exc))
        # Type the launch into the shell ("\r" = Enter, same as a keystroke).
        session.write(command + "\r")
        return {
            "terminal_id": session.id,
            "command": command,
            "cwd": str(cwd),
            "autopilot": bool(flag),
            "cli": cli["label"],
        }

    @app.post("/creative/studio/{terminal_id}/say")
    def studio_say(terminal_id: str, body: StudioSayBody) -> dict[str, Any]:
        session = d.platform.terminals.get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        if not session.alive:
            raise HTTPException(status_code=409, detail="the terminal has exited")
        # Newlines would submit a CLI prompt early — flatten to one line.
        text = " ".join((body.text or "").split())
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        if body.first:
            skill = (body.skill or "").strip()
            skill_line = (
                f"Use your '{skill}' skill." if skill else _AUTO_SKILL_HINT
            )
            where = (body.save_dir or "").strip() or "the current working directory"
            text = (
                f"{skill_line} Save every final media file into {where} (you are "
                "already in it). Work autonomously until the generation is fully "
                "complete — make reasonable creative choices instead of asking me "
                f"questions. Here is the brief: {text}"
            )
        session.write(text + "\r")
        return {"typed": True, "chars": len(text)}

    @app.get("/creative/studio/{terminal_id}/tail")
    def studio_tail(terminal_id: str, chars: int = 4000) -> dict[str, Any]:
        """Clean (ANSI-stripped) recent output for the studio's console preview
        — the full interactive pane lives on the Build page."""
        session = d.platform.terminals.get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        chars = max(200, min(int(chars), 32_000))
        return {
            "tail": session.output_tail()[-chars:],
            "alive": session.alive,
            "exit_code": session.exit_code,
        }

    @app.post("/creative/upload")
    async def creative_upload(body: CreativeUploadBody) -> dict[str, Any]:
        """Add a media file to the gallery (durable, versioned); optionally
        also publish it to Pixio's CDN for a permanent public url."""
        name = Path(body.filename).name.strip()
        if not name or media_kind(name) is None:
            raise HTTPException(
                status_code=415, detail="only image/video/audio files belong in the gallery"
            )
        try:
            blob = base64.b64decode(body.content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
        if not blob:
            raise HTTPException(status_code=400, detail="empty file")
        from .. import app as _app

        if len(blob) > _app._MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
        artifact = d.platform.artifacts.save(
            f"upload-{Path(name).stem}", blob, kind=media_kind(name) or "file", filename=name
        )
        out: dict[str, Any] = {
            "name": artifact.name,
            "version": artifact.version,
            "media": media_kind(name),
            "size": artifact.size,
        }
        if body.publish:
            import asyncio

            from ...tools.pixio import pixio_publish

            key = _pixio_key()
            if not key:
                out["publish_error"] = _MISSING_KEY
                return out
            try:
                out["url"] = await asyncio.to_thread(
                    pixio_publish, key, blob=blob, filename=name, mime=mime_for(name)
                )
            except RuntimeError as exc:
                out["publish_error"] = str(exc)
        return out
