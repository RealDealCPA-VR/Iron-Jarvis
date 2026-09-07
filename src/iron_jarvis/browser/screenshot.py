"""A browser screenshot that is BOTH a durable artifact and something a model saw (D14, plan §10.1).

This module exists for one reason: the two halves of "look at the page" already
exist in this repository and have never been joined.

* :meth:`~iron_jarvis.computeruse.trace.TraceRecorder.record_screenshot` persists
  a capture as an :class:`~iron_jarvis.artifacts.store.ArtifactStore` version and
  records the path — and **no model ever sees it**.
* :meth:`~iron_jarvis.computeruse.tools.WebLookTool.execute` shows a capture to a
  vision model through a nested ``router.complete`` with
  ``LLMMessage(images=[{"data_b64", "media_type"}])`` — and **nothing is kept**,
  so the user cannot open what the model described and nobody can check it later.

``browser_screenshot`` does both, which is also why its ``question`` is optional:
with no question this module saves the PNG and returns the reference at no model
cost (plan §10.1), and with a question it additionally asks the vision role about
the very bytes it just wrote. There is deliberately **no browser-specific image
transport** (D14): the bytes arrive base64 on the ordinary response frame, are
saved through the ordinary artifact store, and are served by the ordinary
``GET /creative/file/{name}``.

Four details are load-bearing, and each of them is a bug this module is written to
not have:

1. **``kind="screenshot"``.** That string is what makes the artifact report
   ``media: "image"`` in ``daemon/routes/knowledge.py`` — a capture saved as the
   default ``kind="file"`` lands in the gallery as an un-previewable blob.
2. **A ``.png`` filename.** ``GET /creative/file/{name}`` answers **415** when
   ``creative.service.media_kind`` cannot read an image extension off the stored
   filename, so a suffix-less file is saved, indexed, and then unviewable. The
   suffix is derived from the ``media_type`` the browser reported rather than
   assumed, so a JPEG capture (an add-on option Chrome supports) stays honest and
   still passes ``media_kind``.
3. **The save runs OFF the event loop.** ``ArtifactStore.save`` writes a file and
   commits a SQLite row; the daemon is ONE asyncio loop (the v1.153.1 outage), so
   it goes through :func:`asyncio.to_thread`. The base64 decode rides along in the
   same hop rather than getting its own — one thread transition, and the CPU work
   is on the far side of it.
4. **``abs_path`` is absolute.** The standing rule since v1.153.2: a tool that
   writes a file says where, absolutely. A workspace-relative answer is a bare
   filename whenever the file lands in the root, the model relays it, and the
   user looks in the wrong folder.

**Can ``view_image`` open the file we just wrote?** Yes — verified rather than
assumed, which plan §10.1 asks for by name. ``config.artifacts_dir``
(``<home>/artifacts``) is **not** a ``register_protected_root``, and none of the
registered roots (``<home>/secrets``, ``<home>/browser``, ``<home>/undo``, the DB
file and its WAL/SHM sidecars — ``platform.py``) is an ancestor of it, so
``fs_read_ok`` returns ``(True, "")`` for a saved capture and ``view_image`` can
re-open it by absolute path later in the same conversation. Note the near miss:
the artifact **name** starts ``browser/``, but ``ArtifactStore`` slugifies a name
into ONE path segment (``browser_example.com_screenshot-3``) under
``artifacts/``, so a capture never lands inside the protected ``<home>/browser``
vault that holds Computer-Use cookies. ``tests/test_browser_screenshot_v1236.py``
pins both facts against a real ``build_platform``, so if a later ship protects the
artifacts tree this docstring's claim fails loudly instead of quietly becoming
false.

**Fencing is the lane's job, not this module's.** ``browser_screenshot`` declares
``returns_untrusted_content = True``, so all three execution lanes already fence
and injection-scan its output (``agents/runtime.py``, ``daemon/chat_turn.py``,
``daemon/routes/chat.py``). ``WebLookTool`` calls ``wrap_untrusted`` by hand
because it does *not* set that flag; doing both here would double-fence the answer
and teach a model that the markers are noise.

**A refusal is not a crash, and a failed answer is not a failed capture.** When
there is no router, when the vision model returns nothing, or when the provider
raises, this module keeps the artifact it already saved and reports the refusal
alongside it (:attr:`ScreenshotOutcome.refusal`). The user still gets the
screenshot they asked for and the model is told, in the honest words
``tools/images.py`` already uses, why it has no description. The one hard failure
is being unable to persist the bytes at all, which raises
:class:`ScreenshotSaveFailed` carrying a model-facing ``.message`` — see its
docstring for why that is not a ``BROWSER_*`` code.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import itertools
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from ..core.logging import get_logger
from ..providers.roles import resolve_role
from ..tools.images import _MAX_VIEW_BYTES as MAX_VISION_BYTES
from ..tools.images import _NO_VISION_ERROR as NO_VISION_ANSWER
from .errors import BrowserError, BrowserErrorCode

logger = get_logger(__name__)

#: The kind that makes the gallery treat the artifact as an image. Not a default,
#: not a guess: ``routes/knowledge.py`` reads this exact string.
ARTIFACT_KIND = "screenshot"

#: Every capture's artifact name starts here, so one gallery prefix holds every
#: page Jarvis ever looked at: ``browser/<tab-host>/<label>-<n>``. Mirrors
#: ``computeruse/trace.py``'s ``computeruse/<run>/<label>-<n>``.
NAME_PREFIX = "browser"

#: The default ``<label>`` in that name.
DEFAULT_LABEL = "screenshot"

#: What we ask when the caller asked nothing but a question was still wanted.
#: Deliberately about a *page*, not a generic image: the same wording
#: ``WebLookTool`` uses, so the two visual paths describe pages the same way.
DEFAULT_QUESTION = (
    "Describe this page: layout, key elements, visible text, and state."
)

#: The nested call's system prompt. Also ``WebLookTool``'s, verbatim in spirit:
#: the model is looking at a live page belonging to the user, not at art.
VISION_SYSTEM = (
    "You are a precise visual analyst looking at a live web page screenshot. "
    "Answer factually; quote visible text exactly."
)

#: No router wired at all — the platform never handed the runtime a resolver.
#: Distinct from :data:`NO_VISION_ANSWER` (a model that answered nothing) because
#: the remedies differ: one is an install problem, the other a model choice.
NO_VISION_ROUTER = (
    "vision is not wired on this platform (no router) — the screenshot was saved, "
    "so open it or call view_image on its path instead"
)

#: ``media_type`` -> the filename suffix that keeps ``media_kind`` happy. PNG is
#: what ``chrome.tabs.captureVisibleTab`` returns by default; JPEG is its one
#: documented alternative. Anything else is refused rather than saved with a
#: suffix that makes ``GET /creative/file`` answer 415 later.
MEDIA_SUFFIXES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
DEFAULT_MEDIA_TYPE = "image/png"

_counter = itertools.count(1)
_counter_lock = threading.Lock()


class ScreenshotSaveFailed(RuntimeError):
    """The bytes could not be persisted at all — the one hard failure here.

    Carries ``.message``, written for the model that just called the tool, in the
    same shape the tool registry already uses for an argument problem
    ("<what happened> — <what to do>"). The tool lane catches this and returns a
    FAILED ``ToolResult`` carrying ``.message``.

    DELIBERATE DEVIATION from plan §8.6's "a ``BROWSER_*`` code with a remedy",
    for the same reason ``snapshot.UnknownSnapshotMode`` deviates: none of the
    seventeen D15 codes describes a **local** failure. ``EXTENSION_ERROR`` would
    blame a browser that did its job perfectly — it captured the page and
    delivered the bytes — and send the user to ``chrome://extensions`` to fix a
    full disk or an unwired artifact store. An 18th code would break the Ship 1
    pin that the set is exactly seventeen. So the failure stays local and says
    what it is.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def next_capture_number() -> int:
    """The ``<n>`` in ``browser/<host>/<label>-<n>``, monotonic for this process.

    In-process and monotonic, exactly like ``TraceRecorder``'s ``_seq``: its only
    job is keeping sibling captures distinguishable in the gallery within one
    daemon lifetime. The DURABLE identity is the store's own ``version``, which is
    what :attr:`SavedScreenshot.version` reports and what
    ``GET /creative/file/{name}?version=`` addresses — so a restart reusing a
    number costs nothing but a second version under the same name, and no capture
    is ever overwritten. Locked because :func:`save_capture` runs in a worker
    thread; ``itertools.count`` is effectively atomic on CPython today, and
    "effectively, today" is not a thing to build an audit trail on.
    """
    with _counter_lock:
        return next(_counter)


def tab_host(url: str | None, *, tab_id: int | None = None) -> str:
    """The gallery folder for a capture: the page's host, or an honest stand-in.

    Never raises and never returns an empty string, because this value becomes
    part of an artifact name and an empty segment would silently collapse
    ``browser//screenshot-1`` into something that no longer says where the
    picture came from. A URL with no host (``about:blank``, a ``file://`` path, a
    tab whose title and URL were withheld for want of the site grant) falls back
    to ``tab-<id>`` and then to ``unknown-tab``: both are true, and neither
    claims a domain the capture did not come from.
    """
    host = ""
    if url:
        try:
            host = (urlsplit(str(url)).hostname or "").strip().lower()
        except (ValueError, AttributeError):  # a malformed URL is not a crash
            host = ""
    if host:
        return host
    if tab_id is not None:
        return f"tab-{tab_id}"
    return "unknown-tab"


def artifact_name(
    url: str | None = None,
    *,
    tab_id: int | None = None,
    label: str = DEFAULT_LABEL,
    number: int | None = None,
) -> str:
    """``browser/<tab-host>/<label>-<n>`` (plan §10.1), mirroring ``trace.py``.

    The slashes are kept even though ``ArtifactStore`` slugifies them away on
    disk: the NAME is what the gallery, ``GET /creative/file/{name:path}`` and the
    ``ArtifactRecord`` row all carry, and reading ``browser/irs.gov/screenshot-3``
    tells a human where the picture came from without opening it.
    """
    clean_label = str(label or DEFAULT_LABEL).strip() or DEFAULT_LABEL
    n = number if isinstance(number, int) and number > 0 else next_capture_number()
    return f"{NAME_PREFIX}/{tab_host(url, tab_id=tab_id)}/{clean_label}-{n}"


def artifact_url(name: str) -> str:
    """The daemon path the dashboard renders the capture from.

    ``{name:path}`` on that route (not ``{name}``) is why a name with slashes
    resolves at all — ``routes/creative.py`` says so explicitly, having been
    bitten by computer-use screenshots.
    """
    return f"/creative/file/{name}"


def filename_for(media_type: str, label: str = DEFAULT_LABEL) -> str:
    """``<label><suffix>`` — the stored filename, whose suffix decides 415 or 200.

    Raises:
        BrowserError: ``EXTENSION_ERROR`` for a media type this daemon has no
            image suffix for. Refusing beats saving: a file the store accepts and
            the media route then 415s is a capture the user can never open, and
            nothing about that failure points back to here.
    """
    suffix = MEDIA_SUFFIXES.get(str(media_type or "").strip().lower())
    if suffix is None:
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            detail=(
                f"the capture arrived as {media_type!r}, and Iron Jarvis stores "
                f"page captures as {', '.join(sorted(MEDIA_SUFFIXES))}"
            ),
        )
    clean_label = str(label or DEFAULT_LABEL).strip() or DEFAULT_LABEL
    return f"{clean_label}{suffix}"


def decode_capture(result: Mapping[str, Any] | None) -> tuple[bytes, str, int | None]:
    """``(png_bytes, media_type, tab_id)`` from a ``screenshot`` response payload.

    ``validate=True`` on the decode, deliberately: the lenient default DISCARDS
    every character outside the base64 alphabet, so a truncated or
    text-corrupted capture would quietly decode to a shorter, broken image
    instead of failing. And because a non-empty, strictly-valid base64 string
    always decodes to at least one byte, the blank-``data_b64`` check below is
    the whole zero-length guard — there is no separate "decoded to nothing"
    branch, because nothing could reach it.

    Raises:
        BrowserError: ``EXTENSION_ERROR`` for a non-mapping payload, a missing,
            blank or non-string ``data_b64``, or base64 that will not decode.
            All three are the add-on's contract broken, and every one of them is
            silent if unchecked: an empty artifact saves happily, reports a
            plausible path, and shows the user a blank square.
    """
    if not isinstance(result, Mapping):
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            detail=f"the screenshot reply was {type(result).__name__}, not an object",
        )
    raw = result.get("data_b64")
    if not isinstance(raw, str) or not raw.strip():
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            detail="the screenshot reply carried no image data (data_b64)",
        )
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BrowserError(
            BrowserErrorCode.EXTENSION_ERROR,
            detail=f"the screenshot's image data was not valid base64 ({exc})",
        ) from exc
    media_type = str(result.get("media_type") or DEFAULT_MEDIA_TYPE).strip().lower()
    tab = result.get("tab_id")
    return blob, media_type, tab if isinstance(tab, int) else None


@dataclass(frozen=True)
class SavedScreenshot:
    """One persisted capture: what the store wrote, and how to reach it."""

    name: str
    version: int
    kind: str
    filename: str
    path: Path
    size: int

    @property
    def abs_path(self) -> str:
        """The absolute path as a string — the v1.153.2 rule, in the result."""
        return str(self.path)

    @property
    def url(self) -> str:
        return artifact_url(self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.name,
            "version": self.version,
            "kind": self.kind,
            "filename": self.filename,
            "url": self.url,
            "abs_path": self.abs_path,
            "bytes": self.size,
        }


@dataclass(frozen=True)
class VisionAnswer:
    """What the nested vision call produced, or why it produced nothing.

    ``text`` and ``refusal`` are mutually exclusive and one of them is always
    set, so a caller cannot read this as "answered" by forgetting to check a
    flag. ``asked`` distinguishes "no question, so no call" from both.
    """

    text: str = ""
    refusal: str = ""
    provider: str | None = None
    model: str | None = None
    asked: bool = False
    role_applied: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.refusal


@dataclass(frozen=True)
class ScreenshotOutcome:
    """The whole result of ``browser_screenshot``: the file AND the answer.

    :meth:`to_dict` is the tool's ``data`` payload, and it is what lands in the
    ledger's ``output`` column, so it carries the tab context D24 asks for
    (``tab_id``, the page URL, the page title) and never the image bytes.
    """

    saved: SavedScreenshot
    media_type: str
    tab_id: int | None = None
    page_url: str | None = None
    tab_title: str = ""
    question: str = ""
    vision: VisionAnswer = VisionAnswer()

    @property
    def answer(self) -> str:
        return self.vision.text

    @property
    def refusal(self) -> str:
        return self.vision.refusal

    @property
    def answered(self) -> bool:
        return self.vision.ok

    def to_dict(self) -> dict[str, Any]:
        """``{artifact, version, url, abs_path, answer?}`` plus the tab context.

        ``answer`` is present only when a model actually answered (plan §8.6
        writes it ``answer?``): an empty ``answer`` key would read to a model as
        "the page is blank" rather than "nobody looked", and this repository has
        already shipped that class of bug once, on a truncated listing.
        """
        data = self.saved.to_dict()
        data["media_type"] = self.media_type
        data["tab_id"] = self.tab_id
        # Plan section 10.4 asks that every browser tool's ``data`` make the tab
        # reconstructible: ``tab_id``, the page URL and the page TITLE. The page is
        # ``page_url`` and not ``url`` here because ``url`` is already the ARTIFACT
        # route (``SavedScreenshot.to_dict``) — the one key in the browser tier
        # whose meaning differs between tools, which is precisely why it is not
        # reused for the page as well. ``title`` matches the sibling read tools, so
        # an audit of browser rows can name the photographed page the same way for
        # all six.
        data["page_url"] = self.page_url
        data["title"] = self.tab_title
        if self.question:
            data["question"] = self.question
        if self.answered:
            data["answer"] = self.answer
            data["provider"] = self.vision.provider
            data["model"] = self.vision.model
        elif self.refusal:
            data["answer_unavailable"] = self.refusal
        return data

    def summary(self) -> str:
        """The tool's ``output`` — the file first, the description second.

        The file comes first deliberately: it is the part that is certainly true.
        A model that reads only the first line still relays a real path to the
        user, and a refused description never reads as a described page.
        """
        lines = [
            f"Saved a screenshot of the page as artifact {self.saved.name} "
            f"(v{self.saved.version}) at {self.saved.abs_path}"
        ]
        if self.answered:
            lines.append(self.answer)
        elif self.refusal:
            lines.append(f"No description: {self.refusal}")
        return "\n".join(lines)


async def save_capture(
    artifacts: Any,
    data_b64_or_bytes: str | bytes,
    *,
    name: str,
    filename: str,
    session_id: str | None = None,
    project_id: str | None = None,
) -> SavedScreenshot:
    """Persist one capture as an artifact version, off the event loop.

    Everything blocking happens inside ONE :func:`asyncio.to_thread` hop: the
    base64 decode (CPU), the file write, the SQLite row, and the
    :meth:`Path.resolve` that makes ``abs_path`` absolute (a filesystem call on
    Windows). One hop rather than three, because each transition is a scheduling
    round trip and none of this work needs to interleave with anything.

    Raises:
        ScreenshotSaveFailed: when there is no artifact store on this platform, or
            when the store raises (a full disk, a vanished home, a locked DB). The
            capture is genuinely gone in that case — there is nothing on disk to
            point the user at — so unlike a failed vision call this is a failure
            and says so.
    """
    if artifacts is None:
        raise ScreenshotSaveFailed(
            "the screenshot could not be saved: this install has no artifact "
            "store wired, so there is nowhere to put it — report this, and read "
            "the page with browser_read_page instead"
        )

    def _write() -> SavedScreenshot:
        blob = (
            data_b64_or_bytes
            if isinstance(data_b64_or_bytes, (bytes, bytearray))
            else base64.b64decode(str(data_b64_or_bytes), validate=True)
        )
        artifact = artifacts.save(
            name,
            bytes(blob),
            kind=ARTIFACT_KIND,
            filename=filename,
            session_id=session_id,
            project_id=project_id,
        )
        raw_path = Path(str(getattr(artifact, "path", "")))
        try:
            resolved = raw_path.resolve()
        except OSError:  # an unresolvable path is still absolute-able
            resolved = Path(os.path.abspath(str(raw_path)))
        return SavedScreenshot(
            name=str(getattr(artifact, "name", name)),
            version=int(getattr(artifact, "version", 1) or 1),
            kind=str(getattr(artifact, "kind", ARTIFACT_KIND) or ARTIFACT_KIND),
            filename=resolved.name or filename,
            path=resolved,
            size=int(getattr(artifact, "size", 0) or 0),
        )

    try:
        return await asyncio.to_thread(_write)
    except ScreenshotSaveFailed:
        raise
    except (OSError, binascii.Error, ValueError) as exc:
        raise ScreenshotSaveFailed(
            f"the screenshot could not be saved ({type(exc).__name__}: {exc}) — "
            "check that Iron Jarvis's data folder is writable, then retry"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — never a traceback to a model
        raise ScreenshotSaveFailed(
            f"the screenshot could not be saved ({type(exc).__name__}: {exc}) — "
            "retry, and if it persists report it with this message"
        ) from exc


async def ask_vision(
    png: bytes,
    question: str,
    *,
    router_resolver: Callable[[], Any] | None,
    config: Any = None,
    media_type: str = DEFAULT_MEDIA_TYPE,
    session_id: str | None = None,
) -> VisionAnswer:
    """Show ``png`` to the vision model and return its answer, or the refusal.

    The route is ``tools/images.py``'s, step for step, because there must be one
    way this app looks at an image: resolve the ``vision`` ROLE through
    :func:`~iron_jarvis.providers.roles.resolve_role` (RESOLUTION only — the call
    still goes through ``router.complete``, so failover, health and strict-pin
    semantics are unchanged, and an unmapped role adds no kwargs at all so a
    narrow fake router keeps working), then send one ``LLMMessage`` carrying
    ``images=[{"data_b64", "media_type"}]``.

    Never raises. Every miss becomes a refusal in words a model can act on, and
    the caller has already saved the file, so a refusal costs the user nothing but
    the description:

    * no resolver, or a resolver that hands back nothing → :data:`NO_VISION_ROUTER`
    * bytes over the provider payload cap → a refusal naming the size
    * the provider raised → a refusal naming the failure
    * the model answered with empty text → :data:`NO_VISION_ANSWER`, the honest
      sentence ``tools/images.py`` already uses for "this model has no eyes".
      Guessing at a description instead is the fabrication rule (never let a
      failure return invented output), and a screenshot description is exactly
      the kind of text a user would believe.
    """
    from ..providers.adapters.base import LLMMessage

    router = None
    if router_resolver is not None:
        try:
            router = router_resolver()
        except Exception:  # noqa: BLE001 — a broken resolver is a refusal
            logger.debug("browser screenshot: router_resolver raised", exc_info=True)
            router = None
    if router is None:
        return VisionAnswer(refusal=NO_VISION_ROUTER, asked=True)
    if len(png) > MAX_VISION_BYTES:
        return VisionAnswer(
            asked=True,
            refusal=(
                f"the capture is {len(png)} bytes, over the "
                f"{MAX_VISION_BYTES // (1024 * 1024)}MB vision limit — it was "
                "saved, so open it or read the page with browser_read_page"
            ),
        )

    vision = resolve_role(
        config,
        getattr(router, "manager", None),
        "vision",
        fallback_provider=None,
        fallback_model=None,
    )
    route_kwargs: dict[str, Any] = (
        {"provider": vision.provider, "model": vision.model} if vision.applied else {}
    )
    message = LLMMessage(
        role="user",
        content=question or DEFAULT_QUESTION,
        images=[
            {
                "data_b64": base64.b64encode(png).decode("ascii"),
                "media_type": media_type or DEFAULT_MEDIA_TYPE,
            }
        ],
    )
    try:
        result = await router.complete(
            system=VISION_SYSTEM,
            messages=[message],
            tools=[],
            session_id=session_id,
            **route_kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — a provider failure is a refusal
        logger.debug("browser screenshot: vision call failed", exc_info=True)
        return VisionAnswer(
            asked=True,
            refusal=(
                f"the vision call failed ({type(exc).__name__}: {exc}) — the "
                "screenshot was saved, so retry or open it"
            ),
            role_applied=vision.applied,
        )
    text = (getattr(getattr(result, "response", None), "text", "") or "").strip()
    if not text:
        return VisionAnswer(
            asked=True, refusal=NO_VISION_ANSWER, role_applied=vision.applied
        )
    return VisionAnswer(
        text=text,
        provider=getattr(result, "provider", None),
        model=getattr(result, "model", None),
        asked=True,
        role_applied=vision.applied,
    )


async def capture_screenshot(
    result: Mapping[str, Any] | None,
    *,
    artifacts: Any,
    router_resolver: Callable[[], Any] | None = None,
    config: Any = None,
    question: str | None = None,
    tab_url: str | None = None,
    tab_id: int | None = None,
    tab_title: str | None = None,
    label: str = DEFAULT_LABEL,
    session_id: str | None = None,
    project_id: str | None = None,
) -> ScreenshotOutcome:
    """The composition: decode → save → (optionally) look. Save always happens first.

    The order is the contract. The artifact is written before any model is asked,
    so a vision failure can never lose the user's screenshot, and the answer —
    when there is one — is about the exact bytes on disk rather than about a
    second capture taken a moment later.

    ``question`` is optional (plan §10.1): omitted or blank means no nested call
    and therefore no model cost, and :meth:`ScreenshotOutcome.to_dict` then
    carries no ``answer`` key at all rather than an empty one.

    Raises:
        BrowserError: ``EXTENSION_ERROR`` for a malformed screenshot payload.
        ScreenshotSaveFailed: when the bytes cannot be persisted.
    """
    png, media_type, payload_tab = decode_capture(result)
    # The title travels on the capture payload itself (``screenshot_capture`` adds
    # ``tab_url``/``tab_title`` beside the add-on's own reply), so the tool does not
    # have to unpack it a second time and the two can never disagree about which
    # tab was photographed. An explicit argument still wins, for a caller that
    # already knows the page.
    title = str(
        (tab_title if tab_title is not None else result.get("tab_title")) or ""
    ).strip()
    filename = filename_for(media_type, label)
    name = artifact_name(tab_url, tab_id=tab_id if tab_id is not None else payload_tab, label=label)
    saved = await save_capture(
        artifacts,
        png,
        name=name,
        filename=filename,
        session_id=session_id,
        project_id=project_id,
    )
    asked = str(question or "").strip()
    vision = VisionAnswer()
    if asked:
        vision = await ask_vision(
            png,
            asked,
            router_resolver=router_resolver,
            config=config,
            media_type=media_type,
            session_id=session_id,
        )
    return ScreenshotOutcome(
        saved=saved,
        media_type=media_type,
        tab_id=tab_id if tab_id is not None else payload_tab,
        page_url=tab_url,
        tab_title=title,
        question=asked,
        vision=vision,
    )


async def capture_for_tool(
    result: Mapping[str, Any] | None,
    *,
    browser: Any,
    ctx: Any,
    question: str | None = None,
    tab_url: str | None = None,
    tab_id: int | None = None,
    tab_title: str | None = None,
    label: str = DEFAULT_LABEL,
) -> ScreenshotOutcome:
    """:func:`capture_screenshot` with the pieces read off the runtime and context.

    The one call ``browser_screenshot`` needs. Every collaborator is read here,
    once, rather than in the tool: ``artifacts`` and ``router_resolver`` belong to
    :class:`~iron_jarvis.browser.service.BrowserRuntime`, and ``session_id`` /
    ``project_id`` / ``config`` to the :class:`~iron_jarvis.tools.base.ToolContext`.

    ``project_id`` is read from the context and passed explicitly because chat
    runs as ``session_id="chat"`` — not a ``Session`` row — so the store's
    inherit-from-session path finds nothing and a chat screenshot would strand in
    the global gallery with ``project_id`` NULL (the v1.200.0 audit finding,
    documented on ``ArtifactStore.save`` itself).

    ``getattr`` throughout rather than attribute access: a half-built runtime (or
    a test that wires two things) must produce this module's own refusals, not an
    ``AttributeError`` the registry would render as a traceback.
    """
    return await capture_screenshot(
        result,
        artifacts=getattr(browser, "artifacts", None),
        router_resolver=getattr(browser, "router_resolver", None),
        config=getattr(ctx, "config", None) or getattr(browser, "config", None),
        question=question,
        tab_url=tab_url,
        tab_id=tab_id,
        tab_title=tab_title,
        label=label,
        session_id=getattr(ctx, "session_id", None),
        project_id=getattr(ctx, "project_id", None),
    )


__all__ = [
    "ARTIFACT_KIND",
    "DEFAULT_LABEL",
    "DEFAULT_MEDIA_TYPE",
    "DEFAULT_QUESTION",
    "MAX_VISION_BYTES",
    "MEDIA_SUFFIXES",
    "NAME_PREFIX",
    "NO_VISION_ANSWER",
    "NO_VISION_ROUTER",
    "VISION_SYSTEM",
    "SavedScreenshot",
    "ScreenshotOutcome",
    "ScreenshotSaveFailed",
    "VisionAnswer",
    "artifact_name",
    "artifact_url",
    "ask_vision",
    "capture_for_tool",
    "capture_screenshot",
    "decode_capture",
    "filename_for",
    "next_capture_number",
    "save_capture",
    "tab_host",
]
