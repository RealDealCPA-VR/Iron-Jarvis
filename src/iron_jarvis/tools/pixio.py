"""Pixio generative-media tools — the creative arm (§19 tool interface).

Wires the Pixio API (``https://beta.pixio.myapps.ai``) into the agent runtime
so any session — on ANY LLM provider — can generate images, video, and audio.
The tool flow mirrors the user's ``pixio-skill`` (the agent-facing know-how
lives there): discover models (``pixio_models``), read a model's accepted
params (``pixio_params``) — NEVER invent model ids or params — start a
generation (``pixio_generate``), and poll / collect it (``pixio_status``).
Finished media is downloaded into the session workspace under ``pixio/`` so
downstream tools (read_file, documents, comm attachments) can pick it up.

Auth: every call sends ``Authorization: Bearer <key>`` (keys look like
``pxio_live_...``). The key comes from an injected ``key_resolver`` closure —
the platform passes one over the secrets vault (secret name ``pixio``); the
default resolver falls back to the ``PIXIO_API_KEY`` env var. Default accounts
allow ONE in-flight generation, so 429 is surfaced as a clear one-liner
instead of being retried blindly.

v1.346.0: the HTTP transport, the error map, ``pixio_publish`` and the
delivery path live in :mod:`iron_jarvis.media.pixio_http` and
:mod:`iron_jarvis.media.deliver` — shared with the ``generate_media`` tool's
:class:`~iron_jarvis.media.pixio.PixioProvider`. The old private names are
re-exported here because the avatar and creative routes import them.

Testability: the HTTP transport is dependency-injected (same pattern as
:mod:`iron_jarvis.tools.websearch`). The constructor takes an optional
``http(method, url, headers, json_body) -> response-like`` (anything with
``.status_code``, ``.content`` and ``.json()``); the production default lazily
imports ``httpx``, so this module imports clean without it. Tests inject a
fake that scripts responses — no network.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import quote

from ..media.base import fetch_public
from ..media.deliver import ArtifactSink, deliver
from ..media.pixio_http import BASE_URL as _BASE_URL
from ..media.pixio_http import MISSING_KEY_ERROR as _MISSING_KEY_ERROR
from ..media.pixio_http import (
    HttpRequest,
    HttpUpload,
    KeyResolver,
    api_call,
    pixio_publish,
)
from ..media.pixio_http import default_http as _default_http
from ..media.pixio_http import default_http_upload as _default_http_upload
from ..media.pixio_http import detail as _detail
from ..media.pixio_http import env_key_resolver as _env_key_resolver
from ..media.pixio_http import ext_from_url as _ext_from_url
from ..media.pixio_http import http_error as _http_error
from ..media.pixio_http import output_url as _output_url
from ..media.pixio_http import safe_name as _safe_name
from .base import Tool, ToolContext, ToolResult

__all__ = [
    "ArtifactSink",
    "HttpRequest",
    "HttpUpload",
    "KeyResolver",
    "PixioGenerateTool",
    "PixioModelsTool",
    "PixioParamsTool",
    "PixioStatusTool",
    "PixioUploadTool",
    "pixio_publish",
    "pixio_tools",
    "_BASE_URL",
    "_MISSING_KEY_ERROR",
    "_default_http",
    "_default_http_upload",
    "_detail",
    "_env_key_resolver",
    "_ext_from_url",
    "_http_error",
    "_output_url",
    "_safe_name",
]

_POLL_SECONDS = 5.0
_DEFAULT_TIMEOUT_SECONDS = 600


class _PixioTool(Tool):
    """Shared plumbing: key resolution, authenticated round-trips, result delivery."""

    permission_key = "pixio"  # one switch governs the whole creative group

    def __init__(
        self,
        key_resolver: KeyResolver | None = None,
        http: HttpRequest | None = None,
        artifact_sink: ArtifactSink | None = None,
    ) -> None:
        self._key_resolver: KeyResolver = key_resolver or _env_key_resolver
        self._http: HttpRequest = http or _default_http
        self._artifact_sink = artifact_sink

    def _key(self) -> str | None:
        try:
            return self._key_resolver() or None
        except Exception:  # noqa: BLE001 — a flaky resolver must not crash the tool
            return None

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        key = self._key()
        if not key:
            return ToolResult(ok=False, error=_MISSING_KEY_ERROR)
        try:
            return await self._run(key, args, ctx)
        except Exception as exc:  # noqa: BLE001 — a network fault must not crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def _run(self, key: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError

    async def _api(
        self, method: str, path: str, key: str, json_body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """One authenticated round-trip → ``(status_code, parsed-json)``. The transport
        is a SYNC callable (httpx by default), so it runs off the event loop — a slow
        Pixio round-trip must not freeze the daemon (``media.pixio_http.api_call``)."""
        answer = await api_call(self._http, method, path, key, json_body)
        return answer.status, answer.payload

    async def _deliver(
        self, generation_id: str, payload: dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        """A generation SUCCEEDED — download its output into the session workspace.

        The download and the write run OFF the loop; the landing itself is
        :func:`iron_jarvis.media.deliver.deliver` (shared with ``generate_media``),
        kept under the documented ``pixio/<id>.<ext>`` folder via ``rel_dir``.
        """
        url = _output_url(payload)
        if not url:
            return ToolResult(
                ok=False,
                error=f"generation {generation_id} succeeded but the result has no outputUrl",
                data={"generation_id": generation_id, "status": "succeeded"},
            )
        # Output URLs are public CDN links — never send the bearer key to a
        # third-party host (fetch_public sends NO headers).
        try:
            blob = await asyncio.to_thread(fetch_public, url, self._http)
        except Exception as exc:  # noqa: BLE001 — a failed download is a plain error
            return ToolResult(
                ok=False,
                error=f"{exc} for {url}",
                data={"generation_id": generation_id, "output_url": url, "status": "succeeded"},
            )
        landed = await asyncio.to_thread(
            deliver,
            blob,
            kind="",
            provider="pixio",
            job_id=generation_id,
            ext=_ext_from_url(url),
            workspace=ctx.workspace,
            session_id=ctx.session_id,
            # v1.200.0: tag the artifact with the grounded project so it shows in
            # that project's Media view (getattr keeps old ToolContext doubles working).
            project_id=getattr(ctx, "project_id", None),
            artifact_sink=self._artifact_sink,
            rel_dir="pixio",
        )
        rel = landed["rel"]
        dest = landed["abs_path"]
        artifact_name = landed["artifact"]
        gallery_note = " — added to the Creative gallery" if artifact_name else ""
        return ToolResult(
            ok=True,
            output=(
                f"generation {generation_id} succeeded — saved {rel} "
                f"({len(blob)} bytes){gallery_note}\n{url}\n"
                f"To show it to the user inline, include this markdown in your "
                f"reply: ![generated media]({dest})"
            ),
            data={
                "generation_id": generation_id,
                "output_url": url,
                "saved_path": rel,
                "abs_path": dest,
                **({"artifact": artifact_name} if artifact_name else {}),
                "status": "succeeded",
            },
            # Created-files contract (v1.166.0): the downloaded media, absolute
            # (matches abs_path), success-only — the pending/failed returns
            # never carry it because nothing was written. Only when this call
            # brought the file into existence (a re-delivery over an existing
            # file journaled as "created" would let undo unlink it).
            created_paths=[dest] if landed["created"] else None,
        )

    @staticmethod
    def _failed(generation_id: str, payload: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=False,
            error=f"generation failed: {_detail(payload) or 'no error detail from Pixio'}",
            data={"generation_id": generation_id, "status": "failed"},
        )


class PixioModelsTool(_PixioTool):
    name = "pixio_models"
    description = (
        "List the Pixio generative-media models (image/video/audio) visible to this "
        "account. Always pick a model id from this list — never invent one. Then call "
        "pixio_params for its accepted params before generating."
    )
    input_schema = {"type": "object", "properties": {}}

    async def _run(self, key: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        status, payload = await self._api("GET", "/api/v1/models", key)
        err = _http_error(status, payload)
        if err:
            return ToolResult(ok=False, error=err, data={"status_code": status})
        if isinstance(payload, list):
            models = payload
        else:
            models = payload.get("models") or payload.get("data") or []
        lines: list[str] = []
        for model in models:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or model.get("modelId") or "?")
            name = str(model.get("name") or "")
            kind = str(model.get("type") or model.get("category") or "")
            extra = " — ".join(part for part in (name, kind) if part)
            lines.append(f"{model_id}" + (f" — {extra}" if extra else ""))
        return ToolResult(
            ok=True,
            output="\n".join(lines) or "(no models visible to this account)",
            data={"models": models, "count": len(lines)},
        )


class PixioParamsTool(_PixioTool):
    name = "pixio_params"
    description = (
        "Fetch the accepted params (required fields, defaults, allowed values) for one "
        "Pixio model. Call this BEFORE pixio_generate and pass only params it lists — "
        "never invent params."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "model_id": {
                "type": "string",
                "description": "A model id from pixio_models (e.g. 'pixio/…').",
            },
        },
        "required": ["model_id"],
    }

    async def _run(self, key: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        model_id = str(args.get("model_id") or "").strip()
        if not model_id:
            return ToolResult(ok=False, error="model_id is required")
        status, payload = await self._api(
            "GET", f"/api/v1/params?modelId={quote(model_id, safe='')}", key
        )
        err = _http_error(status, payload)
        if err:
            return ToolResult(ok=False, error=err, data={"status_code": status})
        return ToolResult(
            ok=True,
            output=json.dumps(payload, indent=2, default=str),
            data={"model_id": model_id, "params": payload},
        )


class PixioGenerateTool(_PixioTool):
    name = "pixio_generate"
    description = (
        "Start a Pixio generation (image/video/audio) and, by default, wait for it and "
        "save the output into the session workspace under pixio/. Use a model id from "
        "pixio_models and params from pixio_params. Costs credits; default accounts run "
        "ONE generation at a time. Set wait=false for long renders and re-check with "
        "pixio_status."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "model_id": {
                "type": "string",
                "description": "A model id from pixio_models — never invent one.",
            },
            "params": {
                "type": "object",
                "description": "Generation params exactly as pixio_params describes them.",
            },
            "wait": {
                "type": "boolean",
                "description": "Poll until succeeded/failed (default true).",
            },
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "description": f"Max seconds to wait when wait=true (default {_DEFAULT_TIMEOUT_SECONDS}).",
            },
        },
        "required": ["model_id", "params"],
    }

    def __init__(
        self,
        key_resolver: KeyResolver | None = None,
        http: HttpRequest | None = None,
        poll_seconds: float = _POLL_SECONDS,
        artifact_sink: ArtifactSink | None = None,
    ) -> None:
        super().__init__(key_resolver, http, artifact_sink)
        self._poll_seconds = poll_seconds  # test hook — production keeps ~5s

    async def _run(self, key: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        model_id = str(args.get("model_id") or "").strip()
        if not model_id:
            return ToolResult(ok=False, error="model_id is required")
        params = args.get("params")
        if not isinstance(params, dict):
            return ToolResult(
                ok=False,
                error="params must be an object — call pixio_params for the model's accepted params",
            )
        wait = bool(args.get("wait", True))
        try:
            timeout_seconds = int(args.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
        timeout_seconds = max(1, timeout_seconds)

        status, payload = await self._api(
            "POST",
            "/api/v1/generate",
            key,
            {"providerId": "pixio", "modelId": model_id, "params": params},
        )
        err = _http_error(status, payload)
        if err:
            return ToolResult(ok=False, error=err, data={"status_code": status})
        body = payload if isinstance(payload, dict) else {}
        # The response id field varies — save contentId OR id, whichever is present.
        generation_id = str(body.get("contentId") or body.get("id") or "")
        if not generation_id:
            return ToolResult(ok=False, error="Pixio generate returned no generation id")

        if not wait:
            return ToolResult(
                ok=True,
                output=(
                    f"generation {generation_id} started on {model_id} — "
                    f"re-check with pixio_status"
                ),
                data={"generation_id": generation_id, "status": "pending"},
            )

        deadline = time.monotonic() + timeout_seconds
        while True:
            status, payload = await self._api(
                "GET", f"/api/v1/generations/{generation_id}", key
            )
            err = _http_error(status, payload)
            if err:
                return ToolResult(
                    ok=False, error=err, data={"generation_id": generation_id}
                )
            body = payload if isinstance(payload, dict) else {}
            state = str(body.get("status") or "").lower()
            if state == "succeeded":
                return await self._deliver(generation_id, body, ctx)
            if state == "failed":
                return self._failed(generation_id, body)
            if time.monotonic() >= deadline:
                return ToolResult(
                    ok=False,
                    error=(
                        f"generation {generation_id} still '{state or 'pending'}' after "
                        f"{timeout_seconds}s — re-check later with pixio_status"
                    ),
                    data={"generation_id": generation_id, "status": state or "pending"},
                )
            await asyncio.sleep(self._poll_seconds)


class PixioStatusTool(_PixioTool):
    name = "pixio_status"
    description = (
        "Check one Pixio generation by id (from pixio_generate). On success the output "
        "is downloaded into the session workspace under pixio/; otherwise the current "
        "status or failure detail is returned."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "generation_id": {
                "type": "string",
                "description": "The id returned by pixio_generate.",
            },
        },
        "required": ["generation_id"],
    }

    async def _run(self, key: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        generation_id = str(args.get("generation_id") or "").strip()
        if not generation_id:
            return ToolResult(ok=False, error="generation_id is required")
        status, payload = await self._api(
            "GET", f"/api/v1/generations/{generation_id}", key
        )
        err = _http_error(status, payload)
        if err:
            return ToolResult(ok=False, error=err, data={"generation_id": generation_id})
        body = payload if isinstance(payload, dict) else {}
        state = str(body.get("status") or "").lower()
        if state == "succeeded":
            return await self._deliver(generation_id, body, ctx)
        if state == "failed":
            return self._failed(generation_id, body)
        return ToolResult(
            ok=True,
            output=f"generation {generation_id} is {state or 'pending'}",
            data={"generation_id": generation_id, "status": state or "pending"},
        )


class PixioUploadTool(_PixioTool):
    name = "pixio_upload"
    description = (
        "PUBLISH media to Pixio's public CDN and get a clean, PERMANENT, PUBLIC "
        "url (no signed query string) — use that url directly in a generation "
        "param (image-to-video reference frames, cover art, audio to extend…). "
        "Give either a local `path` (image/video/audio file — uploaded via "
        "multipart) or a remote `url` to mirror. NOTE: the result is publicly "
        "reachable by anyone with the link — never upload private documents."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Local media file — absolute, or relative to the session workspace.",
            },
            "url": {
                "type": "string",
                "description": "Remote media url to mirror instead of a local file.",
            },
            "endpoint": {
                "type": "string",
                "enum": ["media", "images"],
                "description": "'images' accepts images only; 'media' (default) takes any media.",
            },
        },
    }

    #: Never push a non-media file (a config, a key, a database) to a PUBLIC CDN.
    _MAX_UPLOAD = 200 * 1024 * 1024

    def __init__(
        self,
        key_resolver: KeyResolver | None = None,
        http: HttpRequest | None = None,
        artifact_sink: ArtifactSink | None = None,
        http_upload: HttpUpload | None = None,
    ) -> None:
        super().__init__(key_resolver, http, artifact_sink)
        self._http_upload = http_upload or _default_http_upload

    async def _run(self, key: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_path = str(args.get("path") or "").strip()
        remote = str(args.get("url") or "").strip()
        endpoint = "images" if str(args.get("endpoint") or "") == "images" else "media"
        if bool(raw_path) == bool(remote):
            return ToolResult(ok=False, error="give exactly one of `path` or `url`")

        if remote:
            try:
                public = await asyncio.to_thread(
                    pixio_publish, key, url=remote, endpoint=endpoint, http=self._http
                )
            except RuntimeError as exc:
                return ToolResult(ok=False, error=str(exc))
            return ToolResult(
                ok=True,
                output=f"mirrored → permanent public url:\n{public}",
                data={"url": public, "source": remote},
            )

        from pathlib import Path

        from ..core.fs_policy import fs_read_ok
        from ..creative.service import media_kind, mime_for

        p = Path(raw_path)
        if not p.is_absolute():
            p = ctx.workspace / raw_path
        if not p.is_file():
            return ToolResult(ok=False, error=f"no such file: {p}")
        if media_kind(p.name) is None:
            return ToolResult(
                ok=False,
                error=(
                    f"'{p.suffix}' is not a media file — only image/video/audio "
                    "may be published to the public CDN"
                ),
            )
        ok, reason = fs_read_ok(str(p))
        if not ok:
            return ToolResult(ok=False, error=f"blocked: {reason}")
        if p.stat().st_size > self._MAX_UPLOAD:
            return ToolResult(ok=False, error="file too large to publish (200MB max)")

        # read_bytes() (up to 200MB) + the POST both run OFF the loop.
        def _read_and_publish() -> tuple[str, int]:
            blob = p.read_bytes()
            return (
                pixio_publish(
                    key,
                    blob=blob,
                    filename=p.name,
                    mime=mime_for(p.name),
                    endpoint=endpoint,
                    http_upload=self._http_upload,
                ),
                len(blob),
            )

        try:
            public, size = await asyncio.to_thread(_read_and_publish)
        except RuntimeError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(
            ok=True,
            output=(
                f"uploaded {p.name} ({size} bytes) → permanent public url:\n"
                f"{public}\nPass it directly in a generation param."
            ),
            data={"url": public, "source": str(p)},
        )


def pixio_tools(
    key_resolver: KeyResolver | None = None,
    http: HttpRequest | None = None,
    artifact_sink: ArtifactSink | None = None,
    http_upload: HttpUpload | None = None,
) -> list[Tool]:
    """Build the Pixio creative tool group, keyed off the secrets vault.

    Mirrors ``web_search_tools`` so the platform registers it the same way::

        from .tools.pixio import pixio_tools
        for tool in pixio_tools(key_resolver=lambda: secrets.get("pixio")):
            registry.register(tool)
    """
    return [
        PixioModelsTool(key_resolver, http, artifact_sink),
        PixioParamsTool(key_resolver, http, artifact_sink),
        PixioGenerateTool(key_resolver, http, artifact_sink=artifact_sink),
        PixioStatusTool(key_resolver, http, artifact_sink),
        PixioUploadTool(key_resolver, http, artifact_sink, http_upload),
    ]
