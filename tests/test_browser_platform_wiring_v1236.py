"""v1.236.0 — the Browser runtime the APP builds, not the one a test builds.

Every other browser test constructs its own :class:`BrowserRuntime` with
``snapshots=SnapshotCache()``, ``artifacts=<a store>`` and
``router_resolver=<a fake>`` handed in by the test itself. That is the right
shape for testing the runtime, and it is exactly why the runtime the *user* gets
was completely unpinned: the collaborators under test were supplied by the test.

The gap was measured, not guessed. Deleting ``snapshots=SnapshotCache()`` from
``platform.py`` left 379 tests green — including every snapshot, read-tool and
screenshot test — because ``BrowserRuntime._cache_snapshot`` deliberately returns
an uncached snapshot when it has no cache, so the whole staleness feature simply
stops existing and nothing says so. Deleting ``artifacts=artifacts`` left the same
379 green while every real screenshot would fail to save. That is this
repository's own recurring failure — a green suite over a feature no user can
reach — and this file is the pin for it.

So each test here does two things and needs both:

* names the collaborator on the runtime ``build_platform`` actually returned, and
* **drives it**, because a non-null attribute is not a working one. A read has to
  land IN the cache, a capture has to land IN the app's artifact tree, and the
  vision call has to reach the app's OWN router.

The transport is the only thing replaced: one scripted peer stands in for a
Chrome that is not running, installed on the real backend object the platform
built, so the runtime, the service methods, the snapshot module and the
screenshot module are all the real ones.

The last section pins Ship 2's docs deliverable for the same reason: the Guide
answers out of ``docs/COMPUTER-USE.md`` (it is in ``guide/corpus.BUNDLED_DOCS``),
so a stale sentence there is not a stale sentence — it is Iron Jarvis telling a
user, confidently, to open a nav item that no longer exists.

No assertion here measures elapsed time, every spy takes ``*args, **kw``, and
every source pin normalises CRLF at the reader.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser import screenshot as S
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.browser.snapshot import PageSnapshot, SnapshotCache

from ._fakes.browser_peer import BrowserPeer, FakeElement, FakeTab, ScriptedBrowser
from .test_browser_tools_v1235 import FakeBackend

#: The tab every test here reads. Given a title so the ledger row has something
#: to name, and real text so a cached snapshot is recognisable.
TAB_ID = 42


def _page() -> ScriptedBrowser:
    return ScriptedBrowser(
        [
            FakeTab(
                id=TAB_ID,
                title="IRS | Payments",
                url="https://www.irs.gov/payments",
                active=True,
                text="Pay your taxes here. Direct Pay is free.",
                headings=[{"level": 1, "text": "Payments"}],
                elements=[FakeElement(id="e1", role="button", name="Pay now", text="Pay now")],
            )
        ]
    )


def _install(platform, peer: BrowserPeer) -> BrowserPeer:
    """Put ``peer`` behind the REAL runtime's transport, and open the access gate.

    ``backend.command`` is replaced rather than the backend itself, so the object
    ``build_platform`` wired (``access_reader``, ``snapshot_invalidator``) is the
    one still in place. ``browser_access`` ships ``off``; the runtime reads it LIVE
    on every call, so setting it here is exactly what a ``PUT /settings`` does.
    """
    backend = FakeBackend(peer)

    async def command(
        method: str, params: dict[str, Any] | None = None, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        return await backend.command(method, params, timeout_s=timeout_s)

    platform.browser.backend.command = command  # type: ignore[method-assign]
    platform.config.browser_access = "read_only"
    return peer


def _wire(platform) -> BrowserPeer:
    return _install(platform, BrowserPeer(None, page=_page()))


class _Ctx:
    """The three fields ``capture_for_tool`` reads off a ``ToolContext``."""

    def __init__(self, project_id: str | None = None) -> None:
        self.session_id = "chat"
        self.project_id = project_id
        self.config = None


class _Response:
    text = "A payments page with a Pay now button."


class _Result:
    response = _Response()
    provider = "fake"
    model = "fake-vision"


def _watch_router(platform) -> list[dict[str, Any]]:
    """Record what the platform's OWN router was asked, and answer plausibly."""
    seen: list[dict[str, Any]] = []

    async def complete(*args, **kw):  # spy takes *args/**kw
        seen.append(kw)
        return _Result()

    platform.router.complete = complete  # type: ignore[method-assign]
    return seen


# --- snapshots ---------------------------------------------------------------


async def test_the_apps_browser_caches_the_page_it_just_read(platform):
    """``snapshots`` is a real bounded cache AND a read fills it.

    The second half is the one that bites. With the cache missing,
    ``_cache_snapshot`` returns the snapshot uncached on purpose (losing a page the
    user asked for would be worse than a later ``STALE_SNAPSHOT``), so every read
    still answers, every existing test still passes, and every element id a later
    ship resolves is stale forever with re-reading no help — because no read ever
    caches.
    """
    _wire(platform)
    assert isinstance(platform.browser.snapshots, SnapshotCache)

    page = await platform.browser.read_page()

    snapshot_id = page["snapshot_id"]
    assert snapshot_id
    assert platform.browser.snapshots.latest_id(TAB_ID) == snapshot_id
    held = platform.browser.snapshots.get(TAB_ID)
    assert isinstance(held, PageSnapshot)
    assert held.snapshot_id == snapshot_id
    assert "Direct Pay" in held.text


async def test_a_second_read_replaces_the_first_in_the_apps_own_cache(platform):
    """One snapshot per tab, on the runtime the app built — not on a test's copy."""
    _wire(platform)

    first = await platform.browser.read_page()
    second = await platform.browser.read_page()

    assert first["snapshot_id"] != second["snapshot_id"]
    assert platform.browser.snapshots.latest_id(TAB_ID) == second["snapshot_id"]
    assert len(platform.browser.snapshots) == 1


async def test_the_transport_can_invalidate_the_apps_cache(platform):
    """The navigation hook the platform installs reaches the platform's cache.

    ``platform.py`` hands the backend ``snapshot_invalidator`` because only the
    socket hears a navigation. With no cache behind it that callable is a no-op
    nothing notices, and the daemon keeps handing out an id for a page that has
    already been replaced.
    """
    _wire(platform)
    await platform.browser.read_page()
    assert platform.browser.snapshots.latest_id(TAB_ID)

    platform.browser.backend.snapshot_invalidator(TAB_ID)

    assert platform.browser.snapshots.latest_id(TAB_ID) == ""
    assert platform.browser.snapshots.get(TAB_ID) is None


# --- artifacts ---------------------------------------------------------------


async def test_a_screenshot_lands_in_the_apps_own_artifact_store(platform):
    """``artifacts`` is the app's store, and a capture really reaches it.

    Dropped, every screenshot in the real app fails to save while the suite stays
    clean, because every screenshot test brings a store of its own.
    """
    _wire(platform)
    assert platform.browser.artifacts is platform.artifacts

    capture = await platform.browser.screenshot_capture()
    outcome = await S.capture_for_tool(
        capture,
        browser=platform.browser,
        ctx=_Ctx(project_id="proj_7"),
        tab_url=capture.get("tab_url"),
        tab_id=capture.get("tab_id"),
        tab_title=capture.get("tab_title"),
    )

    path = Path(outcome.saved.abs_path)
    assert path.is_absolute() and path.is_file()
    # In the APP's tree, which is the whole claim: a store of the test's own would
    # have satisfied every other assertion here.
    assert Path(platform.config.artifacts_dir).resolve() in path.parents
    assert outcome.saved.kind == "screenshot"
    # And the tab context §10.4 asks for, read off the real service seam.
    data = outcome.to_dict()
    assert data["title"] == "IRS | Payments"
    assert data["page_url"] == "https://www.irs.gov/payments"
    assert data["tab_id"] == TAB_ID


# --- the router --------------------------------------------------------------


async def test_the_vision_call_reaches_the_apps_own_router(platform):
    """``router_resolver`` resolves to the platform's router, and is really used.

    Dropped, ``browser_screenshot`` answers ``NO_VISION_ROUTER`` — "this install
    has no model router" — for every user, forever, with the suite green because
    every screenshot test supplies a resolver of its own.
    """
    _wire(platform)
    assert callable(platform.browser.router_resolver)
    assert platform.browser.router_resolver() is platform.router
    seen = _watch_router(platform)

    capture = await platform.browser.screenshot_capture()
    outcome = await S.capture_for_tool(
        capture,
        browser=platform.browser,
        ctx=_Ctx(),
        question="What is on this page?",
        tab_url=capture.get("tab_url"),
        tab_id=capture.get("tab_id"),
    )

    assert outcome.refusal != S.NO_VISION_ROUTER
    assert outcome.answered is True
    assert outcome.answer == _Response.text
    (call,) = seen
    (message,) = call["messages"]
    (image,) = message.images
    assert image["media_type"] == "image/png"
    # Follow the bytes: the model saw the file that reached the app's store.
    assert base64.b64decode(image["data_b64"]) == Path(outcome.saved.abs_path).read_bytes()


# --- the media type, across the service seam ---------------------------------


class _JpegPeer(BrowserPeer):
    """A peer whose capture is a JPEG — a real reply on a large window.

    ``background/tabs.ts`` tries PNG and falls back through three JPEG qualities
    when the PNG is over the frame budget, so this is not a hypothetical shape.
    """

    def _h_screenshot(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._h_screenshot(params)
        result["media_type"] = "image/jpeg"
        return result


async def test_a_jpeg_capture_keeps_its_media_type_all_the_way_to_the_model(platform):
    """The media type survives the SERVICE seam, not just ``filename_for``.

    ``screenshot_capture`` preserves ``media_type`` only because it copies the
    whole reply. Copy just the bytes instead and every test stays green while the
    file is stored as ``.png`` holding JPEG bytes, ``GET /creative/file`` serves it
    as ``image/png``, and the vision call declares a media type the image does not
    have — which some providers reject outright. So the type is followed from the
    peer's reply to the stored filename and on into the model's message.
    """
    _install(platform, _JpegPeer(None, page=_page()))
    seen = _watch_router(platform)

    capture = await platform.browser.screenshot_capture()
    assert capture["media_type"] == "image/jpeg"

    outcome = await S.capture_for_tool(
        capture,
        browser=platform.browser,
        ctx=_Ctx(),
        question="what is this?",
        tab_url=capture.get("tab_url"),
        tab_id=capture.get("tab_id"),
    )

    assert outcome.saved.filename.endswith(".jpg")
    assert Path(outcome.saved.abs_path).name.endswith(".jpg")
    assert outcome.to_dict()["media_type"] == "image/jpeg"
    (call,) = seen
    (message,) = call["messages"]
    assert message.images[0]["media_type"] == "image/jpeg"


# --- the gate is still the gate ----------------------------------------------


async def test_the_apps_browser_still_ships_off(platform):
    """None of the above is reachable until the user turns it on.

    The wiring exists unconditionally so the Your browser card, ``/health`` and the
    doctor can tell the truth about a browser that is not connected; the CAPABILITY
    is the setting, which ships ``off``. Pinned here because every other test in
    this file opens the gate, and a wiring test that quietly proved the feature was
    on by default would be the worst possible outcome.
    """
    _wire(platform)
    platform.config.browser_access = "off"

    with pytest.raises(BrowserError) as excinfo:
        await platform.browser.read_page()

    assert excinfo.value.code == BrowserErrorCode.BROWSER_ACCESS_OFF.value
    assert platform.browser.snapshots.latest_id(TAB_ID) == ""
    assert P.METHOD_READ_PAGE == "read_page"


# --- the doc the Guide answers out of ----------------------------------------

REPO = Path(__file__).resolve().parents[1]
COMPUTER_USE_DOC = REPO / "docs" / "COMPUTER-USE.md"
NAV = REPO / "dashboard" / "lib" / "nav.ts"


def _read_normalised(path: Path) -> str:
    """Read a source file with CRLF folded to LF — normalise at the READER, once.

    CI checks out with ``core.autocrlf=true``; a needle carrying an embedded
    newline matches every local run and never matches there (v1.232.1).
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _flat(path: Path) -> str:
    """The same text with every run of whitespace collapsed to one space.

    A prose needle spans a line wrap in a hand-wrapped Markdown file, and a
    fixed window around a match breaks the moment a sentence is reflowed. Both
    traps are removed by flattening once, at the reader.
    """
    return " ".join(_read_normalised(path).split())


def test_the_computer_use_doc_tells_the_two_browsers_apart():
    """Ship 2's named docs deliverable: which browser is which, in that file.

    Two browser features landed on one dashboard page, and this doc knew about
    only one of them — while being one of the files the Guide retrieves from. A
    reader (or the Guide) answering "is that my Chrome?" out of a doc that has
    never heard of the add-on gets a confident wrong answer.
    """
    doc = _flat(COMPUTER_USE_DOC)

    # Both features are named, in the repository's own vocabulary.
    assert "Your browser" in doc
    assert "add-on" in doc
    assert "Pair" in doc
    # And they are told APART: separate browser, no shared cookies.
    assert "headless Chromium" in doc
    assert "shares no cookies" in doc


def test_the_computer_use_doc_does_not_oversell_your_browser():
    """v1.236.0 reads and photographs. It does not click, type or navigate.

    The tools that exist are the claim's evidence: this asserts the doc's promise
    against the ROSTER, so a doc that grows an ability before the tool does goes
    red rather than shipping as a lie the Guide repeats.
    """
    from iron_jarvis.browser.tools import browser_tools

    names = {tool.name for tool in browser_tools(None)}
    assert names == {
        "browser_get_status",
        "browser_list_tabs",
        "browser_get_active_tab",
        "browser_read_page",
        "browser_get_elements",
        "browser_screenshot",
    }

    assert "It cannot click, type or navigate." in _flat(COMPUTER_USE_DOC)


def test_the_computer_use_doc_names_the_nav_item_that_exists():
    """The route was relabelled **Browser** in v1.235.0; the doc said Computer Use.

    A user following the old instruction looks for a sidebar entry that is not
    there. The doc's label is checked against ``nav.ts`` itself rather than
    against a string typed twice, so the next rename fails here instead of in
    somebody's support question.
    """
    nav = _read_normalised(NAV)
    label = nav.split('href: "/computeruse"', 1)[1].split("label:", 1)[1]
    label = label.split(",", 1)[0].strip().strip('"')
    assert label == "Browser", f"nav.ts labels /computeruse {label!r}"

    doc = _flat(COMPUTER_USE_DOC)
    assert f"Dashboard → **{label}**" in doc
    # The dead instruction is gone, not merely joined by a live one.
    assert "Dashboard → **Computer Use**" not in doc


def test_the_computer_use_doc_is_one_the_guide_actually_carries():
    """The claim above only matters because this file is bundled for the Guide."""
    from iron_jarvis.guide.corpus import BUNDLED_DOCS

    assert any(entry[1] == "docs/COMPUTER-USE.md" for entry in BUNDLED_DOCS)
