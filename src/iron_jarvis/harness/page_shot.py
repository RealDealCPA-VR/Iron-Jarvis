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

Second review S1 (v1.515.0): the one-shot picture is taken through the
AGENT browser's backend (``page_browser.AgentBrowsers.one_shot``), started for
that picture alone and ended after it. The bare ``--screenshot`` command line
it replaced judged nothing after the first address: a page could send itself
(a meta refresh, a script, a redirect) or draw a frame of another program's
page, and the picture of THAT went to the model; it could not even say where
the page had ended up, and in a Local only session nothing stopped a public
address. The backend holds the address rules on every navigation, frames
included, and the Local only proxy; a page that moved gives no picture. Its
processes live in a kill-on-close Job (``procs.adopt``), its profile in a
throwaway folder in the SYSTEM temp folder, removed after; a Stop ends it.

Saved pictures live in ``<home>/page-screenshots/<session>/`` (second review
S9: per session, newest :data:`KEEP_SHOTS` each), and go to the app's trash
when the session is deleted (:func:`trash_session_shots`). Pictures saved
before v1.515.0 in the shared ``<home>/page-screenshots`` stay where they are.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import io
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.logging import get_logger
from ..tools.base import (
    Reversibility,
    RiskClass,
    SpendCheck,
    Tool,
    ToolContext,
    ToolResult,
)
from .preview import is_preview_page, local_address, own_program_page

log = get_logger(__name__)

TOOL_NAME = "page_screenshot"
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 800
MIN_WIDTH, MAX_WIDTH = 320, 1920
MIN_HEIGHT, MAX_HEIGHT = 240, 1600
DEFAULT_WAIT_MS, MAX_WAIT_MS = 1500, 10_000
#: The whole browser run (start, load, settle, write the picture).
RUN_TIMEOUT_S = 45.0
#: Pictures kept per session in ``<home>/page-screenshots/<session>``
#: (oldest removed first).
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


#: The folder (under the app's home) a session's pictures are kept in, one
#: sub-folder per session (second review S9).
SHOTS_DIRNAME = "page-screenshots"


def _session_dirname(thread_or_session: str) -> str:
    """A session's own sub-folder name: its thread id (``build:<id>`` read
    as ``<id>``), letters, digits, ``-`` and ``_`` only."""
    raw = str(thread_or_session or "")
    if raw.startswith("build:"):
        raw = raw[len("build:"):]
    return "".join(c for c in raw if c.isalnum() or c in "-_")[:80] or "session"


def shots_folder(home: Any, ctx: Any) -> Path:
    """Where a tool call's picture is kept: ``<home>/page-screenshots/<the
    session's thread id>/`` — a helper's call keeps it in ITS session's
    folder (the agent browser's own key, ``page_browser.browser_key``)."""
    from . import page_browser as PB

    return Path(home) / SHOTS_DIRNAME / _session_dirname(PB.browser_key(ctx))


def trash_session_shots(home: Any, thread_id: str) -> str:
    """BLOCKING: MOVE a deleted session's pictures (they can show another
    program's data) to ``<home>/trash/<stamp>/page-screenshots/<thread id>``
    — never delete them. Returns where they went ("" when the session had
    none, or they could not be moved: said in the log, nothing deleted).
    Never raises."""
    from ..maintenance import TRASH_DIRNAME, trash_stamp

    name = _session_dirname(thread_id)
    src = Path(home) / SHOTS_DIRNAME / name
    try:
        if not src.is_dir():
            return ""
        dest = Path(home) / TRASH_DIRNAME / trash_stamp() / SHOTS_DIRNAME / name
        if dest.exists():
            dest = dest.with_name(f"{name}-{uuid.uuid4().hex[:6]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return str(dest)
    except OSError:
        log.warning("a deleted coding session's screenshots could not be moved to the trash", exc_info=True)
        return ""


def _session_page(args: dict[str, Any], ctx: Any) -> str:
    """v1.376.0: the browser key whose OPEN agent browser this call looks at
    — no ``url`` (the page it is on), or the very page it is on — else ""
    (today's one-shot of ``url``)."""
    from . import page_browser as PB

    key = PB.browser_key(ctx)
    current = PB.BROWSERS.current_url(key)
    if not current:
        return ""
    raw = str((args or {}).get("url") or "").strip()
    if not raw:
        return key
    want = local_address(PB.normalize(raw)).split("#", 1)[0].rstrip("/")
    have = local_address(current).split("#", 1)[0].rstrip("/")
    return key if want and want == have else ""


class PageScreenshotTool(Tool):
    name = TOOL_NAME
    description = (
        "See how a web page on this PC or the user's own network looks: a screenshot you can "
        "look at. With your browser open (page_open), leave out url to see the page it is on "
        "right now. Otherwise give url: the page opens in a private, invisible browser window "
        "of its own. Use it for the user's dev server (for example http://localhost:5173) "
        "after you change something, or when the user asks how a page looks. Not for "
        "public websites. `width`/`height` set the window (default 1280×800); "
        "`wait_ms` lets the page finish loading first (default 1500)."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The page address, e.g. http://localhost:5173/settings. Leave it "
                "out to see the page your browser (page_open) is on.",
            },
            "width": {"type": "integer", "minimum": MIN_WIDTH, "maximum": MAX_WIDTH},
            "height": {"type": "integer", "minimum": MIN_HEIGHT, "maximum": MAX_HEIGHT},
            "wait_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS},
        },
        "required": [],
    }

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> "SpendCheck | None":
        """The page the user's Browser pane shows runs without a card; any
        other local page asks. v1.376.0: the page the agent's own browser is
        on, and an address the user allowed for that browser, need no card
        either (the user already allowed it). An address ``execute`` REFUSES
        (not on this PC or network, or none given) never cards: the user was
        asked to allow a call that was then refused in words. A page a
        program this session started is serving is the session's own work
        (``preview.own_program_page``) and needs no card."""
        if _session_page(args, ctx):
            return SpendCheck(ask=False)
        url = local_address(str((args or {}).get("url") or "").strip())
        if not url:
            return SpendCheck(ask=False)
        from . import page_browser as PB

        if is_preview_page(url) or PB.BROWSERS.allowed(PB.browser_key(ctx), url):
            return SpendCheck(ask=False)
        if await own_program_page(ctx, url):
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
        if _session_page(args, ctx):
            return await self._from_session_browser(ctx)
        raw = str((args or {}).get("url") or "").strip()
        if not raw:
            return ToolResult(
                ok=False,
                error="Give the page's address (url), or open it with page_open first.",
            )
        url = local_address(raw)
        if not url:
            return ToolResult(ok=False, error=NOT_LOCAL)
        width = _clamp(args.get("width"), MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH)
        height = _clamp(args.get("height"), MIN_HEIGHT, MAX_HEIGHT, DEFAULT_HEIGHT)
        wait_ms = _clamp(args.get("wait_ms"), 0, MAX_WAIT_MS, DEFAULT_WAIT_MS)
        binary = await asyncio.to_thread(browser_executable)
        if not binary:
            return ToolResult(ok=False, error=NO_BROWSER)
        from . import page_browser as PB
        from .preview import current_preview

        # Second review S1: through the agent browser's backend, for this
        # picture alone (the address rules on every navigation, frames
        # included; Local only behind the refusing proxy; no picture of a
        # page that moved).
        try:
            shot = await PB.BROWSERS.one_shot(
                url, edge=binary, preview=current_preview(), width=width, height=height,
                wait_ms=wait_ms, limit_s=RUN_TIMEOUT_S,
            )
        except PB.BrowserProblem as exc:
            return ToolResult(ok=False, error=f"could not take a screenshot of {url}: {exc}")
        if not shot.picture:
            return ToolResult(ok=False, error=f"could not take a screenshot of {url}: the browser gave no picture")
        raw = shot.picture
        path = await asyncio.to_thread(_save_picture, raw, shots_folder(ctx.config.home, ctx), url)
        data, media = await asyncio.to_thread(_fit, raw)
        data_out = {"url": url, "file": str(path), "width": width, "height": height,
                    "bytes": len(raw)}
        if shot.blocked:
            data_out["blocked"] = list(shot.blocked)
        said = "".join(" " + line for line in shot.blocked)
        if len(data) > HARD_IMAGE_BYTES:
            # Too big to hand any model even after re-encoding (a failed
            # Pillow, a huge window): the file is kept and named instead.
            return ToolResult(
                ok=True,
                output=(
                    f"Screenshot of {url} at {width}×{height} saved at {path}, but it is "
                    "too large to attach here. Take a smaller one (width/height), or call "
                    "view_image with that path." + said
                ),
                data={**data_out, "attached": False},
            )
        return ToolResult(
            ok=True,
            output=(
                f"Screenshot of {url} at {width}×{height}; the picture is attached to "
                f"this result. Saved at {path}. If you cannot see a picture here, call "
                "view_image with that path." + said
            ),
            data=data_out,
            images=[{"media_type": media, "data_b64": base64.b64encode(data).decode("ascii")}],
        )


    async def _from_session_browser(self, ctx: ToolContext) -> ToolResult:
        """v1.376.0: the page the agent's own browser is on, as it is right
        now (after the clicks and the typing), at that browser's window size.
        Saved in the session's own folder like every screenshot. Second review
        S1: a frame the rules left empty is said."""
        from . import page_browser as PB
        from .page_act import run_action

        out = await run_action(ctx, lambda key, preview: PB.BROWSERS.picture(key, preview=preview))
        if isinstance(out, ToolResult):
            return out
        if not out.picture:
            return ToolResult(ok=False, error="The agent's browser could not take a screenshot of its page.")
        url = out.url
        path = await asyncio.to_thread(_save_picture, out.picture, shots_folder(ctx.config.home, ctx), url)
        data, media = await asyncio.to_thread(_fit, out.picture)
        width, height = PB.VIEWPORT["width"], PB.VIEWPORT["height"]
        shown = PB.where(url) or "its page"
        data_out = {"url": url, "file": str(path), "width": width, "height": height,
                    "bytes": len(out.picture), "browser": "agent"}
        if out.blocked:
            data_out["blocked"] = list(out.blocked)
        said = "".join(" " + line for line in out.blocked)
        if len(data) > HARD_IMAGE_BYTES:
            return ToolResult(
                ok=True,
                output=(
                    f"Screenshot of {shown} (the page the agent's browser is on) saved at {path}, "
                    "but it is too large to attach here. Call view_image with that path." + said
                ),
                data={**data_out, "attached": False},
            )
        return ToolResult(
            ok=True,
            output=(
                f"Screenshot of {shown}, the page the agent's browser is on, at {width}×{height}; "
                f"the picture is attached to this result. Saved at {path}. If you cannot see a "
                "picture here, call view_image with that path." + said
            ),
            data=data_out,
            images=[{"media_type": media, "data_b64": base64.b64encode(data).decode("ascii")}],
        )


def _save_picture(data: bytes, folder: Path, url: str) -> Path:
    """BLOCKING: a picture taken in memory, into its session's folder
    (:func:`shots_folder`; the newest :data:`KEEP_SHOTS` of that session
    kept)."""
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / _shot_name(url)
    dest.write_bytes(data)
    _prune(folder)
    return dest


def page_tools() -> list[Tool]:
    """Every page tool: the screenshot (v1.371.0) and the agent's browser
    (v1.376.0, ``harness/page_act``)."""
    from .page_act import page_act_tools

    return [PageScreenshotTool(), *page_act_tools()]
