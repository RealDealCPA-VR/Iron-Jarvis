"""B3 (second review, v1.510.0): a lost answer to an ACTING call is an UNKNOWN outcome.

When the frame of a click, typing, key press or navigation has gone to the
browser and its answer is lost (the page took longer than the limit, another
browser connected, the socket dropped), the action may already have happened on
the page. The model used to be told "retry the call once" / "retry against the
connected browser", so a form could be submitted twice while the ledger showed
one failed call and one success.

These tests drive the REAL ``ExtensionBackend`` (its pending map, its timeout,
``fail_pending`` from ``adopt`` and ``release``) over a socket whose add-on runs
every command on the scripted page (``tests/_fakes/browser_peer.py``) and, for
the methods named, drops the answer — what ``sendRaw`` does in the add-on when
the socket is no longer open.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.errors import BrowserErrorCode, remedy
from iron_jarvis.browser.extension_backend import ExtensionBackend, ExtensionConnection
from iron_jarvis.browser.service import ACCESS_INTERACTIVE, BrowserRuntime
from iron_jarvis.browser.snapshot import SnapshotCache
from iron_jarvis.browser.tools import BrowserClickTool, BrowserReadPageTool
from iron_jarvis.computeruse.policy import ComputerUsePolicy

from ._fakes.browser_peer import BrowserPeer
from .test_browser_actions_v1237 import FakeApprovals, _ctx, _form_page
from .test_browser_tools_v1235 import FakeConfig

UNKNOWN = (
    "The browser did not answer, so this may already have happened on the page. "
    "Read the page before doing it again."
)


class _LosingSocket:
    """The add-on's end of the socket. Every command RUNS on the scripted page;
    for a method in ``lose`` the answer is dropped and ``on_lost`` (if any) runs
    on the loop afterwards — the in-flight failure the daemon then sees."""

    def __init__(self, peer: BrowserPeer, backend: ExtensionBackend) -> None:
        self.peer = peer
        self.backend = backend
        self.conn: ExtensionConnection | None = None
        self.lose: set[str] = set()
        self.on_lost: Any = None
        self.performed: list[str] = []
        self._tasks: set[asyncio.Task] = set()

    async def send_json(self, frame: dict[str, Any]) -> None:
        if frame.get("type") != P.FRAME_COMMAND:
            return  # ready / replaced frames: nothing to answer
        answers: list[dict[str, Any]] = []
        self.peer.send = answers.append  # type: ignore[method-assign]
        self.peer._answer_command(frame)  # the page ACTS here
        method = str(frame.get("method") or "")
        self.performed.append(method)
        if method in self.lose:
            if self.on_lost is not None:
                task = asyncio.get_running_loop().create_task(self.on_lost())
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            return
        assert self.conn is not None
        await self.backend.handle_frame(self.conn, answers[-1])

    async def close(self, code: int = 1000) -> None:
        return None


class _QuietSocket:
    async def send_json(self, frame: dict[str, Any]) -> None:
        return None

    async def close(self, code: int = 1000) -> None:
        return None


def _scene(*, approvals: Any = None, resolver: Any = None, policy: Any = None):
    peer = BrowserPeer(None, page=_form_page())
    backend = ExtensionBackend()
    sock = _LosingSocket(peer, backend)
    conn = ExtensionConnection(sock, paired=True)
    sock.conn = conn
    runtime = BrowserRuntime(
        backend=backend,
        config=FakeConfig(ACCESS_INTERACTIVE),
        snapshots=SnapshotCache(),
        policy=policy,
        approvals=approvals,
        approval_resolver=resolver,
    )
    return peer, backend, sock, conn, runtime


def _zero_bound_for(monkeypatch, method: str) -> None:
    """The add-on's answer limit for ``method`` is zero: the deadline passes the
    moment the daemon starts waiting. A bound, not a sleep."""
    original = P.command_timeout_s
    monkeypatch.setattr(
        P, "command_timeout_s", lambda m: 0.0 if m == method else original(m)
    )


def _click_after_read(tmp_path: Path, backend, conn, runtime) -> Any:
    async def go():
        await backend.adopt(conn)
        read = await BrowserReadPageTool(runtime).execute({}, _ctx(tmp_path))
        assert read.ok, read.error
        return await BrowserClickTool(runtime).execute(
            {"target": {"element_id": "e3"}}, _ctx(tmp_path)
        )

    return asyncio.run(go())


def _assert_unknown(result: Any, code: BrowserErrorCode) -> None:
    assert not result.ok, "a lost answer is not a success in the ledger"
    text = f"{result.error}\n{result.output}"
    assert UNKNOWN in text
    assert "retry" not in text.lower(), text
    assert code.value in (result.error or "")
    data = result.data or {}
    assert data.get("outcome") == "unknown"
    assert data.get("code") == code.value
    assert data.get("tab_id") == 42
    assert data.get("url") == "https://portal.example/account"
    # The page and element context the ledger row reads.
    assert "portal.example/account" in result.output
    assert "Element: e3." in result.output


def test_a_click_that_happened_and_timed_out_says_the_outcome_is_unknown(tmp_path, monkeypatch):
    _zero_bound_for(monkeypatch, P.METHOD_CLICK)
    peer, backend, sock, conn, runtime = _scene()
    sock.lose = {P.METHOD_CLICK}
    before = peer.page.tab(42).page_version
    result = _click_after_read(tmp_path, backend, conn, runtime)
    # The click DID land on the page — that is the whole point.
    assert P.METHOD_CLICK in sock.performed
    assert peer.page.tab(42).page_version == before + 1
    _assert_unknown(result, BrowserErrorCode.ACTION_TIMEOUT)


def test_a_click_cut_off_by_another_browser_connecting_is_unknown(tmp_path):
    peer, backend, sock, conn, runtime = _scene()
    sock.lose = {P.METHOD_CLICK}
    sock.on_lost = lambda: backend.adopt(ExtensionConnection(_QuietSocket(), paired=True))
    result = _click_after_read(tmp_path, backend, conn, runtime)
    assert P.METHOD_CLICK in sock.performed
    _assert_unknown(result, BrowserErrorCode.CONNECTION_REPLACED)


def test_a_click_cut_off_by_the_socket_dropping_is_unknown(tmp_path):
    peer, backend, sock, conn, runtime = _scene()
    sock.lose = {P.METHOD_CLICK}
    sock.on_lost = lambda: backend.release(conn, reason="closed")
    result = _click_after_read(tmp_path, backend, conn, runtime)
    assert P.METHOD_CLICK in sock.performed
    _assert_unknown(result, BrowserErrorCode.BROWSER_NOT_CONNECTED)


def test_a_click_with_no_browser_connected_keeps_the_pairing_remedy(tmp_path):
    """Nothing was sent, so nothing can have happened: the honest words stay."""
    peer, backend, sock, conn, runtime = _scene()

    async def go():
        await backend.adopt(conn)
        read = await BrowserReadPageTool(runtime).execute({}, _ctx(tmp_path))
        assert read.ok, read.error
        await backend.release(conn, reason="closed")
        return await BrowserClickTool(runtime).execute(
            {"target": {"element_id": "e3"}}, _ctx(tmp_path)
        )

    result = asyncio.run(go())
    assert P.METHOD_CLICK not in sock.performed
    assert not result.ok
    assert result.error == (
        f"{BrowserErrorCode.BROWSER_NOT_CONNECTED.value}: "
        f"{remedy(BrowserErrorCode.BROWSER_NOT_CONNECTED)}"
    )
    assert (result.data or {}).get("outcome") is None
    assert UNKNOWN not in (result.output or "")


def test_a_read_that_timed_out_still_says_retry(tmp_path, monkeypatch):
    """A read changes nothing, so retrying it is safe and the remedy stays."""
    _zero_bound_for(monkeypatch, P.METHOD_READ_PAGE)
    peer, backend, sock, conn, runtime = _scene()
    sock.lose = {P.METHOD_READ_PAGE}

    async def go():
        await backend.adopt(conn)
        return await BrowserReadPageTool(runtime).execute({}, _ctx(tmp_path))

    result = asyncio.run(go())
    assert not result.ok
    assert BrowserErrorCode.ACTION_TIMEOUT.value in (result.error or "")
    assert "retry the call once" in (result.error or "")
    assert (result.data or {}).get("outcome") is None


def test_an_approved_click_whose_answer_was_lost_asks_again_before_a_retry(
    tmp_path, monkeypatch
):
    """The first approval was spent by the call that may have happened; an
    identical retry gets a fresh card, never the old approval."""
    _zero_bound_for(monkeypatch, P.METHOD_CLICK)
    approvals = FakeApprovals()
    grants: list[Any] = []

    def _resolver(req: Any, *args: Any, **kw: Any) -> bool:
        grants.append(req)
        return True

    peer, backend, sock, conn, runtime = _scene(
        approvals=approvals, resolver=_resolver, policy=ComputerUsePolicy()
    )
    sock.lose = {P.METHOD_CLICK}
    ctx = _ctx(tmp_path, run_id="run-B3")

    async def go():
        await backend.adopt(conn)
        out = []
        for _ in range(2):
            read = await BrowserReadPageTool(runtime).execute({}, ctx)
            assert read.ok, read.error
            out.append(
                await BrowserClickTool(runtime).execute(
                    {"target": {"element_id": "e5"}}, ctx  # "Delete account"
                )
            )
            sock.lose = set()
        return out

    first, second = asyncio.run(go())
    assert (first.data or {}).get("outcome") == "unknown"
    assert second.ok, second.error
    assert len(grants) == 2, "the approval spent by the lost call carried the retry"
    assert [row.status for row in approvals.rows] == ["consumed", "consumed"]
