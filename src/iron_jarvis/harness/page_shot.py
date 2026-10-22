"""``page_screenshot`` — the agent SEES a page on this PC or the user's
network (v1.371.0).

The IJ harness's Browser pane showed the user's dev server, but the agent
could only check its work with commands: no tool looked at the pane, the
paired browser's tools are not armed in a Build session, and ``web_fetch``
refuses local addresses on purpose (a page must not steer a fetch into the
user's network). This tool opens ONE local page in a private, invisible
browser window (Edge or Chrome, ``--headless=new --screenshot``, a throwaway
profile, no extensions) and hands the PICTURE back with the result
(:attr:`ToolResult.images`): Claude and Codex see it inside the tool result;
any other model gets the text, which names the saved file so ``view_image``
can describe it.

WHO DECIDES WHAT IT MAY LOOK AT. Only http(s) addresses on this PC or the
user's own network (:func:`..preview.local_address`). The page the user's
Browser pane shows (:mod:`..preview`) is looked at without asking — the user
opened it. Any OTHER local address asks first (:meth:`spend_check` →
``ask``), whatever the approval posture: a screenshot goes to the turn's
model, and a local page can be another app's admin page with client names
on it. Under low trust the tool is refused (``core/trust.LOW_TRUST_DENY``).

The browser runs through ``core/procs`` (its own Job, a hard bound, the tree
killed on timeout or cancel — :func:`procs.run_async`) in a throwaway folder
in the SYSTEM temp folder (profile, picture, any download), deleted after;
saved pictures live in ``<home>/page-screenshots`` (newest :data:`KEEP_SHOTS`).
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core import procs
from ..core.logging import get_logger
from ..tools.base import (
    Reversibility,
    RiskClass,
    SpendCheck,
    Tool,
    ToolContext,
    ToolResult,
)
from .preview import is_preview_page, local_address

log = get_logger(__name__)

TOOL_NAME = "page_screenshot"
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 800
MIN_WIDTH, MAX_WIDTH = 320, 1920
MIN_HEIGHT, MAX_HEIGHT = 240, 1600
DEFAULT_WAIT_MS, MAX_WAIT_MS = 1500, 10_000
#: The whole browser run (start, load, settle, write the picture).
RUN_TIMEOUT_S = 45.0
#: Pictures kept in ``<home>/page-screenshots`` (oldest removed first).
KEEP_SHOTS = 50
#: A picture bigger than this is re-encoded as JPEG (Claude takes ≤ 5 MB).
MAX_IMAGE_BYTES = 3_500_000
#: Past this the picture is NOT attached (Claude refuses an image over 5 MB,
#: and one refused image fails the whole turn): the text says where it is.
HARD_IMAGE_BYTES = 4_900_000

NOT_LOCAL = (
    "page_screenshot only opens a page on this PC or your own network "
    "(http://localhost:…, http://127.0.0.1:…, a 192.168.… or .local address). "
    "It does not open public websites."
)
NO_BROWSER = (
    "page_screenshot needs Microsoft Edge or Google Chrome on this PC, and "
    "neither was found."
)


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def browser_executable() -> str:
    """The first Edge or Chrome on this PC ("" when there is none). The
    doctor's own list of places (``onboarding.doctor._browser_candidates``) —
    reached by module path, because ``onboarding`` re-exports a FUNCTION
    named ``doctor`` that shadows the module."""
    try:
        doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
        candidates = doctor._browser_candidates()
    except Exception:  # noqa: BLE001 — fall back to PATH alone
        candidates = [shutil.which(n) for n in ("msedge", "chrome", "google-chrome")]
    for path in candidates:
        if path and os.path.isfile(path):
            return str(path)
    return ""


def browser_argv(binary: str, url: str, out: Path, profile: Path,
                 width: int, height: int, wait_ms: int) -> list[str]:
    """The one command line. ``--headless=new`` never shows a window;
    ``--user-data-dir`` is a fresh folder, so none of the user's cookies,
    logins or extensions are in it."""
    return [
        binary,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--mute-audio",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        f"--virtual-time-budget={wait_ms}",
        f"--screenshot={out}",
        url,
    ]


def _shot_name(url: str) -> str:
    parts = urlsplit(url)
    host = "".join(c if c.isalnum() else "-" for c in (parts.hostname or "page"))[:40]
    port = f"-{parts.port}" if parts.port else ""
    return f"page-{time.strftime('%Y%m%d-%H%M%S')}-{host}{port}-{uuid.uuid4().hex[:6]}.png"


def _prune(folder: Path, keep: int = KEEP_SHOTS) -> None:
    try:
        shots = sorted(
            (p for p in folder.glob("page-*.*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in shots[keep:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def _fit(data: bytes) -> tuple[bytes, str]:
    """``(bytes, media type)`` small enough to hand a model: the PNG as it is,
    or a JPEG when the PNG is over :data:`MAX_IMAGE_BYTES`."""
    if len(data) <= MAX_IMAGE_BYTES:
        return data, "image/png"
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        for quality in (85, 70, 55):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= MAX_IMAGE_BYTES:
                return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 — hand back what we have
        pass
    return data, "image/png"


def _new_work() -> Path:
    """BLOCKING: the run's throwaway folder, in the SYSTEM temp folder — never
    in Iron Jarvis's home (v1.371.0 review). While the browser runs, ~60 files
    of its profile are locked (measured: ``lockfile``, the shader caches), and
    a backup walks the whole home and fails on the first file it cannot read
    (``maintenance.create_backup``, the v1.337.1 class). The picture is
    written here too and moved into ``<home>/page-screenshots`` only after
    the browser has exited.

    The profile is seeded so a DOWNLOAD lands in this folder too: a page can
    start one with no click (a redirect to an attachment), and a fresh
    profile saves it to the user's own Downloads folder (measured with
    Edge 153 ``--headless=new``)."""
    work = Path(tempfile.mkdtemp(prefix="ij-page-shot-"))
    default = work / "profile" / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Preferences").write_text(
        json.dumps({"download": {
            "default_directory": str(work / "downloads"),
            "prompt_for_download": False,
            "directory_upgrade": True,
        }}),
        encoding="utf-8",
    )
    return work


def _remove_work(work: Path) -> None:
    """BLOCKING: delete the throwaway folder. Files of a browser that was
    just stopped can stay locked for a moment, so a few tries."""
    for _ in range(10):
        shutil.rmtree(work, ignore_errors=True)
        if not work.exists():
            return
        time.sleep(0.2)


def _keep(shot: Path, folder: Path, url: str) -> Path:
    """BLOCKING: move the finished picture into ``<home>/page-screenshots``."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / _shot_name(url)
    shutil.move(str(shot), str(dest))
    _prune(folder)
    return dest


def _shot_problem(shot: Path, res: Any) -> str:
    """BLOCKING: "" when the browser wrote a picture, else why not."""
    if res.timed_out:
        return f"the browser did not finish within {RUN_TIMEOUT_S:g} s"
    if not shot.is_file() or shot.stat().st_size == 0:
        tail = (res.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        why = f" ({' | '.join(tail)[:300]})" if tail else ""
        return f"the browser wrote no picture (exit {res.returncode}){why}"
    return ""


async def _capture(binary: str, url: str, folder: Path, width: int, height: int,
                   wait_ms: int) -> "tuple[Path | None, str]":
    """Run the browser once. ``(picture path, problem)``.

    Through :func:`procs.run_async`, so a Stop or a deadline KILLS the
    browser's tree (a cancelled ``to_thread`` would have let it run to its own
    45 s bound). The throwaway folder goes on every path: awaited on success
    and on an ordinary failure, from a thread of its own when the call was
    cancelled (an ``await`` in a cancelled task can be cancelled again)."""
    work = await asyncio.to_thread(_new_work)
    try:
        shot = work / "shot.png"
        res = await procs.run_async(
            browser_argv(binary, url, shot, work / "profile", width, height, wait_ms),
            timeout=RUN_TIMEOUT_S,
        )
        problem = await asyncio.to_thread(_shot_problem, shot, res)
        kept = None if problem else await asyncio.to_thread(_keep, shot, folder, url)
    except BaseException:
        threading.Thread(
            target=_remove_work, args=(work,), name="page-shot-cleanup", daemon=True,
        ).start()
        raise
    await asyncio.to_thread(_remove_work, work)
    return kept, problem


class PageScreenshotTool(Tool):
    name = TOOL_NAME
    description = (
        "See how a web page on this PC or the user's own network looks: it opens the "
        "page in a private, invisible browser window and returns a screenshot you can "
        "look at. Use it for the user's dev server (for example http://localhost:5173) "
        "after you change something, or when the user asks how a page looks. Not for "
        "public websites. `width`/`height` set the window (default 1280×800); "
        "`wait_ms` lets the page finish loading first (default 1500)."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The page address, e.g. http://localhost:5173/settings"},
            "width": {"type": "integer", "minimum": MIN_WIDTH, "maximum": MAX_WIDTH},
            "height": {"type": "integer", "minimum": MIN_HEIGHT, "maximum": MAX_HEIGHT},
            "wait_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS},
        },
        "required": ["url"],
    }

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> "SpendCheck | None":
        """The page the user's Browser pane shows runs without a card; any
        other local page asks; a non-local address has no opinion (execute
        refuses it in words)."""
        url = local_address(str((args or {}).get("url") or ""))
        if not url:
            return None
        if is_preview_page(url):
            return SpendCheck(ask=False)
        return SpendCheck(
            ask=True,
            words=(
                # v1.371.0 review: the words a person reads on the card —
                # "pane" is a retired word (VOCABULARY.md); the panel's own
                # label is "Browser".
                f"Take a screenshot of {url} for the model to look at. It is not the "
                "page open in this session's Browser."
            ),
        )

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = local_address(str((args or {}).get("url") or ""))
        if not url:
            return ToolResult(ok=False, error=NOT_LOCAL)
        width = _clamp(args.get("width"), MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH)
        height = _clamp(args.get("height"), MIN_HEIGHT, MAX_HEIGHT, DEFAULT_HEIGHT)
        wait_ms = _clamp(args.get("wait_ms"), 0, MAX_WAIT_MS, DEFAULT_WAIT_MS)
        binary = await asyncio.to_thread(browser_executable)
        if not binary:
            return ToolResult(ok=False, error=NO_BROWSER)
        folder = Path(ctx.config.home) / "page-screenshots"
        try:
            path, problem = await _capture(binary, url, folder, width, height, wait_ms)
        except OSError as exc:
            return ToolResult(ok=False, error=f"could not start the browser: {exc}")
        if path is None:
            return ToolResult(ok=False, error=f"could not take a screenshot of {url}: {problem}")
        raw = await asyncio.to_thread(path.read_bytes)
        data, media = await asyncio.to_thread(_fit, raw)
        data_out = {"url": url, "file": str(path), "width": width, "height": height,
                    "bytes": len(raw)}
        if len(data) > HARD_IMAGE_BYTES:
            # Too big to hand any model even after re-encoding (a failed
            # Pillow, a huge window): the file is kept and named instead.
            return ToolResult(
                ok=True,
                output=(
                    f"Screenshot of {url} at {width}×{height} saved at {path}, but it is "
                    "too large to attach here. Take a smaller one (width/height), or call "
                    "view_image with that path."
                ),
                data={**data_out, "attached": False},
            )
        return ToolResult(
            ok=True,
            output=(
                f"Screenshot of {url} at {width}×{height}; the picture is attached to "
                f"this result. Saved at {path}. If you cannot see a picture here, call "
                "view_image with that path."
            ),
            data=data_out,
            images=[{"media_type": media, "data_b64": base64.b64encode(data).decode("ascii")}],
        )


def page_tools() -> list[Tool]:
    return [PageScreenshotTool()]
