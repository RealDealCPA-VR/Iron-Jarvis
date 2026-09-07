"""Downloads: what Chrome saved, where it really put it, and what Jarvis may claim (v1.237.0).

Ship 3, D23 and plan section 10.3. A download is the one browser capability that
reaches OFF the page and onto the user's disk, and the whole feature is two
sentences long: the add-on reports a completed transfer, the daemon VERIFIES the
path before calling it one, and the existing file tools do the rest. There is no
``browser_download`` tool and no new file tool, by design.

**The composition tradeoff is the feature, not a bug to be hidden.** The file lands
wherever Chrome puts it — normally ``~/Downloads`` — and every write tool in this
repository is workspace-confined by ``safe_path``. So ``rename_file`` CANNOT move a
downloaded statement into a project, and
``test_rename_file_refuses_to_move_a_download_and_that_is_the_documented_behaviour``
asserts that refusal as a contract rather than treating it as a defect. What DOES
work is asserted right beside it, end to end on a real file: the verified absolute
path reaches ``list_folder`` and ``read_document``. Naming the limitation plainly is
what plan 10.3 asks the docs to do; this file is where the code says the same thing.

Three silent failures these pins exist for, each of which would ship green without
them:

* **A path trusted instead of verified.** ``DownloadItem.filename`` is documented as
  an absolute local path, and it is still a string arriving over a socket. If it is
  relative, or invented, the daemon's ``local_path`` becomes something
  ``read_document`` resolves against the SESSION WORKSPACE — so Jarvis reads, or
  fails to read, a completely different file while every surface reports the
  download as found. ``absolute_local_path`` is asked on BOTH routes a download can
  travel (the event frame and an acting method's result), because a result is not a
  more trustworthy channel than an event merely because the daemon asked for it.
* **A forged ``local_path``.** The bus payload is built from a WHITELIST of the
  protocol's own ``DownloadPayload`` keys, so a buggy — or compromised — add-on
  cannot simply send ``local_path`` and have every consumer downstream treat an
  unchecked string as one the daemon verified.
* **An interrupted download reported as a completion.** The event names a file that
  is not on disk; the model hands the path to ``read_document``, gets a
  missing-file error, and reports a failure the user cannot reproduce because their
  own downloads list shows the transfer as cancelled. The add-on publishes on
  ``"complete"`` and on nothing else.

The add-on half has no TypeScript test harness in this repository (there is no
vitest project under ``extensions/chrome``), so it is pinned from Python the way
``tests/test_browser_content_script_v1236.py`` and
``tests/test_browser_navigation_event_v1236.py`` pin theirs: the reader normalises
CRLF once, no needle carries an embedded newline, no pin uses a fixed-size window,
and every pin that reads BEHAVIOUR reads the source with its comments stripped —
this file's own needles are quoted in those comments, where they explain the rule
being pinned, and a pin that matched prose would go red on a comment and green on
the bug.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.extension_backend import (
    DOWNLOAD_PAYLOAD_KEYS,
    MAX_TRACKED_DOWNLOADS,
    ExtensionBackend,
    ExtensionConnection,
    absolute_local_path,
    download_bus_payload,
)
from iron_jarvis.documents.tools import ListFolderTool, ReadDocumentTool
from iron_jarvis.tools.builtins import RenameFileTool

REPO = Path(__file__).resolve().parents[1]
ADDON = REPO / "extensions" / "chrome"
DOWNLOADS_TS = ADDON / "src" / "background" / "downloads.ts"
WORKER_TS = ADDON / "src" / "background" / "index.ts"

#: Seconds any single await here may block before the test fails BY NAME. Generous
#: enough that a loaded CI runner never trips it, finite so a regression that
#: reintroduces a hanging await fails the gate instead of pinning a core. Nothing
#: in this file asserts an elapsed duration.
HANG_GUARD_S = 10.0


# --------------------------------------------------------------------------- #
# Reading the add-on's source
# --------------------------------------------------------------------------- #


def _read(path: Path) -> str:
    """One CRLF normalisation per file, at the reader — never at a call site.

    GitHub's Windows runners check the tree out with ``\\r\\n``, and a needle
    carrying an embedded newline matches locally and never on CI. That is the trap
    that took the v1.232.0 installer down.
    """
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _code(path: Path) -> str:
    """The file with its comments removed, for pins that read behaviour."""
    text = _read(path)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("//"))


def _braced(source: str, declaration: str) -> str:
    """The braced body that follows ``declaration``, found by counting braces.

    A fixed-size window is the trap this repository paid a release for: add one
    line and the window stops reaching the end, so the pin reports "never emitted"
    when the truth is "a line moved".
    """
    start = source.find(declaration)
    assert start != -1, f"{declaration!r} is not in the source at all"
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"{declaration!r} has no closing brace")


# --------------------------------------------------------------------------- #
# The add-on: completion only, a real path, and the one socket
# --------------------------------------------------------------------------- #


def test_the_add_on_has_a_downloads_module_at_all():
    """Ship 2's review named the shape: shipping the mechanism is not shipping it.

    The daemon verifies, publishes, remembers and hands out download paths — all of
    which is exercised by the cases below driving ``ExtensionBackend`` directly, and
    none of which ever RUNS if nothing in the browser listens to
    ``chrome.downloads``. Deleting this file leaves every other case in this suite
    green, which is exactly why the emitter needs a pin of its own.
    """
    assert DOWNLOADS_TS.is_file(), "extensions/chrome/src/background/downloads.ts is missing"
    code = _code(DOWNLOADS_TS)
    assert "chrome.downloads.onChanged.addListener" in code, (
        "nothing listens for a download changing state, so no completion is ever "
        "reported and the whole D23 flow stops at Chrome"
    )
    assert "chrome.downloads.onCreated.addListener" in code, (
        "nothing listens for a download starting, so the origin tab — the only "
        "moment Chromium can answer that question — is never captured"
    )


def test_only_a_completed_download_is_reported():
    """An event for a file that is not on disk is worse than no event at all."""
    body = _braced(_code(DOWNLOADS_TS), "chrome.downloads.onChanged.addListener")
    assert "emit(" in body, "the listener computes but never sends anything"
    # The DELTA's own state, spelled exactly: the item is re-read afterwards and
    # checked again (`item.state !== "complete"`), and a pin that matched either one
    # would pass with the first guard deleted.
    guard = body.find('if (state !== "complete") {')
    assert guard != -1, (
        "the completion listener does not gate on the complete state, so a paused, "
        "cancelled or interrupted transfer is reported as a finished download"
    )
    assert guard < body.find("emit("), (
        "the emit happens before the complete-state guard, so the guard cannot stop it"
    )


def test_an_interrupted_download_publishes_nothing():
    """The transfer failed or the user cancelled it. Say nothing; there is no file."""
    body = _braced(_code(DOWNLOADS_TS), "chrome.downloads.onChanged.addListener")
    marker = body.find('=== "interrupted"')
    assert marker != -1, (
        "the listener does not recognise an interrupted download at all; it would "
        "fall through to the ordinary path or leave the origin remembered forever"
    )
    branch = _braced(body[marker:], "{")
    assert "emit(" not in branch, (
        "the interrupted branch emits an event. A download_completed naming a file "
        "that was cancelled sends the model to read a path that does not exist"
    )
    assert "return" in branch, "the interrupted branch falls through to the complete path"


def test_the_item_is_re_read_rather_than_the_delta_trusted():
    """``onChanged`` hands over a delta whose only reliable field is ``state``.

    ``filename``, ``mime`` and ``fileSize`` are absent from the delta unless they
    changed in that same tick, so a payload built from the delta carries an empty
    filename most of the time — which reads downstream as "Chrome gave us no path"
    rather than as a bug in this listener.
    """
    body = _braced(_code(DOWNLOADS_TS), "chrome.downloads.onChanged.addListener")
    assert "chrome.downloads.search" in body, (
        "the completion is reported from the delta alone; the delta does not carry "
        "the filename, so the daemon would receive a completion with no path"
    )


def test_the_origin_tab_survives_a_service_worker_eviction():
    """A download that takes thirty seconds outlives an idle MV3 worker.

    Module-level state is rebuilt empty on the next wake, so a ``Map`` from download
    id to tab id is empty exactly when a slow download — the ones that matter —
    completes. ``tab_id`` then silently disappears from every large download, and
    the daemon loses the evidence it uses to decide which tool result may claim the
    file.
    """
    code = _code(DOWNLOADS_TS)
    assert "chrome.storage.session" in code, (
        "the origin tab is held only in worker memory, so it is lost whenever MV3 "
        "evicts the worker between onCreated and the completion"
    )
    created = _braced(code, "chrome.downloads.onCreated.addListener")
    assert "rememberOrigin" in created, (
        "the origin tab is not captured at creation. A DownloadItem carries no tab "
        "at all, so creation is the only moment the question can be answered"
    )


def test_the_payload_carries_every_field_the_plan_names():
    """Plan 10.3 lists them, ``protocol.DownloadPayload`` types them, this builds them."""
    body = _braced(_code(DOWNLOADS_TS), "export function downloadPayload")
    for key in ("download_id", "filename", "source_url", "final_url", "bytes", "mime", "timestamp"):
        assert f"{key}:" in body, f"the add-on never reports {key}"
    assert "tab_id" in body, "the add-on never reports the originating tab"
    # And the keys it sends are the ones the PYTHON protocol declares — compared
    # against the real TypedDict rather than against a list typed here, because the
    # generator is what keeps the two halves in step.
    for key in DOWNLOAD_PAYLOAD_KEYS:
        assert key in body, f"protocol.DownloadPayload declares {key}; the add-on never sends it"


def test_the_add_on_never_sends_a_local_path():
    """``local_path`` is the DAEMON's word for a path it verified.

    An add-on that could set it would be an add-on that could hand every file tool
    in the application a path nothing checked.
    """
    assert "local_path" not in _code(DOWNLOADS_TS), (
        "the add-on names local_path. That key means 'the daemon verified this "
        "path'; the add-on's claim is `filename`"
    )


def test_the_worker_routes_downloads_onto_the_one_socket():
    """D22: no second bus — and, here, no second transport either."""
    worker = _code(WORKER_TS)
    assert "watchDownloads(" in worker, (
        "the service worker never starts the downloads watcher, so downloads.ts is "
        "dead code and no completion is ever reported"
    )
    body = _braced(worker, "watchDownloads(")
    assert "socket.emitEvent" in body, "the download emitter does not reach the socket"
    assert "EVENT_ID_PREFIX" in body and "eventSeq" in body, (
        "the download event is minted without the worker's own event counter, so two "
        "different events can carry the same id"
    )


# --------------------------------------------------------------------------- #
# The daemon: verify, then publish
# --------------------------------------------------------------------------- #


class RecordingBus:
    """Every publish, in order. The event stream is the thing under test."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, name, payload=None, session_id=None):
        self.published.append((name, dict(payload or {})))

    def of(self, name: str) -> list[dict]:
        return [payload for published, payload in self.published if published == name]

    def names(self) -> list[str]:
        return [name for name, _ in self.published]


class FakeSocket:
    """A real object with the two methods ``ExtensionConnection`` calls. Not a mock."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed_with: int | None = None

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def paired_connection() -> tuple[ExtensionConnection, FakeSocket]:
    socket = FakeSocket()
    conn = ExtensionConnection(
        socket,
        extension_id="lgihfomaieifpnemakmpadmggjnoojmm",
        extension_version="1.237.0",
        host_permission=True,
        paired=True,
    )
    return conn, socket


def download_frame(**overrides: Any) -> dict:
    """One ``browser.event download_completed`` frame, as the add-on sends it."""
    payload: dict[str, Any] = {
        "download_id": 7,
        "filename": "/home/vr/Downloads/statement.pdf",
        "source_url": "https://bank.example/statement",
        "final_url": "https://cdn.bank.example/abc/statement.pdf",
        "tab_id": 42,
        "bytes": 51_200,
        "mime": "application/pdf",
        "timestamp": "2026-09-07T12:00:00Z",
    }
    payload.update(overrides)
    return P.event_frame("evt_1", P.EVENT_DOWNLOAD_COMPLETED, payload)


async def adopted(bus: RecordingBus) -> tuple[ExtensionBackend, ExtensionConnection]:
    backend = ExtensionBackend(event_bus=bus)
    conn, _socket = paired_connection()
    await backend.adopt(conn)
    return backend, conn


async def test_a_completed_download_publishes_an_absolute_path():
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    await backend.handle_frame(conn, download_frame())

    published = bus.of("browser.download_completed")
    assert len(published) == 1, f"expected exactly one download event, got {bus.names()}"
    payload = published[0]
    assert payload["local_path"] == "/home/vr/Downloads/statement.pdf"
    assert payload["download_id"] == 7
    assert payload["source_url"] == "https://bank.example/statement"
    assert payload["final_url"] == "https://cdn.bank.example/abc/statement.pdf"
    assert payload["tab_id"] == 42
    assert payload["bytes"] == 51_200
    assert payload["mime"] == "application/pdf"


async def test_a_relative_path_is_reported_without_a_local_path_and_named():
    """Absent, not guessed. A present-but-wrong ``local_path`` reads as verified.

    ``read_document`` resolves a relative path against the session workspace, so a
    ``local_path`` of ``"statement.pdf"`` sends Jarvis to read a scratch file that
    has nothing to do with the download, and to say it succeeded.
    """
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    await backend.handle_frame(conn, download_frame(filename="Downloads/statement.pdf"))

    payload = bus.of("browser.download_completed")[0]
    assert "local_path" not in payload, (
        "an unverified path was published as local_path; every consumer reads that "
        "key as a path the daemon checked"
    )
    assert payload["download_id"] == 7, "the completion itself is still reported"
    assert "statement.pdf" in backend.last_error and "absolute" in backend.last_error, (
        "the daemon swallowed the problem silently; the card's Last problem line is "
        "where a user learns their download could not be located"
    )
    assert backend.claim_download(42) is None, (
        "an unverifiable path is claimable, so a tool result would name a path that "
        "points at nothing"
    )


async def test_the_add_on_cannot_forge_the_key_the_file_tools_trust():
    """The bus payload is a WHITELIST of the protocol's own keys, not a copy."""
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    await backend.handle_frame(
        conn,
        download_frame(local_path="/etc/shadow", pairing_token="tok_secret", extra="x"),
    )

    payload = bus.of("browser.download_completed")[0]
    assert payload["local_path"] == "/home/vr/Downloads/statement.pdf", (
        "the add-on's own local_path survived. A compromised add-on could then hand "
        "every file tool in the application a path nothing verified"
    )
    assert "pairing_token" not in payload and "extra" not in payload, (
        "an undeclared key rode the event onto the bus, which is persisted as an "
        "EventRecord and read back later"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "/home/vr/Downloads/a.pdf",
        "C:\\Users\\VR\\Downloads\\a.pdf",
        "//fileserver/share/a.pdf",
    ],
)
def test_a_real_absolute_path_verifies_on_either_operating_system(claim: str):
    """A POSIX path is not absolute to ``PureWindowsPath``, and the reverse.

    A single-flavour check would call a genuine path a fake one on the other OS —
    and this suite runs on both.
    """
    assert absolute_local_path(claim) == claim


@pytest.mark.parametrize("claim", ["statement.pdf", "  ", "", None, 17, "./a.pdf", "a\x00b"])
def test_anything_that_is_not_an_absolute_path_verifies_as_nothing(claim: Any):
    assert absolute_local_path(claim) == ""
    payload, problem = download_bus_payload({"download_id": 1, "filename": claim})
    assert "local_path" not in payload and problem


# --------------------------------------------------------------------------- #
# The one agent-facing capability: the path rides the result
# --------------------------------------------------------------------------- #


async def test_the_path_reaches_the_result_of_the_action_that_started_it():
    """Plan 10.3's single agent-facing addition, and why it is not just an event.

    A model that asked for a file needs the path in the answer to the call it made.
    An event it never sees is a path it cannot hand to ``read_document``.
    """
    bus = RecordingBus()
    backend, conn = await adopted(bus)
    await backend.handle_frame(conn, download_frame())

    claimed = backend.claim_download(42)

    assert claimed is not None and claimed["local_path"] == "/home/vr/Downloads/statement.pdf"


async def test_one_download_is_named_in_exactly_one_result():
    """Claimed, not merely read.

    Repeating it on every later browser call tells the model that each click
    produced a file, and "put the statement in the project" then runs three times on
    one statement.
    """
    bus = RecordingBus()
    backend, conn = await adopted(bus)
    await backend.handle_frame(conn, download_frame())

    assert backend.claim_download(42) is not None
    assert backend.claim_download(42) is None, "the same file was offered to a second call"


async def test_another_tabs_download_is_not_offered_to_this_tabs_click():
    bus = RecordingBus()
    backend, conn = await adopted(bus)
    await backend.handle_frame(conn, download_frame(tab_id=99))

    assert backend.claim_download(42) is None, (
        "a download from a different tab was attributed to this tab's action"
    )
    assert backend.claim_download(99) is not None


async def test_a_download_chrome_could_not_attribute_is_still_offered():
    """Chromium's ``DownloadItem`` carries no tab. Losing the path is the worse answer."""
    bus = RecordingBus()
    backend, conn = await adopted(bus)
    frame = download_frame()
    frame["payload"].pop("tab_id")

    await backend.handle_frame(conn, frame)

    assert backend.claim_download(42) is not None


async def test_a_download_on_an_acting_result_is_verified_the_same_way():
    """A result is not a more trustworthy channel than an event.

    The add-on may attach the completed download to the click's own result. That
    string gets exactly the check the event path gets, and is recorded as ALREADY
    claimed so the ``download_completed`` frame that follows cannot hand the same
    file to a second tool call.
    """
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    async def answer() -> dict:
        return await backend.command(P.METHOD_CLICK, {"target": {"element_id": "e1"}})

    async with asyncio.timeout(HANG_GUARD_S):
        task = asyncio.create_task(answer())
        for _ in range(200):
            await asyncio.sleep(0)
            if conn.pending:
                break
        request_id = next(iter(conn.pending))
        await backend.handle_frame(
            conn,
            P.response_frame(
                request_id,
                result={
                    "tab_id": 42,
                    "clicked": {"element_id": "e1", "role": "link", "name": "Statement"},
                    "url": "https://bank.example/",
                    "page_version": 3,
                    "navigated": False,
                    "download": {
                        "download_id": 7,
                        "filename": "Downloads/statement.pdf",
                        "local_path": "/etc/shadow",
                        "source_url": "https://bank.example/statement",
                    },
                },
            ),
        )
        result = await task

    assert "local_path" not in result["download"], (
        "an unverified filename on a RESULT became a local_path. The event path is "
        "checked; a result must not be a way around it"
    )
    assert result["download"]["filename"] == "Downloads/statement.pdf"


async def test_the_same_completion_reported_twice_is_one_file():
    """It legitimately arrives twice — on the acting result, then on the event frame."""
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    backend.record_download(
        {
            "download_id": 7,
            "filename": "/home/vr/Downloads/statement.pdf",
            "source_url": "https://bank.example/statement",
        },
        claimed=True,
    )
    await backend.handle_frame(conn, download_frame())

    assert len(backend.recent_downloads) == 1, (
        f"one completion became {len(backend.recent_downloads)} records; the model "
        "would be told about one file twice and copy it into the project twice"
    )
    assert backend.claim_download(42) is None, (
        "the event frame un-claimed a download an acting result had already named"
    )
    assert len(bus.of("browser.download_completed")) == 1, "the event still publishes once"


async def test_the_remembered_list_is_bounded():
    """Every entry is an absolute path into the user's private folders."""
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    for index in range(MAX_TRACKED_DOWNLOADS + 5):
        await backend.handle_frame(
            conn, download_frame(download_id=index, filename=f"/home/vr/Downloads/f{index}.pdf")
        )

    assert len(backend.recent_downloads) == MAX_TRACKED_DOWNLOADS


async def test_a_download_that_lands_while_the_action_is_waiting_is_not_missed():
    """The lost-wakeup window between "nothing yet" and "now waiting".

    ``await_download`` captures the signal BEFORE it checks for a download, so a
    completion recorded in between wakes it. A ``set()``-then-``clear()`` signal
    would leave the waiter asleep until its bound expired, and the click that
    downloaded the file would answer without naming it.
    """
    bus = RecordingBus()
    backend, conn = await adopted(bus)

    async with asyncio.timeout(HANG_GUARD_S):
        waiting = asyncio.create_task(backend.await_download(42, timeout_s=HANG_GUARD_S))
        for _ in range(200):
            await asyncio.sleep(0)
        await backend.handle_frame(conn, download_frame())
        claimed = await waiting

    assert claimed is not None and claimed["local_path"] == "/home/vr/Downloads/statement.pdf"


async def test_an_action_that_downloads_nothing_answers_without_one():
    """The ordinary click. ``None``, not an error, and not a wait without end."""
    bus = RecordingBus()
    backend, _conn = await adopted(bus)

    async with asyncio.timeout(HANG_GUARD_S):
        assert await backend.await_download(42, timeout_s=0) is None


async def test_a_disconnected_browser_leaves_no_paths_behind():
    """Those paths were remembered only so this browser's NEXT call could name a file.

    There is no next call — and a path claimed against a REPLACEMENT browser would
    attribute one browser's download to another browser's click.
    """
    bus = RecordingBus()
    backend, conn = await adopted(bus)
    await backend.handle_frame(conn, download_frame())

    await backend.release(conn, reason="closed")

    assert backend.recent_downloads == []


# --------------------------------------------------------------------------- #
# The composition, end to end on a real file
# --------------------------------------------------------------------------- #


class _Ctx:
    """The three attributes the file tools read, and a home for the undo journal."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.session_id = "s"
        self.agent_run_id = "r"


async def _downloaded(tmp_path: Path) -> tuple[str, Path]:
    """A real completed download: a real file, reported through the real event path."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    saved = downloads / "statement.txt"
    saved.write_text("Closing balance 1,234.56\n", encoding="utf-8")

    backend = ExtensionBackend(event_bus=RecordingBus())
    conn, _socket = paired_connection()
    await backend.adopt(conn)
    await backend.handle_frame(conn, download_frame(filename=str(saved)))
    claimed = backend.claim_download(42)
    assert claimed is not None, "the download was not claimable at all"
    return claimed["local_path"], saved


async def test_list_folder_can_see_a_completed_download(tmp_path):
    local_path, saved = await _downloaded(tmp_path)

    result = await ListFolderTool().execute(
        {"path": str(Path(local_path).parent)}, _Ctx(tmp_path / "ws")
    )

    assert result.ok, result.error
    assert saved.name in result.output


async def test_read_document_can_read_a_completed_download(tmp_path):
    local_path, _saved = await _downloaded(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = await ReadDocumentTool().execute({"path": local_path}, _Ctx(workspace))

    assert result.ok, result.error
    assert "Closing balance 1,234.56" in result.output, (
        "the verified absolute path did not reach the reader; this composition IS "
        "the download feature — there is no browser_download tool"
    )


async def test_rename_file_refuses_to_move_a_download_and_that_is_the_documented_behaviour(
    tmp_path,
):
    """The tradeoff of plan 10.3, asserted as a contract rather than filed as a bug.

    ``rename_file`` resolves BOTH ends under the session workspace through
    ``safe_path``, so it cannot move a file out of ``~/Downloads`` and into a
    project. That is the confinement every write tool in this repository obeys, and
    weakening it for downloads would give a page the user visited a way to name a
    destination anywhere on disk.

    What serves "download this statement and put it in the current project" is the
    route that already exists — ``POST /documents/save-copy`` — plus the read tools
    pinned above. The refusal must NAME the confinement, because a model that reads
    "escapes the session workspace" tries the other route, and one that reads a bare
    "failed" retries the same call.
    """
    local_path, _saved = await _downloaded(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = await RenameFileTool().execute(
        {"path": local_path, "new_path": "statement.txt"}, _Ctx(workspace)
    )

    assert not result.ok, (
        "rename_file moved a file from outside the workspace. Every write tool here "
        "is workspace-confined; a download is not an exception to that"
    )
    assert "escapes the session workspace" in result.error, (
        f"the refusal does not name the confinement, so a model cannot tell that the "
        f"answer is a different route rather than a retry. Got: {result.error!r}"
    )
    # THE CONTROL, so the pin above cannot pass because rename_file is simply
    # broken: the identical call on a file INSIDE the workspace succeeds. What
    # differs between the two is the location and nothing else.
    inside = workspace / "statement.txt"
    inside.write_text("Closing balance", encoding="utf-8")
    allowed = await RenameFileTool().execute(
        {"path": "statement.txt", "new_path": "renamed.txt"}, _Ctx(workspace)
    )
    assert allowed.ok, allowed.error
