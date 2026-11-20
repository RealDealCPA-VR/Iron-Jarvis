"""R2.15 (phase 27-a of docs/next-leaps/07-coding-github-evidence-scoreboard-
sealed-room.md, idea 27 "Show me it works"): before and after pictures under a
coding reply that changed a screen.

Decision D-27.1 as the owner settled it: pictures are taken by themselves only
when the person's message asks ("show me it works") or the setting "Take before
and after pictures by themselves" is on; any other reply that changed a screen
file is only marked, and Show me under it takes the after picture then. The
setting starts not set (off) and is offered ONCE.

Every picture here is taken through the agent browser's real one-shot
(``page_browser.AgentBrowsers.one_shot``) with a stand-in backend whose picture
is the BYTES OF THE WATCHED FILE AT THE MOMENT THE PICTURE IS TAKEN — so "the
before was taken before the write landed" is read from the picture (ORDER),
never from seconds. File writes go through the real tool registry; whole
replies through the real build turn route and the real stream lane, with a
scripted model.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from iron_jarvis.harness import evidence as E
from iron_jarvis.harness import page_browser as PB
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

DEV = "http://localhost:5173/cart?token=abc#top"


class _PageBackend:
    """Stands in for EdgeBackend in a one-shot. Class settings (reset per
    test by the fixture): ``watch`` — the file whose bytes ARE the picture;
    ``fail`` — words the picture fails with; ``hang`` — an Event the page
    load waits on; ``same`` — every picture the same bytes."""

    watch: "Path | None" = None
    fail = ""
    hang: "threading.Event | None" = None
    same = False

    def __init__(self, *, edge, work, guard, note, background=False, extra_args=(), **kw):
        self.work = Path(work)
        self.kw = kw
        self._url = "about:blank"
        self.started = self.closed = self.killed = False

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
        return None

    async def goto(self, url, timeout_s):
        # A THREAD event: the page loads on the browser's own thread and loop.
        gate = type(self).hang
        while gate is not None and not gate.is_set():
            await asyncio.sleep(0.005)
        self._url = url

    async def settle(self):
        return None

    async def quiet(self):
        return None

    def url(self):
        return self._url

    async def title(self):
        return ""

    async def screenshot(self, *, jpeg):
        cls = type(self)
        if cls.fail:
            raise RuntimeError(cls.fail)
        if cls.same:
            return b"PIC:same"
        body = cls.watch.read_bytes() if (cls.watch is not None and cls.watch.exists()) else b"<none>"
        return b"PIC:" + body


@pytest.fixture()
def browsers(monkeypatch):
    made: list[_PageBackend] = []

    def factory(**kw):
        b = _PageBackend(**kw)
        made.append(b)
        return b

    for attr, value in (("watch", None), ("fail", ""), ("hang", None), ("same", False)):
        monkeypatch.setattr(_PageBackend, attr, value)
    inst = PB.AgentBrowsers(backend_factory=factory)
    inst.made = made  # type: ignore[attr-defined]
    monkeypatch.setattr(PB, "BROWSERS", inst)
    monkeypatch.setattr(E, "_edge", lambda: "edge.exe")
    monkeypatch.setattr(E, "SETTLE_MS", 0)
    yield inst
    inst.shutdown()


@pytest.fixture()
def reg(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    perms = PermissionEngine({**p.config.permissions, "write_file": "allow", "edit_file": "allow",
                              "apply_patch": "allow"})
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    ctx = ToolContext(workspace=ws, session_id="build:t1", agent_run_id="r-27a",
                      config=p.config, event_bus=p.event_bus, engine=p.engine)
    return p, perms, ws, ctx


def _arm(p, *, take=True, address=DEV, turn="turn-1"):
    return E.arm(p.config.home, "build:t1", turn, address, take=take)


async def _write(p, perms, ctx, path, content):
    res = await p.registry.invoke("write_file", {"path": path, "content": content}, ctx, perms)
    assert res.ok, res.error
    return res


# --------------------------------------------------------------------------- #
# the pure rules
# --------------------------------------------------------------------------- #


def test_the_words_that_ask_for_pictures():
    for yes in ("make the button blue and show me it works", "Show me that it works!",
                "show me before and after", "I want before and after pictures",
                "before-and-after screenshots please"):
        assert E.asks(yes), yes
    for no in ("show me the code", "it works", "does it work?", "show me what you changed", ""):
        assert not E.asks(no), no


def test_a_turn_takes_pictures_by_itself_only_when_asked_or_switched_on():
    # D-27.1: never chosen (None) or off: only when the message asks.
    assert E.takes(None, "make it blue", DEV, by_person=True) is False
    assert E.takes(False, "make it blue", DEV, by_person=True) is False
    assert E.takes(None, "make it blue and show me it works", DEV, by_person=True) is True
    assert E.takes(False, "show me it works", DEV, by_person=True) is True
    assert E.takes(True, "make it blue", DEV, by_person=True) is True
    # No page open: nothing to take. A turn Iron Jarvis started: never.
    assert E.takes(True, "show me it works", "", by_person=True) is False
    assert E.takes(True, "show me it works", DEV, by_person=False) is False


def test_screen_files_are_known_by_their_kind():
    for yes in ("src/App.tsx", "C:\\web\\Card.VUE", "page.html", "site.scss", "x/y.svelte", "a.jsx"):
        assert E.screen_file(yes), yes
    for no in ("app.py", "server.ts", "README.md", "data.json", "", None):
        assert not E.screen_file(no), no


def test_a_patch_names_its_files():
    patch = ("*** Begin Patch\n*** Update File: src/App.tsx\n@@\n-a\n+b\n"
             "*** Add File: src/new.css\n+x\n*** Update File: api.py\n*** Move to: web/Main.vue\n*** End Patch")
    assert E.written_paths("apply_patch", {"patch": patch}) == ["src/App.tsx", "src/new.css", "api.py", "web/Main.vue"]
    assert E.written_paths("rename_file", {"path": "a.css", "new_path": "b.css"}) == ["a.css", "b.css"]
    assert E.written_paths("read_file", {"path": "a.css"}) == []  # not a writer


# --------------------------------------------------------------------------- #
# through the real registry: the before is taken BEFORE the write lands
# --------------------------------------------------------------------------- #


async def test_the_before_is_taken_before_the_first_screen_write_lands(reg, browsers):
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("<button>old</button>", encoding="utf-8")
    _PageBackend.watch = target
    token = _arm(p)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "<button>new</button>")
        assert target.read_text(encoding="utf-8") == "<button>new</button>"
        rec = await E.at_end([str(target)])
    finally:
        E.reset(token)
    assert [i["id"] for i in rec["items"]] == ["before", "after"]
    folder = Path(rec["folder"])
    # The ORDER, read from the pictures themselves.
    assert (folder / "before.png").read_bytes() == b"PIC:<button>old</button>"
    assert (folder / "after.png").read_bytes() == b"PIC:<button>new</button>"
    assert rec["note"] == ""
    assert {i["where"] for i in rec["items"]} == {"localhost:5173/cart"}
    assert all(i["label"] in ("Before", "After") and i["at"] and len(i["sha"]) == 16 for i in rec["items"])
    # The folder is the app's own, absolute, and named on the record.
    assert folder.is_absolute() and folder.parent.parent == Path(p.config.home) / E.DIRNAME
    for b in browsers.made:
        assert b.killed and not b.work.exists()  # each one-shot ended, its folder gone


async def test_only_the_first_screen_write_waits_for_a_picture(reg, browsers):
    p, perms, ws, ctx = reg
    _PageBackend.watch = ws / "src" / "App.tsx"
    token = _arm(p)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "one")
        await _write(p, perms, ctx, "src/site.css", "two")
        await _write(p, perms, ctx, "src/App.tsx", "three")
        assert len(browsers.made) == 1  # one before, for the first screen write only
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert len(browsers.made) == 2  # and one after
    folder = Path(rec["folder"])
    assert (folder / "before.png").read_bytes() == b"PIC:<none>"  # the file did not exist yet
    assert (folder / "after.png").read_bytes() == b"PIC:three"


async def test_parallel_screen_writes_all_wait_for_the_before(reg, browsers):
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("old", encoding="utf-8")
    _PageBackend.watch = target
    gate = threading.Event()
    _PageBackend.hang = gate
    token = _arm(p)
    try:
        st = E.TURN.get()
        first = asyncio.ensure_future(_write(p, perms, ctx, "src/App.tsx", "from-a"))
        for _ in range(500):  # the before is being taken (wait for what is asserted)
            if browsers.made and st.lock.locked():
                break
            await asyncio.sleep(0.01)
        second = asyncio.ensure_future(_write(p, perms, ctx, "src/App.tsx", "from-b"))
        for _ in range(20):
            await asyncio.sleep(0.01)
        # Neither write has landed while the picture is still being taken.
        assert target.read_text(encoding="utf-8") == "old"
        _PageBackend.hang = None
        gate.set()
        await asyncio.gather(first, second)
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert (Path(rec["folder"]) / "before.png").read_bytes() == b"PIC:old"


async def test_a_change_to_no_screen_file_takes_no_picture(reg, browsers):
    p, perms, ws, ctx = reg
    token = _arm(p)
    try:
        await _write(p, perms, ctx, "app.py", "x = 2\n")
        assert await E.at_end([str(ws / "app.py")]) is None
    finally:
        E.reset(token)
    assert browsers.made == []
    assert not (Path(p.config.home) / E.DIRNAME).exists()


async def test_no_page_open_takes_no_picture(reg, browsers):
    p, perms, ws, ctx = reg
    token = _arm(p, address="", take=True)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "x")
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert browsers.made == []
    assert rec == {"kind": "pictures", "screen": True, "items": [], "note": ""}


async def test_not_asked_takes_none_and_marks_the_reply_for_show_me(reg, browsers):
    p, perms, ws, ctx = reg
    token = _arm(p, take=False)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "x")
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert browsers.made == []  # no invisible browser started unasked
    assert rec == {"kind": "pictures", "screen": True, "items": [], "note": ""}


async def test_a_screen_file_known_only_from_the_replys_files_still_marks_it(reg, browsers):
    p, perms, ws, ctx = reg
    token = _arm(p, take=False)
    try:
        rec = await E.at_end([str(ws / "src" / "made-by-a-tool.html")])
    finally:
        E.reset(token)
    assert rec is not None and rec["screen"] is True


async def test_a_screen_change_with_no_write_seen_first_says_why_there_is_no_before(reg, browsers):
    p, perms, ws, ctx = reg
    page = ws / "src" / "made-by-a-tool.html"
    page.write_text("<p>made</p>", encoding="utf-8")
    _PageBackend.watch = page
    token = _arm(p)
    try:
        rec = await E.at_end([str(page)])
    finally:
        E.reset(token)
    assert [i["id"] for i in rec["items"]] == ["after"]
    assert rec["note"] == E.UNSEEN_WORDS and rec["beforeNote"] == E.UNSEEN_WORDS
    assert len(browsers.made) == 1  # the after only


async def test_a_failed_picture_is_said_never_invented(reg, browsers):
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    _PageBackend.watch = target
    _PageBackend.fail = "the page crashed"
    token = _arm(p)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "new")
        assert target.read_text(encoding="utf-8") == "new"  # the write went ahead
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert rec["items"] == []
    assert rec["note"].startswith("No before picture: ")
    assert "No after picture: " in rec["note"]
    assert not list((Path(p.config.home) / E.DIRNAME).rglob("*.png"))


async def test_a_page_that_does_not_answer_in_time_lets_the_write_go_ahead(reg, browsers, monkeypatch):
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("old", encoding="utf-8")
    _PageBackend.watch = target
    never = threading.Event()
    _PageBackend.hang = never
    monkeypatch.setattr(E, "BEFORE_WAIT_S", 0.05)
    token = _arm(p)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "new")
        # The write landed although the before picture never came.
        assert target.read_text(encoding="utf-8") == "new"
        _PageBackend.hang = None
        rec = await E.at_end([])
    finally:
        E.reset(token)
        never.set()
    assert [i["id"] for i in rec["items"]] == ["after"]
    assert E.BEFORE_SLOW_WORDS in rec["note"]
    assert (Path(rec["folder"]) / "after.png").read_bytes() == b"PIC:new"
    assert not (Path(rec["folder"]) / "before.png").exists()


async def test_identical_pictures_say_the_page_looks_the_same(reg, browsers):
    p, perms, ws, ctx = reg
    _PageBackend.same = True
    token = _arm(p)
    try:
        await _write(p, perms, ctx, "src/App.tsx", "x")
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert [i["id"] for i in rec["items"]] == ["before", "after"]
    assert rec["note"] == E.SAME_WORDS


async def test_a_reply_with_no_turn_armed_is_untouched(reg, browsers):
    p, perms, ws, ctx = reg
    await _write(p, perms, ctx, "src/App.tsx", "x")
    assert await E.at_end([str(ws / "src" / "App.tsx")]) is None
    assert browsers.made == []


# --------------------------------------------------------------------------- #
# through the real build turn route and the real stream lane
# --------------------------------------------------------------------------- #


def _scene(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "App.tsx").write_text("<button>old</button>", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    sid = new_session(app, work)["id"]
    return app, sid, work


def _write_call(cid: str, content: str):
    from iron_jarvis.providers.adapters.base import ToolCall

    return [ToolCall(id=cid, name="write_file", arguments={"path": "src/App.tsx", "content": content})]


async def _reply(app, sid, text, turn_id, *, preview=DEV, moves=()):
    from tests.test_build_sessions_v1367 import Script, frames_of, messages_of, open_turn, wire

    wire(app, Script(list(moves)))
    body = {"text": text, "turn_id": turn_id}
    if preview:
        body["preview_url"] = preview
    status, raw = await open_turn(app, sid, body, timeout=120.0)
    assert status == 200, raw
    done = [d for e, d in frames_of(raw) if e == "done"]
    assert done, raw[-800:]
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == turn_id][-1]
    return done[-1], line


async def test_asking_in_the_message_puts_both_pictures_on_the_reply_line(tmp_path, monkeypatch, browsers):
    from tests.test_build_sessions_v1367 import asgi

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make the button blue and show me it works.", "t-ask",
                              moves=[_write_call("c1", "<button>blue</button>")])
    ev = line["evidence"]
    assert done["evidence"] == ev
    assert [i["id"] for i in ev["items"]] == ["before", "after"]
    assert ev["items"][0]["where"] == "localhost:5173/cart"
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-ask/before")
    assert status == 200 and pic == "PIC:<button>old</button>"
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-ask/after")
    assert status == 200 and pic == "PIC:<button>blue</button>"
    # Both still there after a restart: a new daemon on the same home.
    from iron_jarvis.daemon.app import create_app

    again = create_app(str(tmp_path / "home"))
    status, view = await asgi(again, "GET", f"/build/sessions/{sid}")
    assert status == 200, view
    kept = [m for m in view["messages"] if m.get("turnId") == "t-ask" and m["role"] == "assistant"]
    assert kept and kept[-1]["evidence"] == ev
    status, pic = await asgi(again, "GET", f"/build/sessions/{sid}/evidence/t-ask/after")
    assert status == 200 and pic == "PIC:<button>blue</button>"


async def test_a_branched_session_keeps_show_me_and_says_where_the_pictures_are(tmp_path, monkeypatch, browsers):
    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make it blue and show me it works.", "t-b",
                              moves=[_write_call("c1", "<button>blue</button>")])
    assert len(line["evidence"]["items"]) == 2
    status, view = await asgi(app, "POST", f"/build/sessions/{sid}/branch", {"turn_id": "t-b", "at": "reply"})
    assert status == 201, view
    copied = [m for m in messages_of(app, view["id"]) if m.get("turnId") == "t-b" and m["role"] == "assistant"][-1]
    assert copied["evidence"] == {"kind": "pictures", "screen": True, "items": [], "note": E.BRANCHED_WORDS}
    # The origin's pictures are untouched.
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-b/after")
    assert status == 200 and pic == "PIC:<button>blue</button>"


async def test_without_asking_no_browser_starts_and_show_me_takes_the_after(tmp_path, monkeypatch, browsers):
    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make the button blue.", "t-quiet",
                              moves=[_write_call("c1", "<button>blue</button>")])
    assert browsers.made == []  # D-27.1: nothing started unasked
    assert line["evidence"] == {"kind": "pictures", "screen": True, "items": [], "note": ""}
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-quiet", "preview_url": DEV})
    assert status == 200, body
    ev = body["evidence"]
    assert [i["id"] for i in ev["items"]] == ["after"]
    assert ev["note"] == E.NOT_ASKED_WORDS
    assert body["offer"] is True  # the automatic setting, offered once …
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-quiet" and m["role"] == "assistant"][-1]
    assert kept["evidence"] == ev
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-quiet", "preview_url": DEV})
    assert status == 200 and body["offer"] is False  # … and never again
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-quiet/after")
    assert status == 200 and pic == "PIC:<button>blue</button>"


async def test_with_the_setting_on_pictures_are_taken_by_themselves(tmp_path, monkeypatch, browsers):
    app, sid, work = _scene(tmp_path, monkeypatch)
    app.state.platform.config.reply_pictures = True
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make the button blue.", "t-auto",
                              moves=[_write_call("c1", "<button>blue</button>")])
    assert [i["id"] for i in line["evidence"]["items"]] == ["before", "after"]
    from tests.test_build_sessions_v1367 import asgi

    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-auto", "preview_url": DEV})
    assert status == 200 and body["offer"] is False  # it was chosen: never offered


async def test_a_reply_that_changed_no_screen_file_has_no_pictures_key(tmp_path, monkeypatch, browsers):
    from iron_jarvis.providers.adapters.base import ToolCall

    app, sid, work = _scene(tmp_path, monkeypatch)
    call = [ToolCall(id="c1", name="write_file", arguments={"path": "notes.py", "content": "x = 1\n"})]
    done, line = await _reply(app, sid, "Write notes and show me it works.", "t-py", moves=[call])
    assert "evidence" not in done and "evidence" not in line
    assert browsers.made == []


async def test_show_me_says_why_in_words(tmp_path, monkeypatch, browsers):
    from tests.test_build_sessions_v1367 import asgi

    app, sid, work = _scene(tmp_path, monkeypatch)
    done, line = await _reply(app, sid, "Make the button blue.", "t-w", preview="",
                              moves=[_write_call("c1", "<button>blue</button>")])
    # No page open now, none kept with the session.
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-w"})
    assert status == 409 and body["detail"] == "Open the page in the Browser first, then press Show me."
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "nope", "preview_url": DEV})
    assert status == 404 and body["detail"] == "That reply is not in this session any more."
    # A public address is never opened: it is no page of this PC or network.
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-w", "preview_url": "https://example.com/"})
    assert status == 409 and body["detail"] == "Open the page in the Browser first, then press Show me."
    _PageBackend.fail = "the page crashed"
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-w", "preview_url": DEV})
    assert status == 409 and body["detail"].startswith("No picture: ")
    from tests.test_build_sessions_v1367 import messages_of

    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-w" and m["role"] == "assistant"][-1]
    assert kept["evidence"]["items"] == []  # nothing invented, nothing changed
    assert browsers.made and browsers.made[-1].killed


async def test_show_me_is_refused_while_a_reply_runs(tmp_path, monkeypatch, browsers):
    from iron_jarvis.daemon.routes import build as build_routes

    from tests.test_build_sessions_v1367 import asgi

    app, sid, work = _scene(tmp_path, monkeypatch)
    done, line = await _reply(app, sid, "Make the button blue.", "t-r",
                              moves=[_write_call("c1", "<button>blue</button>")])
    monkeypatch.setattr(build_routes, "_running_turn", lambda d, tid: "t-next")
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-r", "preview_url": DEV})
    assert status == 409 and "still running" in body["detail"]
    assert browsers.made == []


async def test_a_local_only_session_opens_the_page_behind_the_refusing_proxy(tmp_path, monkeypatch, browsers):
    from iron_jarvis.harness import sessions as S

    from tests.test_build_sessions_v1367 import asgi

    app, sid, work = _scene(tmp_path, monkeypatch)
    done, line = await _reply(app, sid, "Make the button blue.", "t-lo",
                              moves=[_write_call("c1", "<button>blue</button>")])
    S.update(app.state.platform.engine, sid, setup={"local_only": True})
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-lo", "preview_url": DEV})
    assert status == 200, body
    assert browsers.made[-1].kw.get("local_only") is True


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_both_lanes_put_the_record_on_the_same_key(tmp_path, monkeypatch, lane):
    """MIRROR NOTE: ``chat_turn.py``'s POST answer and the stream lane's done
    frame call ``evidence.at_end`` with the reply's files at the same seam."""
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app
    from tests.test_clients_lanes_v1499 import _frames

    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    from tests.test_build_sessions_v1367 import Script, wire

    wire(app, Script([]))
    seen: list = []
    mark = {"kind": "pictures", "screen": True, "items": [], "note": ""}

    async def fake_at_end(documents=None):
        seen.append(list(documents or []))
        return mark

    monkeypatch.setattr(E, "at_end", fake_at_end)
    body = {"messages": [{"role": "user", "content": "hello"}], "auto_tools": False}
    if lane == "post":
        r = client.post("/chat", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
    else:
        r = client.post("/chat/stream", json=body)
        assert r.status_code == 200, r.text
        out = [d for e, d in _frames(r.text) if e == "done"][-1]
    assert out["evidence"] == mark
    assert seen == [[]]
