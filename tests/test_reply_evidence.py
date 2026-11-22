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
import time
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
    #: The close goes on until the wall clock is 50 ms past the picture
    #: (``shot_at``), then notes when it ended (``closed_at``).
    slow_close = False
    shot_at: "list[float]" = []
    closed_at: "list[float]" = []

    def __init__(self, *, edge, work, guard, note, background=False, extra_args=(), **kw):
        self.work = Path(work)
        self.kw = kw
        self._url = "about:blank"
        self.started = self.closed = self.killed = False

    async def start(self):
        self.started = True

    async def close(self):
        cls = type(self)
        if cls.slow_close and cls.shot_at:
            # Wait for what is asserted: the clock itself moved on (never a
            # fixed sleep, which can measure short on Windows).
            while time.time() < cls.shot_at[-1] + 0.05:
                await asyncio.sleep(0.005)
            cls.closed_at.append(time.time())
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
        if cls.slow_close:
            cls.shot_at.append(time.time())
        return b"PIC:" + body


@pytest.fixture()
def browsers(monkeypatch):
    made: list[_PageBackend] = []

    def factory(**kw):
        b = _PageBackend(**kw)
        made.append(b)
        return b

    for attr, value in (("watch", None), ("fail", ""), ("hang", None), ("same", False), ("slow_close", False),
                        ("shot_at", []), ("closed_at", [])):
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
    for no in ("show me the code", "it works", "does it work?", "show me what you changed", "",
               # A thing to BUILD, not pictures of the page (two browsers unasked).
               "add a before-and-after screenshots section to the landing page",
               "build a before and after pictures gallery", "a before and after shots slider"):
        assert not E.asks(no), no


def test_the_true_reason_a_turn_takes_no_before_picture():
    # Review: a reply that ASKED but had no page open, or a turn Iron Jarvis
    # started, must not be told "your message did not ask".
    assert E.no_before_words(None, "make it blue", DEV, by_person=True) == E.NOT_ASKED_WORDS
    assert E.no_before_words(None, "make it blue and show me it works", "", by_person=True) == E.NO_PAGE_WORDS
    assert E.no_before_words(True, "make it blue", "", by_person=True) == E.NO_PAGE_WORDS
    assert E.no_before_words(True, "show me it works", DEV, by_person=False) == E.STARTED_WORDS
    # Review note: a reply Iron Jarvis CARRIED ON (after a limit, a restart)
    # carries out a message, so it is never called "not a message of yours".
    carried = E.no_before_words(True, "show me it works", DEV, by_person=False, carried=True)
    assert carried == E.CARRIED_WORDS and "not a message of yours" not in carried
    # Review 5 (should): a night task's words are the person's own (queued
    # for tonight), so its reply is never called "not a message of yours".
    night = E.no_before_words(True, "show me it works", DEV, by_person=False, night=True)
    assert night == E.NIGHT_WORDS and "not a message of yours" not in night
    assert "queued for tonight" in night
    assert E.no_before_words(None, "make it blue and show me it works", DEV, by_person=True) == ""


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


def test_show_me_is_for_the_newest_reply_that_changed_a_screen_and_has_no_after():
    """The route's rule (``build_evidence.not_offered``), read off the line."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import build_evidence as BE

    mark = {"kind": "pictures", "screen": True, "items": [], "note": ""}
    after = {**mark, "items": [{"id": "after", "label": "After", "where": "x", "at": "t", "sha": "a" * 16}]}

    def row(*lines):
        return SimpleNamespace(messages_json=json.dumps(list(lines)))

    user = {"role": "user", "content": "x"}
    carry = {"role": "user", "content": "carry on", "continuation": True}

    def reply(turn, ev=None):
        return {"role": "assistant", "content": "ok", "turnId": turn, **({"evidence": ev} if ev else {})}

    assert BE.not_offered(row(user, reply("t1", mark)), "t1") == ""
    # The daemon's own hidden "carry on" question, as the screen hides it.
    assert BE.not_offered(row(user, reply("t1", mark), carry), "t1") == ""
    assert BE.not_offered(row(reply("t1", mark), user, reply("t2", mark)), "t1") == BE.NOT_NEWEST
    assert BE.not_offered(row(reply("t1", mark), user), "t1") == BE.NOT_NEWEST
    assert BE.not_offered(row(user, reply("t1")), "t1") == BE.NO_SCREEN
    assert BE.not_offered(row(user, reply("t1", after)), "t1") == E.HAS_AFTER_WORDS
    assert BE.not_offered(row(user, reply("t1", after)), "t1", after_too=False) == ""
    assert BE.not_offered(row(user, reply("t1", mark)), "t9") == BE.NO_REPLY


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
        # Another screen file (two writes to ONE file at once collide on
        # Windows whatever this hook does): it waits for the same before.
        other = ws / "src" / "site.css"
        second = asyncio.ensure_future(_write(p, perms, ctx, "src/site.css", "from-b"))
        # Wait for what is asserted: the second write PARKED on the turn's
        # lock (never a fixed sleep: without the lock it never parks).
        parked = False
        for _ in range(500):
            if len(getattr(st.lock, "_waiters", None) or ()) >= 1:
                parked = True
                break
            if second.done():
                break
            await asyncio.sleep(0.01)
        # Neither write has landed while the picture is still being taken.
        assert target.read_text(encoding="utf-8") == "old"
        assert not other.exists()
        assert parked, "the second screen write did not wait for the before picture"
        _PageBackend.hang = None
        gate.set()
        await asyncio.gather(first, second)
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert (Path(rec["folder"]) / "before.png").read_bytes() == b"PIC:old"
    assert target.read_text(encoding="utf-8") == "from-a" and other.read_text(encoding="utf-8") == "from-b"


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


async def _early_done(st) -> None:
    """Wait for what is asserted: the picture started as the reply began has
    finished (never a fixed sleep)."""
    for _ in range(1000):
        if st.early is not None and st.early.done():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the before picture started as the reply began did not finish")


async def test_the_before_starts_as_the_reply_begins_so_a_slow_page_still_gets_one(reg, browsers, monkeypatch):
    """Review (should 2): with the real Edge, one picture can take longer than
    the 8 s a screen write is held. The before starts as the reply begins, so
    by the model's first write it is there; the write does not need one."""
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("<button>old</button>", encoding="utf-8")
    _PageBackend.watch = target
    monkeypatch.setattr(E, "BEFORE_WAIT_S", 0.05)  # far less than a picture takes here
    token = _arm(p)
    try:
        st = E.TURN.get()
        assert E.start_before(local_only=False) is True
        assert E.start_before(local_only=False) is False  # once per reply
        await _early_done(st)  # the model is still thinking meanwhile
        # From now on any NEW picture never comes: the write must not need one.
        later = threading.Event()
        _PageBackend.hang = later
        await _write(p, perms, ctx, "src/App.tsx", "<button>new</button>")
        assert target.read_text(encoding="utf-8") == "<button>new</button>"
        assert len(browsers.made) == 1
        _PageBackend.hang = None
        later.set()
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert [i["id"] for i in rec["items"]] == ["before", "after"] and rec["note"] == ""
    folder = Path(rec["folder"])
    assert (folder / "before.png").read_bytes() == b"PIC:<button>old</button>"
    assert (folder / "after.png").read_bytes() == b"PIC:<button>new</button>"
    assert browsers.made[0].kw.get("local_only") is None  # the scope it was started under


async def test_the_before_says_when_it_was_taken_not_when_the_first_write_kept_it(reg, browsers, monkeypatch):
    """Review (should): the before started as the reply began is kept only at
    the first screen write, which can come minutes later in a long reply.
    Its time on the reply, and in ``evidence.json``, is when the picture was
    TAKEN; only ``saved_at`` is the save's."""
    from datetime import datetime, timedelta, timezone

    def picture_clock() -> datetime:
        # The bounds are read the way the picture's own time is read:
        # time.time() (ActResult.taken_at) made a datetime. E.utcnow()
        # (datetime.now) cuts to the microsecond while a float timestamp
        # rounds to the nearest one, so on Windows a picture taken BEFORE a
        # utcnow() read could say 1 us after it (about a third of
        # back-to-back reads on Python 3.12), and this test passed only on
        # CI's retry. One clock read one way keeps the order exact.
        return datetime.fromtimestamp(time.time(), timezone.utc)

    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("<button>old</button>", encoding="utf-8")
    _PageBackend.watch = target
    token = _arm(p)
    try:
        st = E.TURN.get()
        began = picture_clock()
        assert E.start_before(local_only=False) is True
        await _early_done(st)
        taken_by = picture_clock()
        # The model reads and runs things for four minutes before its first
        # screen write: the app's clock reads that late from now on.
        later = taken_by + timedelta(minutes=4)
        monkeypatch.setattr(E, "utcnow", lambda: later)
        await _write(p, perms, ctx, "src/App.tsx", "<button>new</button>")
        rec = await E.at_end([])
    finally:
        E.reset(token)
    folder = Path(rec["folder"])
    assert (folder / "before.png").read_bytes() == b"PIC:<button>old</button>"  # the early one
    before = next(i for i in rec["items"] if i["id"] == "before")
    at = datetime.fromisoformat(before["at"])
    assert began <= at <= taken_by, (began.isoformat(), before["at"], taken_by.isoformat())
    data = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
    assert next(i for i in data["items"] if i["id"] == "before")["at"] == before["at"]
    assert data["saved_at"] == later.isoformat()  # the save's own moment, apart


async def test_a_pictures_time_is_the_browsers_own_moment_never_after_its_close(browsers):
    """The time is read where the picture is taken (``ActResult.taken_at`` in
    the one-shot), not when the one-shot returns: its browser's close comes
    after the picture and can take seconds."""
    from datetime import datetime

    _PageBackend.slow_close = True
    png, _blocked, at = await E._take(DEV, limit_s=10)
    assert png == b"PIC:<none>"
    assert len(_PageBackend.shot_at) == 1 and len(_PageBackend.closed_at) == 1
    taken = datetime.fromisoformat(at).timestamp()  # whole microseconds: 1 ms of slack below
    assert _PageBackend.shot_at[0] - 0.001 <= taken < _PageBackend.closed_at[0], (
        _PageBackend.shot_at, taken, _PageBackend.closed_at)


async def test_a_before_still_being_taken_at_the_first_write_is_never_kept_late(reg, browsers, monkeypatch):
    """Held at most BEFORE_WAIT_S; then the write goes ahead and the picture,
    which could now show the change, is ended — never kept as the before."""
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("old", encoding="utf-8")
    _PageBackend.watch = target
    never = threading.Event()
    _PageBackend.hang = never
    monkeypatch.setattr(E, "BEFORE_WAIT_S", 0.05)
    token = _arm(p)
    try:
        st = E.TURN.get()
        assert E.start_before(local_only=True) is True
        await _write(p, perms, ctx, "src/App.tsx", "new")
        assert target.read_text(encoding="utf-8") == "new"
        assert st.early.cancelled()
        _PageBackend.hang = None
        rec = await E.at_end([])
    finally:
        E.reset(token)
        never.set()
    assert [i["id"] for i in rec["items"]] == ["after"]
    assert E.BEFORE_SLOW_WORDS in rec["note"]
    assert not (Path(rec["folder"]) / "before.png").exists()
    assert browsers.made[0].kw.get("local_only") is True  # a Local only turn's scope


async def test_a_before_that_failed_as_the_reply_began_is_taken_again_at_the_write(reg, browsers):
    """A page not up yet as the reply began (the agent starts the dev server
    itself): the first screen write tries once more within its own hold."""
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("old", encoding="utf-8")
    _PageBackend.watch = target
    _PageBackend.fail = "the page is not up yet"
    token = _arm(p)
    try:
        st = E.TURN.get()
        assert E.start_before(local_only=False) is True
        await _early_done(st)
        assert st.early.exception() is not None
        _PageBackend.fail = ""
        await _write(p, perms, ctx, "src/App.tsx", "new")
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert [i["id"] for i in rec["items"]] == ["before", "after"] and rec["note"] == ""
    assert (Path(rec["folder"]) / "before.png").read_bytes() == b"PIC:old"
    assert len(browsers.made) == 3  # the failed early one, the write's, the after


async def test_a_reply_that_writes_no_screen_file_keeps_no_early_picture(reg, browsers):
    p, perms, ws, ctx = reg
    never = threading.Event()
    _PageBackend.hang = never
    token = _arm(p)
    try:
        st = E.TURN.get()
        assert E.start_before(local_only=False) is True
        await _write(p, perms, ctx, "app.py", "x = 2\n")
        assert await E.at_end([str(ws / "app.py")]) is None
        for _ in range(500):  # ended, not left running (wait for what is asserted)
            if st.early.done():
                break
            await asyncio.sleep(0.01)
        assert st.early.cancelled()
    finally:
        E.reset(token)
        never.set()
    assert not (Path(p.config.home) / E.DIRNAME).exists()


async def test_the_turns_end_ends_a_before_still_being_taken(reg, browsers):
    """A Stop or a failure ends the turn with no done frame: its end (the
    turn task's callback in ``start_build_turn``) ends the picture."""
    p, perms, ws, ctx = reg
    never = threading.Event()
    _PageBackend.hang = never
    token = _arm(p)
    try:
        st = E.TURN.get()
        assert E.start_before(local_only=False) is True
    finally:
        E.reset(token)
    try:
        E.stop_before(st)
        for _ in range(500):
            if st.early.done():
                break
            await asyncio.sleep(0.01)
        assert st.early.cancelled()
    finally:
        never.set()
    assert not (Path(p.config.home) / E.DIRNAME).exists()


async def test_a_turn_that_takes_no_pictures_starts_none(reg, browsers):
    p, perms, ws, ctx = reg
    for take, address in ((False, DEV), (True, "")):
        token = _arm(p, take=take, address=address)
        try:
            assert E.start_before(local_only=False) is False
            assert E.TURN.get().early is None
        finally:
            E.reset(token)
    assert browsers.made == []


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


@pytest.mark.parametrize("take", [False, True])
async def test_a_screen_write_that_failed_is_no_change(reg, browsers, take):
    """Review: a write that did not land (edit_file whose text is not there)
    must not mark the reply as having changed a screen, nor start the after
    browser; a before taken for it is not kept."""
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("<button>old</button>", encoding="utf-8")
    _PageBackend.watch = target
    token = _arm(p, take=take)
    try:
        res = await p.registry.invoke(
            "edit_file", {"path": "src/App.tsx", "old": "not in the file", "new": "x"}, ctx, perms,
        )
        assert not res.ok
        assert target.read_text(encoding="utf-8") == "<button>old</button>"
        assert len(browsers.made) == (1 if take else 0)  # the before was tried, before the write
        rec = await E.at_end([])
    finally:
        E.reset(token)
    assert rec is None
    assert len(browsers.made) == (1 if take else 0)  # no after for a change that did not happen
    assert not list((Path(p.config.home) / E.DIRNAME).rglob("*.png"))


async def test_show_me_keeps_a_before_only_while_it_is_on_disk(reg, browsers):
    """Review: a before the record names but that is no longer kept is never
    shown as if it were; the note says why."""
    p, perms, ws, ctx = reg
    target = ws / "src" / "App.tsx"
    target.write_text("old", encoding="utf-8")
    _PageBackend.watch = target
    home = p.config.home
    before = E._save(home, "build:t1", "turn-9", "before", b"PIC:old", DEV, at=E._now())
    current = {"kind": "pictures", "screen": True, "items": [before], "note": "No after picture: x."}
    (E.reply_folder(home, "build:t1", "turn-9") / "before.png").unlink()
    assert E.picture_file(home, "build:t1", "turn-9", "before") is None
    rec = await E.take_after_now(home, "build:t1", "turn-9", DEV, current)
    assert [i["id"] for i in rec["items"]] == ["after"]
    assert rec["note"] == E.BEFORE_GONE_WORDS


async def test_show_me_never_replaces_an_after_picture(reg, browsers):
    p, perms, ws, ctx = reg
    home = p.config.home
    after = E._save(home, "build:t1", "turn-8", "after", b"PIC:as-the-reply-left-it", DEV, at=E._now())
    current = {"kind": "pictures", "screen": True, "items": [after], "note": ""}
    with pytest.raises(E.Refused) as err:
        await E.take_after_now(home, "build:t1", "turn-8", DEV, current)
    assert str(err.value) == E.HAS_AFTER_WORDS
    assert browsers.made == []
    assert E.picture_file(home, "build:t1", "turn-8", "after").read_bytes() == b"PIC:as-the-reply-left-it"


async def test_show_me_keeps_nothing_when_asked_again_says_no(reg, browsers):
    p, perms, ws, ctx = reg
    home = p.config.home
    asked: list[str] = []

    async def still():
        asked.append("x")
        return "A reply started meanwhile."

    with pytest.raises(E.Refused) as err:
        await E.take_after_now(home, "build:t1", "turn-7", DEV, {"kind": "pictures", "screen": True, "items": []},
                               still=still)
    assert str(err.value) == "A reply started meanwhile." and asked == ["x"]
    assert len(browsers.made) == 1  # the picture was taken …
    assert E.picture_file(home, "build:t1", "turn-7", "after") is None  # … and not kept


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
    started: list[bool] = []

    async def first_move(_script):
        # Review: the before picture started as the reply began — before
        # the model's first answer, so long before its first screen write.
        st = E.TURN.get()
        started.append(st is not None and st.early is not None)
        return _write_call("c1", "<button>blue</button>")

    done, line = await _reply(app, sid, "Make the button blue and show me it works.", "t-ask",
                              moves=[first_move])
    assert started == [True]
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
    assert line["evidence"] == {"kind": "pictures", "screen": True, "items": [], "note": "",
                                "beforeNote": E.NOT_ASKED_WORDS}
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-quiet", "preview_url": DEV})
    assert status == 200, body
    ev = body["evidence"]
    assert [i["id"] for i in ev["items"]] == ["after"]
    assert ev["note"] == E.NOT_ASKED_WORDS
    assert body["offer"] is True  # the automatic setting, offered once …
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-quiet" and m["role"] == "assistant"][-1]
    assert kept["evidence"] == ev
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-quiet/after")
    assert status == 200 and pic == "PIC:<button>blue</button>"
    done, line = await _reply(app, sid, "Now make it red.", "t-quiet2",
                              moves=[_write_call("c2", "<button>red</button>")])
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-quiet2", "preview_url": DEV})
    assert status == 200 and body["offer"] is False  # … and never again


async def test_show_me_reads_the_conversation_off_the_event_loop(tmp_path, monkeypatch, browsers):
    """Review note (hard rule: nothing blocking on the event loop): a Show me
    press reads the session's whole conversation (its JSON) several times
    — the line, newest-reply check before and after the picture — each in a
    worker thread, never on the loop that serves every other request."""
    from iron_jarvis.harness import sessions as S

    from tests.test_build_sessions_v1367 import asgi

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    await _reply(app, sid, "Make the button blue.", "t-loop", moves=[_write_call("c1", "<button>blue</button>")])
    real = S._messages
    reads: list[bool] = []

    def watched(row):
        try:
            asyncio.get_running_loop()
            reads.append(True)  # on the event loop
        except RuntimeError:
            reads.append(False)  # in a worker thread
        return real(row)

    monkeypatch.setattr(S, "_messages", watched)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-loop", "preview_url": DEV})
    monkeypatch.setattr(S, "_messages", real)
    assert status == 200, body
    assert [i["id"] for i in body["evidence"]["items"]] == ["after"]
    assert reads and True not in reads, reads


async def test_with_the_setting_on_pictures_are_taken_by_themselves(tmp_path, monkeypatch, browsers):
    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    app.state.platform.config.reply_pictures = True
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make the button blue.", "t-auto",
                              moves=[_write_call("c1", "<button>blue</button>")])
    assert [i["id"] for i in line["evidence"]["items"]] == ["before", "after"]
    # Review B (blocking, case b): Show me never replaces the after the
    # reply already has — the page has changed since (by hand here).
    (work / "src" / "App.tsx").write_text("<button>changed later</button>", encoding="utf-8")
    made = len(browsers.made)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-auto", "preview_url": DEV})
    assert status == 409 and body["detail"] == E.HAS_AFTER_WORDS
    assert len(browsers.made) == made  # no browser started for it
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-auto/after")
    assert status == 200 and pic == "PIC:<button>blue</button>"  # the after as the reply left it
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-auto" and m["role"] == "assistant"][-1]
    assert kept["evidence"] == line["evidence"]
    # It was chosen: never offered (a reply with no page open, pressed later).
    done, line = await _reply(app, sid, "Make it green.", "t-auto2", preview="",
                              moves=[_write_call("c2", "<button>green</button>")])
    assert line["evidence"]["beforeNote"] == E.NO_PAGE_WORDS
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence",
                              {"turn_id": "t-auto2", "preview_url": DEV})
    assert status == 200 and body["offer"] is False


async def test_show_me_is_only_for_the_newest_reply(tmp_path, monkeypatch, browsers):
    """Review B (blocking, case a): Show me pictures the page AS IT IS NOW,
    so an earlier reply never files it as its after picture."""
    from iron_jarvis.providers.adapters.base import ToolCall

    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    await _reply(app, sid, "Make the button blue.", "t-old", moves=[_write_call("c1", "<button>blue</button>")])
    await _reply(app, sid, "Now make it red.", "t-new", moves=[_write_call("c2", "<button>red</button>")])
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-old", "preview_url": DEV})
    assert status == 409 and body["detail"].startswith("Show me takes the page as it is now")
    assert browsers.made == []
    old = [m for m in messages_of(app, sid) if m.get("turnId") == "t-old" and m["role"] == "assistant"][-1]
    assert old["evidence"]["items"] == []
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-old/after")
    assert status == 404
    # The newest reply's own press works.
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-new", "preview_url": DEV})
    assert status == 200, body
    # A later reply that changed no screen file: the earlier one is no longer
    # the newest, and the newest has no picture to take.
    call = [ToolCall(id="c3", name="write_file", arguments={"path": "api.py", "content": "x = 1\n"})]
    await _reply(app, sid, "Fix the api.", "t-api", moves=[call])
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-api", "preview_url": DEV})
    assert status == 409 and body["detail"] == "That reply changed no screen file, so there is no picture to take for it."


async def test_show_me_keeps_nothing_when_a_reply_starts_while_it_is_taken(tmp_path, monkeypatch, browsers):
    from iron_jarvis.daemon.routes import build as build_routes

    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    await _reply(app, sid, "Make the button blue.", "t-m", moves=[_write_call("c1", "<button>blue</button>")])
    real_take = E._take
    running = {"turn": None}

    async def take_then_a_reply_starts(address, *, limit_s):
        out = await real_take(address, limit_s=limit_s)
        running["turn"] = "t-next"
        return out

    monkeypatch.setattr(E, "_take", take_then_a_reply_starts)
    monkeypatch.setattr(build_routes, "_running_turn", lambda d, tid: running["turn"])
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-m", "preview_url": DEV})
    assert status == 409 and body["detail"].startswith("A reply started while the picture was being taken")
    assert len(browsers.made) == 1
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-m" and m["role"] == "assistant"][-1]
    assert kept["evidence"]["items"] == []
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-m/after")
    assert status == 404


async def test_show_me_never_overwrites_an_after_filed_while_it_was_taken(tmp_path, monkeypatch, browsers):
    """Asked again once the picture is taken, BEFORE it is kept: an after
    that reached the reply meanwhile is kept, on the line and on disk."""
    from iron_jarvis.harness import sessions as S

    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    await _reply(app, sid, "Make the button blue.", "t-race", moves=[_write_call("c1", "<button>blue</button>")])
    home = app.state.platform.config.home
    real_take = E._take

    async def take_while_another_after_lands(address, *, limit_s):
        out = await real_take(address, limit_s=limit_s)
        other = E._save(home, sid, "t-race", "after", b"PIC:the-other-one", DEV, at=E._now())
        assert S.amend_reply(app.state.platform.engine, sid, "t-race", {
            "evidence": {"kind": "pictures", "screen": True, "items": [other], "note": ""}})
        return out

    monkeypatch.setattr(E, "_take", take_while_another_after_lands)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-race", "preview_url": DEV})
    assert status == 409 and body["detail"] == E.HAS_AFTER_WORDS
    status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/t-race/after")
    assert status == 200 and pic == "PIC:the-other-one"
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-race" and m["role"] == "assistant"][-1]
    assert [i["id"] for i in kept["evidence"]["items"]] == ["after"]


async def test_show_me_never_writes_over_an_after_that_reaches_the_line_as_it_is_kept(tmp_path, monkeypatch,
                                                                                         browsers):
    """Review note (the last guard, ``amend_reply(expect=...)``): an after
    that reaches the line AFTER the question is asked again, while the press's
    picture is being kept, stays on the line; the press is refused."""
    from iron_jarvis.harness import sessions as S

    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    await _reply(app, sid, "Make the button blue.", "t-late", moves=[_write_call("c1", "<button>blue</button>")])
    engine = app.state.platform.engine
    other = {"id": "after", "label": "After", "where": "localhost:5173/cart", "at": "then", "sha": "f" * 16}
    real_save = E._save

    def save_while_another_after_lands(home, thread_id, turn_id, item_id, png, address, blocked=None, *, at):
        out = real_save(home, thread_id, turn_id, item_id, png, address, blocked, at=at)
        assert S.amend_reply(engine, sid, "t-late", {
            "evidence": {"kind": "pictures", "screen": True, "items": [other], "note": ""}})
        return out

    monkeypatch.setattr(E, "_save", save_while_another_after_lands)
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-late", "preview_url": DEV})
    assert status == 409 and body["detail"] == "The picture was taken but could not be kept on the reply. Try again."
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-late" and m["role"] == "assistant"][-1]
    assert kept["evidence"]["items"] == [other]


async def test_show_me_is_refused_in_an_archived_session(tmp_path, monkeypatch, browsers):
    from tests.test_build_sessions_v1367 import asgi, messages_of

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    await _reply(app, sid, "Make the button blue.", "t-arch", moves=[_write_call("c1", "<button>blue</button>")])
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/archive", {})
    assert status == 200, body
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-arch", "preview_url": DEV})
    assert status == 409 and body["detail"] == "This session is archived. Bring it back to take pictures."
    assert browsers.made == []
    kept = [m for m in messages_of(app, sid) if m.get("turnId") == "t-arch" and m["role"] == "assistant"][-1]
    assert kept["evidence"]["items"] == []


async def test_asking_with_no_page_open_says_so_not_that_you_did_not_ask(tmp_path, monkeypatch, browsers):
    """Review A: the message asked, but no page was open when the reply began."""
    from tests.test_build_sessions_v1367 import asgi

    app, sid, work = _scene(tmp_path, monkeypatch)
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make the button blue and show me it works.", "t-np", preview="",
                              moves=[_write_call("c1", "<button>blue</button>")])
    assert browsers.made == []
    assert line["evidence"] == {"kind": "pictures", "screen": True, "items": [], "note": "",
                                "beforeNote": E.NO_PAGE_WORDS}
    status, body = await asgi(app, "POST", f"/build/sessions/{sid}/evidence", {"turn_id": "t-np", "preview_url": DEV})
    assert status == 200, body
    assert body["evidence"]["note"] == E.NO_PAGE_WORDS


@pytest.mark.parametrize(("started_by", "continuation", "why"), [
    ("wait", True, E.CARRIED_WORDS), ("restart", True, E.CARRIED_WORDS), ("night", False, E.NIGHT_WORDS),
    ("ci", False, E.STARTED_WORDS),
], ids=["wait", "restart", "night", "ci"])
async def test_a_turn_iron_jarvis_started_takes_no_pictures_by_itself(tmp_path, monkeypatch, browsers,
                                                                     started_by, continuation, why):
    """Review A/B: the wiring (``by_person``) that keeps a night task or a
    carry-on Iron Jarvis started itself (Resume when ready, a restart) from
    starting the invisible browser, with the setting ON, a page open and the
    words in the message. (A carry-on a PERSON pressed is theirs: below.)
    Review note: a carry-on says it was CARRIED ON, never "not a message of
    yours". Review 5: a night task carries out words the person queued for
    tonight, so it says so, never "not a message of yours"; only a fix of
    failing checks (CI, Iron Jarvis's own words) says STARTED_WORDS."""
    from iron_jarvis.daemon.routes import build as build_routes

    from tests.test_build_sessions_v1367 import Script, messages_of, until, wire

    app, sid, work = _scene(tmp_path, monkeypatch)
    app.state.platform.config.reply_pictures = True
    target = work / "src" / "App.tsx"
    _PageBackend.watch = target
    wire(app, Script([_write_call("c1", "<button>blue</button>")]))
    turn = f"t-{started_by}-{continuation}"
    lg = await build_routes.start_build_turn(
        app.state.d, sid, "Make the button blue and show me it works.", turn,
        preview_url=DEV, started_by=started_by, continuation=continuation,
    )
    await until(lambda: lg.status != "running", 60)
    assert target.read_text(encoding="utf-8") == "<button>blue</button>"  # the write landed
    assert browsers.made == []  # no invisible browser, before or after
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == turn][-1]
    assert line["evidence"] == {"kind": "pictures", "screen": True, "items": [], "note": "",
                                "beforeNote": why}
    if started_by != "ci":
        assert "not a message of yours" not in json.dumps(line["evidence"])


@pytest.mark.parametrize(("setting", "words", "pictures"), [
    (True, "Plan how to make the button blue.", True),
    (None, "Plan how to make the button blue, then show me it works.", True),
    (None, "Plan how to make the button blue.", False),
])
async def test_the_act_turn_of_a_plan_you_allowed_is_your_reply(tmp_path, monkeypatch, browsers,
                                                               setting, words, pictures):
    """Review (should 1): Allow on the plan door is the PERSON's press. The
    act turn (a hidden carry-on, its words Iron Jarvis's own) is their reply:
    it takes pictures as their plan message asked, or as the setting says,
    on the page address kept with the session — and never says Iron Jarvis
    started it."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.routes import build as build_routes
    from iron_jarvis.providers.adapters.base import ToolCall

    from tests.test_build_sessions_v1367 import (
        Script,
        asgi,
        messages_of,
        new_session,
        open_turn,
        turn_frames,
        turn_log,
        until,
        wire,
    )
    from tests.test_chat_turn_stop_v1241 import _asgi_post

    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    target = work / "src" / "App.tsx"
    target.write_text("<button>old</button>", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    app.state.platform.config.reply_pictures = setting
    sid = new_session(app, work, mode="plan", approval_mode="yolo")["id"]
    _PageBackend.watch = target
    armed: list[tuple[bool, bool]] = []

    def look() -> None:
        st = E.TURN.get()
        armed.append((bool(st and st.take), bool(st and st.early is not None)))

    async def plan_move(_script):
        look()
        return ("My plan: make the button blue.", [ToolCall(id="x1", name="ExitPlanMode", arguments={})])

    async def act_move(_script):
        look()
        return _write_call("c1", "<button>blue</button>")

    wire(app, Script([plan_move, "Here is the plan.", act_move, "Made it blue."]))
    drop = asyncio.Event()
    plan = asyncio.create_task(open_turn(app, sid, {"text": words, "turn_id": "bp-1", "preview_url": DEV},
                                         drop=drop, timeout=120.0))
    try:
        card = (await until(lambda: [d for e, d in turn_frames(app, "bp-1") if e == "approval"], 60))[0]
        assert card["tool"] == "exit_plan_mode"
        assert (await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "once"}))[0] == 200
        cont = await until(lambda: [m for m in messages_of(app, sid)
                                    if m["role"] == "user" and m.get("continuation")], 60)
        assert cont and cont[0]["content"] == build_routes.PLAN_RUN_TEXT
        act = cont[0]["turnId"]
        lg = await until(lambda: turn_log(app, act), 60)
        await until(lambda: lg.status != "running", 60)
        assert lg.status == "done"
    finally:
        drop.set()
        await plan
        build_routes._PLAN_RUNS.clear()
    assert target.read_text(encoding="utf-8") == "<button>blue</button>"
    line = [m for m in messages_of(app, sid) if m.get("role") == "assistant" and m.get("turnId") == act][-1]
    ev = line["evidence"]
    assert E.STARTED_WORDS not in json.dumps(ev) and E.CARRIED_WORDS not in json.dumps(ev)
    # The plan turn (plan mode: it changes no file) starts no browser; the act
    # turn starts its before as it begins.
    assert armed == [(pictures, False), (pictures, pictures)]
    if pictures:
        assert [i["id"] for i in ev["items"]] == ["before", "after"], ev
        assert ev["note"] == ""
        status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/{act}/before")
        assert status == 200 and pic == "PIC:<button>old</button>"
        status, pic = await asgi(app, "GET", f"/build/sessions/{sid}/evidence/{act}/after")
        assert status == 200 and pic == "PIC:<button>blue</button>"
    else:
        assert ev == {"kind": "pictures", "screen": True, "items": [], "note": "",
                      "beforeNote": E.NOT_ASKED_WORDS}
        assert browsers.made == []


async def test_a_local_only_replys_before_starts_behind_the_refusing_proxy(tmp_path, monkeypatch, browsers):
    """The before starts in ``start_build_turn``, before the lane sets the
    turn's own Local only scope: it is started under the session's."""
    from iron_jarvis.daemon.app import create_app

    from tests.test_build_sessions_v1367 import new_session

    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "App.tsx").write_text("<button>old</button>", encoding="utf-8")
    app = create_app(str(tmp_path / "home"))
    sid = new_session(app, work, local_only=True)["id"]
    _PageBackend.watch = work / "src" / "App.tsx"
    done, line = await _reply(app, sid, "Make the button blue and show me it works.", "t-lo-ask",
                              moves=[_write_call("c1", "<button>blue</button>")])
    assert [i["id"] for i in line["evidence"]["items"]] == ["before", "after"], line
    assert len(browsers.made) == 2
    assert all(b.kw.get("local_only") is True for b in browsers.made)


async def test_a_stopped_reply_ends_its_before_picture(tmp_path, monkeypatch, browsers):
    """A Stop ends the turn: the before picture still being taken is ended,
    never left running, and nothing is kept."""
    from tests.test_build_sessions_v1367 import Script, asgi, open_turn, turn_log, until, wire

    app, sid, work = _scene(tmp_path, monkeypatch)
    never = threading.Event()
    _PageBackend.hang = never
    seen: dict = {}
    hold = asyncio.Event()

    async def think_on(_script):
        seen["st"] = E.TURN.get()
        await hold.wait()
        return "Done."

    wire(app, Script([think_on]))
    drop = asyncio.Event()
    turn = asyncio.create_task(open_turn(app, sid, {"text": "Make it blue and show me it works.",
                                                    "turn_id": "t-stop", "preview_url": DEV},
                                         drop=drop, timeout=120.0))
    try:
        st = await until(lambda: seen.get("st"), 60)
        assert st is not None and st.early is not None and not st.early.done()
        await asgi(app, "POST", "/chat/turns/t-stop/stop", {})
        lg = await until(lambda: turn_log(app, "t-stop"), 30)
        await until(lambda: lg.status != "running", 60)
        await until(lambda: st.early.done(), 30)
        assert st.early.cancelled()
    finally:
        drop.set()
        hold.set()
        never.set()
        await turn
    assert not list((Path(app.state.platform.config.home) / E.DIRNAME).rglob("*.png"))


def test_the_question_a_pressed_carry_on_carries_out_is_the_persons_newest():
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import build as build_routes

    def row(*lines):
        return SimpleNamespace(messages_json=json.dumps(list(lines)))

    asked = {"role": "user", "content": "Make it blue and show me it works.", "turnId": "t1"}
    older = {"role": "user", "content": "Older words.", "turnId": "t0"}
    steer = {"role": "user", "content": "steer words", "turnId": "t1", "steer": True}
    carry = {"role": "user", "content": build_routes.PLAN_RUN_TEXT, "turnId": "t2", "continuation": True}
    reply = {"role": "assistant", "content": "ok", "turnId": "t1"}
    assert build_routes._person_question(row(older, asked, steer, reply, carry, reply)) == asked["content"]
    assert build_routes._person_question(row(carry)) == ""


async def test_a_reply_that_changed_no_screen_file_has_no_pictures_key(tmp_path, monkeypatch, browsers):
    from iron_jarvis.providers.adapters.base import ToolCall

    app, sid, work = _scene(tmp_path, monkeypatch)
    call = [ToolCall(id="c1", name="write_file", arguments={"path": "notes.py", "content": "x = 1\n"})]
    done, line = await _reply(app, sid, "Write notes and show me it works.", "t-py", moves=[call])
    assert "evidence" not in done and "evidence" not in line
    # It asked, so its before picture started as it began; no screen write
    # took it, so nothing is kept, and the turn's end ended it.
    assert not list((Path(app.state.platform.config.home) / E.DIRNAME).rglob("*.png"))


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


def test_the_stream_lane_takes_the_after_before_it_reads_the_owed_notes():
    """Review B: the after picture (up to AFTER_WAIT_S) is awaited BEFORE the
    stream lane reads its unread notes as owed, so a Stop while it is taken
    hands them back on the error frame instead of losing them."""
    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon" / "routes" / "chat.py")
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
    taken = text.index("_evidence_rec = await _evidence_at_end(made_docs)")
    owed = text.index("unread_steers = handle.owed_steers() if handle is not None else []")
    keyed = text.index('done_frame["evidence"] = _evidence_rec')
    assert taken < owed < keyed
