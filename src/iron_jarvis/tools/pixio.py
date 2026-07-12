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
import os
import re
import time
from pathlib import PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, urlparse

from .base import Tool, ToolContext, ToolResult

#: (method, url, headers, json_body) -> response-ish with
#: ``.status_code`` / ``.content`` / ``.json()``.
HttpRequest = Callable[[str, str, dict[str, str], "dict[str, Any] | None"], Any]
#: (url, headers, blob, filename, mime) -> response-ish — multipart uploads.
HttpUpload = Callable[[str, "dict[str, str]", bytes, str, str], Any]
#: () -> Pixio API key (or ``None`` when not configured).
KeyResolver = Callable[[], "str | None"]
#: (artifact_name, blob, filename, kind, session_id) — durable gallery sink.
#: The platform wires this to ArtifactStore.save so every generation lands in
#: the Creative gallery (and fires artifact.generated) instead of dying with
#: the disposable session workspace.
ArtifactSink = Callable[[str, bytes, str, str, "str | None"], Any]

_BASE_URL = "https://beta.pixio.myapps.ai"
_POLL_SECONDS = 5.0
_DEFAULT_TIMEOUT_SECONDS = 600
_MISSING_KEY_ERROR = (
    "Pixio API key not configured — add a secret named 'pixio' (Secrets page) "
    "or set PIXIO_API_KEY"
)

#: A plausible file extension: dot + up to 8 alphanumerics (``.png``, ``.mp4``).
_EXT_RX = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def _env_key_resolver() -> str | None:
    """Default key source — the ``PIXIO_API_KEY`` env var (vault closure wins in prod)."""
    return os.environ.get("PIXIO_API_KEY") or None


def _default_http(
    method: str, url: str, headers: dict[str, str], json_body: dict[str, Any] | None
) -> Any:
    """Production transport — httpx imported lazily so import stays dependency-light."""
    import httpx

    # Generous read timeout: media downloads can be tens of MB.
    timeout = httpx.Timeout(120.0, connect=10.0)
    return httpx.request(
        method, url, headers=headers, json=json_body, timeout=timeout, follow_redirects=True
    )


def _default_http_upload(
    url: str, headers: dict[str, str], blob: bytes, filename: str, mime: str
) -> Any:
    """Production multipart transport (uploads can be large — generous timeout)."""
    import httpx

    timeout = httpx.Timeout(300.0, connect=10.0)
    return httpx.request(
        "POST",
        url,
        headers=headers,
        files={"file": (filename, blob, mime or "application/octet-stream")},
        timeout=timeout,
        follow_redirects=True,
    )


def pixio_publish(
    key: str,
    *,
    blob: bytes | None = None,
    filename: str = "",
    mime: str = "",
    url: str | None = None,
    endpoint: str = "media",
    http: HttpRequest | None = None,
    http_upload: HttpUpload | None = None,
) -> str:
    """Publish media to Pixio's public CDN → a CLEAN, PERMANENT, PUBLIC url
    (no signed query string) usable directly in generation params.

    ``POST /api/v1/images`` (images only) / ``POST /api/v1/media`` (any media).
    Two input modes: a local ``blob`` (multipart ``file=``) or a remote ``url``
    to mirror (JSON ``{"url": …}``). Both return ``{"url": "https://…"}``.
    SYNC (callers run it off the loop); raises ``RuntimeError`` with an honest
    message on any failure — never a fabricated URL.
    """
    path_part = "images" if endpoint == "images" else "media"
    target = f"{_BASE_URL}/api/v1/{path_part}"
    auth = {"Authorization": f"Bearer {key}"}
    if url:
        resp = (http or _default_http)(
            "POST", target, {**auth, "Accept": "application/json"}, {"url": url}
        )
    elif blob is not None:
        resp = (http_upload or _default_http_upload)(
            target, auth, blob, filename or "file.bin", mime
        )
    else:
        raise RuntimeError("pixio_publish needs a blob or a url")
    status = int(getattr(resp, "status_code", 0) or 0)
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — non-JSON error bodies happen
        payload = {}
    err = _http_error(status, payload if isinstance(payload, dict) else {})
    if err:
        raise RuntimeError(err)
    public = str((payload or {}).get("url") or "") if isinstance(payload, dict) else ""
    if not public:
        raise RuntimeError("Pixio upload returned no url")
    return public


def _detail(payload: Any) -> str:
    """Best-effort human detail from an API body (error/message/detail, str or dict)."""
    if not isinstance(payload, dict):
        return ""
    for key in ("error", "message", "detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("message") or value.get("detail") or ""
        if value:
            return str(value)
    return ""


def _http_error(status: int, payload: Any) -> str | None:
    """Map a Pixio HTTP status to one clear line (``None`` when 2xx). Honest errors
    beat fabricated output — each mapped line tells the agent what to DO next."""
    if 200 <= status < 300:
        return None
    detail = _detail(payload)
    suffix = f": {detail}" if detail else ""
    if status == 401:
        return "Pixio rejected the API key (401) — update the 'pixio' secret or PIXIO_API_KEY"
    if status == 402:
        return f"Pixio: insufficient credits (402) — top up the account before generating{suffix}"
    if status == 404:
        return f"Pixio: not found (404){suffix or ' — check the model/generation id'}"
    if status == 429:
        return (
            "Pixio concurrency limit — one generation at a time on default accounts; "
            "wait for the current one"
        )
    return f"Pixio API error {status}{suffix}"


def _output_url(payload: dict[str, Any]) -> str:
    """Extract the result URL — ``outputUrl`` is canonical, ``outputs`` tolerated."""
    url = payload.get("outputUrl") or payload.get("output_url") or ""
    if not url:
        outputs = payload.get("outputs")
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str):
                url = first
            elif isinstance(first, dict):
                url = first.get("url") or first.get("outputUrl") or ""
    return str(url or "")


def _ext_from_url(url: str) -> str:
    """Guess a file extension from the URL path (``.bin`` when unguessable)."""
    try:
        suffix = PurePosixPath(urlparse(url).path).suffix
    except (ValueError, TypeError):
        return ".bin"
    return suffix if _EXT_RX.match(suffix or "") else ".bin"


def _safe_name(generation_id: str) -> str:
    """Generation ids become filenames — strip anything filesystem-hostile."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", generation_id) or "generation"


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
        Pixio round-trip must not freeze the daemon."""
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        resp = await asyncio.to_thread(self._http, method, _BASE_URL + path, headers, json_body)
        status = int(getattr(resp, "status_code", 0) or 0)
        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON error bodies happen
            payload = {}
        return status, payload if isinstance(payload, (dict, list)) else {}

    async def _deliver(
        self, generation_id: str, payload: dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        """A generation SUCCEEDED — download its output into the session workspace."""
        url = _output_url(payload)
        if not url:
            return ToolResult(
                ok=False,
                error=f"generation {generation_id} succeeded but the result has no outputUrl",
                data={"generation_id": generation_id, "status": "succeeded"},
            )
        # Output URLs are public CDN links — never send the bearer key to a
        # third-party host. Downloaded off the event loop like every round-trip.
        resp = await asyncio.to_thread(self._http, "GET", url, {}, None)
        status = int(getattr(resp, "status_code", 0) or 0)
        if not 200 <= status < 300:
            return ToolResult(
                ok=False,
                error=f"output download failed ({status}) for {url}",
                data={"generation_id": generation_id, "output_url": url, "status": "succeeded"},
            )
        blob: bytes = getattr(resp, "content", b"") or b""
        rel = f"pixio/{_safe_name(generation_id)}{_ext_from_url(url)}"
        dest = ctx.workspace / rel  # workspace-scoped by construction (§17)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        # Durable copy → the Creative gallery (ArtifactStore; fires
        # artifact.generated so the dashboard updates live). The workspace copy
        # is DISPOSABLE — without this, creations vanish with the session.
        gallery_note = ""
        artifact_name = ""
        if self._artifact_sink is not None:
            try:
                from ..creative.service import media_kind

                artifact_name = f"creative-{_safe_name(generation_id)}"
                self._artifact_sink(
                    artifact_name,
                    blob,
                    dest.name,
                    media_kind(dest.name) or "file",
                    ctx.session_id,
                )
                gallery_note = " — added to the Creative gallery"
            except Exception:  # noqa: BLE001 — the gallery is a bonus, never break delivery
                artifact_name = ""
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
                "abs_path": str(dest),
                **({"artifact": artifact_name} if artifact_name else {}),
                "status": "succeeded",
            },
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
