"""Image tools — eyes and image-handling hands (§19 tool interface).

Four tools that let any session SEE and manipulate images:

* ``view_image``    — send an image to the current vision-capable model with a
  question and return its answer (the eyes).
* ``image_convert`` — re-encode an image to another format (Pillow).
* ``image_resize``  — aspect-preserving downscale (never upscales).
* ``image_info``    — format / dimensions / mode / size, no model call.

Path policy mirrors the repo's documents precedent (``documents/tools.py``):
READ paths (``view_image`` / ``image_info``, and the ``source`` of convert /
resize) may be absolute — looking at the user's real files is the point — gated
by the shared filesystem policy (:func:`iron_jarvis.core.fs_policy.fs_read_ok`);
relative paths resolve inside the session workspace via :func:`safe_path`.
WRITE targets are always workspace-only via :func:`safe_path` (§17), so a
resize of an out-of-workspace source requires an explicit workspace ``target``.

The model call is routed through the platform's ``ModelRouter``, injected as a
zero-arg ``router_resolver`` closure at registration (same closure-injection
pattern as :mod:`iron_jarvis.tools.pixio`), so tests pass a fake router and the
module imports without the platform. Pillow is imported lazily inside
``execute`` so this module stays import-safe even if the dep is missing.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Callable

from ..core.fs_policy import fs_read_ok
from ..providers.adapters.base import LLMMessage
from .base import Tool, ToolContext, ToolResult, safe_path

#: () -> the platform's ModelRouter (anything with an async ``complete``).
RouterResolver = Callable[[], Any]

#: Suffix -> media type the vision providers accept.
_VIEW_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_VIEW_SUPPORTED = "png, jpg, jpeg, webp, gif"

#: Target suffix -> Pillow save format for convert/resize.
_SAVE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".gif": "GIF",
}
_CONVERT_SUPPORTED = "png, jpg, jpeg, webp, bmp, gif"

#: Providers reject huge payloads (and base64 inflates them ~33%) — cap here
#: with an actionable hint instead of a cryptic provider 4xx.
_MAX_VIEW_BYTES = 8 * 1024 * 1024

_DEFAULT_QUESTION = (
    "Describe this image in detail — layout, text, notable elements."
)
_NO_VISION_ERROR = (
    "the current model returned nothing — it may not support vision; "
    "try an Anthropic/Google model"
)


def _resolve_read_path(raw: str, ctx: ToolContext) -> "tuple[Path | None, str | None]":
    """Documents precedent: absolute paths are allowed for READS (fs-policy
    gated); relative paths resolve under the workspace via ``safe_path``.

    Returns ``(path, None)`` or ``(None, error)``.
    """
    p = Path(raw)
    if p.is_absolute():
        allowed, reason = fs_read_ok(p)
        if not allowed:
            return None, reason
        return p, None
    try:
        return safe_path(ctx.workspace, raw), None
    except PermissionError as exc:
        return None, str(exc)


def _resolve_write_path(raw: str, ctx: ToolContext) -> "tuple[Path | None, str | None]":
    """WRITES are workspace-only (§17) — always through ``safe_path``."""
    try:
        return safe_path(ctx.workspace, raw), None
    except PermissionError as exc:
        return None, str(exc)


def _positive_int(value: Any, name: str) -> "tuple[int | None, str | None]":
    """Parse an optional positive-int arg → ``(value, None)`` or ``(None, error)``."""
    if value is None:
        return None, None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, f"{name} must be a positive integer"
    if parsed < 1:
        return None, f"{name} must be a positive integer"
    return parsed, None


def _flatten_to_rgb(img: Any) -> Any:
    """JPEG has no alpha channel — composite transparent pixels onto white."""
    from PIL import Image

    if img.mode == "RGB":
        return img
    rgba = img.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


class _ImageTool(Tool):
    """Shared plumbing: one permission switch + never-raise execution."""

    permission_key = "images"  # one switch governs the whole image group

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            return await self._run(args, ctx)
        except Exception as exc:  # noqa: BLE001 — a bad file/model fault must not crash the runtime
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


class ViewImageTool(_ImageTool):
    name = "view_image"
    #: An image can carry planted text/instructions the model will transcribe —
    #: same fencing as read_document.
    returns_untrusted_content = True
    description = (
        "Look at an image with the current vision model and answer a question "
        f"about it (default: a detailed description). Supports {_VIEW_SUPPORTED}; "
        "path may be absolute or workspace-relative. Images over 8MB must be "
        "downscaled first with image_resize."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Image path (absolute, or relative to the workspace).",
            },
            "question": {
                "type": "string",
                "description": "What to ask about the image (default: describe it in detail).",
            },
        },
        "required": ["path"],
    }

    def __init__(self, router_resolver: RouterResolver) -> None:
        self._router_resolver = router_resolver

    async def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args.get("path") or "").strip()
        if not raw:
            return ToolResult(ok=False, error="path is required")
        path, err = _resolve_read_path(raw, ctx)
        if err or path is None:
            return ToolResult(ok=False, error=err or "path could not be resolved")
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such image: {raw}")
        media_type = _VIEW_MEDIA_TYPES.get(path.suffix.lower())
        if not media_type:
            return ToolResult(
                ok=False,
                error=(
                    f"unsupported image type '{path.suffix or '(no suffix)'}' — "
                    f"supported: {_VIEW_SUPPORTED} (image_convert can re-encode it)"
                ),
            )
        blob = await asyncio.to_thread(path.read_bytes)
        if len(blob) > _MAX_VIEW_BYTES:
            return ToolResult(
                ok=False,
                error=(
                    f"image is {len(blob)} bytes (over the {_MAX_VIEW_BYTES // (1024 * 1024)}MB "
                    "vision limit) — downscale it first with image_resize"
                ),
            )
        question = str(args.get("question") or "").strip() or _DEFAULT_QUESTION
        message = LLMMessage(
            role="user",
            content=question,
            images=[
                {
                    "data_b64": base64.b64encode(blob).decode("ascii"),
                    "media_type": media_type,
                }
            ],
        )
        # Exceptions from the router are wrapped by _ImageTool.execute.
        result = await self._router_resolver().complete(
            system="You are a precise visual analyst.",
            messages=[message],
            tools=[],
            session_id=ctx.session_id,
        )
        text = (getattr(result.response, "text", "") or "").strip()
        if not text:
            # Honest error beats a fabricated description (mock/text-only models).
            return ToolResult(ok=False, error=_NO_VISION_ERROR)
        return ToolResult(
            ok=True,
            output=text,
            data={
                "path": raw,
                "media_type": media_type,
                "bytes": len(blob),
                "provider": getattr(result, "provider", None),
                "model": getattr(result, "model", None),
            },
        )


class ImageConvertTool(_ImageTool):
    name = "image_convert"
    description = (
        f"Convert an image to another format ({_CONVERT_SUPPORTED}) chosen by the "
        "target path's suffix. JPEG output is flattened to RGB on white at "
        "quality 90. The source may be absolute or workspace-relative; the "
        "target is created inside the workspace (parents auto-created)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source image (absolute, or relative to the workspace).",
            },
            "target": {
                "type": "string",
                "description": "Workspace-relative output path; suffix picks the format.",
            },
        },
        "required": ["source", "target"],
    }

    async def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_source = str(args.get("source") or "").strip()
        raw_target = str(args.get("target") or "").strip()
        if not raw_source or not raw_target:
            return ToolResult(ok=False, error="source and target are required")
        source, err = _resolve_read_path(raw_source, ctx)
        if err or source is None:
            return ToolResult(ok=False, error=err or "source could not be resolved")
        if not source.is_file():
            return ToolResult(ok=False, error=f"no such image: {raw_source}")
        target, err = _resolve_write_path(raw_target, ctx)
        if err or target is None:
            return ToolResult(ok=False, error=err or "target could not be resolved")
        fmt = _SAVE_FORMATS.get(target.suffix.lower())
        if not fmt:
            return ToolResult(
                ok=False,
                error=(
                    f"unsupported target type '{target.suffix or '(no suffix)'}' — "
                    f"supported: {_CONVERT_SUPPORTED}"
                ),
            )

        def _convert() -> None:
            from PIL import Image

            with Image.open(source) as img:
                img.load()
                out = _flatten_to_rgb(img) if fmt == "JPEG" else img
                target.parent.mkdir(parents=True, exist_ok=True)
                save_kwargs: dict[str, Any] = {"quality": 90} if fmt == "JPEG" else {}
                out.save(target, format=fmt, **save_kwargs)

        # Pillow decode/encode is CPU-bound — keep it off the event loop.
        await asyncio.to_thread(_convert)
        size = target.stat().st_size
        return ToolResult(
            ok=True,
            output=f"converted {raw_source} -> {raw_target} ({fmt}, {size} bytes)",
            data={"source": raw_source, "target": raw_target, "format": fmt, "bytes": size},
        )


class ImageResizeTool(_ImageTool):
    name = "image_resize"
    description = (
        "Downscale an image to fit within max_width/max_height, preserving "
        "aspect ratio — never upscales. target defaults to overwriting the "
        "source (source must then be inside the workspace); writes always land "
        "in the workspace."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source image (absolute, or relative to the workspace).",
            },
            "target": {
                "type": "string",
                "description": "Workspace-relative output path (default: overwrite source).",
            },
            "max_width": {"type": "integer", "minimum": 1},
            "max_height": {"type": "integer", "minimum": 1},
        },
        "required": ["source"],
    }

    async def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_source = str(args.get("source") or "").strip()
        if not raw_source:
            return ToolResult(ok=False, error="source is required")
        max_width, err = _positive_int(args.get("max_width"), "max_width")
        if err:
            return ToolResult(ok=False, error=err)
        max_height, err = _positive_int(args.get("max_height"), "max_height")
        if err:
            return ToolResult(ok=False, error=err)
        if max_width is None and max_height is None:
            return ToolResult(
                ok=False, error="provide max_width and/or max_height (at least one)"
            )
        source, err = _resolve_read_path(raw_source, ctx)
        if err or source is None:
            return ToolResult(ok=False, error=err or "source could not be resolved")
        if not source.is_file():
            return ToolResult(ok=False, error=f"no such image: {raw_source}")

        raw_target = str(args.get("target") or "").strip()
        if raw_target:
            target, err = _resolve_write_path(raw_target, ctx)
            if err or target is None:
                return ToolResult(ok=False, error=err or "target could not be resolved")
        else:
            # Default = overwrite source, which is only legal for workspace files.
            if not source.resolve().is_relative_to(ctx.workspace.resolve()):
                return ToolResult(
                    ok=False,
                    error=(
                        "source is outside the workspace and writes are workspace-only — "
                        "pass a workspace-relative target"
                    ),
                )
            target, raw_target = source, raw_source

        def _resize() -> tuple[int, int, int, int]:
            from PIL import Image

            with Image.open(source) as img:
                img.load()
                original = (img.width, img.height)
                fmt = _SAVE_FORMATS.get(target.suffix.lower()) or img.format
                bounds = (max_width or img.width, max_height or img.height)
                # thumbnail() preserves aspect ratio and NEVER upscales.
                img.thumbnail(bounds, Image.Resampling.LANCZOS)
                out = _flatten_to_rgb(img) if fmt == "JPEG" else img
                target.parent.mkdir(parents=True, exist_ok=True)
                save_kwargs: dict[str, Any] = {"quality": 90} if fmt == "JPEG" else {}
                out.save(target, format=fmt, **save_kwargs)
                return original[0], original[1], out.width, out.height

        old_w, old_h, new_w, new_h = await asyncio.to_thread(_resize)
        return ToolResult(
            ok=True,
            output=f"resized {raw_source}: {old_w}x{old_h} -> {new_w}x{new_h} at {raw_target}",
            data={
                "source": raw_source,
                "target": raw_target,
                "width": new_w,
                "height": new_h,
                "original_width": old_w,
                "original_height": old_h,
            },
        )


class ImageInfoTool(_ImageTool):
    name = "image_info"
    description = (
        "Inspect an image without calling a model: format, dimensions, color "
        "mode, and file size. Path may be absolute or workspace-relative."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Image path (absolute, or relative to the workspace).",
            },
        },
        "required": ["path"],
    }

    async def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args.get("path") or "").strip()
        if not raw:
            return ToolResult(ok=False, error="path is required")
        path, err = _resolve_read_path(raw, ctx)
        if err or path is None:
            return ToolResult(ok=False, error=err or "path could not be resolved")
        if not path.is_file():
            return ToolResult(ok=False, error=f"no such image: {raw}")

        def _probe() -> tuple[str, int, int, str]:
            from PIL import Image

            with Image.open(path) as img:
                return str(img.format or "?"), img.width, img.height, img.mode

        fmt, width, height, mode = await asyncio.to_thread(_probe)
        size = path.stat().st_size
        return ToolResult(
            ok=True,
            output=f"{fmt} {width}x{height} {mode} — {size} bytes",
            data={
                "path": raw,
                "format": fmt,
                "width": width,
                "height": height,
                "mode": mode,
                "bytes": size,
            },
        )


def image_tools(router_resolver: RouterResolver) -> list[Tool]:
    """Build the image tool group around an injected ModelRouter closure.

    Mirrors ``pixio_tools`` so the platform registers it the same way::

        from .tools.images import image_tools
        for tool in image_tools(lambda: platform.router):
            registry.register(tool)
    """
    return [
        ViewImageTool(router_resolver),
        ImageConvertTool(),
        ImageResizeTool(),
        ImageInfoTool(),
    ]
