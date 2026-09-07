"""v1.236.0 — a browser screenshot is a real artifact AND something a model saw (D14, §10.1).

``browser_screenshot`` is a composition of two halves this repository already had
and had never joined: ``computeruse/trace.py`` persists a capture no model ever
sees, and ``computeruse/tools.py``'s ``WebLookTool`` shows a model a capture
nothing keeps. This file pins the join, and every assertion below names a failure
that is INVISIBLE without it:

* **``kind="screenshot"`` and a ``.png`` filename.** Both are strings that other
  code reads. ``kind`` is what makes ``routes/knowledge.py`` report the artifact
  as an image; the suffix is what stops ``GET /creative/file/{name}`` answering
  **415**. Get either wrong and the capture saves, indexes, reports a plausible
  path — and the user can never see it. So the stored filename is checked through
  ``creative.service.media_kind``, the same function the route uses, rather than
  against a string of our own.
* **``abs_path`` is absolute.** The v1.153.2 rule. A relative answer is a bare
  filename whenever the file lands in the artifacts root, the model relays it,
  and the user looks in the wrong folder.
* **The bytes reach the model.** Following the bytes is the reviewer question the
  plan writes down ("Does the screenshot reach the model, or only the disk?"), so
  the test decodes the ``data_b64`` the router was handed and compares it to the
  PNG on disk. A vision call carrying the right ``media_type`` and the WRONG image
  is a bug no smaller assertion catches.
* **No vision model is a refusal, not a crash, and never a fabrication.** Three
  ways to have no eyes — no router, a model that answers nothing, a provider that
  raises — and all three must keep the saved artifact and say why there is no
  description, in the honest words ``tools/images.py`` already uses.
* **``save()`` runs OFF the event loop.** ``ArtifactStore.save`` writes a file and
  commits a SQLite row; the daemon is ONE loop (the v1.153.1 outage, which the
  user experienced as "Daemon offline"). Asserted by capturing the THREAD the save
  ran on — never by timing anything.

And one thing the plan asks be VERIFIED rather than assumed (§10.1's last
paragraph): whether ``config.artifacts_dir`` sits under a
``register_protected_root``, which would stop ``view_image`` reopening the capture
by absolute path. ``test_saved_capture_is_readable_by_absolute_path`` answers it
against a REAL ``build_platform``: it is not protected, ``fs_read_ok`` says yes,
and the near miss is pinned too — the artifact name starts ``browser/`` while the
protected Computer-Use vault is ``<home>/browser``, and the store's slugify is the
only reason those do not collide.

No assertion here measures elapsed time, every spy takes ``*args, **kw`` and calls
through, and each source pin normalises CRLF at the reader.
"""

from __future__ import annotations

import asyncio
import base64
import threading
from pathlib import Path

import pytest

from iron_jarvis.artifacts.store import ArtifactStore
from iron_jarvis.browser import screenshot as S
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.creative.service import media_kind

REPO = Path(__file__).resolve().parents[1]
SCREENSHOT_SOURCE = REPO / "src" / "iron_jarvis" / "browser" / "screenshot.py"

#: A real 1x1 PNG — the same bytes the deterministic peer sends, so this file and
#: ``tests/_fakes/browser_peer.py`` agree about what a capture looks like.
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4z8AAAAMBAQDN"
    "hb0OAAAAAElFTkSuQmCC"
)
PNG_BYTES = base64.b64decode(PNG_B64)


def _read_normalised(path: Path) -> str:
    """Read a source file with CRLF folded to LF — normalise at the READER, once."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _result(**over) -> dict:
    """A believable ``screenshot`` response payload, overridable per test."""
    base = {"tab_id": 7, "media_type": "image/png", "data_b64": PNG_B64}
    base.update(over)
    return base


class _Ctx:
    """The two fields ``capture_for_tool`` reads off a ``ToolContext``."""

    def __init__(self, session_id: str = "chat", project_id: str | None = None) -> None:
        self.session_id = session_id
        self.project_id = project_id
        self.config = None


class _Runtime:
    """The two collaborators ``capture_for_tool`` reads off a ``BrowserRuntime``."""

    def __init__(self, artifacts, router=None) -> None:
        self.artifacts = artifacts
        self.router_resolver = (lambda: router) if router is not None else None
        self.config = None


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text


class _RouteResult:
    def __init__(self, text: str) -> None:
        self.response = _Response(text)
        self.provider = "fake"
        self.model = "fake-vision"


class FakeRouter:
    """Records the exact completion request; returns a RouteResult-alike.

    Shaped after ``tests/test_image_tools.py``'s fake, because this call is meant
    to be the SAME nested vision call ``view_image`` makes — a fake that only this
    file's code satisfies would hide a divergence.
    """

    def __init__(self, text: str = "An IRS payment page with two fields.") -> None:
        self.text = text
        self.calls: list[dict] = []

    async def complete(
        self,
        *,
        system,
        messages,
        tools,
        session_id=None,
        provider=None,
        model=None,
        task_class=None,
    ):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "session_id": session_id,
                "provider": provider,
                "model": model,
            }
        )
        return _RouteResult(self.text)


class ExplodingRouter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("provider melted")


class ThreadWatchingStore(ArtifactStore):
    """A REAL store that also records which thread each ``save`` ran on.

    Subclassed rather than monkeypatched so the save under test is the real one —
    a real file write and a real version number — with one extra fact recorded.
    """

    def __init__(self, root) -> None:
        super().__init__(root)
        self.threads: list[str] = []

    def save(self, *args, **kw):  # spy takes *args/**kw and calls through
        self.threads.append(threading.current_thread().name)
        return super().save(*args, **kw)


# --- the artifact: kind, filename, path ------------------------------------------


async def test_capture_saves_a_screenshot_kind_png_artifact(tmp_path):
    """The two strings other code reads: ``kind="screenshot"`` and a ``.png`` name."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_screenshot(
        _result(), artifacts=store, tab_url="https://www.irs.gov/payments"
    )

    assert out.saved.kind == "screenshot"
    assert out.saved.path.name.endswith(".png")
    assert out.saved.path.read_bytes() == PNG_BYTES
    assert out.saved.size == len(PNG_BYTES)
    # The route's OWN predicate, not a string of ours: this is what decides
    # whether GET /creative/file/{name} serves the file or answers 415.
    assert media_kind(out.saved.path.name) == "image"
    assert out.to_dict()["url"] == f"/creative/file/{out.saved.name}"


async def test_artifact_name_is_browser_slash_host_slash_label_n(tmp_path):
    """``browser/<tab-host>/<label>-<n>`` (§10.1), mirroring ``trace.py``."""
    store = ArtifactStore(tmp_path / "artifacts")
    first = await S.capture_screenshot(
        _result(), artifacts=store, tab_url="https://www.irs.gov/payments?x=1"
    )
    second = await S.capture_screenshot(
        _result(), artifacts=store, tab_url="https://www.irs.gov/payments?x=1"
    )

    assert first.saved.name.startswith("browser/www.irs.gov/screenshot-")
    # Monotonic within the process, so two captures of one page are two artifacts
    # rather than one that silently overwrote the other.
    assert first.saved.name != second.saved.name
    # And a capture never lands in the PROTECTED <home>/browser vault: the store
    # slugifies the whole name into one segment under artifacts/.
    assert first.saved.path.parent.parent.name.startswith("browser_www.irs.gov_")


async def test_abs_path_is_absolute(tmp_path):
    """v1.153.2: a tool that writes a file says where, ABSOLUTELY."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_screenshot(_result(), artifacts=store, tab_url="https://a.test/")

    data = out.to_dict()
    assert Path(data["abs_path"]).is_absolute()
    assert Path(data["abs_path"]).is_file()
    assert data["abs_path"] == str(out.saved.path)


async def test_a_tab_with_no_readable_url_still_names_the_tab(tmp_path):
    """No host is not "unknown": a withheld URL still says which tab it was."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_screenshot(_result(tab_id=31), artifacts=store, tab_url=None)
    assert out.saved.name.startswith("browser/tab-31/screenshot-")
    assert S.tab_host(None) == "unknown-tab"
    assert S.tab_host("about:blank") == "unknown-tab"


# --- the save runs off the event loop --------------------------------------------


async def test_save_runs_off_the_event_loop(tmp_path):
    """v1.153.1: a file write plus a SQLite commit never runs on the ONE loop.

    The thread is the assertion. Nothing here measures duration — a wall-clock
    threshold would be measuring the machine.
    """
    store = ThreadWatchingStore(tmp_path / "artifacts")
    await S.capture_screenshot(_result(), artifacts=store, tab_url="https://a.test/")

    assert store.threads, "the store's save() was never called"
    assert store.threads[0] != threading.main_thread().name
    assert store.threads[0] != threading.current_thread().name


async def test_the_loop_keeps_ticking_while_a_capture_is_saved(tmp_path):
    """The other half of the offload: a slow save does not stall the loop.

    A blocking save that happened to be fast would pass the thread assertion by
    accident on a fast machine. Here the save waits on an Event only a coroutine
    running on the loop can set, and RECORDS whether it was released before it
    returned. Run on the loop, that coroutine cannot run until the save is over,
    so the wait times out and the recorded answer is False — a deterministic
    failure, not a slow pass. The bounded wait is a bound, not a duration
    assertion: nothing here compares elapsed time to a threshold.
    """
    released = threading.Event()
    concurrent: list[bool] = []
    store = ArtifactStore(tmp_path / "artifacts")
    real_save = store.save

    def blocking_save(*args, **kw):
        concurrent.append(released.wait(10))
        return real_save(*args, **kw)

    store.save = blocking_save  # type: ignore[method-assign]

    async def let_it_go() -> str:
        await asyncio.sleep(0)
        released.set()
        return "loop kept ticking"

    task = asyncio.ensure_future(let_it_go())
    out = await S.capture_screenshot(_result(), artifacts=store, tab_url="https://a.test/")
    assert await task == "loop kept ticking"
    assert concurrent == [True], "the save blocked the event loop"
    assert out.saved.path.is_file()


# --- the vision call --------------------------------------------------------------


async def test_vision_call_carries_the_png_and_its_media_type(tmp_path):
    """Follow the bytes: the model is shown the same image that reached the disk."""
    store = ArtifactStore(tmp_path / "artifacts")
    router = FakeRouter()
    out = await S.capture_for_tool(
        _result(),
        browser=_Runtime(store, router),
        ctx=_Ctx(session_id="s-42"),
        question="What is on this page?",
        tab_url="https://www.irs.gov/payments",
    )

    (call,) = router.calls
    assert call["tools"] == []
    assert call["session_id"] == "s-42"
    assert "visual analyst" in call["system"]
    # An unmapped vision role adds NO kwargs, so a narrow router keeps working.
    assert call["provider"] is None and call["model"] is None
    (message,) = call["messages"]
    assert message.role == "user"
    assert "What is on this page?" in message.content
    (image,) = message.images
    assert image["media_type"] == "image/png"
    assert base64.b64decode(image["data_b64"]) == out.saved.path.read_bytes()

    assert out.answered is True
    assert out.answer == router.text
    data = out.to_dict()
    assert data["answer"] == router.text
    assert data["model"] == "fake-vision"
    assert out.summary().startswith("Saved a screenshot")
    assert router.text in out.summary()


async def test_a_mapped_vision_role_pins_the_call(tmp_path, monkeypatch):
    """``resolve_role`` resolution reaches ``complete`` exactly as in images.py."""

    class _Manager:
        def available(self, provider: str) -> bool:
            return True

    class _Config:
        model_roles = {"vision": "anthropic:claude-vision"}

    store = ArtifactStore(tmp_path / "artifacts")
    router = FakeRouter()
    router.manager = _Manager()  # type: ignore[attr-defined]
    await S.capture_screenshot(
        _result(),
        artifacts=store,
        router_resolver=lambda: router,
        config=_Config(),
        question="describe it",
        tab_url="https://a.test/",
    )
    (call,) = router.calls
    assert call["provider"] == "anthropic"
    assert call["model"] == "claude-vision"


async def test_no_question_means_no_model_call_and_no_answer_key(tmp_path):
    """§10.1: with no question the capture is saved and returned at NO model cost.

    And ``to_dict`` carries no ``answer`` key at all — an empty one would read to
    a model as "the page is blank" rather than "nobody looked".
    """
    store = ArtifactStore(tmp_path / "artifacts")
    router = FakeRouter()
    out = await S.capture_for_tool(
        _result(), browser=_Runtime(store, router), ctx=_Ctx(), tab_url="https://a.test/"
    )

    assert router.calls == []
    assert out.answered is False
    assert out.refusal == ""
    assert "answer" not in out.to_dict()
    assert "answer_unavailable" not in out.to_dict()
    assert out.saved.path.is_file()
    assert out.summary().count("\n") == 0


# --- the honest refusals ----------------------------------------------------------


async def test_no_vision_model_is_the_honest_refusal_not_a_crash(tmp_path):
    """An empty answer is NEVER filled in with a description of our own.

    ``tools/images.py``'s sentence, imported rather than retyped: two copies would
    drift and the user would meet two different explanations of one condition.
    """
    store = ArtifactStore(tmp_path / "artifacts")
    router = FakeRouter(text="   ")
    out = await S.capture_screenshot(
        _result(),
        artifacts=store,
        router_resolver=lambda: router,
        question="what is this?",
        tab_url="https://a.test/",
    )

    assert out.answered is False
    assert out.refusal == S.NO_VISION_ANSWER
    assert "may not support vision" in out.refusal
    # The capture SURVIVES a failed look — that is the whole point of saving first.
    assert out.saved.path.read_bytes() == PNG_BYTES
    assert out.to_dict()["answer_unavailable"] == S.NO_VISION_ANSWER
    assert "answer" not in out.to_dict()
    assert "No description:" in out.summary()


async def test_no_router_at_all_refuses_and_still_saves(tmp_path):
    """A platform with no router is an install problem, and says so."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_for_tool(
        _result(),
        browser=_Runtime(store, router=None),
        ctx=_Ctx(),
        question="what is this?",
        tab_url="https://a.test/",
    )
    assert out.refusal == S.NO_VISION_ROUTER
    assert out.saved.path.is_file()


async def test_a_provider_failure_is_a_refusal_that_names_it(tmp_path):
    """The provider raised: no traceback reaches the model, and the file stays."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_screenshot(
        _result(),
        artifacts=store,
        router_resolver=lambda: ExplodingRouter(),
        question="what is this?",
        tab_url="https://a.test/",
    )
    assert out.answered is False
    assert "provider melted" in out.refusal
    assert "saved" in out.refusal
    assert out.saved.path.is_file()


async def test_an_oversized_capture_refuses_the_look_but_keeps_the_file(tmp_path):
    """Over the provider payload cap: refuse the description, keep the screenshot."""
    store = ArtifactStore(tmp_path / "artifacts")
    router = FakeRouter()
    big = base64.b64encode(b"\0" * (S.MAX_VISION_BYTES + 1)).decode("ascii")
    out = await S.capture_screenshot(
        _result(data_b64=big),
        artifacts=store,
        router_resolver=lambda: router,
        question="what is this?",
        tab_url="https://a.test/",
    )
    assert router.calls == []
    assert "vision limit" in out.refusal
    assert out.saved.size == S.MAX_VISION_BYTES + 1


async def test_no_artifact_store_fails_with_a_model_facing_message(tmp_path):
    """The ONE hard failure: bytes that cannot be persisted at all."""
    with pytest.raises(S.ScreenshotSaveFailed) as excinfo:
        await S.capture_screenshot(_result(), artifacts=None, tab_url="https://a.test/")
    assert "could not be saved" in excinfo.value.message
    assert "browser_read_page" in excinfo.value.message


async def test_a_store_that_raises_becomes_a_named_failure(tmp_path):
    """A full disk is not a traceback: the message says what to check."""

    class BrokenStore:
        def save(self, *args, **kw):
            raise OSError("disk full")

    with pytest.raises(S.ScreenshotSaveFailed) as excinfo:
        await S.capture_screenshot(
            _result(), artifacts=BrokenStore(), tab_url="https://a.test/"
        )
    assert "disk full" in excinfo.value.message
    assert "writable" in excinfo.value.message


# --- the payload contract ---------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"tab_id": 1, "media_type": "image/png"},
        {"tab_id": 1, "media_type": "image/png", "data_b64": ""},
        {"tab_id": 1, "media_type": "image/png", "data_b64": "not base64 !!"},
        {"tab_id": 1, "media_type": "image/png", "data_b64": "   "},
        # Lenient base64 DISCARDS characters outside the alphabet, so a capture
        # corrupted in transit would decode to a shorter, broken image. Strict
        # decoding refuses it instead — this is the case that pins ``validate``.
        {"tab_id": 1, "media_type": "image/png", "data_b64": PNG_B64[:20] + "<<>>" + PNG_B64[20:]},
    ],
)
def test_a_broken_capture_payload_is_extension_error(payload):
    """Four ways the add-on can break its contract, all of them silent unchecked.

    An empty or undecodable capture saves happily, reports a plausible path, and
    shows the user a blank square — so it is refused at the door with the code
    that names the browser half.
    """
    with pytest.raises(BrowserError) as excinfo:
        S.decode_capture(payload)
    assert excinfo.value.code == BrowserErrorCode.EXTENSION_ERROR.value


async def test_the_context_spine_reaches_the_store(tmp_path):
    """``session_id`` AND ``project_id`` are passed EXPLICITLY (the v1.200.0 finding).

    Chat runs as ``session_id="chat"`` — not a ``Session`` row — so the store's
    inherit-the-session's-project path finds nothing for it. A capture taken in a
    project-grounded chat that does not carry ``project_id`` strands in the global
    gallery with ``project_id`` NULL and never appears in that project's Media
    view, which looks exactly like a screenshot that was never taken.
    """
    seen: list[dict] = []

    class RecordingStore(ArtifactStore):
        def save(self, *args, **kw):  # spy takes *args/**kw and calls through
            seen.append(kw)
            return super().save(*args, **kw)

    store = RecordingStore(tmp_path / "artifacts")
    await S.capture_for_tool(
        _result(),
        browser=_Runtime(store),
        ctx=_Ctx(session_id="chat", project_id="proj_7"),
        tab_url="https://a.test/",
    )

    (kw,) = seen
    assert kw["session_id"] == "chat"
    assert kw["project_id"] == "proj_7"
    assert kw["kind"] == "screenshot"


def test_a_jpeg_capture_keeps_an_extension_the_media_route_accepts():
    """Chrome's one documented alternative format still passes ``media_kind``."""
    assert S.filename_for("image/jpeg") == "screenshot.jpg"
    assert media_kind(S.filename_for("image/jpeg")) == "image"


def test_an_unknown_media_type_is_refused_rather_than_saved_unviewable():
    """A suffix ``media_kind`` cannot read would 415 later, pointing nowhere."""
    with pytest.raises(BrowserError) as excinfo:
        S.filename_for("image/tiff")
    assert excinfo.value.code == BrowserErrorCode.EXTENSION_ERROR.value
    assert "image/tiff" in excinfo.value.message


# --- the ORDER: save, then look --------------------------------------------------


async def test_the_file_is_written_before_any_model_is_asked(tmp_path):
    """The composition's contract is an ORDER, and the order is the only pin.

    ``capture_screenshot`` saves and then looks. Swap the two and every existing
    assertion in this file still passes — proved by mutation: moving ``ask_vision``
    above ``save_capture`` left 268 tests across seven files green. So the order is
    recorded directly, from both sides.

    Here, the happy path: one shared list, appended by the store and by the router,
    and the store's entry must come first. Nothing here measures time; the list is
    an order, not a duration.
    """
    events: list[str] = []

    class OrderedStore(ArtifactStore):
        def save(self, *args, **kw):  # spy takes *args/**kw and calls through
            events.append("saved")
            return super().save(*args, **kw)

    class OrderedRouter(FakeRouter):
        async def complete(self, *args, **kw):  # spy takes *args/**kw and calls through
            events.append("looked")
            return await super().complete(*args, **kw)

    out = await S.capture_screenshot(
        _result(),
        artifacts=OrderedStore(tmp_path / "artifacts"),
        router_resolver=lambda: OrderedRouter(),
        question="what is this?",
        tab_url="https://a.test/",
    )

    assert events == ["saved", "looked"]
    assert out.answered is True


async def test_a_save_that_fails_never_spends_a_model_call(tmp_path):
    """The other side of the order, and the reason it is the right way round.

    A store that cannot write is the one hard failure of this module. With the look
    first, the user is billed for a vision call describing a picture that is then
    thrown away — and the refusal they read says nothing about the money spent. So
    the router must be untouched when the save raises.
    """

    class BrokenStore:
        def save(self, *args, **kw):
            raise OSError("disk full")

    router = FakeRouter()
    with pytest.raises(S.ScreenshotSaveFailed):
        await S.capture_screenshot(
            _result(),
            artifacts=BrokenStore(),
            router_resolver=lambda: router,
            question="what is this?",
            tab_url="https://a.test/",
        )

    assert router.calls == [], "a screenshot that could not be saved was still sent to a model"


# --- the ledger row (§10.4) -------------------------------------------------------


async def test_the_ledger_row_names_the_photographed_page(tmp_path):
    """§10.4: ``tab_id``, the page URL and the page TITLE, reconstructible from ``data``.

    ``url`` in this one tool's ``data`` is the ARTIFACT route, not the page — the
    single key in the browser tier whose meaning differs between tools. That is why
    the page carries its own key here, and why the title (which the capture payload
    has been carrying all along and this module used to drop) is emitted beside it.
    Without the title a later audit of browser rows can say a picture was taken and
    cannot say of what.
    """
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_for_tool(
        _result(tab_title="IRS | Payments"),
        browser=_Runtime(store),
        ctx=_Ctx(),
        tab_url="https://www.irs.gov/payments",
        tab_id=7,
    )

    data = out.to_dict()
    assert data["title"] == "IRS | Payments"
    assert data["page_url"] == "https://www.irs.gov/payments"
    assert data["tab_id"] == 7
    # And the two URLs are kept apart: one reaches the file, one names the page.
    assert data["url"] == f"/creative/file/{out.saved.name}"
    assert data["url"] != data["page_url"]


async def test_a_title_given_outright_beats_the_payloads(tmp_path):
    """A caller that already knows the page wins over the add-on's copy."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_screenshot(
        _result(tab_title="stale title"),
        artifacts=store,
        tab_url="https://a.test/",
        tab_title="Sign in",
    )
    assert out.to_dict()["title"] == "Sign in"


async def test_a_capture_with_no_title_says_so_with_an_empty_string(tmp_path):
    """No title is "", never the string ``None`` — the ledger holds it verbatim."""
    store = ArtifactStore(tmp_path / "artifacts")
    out = await S.capture_screenshot(_result(), artifacts=store, tab_url="https://a.test/")
    assert out.to_dict()["title"] == ""


# --- the verification plan §10.1 asks for ----------------------------------------


def test_saved_capture_is_readable_by_absolute_path(tmp_path):
    """VERIFIED, not assumed: ``artifacts_dir`` is not a protected root.

    Plan §10.1's open question. If a later ship registers the artifacts tree (or
    ``<home>`` itself) as protected, ``view_image`` can no longer reopen a capture
    by absolute path and this module's docstring becomes false — this test is what
    makes that loud. Built on a REAL ``build_platform`` because the protected-root
    registrations live there, not in a fixture.
    """
    from iron_jarvis.core.fs_policy import fs_read_ok, is_protected_path
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path / "home"))
    artifact = platform.artifacts.save(
        "browser/example.com/screenshot-1",
        PNG_BYTES,
        kind=S.ARTIFACT_KIND,
        filename="screenshot.png",
    )

    assert is_protected_path(platform.config.artifacts_dir) is False
    ok, reason = fs_read_ok(str(artifact.path))
    assert ok is True, reason
    # The near miss, pinned: the NAME starts "browser/" while <home>/browser is
    # the protected Computer-Use vault. The store's slugify is the only reason a
    # capture does not land inside it.
    assert is_protected_path(platform.config.browser_dir) is True
    assert platform.config.browser_dir not in artifact.path.parents


def test_the_module_documents_both_halves_it_joins():
    """The module's reason to exist is a claim about two other files — pin it.

    A future reader deleting the "why" would leave a module that looks like an
    arbitrary indirection over ``ArtifactStore.save``.
    """
    source = _read_normalised(SCREENSHOT_SOURCE)
    assert "record_screenshot" in source
    assert "WebLookTool" in source
    assert 'kind="screenshot"' in source
    assert "asyncio.to_thread" in source
