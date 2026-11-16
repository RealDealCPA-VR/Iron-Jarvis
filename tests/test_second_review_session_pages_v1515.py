"""v1.515.0 (second review, group 15 part C): what a coding session's own page
browser may show the model, what it keeps, and which account a turn runs on.

* S1: a FRAME is a page too. The agent's browser judges a document loaded
  into an iframe with the same address rules as a top-level page (another
  local address is never drawn into the picture the model gets; the frame
  stays empty and the result says why). The ONE-SHOT ``page_screenshot`` (a
  ``url`` the agent's browser is not on) is taken through the agent
  browser's own backend, started for that one picture and ended after it:
  the route guard, the frame guard and the Local only proxy hold there too,
  and a page that MOVED to another address gives no picture at all.
* S7: a lesson (or an edit) that quotes a secret is never kept, never
  published on the bell, never put in a later prompt.
* S9: page screenshots are kept per session and go to the app's trash when
  the session is deleted; pictures in the old shared folder stay.
* S10: with "Several accounts" on, a coding turn on a subscription CLI whose
  accounts list cannot be read is refused in words, never started unpinned.

Fast pins use stand-ins; the real-Edge pins (``skipif`` Edge or Playwright
is missing) run against local ``http.server`` sites, never the network.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.harness import page_browser as PB
from iron_jarvis.harness import page_shot as PS

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63fccf805f0f0004850180848a63210000000049454e44ae426082"
)

DEV = "http://localhost:5173/"
OTHER = "http://localhost:8020/admin"


# --------------------------------------------------------------------------- #
# S10: no turn starts unpinned when the accounts list could not be read
# --------------------------------------------------------------------------- #


class _Svc:
    """Iron-Proxy as the route sees it: ``cache`` None = never read."""

    def __init__(self, *, enabled=True, cache=None, check_raises=False, read_gives=None):
        self.enabled = enabled
        self._cache = cache
        self._check_raises = check_raises
        self._read_gives = read_gives
        self.checked = 0

    def cached_accounts(self):
        return self._cache

    def check(self):
        self.checked += 1
        if self._check_raises:
            raise RuntimeError("the account manager did not answer")
        return True

    def refresh_accounts(self):
        if self._read_gives is None:
            raise RuntimeError("the list could not be read")
        self._cache = self._read_gives


def _platform(svc):
    return SimpleNamespace(iron_proxy=svc, config=SimpleNamespace(default_provider=""),
                           providers=None, engine=None)


def _profiles():
    return ([{"id": "c-work", "provider": "anthropic", "title": "Work", "order": 1, "lane": "cli"}],
            {"c-work": {"status": "ready"}})


def test_a_turn_whose_accounts_could_not_be_read_is_refused_not_started_unpinned():
    from iron_jarvis.daemon.routes import build as R

    for svc in (_Svc(check_raises=True), _Svc()):
        with pytest.raises(HTTPException) as caught:
            R.turn_account(_platform(svc), "t1", {"provider": "claude-cli"})
        assert caught.value.status_code == 409 and caught.value.detail == BA.NOT_READ_NOTE
        assert svc.checked == 1  # the read WAS tried first


def test_off_unmanaged_or_no_account_runs_exactly_as_before(monkeypatch):
    from iron_jarvis.daemon.routes import build as R

    # Several accounts off: this PC's own login, as before.
    assert R.turn_account(_platform(_Svc(enabled=False)), "t1", {"provider": "claude-cli"}) == (None, "")
    # A provider the manager does not manage (a local model): as before.
    assert R.turn_account(_platform(_Svc(check_raises=True)), "t1", {"provider": "ollama"}) == (None, "")
    # The list is known and holds no account for the vendor: as before.
    known_empty = _Svc(cache=([], {}))
    assert R.turn_account(_platform(known_empty), "t1", {"provider": "claude-cli"}) == (None, "")
    # The read succeeds on the try: the Default is stored and pinned.
    stored: list[dict] = []
    monkeypatch.setattr(R.S, "update", lambda engine, tid, setup: stored.append(setup))
    pin, provider = R.turn_account(_platform(_Svc(read_gives=_profiles())), "t1", {"provider": "claude-cli"})
    assert pin.profile_id == "c-work" and provider == "claude-cli"
    assert stored == [{"account": "c-work", "account_name": "Work", "provider": "claude-cli"}]


# --------------------------------------------------------------------------- #
# S7: a lesson that quotes a secret is not kept
# --------------------------------------------------------------------------- #

#: A plain word the test's masker treats as a secret (no credential-shaped
#: literal in this file: the repository is public and its scanner reads it).
MARKER = "plainmarkerword"


def _mask_marker(monkeypatch):
    from iron_jarvis.providers import failure_words as FW

    real = FW.mask_failure_text

    def masked(text, *, strict=False):
        out = real(text, strict=strict)
        return out.replace(MARKER, "***") if isinstance(out, str) else out

    monkeypatch.setattr(FW, "mask_failure_text", masked)


@pytest.fixture()
def lib_app(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.harness import library as L

    L.shutdown()
    yield create_app(str(tmp_path / "home"))
    L.shutdown()


@pytest.fixture()
def lib_work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
    return folder


def _spy_bus(app, monkeypatch) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    bus = app.state.platform.event_bus
    real_publish = bus.publish

    async def spy(kind, payload, **kw):
        seen.append((str(kind), payload))
        return await real_publish(kind, payload, **kw)

    monkeypatch.setattr(bus, "publish", spy)
    return seen


async def test_a_lesson_that_quotes_a_secret_is_dropped_and_never_published(lib_app, lib_work, monkeypatch):
    from iron_jarvis.harness import library as L
    from tests.test_harness_library_v1370 import _TURN, _rows, _worked

    _mask_marker(monkeypatch)
    seen = _spy_bus(lib_app, monkeypatch)
    lesson = {"lesson": f"add() lives in calc.py; the key it printed is {MARKER}.\n"
                        "The project's check is `echo 3 passed`.", "edit": None}
    sid, _ = await _worked(lib_app, lib_work, lesson)
    out = await L.reflect(lib_app.state.platform, sid, _TURN[sid])
    assert out.minted == []
    assert all(MARKER not in (r.text or "") for r in _rows(lib_app))
    assert MARKER not in json.dumps([p for _k, p in seen])


async def test_an_edit_that_quotes_a_secret_is_dropped_and_the_clean_lesson_stays(lib_app, lib_work, monkeypatch):
    from iron_jarvis.harness import library as L
    from tests.test_harness_library_v1370 import _TURN, _worked

    _mask_marker(monkeypatch)
    edit = {"lesson": "add() lives in calc.py.", "edit": {
        "file": "memory.md", "text": f"Notes:\nadd() lives in calc.py.\nThe key is {MARKER}.\n",
        "why": "Keep where add() lives."}}
    sid, _ = await _worked(lib_app, lib_work, edit)
    out = await L.reflect(lib_app.state.platform, sid, _TURN[sid])
    assert [r.kind for r in out.minted] == ["lesson"]
    # The same edit with no secret in it IS proposed (the secret is what dropped it).
    clean = {"lesson": "add() lives in calc.py.", "edit": {
        "file": "memory.md", "text": "Notes:\nadd() lives in calc.py.\n", "why": "Keep where add() lives."}}
    work2 = lib_work.parent / "work2"
    work2.mkdir()
    (work2 / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
    sid2, _ = await _worked(lib_app, work2, clean)
    out2 = await L.reflect(lib_app.state.platform, sid2, _TURN[sid2])
    assert sorted(r.kind for r in out2.minted) == ["layer_edit", "lesson"]


# --------------------------------------------------------------------------- #
# S1 (b): the agent's browser judges a FRAME's page like a top-level one
# --------------------------------------------------------------------------- #


class _Frame:
    def __init__(self, url="", parent=None):
        self.url = url
        self.parent_frame = parent
        self.went: list[str] = []

    async def goto(self, url, timeout=None):
        self.went.append(url)
        self.url = url


class _Req:
    def __init__(self, url, frame, nav=True):
        self.url, self.frame, self._nav = url, frame, nav
        self.redirected_from = None

    def is_navigation_request(self):
        return self._nav


class _Route:
    def __init__(self):
        self.did: list[str] = []

    async def abort(self, why=""):
        self.did.append(f"abort:{why}")

    async def continue_(self):
        self.did.append("continue")


def _backend(tmp_path, seen):
    return PB.EdgeBackend(
        edge="edge", work=tmp_path, guard=lambda u: PB.judge(u, set(), "http://localhost:5173"),
        note=lambda k, t: seen.append((k, t)),
    )


async def test_a_frame_loading_another_local_address_is_stopped_and_said(tmp_path):
    seen: list[tuple[str, str]] = []
    backend = _backend(tmp_path, seen)
    top = _Frame(DEV)
    child = _Frame("", parent=top)
    # A frame's page on another app's address: stopped before it is sent.
    r1 = _Route()
    await backend._route(r1, _Req("http://192.168.1.1/", child))
    assert r1.did == ["abort:blockedbyclient"]
    assert seen == [("blocked", "frame-ask|http://192.168.1.1/")]
    # A frame's page on the dev server, a picture from another local address
    # (a sub-request, not a page) and the top-level page: as before.
    for req in (_Req("http://localhost:5173/embed", child), _Req("http://localhost:8020/logo.png", top, nav=False),
                _Req("http://localhost:5173/cart", top)):
        r = _Route()
        await backend._route(r, req)
        assert r.did == ["continue"], req.url
    # The words the model is given: a frame was left empty, and why.
    sess = PB._Session(key="build:x", backend=None, work=tmp_path, lock=asyncio.Lock())
    PB.AgentBrowsers()._note_for(sess)("blocked", "frame-ask|http://192.168.1.1/")
    assert len(sess.blocked) == 1 and "frame" in sess.blocked[0] and "192.168.1.1" in sess.blocked[0]
    assert "left empty" in sess.blocked[0]


async def test_a_frame_that_arrives_somewhere_refused_is_emptied(tmp_path):
    """A server redirect inside a frame is not asked of the route: the frame
    is emptied when it arrives, and the picture is never taken with it."""
    seen: list[tuple[str, str]] = []
    backend = _backend(tmp_path, seen)
    top = _Frame(DEV)
    child = _Frame("http://localhost:8020/admin", parent=top)
    backend._on_navigated(SimpleNamespace(), child)
    await backend.quiet()
    assert seen == [("blocked", "frame-ask|http://localhost:8020/admin")]
    assert child.went == ["about:blank"]


async def test_the_picture_is_never_taken_with_a_refused_frame_in_it(tmp_path):
    seen: list[tuple[str, str]] = []
    backend = _backend(tmp_path, seen)
    top = _Frame(DEV)
    child = _Frame("http://localhost:8020/admin", parent=top)
    shots: list[str] = []

    class _Page:
        frames = [top, child]
        main_frame = top
        url = DEV

        def is_closed(self):
            return False

        async def screenshot(self, **kw):
            shots.append(child.url)
            return PNG_1X1

    backend._page = _Page()
    assert await backend.screenshot(jpeg=False) == PNG_1X1
    assert shots == ["about:blank"]  # emptied BEFORE the picture
    assert ("blocked", "frame-ask|http://localhost:8020/admin") in seen

    # A frame that cannot be emptied: no picture.
    async def stuck(url, timeout=None):
        raise RuntimeError("frame was detached")

    child.url = "http://localhost:8020/admin"
    child.goto = stuck
    with pytest.raises(PB.BrowserProblem):
        await backend.screenshot(jpeg=False)


# --------------------------------------------------------------------------- #
# S1 (a): the one-shot picture goes through the agent browser's backend
# --------------------------------------------------------------------------- #


class _ShotBackend:
    """Stands in for EdgeBackend in a one-shot: ``moves_to`` is where the
    page sends itself after it loads (a meta refresh)."""

    moves_to = ""

    def __init__(self, *, edge, work, guard, note, background=False, extra_args=(), **kw):
        self.guard, self.note, self.work = guard, note, Path(work)
        self.kw = kw
        self._url = "about:blank"
        self.started = self.closed = self.killed = False
        self.size: tuple[int, int] | None = None
        self.hang: asyncio.Event | None = None

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    def kill(self):
        self.killed = True
        return [1]

    def pids(self):
        return []

    async def resize(self, width, height):
        self.size = (width, height)

    async def goto(self, url, timeout_s):
        if self.hang is not None:
            await self.hang.wait()
        self._url = url
        if self.moves_to:
            verdict = self.guard(self.moves_to)
            if verdict.kind == "ok":
                self._url = self.moves_to
            else:
                self.note("blocked", f"{verdict.kind}|{self.moves_to}")
                self._url = "chrome-error://chromewebdata/"

    async def settle(self):
        return None

    async def quiet(self):
        return None

    def url(self):
        return self._url

    async def title(self):
        return ""

    async def screenshot(self, *, jpeg):
        return PNG_1X1


@pytest.fixture()
def shots(monkeypatch):
    made: list[_ShotBackend] = []

    def factory(**kw):
        b = _ShotBackend(**kw)
        made.append(b)
        return b

    inst = PB.AgentBrowsers(backend_factory=factory)
    inst.made = made  # type: ignore[attr-defined]
    monkeypatch.setattr(PB, "BROWSERS", inst)
    monkeypatch.setattr(PS, "browser_executable", lambda: "edge.exe")
    yield inst
    inst.shutdown()


def _ctx(home: Path, session="build:s1"):
    return SimpleNamespace(session_id=session, turn_id="", workspace=home, config=SimpleNamespace(home=home))


async def test_a_page_that_moves_to_another_address_gives_no_picture(tmp_path, shots, monkeypatch):
    monkeypatch.setattr(_ShotBackend, "moves_to", OTHER)
    res = await PS.PageScreenshotTool().execute({"url": DEV, "wait_ms": 0}, _ctx(tmp_path / "home"))
    assert res.ok is False and not res.images
    assert "localhost:8020/admin" in res.error and "no picture" in res.error
    b = shots.made[0]
    assert b.started and b.killed and not b.work.exists()  # ended, its folder gone
    assert not (tmp_path / "home" / "page-screenshots").exists() or not any(
        (tmp_path / "home" / "page-screenshots").rglob("page-*"))


async def test_a_one_shot_is_taken_through_the_backend_and_ended(tmp_path, shots):
    res = await PS.PageScreenshotTool().execute(
        {"url": "http://0.0.0.0:5173/", "width": 99999, "wait_ms": 0}, _ctx(tmp_path / "home"))
    assert res.ok, res.error
    b = shots.made[0]
    assert b.size == (PS.MAX_WIDTH, PS.DEFAULT_HEIGHT)
    assert b.killed and b.closed and not b.work.exists()
    assert shots.open_keys() == []  # no session browser was left behind
    saved = Path(res.data["file"])
    assert saved.read_bytes() == PNG_1X1
    assert res.data["url"] == "http://localhost:5173/"


async def test_a_one_shot_stopped_midway_ends_its_browser_and_folder(tmp_path, shots, monkeypatch):
    hang = asyncio.Event()
    real_init = _ShotBackend.__init__

    def init(self, **kw):
        real_init(self, **kw)
        self.hang = hang

    monkeypatch.setattr(_ShotBackend, "__init__", init)
    task = asyncio.ensure_future(PS.PageScreenshotTool().execute({"url": DEV}, _ctx(tmp_path / "home")))
    for _ in range(400):  # the stand-in has started (wait for what is asserted)
        if shots.made and shots.made[0].started:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    b = shots.made[0]
    for _ in range(400):
        if b.killed and not b.work.exists():
            break
        await asyncio.sleep(0.01)
    assert b.killed and not b.work.exists()


async def test_a_local_only_one_shot_starts_behind_the_refusing_proxy(tmp_path, shots):
    from iron_jarvis.providers import egress

    with egress.local_only_scope(True):
        res = await PS.PageScreenshotTool().execute({"url": DEV, "wait_ms": 0}, _ctx(tmp_path / "home"))
    assert res.ok, res.error
    assert shots.made[0].kw.get("local_only") is True


# --------------------------------------------------------------------------- #
# S9: pictures are kept per session and go to the trash with it
# --------------------------------------------------------------------------- #


async def test_a_screenshot_is_kept_in_its_sessions_own_folder(tmp_path, shots):
    home = tmp_path / "home"
    res = await PS.PageScreenshotTool().execute({"url": DEV, "wait_ms": 0}, _ctx(home, "build:abc"))
    assert res.ok, res.error
    assert Path(res.data["file"]).parent == home / "page-screenshots" / "abc"


def test_deleting_a_session_moves_its_pictures_to_the_trash(tmp_path, shots):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    work.mkdir()
    app = create_app(str(tmp_path / "root"))
    client = TestClient(app)
    home = Path(app.state.platform.config.home)  # what a tool call's ctx.config.home is
    sid = new_session(client, work)["id"]
    other = new_session(client, work)["id"]
    res = asyncio.run(PS.PageScreenshotTool().execute({"url": DEV, "wait_ms": 0}, _ctx(home, f"build:{sid}")))
    kept = asyncio.run(PS.PageScreenshotTool().execute({"url": DEV, "wait_ms": 0}, _ctx(home, f"build:{other}")))
    assert res.ok and kept.ok
    shot = Path(res.data["file"])
    old = home / "page-screenshots" / "page-20260101-000000-localhost-5173-abcdef.png"
    old.write_bytes(PNG_1X1)  # a picture from before: the shared folder
    r = client.delete(f"/build/sessions/{sid}")
    assert r.status_code == 200, r.text
    assert not shot.exists() and not shot.parent.exists()
    moved = list((home / "trash").rglob(shot.name))
    assert len(moved) == 1 and moved[0].read_bytes() == PNG_1X1
    assert moved[0].parent.name == sid and moved[0].parent.parent.name == "page-screenshots"
    assert Path(kept.data["file"]).is_file()  # another session's pictures stay
    assert old.is_file()  # the old shared folder is left as it is


# --------------------------------------------------------------------------- #
# the REAL Edge (skipped when Edge or Playwright is missing)
# --------------------------------------------------------------------------- #

EDGE = PB.edge_executable()
REAL = pytest.mark.skipif(
    not EDGE or bool(PB.playwright_problem()) or sys.platform != "win32",
    reason="needs the installed Microsoft Edge and Playwright's driver (Windows)",
)


class _Site:
    """A local http.server (never the network) that records what it served."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.hits: list[str] = []
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                site.hits.append(self.path)
                body = pages.get(self.path.split("?", 1)[0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = body.replace("PORT", str(site.port)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


@REAL
@pytest.mark.timeout(240)
async def test_real_edge_never_draws_another_local_app_into_a_frame(tmp_path):
    other = _Site({"/secret": "<title>Other</title><h1>other app's page</h1>"})
    site = _Site({"/": f"<title>Shop</title><h1>Shop</h1>"
                       f"<iframe src='http://localhost:{other.port}/secret' width=400 height=200></iframe>"})
    browsers = PB.AgentBrowsers()
    key = "build:frames"
    try:
        opened = await browsers.open(key, site.base, preview=site.base)
        picture = await browsers.picture(key, preview=site.base)
        assert picture.picture
        assert other.hits == []  # the frame's page was never requested
        said = " ".join([*opened.blocked, *picture.blocked])
        assert "frame" in said and f"localhost:{other.port}" in said
    finally:
        browsers.shutdown()
        site.close()
        other.close()


@REAL
@pytest.mark.timeout(240)
async def test_real_edge_one_shot_of_a_page_that_moves_gives_no_picture(tmp_path, monkeypatch):
    other = _Site({"/admin": "<title>Admin</title><h1>another app's admin page</h1>"})
    site = _Site({"/refresh": f"<meta http-equiv='refresh' content='0;url=http://localhost:{other.port}/admin'>"
                              "<title>Moving</title>moving"})
    browsers = PB.AgentBrowsers()
    monkeypatch.setattr(PB, "BROWSERS", browsers)
    try:
        res = await PS.PageScreenshotTool().execute(
            {"url": site.base + "refresh", "wait_ms": 1500}, _ctx(tmp_path / "home"))
        assert res.ok is False and not res.images, res.output
        assert other.hits == []  # the other app's page was never loaded
        assert browsers.open_keys() == []
    finally:
        browsers.shutdown()
        site.close()
        other.close()


@REAL
@pytest.mark.timeout(240)
async def test_real_edge_local_only_one_shot_reaches_no_public_host(tmp_path, monkeypatch):
    from iron_jarvis.providers import egress

    rule = "--host-resolver-rules=MAP public.test 127.0.0.1"
    site = _Site({"/": "<title>Page</title><h1>page</h1><img src='http://public.test:PORT/pixel'>",
                  "/pixel": "x"})
    browsers = PB.AgentBrowsers()
    browsers.extra_args = (rule,)
    monkeypatch.setattr(PB, "BROWSERS", browsers)
    # The bare command line this replaced: given the same name rule, so the
    # pin can tell the two apart.
    real_argv = getattr(PS, "browser_argv", None)
    if real_argv is not None:
        monkeypatch.setattr(PS, "browser_argv", lambda *a: [*real_argv(*a)[:-1], rule, real_argv(*a)[-1]])
    try:
        with egress.local_only_scope(True):
            res = await PS.PageScreenshotTool().execute({"url": site.base, "wait_ms": 1500}, _ctx(tmp_path / "home"))
        assert res.ok, res.error
        assert "/" in site.hits and "/pixel" not in site.hits, site.hits
        # The same page outside Local only reaches the name (the rule works).
        res = await PS.PageScreenshotTool().execute({"url": site.base, "wait_ms": 1500}, _ctx(tmp_path / "home"))
        assert res.ok, res.error
        assert "/pixel" in site.hits
    finally:
        browsers.shutdown()
        site.close()
